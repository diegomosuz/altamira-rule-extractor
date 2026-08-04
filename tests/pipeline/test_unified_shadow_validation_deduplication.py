"""Tests de deduplicacion semantica de issues (Fase 12, cierre de
seguridad `feat/unified-shadow-differential-validation`).

`unified_shadow_validation_analyzer.py::_build_issues` deduplica
`RawFinding` por una identidad semantica deterministica (code, gate,
severity, referencias ordenadas, message_code) ANTES de generar
`issue_id`, `gate_results.issue_ids` y reconciliar `summary` -- nunca
usa un contador de aparicion para distinguir el mismo hallazgo emitido
independientemente por mas de un validador (p. ej. Parte 6 y Parte 8
detectando ambas "miembro sin evidence" para el mismo `member_id`)."""

from __future__ import annotations

from altamira_extractor.contracts.candidate_promotion_assessment import UnifiedRuleFamily
from altamira_extractor.contracts.unified_shadow_validation import (
    UnifiedShadowValidationGate,
    UnifiedShadowValidationIssueCode,
)
from altamira_extractor.pipeline.unified_shadow_validation_analyzer import (
    _build_issues,
    _deduplicate_findings,
    _semantic_key,
)
from altamira_extractor.pipeline.unified_shadow_validation_policy import RawFinding

from ._unified_shadow_validation_fixtures import (
    analyze_golden_path,
    golden_path_with_working_v2_resolution,
    second_member,
)

Code = UnifiedShadowValidationIssueCode
Gate = UnifiedShadowValidationGate


def _finding(**overrides: object) -> RawFinding:
    base: dict[str, object] = dict(
        code=Code.SHADOW_MEMBER_WITHOUT_EVIDENCE,
        gate=Gate.EVIDENCE_COMPLETENESS,
        shadow_member_ids=("member::1",),
    )
    base.update(overrides)
    return RawFinding(**base)  # type: ignore[arg-type]


# --- Unidad: _semantic_key / _deduplicate_findings ---


def test_semantic_key_identical_for_two_structurally_equal_findings() -> None:
    a = _finding()
    b = _finding()
    assert _semantic_key(a) == _semantic_key(b)


def test_semantic_key_ignores_reference_list_order() -> None:
    a = _finding(shadow_member_ids=("member::a", "member::b"))
    b = _finding(shadow_member_ids=("member::b", "member::a"))
    assert _semantic_key(a) == _semantic_key(b)


def test_semantic_key_differs_by_member_id() -> None:
    a = _finding(shadow_member_ids=("member::1",))
    b = _finding(shadow_member_ids=("member::2",))
    assert _semantic_key(a) != _semantic_key(b)


def test_semantic_key_differs_by_severity_override() -> None:
    from altamira_extractor.contracts.unified_shadow_validation import (
        UnifiedShadowValidationIssueSeverity,
    )

    a = _finding()
    b = _finding(severity=UnifiedShadowValidationIssueSeverity.WARNING)
    assert _semantic_key(a) != _semantic_key(b)


def test_deduplicate_findings_collapses_two_identical_findings_to_one() -> None:
    findings = [_finding(), _finding()]
    result = _deduplicate_findings(findings)
    assert len(result) == 1


def test_deduplicate_findings_preserves_two_distinct_findings() -> None:
    findings = [
        _finding(shadow_member_ids=("member::1",)),
        _finding(shadow_member_ids=("member::2",)),
    ]
    result = _deduplicate_findings(findings)
    assert len(result) == 2


def test_build_issues_deduplicates_before_generating_issue_ids() -> None:
    issues = _build_issues([_finding(), _finding()])
    assert len(issues) == 1


def test_build_issues_keeps_distinct_member_findings_separate() -> None:
    issues = _build_issues(
        [
            _finding(shadow_member_ids=("member::1",)),
            _finding(shadow_member_ids=("member::2",)),
        ]
    )
    assert len(issues) == 2
    assert {issue.issue_id for issue in issues} == {issues[0].issue_id, issues[1].issue_id}
    assert issues[0].issue_id != issues[1].issue_id


# --- Analizador real: mismo defecto detectado por Parte 6 (group_validator) ---
# --- y Parte 8 (evidence_validator) para el MISMO member ---


def test_analyzer_deduplicates_evidence_finding_shared_by_group_and_evidence_validator() -> None:
    """1. El mismo hallazgo (SHADOW_MEMBER_WITHOUT_EVIDENCE) emitido por
    dos validadores (group_validator Parte 6, evidence_validator Parte
    8) para el mismo member aparece una sola vez en el reporte."""
    gp, v2 = golden_path_with_working_v2_resolution()
    member = gp.unified_shadow.shadow_members[0].model_copy(update={"evidence_ids": []})
    group = gp.unified_shadow.shadow_groups[0].model_copy(update={"evidence_ids": []})
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
    codes = [issue.code.value for issue in report.issues]
    assert codes.count("SHADOW_MEMBER_WITHOUT_EVIDENCE") == 1


