"""Evaluador PURO de activacion unificada (Fase 14A Parte 8,
`feat/controlled-unified-activation`).

Orquesta: validar bindings globales -> seleccionar canary -> adaptar
V1 -> adaptar unified -> comparar -> aplicar politica -> generar
issues informativos -> reconciliar summary -> producir el artefacto.
Puro: sin filesystem, sin Neo4j, sin red, sin proveedor, nunca muta
sus argumentos, deterministico.

Inconsistencias GLOBALES entre fuentes (run_id/source_package_hash
distintos entre los artefactos recibidos) son un fallo TECNICO -- se
propagan como `UnifiedActivationEvaluatorError` (mismo principio que
`unified_shadow_downstream_executor.py::
UnifiedShadowDownstreamExecutorError`, Fase 13): nunca se construye un
artefacto parcial, el llamador (Parte 9, servicio) nunca persiste
nada."""

from __future__ import annotations

from ..contracts.candidate import CandidateArtifact
from ..contracts.guardrail_candidate import GuardrailCandidateArtifact
from ..contracts.unified_activation_config import UnifiedActivationConfig, UnifiedActivationMode
from ..contracts.unified_activation_evaluation import (
    UnifiedActivationComparison,
    UnifiedActivationComparisonKind,
    UnifiedActivationComparisonLevel,
    UnifiedActivationDecision,
    UnifiedActivationEvaluationArtifact,
    UnifiedActivationEvaluationSummary,
    UnifiedActivationIssue,
    UnifiedActivationIssueCode,
    UnifiedActivationIssueSeverity,
    UnifiedActivationReadinessDisposition,
    UnifiedActivationUnifiedReference,
    UnifiedActivationV1Reference,
)
from ..contracts.unified_candidates_shadow import UnifiedCandidatesShadowArtifact
from ..contracts.unified_shadow_downstream import (
    UnifiedShadowDownstreamArtifact,
    UnifiedShadowGuardrailStatus,
)
from ..contracts.unified_shadow_validation import UnifiedShadowValidationReport
from .unified_activation_canary_selector import select_canary
from .unified_activation_comparator import compare_references
from .unified_activation_policy import apply_activation_policy
from .unified_activation_reference_adapters import (
    adapt_unified_references,
    adapt_v1_references,
)


class UnifiedActivationEvaluatorError(Exception):
    """Fallo tecnico GLOBAL (nunca aislable): las fuentes recibidas
    son inconsistentes entre si (run_id/source_package_hash distintos)
    -- el llamador nunca debe persistir un artefacto parcial."""


_CANARY_MODES = frozenset(
    {UnifiedActivationMode.UNIFIED_CANARY, UnifiedActivationMode.UNIFIED_PRIMARY_WITH_V1_FALLBACK}
)

_IssueEntry = tuple[UnifiedActivationIssueCode, UnifiedActivationIssueSeverity]
_INFO_ISSUE_BY_KIND: dict[UnifiedActivationComparisonKind, _IssueEntry] = {
    UnifiedActivationComparisonKind.EXACT_EQUIVALENT: (
        UnifiedActivationIssueCode.V1_UNIFIED_EXACT_EQUIVALENT,
        UnifiedActivationIssueSeverity.INFO,
    ),
    UnifiedActivationComparisonKind.UNIFIED_ADDITIVE: (
        UnifiedActivationIssueCode.UNIFIED_INCREMENTAL_RESULT,
        UnifiedActivationIssueSeverity.INFO,
    ),
    UnifiedActivationComparisonKind.V1_ONLY: (
        UnifiedActivationIssueCode.V1_RESULT_NOT_REPRESENTED,
        UnifiedActivationIssueSeverity.WARNING,
    ),
    UnifiedActivationComparisonKind.CONFLICTING: (
        UnifiedActivationIssueCode.V1_UNIFIED_CONFLICT,
        UnifiedActivationIssueSeverity.WARNING,
    ),
    UnifiedActivationComparisonKind.NOT_COMPARABLE: (
        UnifiedActivationIssueCode.RESULT_NOT_COMPARABLE,
        UnifiedActivationIssueSeverity.INFO,
    ),
}


def _check_hash(expected: str, actual: str | None, *, label: str) -> None:
    if actual is not None and actual != expected:
        raise UnifiedActivationEvaluatorError(
            f"source_package_hash inconsistente en {label}: se esperaba {expected!r}"
        )


