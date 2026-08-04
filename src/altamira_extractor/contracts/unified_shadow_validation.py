"""Contrato tipado del reporte de validacion diferencial del artefacto
unificado de candidatos en shadow mode (Fase 12 de la ampliacion
semantica, `feat/unified-shadow-differential-validation`).

Diagnostico, NO contractual: persiste en `<run_dir>/diagnostics/
unified-shadow-validation-report.json`. Determina si un
`UnifiedCandidatesShadowArtifact` (Fase 11) cumple las condiciones
TECNICAS minimas para alimentar, en una fase POSTERIOR, un flujo
downstream tambien en shadow mode -- nunca afirma correccion
funcional, nunca promueve nada, nunca modifica ninguna fuente ni el
propio `UnifiedCandidatesShadowArtifact`.

Principio rector (Fase 12 Parte 1): este modulo distingue DOS
conceptos que NUNCA se confunden entre si:

- VALIDEZ ESTRUCTURAL: verificable automaticamente -- integridad de
  hashes, consistencia de IDs, existencia de fuentes, provenance,
  evidence, agrupacion, duplicados, conflictos, determinismo,
  reconciliacion de conteos.
- CORRECCION FUNCIONAL: NO puede inferirse automaticamente sin
  revision humana o ground truth -- si la regla representa
  correctamente el negocio, si el literal es funcionalmente correcto,
  si una omision es un falso negativo, precision/recall reales. Ver
  `UnifiedShadowValidationIssueCode.FUNCTIONAL_VALIDATION_REQUIRED`,
  que SIEMPRE acompana a un reporte calificado, como recordatorio
  explicito de este limite.

`UnifiedShadowValidationDisposition.QUALIFIED_FOR_DOWNSTREAM_SHADOW`
significa UNICAMENTE que el artefacto supero los gates ESTRUCTURALES
para ejecutar el flujo downstream EN PARALELO y SIN IMPACTO
PRODUCTIVO. NUNCA significa: regla validada funcionalmente, candidato
promovido, precision demostrada, autorizacion productiva -- ver
`docs/UNIFIED_SHADOW_DIFFERENTIAL_VALIDATION.md`."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, Sha256Hex
from .candidate_promotion_assessment import UnifiedRuleFamily
from .unified_candidates_shadow import UnifiedShadowComparisonKind, UnifiedShadowGroupStatus


class UnifiedShadowValidationGate(StrEnum):
    """Los 12 gates -- cada uno aparece EXACTAMENTE una vez en
    `UnifiedShadowValidationReport.gate_results` (invariante 2).
    `DOWNSTREAM_SHADOW_ELIGIBILITY` resume el resultado (Fase 12 Parte
    9): nunca es un gate independiente de los otros 11, siempre se
    deriva de ellos mas la clasificacion por grupo."""

    SOURCE_INTEGRITY = "SOURCE_INTEGRITY"
    BASELINE_COMPLETENESS = "BASELINE_COMPLETENESS"
    PLAN_BINDING_INTEGRITY = "PLAN_BINDING_INTEGRITY"
    MEMBER_SOURCE_RESOLUTION = "MEMBER_SOURCE_RESOLUTION"
    GROUP_INTERNAL_CONSISTENCY = "GROUP_INTERNAL_CONSISTENCY"
    BASELINE_DIFFERENTIAL_SAFETY = "BASELINE_DIFFERENTIAL_SAFETY"
    EVIDENCE_COMPLETENESS = "EVIDENCE_COMPLETENESS"
    PROVENANCE_COMPLETENESS = "PROVENANCE_COMPLETENESS"
    DECISION_TRACEABILITY = "DECISION_TRACEABILITY"
    SUMMARY_RECONCILIATION = "SUMMARY_RECONCILIATION"
    DETERMINISTIC_SERIALIZATION = "DETERMINISTIC_SERIALIZATION"
    DOWNSTREAM_SHADOW_ELIGIBILITY = "DOWNSTREAM_SHADOW_ELIGIBILITY"


class UnifiedShadowGateStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NOT_EVALUATED = "NOT_EVALUATED"


class UnifiedShadowValidationIssueSeverity(StrEnum):
    """`BLOCKING` es estrictamente mas grave que `ERROR`: un `ERROR`
    puede coexistir con `disposition=REVIEW_REQUIRED`; un `BLOCKING`
    exige `disposition=BLOCKED` (invariante 8). Nunca se usan como
    puntaje numerico -- solo como severidad discreta."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    BLOCKING = "BLOCKING"


