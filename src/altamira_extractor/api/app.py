"""Factoria de la API HTTP + UI (Prompt 13b, extendido en Prompt 13d).

`create_app(settings)`: sin `app = FastAPI()` a nivel de modulo -- todo
objeto con estado (el `Settings` recibido, el `RunExecutor` local, las
`Jinja2Templates`) vive en `app.state`, poblado dentro del `lifespan`, y
se inyecta via `Depends` (`api/deps.py`/`ui/deps.py`). No hay ningun
global mutable de modulo.

Alcance: API JSON + descarga de artefactos + UI HTML server-rendered
(Jinja2/HTMX minimo, same-origin, sin CORS -- la UI la sirve este mismo
`app`) y sin extension adicional de Typer (ya cubierta en `cli.py`,
Prompt 13c). No se agrega autenticacion (fuera de alcance V1, no
documentada) -- el acceso de red a este servicio debe restringirse
externamente (firewall/red interna) hasta que exista un mecanismo de
autenticacion. Las rutas `/ui/*` llevan `include_in_schema=False`: el
OpenAPI JSON solo documenta la API JSON ya autorizada.

V1 requiere un UNICO proceso Uvicorn: `RunExecutor` (`api/executor.py`)
coordina concurrencia solo dentro de este proceso (un `threading.Lock` +
un registro en memoria de `run_id` activos). Con `--workers > 1`, cada
worker tendria su propio registro desincronizado del resto -- eso
rompe las garantias de "un solo run activo a la vez" y "capacidad
acotada" documentadas en `api/executor.py`. No se agrega Redis/Celery/
Kafka para resolver esto: es una limitacion de despliegue V1 documentada,
no un defecto a resolver con mas infraestructura.

Seguridad HTTP: headers `X-Content-Type-Options`/`Referrer-Policy`/
`X-Frame-Options` se agregan a TODAS las respuestas; una
Content-Security-Policy estricta (sin `unsafe-inline`/`unsafe-eval`, sin
dominios CDN) se agrega UNICAMENTE a `/`, `/ui` y `/ui/*` -- aplicarla
globalmente romperia `/docs`/`/redoc` (Swagger UI/ReDoc cargan sus
propios scripts/estilos de forma incompatible con `script-src 'self'`).
Los 5 exception handlers de mas abajo sirven JSON para `/api/*`/
`/health`/`/openapi.json` (comportamiento identico al de Prompt 13b, sin
cambios) y HTML (`error.html`, nunca traceback/path absoluto/credencial/
prompt/cuerpo LLM) para `/ui/*`."""

from __future__ import annotations

import logging
import secrets
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import SecretStr
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .. import ui
from ..config import Settings, load_settings
from ..contracts.security_config import AuthenticationMode
from ..security.correlation import (
    CORRELATION_HEADER_NAME,
    resolve_correlation_id,
    set_correlation_id,
)
from ..security.fastapi_deps import pending_session
from ..security.identity_resolver import resolve_principal
from ..security.security_config_loader import SecurityConfigOutcome, load_security_config
from ..security.session import sign_session_cookie
from ..ui.governance_actions_router import router as governance_actions_router
from ..ui.presentation import omit_keys, program_name_from_source_file, status_label
from ..ui.router import router as ui_router
from .errors import ApiError, ExecutorAtCapacityError
from .executor import RunExecutor
from .routers.governance import router as governance_router
from .routers.health import router as health_router
from .routers.runs import router as runs_router

logger = logging.getLogger(__name__)

_UI_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self'; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "object-src 'none'"
)


def _is_ui_path(path: str) -> bool:
    return path == "/" or path == "/ui" or path.startswith("/ui/")


def _is_sensitive_ui_path(path: str) -> bool:
    """Paginas de gobierno/ejecucion de un run -- muestran estado
    operativo/identidad y nunca deben cachearse (Fase 15B1 Parte 14),
    a diferencia de `/static/*` (assets inmutables sin datos de run)."""
    return path.startswith("/ui/runs/")


