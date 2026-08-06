"""Tests unitarios del CLI Typer (Prompt 13c): sin Docker, sin JAR, sin
Neo4j y sin FastAPI -- `run_ingestion` siempre queda monkeypatcheado por
un stub controlado (el CLI lo llama de forma sincrona y bloqueante,
a diferencia de la API que lo programa en background). El integration
real end-to-end vive en tests/test_cli_integration.py."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

import altamira_extractor.cli as cli_module
from altamira_extractor.config import Settings
from altamira_extractor.contracts.enums import PipelineStage, StageStatus
from altamira_extractor.contracts.guardrail import GuardrailViolation
from altamira_extractor.contracts.run_state import RunState

from .api.conftest import (
    CANDIDATE_ID,
    HASH_A,
    RUN_ID,
    build_context_package,
    build_run_completed,
    build_run_state,
    build_run_up_to_candidates_detected,
    build_run_up_to_contexts_built,
    build_run_up_to_guardrails_applied,
    stage_execution,
    write_candidates_artifact,
    write_context_directory,
    write_input_package_zip,
    write_run_state,
)

runner = CliRunner()

NONEXISTENT_RUN_ID = "20260101T000000000000-ffffffff"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        runs_dir=tmp_path / "data" / "runs",
        incoming_dir=tmp_path / "data" / "incoming",
    )


@pytest.fixture
def patched_settings(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> Settings:
    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)
    return settings


def _stub_run_ingestion(
    monkeypatch: pytest.MonkeyPatch, final_state: RunState
) -> list[tuple[Path, str | None]]:
    """Reemplaza `cli.run_ingestion` (nunca el `pipeline.runner.run_ingestion`
    original: el CLI ya se lo importo por nombre a su propio modulo) por un
    stub deterministico -- evita depender del JAR/Neo4j reales en tests
    unitarios (`.claude/rules/testing.md`: "Tests unitarios sin Docker")."""
    calls: list[tuple[Path, str | None]] = []

    def _fake(source_zip: Path, settings: Settings, run_id: str | None = None) -> RunState:
        calls.append((source_zip, run_id))
        return final_state

    monkeypatch.setattr(cli_module, "run_ingestion", _fake)
    return calls


def _completed_state(run_id: str = RUN_ID) -> RunState:
    stages = [
        stage_execution(PipelineStage.RECEIVED, StageStatus.SUCCEEDED),
        stage_execution(PipelineStage.VALIDATED, StageStatus.SUCCEEDED),
        stage_execution(PipelineStage.COMPLETED, StageStatus.SUCCEEDED),
    ]
    return build_run_state(run_id, stages=stages, current_stage=PipelineStage.COMPLETED)


def _failed_state(run_id: str = RUN_ID) -> RunState:
    stages = [
        stage_execution(PipelineStage.RECEIVED, StageStatus.SUCCEEDED),
        stage_execution(PipelineStage.VALIDATED, StageStatus.FAILED),
    ]
    return build_run_state(run_id, stages=stages, current_stage=PipelineStage.FAILED)


def _expected_summary_lines(state: RunState) -> list[str]:
    lines = [f"run_id: {state.run_id}", f"stage: {state.current_stage.value}"]
    for stage in state.stages:
        line = f"  {stage.stage.value}: {stage.status.value}"
        if stage.error:
            line += f" ({stage.error})"
        lines.append(line)
    return lines


# ---------------------------------------------------------------------------
# ingest: regresion (captura el comportamiento EXISTENTE antes de tocar nada)
# ---------------------------------------------------------------------------


def test_ingest_package_positional_completed_exit_0(
    monkeypatch: pytest.MonkeyPatch, patched_settings: Settings, tmp_path: Path
) -> None:
    package = tmp_path / "package.zip"
    package.write_bytes(b"dummy")
    final_state = _completed_state()
    calls = _stub_run_ingestion(monkeypatch, final_state)

    result = runner.invoke(cli_module.app, ["ingest", str(package)])

    assert result.exit_code == 0, result.stderr
    assert result.stdout.splitlines() == _expected_summary_lines(final_state)
    assert calls == [(package, None)]


def test_ingest_run_id_option_forwarded(
    monkeypatch: pytest.MonkeyPatch, patched_settings: Settings, tmp_path: Path
) -> None:
    package = tmp_path / "package.zip"
    package.write_bytes(b"dummy")
    calls = _stub_run_ingestion(monkeypatch, _completed_state("custom-run-id"))

    result = runner.invoke(
        cli_module.app, ["ingest", str(package), "--run-id", "custom-run-id"]
    )

    assert result.exit_code == 0
    assert calls == [(package, "custom-run-id")]


def test_ingest_failed_exit_1(
    monkeypatch: pytest.MonkeyPatch, patched_settings: Settings, tmp_path: Path
) -> None:
    package = tmp_path / "package.zip"
    package.write_bytes(b"dummy")
    final_state = _failed_state()
    _stub_run_ingestion(monkeypatch, final_state)

    result = runner.invoke(cli_module.app, ["ingest", str(package)])

    assert result.exit_code == 1
    assert result.stdout.splitlines() == _expected_summary_lines(final_state)
    assert "VALIDATED: FAILED (fallo simulado)" in result.stdout


def test_ingest_nonexistent_package_is_usage_error(patched_settings: Settings) -> None:
    result = runner.invoke(cli_module.app, ["ingest", "/no/existe/package.zip"])
    assert result.exit_code == 2


def test_ingest_help_documents_completed_and_failed() -> None:
    result = runner.invoke(cli_module.app, ["ingest", "--help"])
    assert result.exit_code == 0
    assert "COMPLETED" in result.stdout
    assert "FAILED" in result.stdout


def test_cli_module_does_not_import_fastapi() -> None:
    # Prueba real de "el CLI no requiere FastAPI levantado": ni siquiera
    # IMPORTA el paquete fastapi al cargar altamira_extractor.cli (nunca
    # se importa api.app/routers/deps/uploads/executor -- solo los
    # modulos puros api.reads/errors/validation/downloads/schemas).
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import altamira_extractor.cli, sys; "
            "leaked = sorted(m for m in sys.modules "
            "if m == 'fastapi' or m.startswith('fastapi.')); "
            "assert not leaked, leaked",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert completed.returncode == 0, completed.stderr


# ---------------------------------------------------------------------------
# resume
# ---------------------------------------------------------------------------


def test_resume_happy_path_exit_0(
    monkeypatch: pytest.MonkeyPatch, patched_settings: Settings
) -> None:
    run_dir = patched_settings.runs_dir / RUN_ID
    build_run_up_to_guardrails_applied(run_dir, RUN_ID)
    final_state = _completed_state()
    calls = _stub_run_ingestion(monkeypatch, final_state)

    result = runner.invoke(cli_module.app, ["resume", RUN_ID])

    assert result.exit_code == 0, result.stderr
    assert result.stdout.splitlines() == _expected_summary_lines(final_state)
    assert calls == [(run_dir / "input" / "package.zip", RUN_ID)]


def test_resume_nonexistent_run_exit_3(patched_settings: Settings) -> None:
    result = runner.invoke(cli_module.app, ["resume", NONEXISTENT_RUN_ID])
    assert result.exit_code == 3
    assert result.stderr.strip() != ""


def test_resume_completed_run_exit_4(patched_settings: Settings) -> None:
    build_run_completed(patched_settings.runs_dir / RUN_ID, RUN_ID)
    result = runner.invoke(cli_module.app, ["resume", RUN_ID])
    assert result.exit_code == 4
    assert "COMPLETED" in result.stderr


def test_resume_missing_package_zip_exit_5(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / RUN_ID
    build_run_up_to_guardrails_applied(run_dir, RUN_ID)
    (run_dir / "input" / "package.zip").unlink()

    result = runner.invoke(cli_module.app, ["resume", RUN_ID])
    assert result.exit_code == 5


def test_resume_symlinked_package_zip_exit_5(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / RUN_ID
    build_run_up_to_guardrails_applied(run_dir, RUN_ID)
    package_path = run_dir / "input" / "package.zip"
    real_target = run_dir / "input" / "real.zip"
    package_path.rename(real_target)
    try:
        os.symlink(real_target, package_path)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks no soportados en este entorno")

    result = runner.invoke(cli_module.app, ["resume", RUN_ID])
    assert result.exit_code == 5


def test_resume_does_not_duplicate_stage_execution(
    monkeypatch: pytest.MonkeyPatch, patched_settings: Settings
) -> None:
    run_dir = patched_settings.runs_dir / RUN_ID
    build_run_up_to_guardrails_applied(run_dir, RUN_ID)
    final_state = _completed_state()
    _stub_run_ingestion(monkeypatch, final_state)

    result = runner.invoke(cli_module.app, ["resume", RUN_ID])

    printed_stage_lines = [
        line for line in result.stdout.splitlines() if line.startswith("  ")
    ]
    assert len(printed_stage_lines) == len(final_state.stages)


def test_resume_failed_exit_1(
    monkeypatch: pytest.MonkeyPatch, patched_settings: Settings
) -> None:
    run_dir = patched_settings.runs_dir / RUN_ID
    build_run_up_to_guardrails_applied(run_dir, RUN_ID)
    _stub_run_ingestion(monkeypatch, _failed_state())

    result = runner.invoke(cli_module.app, ["resume", RUN_ID])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------


def test_runs_listed_descending(patched_settings: Settings) -> None:
    for run_id in ("20260101T000000000000-aaaaaaaa", "20260201T000000000000-bbbbbbbb"):
        run_dir = patched_settings.runs_dir / run_id
        state = build_run_state(
            run_id,
            stages=[stage_execution(PipelineStage.RECEIVED, StageStatus.SUCCEEDED)],
            current_stage=PipelineStage.RECEIVED,
        )
        write_run_state(run_dir, state)

    result = runner.invoke(cli_module.app, ["runs"])

    assert result.exit_code == 0
    lines = result.stdout.strip().splitlines()
    assert lines[0].startswith("20260201T000000000000-bbbbbbbb")
    assert lines[1].startswith("20260101T000000000000-aaaaaaaa")


def test_runs_limit(patched_settings: Settings) -> None:
    for run_id in ("20260101T000000000000-aaaaaaaa", "20260201T000000000000-bbbbbbbb"):
        state = build_run_state(
            run_id,
            stages=[stage_execution(PipelineStage.RECEIVED, StageStatus.SUCCEEDED)],
            current_stage=PipelineStage.RECEIVED,
        )
        write_run_state(patched_settings.runs_dir / run_id, state)

    result = runner.invoke(cli_module.app, ["runs", "--limit", "1"])

    assert result.exit_code == 0
    assert len(result.stdout.strip().splitlines()) == 1
    assert "bbbbbbbb" in result.stdout


def test_runs_offset(patched_settings: Settings) -> None:
    for run_id in ("20260101T000000000000-aaaaaaaa", "20260201T000000000000-bbbbbbbb"):
        state = build_run_state(
            run_id,
            stages=[stage_execution(PipelineStage.RECEIVED, StageStatus.SUCCEEDED)],
            current_stage=PipelineStage.RECEIVED,
        )
        write_run_state(patched_settings.runs_dir / run_id, state)

    result = runner.invoke(cli_module.app, ["runs", "--limit", "1", "--offset", "1"])

    assert result.exit_code == 0
    assert "aaaaaaaa" in result.stdout
    assert "bbbbbbbb" not in result.stdout


def test_runs_empty(patched_settings: Settings) -> None:
    result = runner.invoke(cli_module.app, ["runs"])
    assert result.exit_code == 0
    assert result.stdout == "No hay ejecuciones registradas.\n"


def test_runs_skips_corrupt_run(patched_settings: Settings) -> None:
    good_id = "20260101T000000000000-aaaaaaaa"
    state = build_run_state(
        good_id,
        stages=[stage_execution(PipelineStage.RECEIVED, StageStatus.SUCCEEDED)],
        current_stage=PipelineStage.RECEIVED,
    )
    write_run_state(patched_settings.runs_dir / good_id, state)

    corrupt_dir = patched_settings.runs_dir / "20260102T000000000000-cccccccc"
    corrupt_dir.mkdir(parents=True)
    (corrupt_dir / "run.json").write_text("not json", encoding="utf-8")

    result = runner.invoke(cli_module.app, ["runs"])

    assert result.exit_code == 0
    assert good_id in result.stdout
    assert "cccccccc" not in result.stdout


def test_runs_limit_out_of_range_is_usage_error(patched_settings: Settings) -> None:
    assert runner.invoke(cli_module.app, ["runs", "--limit", "0"]).exit_code == 2
    assert runner.invoke(cli_module.app, ["runs", "--limit", "101"]).exit_code == 2


def test_runs_offset_negative_is_usage_error(patched_settings: Settings) -> None:
    result = runner.invoke(cli_module.app, ["runs", "--offset=-1"])
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_valid_run(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / RUN_ID
    build_run_up_to_contexts_built(run_dir, RUN_ID)

    result = runner.invoke(cli_module.app, ["status", RUN_ID])

    assert result.exit_code == 0
    assert f"run_id: {RUN_ID}" in result.stdout
    assert "current_stage: CONTEXTS_BUILT" in result.stdout
    assert "package_filename: input/package.zip" in result.stdout
    assert f"source_package_hash: {HASH_A}" in result.stdout
    assert "  RECEIVED: SUCCEEDED" in result.stdout
    assert "  CANDIDATES_DETECTED: SUCCEEDED" in result.stdout
    assert "  CONTEXTS_BUILT: SUCCEEDED" in result.stdout
    assert "started_at:" in result.stdout
    assert "finished_at:" in result.stdout


def test_status_nonexistent_run_exit_3(patched_settings: Settings) -> None:
    result = runner.invoke(cli_module.app, ["status", NONEXISTENT_RUN_ID])
    assert result.exit_code == 3
    assert str(patched_settings.runs_dir) not in result.stderr


def test_status_shows_warnings(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / RUN_ID
    stages = [
        stage_execution(
            PipelineStage.RECEIVED, StageStatus.SUCCEEDED, warnings=["w1", "w2"]
        )
    ]
    state = build_run_state(RUN_ID, stages=stages, current_stage=PipelineStage.RECEIVED)
    write_run_state(run_dir, state)
    write_input_package_zip(run_dir)

    result = runner.invoke(cli_module.app, ["status", RUN_ID])

    assert result.exit_code == 0
    assert "    warning: w1" in result.stdout
    assert "    warning: w2" in result.stdout


def test_status_shows_stage_error(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / RUN_ID
    stages = [
        stage_execution(PipelineStage.RECEIVED, StageStatus.SUCCEEDED),
        stage_execution(PipelineStage.VALIDATED, StageStatus.FAILED),
    ]
    state = build_run_state(RUN_ID, stages=stages, current_stage=PipelineStage.FAILED)
    write_run_state(run_dir, state)
    write_input_package_zip(run_dir)

    result = runner.invoke(cli_module.app, ["status", RUN_ID])

    assert result.exit_code == 0
    assert "    error: fallo simulado" in result.stdout


def test_status_traversal_run_id_is_usage_error(patched_settings: Settings) -> None:
    result = runner.invoke(cli_module.app, ["status", "../../etc/passwd"])
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# candidates
# ---------------------------------------------------------------------------


def test_candidates_lists_one_line_per_candidate(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / RUN_ID
    build_run_up_to_candidates_detected(run_dir, RUN_ID)

    result = runner.invoke(cli_module.app, ["candidates", RUN_ID])

    assert result.exit_code == 0
    line = result.stdout.strip()
    fields = line.split("\t")
    assert fields[0] == CANDIDATE_ID
    assert fields[1] == "MAIN"
    assert fields[2] == "WS-COD = 'R001'"
    assert fields[3] == "R001"
    assert fields[4] == "No informado"  # rule_type es None en build_candidate()
    assert fields[5] == "DETECTED_CANDIDATE"
    # nunca provenance interna (candidate_id SI puede contener HASH_A como
    # parte de su propio identificador contractual -- eso no es una fuga)
    assert "detector" not in result.stdout.lower()
    assert "line_start" not in result.stdout.lower()


def test_candidates_empty(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / RUN_ID
    stages = [
        stage_execution(PipelineStage.RECEIVED, StageStatus.SUCCEEDED),
        stage_execution(PipelineStage.CANDIDATES_DETECTED, StageStatus.SUCCEEDED),
    ]
    state = build_run_state(
        RUN_ID, stages=stages, current_stage=PipelineStage.CANDIDATES_DETECTED
    )
    write_run_state(run_dir, state)
    write_input_package_zip(run_dir)
    write_candidates_artifact(run_dir, [])

    result = runner.invoke(cli_module.app, ["candidates", RUN_ID])

    assert result.exit_code == 0
    assert result.stdout == "No hay candidatos detectados.\n"


def test_candidates_stage_not_reached_exit_4(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / RUN_ID
    stages = [stage_execution(PipelineStage.RECEIVED, StageStatus.SUCCEEDED)]
    state = build_run_state(RUN_ID, stages=stages, current_stage=PipelineStage.RECEIVED)
    write_run_state(run_dir, state)
    write_input_package_zip(run_dir)

    result = runner.invoke(cli_module.app, ["candidates", RUN_ID])
    assert result.exit_code == 4


def test_candidates_corrupted_artifact_exit_5(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / RUN_ID
    build_run_up_to_candidates_detected(run_dir, RUN_ID)
    (run_dir / "artifacts" / "06-candidates.json").write_text("not json", encoding="utf-8")

    result = runner.invoke(cli_module.app, ["candidates", RUN_ID])
    assert result.exit_code == 5


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------


def test_context_candidate_id_with_double_colon(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / RUN_ID
    build_run_up_to_contexts_built(run_dir, RUN_ID)
    assert "::" in CANDIDATE_ID

    result = runner.invoke(cli_module.app, ["context", RUN_ID, CANDIDATE_ID])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["candidate"]["candidate_id"] == CANDIDATE_ID
    # JSON funcional completo: campos reales de ContextPackage presentes.
    assert "scope" in payload
    assert "code_slice" in payload
    assert "decision" in payload
    assert "evidence" in payload
    assert "completeness" in payload


def test_context_candidate_not_found_exit_3(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / RUN_ID
    build_run_up_to_contexts_built(run_dir, RUN_ID)

    result = runner.invoke(cli_module.app, ["context", RUN_ID, "candidato-inexistente"])
    assert result.exit_code == 3


def test_context_hash_mismatch_exit_5(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / RUN_ID
    build_run_up_to_contexts_built(run_dir, RUN_ID)
    package = build_context_package()
    tampered = package.model_copy(
        update={"decision": package.decision.model_copy(update={"outcome_code": "R999"})}
    )
    write_context_directory(run_dir, [package])  # restaura manifest coherente primero
    context_dir = run_dir / "artifacts" / "07-context"
    import hashlib

    filename = hashlib.sha256(CANDIDATE_ID.encode("utf-8")).hexdigest() + ".json"
    (context_dir / filename).write_text(tampered.to_stable_json(), encoding="utf-8")

    result = runner.invoke(cli_module.app, ["context", RUN_ID, CANDIDATE_ID])
    assert result.exit_code == 5


def test_context_stage_not_reached_exit_4(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / RUN_ID
    build_run_up_to_candidates_detected(run_dir, RUN_ID)

    result = runner.invoke(cli_module.app, ["context", RUN_ID, CANDIDATE_ID])
    assert result.exit_code == 4


def test_context_candidate_id_with_slash_is_usage_error(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / RUN_ID
    build_run_up_to_contexts_built(run_dir, RUN_ID)

    result = runner.invoke(cli_module.app, ["context", RUN_ID, "foo/bar"])
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# rule
# ---------------------------------------------------------------------------


def test_rule_evidence_validated(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / RUN_ID
    build_run_up_to_guardrails_applied(run_dir, RUN_ID)
    assert not (run_dir / "artifacts" / "08-rule-drafts").exists()

    result = runner.invoke(cli_module.app, ["rule", RUN_ID, CANDIDATE_ID])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["candidate_id"] == CANDIDATE_ID
    assert payload["final_rule_draft"]["evidence_validation_status"] == "EVIDENCE_VALIDATED"
    assert (
        payload["final_rule_draft"]["functional_review_status"] == "NEEDS_FUNCTIONAL_REVIEW"
    )
    assert payload["guardrail"]["verdict"] == "EVIDENCE_VALIDATED"
    assert set(payload["guardrail"].keys()) == {
        "verdict",
        "violations",
        "warnings",
        "repair_attempts_used",
    }


def test_rule_never_exposes_internal_provenance(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / RUN_ID
    build_run_up_to_guardrails_applied(run_dir, RUN_ID)

    result = runner.invoke(cli_module.app, ["rule", RUN_ID, CANDIDATE_ID])

    assert result.exit_code == 0
    for forbidden in (
        "repair_history",
        "response_hash",
        "produced_rule_draft_hash",
        "provider",
        "context_hash",
        "initial_rule_draft_hash",
        "final_rule_draft_hash",
        "source_package_hash",
    ):
        assert forbidden not in result.stdout


def test_rule_violations_projected(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / RUN_ID
    # GuardrailReport rechaza verdict=EVIDENCE_VALIDATED junto a
    # violaciones de severidad ERROR (contracts/guardrail.py); solo
    # WARNING puede coexistir con el unico verdict que
    # build_guardrail_artifact() produce (no hay builder REJECTED
    # persistido: GUARDRAILS_APPLIED nunca escribe artifacts/09-guardrails/
    # para candidatos REJECTED).
    violation = GuardrailViolation(
        violation_id="v1", rule="R1", field="condition", message="ojo", severity="WARNING"
    )
    build_run_up_to_guardrails_applied(run_dir, RUN_ID, violations=[violation])

    result = runner.invoke(cli_module.app, ["rule", RUN_ID, CANDIDATE_ID])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["guardrail"]["violations"] == [
        {
            "violation_id": "v1",
            "rule": "R1",
            "field": "condition",
            "message": "ojo",
            "severity": "WARNING",
        }
    ]


def test_rule_stage_not_reached_exit_4(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / RUN_ID
    build_run_up_to_contexts_built(run_dir, RUN_ID)

    result = runner.invoke(cli_module.app, ["rule", RUN_ID, CANDIDATE_ID])
    assert result.exit_code == 4


def test_rule_candidate_not_found_exit_3(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / RUN_ID
    build_run_up_to_guardrails_applied(run_dir, RUN_ID)

    result = runner.invoke(cli_module.app, ["rule", RUN_ID, "otro-candidato"])
    assert result.exit_code == 3


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------


def test_download_default_output(
    patched_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = patched_settings.runs_dir / RUN_ID
    build_run_completed(run_dir, RUN_ID)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    result = runner.invoke(cli_module.app, ["download", RUN_ID])
    assert result.exit_code == 0, result.stderr
    expected = cwd / f"{RUN_ID}-rules.zip"
    assert expected.is_file()
    assert f"Reglas descargadas en: {RUN_ID}-rules.zip" in result.stdout


def test_download_explicit_output(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir = patched_settings.runs_dir / RUN_ID
    build_run_completed(run_dir, RUN_ID)
    destination_dir = tmp_path / "out"
    destination_dir.mkdir()
    destination = destination_dir / "mis-reglas.zip"

    result = runner.invoke(
        cli_module.app, ["download", RUN_ID, "--output", str(destination)]
    )

    assert result.exit_code == 0, result.stderr
    assert destination.is_file()


def test_download_existing_destination_rejected(
    patched_settings: Settings, tmp_path: Path
) -> None:
    run_dir = patched_settings.runs_dir / RUN_ID
    build_run_completed(run_dir, RUN_ID)
    destination = tmp_path / "ya-existe.zip"
    destination.write_bytes(b"algo")

    result = runner.invoke(
        cli_module.app, ["download", RUN_ID, "--output", str(destination)]
    )

    assert result.exit_code == 2
    assert "ya existe" in result.stderr


def test_download_missing_parent_rejected(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir = patched_settings.runs_dir / RUN_ID
    build_run_completed(run_dir, RUN_ID)
    destination = tmp_path / "no-existe" / "out.zip"

    result = runner.invoke(
        cli_module.app, ["download", RUN_ID, "--output", str(destination)]
    )

    assert result.exit_code == 2
    assert "directorio padre" in result.stderr


def test_download_valid_zip_only_declared_files(
    patched_settings: Settings, tmp_path: Path
) -> None:
    import hashlib

    run_dir = patched_settings.runs_dir / RUN_ID
    build_run_completed(run_dir, RUN_ID)
    destination = tmp_path / "out.zip"

    result = runner.invoke(
        cli_module.app, ["download", RUN_ID, "--output", str(destination)]
    )

    assert result.exit_code == 0
    with zipfile.ZipFile(destination) as archive:
        expected_md = hashlib.sha256(CANDIDATE_ID.encode("utf-8")).hexdigest() + ".md"
        assert set(archive.namelist()) == {"rules-manifest.json", expected_md}


def test_download_hash_mismatch_exit_5_and_cleans_temp(
    patched_settings: Settings, tmp_path: Path
) -> None:
    import hashlib

    run_dir = patched_settings.runs_dir / RUN_ID
    build_run_completed(run_dir, RUN_ID)
    filename = hashlib.sha256(CANDIDATE_ID.encode("utf-8")).hexdigest() + ".md"
    (run_dir / "artifacts" / "10-rules" / filename).write_bytes(b"# tampered\n")
    destination = tmp_path / "out.zip"

    result = runner.invoke(
        cli_module.app, ["download", RUN_ID, "--output", str(destination)]
    )

    assert result.exit_code == 5
    assert not destination.exists()
    leftovers = list(Path(tempfile.gettempdir()).glob(f".{RUN_ID}-rules-*"))
    assert leftovers == []


def test_download_stage_not_reached_exit_4(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir = patched_settings.runs_dir / RUN_ID
    build_run_up_to_guardrails_applied(run_dir, RUN_ID)
    destination = tmp_path / "out.zip"

    result = runner.invoke(
        cli_module.app, ["download", RUN_ID, "--output", str(destination)]
    )
    assert result.exit_code == 4


# ---------------------------------------------------------------------------
# Errores y seguridad transversales
# ---------------------------------------------------------------------------


def test_keyboard_interrupt_exit_130(
    monkeypatch: pytest.MonkeyPatch, patched_settings: Settings
) -> None:
    def _raise_interrupt(run_dir: Path) -> RunState:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_module, "read_run_state", _raise_interrupt)

    result = runner.invoke(cli_module.app, ["status", NONEXISTENT_RUN_ID])

    assert result.exit_code == 130
    assert result.stderr.strip() == "Operación interrumpida."


def test_unexpected_exception_sanitized_exit_1(
    monkeypatch: pytest.MonkeyPatch, patched_settings: Settings
) -> None:
    def _boom(run_dir: Path) -> RunState:
        raise RuntimeError("secreto interno: password=hunter2 en /var/secret/path")

    monkeypatch.setattr(cli_module, "read_run_state", _boom)

    result = runner.invoke(cli_module.app, ["status", NONEXISTENT_RUN_ID])

    assert result.exit_code == 1
    # Desde Fase 15B2-B, el callback `_bootstrap_logging` conecta
    # logging JSON estructurado al entrypoint CLI: `logger.error(...)`
    # ahora SI escribe una linea JSON a stderr ademas del
    # `typer.echo("error interno", err=True)` de siempre -- la ultima
    # linea de stderr sigue siendo el texto plano para el usuario.
    stderr_lines = result.stderr.strip().splitlines()
    assert stderr_lines[-1] == "error interno"
    assert "hunter2" not in result.stdout
    assert "hunter2" not in result.stderr
    assert "/var/secret/path" not in result.stderr


def test_no_secret_or_absolute_path_in_error_output(patched_settings: Settings) -> None:
    result = runner.invoke(cli_module.app, ["status", NONEXISTENT_RUN_ID])
    assert str(patched_settings.runs_dir) not in result.stdout
    assert str(patched_settings.runs_dir) not in result.stderr
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr
