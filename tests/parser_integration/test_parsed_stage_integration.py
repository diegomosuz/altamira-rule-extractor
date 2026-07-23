"""Test de integracion real de la etapa PARSED (Prompt 5): JAR real +
Java 17 real, sobre paquetes `.zip` completos via `run_ingestion`.

Requiere el JAR ya construido:

    mvn -q -f parser/pom.xml package

No corre en la suite por defecto (marcado `integration`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from altamira_extractor.config import Settings
from altamira_extractor.contracts.canonical import CanonicalProgram
from altamira_extractor.contracts.enums import PipelineStage, StageStatus
from altamira_extractor.pipeline.runner import run_ingestion

from ..pipeline.conftest import write_zip

REPO_ROOT = Path(__file__).resolve().parents[2]
JAR_PATH = REPO_ROOT / "parser" / "target" / "altamira-cobol-parser.jar"
FIXTURES_DIR = REPO_ROOT / "parser" / "src" / "test" / "resources" / "fixtures"


def _require_jar() -> None:
    if not JAR_PATH.is_file():
        pytest.fail(f"{JAR_PATH} no existe. Ejecute primero: mvn -q -f parser/pom.xml package")


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        runs_dir=tmp_path / "data" / "runs",
        incoming_dir=tmp_path / "data" / "incoming",
        **overrides,  # type: ignore[arg-type]
    )


def _manifest_xml(*, source_format: str = "AUTO", source_encoding: str = "AUTO") -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<altamira-package schema-version="1.0">
  <country code="AR" name="Argentina"/>
  <application name="Transferencias"/>
  <operation logical-name="OP-TRF-PROPIA" description="Transferencia entre cuentas propias"/>
  <implementation version="1.0">
    <entry-program>PROGA1</entry-program>
  </implementation>
  <source format="{source_format}" encoding="{source_encoding}"/>
  <parameter-tables>
    <table name="PARAM_DEMO" ddl="02-parametria/ddl/PARAM_DEMO.sql"/>
  </parameter-tables>
</altamira-package>
""".encode()


_PROG_A = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. PROGA1.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-VALOR PIC 9(5) VALUE 100.
       PROCEDURE DIVISION.
       MAIN-PARA.
           DISPLAY WS-VALOR.
           GOBACK.
"""

_PROG_A_OTHER_DIR = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. PROGA2.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-OTRO PIC 9(5) VALUE 200.
       PROCEDURE DIVISION.
       MAIN-PARA.
           DISPLAY WS-OTRO.
           GOBACK.
"""

_PROG_B_COB = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. PROGB1.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-CODIGO PIC X(3) VALUE 'ABC'.
       PROCEDURE DIVISION.
       MAIN-PARA.
           DISPLAY WS-CODIGO.
           GOBACK.