class UnifiedShadowValidationIssueCode(StrEnum):
    """Catalogo ESTABLE y CERRADO -- nunca texto libre. Cada valor tiene
    un `message_code` registrado 1:1 (`_MESSAGE_CODE_BY_ISSUE_CODE`),
    de modo que la LOGICA nunca depende de un mensaje humano, solo de
    este `code`."""

    SOURCE_ARTIFACT_MISSING = "SOURCE_ARTIFACT_MISSING"
    SOURCE_ARTIFACT_INVALID = "SOURCE_ARTIFACT_INVALID"
    SOURCE_HASH_MISMATCH = "SOURCE_HASH_MISMATCH"
    UNIFIED_ARTIFACT_HASH_MISMATCH = "UNIFIED_ARTIFACT_HASH_MISMATCH"
    BASELINE_CANDIDATE_MISSING = "BASELINE_CANDIDATE_MISSING"
    BASELINE_COUNT_MISMATCH = "BASELINE_COUNT_MISMATCH"
    PLAN_ITEM_UNACCOUNTED = "PLAN_ITEM_UNACCOUNTED"
    PLAN_BINDING_MISMATCH = "PLAN_BINDING_MISMATCH"
    SHADOW_MEMBER_SOURCE_NOT_FOUND = "SHADOW_MEMBER_SOURCE_NOT_FOUND"
    SHADOW_MEMBER_IDENTITY_MISMATCH = "SHADOW_MEMBER_IDENTITY_MISMATCH"
    SHADOW_MEMBER_WITHOUT_APPROVAL = "SHADOW_MEMBER_WITHOUT_APPROVAL"
    SHADOW_MEMBER_WITHOUT_EVIDENCE = "SHADOW_MEMBER_WITHOUT_EVIDENCE"
    SHADOW_MEMBER_WITHOUT_PROVENANCE = "SHADOW_MEMBER_WITHOUT_PROVENANCE"
    GROUP_WITHOUT_MEMBERS = "GROUP_WITHOUT_MEMBERS"
    GROUP_MEMBER_NOT_FOUND = "GROUP_MEMBER_NOT_FOUND"
    GROUP_MULTIPLE_FAMILIES = "GROUP_MULTIPLE_FAMILIES"
    GROUP_MULTIPLE_TARGETS = "GROUP_MULTIPLE_TARGETS"
    GROUP_MULTIPLE_OUTPUTS = "GROUP_MULTIPLE_OUTPUTS"
    GROUP_INCONSISTENT_SCOPE = "GROUP_INCONSISTENT_SCOPE"
    GROUP_UNKNOWN_FAMILY = "GROUP_UNKNOWN_FAMILY"
    GROUP_BLOCKED = "GROUP_BLOCKED"
    GROUP_DUPLICATES_BASELINE = "GROUP_DUPLICATES_BASELINE"
    GROUP_CONFLICTS_WITH_BASELINE = "GROUP_CONFLICTS_WITH_BASELINE"
    GROUP_BASELINE_NOT_EVALUATED = "GROUP_BASELINE_NOT_EVALUATED"
    GROUP_RELATED_TO_BASELINE = "GROUP_RELATED_TO_BASELINE"
    GROUP_NOT_IN_BASELINE = "GROUP_NOT_IN_BASELINE"
    SUMMARY_MISMATCH = "SUMMARY_MISMATCH"
    NON_DETERMINISTIC_SERIALIZATION = "NON_DETERMINISTIC_SERIALIZATION"
    NO_VALID_SHADOW_GROUPS = "NO_VALID_SHADOW_GROUPS"
    FUNCTIONAL_VALIDATION_REQUIRED = "FUNCTIONAL_VALIDATION_REQUIRED"


class UnifiedShadowValidationDisposition(StrEnum):
    QUALIFIED_FOR_DOWNSTREAM_SHADOW = "QUALIFIED_FOR_DOWNSTREAM_SHADOW"
    QUALIFIED_WITH_WARNINGS = "QUALIFIED_WITH_WARNINGS"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    BLOCKED = "BLOCKED"
    NOT_EVALUATED = "NOT_EVALUATED"


