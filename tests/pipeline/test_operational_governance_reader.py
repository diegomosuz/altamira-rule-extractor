"""Tests del reader de gobierno operativo (Fase 15A Parte 4/5/12, items
9-41, `feat/operational-governance-ui`)."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import NamedTuple

import pytest

from altamira_extractor.contracts.operational_governance import (
    GovernanceEventChainStatus,
    GovernanceGenerationReachability,
    GovernanceIntegrityStatus,
    GovernanceIssueCode,
    OperationalGovernanceStatus,
)
from altamira_extractor.contracts.unified_activation_evaluation import (
    UnifiedActivationReadinessDisposition,
)
from altamira_extractor.contracts.unified_activation_materialization import (
    ActivationTransitionAction,
    ActivationTransitionEvent,
    MaterializedActivationLane,
    MaterializedGenerationKind,
    MaterializedGenerationManifest,
    compute_event_id,
)
from altamira_extractor.contracts.unified_materialization_authorization import (
    UnifiedMaterializationAction,
    UnifiedMaterializationAuthorization,
    UnifiedMaterializationReasonCode,
)
from altamira_extractor.pipeline.operational_governance_reader import (
    OperationalGovernanceReadError,
    build_operational_governance_overview,
)
from altamira_extractor.pipeline.unified_activation_generation_builder import (
    build_unified_generation,
)
from altamira_extractor.pipeline.unified_activation_store import UnifiedActivationStore
from altamira_extractor.pipeline.unified_activation_transition import (
    rollback_to_generation,
    rollback_to_previous,
)
from altamira_extractor.pipeline.unified_materialization_service import MaterializationResult

from ._operational_governance_fixtures import (
    MaterializationFixture,
    build_materialization_fixture,
    evaluation_hash_of,
    governance_run_dir,
    materialize_keep_v1,
    materialize_unified_canary,
    write_run_json,
)

HASH64 = "f" * 64


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {
        p.relative_to(directory).as_posix(): p.read_bytes()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


# --------------------------------------------------------------------
# READER (9-24)
# --------------------------------------------------------------------


# 9. activation ausente.
def test_activation_absent_is_not_initialized(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    assert overview.status == OperationalGovernanceStatus.NOT_INITIALIZED
    assert overview.activation_initialized is False
    assert not (run_dir / "activation").exists()


# 10. active V1 valido.
def test_active_v1_valid_is_healthy(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    materialize_keep_v1(run_dir, fx, tmp_path)
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    assert overview.status == OperationalGovernanceStatus.HEALTHY_V1
    assert overview.active_lane == MaterializedActivationLane.V1
    assert overview.active_manifest_integrity == GovernanceIntegrityStatus.VALID


# 11. active unified valido.
def test_active_unified_valid_is_healthy(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    assert overview.status == OperationalGovernanceStatus.HEALTHY_UNIFIED
    assert overview.active_lane == MaterializedActivationLane.UNIFIED
    assert overview.active_generation_kind == MaterializedGenerationKind.UNIFIED_CANARY


# 12. active pointer invalido.
def test_active_pointer_invalid_is_blocked(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    materialize_keep_v1(run_dir, fx, tmp_path)
    (run_dir / "activation" / "active.json").write_text("{not valid json", encoding="utf-8")
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    assert overview.status == OperationalGovernanceStatus.BLOCKED
    codes = {i.code for i in overview.issues}
    assert GovernanceIssueCode.ACTIVE_POINTER_INVALID in codes


# 13. active generation ausente.
def test_active_generation_missing_is_blocked(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    result = materialize_keep_v1(run_dir, fx, tmp_path)
    store = UnifiedActivationStore(run_dir)
    shutil.rmtree(store.generation_dir(result.generation_id))
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    assert overview.status == OperationalGovernanceStatus.BLOCKED
    assert overview.active_manifest_integrity == GovernanceIntegrityStatus.MISSING
    codes = {i.code for i in overview.issues}
    assert GovernanceIssueCode.ACTIVE_GENERATION_MISSING in codes


# 14. manifest corrupto.
def test_active_manifest_invalid_is_blocked(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    result = materialize_keep_v1(run_dir, fx, tmp_path)
    store = UnifiedActivationStore(run_dir)
    manifest_path = store.generation_dir(result.generation_id) / "manifest.json"
    manifest_path.write_text("{not valid json", encoding="utf-8")
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    assert overview.status == OperationalGovernanceStatus.BLOCKED
    assert overview.active_manifest_integrity == GovernanceIntegrityStatus.INVALID
    codes = {i.code for i in overview.issues}
    assert GovernanceIssueCode.ACTIVE_MANIFEST_INVALID in codes


# 15. manifest hash incorrecto.
def test_active_manifest_hash_mismatch_is_blocked(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    result = materialize_keep_v1(run_dir, fx, tmp_path)
    store = UnifiedActivationStore(run_dir)
    manifest_path = store.generation_dir(result.generation_id) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["diagnostics"] = ["tampered-for-test"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    assert overview.status == OperationalGovernanceStatus.BLOCKED
    assert overview.active_manifest_integrity == GovernanceIntegrityStatus.INVALID
    codes = {i.code for i in overview.issues}
    assert GovernanceIssueCode.ACTIVE_MANIFEST_HASH_MISMATCH in codes


# 16. file missing.
def test_active_file_missing_reported_as_missing(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    result = materialize_unified_canary(run_dir, fx, tmp_path)
    store = UnifiedActivationStore(run_dir)
    (store.generation_dir(result.generation_id) / "candidates.json").unlink()
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    candidates = next(a for a in overview.artifacts if a.logical_name == "candidates")
    assert candidates.status.value == "MISSING"
    codes = {i.code for i in overview.issues}
    assert GovernanceIssueCode.ACTIVE_FILE_MISSING in codes


# 17. file hash incorrecto.
def test_active_file_hash_mismatch_reported_as_corrupt(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    result = materialize_unified_canary(run_dir, fx, tmp_path)
    store = UnifiedActivationStore(run_dir)
    (store.generation_dir(result.generation_id) / "candidates.json").write_bytes(b"{corrupt}")
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    candidates = next(a for a in overview.artifacts if a.logical_name == "candidates")
    assert candidates.status.value == "CORRUPT"
    assert candidates.downloadable is False
    codes = {i.code for i in overview.issues}
    assert GovernanceIssueCode.ACTIVE_FILE_HASH_MISMATCH in codes


# 18. fallback V1 valido.
def test_fallback_v1_valid_has_no_fallback_issue(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    codes = {i.code for i in overview.issues}
    assert GovernanceIssueCode.FALLBACK_GENERATION_MISSING not in codes
    assert GovernanceIssueCode.FALLBACK_NOT_V1 not in codes
    fallback = next(
        g for g in overview.generations if g.generation_id == overview.fallback_generation_id
    )
    assert fallback.lane == MaterializedActivationLane.V1


# 19. fallback ausente.
def test_fallback_generation_missing_reported(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    store = UnifiedActivationStore(run_dir)
    pointer = store.read_active_pointer()
    assert pointer is not None
    shutil.rmtree(store.generation_dir(pointer.fallback_generation_id))
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    codes = {i.code for i in overview.issues}
    assert GovernanceIssueCode.FALLBACK_GENERATION_MISSING in codes


# 20. fallback no V1.
def test_fallback_not_v1_reported(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    result = materialize_unified_canary(run_dir, fx, tmp_path)
    store = UnifiedActivationStore(run_dir)
    pointer = store.read_active_pointer()
    assert pointer is not None
    tampered = pointer.model_copy(update={"fallback_generation_id": result.generation_id})
    store.write_active_pointer(tampered)
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    codes = {i.code for i in overview.issues}
    assert GovernanceIssueCode.FALLBACK_NOT_V1 in codes


# 21. evaluation ausente.
def test_evaluation_missing_reported(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    materialize_keep_v1(run_dir, fx, tmp_path)
    (run_dir / "diagnostics" / "unified-activation-evaluation.json").unlink()
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    assert overview.readiness is None
    codes = {i.code for i in overview.issues}
    assert GovernanceIssueCode.ACTIVATION_EVALUATION_MISSING in codes


# 22. evaluation valida.
def test_evaluation_valid_populates_readiness(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    materialize_keep_v1(run_dir, fx, tmp_path)
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    assert overview.readiness is not None
    assert (
        overview.readiness.readiness_disposition
        == UnifiedActivationReadinessDisposition.READY_FOR_UNIFIED_CANARY.value
    )


# 23. inputs no modificados.
def test_reader_never_modifies_run_dir(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    before = _snapshot(run_dir)
    build_operational_governance_overview(run_dir, fx.run_id)
    after = _snapshot(run_dir)
    assert before == after


# 24. cero escrituras (repetido: dos lecturas consecutivas son idempotentes
# y no crean ningun archivo nuevo).
def test_reader_creates_no_new_files(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    before_paths = {p.relative_to(run_dir).as_posix() for p in run_dir.rglob("*")}
    build_operational_governance_overview(run_dir, fx.run_id)
    build_operational_governance_overview(run_dir, fx.run_id)
    after_paths = {p.relative_to(run_dir).as_posix() for p in run_dir.rglob("*")}
    assert before_paths == after_paths


# --------------------------------------------------------------------
# EVENT CHAIN (25-34)
# --------------------------------------------------------------------


# 25. cadena valida.
def test_event_chain_valid(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    assert overview.event_chain_status == GovernanceEventChainStatus.VALID
    assert overview.event_chain_length == 2
    assert all(e.confirmed for e in overview.events)


# 26. cadena vacia.
def test_event_chain_empty_without_activation(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    assert overview.event_chain_status == GovernanceEventChainStatus.EMPTY
    assert overview.events == []


# 27. previous event ausente.
def test_event_chain_broken_when_previous_missing(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    store = UnifiedActivationStore(run_dir)
    pointer = store.read_active_pointer()
    assert pointer is not None
    latest = store.read_event(pointer.latest_event_id)
    assert latest.previous_event_id is not None
    (run_dir / "activation" / "events" / f"{latest.previous_event_id}.json").unlink()
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    assert overview.event_chain_status == GovernanceEventChainStatus.BROKEN
    codes = {i.code for i in overview.issues}
    assert GovernanceIssueCode.EVENT_CHAIN_BROKEN in codes


# 28. ciclo.
def test_event_chain_cycle_detected(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    materialize_keep_v1(run_dir, fx, tmp_path)
    store = UnifiedActivationStore(run_dir)
    pointer = store.read_active_pointer()
    assert pointer is not None
    only_event = store.read_event(pointer.latest_event_id)
    # Fuerza un ciclo: el evento se referencia a si mismo como su propio
    # previous_event_id (nunca ocurre en la practica; construido para
    # este test unicamente).
    cyclic = only_event.model_copy(update={"sequence": 2, "previous_event_id": only_event.event_id})
    event_path = run_dir / "activation" / "events" / f"{only_event.event_id}.json"
    event_path.write_text(cyclic.to_stable_json(), encoding="utf-8")
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    assert overview.event_chain_status == GovernanceEventChainStatus.CYCLIC
    codes = {i.code for i in overview.issues}
    assert GovernanceIssueCode.EVENT_CHAIN_CYCLE in codes


# 29. sequence inconsistente.
def test_event_chain_broken_on_sequence_gap(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    store = UnifiedActivationStore(run_dir)
    pointer = store.read_active_pointer()
    assert pointer is not None
    # Tampera la SEGUNDA transicion (no la raiz): sequence=3 en vez de
    # 2, dejando `previous_event_id` intacto -- el evento sigue siendo
    # valido segun sus PROPIOS validadores (sequence>1 exige
    # previous_event_id, que sigue presente), asi que `store.read_event`
    # lo relee sin lanzar; el gap solo lo detecta la reconstruccion de
    # la cadena del reader (Parte 5), nunca el modelo en si.
    latest_event = store.read_event(pointer.latest_event_id)
    tampered = latest_event.model_copy(update={"sequence": 3})
    (run_dir / "activation" / "events" / f"{latest_event.event_id}.json").write_text(
        tampered.to_stable_json(), encoding="utf-8"
    )
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    codes = {i.code for i in overview.issues}
    assert GovernanceIssueCode.EVENT_SEQUENCE_INVALID in codes


# 30. event run_id incorrecto.
def test_event_chain_broken_on_wrong_run_id(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    materialize_keep_v1(run_dir, fx, tmp_path)
    store = UnifiedActivationStore(run_dir)
    pointer = store.read_active_pointer()
    assert pointer is not None
    event = store.read_event(pointer.latest_event_id)
    tampered = event.model_copy(update={"run_id": "20260101T000000000000-deadbeef"})
    (run_dir / "activation" / "events" / f"{event.event_id}.json").write_text(
        tampered.to_stable_json(), encoding="utf-8"
    )
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    assert overview.event_chain_status == GovernanceEventChainStatus.BROKEN
    codes = {i.code for i in overview.issues}
    assert GovernanceIssueCode.EVENT_CHAIN_BROKEN in codes


# 31. latest event no confirma activo.
def test_event_pointer_mismatch_detected(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    v1_result = materialize_keep_v1(run_dir, fx, tmp_path)
    materialize_unified_canary(run_dir, fx, tmp_path)
    store = UnifiedActivationStore(run_dir)
    pointer = store.read_active_pointer()
    assert pointer is not None

    # Genera una TERCERA generacion valida, nunca referenciada por
    # ningun evento -- apuntar `active_generation_id` alli (en vez de
    # reutilizar V1, que ya es `previous_generation_id` y violaria el
    # invariante previous!=active del propio contrato) produce un
    # mismatch limpio: el ultimo evento CONFIRMADO sigue apuntando a la
    # generacion canary, nunca a esta.
    unrelated_authorization = UnifiedMaterializationAuthorization(
        run_id=fx.run_id,
        activation_evaluation_hash=evaluation_hash_of(run_dir),
        expected_readiness_disposition=(
            UnifiedActivationReadinessDisposition.READY_FOR_PRIMARY_TRIAL
        ),
        action=UnifiedMaterializationAction.ACTIVATE_UNIFIED_PRIMARY,
        reason_code=UnifiedMaterializationReasonCode.PRIMARY_TRIAL_APPROVED,
        review_reference="pointer-mismatch-test",
        approved_group_ids=fx.approved_group_ids,
        fallback_authorized=True,
    )
    unrelated_manifest, unrelated_files = build_unified_generation(
        evaluation=fx.evaluation,
        downstream=fx.gp.downstream_artifact,
        authorization=unrelated_authorization,
        run_id=fx.run_id,
        source_package_hash=fx.source_package_hash,
        activation_evaluation_hash=evaluation_hash_of(run_dir),
        authorization_hash="2" * 64,
        fallback_generation_id=v1_result.generation_id,
    )
    store.persist_generation(unrelated_manifest, unrelated_files.bytes_by_logical_name())
    unrelated_hash = hashlib.sha256(unrelated_manifest.to_stable_json().encode("utf-8")).hexdigest()

    tampered = pointer.model_copy(
        update={
            "active_generation_id": unrelated_manifest.generation_id,
            "active_lane": MaterializedActivationLane.UNIFIED,
            "active_generation_manifest_hash": unrelated_hash,
        }
    )
    store.write_active_pointer(tampered)
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    codes = {i.code for i in overview.issues}
    assert GovernanceIssueCode.EVENT_POINTER_MISMATCH in codes


# 32. evento huerfano.
def test_orphan_event_detected(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    materialize_keep_v1(run_dir, fx, tmp_path)
    store = UnifiedActivationStore(run_dir)
    pointer = store.read_active_pointer()
    assert pointer is not None
    fake_previous_event_id = f"event-{'0' * 64}"
    orphan = ActivationTransitionEvent(
        event_id=compute_event_id(
            run_id=fx.run_id,
            sequence=99,
            action=ActivationTransitionAction.KEEP_CURRENT,
            from_generation_id=pointer.active_generation_id,
            to_generation_id=pointer.active_generation_id,
            previous_event_id=fake_previous_event_id,
            authorization_hash=HASH64,
        ),
        run_id=fx.run_id,
        sequence=99,
        action=ActivationTransitionAction.KEEP_CURRENT,
        from_generation_id=pointer.active_generation_id,
        to_generation_id=pointer.active_generation_id,
        previous_event_id=fake_previous_event_id,
        activation_evaluation_hash=HASH64,
        authorization_hash=HASH64,
        reason_code=UnifiedMaterializationReasonCode.KEEP_BASELINE,
        resulting_lane=MaterializedActivationLane.V1,
    )
    store.persist_event(orphan)
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    assert overview.orphan_event_count == 1
    orphan_summary = next(e for e in overview.events if not e.confirmed)
    assert orphan_summary.event_id == orphan.event_id
    codes = {i.code for i in overview.issues}
    assert GovernanceIssueCode.ORPHAN_EVENT in codes


# 33. multiples huerfanos.
def test_multiple_orphan_events_detected(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    materialize_keep_v1(run_dir, fx, tmp_path)
    store = UnifiedActivationStore(run_dir)
    pointer = store.read_active_pointer()
    assert pointer is not None
    fake_previous_event_id = f"event-{'0' * 64}"
    for sequence in (97, 98):
        orphan = ActivationTransitionEvent(
            event_id=compute_event_id(
                run_id=fx.run_id,
                sequence=sequence,
                action=ActivationTransitionAction.KEEP_CURRENT,
                from_generation_id=pointer.active_generation_id,
                to_generation_id=pointer.active_generation_id,
                previous_event_id=fake_previous_event_id,
                authorization_hash=HASH64,
            ),
            run_id=fx.run_id,
            sequence=sequence,
            action=ActivationTransitionAction.KEEP_CURRENT,
            from_generation_id=pointer.active_generation_id,
            to_generation_id=pointer.active_generation_id,
            previous_event_id=fake_previous_event_id,
            activation_evaluation_hash=HASH64,
            authorization_hash=HASH64,
            reason_code=UnifiedMaterializationReasonCode.KEEP_BASELINE,
            resulting_lane=MaterializedActivationLane.V1,
        )
        store.persist_event(orphan)
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    assert overview.orphan_event_count == 2


# 34. orden deterministico.
def test_events_and_generations_are_deterministic_across_reads(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    first = build_operational_governance_overview(run_dir, fx.run_id)
    second = build_operational_governance_overview(run_dir, fx.run_id)
    assert first.to_stable_json() == second.to_stable_json()


# --------------------------------------------------------------------
# GENERATIONS (35-41)
# --------------------------------------------------------------------


class _LifecycleFixture(NamedTuple):
    fx: MaterializationFixture
    v1_result: MaterializationResult
    canary_result: MaterializationResult
    valid_manifest: MaterializedGenerationManifest


def _lifecycle_run_dir(tmp_path: Path) -> tuple[Path, _LifecycleFixture]:
    """A(V1) -> B(canary) -> C(rollback_to_generation, unified VALIDA
    distinta, kind=UNIFIED_PRIMARY) -> B (rollback_to_previous) -> A
    (rollback_to_generation, de vuelta a V1). `rollback_to_previous`
    SIEMPRE apunta a `previous_generation_id` TAL COMO ESTABA en el
    momento de la llamada (nunca "el V1 original"): el swap real es
    C<->B en el paso 4, no A<->C. Se necesita este QUINTO paso
    (volver explicitamente a A) para que C quede referenciada
    UNICAMENTE por eventos historicos -- ni activa, ni previa, ni
    fallback -- y por lo tanto clasifique como HISTORICAL."""
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    v1_result = materialize_keep_v1(run_dir, fx, tmp_path)
    canary_result = materialize_unified_canary(run_dir, fx, tmp_path)
    store = UnifiedActivationStore(run_dir)

    primary_authorization = UnifiedMaterializationAuthorization(
        run_id=fx.run_id,
        activation_evaluation_hash=evaluation_hash_of(run_dir),
        expected_readiness_disposition=UnifiedActivationReadinessDisposition.READY_FOR_PRIMARY_TRIAL,
        action=UnifiedMaterializationAction.ACTIVATE_UNIFIED_PRIMARY,
        reason_code=UnifiedMaterializationReasonCode.PRIMARY_TRIAL_APPROVED,
        review_reference="reader-lifecycle-test",
        approved_group_ids=fx.approved_group_ids,
        fallback_authorized=True,
    )
    valid_manifest, valid_files = build_unified_generation(
        evaluation=fx.evaluation,
        downstream=fx.gp.downstream_artifact,
        authorization=primary_authorization,
        run_id=fx.run_id,
        source_package_hash=fx.source_package_hash,
        activation_evaluation_hash=evaluation_hash_of(run_dir),
        authorization_hash="9" * 64,
        fallback_generation_id=v1_result.generation_id,
    )
    store.persist_generation(valid_manifest, valid_files.bytes_by_logical_name())
    rollback_to_generation(
        store,
        run_id=fx.run_id,
        target_generation_id=valid_manifest.generation_id,
        activation_evaluation_hash=evaluation_hash_of(run_dir),
        authorization_hash="a" * 64,
    )
    rollback_to_previous(
        store,
        run_id=fx.run_id,
        activation_evaluation_hash=evaluation_hash_of(run_dir),
        authorization_hash="b" * 64,
    )
    rollback_to_generation(
        store,
        run_id=fx.run_id,
        target_generation_id=v1_result.generation_id,
        activation_evaluation_hash=evaluation_hash_of(run_dir),
        authorization_hash="c" * 64,
    )

    return run_dir, _LifecycleFixture(fx, v1_result, canary_result, valid_manifest)


# 35. active.
def test_generation_reachability_active(tmp_path: Path) -> None:
    run_dir, (fx, v1_result, _canary, _valid) = _lifecycle_run_dir(tmp_path)
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    assert overview.active_generation_id == v1_result.generation_id
    active = next(g for g in overview.generations if g.generation_id == v1_result.generation_id)
    assert active.reachability == GovernanceGenerationReachability.ACTIVE


# 36. previous.
def test_generation_reachability_previous(tmp_path: Path) -> None:
    run_dir, (fx, _v1, canary_result, _valid) = _lifecycle_run_dir(tmp_path)
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    assert overview.previous_generation_id == canary_result.generation_id
    previous = next(
        g for g in overview.generations if g.generation_id == canary_result.generation_id
    )
    assert previous.reachability == GovernanceGenerationReachability.PREVIOUS


# 37. fallback.
def test_generation_reachability_fallback(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    v1_result = materialize_keep_v1(run_dir, fx, tmp_path)
    materialize_unified_canary(run_dir, fx, tmp_path)
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    fallback = next(g for g in overview.generations if g.generation_id == v1_result.generation_id)
    assert fallback.reachability in (
        GovernanceGenerationReachability.FALLBACK,
        GovernanceGenerationReachability.PREVIOUS,
    )


# 38. historical.
def test_generation_reachability_historical(tmp_path: Path) -> None:
    run_dir, (fx, _v1, _canary, valid_manifest) = _lifecycle_run_dir(tmp_path)
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    historical = next(
        g for g in overview.generations if g.generation_id == valid_manifest.generation_id
    )
    assert historical.reachability == GovernanceGenerationReachability.HISTORICAL
    assert historical.generation_id not in (
        overview.active_generation_id,
        overview.previous_generation_id,
        overview.fallback_generation_id,
    )


# 39. orphan.
def test_generation_reachability_orphan(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    v1_result = materialize_keep_v1(run_dir, fx, tmp_path)
    store = UnifiedActivationStore(run_dir)

    unrelated_authorization = UnifiedMaterializationAuthorization(
        run_id=fx.run_id,
        activation_evaluation_hash=evaluation_hash_of(run_dir),
        expected_readiness_disposition=UnifiedActivationReadinessDisposition.READY_FOR_UNIFIED_CANARY,
        action=UnifiedMaterializationAction.ACTIVATE_UNIFIED_CANARY,
        reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
        review_reference="never-activated",
        approved_group_ids=fx.approved_group_ids,
        fallback_authorized=True,
    )
    orphan_manifest, orphan_files = build_unified_generation(
        evaluation=fx.evaluation,
        downstream=fx.gp.downstream_artifact,
        authorization=unrelated_authorization,
        run_id=fx.run_id,
        source_package_hash=fx.source_package_hash,
        activation_evaluation_hash=evaluation_hash_of(run_dir),
        authorization_hash="e" * 64,
        fallback_generation_id=v1_result.generation_id,
    )
    store.persist_generation(orphan_manifest, orphan_files.bytes_by_logical_name())

    overview = build_operational_governance_overview(run_dir, fx.run_id)
    orphan = next(
        g for g in overview.generations if g.generation_id == orphan_manifest.generation_id
    )
    assert orphan.reachability == GovernanceGenerationReachability.ORPHAN
    assert overview.orphan_generation_count == 1
    codes = {i.code for i in overview.issues}
    assert GovernanceIssueCode.ORPHAN_GENERATION in codes
    # Nunca eliminada.
    assert store.generation_exists(orphan_manifest.generation_id)


# 40. generacion corrupta no reparada.
def test_corrupt_generation_never_repaired(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    result = materialize_unified_canary(run_dir, fx, tmp_path)
    store = UnifiedActivationStore(run_dir)
    corrupted_path = store.generation_dir(result.generation_id) / "candidates.json"
    original_bytes = corrupted_path.read_bytes()
    corrupted_path.write_bytes(b"{corrupted}")

    build_operational_governance_overview(run_dir, fx.run_id)
    build_operational_governance_overview(run_dir, fx.run_id)

    assert corrupted_path.read_bytes() == b"{corrupted}"
    assert corrupted_path.read_bytes() != original_bytes


# 41. generacion huerfana no eliminada.
def test_orphan_generation_never_deleted(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    v1_result = materialize_keep_v1(run_dir, fx, tmp_path)
    store = UnifiedActivationStore(run_dir)

    authorization = UnifiedMaterializationAuthorization(
        run_id=fx.run_id,
        activation_evaluation_hash=evaluation_hash_of(run_dir),
        expected_readiness_disposition=UnifiedActivationReadinessDisposition.READY_FOR_UNIFIED_CANARY,
        action=UnifiedMaterializationAction.ACTIVATE_UNIFIED_CANARY,
        reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
        review_reference="never-activated-2",
        approved_group_ids=fx.approved_group_ids,
        fallback_authorized=True,
    )
    orphan_manifest, orphan_files = build_unified_generation(
        evaluation=fx.evaluation,
        downstream=fx.gp.downstream_artifact,
        authorization=authorization,
        run_id=fx.run_id,
        source_package_hash=fx.source_package_hash,
        activation_evaluation_hash=evaluation_hash_of(run_dir),
        authorization_hash="1" * 64,
        fallback_generation_id=v1_result.generation_id,
    )
    store.persist_generation(orphan_manifest, orphan_files.bytes_by_logical_name())

    build_operational_governance_overview(run_dir, fx.run_id)
    build_operational_governance_overview(run_dir, fx.run_id)

    assert store.generation_exists(orphan_manifest.generation_id)


def test_run_dir_unreadable_raises_read_error(tmp_path: Path) -> None:
    missing_run_dir = tmp_path / "does-not-exist"
    with pytest.raises(OperationalGovernanceReadError):
        build_operational_governance_overview(missing_run_dir, "20260101T000000000000-aaaaaaaa")


def test_run_json_corrupt_raises_read_error(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    (run_dir / "run.json").write_text("{not valid", encoding="utf-8")
    with pytest.raises(OperationalGovernanceReadError):
        build_operational_governance_overview(run_dir, fx.run_id)


def test_run_stage_reflects_run_json(tmp_path: Path) -> None:
    from altamira_extractor.contracts.enums import PipelineStage

    from ._unified_materialization_fixtures import write_run_dir

    fx = build_materialization_fixture()
    run_dir = write_run_dir(tmp_path, fx)
    write_run_json(run_dir, fx.run_id, stage=PipelineStage.GUARDRAILS_APPLIED)
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    assert overview.run_stage == "GUARDRAILS_APPLIED"
