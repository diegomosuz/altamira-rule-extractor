"""Deteccion de paquetes duplicados por hash exacto (Fase v1.17.1,
Feature 6): POST /api/runs (misma orquestacion que POST /ui/runs, via
`api/run_actions.py::create_and_submit_run`). `run_ingestion` real
(JAR/Neo4j) se reemplaza por un stub sincrono que marca COMPLETED de
inmediato -- nunca se ejecuta un pipeline real en esta suite."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import altamira_extractor.api.run_actions as run_actions_module
from altamira_extractor.config import Settings
from altamira_extractor.contracts.enums import PipelineStage, StageStatus
from altamira_extractor.contracts.run_state import RunState, StageExecution

from ..pipeline.conftest import build_valid_package_zip


def _instant_completed_ingestion(
    source_zip: Path, settings: Settings, run_id: str | None = None
) -> RunState:
    assert run_id is not None
    run_dir = settings.runs_dir / run_id
    state = RunState.model_validate_json((run_dir / "run.json").read_text(encoding="utf-8"))
    now = datetime.now(UTC)
    stages = list(state.stages)
    stages.append(
        StageExecution(
            stage=PipelineStage.COMPLETED,
            status=StageStatus.SUCCEEDED,
            started_at=now,
            finished_at=now,
            duration_seconds=0.0,
        )
    )
    state = state.model_copy(update={"stages": stages, "current_stage": PipelineStage.COMPLETED})
    (run_dir / "run.json").write_text(state.to_stable_json(), encoding="utf-8")
    return state


def _wait_until(predicate: object, *, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():  # type: ignore[operator]
            return True
        time.sleep(0.02)
    return False


def _upload(client: TestClient, zip_path: Path, filename: str) -> dict:
    with zip_path.open("rb") as fh:
        response = client.post("/api/runs", files={"file": (filename, fh, "application/zip")})
    assert response.status_code == 202, response.text
    return response.json()


def _wait_completed(client: TestClient, run_id: str) -> dict:
    """Espera a que el run REAL (nunca una referencia duplicada, que
    completa de forma sincronica dentro de la propia request) alcance
    COMPLETED via el stub de `run_ingestion` corriendo en el executor
    en background."""

    def _is_completed() -> bool:
        detail = client.get(f"/api/runs/{run_id}").json()
        return detail["current_stage"] == "COMPLETED"

    assert _wait_until(_is_completed, timeout=5.0)
    return client.get(f"/api/runs/{run_id}").json()


@pytest.fixture(autouse=True)
def _stub_run_ingestion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(  # type: ignore[attr-defined]
        run_actions_module, "run_ingestion", _instant_completed_ingestion
    )


def test_same_exact_bytes_produce_same_hash(
    client: TestClient, settings: Settings, tmp_path: Path
) -> None:
    zip_a = build_valid_package_zip(tmp_path / "a.zip")
    zip_b_bytes = zip_a.read_bytes()
    zip_b = tmp_path / "b.zip"
    zip_b.write_bytes(zip_b_bytes)

    result_a = _upload(client, zip_a, "a.zip")
    _wait_completed(client, result_a["run_id"])
    detail_a = client.get(f"/api/runs/{result_a['run_id']}").json()

    result_b = _upload(client, zip_b, "b.zip")
    detail_b = client.get(f"/api/runs/{result_b['run_id']}").json()
    assert detail_a["source_package_hash"] == detail_b["source_package_hash"]


def test_different_filename_same_bytes_is_treated_as_duplicate(
    client: TestClient, settings: Settings, tmp_path: Path
) -> None:
    zip_path = build_valid_package_zip(tmp_path / "original.zip")
    same_bytes_zip = tmp_path / "renamed.zip"
    same_bytes_zip.write_bytes(zip_path.read_bytes())

    first = _upload(client, zip_path, "original.zip")
    _wait_completed(client, first["run_id"])

    second = _upload(client, same_bytes_zip, "renamed.zip")
    second_run_dir = settings.runs_dir / second["run_id"]
    second_state = RunState.model_validate_json(
        (second_run_dir / "run.json").read_text(encoding="utf-8")
    )
    assert second_state.duplicate_of_run_id == first["run_id"]

    ui_response = client.get(f"/ui/runs/{second['run_id']}")
    assert "YA PROCESADO" in ui_response.text
    assert first["run_id"] in ui_response.text


def test_same_filename_different_bytes_is_not_a_duplicate(
    client: TestClient, settings: Settings, tmp_path: Path
) -> None:
    zip_a = build_valid_package_zip(tmp_path / "a.zip")
    zip_b = build_valid_package_zip(
        tmp_path / "a2.zip", extra={"01-codigo/EXTRA.txt": b"contenido distinto"}
    )

    first = _upload(client, zip_a, "pkg.zip")
    _wait_completed(client, first["run_id"])

    second = _upload(client, zip_b, "pkg.zip")
    second_run_dir = settings.runs_dir / second["run_id"]
    second_state = RunState.model_validate_json(
        (second_run_dir / "run.json").read_text(encoding="utf-8")
    )
    assert second_state.duplicate_of_run_id is None
    assert second["run_id"] != first["run_id"]


def test_duplicate_upload_does_not_invoke_pipeline_again(
    client: TestClient, settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_count = {"n": 0}

    def _counting_ingestion(
        source_zip: Path, s: Settings, run_id: str | None = None
    ) -> RunState:
        call_count["n"] += 1
        return _instant_completed_ingestion(source_zip, s, run_id=run_id)

    monkeypatch.setattr(run_actions_module, "run_ingestion", _counting_ingestion)  # type: ignore[attr-defined]

    zip_path = build_valid_package_zip(tmp_path / "a.zip")
    same_bytes = tmp_path / "b.zip"
    same_bytes.write_bytes(zip_path.read_bytes())

    first = _upload(client, zip_path, "a.zip")
    _wait_completed(client, first["run_id"])
    _upload(client, same_bytes, "b.zip")

    assert call_count["n"] == 1


def test_cleaning_authoritative_run_allows_identical_package_to_run_again(
    client: TestClient, settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_count = {"n": 0}

    def _counting_ingestion(
        source_zip: Path, s: Settings, run_id: str | None = None
    ) -> RunState:
        call_count["n"] += 1
        return _instant_completed_ingestion(source_zip, s, run_id=run_id)

    monkeypatch.setattr(run_actions_module, "run_ingestion", _counting_ingestion)  # type: ignore[attr-defined]

    zip_path = build_valid_package_zip(tmp_path / "a.zip")
    same_bytes = tmp_path / "b.zip"
    same_bytes.write_bytes(zip_path.read_bytes())

    first = _upload(client, zip_path, "a.zip")
    _wait_completed(client, first["run_id"])

    duplicate = _upload(client, same_bytes, "b.zip")
    assert call_count["n"] == 1

    # limpiar el job autoritativo (Feature 5) debe arrastrar la
    # referencia duplicada (Feature 6, "Clean Job + Duplicate Interaction")
    clean_response = client.post(
        f"/ui/runs/{first['run_id']}/clean", headers={"Origin": "http://testserver"}
    )
    assert clean_response.status_code in (200, 303)
    assert not (settings.runs_dir / first["run_id"]).exists()
    assert not (settings.runs_dir / duplicate["run_id"]).exists()

    # el mismo ZIP ahora se procesa como una ejecucion nueva real.
    third = _upload(client, same_bytes, "b.zip")
    third_run_dir = settings.runs_dir / third["run_id"]
    third_state = RunState.model_validate_json(
        (third_run_dir / "run.json").read_text(encoding="utf-8")
    )
    assert third_state.duplicate_of_run_id is None
    assert call_count["n"] == 2


def _instant_failed_ingestion(
    source_zip: Path, settings: Settings, run_id: str | None = None
) -> RunState:
    """Falla en VALIDATED (posterior a RECEIVED, que ya establecio un
    source_package_hash real -- ver docstring de
    `api/duplicate_detection.py::find_authoritative_run` sobre por que
    un fallo EN RECEIVED nunca llega a esta situacion)."""
    assert run_id is not None
    run_dir = settings.runs_dir / run_id
    state = RunState.model_validate_json((run_dir / "run.json").read_text(encoding="utf-8"))
    now = datetime.now(UTC)
    stages = list(state.stages)
    stages.append(
        StageExecution(
            stage=PipelineStage.VALIDATED,
            status=StageStatus.FAILED,
            started_at=now,
            finished_at=now,
            duration_seconds=0.0,
            error="fallo simulado de VALIDATED",
        )
    )
    state = state.model_copy(update={"stages": stages, "current_stage": PipelineStage.FAILED})
    (run_dir / "run.json").write_text(state.to_stable_json(), encoding="utf-8")
    return state


def test_duplicate_of_a_failed_run_is_never_labeled_as_successfully_processed(
    client: TestClient, settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CHECK 7 del pre-commit review: un duplicado de una ejecucion
    original FALLIDA nunca debe mostrarse como "YA PROCESADO" (implica
    exito que no ocurrio); debe indicar claramente que la ejecucion
    original fallo, sin dejar de exponer el concepto de referencia/
    duplicado ni de bloquear que el analista reanude o limpie la
    ejecucion original real."""
    monkeypatch.setattr(run_actions_module, "run_ingestion", _instant_failed_ingestion)  # type: ignore[attr-defined]

    zip_path = build_valid_package_zip(tmp_path / "a.zip")
    same_bytes = tmp_path / "b.zip"
    same_bytes.write_bytes(zip_path.read_bytes())

    first = _upload(client, zip_path, "a.zip")

    def _is_failed() -> bool:
        detail = client.get(f"/api/runs/{first['run_id']}").json()
        return detail["current_stage"] == "FAILED"

    assert _wait_until(_is_failed, timeout=5.0)

    duplicate = _upload(client, same_bytes, "b.zip")
    dup_state = RunState.model_validate_json(
        (settings.runs_dir / duplicate["run_id"] / "run.json").read_text(encoding="utf-8")
    )
    assert dup_state.duplicate_of_run_id == first["run_id"]

    ui_response = client.get(f"/ui/runs/{duplicate['run_id']}")
    assert "YA PROCESADO" not in ui_response.text
    assert "fallida" in ui_response.text.lower()

    # el analista sigue pudiendo reanudar o limpiar la ejecucion ORIGINAL
    # real desde su propia pagina de detalle.
    original_page = client.get(f"/ui/runs/{first['run_id']}")
    assert "Reanudar" in original_page.text


