"""Tests del servicio de filesystem y del comando CLI
`unified-candidates-shadow` (Fase 11 de la ampliacion semantica,
`feat/unified-candidate-artifact-shadow`). Mismo patron que
`tests/test_cli_candidate_promotion_review_and_plan.py`: sin Docker,
sin JAR, sin Neo4j, sin FastAPI -- solo filesystem local (`tmp_path`)
via `CliRunner`."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

import altamira_extractor.cli as cli_module
from altamira_extractor.config import Settings
from altamira_extractor.contracts.candidate import CandidateArtifact
from altamira_extractor.contracts.canonical import (
    CanonicalParagraph,
    CanonicalProgram,
    CanonicalStatement,
)
from altamira_extractor.contracts.enums import (
    LocationKind,
    PipelineStage,
    SourceFormat,
    StageStatus,
    StatementKind,
)
from altamira_extractor.contracts.run_state import RunState, StageExecution
from altamira_extractor.contracts.unified_candidates_shadow import (
    UnifiedCandidatesShadowArtifact,
)
from altamira_extractor.pipeline.artifact_store import atomic_write_json
from altamira_extractor.pipeline.errors import UnifiedCandidatesShadowError
from altamira_extractor.pipeline.unified_candidates_shadow_service import (
    compute_unified_candidates_shadow_artifact,
    unified_candidates_shadow_artifact_path,
    write_unified_candidates_shadow_artifact,
)

runner = CliRunner()

_HASH = "7" * 64
_RUN_ID = "20260101T000000000000-7abbccdd"
_NONEXISTENT_RUN_ID = "20260101T000000000000-99999999"


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


def _write_run_state(run_dir: Path, *, stages: tuple[PipelineStage, ...]) -> None:
    now = datetime.now(UTC)
    executions = [
        StageExecution(
            stage=stage,
            status=StageStatus.SUCCEEDED,
            started_at=now,
            finished_at=now,
            duration_seconds=0.0,
        )
        for stage in stages
    ]
    state = RunState(
        run_id=_RUN_ID,
        package_filename="input/package.zip",
        source_package_hash=_HASH,
        current_stage=stages[-1] if stages else PipelineStage.RECEIVED,
        stages=executions,
        created_at=now,
        updated_at=now,
    )
    atomic_write_json(run_dir / "run.json", state)


def _write_canonical(run_dir: Path) -> None:
    stmt = CanonicalStatement(
        statement_id="P1::A::0::IF",
        kind=StatementKind.IF,
        source_text="IF CONDICION",
        location_kind=LocationKind.EXACT,
        source_file="a.cbl",
        line_start=10,
        line_end=10,
        expression="CONDICION",
    )
    paragraph = CanonicalParagraph(
        name="A", source_text="A.", location_kind=LocationKind.UNKNOWN, statements=[stmt]
    )
    program = CanonicalProgram(
        program_name="PROG1",
        source_file="a.cbl",
        source_hash=_HASH,
        source_package_hash=_HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        paragraphs=[paragraph],
    )
    canonical_dir = run_dir / "artifacts" / "02-canonical"
    canonical_dir.mkdir(parents=True)
    atomic_write_json(canonical_dir / "a.cbl.json", program)


def _write_v1_candidates(run_dir: Path) -> None:
    artifact = CandidateArtifact(
        run_id=_RUN_ID,
        source_package_hash=_HASH,
        semantic_graph_hash=_HASH,
        invariants_query_hash=_HASH,
        q0_query_hash=_HASH,
        candidates=[],
    )
    atomic_write_json(run_dir / "artifacts" / "06-candidates.json", artifact)


def _write_parsed_run(run_dir: Path) -> None:
    _write_run_state(run_dir, stages=(PipelineStage.PARSED,))
    _write_canonical(run_dir)
    _write_v1_candidates(run_dir)


def _generate_review_package_and_plan_via_cli(run_dir: Path, tmp_path: Path) -> None:
    result = runner.invoke(cli_module.app, ["candidate-promotion-review-package", _RUN_ID])
    assert result.exit_code == 0, result.stdout

    package_path = run_dir / "diagnostics" / "candidate-promotion-review-package.json"
    payload = json.loads(package_path.read_bytes().decode("utf-8"))
    review_package_hash = hashlib.sha256(package_path.read_bytes()).hexdigest()
    manifest_payload = {
        "schema_version": "1.0",
        "review_package_hash": review_package_hash,
        "assessment_artifact_hash": payload["assessment_artifact_hash"],
        "run_id": _RUN_ID,
        "decisions": [],
    }
    manifest_path = tmp_path / "decisions.json"
    manifest_path.write_text(json.dumps(manifest_payload), encoding="utf-8")

    result = runner.invoke(
        cli_module.app,
        ["candidate-promotion-plan", _RUN_ID, "--decisions", str(manifest_path)],
    )
    assert result.exit_code == 0, result.stdout


# ---------------------------------------------------------------------------
# Servicio: compute_unified_candidates_shadow_artifact
# ---------------------------------------------------------------------------


def test_service_nonexistent_run_raises(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _NONEXISTENT_RUN_ID
    with pytest.raises(UnifiedCandidatesShadowError):
        compute_unified_candidates_shadow_artifact(run_dir, _NONEXISTENT_RUN_ID)


def test_service_stage_not_parsed_raises(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_run_state(run_dir, stages=(PipelineStage.RECEIVED,))
    with pytest.raises(UnifiedCandidatesShadowError, match="PARSED"):
        compute_unified_candidates_shadow_artifact(run_dir, _RUN_ID)


def test_service_v1_absent_raises(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_run_state(run_dir, stages=(PipelineStage.PARSED,))
    _write_canonical(run_dir)
    with pytest.raises(UnifiedCandidatesShadowError, match="V1"):
        compute_unified_candidates_shadow_artifact(run_dir, _RUN_ID)


def test_service_review_package_absent_raises(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    with pytest.raises(UnifiedCandidatesShadowError, match="paquete de revision"):
        compute_unified_candidates_shadow_artifact(run_dir, _RUN_ID)


def test_service_plan_absent_raises(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    result = runner.invoke(cli_module.app, ["candidate-promotion-review-package", _RUN_ID])
    assert result.exit_code == 0, result.stdout
    with pytest.raises(UnifiedCandidatesShadowError, match="plan de promocion"):
        compute_unified_candidates_shadow_artifact(run_dir, _RUN_ID)


def test_service_happy_path_computes_artifact(
    patched_settings: Settings, tmp_path: Path
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_review_package_and_plan_via_cli(run_dir, tmp_path)

    artifact = compute_unified_candidates_shadow_artifact(run_dir, _RUN_ID)
    assert artifact.run_id == _RUN_ID
    assert artifact.baseline_candidates == []
    assert artifact.shadow_members == []


def test_service_determinism(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_review_package_and_plan_via_cli(run_dir, tmp_path)

    a1 = compute_unified_candidates_shadow_artifact(run_dir, _RUN_ID)
    a2 = compute_unified_candidates_shadow_artifact(run_dir, _RUN_ID)
    assert a1.to_stable_json() == a2.to_stable_json()


def test_service_writes_only_the_expected_diagnostic_file(
    patched_settings: Settings, tmp_path: Path
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_review_package_and_plan_via_cli(run_dir, tmp_path)

    existing_diagnostics = set((run_dir / "diagnostics").glob("*.json"))
    artifact = compute_unified_candidates_shadow_artifact(run_dir, _RUN_ID)
    report_path = write_unified_candidates_shadow_artifact(run_dir, artifact)

    assert report_path == unified_candidates_shadow_artifact_path(run_dir)
    new_diagnostics = set((run_dir / "diagnostics").glob("*.json")) - existing_diagnostics
    assert {p.name for p in new_diagnostics} == {"unified-candidates-shadow.json"}


def test_service_hash_roundtrip_via_atomic_write_json(
    patched_settings: Settings, tmp_path: Path
) -> None:
    """Auditoria de cierre, Parte 5: (1) generar el artefacto en
    memoria, (2) serializarlo a disco via `write_unified_candidates_
    shadow_artifact` (que usa `atomic_write_json`, es decir
    `to_stable_json()` con claves ordenadas), (3) volver a cargarlo
    desde disco, (4) recalcular su hash, (5) confirmar que coincide con
    el hash del objeto en memoria original, (6) ejecutar nuevamente el
    servicio completo desde cero, (7) confirmar bytes identicos en
    disco. Nunca usa `model_dump_json()` para este hash (sensible al
    orden de insercion de dicts): usa `to_stable_json()`, el mismo
    metodo que `atomic_write_json`."""
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_review_package_and_plan_via_cli(run_dir, tmp_path)

    in_memory_artifact = compute_unified_candidates_shadow_artifact(run_dir, _RUN_ID)
    in_memory_hash = hashlib.sha256(
        in_memory_artifact.to_stable_json().encode("utf-8")
    ).hexdigest()

    report_path = write_unified_candidates_shadow_artifact(run_dir, in_memory_artifact)
    bytes_on_disk_first = report_path.read_bytes()

    reloaded_artifact = UnifiedCandidatesShadowArtifact.model_validate_json(
        bytes_on_disk_first.decode("utf-8")
    )
    reloaded_hash = hashlib.sha256(
        reloaded_artifact.to_stable_json().encode("utf-8")
    ).hexdigest()

    assert reloaded_hash == in_memory_hash
    assert reloaded_artifact == in_memory_artifact

    # Re-ejecutar el servicio completo (recomputar + reescribir) debe
    # producir bytes identicos en disco -- nunca una escritura parcial
    # ni una segunda version distinta.
    artifact_again = compute_unified_candidates_shadow_artifact(run_dir, _RUN_ID)
    write_unified_candidates_shadow_artifact(run_dir, artifact_again)
    bytes_on_disk_second = report_path.read_bytes()
    assert bytes_on_disk_first == bytes_on_disk_second


# ---------------------------------------------------------------------------
# CLI: unified-candidates-shadow
# ---------------------------------------------------------------------------


def test_cli_summary_and_exit_0(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_review_package_and_plan_via_cli(run_dir, tmp_path)

    result = runner.invoke(cli_module.app, ["unified-candidates-shadow", _RUN_ID])

    assert result.exit_code == 0, result.stdout
    assert f"run_id: {_RUN_ID}" in result.stdout
    assert "shadow_members: 0" in result.stdout
    report_path = run_dir / "diagnostics" / "unified-candidates-shadow.json"
    assert report_path.is_file()


def test_cli_json_option(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_review_package_and_plan_via_cli(run_dir, tmp_path)

    result = runner.invoke(cli_module.app, ["unified-candidates-shadow", _RUN_ID, "--json"])

    assert result.exit_code == 0
    json_start = result.stdout.index("{")
    payload = json.loads(result.stdout[json_start:])
    assert payload["run_id"] == _RUN_ID


def test_cli_missing_plan_exits_nonzero_and_sanitized(
    patched_settings: Settings, tmp_path: Path
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    result = runner.invoke(cli_module.app, ["candidate-promotion-review-package", _RUN_ID])
    assert result.exit_code == 0, result.stdout

    result = runner.invoke(cli_module.app, ["unified-candidates-shadow", _RUN_ID])
    assert result.exit_code != 0
    assert str(patched_settings.runs_dir) not in result.stdout
    assert "Traceback" not in result.stdout


def test_cli_nonexistent_run_exits_nonzero_and_sanitized(patched_settings: Settings) -> None:
    result = runner.invoke(cli_module.app, ["unified-candidates-shadow", _NONEXISTENT_RUN_ID])
    assert result.exit_code != 0
    assert str(patched_settings.runs_dir) not in result.stdout
    assert "Traceback" not in result.stdout


def test_cli_never_modifies_preexisting_artifacts(
    patched_settings: Settings, tmp_path: Path
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_review_package_and_plan_via_cli(run_dir, tmp_path)

    v1_before = (run_dir / "artifacts" / "06-candidates.json").read_bytes()
    plan_before = (run_dir / "diagnostics" / "candidate-promotion-plan.json").read_bytes()

    result = runner.invoke(cli_module.app, ["unified-candidates-shadow", _RUN_ID])
    assert result.exit_code == 0, result.stdout

    assert (run_dir / "artifacts" / "06-candidates.json").read_bytes() == v1_before
    assert (
        run_dir / "diagnostics" / "candidate-promotion-plan.json"
    ).read_bytes() == plan_before
