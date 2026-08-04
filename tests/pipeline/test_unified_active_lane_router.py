"""Tests del router de lane activo (Fase 14B Parte 10/15 items 59-68,
`feat/controlled-unified-materialization`)."""

from __future__ import annotations

from pathlib import Path

import pytest

from altamira_extractor.contracts.unified_activation_evaluation import (
    UnifiedActivationReadinessDisposition,
)
from altamira_extractor.contracts.unified_materialization_authorization import (
    UnifiedMaterializationAction,
    UnifiedMaterializationAuthorization,
    UnifiedMaterializationReasonCode,
)
from altamira_extractor.pipeline.errors import UnifiedMaterializationError
from altamira_extractor.pipeline.unified_activation_generation_builder import (
    build_unified_generation,
)
from altamira_extractor.pipeline.unified_activation_store import UnifiedActivationStore
from altamira_extractor.pipeline.unified_activation_transition import (
    activate_unified_canary,
    initialize_v1,
)
from altamira_extractor.pipeline.unified_active_lane_router import resolve_active_artifact
from altamira_extractor.pipeline.v1_activation_generation_builder import (
    build_v1_generation_manifest,
)

from ._unified_materialization_fixtures import build_materialization_fixture, write_run_dir

EVAL_HASH = "e" * 64
AUTH_HASH_V1 = "c" * 64
AUTH_HASH_UNIFIED = "d" * 64


def _v1_only_store(tmp_path: Path) -> tuple[UnifiedActivationStore, str, str]:
    fx = build_materialization_fixture()
    run_dir = write_run_dir(tmp_path, fx)
    store = UnifiedActivationStore(run_dir)
    v1_manifest = build_v1_generation_manifest(
        run_dir,
        run_id=fx.run_id,
        source_package_hash=fx.source_package_hash,
        activation_evaluation_hash=EVAL_HASH,
        authorization_hash=AUTH_HASH_V1,
    )
    initialize_v1(
        store,
        run_id=fx.run_id,
        v1_manifest=v1_manifest,
        activation_evaluation_hash=EVAL_HASH,
        authorization_hash=AUTH_HASH_V1,
        reason_code=UnifiedMaterializationReasonCode.KEEP_BASELINE,
    )
    return store, fx.run_id, v1_manifest.generation_id


def _unified_active_store(tmp_path: Path) -> tuple[UnifiedActivationStore, str, str]:
    fx = build_materialization_fixture()
    run_dir = write_run_dir(tmp_path, fx)
    store = UnifiedActivationStore(run_dir)
    v1_manifest = build_v1_generation_manifest(
        run_dir,
        run_id=fx.run_id,
        source_package_hash=fx.source_package_hash,
        activation_evaluation_hash=EVAL_HASH,
        authorization_hash=AUTH_HASH_V1,
    )
    initialize_v1(
        store,
        run_id=fx.run_id,
        v1_manifest=v1_manifest,
        activation_evaluation_hash=EVAL_HASH,
        authorization_hash=AUTH_HASH_V1,
        reason_code=UnifiedMaterializationReasonCode.KEEP_BASELINE,
    )
    auth = UnifiedMaterializationAuthorization(
        run_id=fx.run_id,
        activation_evaluation_hash=EVAL_HASH,
        expected_readiness_disposition=UnifiedActivationReadinessDisposition.READY_FOR_UNIFIED_CANARY,
        action=UnifiedMaterializationAction.ACTIVATE_UNIFIED_CANARY,
        reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
        review_reference="router-test",
        approved_group_ids=fx.approved_group_ids,
        fallback_authorized=True,
    )
    unified_manifest, unified_files = build_unified_generation(
        evaluation=fx.evaluation,
        downstream=fx.gp.downstream_artifact,
        authorization=auth,
        run_id=fx.run_id,
        source_package_hash=fx.source_package_hash,
        activation_evaluation_hash=EVAL_HASH,
        authorization_hash=AUTH_HASH_UNIFIED,
        fallback_generation_id=v1_manifest.generation_id,
    )
    activate_unified_canary(
        store,
        run_id=fx.run_id,
        unified_manifest=unified_manifest,
        unified_files=unified_files,
        activation_evaluation_hash=EVAL_HASH,
        authorization_hash=AUTH_HASH_UNIFIED,
        reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
    )
    return store, fx.run_id, unified_manifest.generation_id


# 59. resolve candidates V1.
def test_resolve_candidates_v1(tmp_path: Path) -> None:
    store, run_id, generation_id = _v1_only_store(tmp_path)
    resolution = resolve_active_artifact(store, run_id=run_id, logical_name="candidates")
    assert resolution.status.value == "RESOLVED"
    assert resolution.resolved_lane.value == "V1"
    assert resolution.relative_path == "artifacts/06-candidates.json"


