"""GET /api/runs/{run_id}/download (Prompt 13b): sirve exclusivamente
artifacts/10-rules/ (rules-manifest.json + los .md declarados)."""

from __future__ import annotations

import hashlib
import io
import os
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from altamira_extractor.config import Settings

from .conftest import CANDIDATE_ID, RUN_ID, build_run_completed, build_run_up_to_guardrails_applied


def test_download_returns_valid_zip_with_only_declared_files(
    client: TestClient, settings: Settings
) -> None:
    build_run_completed(settings.runs_dir / RUN_ID, RUN_ID)

    response = client.get(f"/api/runs/{RUN_ID}/download")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert f'filename="{RUN_ID}-rules.zip"' in response.headers["content-disposition"]

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        expected_md = hashlib.sha256(CANDIDATE_ID.encode("utf-8")).hexdigest() + ".md"
        assert names == {"rules-manifest.json", expected_md}
        markdown = archive.read(expected_md).decode("utf-8")
        assert markdown.startswith("# Titulo\n")


def test_download_only_run_files_from_10_rules(client: TestClient, settings: Settings) -> None:
    build_run_completed(settings.runs_dir / RUN_ID, RUN_ID)

    response = client.get(f"/api/runs/{RUN_ID}/download")
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        for forbidden in (
            "run.json",
            "package.zip",
            "input/package.zip",
            "work/extracted",
            "02-canonical",
            "04-semantic-graph.json",
            "06-candidates.json",
            "context-manifest.json",
            "guardrail-manifest.json",
        ):
            assert forbidden not in names


def test_download_stage_not_reached(client: TestClient, settings: Settings) -> None:
    build_run_up_to_guardrails_applied(settings.runs_dir / RUN_ID, RUN_ID)

    response = client.get(f"/api/runs/{RUN_ID}/download")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "stage_not_reached"


def test_download_extra_file_in_10_rules_is_rejected(
    client: TestClient, settings: Settings
) -> None:
    run_dir = settings.runs_dir / RUN_ID
    build_run_completed(run_dir, RUN_ID)
    (run_dir / "artifacts" / "10-rules" / "extra.txt").write_text("intruso", encoding="utf-8")

    response = client.get(f"/api/runs/{RUN_ID}/download")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "artifact_corrupted"


def test_download_subdirectory_in_10_rules_is_rejected(
    client: TestClient, settings: Settings
) -> None:
    run_dir = settings.runs_dir / RUN_ID
    build_run_completed(run_dir, RUN_ID)
    (run_dir / "artifacts" / "10-rules" / "stray-subdir").mkdir()

    response = client.get(f"/api/runs/{RUN_ID}/download")
    assert response.status_code == 500


def test_download_symlink_in_10_rules_is_rejected(client: TestClient, settings: Settings) -> None:
    run_dir = settings.runs_dir / RUN_ID
    build_run_completed(run_dir, RUN_ID)
    target = run_dir / "outside.md"
    target.write_text("# intruso\n", encoding="utf-8")
    try:
        os.symlink(target, run_dir / "artifacts" / "10-rules" / "sneaky.md")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks no soportados en este entorno")

    response = client.get(f"/api/runs/{RUN_ID}/download")
    assert response.status_code == 500


def test_download_hash_mismatch_is_rejected(client: TestClient, settings: Settings) -> None:
    run_dir = settings.runs_dir / RUN_ID
    build_run_completed(run_dir, RUN_ID)
    filename = hashlib.sha256(CANDIDATE_ID.encode("utf-8")).hexdigest() + ".md"
    (run_dir / "artifacts" / "10-rules" / filename).write_bytes(b"# Tampered content\n")

    response = client.get(f"/api/runs/{RUN_ID}/download")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "artifact_corrupted"


def test_download_temp_file_is_cleaned_up(client: TestClient, settings: Settings) -> None:
    import tempfile

    build_run_completed(settings.runs_dir / RUN_ID, RUN_ID)

    response = client.get(f"/api/runs/{RUN_ID}/download")
    assert response.status_code == 200

    leftovers = list(Path(tempfile.gettempdir()).glob(f".{RUN_ID}-rules-*"))
    assert leftovers == []
