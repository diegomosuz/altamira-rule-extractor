"""Modelos publicos de la API (Prompt 13b): delgados, sin campos
inventados, sin exponer provenance interna innecesaria.

`pydantic.BaseModel` liso (no `AltamiraBaseModel`): estos modelos son
forma de respuesta HTTP, no artefactos persistidos con
`to_stable_json()`/`extra="forbid"` -- esa disciplina es de
`contracts/`, no de la capa HTTP."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from ..contracts.enums import (
    CandidateStatus,
    GuardrailVerdict,
    PipelineStage,
    Severity,
    StageStatus,
)
from ..contracts.observability import ComponentStatus
from ..contracts.rule_draft import RuleDraft


class HealthResponse(BaseModel):
    status: Literal["ok"]


# Codigos de motivo cerrados de `/ready` (Fase 15B2-B, Seccion 11):
# `security_misconfigured` es el original del cierre Fase 15B1, los
# demas son bounded process-readiness checks nuevos -- nunca Neo4j/LLM/
# ausencia de runs (fallos por-request, no de arranque del proceso).
ReadinessReasonCode = Literal[
    "security_misconfigured",
    "parser_jar_missing",
    "data_root_unavailable",
    "executor_unavailable",
]


class ReadinessCheckResult(BaseModel):
    """Detalle de un chequeo individual de `/ready` -- nunca expone un
    path absoluto, hostname, credencial ni texto de excepcion."""

    component_id: str
    status: ComponentStatus
    reason_code: ReadinessReasonCode | None


class ReadinessResponse(BaseModel):
    """`ready=false` por el primer chequeo NOT_READY, en orden de
    prioridad (cierre Fase 15B1 + extension Fase 15B2-B): config de
    seguridad, JAR del parser, data root, executor -- nunca por Neo4j/
    LLM/runs_dir vacio (esos son fallos por-request, no de arranque).
    `reason` es `None` cuando `ready=true`; `checks` siempre lista el
    detalle completo de los 4 chequeos, con o sin fallo."""

    ready: bool
    reason: ReadinessReasonCode | None
    checks: list[ReadinessCheckResult]


class StageExecutionView(BaseModel):
    """Proyeccion 1:1 de `StageExecution` (`contracts/run_state.py`):
    todos sus campos ya son datos pensados para observabilidad, ninguno
    es provenance interna."""

    stage: PipelineStage
    status: StageStatus
    started_at: datetime | None
    finished_at: datetime | None
    duration_seconds: float | None
    warnings: list[str]
    error: str | None


class RunSummary(BaseModel):
    """Campos demostrables de `RunState` (`contracts/run_state.py`):
    `run_id`, `current_stage`, `source_package_hash`, `created_at` y
    `updated_at` existen tal cual en el contrato -- ninguno es
    inventado."""

    run_id: str
    current_stage: PipelineStage
    source_package_hash: str | None
    created_at: datetime
    updated_at: datetime


class RunDetail(RunSummary):
    package_filename: str
    stages: list[StageExecutionView]


class RunAcceptedResponse(BaseModel):
    """Respuesta de `POST /api/runs` y `POST /api/runs/{run_id}/resume`.

    `status` es un literal fijo ("accepted"): indica que la solicitud
    HTTP fue aceptada y el resto de la ejecucion quedo programada en el
    executor local -- no intenta reflejar el `StageStatus` de una
    `StageExecution` puntual, porque en `resume` el `current_stage` en
    el momento de aceptar puede ser `FAILED` (ese es justamente el
    motivo de reanudar), que nunca tiene una `StageExecution` propia
    (ver `runner.py`: `FAILED` es un centinela puro de `current_stage`).
    """

    run_id: str
    current_stage: PipelineStage
    status: Literal["accepted"]
    status_url: str


class RunListResponse(BaseModel):
    runs: list[RunSummary]
    total: int
    limit: int
    offset: int


class CandidateSummary(BaseModel):
    """Subconjunto de `RuleCandidate` (`contracts/candidate.py`)
    apropiado para un listado: se omiten los campos puramente tecnicos
    del detector (`detector_id`/`detector_version`/`detector_score`) y de
    ubicacion fisica (`line_start`/`source_file`/`source_package_hash`,
    disponibles via `context`/`rule` si hicieran falta). Ningun campo
    aqui es inventado -- todos existen en `RuleCandidate`."""

    candidate_id: str
    paragraph_id: str
    paragraph_name: str
    decision_id: str
    condition: str
    outcome_code: str | None
    rule_type: str | None
    status: CandidateStatus


class CandidatesResponse(BaseModel):
    candidates: list[CandidateSummary]


class GuardrailViolationView(BaseModel):
    """Proyeccion sanitizada de `GuardrailViolation`
    (`contracts/guardrail.py`) con sus campos reales: `violation_id`,
    `rule`, `field`, `message`, `severity`. Modelo publico SEPARADO
    (nunca se reutiliza `GuardrailViolation` directamente como contrato
    HTTP) para no acoplar la API a cambios futuros del contrato
    interno."""

    violation_id: str
    rule: str
    field: str | None
    message: str
    severity: Severity


class GuardrailView(BaseModel):
    """Modelo publico delgado sobre `GuardrailCandidateArtifact`
    (`contracts/guardrail_candidate.py` + `contracts/guardrail.py`).

    `warnings` proviene de `GuardrailCandidateArtifact.warnings` --
    `GuardrailReport` (el otro candidato posible) NO tiene un campo
    `warnings` en el contrato real (confirmado leyendo
    `contracts/guardrail.py`), asi que no hay ambiguedad que resolver:
    solo existe una fuente posible. Hoy ese campo siempre viene `[]` en
    la practica (`guardrails_applied_stage.py` nunca lo puebla), pero se
    expone igual porque es el campo real del contrato.

    Explicitamente EXCLUIDOS de este modelo (nunca presentes en ningun
    campo, ni anidados): `repair_history` completo, `response_hash`,
    `produced_rule_draft_hash`, `failure_code`/`failure_summary`,
    `initial_rule_draft_hash`, `final_rule_draft_hash`, `context_hash`,
    `source_package_hash`, `provider`/`model`, hashes de templates y
    cualquier contenido del draft inicial de `08-rule-drafts/`.
    """

    verdict: GuardrailVerdict
    violations: list[GuardrailViolationView]
    warnings: list[str]
    repair_attempts_used: int


class RuleResponse(BaseModel):
    """`GET /api/runs/{run_id}/candidates/{candidate_id}/rule`: unico
    endpoint para "regla" y "guardrail" (no existe un endpoint separado
    `/guardrail`). La pantalla futura de regla consumira
    `final_rule_draft`; la pantalla futura de guardrail consumira
    `guardrail`."""

    candidate_id: str
    final_rule_draft: RuleDraft
    guardrail: GuardrailView


# Codigos de motivo cerrados de `/api/operations/component-diagnostics`
# (Fase 15B2-B, Seccion 12) -- uno por componente, nunca texto libre de
# excepcion/path/hostname/credencial.
ComponentReasonCode = Literal[
    "security_config_loaded",
    "security_config_missing",
    "security_config_invalid",
    "parser_jar_present",
    "parser_jar_missing",
    "data_root_present",
    "data_root_missing",
    "executor_initialized",
    "executor_unavailable",
    "neo4j_configuration_valid",
    "neo4j_configuration_invalid",
    "neo4j_reachable",
    "neo4j_unreachable",
    "neo4j_authentication_failed",
    "neo4j_timeout",
    "neo4j_not_attempted",
    "metrics_enabled",
    "metrics_disabled",
    "logging_configured",
    "logging_not_configured",
    "functional_validation_config_present",
    "functional_validation_config_missing",
    "release_readiness_config_present",
    "release_readiness_config_missing",
    "provider_disabled",
    "provider_configured",
    "provider_misconfigured",
]


class ComponentDiagnostic(BaseModel):
    """Un componente del diagnostico operativo -- NUNCA expone hostname,
    URI, usuario, token, path absoluto ni texto de excepcion; solo un
    `reason_code` cerrado. `required_for_*` documenta el impacto de un
    estado distinto de `READY`, no el estado en si."""

    component_id: str
    status: ComponentStatus
    reason_code: ComponentReasonCode
    checked_at_utc: datetime
    required_for_ingestion: bool
    required_for_query: bool
    required_for_operational_actions: bool


class ComponentDiagnosticsResponse(BaseModel):
    generated_at_utc: datetime
    components: list[ComponentDiagnostic]