def _ordered_unique_strings(values: Iterable[str]) -> list[str]:
    return sorted(set(values))


_MESSAGE_CODE_BY_ISSUE_CODE: dict[UnifiedShadowValidationIssueCode, str] = {
    code: f"MSG_{code.value}" for code in UnifiedShadowValidationIssueCode
}


class UnifiedShadowValidationIssue(AltamiraBaseModel):
    """UN hallazgo estructural, nunca una afirmacion de correccion
    funcional. `message_code` pertenece SIEMPRE al catalogo estable
    derivado de `code` (`_MESSAGE_CODE_BY_ISSUE_CODE`) -- nunca texto
    libre, nunca usado para decidir logica (eso es responsabilidad
    exclusiva de `code`/`severity`)."""

    issue_id: str = Field(min_length=1)
    code: UnifiedShadowValidationIssueCode
    severity: UnifiedShadowValidationIssueSeverity
    gate: UnifiedShadowValidationGate
    message_code: str = Field(min_length=1)
    baseline_reference_ids: list[str] = Field(default_factory=list)
    shadow_member_ids: list[str] = Field(default_factory=list)
    shadow_group_ids: list[str] = Field(default_factory=list)
    plan_item_ids: list[str] = Field(default_factory=list)
    source_candidate_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    provenance_references: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_message_code_registered(self) -> UnifiedShadowValidationIssue:
        expected = _MESSAGE_CODE_BY_ISSUE_CODE[self.code]
        if self.message_code != expected:
            raise ValueError(
                f"UnifiedShadowValidationIssue({self.issue_id!r}): message_code "
                f"{self.message_code!r} no coincide con el catalogo estable para "
                f"code={self.code.value!r} (se esperaba {expected!r})"
            )
        return self

    @model_validator(mode="after")
    def _check_lists_sorted_and_unique(self) -> UnifiedShadowValidationIssue:
        label = f"UnifiedShadowValidationIssue({self.issue_id!r})"
        for field_name in (
            "baseline_reference_ids",
            "shadow_member_ids",
            "shadow_group_ids",
            "plan_item_ids",
            "source_candidate_ids",
            "evidence_ids",
            "provenance_references",
            "diagnostics",
        ):
            values = getattr(self, field_name)
            if values != _ordered_unique_strings(values):
                raise ValueError(f"{label}: {field_name} debe estar ordenado y sin duplicados")
        return self


class UnifiedShadowValidationGateResult(AltamiraBaseModel):
    gate: UnifiedShadowValidationGate
    status: UnifiedShadowGateStatus
    required: bool
    blocking: bool
    issue_ids: list[str] = Field(default_factory=list)
    checked_reference_ids: list[str] = Field(default_factory=list)
    checked_member_ids: list[str] = Field(default_factory=list)
    checked_group_ids: list[str] = Field(default_factory=list)
    reason_code: UnifiedShadowValidationIssueCode | None = None
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_lists_sorted_and_unique(self) -> UnifiedShadowValidationGateResult:
        label = f"UnifiedShadowValidationGateResult({self.gate.value!r})"
        for field_name in (
            "issue_ids",
            "checked_reference_ids",
            "checked_member_ids",
            "checked_group_ids",
            "diagnostics",
        ):
            values = getattr(self, field_name)
            if values != _ordered_unique_strings(values):
                raise ValueError(f"{label}: {field_name} debe estar ordenado y sin duplicados")
        return self


