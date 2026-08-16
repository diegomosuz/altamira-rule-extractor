"""POST /ui/runs/{run_id}/clean ("Limpiar job", Fase v1.17.1, Feature 5).

Nunca requiere JAR/Neo4j real: los runs de prueba nunca alcanzan
SEMANTIC_GRAPH_LOADED (ver `_reached_semantic_graph_loaded` en
`api/run_cleanup.py`), asi que la limpieza de Neo4j es un no-op
estructural salvo en las pruebas que monkeypatchean `Neo4jRepository`
explicitamente para probar el scoping por ownership."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import altamira_extractor.api.run_cleanup as run_cleanup_module
from altamira_extractor.config import Settings
from altamira_extractor.contracts.enums import PipelineStage, StageStatus
from altamira_extractor.pipeline.neo4j_repository import ActiveGraphLoad

from .conftest import (
    HASH_A,
    RUN_ID,
    build_run_completed,
    build_run_state,
    build_run_up_to_guardrails_applied,
    stage_execution,
    write_input_package_zip,
    write_run_state,
)

SAME_ORIGIN = "http://testserver"


def test_clean_job_is_never_a_get_request(client: TestClient, settings: Settings) -> None:
    build_run_completed(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.get(f"/ui/runs/{RUN_ID}/clean")
    assert response.status_code == 405


def test_clean_completed_run_removes_filesystem_data(
    client: TestClient, settings: Settings
) -> None:
    run_dir = settings.runs_dir / RUN_ID
    build_run_completed(run_dir, RUN_ID)
    assert run_dir.is_dir()

    response = client.post(
        f"/ui/runs/{RUN_ID}/clean", headers={"Origin": SAME_ORIGIN}, follow_redirects=False
    )
    assert response.status_code == 303
    assert not run_dir.exists()


def test_clean_failed_run_removes_filesystem_data(client: TestClient, settings: Settings) -> None:
    run_dir = settings.runs_dir / RUN_ID
    stages = [
        stage_execution(PipelineStage.RECEIVED, StageStatus.SUCCEEDED),
        stage_execution(PipelineStage.VALIDATED, StageStatus.FAILED),
    ]
    state = build_run_state(RUN_ID, stages=stages, current_stage=PipelineStage.FAILED)
    write_run_state(run_dir, state)
    write_input_package_zip(run_dir)

    response = client.post(
        f"/ui/runs/{RUN_ID}/clean", headers={"Origin": SAME_ORIGIN}, follow_redirects=False
    )
    assert response.status_code == 303
    assert not run_dir.exists()


def test_clean_running_job_is_rejected_server_side(client: TestClient, settings: Settings) -> None:
    run_dir = settings.runs_dir / RUN_ID
    build_run_up_to_guardrails_applied(run_dir, RUN_ID)

    executor = client.app.state.executor  # type: ignore[attr-defined]
    block = threading.Event()
    started = threading.Event()

    def _blocking() -> None:
        started.set()
        block.wait(timeout=5)

    try:
        executor.try_submit(RUN_ID, _blocking)
        assert started.wait(timeout=5)

        response = client.post(
            f"/ui/runs/{RUN_ID}/clean", headers={"Origin": SAME_ORIGIN}, follow_redirects=False
        )
        assert response.status_code == 409
        assert run_dir.is_dir()  # nunca se toco el filesystem
    finally:
        block.set()


def test_clean_nonexistent_run_returns_404(client: TestClient) -> None:
    response = client.post(
        "/ui/runs/20260101T000000000000-ffffffff/clean",
        headers={"Origin": SAME_ORIGIN},
        follow_redirects=False,
    )
    assert response.status_code == 404


def test_repeated_clean_is_idempotent_never_corrupts_state(
    client: TestClient, settings: Settings
) -> None:
    run_dir = settings.runs_dir / RUN_ID
    build_run_completed(run_dir, RUN_ID)

    first = client.post(
        f"/ui/runs/{RUN_ID}/clean", headers={"Origin": SAME_ORIGIN}, follow_redirects=False
    )
    assert first.status_code == 303
    assert not run_dir.exists()

    second = client.post(
        f"/ui/runs/{RUN_ID}/clean", headers={"Origin": SAME_ORIGIN}, follow_redirects=False
    )
    assert second.status_code == 404
    assert not run_dir.exists()


def test_cleaning_one_run_leaves_unrelated_run_intact(
    client: TestClient, settings: Settings
) -> None:
    other_run_id = "20260102T000000000000-bbbbbbbb"
    build_run_completed(settings.runs_dir / RUN_ID, RUN_ID)
    build_run_completed(settings.runs_dir / other_run_id, other_run_id)

    response = client.post(
        f"/ui/runs/{RUN_ID}/clean", headers={"Origin": SAME_ORIGIN}, follow_redirects=False
    )
    assert response.status_code == 303
    assert not (settings.runs_dir / RUN_ID).exists()
    assert (settings.runs_dir / other_run_id).is_dir()


def test_ui_runs_list_no_longer_shows_cleaned_job(client: TestClient, settings: Settings) -> None:
    build_run_completed(settings.runs_dir / RUN_ID, RUN_ID)
    client.post(f"/ui/runs/{RUN_ID}/clean", headers={"Origin": SAME_ORIGIN})

    response = client.get("/ui/runs")
    assert RUN_ID not in response.text


def test_runs_list_offers_clean_job_with_confirmation_dialog(
    client: TestClient, settings: Settings
) -> None:
    build_run_completed(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.get("/ui/runs")
    assert "Limpiar job" in response.text
    assert "<dialog" in response.text
    assert "Cancelar" in response.text
    assert "data-clean-job-trigger" in response.text
    assert 'method="post"' in response.text


# --- Ownership de Neo4j: scoping preciso, nunca un DETACH DELETE amplio ---


class _FakeNeo4jRepository:
    """Doble de prueba minimo: simula un `AltamiraGraphLoad` activo con un
    `source_package_hash` FIJO, distinto o igual al del run bajo prueba
    segun el escenario. Registra si `delete_managed_graph` fue invocado."""

    active_hash: str | None = None
    delete_calls: list[bool] = []  # noqa: RUF012 - reinicializado en cada test via monkeypatch

    def __init__(self) -> None:
        self.closed = False

    @classmethod
    def connect(cls, settings: Settings) -> _FakeNeo4jRepository:
        return cls()

    def read_active_graph_load(self) -> ActiveGraphLoad | None:
        if type(self).active_hash is None:
            return None
        return ActiveGraphLoad(
            semantic_graph_hash="g" * 64,
            source_package_hash=type(self).active_hash,
            node_count=1,
            relationship_count=0,
            server_version="5.26.0",
            database="neo4j",
        )

    def delete_managed_graph(self) -> None:
        type(self).delete_calls.append(True)

    def close(self) -> None:
        self.closed = True


def _build_run_with_semantic_graph_loaded(run_dir: Path, run_id: str, source_hash: str) -> None:
    stages = [
        stage_execution(PipelineStage.RECEIVED, StageStatus.SUCCEEDED),
        stage_execution(PipelineStage.SEMANTIC_GRAPH_LOADED, StageStatus.SUCCEEDED),
        stage_execution(PipelineStage.COMPLETED, StageStatus.SUCCEEDED),
    ]
    from altamira_extractor.contracts.run_state import RunState

    created = datetime.now(UTC)
    state = RunState(
        run_id=run_id,
        package_filename="input/package.zip",
        source_package_hash=source_hash,
        current_stage=PipelineStage.COMPLETED,
        stages=stages,
        created_at=created,
        updated_at=created,
    )
    write_run_state(run_dir, state)
    write_input_package_zip(run_dir)


def test_neo4j_is_wiped_only_when_run_owns_the_active_graph(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = settings.runs_dir / RUN_ID
    _build_run_with_semantic_graph_loaded(run_dir, RUN_ID, HASH_A)

    _FakeNeo4jRepository.active_hash = HASH_A
    _FakeNeo4jRepository.delete_calls = []
    monkeypatch.setattr(run_cleanup_module, "Neo4jRepository", _FakeNeo4jRepository)

    response = client.post(
        f"/ui/runs/{RUN_ID}/clean", headers={"Origin": SAME_ORIGIN}, follow_redirects=False
    )
    assert response.status_code == 303
    assert _FakeNeo4jRepository.delete_calls == [True]


def test_neo4j_is_never_wiped_when_another_run_now_owns_the_active_graph(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V1 es mono-tenant: si otro paquete ya cargo su propio grafo
    despues, limpiar ESTE run (con un hash distinto al activo) nunca
    debe tocar Neo4j -- ese grafo pertenece al otro run."""
    run_dir = settings.runs_dir / RUN_ID
    _build_run_with_semantic_graph_loaded(run_dir, RUN_ID, HASH_A)

    other_hash = "b" * 64
    _FakeNeo4jRepository.active_hash = other_hash
    _FakeNeo4jRepository.delete_calls = []
    monkeypatch.setattr(run_cleanup_module, "Neo4jRepository", _FakeNeo4jRepository)

    response = client.post(
        f"/ui/runs/{RUN_ID}/clean", headers={"Origin": SAME_ORIGIN}, follow_redirects=False
    )
    assert response.status_code == 303
    assert _FakeNeo4jRepository.delete_calls == []
    assert not run_dir.exists()  # filesystem si se limpia; Neo4j no se toco


def test_neo4j_unreachable_aborts_before_touching_filesystem(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from altamira_extractor.pipeline.errors import Neo4jUnavailableError

    run_dir = settings.runs_dir / RUN_ID
    _build_run_with_semantic_graph_loaded(run_dir, RUN_ID, HASH_A)

    class _UnreachableRepository(_FakeNeo4jRepository):
        def read_active_graph_load(self) -> ActiveGraphLoad | None:
            raise Neo4jUnavailableError("el servidor Neo4j no esta disponible")

    monkeypatch.setattr(run_cleanup_module, "Neo4jRepository", _UnreachableRepository)

    response = client.post(
        f"/ui/runs/{RUN_ID}/clean", headers={"Origin": SAME_ORIGIN}, follow_redirects=False
    )
    assert response.status_code == 502
    assert run_dir.is_dir()  # nunca se llego a tocar el filesystem
