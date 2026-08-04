"""Tests del constructor de generaciones UNIFIED (Fase 14B Parte 7/15
items 20-30, `feat/controlled-unified-materialization`)."""

from __future__ import annotations

import pytest

from altamira_extractor.contracts.unified_activation_evaluation import (
    UnifiedActivationComparisonLevel,
    UnifiedActivationReadinessDisposition,
)
from altamira_extractor.contracts.unified_activation_materialization import (
    MaterializedGenerationManifest,
)
from altamira_extractor.contracts.unified_materialization_authorization import (
    UnifiedMaterializationAction,
    UnifiedMaterializationAuthorization,
    UnifiedMaterializationReasonCode,
)
from altamira_extractor.contracts.unified_shadow_downstream import (
    UnifiedShadowDownstreamExecutionStatus,
    UnifiedShadowGuardrailStatus,
)
from altamira_extractor.pipeline.errors import UnifiedMaterializationError
from altamira_extractor.pipeline.unified_activation_generation_builder import (
    UnifiedGenerationFiles,
    build_unified_generation,
)

from ._unified_materialization_fixtures import MaterializationFixture, build_materialization_fixture

HASH = "e" * 64
FALLBACK_ID = "generation-fallback"


def _authorization(
    fx: MaterializationFixture, **overrides: object
) -> UnifiedMaterializationAuthorization:
    base: dict[str, object] = {
        "run_id": fx.run_id,
        "activation_evaluation_hash": HASH,
        "expected_readiness_disposition": (
            UnifiedActivationReadinessDisposition.READY_FOR_UNIFIED_CANARY
        ),
        "action": UnifiedMaterializationAction.ACTIVATE_UNIFIED_CANARY,
        "reason_code": UnifiedMaterializationReasonCode.CANARY_APPROVED,
        "review_reference": "reviewer",
        "approved_group_ids": fx.approved_group_ids,
        "fallback_authorized": True,
    }
    base.update(overrides)
    return UnifiedMaterializationAuthorization(**base)  # type: ignore[arg-type]


def _build(
    fx: MaterializationFixture, authorization: UnifiedMaterializationAuthorization | None = None
) -> tuple[MaterializedGenerationManifest, UnifiedGenerationFiles]:
    auth = authorization or _authorization(fx)
    return build_unified_generation(
        evaluation=fx.evaluation,
        downstream=fx.gp.downstream_artifact,
        authorization=auth,
        run_id=fx.run_id,
        source_package_hash=fx.source_package_hash,
        activation_evaluation_hash=HASH,
        authorization_hash="f" * 64,
        fallback_generation_id=FALLBACK_ID,
    )


def test_unified_manifest_built_from_real_golden_path() -> None:
    fx = build_materialization_fixture()
    manifest, files = _build(fx)
    assert manifest.lane.value == "UNIFIED"
    assert manifest.kind.value == "UNIFIED_CANARY"
    assert len(files.candidates.candidates) == 1
    assert manifest.approved_group_ids == fx.approved_group_ids


# 20. unified solo grupos aprobados.
def test_only_approved_groups_included() -> None:
    fx = build_materialization_fixture()
    manifest, files = _build(fx)
    assert {c.group_id for c in files.candidates.candidates} == set(fx.approved_group_ids)
    assert {c.group_id for c in files.context_packages.context_packages} == set(
        fx.approved_group_ids
    )
    assert {c.group_id for c in files.rule_drafts.rule_drafts} == set(fx.approved_group_ids)
    assert {c.group_id for c in files.guardrails.guardrails} == set(fx.approved_group_ids)


def test_approved_group_not_in_evaluation_raises() -> None:
    fx = build_materialization_fixture()
    auth = _authorization(fx, approved_group_ids=["group::does-not-exist"])
    with pytest.raises(UnifiedMaterializationError):
        _build(fx, auth)


def test_empty_approved_groups_raises() -> None:
    fx = build_materialization_fixture()
    with pytest.raises(UnifiedMaterializationError):
        build_unified_generation(
            evaluation=fx.evaluation,
            downstream=fx.gp.downstream_artifact,
            authorization=_authorization(fx, approved_group_ids=[]),
            run_id=fx.run_id,
            source_package_hash=fx.source_package_hash,
            activation_evaluation_hash=HASH,
            authorization_hash="f" * 64,
            fallback_generation_id=FALLBACK_ID,
        )


