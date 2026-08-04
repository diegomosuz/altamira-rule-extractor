"""Tests del evaluador puro (Fase 14A Parte 8,
`feat/controlled-unified-activation`)."""

from __future__ import annotations

import pytest

from altamira_extractor.contracts.unified_activation_config import (
    UnifiedActivationConfig,
    UnifiedActivationMode,
    UnifiedCanarySelectionStrategy,
    UnifiedFallbackPolicy,
)
from altamira_extractor.contracts.unified_activation_evaluation import (
    UnifiedActivationEvaluationArtifact,
    UnifiedActivationLane,
)
from altamira_extractor.pipeline.unified_activation_evaluator import (
    UnifiedActivationEvaluatorError,
    evaluate_unified_activation,
)

from ._unified_activation_fixtures import ActivationGoldenPath, activation_golden_path


def _evaluate(
    config: UnifiedActivationConfig, gp: ActivationGoldenPath, **overrides: object
) -> UnifiedActivationEvaluationArtifact:
    base: dict[str, object] = {
        "run_id": gp.unified_shadow.run_id,
        "source_package_hash": gp.unified_shadow.source_package_hash,
        "config_hash": "c" * 64,
        "candidate_v1_artifact": gp.v1_artifact,
        "candidate_v1_artifact_hash": gp.unified_shadow.source_package_hash,
        "unified_shadow": gp.unified_shadow,
        "unified_candidates_shadow_hash": gp.unified_shadow.source_package_hash,
        "validation_report": gp.validation_report,
        "validation_report_hash": gp.unified_shadow.source_package_hash,
        "downstream_artifact": gp.downstream_artifact,
        "downstream_artifact_hash": gp.unified_shadow.source_package_hash,
    }
    base.update(overrides)
    return evaluate_unified_activation(config, **base)  # type: ignore[arg-type]


class TestServiceModesEndToEnd:
    def test_v1_only(self) -> None:
        gp = activation_golden_path()
        config = UnifiedActivationConfig(mode=UnifiedActivationMode.V1_ONLY)
        artifact = _evaluate(config, gp)
        assert artifact.effective_lane == UnifiedActivationLane.V1
        assert artifact.readiness_disposition.value == "V1_ONLY_READY"

    def test_shadow_compare(self) -> None:
        gp = activation_golden_path()
        config = UnifiedActivationConfig(mode=UnifiedActivationMode.SHADOW_COMPARE)
        artifact = _evaluate(config, gp)
        assert artifact.effective_lane == UnifiedActivationLane.V1
        assert artifact.readiness_disposition.value == "READY_FOR_SHADOW_COMPARISON"
        assert len(artifact.comparisons) >= 1

    def test_canary(self) -> None:
        gp = activation_golden_path()
        config = UnifiedActivationConfig(
            mode=UnifiedActivationMode.UNIFIED_CANARY,
            canary_strategy=UnifiedCanarySelectionStrategy.EXPLICIT_ALLOWLIST,
            package_hash_allowlist=[gp.unified_shadow.source_package_hash],
            fallback_policy=UnifiedFallbackPolicy.FALLBACK_TO_V1,
        )
        artifact = _evaluate(config, gp)
        assert artifact.canary_selection is not None
        assert artifact.canary_selection.selected is True
        assert artifact.readiness_disposition.value == "READY_FOR_UNIFIED_CANARY"
        assert artifact.materialization_enabled is False

    def test_primary_dry_run(self) -> None:
        gp = activation_golden_path()
        config = UnifiedActivationConfig(
            mode=UnifiedActivationMode.UNIFIED_PRIMARY_WITH_V1_FALLBACK,
            canary_strategy=UnifiedCanarySelectionStrategy.EXPLICIT_ALLOWLIST,
            package_hash_allowlist=[gp.unified_shadow.source_package_hash],
            fallback_policy=UnifiedFallbackPolicy.FALLBACK_TO_V1,
        )
        artifact = _evaluate(config, gp)
        assert artifact.readiness_disposition.value == "READY_FOR_PRIMARY_TRIAL"
        assert artifact.effective_lane == UnifiedActivationLane.V1


class TestHashAndRunIdConsistency:
    def test_run_id_mismatch_raises(self) -> None:
        gp = activation_golden_path()
        config = UnifiedActivationConfig(mode=UnifiedActivationMode.V1_ONLY)
        with pytest.raises(UnifiedActivationEvaluatorError):
            _evaluate(config, gp, run_id="not-the-real-run-id")

    def test_source_package_hash_mismatch_raises(self) -> None:
        gp = activation_golden_path()
        config = UnifiedActivationConfig(mode=UnifiedActivationMode.V1_ONLY)
        with pytest.raises(UnifiedActivationEvaluatorError):
            _evaluate(config, gp, source_package_hash="f" * 64)


class TestDeterminism:
    def test_two_evaluations_produce_byte_identical_output(self) -> None:
        gp = activation_golden_path()
        config = UnifiedActivationConfig(mode=UnifiedActivationMode.V1_ONLY)
        artifact_1 = _evaluate(config, gp)
        artifact_2 = _evaluate(config, gp)
        assert artifact_1.to_stable_json() == artifact_2.to_stable_json()

    def test_evaluator_does_not_mutate_inputs(self) -> None:
        gp = activation_golden_path()
        config = UnifiedActivationConfig(mode=UnifiedActivationMode.SHADOW_COMPARE)
        v1_snapshot = gp.v1_artifact.model_copy(deep=True)
        unified_snapshot = gp.unified_shadow.model_copy(deep=True)
        _evaluate(config, gp)
        assert gp.v1_artifact == v1_snapshot
        assert gp.unified_shadow == unified_snapshot


class TestMissingSourcesHandledSoftly:
    def test_missing_v1_artifact_never_raises(self) -> None:
        gp = activation_golden_path()
        config = UnifiedActivationConfig(mode=UnifiedActivationMode.V1_ONLY)
        artifact = _evaluate(
            config, gp, candidate_v1_artifact=None, candidate_v1_artifact_hash=None
        )
        assert artifact.readiness_disposition.value == "NOT_EVALUATED"

    def test_missing_unified_sources_never_raises_for_v1_only(self) -> None:
        gp = activation_golden_path()
        config = UnifiedActivationConfig(mode=UnifiedActivationMode.V1_ONLY)
        artifact = _evaluate(
            config,
            gp,
            unified_shadow=None,
            unified_candidates_shadow_hash=None,
            validation_report=None,
            validation_report_hash=None,
            downstream_artifact=None,
            downstream_artifact_hash=None,
        )
        assert artifact.readiness_disposition.value == "V1_ONLY_READY"
