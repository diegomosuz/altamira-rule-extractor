"""Tests de contrato del artefacto de evaluacion de activacion
unificada (Fase 14A Parte 4, `feat/controlled-unified-activation`).
Construye instancias MINIMAS directamente contra el modelo Pydantic
(sin pasar por el evaluador) para aislar cada invariante -- ver
`tests/pipeline/test_unified_activation_evaluator.py` para pruebas de
comportamiento end-to-end del evaluador real."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts.unified_activation_config import (
    UnifiedActivationMode,
    UnifiedActivationProviderPolicy,
)
from altamira_extractor.contracts.unified_activation_evaluation import (
    UnifiedActivationComparison,
    UnifiedActivationComparisonKind,
    UnifiedActivationComparisonLevel,
    UnifiedActivationDecision,
    UnifiedActivationEvaluationArtifact,
    UnifiedActivationEvaluationSummary,
    UnifiedActivationIssue,
    UnifiedActivationIssueCode,
    UnifiedActivationIssueSeverity,
    UnifiedActivationLane,
    UnifiedActivationReadinessDisposition,
    UnifiedActivationUnifiedReference,
    UnifiedActivationV1Reference,
)

HASH = "a" * 64
RUN_ID = "20260101T000000000000-aaaaaaaa"


def _v1_ref(rid: str = "v1::1") -> UnifiedActivationV1Reference:
    return UnifiedActivationV1Reference(
        reference_id=rid,
        source_candidate_id=rid,
        level=UnifiedActivationComparisonLevel.CANDIDATE,
        program="PROG",
        output_literal="R001",
    )


def _unified_ref(rid: str = "unified::1") -> UnifiedActivationUnifiedReference:
    return UnifiedActivationUnifiedReference(
        reference_id=rid,
        group_id="g1",
        level=UnifiedActivationComparisonLevel.CANDIDATE,
        program="PROG",
        output_literal="R001",
        rule_family="RETURN_CODE",
    )


def _comparison(
    *, v1_ids: list[str] | None = None, unified_ids: list[str] | None = None
) -> UnifiedActivationComparison:
    return UnifiedActivationComparison(
        comparison_id="cmp::1",
        kind=UnifiedActivationComparisonKind.EXACT_EQUIVALENT,
        v1_reference_ids=v1_ids or ["v1::1"],
        unified_reference_ids=unified_ids or ["unified::1"],
        reason_code="test",
    )


def _summary(**overrides: object) -> UnifiedActivationEvaluationSummary:
    base: dict[str, object] = {
        "v1_reference_count": 1,
        "unified_reference_count": 1,
        "exact_equivalent_count": 1,
        "unified_additive_count": 0,
        "v1_only_count": 0,
        "related_count": 0,
        "conflicting_count": 0,
        "not_comparable_count": 0,
        "not_evaluated_count": 0,
        "guardrail_passed_count": 0,
        "guardrail_rejected_count": 0,
        "technical_failure_count": 0,
        "error_count": 0,
        "warning_count": 0,
        "blocking_issue_count": 0,
        "counts_by_comparison_kind": {UnifiedActivationComparisonKind.EXACT_EQUIVALENT: 1},
        "counts_by_issue_severity": {},
        "counts_by_decision": {UnifiedActivationDecision.KEEP_V1: 1},
    }
    base.update(overrides)
    return UnifiedActivationEvaluationSummary(**base)  # type: ignore[arg-type]


def _artifact(**overrides: object) -> UnifiedActivationEvaluationArtifact:
    base: dict[str, object] = {
        "run_id": RUN_ID,
        "source_package_hash": HASH,
        "config_hash": HASH,
        "mode": UnifiedActivationMode.V1_ONLY,
        "provider_policy": UnifiedActivationProviderPolicy.DETERMINISTIC_FAKE_ONLY,
        "requested_lane": UnifiedActivationLane.V1,
        "effective_lane": UnifiedActivationLane.V1,
        "fallback_lane": UnifiedActivationLane.NONE,
        "readiness_disposition": UnifiedActivationReadinessDisposition.V1_ONLY_READY,
        "activation_decision": UnifiedActivationDecision.KEEP_V1,
        "v1_references": [_v1_ref()],
        "unified_references": [_unified_ref()],
        "comparisons": [_comparison()],
        "issues": [],
        "summary": _summary(),
    }
    base.update(overrides)
    return UnifiedActivationEvaluationArtifact(**base)  # type: ignore[arg-type]


class TestHappyPath:
    def test_minimal_valid_artifact(self) -> None:
        artifact = _artifact()
        assert artifact.materialization_enabled is False


class TestMaterializationAndProviderInvariants:
    def test_materialization_true_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UnifiedActivationEvaluationArtifact.model_validate(
                {
                    **_artifact().model_dump(mode="json"),
                    "materialization_enabled": True,
                }
            )

    def test_real_provider_policy_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _artifact(provider_policy="PRODUCT_PROVIDER_EXPLICITLY_AUTHORIZED")


class TestEffectiveLaneInvariant:
    def test_effective_lane_unified_shadow_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _artifact(effective_lane=UnifiedActivationLane.UNIFIED_SHADOW)

    def test_effective_lane_v1_accepted(self) -> None:
        artifact = _artifact(effective_lane=UnifiedActivationLane.V1)
        assert artifact.effective_lane == UnifiedActivationLane.V1


class TestIdUniquenessAndOrder:
    def test_duplicate_v1_reference_id_rejected(self) -> None:
        ref = _v1_ref()
        with pytest.raises(ValidationError):
            _artifact(v1_references=[ref, ref])

    def test_unordered_comparisons_rejected(self) -> None:
        v1_a = _v1_ref("v1::a")
        v1_b = _v1_ref("v1::b")
        cmp_b = UnifiedActivationComparison(
            comparison_id="cmp::b",
            kind=UnifiedActivationComparisonKind.EXACT_EQUIVALENT,
            v1_reference_ids=["v1::b"],
            unified_reference_ids=["unified::1"],
            reason_code="test",
        )
        cmp_a = UnifiedActivationComparison(
            comparison_id="cmp::a",
            kind=UnifiedActivationComparisonKind.EXACT_EQUIVALENT,
            v1_reference_ids=["v1::a"],
            unified_reference_ids=["unified::1"],
            reason_code="test",
        )
        with pytest.raises(ValidationError):
            _artifact(
                v1_references=[v1_a, v1_b],
                comparisons=[cmp_b, cmp_a],
                summary=_summary(
                    v1_reference_count=2,
                    counts_by_comparison_kind={UnifiedActivationComparisonKind.EXACT_EQUIVALENT: 2},
                ),
            )


class TestComparisonPairUniqueness:
    def test_same_pair_twice_rejected(self) -> None:
        comparison = _comparison()
        with pytest.raises(ValidationError):
            _artifact(
                comparisons=[
                    comparison,
                    comparison.model_copy(update={"comparison_id": "cmp::2"}),
                ],
                summary=_summary(
                    counts_by_comparison_kind={UnifiedActivationComparisonKind.EXACT_EQUIVALENT: 2}
                ),
            )


class TestReferentialIntegrity:
    def test_comparison_referencing_unknown_v1_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _artifact(comparisons=[_comparison(v1_ids=["v1::unknown"])])

    def test_comparison_referencing_unknown_unified_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _artifact(comparisons=[_comparison(unified_ids=["unified::unknown"])])

    def test_issue_referencing_unknown_comparison_id_rejected(self) -> None:
        issue = UnifiedActivationIssue(
            issue_id="issue::1",
            code=UnifiedActivationIssueCode.V1_UNIFIED_EXACT_EQUIVALENT,
            severity=UnifiedActivationIssueSeverity.INFO,
            comparison_ids=["cmp::unknown"],
            message_code="MSG_V1_UNIFIED_EXACT_EQUIVALENT",
        )
        with pytest.raises(ValidationError):
            _artifact(issues=[issue])


class TestSummaryReconciliation:
    def test_v1_reference_count_mismatch_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _artifact(summary=_summary(v1_reference_count=99))

    def test_comparison_kind_counts_mismatch_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _artifact(
                summary=_summary(
                    counts_by_comparison_kind={UnifiedActivationComparisonKind.CONFLICTING: 1}
                )
            )


class TestIssueMessageCodeCatalog:
    def test_message_code_must_match_catalog(self) -> None:
        with pytest.raises(ValidationError):
            UnifiedActivationIssue(
                issue_id="issue::1",
                code=UnifiedActivationIssueCode.CANARY_NOT_SELECTED,
                severity=UnifiedActivationIssueSeverity.INFO,
                message_code="WRONG_CODE",
            )

    def test_correct_message_code_accepted(self) -> None:
        issue = UnifiedActivationIssue(
            issue_id="issue::1",
            code=UnifiedActivationIssueCode.CANARY_NOT_SELECTED,
            severity=UnifiedActivationIssueSeverity.INFO,
            message_code="MSG_CANARY_NOT_SELECTED",
        )
        assert issue.code == UnifiedActivationIssueCode.CANARY_NOT_SELECTED


class TestDiagnosticsSortedAndUnique:
    def test_unsorted_diagnostics_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _artifact(diagnostics=["b", "a"])


class TestRuleLevelRequiresDraftId:
    def test_v1_reference_rule_level_without_draft_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UnifiedActivationV1Reference(
                reference_id="v1::1",
                source_candidate_id="v1::1",
                level=UnifiedActivationComparisonLevel.RULE,
                rule_draft_id=None,
            )

    def test_unified_reference_rule_level_without_draft_record_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UnifiedActivationUnifiedReference(
                reference_id="unified::1",
                group_id="g1",
                level=UnifiedActivationComparisonLevel.RULE,
                rule_draft_record_id=None,
                rule_family="RETURN_CODE",
                program="PROG",
            )