class UnifiedShadowGroupValidation(AltamiraBaseModel):
    """Validacion estructural de UN `UnifiedShadowCandidateGroup` real
    (Fase 11) -- nunca lo modifica, nunca elige un candidato "ganador"
    entre sus miembros. `downstream_shadow_eligible` implica
    `structurally_valid`, y NUNCA es verdadero para un grupo `BLOCKED`,
    `DUPLICATE_BASELINE_COVERAGE`, o con `comparison_to_v1` en
    `{CONFLICTS_WITH_BASELINE, EXACT_BASELINE_MATCH}` (invariantes
    13-16)."""

    group_id: str = Field(min_length=1)
    group_status: UnifiedShadowGroupStatus
    comparison_to_v1: UnifiedShadowComparisonKind
    structurally_valid: bool
    downstream_shadow_eligible: bool
    gate_results: list[UnifiedShadowValidationGateResult] = Field(default_factory=list)
    issue_ids: list[str] = Field(default_factory=list)
    member_ids: list[str] = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    provenance_references: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_lists_sorted_and_unique(self) -> UnifiedShadowGroupValidation:
        label = f"UnifiedShadowGroupValidation({self.group_id!r})"
        for field_name in (
            "issue_ids",
            "member_ids",
            "evidence_ids",
            "provenance_references",
            "diagnostics",
        ):
            values = getattr(self, field_name)
            if values != _ordered_unique_strings(values):
                raise ValueError(f"{label}: {field_name} debe estar ordenado y sin duplicados")
        return self

    @model_validator(mode="after")
    def _check_eligibility_implies_structurally_valid(self) -> UnifiedShadowGroupValidation:
        if self.downstream_shadow_eligible and not self.structurally_valid:
            raise ValueError(
                f"UnifiedShadowGroupValidation({self.group_id!r}): "
                "downstream_shadow_eligible=True exige structurally_valid=True"
            )
        return self

    @model_validator(mode="after")
    def _check_blocked_group_never_eligible(self) -> UnifiedShadowGroupValidation:
        """Invariante 13."""
        if (
            self.group_status == UnifiedShadowGroupStatus.BLOCKED
            and self.downstream_shadow_eligible
        ):
            raise ValueError(
                f"UnifiedShadowGroupValidation({self.group_id!r}): un grupo BLOCKED nunca "
                "es downstream elegible (invariante 13)"
            )
        return self

    @model_validator(mode="after")
    def _check_duplicate_baseline_never_eligible(self) -> UnifiedShadowGroupValidation:
        """Invariante 14."""
        if (
            self.group_status == UnifiedShadowGroupStatus.DUPLICATE_BASELINE_COVERAGE
            and self.downstream_shadow_eligible
        ):
            raise ValueError(
                f"UnifiedShadowGroupValidation({self.group_id!r}): "
                "DUPLICATE_BASELINE_COVERAGE nunca es downstream elegible (invariante 14)"
            )
        return self

    @model_validator(mode="after")
    def _check_conflicts_never_eligible(self) -> UnifiedShadowGroupValidation:
        """Invariante 15."""
        if (
            self.comparison_to_v1 == UnifiedShadowComparisonKind.CONFLICTS_WITH_BASELINE
            and self.downstream_shadow_eligible
        ):
            raise ValueError(
                f"UnifiedShadowGroupValidation({self.group_id!r}): "
                "CONFLICTS_WITH_BASELINE nunca es downstream elegible (invariante 15)"
            )
        return self

    @model_validator(mode="after")
    def _check_exact_match_never_eligible(self) -> UnifiedShadowGroupValidation:
        """Invariante 16: un EXACT_BASELINE_MATCH nunca es un
        incremento valido (siempre redundante con V1 por diseno, ver
        Fase 11 Parte 8 -- su group_status real es SIEMPRE
        DUPLICATE_BASELINE_COVERAGE, cubierto tambien por invariante
        14, pero se revalida aqui por la dimension `comparison_to_v1`
        directamente, en caso de inconsistencia entre ambos campos)."""
        if (
            self.comparison_to_v1 == UnifiedShadowComparisonKind.EXACT_BASELINE_MATCH
            and self.downstream_shadow_eligible
        ):
            raise ValueError(
                f"UnifiedShadowGroupValidation({self.group_id!r}): "
                "EXACT_BASELINE_MATCH nunca es un incremento valido (invariante 16)"
            )
        return self


