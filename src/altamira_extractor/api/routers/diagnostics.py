"""GET /api/operations/component-diagnostics (Fase 15B2-B, Seccion 12).

Requiere `OperationalPermission.VIEW_SECURITY_STATUS` (OPERATOR/ADMIN
-- ver `contracts/security_config.py`, definido desde Fase 15B1 pero
nunca antes verificado en ningun endpoint). Reporta el estado de 11
componentes internos SIN exponer hostname/URI/usuario/token/path
absoluto/texto de excepcion: unicamente `component_id`/`status`/
`reason_code` cerrado/`checked_at_utc`. La conectividad Neo4j se
verifica con un timeout corto y de solo lectura (`RETURN 1`
implicito via `verify_connectivity`) -- el proveedor LLM NUNCA se
conecta, solo se valida que su configuracion este completa
(`resolve_llm_profile`, ya usado por el pipeline real, nunca duplicado
aqui)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request

from ...config import Settings
from ...contracts.observability import ComponentStatus
from ...contracts.security_config import OperationalPermission
from ...contracts.security_identity import AuthenticatedPrincipal
from ...pipeline.errors import (
    LlmConfigurationError,
    Neo4jAuthenticationError,
    Neo4jConfigurationError,
    Neo4jTimeoutError,
    Neo4jUnavailableError,
)
from ...pipeline.llm_client import resolve_llm_profile
from ...pipeline.neo4j_repository import Neo4jRepository
from ...security.fastapi_deps import get_principal
from ..deps import get_settings
from ..errors import ForbiddenError
from ..schemas import ComponentDiagnostic, ComponentDiagnosticsResponse
from .health import (
    _check_data_root,
    _check_executor,
    _check_parser_jar,
    _check_security_configuration,
)

router = APIRouter(prefix="/api/operations", tags=["operations"])

# Timeout corto y deliberado, distinto del timeout de produccion del
# pipeline (`settings.neo4j_connection_timeout_seconds`, tipicamente
# 30s): este endpoint es un handler HTTP sincrono, nunca debe bloquear
# una request de diagnostico por mas de unos segundos.
_DIAGNOSTICS_NEO4J_TIMEOUT_SECONDS = 1.0


def _require_view_security_status(principal: AuthenticatedPrincipal) -> None:
    if not principal.has_permission(OperationalPermission.VIEW_SECURITY_STATUS):
        raise ForbiddenError("permiso insuficiente")


def _diagnostic(
    *,
    component_id: str,
    status: ComponentStatus,
    reason_code: str,
    required_for_ingestion: bool,
    required_for_query: bool,
    required_for_operational_actions: bool,
) -> ComponentDiagnostic:
    return ComponentDiagnostic(
        component_id=component_id,
        status=status,
        reason_code=reason_code,  # type: ignore[arg-type]
        checked_at_utc=datetime.now(UTC),
        required_for_ingestion=required_for_ingestion,
        required_for_query=required_for_query,
        required_for_operational_actions=required_for_operational_actions,
    )


def _security_configuration_diagnostic(request: Request) -> ComponentDiagnostic:
    check = _check_security_configuration(request)
    reason_code = (
        "security_config_loaded"
        if check.status == ComponentStatus.READY
        else "security_config_missing"
    )
    outcome = getattr(request.app.state, "security_config_error", None)
    if check.status != ComponentStatus.READY and outcome is not None:
        reason_code = "security_config_invalid"
    return _diagnostic(
        component_id="security_configuration",
        status=check.status,
        reason_code=reason_code,
        required_for_ingestion=False,
        required_for_query=False,
        required_for_operational_actions=True,
    )


def _parser_jar_diagnostic(settings: Settings) -> ComponentDiagnostic:
    check = _check_parser_jar(settings)
    reason_code = (
        "parser_jar_present" if check.status == ComponentStatus.READY else "parser_jar_missing"
    )
    return _diagnostic(
        component_id="parser_jar",
        status=check.status,
        reason_code=reason_code,
        required_for_ingestion=True,
        required_for_query=False,
        required_for_operational_actions=False,
    )


def _data_root_diagnostic(settings: Settings) -> ComponentDiagnostic:
    check = _check_data_root(settings)
    reason_code = (
        "data_root_present" if check.status == ComponentStatus.READY else "data_root_missing"
    )
    return _diagnostic(
        component_id="data_root",
        status=check.status,
        reason_code=reason_code,
        required_for_ingestion=True,
        required_for_query=True,
        required_for_operational_actions=True,
    )


def _executor_diagnostic(request: Request) -> ComponentDiagnostic:
    check = _check_executor(request)
    reason_code = (
        "executor_initialized" if check.status == ComponentStatus.READY else "executor_unavailable"
    )
    return _diagnostic(
        component_id="executor",
        status=check.status,
        reason_code=reason_code,
        required_for_ingestion=True,
        required_for_query=False,
        required_for_operational_actions=False,
    )


def _neo4j_configuration_diagnostic(settings: Settings) -> tuple[ComponentDiagnostic, bool]:
    """Retorna tambien si vale la pena intentar conectividad (config
    valida) -- una config invalida nunca debe intentar abrir un socket."""
    try:
        Neo4jRepository.connect(
            settings.model_copy(
                update={"neo4j_connection_timeout_seconds": _DIAGNOSTICS_NEO4J_TIMEOUT_SECONDS}
            )
        ).close()
        valid = True
    except Neo4jConfigurationError:
        valid = False
    except Exception:
        # El driver pudo construirse (config con forma valida) aunque el
        # servidor no responda todavia -- eso es un problema de
        # CONECTIVIDAD, no de configuracion.
        valid = True
    return (
        _diagnostic(
            component_id="neo4j_configuration",
            status=ComponentStatus.READY if valid else ComponentStatus.NOT_READY,
            reason_code="neo4j_configuration_valid" if valid else "neo4j_configuration_invalid",
            required_for_ingestion=True,
            required_for_query=False,
            required_for_operational_actions=False,
        ),
        valid,
    )


def _neo4j_connectivity_diagnostic(
    settings: Settings, *, configuration_valid: bool
) -> ComponentDiagnostic:
    if not configuration_valid:
        return _diagnostic(
            component_id="neo4j_connectivity",
            status=ComponentStatus.NOT_APPLICABLE,
            reason_code="neo4j_not_attempted",
            required_for_ingestion=True,
            required_for_query=False,
            required_for_operational_actions=False,
        )
    short_timeout_settings = settings.model_copy(
        update={"neo4j_connection_timeout_seconds": _DIAGNOSTICS_NEO4J_TIMEOUT_SECONDS}
    )
    try:
        repository = Neo4jRepository.connect(short_timeout_settings)
        try:
            repository.verify_connectivity()
        finally:
            repository.close()
        status, reason_code = ComponentStatus.READY, "neo4j_reachable"
    except Neo4jAuthenticationError:
        status, reason_code = ComponentStatus.NOT_READY, "neo4j_authentication_failed"
    except Neo4jTimeoutError:
        status, reason_code = ComponentStatus.DEGRADED, "neo4j_timeout"
    except (Neo4jUnavailableError, Neo4jConfigurationError):
        status, reason_code = ComponentStatus.NOT_READY, "neo4j_unreachable"
    except Exception:
        status, reason_code = ComponentStatus.NOT_READY, "neo4j_unreachable"
    return _diagnostic(
        component_id="neo4j_connectivity",
        status=status,
        reason_code=reason_code,
        required_for_ingestion=True,
        required_for_query=False,
        required_for_operational_actions=False,
    )


def _metrics_diagnostic(request: Request) -> ComponentDiagnostic:
    observability_config = getattr(request.app.state, "observability_config", None)
    enabled = (
        observability_config is not None and observability_config.metrics.mode.value == "ENABLED"
    )
    return _diagnostic(
        component_id="metrics",
        status=ComponentStatus.READY if enabled else ComponentStatus.NOT_APPLICABLE,
        reason_code="metrics_enabled" if enabled else "metrics_disabled",
        required_for_ingestion=False,
        required_for_query=False,
        required_for_operational_actions=False,
    )


def _logging_diagnostic() -> ComponentDiagnostic:
    configured = bool(logging.getLogger("altamira_extractor").handlers)
    return _diagnostic(
        component_id="logging",
        status=ComponentStatus.READY if configured else ComponentStatus.NOT_READY,
        reason_code="logging_configured" if configured else "logging_not_configured",
        required_for_ingestion=False,
        required_for_query=False,
        required_for_operational_actions=False,
    )


def _functional_validation_configuration_diagnostic(settings: Settings) -> ComponentDiagnostic:
    present = settings.ground_truth_path.is_file() and not settings.ground_truth_path.is_symlink()
    return _diagnostic(
        component_id="functional_validation_configuration",
        status=ComponentStatus.READY if present else ComponentStatus.NOT_APPLICABLE,
        reason_code=(
            "functional_validation_config_present"
            if present
            else "functional_validation_config_missing"
        ),
        required_for_ingestion=False,
        required_for_query=False,
        required_for_operational_actions=False,
    )


def _release_readiness_configuration_diagnostic(settings: Settings) -> ComponentDiagnostic:
    present = (
        settings.release_readiness_policy_path.is_file()
        and not settings.release_readiness_policy_path.is_symlink()
    )
    return _diagnostic(
        component_id="release_readiness_configuration",
        status=ComponentStatus.READY if present else ComponentStatus.NOT_APPLICABLE,
        reason_code=(
            "release_readiness_config_present" if present else "release_readiness_config_missing"
        ),
        required_for_ingestion=False,
        required_for_query=False,
        required_for_operational_actions=False,
    )


def _provider_configuration_diagnostic(settings: Settings) -> ComponentDiagnostic:
    """NUNCA se conecta al proveedor -- unicamente valida que la
    configuracion (nombre de proveedor + credenciales + modelo) este
    completa, reutilizando `resolve_llm_profile` (el mismo validador
    que usa el pipeline real)."""
    if not settings.llm_provider:
        status, reason_code = ComponentStatus.NOT_APPLICABLE, "provider_disabled"
    else:
        try:
            resolve_llm_profile(settings)
            status, reason_code = ComponentStatus.READY, "provider_configured"
        except LlmConfigurationError:
            status, reason_code = ComponentStatus.NOT_READY, "provider_misconfigured"
    return _diagnostic(
        component_id="provider_configuration",
        status=status,
        reason_code=reason_code,
        required_for_ingestion=False,
        required_for_query=False,
        required_for_operational_actions=False,
    )


@router.get(
    "/component-diagnostics",
    response_model=ComponentDiagnosticsResponse,
    summary="Diagnostico de componentes internos (OPERATOR/ADMIN)",
)
def get_component_diagnostics(
    request: Request,
    settings: Settings = Depends(get_settings),
    principal: AuthenticatedPrincipal = Depends(get_principal),
) -> ComponentDiagnosticsResponse:
    _require_view_security_status(principal)

    neo4j_configuration, neo4j_configuration_valid = _neo4j_configuration_diagnostic(settings)
    components = [
        _security_configuration_diagnostic(request),
        _parser_jar_diagnostic(settings),
        _data_root_diagnostic(settings),
        _executor_diagnostic(request),
        neo4j_configuration,
        _neo4j_connectivity_diagnostic(settings, configuration_valid=neo4j_configuration_valid),
        _metrics_diagnostic(request),
        _logging_diagnostic(),
        _functional_validation_configuration_diagnostic(settings),
        _release_readiness_configuration_diagnostic(settings),
        _provider_configuration_diagnostic(settings),
    ]
    return ComponentDiagnosticsResponse(generated_at_utc=datetime.now(UTC), components=components)


__all__ = ["router"]
