"""Tests de la politica de activacion (Fase 14A Parte 7,
`feat/controlled-unified-activation`)."""

from __future__ import annotations

from altamira_extractor.contracts.unified_activation_config import (
    UnifiedActivationConfig,
    UnifiedActivationMode,
    UnifiedFallbackPolicy,
)
from altamira_extractor.contracts.unified_activation_evaluation import (
    UnifiedActivationCanarySelection,
    UnifiedActivationComparison,
    UnifiedActivationComparisonKind,
    UnifiedActivationDecision,
    UnifiedActivationLane,
    UnifiedActivationReadinessDisposition,
)
from altamira_extractor.pipeline.unified_activation_policy import apply_activation_policy

from ._unified_activation_fixtures import activation_golden_path


def _selection(
    *, selected: bool, matched_denylist: bool = False, matched_allowlist: bool = False
) -> UnifiedActivationCanarySelection:
    return UnifiedActivationCanarySelection(
        selected=selected,
        bucket=None,
        reason="test",
        matched_allowlist=matched_allowlist,
        matched_denylist=matched_denylist,
    )


def _canary_config(
    *, mode: UnifiedActivationMode = UnifiedActivationMode.UNIFIED_CANARY, **overrides: object
) -> UnifiedActivationConfig:
    base: dict[str, object] = {
        "mode": mode,
        "fallback_policy": UnifiedFallbackPolicy.FALLBACK_TO_V1,
    }
    base.update(overrides)
    return UnifiedActivationConfig(**base)  # type: ignore[arg-type]


def _base_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "canary_selection": None,
        "v1_available": True,
        "unified_shadow_available": True,
        "validation_report": None,
        "downstream_artifact": None,
        "comparisons": [],
        "v1_rule_level_reference_ids": frozenset(),
    }
    base.update(overrides)
    return base


class TestV1Only:
    def test_ready_when_v1_available(self) -> None:
        config = UnifiedActivationConfig(mode=UnifiedActivationMode.V1_ONLY)
        result = apply_activation_policy(config, **_base_kwargs())  # type: ignore[arg-type]
        assert result.requested_lane == UnifiedActivationLane.V1
        assert result.effective_lane == UnifiedActivationLane.V1
        assert result.activation_decision == UnifiedActivationDecision.KEEP_V1
        assert result.readiness_disposition == UnifiedActivationReadinessDisposition.V1_ONLY_READY

    def test_not_evaluated_when_v1_missing(self) -> None:
        config = UnifiedActivationConfig(mode=UnifiedActivationMode.V1_ONLY)
        result = apply_activation_policy(config, **_base_kwargs(v1_available=False))  # type: ignore[arg-type]
        assert result.readiness_disposition == UnifiedActivationReadinessDisposition.NOT_EVALUATED
        assert result.effective_lane == UnifiedActivationLane.V1

    def test_v1_only_never_requires_unified(self) -> None:
        config = UnifiedActivationConfig(mode=UnifiedActivationMode.V1_ONLY)
        result = apply_activation_policy(
            config,
            **_base_kwargs(unified_shadow_available=False),  # type: ignore[arg-type]
        )
        assert result.readiness_disposition == UnifiedActivationReadinessDisposition.V1_ONLY_READY


class TestShadowCompare:
    def test_ready_when_both_available(self) -> None:
        config = UnifiedActivationConfig(mode=UnifiedActivationMode.SHADOW_COMPARE)
        result = apply_activation_policy(config, **_base_kwargs())  # type: ignore[arg-type]
        assert result.effective_lane == UnifiedActivationLane.V1
        assert result.requested_lane == UnifiedActivationLane.UNIFIED_SHADOW
        assert result.activation_decision == UnifiedActivationDecision.RUN_SHADOW_COMPARISON
        assert (
            result.readiness_disposition
            == UnifiedActivationReadinessDisposition.READY_FOR_SHADOW_COMPARISON
        )

    def test_unified_failure_never_changes_effective_lane(self) -> None:
        config = UnifiedActivationConfig(mode=UnifiedActivationMode.SHADOW_COMPARE)
        result = apply_activation_policy(
            config,
            **_base_kwargs(unified_shadow_available=False),  # type: ignore[arg-type]
        )
        assert result.effective_lane == UnifiedActivationLane.V1
        assert result.readiness_disposition == UnifiedActivationReadinessDisposition.NOT_EVALUATED