# 60. resolve unified candidates.
def test_resolve_unified_candidates(tmp_path: Path) -> None:
    store, run_id, generation_id = _unified_active_store(tmp_path)
    resolution = resolve_active_artifact(store, run_id=run_id, logical_name="candidates")
    assert resolution.status.value == "RESOLVED"
    assert resolution.resolved_lane.value == "UNIFIED"
    assert resolution.generation_id == generation_id


# 61. resolve context packages.
def test_resolve_context_packages(tmp_path: Path) -> None:
    store, run_id, generation_id = _unified_active_store(tmp_path)
    resolution = resolve_active_artifact(store, run_id=run_id, logical_name="context-packages")
    assert resolution.status.value == "RESOLVED"
    assert resolution.relative_path is not None
    assert resolution.relative_path.endswith("context-packages.json")


# 62. resolve rule drafts.
def test_resolve_rule_drafts(tmp_path: Path) -> None:
    store, run_id, generation_id = _unified_active_store(tmp_path)
    resolution = resolve_active_artifact(store, run_id=run_id, logical_name="rule-drafts")
    assert resolution.status.value == "RESOLVED"
    assert resolution.relative_path is not None
    assert resolution.relative_path.endswith("rule-drafts.json")


# 63. resolve guardrails.
def test_resolve_guardrails(tmp_path: Path) -> None:
    store, run_id, generation_id = _unified_active_store(tmp_path)
    resolution = resolve_active_artifact(store, run_id=run_id, logical_name="guardrails")
    assert resolution.status.value == "RESOLVED"
    assert resolution.relative_path is not None
    assert resolution.relative_path.endswith("guardrails.json")


def test_resolve_guardrails_absent_in_v1_only(tmp_path: Path) -> None:
    store, run_id, generation_id = _v1_only_store(tmp_path)
    resolution = resolve_active_artifact(store, run_id=run_id, logical_name="guardrails")
    assert resolution.status.value == "NOT_AVAILABLE_IN_LANE"
    assert resolution.relative_path is None


# 64. logical name desconocido.
def test_unknown_logical_name_raises(tmp_path: Path) -> None:
    store, run_id, generation_id = _v1_only_store(tmp_path)
    with pytest.raises(UnifiedMaterializationError):
        resolve_active_artifact(store, run_id=run_id, logical_name="not-a-real-artifact")


def test_no_active_lane_raises(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = write_run_dir(tmp_path, fx)
    store = UnifiedActivationStore(run_dir)
    with pytest.raises(UnifiedMaterializationError):
        resolve_active_artifact(store, run_id=fx.run_id, logical_name="candidates")


# 65. generation inexistente.
def test_generation_missing_returns_blocked(tmp_path: Path) -> None:
    import shutil

    store, run_id, generation_id = _v1_only_store(tmp_path)
    shutil.rmtree(store.generation_dir(generation_id))
    resolution = resolve_active_artifact(store, run_id=run_id, logical_name="candidates")
    assert resolution.status.value == "BLOCKED"


# 66. manifest invalido.
def test_corrupt_manifest_returns_blocked(tmp_path: Path) -> None:
    store, run_id, generation_id = _v1_only_store(tmp_path)
    manifest_path = store.generation_dir(generation_id) / "manifest.json"
    manifest_path.write_text("{not valid", encoding="utf-8")
    resolution = resolve_active_artifact(store, run_id=run_id, logical_name="candidates")
    assert resolution.status.value == "BLOCKED"


# 67. file hash invalido.
def test_file_hash_mismatch_returns_blocked(tmp_path: Path) -> None:
    store, run_id, generation_id = _v1_only_store(tmp_path)
    (store.run_dir / "artifacts" / "06-candidates.json").write_bytes(b'{"tampered":true}')
    resolution = resolve_active_artifact(store, run_id=run_id, logical_name="candidates")
    assert resolution.status.value == "BLOCKED"
    # Aun BLOCKED, se reporta la ruta/hash ESPERADOS (auditable).
    assert resolution.relative_path == "artifacts/06-candidates.json"
    assert resolution.sha256 is not None


# 68. ruta relativa unicamente.
def test_resolution_never_returns_absolute_path(tmp_path: Path) -> None:
    store, run_id, generation_id = _v1_only_store(tmp_path)
    resolution = resolve_active_artifact(store, run_id=run_id, logical_name="candidates")
    assert resolution.relative_path is not None
    assert not Path(resolution.relative_path).is_absolute()
    assert str(tmp_path) not in resolution.relative_path
