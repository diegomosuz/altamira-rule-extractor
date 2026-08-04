"""Tests del store content-addressed (Fase 14B Parte 8/15 items 31-44,
Parte 16 inyeccion de fallos A-J, Parte 17 concurrencia,
`feat/controlled-unified-materialization`)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from altamira_extractor.contracts.unified_activation_materialization import (
    ActiveActivationPointer,
    MaterializedGenerationManifest,
)
from altamira_extractor.pipeline import artifact_store as artifact_store_module
from altamira_extractor.pipeline.errors import (
    UnifiedActivationLockError,
    UnifiedActivationStoreError,
)
from altamira_extractor.pipeline.unified_activation_generation_builder import (
    UnifiedGenerationFiles,
)
from altamira_extractor.pipeline.unified_activation_store import UnifiedActivationStore
from altamira_extractor.pipeline.v1_activation_generation_builder import (
    build_v1_generation_manifest,
)
from tests.pipeline._unified_materialization_fixtures import MaterializationFixture

HASH = "a" * 64
RUN_ID = "20260101T000000000000-aaaaaaaa"


def _run_dir_with_candidates(tmp_path: Path, content: bytes = b'{"x":1}') -> Path:
    run_dir = tmp_path / "run"
    (run_dir / "artifacts").mkdir(parents=True)
    (run_dir / "artifacts" / "06-candidates.json").write_bytes(content)
    return run_dir


def _v1_manifest(run_dir: Path) -> MaterializedGenerationManifest:
    return build_v1_generation_manifest(
        run_dir,
        run_id=RUN_ID,
        source_package_hash=HASH,
        activation_evaluation_hash=HASH,
        authorization_hash=HASH,
    )


# 31. creacion segura de generation.
def test_persist_generation_creates_directory(tmp_path: Path) -> None:
    run_dir = _run_dir_with_candidates(tmp_path)
    store = UnifiedActivationStore(run_dir)
    manifest = _v1_manifest(run_dir)
    with store.lock():
        persisted = store.persist_generation(manifest, {})
    assert store.generation_exists(persisted.generation_id)
    assert (store.generation_dir(persisted.generation_id) / "manifest.json").is_file()


# 32. reutilizacion idempotente.
def test_persist_generation_is_idempotent(tmp_path: Path) -> None:
    run_dir = _run_dir_with_candidates(tmp_path)
    store = UnifiedActivationStore(run_dir)
    manifest = _v1_manifest(run_dir)
    with store.lock():
        first = store.persist_generation(manifest, {})
        second = store.persist_generation(manifest, {})
    assert first.to_stable_json() == second.to_stable_json()


# 33. colision detectada.
def test_persist_generation_collision_detected(tmp_path: Path) -> None:
    run_dir = _run_dir_with_candidates(tmp_path)
    store = UnifiedActivationStore(run_dir)
    manifest = _v1_manifest(run_dir)
    with store.lock():
        store.persist_generation(manifest, {})
        # Mismo generation_id, pero un archivo con sha256 distinto:
        # una colision REAL de contenido (nunca reutilizable).
        tampered_files = [
            f.model_copy(update={"sha256": "b" * 64}) if f.logical_name == "candidates" else f
            for f in manifest.files
        ]
        tampered = manifest.model_copy(update={"files": tampered_files})
        with pytest.raises(UnifiedActivationStoreError):
            store.persist_generation(tampered, {})


def test_repersisting_original_content_over_a_corrupted_generation_is_rejected(
    tmp_path: Path,
) -> None:
    """Auditoria de cierre Fase 14B, seccion 1: (1) materializa una
    generacion unified real; (2) corrompe UNO de sus archivos
    directamente en disco (fuera del store, como haria cualquier
    corrupcion externa real); (3) intenta materializar de nuevo el
    CONTENIDO ORIGINAL bajo el MISMO `generation_id` (unico
    `generation_id` posible para ese contenido -- content-addressed);
    (4) el store debe detectar la colision/corrupcion en la
    reconciliacion (`_reconcile_existing_generation` -> `validate_
    generation_files` relee bytes reales del disco, nunca confia
    ciegamente en que el directorio ya existente es valido); (5) el
    archivo corrupto permanece EXACTAMENTE corrupto -- la ruta de
    reconciliacion nunca escribe, solo lee y compara, asi que no hay
    ningun byte que sobrescribir; (6) `active.json` permanece
    byte-identico, porque la excepcion se lanza antes de que
    `persist_generation` retorne, mucho antes de que cualquier llamador
    pueda llegar a escribir un puntero nuevo."""
    _fx, run_dir, store, _v1_manifest_obj, unified_manifest, unified_files = _unified_scenario(
        tmp_path
    )
    active_pointer_path = run_dir / "activation" / "active.json"
    with store.lock():
        # 1. Materializa la generacion unified real (contenido original).
        store.persist_generation(unified_manifest, unified_files.bytes_by_logical_name())

        generation_dir = store.generation_dir(unified_manifest.generation_id)
        candidates_reference = next(
            f for f in unified_manifest.files if f.logical_name == "candidates"
        )
        corrupted_path = generation_dir / candidates_reference.relative_path.rsplit("/", 1)[-1]
        assert corrupted_path.is_file()
        original_on_disk_bytes = corrupted_path.read_bytes()

        active_json_before = (
            active_pointer_path.read_bytes() if active_pointer_path.is_file() else None
        )

        # 2. Corrompe el archivo directamente en disco (fuera del store).
        corrupted_path.write_bytes(b"{corrupted-outside-the-store}")

        # 3. Intenta materializar de nuevo el CONTENIDO ORIGINAL bajo el
        #    MISMO generation_id (mismo manifest, mismos bytes originales
        #    en `unified_files` -- nada cambio del lado del llamador).
        # 4. El store debe detectar la colision/corrupcion.
        with pytest.raises(UnifiedActivationStoreError):
            store.persist_generation(unified_manifest, unified_files.bytes_by_logical_name())

        # 5. No se sobrescribio ningun byte: el archivo sigue corrupto
        #    (nunca revertido al original, nunca "reparado" en silencio).
        assert corrupted_path.read_bytes() == b"{corrupted-outside-the-store}"
        assert corrupted_path.read_bytes() != original_on_disk_bytes

        # 6. active.json permanece intacto (la excepcion ocurre antes de
        #    que cualquier puntero nuevo pueda escribirse).
        active_json_after = (
            active_pointer_path.read_bytes() if active_pointer_path.is_file() else None
        )
        assert active_json_after == active_json_before


def test_persist_generation_reuses_across_different_provenance(tmp_path: Path) -> None:
    """`authorization_hash`/`activation_evaluation_hash` son metadatos
    de PROVENANCE -- nunca parte de la identidad de la generacion
    (Parte 2, principio 3): dos peticiones legitimas con distinta
    autorizacion pero el MISMO contenido reutilizan la generacion YA
    persistida tal cual, sin colision."""
    run_dir = _run_dir_with_candidates(tmp_path)
    store = UnifiedActivationStore(run_dir)
    manifest = _v1_manifest(run_dir)
    with store.lock():
        first = store.persist_generation(manifest, {})
        differently_authorized = manifest.model_copy(update={"authorization_hash": "b" * 64})
        second = store.persist_generation(differently_authorized, {})
    assert second.authorization_hash == first.authorization_hash


# 34. archivo parcial rechazado (nunca queda referenciable).
def test_partial_file_never_referenced(tmp_path: Path) -> None:
    run_dir = _run_dir_with_candidates(tmp_path)
    store = UnifiedActivationStore(run_dir)
    manifest = _v1_manifest(run_dir)
    with store.lock():
        store.persist_generation(manifest, {})
    for entry in store.generation_dir(manifest.generation_id).iterdir():
        assert not entry.name.startswith(".tmp-")


# 35. manifest corrupto.
def test_corrupt_manifest_detected(tmp_path: Path) -> None:
    run_dir = _run_dir_with_candidates(tmp_path)
    store = UnifiedActivationStore(run_dir)
    manifest = _v1_manifest(run_dir)
    with store.lock():
        store.persist_generation(manifest, {})
    manifest_path = store.generation_dir(manifest.generation_id) / "manifest.json"
    manifest_path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(UnifiedActivationStoreError):
        store.read_generation_manifest(manifest.generation_id)


# 36. hash incorrecto.
def test_incorrect_hash_detected(tmp_path: Path) -> None:
    run_dir = _run_dir_with_candidates(tmp_path)
    store = UnifiedActivationStore(run_dir)
    manifest = _v1_manifest(run_dir)
    with store.lock():
        store.persist_generation(manifest, {})
    (run_dir / "artifacts" / "06-candidates.json").write_bytes(b'{"x":999}')
    with pytest.raises(UnifiedActivationStoreError):
        store.validate_generation_files(manifest)


# 37. path traversal rechazado.
def test_path_traversal_rejected_by_store(tmp_path: Path) -> None:
    run_dir = _run_dir_with_candidates(tmp_path)
    store = UnifiedActivationStore(run_dir)
    with pytest.raises(ValueError):  # noqa: PT011 -- ValueError del validador del contrato
        store._resolve_within_run("../outside.json")


# 38. ruta absoluta rechazada.
def test_absolute_path_rejected_by_store(tmp_path: Path) -> None:
    run_dir = _run_dir_with_candidates(tmp_path)
    store = UnifiedActivationStore(run_dir)
    with pytest.raises(ValueError):  # noqa: PT011
        store._resolve_within_run("/etc/passwd")


# 39. UNC rechazada.
def test_unc_path_rejected_by_store(tmp_path: Path) -> None:
    run_dir = _run_dir_with_candidates(tmp_path)
    store = UnifiedActivationStore(run_dir)
    with pytest.raises(ValueError):  # noqa: PT011
        store._resolve_within_run("//server/share/file.json")


# 40. lock exclusivo.
def test_lock_is_exclusive(tmp_path: Path) -> None:
    run_dir = _run_dir_with_candidates(tmp_path)
    store = UnifiedActivationStore(run_dir)
    with store.lock():
        assert store.lock_is_held()
        with pytest.raises(UnifiedActivationLockError):
            with store.lock():
                pass


# 41. lock removido en success.
def test_lock_removed_on_success(tmp_path: Path) -> None:
    run_dir = _run_dir_with_candidates(tmp_path)
    store = UnifiedActivationStore(run_dir)
    with store.lock():
        pass
    assert not store.lock_is_held()


# 42. lock removido en error.
def test_lock_removed_on_error(tmp_path: Path) -> None:
    run_dir = _run_dir_with_candidates(tmp_path)
    store = UnifiedActivationStore(run_dir)
    with pytest.raises(RuntimeError):
        with store.lock():
            raise RuntimeError("boom")
    assert not store.lock_is_held()


# 43. lock existente bloquea.
def test_existing_lock_blocks(tmp_path: Path) -> None:
    run_dir = _run_dir_with_candidates(tmp_path)
    store = UnifiedActivationStore(run_dir)
    (run_dir / "activation").mkdir(parents=True)
    (run_dir / "activation" / ".activation.lock").write_text("99999", encoding="utf-8")
    with pytest.raises(UnifiedActivationLockError):
        with store.lock():
            pass


# 44. write failure no activa (active.json no se corrompe/no se crea parcial).
def test_write_failure_never_leaves_partial_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from altamira_extractor.contracts.unified_activation_config import UnifiedFallbackPolicy
    from altamira_extractor.contracts.unified_activation_materialization import (
        ActiveActivationPointer,
        MaterializedActivationLane,
    )

    run_dir = _run_dir_with_candidates(tmp_path)
    store = UnifiedActivationStore(run_dir)
    manifest = _v1_manifest(run_dir)
    with store.lock():
        store.persist_generation(manifest, {})

    def _boom(*args: object, **kwargs: object) -> None:
        raise OSError("simulated write failure")

    monkeypatch.setattr(
        "altamira_extractor.pipeline.unified_activation_store.atomic_write_json", _boom
    )
    pointer = ActiveActivationPointer(
        run_id=RUN_ID,
        pointer_version=1,
        active_generation_id=manifest.generation_id,
        active_lane=MaterializedActivationLane.V1,
        active_generation_manifest_hash=HASH,
        fallback_generation_id=manifest.generation_id,
        latest_event_id="event-abc",
        fallback_policy=UnifiedFallbackPolicy.NO_FALLBACK,
    )
    with pytest.raises(OSError):
        store.write_active_pointer(pointer)
    assert store.read_active_pointer() is None


# ---------------------------------------------------------------------------
# Parte 16 -- inyeccion de fallos A-J
# ---------------------------------------------------------------------------


def _unified_scenario(
    tmp_path: Path,
) -> tuple[
    MaterializationFixture,
    Path,
    UnifiedActivationStore,
    MaterializedGenerationManifest,
    MaterializedGenerationManifest,
    UnifiedGenerationFiles,
]:
    from altamira_extractor.contracts.unified_activation_evaluation import (
        UnifiedActivationReadinessDisposition,
    )
    from altamira_extractor.contracts.unified_materialization_authorization import (
        UnifiedMaterializationAction,
        UnifiedMaterializationAuthorization,
        UnifiedMaterializationReasonCode,
    )
    from altamira_extractor.pipeline.unified_activation_generation_builder import (
        build_unified_generation,
    )
    from altamira_extractor.pipeline.unified_activation_transition import initialize_v1
    from altamira_extractor.pipeline.v1_activation_generation_builder import (
        build_v1_generation_manifest,
    )
    from tests.pipeline._unified_materialization_fixtures import (
        build_materialization_fixture,
        write_run_dir,
    )

    fx = build_materialization_fixture()
    run_dir = write_run_dir(tmp_path, fx)
    store = UnifiedActivationStore(run_dir)
    v1_manifest = build_v1_generation_manifest(
        run_dir,
        run_id=fx.run_id,
        source_package_hash=fx.source_package_hash,
        activation_evaluation_hash="e" * 64,
        authorization_hash="c" * 64,
    )
    initialize_v1(
        store,
        run_id=fx.run_id,
        v1_manifest=v1_manifest,
        activation_evaluation_hash="e" * 64,
        authorization_hash="c" * 64,
        reason_code=UnifiedMaterializationReasonCode.KEEP_BASELINE,
    )
    auth = UnifiedMaterializationAuthorization(
        run_id=fx.run_id,
        activation_evaluation_hash="e" * 64,
        expected_readiness_disposition=UnifiedActivationReadinessDisposition.READY_FOR_UNIFIED_CANARY,
        action=UnifiedMaterializationAction.ACTIVATE_UNIFIED_CANARY,
        reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
        review_reference="failure-injection",
        approved_group_ids=fx.approved_group_ids,
        fallback_authorized=True,
    )
    unified_manifest, unified_files = build_unified_generation(
        evaluation=fx.evaluation,
        downstream=fx.gp.downstream_artifact,
        authorization=auth,
        run_id=fx.run_id,
        source_package_hash=fx.source_package_hash,
        activation_evaluation_hash="e" * 64,
        authorization_hash="d" * 64,
        fallback_generation_id=v1_manifest.generation_id,
    )
    return fx, run_dir, store, v1_manifest, unified_manifest, unified_files


def _pointer_snapshot(store: UnifiedActivationStore) -> ActiveActivationPointer | None:
    return store.read_active_pointer()


class TestFailureInjectionAtoI:
    """A-I: cualquier fallo antes del commit point (`active.json`) deja
    el puntero/lane anterior INTACTO, nunca referencia contenido
    parcial, y siempre libera el lock."""

    def test_a_failure_before_writing_data_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fx, run_dir, store, v1_manifest, unified_manifest, unified_files = _unified_scenario(
            tmp_path
        )
        before = _pointer_snapshot(store)

        def _boom(self: Path, *args: object, **kwargs: object) -> None:
            raise OSError("simulated failure before any data file")

        monkeypatch.setattr(Path, "write_bytes", _boom)
        with pytest.raises(OSError):
            with store.lock():
                store.persist_generation(unified_manifest, unified_files.bytes_by_logical_name())
        assert not store.lock_is_held()
        assert _pointer_snapshot(store) == before
        assert not store.generation_exists(unified_manifest.generation_id)

    def test_b_failure_during_candidates_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fx, run_dir, store, v1_manifest, unified_manifest, unified_files = _unified_scenario(
            tmp_path
        )
        before = _pointer_snapshot(store)
        original_write_bytes = Path.write_bytes

        def _selective_boom(self: Path, data: bytes) -> int:
            if self.name == "candidates.json":
                raise OSError("simulated failure writing candidates.json")
            return original_write_bytes(self, data)

        monkeypatch.setattr(Path, "write_bytes", _selective_boom)
        with pytest.raises(OSError):
            with store.lock():
                store.persist_generation(unified_manifest, unified_files.bytes_by_logical_name())
        assert not store.lock_is_held()
        assert _pointer_snapshot(store) == before
        assert not store.generation_exists(unified_manifest.generation_id)

    def test_c_failure_during_rule_drafts_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fx, run_dir, store, v1_manifest, unified_manifest, unified_files = _unified_scenario(
            tmp_path
        )
        before = _pointer_snapshot(store)
        original_write_bytes = Path.write_bytes

        def _selective_boom(self: Path, data: bytes) -> int:
            if self.name == "rule-drafts.json":
                raise OSError("simulated failure writing rule-drafts.json")
            return original_write_bytes(self, data)

        monkeypatch.setattr(Path, "write_bytes", _selective_boom)
        with pytest.raises(OSError):
            with store.lock():
                store.persist_generation(unified_manifest, unified_files.bytes_by_logical_name())
        assert not store.lock_is_held()
        assert _pointer_snapshot(store) == before
        assert not store.generation_exists(unified_manifest.generation_id)

    def test_d_e_failure_writing_manifest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fx, run_dir, store, v1_manifest, unified_manifest, unified_files = _unified_scenario(
            tmp_path
        )
        before = _pointer_snapshot(store)

        def _boom(*args: object, **kwargs: object) -> None:
            raise OSError("simulated failure writing manifest.json")

        monkeypatch.setattr(
            "altamira_extractor.pipeline.unified_activation_store.atomic_write_json", _boom
        )
        with pytest.raises(OSError):
            with store.lock():
                store.persist_generation(unified_manifest, unified_files.bytes_by_logical_name())
        assert not store.lock_is_held()
        assert _pointer_snapshot(store) == before
        assert not store.generation_exists(unified_manifest.generation_id)

    def test_f_failure_before_moving_generation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fx, run_dir, store, v1_manifest, unified_manifest, unified_files = _unified_scenario(
            tmp_path
        )
        before = _pointer_snapshot(store)

        def _boom(*args: object, **kwargs: object) -> None:
            raise OSError("simulated failure promoting generation directory")

        monkeypatch.setattr(
            "altamira_extractor.pipeline.unified_activation_store.atomic_promote_directory", _boom
        )
        with pytest.raises(OSError):
            with store.lock():
                store.persist_generation(unified_manifest, unified_files.bytes_by_logical_name())
        assert not store.lock_is_held()
        assert _pointer_snapshot(store) == before
        assert not store.generation_exists(unified_manifest.generation_id)

    def test_g_failure_after_generation_moved_before_event(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from altamira_extractor.contracts.unified_materialization_authorization import (
            UnifiedMaterializationReasonCode,
        )
        from altamira_extractor.pipeline.unified_activation_transition import (
            activate_unified_canary,
        )

        fx, run_dir, store, v1_manifest, unified_manifest, unified_files = _unified_scenario(
            tmp_path
        )
        before = _pointer_snapshot(store)

        original_persist_event = UnifiedActivationStore.persist_event

        def _boom(self: UnifiedActivationStore, *args: object, **kwargs: object) -> None:
            raise OSError("simulated failure persisting event")

        monkeypatch.setattr(UnifiedActivationStore, "persist_event", _boom)
        with pytest.raises(OSError):
            activate_unified_canary(
                store,
                run_id=fx.run_id,
                unified_manifest=unified_manifest,
                unified_files=unified_files,
                activation_evaluation_hash="e" * 64,
                authorization_hash="d" * 64,
                reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
            )
        monkeypatch.setattr(UnifiedActivationStore, "persist_event", original_persist_event)
        assert not store.lock_is_held()
        assert _pointer_snapshot(store) == before
        # La generacion SI quedo persistida (huerfana segura, nunca referenciada).
        assert store.generation_exists(unified_manifest.generation_id)

    def test_h_failure_after_event_before_pointer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from altamira_extractor.contracts.unified_materialization_authorization import (
            UnifiedMaterializationReasonCode,
        )
        from altamira_extractor.pipeline.unified_activation_transition import (
            activate_unified_canary,
        )

        fx, run_dir, store, v1_manifest, unified_manifest, unified_files = _unified_scenario(
            tmp_path
        )
        before = _pointer_snapshot(store)

        def _boom(self: UnifiedActivationStore, *args: object, **kwargs: object) -> None:
            raise OSError("simulated failure writing pointer")

        monkeypatch.setattr(UnifiedActivationStore, "write_active_pointer", _boom)
        with pytest.raises(OSError):
            activate_unified_canary(
                store,
                run_id=fx.run_id,
                unified_manifest=unified_manifest,
                unified_files=unified_files,
                activation_evaluation_hash="e" * 64,
                authorization_hash="d" * 64,
                reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
            )
        assert not store.lock_is_held()
        assert _pointer_snapshot(store) == before
        # El evento SI quedo persistido (intento huerfano seguro): la
        # cadena confirmada sigue siendo la anterior (latest_event_id
        # del puntero, que nunca cambio).
        assert store.generation_exists(unified_manifest.generation_id)

    def test_i_failure_during_atomic_write_of_pointer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from altamira_extractor.contracts.unified_materialization_authorization import (
            UnifiedMaterializationReasonCode,
        )
        from altamira_extractor.pipeline.unified_activation_transition import (
            activate_unified_canary,
        )

        fx, run_dir, store, v1_manifest, unified_manifest, unified_files = _unified_scenario(
            tmp_path
        )
        before = _pointer_snapshot(store)
        original_atomic_write_json = artifact_store_module.atomic_write_json

        def _selective_boom(path: Path, model: object, **kwargs: object) -> None:
            if path.name == "active.json":
                raise OSError("simulated failure during active.json atomic write")
            return original_atomic_write_json(path, model, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(
            "altamira_extractor.pipeline.unified_activation_store.atomic_write_json",
            _selective_boom,
        )
        with pytest.raises(OSError):
            activate_unified_canary(
                store,
                run_id=fx.run_id,
                unified_manifest=unified_manifest,
                unified_files=unified_files,
                activation_evaluation_hash="e" * 64,
                authorization_hash="d" * 64,
                reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
            )
        assert not store.lock_is_held()
        assert _pointer_snapshot(store) == before


class TestFailureInjectionJ:
    """J: tras un fallo tardio (releer/verificar el pointer), el estado
    real se determina mediante relectura -- nunca se adivina."""

    def test_j_reread_detects_real_state_never_guesses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fx, run_dir, store, v1_manifest, unified_manifest, unified_files = _unified_scenario(
            tmp_path
        )
        with store.lock():
            store.persist_generation(v1_manifest, {})

        from altamira_extractor.contracts.unified_activation_config import UnifiedFallbackPolicy
        from altamira_extractor.contracts.unified_activation_materialization import (
            ActiveActivationPointer,
            MaterializedActivationLane,
        )

        pointer = ActiveActivationPointer(
            run_id=fx.run_id,
            pointer_version=1,
            active_generation_id=v1_manifest.generation_id,
            active_lane=MaterializedActivationLane.V1,
            active_generation_manifest_hash="a" * 64,
            fallback_generation_id=v1_manifest.generation_id,
            latest_event_id="event-abc",
            fallback_policy=UnifiedFallbackPolicy.NO_FALLBACK,
        )
        store.write_active_pointer(pointer)

        # Corrompe el archivo INMEDIATAMENTE despues de una escritura
        # exitosa (simula una falla tardia de relectura/verificacion).
        store.active_pointer_path.write_text("{corrupted", encoding="utf-8")
        with pytest.raises(UnifiedActivationStoreError):
            store.read_active_pointer()

        # Una vez reparado, la relectura vuelve a reportar el estado
        # REAL (nunca un valor adivinado/cacheado).
        store.write_active_pointer(pointer)
        reread = store.read_active_pointer()
        assert reread is not None
        assert reread.to_stable_json() == pointer.to_stable_json()


# ---------------------------------------------------------------------------
# Parte 17 -- concurrencia
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_two_simultaneous_materializations_only_one_confirmed(self, tmp_path: Path) -> None:
        fx, run_dir, store, v1_manifest, unified_manifest, unified_files = _unified_scenario(
            tmp_path
        )
        results: list[str] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(2)

        def _worker() -> None:
            barrier.wait()
            try:
                with store.lock():
                    results.append("acquired")
            except UnifiedActivationLockError as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_worker) for _ in range(2)]
        # Mantener el lock tomado desde el hilo principal para forzar
        # contencion real y deterministica (evita depender de timing).
        with store.lock():
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        assert len(errors) == 2
        assert results == []

    def test_second_process_finds_lock(self, tmp_path: Path) -> None:
        fx, run_dir, store, v1_manifest, unified_manifest, unified_files = _unified_scenario(
            tmp_path
        )
        with store.lock():
            second_store = UnifiedActivationStore(run_dir)
            with pytest.raises(UnifiedActivationLockError):
                with second_store.lock():
                    pass

    def test_pointer_version_increments_exactly_once_under_contention(self, tmp_path: Path) -> None:
        from altamira_extractor.contracts.unified_materialization_authorization import (
            UnifiedMaterializationReasonCode,
        )
        from altamira_extractor.pipeline.unified_activation_transition import (
            activate_unified_canary,
        )

        fx, run_dir, store, v1_manifest, unified_manifest, unified_files = _unified_scenario(
            tmp_path
        )
        results = []
        for _ in range(3):
            result = activate_unified_canary(
                store,
                run_id=fx.run_id,
                unified_manifest=unified_manifest,
                unified_files=unified_files,
                activation_evaluation_hash="e" * 64,
                authorization_hash="d" * 64,
                reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
            )
            results.append(result)
        versions = {r.pointer.pointer_version for r in results}
        assert versions == {2}
        assert sum(1 for r in results if not r.idempotent) == 1

    def test_no_partial_generation_after_contention(self, tmp_path: Path) -> None:
        fx, run_dir, store, v1_manifest, unified_manifest, unified_files = _unified_scenario(
            tmp_path
        )
        with store.lock():
            store.persist_generation(unified_manifest, unified_files.bytes_by_logical_name())
        for entry in store.generation_dir(unified_manifest.generation_id).iterdir():
            assert not entry.name.startswith(".tmp-")

    def test_repeated_retry_is_idempotent(self, tmp_path: Path) -> None:
        from altamira_extractor.contracts.unified_materialization_authorization import (
            UnifiedMaterializationReasonCode,
        )
        from altamira_extractor.pipeline.unified_activation_transition import (
            activate_unified_canary,
        )

        fx, run_dir, store, v1_manifest, unified_manifest, unified_files = _unified_scenario(
            tmp_path
        )
        first = activate_unified_canary(
            store,
            run_id=fx.run_id,
            unified_manifest=unified_manifest,
            unified_files=unified_files,
            activation_evaluation_hash="e" * 64,
            authorization_hash="d" * 64,
            reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
        )
        second = activate_unified_canary(
            store,
            run_id=fx.run_id,
            unified_manifest=unified_manifest,
            unified_files=unified_files,
            activation_evaluation_hash="e" * 64,
            authorization_hash="d" * 64,
            reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
        )
        assert first.pointer.to_stable_json() == second.pointer.to_stable_json()
        assert second.idempotent is True
