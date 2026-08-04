"""Casos negativos aislados (Fase 12 Parte 16,
`feat/unified-shadow-differential-validation`) A-G. Cada test parte de
`golden_path_with_working_v2_resolution()` -- el UNICO escenario donde
`MEMBER_SOURCE_RESOLUTION` pasa genuinamente -- y muta UNICAMENTE lo
necesario para aislar un hallazgo especifico, para que la disposition
resultante refleje exclusivamente ese defecto y no el ruido de una
resolucion V2 fallida."""

from __future__ import annotations

from altamira_extractor.contracts.unified_candidates_shadow import (
    UnifiedShadowComparisonKind,
    UnifiedShadowGroupStatus,
)
from altamira_extractor.contracts.unified_shadow_validation import (
    UnifiedShadowValidationDisposition,
    UnifiedShadowValidationIssueCode,
)

from ._unified_shadow_validation_fixtures import (
    analyze_golden_path,
    golden_path_with_working_v2_resolution,
)

Code = UnifiedShadowValidationIssueCode
Disposition = UnifiedShadowValidationDisposition
Comparison = UnifiedShadowComparisonKind
Status = UnifiedShadowGroupStatus


def _group(gp: object):
    return gp.unified_shadow.shadow_groups[0]  # type: ignore[attr-defined]


def _with_group(gp: object, **updates: object):
    group = _group(gp).model_copy(update=updates)
    unified_shadow = gp.unified_shadow.model_copy(update={"shadow_groups": [group]})  # type: ignore[attr-defined]
    return gp.__class__(  # type: ignore[attr-defined]
        v1=gp.v1,  # type: ignore[attr-defined]
        assessment=gp.assessment,  # type: ignore[attr-defined]
        review_package=gp.review_package,  # type: ignore[attr-defined]
        plan=gp.plan,  # type: ignore[attr-defined]
        unified_shadow=unified_shadow,
    )


def test_case_a_v1_duplicate_never_eligible_and_blocks() -> None:
    """A. Duplicado exacto con V1: GROUP_DUPLICATES_BASELINE, grupo
    nunca elegible, disposition BLOCKED (severidad BLOCKING por
    politica explicita, Fase 12 Parte 9)."""
    gp, v2 = golden_path_with_working_v2_resolution()
    gp = _with_group(
        gp,
        comparison_to_v1=Comparison.EXACT_BASELINE_MATCH,
        status=Status.DUPLICATE_BASELINE_COVERAGE,
        exact_baseline_reference_ids=["baseline::exact::1"],
    )
    report = analyze_golden_path(gp, v2=v2)
    assert report.disposition == Disposition.BLOCKED
    assert report.group_validations[0].downstream_shadow_eligible is False
    codes = {issue.code for issue in report.issues}
    assert Code.GROUP_DUPLICATES_BASELINE in codes


def test_case_b_v1_conflict_never_eligible_and_blocks() -> None:
    """B. Conflicto con V1: GROUP_CONFLICTS_WITH_BASELINE, grupo nunca
    elegible, disposition BLOCKED."""
    gp, v2 = golden_path_with_working_v2_resolution()
    gp = _with_group(
        gp,
        comparison_to_v1=Comparison.CONFLICTS_WITH_BASELINE,
        status=Status.BLOCKED,
        conflicting_baseline_reference_ids=["baseline::conflict::1"],
    )
    report = analyze_golden_path(gp, v2=v2)
    assert report.disposition == Disposition.BLOCKED
    assert report.group_validations[0].downstream_shadow_eligible is False
    codes = {issue.code for issue in report.issues}
    assert Code.GROUP_CONFLICTS_WITH_BASELINE in codes


def test_case_c_related_to_baseline_warns_but_never_blocks() -> None:
    """C. Relacionado (no equivalente) con V1: WARNING, no
    auto-elegible, nunca tratado como conflicto -- disposition
    REVIEW_REQUIRED (cero grupos elegibles, cero BLOCKING)."""
    gp, v2 = golden_path_with_working_v2_resolution()
    gp = _with_group(
        gp,
        comparison_to_v1=Comparison.RELATED_TO_BASELINE,
        related_baseline_reference_ids=["baseline::related::1"],
    )
    report = analyze_golden_path(gp, v2=v2)
    assert report.disposition == Disposition.REVIEW_REQUIRED
    assert report.group_validations[0].downstream_shadow_eligible is False
    assert report.summary.blocking_issue_count == 0
    codes = {issue.code for issue in report.issues}
    assert Code.GROUP_RELATED_TO_BASELINE in codes
    assert Code.GROUP_CONFLICTS_WITH_BASELINE not in codes


