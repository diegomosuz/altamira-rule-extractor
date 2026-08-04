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
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from .. import ui
from ..config import Settings, load_settings
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


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """`X-Content-Type-Options`/`Referrer-Policy`/`X-Frame-Options` en
    todas las respuestas; `Content-Security-Policy` estricta solo en
    `/`, `/ui` y `/ui/*` (ver docstring del modulo: aplicarla a `/docs`/
    `/redoc` los rompe)."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if _is_ui_path(request.url.path):
            response.headers["Content-Security-Policy"] = _UI_CSP
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

    app.add_middleware(SecurityHeadersMiddleware)

    app.mount("/static", StaticFiles(directory=str(ui.STATIC_DIR)), name="ui_static")

    app.include_router(health_router)
    app.include_router(runs_router)
    app.include_router(governance_router)
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
