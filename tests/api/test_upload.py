"""POST /api/runs (Prompt 13b): recepcion segura del upload multipart."""

from __future__ import annotations

import io
import time
from pathlib import Path

from fastapi.testclient import TestClient

from altamira_extractor.config import Settings
from altamira_extractor.contracts.run_state import RunState

from ..e2e_support import write_disabled_dev_security_config
from ..pipeline.conftest import build_valid_package_zip


def _wait_for_stage(
    client: TestClient, run_id: str, stage: str, *, timeout: float = 5.0
) -> dict:
    deadline = time.monotonic() + timeout
    last_body: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        if response.status_code == 200:
            last_body = response.json()
            if last_body["current_stage"] == stage:
                return last_body
        time.sleep(0.02)
    return last_body


def test_upload_valid_zip_returns_202(client: TestClient, tmp_path: Path) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    with zip_path.open("rb") as fh:
        response = client.post("/api/runs", files={"file": ("package.zip", fh, "application/zip")})

    assert response.status_code == 202
    body = response.json()
    assert body["current_stage"] == "RECEIVED"
    assert body["status"] == "accepted"
    assert body["status_url"] == f"/api/runs/{body['run_id']}"


def test_upload_missing_file_field_is_422(client: TestClient) -> None:
    response = client.post("/api/runs", data={"not_file": "x"})
    assert response.status_code == 422


def test_upload_empty_file_is_400(client: TestClient) -> None:
    response = client.post("/api/runs", files={"file": ("package.zip", b"", "application/zip")})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "empty_upload"


def test_upload_exceeds_limit_is_413(tmp_path: Path) -> None:
    from altamira_extractor.api.app import create_app

    settings = Settings(
        data_dir=tmp_path / "data",
        runs_dir=tmp_path / "data" / "runs",
        incoming_dir=tmp_path / "data" / "incoming",
        max_package_size_bytes=10,
        security_config_path=write_disabled_dev_security_config(tmp_path),
    )
    payload = b"x" * 1000

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/runs", files={"file": ("package.zip", payload, "application/zip")}
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "upload_too_large"
    # ningun temporal parcial queda en incoming_dir
    assert list(settings.incoming_dir.glob("*")) == []


def test_upload_filename_traversal_does_not_influence_path(
    client: TestClient, settings: Settings, tmp_path: Path
) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    with zip_path.open("rb") as fh:
        response = client.post(
            "/api/runs",
            files={"file": ("../../../../etc/evil.zip", fh, "application/zip")},
        )

    assert response.status_code == 202
    run_id = response.json()["run_id"]
    # El run se creo exactamente en runs_dir/{run_id}, nunca en una ruta
    # derivada del filename del cliente.
    assert (settings.runs_dir / run_id / "input" / "package.zip").is_file()
    assert not (settings.runs_dir.parent / "etc").exists()


def test_upload_misleading_content_type_is_ignored(client: TestClient, tmp_path: Path) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    with zip_path.open("rb") as fh:
        response = client.post(
            "/api/runs", files={"file": ("package.zip", fh, "text/plain")}
        )
    assert response.status_code == 202


def test_received_persisted_before_response(
    client: TestClient, settings: Settings, tmp_path: Path
) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    with zip_path.open("rb") as fh:
        response = client.post("/api/runs", files={"file": ("package.zip", fh, "application/zip")})

    run_id = response.json()["run_id"]
    state = RunState.model_validate_json(
        (settings.runs_dir / run_id / "run.json").read_text(encoding="utf-8")
    )
    received = next(s for s in state.stages if s.stage.value == "RECEIVED")
    assert received.status.value == "SUCCEEDED"


def test_get_run_immediately_after_202_never_404s(client: TestClient, tmp_path: Path) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    with zip_path.open("rb") as fh:
        response = client.post("/api/runs", files={"file": ("package.zip", fh, "application/zip")})
    run_id = response.json()["run_id"]

    get_response = client.get(f"/api/runs/{run_id}")
    assert get_response.status_code == 200


def test_upload_temp_file_removed_from_incoming_dir(
    client: TestClient, settings: Settings, tmp_path: Path
) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    with zip_path.open("rb") as fh:
        client.post("/api/runs", files={"file": ("package.zip", fh, "application/zip")})

    assert list(settings.incoming_dir.glob("*.upload")) == []


def test_invalid_zip_ends_up_failed_at_validated(client: TestClient, tmp_path: Path) -> None:
    garbage = io.BytesIO(b"this is not a zip file at all")
    response = client.post("/api/runs", files={"file": ("package.zip", garbage, "application/zip")})
    assert response.status_code == 202
    run_id = response.json()["run_id"]

    body = _wait_for_stage(client, run_id, "FAILED")
    assert body["current_stage"] == "FAILED"
    validated_execution = next(s for s in body["stages"] if s["stage"] == "VALIDATED")
    assert validated_execution["status"] == "FAILED"


def test_prepare_received_failure_leaves_no_active_run_and_cleans_temp(
    settings: Settings, tmp_path: Path, monkeypatch: object
) -> None:
    import altamira_extractor.api.run_actions as run_actions_module
    from altamira_extractor.api.app import create_app

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("fallo simulado antes de programar en el executor")

    monkeypatch.setattr(run_actions_module, "prepare_received", _boom)  # type: ignore[attr-defined]

    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    # raise_server_exceptions=False: se quiere observar la respuesta 500
    # real que veria un cliente HTTP, no la excepcion Python re-lanzada
    # (comportamiento por defecto de TestClient, pensado para depurar).
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        with zip_path.open("rb") as fh:
            response = client.post(
                "/api/runs", files={"file": ("package.zip", fh, "application/zip")}
            )

    assert response.status_code == 500
    # nunca se genero un run_id (prepare_received nunca retorno), asi que
    # no hay absolutamente nada que pudiera verse como "activo": ni
    # directorio de run, ni entrada en el registro del executor.
    assert not settings.runs_dir.exists() or list(settings.runs_dir.iterdir()) == []
    # el temporal de incoming_dir se elimina tambien ante este fallo
    # (el finally de create_run corre incluso cuando prepare_received
    # lanza una excepcion inesperada).
    assert list(settings.incoming_dir.glob("*.upload")) == []
