"""GET /health (Prompt 13b): liveness pura. GET /ready (cierre Fase
15B1, "DISABLED_DEV explicito"; extendido Fase 15B2-B Seccion 11):
readiness -- distinta de liveness por diseno (semantica tipo Kubernetes
liveness/readiness probe).

`/ready` es un chequeo de PROCESO acotado: config de seguridad, JAR del
parser, data root, executor local -- nunca Neo4j/proveedor LLM/
ground-truth (esos son recursos externos o por-request, cuya ausencia
no debe bloquear el arranque). Ningun chequeo muta estado ni expone un
path absoluto/hostname/credencial/texto de excepcion."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from ...config import Settings
from ...contracts.observability import ComponentStatus
from ..deps import get_settings
from ..schemas import HealthResponse, ReadinessCheckResult, ReadinessResponse

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


def _check_security_configuration(request: Request) -> ReadinessCheckResult:
    misconfigured = bool(getattr(request.app.state, "security_misconfigured", False))
    if misconfigured:
        return ReadinessCheckResult(
            component_id="security_configuration",
            status=ComponentStatus.NOT_READY,
            reason_code="security_misconfigured",
        )
    return ReadinessCheckResult(
        component_id="security_configuration", status=ComponentStatus.READY, reason_code=None
    )


def _check_parser_jar(settings: Settings) -> ReadinessCheckResult:
    path = settings.parser_jar_path
    if path.is_symlink() or not path.is_file():
        return ReadinessCheckResult(
            component_id="parser_jar",
            status=ComponentStatus.NOT_READY,
            reason_code="parser_jar_missing",
        )
    return ReadinessCheckResult(
        component_id="parser_jar", status=ComponentStatus.READY, reason_code=None
    )


def _check_data_root(settings: Settings) -> ReadinessCheckResult:
    """Basado en `runs_dir` (nunca `data_dir`, que muchos tests dejan en
    su default relativo sin crear nunca): existe como directorio -> OK;
    todavia no existe pero su padre si -> OK (instalacion nueva, cero
    runs todavia -- nunca se exige crear el directorio); ninguno de los
    dos -> NOT_READY."""
    runs_dir = settings.runs_dir
    if runs_dir.is_symlink():
        return ReadinessCheckResult(
            component_id="data_root",
            status=ComponentStatus.NOT_READY,
            reason_code="data_root_unavailable",
        )
    if runs_dir.is_dir() or runs_dir.parent.is_dir():
        return ReadinessCheckResult(
            component_id="data_root", status=ComponentStatus.READY, reason_code=None
        )
    return ReadinessCheckResult(
        component_id="data_root",
        status=ComponentStatus.NOT_READY,
        reason_code="data_root_unavailable",
    )


def _check_executor(request: Request) -> ReadinessCheckResult:
    if getattr(request.app.state, "executor", None) is None:
        return ReadinessCheckResult(
            component_id="executor",
            status=ComponentStatus.NOT_READY,
            reason_code="executor_unavailable",
        )
    return ReadinessCheckResult(
        component_id="executor", status=ComponentStatus.READY, reason_code=None
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness del proceso API",
    description=(
        "200 con ready=true si TODOS los chequeos acotados de proceso pasan: config de "
        "seguridad cargada, JAR del parser presente, data root existente o creable, "
        "executor local inicializado. 503 con ready=false y el primer reason_code que "
        "fallo (orden de prioridad fijo) en caso contrario -- el proceso sigue vivo "
        "(/health sigue en 200). Nunca depende de Neo4j, de un proveedor LLM, de que "
        "existan runs, ni de metricas/ground-truth habilitados."
    ),
)
def get_readiness(
    request: Request, response: Response, settings: Settings = Depends(get_settings)
) -> ReadinessResponse:
    checks = [
        _check_security_configuration(request),
        _check_parser_jar(settings),
        _check_data_root(settings),
        _check_executor(request),
    ]
    failing = next((c for c in checks if c.status != ComponentStatus.READY), None)
    if failing is not None:
        response.status_code = 503
        return ReadinessResponse(ready=False, reason=failing.reason_code, checks=checks)
    return ReadinessResponse(ready=True, reason=None, checks=checks)