def test_duplicate_reference_cleanup_does_not_affect_unrelated_hash(
    client: TestClient, settings: Settings, tmp_path: Path
) -> None:
    zip_a = build_valid_package_zip(tmp_path / "a.zip")
    same_bytes_a = tmp_path / "a-dup.zip"
    same_bytes_a.write_bytes(zip_a.read_bytes())
    zip_b = build_valid_package_zip(
        tmp_path / "b.zip", extra={"01-codigo/EXTRA.txt": b"paquete no relacionado"}
    )

    run_a = _upload(client, zip_a, "a.zip")
    _wait_completed(client, run_a["run_id"])
    dup_a = _upload(client, same_bytes_a, "a-dup.zip")

    run_b = _upload(client, zip_b, "b.zip")
    _wait_completed(client, run_b["run_id"])

    client.post(
        f"/ui/runs/{run_a['run_id']}/clean", headers={"Origin": "http://testserver"}
    )

    assert not (settings.runs_dir / run_a["run_id"]).exists()
    assert not (settings.runs_dir / dup_a["run_id"]).exists()
    assert (settings.runs_dir / run_b["run_id"]).is_dir()


def test_cleaning_a_duplicate_reference_never_deletes_the_authoritative_run(
    client: TestClient, settings: Settings, tmp_path: Path
) -> None:
    """CHECK 4 del pre-commit review (operacion inversa de Feature 6):
    limpiar el registro de referencia B (que apunta a A via
    `duplicate_of_run_id`) debe eliminar UNICAMENTE B -- nunca debe
    seguir `B.duplicate_of_run_id` para borrar A. `_cascade_delete_
    duplicate_references` (run_cleanup.py) solo busca en la direccion
    CONTRARIA (quien apunta a MI run_id), nunca sigue el campo del run
    que se esta limpiando."""
    zip_a = build_valid_package_zip(tmp_path / "a.zip")
    same_bytes = tmp_path / "a-dup.zip"
    same_bytes.write_bytes(zip_a.read_bytes())

    run_a = _upload(client, zip_a, "a.zip")
    _wait_completed(client, run_a["run_id"])
    run_b = _upload(client, same_bytes, "a-dup.zip")  # referencia -> A

    run_a_dir = settings.runs_dir / run_a["run_id"]
    run_b_dir = settings.runs_dir / run_b["run_id"]
    assert run_a_dir.is_dir() and run_b_dir.is_dir()

    clean_response = client.post(
        f"/ui/runs/{run_b['run_id']}/clean",
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert clean_response.status_code == 303

    # B desaparecio; A permanece COMPLETAMENTE intacto (directorio,
    # run.json, y por transitividad su propio dueno de Neo4j, que
    # `_clean_neo4j_if_owned` ni siquiera considero: B nunca posee
    # Neo4j por diseno).
    assert not run_b_dir.exists()
    assert run_a_dir.is_dir()
    assert (run_a_dir / "run.json").is_file()
    a_state_after = RunState.model_validate_json(
        (run_a_dir / "run.json").read_text(encoding="utf-8")
    )
    assert a_state_after.run_id == run_a["run_id"]
    assert a_state_after.current_stage == PipelineStage.COMPLETED

    # el mismo hash sigue resolviendo a A como "ya procesado" (A sigue
    # siendo la unica ejecucion autoritativa real para este hash).
    still_a_ref = client.get(f"/api/runs/{run_a['run_id']}").json()
    assert still_a_ref["current_stage"] == "COMPLETED"


def test_authoritative_clean_removes_all_dependent_duplicate_references(
    client: TestClient, settings: Settings, tmp_path: Path
) -> None:
    """CHECK 5 del pre-commit review: multiples referencias (B, C -> A)
    se eliminan TODAS al limpiar A; un run D no relacionado (hash
    distinto) permanece intacto."""
    zip_a = build_valid_package_zip(tmp_path / "a.zip")
    same_bytes_1 = tmp_path / "a-dup1.zip"
    same_bytes_1.write_bytes(zip_a.read_bytes())
    same_bytes_2 = tmp_path / "a-dup2.zip"
    same_bytes_2.write_bytes(zip_a.read_bytes())
    zip_d = build_valid_package_zip(
        tmp_path / "d.zip", extra={"01-codigo/EXTRA.txt": b"paquete D no relacionado"}
    )

    run_a = _upload(client, zip_a, "a.zip")
    _wait_completed(client, run_a["run_id"])
    run_b = _upload(client, same_bytes_1, "a-dup1.zip")
    run_c = _upload(client, same_bytes_2, "a-dup2.zip")
    run_d = _upload(client, zip_d, "d.zip")
    _wait_completed(client, run_d["run_id"])

    assert run_b["run_id"] != run_c["run_id"]

    client.post(f"/ui/runs/{run_a['run_id']}/clean", headers={"Origin": "http://testserver"})

    assert not (settings.runs_dir / run_a["run_id"]).exists()
    assert not (settings.runs_dir / run_b["run_id"]).exists()
    assert not (settings.runs_dir / run_c["run_id"]).exists()
    assert (settings.runs_dir / run_d["run_id"]).is_dir()

    # el mismo paquete (hash de A) ahora puede procesarse desde cero.
    fresh = _upload(client, same_bytes_1, "a-dup1.zip")
    fresh_state = RunState.model_validate_json(
        (settings.runs_dir / fresh["run_id"] / "run.json").read_text(encoding="utf-8")
    )
    assert fresh_state.duplicate_of_run_id is None
