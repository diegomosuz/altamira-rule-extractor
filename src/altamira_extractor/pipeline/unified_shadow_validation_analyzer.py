"""Analizador PURO principal de la validacion diferencial del artefacto
unificado de candidatos en shadow mode (Fase 12 de la ampliacion
semantica, `feat/unified-shadow-differential-validation`).

Orquesta, en orden: (1) integridad de fuentes (Parte 4) -- si una
fuente REQUERIDA esta ausente/invalida o el hash del propio artefacto
unificado no reconcilia, produce de inmediato un reporte con los demas
gates `NOT_EVALUATED`, sin intentar ninguna otra verificacion (nunca un
PASS/FAIL parcial sobre datos no confiables); (2) completitud del
baseline V1 (Parte 5); (3) reconciliacion de plan items contra el plan
REAL (`PLAN_ITEM_UNACCOUNTED`); (4) para CADA
`UnifiedShadowCandidateGroup`: consistencia interna de members/group
(Parte 6), diferencial contra V1 (Parte 7), evidence/provenance/
trazabilidad de decision (Parte 8); (5) construccion de issues con
`issue_id` determinista (unico lugar que los genera: SHA-256 sobre
`code`+`gate`+referencias ordenadas); (6) aplicacion de la politica
(Parte 9) para el estado de cada gate y la disposition global; (7)
reconciliacion del summary; (8) produccion del reporte.

Puro: sin filesystem, sin Neo4j, sin LLM, nunca muta ninguno de sus
argumentos, deterministico (misma entrada siempre produce el mismo
`UnifiedShadowValidationReport`, mismos bytes JSON)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..contracts.candidate import CandidateArtifact
from ..contracts.candidate_promotion_assessment import CandidatePromotionAssessmentArtifact
from ..contracts.candidate_promotion_plan import CandidatePromotionPlanArtifact
from ..contracts.candidate_promotion_review import CandidatePromotionReviewPackage
from ..contracts.interprocedural_rule_candidates import InterproceduralRuleCandidatesArtifact
from ..contracts.unified_candidates_shadow import (
    UnifiedCandidatesShadowArtifact,
    UnifiedShadowCandidateGroup,
    UnifiedShadowComparisonKind,
    UnifiedShadowGroupStatus,
)
from ..contracts.unified_shadow_validation import (
    UnifiedShadowGateStatus,
    UnifiedShadowGroupValidation,
    UnifiedShadowValidationDisposition,
    UnifiedShadowValidationGate,
    UnifiedShadowValidationGateResult,
    UnifiedShadowValidationIssue,
    UnifiedShadowValidationIssueSeverity,
    UnifiedShadowValidationReport,
    UnifiedShadowValidationSummary,
)
from ..contracts.unified_shadow_validation import UnifiedShadowValidationIssueCode as Code
from ..contracts.v2_shadow_candidates import V2ShadowCandidatesArtifact
from .unified_shadow_baseline_validator import validate_baseline_completeness
from .unified_shadow_differential_validator import validate_baseline_differential
from .unified_shadow_evidence_validator import GroupTraceabilityResult, validate_group_traceability
from .unified_shadow_group_validator import validate_group
from .unified_shadow_source_validator import LoadedSource, validate_source_integrity
from .unified_shadow_validation_policy import (
    GATE_BLOCKING,
    GLOBAL_GATES,
    SUMMARY_GATE,
    RawFinding,
    derive_disposition,
    is_group_downstream_eligible,
)

VALIDATOR_VERSION = "1.0"

_GROUP_SCOPED_GATES = (
    UnifiedShadowValidationGate.MEMBER_SOURCE_RESOLUTION,
    UnifiedShadowValidationGate.GROUP_INTERNAL_CONSISTENCY,
    UnifiedShadowValidationGate.BASELINE_DIFFERENTIAL_SAFETY,
    UnifiedShadowValidationGate.EVIDENCE_COMPLETENESS,
    UnifiedShadowValidationGate.PROVENANCE_COMPLETENESS,
    UnifiedShadowValidationGate.DECISION_TRACEABILITY,
)


@dataclass(frozen=True)
class _GroupOutcome:
    group_id: str
    member_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    provenance_references: tuple[str, ...]
    structurally_valid: bool
    downstream_shadow_eligible: bool
    traceability: GroupTraceabilityResult


def _digest(*parts: str) -> str:
    canonical = "\x1f".join(parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _issue_id_for(finding: RawFinding, *, ordinal: int) -> str:
    """Unica funcion que genera `issue_id` -- determinista (SHA-256
    sobre code + gate + todas las referencias ordenadas + un ordinal
    de desempate para dos hallazgos estructuralmente identicos, nunca
    UUID/timestamp/`hash()` de Python)."""
    canonical = _digest(
        finding.code.value,
        finding.gate.value,
        "\x1e".join(sorted(finding.baseline_reference_ids)),
        "\x1e".join(sorted(finding.shadow_member_ids)),
        "\x1e".join(sorted(finding.shadow_group_ids)),
        "\x1e".join(sorted(finding.plan_item_ids)),
        "\x1e".join(sorted(finding.source_candidate_ids)),
        "\x1e".join(sorted(finding.evidence_ids)),
        "\x1e".join(sorted(finding.provenance_references)),
        "\x1e".join(sorted(finding.diagnostics)),
        str(ordinal),
    )
    return f"issue::{canonical}"


def _semantic_key(finding: RawFinding) -> tuple[object, ...]:
    """Identidad semantica deterministica de UN hallazgo -- dos
    `RawFinding` con la misma clave representan el MISMO defecto,
    reportado independientemente por mas de un validador (p. ej.
    `unified_shadow_group_validator.py` Parte 6 y
    `unified_shadow_evidence_validator.py` Parte 8 detectando ambos
    "miembro sin evidence" para el mismo `member_id`) -- nunca se usa
    un contador de aparicion para distinguirlos."""
    return (
        finding.code,
        finding.gate,
        finding.resolved_severity(),
        tuple(sorted(finding.baseline_reference_ids)),
        tuple(sorted(finding.shadow_member_ids)),
        tuple(sorted(finding.shadow_group_ids)),
        tuple(sorted(finding.plan_item_ids)),
        tuple(sorted(finding.source_candidate_ids)),
        tuple(sorted(finding.evidence_ids)),
        tuple(sorted(finding.provenance_references)),
        tuple(sorted(finding.diagnostics)),
        f"MSG_{finding.code.value}",
    )


def _deduplicate_findings(findings: list[RawFinding]) -> list[RawFinding]:
    """Colapsa hallazgos semanticamente identicos (misma clave de
    `_semantic_key`) a UNA sola ocurrencia, preservando el primer orden
    de aparicion -- determinista, ya que `findings` se construye en un
    orden ya determinista (fuentes -> baseline -> plan -> grupos
    ordenados por `unified_shadow_candidate_id`). Se ejecuta ANTES de
    generar `issue_id`, de calcular `gate_results.issue_ids` y de
    reconciliar `summary` -- todos esos calculos derivan exclusivamente
    de la lista ya deduplicada."""
    seen: dict[tuple[object, ...], RawFinding] = {}
    for finding in findings:
        key = _semantic_key(finding)
        if key not in seen:
            seen[key] = finding
    return list(seen.values())


def _build_issues(findings: list[RawFinding]) -> list[UnifiedShadowValidationIssue]:
    findings = _deduplicate_findings(findings)
    issues: list[UnifiedShadowValidationIssue] = []
    seen_ids: set[str] = set()
    for finding in findings:
        ordinal = 0
        while True:
            issue_id = _issue_id_for(finding, ordinal=ordinal)
            if issue_id not in seen_ids:
                break
            ordinal += 1
        seen_ids.add(issue_id)
        issues.append(
            UnifiedShadowValidationIssue(
                issue_id=issue_id,
                code=finding.code,
                severity=finding.resolved_severity(),
                gate=finding.gate,
                message_code=f"MSG_{finding.code.value}",
                baseline_reference_ids=sorted(set(finding.baseline_reference_ids)),
                shadow_member_ids=sorted(set(finding.shadow_member_ids)),
                shadow_group_ids=sorted(set(finding.shadow_group_ids)),
                plan_item_ids=sorted(set(finding.plan_item_ids)),
                source_candidate_ids=sorted(set(finding.source_candidate_ids)),
                evidence_ids=sorted(set(finding.evidence_ids)),
                provenance_references=sorted(set(finding.provenance_references)),
                diagnostics=sorted(set(finding.diagnostics)),
            )
        )
    return sorted(issues, key=lambda issue: issue.issue_id)


def _build_gate_results(
    *,
    issues: list[UnifiedShadowValidationIssue],
    status_by_gate: dict[UnifiedShadowValidationGate, UnifiedShadowGateStatus],
    checked_group_ids: list[str],
) -> list[UnifiedShadowValidationGateResult]:
    results = [
        UnifiedShadowValidationGateResult(
            gate=gate,
            status=status_by_gate[gate],
            required=True,
            blocking=GATE_BLOCKING[gate],
            issue_ids=sorted({issue.issue_id for issue in issues if issue.gate == gate}),
            checked_group_ids=(
                checked_group_ids
                if status_by_gate[gate] != UnifiedShadowGateStatus.NOT_EVALUATED
                else []
            ),
        )
        for gate in (*GLOBAL_GATES, SUMMARY_GATE)
    ]
    return sorted(results, key=lambda result: result.gate.value)


def _empty_summary(
    *,
    issues: list[UnifiedShadowValidationIssue],
    gate_results: list[UnifiedShadowValidationGateResult],
) -> UnifiedShadowValidationSummary:
    counts_by_gate_status: dict[UnifiedShadowGateStatus, int] = {}
    for result in gate_results:
        counts_by_gate_status[result.status] = counts_by_gate_status.get(result.status, 0) + 1
    counts_by_severity: dict[UnifiedShadowValidationIssueSeverity, int] = {}
    counts_by_code: dict[Code, int] = {}
    for issue in issues:
        counts_by_severity[issue.severity] = counts_by_severity.get(issue.severity, 0) + 1
        counts_by_code[issue.code] = counts_by_code.get(issue.code, 0) + 1
    return UnifiedShadowValidationSummary(
        baseline_candidate_count=0,
        shadow_member_count=0,
        shadow_group_count=0,
        valid_shadow_group_count=0,
        invalid_shadow_group_count=0,
        downstream_eligible_group_count=0,
        exact_baseline_match_group_count=0,
        related_to_baseline_group_count=0,
        not_in_baseline_group_count=0,
        conflicting_with_baseline_group_count=0,
        not_evaluated_group_count=0,
        groups_with_complete_evidence_count=0,
        groups_with_complete_provenance_count=0,
        groups_with_complete_decision_trace_count=0,
        error_count=counts_by_severity.get(UnifiedShadowValidationIssueSeverity.ERROR, 0),
        warning_count=counts_by_severity.get(UnifiedShadowValidationIssueSeverity.WARNING, 0),
        blocking_issue_count=counts_by_severity.get(
            UnifiedShadowValidationIssueSeverity.BLOCKING, 0
        ),
        counts_by_gate_status=counts_by_gate_status,
        counts_by_issue_severity=counts_by_severity,
        counts_by_issue_code=counts_by_code,
        counts_by_group_status={},
        counts_by_baseline_comparison={},
    )


def _not_evaluated_report(
    *,
    run_id: str,
    source_package_hash: str,
    unified_candidates_shadow_hash: str | None,
    candidate_v1_artifact_hash: str | None,
    v2_artifact_hash: str | None,
    interprocedural_artifact_hash: str | None,
    assessment_artifact_hash: str | None,
    review_package_hash: str | None,
    promotion_plan_hash: str | None,
    source_artifact_hashes: dict[str, str],
    findings: list[RawFinding],
) -> UnifiedShadowValidationReport:
    issues = _build_issues(findings)
    status_by_gate = {
        gate: (
            UnifiedShadowGateStatus.FAIL
            if gate == UnifiedShadowValidationGate.SOURCE_INTEGRITY
            else UnifiedShadowGateStatus.NOT_EVALUATED
        )
        for gate in (*GLOBAL_GATES, SUMMARY_GATE)
    }
    gate_results = _build_gate_results(
        issues=issues, status_by_gate=status_by_gate, checked_group_ids=[]
    )
    summary = _empty_summary(issues=issues, gate_results=gate_results)

    return UnifiedShadowValidationReport(
        run_id=run_id,
        source_package_hash=source_package_hash,
        unified_candidates_shadow_hash=unified_candidates_shadow_hash or ("0" * 64),
        candidate_v1_artifact_hash=candidate_v1_artifact_hash or ("0" * 64),
        v2_artifact_hash=v2_artifact_hash,
        interprocedural_artifact_hash=interprocedural_artifact_hash,
        assessment_artifact_hash=assessment_artifact_hash or ("0" * 64),
        review_package_hash=review_package_hash or ("0" * 64),
        promotion_plan_hash=promotion_plan_hash or ("0" * 64),
        source_artifact_hashes=source_artifact_hashes,
        disposition=UnifiedShadowValidationDisposition.NOT_EVALUATED,
        gate_results=gate_results,
        group_validations=[],
        issues=issues,
        summary=summary,
        diagnostics=[],
    )


def _group_scoped_gate_results(
    *, outcome: _GroupOutcome, issues: list[UnifiedShadowValidationIssue]
) -> list[UnifiedShadowValidationGateResult]:
    group_issue_ids = {
        issue.issue_id for issue in issues if outcome.group_id in issue.shadow_group_ids
    }
    results = []
    for gate in _GROUP_SCOPED_GATES:
        gate_issue_ids = sorted(
            issue.issue_id
            for issue in issues
            if issue.gate == gate and issue.issue_id in group_issue_ids
        )
        failed = any(
            issue.severity
            in (
                UnifiedShadowValidationIssueSeverity.ERROR,
                UnifiedShadowValidationIssueSeverity.BLOCKING,
            )
            for issue in issues
            if issue.issue_id in gate_issue_ids
        )
        results.append(
            UnifiedShadowValidationGateResult(
                gate=gate,
                status=(UnifiedShadowGateStatus.FAIL if failed else UnifiedShadowGateStatus.PASS),
                required=True,
                blocking=GATE_BLOCKING[gate],
                issue_ids=gate_issue_ids,
                checked_group_ids=[outcome.group_id],
            )
        )
    return sorted(results, key=lambda result: result.gate.value)


def _build_summary(
    *,
    unified_shadow_artifact: UnifiedCandidatesShadowArtifact,
    group_validations: list[UnifiedShadowGroupValidation],
    outcomes: list[_GroupOutcome],
    issues: list[UnifiedShadowValidationIssue],
    gate_results: list[UnifiedShadowValidationGateResult],
    eligible_count: int,
) -> UnifiedShadowValidationSummary:
    traceability_by_group = {outcome.group_id: outcome.traceability for outcome in outcomes}

    counts_by_group_status: dict[UnifiedShadowGroupStatus, int] = {}
    counts_by_comparison: dict[UnifiedShadowComparisonKind, int] = {}
    for gv in group_validations:
        counts_by_group_status[gv.group_status] = counts_by_group_status.get(gv.group_status, 0) + 1
        counts_by_comparison[gv.comparison_to_v1] = (
            counts_by_comparison.get(gv.comparison_to_v1, 0) + 1
        )

    counts_by_severity: dict[UnifiedShadowValidationIssueSeverity, int] = {}
    counts_by_code: dict[Code, int] = {}
    for issue in issues:
        counts_by_severity[issue.severity] = counts_by_severity.get(issue.severity, 0) + 1
        counts_by_code[issue.code] = counts_by_code.get(issue.code, 0) + 1
    counts_by_gate_status: dict[UnifiedShadowGateStatus, int] = {}
    for result in gate_results:
        counts_by_gate_status[result.status] = counts_by_gate_status.get(result.status, 0) + 1

    valid_group_count = sum(1 for gv in group_validations if gv.structurally_valid)

    return UnifiedShadowValidationSummary(
        baseline_candidate_count=len(unified_shadow_artifact.baseline_candidates),
        shadow_member_count=len(unified_shadow_artifact.shadow_members),
        shadow_group_count=len(group_validations),
        valid_shadow_group_count=valid_group_count,
        invalid_shadow_group_count=len(group_validations) - valid_group_count,
        downstream_eligible_group_count=eligible_count,
        exact_baseline_match_group_count=counts_by_comparison.get(
            UnifiedShadowComparisonKind.EXACT_BASELINE_MATCH, 0
        ),
        related_to_baseline_group_count=counts_by_comparison.get(
            UnifiedShadowComparisonKind.RELATED_TO_BASELINE, 0
        ),
        not_in_baseline_group_count=counts_by_comparison.get(
            UnifiedShadowComparisonKind.NOT_IN_BASELINE, 0
        ),
        conflicting_with_baseline_group_count=counts_by_comparison.get(
            UnifiedShadowComparisonKind.CONFLICTS_WITH_BASELINE, 0
        ),
        not_evaluated_group_count=counts_by_comparison.get(
            UnifiedShadowComparisonKind.NOT_EVALUATED, 0
        ),
        groups_with_complete_evidence_count=sum(
            1 for t in traceability_by_group.values() if t.evidence_complete
        ),
        groups_with_complete_provenance_count=sum(
            1 for t in traceability_by_group.values() if t.provenance_complete
        ),
        groups_with_complete_decision_trace_count=sum(
            1 for t in traceability_by_group.values() if t.decision_trace_complete
        ),
        error_count=counts_by_severity.get(UnifiedShadowValidationIssueSeverity.ERROR, 0),
        warning_count=counts_by_severity.get(UnifiedShadowValidationIssueSeverity.WARNING, 0),
        blocking_issue_count=counts_by_severity.get(
            UnifiedShadowValidationIssueSeverity.BLOCKING, 0
        ),
        counts_by_gate_status=counts_by_gate_status,
        counts_by_issue_severity=counts_by_severity,
        counts_by_issue_code=counts_by_code,
        counts_by_group_status=counts_by_group_status,
        counts_by_baseline_comparison=counts_by_comparison,
    )


def analyze_unified_shadow_validation(
    *,
    run_id: str,
    source_package_hash: str,
    v1: LoadedSource,
    v2: LoadedSource,
    interprocedural: LoadedSource,
    assessment: LoadedSource,
    review_package: LoadedSource,
    plan: LoadedSource,
    unified_shadow: LoadedSource,
    candidate_v1_artifact_hash: str | None,
    v2_artifact_hash: str | None,
    interprocedural_artifact_hash: str | None,
    assessment_artifact_hash: str | None,
    review_package_hash: str | None,
    promotion_plan_hash: str | None,
    unified_candidates_shadow_hash: str | None,
    source_artifact_hashes: dict[str, str],
) -> UnifiedShadowValidationReport:
    """Punto de entrada puro. Nunca muta ninguno de sus argumentos.
    Determinista: misma entrada siempre produce el mismo
    `UnifiedShadowValidationReport` (mismos bytes JSON)."""
    source_result = validate_source_integrity(
        run_id=run_id,
        source_package_hash=source_package_hash,
        v1=v1,
        v2=v2,
        interprocedural=interprocedural,
        assessment=assessment,
        review_package=review_package,
        plan=plan,
        unified_shadow=unified_shadow,
        candidate_v1_artifact_hash=candidate_v1_artifact_hash,
        v2_artifact_hash=v2_artifact_hash,
        interprocedural_artifact_hash=interprocedural_artifact_hash,
        assessment_artifact_hash=assessment_artifact_hash,
        review_package_hash=review_package_hash,
        promotion_plan_hash=promotion_plan_hash,
        unified_candidates_shadow_hash=unified_candidates_shadow_hash,
    )

    if source_result.required_source_missing or not source_result.gate_passed:
        return _not_evaluated_report(
            run_id=run_id,
            source_package_hash=source_package_hash,
            unified_candidates_shadow_hash=unified_candidates_shadow_hash,
            candidate_v1_artifact_hash=candidate_v1_artifact_hash,
            v2_artifact_hash=v2_artifact_hash,
            interprocedural_artifact_hash=interprocedural_artifact_hash,
            assessment_artifact_hash=assessment_artifact_hash,
            review_package_hash=review_package_hash,
            promotion_plan_hash=promotion_plan_hash,
            source_artifact_hashes=source_artifact_hashes,
            findings=list(source_result.findings),
        )

    v1_candidates = v1.artifact
    v2_candidates = v2.artifact
    interprocedural_candidates = interprocedural.artifact
    assessment_artifact = assessment.artifact
    review_package_artifact = review_package.artifact
    plan_artifact = plan.artifact
    unified_shadow_artifact = unified_shadow.artifact
    assert isinstance(v1_candidates, CandidateArtifact)
    assert isinstance(assessment_artifact, CandidatePromotionAssessmentArtifact)
    assert isinstance(review_package_artifact, CandidatePromotionReviewPackage)
    assert isinstance(plan_artifact, CandidatePromotionPlanArtifact)
    assert isinstance(unified_shadow_artifact, UnifiedCandidatesShadowArtifact)
    if v2_candidates is not None:
        assert isinstance(v2_candidates, V2ShadowCandidatesArtifact)
    if interprocedural_candidates is not None:
        assert isinstance(interprocedural_candidates, InterproceduralRuleCandidatesArtifact)

    all_findings: list[RawFinding] = list(source_result.findings)

    baseline_result = validate_baseline_completeness(
        v1_candidates=v1_candidates,
        unified_shadow=unified_shadow_artifact,
        candidate_v1_artifact_hash=candidate_v1_artifact_hash or "",
    )
    all_findings.extend(baseline_result.findings)

    # PLAN_ITEM_UNACCOUNTED: reconciliacion cruzada contra el plan REAL
    # (nunca solo contra los conteos internos de Fase 11).
    plan_item_ids = {item.plan_item_id for item in plan_artifact.plan_items}
    accounted_ids = {member.plan_item_id for member in unified_shadow_artifact.shadow_members} | {
        item.plan_item_id for item in unified_shadow_artifact.excluded_plan_items
    }
    unaccounted = sorted(plan_item_ids - accounted_ids)
    if unaccounted or plan_item_ids != accounted_ids:
        all_findings.append(
            RawFinding(
                code=Code.PLAN_ITEM_UNACCOUNTED,
                gate=UnifiedShadowValidationGate.PLAN_BINDING_INTEGRITY,
                plan_item_ids=tuple(unaccounted),
                diagnostics=(
                    f"plan_item_count={len(plan_item_ids)}",
                    f"accounted_count={len(accounted_ids)}",
                ),
            )
        )

    members_by_id = {member.member_id: member for member in unified_shadow_artifact.shadow_members}

    outcomes: list[_GroupOutcome] = []
    for group in unified_shadow_artifact.shadow_groups:
        group_result = validate_group(
            group,
            members_by_id=members_by_id,
            assessment=assessment_artifact,
            plan=plan_artifact,
            v1_candidates=v1_candidates,
            v2_candidates=v2_candidates,
            interprocedural_candidates=interprocedural_candidates,
        )
        differential_result = validate_baseline_differential(group)
        members = [
            members_by_id[member_id] for member_id in group.member_ids if member_id in members_by_id
        ]
        traceability_result = validate_group_traceability(
            group,
            members=members,
            assessment=assessment_artifact,
            review_package=review_package_artifact,
            plan=plan_artifact,
        )

        group_findings = (
            list(group_result.findings)
            + list(differential_result.findings)
            + list(traceability_result.findings)
        )
        all_findings.extend(group_findings)

        has_error_or_blocking = any(
            finding.resolved_severity()
            in (
                UnifiedShadowValidationIssueSeverity.ERROR,
                UnifiedShadowValidationIssueSeverity.BLOCKING,
            )
            for finding in group_findings
        )
        member_source_resolution_complete = bool(group_result.member_results) and all(
            r.source_resolution_complete for r in group_result.member_results
        )
        eligible = is_group_downstream_eligible(
            group_status=group.status.value,
            comparison_to_v1=group.comparison_to_v1.value,
            rule_family_is_unknown=group.rule_family.value == "UNKNOWN",
            support_is_blocked=group.support.value == "BLOCKED",
            member_source_resolution_complete=member_source_resolution_complete,
            evidence_complete=traceability_result.evidence_complete,
            provenance_complete=traceability_result.provenance_complete,
            decision_trace_complete=traceability_result.decision_trace_complete,
            has_error_or_blocking_issue=has_error_or_blocking,
        )
        structurally_valid = group_result.structurally_valid and not has_error_or_blocking

        outcomes.append(
            _GroupOutcome(
                group_id=group.unified_shadow_candidate_id,
                member_ids=tuple(sorted(group.member_ids)),
                evidence_ids=tuple(sorted(set(group.evidence_ids))),
                provenance_references=tuple(sorted(set(group.provenance_references))),
                structurally_valid=structurally_valid,
                downstream_shadow_eligible=eligible,
                traceability=traceability_result,
            )
        )

    if not any(group.status.value == "VALID" for group in unified_shadow_artifact.shadow_groups):
        all_findings.append(
            RawFinding(
                code=Code.NO_VALID_SHADOW_GROUPS,
                gate=UnifiedShadowValidationGate.DOWNSTREAM_SHADOW_ELIGIBILITY,
            )
        )

    all_findings.append(
        RawFinding(
            code=Code.FUNCTIONAL_VALIDATION_REQUIRED,
            gate=UnifiedShadowValidationGate.DOWNSTREAM_SHADOW_ELIGIBILITY,
        )
    )

    issues = _build_issues(all_findings)
    issue_ids_by_group: dict[str, list[str]] = {}
    for issue in issues:
        for group_id in issue.shadow_group_ids:
            issue_ids_by_group.setdefault(group_id, []).append(issue.issue_id)

    groups_by_id: dict[str, UnifiedShadowCandidateGroup] = {
        group.unified_shadow_candidate_id: group for group in unified_shadow_artifact.shadow_groups
    }

    group_validations = sorted(
        (
            UnifiedShadowGroupValidation(
                group_id=outcome.group_id,
                group_status=groups_by_id[outcome.group_id].status,
                comparison_to_v1=groups_by_id[outcome.group_id].comparison_to_v1,
                structurally_valid=outcome.structurally_valid,
                downstream_shadow_eligible=outcome.downstream_shadow_eligible,
                gate_results=_group_scoped_gate_results(outcome=outcome, issues=issues),
                issue_ids=sorted(set(issue_ids_by_group.get(outcome.group_id, []))),
                member_ids=list(outcome.member_ids),
                evidence_ids=list(outcome.evidence_ids),
                provenance_references=list(outcome.provenance_references),
                diagnostics=[],
            )
            for outcome in outcomes
        ),
        key=lambda gv: gv.group_id,
    )

    status_by_gate: dict[UnifiedShadowValidationGate, UnifiedShadowGateStatus] = {}
    for gate in GLOBAL_GATES:
        failed = any(
            issue.severity
            in (
                UnifiedShadowValidationIssueSeverity.ERROR,
                UnifiedShadowValidationIssueSeverity.BLOCKING,
            )
            for issue in issues
            if issue.gate == gate
        )
        # DETERMINISTIC_SERIALIZATION se demuestra por fuera del
        # analizador (doble ejecucion del servicio/tests, Parte 11),
        # nunca por autocomparacion dentro de una unica llamada -- PASS
        # aqui siempre que la evaluacion se completo.
        status_by_gate[gate] = (
            UnifiedShadowGateStatus.PASS
            if gate == UnifiedShadowValidationGate.DETERMINISTIC_SERIALIZATION
            else (UnifiedShadowGateStatus.FAIL if failed else UnifiedShadowGateStatus.PASS)
        )
    eligible_count = sum(1 for gv in group_validations if gv.downstream_shadow_eligible)
    status_by_gate[SUMMARY_GATE] = (
        UnifiedShadowGateStatus.PASS if eligible_count > 0 else UnifiedShadowGateStatus.FAIL
    )

    gate_results = _build_gate_results(
        issues=issues,
        status_by_gate=status_by_gate,
        checked_group_ids=sorted(gv.group_id for gv in group_validations),
    )

    has_blocking_issue = any(
        issue.severity == UnifiedShadowValidationIssueSeverity.BLOCKING for issue in issues
    )
    has_warning_issue = any(
        issue.severity == UnifiedShadowValidationIssueSeverity.WARNING for issue in issues
    )
    disposition = derive_disposition(
        required_source_missing=False,
        gate_statuses=status_by_gate,
        has_blocking_issue=has_blocking_issue,
        has_warning_issue=has_warning_issue,
        group_validations=group_validations,
    )

    summary = _build_summary(
        unified_shadow_artifact=unified_shadow_artifact,
        group_validations=group_validations,
        outcomes=outcomes,
        issues=issues,
        gate_results=gate_results,
        eligible_count=eligible_count,
    )

    return UnifiedShadowValidationReport(
        run_id=run_id,
        source_package_hash=source_package_hash,
        unified_candidates_shadow_hash=unified_candidates_shadow_hash or "",
        candidate_v1_artifact_hash=candidate_v1_artifact_hash or "",
        v2_artifact_hash=v2_artifact_hash,
        interprocedural_artifact_hash=interprocedural_artifact_hash,
        assessment_artifact_hash=assessment_artifact_hash or "",
        review_package_hash=review_package_hash or "",
        promotion_plan_hash=promotion_plan_hash or "",
        source_artifact_hashes=source_artifact_hashes,
        disposition=disposition,
        gate_results=gate_results,
        group_validations=group_validations,
        issues=issues,
        summary=summary,
        diagnostics=[],
    )
