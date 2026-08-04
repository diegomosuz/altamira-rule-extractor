"""Contratos de materializacion, puntero activo y cadena de eventos
(Fase 14B de la ampliacion semantica,
`feat/controlled-unified-materialization`).

Toda la materializacion vive EXCLUSIVAMENTE bajo `<run_dir>/activation/`
(`generations/<generation_id>/manifest.json` + archivos de datos,
`active.json`, `events/<event_id>.json`) -- nunca dentro de
`artifacts/01-10`, nunca sobrescribiendo `run.json` ni ningun
`diagnostics/*.json` preexistente (Fase 9-14A permanecen intactas).

Principios de identidad (Fase 14B Parte 2, aplicados en TODO este
modulo): `generation_id`/`event_id` se derivan EXCLUSIVAMENTE del
contenido -- nunca de un timestamp, nunca de `random`, nunca de
`hash()` nativo de Python. Una generacion es inmutable una vez
persistida; el UNICO "commit point" de una transicion es la escritura
atomica de `active.json` (ver `pipeline/unified_activation_transition.
py`)."""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, Sha256Hex
from .unified_activation_config import UnifiedFallbackPolicy
from .unified_activation_evaluation import UnifiedActivationUnifiedReference
from .unified_materialization_authorization import UnifiedMaterializationReasonCode
from .unified_shadow_downstream import (
    UnifiedShadowContextPackageRecord,
    UnifiedShadowGuardrailRecord,
    UnifiedShadowRuleDraftRecord,
)


class MaterializedActivationLane(StrEnum):
    V1 = "V1"
    UNIFIED = "UNIFIED"


class MaterializedGenerationKind(StrEnum):
    V1_BASELINE = "V1_BASELINE"
    UNIFIED_CANARY = "UNIFIED_CANARY"
    UNIFIED_PRIMARY = "UNIFIED_PRIMARY"


class MaterializedGenerationStatus(StrEnum):
    """Catalogo CERRADO de un unico valor: ninguna generacion parcial
    llega jamas a tener un manifest persistido (ver Fase 14B Parte 2,
    principios 4-6 y 13) -- `status` existe para que el contrato sea
    explicito, nunca para representar un estado intermedio."""

    COMPLETE = "COMPLETE"


class ActivationTransitionAction(StrEnum):
    INITIALIZE_V1 = "INITIALIZE_V1"
    ACTIVATE_UNIFIED_CANARY = "ACTIVATE_UNIFIED_CANARY"
    ACTIVATE_UNIFIED_PRIMARY = "ACTIVATE_UNIFIED_PRIMARY"
    FALLBACK_TO_V1 = "FALLBACK_TO_V1"
    ROLLBACK_TO_PREVIOUS = "ROLLBACK_TO_PREVIOUS"
    ROLLBACK_TO_GENERATION = "ROLLBACK_TO_GENERATION"
    KEEP_CURRENT = "KEEP_CURRENT"


class ActivationResolutionStatus(StrEnum):
    """`NOT_AVAILABLE_IN_LANE` (decision de diseno, Fase 14B Parte 10,
    "una ausencia legitima debe ser tipada"): distingue una ausencia
    LEGITIMA (p. ej. V1 sin `09-guardrails/guardrail-manifest.json`
    porque GUARDRAILS_APPLIED nunca corrio en este run) de `BLOCKED`
    (corrupcion/hash no reconciliado) -- nunca se conflacionan.
    `ActiveArtifactResolution.relative_path`/`sha256` son `None`
    UNICAMENTE con este status."""

    RESOLVED = "RESOLVED"
    FALLBACK_APPLIED = "FALLBACK_APPLIED"
    BLOCKED = "BLOCKED"
    NOT_AVAILABLE_IN_LANE = "NOT_AVAILABLE_IN_LANE"


_UNIFIED_GENERATION_KINDS = frozenset(
    {MaterializedGenerationKind.UNIFIED_CANARY, MaterializedGenerationKind.UNIFIED_PRIMARY}
)


def _ordered_unique(values: list[str]) -> list[str]:
    return sorted(set(values))