_PERMISSIONS_POLICY = "geolocation=(), microphone=(), camera=(), payment=()"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """`X-Content-Type-Options`/`Referrer-Policy`/`X-Frame-Options`/
    `Permissions-Policy` en todas las respuestas; `Content-Security-
    Policy` estricta solo en `/`, `/ui` y `/ui/*` (ver docstring del
    modulo: aplicarla a `/docs`/`/redoc` los rompe);
    `Cache-Control: no-store` en `/ui/runs/*` (Fase 15B1 Parte 14: paginas
    de gobierno/identidad, nunca cacheables); `Strict-Transport-Security`
    UNICAMENTE cuando `settings.hsts_enabled=true` -- nunca por defecto,
    porque V1 no garantiza que el trafico llegue por HTTPS (eso lo
    decide el reverse proxy del despliegue, ver
    `docs/REVERSE_PROXY_SECURITY_REQUIREMENTS.md`)."""

    def __init__(self, app: object, *, hsts_enabled: bool) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._hsts_enabled = hsts_enabled

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = _PERMISSIONS_POLICY
        if _is_ui_path(request.url.path):
            response.headers["Content-Security-Policy"] = _UI_CSP
        if _is_sensitive_ui_path(request.url.path):
            response.headers["Cache-Control"] = "no-store"
        if self._hsts_enabled:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response


class SessionCookieMiddleware(BaseHTTPMiddleware):
    """Fija la cookie de sesion firmada (Fase 15B1 Parte 5) sobre la
    respuesta FINAL real -- nunca sobre el `Response` que FastAPI
    inyecta en las dependencias, que se descarta en silencio cuando el
    handler retorna su propio `Response`/`TemplateResponse`/
    `RedirectResponse` (ver docstring de `security/fastapi_deps.py`).
    Sin efecto si ningun handler de la request llamo a `get_session`."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        session = pending_session(request)
        if session is not None:
            config = getattr(request.app.state, "security_config", None)
            secret = getattr(request.app.state, "session_secret", None)
            if config is not None and secret is not None:
                signed = sign_session_cookie(session, secret)
                response.set_cookie(
                    key=config.session_cookie_name,
                    value=signed,
                    max_age=config.session_ttl_seconds,
                    secure=config.session_cookie_secure,
                    httponly=config.session_cookie_httponly,
                    samesite=config.session_cookie_samesite,
                )
        return response


_MISCONFIGURED_ALLOWED_PATHS = frozenset({"/health", "/ready"})


def _is_misconfigured_allowed_path(path: str) -> bool:
    return path in _MISCONFIGURED_ALLOWED_PATHS or path.startswith("/static/")


class SecurityMisconfiguredGateMiddleware(BaseHTTPMiddleware):
    """Fail-closed GLOBAL (cierre Fase 15B1, "DISABLED_DEV explicito"):
    cuando `config/security.yaml` esta ausente o es invalido
    (`app.state.security_misconfigured=True`), NINGUNA ruta entrega
    datos salvo `/health` (liveness), `/ready` (readiness, que
    justamente informa el propio estado misconfigured) y `/static/*`
    (assets inmutables, necesarios para renderizar la pagina de error
    con estilo). Esto incluye el pipeline V1 pre-existente (`/ui/runs`,
    `/api/runs`, descarga de artefactos, etc.) que en los otros dos
    estados (DISABLED_DEV explicito, TRUSTED_PROXY_HEADERS) permanece
    sin autenticacion por diseno (ver docstring de `create_app`) --
    aqui la falta de una postura de seguridad conocida es en si misma
    motivo suficiente para no exponer nada, no solo la capa de
    gobierno operativo de Fase 15B1."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if getattr(request.app.state, "security_misconfigured", False) and (
            not _is_misconfigured_allowed_path(request.url.path)
        ):
            return _render_error(
                request,
                status_code=503,
                code="security_misconfigured",
                message="configuracion de seguridad ausente o invalida",
            )
        return await call_next(request)


