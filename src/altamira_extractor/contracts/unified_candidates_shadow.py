"""Contrato tipado del artefacto unificado de candidatos en shadow mode
(Fase 11 de la ampliacion semantica,
`feat/unified-candidate-artifact-shadow`).

Diagnostico, NO contractual: persiste en `<run_dir>/diagnostics/
unified-candidates-shadow.json`. Consolida DOS lineas ("lanes") que
NUNCA se fusionan:

- `BASELINE_V1` (`UnifiedBaselineCandidateReference`): una referencia
  inmutable a CADA candidato V1 real (`Q0`), independientemente de si
  algun plan item lo menciona. Nunca derivada del plan; nunca una
  promocion.
- `SHADOW_PROPOSAL` (`UnifiedShadowCandidateGroup`): UNICAMENTE
  derivada de `CandidatePromotionPlanItem` con `action=
  PROPOSE_SHADOW_PROMOTION` y `status=VALID` -- cada propuesta esta
  necesariamente ligada a una decision humana `APPROVE_FOR_SHADOW_
  PROMOTION` real (Fase 10), y proviene UNICAMENTE de V2 o
  INTERPROCEDURAL (V1 nunca puede producir un `UnifiedShadowSourceMember`,
  invariante 11).

Este modulo NUNCA reescribe `CandidateArtifact` V1 (`artifacts/06-
candidates.json`), nunca lo reemplaza, nunca crea un `CandidateArtifact`
productivo nuevo, nunca promueve ningun candidato: `UnifiedShadowCandidateGroup`
es exclusivamente una consolidacion DIAGNOSTICA de propuestas ya
aprobadas por un humano, agrupadas UNICAMENTE cuando Fase 9 ya
demostro una relacion `EXACT_MATCH` entre ellas -- nunca por semejanza
textual, nunca eligiendo un candidato "ganador" (todos los miembros de
un grupo conservan igual jerarquia, ver `pipeline/unified_shadow_
candidate_grouper.py`)."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, Sha256Hex
from .candidate_promotion_assessment import CandidateSource, UnifiedRuleFamily
from .candidate_promotion_plan import PromotionPlanAction, PromotionPlanItemStatus
from .candidate_promotion_review import DecisionReasonCode, ReviewDecision


class UnifiedShadowLane(StrEnum):
    """Las DOS lineas del artefacto, nunca fusionadas. Ver docstring
    del modulo."""

    BASELINE_V1 = "BASELINE_V1"
    SHADOW_PROPOSAL = "SHADOW_PROPOSAL"


class UnifiedShadowSupport(StrEnum):
    """Soporte consolidado de un `UnifiedShadowCandidateGroup` --
    derivado del `original_support` de sus miembros (nunca un valor
    inventado): `DETERMINISTIC` unicamente si TODOS los miembros lo son;
    `BLOCKED` si algun miembro lo es (un grupo nunca mezcla
    silenciosamente un miembro bloqueado con uno determinista sin
    reflejarlo); `PARTIAL` para soporte parcial; `UNKNOWN` cuando el
    soporte original no es reconocible (nunca se asume determinista por
    defecto)."""

    DETERMINISTIC = "DETERMINISTIC"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class UnifiedShadowGroupStatus(StrEnum):
    """Estado de un `UnifiedShadowCandidateGroup`. `VALID` es el UNICO
    estado que representa una propuesta shadow genuinamente nueva y
    consistente. `DUPLICATE_BASELINE_COVERAGE` (regla de seguridad,
    Fase 11 Parte 8): un grupo `EXACT_BASELINE_MATCH` NUNCA puede quedar
    `VALID` -- demuestra una desalineacion entre el plan (aprobado como
    si fuera nuevo) y las fuentes actuales (V1 ya lo cubre)."""

    VALID = "VALID"
    INVALID_SOURCE_REFERENCE = "INVALID_SOURCE_REFERENCE"
    INVALID_PLAN_BINDING = "INVALID_PLAN_BINDING"
    INCONSISTENT_MEMBERS = "INCONSISTENT_MEMBERS"
    DUPLICATE_BASELINE_COVERAGE = "DUPLICATE_BASELINE_COVERAGE"
    BLOCKED = "BLOCKED"


class UnifiedShadowComparisonKind(StrEnum):
    """Resultado de comparar UN `UnifiedShadowCandidateGroup` contra el
    baseline V1 -- evidencia PREFERENTE de Fase 9 (`exact_match_
    reference_ids`/`related_reference_ids`/`conflict_ids`/`CandidateRelation`/
    `CandidateConflict`), nunca semejanza recalculada. `NOT_EVALUATED`
    (V1 ausente/invalido) es DISTINTO de `NOT_IN_BASELINE` (V1
    disponible, evaluado por completo, sin relacion alguna) -- la
    ausencia de una fuente nunca se disfraza de "sin equivalente"."""

    NOT_IN_BASELINE = "NOT_IN_BASELINE"
    EXACT_BASELINE_MATCH = "EXACT_BASELINE_MATCH"
    RELATED_TO_BASELINE = "RELATED_TO_BASELINE"
    CONFLICTS_WITH_BASELINE = "CONFLICTS_WITH_BASELINE"
    NOT_EVALUATED = "NOT_EVALUATED"


class UnifiedShadowExclusionReason(StrEnum):
    """Motivo ESTABLE por el que un `CandidatePromotionPlanItem` no
    entra al artefacto shadow como miembro -- nunca una reinterpretacion
    de la decision humana, solo documenta la causa."""

    PLAN_ACTION_NOT_PROPOSE = "PLAN_ACTION_NOT_PROPOSE"
    PLAN_ITEM_NOT_VALID = "PLAN_ITEM_NOT_VALID"
    BASELINE_ITEM = "BASELINE_ITEM"
    ALREADY_COVERED = "ALREADY_COVERED"
    BLOCKED_ITEM = "BLOCKED_ITEM"
    PENDING_DECISION = "PENDING_DECISION"
    REJECTED = "REJECTED"
    DEFERRED = "DEFERRED"
    UNKNOWN_SOURCE = "UNKNOWN_SOURCE"
    SOURCE_CANDIDATE_NOT_FOUND = "SOURCE_CANDIDATE_NOT_FOUND"
    HASH_MISMATCH = "HASH_MISMATCH"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    INCONSISTENT_EXACT_MATCH_GROUP = "INCONSISTENT_EXACT_MATCH_GROUP"


def _ordered_unique_strings(values: Iterable[str]) -> list[str]:
    return sorted(set(values))


class UnifiedBaselineCandidateReference(AltamiraBaseModel):
    """Referencia INMUTABLE a UN candidato V1 real (`Q0`). Adaptacion
    pura (`pipeline/unified_shadow_baseline_adapter.py`): nunca
    modifica V1, nunca completa campos ausentes con V2/interprocedural,
    nunca es, por si misma, una promocion. TODO candidato V1 aparece
    aqui, sea o no referenciado por algun plan item."""

    baseline_reference_id: str = Field(min_length=1)
    source_candidate_id: str = Field(min_length=1)
    source_artifact_hash: Sha256Hex
    original_candidate_hash: Sha256Hex
    rule_family: UnifiedRuleFamily
    original_rule_type: str | None = None
    original_support: str = Field(min_length=1)
    program: str = Field(min_length=1)
    paragraph: str | None = None
    decision_id: str | None = None
    target: str | None = None
    input_literal: str | None = None
    output_literal: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    provenance_references: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_lists_sorted_and_unique(self) -> UnifiedBaselineCandidateReference:
        label = f"UnifiedBaselineCandidateReference({self.baseline_reference_id!r})"
        for field_name in ("evidence_ids", "provenance_references", "diagnostics"):
            values = getattr(self, field_name)
            if values != _ordered_unique_strings(values):
                raise ValueError(f"{label}: {field_name} debe estar ordenado y sin duplicados")
        return self


class UnifiedShadowSourceMember(AltamiraBaseModel):
    """UN candidato fuente (V2 o INTERPROCEDURAL, NUNCA V1) resuelto a
    partir de un `CandidatePromotionPlanItem` con `action=
    PROPOSE_SHADOW_PROMOTION` y `status=VALID`. `source_decision_id` es
    la `decision_id` SEMANTICA del candidato fuente (estructural, del
    `UnifiedCandidateReference`/review item de Fase 9/10 -- SIEMPRE
    `None` para INTERPROCEDURAL, ver `docs/
    INTERPROCEDURAL_RULE_DETECTORS_SHADOW.md`); `review_decision_id` es
    el `decision_id` de la DECISION HUMANA (Fase 10,
    `CandidatePromotionDecision.decision_id`) -- dos conceptos
    DISTINTOS, nunca colapsados en un unico campo `decision_id`.
    `decision_reference_id` preserva, si existe, la cadena de
    supersesion declarada por el humano (`CandidatePromotionDecision.
    decision_reference`)."""

    member_id: str = Field(min_length=1)
    source: CandidateSource
    source_candidate_id: str = Field(min_length=1)
    source_artifact_hash: Sha256Hex
    source_candidate_hash: Sha256Hex
    assessment_id: str = Field(min_length=1)
    assessment_reference_id: str = Field(min_length=1)
    review_item_id: str = Field(min_length=1)
    plan_item_id: str = Field(min_length=1)
    review_decision_id: str = Field(min_length=1)
    decision: ReviewDecision
    reason_code: DecisionReasonCode
    reviewer_reference: str = Field(min_length=1)
    rule_family: UnifiedRuleFamily
    original_rule_type: str | None = None
    original_support: str = Field(min_length=1)
    program: str = Field(min_length=1)
    paragraph: str | None = None
    source_decision_id: str | None = None
    decision_reference_id: str | None = None
    call_site_id: str | None = None
    target: str | None = None
    input_literal: str | None = None
    output_literal: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    provenance_references: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_source_is_never_v1(self) -> UnifiedShadowSourceMember:
        if self.source == CandidateSource.V1:
            raise ValueError(
                f"UnifiedShadowSourceMember({self.member_id!r}): source=V1 nunca puede "
                "producir un shadow member (invariante 11)"
            )
        return self

    @model_validator(mode="after")
    def _check_decision_is_approve(self) -> UnifiedShadowSourceMember:
        if self.decision != ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION:
            raise ValueError(
                f"UnifiedShadowSourceMember({self.member_id!r}): decision debe ser "
                f"APPROVE_FOR_SHADOW_PROMOTION, recibido {self.decision.value!r} "
                "(invariante 13)"
            )
        return self

    @model_validator(mode="after")
    def _check_reason_code_compatible_with_approval(self) -> UnifiedShadowSourceMember:
        allowed = (
            DecisionReasonCode.EVIDENCE_CONFIRMED,
            DecisionReasonCode.BUSINESS_RULE_CONFIRMED,
        )
        if self.reason_code not in allowed:
            raise ValueError(
                f"UnifiedShadowSourceMember({self.member_id!r}): reason_code "
                f"{self.reason_code.value!r} no es compatible con una aprobacion "
                "(invariante 14)"
            )
        return self

    @model_validator(mode="after")
    def _check_lists_sorted_and_unique(self) -> UnifiedShadowSourceMember:
        label = f"UnifiedShadowSourceMember({self.member_id!r})"
        for field_name in ("evidence_ids", "provenance_references", "diagnostics"):
            values = getattr(self, field_name)
            if values != _ordered_unique_strings(values):
                raise ValueError(f"{label}: {field_name} debe estar ordenado y sin duplicados")
        return self


class UnifiedShadowCandidateGroup(AltamiraBaseModel):
    """Un componente conexo de relaciones `EXACT_MATCH` (Fase 9) entre
    `UnifiedShadowSourceMember` -- NUNCA elige un "ganador": todos los
    `member_ids` conservan igual jerarquia, ninguno se descarta.
    `lane` es SIEMPRE `SHADOW_PROPOSAL` (un grupo nunca representa
    baseline)."""

    unified_shadow_candidate_id: str = Field(min_length=1)
    lane: Literal["SHADOW_PROPOSAL"] = "SHADOW_PROPOSAL"
    status: UnifiedShadowGroupStatus
    rule_family: UnifiedRuleFamily
    program: str = Field(min_length=1)
    paragraph: str | None = None
    source_decision_ids: list[str] = Field(default_factory=list)
    call_site_ids: list[str] = Field(default_factory=list)
    target: str | None = None
    input_literal: str | None = None
    output_literal: str | None = None
    support: UnifiedShadowSupport
    member_ids: list[str] = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    provenance_references: list[str] = Field(default_factory=list)
    assessment_ids: list[str] = Field(default_factory=list)
    review_item_ids: list[str] = Field(default_factory=list)
    plan_item_ids: list[str] = Field(default_factory=list)
    review_decision_ids: list[str] = Field(default_factory=list)
    comparison_to_v1: UnifiedShadowComparisonKind
    exact_baseline_reference_ids: list[str] = Field(default_factory=list)
    related_baseline_reference_ids: list[str] = Field(default_factory=list)
    conflicting_baseline_reference_ids: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_lists_sorted_and_unique(self) -> UnifiedShadowCandidateGroup:
        label = f"UnifiedShadowCandidateGroup({self.unified_shadow_candidate_id!r})"
        for field_name in (
            "source_decision_ids",
            "call_site_ids",
            "member_ids",
            "evidence_ids",
            "provenance_references",
            "assessment_ids",
            "review_item_ids",
            "plan_item_ids",
            "review_decision_ids",
            "exact_baseline_reference_ids",
            "related_baseline_reference_ids",
            "conflicting_baseline_reference_ids",
            "blocking_reasons",
            "diagnostics",
        ):
            values = getattr(self, field_name)
            if values != _ordered_unique_strings(values):
                raise ValueError(f"{label}: {field_name} debe estar ordenado y sin duplicados")
        return self

    @model_validator(mode="after")
    def _check_duplicate_baseline_coverage_never_valid(self) -> UnifiedShadowCandidateGroup:
        label = f"UnifiedShadowCandidateGroup({self.unified_shadow_candidate_id!r})"
        if (
            self.comparison_to_v1 == UnifiedShadowComparisonKind.EXACT_BASELINE_MATCH
            and self.status != UnifiedShadowGroupStatus.DUPLICATE_BASELINE_COVERAGE
        ):
            raise ValueError(
                f"{label}: comparison_to_v1=EXACT_BASELINE_MATCH exige "
                "status=DUPLICATE_BASELINE_COVERAGE (invariante 23) -- nunca puede quedar "
                "VALID silenciosamente"
            )
        if (
            self.status == UnifiedShadowGroupStatus.DUPLICATE_BASELINE_COVERAGE
            and not self.exact_baseline_reference_ids
        ):
            raise ValueError(
                f"{label}: DUPLICATE_BASELINE_COVERAGE exige al menos un "
                "exact_baseline_reference_id"
            )
        return self

    @model_validator(mode="after")
    def _check_conflicts_with_baseline_blocks_group(self) -> UnifiedShadowCandidateGroup:
        label = f"UnifiedShadowCandidateGroup({self.unified_shadow_candidate_id!r})"
        if (
            self.comparison_to_v1 == UnifiedShadowComparisonKind.CONFLICTS_WITH_BASELINE
            and self.status != UnifiedShadowGroupStatus.BLOCKED
        ):
            raise ValueError(
                f"{label}: comparison_to_v1=CONFLICTS_WITH_BASELINE exige status=BLOCKED "
                "(invariante 24)"
            )
        # BLOCKED puede originarse tambien por inconsistencia interna
        # (Parte 6), no solo por conflicto con V1 -- `conflicting_
        # baseline_reference_ids` solo se exige cuando la CAUSA es el
        # conflicto con baseline (chequeado arriba), nunca en el caso
        # contrario.
        return self


class UnifiedShadowExcludedPlanItem(AltamiraBaseModel):
    """Registra, sin reinterpretar la decision humana, por que UN
    `CandidatePromotionPlanItem` no entra al artefacto shadow como
    miembro."""

    exclusion_id: str = Field(min_length=1)
    plan_item_id: str = Field(min_length=1)
    review_item_id: str = Field(min_length=1)
    assessment_id: str = Field(min_length=1)
    reference_id: str = Field(min_length=1)
    source: CandidateSource
    source_candidate_id: str = Field(min_length=1)
    action: PromotionPlanAction
    status: PromotionPlanItemStatus
    reason: UnifiedShadowExclusionReason
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_diagnostics_sorted_and_unique(self) -> UnifiedShadowExcludedPlanItem:
        if self.diagnostics != _ordered_unique_strings(self.diagnostics):
            raise ValueError(
                f"UnifiedShadowExcludedPlanItem({self.exclusion_id!r}): diagnostics debe "
                "estar ordenado y sin duplicados"
            )
        return self


class UnifiedCandidatesShadowSummary(AltamiraBaseModel):
    v1_baseline_count: int = Field(ge=0)
    proposed_plan_item_count: int = Field(ge=0)
    shadow_member_count: int = Field(ge=0)
    shadow_group_count: int = Field(ge=0)
    excluded_plan_item_count: int = Field(ge=0)
    exact_baseline_match_group_count: int = Field(ge=0)
    related_to_baseline_group_count: int = Field(ge=0)
    not_in_baseline_group_count: int = Field(ge=0)
    conflicting_with_baseline_group_count: int = Field(ge=0)
    not_evaluated_group_count: int = Field(ge=0)
    valid_group_count: int = Field(ge=0)
    invalid_group_count: int = Field(ge=0)
    counts_by_source: dict[CandidateSource, int] = Field(default_factory=dict)
    counts_by_rule_family: dict[UnifiedRuleFamily, int] = Field(default_factory=dict)
    counts_by_group_status: dict[UnifiedShadowGroupStatus, int] = Field(default_factory=dict)
    counts_by_baseline_comparison: dict[UnifiedShadowComparisonKind, int] = Field(
        default_factory=dict
    )
    counts_by_exclusion_reason: dict[UnifiedShadowExclusionReason, int] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def _check_group_counts_reconcile(self) -> UnifiedCandidatesShadowSummary:
        comparison_total = (
            self.exact_baseline_match_group_count
            + self.related_to_baseline_group_count
            + self.not_in_baseline_group_count
            + self.conflicting_with_baseline_group_count
            + self.not_evaluated_group_count
        )
        if comparison_total != self.shadow_group_count:
            raise ValueError(
                "la suma de conteos por comparacion con baseline no coincide con shadow_group_count"
            )
        if self.valid_group_count + self.invalid_group_count != self.shadow_group_count:
            raise ValueError(
                "valid_group_count + invalid_group_count no coincide con shadow_group_count"
            )
        if sum(self.counts_by_group_status.values()) != self.shadow_group_count:
            raise ValueError("suma de counts_by_group_status no coincide con shadow_group_count")
        if sum(self.counts_by_baseline_comparison.values()) != self.shadow_group_count:
            raise ValueError(
                "suma de counts_by_baseline_comparison no coincide con shadow_group_count"
            )
        if sum(self.counts_by_exclusion_reason.values()) != self.excluded_plan_item_count:
            raise ValueError(
                "suma de counts_by_exclusion_reason no coincide con excluded_plan_item_count"
            )
        if (
            self.proposed_plan_item_count
            != self.shadow_member_count + self.excluded_plan_item_count
        ):
            raise ValueError(
                "proposed_plan_item_count debe ser igual a shadow_member_count + "
                "excluded_plan_item_count (Parte 9)"
            )
        return self


class UnifiedCandidatesShadowArtifact(AltamiraBaseModel):
    """Contenedor persistido en `<run_dir>/diagnostics/unified-
    candidates-shadow.json`. NO contractual, sin timestamps, sin
    `source_text` completo, sin rutas absolutas, sin el manifiesto
    humano completo: dos ejecuciones sobre los mismos artefactos de
    entrada deben producir bytes identicos."""

    schema_version: Literal["1.0"] = "1.0"
    generator_version: Literal["1.0"] = "1.0"
    policy_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    source_package_hash: Sha256Hex
    candidate_v1_artifact_hash: Sha256Hex
    v2_artifact_hash: Sha256Hex | None = None
    interprocedural_artifact_hash: Sha256Hex | None = None
    assessment_artifact_hash: Sha256Hex
    review_package_hash: Sha256Hex
    promotion_plan_hash: Sha256Hex
    source_artifact_hashes: dict[str, Sha256Hex] = Field(default_factory=dict)
    summary: UnifiedCandidatesShadowSummary
    baseline_candidates: list[UnifiedBaselineCandidateReference] = Field(default_factory=list)
    shadow_members: list[UnifiedShadowSourceMember] = Field(default_factory=list)
    shadow_groups: list[UnifiedShadowCandidateGroup] = Field(default_factory=list)
    excluded_plan_items: list[UnifiedShadowExcludedPlanItem] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_baseline_reference_id_unique(self) -> UnifiedCandidatesShadowArtifact:
        ids = [ref.baseline_reference_id for ref in self.baseline_candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("baseline_candidates contiene baseline_reference_id duplicado")
        if ids != sorted(ids):
            raise ValueError("baseline_candidates no esta ordenado por baseline_reference_id")
        return self

    @model_validator(mode="after")
    def _check_member_id_unique_and_no_v1_duplicates(self) -> UnifiedCandidatesShadowArtifact:
        ids = [member.member_id for member in self.shadow_members]
        if len(ids) != len(set(ids)):
            raise ValueError("shadow_members contiene member_id duplicado")
        if ids != sorted(ids):
            raise ValueError("shadow_members no esta ordenado por member_id")
        seen: set[tuple[CandidateSource, str]] = set()
        for member in self.shadow_members:
            key = (member.source, member.source_candidate_id)
            if key in seen:
                raise ValueError(
                    f"source_candidate_id {member.source_candidate_id!r} de la fuente "
                    f"{member.source.value} aparece en mas de un shadow member "
                    "(invariante 5)"
                )
            seen.add(key)
        return self

    @model_validator(mode="after")
    def _check_group_id_unique(self) -> UnifiedCandidatesShadowArtifact:
        ids = [group.unified_shadow_candidate_id for group in self.shadow_groups]
        if len(ids) != len(set(ids)):
            raise ValueError("shadow_groups contiene unified_shadow_candidate_id duplicado")
        if ids != sorted(ids):
            raise ValueError("shadow_groups no esta ordenado por unified_shadow_candidate_id")
        return self

    @model_validator(mode="after")
    def _check_exclusion_id_unique(self) -> UnifiedCandidatesShadowArtifact:
        ids = [item.exclusion_id for item in self.excluded_plan_items]
        if len(ids) != len(set(ids)):
            raise ValueError("excluded_plan_items contiene exclusion_id duplicado")
        if ids != sorted(ids):
            raise ValueError("excluded_plan_items no esta ordenado por exclusion_id")
        return self

    @model_validator(mode="after")
    def _check_every_member_belongs_to_exactly_one_group(self) -> UnifiedCandidatesShadowArtifact:
        member_ids = {member.member_id for member in self.shadow_members}
        seen: dict[str, str] = {}
        for group in self.shadow_groups:
            for member_id in group.member_ids:
                if member_id not in member_ids:
                    raise ValueError(
                        f"UnifiedShadowCandidateGroup({group.unified_shadow_candidate_id!r}) "
                        f"referencia un member_id inexistente: {member_id!r}"
                    )
                previous = seen.get(member_id)
                if previous is not None:
                    raise ValueError(
                        f"member_id {member_id!r} pertenece a mas de un grupo "
                        f"({previous!r} y {group.unified_shadow_candidate_id!r})"
                    )
                seen[member_id] = group.unified_shadow_candidate_id
        missing = member_ids - set(seen)
        if missing:
            raise ValueError(f"shadow members sin grupo: {sorted(missing)}")
        return self

    @model_validator(mode="after")
    def _check_baseline_match_ids_exist(self) -> UnifiedCandidatesShadowArtifact:
        baseline_ids = {ref.baseline_reference_id for ref in self.baseline_candidates}
        for group in self.shadow_groups:
            for field_name in (
                "exact_baseline_reference_ids",
                "related_baseline_reference_ids",
                "conflicting_baseline_reference_ids",
            ):
                for baseline_id in getattr(group, field_name):
                    if baseline_id not in baseline_ids:
                        raise ValueError(
                            f"UnifiedShadowCandidateGroup("
                            f"{group.unified_shadow_candidate_id!r}).{field_name} referencia "
                            f"un baseline_reference_id inexistente: {baseline_id!r}"
                        )
        return self

    @model_validator(mode="after")
    def _check_plan_items_appear_exactly_once(self) -> UnifiedCandidatesShadowArtifact:
        member_plan_item_ids = [member.plan_item_id for member in self.shadow_members]
        excluded_plan_item_ids = [item.plan_item_id for item in self.excluded_plan_items]
        if len(member_plan_item_ids) != len(set(member_plan_item_ids)):
            raise ValueError("shadow_members contiene plan_item_id duplicado")
        if len(excluded_plan_item_ids) != len(set(excluded_plan_item_ids)):
            raise ValueError("excluded_plan_items contiene plan_item_id duplicado")
        overlap = set(member_plan_item_ids) & set(excluded_plan_item_ids)
        if overlap:
            raise ValueError(
                f"plan_item_id presente tanto en shadow_members como en "
                f"excluded_plan_items: {sorted(overlap)} (invariante 9)"
            )
        return self

    @model_validator(mode="after")
    def _check_group_single_family_program_target_output(
        self,
    ) -> UnifiedCandidatesShadowArtifact:
        member_by_id = {member.member_id: member for member in self.shadow_members}
        for group in self.shadow_groups:
            members = [member_by_id[member_id] for member_id in group.member_ids]
            families = {member.rule_family for member in members}
            if len(families) > 1 or (families and group.rule_family not in families):
                raise ValueError(
                    f"UnifiedShadowCandidateGroup({group.unified_shadow_candidate_id!r}): "
                    "todos los miembros deben compartir una unica rule_family "
                    "(invariante 18)"
                )
            targets = {member.target for member in members}
            if len(targets) > 1:
                raise ValueError(
                    f"UnifiedShadowCandidateGroup({group.unified_shadow_candidate_id!r}): "
                    "todos los miembros deben compartir un unico target (invariante 19)"
                )
            outputs = {member.output_literal for member in members}
            if len(outputs) > 1:
                raise ValueError(
                    f"UnifiedShadowCandidateGroup({group.unified_shadow_candidate_id!r}): "
                    "todos los miembros deben compartir un unico output_literal "
                    "(invariante 20)"
                )
            programs = {member.program for member in members}
            if len(programs) > 1:
                raise ValueError(
                    f"UnifiedShadowCandidateGroup({group.unified_shadow_candidate_id!r}): "
                    "todos los miembros deben compartir un ambito funcional compatible "
                    "(mismo program, invariante 21)"
                )
        return self

    @model_validator(mode="after")
    def _check_diagnostics_sorted_and_unique(self) -> UnifiedCandidatesShadowArtifact:
        if self.diagnostics != _ordered_unique_strings(self.diagnostics):
            raise ValueError("diagnostics debe estar ordenado alfabeticamente y sin duplicados")
        return self

    @model_validator(mode="after")
    def _check_summary_matches_content(self) -> UnifiedCandidatesShadowArtifact:
        if self.summary.v1_baseline_count != len(self.baseline_candidates):
            raise ValueError(
                "summary.v1_baseline_count no coincide con la cantidad real de baseline_candidates"
            )
        if self.summary.shadow_member_count != len(self.shadow_members):
            raise ValueError(
                "summary.shadow_member_count no coincide con la cantidad real de shadow_members"
            )
        if self.summary.shadow_group_count != len(self.shadow_groups):
            raise ValueError(
                "summary.shadow_group_count no coincide con la cantidad real de shadow_groups"
            )
        if self.summary.excluded_plan_item_count != len(self.excluded_plan_items):
            raise ValueError(
                "summary.excluded_plan_item_count no coincide con la cantidad real de "
                "excluded_plan_items"
            )
        expected_by_source: dict[CandidateSource, int] = {}
        expected_by_family: dict[UnifiedRuleFamily, int] = {}
        for member in self.shadow_members:
            expected_by_source[member.source] = expected_by_source.get(member.source, 0) + 1
            expected_by_family[member.rule_family] = (
                expected_by_family.get(member.rule_family, 0) + 1
            )
        if self.summary.counts_by_source != expected_by_source:
            raise ValueError("summary.counts_by_source no coincide con la agregacion real")
        if self.summary.counts_by_rule_family != expected_by_family:
            raise ValueError("summary.counts_by_rule_family no coincide con la agregacion real")

        expected_by_status: dict[UnifiedShadowGroupStatus, int] = {}
        expected_by_comparison: dict[UnifiedShadowComparisonKind, int] = {}
        for group in self.shadow_groups:
            expected_by_status[group.status] = expected_by_status.get(group.status, 0) + 1
            expected_by_comparison[group.comparison_to_v1] = (
                expected_by_comparison.get(group.comparison_to_v1, 0) + 1
            )
        if self.summary.counts_by_group_status != expected_by_status:
            raise ValueError("summary.counts_by_group_status no coincide con la agregacion real")
        if self.summary.counts_by_baseline_comparison != expected_by_comparison:
            raise ValueError(
                "summary.counts_by_baseline_comparison no coincide con la agregacion real"
            )

        expected_by_reason: dict[UnifiedShadowExclusionReason, int] = {}
        for item in self.excluded_plan_items:
            expected_by_reason[item.reason] = expected_by_reason.get(item.reason, 0) + 1
        if self.summary.counts_by_exclusion_reason != expected_by_reason:
            raise ValueError(
                "summary.counts_by_exclusion_reason no coincide con la agregacion real"
            )
        return self