"""

_DDL = b"CREATE TABLE PARAM_DEMO (ID INT);\n"


def _happy_package_entries() -> dict[str, bytes]:
    return {
        "manifest.xml": _manifest_xml(),
        "01-codigo/cobol/PROGA1.cbl": _PROG_A,
        "01-codigo/cobol/modulo/PROGA1.cbl": _PROG_A_OTHER_DIR,
        "01-codigo/cobol/PROGB1.cob": _PROG_B_COB,
        "01-codigo/cobol/PROGBOM.cbl": (FIXTURES_DIR / "auto-fixed-utf8-bom.cbl").read_bytes(),
        "02-parametria/ddl/PARAM_DEMO.sql": _DDL,
    }


@pytest.mark.integration
def test_parsed_stage_processes_cbl_cob_bom_and_duplicate_basenames(tmp_path: Path) -> None:
    _require_jar()
    zip_path = tmp_path / "package.zip"
    write_zip(zip_path, _happy_package_entries())
    settings = _settings(tmp_path)

    state = run_ingestion(zip_path, settings)

    # El pipeline ahora continua hasta SEMANTIC_GRAPH_BUILT (Prompt 8);
    # esta prueba solo verifica que PARSED en si mismo quedo SUCCEEDED con
    # los artefactos correctos, no que sea la etapa final.
    assert state.current_stage == PipelineStage.SEMANTIC_GRAPH_BUILT
    parsed_executions = [s for s in state.stages if s.stage == PipelineStage.PARSED]
    assert len(parsed_executions) == 1
    assert parsed_executions[0].status == StageStatus.SUCCEEDED

    canonical_dir = settings.runs_dir / state.run_id / "artifacts" / "02-canonical"
    expected_program_name_by_relative = {
        "01-codigo/cobol/PROGA1.cbl.json": "PROGA1",
        "01-codigo/cobol/modulo/PROGA1.cbl.json": "PROGA2",
        "01-codigo/cobol/PROGB1.cob.json": "PROGB1",
        "01-codigo/cobol/PROGBOM.cbl.json": "BOMUTF1",
    }
    for relative, expected_program_name in expected_program_name_by_relative.items():
        artifact_path = canonical_dir / relative
        assert artifact_path.is_file(), f"falta el artefacto esperado: {relative}"
        program = CanonicalProgram.model_validate_json(artifact_path.read_text(encoding="utf-8"))
        assert program.program_name == expected_program_name
        assert program.source_package_hash == state.source_package_hash
        assert program.source_format.value in ("FIXED", "TANDEM")


@pytest.mark.integration
def test_second_run_is_idempotent_without_reinvoking_jar(tmp_path: Path) -> None:
    _require_jar()
    zip_path = tmp_path / "package.zip"
    write_zip(zip_path, _happy_package_entries())
    settings = _settings(tmp_path)

    first_state = run_ingestion(zip_path, settings)
    assert first_state.current_stage == PipelineStage.SEMANTIC_GRAPH_BUILT

    canonical_dir = settings.runs_dir / first_state.run_id / "artifacts" / "02-canonical"
    artifact_snapshot = {path: path.read_bytes() for path in canonical_dir.rglob("*.json")}
    assert len(artifact_snapshot) == 4

    # Se apunta a un JAR inexistente en la segunda corrida: si
    # run_parsed_stage necesitara reinvocar el parser para CUALQUIER
    # programa, fallaria de inmediato con ParserUnavailableError. Que la
    # segunda corrida siga en PARSED SUCCEEDED demuestra reutilizacion real
    # de los artefactos ya validos, sin depender de timestamps de
    # modificacion (mtimes son fragiles y no se usan en ningun lado de la
    # logica de idempotencia).
    settings_missing_jar = _settings(tmp_path, parser_jar_path=tmp_path / "does-not-exist.jar")
    second_state = run_ingestion(zip_path, settings_missing_jar, run_id=first_state.run_id)

    assert second_state.current_stage == PipelineStage.SEMANTIC_GRAPH_BUILT
    parsed_executions = [s for s in second_state.stages if s.stage == PipelineStage.PARSED]
    assert len(parsed_executions) == 1
    assert parsed_executions[0].status == StageStatus.SUCCEEDED

    for path, original_bytes in artifact_snapshot.items():
        assert path.read_bytes() == original_bytes


@pytest.mark.integration
def test_partial_failure_keeps_valid_artifact_and_fails_stage(tmp_path: Path) -> None:
    _require_jar()
    entries = {
        "manifest.xml": _manifest_xml(source_format="FIXED", source_encoding="UTF-8"),
        "01-codigo/cobol/GOOD1.cbl": _PROG_A,
        "01-codigo/cobol/BAD1.cbl": (FIXTURES_DIR / "invalid-syntax.cbl").read_bytes(),
        "02-parametria/ddl/PARAM_DEMO.sql": _DDL,
    }
    zip_path = tmp_path / "package.zip"
    write_zip(zip_path, entries)
    settings = _settings(tmp_path)

    state = run_ingestion(zip_path, settings)

    assert state.current_stage == PipelineStage.FAILED
    parsed_executions = [s for s in state.stages if s.stage == PipelineStage.PARSED]
    assert len(parsed_executions) == 1
    assert parsed_executions[0].status == StageStatus.FAILED
    assert parsed_executions[0].error is not None
    assert "BAD1.cbl" in parsed_executions[0].error
    assert "GOOD1.cbl" not in parsed_executions[0].error

    canonical_dir = settings.runs_dir / state.run_id / "artifacts" / "02-canonical"
    good_artifact = canonical_dir / "01-codigo/cobol/GOOD1.cbl.json"
    assert good_artifact.is_file()
    program = CanonicalProgram.model_validate_json(good_artifact.read_text(encoding="utf-8"))
    assert program.program_name == "PROGA1"

    bad_artifact = canonical_dir / "01-codigo/cobol/BAD1.cbl.json"
    assert not bad_artifact.exists()