def evaluate_unified_activation(
    config: UnifiedActivationConfig,
    *,
    run_id: str,
    source_package_hash: str,
    config_hash: str,
    candidate_v1_artifact: CandidateArtifact | None,
    candidate_v1_artifact_hash: str | None,
    guardrail_artifacts_by_candidate_id: dict[str, GuardrailCandidateArtifact] | None = None,
    rule_markdown_filename_by_candidate_id: dict[str, str] | None = None,
    unified_shadow: UnifiedCandidatesShadowArtifact | None = None,
    unified_candidates_shadow_hash: str | None = None,
    validation_report: UnifiedShadowValidationReport | None = None,
    validation_report_hash: str | None = None,
    downstream_artifact: UnifiedShadowDownstreamArtifact | None = None,
    downstream_artifact_hash: str | None = None,
) -> UnifiedActivationEvaluationArtifact:
    """Punto de entrada puro. Lanza `UnifiedActivationEvaluatorError`
    (tecnico, global) si `run_id`/`source_package_hash` son
    inconsistentes entre las fuentes recibidas. En cualquier otro caso
    SIEMPRE retorna un artefacto valido."""
    if candidate_v1_artifact is not None and candidate_v1_artifact.run_id != run_id:
        raise UnifiedActivationEvaluatorError(
            "run_id inconsistente entre CandidateArtifact V1 y el run_id esperado"
        )
    if unified_shadow is not None and unified_shadow.run_id != run_id:
        raise UnifiedActivationEvaluatorError(
            "run_id inconsistente entre UnifiedCandidatesShadowArtifact y el run_id esperado"
        )
    if validation_report is not None and validation_report.run_id != run_id:
        raise UnifiedActivationEvaluatorError(
            "run_id inconsistente entre UnifiedShadowValidationReport y el run_id esperado"
        )
    if downstream_artifact is not None and downstream_artifact.run_id != run_id:
        raise UnifiedActivationEvaluatorError(
            "run_id inconsistente entre UnifiedShadowDownstreamArtifact y el run_id esperado"
        )

    if candidate_v1_artifact is not None:
        _check_hash(
            source_package_hash,
            candidate_v1_artifact.source_package_hash,
            label="CandidateArtifact V1",
        )
    if unified_shadow is not None:
        _check_hash(
            source_package_hash,
            unified_shadow.source_package_hash,
            label="UnifiedCandidatesShadowArtifact",
        )
    if validation_report is not None:
        _check_hash(
            source_package_hash,
            validation_report.source_package_hash,
            label="UnifiedShadowValidationReport",
        )
    if downstream_artifact is not None:
        _check_hash(
            source_package_hash,
            downstream_artifact.source_package_hash,
            label="UnifiedShadowDownstreamArtifact",
        )

    v1_available = candidate_v1_artifact is not None
    unified_shadow_available = unified_shadow is not None

    canary_selection = None
    if config.mode in _CANARY_MODES:
        canary_selection = select_canary(
            config, source_package_hash=source_package_hash, run_id=run_id
        )

    v1_references = adapt_v1_references(
        candidate_v1_artifact,
        guardrail_artifacts_by_candidate_id=guardrail_artifacts_by_candidate_id,
        rule_markdown_filename_by_candidate_id=rule_markdown_filename_by_candidate_id,
    )
    unified_references = adapt_unified_references(unified_shadow, downstream=downstream_artifact)

    comparisons = (
        compare_references(
            v1_references,
            unified_references,
            v1_available=v1_available,
            unified_available=unified_shadow_available,
        )
        if config.comparison_required or config.mode != UnifiedActivationMode.V1_ONLY
        else []
    )

    v1_rule_level_ids = frozenset(
        r.reference_id for r in v1_references if r.level == UnifiedActivationComparisonLevel.RULE
    )

    policy_result = apply_activation_policy(
        config,
        canary_selection=canary_selection,
        v1_available=v1_available,
        unified_shadow_available=unified_shadow_available,
        validation_report=validation_report,
        downstream_artifact=downstream_artifact,
        comparisons=comparisons,
        v1_rule_level_reference_ids=v1_rule_level_ids,
    )

    issues = list(policy_result.issues)
    policy_flagged_comparison_ids = {
        cid for issue in policy_result.issues for cid in issue.comparison_ids
    }
    for comparison in comparisons:
        if comparison.comparison_id in policy_flagged_comparison_ids:
            continue
        entry = _INFO_ISSUE_BY_KIND.get(comparison.kind)
        if entry is None:
            continue
        code, severity = entry
        issues.append(
            UnifiedActivationIssue(
                issue_id=f"activation-issue::info::{comparison.comparison_id}",
                code=code,
                severity=severity,
                comparison_ids=[comparison.comparison_id],
                related_v1_reference_ids=sorted(comparison.v1_reference_ids),
                related_unified_reference_ids=sorted(comparison.unified_reference_ids),
                message_code=f"MSG_{code.value}",
            )
        )

    if policy_result.readiness_disposition != UnifiedActivationReadinessDisposition.NOT_EVALUATED:
        issues.append(
            UnifiedActivationIssue(
                issue_id="activation-issue::functional-validation-required",
                code=UnifiedActivationIssueCode.FUNCTIONAL_VALIDATION_REQUIRED,
                severity=UnifiedActivationIssueSeverity.INFO,
                message_code=f"MSG_{UnifiedActivationIssueCode.FUNCTIONAL_VALIDATION_REQUIRED.value}",
            )
        )

    issues = sorted(issues, key=lambda i: i.issue_id)

    summary = _build_summary(
        v1_references,
        unified_references,
        comparisons,
        issues,
        downstream_artifact,
        policy_result.activation_decision,
    )

    return UnifiedActivationEvaluationArtifact(
        run_id=run_id,
        source_package_hash=source_package_hash,
        config_hash=config_hash,
        mode=config.mode,
        provider_policy=config.provider_policy,
        canary_selection=canary_selection,
        requested_lane=policy_result.requested_lane,
        effective_lane=policy_result.effective_lane,
        fallback_lane=policy_result.fallback_lane,
        readiness_disposition=policy_result.readiness_disposition,
        activation_decision=policy_result.activation_decision,
        candidate_v1_artifact_hash=candidate_v1_artifact_hash,
        unified_candidates_shadow_hash=unified_candidates_shadow_hash,
        validation_report_hash=validation_report_hash,
        downstream_artifact_hash=downstream_artifact_hash,
        v1_references=sorted(v1_references, key=lambda r: r.reference_id),
        unified_references=sorted(unified_references, key=lambda r: r.reference_id),
        comparisons=sorted(comparisons, key=lambda c: c.comparison_id),
        issues=issues,
        summary=summary,
    )