def check_relative_path_is_safe(path: str) -> str:
    """Reutilizada por el store (Fase 14B Parte 8) como segunda linea
    de defensa: ninguna ruta puede escapar del run (`..`), ser
    absoluta (POSIX `/...` o unidad Windows `C:\\...`) ni UNC
    (`\\\\server\\share`). Nunca se confia UNICAMENTE en la validacion
    del contrato -- el store vuelve a verificar contra el `run_dir`
    real antes de tocar el filesystem."""
    if not path or path.strip() != path:
        raise ValueError(f"relative_path invalida (vacia o con espacios en los bordes): {path!r}")
    normalized = path.replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("//"):
        raise ValueError(f"relative_path no puede ser absoluta: {path!r}")
    if len(normalized) >= 2 and normalized[1] == ":":
        raise ValueError(f"relative_path no puede incluir una unidad: {path!r}")
    segments = normalized.split("/")
    if any(segment in ("", "..") for segment in segments):
        raise ValueError(f"relative_path no puede contener '..' ni segmentos vacios: {path!r}")
    if segments[0] == ".":
        raise ValueError(f"relative_path no puede empezar con './': {path!r}")
    return path


def compute_generation_id(
    *,
    lane: MaterializedActivationLane,
    kind: MaterializedGenerationKind,
    run_id: str,
    file_hashes: dict[str, str],
) -> str:
    """`generation_id` determinista (Parte 2, principios 3/15/16/17):
    sha256 sobre `{lane, kind, run_id, pares ordenados (logical_name,
    sha256)}` -- NUNCA un timestamp, NUNCA `random`, NUNCA `hash()`
    nativo de Python. Compartida por ambos constructores (V1 y
    unified) para que el algoritmo de identidad nunca pueda divergir
    entre lanes."""
    canonical = "\x1f".join(
        [
            lane.value,
            kind.value,
            run_id,
            *(f"{name}={digest}" for name, digest in sorted(file_hashes.items())),
        ]
    )
    return f"generation-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def compute_event_id(
    *,
    run_id: str,
    sequence: int,
    action: ActivationTransitionAction,
    from_generation_id: str | None,
    to_generation_id: str,
    previous_event_id: str | None,
    authorization_hash: str,
) -> str:
    """`event_id` determinista respecto del estado anterior y la
    accion (Parte 2, principio 3; Parte 5, invariante 2) -- NUNCA un
    timestamp, NUNCA `random`, NUNCA `hash()` nativo de Python."""
    canonical = "\x1f".join(
        [
            run_id,
            str(sequence),
            action.value,
            from_generation_id or "",
            to_generation_id,
            previous_event_id or "",
            authorization_hash,
        ]
    )
    return f"event-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


class MaterializedFileReference(AltamiraBaseModel):
    """Referencia a UN archivo de datos materializado -- nunca su
    contenido embebido: el store (Parte 8) recalcula `sha256`/
    `byte_size` leyendo el archivo real y los compara contra estos
    valores antes de permitir cualquier resolucion (Parte 5, invariante
    7)."""

    logical_name: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)
    sha256: Sha256Hex
    byte_size: int = Field(ge=0)
    record_count: int | None = Field(default=None, ge=0)
    schema_version: str | None = None

    @model_validator(mode="after")
    def _check_relative_path(self) -> MaterializedFileReference:
        check_relative_path_is_safe(self.relative_path)
        return self