class TestCanary:
    def test_not_selected_keeps_v1(self) -> None:
        config = _canary_config()
        result = apply_activation_policy(
            config,
            **_base_kwargs(canary_selection=_selection(selected=False)),  # type: ignore[arg-type]
        )
        assert result.activation_decision == UnifiedActivationDecision.KEEP_V1
        assert result.effective_lane == UnifiedActivationLane.V1
        assert result.readiness_disposition == UnifiedActivationReadinessDisposition.NOT_EVALUATED
        assert any(i.code.value == "CANARY_NOT_SELECTED" for i in result.issues)

    def test_denylisted_emits_denylisted_issue(self) -> None:
        config = _canary_config()
        result = apply_activation_policy(
            config,
            **_base_kwargs(  # type: ignore[arg-type]
                canary_selection=_selection(selected=False, matched_denylist=True)
            ),
        )
        assert any(i.code.value == "CANARY_DENYLISTED" for i in result.issues)
        assert result.effective_lane == UnifiedActivationLane.V1

    def test_missing_sources_produces_not_evaluated(self) -> None:
        config = _canary_config()
        result = apply_activation_policy(
            config,
            **_base_kwargs(canary_selection=_selection(selected=True)),  # type: ignore[arg-type]
        )
        assert result.readiness_disposition == UnifiedActivationReadinessDisposition.NOT_EVALUATED
        assert result.activation_decision == UnifiedActivationDecision.KEEP_V1

    def test_validation_not_qualified_blocks(self) -> None:
        gp = activation_golden_path()
        config = _canary_config()
        bad_report = gp.validation_report.model_copy(update={"disposition": "REVIEW_REQUIRED"})
        result = apply_activation_policy(
            config,
            **_base_kwargs(  # type: ignore[arg-type]
                canary_selection=_selection(selected=True),
                validation_report=bad_report,
                downstream_artifact=gp.downstream_artifact,
            ),
        )
        assert result.readiness_disposition == UnifiedActivationReadinessDisposition.BLOCKED
        assert any(i.code.value == "VALIDATION_NOT_QUALIFIED" for i in result.issues)

    def test_downstream_not_completed_blocks(self) -> None:
        gp = activation_golden_path()
        config = _canary_config()
        bad_downstream = gp.downstream_artifact.model_copy(update={"disposition": "NOT_EXECUTED"})
        result = apply_activation_policy(
            config,
            **_base_kwargs(  # type: ignore[arg-type]
                canary_selection=_selection(selected=True),
                validation_report=gp.validation_report,
                downstream_artifact=bad_downstream,
            ),
        )
        assert result.readiness_disposition == UnifiedActivationReadinessDisposition.BLOCKED
        assert any(i.code.value == "DOWNSTREAM_NOT_COMPLETED" for i in result.issues)

    def test_completed_with_rejections_blocked_by_default(self) -> None:
        gp = activation_golden_path()
        config = _canary_config()
        rejected_downstream = gp.downstream_artifact.model_copy(
            update={"disposition": "COMPLETED_WITH_REJECTIONS"}
        )
        result = apply_activation_policy(
            config,
            **_base_kwargs(  # type: ignore[arg-type]
                canary_selection=_selection(selected=True),
                validation_report=gp.validation_report,
                downstream_artifact=rejected_downstream,
            ),
        )
        assert result.readiness_disposition == UnifiedActivationReadinessDisposition.BLOCKED

    def test_completed_with_rejections_allowed_when_configured(self) -> None:
        gp = activation_golden_path()
        config = _canary_config(allow_completed_with_rejections=True)
        rejected_downstream = gp.downstream_artifact.model_copy(
            update={"disposition": "COMPLETED_WITH_REJECTIONS"}
        )
        result = apply_activation_policy(
            config,
            **_base_kwargs(  # type: ignore[arg-type]
                canary_selection=_selection(selected=True),
                validation_report=gp.validation_report,
                downstream_artifact=rejected_downstream,
            ),
        )
        assert (
            result.readiness_disposition
            == UnifiedActivationReadinessDisposition.READY_FOR_UNIFIED_CANARY
        )

    def test_technical_failure_plans_fallback(self) -> None:
        gp = activation_golden_path()
        config = _canary_config()
        failed_downstream = gp.downstream_artifact.model_copy(
            update={
                "disposition": "BLOCKED",
                "summary": gp.downstream_artifact.summary.model_copy(
                    update={"technical_failure_count": 1}
                ),
            }
        )
        result = apply_activation_policy(
            config,
            **_base_kwargs(  # type: ignore[arg-type]
                canary_selection=_selection(selected=True),
                validation_report=gp.validation_report,
                downstream_artifact=failed_downstream,
            ),
        )
        assert result.readiness_disposition == UnifiedActivationReadinessDisposition.BLOCKED
        assert result.activation_decision == UnifiedActivationDecision.FALLBACK_TO_V1_PLANNED
        assert result.fallback_lane == UnifiedActivationLane.V1

    def test_ready_for_canary_when_all_gates_pass(self) -> None:
        gp = activation_golden_path()
        config = _canary_config()
        result = apply_activation_policy(
            config,
            **_base_kwargs(  # type: ignore[arg-type]
                canary_selection=_selection(selected=True),
                validation_report=gp.validation_report,
                downstream_artifact=gp.downstream_artifact,
            ),
        )
        assert (
            result.readiness_disposition
            == UnifiedActivationReadinessDisposition.READY_FOR_UNIFIED_CANARY
        )
        assert result.activation_decision == UnifiedActivationDecision.SELECT_UNIFIED_CANARY_DRY_RUN
        assert result.effective_lane == UnifiedActivationLane.V1
        assert result.fallback_lane == UnifiedActivationLane.V1

    def test_conflicting_comparison_never_blocks_canary(self) -> None:
        gp = activation_golden_path()
        config = _canary_config()
        conflicting = UnifiedActivationComparison(
            comparison_id="cmp::1",
            kind=UnifiedActivationComparisonKind.CONFLICTING,
            v1_reference_ids=["activation::v1::1"],
            unified_reference_ids=["activation::unified::g1"],
            reason_code="test",
        )
        result = apply_activation_policy(
            config,
            **_base_kwargs(  # type: ignore[arg-type]
                canary_selection=_selection(selected=True),
                validation_report=gp.validation_report,
                downstream_artifact=gp.downstream_artifact,
                comparisons=[conflicting],
            ),
        )
        assert (
            result.readiness_disposition
            == UnifiedActivationReadinessDisposition.READY_FOR_UNIFIED_CANARY
        )