def test_analyzer_keeps_two_issues_for_two_different_members_without_evidence() -> None:
    """2. Dos hallazgos genuinamente distintos (mismo `code`/`gate`,
    pero sobre members DIFERENTES) siguen siendo dos issues -- la
    deduplicacion nunca colapsa defectos reales distintos."""
    gp, v2 = golden_path_with_working_v2_resolution()
    member1 = gp.unified_shadow.shadow_members[0].model_copy(update={"evidence_ids": []})
    member2 = second_member(rule_family=UnifiedRuleFamily.RETURN_CODE, member_id="member::second")
    member2 = member2.model_copy(update={"evidence_ids": []})
    group = gp.unified_shadow.shadow_groups[0].model_copy(
        update={
            "member_ids": [member1.member_id, member2.member_id],
            "evidence_ids": [],
        }
    )
    unified_shadow = gp.unified_shadow.model_copy(
        update={"shadow_members": [member1, member2], "shadow_groups": [group]}
    )
    gp = gp.__class__(
        v1=gp.v1,
        assessment=gp.assessment,
        review_package=gp.review_package,
        plan=gp.plan,
        unified_shadow=unified_shadow,
    )
    report = analyze_golden_path(gp, v2=v2)
    evidence_issues = [
        issue for issue in report.issues if issue.code.value == "SHADOW_MEMBER_WITHOUT_EVIDENCE"
    ]
    assert len(evidence_issues) == 2
    assert {issue.shadow_member_ids[0] for issue in evidence_issues} == {
        member1.member_id,
        member2.member_id,
    }
    assert evidence_issues[0].issue_id != evidence_issues[1].issue_id


def test_analyzer_gate_results_reference_the_deduplicated_issue() -> None:
    """3. `gate_results` referencia el issue EXISTENTE (deduplicado),
    nunca un issue_id huerfano de una copia descartada."""
    gp, v2 = golden_path_with_working_v2_resolution()
    member = gp.unified_shadow.shadow_members[0].model_copy(update={"evidence_ids": []})
    group = gp.unified_shadow.shadow_groups[0].model_copy(update={"evidence_ids": []})
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
    known_issue_ids = {issue.issue_id for issue in report.issues}
    evidence_gate = next(g for g in report.gate_results if g.gate.value == "EVIDENCE_COMPLETENESS")
    assert set(evidence_gate.issue_ids).issubset(known_issue_ids)
    assert len(evidence_gate.issue_ids) == 1
    group_validation = report.group_validations[0]
    assert set(group_validation.issue_ids).issubset(known_issue_ids)


def test_analyzer_summary_reconciles_after_deduplication() -> None:
    """4. El summary reconcilia contra la lista de issues YA
    deduplicada (invariante 21, ya verificada por el propio contrato al
    construir el reporte -- este test la ejercita con un escenario que
    ANTES del fix habria producido un conteo duplicado)."""
    gp, v2 = golden_path_with_working_v2_resolution()
    member = gp.unified_shadow.shadow_members[0].model_copy(update={"evidence_ids": []})
    group = gp.unified_shadow.shadow_groups[0].model_copy(update={"evidence_ids": []})
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
    expected_error_count = sum(1 for issue in report.issues if issue.severity.value == "ERROR")
    assert report.summary.error_count == expected_error_count == 1
    expected_by_code = sum(
        1 for issue in report.issues if issue.code.value == "SHADOW_MEMBER_WITHOUT_EVIDENCE"
    )
    assert report.summary.counts_by_issue_code[Code.SHADOW_MEMBER_WITHOUT_EVIDENCE] == (
        expected_by_code
    )


def test_analyzer_deduplication_is_deterministic_across_calls() -> None:
    """5. IDs y bytes siguen siendo deterministicos: dos ejecuciones
    independientes sobre el mismo escenario (con el hallazgo duplicado
    por dos validadores) producen bytes identicos."""
    gp, v2 = golden_path_with_working_v2_resolution()
    member = gp.unified_shadow.shadow_members[0].model_copy(update={"evidence_ids": []})
    group = gp.unified_shadow.shadow_groups[0].model_copy(update={"evidence_ids": []})
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
    report_a = analyze_golden_path(gp, v2=v2)
    report_b = analyze_golden_path(gp, v2=v2)
    assert report_a.to_stable_json() == report_b.to_stable_json()