def test_case_d_v1_not_evaluable_never_confused_with_not_in_baseline() -> None:
    """D. V1 no evaluable: GROUP_BASELINE_NOT_EVALUATED, nunca
    NOT_IN_BASELINE -- grupo nunca elegible."""
    gp, v2 = golden_path_with_working_v2_resolution()
    gp = _with_group(gp, comparison_to_v1=Comparison.NOT_EVALUATED)
    report = analyze_golden_path(gp, v2=v2)
    assert report.group_validations[0].downstream_shadow_eligible is False
    codes = {issue.code for issue in report.issues}
    assert Code.GROUP_BASELINE_NOT_EVALUATED in codes
    assert Code.GROUP_NOT_IN_BASELINE not in codes
    assert report.disposition == Disposition.BLOCKED


def test_case_e_incomplete_evidence_blocks_eligibility() -> None:
    """E. Evidence incompleta: EVIDENCE_COMPLETENESS falla para el
    grupo, grupo nunca elegible."""
    gp, v2 = golden_path_with_working_v2_resolution()
    member = gp.unified_shadow.shadow_members[0].model_copy(update={"evidence_ids": []})
    group = _group(gp).model_copy(update={"evidence_ids": []})
    unified_shadow = gp.unified_shadow.model_copy(
        update={"shadow_members": [member], "shadow_groups": [group]}
    )
    gp = gp.__class__(
        v1=gp.v1,
        assessment=gp.assessment,
        review_package=gp.review_package,
        plan=gp.plan,
        unified_shadow=unified_shadow,
    )
    report = analyze_golden_path(gp, v2=v2)
    assert report.group_validations[0].downstream_shadow_eligible is False
    gate = next(g for g in report.gate_results if g.gate.value == "EVIDENCE_COMPLETENESS")
    assert gate.status.value == "FAIL"
    assert report.summary.groups_with_complete_evidence_count == 0
    codes = [issue.code.value for issue in report.issues]
    assert codes.count("SHADOW_MEMBER_WITHOUT_EVIDENCE") == 1, (
        "el mismo hallazgo detectado independientemente por el group validator "
        "(Parte 6) y el evidence validator (Parte 8) debe deduplicarse a un unico issue"
    )


def test_case_f_incomplete_provenance_blocks_eligibility() -> None:
    """F. Provenance incompleta: PROVENANCE_COMPLETENESS falla para el
    grupo, grupo nunca elegible."""
    gp, v2 = golden_path_with_working_v2_resolution()
    member = gp.unified_shadow.shadow_members[0].model_copy(update={"provenance_references": []})
    group = _group(gp).model_copy(update={"provenance_references": []})
    unified_shadow = gp.unified_shadow.model_copy(
        update={"shadow_members": [member], "shadow_groups": [group]}
    )
    gp = gp.__class__(
        v1=gp.v1,
        assessment=gp.assessment,
        review_package=gp.review_package,
        plan=gp.plan,
        unified_shadow=unified_shadow,
    )
    report = analyze_golden_path(gp, v2=v2)
    assert report.group_validations[0].downstream_shadow_eligible is False
    gate = next(g for g in report.gate_results if g.gate.value == "PROVENANCE_COMPLETENESS")
    assert gate.status.value == "FAIL"
    assert report.summary.groups_with_complete_provenance_count == 0
    codes = [issue.code.value for issue in report.issues]
    assert codes.count("SHADOW_MEMBER_WITHOUT_PROVENANCE") == 1, (
        "el mismo hallazgo detectado independientemente por el group validator "
        "(Parte 6) y el evidence validator (Parte 8) debe deduplicarse a un unico issue"
    )


def test_case_g_stale_v1_hash_blocks_without_silent_correction() -> None:
    """G. Hash desactualizado (V1 cambio desde que se calculo el
    assessment): SOURCE_INTEGRITY falla, disposition NOT_EVALUATED (la
    cadena de confianza se rompe, ninguna otra verificacion se
    intenta) -- nunca se corrige el hash silenciosamente."""
    gp, v2 = golden_path_with_working_v2_resolution()
    report = analyze_golden_path(
        gp, v2=v2, v1_override=gp.v1.model_copy(update={"source_package_hash": "b" * 64})
    )
    assert report.disposition == Disposition.NOT_EVALUATED
    assert report.group_validations == []
    source_gate = next(g for g in report.gate_results if g.gate.value == "SOURCE_INTEGRITY")
    assert source_gate.status.value == "FAIL"