class UnifiedShadowValidationSummary(AltamiraBaseModel):
    baseline_candidate_count: int = Field(ge=0)
    shadow_member_count: int = Field(ge=0)
    shadow_group_count: int = Field(ge=0)
    valid_shadow_group_count: int = Field(ge=0)
    invalid_shadow_group_count: int = Field(ge=0)
    downstream_eligible_group_count: int = Field(ge=0)
    exact_baseline_match_group_count: int = Field(ge=0)
    related_to_baseline_group_count: int = Field(ge=0)
    not_in_baseline_group_count: int = Field(ge=0)
    conflicting_with_baseline_group_count: int = Field(ge=0)
    not_evaluated_group_count: int = Field(ge=0)
    groups_with_complete_evidence_count: int = Field(ge=0)
    groups_with_complete_provenance_count: int = Field(ge=0)
    groups_with_complete_decision_trace_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    blocking_issue_count: int = Field(ge=0)
    counts_by_gate_status: dict[UnifiedShadowGateStatus, int] = Field(default_factory=dict)
    counts_by_issue_severity: dict[UnifiedShadowValidationIssueSeverity, int] = Field(
        default_factory=dict
    )
    counts_by_issue_code: dict[UnifiedShadowValidationIssueCode, int] = Field(default_factory=dict)
    counts_by_group_status: dict[UnifiedShadowGroupStatus, int] = Field(default_factory=dict)
    counts_by_baseline_comparison: dict[UnifiedShadowComparisonKind, int] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def _check_group_counts_reconcile(self) -> UnifiedShadowValidationSummary:
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
        if self.valid_shadow_group_count + self.invalid_shadow_group_count != (
            self.shadow_group_count
        ):
            raise ValueError(
                "valid_shadow_group_count + invalid_shadow_group_count no coincide con "
                "shadow_group_count"
            )
        if sum(self.counts_by_group_status.values()) != self.shadow_group_count:
            raise ValueError("suma de counts_by_group_status no coincide con shadow_group_count")
        if sum(self.counts_by_baseline_comparison.values()) != self.shadow_group_count:
            raise ValueError(
                "suma de counts_by_baseline_comparison no coincide con shadow_group_count"
            )
        if self.downstream_eligible_group_count > self.shadow_group_count:
            raise ValueError("downstream_eligible_group_count no puede exceder shadow_group_count")
        for field_name in (
            "groups_with_complete_evidence_count",
            "groups_with_complete_provenance_count",
            "groups_with_complete_decision_trace_count",
        ):
            if getattr(self, field_name) > self.shadow_group_count:
                raise ValueError(f"{field_name} no puede exceder shadow_group_count")
        return self

    @model_validator(mode="after")
    def _check_issue_severity_counts_reconcile(self) -> UnifiedShadowValidationSummary:
        expected_error = self.counts_by_issue_severity.get(
            UnifiedShadowValidationIssueSeverity.ERROR, 0
        )
        expected_warning = self.counts_by_issue_severity.get(
            UnifiedShadowValidationIssueSeverity.WARNING, 0
        )
        expected_blocking = self.counts_by_issue_severity.get(
            UnifiedShadowValidationIssueSeverity.BLOCKING, 0
        )
        if self.error_count != expected_error:
            raise ValueError("error_count no coincide con counts_by_issue_severity[ERROR]")
        if self.warning_count != expected_warning:
            raise ValueError("warning_count no coincide con counts_by_issue_severity[WARNING]")
        if self.blocking_issue_count != expected_blocking:
            raise ValueError(
                "blocking_issue_count no coincide con counts_by_issue_severity[BLOCKING]"
            )
        return self

    @model_validator(mode="after")
    def _check_gate_status_counts_reconcile(self) -> UnifiedShadowValidationSummary:
        if sum(self.counts_by_gate_status.values()) != len(UnifiedShadowValidationGate):
            raise ValueError(
                "suma de counts_by_gate_status debe ser igual a la cantidad total de gates "
                "(invariante 2: cada gate aparece exactamente una vez)"
            )
        return self