class MaterializedGenerationManifest(AltamiraBaseModel):
    """Manifiesto INMUTABLE de una generacion -- escrito UNICAMENTE
    despues de que todos sus archivos de datos ya fueron escritos y
    revalidados (Parte 2, principios 5-6). `lane`/`kind` determinan
    donde puede apuntar `MaterializedFileReference.relative_path` de
    cada archivo: `V1_BASELINE` SIEMPRE referencia archivos productivos
    ya existentes bajo `artifacts/` (nunca los copia, nunca los
    modifica -- Parte 5, invariante 16); `UNIFIED_CANARY`/`UNIFIED_
    PRIMARY` SIEMPRE referencian archivos propios, nuevos, bajo
    `activation/generations/<generation_id>/` (Parte 5, invariante
    17)."""

    schema_version: Literal["1.0"] = "1.0"
    generation_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    lane: MaterializedActivationLane
    kind: MaterializedGenerationKind
    status: Literal[MaterializedGenerationStatus.COMPLETE] = MaterializedGenerationStatus.COMPLETE
    source_package_hash: Sha256Hex
    activation_evaluation_hash: Sha256Hex
    authorization_hash: Sha256Hex
    candidate_v1_artifact_hash: Sha256Hex
    unified_candidates_shadow_hash: Sha256Hex | None = None
    validation_report_hash: Sha256Hex | None = None
    downstream_artifact_hash: Sha256Hex | None = None
    approved_group_ids: list[str] = Field(default_factory=list)
    files: list[MaterializedFileReference] = Field(default_factory=list)
    fallback_generation_id: str | None = Field(default=None, min_length=1)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_lane_kind_consistency(self) -> MaterializedGenerationManifest:
        if self.lane == MaterializedActivationLane.V1:
            if self.kind != MaterializedGenerationKind.V1_BASELINE:
                raise ValueError("lane=V1 exige kind=V1_BASELINE")
        elif self.kind not in _UNIFIED_GENERATION_KINDS:
            raise ValueError("lane=UNIFIED exige kind=UNIFIED_CANARY o UNIFIED_PRIMARY")
        return self

    @model_validator(mode="after")
    def _check_v1_generation_never_has_approved_groups(self) -> MaterializedGenerationManifest:
        if self.kind == MaterializedGenerationKind.V1_BASELINE and self.approved_group_ids:
            raise ValueError("kind=V1_BASELINE no puede declarar approved_group_ids")
        return self

    @model_validator(mode="after")
    def _check_files_ordered_unique_and_scoped_to_lane(self) -> MaterializedGenerationManifest:
        names = [f.logical_name for f in self.files]
        if len(names) != len(set(names)):
            raise ValueError("files contiene logical_name duplicado")
        if names != sorted(names):
            raise ValueError("files no esta ordenado por logical_name")
        for file_reference in self.files:
            normalized = file_reference.relative_path.replace("\\", "/")
            if self.lane == MaterializedActivationLane.V1:
                if not normalized.startswith("artifacts/"):
                    raise ValueError(
                        f"generacion V1_BASELINE: {file_reference.logical_name!r} debe "
                        f"referenciar 'artifacts/...' (nunca copia archivos), obtuvo: "
                        f"{file_reference.relative_path!r}"
                    )
            else:
                prefix = f"activation/generations/{self.generation_id}/"
                if not normalized.startswith(prefix):
                    raise ValueError(
                        f"generacion {self.kind.value}: {file_reference.logical_name!r} debe "
                        f"referenciar su propia carpeta ({prefix!r}), obtuvo: "
                        f"{file_reference.relative_path!r}"
                    )
        return self

    @model_validator(mode="after")
    def _check_fallback_generation_id_never_self(self) -> MaterializedGenerationManifest:
        if (
            self.fallback_generation_id is not None
            and self.fallback_generation_id == self.generation_id
        ):
            raise ValueError("fallback_generation_id no puede ser la propia generacion")
        return self

    @model_validator(mode="after")
    def _check_lists_sorted_and_unique(self) -> MaterializedGenerationManifest:
        for field_name in ("approved_group_ids", "diagnostics"):
            values = getattr(self, field_name)
            if values != _ordered_unique(values):
                raise ValueError(f"{field_name} debe estar ordenado y sin duplicados")
        return self


class ActiveActivationPointer(AltamiraBaseModel):
    """`activation/active.json` -- el UNICO commit point de una
    transicion (Parte 2, principio 9). Escrito atomicamente en ultimo
    lugar (Parte 9); su version anterior permanece intacta ante
    cualquier fallo previo al `os.replace` final."""

    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    pointer_version: int = Field(ge=1)
    active_generation_id: str = Field(min_length=1)
    active_lane: MaterializedActivationLane
    active_generation_manifest_hash: Sha256Hex
    previous_generation_id: str | None = Field(default=None, min_length=1)
    fallback_generation_id: str = Field(min_length=1)
    latest_event_id: str = Field(min_length=1)
    fallback_policy: UnifiedFallbackPolicy
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_previous_not_equal_active(self) -> ActiveActivationPointer:
        if (
            self.previous_generation_id is not None
            and self.previous_generation_id == self.active_generation_id
        ):
            raise ValueError("previous_generation_id no puede ser igual a active_generation_id")
        return self

    @model_validator(mode="after")
    def _check_diagnostics_sorted_and_unique(self) -> ActiveActivationPointer:
        if self.diagnostics != _ordered_unique(self.diagnostics):
            raise ValueError("diagnostics debe estar ordenado y sin duplicados")
        return self


