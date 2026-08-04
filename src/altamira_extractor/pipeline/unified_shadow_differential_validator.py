"""Validador PURO diferencial contra el baseline V1 (Fase 12 Parte 7,
`feat/unified-shadow-differential-validation`).

Clasifica CADA `UnifiedShadowCandidateGroup` usando EXCLUSIVAMENTE
`comparison_to_v1` -- el resultado que Fase 11 YA CALCULO a partir de
evidencia real de Fase 9 (`CandidateRelation`/`CandidateConflict`).
Nunca recalcula semejanza, nunca inventa un conflicto, nunca
reinterpreta la clasificacion original: solo verifica que sea
INTERNAMENTE coherente (referencias baseline presentes cuando el tipo
de comparacion las exige, ausentes cuando no aplica) y traduce cada
clasificacion a un `RawFinding` con la severidad fijada por la
politica (Fase 12 Parte 9)."""

from __future__ import annotations

from dataclasses import dataclass

from ..contracts.unified_candidates_shadow import (
    UnifiedShadowCandidateGroup,
    UnifiedShadowComparisonKind,
    UnifiedShadowGroupStatus,
)
from ..contracts.unified_shadow_validation import UnifiedShadowValidationGate
from ..contracts.unified_shadow_validation import UnifiedShadowValidationIssueCode as Code
from .unified_shadow_validation_policy import RawFinding

_GATE = UnifiedShadowValidationGate.BASELINE_DIFFERENTIAL_SAFETY

_ISSUE_CODE_BY_COMPARISON: dict[UnifiedShadowComparisonKind, Code] = {
    UnifiedShadowComparisonKind.EXACT_BASELINE_MATCH: Code.GROUP_DUPLICATES_BASELINE,
    UnifiedShadowComparisonKind.RELATED_TO_BASELINE: Code.GROUP_RELATED_TO_BASELINE,
    UnifiedShadowComparisonKind.CONFLICTS_WITH_BASELINE: Code.GROUP_CONFLICTS_WITH_BASELINE,
    UnifiedShadowComparisonKind.NOT_IN_BASELINE: Code.GROUP_NOT_IN_BASELINE,
    UnifiedShadowComparisonKind.NOT_EVALUATED: Code.GROUP_BASELINE_NOT_EVALUATED,
}


@dataclass(frozen=True)
class DifferentialValidationResult:
    findings: tuple[RawFinding, ...]
    baseline_safe: bool


def validate_baseline_differential(
    group: UnifiedShadowCandidateGroup,
) -> DifferentialValidationResult:
    """Punto de entrada puro. Nunca muta `group`, nunca recalcula
    `comparison_to_v1`."""
    findings: list[RawFinding] = []
    group_id = group.unified_shadow_candidate_id
    comparison = group.comparison_to_v1

    findings.append(
        RawFinding(
            code=_ISSUE_CODE_BY_COMPARISON[comparison],
            gate=_GATE,
            shadow_group_ids=(group_id,),
            baseline_reference_ids=tuple(
                sorted(
                    set(group.exact_baseline_reference_ids)
                    | set(group.related_baseline_reference_ids)
                    | set(group.conflicting_baseline_reference_ids)
                )
            ),
        )
    )

    # Coherencia interna (Fase 11 ya la exige a nivel de contrato --
    # revalidada aqui como deteccion de deriva/corrupcion, nunca
    # recalculada desde cero).
    if comparison == UnifiedShadowComparisonKind.EXACT_BASELINE_MATCH:
        if not group.exact_baseline_reference_ids:
            findings.append(
                RawFinding(
                    code=Code.GROUP_INCONSISTENT_SCOPE,
                    gate=UnifiedShadowValidationGate.GROUP_INTERNAL_CONSISTENCY,
                    shadow_group_ids=(group_id,),
                    diagnostics=("exact_baseline_match_without_references",),
                )
            )
        if group.status != UnifiedShadowGroupStatus.DUPLICATE_BASELINE_COVERAGE:
            findings.append(
                RawFinding(
                    code=Code.GROUP_INCONSISTENT_SCOPE,
                    gate=UnifiedShadowValidationGate.GROUP_INTERNAL_CONSISTENCY,
                    shadow_group_ids=(group_id,),
                    diagnostics=("exact_baseline_match_status_incoherent",),
                )
            )
    elif comparison == UnifiedShadowComparisonKind.CONFLICTS_WITH_BASELINE:
        if not group.conflicting_baseline_reference_ids:
            findings.append(
                RawFinding(
                    code=Code.GROUP_INCONSISTENT_SCOPE,
                    gate=UnifiedShadowValidationGate.GROUP_INTERNAL_CONSISTENCY,
                    shadow_group_ids=(group_id,),
                    diagnostics=("conflict_without_references",),
                )
            )
        if group.status != UnifiedShadowGroupStatus.BLOCKED:
            findings.append(
                RawFinding(
                    code=Code.GROUP_INCONSISTENT_SCOPE,
                    gate=UnifiedShadowValidationGate.GROUP_INTERNAL_CONSISTENCY,
                    shadow_group_ids=(group_id,),
                    diagnostics=("conflict_status_incoherent",),
                )
            )
    elif comparison == UnifiedShadowComparisonKind.NOT_IN_BASELINE:
        if (
            group.exact_baseline_reference_ids
            or group.related_baseline_reference_ids
            or group.conflicting_baseline_reference_ids
        ):
            findings.append(
                RawFinding(
                    code=Code.GROUP_INCONSISTENT_SCOPE,
                    gate=UnifiedShadowValidationGate.GROUP_INTERNAL_CONSISTENCY,
                    shadow_group_ids=(group_id,),
                    diagnostics=("not_in_baseline_with_references",),
                )
            )
    elif comparison == UnifiedShadowComparisonKind.NOT_EVALUATED:
        if (
            group.exact_baseline_reference_ids
            or group.related_baseline_reference_ids
            or group.conflicting_baseline_reference_ids
        ):
            findings.append(
                RawFinding(
                    code=Code.GROUP_INCONSISTENT_SCOPE,
                    gate=UnifiedShadowValidationGate.GROUP_INTERNAL_CONSISTENCY,
                    shadow_group_ids=(group_id,),
                    diagnostics=("not_evaluated_with_references",),
                )
            )

    baseline_safe = comparison == UnifiedShadowComparisonKind.NOT_IN_BASELINE and not any(
        finding.code == Code.GROUP_INCONSISTENT_SCOPE for finding in findings
    )
    return DifferentialValidationResult(findings=tuple(findings), baseline_safe=baseline_safe)