class UnifiedShadowValidationReport(AltamiraBaseModel):
    """Contenedor persistido en `<run_dir>/diagnostics/unified-shadow-
    validation-report.json`. NO contractual, sin timestamps, sin
    `source_text` completo, sin rutas absolutas, sin manifiesto humano
    completo, sin score numerico: dos ejecuciones sobre los mismos
    artefactos de entrada deben producir bytes identicos."""

    schema_version: Literal["1.0"] = "1.0"
    validator_version: Literal["1.0"] = "1.0"
    policy_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    source_package_hash: Sha256Hex
    unified_candidates_shadow_hash: Sha256Hex
    candidate_v1_artifact_hash: Sha256Hex
    v2_artifact_hash: Sha256Hex | None = None
    interprocedural_artifact_hash: Sha256Hex | None = None
    assessment_artifact_hash: Sha256Hex
    review_package_hash: Sha256Hex
    promotion_plan_hash: Sha256Hex
    source_artifact_hashes: dict[str, Sha256Hex] = Field(default_factory=dict)
    disposition: UnifiedShadowValidationDisposition
    gate_results: list[UnifiedShadowValidationGateResult] = Field(default_factory=list)
    group_validations: list[UnifiedShadowGroupValidation] = Field(default_factory=list)
    issues: list[UnifiedShadowValidationIssue] = Field(default_factory=list)
    summary: UnifiedShadowValidationSummary
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_issue_id_unique_and_ordered(self) -> UnifiedShadowValidationReport:
        """Invariante 1 (unicidad) + 22 (orden estable)."""
        ids = [issue.issue_id for issue in self.issues]
        if len(ids) != len(set(ids)):
            raise ValueError("issues contiene issue_id duplicado (invariante 1)")
        if ids != sorted(ids):
            raise ValueError("issues no esta ordenado por issue_id (invariante 22)")
        return self

    @model_validator(mode="after")
    def _check_every_gate_exactly_once(self) -> UnifiedShadowValidationReport:
        """Invariante 2."""
        gates = [result.gate for result in self.gate_results]
        if len(gates) != len(set(gates)):
            raise ValueError("gate_results contiene un gate duplicado (invariante 2)")
        if set(gates) != set(UnifiedShadowValidationGate):
            missing = set(UnifiedShadowValidationGate) - set(gates)
            raise ValueError(
                f"gate_results no cubre todos los gates exactamente una vez "
                f"(invariante 2): faltan {sorted(g.value for g in missing)}"
            )
        return self

    @model_validator(mode="after")
    def _check_group_validation_unique_and_ordered(self) -> UnifiedShadowValidationReport:
        """Invariante 3 (exactamente una GroupValidation por grupo,
        nunca duplicada) + 22 (orden estable)."""
        ids = [gv.group_id for gv in self.group_validations]
        if len(ids) != len(set(ids)):
            raise ValueError("group_validations contiene group_id duplicado (invariante 3)")
        if ids != sorted(ids):
            raise ValueError("group_validations no esta ordenado por group_id (invariante 22)")
        return self

    @model_validator(mode="after")
    def _check_referenced_issue_ids_exist(self) -> UnifiedShadowValidationReport:
        """Invariante 4."""
        known_issue_ids = {issue.issue_id for issue in self.issues}
        for result in self.gate_results:
            for issue_id in result.issue_ids:
                if issue_id not in known_issue_ids:
                    raise ValueError(
                        f"UnifiedShadowValidationGateResult({result.gate.value!r}) referencia "
                        f"un issue_id inexistente: {issue_id!r} (invariante 4)"
                    )
        for gv in self.group_validations:
            for issue_id in gv.issue_ids:
                if issue_id not in known_issue_ids:
                    raise ValueError(
                        f"UnifiedShadowGroupValidation({gv.group_id!r}) referencia un "
                        f"issue_id inexistente: {issue_id!r} (invariante 4)"
                    )
        return self

    @model_validator(mode="after")
    def _check_referenced_group_ids_exist(self) -> UnifiedShadowValidationReport:
        """Invariante 5."""
        known_group_ids = {gv.group_id for gv in self.group_validations}
        for issue in self.issues:
            for group_id in issue.shadow_group_ids:
                if group_id not in known_group_ids:
                    raise ValueError(
                        f"UnifiedShadowValidationIssue({issue.issue_id!r}) referencia un "
                        f"shadow_group_id inexistente: {group_id!r} (invariante 5)"
                    )
        for result in self.gate_results:
            for group_id in result.checked_group_ids:
                if group_id not in known_group_ids:
                    raise ValueError(
                        f"UnifiedShadowValidationGateResult({result.gate.value!r}) referencia "
                        f"un group_id inexistente: {group_id!r} (invariante 5)"
                    )
        return self

    @model_validator(mode="after")
    def _check_referenced_member_ids_exist(self) -> UnifiedShadowValidationReport:
        """Invariante 6."""
        known_member_ids = {
            member_id for gv in self.group_validations for member_id in gv.member_ids
        }
        for issue in self.issues:
            for member_id in issue.shadow_member_ids:
                if member_id not in known_member_ids:
                    raise ValueError(
                        f"UnifiedShadowValidationIssue({issue.issue_id!r}) referencia un "
                        f"shadow_member_id inexistente: {member_id!r} (invariante 6)"
                    )
        for result in self.gate_results:
            for member_id in result.checked_member_ids:
                if member_id not in known_member_ids:
                    raise ValueError(
                        f"UnifiedShadowValidationGateResult({result.gate.value!r}) referencia "
                        f"un member_id inexistente: {member_id!r} (invariante 6)"
                    )
        return self

    @model_validator(mode="after")
    def _check_blocked_requires_blocking_issue(self) -> UnifiedShadowValidationReport:
        """Invariante 8."""
        if self.disposition == UnifiedShadowValidationDisposition.BLOCKED:
            has_blocking = any(
                issue.severity == UnifiedShadowValidationIssueSeverity.BLOCKING
                for issue in self.issues
            )
            if not has_blocking:
                raise ValueError(
                    "disposition=BLOCKED exige al menos un issue BLOCKING (invariante 8)"
                )
        return self

    @model_validator(mode="after")
    def _check_qualified_for_downstream_shadow(self) -> UnifiedShadowValidationReport:
        """Invariante 9."""
        if self.disposition != UnifiedShadowValidationDisposition.QUALIFIED_FOR_DOWNSTREAM_SHADOW:
            return self
        required_not_passed = [
            r.gate.value for r in self.gate_results if r.required and r.status != "PASS"
        ]
        if required_not_passed:
            raise ValueError(
                "QUALIFIED_FOR_DOWNSTREAM_SHADOW exige todos los gates requeridos PASS "
                f"(invariante 9): fallan {sorted(required_not_passed)}"
            )
        if any(
            issue.severity == UnifiedShadowValidationIssueSeverity.BLOCKING for issue in self.issues
        ):
            raise ValueError(
                "QUALIFIED_FOR_DOWNSTREAM_SHADOW exige cero issues BLOCKING (invariante 9)"
            )
        if not any(gv.downstream_shadow_eligible for gv in self.group_validations):
            raise ValueError(
                "QUALIFIED_FOR_DOWNSTREAM_SHADOW exige al menos un grupo downstream "
                "elegible (invariante 9)"
            )
        return self

    @model_validator(mode="after")
    def _check_qualified_with_warnings(self) -> UnifiedShadowValidationReport:
        """Invariante 10."""
        if self.disposition != UnifiedShadowValidationDisposition.QUALIFIED_WITH_WARNINGS:
            return self
        required_not_passed = [
            r.gate.value for r in self.gate_results if r.required and r.status != "PASS"
        ]
        if required_not_passed:
            raise ValueError(
                "QUALIFIED_WITH_WARNINGS exige todos los gates requeridos PASS "
                f"(invariante 10): fallan {sorted(required_not_passed)}"
            )
        if any(
            issue.severity == UnifiedShadowValidationIssueSeverity.BLOCKING for issue in self.issues
        ):
            raise ValueError("QUALIFIED_WITH_WARNINGS exige cero issues BLOCKING (invariante 10)")
        if not any(
            issue.severity == UnifiedShadowValidationIssueSeverity.WARNING for issue in self.issues
        ):
            raise ValueError(
                "QUALIFIED_WITH_WARNINGS exige al menos una advertencia (invariante 10)"
            )
        if not any(gv.downstream_shadow_eligible for gv in self.group_validations):
            raise ValueError(
                "QUALIFIED_WITH_WARNINGS exige al menos un grupo elegible (invariante 10)"
            )
        return self

    @model_validator(mode="after")
    def _check_not_evaluated_requires_missing_source(self) -> UnifiedShadowValidationReport:
        """Invariante 12."""
        if self.disposition != UnifiedShadowValidationDisposition.NOT_EVALUATED:
            return self
        has_missing_source_issue = any(
            issue.code
            in (
                UnifiedShadowValidationIssueCode.SOURCE_ARTIFACT_MISSING,
                UnifiedShadowValidationIssueCode.SOURCE_ARTIFACT_INVALID,
            )
            for issue in self.issues
        )
        has_not_evaluated_gate = any(
            result.status == UnifiedShadowGateStatus.NOT_EVALUATED and result.required
            for result in self.gate_results
        )
        if not (has_missing_source_issue or has_not_evaluated_gate):
            raise ValueError(
                "NOT_EVALUATED exige una fuente requerida ausente o invalida (invariante 12)"
            )
        return self

    @model_validator(mode="after")
    def _check_summary_reconciles_with_lists(self) -> UnifiedShadowValidationReport:
        """Invariante 21."""
        if self.summary.shadow_group_count != len(self.group_validations):
            raise ValueError(
                "summary.shadow_group_count no coincide con la cantidad real de group_validations"
            )
        expected_eligible = sum(1 for gv in self.group_validations if gv.downstream_shadow_eligible)
        if self.summary.downstream_eligible_group_count != expected_eligible:
            raise ValueError(
                "summary.downstream_eligible_group_count no coincide con la cantidad real "
                "de grupos downstream elegibles"
            )
        expected_by_group_status: dict[str, int] = {}
        expected_by_comparison: dict[str, int] = {}
        for gv in self.group_validations:
            expected_by_group_status[gv.group_status.value] = (
                expected_by_group_status.get(gv.group_status.value, 0) + 1
            )
            expected_by_comparison[gv.comparison_to_v1.value] = (
                expected_by_comparison.get(gv.comparison_to_v1.value, 0) + 1
            )
        actual_by_group_status = {
            k.value: v for k, v in self.summary.counts_by_group_status.items()
        }
        actual_by_comparison = {
            k.value: v for k, v in self.summary.counts_by_baseline_comparison.items()
        }
        if actual_by_group_status != expected_by_group_status:
            raise ValueError(
                "summary.counts_by_group_status no coincide con la agregacion real de "
                "group_validations"
            )
        if actual_by_comparison != expected_by_comparison:
            raise ValueError(
                "summary.counts_by_baseline_comparison no coincide con la agregacion real "
                "de group_validations"
            )
        expected_by_severity: dict[str, int] = {}
        expected_by_code: dict[str, int] = {}
        for issue in self.issues:
            expected_by_severity[issue.severity.value] = (
                expected_by_severity.get(issue.severity.value, 0) + 1
            )
            expected_by_code[issue.code.value] = expected_by_code.get(issue.code.value, 0) + 1
        actual_by_severity = {k.value: v for k, v in self.summary.counts_by_issue_severity.items()}
        actual_by_code = {k.value: v for k, v in self.summary.counts_by_issue_code.items()}
        if actual_by_severity != expected_by_severity:
            raise ValueError(
                "summary.counts_by_issue_severity no coincide con la agregacion real de issues"
            )
        if actual_by_code != expected_by_code:
            raise ValueError(
                "summary.counts_by_issue_code no coincide con la agregacion real de issues"
            )
        expected_by_gate_status: dict[str, int] = {}
        for result in self.gate_results:
            expected_by_gate_status[result.status.value] = (
                expected_by_gate_status.get(result.status.value, 0) + 1
            )
        actual_by_gate_status = {k.value: v for k, v in self.summary.counts_by_gate_status.items()}
        if actual_by_gate_status != expected_by_gate_status:
            raise ValueError(
                "summary.counts_by_gate_status no coincide con la agregacion real de gate_results"
            )
        return self

    @model_validator(mode="after")
    def _check_diagnostics_sorted_and_unique(self) -> UnifiedShadowValidationReport:
        if self.diagnostics != _ordered_unique_strings(self.diagnostics):
            raise ValueError("diagnostics debe estar ordenado alfabeticamente y sin duplicados")
        return self


__all__ = [
    "UnifiedRuleFamily",
    "UnifiedShadowGateStatus",
    "UnifiedShadowGroupValidation",
    "UnifiedShadowValidationDisposition",
    "UnifiedShadowValidationGate",
    "UnifiedShadowValidationGateResult",
    "UnifiedShadowValidationIssue",
    "UnifiedShadowValidationIssueCode",
    "UnifiedShadowValidationIssueSeverity",
    "UnifiedShadowValidationReport",
    "UnifiedShadowValidationSummary",
]