class ActivationTransitionEvent(AltamiraBaseModel):
    """UN evento INMUTABLE de la cadena de auditoria -- nunca se
    reescribe, nunca se borra (Parte 9). Un evento alcanzable desde
    `active.json.latest_event_id` siguiendo `previous_event_id` es una
    transicion CONFIRMADA; cualquier otro evento persistido es un
    intento no confirmado (huerfano seguro, nunca una transicion
    activa -- Parte 5, invariante 11 y Parte 9)."""

    schema_version: Literal["1.0"] = "1.0"
    event_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    action: ActivationTransitionAction
    from_generation_id: str | None = Field(default=None, min_length=1)
    to_generation_id: str = Field(min_length=1)
    previous_event_id: str | None = Field(default=None, min_length=1)
    activation_evaluation_hash: Sha256Hex
    authorization_hash: Sha256Hex
    reason_code: UnifiedMaterializationReasonCode
    expected_previous_pointer_hash: Sha256Hex | None = None
    resulting_lane: MaterializedActivationLane
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_sequence_matches_previous_event_presence(self) -> ActivationTransitionEvent:
        if self.sequence == 1 and self.previous_event_id is not None:
            raise ValueError("sequence=1 (primer evento) no puede declarar previous_event_id")
        if self.sequence > 1 and self.previous_event_id is None:
            raise ValueError(f"sequence={self.sequence} exige previous_event_id")
        return self

    @model_validator(mode="after")
    def _check_initialize_v1_has_no_source_generation(self) -> ActivationTransitionEvent:
        if self.action == ActivationTransitionAction.INITIALIZE_V1:
            if self.from_generation_id is not None:
                raise ValueError("action=INITIALIZE_V1 no puede declarar from_generation_id")
        elif self.from_generation_id is None:
            raise ValueError(f"action={self.action.value} exige from_generation_id")
        return self

    @model_validator(mode="after")
    def _check_diagnostics_sorted_and_unique(self) -> ActivationTransitionEvent:
        if self.diagnostics != _ordered_unique(self.diagnostics):
            raise ValueError("diagnostics debe estar ordenado y sin duplicados")
        return self


class ActiveArtifactResolution(AltamiraBaseModel):
    """Resultado tipado de `pipeline/unified_active_lane_router.py`.
    `relative_path`/`sha256` reflejan lo que el manifiesto RESUELTO
    declara -- incluso cuando `status=BLOCKED` (el archivo real no
    coincide): permiten auditar exactamente que se esperaba encontrar.
    Son `None` UNICAMENTE cuando `status=NOT_AVAILABLE_IN_LANE` (una
    ausencia LEGITIMA -- nunca se fabrica una ruta/hash sintetico para
    un archivo que nunca existio)."""

    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    status: ActivationResolutionStatus
    requested_logical_name: str = Field(min_length=1)
    resolved_lane: MaterializedActivationLane
    generation_id: str = Field(min_length=1)
    relative_path: str | None = Field(default=None, min_length=1)
    sha256: Sha256Hex | None = None
    fallback_applied: bool
    fallback_event_id: str | None = Field(default=None, min_length=1)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_relative_path(self) -> ActiveArtifactResolution:
        if self.relative_path is not None:
            check_relative_path_is_safe(self.relative_path)
        return self

    @model_validator(mode="after")
    def _check_path_and_hash_presence_by_status(self) -> ActiveArtifactResolution:
        if self.status == ActivationResolutionStatus.NOT_AVAILABLE_IN_LANE:
            if self.relative_path is not None or self.sha256 is not None:
                raise ValueError(
                    "status=NOT_AVAILABLE_IN_LANE no puede declarar relative_path/sha256"
                )
        elif self.status in (
            ActivationResolutionStatus.RESOLVED,
            ActivationResolutionStatus.FALLBACK_APPLIED,
        ) and (self.relative_path is None or self.sha256 is None):
            raise ValueError(f"status={self.status.value} exige relative_path y sha256")
        # BLOCKED puede o no conocer relative_path/sha256: si el manifiesto
        # mismo esta ausente/corrupto, el router (Parte 10) nunca llega a
        # saber que archivo se esperaba -- reportar None en ese caso es
        # la unica opcion honesta, nunca se fabrica un valor.
        return self

    @model_validator(mode="after")
    def _check_fallback_consistency(self) -> ActiveArtifactResolution:
        if self.fallback_event_id is not None and not self.fallback_applied:
            raise ValueError("fallback_event_id exige fallback_applied=true")
        if self.status == ActivationResolutionStatus.FALLBACK_APPLIED:
            if not self.fallback_applied:
                raise ValueError("status=FALLBACK_APPLIED exige fallback_applied=true")
            if self.resolved_lane != MaterializedActivationLane.V1:
                raise ValueError("status=FALLBACK_APPLIED exige resolved_lane=V1")
        return self

    @model_validator(mode="after")
    def _check_diagnostics_sorted_and_unique(self) -> ActiveArtifactResolution:
        if self.diagnostics != _ordered_unique(self.diagnostics):
            raise ValueError("diagnostics debe estar ordenado y sin duplicados")
        return self


