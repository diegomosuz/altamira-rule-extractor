"""GET /health (Prompt 13b): liveness pura. GET /ready (cierre Fase
15B1, "DISABLED_DEV explicito"): readiness -- distinta de liveness por
diseno (semantica tipo Kubernetes liveness/readiness probe)."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from ..schemas import HealthResponse, ReadinessResponse

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness del proceso API",
    description=(
        "Liveness pura: 200 mientras el proceso pueda responder. No consulta Neo4j "
        "ni ningun proveedor LLM (una dependencia externa caida no debe convertir esto "
        "en 'not alive'), no ejecuta el pipeline, no depende de que existan runs en "
        "runs_dir, y no depende de que `config/security.yaml` sea valido (ver /ready "
        "para eso)."
    ),
)
def get_health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness del proceso API",
    description=(
        "200 con ready=true si `config/security.yaml` se cargo correctamente "
        "(TRUSTED_PROXY_HEADERS o DISABLED_DEV, ambos EXPLICITOS); 503 con "
        "ready=false si esta ausente o es invalido -- el proceso sigue vivo "
        "(/health sigue en 200) pero ninguna ruta que dependa de identidad/sesion "
        "entrega datos mientras tanto."
    ),
)
def get_readiness(request: Request, response: Response) -> ReadinessResponse:
    misconfigured = bool(getattr(request.app.state, "security_misconfigured", False))
    if misconfigured:
        response.status_code = 503
        return ReadinessResponse(ready=False, reason="security_misconfigured")
    return ReadinessResponse(ready=True, reason=None)
