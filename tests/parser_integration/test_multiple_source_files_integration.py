"""Matriz de complejidad (Prompt 15), caso "multiples programas": la
interpretacion cerrada para esta matriz es "varios archivos fuente COBOL
dentro del mismo paquete" -- nunca programas anidados dentro de una
unica compilation unit (fuera del alcance probado, ver observacion mas
abajo). JAR real + Java 17 real (marcado `integration`, igual que el
resto de `tests/parser_integration/`), pero deliberadamente SIN requerir
Neo4j ni un proveedor LLM: las aserciones solo dependen de que PARSED
haya tenido exito, algo que no depende de si Neo4j esta disponible en el
entorno que ejecuta la prueba (las etapas posteriores si lo requieren,
pero esta prueba no las verifica).

Requiere el JAR ya construido:

    mvn -q -f parser/pom.xml package
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


def _require_jar() -> None:
    if not JAR_PATH.is_file():
        pytest.fail(f"{JAR_PATH} no existe. Ejecute primero: mvn -q -f parser/pom.xml package")


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        runs_dir=tmp_path / "data" / "runs",
        incoming_dir=tmp_path / "data" / "incoming",
    )


_MANIFEST_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<altamira-package schema-version="1.0">
  <country code="AR" name="Argentina"/>
  <application name="Transferencias"/>
  <operation logical-name="OP-TRF-PROPIA" description="Transferencia entre cuentas propias"/>
  <implementation version="1.0">
    <entry-program>PROG001</entry-program>
    <entry-program>PROG002</entry-program>
  </implementation>
  <source format="FIXED" encoding="UTF-8"/>
  <parameter-tables>
    <table name="PARAM_DEMO" ddl="02-parametria/ddl/PARAM_DEMO.sql"/>
  </parameter-tables>
</altamira-package>
"""

# Construccion canonica verificable y DISTINTA en cada programa: PROG001
# tiene una decision IF, PROG002 un COMPUTE -- para que la prueba pueda
# confirmar que cada CanonicalProgram fue realmente extraido de SU
# propio archivo, no una copia o mezcla del otro.
_PROG001 = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. PROG001.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-MONTO-001 PIC 9(7)V99 VALUE 0.
       01 WS-RESULT-001 PIC X(4) VALUE SPACES.
       PROCEDURE DIVISION.
       MAIN-PARA.
           IF WS-MONTO-001 > 0
               MOVE 'POS1' TO WS-RESULT-001
           END-IF.
           GOBACK.
"""

_PROG002 = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. PROG002.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-BASE-002 PIC 9(7)V99 VALUE 0.
       01 WS-AJUSTE-002 PIC 9(7)V99 VALUE 0.
       01 WS-TOTAL-002 PIC 9(7)V99 VALUE 0.
       PROCEDURE DIVISION.
       MAIN-PARA.
           COMPUTE WS-TOTAL-002 = WS-BASE-002 + WS-AJUSTE-002.
           GOBACK.
"""

_DDL = b"CREATE TABLE PARAM_DEMO (ID INT);\n"


def _package_entries() -> dict[str, bytes]:
    return {
        "manifest.xml": _MANIFEST_XML,
        "01-codigo/cobol/PROG001.cbl": _PROG001,
        "01-codigo/cobol/PROG002.cbl": _PROG002,
        "02-parametria/ddl/PARAM_DEMO.sql": _DDL,
    }