class MaterializedUnifiedCandidatesFile(AltamiraBaseModel):
    """`candidates.json` de una generacion UNIFIED -- proyeccion
    EXACTA (nunca reinterpretada) de `UnifiedActivationUnifiedReference`
    (Fase 14A) filtrada a `approved_group_ids`. Nunca elige un member
    "ganador": cada entrada preserva `member_ids`/`source_candidate_
    ids`/`evidence_ids`/`provenance_references` tal como el evaluador
    de Fase 14A los produjo."""

    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    candidates: list[UnifiedActivationUnifiedReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_ordered_and_unique(self) -> MaterializedUnifiedCandidatesFile:
        ids = [c.reference_id for c in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("candidates contiene reference_id duplicado")
        if ids != sorted(ids):
            raise ValueError("candidates no esta ordenado por reference_id")
        return self


class MaterializedUnifiedContextPackagesFile(AltamiraBaseModel):
    """`context-packages.json` de una generacion UNIFIED -- proyeccion
    EXACTA de `UnifiedShadowContextPackageRecord` (Fase 13, contrato
    productivo `ContextPackage` embebido sin deformar) filtrada a
    `approved_group_ids`. Nunca regenera ni reinterpreta el contenido."""

    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    context_packages: list[UnifiedShadowContextPackageRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_ordered_and_unique(self) -> MaterializedUnifiedContextPackagesFile:
        ids = [c.record_id for c in self.context_packages]
        if len(ids) != len(set(ids)):
            raise ValueError("context_packages contiene record_id duplicado")
        if ids != sorted(ids):
            raise ValueError("context_packages no esta ordenado por record_id")
        return self


class MaterializedUnifiedRuleDraftsFile(AltamiraBaseModel):
    """`rule-drafts.json` de una generacion UNIFIED -- proyeccion
    EXACTA de `UnifiedShadowRuleDraftRecord` (Fase 13, contrato
    productivo `RuleDraft` embebido sin deformar) filtrada a
    `approved_group_ids`. Nunca invoca al proveedor fake nuevamente:
    materializa exactamente el draft ya generado y validado."""

    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    rule_drafts: list[UnifiedShadowRuleDraftRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_ordered_and_unique(self) -> MaterializedUnifiedRuleDraftsFile:
        ids = [d.record_id for d in self.rule_drafts]
        if len(ids) != len(set(ids)):
            raise ValueError("rule_drafts contiene record_id duplicado")
        if ids != sorted(ids):
            raise ValueError("rule_drafts no esta ordenado por record_id")
        return self


class MaterializedUnifiedGuardrailsFile(AltamiraBaseModel):
    """`guardrails.json` de una generacion UNIFIED -- proyeccion EXACTA
    de `UnifiedShadowGuardrailRecord` (Fase 13) filtrada a
    `approved_group_ids`. UNICAMENTE `PASSED` (Fase 14B Parte 5,
    invariante 19: un grupo `GUARDRAIL_REJECTED` nunca puede
    materializarse como aprobado)."""

    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    guardrails: list[UnifiedShadowGuardrailRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_ordered_and_unique(self) -> MaterializedUnifiedGuardrailsFile:
        ids = [g.record_id for g in self.guardrails]
        if len(ids) != len(set(ids)):
            raise ValueError("guardrails contiene record_id duplicado")
        if ids != sorted(ids):
            raise ValueError("guardrails no esta ordenado por record_id")
        return self


__all__ = [
    "ActivationResolutionStatus",
    "ActivationTransitionAction",
    "ActivationTransitionEvent",
    "ActiveActivationPointer",
    "ActiveArtifactResolution",
    "MaterializedActivationLane",
    "MaterializedFileReference",
    "MaterializedGenerationKind",
    "MaterializedGenerationManifest",
    "MaterializedGenerationStatus",
    "MaterializedUnifiedCandidatesFile",
    "MaterializedUnifiedContextPackagesFile",
    "MaterializedUnifiedGuardrailsFile",
    "MaterializedUnifiedRuleDraftsFile",
    "check_relative_path_is_safe",
    "compute_event_id",
    "compute_generation_id",
]