def _build_summary(
    v1_references: list[UnifiedActivationV1Reference],
    unified_references: list[UnifiedActivationUnifiedReference],
    comparisons: list[UnifiedActivationComparison],
    issues: list[UnifiedActivationIssue],
    downstream_artifact: UnifiedShadowDownstreamArtifact | None,
    decision: UnifiedActivationDecision,
) -> UnifiedActivationEvaluationSummary:
    counts_by_kind: dict[UnifiedActivationComparisonKind, int] = {}
    for comparison in comparisons:
        counts_by_kind[comparison.kind] = counts_by_kind.get(comparison.kind, 0) + 1

    counts_by_severity: dict[UnifiedActivationIssueSeverity, int] = {}
    for issue in issues:
        counts_by_severity[issue.severity] = counts_by_severity.get(issue.severity, 0) + 1

    guardrail_passed = 0
    guardrail_rejected = 0
    technical_failures = 0
    if downstream_artifact is not None:
        guardrail_passed = sum(
            1
            for r in downstream_artifact.guardrail_results
            if r.status == UnifiedShadowGuardrailStatus.PASSED
        )
        guardrail_rejected = sum(
            1
            for r in downstream_artifact.guardrail_results
            if r.status == UnifiedShadowGuardrailStatus.REJECTED
        )
        technical_failures = downstream_artifact.summary.technical_failure_count

    return UnifiedActivationEvaluationSummary(
        v1_reference_count=len(v1_references),
        unified_reference_count=len(unified_references),
        exact_equivalent_count=counts_by_kind.get(
            UnifiedActivationComparisonKind.EXACT_EQUIVALENT, 0
        ),
        unified_additive_count=counts_by_kind.get(
            UnifiedActivationComparisonKind.UNIFIED_ADDITIVE, 0
        ),
        v1_only_count=counts_by_kind.get(UnifiedActivationComparisonKind.V1_ONLY, 0),
        related_count=counts_by_kind.get(UnifiedActivationComparisonKind.RELATED, 0),
        conflicting_count=counts_by_kind.get(UnifiedActivationComparisonKind.CONFLICTING, 0),
        not_comparable_count=counts_by_kind.get(UnifiedActivationComparisonKind.NOT_COMPARABLE, 0),
        not_evaluated_count=counts_by_kind.get(UnifiedActivationComparisonKind.NOT_EVALUATED, 0),
        guardrail_passed_count=guardrail_passed,
        guardrail_rejected_count=guardrail_rejected,
        technical_failure_count=technical_failures,
        error_count=counts_by_severity.get(UnifiedActivationIssueSeverity.ERROR, 0),
        warning_count=counts_by_severity.get(UnifiedActivationIssueSeverity.WARNING, 0),
        blocking_issue_count=counts_by_severity.get(UnifiedActivationIssueSeverity.BLOCKING, 0),
        counts_by_comparison_kind=counts_by_kind,
        counts_by_issue_severity=counts_by_severity,
        counts_by_decision={decision: 1},
    )


__all__ = ["UnifiedActivationEvaluatorError", "evaluate_unified_activation"]