class TestPrimary:
    def test_ready_for_primary_trial_when_all_gates_pass(self) -> None:
        gp = activation_golden_path()
        config = _canary_config(mode=UnifiedActivationMode.UNIFIED_PRIMARY_WITH_V1_FALLBACK)
        result = apply_activation_policy(
            config,
            **_base_kwargs(  # type: ignore[arg-type]
                canary_selection=_selection(selected=True),
                validation_report=gp.validation_report,
                downstream_artifact=gp.downstream_artifact,
            ),
        )
        assert (
            result.readiness_disposition
            == UnifiedActivationReadinessDisposition.READY_FOR_PRIMARY_TRIAL
        )
        assert (
            result.activation_decision == UnifiedActivationDecision.SELECT_UNIFIED_PRIMARY_DRY_RUN
        )
        assert result.effective_lane == UnifiedActivationLane.V1

    def test_conflicting_comparison_blocks_primary(self) -> None:
        gp = activation_golden_path()
        config = _canary_config(mode=UnifiedActivationMode.UNIFIED_PRIMARY_WITH_V1_FALLBACK)
        conflicting = UnifiedActivationComparison(
            comparison_id="cmp::1",
            kind=UnifiedActivationComparisonKind.CONFLICTING,
            v1_reference_ids=["activation::v1::1"],
            unified_reference_ids=["activation::unified::g1"],
            reason_code="test",
        )
        result = apply_activation_policy(
            config,
            **_base_kwargs(  # type: ignore[arg-type]
                canary_selection=_selection(selected=True),
                validation_report=gp.validation_report,
                downstream_artifact=gp.downstream_artifact,
                comparisons=[conflicting],
            ),
        )
        assert result.readiness_disposition == UnifiedActivationReadinessDisposition.BLOCKED
        assert any(i.code.value == "V1_UNIFIED_CONFLICT" for i in result.issues)

    def test_v1_only_at_rule_level_blocks_primary(self) -> None:
        gp = activation_golden_path()
        config = _canary_config(mode=UnifiedActivationMode.UNIFIED_PRIMARY_WITH_V1_FALLBACK)
        v1_only = UnifiedActivationComparison(
            comparison_id="cmp::2",
            kind=UnifiedActivationComparisonKind.V1_ONLY,
            v1_reference_ids=["activation::v1::1"],
            unified_reference_ids=[],
            reason_code="test",
        )
        result = apply_activation_policy(
            config,
            **_base_kwargs(  # type: ignore[arg-type]
                canary_selection=_selection(selected=True),
                validation_report=gp.validation_report,
                downstream_artifact=gp.downstream_artifact,
                comparisons=[v1_only],
                v1_rule_level_reference_ids=frozenset({"activation::v1::1"}),
            ),
        )
        assert result.readiness_disposition == UnifiedActivationReadinessDisposition.BLOCKED
        assert any(i.code.value == "V1_RESULT_NOT_REPRESENTED" for i in result.issues)

    def test_v1_only_at_candidate_level_never_blocks_primary(self) -> None:
        gp = activation_golden_path()
        config = _canary_config(mode=UnifiedActivationMode.UNIFIED_PRIMARY_WITH_V1_FALLBACK)
        v1_only = UnifiedActivationComparison(
            comparison_id="cmp::2",
            kind=UnifiedActivationComparisonKind.V1_ONLY,
            v1_reference_ids=["activation::v1::1"],
            unified_reference_ids=[],
            reason_code="test",
        )
        result = apply_activation_policy(
            config,
            **_base_kwargs(  # type: ignore[arg-type]
                canary_selection=_selection(selected=True),
                validation_report=gp.validation_report,
                downstream_artifact=gp.downstream_artifact,
                comparisons=[v1_only],
                v1_rule_level_reference_ids=frozenset(),
            ),
        )
        assert (
            result.readiness_disposition
            == UnifiedActivationReadinessDisposition.READY_FOR_PRIMARY_TRIAL
        )


class TestEffectiveLaneNeverUnified:
    def test_all_modes_produce_v1_effective_lane(self) -> None:
        gp = activation_golden_path()
        for mode in UnifiedActivationMode:
            if mode == UnifiedActivationMode.V1_ONLY:
                config = UnifiedActivationConfig(mode=mode)
                kwargs = _base_kwargs()
            elif mode == UnifiedActivationMode.SHADOW_COMPARE:
                config = UnifiedActivationConfig(mode=mode)
                kwargs = _base_kwargs()
            else:
                config = _canary_config(mode=mode)
                kwargs = _base_kwargs(
                    canary_selection=_selection(selected=True),
                    validation_report=gp.validation_report,
                    downstream_artifact=gp.downstream_artifact,
                )
            result = apply_activation_policy(config, **kwargs)  # type: ignore[arg-type]
            assert result.effective_lane == UnifiedActivationLane.V1, mode
