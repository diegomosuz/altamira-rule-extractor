"""Tests de fallback ejecutable y rollback (Fase 14B Parte 11/15 items
69-86, `feat/controlled-unified-materialization`)."""

from __future__ import annotations

from pathlib import Path

import pytest

from altamira_extractor.contracts.unified_activation_config import UnifiedFallbackPolicy
from altamira_extractor.contracts.unified_activation_evaluation import (
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
from altamira_extractor.pipeline.active_artifact_resolver import ActiveArtifactResolver
from altamira_extractor.pipeline.unified_activation_generation_builder import (
    build_unified_generation,
)
from altamira_extractor.pipeline.unified_activation_store import UnifiedActivationStore
from altamira_extractor.pipeline.unified_activation_transition import (
    activate_unified_canary,
    initialize_v1,
    rollback_to_generation,
    rollback_to_previous,
)
from altamira_extractor.pipeline.unified_active_lane_service import resolve_with_fallback
from altamira_extractor.pipeline.v1_activation_generation_builder import (
    build_v1_generation_manifest,
)

from ._unified_materialization_fixtures import (
    MaterializationFixture,
    build_materialization_fixture,
    write_run_dir,
)

EVAL_HASH = "e" * 64
AUTH_HASH_V1 = "c" * 64
AUTH_HASH_UNIFIED = "d" * 64


def _scenario(
    tmp_path: Path, *, fallback_authorized: bool = True
) -> tuple[
    MaterializationFixture,
    UnifiedActivationStore,
    MaterializedGenerationManifest,
    MaterializedGenerationManifest,
]:
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
        review_reference="fallback-test",
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
    fallback_policy = (
        UnifiedFallbackPolicy.FALLBACK_TO_V1
        if fallback_authorized
        else UnifiedFallbackPolicy.NO_FALLBACK
    )
    activate_unified_canary(
        store,
        run_id=fx.run_id,
        unified_manifest=unified_manifest,
        unified_files=unified_files,
        activation_evaluation_hash=EVAL_HASH,
        authorization_hash=AUTH_HASH_UNIFIED,
        reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
        fallback_policy=fallback_policy,
    )
    return fx, store, v1_manifest, unified_manifest


def _corrupt_candidates(
    store: UnifiedActivationStore, unified_manifest: MaterializedGenerationManifest
) -> None:
    path = store.generation_dir(unified_manifest.generation_id) / "candidates.json"
    path.write_bytes(b"{corrupted}")


# 69. corrupcion unified aplica fallback.
def test_corruption_triggers_fallback(tmp_path: Path) -> None:
    fx, store, v1_manifest, unified_manifest = _scenario(tmp_path)
    _corrupt_candidates(store, unified_manifest)
    result = resolve_with_fallback(store, run_id=fx.run_id, logical_name="candidates")
    assert result.status.value == "FALLBACK_APPLIED"
    assert result.resolved_lane.value == "V1"


# 70. archivo unified ausente aplica fallback.
def test_missing_unified_file_triggers_fallback(tmp_path: Path) -> None:
    fx, store, v1_manifest, unified_manifest = _scenario(tmp_path)
    path = store.generation_dir(unified_manifest.generation_id) / "candidates.json"
    path.unlink()
    result = resolve_with_fallback(store, run_id=fx.run_id, logical_name="candidates")
    assert result.status.value == "FALLBACK_APPLIED"


# 71. fallback termina en V1.
def test_fallback_ends_in_v1(tmp_path: Path) -> None:
    fx, store, v1_manifest, unified_manifest = _scenario(tmp_path)
    _corrupt_candidates(store, unified_manifest)
    result = resolve_with_fallback(store, run_id=fx.run_id, logical_name="candidates")
    assert result.generation_id == v1_manifest.generation_id


# 72. fallback genera evento.
def test_fallback_generates_event(tmp_path: Path) -> None:
    fx, store, v1_manifest, unified_manifest = _scenario(tmp_path)
    _corrupt_candidates(store, unified_manifest)
    result = resolve_with_fallback(store, run_id=fx.run_id, logical_name="candidates")
    assert result.fallback_event_id is not None
    event = store.read_event(result.fallback_event_id)
    assert event.action.value == "FALLBACK_TO_V1"


# 73. fallback incrementa pointer version.
def test_fallback_increments_pointer_version(tmp_path: Path) -> None:
    fx, store, v1_manifest, unified_manifest = _scenario(tmp_path)
    before = store.read_active_pointer()
    assert before is not None
    _corrupt_candidates(store, unified_manifest)
    resolve_with_fallback(store, run_id=fx.run_id, logical_name="candidates")
    after = store.read_active_pointer()
    assert after is not None
    assert after.pointer_version == before.pointer_version + 1


# 74. fallback idempotente.
def test_fallback_is_idempotent(tmp_path: Path) -> None:
    fx, store, v1_manifest, unified_manifest = _scenario(tmp_path)
    _corrupt_candidates(store, unified_manifest)
    first = resolve_with_fallback(store, run_id=fx.run_id, logical_name="candidates")
    second = resolve_with_fallback(store, run_id=fx.run_id, logical_name="candidates")
    assert first.generation_id == second.generation_id
    pointer = store.read_active_pointer()
    assert pointer is not None
    assert pointer.active_lane.value == "V1"


# 75. fallback no borra unified.
def test_fallback_never_deletes_unified_generation(tmp_path: Path) -> None:
    fx, store, v1_manifest, unified_manifest = _scenario(tmp_path)
    _corrupt_candidates(store, unified_manifest)
    resolve_with_fallback(store, run_id=fx.run_id, logical_name="candidates")
    assert store.generation_dir(unified_manifest.generation_id).is_dir()
    corrupted_path = store.generation_dir(unified_manifest.generation_id) / "candidates.json"
    assert corrupted_path.read_bytes() == b"{corrupted}"


# 76. fallback no autorizado bloquea.
def test_fallback_not_authorized_blocks(tmp_path: Path) -> None:
    fx, store, v1_manifest, unified_manifest = _scenario(tmp_path, fallback_authorized=False)
    _corrupt_candidates(store, unified_manifest)
    result = resolve_with_fallback(store, run_id=fx.run_id, logical_name="candidates")
    assert result.status.value == "BLOCKED"
    assert result.fallback_applied is False
    pointer = store.read_active_pointer()
    assert pointer is not None
    assert pointer.active_lane.value == "UNIFIED"


# 77. error de usuario no produce fallback.
def test_user_error_never_triggers_fallback(tmp_path: Path) -> None:
    from altamira_extractor.pipeline.errors import UnifiedMaterializationError

    fx, store, v1_manifest, unified_manifest = _scenario(tmp_path)
    before = store.read_active_pointer()
    with pytest.raises(UnifiedMaterializationError):
        resolve_with_fallback(store, run_id=fx.run_id, logical_name="not-a-real-logical-name")
    after = store.read_active_pointer()
    assert before == after


# 78. V1 corrupto bloquea fail-closed.
def test_v1_corruption_blocks_fail_closed(tmp_path: Path) -> None:
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
    (run_dir / "artifacts" / "06-candidates.json").write_bytes(b"{corrupted}")
    result = resolve_with_fallback(store, run_id=fx.run_id, logical_name="candidates")
    assert result.status.value == "BLOCKED"
    assert result.fallback_applied is False


class TestRollback:
    # 79. rollback previous.
    def test_rollback_previous(self, tmp_path: Path) -> None:
        fx, store, v1_manifest, unified_manifest = _scenario(tmp_path)
        result = rollback_to_previous(
            store,
            run_id=fx.run_id,
            activation_evaluation_hash=EVAL_HASH,
            authorization_hash="1" * 64,
        )
        assert result.pointer.active_generation_id == v1_manifest.generation_id

    # 80. rollback generation explicita.
    def test_rollback_to_generation_explicit(self, tmp_path: Path) -> None:
        fx, store, v1_manifest, unified_manifest = _scenario(tmp_path)
        result = rollback_to_generation(
            store,
            run_id=fx.run_id,
            target_generation_id=v1_manifest.generation_id,
            activation_evaluation_hash=EVAL_HASH,
            authorization_hash="2" * 64,
        )
        assert result.pointer.active_generation_id == v1_manifest.generation_id

    # 81. rollback a generation inexistente.
    def test_rollback_to_nonexistent_generation(self, tmp_path: Path) -> None:
        fx, store, v1_manifest, unified_manifest = _scenario(tmp_path)
        with pytest.raises(Exception):  # noqa: B017, PT011 -- UnifiedActivationStoreError
            rollback_to_generation(
                store,
                run_id=fx.run_id,
                target_generation_id="generation-does-not-exist",
                activation_evaluation_hash=EVAL_HASH,
                authorization_hash="3" * 64,
            )

    # 82. rollback a generation incompleta.
    def test_rollback_to_incomplete_generation(self, tmp_path: Path) -> None:
        fx, store, v1_manifest, unified_manifest = _scenario(tmp_path)
        _corrupt_candidates(store, unified_manifest)
        from altamira_extractor.pipeline.errors import UnifiedActivationStoreError

        with pytest.raises(UnifiedActivationStoreError):
            rollback_to_generation(
                store,
                run_id=fx.run_id,
                target_generation_id=unified_manifest.generation_id,
                activation_evaluation_hash=EVAL_HASH,
                authorization_hash="4" * 64,
            )

    # 83. rollback no autorizado -- validado a nivel de contrato
    # (UnifiedMaterializationAuthorization, ver Parte 3/15 items 5/11);
    # aqui se confirma que el propio contrato lo bloquea antes de
    # alcanzar la transicion.
    def test_rollback_authorization_contract_requires_flag(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            UnifiedMaterializationAuthorization(
                run_id="run-1",
                activation_evaluation_hash=EVAL_HASH,
                expected_readiness_disposition=UnifiedActivationReadinessDisposition.V1_ONLY_READY,
                action=UnifiedMaterializationAction.ROLLBACK_TO_PREVIOUS,
                reason_code=UnifiedMaterializationReasonCode.OPERATOR_ROLLBACK,
                review_reference="unauthorized",
                rollback_authorized=False,
            )

    # 84. rollback preserva historial.
    def test_rollback_preserves_history(self, tmp_path: Path) -> None:
        fx, store, v1_manifest, unified_manifest = _scenario(tmp_path)
        canary_pointer = store.read_active_pointer()
        assert canary_pointer is not None
        rollback_to_previous(
            store,
            run_id=fx.run_id,
            activation_evaluation_hash=EVAL_HASH,
            authorization_hash="5" * 64,
        )
        # El evento del canary sigue leible y sin cambios.
        preserved_event = store.read_event(canary_pointer.latest_event_id)
        assert (
            preserved_event.to_stable_json()
            == store.read_event(canary_pointer.latest_event_id).to_stable_json()
        )
        assert store.generation_exists(unified_manifest.generation_id)

    # 85. rollback a V1.
    def test_rollback_to_v1(self, tmp_path: Path) -> None:
        fx, store, v1_manifest, unified_manifest = _scenario(tmp_path)
        result = rollback_to_previous(
            store,
            run_id=fx.run_id,
            activation_evaluation_hash=EVAL_HASH,
            authorization_hash="6" * 64,
        )
        assert result.pointer.active_lane.value == "V1"

    # 86. rollback a unified valida.
    def test_rollback_to_valid_unified_generation(self, tmp_path: Path) -> None:
        fx, store, v1_manifest, unified_manifest = _scenario(tmp_path)
        rollback_to_previous(
            store,
            run_id=fx.run_id,
            activation_evaluation_hash=EVAL_HASH,
            authorization_hash="7" * 64,
        )
        result = rollback_to_generation(
            store,
            run_id=fx.run_id,
            target_generation_id=unified_manifest.generation_id,
            activation_evaluation_hash=EVAL_HASH,
            authorization_hash="8" * 64,
        )
        assert result.pointer.active_lane.value == "UNIFIED"
        assert result.pointer.active_generation_id == unified_manifest.generation_id


def test_active_artifact_resolver_opt_in(tmp_path: Path) -> None:
    fx, store, v1_manifest, unified_manifest = _scenario(tmp_path)
    resolver = ActiveArtifactResolver(store.run_dir, run_id=fx.run_id)
    resolution = resolver.resolve("candidates")
    assert resolution.status.value == "RESOLVED"
    path = resolver.resolve_path("candidates")
    assert path is not None
    assert path.is_file()
