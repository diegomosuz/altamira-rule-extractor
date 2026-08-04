"""Contrato tipado de la evaluacion de activacion unificada (Fase 14A
de la ampliacion semantica, `feat/controlled-unified-activation`).

Diagnostico, NO contractual para el pipeline V1: persiste en
`<run_dir>/diagnostics/unified-activation-evaluation.json`. Compara el
pipeline V1 (`CandidateArtifact`, `GuardrailCandidateArtifact` cuando
GUARDRAILS_APPLIED tuvo exito) con el downstream unified shadow
(`UnifiedShadowDownstreamArtifact`, Fase 13) UNICAMENTE mediante
igualdad demostrable de campos estructurales -- nunca fuzzy matching,
distancia de edicion, embeddings ni LLM (ver `pipeline/
unified_activation_comparator.py`).

Invariante estructural de Fase 14A, verificada en el TIPO (no solo en
un valor por defecto): `materialization_enabled: Literal[False]` y
`effective_lane` JAMAS puede ser `UNIFIED_SHADOW` (ver
`_check_effective_lane_never_unified`) -- ningun modo, ninguna
configuracion, selecciona `unified` como productor real todavia. Esa
decision (Fase 14B) permanece fuera de alcance.

Sin timestamps. Sin rutas absolutas. Sin la configuracion completa
copiada (solo `config_hash`, nunca el YAML original). Sin secretos,
endpoint, modelo ni API key."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, Sha256Hex
from .unified_activation_config import UnifiedActivationMode, UnifiedActivationProviderPolicy
from .unified_shadow_downstream import UnifiedShadowGuardrailStatus


class UnifiedActivationLane(StrEnum):
    """`NONE` representa ausencia de lane aplicable (p. ej. antes de
    cualquier evaluacion, o un fallo de configuracion que impide
    determinar un lane)."""

    V1 = "V1"
    UNIFIED_SHADOW = "UNIFIED_SHADOW"
    NONE = "NONE"


class UnifiedActivationComparisonLevel(StrEnum):
    """Nivel del artefacto comparable -- NUNCA se compara un candidato
    estructural V1 (`RULE_CANDIDATE`) contra un draft/regla unified
    como si fueran equivalentes sin declarar este nivel (Fase 14A
    Parte 5). `EXACT_EQUIVALENT` exige el MISMO nivel en ambos lados."""

    RULE = "RULE"
    CANDIDATE = "CANDIDATE"


class UnifiedActivationComparisonKind(StrEnum):
    EXACT_EQUIVALENT = "EXACT_EQUIVALENT"
    UNIFIED_ADDITIVE = "UNIFIED_ADDITIVE"
    V1_ONLY = "V1_ONLY"
    RELATED = "RELATED"
    CONFLICTING = "CONFLICTING"
    NOT_COMPARABLE = "NOT_COMPARABLE"
    NOT_EVALUATED = "NOT_EVALUATED"


class UnifiedActivationReadinessDisposition(StrEnum):
    V1_ONLY_READY = "V1_ONLY_READY"
    READY_FOR_SHADOW_COMPARISON = "READY_FOR_SHADOW_COMPARISON"
    READY_FOR_UNIFIED_CANARY = "READY_FOR_UNIFIED_CANARY"
    READY_FOR_PRIMARY_TRIAL = "READY_FOR_PRIMARY_TRIAL"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    BLOCKED = "BLOCKED"
    NOT_EVALUATED = "NOT_EVALUATED"


class UnifiedActivationDecision(StrEnum):
    """Decision informativa de lo que la FUTURA Fase 14B haria --
    nunca una accion ejecutada por esta fase (ver invariante
    `materialization_enabled=False` y `effective_lane != UNIFIED_
    SHADOW`)."""

    KEEP_V1 = "KEEP_V1"
    RUN_SHADOW_COMPARISON = "RUN_SHADOW_COMPARISON"
    SELECT_UNIFIED_CANARY_DRY_RUN = "SELECT_UNIFIED_CANARY_DRY_RUN"
    SELECT_UNIFIED_PRIMARY_DRY_RUN = "SELECT_UNIFIED_PRIMARY_DRY_RUN"
    FALLBACK_TO_V1_PLANNED = "FALLBACK_TO_V1_PLANNED"
    DO_NOT_ACTIVATE = "DO_NOT_ACTIVATE"


class UnifiedActivationIssueSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    BLOCKING = "BLOCKING"


class UnifiedActivationIssueCode(StrEnum):
    ACTIVATION_CONFIG_INVALID = "ACTIVATION_CONFIG_INVALID"
    PROVIDER_POLICY_NOT_ALLOWED = "PROVIDER_POLICY_NOT_ALLOWED"
    MATERIALIZATION_NOT_ALLOWED = "MATERIALIZATION_NOT_ALLOWED"
    V1_ARTIFACT_MISSING = "V1_ARTIFACT_MISSING"
    UNIFIED_ARTIFACT_MISSING = "UNIFIED_ARTIFACT_MISSING"
    VALIDATION_REPORT_MISSING = "VALIDATION_REPORT_MISSING"
    DOWNSTREAM_ARTIFACT_MISSING = "DOWNSTREAM_ARTIFACT_MISSING"
    SOURCE_HASH_MISMATCH = "SOURCE_HASH_MISMATCH"
    RUN_ID_MISMATCH = "RUN_ID_MISMATCH"
    CANARY_NOT_SELECTED = "CANARY_NOT_SELECTED"
    CANARY_DENYLISTED = "CANARY_DENYLISTED"
    VALIDATION_NOT_QUALIFIED = "VALIDATION_NOT_QUALIFIED"
    DOWNSTREAM_NOT_COMPLETED = "DOWNSTREAM_NOT_COMPLETED"
    GUARDRAIL_REJECTIONS_PRESENT = "GUARDRAIL_REJECTIONS_PRESENT"
    TECHNICAL_FAILURES_PRESENT = "TECHNICAL_FAILURES_PRESENT"
    V1_UNIFIED_EXACT_EQUIVALENT = "V1_UNIFIED_EXACT_EQUIVALENT"
    UNIFIED_INCREMENTAL_RESULT = "UNIFIED_INCREMENTAL_RESULT"
    V1_RESULT_NOT_REPRESENTED = "V1_RESULT_NOT_REPRESENTED"
    V1_UNIFIED_CONFLICT = "V1_UNIFIED_CONFLICT"
    RESULT_NOT_COMPARABLE = "RESULT_NOT_COMPARABLE"
    FALLBACK_REQUIRED = "FALLBACK_REQUIRED"
    FUNCTIONAL_VALIDATION_REQUIRED = "FUNCTIONAL_VALIDATION_REQUIRED"


_MESSAGE_CODE_BY_ISSUE_CODE: dict[UnifiedActivationIssueCode, str] = {
    code: f"MSG_{code.value}" for code in UnifiedActivationIssueCode
}


def _ordered_unique(values: Iterable[str]) -> list[str]:
    return sorted(set(values))


class UnifiedActivationCanarySelection(AltamiraBaseModel):
    """Salida tipada del selector deterministico de canary
    (`pipeline/unified_activation_canary_selector.py`). `matched_
    allowlist` y `matched_denylist` NO son mutuamente excluyentes: un
    hash puede pertenecer a ambas listas simultaneamente (el contrato
    de configuracion lo permite deliberadamente) -- en ese caso ambos
    son `True`, pero `selected` es siempre `False` (ver `_check_
    denylist_precedence`: la denylist prevalece sin excepcion)."""

    selected: bool
    bucket: int | None = Field(default=None, ge=0, le=99)
    reason: str = Field(min_length=1)
    matched_allowlist: bool
    matched_denylist: bool
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_denylist_precedence(self) -> UnifiedActivationCanarySelection:
        if self.matched_denylist and self.selected:
            raise ValueError(
                "matched_denylist=True nunca puede coexistir con selected=True -- "
                "la denylist siempre prevalece"
            )
        return self

    @model_validator(mode="after")
    def _check_diagnostics_sorted_and_unique(self) -> UnifiedActivationCanarySelection:
        if self.diagnostics != _ordered_unique(self.diagnostics):
            raise ValueError("diagnostics debe estar ordenado alfabeticamente y sin duplicados")
        return self


class UnifiedActivationV1Reference(AltamiraBaseModel):
    """Referencia comparable de UN resultado V1 -- adaptada por
    `pipeline/unified_activation_reference_adapters.py::
    adapt_v1_references`, nunca fabricada. `level=RULE` UNICAMENTE
    cuando el candidato alcanzo `artifacts/09-guardrails/`
    (EVIDENCE_VALIDATED -- GUARDRAILS_APPLIED es fail-fast, un
    candidato REJECTED nunca llega a persistirse ahi, ver docstring de
    `adapt_v1_references`); en cualquier otro caso `level=CANDIDATE`
    (unicamente `artifacts/06-candidates.json`). `target` es SIEMPRE
    `None`: V1 nunca lo expone (mismo principio que `pipeline/
    candidate_source_adapters.py::adapt_v1_candidates`)."""

    reference_id: str = Field(min_length=1)
    source_candidate_id: str = Field(min_length=1)
    level: UnifiedActivationComparisonLevel
    rule_draft_id: str | None = None
    rule_id: str | None = None
    rule_family: str | None = None
    program: str | None = None
    paragraph: str | None = None
    target: str | None = None
    output_literal: str | None = None
    statement: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    provenance_references: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_rule_level_requires_draft(self) -> UnifiedActivationV1Reference:
        if self.level == UnifiedActivationComparisonLevel.RULE and self.rule_draft_id is None:
            raise ValueError(
                f"UnifiedActivationV1Reference({self.reference_id!r}): level=RULE exige "
                "rule_draft_id"
            )
        return self

    @model_validator(mode="after")
    def _check_lists_sorted_and_unique(self) -> UnifiedActivationV1Reference:
        label = f"UnifiedActivationV1Reference({self.reference_id!r})"
        for field_name in ("evidence_ids", "provenance_references", "diagnostics"):
            values = getattr(self, field_name)
            if values != _ordered_unique(values):
                raise ValueError(f"{label}: {field_name} debe estar ordenado y sin duplicados")
        return self


class UnifiedActivationUnifiedReference(AltamiraBaseModel):
    """Referencia comparable de UN grupo unified shadow -- adaptada
    por `adapt_unified_references`. Identidad principal =
    `group_id` (`unified_shadow_candidate_id`, Fase 11), nunca un
    `source_candidate_id` individual -- ningun member se elige como
    "ganador" (mismo principio que Fase 13)."""

    reference_id: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
    level: UnifiedActivationComparisonLevel
    member_ids: list[str] = Field(default_factory=list)
    source_candidate_ids: list[str] = Field(default_factory=list)
    rule_draft_record_id: str | None = None
    rule_family: str = Field(min_length=1)
    program: str = Field(min_length=1)
    target: str | None = None
    output_literal: str | None = None
    statement: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    provenance_references: list[str] = Field(default_factory=list)
    guardrail_status: UnifiedShadowGuardrailStatus = UnifiedShadowGuardrailStatus.NOT_EVALUATED
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_rule_level_requires_draft(self) -> UnifiedActivationUnifiedReference:
        if (
            self.level == UnifiedActivationComparisonLevel.RULE
            and self.rule_draft_record_id is None
        ):
            raise ValueError(
                f"UnifiedActivationUnifiedReference({self.reference_id!r}): level=RULE exige "
                "rule_draft_record_id"
            )
        return self

    @model_validator(mode="after")
    def _check_lists_sorted_and_unique(self) -> UnifiedActivationUnifiedReference:
        label = f"UnifiedActivationUnifiedReference({self.reference_id!r})"
        for field_name in (
            "member_ids",
            "source_candidate_ids",
            "evidence_ids",
            "provenance_references",
            "diagnostics",
        ):
            values = getattr(self, field_name)
            if values != _ordered_unique(values):
                raise ValueError(f"{label}: {field_name} debe estar ordenado y sin duplicados")
        return self


class UnifiedActivationComparison(AltamiraBaseModel):
    comparison_id: str = Field(min_length=1)
    kind: UnifiedActivationComparisonKind
    v1_reference_ids: list[str] = Field(default_factory=list)
    unified_reference_ids: list[str] = Field(default_factory=list)
    shared_family: str | None = None
    shared_program: str | None = None
    shared_target: str | None = None
    shared_output_literal: str | None = None
    reason_code: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_lists_sorted_and_unique(self) -> UnifiedActivationComparison:
        label = f"UnifiedActivationComparison({self.comparison_id!r})"
        for field_name in (
            "v1_reference_ids",
            "unified_reference_ids",
            "evidence_ids",
            "diagnostics",
        ):
            values = getattr(self, field_name)
            if values != _ordered_unique(values):
                raise ValueError(f"{label}: {field_name} debe estar ordenado y sin duplicados")
        return self

    @model_validator(mode="after")
    def _check_at_least_one_side_present(self) -> UnifiedActivationComparison:
        if not self.v1_reference_ids and not self.unified_reference_ids:
            raise ValueError(
                f"UnifiedActivationComparison({self.comparison_id!r}): exige al menos una "
                "referencia (V1 o unified)"
            )
        return self


class UnifiedActivationIssue(AltamiraBaseModel):
    issue_id: str = Field(min_length=1)
    code: UnifiedActivationIssueCode
    severity: UnifiedActivationIssueSeverity
    related_v1_reference_ids: list[str] = Field(default_factory=list)
    related_unified_reference_ids: list[str] = Field(default_factory=list)
    comparison_ids: list[str] = Field(default_factory=list)
    message_code: str = Field(min_length=1)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_message_code_matches_catalog(self) -> UnifiedActivationIssue:
        expected = _MESSAGE_CODE_BY_ISSUE_CODE[self.code]
        if self.message_code != expected:
            raise ValueError(
                f"UnifiedActivationIssue({self.issue_id!r}): message_code={self.message_code!r} "
                f"no coincide con el catalogo esperado para {self.code.value!r}: {expected!r}"
            )
        return self

    @model_validator(mode="after")
    def _check_lists_sorted_and_unique(self) -> UnifiedActivationIssue:
        label = f"UnifiedActivationIssue({self.issue_id!r})"
        for field_name in (
            "related_v1_reference_ids",
            "related_unified_reference_ids",
            "comparison_ids",
            "diagnostics",
        ):
            values = getattr(self, field_name)
            if values != _ordered_unique(values):
                raise ValueError(f"{label}: {field_name} debe estar ordenado y sin duplicados")
        return self


class UnifiedActivationEvaluationSummary(AltamiraBaseModel):
    v1_reference_count: int = Field(ge=0)
    unified_reference_count: int = Field(ge=0)
    exact_equivalent_count: int = Field(ge=0)
    unified_additive_count: int = Field(ge=0)
    v1_only_count: int = Field(ge=0)
    related_count: int = Field(ge=0)
    conflicting_count: int = Field(ge=0)
    not_comparable_count: int = Field(ge=0)
    not_evaluated_count: int = Field(ge=0)
    guardrail_passed_count: int = Field(ge=0)
    guardrail_rejected_count: int = Field(ge=0)
    technical_failure_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    blocking_issue_count: int = Field(ge=0)
    counts_by_comparison_kind: dict[UnifiedActivationComparisonKind, int] = Field(
        default_factory=dict
    )
    counts_by_issue_severity: dict[UnifiedActivationIssueSeverity, int] = Field(
        default_factory=dict
    )
    counts_by_decision: dict[UnifiedActivationDecision, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_comparison_counts_reconcile(self) -> UnifiedActivationEvaluationSummary:
        total = (
            self.exact_equivalent_count
            + self.unified_additive_count
            + self.v1_only_count
            + self.related_count
            + self.conflicting_count
            + self.not_comparable_count
            + self.not_evaluated_count
        )
        if sum(self.counts_by_comparison_kind.values()) != total:
            raise ValueError(
                "suma de counts_by_comparison_kind no coincide con la suma de los "
                "conteos individuales por tipo de comparacion"
            )
        return self


class UnifiedActivationEvaluationArtifact(AltamiraBaseModel):
    """Contenedor persistido en `<run_dir>/diagnostics/unified-
    activation-evaluation.json`. NO contractual para el pipeline V1,
    sin timestamps, sin rutas absolutas, sin la configuracion completa
    copiada: dos ejecuciones sobre los mismos artefactos de entrada
    producen bytes identicos."""

    schema_version: Literal["1.0"] = "1.0"
    evaluator_version: Literal["1.0"] = "1.0"
    policy_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    source_package_hash: Sha256Hex
    config_hash: Sha256Hex
    mode: UnifiedActivationMode
    provider_policy: UnifiedActivationProviderPolicy
    materialization_enabled: Literal[False] = False
    canary_selection: UnifiedActivationCanarySelection | None = None
    requested_lane: UnifiedActivationLane
    effective_lane: UnifiedActivationLane
    fallback_lane: UnifiedActivationLane
    readiness_disposition: UnifiedActivationReadinessDisposition
    activation_decision: UnifiedActivationDecision
    candidate_v1_artifact_hash: Sha256Hex | None = None
    unified_candidates_shadow_hash: Sha256Hex | None = None
    validation_report_hash: Sha256Hex | None = None
    downstream_artifact_hash: Sha256Hex | None = None
    v1_references: list[UnifiedActivationV1Reference] = Field(default_factory=list)
    unified_references: list[UnifiedActivationUnifiedReference] = Field(default_factory=list)
    comparisons: list[UnifiedActivationComparison] = Field(default_factory=list)
    issues: list[UnifiedActivationIssue] = Field(default_factory=list)
    summary: UnifiedActivationEvaluationSummary
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_provider_policy_is_fake_only(self) -> UnifiedActivationEvaluationArtifact:
        if self.provider_policy != UnifiedActivationProviderPolicy.DETERMINISTIC_FAKE_ONLY:
            raise ValueError("provider_policy debe ser siempre DETERMINISTIC_FAKE_ONLY en Fase 14A")
        return self

    @model_validator(mode="after")
    def _check_effective_lane_never_unified(self) -> UnifiedActivationEvaluationArtifact:
        """Invariante estructural de Fase 14A: NUNCA se selecciona
        `unified` como lane efectivo -- esa decision (Fase 14B)
        permanece completamente fuera de alcance."""
        if self.effective_lane == UnifiedActivationLane.UNIFIED_SHADOW:
            raise ValueError(
                "effective_lane nunca puede ser UNIFIED_SHADOW en Fase 14A -- esta fase es "
                "exclusivamente control plane y dry-run"
            )
        return self

    @model_validator(mode="after")
    def _check_ids_unique_and_ordered(self) -> UnifiedActivationEvaluationArtifact:
        for label, ids in (
            ("v1_references", [r.reference_id for r in self.v1_references]),
            ("unified_references", [r.reference_id for r in self.unified_references]),
            ("comparisons", [c.comparison_id for c in self.comparisons]),
            ("issues", [i.issue_id for i in self.issues]),
        ):
            if len(ids) != len(set(ids)):
                raise ValueError(f"{label} contiene un id duplicado")
            if ids != sorted(ids):
                raise ValueError(f"{label} no esta ordenado por id")
        return self

    @model_validator(mode="after")
    def _check_comparison_pairs_serialized_once(self) -> UnifiedActivationEvaluationArtifact:
        seen: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
        for comparison in self.comparisons:
            key = (
                tuple(sorted(comparison.v1_reference_ids)),
                tuple(sorted(comparison.unified_reference_ids)),
            )
            if key in seen:
                raise ValueError(
                    f"el par (v1_reference_ids, unified_reference_ids)={key} aparece en mas "
                    "de una comparacion -- cada par semantico se serializa una sola vez"
                )
            seen.add(key)
        return self

    @model_validator(mode="after")
    def _check_comparison_references_exist(self) -> UnifiedActivationEvaluationArtifact:
        known_v1_ids = {r.reference_id for r in self.v1_references}
        known_unified_ids = {r.reference_id for r in self.unified_references}
        for comparison in self.comparisons:
            for ref_id in comparison.v1_reference_ids:
                if ref_id not in known_v1_ids:
                    raise ValueError(
                        f"UnifiedActivationComparison({comparison.comparison_id!r}) referencia "
                        f"un v1_reference_id inexistente: {ref_id!r}"
                    )
            for ref_id in comparison.unified_reference_ids:
                if ref_id not in known_unified_ids:
                    raise ValueError(
                        f"UnifiedActivationComparison({comparison.comparison_id!r}) referencia "
                        f"un unified_reference_id inexistente: {ref_id!r}"
                    )
        return self

    @model_validator(mode="after")
    def _check_issue_references_exist(self) -> UnifiedActivationEvaluationArtifact:
        known_v1_ids = {r.reference_id for r in self.v1_references}
        known_unified_ids = {r.reference_id for r in self.unified_references}
        known_comparison_ids = {c.comparison_id for c in self.comparisons}
        for issue in self.issues:
            for ref_id in issue.related_v1_reference_ids:
                if ref_id not in known_v1_ids:
                    raise ValueError(
                        f"UnifiedActivationIssue({issue.issue_id!r}) referencia un "
                        f"v1_reference_id inexistente: {ref_id!r}"
                    )
            for ref_id in issue.related_unified_reference_ids:
                if ref_id not in known_unified_ids:
                    raise ValueError(
                        f"UnifiedActivationIssue({issue.issue_id!r}) referencia un "
                        f"unified_reference_id inexistente: {ref_id!r}"
                    )
            for comparison_id in issue.comparison_ids:
                if comparison_id not in known_comparison_ids:
                    raise ValueError(
                        f"UnifiedActivationIssue({issue.issue_id!r}) referencia un "
                        f"comparison_id inexistente: {comparison_id!r}"
                    )
        return self

    @model_validator(mode="after")
    def _check_summary_reconciles(self) -> UnifiedActivationEvaluationArtifact:
        if self.summary.v1_reference_count != len(self.v1_references):
            raise ValueError(
                "summary.v1_reference_count no coincide con la cantidad real de v1_references"
            )
        if self.summary.unified_reference_count != len(self.unified_references):
            raise ValueError(
                "summary.unified_reference_count no coincide con la cantidad real de "
                "unified_references"
            )
        expected_by_kind: dict[str, int] = {}
        for comparison in self.comparisons:
            expected_by_kind[comparison.kind.value] = (
                expected_by_kind.get(comparison.kind.value, 0) + 1
            )
        actual_by_kind = {k.value: v for k, v in self.summary.counts_by_comparison_kind.items()}
        if actual_by_kind != expected_by_kind:
            raise ValueError(
                "summary.counts_by_comparison_kind no coincide con la agregacion real de "
                "comparisons"
            )
        expected_by_severity: dict[str, int] = {}
        for issue in self.issues:
            expected_by_severity[issue.severity.value] = (
                expected_by_severity.get(issue.severity.value, 0) + 1
            )
        actual_by_severity = {k.value: v for k, v in self.summary.counts_by_issue_severity.items()}
        if actual_by_severity != expected_by_severity:
            raise ValueError(
                "summary.counts_by_issue_severity no coincide con la agregacion real de issues"
            )
        expected_error_count = expected_by_severity.get(
            UnifiedActivationIssueSeverity.ERROR.value, 0
        )
        expected_warning_count = expected_by_severity.get(
            UnifiedActivationIssueSeverity.WARNING.value, 0
        )
        expected_blocking_count = expected_by_severity.get(
            UnifiedActivationIssueSeverity.BLOCKING.value, 0
        )
        if self.summary.error_count != expected_error_count:
            raise ValueError("summary.error_count no coincide con la cantidad real de issues ERROR")
        if self.summary.warning_count != expected_warning_count:
            raise ValueError(
                "summary.warning_count no coincide con la cantidad real de issues WARNING"
            )
        if self.summary.blocking_issue_count != expected_blocking_count:
            raise ValueError(
                "summary.blocking_issue_count no coincide con la cantidad real de issues BLOCKING"
            )
        return self

    @model_validator(mode="after")
    def _check_diagnostics_sorted_and_unique(self) -> UnifiedActivationEvaluationArtifact:
        if self.diagnostics != _ordered_unique(self.diagnostics):
            raise ValueError("diagnostics debe estar ordenado alfabeticamente y sin duplicados")
        return self


__all__ = [
    "UnifiedActivationCanarySelection",
    "UnifiedActivationComparison",
    "UnifiedActivationComparisonKind",
    "UnifiedActivationComparisonLevel",
    "UnifiedActivationDecision",
    "UnifiedActivationEvaluationArtifact",
    "UnifiedActivationEvaluationSummary",
    "UnifiedActivationIssue",
    "UnifiedActivationIssueCode",
    "UnifiedActivationIssueSeverity",
    "UnifiedActivationLane",
    "UnifiedActivationReadinessDisposition",
    "UnifiedActivationUnifiedReference",
    "UnifiedActivationV1Reference",
]
