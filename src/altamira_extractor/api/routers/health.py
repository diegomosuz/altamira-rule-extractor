"""GET /health (Prompt 13b): liveness pura."""

from __future__ import annotations

from fastapi import APIRouter

from ..schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness del proceso API",
    description=(
        "Liveness pura: 200 mientras el proceso pueda responder. No consulta Neo4j "
        "ni ningun proveedor LLM (una dependencia externa caida no debe convertir esto "
        "en 'not alive'), no ejecuta el pipeline, y no depende de que existan runs en "
        "runs_dir."
    ),
)
def get_health() -> HealthResponse:
    return HealthResponse(status="ok")
