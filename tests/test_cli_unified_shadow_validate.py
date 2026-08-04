"""Tests del servicio de filesystem y del comando CLI
`unified-shadow-validate` (Fase 12 de la ampliacion semantica,
`feat/unified-shadow-differential-validation`). Mismo patron que
`tests/test_cli_unified_candidates_shadow.py` (Fase 11), del que
reutiliza el helper de escenario REAL-pero-vacio (V1/V2/interprocedural
sin candidatos, `candidate-promotion-review-package`/`candidate-
promotion-plan` reales via CLI con un manifiesto de decisiones vacio) --
sin Docker, sin JAR, sin Neo4j, sin FastAPI, solo filesystem local
(`tmp_path`) via `CliRunner`. El escenario V2+interprocedural real
(grupo VALID/NOT_IN_BASELINE elegible) se cubre en
`tests/parser_integration/test_unified_shadow_validation_integration.py`."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import altamira_extractor.cli as cli_module
from altamira_extractor.config import Settings
from altamira_extractor.contracts.enums import PipelineStage
from altamira_extractor.contracts.unified_candidates_shadow import UnifiedCandidatesShadowArtifact
from altamira_extractor.pipeline.errors import UnifiedShadowValidationError
from altamira_extractor.pipeline.unified_candidates_shadow_service import (
    compute_unified_candidates_shadow_artifact,
    write_unified_candidates_shadow_artifact,
)
from altamira_extractor.pipeline.unified_shadow_validation_service import (
    compute_unified_shadow_validation_report,
    unified_shadow_validation_report_path,
    write_unified_shadow_validation_report,
)

from .test_cli_unified_candidates_shadow import (
    _HASH,
    _NONEXISTENT_RUN_ID,
    _RUN_ID,
    _generate_review_package_and_plan_via_cli,
    _write_parsed_run,
    _write_run_state,
)

runner = CliRunner()

__all__ = ["_HASH", "_NONEXISTENT_RUN_ID", "_RUN_ID"]


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


def _generate_empty_unified_shadow(run_dir: Path, tmp_path: Path) -> None:
    _generate_review_package_and_plan_via_cli(run_dir, tmp_path)
    artifact = compute_unified_candidates_shadow_artifact(run_dir, _RUN_ID)
    write_unified_candidates_shadow_artifact(run_dir, artifact)


# ---------------------------------------------------------------------------
# Servicio: compute_unified_shadow_validation_report
# ---------------------------------------------------------------------------


def test_service_nonexistent_run_raises(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _NONEXISTENT_RUN_ID
    with pytest.raises(UnifiedShadowValidationError):
        compute_unified_shadow_validation_report(run_dir, _NONEXISTENT_RUN_ID)


def test_service_stage_not_parsed_raises(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_run_state(run_dir, stages=(PipelineStage.RECEIVED,))
    with pytest.raises(UnifiedShadowValidationError, match="PARSED"):
        compute_unified_shadow_validation_report(run_dir, _RUN_ID)


def test_service_empty_scenario_produces_review_required(
    patched_settings: Settings, tmp_path: Path
) -> None:
    """Un run 100% real pero vacio (cero candidatos V1/V2/
    interprocedural, cero shadow members/groups) es estructuralmente
    VALIDO -- nunca un error -- y produce REVIEW_REQUIRED (integridad
    global correcta, cero grupos que puedan ser elegibles porque no
    existe ninguno)."""
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_empty_unified_shadow(run_dir, tmp_path)

    report = compute_unified_shadow_validation_report(run_dir, _RUN_ID)
    assert report.run_id == _RUN_ID
    assert report.summary.shadow_group_count == 0
    assert report.summary.baseline_candidate_count == 0
    assert report.disposition.value == "REVIEW_REQUIRED"
    assert all(
        g.status.value == "PASS"
        for g in report.gate_results
        if g.gate.value != "DOWNSTREAM_SHADOW_ELIGIBILITY"
    )


def test_service_missing_unified_shadow_raises_hard_error(
    patched_settings: Settings, tmp_path: Path
) -> None:
    """`UnifiedCandidatesShadowArtifact` es el objeto PRINCIPAL de esta
    validacion, nunca una fuente opcional: su ausencia es un error
    tecnico DURO (`UnifiedShadowValidationError`), nunca una
    disposition NOT_EVALUATED representada dentro de un reporte --
    Fase 12 nunca genera un reporte ni regenera el artefacto ausente."""
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_review_package_and_plan_via_cli(run_dir, tmp_path)
    # Deliberadamente NO se genera unified-candidates-shadow.json.

    report_path = run_dir / "diagnostics" / "unified-shadow-validation-report.json"
    with pytest.raises(UnifiedShadowValidationError):
        compute_unified_shadow_validation_report(run_dir, _RUN_ID)
    assert not report_path.exists()


def test_service_unified_shadow_syntactically_invalid_json_raises_hard_error(
    patched_settings: Settings, tmp_path: Path
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_empty_unified_shadow(run_dir, tmp_path)
    unified_shadow_path = run_dir / "diagnostics" / "unified-candidates-shadow.json"
    unified_shadow_path.write_text("{this is not valid json!!", encoding="utf-8")

    report_path = run_dir / "diagnostics" / "unified-shadow-validation-report.json"
    with pytest.raises(UnifiedShadowValidationError) as excinfo:
        compute_unified_shadow_validation_report(run_dir, _RUN_ID)
    assert "{this is not valid json" not in str(excinfo.value)
    assert not report_path.exists()


def test_service_unified_shadow_incompatible_version_raises_hard_error(
    patched_settings: Settings, tmp_path: Path
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_empty_unified_shadow(run_dir, tmp_path)
    unified_shadow_path = run_dir / "diagnostics" / "unified-candidates-shadow.json"
    payload = json.loads(unified_shadow_path.read_text(encoding="utf-8"))
    payload["schema_version"] = "99.0"
    unified_shadow_path.write_text(json.dumps(payload), encoding="utf-8")

    report_path = run_dir / "diagnostics" / "unified-shadow-validation-report.json"
    with pytest.raises(UnifiedShadowValidationError):
        compute_unified_shadow_validation_report(run_dir, _RUN_ID)
    assert not report_path.exists()


def test_service_unified_shadow_invalid_pydantic_contract_raises_hard_error(
    patched_settings: Settings, tmp_path: Path
) -> None:
    """JSON sintacticamente valido pero que viola un model_validator
    del contrato (aqui: `baseline_candidates` con un campo requerido
    ausente) tambien es un error tecnico duro -- nunca un reporte."""
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_empty_unified_shadow(run_dir, tmp_path)
    unified_shadow_path = run_dir / "diagnostics" / "unified-candidates-shadow.json"
    payload = json.loads(unified_shadow_path.read_text(encoding="utf-8"))
    payload["baseline_candidates"] = [{"baseline_reference_id": "baseline::broken::1"}]
    unified_shadow_path.write_text(json.dumps(payload), encoding="utf-8")

    report_path = run_dir / "diagnostics" / "unified-shadow-validation-report.json"
    with pytest.raises(UnifiedShadowValidationError):
        compute_unified_shadow_validation_report(run_dir, _RUN_ID)
    assert not report_path.exists()


def test_service_unified_shadow_filesystem_read_error_raises_hard_error(
    patched_settings: Settings, tmp_path: Path
) -> None:
    """Un fallo de lectura real del filesystem (aqui: el destino es un
    DIRECTORIO, no un archivo) tambien es un error tecnico duro."""
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_review_package_and_plan_via_cli(run_dir, tmp_path)
    unified_shadow_path = run_dir / "diagnostics" / "unified-candidates-shadow.json"
    unified_shadow_path.mkdir(parents=True, exist_ok=True)

    report_path = run_dir / "diagnostics" / "unified-shadow-validation-report.json"
    with pytest.raises(UnifiedShadowValidationError):
        compute_unified_shadow_validation_report(run_dir, _RUN_ID)
    assert not report_path.exists()


def test_service_not_evaluated_from_secondary_source_still_exits_via_report(
    patched_settings: Settings, tmp_path: Path
) -> None:
    """Control: el artefacto unificado PRINCIPAL esta presente y es
    valido, pero una fuente SECUNDARIA (aqui: review package/plan,
    borrados DESPUES de que Fase 11 ya los uso) esta ausente -- sigue
    siendo un reporte NOT_EVALUATED representable, nunca una
    excepcion."""
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_empty_unified_shadow(run_dir, tmp_path)
    (run_dir / "diagnostics" / "candidate-promotion-review-package.json").unlink()
    (run_dir / "diagnostics" / "candidate-promotion-plan.json").unlink()

    report = compute_unified_shadow_validation_report(run_dir, _RUN_ID)
    assert report.disposition.value == "NOT_EVALUATED"
    assert report.group_validations == []


def test_service_determinism(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_empty_unified_shadow(run_dir, tmp_path)

    r1 = compute_unified_shadow_validation_report(run_dir, _RUN_ID)
    r2 = compute_unified_shadow_validation_report(run_dir, _RUN_ID)
    assert r1.to_stable_json() == r2.to_stable_json()


def test_service_writes_only_the_expected_diagnostic_file(
    patched_settings: Settings, tmp_path: Path
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_empty_unified_shadow(run_dir, tmp_path)

    existing_diagnostics = set((run_dir / "diagnostics").glob("*.json"))
    report = compute_unified_shadow_validation_report(run_dir, _RUN_ID)
    report_path = write_unified_shadow_validation_report(run_dir, report)

    assert report_path == unified_shadow_validation_report_path(run_dir)
    new_diagnostics = set((run_dir / "diagnostics").glob("*.json")) - existing_diagnostics
    assert {p.name for p in new_diagnostics} == {"unified-shadow-validation-report.json"}


def test_service_never_modifies_preexisting_artifacts(
    patched_settings: Settings, tmp_path: Path
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_empty_unified_shadow(run_dir, tmp_path)

    v1_before = (run_dir / "artifacts" / "06-candidates.json").read_bytes()
    unified_shadow_before = (
        run_dir / "diagnostics" / "unified-candidates-shadow.json"
    ).read_bytes()
    plan_before = (run_dir / "diagnostics" / "candidate-promotion-plan.json").read_bytes()

    report = compute_unified_shadow_validation_report(run_dir, _RUN_ID)
    write_unified_shadow_validation_report(run_dir, report)

    assert (run_dir / "artifacts" / "06-candidates.json").read_bytes() == v1_before
    assert (
        run_dir / "diagnostics" / "unified-candidates-shadow.json"
    ).read_bytes() == unified_shadow_before
    assert (run_dir / "diagnostics" / "candidate-promotion-plan.json").read_bytes() == plan_before


def test_service_hash_roundtrip_via_atomic_write_json(
    patched_settings: Settings, tmp_path: Path
) -> None:
    """(1) generar el reporte en memoria, (2) serializarlo a disco via
    `write_unified_shadow_validation_report` (`atomic_write_json`, es
    decir `to_stable_json()` con claves ordenadas), (3) volver a
    cargarlo desde disco, (4) recalcular su hash, (5) confirmar que
    coincide con el hash del objeto en memoria original, (6) ejecutar
    nuevamente el servicio completo desde cero, (7) confirmar bytes
    identicos en disco. Nunca usa `model_dump_json()` para este hash."""
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_empty_unified_shadow(run_dir, tmp_path)

    in_memory_report = compute_unified_shadow_validation_report(run_dir, _RUN_ID)
    in_memory_hash = hashlib.sha256(in_memory_report.to_stable_json().encode("utf-8")).hexdigest()

    report_path = write_unified_shadow_validation_report(run_dir, in_memory_report)
    bytes_on_disk_first = report_path.read_bytes()

    from altamira_extractor.contracts.unified_shadow_validation import (
        UnifiedShadowValidationReport,
    )

    reloaded_report = UnifiedShadowValidationReport.model_validate_json(
        bytes_on_disk_first.decode("utf-8")
    )
    reloaded_hash = hashlib.sha256(reloaded_report.to_stable_json().encode("utf-8")).hexdigest()

    assert reloaded_hash == in_memory_hash
    assert reloaded_report == in_memory_report

    report_again = compute_unified_shadow_validation_report(run_dir, _RUN_ID)
    write_unified_shadow_validation_report(run_dir, report_again)
    bytes_on_disk_second = report_path.read_bytes()
    assert bytes_on_disk_first == bytes_on_disk_second


# ---------------------------------------------------------------------------
# CLI: unified-shadow-validate
# ---------------------------------------------------------------------------


def test_cli_summary_and_exit_0(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_empty_unified_shadow(run_dir, tmp_path)

    result = runner.invoke(cli_module.app, ["unified-shadow-validate", _RUN_ID])

    assert result.exit_code == 0, result.stdout
    assert f"run_id: {_RUN_ID}" in result.stdout
    assert "disposition: REVIEW_REQUIRED" in result.stdout
    assert "shadow_groups: 0" in result.stdout
    report_path = run_dir / "diagnostics" / "unified-shadow-validation-report.json"
    assert report_path.is_file()


def test_cli_json_option(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_empty_unified_shadow(run_dir, tmp_path)

    result = runner.invoke(cli_module.app, ["unified-shadow-validate", _RUN_ID, "--json"])

    assert result.exit_code == 0
    json_start = result.stdout.index("{")
    payload = json.loads(result.stdout[json_start:])
    assert payload["run_id"] == _RUN_ID


def test_cli_missing_unified_shadow_exits_nonzero_no_report(
    patched_settings: Settings, tmp_path: Path
) -> None:
    """El artefacto unificado es el objeto PRINCIPAL: su ausencia es un
    error tecnico -- exit code distinto de cero, ningun reporte
    generado, sin traceback, sin ruta absoluta."""
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_review_package_and_plan_via_cli(run_dir, tmp_path)

    result = runner.invoke(cli_module.app, ["unified-shadow-validate", _RUN_ID])
    assert result.exit_code != 0
    assert "Traceback" not in result.stdout
    assert str(patched_settings.runs_dir) not in result.stdout
    report_path = run_dir / "diagnostics" / "unified-shadow-validation-report.json"
    assert not report_path.exists()


def test_cli_not_evaluated_from_secondary_source_still_exits_0(
    patched_settings: Settings, tmp_path: Path
) -> None:
    """Control: artefacto unificado PRINCIPAL presente y valido, fuente
    SECUNDARIA (review package/plan) ausente -- exit 0, reporte
    NOT_EVALUATED generado y persistido."""
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_empty_unified_shadow(run_dir, tmp_path)
    (run_dir / "diagnostics" / "candidate-promotion-review-package.json").unlink()
    (run_dir / "diagnostics" / "candidate-promotion-plan.json").unlink()

    result = runner.invoke(cli_module.app, ["unified-shadow-validate", _RUN_ID])
    assert result.exit_code == 0, result.stdout
    assert "disposition: NOT_EVALUATED" in result.stdout


def test_cli_nonexistent_run_exits_nonzero_and_sanitized(patched_settings: Settings) -> None:
    result = runner.invoke(cli_module.app, ["unified-shadow-validate", _NONEXISTENT_RUN_ID])
    assert result.exit_code != 0
    assert str(patched_settings.runs_dir) not in result.stdout
    assert "Traceback" not in result.stdout


def test_cli_stage_not_reached_exits_nonzero_and_sanitized(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_run_state(run_dir, stages=(PipelineStage.RECEIVED,))

    result = runner.invoke(cli_module.app, ["unified-shadow-validate", _RUN_ID])
    assert result.exit_code != 0
    assert str(patched_settings.runs_dir) not in result.stdout
    assert "Traceback" not in result.stdout


def test_cli_never_modifies_preexisting_artifacts(
    patched_settings: Settings, tmp_path: Path
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_empty_unified_shadow(run_dir, tmp_path)

    v1_before = (run_dir / "artifacts" / "06-candidates.json").read_bytes()
    unified_shadow_before = (
        run_dir / "diagnostics" / "unified-candidates-shadow.json"
    ).read_bytes()

    result = runner.invoke(cli_module.app, ["unified-shadow-validate", _RUN_ID])
    assert result.exit_code == 0, result.stdout

    assert (run_dir / "artifacts" / "06-candidates.json").read_bytes() == v1_before
    assert (
        run_dir / "diagnostics" / "unified-candidates-shadow.json"
    ).read_bytes() == unified_shadow_before


def test_cli_never_registered_under_the_wrong_name() -> None:
    result = runner.invoke(cli_module.app, ["--help"])
    assert "unified-shadow-validate" in result.stdout


def test_service_report_matches_run_id_and_source_package_hash(
    patched_settings: Settings, tmp_path: Path
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_empty_unified_shadow(run_dir, tmp_path)

    unified_shadow_path = run_dir / "diagnostics" / "unified-candidates-shadow.json"
    unified_shadow = UnifiedCandidatesShadowArtifact.model_validate_json(
        unified_shadow_path.read_text(encoding="utf-8")
    )
    report = compute_unified_shadow_validation_report(run_dir, _RUN_ID)
    assert report.source_package_hash == unified_shadow.source_package_hash
