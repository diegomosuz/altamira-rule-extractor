"""Tests de transiciones atomicas (Fase 14B Parte 9/15 items 45-58,
`feat/controlled-unified-materialization`)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

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
from altamira_extractor.pipeline.errors import UnifiedActivationTransitionError
from altamira_extractor.pipeline.unified_activation_generation_builder import (
    UnifiedGenerationFiles,
    build_unified_generation,
)
from altamira_extractor.pipeline.unified_activation_store import UnifiedActivationStore
from altamira_extractor.pipeline.unified_activation_transition import (
    activate_unified_canary,
    initialize_v1,
    rollback_to_previous,
)
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


def _hash_model(model: object) -> str:
    return hashlib.sha256(model.to_stable_json().encode("utf-8")).hexdigest()  # type: ignore[attr-defined]


def _setup(
    tmp_path: Path,
) -> tuple[
    MaterializationFixture,
    UnifiedActivationStore,
    MaterializedGenerationManifest,
    MaterializedGenerationManifest,
    UnifiedGenerationFiles,
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
    auth = UnifiedMaterializationAuthorization(
        run_id=fx.run_id,
        activation_evaluation_hash=EVAL_HASH,
        expected_readiness_disposition=UnifiedActivationReadinessDisposition.READY_FOR_UNIFIED_CANARY,
        action=UnifiedMaterializationAction.ACTIVATE_UNIFIED_CANARY,
        reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
        review_reference="transition-test",
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
    return fx, store, v1_manifest, unified_manifest, unified_files


# 45. inicializacion V1.
def test_initialize_v1(tmp_path: Path) -> None:
    fx, store, v1_manifest, _unified_manifest, _unified_files = _setup(tmp_path)
    result = initialize_v1(
        store,
        run_id=fx.run_id,
        v1_manifest=v1_manifest,
        activation_evaluation_hash=EVAL_HASH,
        authorization_hash=AUTH_HASH_V1,
        reason_code=UnifiedMaterializationReasonCode.KEEP_BASELINE,
    )
    assert result.pointer.active_lane.value == "V1"
    assert result.pointer.pointer_version == 1
    assert result.event.sequence == 1
    assert result.event.previous_event_id is None


# 46. pointer version.
def test_pointer_version_increments_by_one_per_transition(tmp_path: Path) -> None:
    fx, store, v1_manifest, unified_manifest, unified_files = _setup(tmp_path)
    initialize_v1(
        store,
        run_id=fx.run_id,
        v1_manifest=v1_manifest,
        activation_evaluation_hash=EVAL_HASH,
        authorization_hash=AUTH_HASH_V1,
        reason_code=UnifiedMaterializationReasonCode.KEEP_BASELINE,
    )
    canary_result = activate_unified_canary(
        store,
        run_id=fx.run_id,
        unified_manifest=unified_manifest,
        unified_files=unified_files,
        activation_evaluation_hash=EVAL_HASH,
        authorization_hash=AUTH_HASH_UNIFIED,
        reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
    )
    assert canary_result.pointer.pointer_version == 2


# 47. evento enlazado.
def test_event_linked_to_previous(tmp_path: Path) -> None:
    fx, store, v1_manifest, unified_manifest, unified_files = _setup(tmp_path)
    init_result = initialize_v1(
        store,
        run_id=fx.run_id,
        v1_manifest=v1_manifest,
        activation_evaluation_hash=EVAL_HASH,
        authorization_hash=AUTH_HASH_V1,
        reason_code=UnifiedMaterializationReasonCode.KEEP_BASELINE,
    )
    canary_result = activate_unified_canary(
        store,
        run_id=fx.run_id,
        unified_manifest=unified_manifest,
        unified_files=unified_files,
        activation_evaluation_hash=EVAL_HASH,
        authorization_hash=AUTH_HASH_UNIFIED,
        reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
    )
    assert canary_result.event.previous_event_id == init_result.event.event_id


# 48. activacion canary.
def test_activate_unified_canary(tmp_path: Path) -> None:
    fx, store, v1_manifest, unified_manifest, unified_files = _setup(tmp_path)
    initialize_v1(
        store,
        run_id=fx.run_id,
        v1_manifest=v1_manifest,
        activation_evaluation_hash=EVAL_HASH,
        authorization_hash=AUTH_HASH_V1,
        reason_code=UnifiedMaterializationReasonCode.KEEP_BASELINE,
    )
    result = activate_unified_canary(
        store,
        run_id=fx.run_id,
        unified_manifest=unified_manifest,
        unified_files=unified_files,
        activation_evaluation_hash=EVAL_HASH,
        authorization_hash=AUTH_HASH_UNIFIED,
        reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
    )
    assert result.pointer.active_lane.value == "UNIFIED"
    assert result.pointer.active_generation_id == unified_manifest.generation_id
    assert result.pointer.fallback_generation_id == v1_manifest.generation_id


# 49. activacion primary.
def test_activate_unified_primary(tmp_path: Path) -> None:
    from altamira_extractor.pipeline.unified_activation_transition import (
        activate_unified_primary,
    )

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
    auth = UnifiedMaterializationAuthorization(
        run_id=fx.run_id,
        activation_evaluation_hash=EVAL_HASH,
        expected_readiness_disposition=UnifiedActivationReadinessDisposition.READY_FOR_PRIMARY_TRIAL,
        action=UnifiedMaterializationAction.ACTIVATE_UNIFIED_PRIMARY,
        reason_code=UnifiedMaterializationReasonCode.PRIMARY_TRIAL_APPROVED,
        review_reference="primary-test",
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
    initialize_v1(
        store,
        run_id=fx.run_id,
        v1_manifest=v1_manifest,
        activation_evaluation_hash=EVAL_HASH,
        authorization_hash=AUTH_HASH_V1,
        reason_code=UnifiedMaterializationReasonCode.KEEP_BASELINE,
    )
    result = activate_unified_primary(
        store,
        run_id=fx.run_id,
        unified_manifest=unified_manifest,
        unified_files=unified_files,
        activation_evaluation_hash=EVAL_HASH,
        authorization_hash=AUTH_HASH_UNIFIED,
        reason_code=UnifiedMaterializationReasonCode.PRIMARY_TRIAL_APPROVED,
    )
    assert result.pointer.active_lane.value == "UNIFIED"
    assert result.manifest.kind.value == "UNIFIED_PRIMARY"


# 50. expected pointer hash correcto.
def test_expected_pointer_hash_correct(tmp_path: Path) -> None:
    fx, store, v1_manifest, unified_manifest, unified_files = _setup(tmp_path)
    init_result = initialize_v1(
        store,
        run_id=fx.run_id,
        v1_manifest=v1_manifest,
        activation_evaluation_hash=EVAL_HASH,
        authorization_hash=AUTH_HASH_V1,
        reason_code=UnifiedMaterializationReasonCode.KEEP_BASELINE,
    )
    expected_hash = _hash_model(init_result.pointer)
    result = activate_unified_canary(
        store,
        run_id=fx.run_id,
        unified_manifest=unified_manifest,
        unified_files=unified_files,
        activation_evaluation_hash=EVAL_HASH,
        authorization_hash=AUTH_HASH_UNIFIED,
        reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
        expected_active_pointer_hash=expected_hash,
    )
    assert result.pointer.pointer_version == 2


# 51. expected pointer hash incorrecto.
def test_expected_pointer_hash_incorrect_blocks(tmp_path: Path) -> None:
    fx, store, v1_manifest, unified_manifest, unified_files = _setup(tmp_path)
    initialize_v1(
        store,
        run_id=fx.run_id,
        v1_manifest=v1_manifest,
        activation_evaluation_hash=EVAL_HASH,
        authorization_hash=AUTH_HASH_V1,
        reason_code=UnifiedMaterializationReasonCode.KEEP_BASELINE,
    )
    before = store.read_active_pointer()
    with pytest.raises(UnifiedActivationTransitionError):
        activate_unified_canary(
            store,
            run_id=fx.run_id,
            unified_manifest=unified_manifest,
            unified_files=unified_files,
            activation_evaluation_hash=EVAL_HASH,
            authorization_hash=AUTH_HASH_UNIFIED,
            reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
            expected_active_pointer_hash="f" * 64,
        )
    after = store.read_active_pointer()
    assert before == after


# 52. evento previo preservado.
def test_previous_event_preserved_after_new_transition(tmp_path: Path) -> None:
    fx, store, v1_manifest, unified_manifest, unified_files = _setup(tmp_path)
    init_result = initialize_v1(
        store,
        run_id=fx.run_id,
        v1_manifest=v1_manifest,
        activation_evaluation_hash=EVAL_HASH,
        authorization_hash=AUTH_HASH_V1,
        reason_code=UnifiedMaterializationReasonCode.KEEP_BASELINE,
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
    preserved = store.read_event(init_result.event.event_id)
    assert preserved.to_stable_json() == init_result.event.to_stable_json()


# 53. active pointer atomico -- verificado por reread+validate dentro
# de write_active_pointer (store), aqui se confirma que el pointer
# final es exactamente el que se leyo de vuelta.
def test_active_pointer_matches_what_was_written(tmp_path: Path) -> None:
    fx, store, v1_manifest, _unified_manifest, _unified_files = _setup(tmp_path)
    result = initialize_v1(
        store,
        run_id=fx.run_id,
        v1_manifest=v1_manifest,
        activation_evaluation_hash=EVAL_HASH,
        authorization_hash=AUTH_HASH_V1,
        reason_code=UnifiedMaterializationReasonCode.KEEP_BASELINE,
    )
    reread = store.read_active_pointer()
    assert reread is not None
    assert reread.to_stable_json() == result.pointer.to_stable_json()


# 56. evento huerfano no se considera activo.
def test_orphan_event_never_considered_active(tmp_path: Path) -> None:
    fx, store, v1_manifest, unified_manifest, unified_files = _setup(tmp_path)
    init_result = initialize_v1(
        store,
        run_id=fx.run_id,
        v1_manifest=v1_manifest,
        activation_evaluation_hash=EVAL_HASH,
        authorization_hash=AUTH_HASH_V1,
        reason_code=UnifiedMaterializationReasonCode.KEEP_BASELINE,
    )
    # Un evento persistido manualmente, NUNCA enlazado desde
    # active.json.latest_event_id, es un intento no confirmado.
    from altamira_extractor.contracts.unified_activation_materialization import (
        ActivationTransitionAction,
        ActivationTransitionEvent,
        MaterializedActivationLane,
    )

    orphan = ActivationTransitionEvent(
        event_id="event-orphan",
        run_id=fx.run_id,
        sequence=99,
        action=ActivationTransitionAction.KEEP_CURRENT,
        from_generation_id=v1_manifest.generation_id,
        to_generation_id=v1_manifest.generation_id,
        previous_event_id=init_result.event.event_id,
        activation_evaluation_hash=EVAL_HASH,
        authorization_hash=AUTH_HASH_V1,
        reason_code=UnifiedMaterializationReasonCode.KEEP_BASELINE,
        resulting_lane=MaterializedActivationLane.V1,
    )
    store.persist_event(orphan)
    pointer = store.read_active_pointer()
    assert pointer is not None
    assert pointer.latest_event_id == init_result.event.event_id
    assert pointer.latest_event_id != orphan.event_id


# 57. misma accion idempotente.
def test_same_action_is_idempotent(tmp_path: Path) -> None:
    fx, store, v1_manifest, unified_manifest, unified_files = _setup(tmp_path)
    initialize_v1(
        store,
        run_id=fx.run_id,
        v1_manifest=v1_manifest,
        activation_evaluation_hash=EVAL_HASH,
        authorization_hash=AUTH_HASH_V1,
        reason_code=UnifiedMaterializationReasonCode.KEEP_BASELINE,
    )
    result_1 = activate_unified_canary(
        store,
        run_id=fx.run_id,
        unified_manifest=unified_manifest,
        unified_files=unified_files,
        activation_evaluation_hash=EVAL_HASH,
        authorization_hash=AUTH_HASH_UNIFIED,
        reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
    )
    result_2 = activate_unified_canary(
        store,
        run_id=fx.run_id,
        unified_manifest=unified_manifest,
        unified_files=unified_files,
        activation_evaluation_hash=EVAL_HASH,
        authorization_hash=AUTH_HASH_UNIFIED,
        reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
    )
    assert result_1.pointer.to_stable_json() == result_2.pointer.to_stable_json()
    assert result_2.idempotent is True


# 58. no evento duplicado.
def test_no_duplicate_event_on_repeat(tmp_path: Path) -> None:
    fx, store, v1_manifest, unified_manifest, unified_files = _setup(tmp_path)
    initialize_v1(
        store,
        run_id=fx.run_id,
        v1_manifest=v1_manifest,
        activation_evaluation_hash=EVAL_HASH,
        authorization_hash=AUTH_HASH_V1,
        reason_code=UnifiedMaterializationReasonCode.KEEP_BASELINE,
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
    events_before = list((store.activation_dir / "events").iterdir())
    activate_unified_canary(
        store,
        run_id=fx.run_id,
        unified_manifest=unified_manifest,
        unified_files=unified_files,
        activation_evaluation_hash=EVAL_HASH,
        authorization_hash=AUTH_HASH_UNIFIED,
        reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
    )
    events_after = list((store.activation_dir / "events").iterdir())
    assert len(events_before) == len(events_after)


def test_rollback_to_previous_relinks_chain(tmp_path: Path) -> None:
    fx, store, v1_manifest, unified_manifest, unified_files = _setup(tmp_path)
    initialize_v1(
        store,
        run_id=fx.run_id,
        v1_manifest=v1_manifest,
        activation_evaluation_hash=EVAL_HASH,
        authorization_hash=AUTH_HASH_V1,
        reason_code=UnifiedMaterializationReasonCode.KEEP_BASELINE,
    )
    canary_result = activate_unified_canary(
        store,
        run_id=fx.run_id,
        unified_manifest=unified_manifest,
        unified_files=unified_files,
        activation_evaluation_hash=EVAL_HASH,
        authorization_hash=AUTH_HASH_UNIFIED,
        reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
    )
    rollback_result = rollback_to_previous(
        store,
        run_id=fx.run_id,
        activation_evaluation_hash=EVAL_HASH,
        authorization_hash="9" * 64,
    )
    assert rollback_result.pointer.active_generation_id == v1_manifest.generation_id
    assert rollback_result.event.previous_event_id == canary_result.event.event_id