@pytest.mark.integration
def test_multiple_source_files_are_all_inventoried_and_parsed_independently(
    tmp_path: Path,
) -> None:
    _require_jar()
    zip_path = tmp_path / "package.zip"
    write_zip(zip_path, _package_entries())
    settings = _settings(tmp_path)

    state = run_ingestion(zip_path, settings)

    # No se exige que la corrida complete: SEMANTIC_GRAPH_LOADED en
    # adelante requiere Neo4j real, fuera del alcance de esta prueba
    # (ver docstring del modulo). Solo importa que PARSED en si mismo
    # haya tenido exito real con el JAR real.
    parsed_executions = [s for s in state.stages if s.stage == PipelineStage.PARSED]
    assert len(parsed_executions) == 1, "PARSED debe aparecer exactamente una vez"
    assert parsed_executions[0].status == StageStatus.SUCCEEDED

    inventory_path = settings.runs_dir / state.run_id / "artifacts" / "01-inventory.json"
    inventory_text = inventory_path.read_text(encoding="utf-8")
    assert "PROG001.cbl" in inventory_text
    assert "PROG002.cbl" in inventory_text

    canonical_dir = settings.runs_dir / state.run_id / "artifacts" / "02-canonical"
    prog001_path = canonical_dir / "01-codigo/cobol/PROG001.cbl.json"
    prog002_path = canonical_dir / "01-codigo/cobol/PROG002.cbl.json"
    assert prog001_path.is_file()
    assert prog002_path.is_file()

    prog001 = CanonicalProgram.model_validate_json(prog001_path.read_text(encoding="utf-8"))
    prog002 = CanonicalProgram.model_validate_json(prog002_path.read_text(encoding="utf-8"))

    # program_id (aqui: program_name, base del ID compuesto en etapas
    # posteriores) distintos -- ningun artefacto sobrescribe al otro.
    assert prog001.program_name == "PROG001"
    assert prog002.program_name == "PROG002"
    assert prog001.program_name != prog002.program_name

    # Basenames y rutas relativas permanecen separados.
    assert prog001.source_file == "01-codigo/cobol/PROG001.cbl"
    assert prog002.source_file == "01-codigo/cobol/PROG002.cbl"

    # Construccion canonica distinta y verificable en cada uno.
    prog001_kinds = {stmt.kind for para in prog001.paragraphs for stmt in para.statements}
    prog002_kinds = {stmt.kind for para in prog002.paragraphs for stmt in para.statements}
    assert "IF" in prog001_kinds
    assert "COMPUTE" in prog002_kinds

    # Mismo source_package_hash para ambos (mismo ZIP): determinismo del
    # hash a nivel de paquete.
    assert prog001.source_package_hash == state.source_package_hash
    assert prog002.source_package_hash == state.source_package_hash
    assert prog001.source_hash != prog002.source_hash, (
        "hashes de codigo fuente propios (source_hash12 subyacente) deben diferir: "
        "contenido distinto"
    )


@pytest.mark.integration
def test_multiple_source_files_produce_deterministic_hashes_across_runs(
    tmp_path: Path,
) -> None:
    _require_jar()
    zip_path = tmp_path / "package.zip"
    write_zip(zip_path, _package_entries())

    first_settings = _settings(tmp_path)
    first_state = run_ingestion(zip_path, first_settings)
    first_canonical_dir = (
        first_settings.runs_dir / first_state.run_id / "artifacts" / "02-canonical"
    )
    first_prog001 = CanonicalProgram.model_validate_json(
        (first_canonical_dir / "01-codigo/cobol/PROG001.cbl.json").read_text(encoding="utf-8")
    )
    first_prog002 = CanonicalProgram.model_validate_json(
        (first_canonical_dir / "01-codigo/cobol/PROG002.cbl.json").read_text(encoding="utf-8")
    )

    second_settings = _settings(tmp_path / "second")
    second_state = run_ingestion(zip_path, second_settings)
    second_canonical_dir = (
        second_settings.runs_dir / second_state.run_id / "artifacts" / "02-canonical"
    )
    second_prog001 = CanonicalProgram.model_validate_json(
        (second_canonical_dir / "01-codigo/cobol/PROG001.cbl.json").read_text(encoding="utf-8")
    )
    second_prog002 = CanonicalProgram.model_validate_json(
        (second_canonical_dir / "01-codigo/cobol/PROG002.cbl.json").read_text(encoding="utf-8")
    )

    assert first_state.source_package_hash == second_state.source_package_hash
    assert first_prog001.source_hash == second_prog001.source_hash
    assert first_prog002.source_hash == second_prog002.source_hash
    assert first_prog001.model_dump() == second_prog001.model_dump()
    assert first_prog002.model_dump() == second_prog002.model_dump()
