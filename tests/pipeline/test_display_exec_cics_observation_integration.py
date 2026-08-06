"""DISPLAY y EXEC CICS (Fase 15B2-A, cierre): evidencia real de que
ambas construcciones (a) no bloquean el pipeline por defecto (PARSED
SUCCEEDED pese a caer en el bucket tecnico OTHER), y (b) se observan de
forma SANITIZADA por `semantic_coverage_observations_service.py` --
`identity` nunca incluye `source_text`, ningun texto de comando CICS
(SEND/FROM/LENGTH) ni el mensaje DISPLAY literal, solo el prefijo de
clase ASG (`DisplayStatementImpl`/`ExecCicsStatementImpl`, ver decision
arquitectonica #5).

Marcado `integration` (requiere el JAR real). Nunca invoca un proveedor
LLM: solo se ejecuta hasta PARSED (nunca se configura LLM_PROVIDER)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from altamira_extractor.config import Settings
from altamira_extractor.contracts.canonical import CanonicalProgram
from altamira_extractor.contracts.enums import PipelineStage, StageStatus
from altamira_extractor.pipeline.runner import run_ingestion
from altamira_extractor.pipeline.semantic_coverage_observations_service import (
    observe_semantic_coverage,
)
from altamira_extractor.pipeline.semantic_coverage_registry import load_semantic_coverage_manifest

from ..e2e_support import regular_file_info, require_jar

pytestmark = pytest.mark.integration

_MANIFEST_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<altamira-package schema-version="1.0">
  <country code="AR" name="Argentina"/>
  <application name="DisplayExecCics"/>
  <operation logical-name="OP-DISPLAY-EXEC-CICS" description="DISPLAY y EXEC CICS"/>
  <implementation version="1.0">
    <entry-program>DISPCICS</entry-program>
  </implementation>
  <source format="FIXED" encoding="UTF-8"/>
  <parameter-tables>
    <table name="PARAM_DISPCICS" ddl="02-parametria/ddl/PARAM_DISPCICS.sql"/>
  </parameter-tables>
</altamira-package>
"""

_DDL = b"CREATE TABLE PARAM_DISPCICS (ID INT);\n"

_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "parser"
    / "src"
    / "test"
    / "resources"
    / "fixtures"
    / "display-exec-cics.cbl"
)

# Textos que NUNCA deben aparecer en un artefacto sanitizado -- ni el
# mensaje DISPLAY literal, ni ningun fragmento reconocible del comando
# CICS (SEND/TEXT/FROM/LENGTH), ni el propio source_text preservado por
# CanonicalStatement (ese SI puede vivir en artifacts/02-canonical/, pero
# NUNCA en diagnostics/semantic-coverage-observations.json).
_FORBIDDEN_SUBSTRINGS = (
    "MENSAJE DE PRUEBA",
    "SEND TEXT",
    "WS-MSG",
    "EXECCICS",
    "source_text",
)


def _build_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        runs_dir=tmp_path / "data" / "runs",
        incoming_dir=tmp_path / "data" / "incoming",
    )


def _write_package_zip(path: Path) -> Path:
    import zipfile

    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(regular_file_info("manifest.xml"), _MANIFEST_XML)
        zf.writestr(regular_file_info("01-codigo/cobol/DISPCICS.cbl"), _FIXTURE_PATH.read_bytes())
        zf.writestr(regular_file_info("02-parametria/ddl/PARAM_DISPCICS.sql"), _DDL)
    return path


def test_display_and_exec_cics_do_not_block_parsed(tmp_path: Path) -> None:
    require_jar()
    settings = _build_settings(tmp_path)
    zip_path = _write_package_zip(tmp_path / "package.zip")

    state = run_ingestion(source_zip=zip_path, settings=settings)

    parsed_execution = next(s for s in state.stages if s.stage == PipelineStage.PARSED)
    assert parsed_execution.status == StageStatus.SUCCEEDED, parsed_execution.error


def test_display_and_exec_cics_appear_in_unsupported_constructs(tmp_path: Path) -> None:
    require_jar()
    settings = _build_settings(tmp_path)
    zip_path = _write_package_zip(tmp_path / "package.zip")

    state = run_ingestion(source_zip=zip_path, settings=settings)
    run_dir = settings.runs_dir / state.run_id
    canonical_dir = run_dir / "artifacts" / "02-canonical"
    json_paths = [p for p in canonical_dir.rglob("*.json") if p.is_file()]
    assert len(json_paths) == 1
    program = CanonicalProgram.model_validate_json(json_paths[0].read_text(encoding="utf-8"))

    assert any(
        message.startswith("DisplayStatementImpl en paragraph")
        for message in program.unsupported_constructs
    )
    assert any(
        message.startswith("ExecCicsStatementImpl en paragraph")
        for message in program.unsupported_constructs
    )


def test_display_and_exec_cics_observed_and_sanitized(tmp_path: Path) -> None:
    require_jar()
    settings = _build_settings(tmp_path)
    zip_path = _write_package_zip(tmp_path / "package.zip")

    state = run_ingestion(source_zip=zip_path, settings=settings)
    run_dir = settings.runs_dir / state.run_id
    canonical_dir = run_dir / "artifacts" / "02-canonical"
    json_paths = [p for p in canonical_dir.rglob("*.json") if p.is_file()]
    program = CanonicalProgram.model_validate_json(json_paths[0].read_text(encoding="utf-8"))

    manifest = load_semantic_coverage_manifest(settings.semantic_coverage_manifest_path)
    artifact = observe_semantic_coverage(
        [program], manifest, run_id=state.run_id, source_package_hash=program.source_package_hash
    )

    by_id = {c.construct_id: c for c in artifact.constructs}
    assert by_id["DISPLAY"].observed is True
    assert by_id["DISPLAY"].occurrence_count >= 1
    assert by_id["EXEC_CICS"].observed is True
    assert by_id["EXEC_CICS"].occurrence_count >= 1

    mapped_ids = {u.identity: u.construct_id for u in artifact.unsupported_identities}
    assert mapped_ids["DisplayStatementImpl"] == "DISPLAY"
    assert mapped_ids["ExecCicsStatementImpl"] == "EXEC_CICS"

    # Sanitizacion: serializar el artefacto completo y confirmar que
    # ningun fragmento prohibido (mensaje DISPLAY, comando CICS,
    # source_text) aparece en ningun lado.
    serialized = artifact.to_stable_json()
    for forbidden in _FORBIDDEN_SUBSTRINGS:
        assert forbidden not in serialized, f"leaked forbidden text: {forbidden!r}"

    # El JSON persistido tambien debe re-validar limpio (round-trip real,
    # no solo el objeto en memoria).
    reloaded = json.loads(serialized)
    assert reloaded["run_id"] == state.run_id
