"""Integracion dedicada del CLI (Prompt 13c): mismo fixture COBOL, mismo
LLM fake y mismos evidence_id empiricamente confirmados que
tests/e2e_support.py (reutilizados por import, no reescritos) -- pero
conducidos por `typer.testing.CliRunner` a traves de
`ingest -> status -> candidates -> context -> rule -> download`, nunca
por `TestClient`/HTTP. No requiere FastAPI ni Uvicorn levantados; Java 17
y Neo4j 5 reales, cliente LLM fake (nunca un proveedor real)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

import altamira_extractor.cli as cli_module
import altamira_extractor.pipeline.guardrails_applied_stage as guardrails_stage_module
import altamira_extractor.pipeline.rule_drafts_generated_stage as rule_drafts_stage_module

from .e2e_support import (
    build_settings,
    install_dynamic_rule_draft_fake_client,
    install_fake_client,
    require_jar,
    write_package_zip,
)

pytestmark = pytest.mark.integration

runner = CliRunner()


def test_cli_end_to_end_ingest_reaches_completed_and_downloads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    require_jar()
    settings = build_settings(tmp_path)
    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)
    install_dynamic_rule_draft_fake_client(monkeypatch, rule_drafts_stage_module)
    # Ninguna reparacion deberia invocarse: el draft inicial ya pasa el
    # guardrail (mismo fixture que tests/e2e_support.py).
    repair_calls = install_fake_client(monkeypatch, guardrails_stage_module, [])

    zip_path = write_package_zip(tmp_path / "package.zip")

    ingest_result = runner.invoke(cli_module.app, ["ingest", str(zip_path)])
    assert ingest_result.exit_code == 0, ingest_result.stderr
    ingest_lines = ingest_result.stdout.splitlines()
    assert ingest_lines[0].startswith("run_id: ")
    run_id = ingest_lines[0].removeprefix("run_id: ")
    assert ingest_lines[1] == "stage: COMPLETED"
    assert all("FAILED" not in line for line in ingest_lines)
    assert len(repair_calls) == 0

    status_result = runner.invoke(cli_module.app, ["status", run_id])
    assert status_result.exit_code == 0, status_result.stderr
    assert f"run_id: {run_id}" in status_result.stdout
    assert "current_stage: COMPLETED" in status_result.stdout
    assert "  GUARDRAILS_APPLIED: SUCCEEDED" in status_result.stdout
    assert "  COMPLETED: SUCCEEDED" in status_result.stdout

    candidates_result = runner.invoke(cli_module.app, ["candidates", run_id])
    assert candidates_result.exit_code == 0, candidates_result.stderr
    candidate_lines = candidates_result.stdout.strip().splitlines()
    assert len(candidate_lines) == 1
    candidate_fields = candidate_lines[0].split("\t")
    candidate_id = candidate_fields[0]
    assert candidate_fields[1] == "CHECK-SALDO-PARA"
    assert candidate_fields[3] == "R001"

    context_result = runner.invoke(cli_module.app, ["context", run_id, candidate_id])
    assert context_result.exit_code == 0, context_result.stderr
    context_payload = json.loads(context_result.stdout)
    assert context_payload["candidate"]["candidate_id"] == candidate_id
    assert context_payload["decision"]["outcome_code"] == "R001"
    assert context_payload["decision"]["normalized_expression"] is not None

    rule_result = runner.invoke(cli_module.app, ["rule", run_id, candidate_id])
    assert rule_result.exit_code == 0, rule_result.stderr
    rule_payload = json.loads(rule_result.stdout)
    assert rule_payload["candidate_id"] == candidate_id
    assert rule_payload["guardrail"]["verdict"] == "EVIDENCE_VALIDATED"
    assert rule_payload["final_rule_draft"]["evidence_validation_status"] == "EVIDENCE_VALIDATED"
    for forbidden in ("repair_history", "response_hash", "provider", "context_hash"):
        assert forbidden not in rule_result.stdout

    cwd = tmp_path / "download-cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    download_result = runner.invoke(cli_module.app, ["download", run_id])
    assert download_result.exit_code == 0, download_result.stderr
    zip_file = cwd / f"{run_id}-rules.zip"
    assert zip_file.is_file()
    with zipfile.ZipFile(zip_file) as archive:
        names = set(archive.namelist())
        assert "rules-manifest.json" in names
        assert len(names) == 2
        markdown_name = next(n for n in names if n != "rules-manifest.json")
        markdown = archive.read(markdown_name).decode("utf-8")
        assert markdown.startswith("# Regla de saldo negativo\n")
