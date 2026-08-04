"""Contratos del read model de gobierno operativo (Fase 15A,
`feat/operational-governance-ui`).

Este modulo describe EXCLUSIVAMENTE una vista de solo lectura sobre el
estado de activacion de Fase 14B (`activation/active.json`,
`activation/generations/*`, `activation/events/*`) y, cuando existe, la
evaluacion de Fase 14A (`diagnostics/unified-activation-evaluation.json`).
Ningun campo de este modulo se persiste: `OperationalGovernanceOverview`
se construye en memoria por `pipeline/operational_governance_reader.py`
en cada peticion GET, nunca se escribe a disco.

Principios (Fase 15A Parte 2, aplicados en todo este modulo):
- sin timestamps nuevos (ningun campo de fecha/hora existe aqui);
- sin rutas absolutas (`relative_path` reutiliza `check_relative_path_
  is_safe` de Fase 14B);
- sin identidad de usuario inventada (`GovernanceEventSummary` audita
  TRANSICIONES, nunca "quien" las hizo mas alla de lo que la cadena de
  eventos de Fase 14B ya registra -- ningun campo `user`/`actor` existe);
- todos los arrays son deterministicos y ordenados (validadores mas
  abajo)."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, Sha256Hex
from .unified_activation_materialization import (
    MaterializedActivationLane,
    MaterializedGenerationKind,
    check_relative_path_is_safe,
)


def _ordered_unique(values: list[str]) -> list[str]:
    return sorted(set(values))


class OperationalGovernanceStatus(StrEnum):
    """Estado general de gobierno de UN run -- resumen de mas alto
    nivel mostrado en la cabecera de la UI."""

    NOT_INITIALIZED = "NOT_INITIALIZED"
    HEALTHY_V1 = "HEALTHY_V1"
    HEALTHY_UNIFIED = "HEALTHY_UNIFIED"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"


class GovernanceIntegrityStatus(StrEnum):
    """Integridad estructural de un manifiesto/puntero -- nunca se
    repara aqui, solo se reporta."""

    VALID = "VALID"
    INVALID = "INVALID"
    MISSING = "MISSING"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class GovernanceEventChainStatus(StrEnum):
    """Resultado de reconstruir la cadena confirmada desde
    `active.json.latest_event_id` (Fase 15A Parte 5)."""

    VALID = "VALID"
    EMPTY = "EMPTY"
    BROKEN = "BROKEN"
    CYCLIC = "CYCLIC"


class GovernanceArtifactStatus(StrEnum):
    """Estado de resolucion de UN `logical_name` desde el lane activo,
    via el router read-only (`unified_active_lane_router.resolve_
    active_artifact`) -- NUNCA `resolve_with_fallback`. `MISSING` (el
    manifiesto referencia un archivo que no existe en disco) y
    `CORRUPT` (el archivo existe pero su hash/tamano no reconcilia) son
    subclasificaciones del `BLOCKED` del router, calculadas por el
    reader; `BLOCKED` puro se usa cuando ni siquiera se pudo determinar
    cual de las dos aplica (p. ej. el manifiesto de la generacion activa
    esta el mismo corrupto)."""

    AVAILABLE = "AVAILABLE"
    NOT_AVAILABLE_IN_LANE = "NOT_AVAILABLE_IN_LANE"
    MISSING = "MISSING"
    CORRUPT = "CORRUPT"
    BLOCKED = "BLOCKED"


class GovernanceGenerationReachability(StrEnum):
    """Clasificacion de alcanzabilidad de UNA generacion (Fase 15A
    Parte 5): `ACTIVE`/`PREVIOUS`/`FALLBACK` provienen directamente de
    `active.json`; `HISTORICAL` es una generacion referenciada por
    algun evento CONFIRMADO de la cadena pero que ya no es
    active/previous/fallback; `ORPHAN` es una generacion persistida en
    disco sin ninguna referencia (ni del puntero, ni de ningun evento
    confirmado) -- nunca se elimina, solo se reporta."""

    ACTIVE = "ACTIVE"
    PREVIOUS = "PREVIOUS"
    FALLBACK = "FALLBACK"
    HISTORICAL = "HISTORICAL"
    ORPHAN = "ORPHAN"


class GovernanceIssueSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    BLOCKING = "BLOCKING"


class GovernanceIssueCode(StrEnum):
    """Catalogo CERRADO de codigos de issue de gobierno (Fase 15A Parte
    3) -- cada uno tiene un mensaje seguro asociado en el reader, nunca
    texto libre con datos internos."""

    ACTIVATION_NOT_INITIALIZED = "ACTIVATION_NOT_INITIALIZED"
    ACTIVE_POINTER_INVALID = "ACTIVE_POINTER_INVALID"
    ACTIVE_GENERATION_MISSING = "ACTIVE_GENERATION_MISSING"
    ACTIVE_MANIFEST_INVALID = "ACTIVE_MANIFEST_INVALID"
    ACTIVE_MANIFEST_HASH_MISMATCH = "ACTIVE_MANIFEST_HASH_MISMATCH"
    ACTIVE_FILE_MISSING = "ACTIVE_FILE_MISSING"
    ACTIVE_FILE_HASH_MISMATCH = "ACTIVE_FILE_HASH_MISMATCH"
    FALLBACK_GENERATION_MISSING = "FALLBACK_GENERATION_MISSING"
    FALLBACK_NOT_V1 = "FALLBACK_NOT_V1"
    EVENT_CHAIN_EMPTY = "EVENT_CHAIN_EMPTY"
    EVENT_CHAIN_BROKEN = "EVENT_CHAIN_BROKEN"
    EVENT_CHAIN_CYCLE = "EVENT_CHAIN_CYCLE"
    EVENT_SEQUENCE_INVALID = "EVENT_SEQUENCE_INVALID"
    EVENT_POINTER_MISMATCH = "EVENT_POINTER_MISMATCH"
    ORPHAN_GENERATION = "ORPHAN_GENERATION"
    ORPHAN_EVENT = "ORPHAN_EVENT"
    ARTIFACT_NOT_AVAILABLE_IN_LANE = "ARTIFACT_NOT_AVAILABLE_IN_LANE"
    ARTIFACT_CORRUPT = "ARTIFACT_CORRUPT"
    ACTIVATION_EVALUATION_MISSING = "ACTIVATION_EVALUATION_MISSING"
    FUNCTIONAL_VALIDATION_NOT_AVAILABLE = "FUNCTIONAL_VALIDATION_NOT_AVAILABLE"
    USER_AUTHENTICATION_NOT_AVAILABLE = "USER_AUTHENTICATION_NOT_AVAILABLE"
    WRITE_OPERATIONS_DISABLED = "WRITE_OPERATIONS_DISABLED"


class GovernanceIssue(AltamiraBaseModel):
    """UN hallazgo de gobierno -- `message` es SIEMPRE texto seguro
    fijo por `code` (catalogo cerrado en el reader, nunca interpolacion
    de contenido no confiable); `references` son IDs (generation_id/
    event_id/logical_name), nunca rutas absolutas ni texto libre."""

    issue_id: str = Field(min_length=1)
    severity: GovernanceIssueSeverity
    code: GovernanceIssueCode
    message: str = Field(min_length=1)
    references: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_lists_sorted_and_unique(self) -> GovernanceIssue:
        for field_name in ("references", "diagnostics"):
            values = getattr(self, field_name)
            if values != _ordered_unique(values):
                raise ValueError(f"{field_name} debe estar ordenado y sin duplicados")
        return self


class GovernanceArtifactSummary(AltamiraBaseModel):
    """Vista de gobierno de UN `logical_name` resuelto (o no) desde el
    lane activo. `downloadable` es `True` UNICAMENTE cuando `status ==
    AVAILABLE` -- la UI/API nunca ofrece un enlace de descarga para un
    estado distinto."""

    logical_name: str = Field(min_length=1)
    status: GovernanceArtifactStatus
    resolved_lane: MaterializedActivationLane | None = None
    generation_id: str | None = Field(default=None, min_length=1)
    relative_path: str | None = Field(default=None, min_length=1)
    sha256: Sha256Hex | None = None
    byte_size: int | None = Field(default=None, ge=0)
    record_count: int | None = Field(default=None, ge=0)
    schema_version: str | None = None
    downloadable: bool
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_relative_path(self) -> GovernanceArtifactSummary:
        if self.relative_path is not None:
            check_relative_path_is_safe(self.relative_path)
        return self

    @model_validator(mode="after")
    def _check_downloadable_requires_available(self) -> GovernanceArtifactSummary:
        if self.downloadable and self.status != GovernanceArtifactStatus.AVAILABLE:
            raise ValueError("downloadable=true exige status=AVAILABLE")
        return self

    @model_validator(mode="after")
    def _check_diagnostics_sorted_and_unique(self) -> GovernanceArtifactSummary:
        if self.diagnostics != _ordered_unique(self.diagnostics):
            raise ValueError("diagnostics debe estar ordenado y sin duplicados")
        return self


class GovernanceGenerationSummary(AltamiraBaseModel):
    """Vista de gobierno de UNA generacion persistida (Fase 15A Parte
    5-6). `manifest_hash` es `None` UNICAMENTE cuando `manifest_
    integrity != VALID` (nunca se fabrica un hash de un manifiesto que
    no se pudo leer)."""

    generation_id: str = Field(min_length=1)
    lane: MaterializedActivationLane | None = None
    kind: MaterializedGenerationKind | None = None
    reachability: GovernanceGenerationReachability
    manifest_integrity: GovernanceIntegrityStatus
    manifest_hash: Sha256Hex | None = None
    approved_group_ids: list[str] = Field(default_factory=list)
    file_count: int = Field(ge=0)
    files: list[str] = Field(default_factory=list)
    fallback_generation_id: str | None = Field(default=None, min_length=1)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_fields_requiring_valid_integrity(self) -> GovernanceGenerationSummary:
        """`lane`/`kind`/`manifest_hash` son `None` UNICAMENTE cuando
        `manifest_integrity != VALID` -- una generacion cuyo manifest no
        se pudo leer no permite conocer su lane/kind (`generation_id`
        es un hash de contenido, nunca codifica lane/kind por si
        mismo); nunca se fabrica un valor para un manifest ilegible
        (deviacion documentada respecto del enunciado original, mismo
        principio que `NOT_AVAILABLE_IN_LANE` en Fase 14B)."""
        known_fields_present = (
            self.lane is not None or self.kind is not None or self.manifest_hash is not None
        )
        if known_fields_present and self.manifest_integrity != GovernanceIntegrityStatus.VALID:
            raise ValueError("lane/kind/manifest_hash exigen manifest_integrity=VALID")
        if self.manifest_integrity == GovernanceIntegrityStatus.VALID and (
            self.lane is None or self.kind is None or self.manifest_hash is None
        ):
            raise ValueError("manifest_integrity=VALID exige lane/kind/manifest_hash presentes")
        return self

    @model_validator(mode="after")
    def _check_files_reconciled(self) -> GovernanceGenerationSummary:
        if len(self.files) != len(set(self.files)):
            raise ValueError("files contiene un logical_name duplicado")
        if self.files != sorted(self.files):
            raise ValueError("files debe estar ordenado")
        if self.file_count != len(self.files):
            raise ValueError("file_count no reconcilia con len(files)")
        return self

    @model_validator(mode="after")
    def _check_lists_sorted_and_unique(self) -> GovernanceGenerationSummary:
        for field_name in ("approved_group_ids", "diagnostics"):
            values = getattr(self, field_name)
            if values != _ordered_unique(values):
                raise ValueError(f"{field_name} debe estar ordenado y sin duplicados")
        return self


class GovernanceEventSummary(AltamiraBaseModel):
    """Vista de gobierno de UN evento persistido -- `confirmed=True`
    UNICAMENTE si es alcanzable desde `active.json.latest_event_id`
    siguiendo `previous_event_id` (Fase 15A Parte 5); un evento
    `confirmed=False` es un intento huerfano, preservado, nunca
    presentado como parte del lane activo."""

    event_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    action: str = Field(min_length=1)
    from_generation_id: str | None = Field(default=None, min_length=1)
    to_generation_id: str = Field(min_length=1)
    resulting_lane: MaterializedActivationLane
    previous_event_id: str | None = Field(default=None, min_length=1)
    confirmed: bool
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_diagnostics_sorted_and_unique(self) -> GovernanceEventSummary:
        if self.diagnostics != _ordered_unique(self.diagnostics):
            raise ValueError("diagnostics debe estar ordenado y sin duplicados")
        return self


class GovernanceUnifiedGroupSummary(AltamiraBaseModel):
    """Vista de gobierno de UN grupo unified de la generacion unified
    activa/seleccionada (Fase 15A Parte 6) -- proyeccion, nunca
    reinterpretacion: cada campo proviene directamente de un registro
    materializado real (Fase 14B), nunca de una inferencia por
    similitud. Ausencia legitima se representa con `None`/lista vacia,
    nunca se inventa un valor."""

    group_id: str = Field(min_length=1)
    rule_family: str = Field(min_length=1)
    program: str = Field(min_length=1)
    target: str | None = None
    output_literal: str | None = None
    member_ids: list[str] = Field(default_factory=list)
    source_candidate_ids: list[str] = Field(default_factory=list)
    review_decision_ids: list[str] = Field(default_factory=list)
    context_package_record_id: str | None = Field(default=None, min_length=1)
    rule_draft_record_id: str | None = Field(default=None, min_length=1)
    guardrail_status: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_aliases: list[str] = Field(default_factory=list)
    provenance_references: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_lists_sorted_and_unique(self) -> GovernanceUnifiedGroupSummary:
        for field_name in (
            "member_ids",
            "source_candidate_ids",
            "review_decision_ids",
            "evidence_ids",
            "evidence_aliases",
            "provenance_references",
            "diagnostics",
        ):
            values = getattr(self, field_name)
            if values != _ordered_unique(values):
                raise ValueError(f"{field_name} debe estar ordenado y sin duplicados")
        return self


class GovernanceActivationReadinessSummary(AltamiraBaseModel):
    """Proyeccion de solo lectura de `UnifiedActivationEvaluationArtifact`
    (Fase 14A) -- nunca la recalcula, nunca la modifica. `None`
    UNICAMENTE cuando la evaluacion nunca se escribio para este run
    (`ACTIVATION_EVALUATION_MISSING`)."""

    mode: str = Field(min_length=1)
    requested_lane: str = Field(min_length=1)
    effective_lane: str = Field(min_length=1)
    fallback_lane: str = Field(min_length=1)
    readiness_disposition: str = Field(min_length=1)
    activation_decision: str = Field(min_length=1)
    canary_selected: bool | None = None
    materialization_enabled: bool
    exact_equivalent_count: int = Field(ge=0)
    unified_additive_count: int = Field(ge=0)
    v1_only_count: int = Field(ge=0)
    conflicting_count: int = Field(ge=0)
    blocking_issue_count: int = Field(ge=0)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_diagnostics_sorted_and_unique(self) -> GovernanceActivationReadinessSummary:
        if self.diagnostics != _ordered_unique(self.diagnostics):
            raise ValueError("diagnostics debe estar ordenado y sin duplicados")
        return self


class OperationalGovernanceOverview(AltamiraBaseModel):
    """Read model completo de gobierno operativo de UN run -- nunca se
    persiste (construido en memoria por `pipeline/operational_
    governance_reader.py` en cada peticion GET). Sin timestamps, sin
    rutas absolutas, sin datos de proveedor, sin secretos, sin
    identidad de usuario inventada."""

    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    run_stage: str = Field(min_length=1)
    activation_initialized: bool
    status: OperationalGovernanceStatus
    active_lane: MaterializedActivationLane | None = None
    active_generation_id: str | None = Field(default=None, min_length=1)
    active_generation_kind: MaterializedGenerationKind | None = None
    pointer_version: int | None = Field(default=None, ge=1)
    previous_generation_id: str | None = Field(default=None, min_length=1)
    fallback_generation_id: str | None = Field(default=None, min_length=1)
    latest_event_id: str | None = Field(default=None, min_length=1)
    event_chain_status: GovernanceEventChainStatus
    event_chain_length: int = Field(ge=0)
    generation_count: int = Field(ge=0)
    confirmed_event_count: int = Field(ge=0)
    orphan_generation_count: int = Field(ge=0)
    orphan_event_count: int = Field(ge=0)
    active_manifest_integrity: GovernanceIntegrityStatus
    readiness: GovernanceActivationReadinessSummary | None = None
    artifacts: list[GovernanceArtifactSummary] = Field(default_factory=list)
    generations: list[GovernanceGenerationSummary] = Field(default_factory=list)
    events: list[GovernanceEventSummary] = Field(default_factory=list)
    unified_groups: list[GovernanceUnifiedGroupSummary] = Field(default_factory=list)
    issues: list[GovernanceIssue] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_artifacts_ordered_unique(self) -> OperationalGovernanceOverview:
        names = [a.logical_name for a in self.artifacts]
        if len(names) != len(set(names)):
            raise ValueError("artifacts contiene logical_name duplicado")
        if names != sorted(names):
            raise ValueError("artifacts no esta ordenado por logical_name")
        return self

    @model_validator(mode="after")
    def _check_generations_ordered_unique(self) -> OperationalGovernanceOverview:
        ids = [g.generation_id for g in self.generations]
        if len(ids) != len(set(ids)):
            raise ValueError("generations contiene generation_id duplicado")
        if ids != sorted(ids):
            raise ValueError("generations no esta ordenado por generation_id")
        if self.generation_count != len(self.generations):
            raise ValueError("generation_count no reconcilia con len(generations)")
        orphan_count = sum(
            1 for g in self.generations if g.reachability == GovernanceGenerationReachability.ORPHAN
        )
        if self.orphan_generation_count != orphan_count:
            raise ValueError("orphan_generation_count no reconcilia con generations")
        return self

    @model_validator(mode="after")
    def _check_events_ordered_unique_and_reconciled(self) -> OperationalGovernanceOverview:
        ids = [e.event_id for e in self.events]
        if len(ids) != len(set(ids)):
            raise ValueError("events contiene event_id duplicado")
        ordered = sorted(self.events, key=lambda e: (e.sequence, e.event_id))
        if [e.event_id for e in ordered] != ids:
            raise ValueError("events no esta ordenado por (sequence, event_id)")
        confirmed = sum(1 for e in self.events if e.confirmed)
        orphan = sum(1 for e in self.events if not e.confirmed)
        if self.confirmed_event_count != confirmed:
            raise ValueError("confirmed_event_count no reconcilia con events")
        if self.orphan_event_count != orphan:
            raise ValueError("orphan_event_count no reconcilia con events")
        if self.event_chain_length != confirmed:
            raise ValueError("event_chain_length no reconcilia con eventos confirmados")
        return self

    @model_validator(mode="after")
    def _check_unified_groups_ordered_unique(self) -> OperationalGovernanceOverview:
        ids = [g.group_id for g in self.unified_groups]
        if len(ids) != len(set(ids)):
            raise ValueError("unified_groups contiene group_id duplicado")
        if ids != sorted(ids):
            raise ValueError("unified_groups no esta ordenado por group_id")
        return self

    @model_validator(mode="after")
    def _check_issues_ordered_unique(self) -> OperationalGovernanceOverview:
        ids = [i.issue_id for i in self.issues]
        if len(ids) != len(set(ids)):
            raise ValueError("issues contiene issue_id duplicado")
        if ids != sorted(ids):
            raise ValueError("issues no esta ordenado por issue_id")
        return self

    @model_validator(mode="after")
    def _check_diagnostics_sorted_and_unique(self) -> OperationalGovernanceOverview:
        if self.diagnostics != _ordered_unique(self.diagnostics):
            raise ValueError("diagnostics debe estar ordenado y sin duplicados")
        return self

    @model_validator(mode="after")
    def _check_activation_initialized_consistency(self) -> OperationalGovernanceOverview:
        if (
            not self.activation_initialized
            and self.status != OperationalGovernanceStatus.NOT_INITIALIZED
        ):
            raise ValueError("activation_initialized=false exige status=NOT_INITIALIZED")
        return self


__all__ = [
    "GovernanceActivationReadinessSummary",
    "GovernanceArtifactStatus",
    "GovernanceArtifactSummary",
    "GovernanceEventChainStatus",
    "GovernanceEventSummary",
    "GovernanceGenerationReachability",
    "GovernanceGenerationSummary",
    "GovernanceIntegrityStatus",
    "GovernanceIssue",
    "GovernanceIssueCode",
    "GovernanceIssueSeverity",
    "GovernanceUnifiedGroupSummary",
    "OperationalGovernanceOverview",
    "OperationalGovernanceStatus",
]