# 21-25. preservacion de member_ids/source_candidate_ids/review_decision_ids/evidence/provenance.
def test_group_metadata_preserved() -> None:
    fx = build_materialization_fixture()
    manifest, files = _build(fx)
    candidate = files.candidates.candidates[0]
    context_record = files.context_packages.context_packages[0]

    assert candidate.member_ids == context_record.member_ids
    assert candidate.source_candidate_ids == context_record.source_candidate_ids
    assert context_record.review_decision_ids != []
    assert candidate.evidence_ids == context_record.evidence_ids or set(
        candidate.evidence_ids
    ) <= set(context_record.evidence_ids)
    assert candidate.provenance_references == context_record.provenance_references


# 26. guardrail rejected excluido.
def test_guardrail_rejected_reference_excluded() -> None:
    fx = build_materialization_fixture()
    rejected_evaluation = fx.evaluation.model_copy(
        update={
            "unified_references": [
                r.model_copy(update={"guardrail_status": UnifiedShadowGuardrailStatus.REJECTED})
                if r.group_id in fx.approved_group_ids
                else r
                for r in fx.evaluation.unified_references
            ]
        }
    )
    with pytest.raises(UnifiedMaterializationError):
        build_unified_generation(
            evaluation=rejected_evaluation,
            downstream=fx.gp.downstream_artifact,
            authorization=_authorization(fx),
            run_id=fx.run_id,
            source_package_hash=fx.source_package_hash,
            activation_evaluation_hash=HASH,
            authorization_hash="f" * 64,
            fallback_generation_id=FALLBACK_ID,
        )


# 27. technical failure excluido.
def test_technical_failure_group_excluded() -> None:
    fx = build_materialization_fixture()
    failing_downstream = fx.gp.downstream_artifact.model_copy(
        update={
            "group_results": [
                gr.model_copy(
                    update={
                        "execution_status": (
                            UnifiedShadowDownstreamExecutionStatus.TECHNICAL_FAILURE
                        )
                    }
                )
                if gr.group_id in fx.approved_group_ids
                else gr
                for gr in fx.gp.downstream_artifact.group_results
            ]
        }
    )
    with pytest.raises(UnifiedMaterializationError):
        build_unified_generation(
            evaluation=fx.evaluation,
            downstream=failing_downstream,
            authorization=_authorization(fx),
            run_id=fx.run_id,
            source_package_hash=fx.source_package_hash,
            activation_evaluation_hash=HASH,
            authorization_hash="f" * 64,
            fallback_generation_id=FALLBACK_ID,
        )


# 28. sin reglas Markdown.
def test_no_markdown_rule_content() -> None:
    fx = build_materialization_fixture()
    manifest, files = _build(fx)
    for payload in (
        files.candidates.to_stable_json(),
        files.context_packages.to_stable_json(),
        files.rule_drafts.to_stable_json(),
        files.guardrails.to_stable_json(),
    ):
        assert ".md" not in payload
        assert "rules-manifest" not in payload


# 29. sin timestamps.
def test_no_timestamps_in_manifest_or_files() -> None:
    fx = build_materialization_fixture()
    manifest, files = _build(fx)
    for payload in (
        manifest.to_stable_json(),
        files.candidates.to_stable_json(),
        files.context_packages.to_stable_json(),
        files.rule_drafts.to_stable_json(),
        files.guardrails.to_stable_json(),
    ):
        assert '"evaluated_at"' not in payload


# 30. inputs no modificados.
def test_inputs_never_mutated() -> None:
    fx = build_materialization_fixture()
    evaluation_snapshot = fx.evaluation.model_copy(deep=True)
    downstream_snapshot = fx.gp.downstream_artifact.model_copy(deep=True)
    _build(fx)
    assert fx.evaluation == evaluation_snapshot
    assert fx.gp.downstream_artifact == downstream_snapshot


def test_only_rule_level_references_considered() -> None:
    fx = build_materialization_fixture()
    for candidate in _build(fx)[1].candidates.candidates:
        assert candidate.level == UnifiedActivationComparisonLevel.RULE
