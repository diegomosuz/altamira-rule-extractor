"""Factoria de la API HTTP (Prompt 13b).

`create_app(settings)`: sin `app = FastAPI()` a nivel de modulo -- todo
objeto con estado (el `Settings` recibido, el `RunExecutor` local) vive
en `app.state`, poblado dentro del `lifespan`, y se inyecta via
`Depends` (`api/deps.py`). No hay ningun global mutable de modulo.

Alcance: API JSON + descarga de artefactos, sin HTML server-rendered
(Jinja2/HTMX quedan para Prompt 13d) y sin extension de Typer (Prompt
13c). No se agrega `CORSMiddleware`: la UI futura sera same-origin (la
sirve este mismo `app`), asi que CORS no aplica. No se agrega
autenticacion (fuera de alcance V1, no documentada) -- el acceso de red
a este servicio debe restringirse externamente (firewall/red interna)
hasta que exista un mecanismo de autenticacion.

V1 requiere un UNICO proceso Uvicorn: `RunExecutor` (`api/executor.py`)
coordina concurrencia solo dentro de este proceso (un `threading.Lock` +
un registro en memoria de `run_id` activos). Con `--workers > 1`, cada
worker tendria su propio registro desincronizado del resto -- eso
rompe las garantias de "un solo run activo a la vez" y "capacidad
acotada" documentadas en `api/executor.py`. No se agrega Redis/Celery/
Kafka para resolver esto: es una limitacion de despliegue V1 documentada,
no un defecto a resolver con mas infraestructura."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..config import Settings
from .errors import ApiError, ExecutorAtCapacityError
from .executor import RunExecutor
from .routers.health import router as health_router
from .routers.runs import router as runs_router

logger = logging.getLogger(__name__)


def create_app(settings: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.executor = RunExecutor(max_workers=settings.api_max_workers)
        try:
            yield
        finally:
            app.state.executor.shutdown(wait=True)

    app = FastAPI(
        title="Altamira Rule Extractor API",
        description=(
            "API JSON de ejecucion y consulta del pipeline Altamira, y descarga de "
            "artefactos validados (artifacts/10-rules/). Sin UI server-rendered."
        ),
        version="1.0",
        debug=False,
        lifespan=lifespan,
    )

    app.include_router(health_router)
    app.include_router(runs_router)

    @app.exception_handler(ExecutorAtCapacityError)
    async def _handle_executor_at_capacity(
        request: Request, exc: ExecutorAtCapacityError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {"code": exc.code, "message": exc.message},
                "run_id": exc.run_id,
            },
        )

    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": "parametros de solicitud invalidos",
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": "http_error", "message": str(exc.detail)}},
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("error interno no controlado en la API")
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error", "message": "error interno"}},
        )

    return app
