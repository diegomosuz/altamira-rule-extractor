"""Politica declarativa y versionada de la validacion diferencial del
artefacto unificado de candidatos en shadow mode (Fase 12 de la
ampliacion semantica, `feat/unified-shadow-differential-validation`).

Define, en UN solo lugar: (1) `RawFinding`, la estructura intermedia
que CADA validador puro (Fase 12 Partes 4-8) produce -- nunca genera
por si mismo un `issue_id` ni un `UnifiedShadowValidationIssue`
completo, eso es responsabilidad EXCLUSIVA del analizador (Parte 10),
que centraliza la generacion determinista de IDs; (2) la severidad
POR DEFECTO de cada `UnifiedShadowValidationIssueCode` -- un catalogo
CERRADO, nunca decidido ad-hoc por cada validador; (3) que gates son
`required`/`blocking`; (4) la derivacion de
`UnifiedShadowValidationDisposition` a partir de gates/issues/grupos
ya calculados -- NUNCA una suma de puntos, NUNCA un porcentaje, NUNCA
un umbral dinamico.

No usar suma de puntos. No usar porcentajes como gate. No cambiar
thresholds dinamicamente."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ..contracts.unified_shadow_validation import (
    UnifiedShadowGateStatus,
    UnifiedShadowGroupValidation,
    UnifiedShadowValidationDisposition,
    UnifiedShadowValidationGate,
    UnifiedShadowValidationIssueCode,
    UnifiedShadowValidationIssueSeverity,
)

POLICY_VERSION = "1.0"

# --- RawFinding: estructura intermedia PURA, nunca persistida por si
# misma (el analizador la convierte en UnifiedShadowValidationIssue
# con un issue_id determinista). ---


@dataclass(frozen=True)
class RawFinding:
    code: UnifiedShadowValidationIssueCode
    gate: UnifiedShadowValidationGate
    severity: UnifiedShadowValidationIssueSeverity | None = None
    baseline_reference_ids: tuple[str, ...] = ()
    shadow_member_ids: tuple[str, ...] = ()
    shadow_group_ids: tuple[str, ...] = ()
    plan_item_ids: tuple[str, ...] = ()
    source_candidate_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    provenance_references: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = field(default_factory=tuple)

    def resolved_severity(self) -> UnifiedShadowValidationIssueSeverity:
        """La severidad explicita del hallazgo (cuando un validador
        necesita anular el valor por defecto -- p.ej. Parte 7, donde
        cada `UnifiedShadowComparisonKind` fija su propia severidad
        exacta) tiene prioridad; en su ausencia, se usa el catalogo
        `_DEFAULT_SEVERITY_BY_CODE`."""
        return self.severity or _DEFAULT_SEVERITY_BY_CODE[self.code]


# --- Catalogo CERRADO de severidad por defecto. Nunca decidido en
# cada validador individualmente. ---

_DEFAULT_SEVERITY_BY_CODE: dict[
    UnifiedShadowValidationIssueCode, UnifiedShadowValidationIssueSeverity
] = {
    UnifiedShadowValidationIssueCode.SOURCE_ARTIFACT_MISSING: (
        UnifiedShadowValidationIssueSeverity.BLOCKING
    ),
    UnifiedShadowValidationIssueCode.SOURCE_ARTIFACT_INVALID: (
        UnifiedShadowValidationIssueSeverity.BLOCKING
    ),
    UnifiedShadowValidationIssueCode.SOURCE_HASH_MISMATCH: (
        UnifiedShadowValidationIssueSeverity.BLOCKING
    ),
    UnifiedShadowValidationIssueCode.UNIFIED_ARTIFACT_HASH_MISMATCH: (
        UnifiedShadowValidationIssueSeverity.BLOCKING
    ),
    UnifiedShadowValidationIssueCode.BASELINE_CANDIDATE_MISSING: (
        UnifiedShadowValidationIssueSeverity.BLOCKING
    ),
    UnifiedShadowValidationIssueCode.BASELINE_COUNT_MISMATCH: (
        UnifiedShadowValidationIssueSeverity.BLOCKING
    ),
    UnifiedShadowValidationIssueCode.PLAN_ITEM_UNACCOUNTED: (
        UnifiedShadowValidationIssueSeverity.BLOCKING
    ),
    UnifiedShadowValidationIssueCode.PLAN_BINDING_MISMATCH: (
        UnifiedShadowValidationIssueSeverity.BLOCKING
    ),
    UnifiedShadowValidationIssueCode.SHADOW_MEMBER_SOURCE_NOT_FOUND: (
        UnifiedShadowValidationIssueSeverity.BLOCKING
    ),
    UnifiedShadowValidationIssueCode.SHADOW_MEMBER_IDENTITY_MISMATCH: (
        UnifiedShadowValidationIssueSeverity.BLOCKING
    ),
    UnifiedShadowValidationIssueCode.SHADOW_MEMBER_WITHOUT_APPROVAL: (
        UnifiedShadowValidationIssueSeverity.BLOCKING
    ),
    UnifiedShadowValidationIssueCode.SHADOW_MEMBER_WITHOUT_EVIDENCE: (
        UnifiedShadowValidationIssueSeverity.ERROR
    ),
    UnifiedShadowValidationIssueCode.SHADOW_MEMBER_WITHOUT_PROVENANCE: (
        UnifiedShadowValidationIssueSeverity.ERROR
    ),
    UnifiedShadowValidationIssueCode.GROUP_WITHOUT_MEMBERS: (
        UnifiedShadowValidationIssueSeverity.BLOCKING
    ),
    UnifiedShadowValidationIssueCode.GROUP_MEMBER_NOT_FOUND: (
        UnifiedShadowValidationIssueSeverity.BLOCKING
    ),
    UnifiedShadowValidationIssueCode.GROUP_MULTIPLE_FAMILIES: (
        UnifiedShadowValidationIssueSeverity.ERROR
    ),
    UnifiedShadowValidationIssueCode.GROUP_MULTIPLE_TARGETS: (
        UnifiedShadowValidationIssueSeverity.ERROR
    ),
    UnifiedShadowValidationIssueCode.GROUP_MULTIPLE_OUTPUTS: (
        UnifiedShadowValidationIssueSeverity.ERROR
    ),
    UnifiedShadowValidationIssueCode.GROUP_INCONSISTENT_SCOPE: (
        UnifiedShadowValidationIssueSeverity.ERROR
    ),
    UnifiedShadowValidationIssueCode.GROUP_UNKNOWN_FAMILY: (
        UnifiedShadowValidationIssueSeverity.ERROR
    ),
    UnifiedShadowValidationIssueCode.GROUP_BLOCKED: UnifiedShadowValidationIssueSeverity.ERROR,
    UnifiedShadowValidationIssueCode.GROUP_DUPLICATES_BASELINE: (
        UnifiedShadowValidationIssueSeverity.BLOCKING
    ),
    UnifiedShadowValidationIssueCode.GROUP_CONFLICTS_WITH_BASELINE: (
        UnifiedShadowValidationIssueSeverity.BLOCKING
    ),
    UnifiedShadowValidationIssueCode.GROUP_BASELINE_NOT_EVALUATED: (
        UnifiedShadowValidationIssueSeverity.BLOCKING
    ),
    UnifiedShadowValidationIssueCode.GROUP_RELATED_TO_BASELINE: (
        UnifiedShadowValidationIssueSeverity.WARNING
    ),
    UnifiedShadowValidationIssueCode.GROUP_NOT_IN_BASELINE: (
        UnifiedShadowValidationIssueSeverity.INFO
    ),
    UnifiedShadowValidationIssueCode.SUMMARY_MISMATCH: (
        UnifiedShadowValidationIssueSeverity.BLOCKING
    ),
    UnifiedShadowValidationIssueCode.NON_DETERMINISTIC_SERIALIZATION: (
        UnifiedShadowValidationIssueSeverity.BLOCKING
    ),
    UnifiedShadowValidationIssueCode.NO_VALID_SHADOW_GROUPS: (
        UnifiedShadowValidationIssueSeverity.WARNING
    ),
    UnifiedShadowValidationIssueCode.FUNCTIONAL_VALIDATION_REQUIRED: (
        UnifiedShadowValidationIssueSeverity.INFO
    ),
}

# --- Gates globales obligatorios (Fase 12 Parte 9). Los 11 primeros
# mas el gate-resumen DOWNSTREAM_SHADOW_ELIGIBILITY -- exactamente los
# 12 valores de `UnifiedShadowValidationGate`. ---

GLOBAL_GATES: tuple[UnifiedShadowValidationGate, ...] = (
    UnifiedShadowValidationGate.SOURCE_INTEGRITY,
    UnifiedShadowValidationGate.BASELINE_COMPLETENESS,
    UnifiedShadowValidationGate.PLAN_BINDING_INTEGRITY,
    UnifiedShadowValidationGate.MEMBER_SOURCE_RESOLUTION,
    UnifiedShadowValidationGate.GROUP_INTERNAL_CONSISTENCY,
    UnifiedShadowValidationGate.BASELINE_DIFFERENTIAL_SAFETY,
    UnifiedShadowValidationGate.EVIDENCE_COMPLETENESS,
    UnifiedShadowValidationGate.PROVENANCE_COMPLETENESS,
    UnifiedShadowValidationGate.DECISION_TRACEABILITY,
    UnifiedShadowValidationGate.SUMMARY_RECONCILIATION,
    UnifiedShadowValidationGate.DETERMINISTIC_SERIALIZATION,
)

SUMMARY_GATE = UnifiedShadowValidationGate.DOWNSTREAM_SHADOW_ELIGIBILITY

# Todos los gates son `required=True` (Fase 12 Parte 9: "Gates globales
# obligatorios"). `blocking=True` para los 11 gates de integridad
# estructural (una falla ahi es sistemica); el gate-resumen es
# `blocking=False` (es un SINTOMA de los resultados por grupo, nunca
# la CAUSA -- su propio FAIL nunca añade una severidad BLOCKING
# adicional, solo refleja "cero grupos elegibles").
GATE_BLOCKING: dict[UnifiedShadowValidationGate, bool] = {
    gate: True for gate in GLOBAL_GATES
} | {SUMMARY_GATE: False}


def is_group_downstream_eligible(
    *,
    group_status: str,
    comparison_to_v1: str,
    rule_family_is_unknown: bool,
    support_is_blocked: bool,
    member_source_resolution_complete: bool,
    evidence_complete: bool,
    provenance_complete: bool,
    decision_trace_complete: bool,
    has_error_or_blocking_issue: bool,
) -> bool:
    """Fase 12 Parte 9: un grupo puede ser downstream elegible
    UNICAMENTE cuando se cumplen TODAS las condiciones -- nunca una
    mayoria, nunca un puntaje. Cada condicion es booleana y
    demostrable."""
    if group_status != "VALID":
        return False
    if comparison_to_v1 != "NOT_IN_BASELINE":
        return False
    if rule_family_is_unknown:
        return False
    if support_is_blocked:
        return False
    if not member_source_resolution_complete:
        return False
    if not evidence_complete:
        return False
    if not provenance_complete:
        return False
    if not decision_trace_complete:
        return False
    if has_error_or_blocking_issue:
        return False
    return True


def derive_disposition(
    *,
    required_source_missing: bool,
    gate_statuses: dict[UnifiedShadowValidationGate, UnifiedShadowGateStatus],
    has_blocking_issue: bool,
    has_warning_issue: bool,
    group_validations: Sequence[UnifiedShadowGroupValidation],
) -> UnifiedShadowValidationDisposition:
    """Fase 12 Parte 9: disposition global -- SIEMPRE derivada de
    condiciones discretas y auditables, NUNCA de una suma de puntos ni
    de un porcentaje. Orden de evaluacion (cada paso es una condicion
    exclusiva, nunca superpuesta con la siguiente):

    1. NOT_EVALUATED: una fuente REQUERIDA esta ausente o invalida --
       se evalua PRIMERO, sin importar el resto (invariante 12).
    2. BLOCKED: existe al menos un issue BLOCKING (invariante 8; segun
       invariante 11, REVIEW_REQUIRED excluye explicitamente cualquier
       "fallo de integridad global bloqueante", asi que la unica
       disposition posible ante un BLOCKING es esta).
    3. QUALIFIED_FOR_DOWNSTREAM_SHADOW / QUALIFIED_WITH_WARNINGS:
       todos los gates requeridos PASS, cero BLOCKING, al menos un
       grupo downstream elegible -- con o sin advertencias
       (invariantes 9/10).
    4. REVIEW_REQUIRED: en cualquier otro caso -- integridad global
       valida, cero corrupcion estructural, pero ningun grupo queda
       elegible (invariante 11)."""
    if required_source_missing:
        return UnifiedShadowValidationDisposition.NOT_EVALUATED

    if has_blocking_issue:
        return UnifiedShadowValidationDisposition.BLOCKED

    required_gates_pass = all(
        gate_statuses.get(gate) == UnifiedShadowGateStatus.PASS for gate in GLOBAL_GATES
    )
    any_group_eligible = any(gv.downstream_shadow_eligible for gv in group_validations)

    if required_gates_pass and any_group_eligible:
        if has_warning_issue:
            return UnifiedShadowValidationDisposition.QUALIFIED_WITH_WARNINGS
        return UnifiedShadowValidationDisposition.QUALIFIED_FOR_DOWNSTREAM_SHADOW

    return UnifiedShadowValidationDisposition.REVIEW_REQUIRED
