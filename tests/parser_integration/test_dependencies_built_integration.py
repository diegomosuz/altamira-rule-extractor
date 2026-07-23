"""Test de integracion real de la etapa DEPENDENCIES_BUILT (Prompt 6): JAR
real + Java 17 real, pipeline completo hasta `artifacts/03-dependencies.json`.

Requiere el JAR ya construido:

    mvn -q -f parser/pom.xml package

No corre en la suite por defecto (marcado `integration`). No se modifica el
parser Java para fabricar este fixture: es COBOL FIXED-format ordinario.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from altamira_extractor.config import Settings
from altamira_extractor.contracts.dependencies import DependencyArtifact
from altamira_extractor.contracts.enums import DependencyType, PipelineStage, StageStatus
from altamira_extractor.pipeline.runner import run_ingestion

from ..pipeline.conftest import write_zip

REPO_ROOT = Path(__file__).resolve().parents[2]
JAR_PATH = REPO_ROOT / "parser" / "target" / "altamira-cobol-parser.jar"


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


def _manifest_xml() -> bytes:
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<altamira-package schema-version="1.0">
  <country code="AR" name="Argentina"/>
  <application name="Transferencias"/>
  <operation logical-name="OP-TRF-PROPIA" description="Transferencia entre cuentas propias"/>
  <implementation version="1.0">
    <entry-program>DEPPROG1</entry-program>
  </implementation>
  <source format="FIXED" encoding="UTF-8"/>
  <parameter-tables>
    <table name="PARAM_DEMO" ddl="02-parametria/ddl/PARAM_DEMO.sql"/>
  </parameter-tables>
</altamira-package>
"""


_PROGRAM = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. DEPPROG1.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-FLAG PIC 9(1) VALUE 0.
       PROCEDURE DIVISION.
       MAIN-PARA.
           PERFORM SET-FLAG-PARA.
           PERFORM CHECK-FLAG-PARA.
           GOBACK.
       SET-FLAG-PARA.
           MOVE 1 TO WS-FLAG.
       CHECK-FLAG-PARA.
           IF WS-FLAG = 1
               DISPLAY 'FLAG SET'
           END-IF.
"""

_DDL = b"CREATE TABLE PARAM_DEMO (ID INT);\n"


def _package_entries() -> dict[str, bytes]:
    return {
        "manifest.xml": _manifest_xml(),
        "01-codigo/cobol/DEPPROG1.cbl": _PROGRAM,
        "02-parametria/ddl/PARAM_DEMO.sql": _DDL,
    }


@pytest.mark.integration
def test_dependencies_built_produces_control_and_data_edges(tmp_path: Path) -> None:
    _require_jar()
    zip_path = tmp_path / "package.zip"
    write_zip(zip_path, _package_entries())
    settings = _settings(tmp_path)

    state = run_ingestion(zip_path, settings)

    # El pipeline ahora continua hasta SEMANTIC_GRAPH_BUILT (Prompt 8);
    # esta prueba solo verifica que DEPENDENCIES_BUILT en si mismo quedo
    # SUCCEEDED con el artefacto correcto, no que sea la etapa final.
    assert state.current_stage == PipelineStage.SEMANTIC_GRAPH_BUILT
    dependencies_executions = [
        s for s in state.stages if s.stage == PipelineStage.DEPENDENCIES_BUILT
    ]
    assert len(dependencies_executions) == 1
    assert dependencies_executions[0].status == StageStatus.SUCCEEDED

    dependencies_path = settings.runs_dir / state.run_id / "artifacts" / "03-dependencies.json"
    assert dependencies_path.is_file()
    artifact = DependencyArtifact.model_validate_json(
        dependencies_path.read_text(encoding="utf-8")
    )
    assert artifact.run_id == state.run_id
    assert artifact.source_package_hash == state.source_package_hash

    control_deps = [
        d for d in artifact.dependencies if d.dependency_type == DependencyType.CONTROL_DEPENDS_ON
    ]
    data_deps = [
        d for d in artifact.dependencies if d.dependency_type == DependencyType.DATA_DEPENDS_ON
    ]
    assert control_deps, "se esperaba al menos un CONTROL_DEPENDS_ON (MAIN-PARA PERFORM ...)"
    assert data_deps, "se esperaba al menos un DATA_DEPENDS_ON (WS-FLAG escrito/leido)"

    # IDs completos y versionados: pais::operativa::programa::version::hash12
    for dep in artifact.dependencies:
        assert dep.from_paragraph_id.startswith("program::AR::OP-TRF-PROPIA::DEPPROG1::1.0::")
        assert dep.to_paragraph_id.startswith("program::AR::OP-TRF-PROPIA::DEPPROG1::1.0::")
        assert "::paragraph::" in dep.from_paragraph_id
        assert "::paragraph::" in dep.to_paragraph_id


@pytest.mark.integration
def test_second_run_reuses_dependencies_artifact(tmp_path: Path) -> None:
    _require_jar()
    zip_path = tmp_path / "package.zip"
    write_zip(zip_path, _package_entries())
    settings = _settings(tmp_path)

    first_state = run_ingestion(zip_path, settings)
    assert first_state.current_stage == PipelineStage.SEMANTIC_GRAPH_BUILT

    dependencies_path = (
        settings.runs_dir / first_state.run_id / "artifacts" / "03-dependencies.json"
    )
    original_bytes = dependencies_path.read_bytes()

    # JAR inexistente en la segunda corrida: si DEPENDENCIES_BUILT
    # necesitara recomputar realmente algo que dependiera del parser,
    # fallaria (aunque DEPENDENCIES_BUILT nunca invoca el JAR, esto
    # tambien prueba que PARSED --que si lo necesitaria si tuviera que
    # reprocesar-- sigue reutilizando sus propios artefactos).
    settings_missing_jar = _settings(tmp_path, parser_jar_path=tmp_path / "does-not-exist.jar")
    second_state = run_ingestion(zip_path, settings_missing_jar, run_id=first_state.run_id)

    assert second_state.current_stage == PipelineStage.SEMANTIC_GRAPH_BUILT
    dependencies_executions = [
        s for s in second_state.stages if s.stage == PipelineStage.DEPENDENCIES_BUILT
    ]
    assert len(dependencies_executions) == 1
    assert dependencies_executions[0].status == StageStatus.SUCCEEDED
    assert dependencies_path.read_bytes() == original_bytes