class CorrelationLoggingMiddleware(BaseHTTPMiddleware):
    """Correlation ID por request (Fase 15B1 Parte 15) -- resuelto UNA
    sola vez aqui (`resolve_correlation_id`), guardado en
    `request.state` para que los handlers lo reutilicen
    (`get_correlation_id`) y devuelto en el header de respuesta.
    Registra UNA linea estructurada por request: `correlation_id`,
    metodo, ruta, status, `principal_id` normalizado (nunca el header
    crudo). NUNCA loguea query string completa, cuerpo de formulario,
    cookies, token CSRF, headers de identidad crudos ni codigo fuente."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        security_config = getattr(request.app.state, "security_config", None)
        correlation_id = (
            resolve_correlation_id(request, security_config)
            if security_config is not None
            else str(uuid.uuid4())
        )
        set_correlation_id(request, correlation_id)

        response = await call_next(request)
        response.headers[CORRELATION_HEADER_NAME] = correlation_id

        principal_id = "-"
        if security_config is not None:
            try:
                principal_id = resolve_principal(request.headers, security_config).principal_id
            except ApiError:
                principal_id = "-"
        logger.info(
            "request completed",
            extra={
                "correlation_id": correlation_id,
                "http_method": request.method,
                "http_path": request.url.path,
                "http_status_code": response.status_code,
                "principal_id": principal_id,
            },
        )
        return response


def _render_error(request: Request, *, status_code: int, code: str, message: str) -> Response:
    if _is_ui_path(request.url.path):
        templates: Jinja2Templates = request.app.state.templates
        return templates.TemplateResponse(
            request,
            "error.html",
            {"status_code": status_code, "code": code, "message": message},
            status_code=status_code,
        )
    return JSONResponse(
        status_code=status_code, content={"error": {"code": code, "message": message}}
    )


def create_app(settings: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.executor = RunExecutor(max_workers=settings.api_max_workers)
        app.state.templates = Jinja2Templates(directory=str(ui.TEMPLATES_DIR))
        app.state.templates.env.filters["status_label"] = status_label
        app.state.templates.env.filters["program_name"] = program_name_from_source_file
        app.state.templates.env.filters["omit_keys"] = omit_keys
        app.state.templates.env.globals["app_version"] = app.version

        # Cierre Fase 15B1 ("DISABLED_DEV explicito"): la AUSENCIA de
        # `config/security.yaml`, o un archivo presente pero invalido,
        # YA NO resuelve en silencio a DISABLED_DEV -- ver docstring de
        # `security_config_loader.py`. `security_misconfigured=True`
        # deja el proceso VIVO (`/health` sigue respondiendo) pero
        # `/ready` falla y ninguna ruta que dependa de identidad/sesion
        # (`security/fastapi_deps.py::_check_not_misconfigured`) entrega
        # datos -- nunca se construye un principal anonimo por defecto.
        load_result = load_security_config(settings.security_config_path)
        app.state.security_misconfigured = load_result.outcome != SecurityConfigOutcome.LOADED
        app.state.security_config_error = load_result.error
        if load_result.outcome == SecurityConfigOutcome.LOADED:
            assert load_result.config is not None
            security_config = load_result.config
            app.state.security_config = security_config
            if security_config.authentication_mode == AuthenticationMode.TRUSTED_PROXY_HEADERS:
                if settings.session_secret is None:
                    raise RuntimeError(
                        "ALTAMIRA_SESSION_SECRET es obligatorio cuando authentication_mode="
                        "TRUSTED_PROXY_HEADERS (config/security.yaml) -- nunca se genera un "
                        "valor por defecto fuera de DISABLED_DEV"
                    )
                app.state.session_secret = settings.session_secret
            else:
                # DISABLED_DEV EXPLICITO (el YAML lo declara): clave
                # efimera generada en memoria SOLO para firmar la
                # cookie de sesion (CSRF/challenge) de este proceso --
                # nunca versionada, nunca impresa, nunca persistida;
                # las sesiones se invalidan en cada reinicio.
                app.state.session_secret = settings.session_secret or SecretStr(
                    secrets.token_hex(32)
                )
        else:
            app.state.security_config = None
            # Ninguna ruta protegida llega a leer `session_secret` en
            # este estado (`_check_not_misconfigured` corta antes), pero
            # se deja un valor definido y nunca `None` sin inicializar
            # para que `SessionCookieMiddleware` (que ya tolera `None`
            # explicitamente) nunca dependa de un atributo ausente.
            app.state.session_secret = None
        try:
            yield
        finally:
            app.state.executor.shutdown(wait=True)

    app = FastAPI(
        title="Altamira Rule Extractor API",
        description=(
            "API JSON de ejecucion y consulta del pipeline Altamira, descarga de "
            "artefactos validados (artifacts/10-rules/), y UI HTML server-rendered "
            "same-origin (Jinja2/HTMX minimo, sin autenticacion en V1)."
        ),
        version="1.0",
        debug=False,
        lifespan=lifespan,
    )

    # Agregado PRIMERO (mas interno): las respuestas 503 que emite
    # todavia pasan por SecurityHeadersMiddleware/SessionCookieMiddleware/
    # CorrelationLoggingMiddleware/TrustedHostMiddleware (mas externos),
    # que se aplican igual a un request bloqueado por misconfiguracion.
    app.add_middleware(SecurityMisconfiguredGateMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, hsts_enabled=settings.hsts_enabled)
    app.add_middleware(SessionCookieMiddleware)
    app.add_middleware(CorrelationLoggingMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)

    app.mount("/static", StaticFiles(directory=str(ui.STATIC_DIR)), name="ui_static")

    app.include_router(health_router)
    app.include_router(runs_router)
    app.include_router(governance_router)
    app.include_router(governance_actions_router)
    app.include_router(ui_router)

    @app.get("/", include_in_schema=False)
    def _root() -> RedirectResponse:
        return RedirectResponse(app.url_path_for("ui_runs"), status_code=303)

    @app.exception_handler(ExecutorAtCapacityError)
    async def _handle_executor_at_capacity(
        request: Request, exc: ExecutorAtCapacityError
    ) -> Response:
        if _is_ui_path(request.url.path):
            return _render_error(
                request, status_code=exc.status_code, code=exc.code, message=exc.message
            )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {"code": exc.code, "message": exc.message},
                "run_id": exc.run_id,
            },
        )

    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError) -> Response:
        return _render_error(
            request, status_code=exc.status_code, code=exc.code, message=exc.message
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(request: Request, exc: RequestValidationError) -> Response:
        return _render_error(
            request,
            status_code=422,
            code="invalid_request",
            message="parametros de solicitud invalidos",
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> Response:
        return _render_error(
            request, status_code=exc.status_code, code="http_error", message=str(exc.detail)
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(request: Request, exc: Exception) -> Response:
        logger.exception("error interno no controlado en la API")
        return _render_error(
            request, status_code=500, code="internal_error", message="error interno"
        )

    return app


def app_factory() -> FastAPI:
    """Punto de entrada para `uvicorn --factory` (Prompt 14a): construye
    `Settings` recien cuando Uvicorn invoca esta funcion, nunca antes
    (nada de esto corre a import-time del modulo). El contenedor arranca
    con exactamente 1 worker (`--workers 1`): `RunExecutor` coordina
    concurrencia solo dentro de un unico proceso (ver docstring de
    `create_app` mas arriba) -- con mas de un worker cada proceso
    tendria su propio registro de runs activos desincronizado del
    resto."""
    return create_app(load_settings())
