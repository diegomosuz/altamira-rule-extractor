"""Determinismo entre procesos JVM de las constantes figurativas (SPACES,
ZERO, HIGH-VALUES, LOW-VALUES, QUOTES) sobre statements ordinarios y sobre
VALUE de condiciones nivel 88: `ValueReferences.canonicalLiteralText`
nunca debe filtrar `Object.toString()` de `FigurativeConstantImpl`
(nombre de clase + hash de identidad, no reproducible entre JVMs -- ver
docs/LEVEL_88_SUPPORT.md).

COBOL embebido inline (mismo patron que `e2e_support.PROGRAM_SOURCE`):
no depende de ningun fixture externo nuevo bajo `examples/`, asi que
funciona en un checkout limpio sin fixtures adicionales.

No corre en la suite por defecto (marcado `integration`, requiere JAR +
Neo4j real). Cliente LLM fake unicamente (nunca un proveedor real, y aqui
ni siquiera se instala: el fixture produce cero candidatos V1)."""

from __future__ import annotations

import re
import stat
import zipfile
from pathlib import Path

import pytest

from altamira_extractor.pipeline.runner import run_ingestion
from altamira_extractor.pipeline.semantic_coverage_service import (
    compute_semantic_coverage_report,
    write_semantic_coverage_report,
)
from altamira_extractor.pipeline.semantic_effects_service import (
    compute_semantic_effects_artifact,
    write_semantic_effects_artifact,
)

from ..e2e_support import build_settings, require_jar

pytestmark = pytest.mark.integration

_OBJECT_TO_STRING_SHAPE = re.compile(r"[A-Za-z0-9.$]+@[0-9a-fA-F]+")

_MANIFEST_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<altamira-package schema-version="1.0">
  <country code="AR" name="Argentina"/>
  <application name="FigConst"/>
  <operation logical-name="OP-FIGCONST" description="Constantes figurativas"/>
  <implementation version="1.0">
    <entry-program>FIGCONST</entry-program>
  </implementation>
  <source format="FIXED" encoding="UTF-8"/>
  <parameter-tables>
    <table name="PARAM_DEMO" ddl="02-parametria/ddl/PARAM_DEMO.sql"/>
  </parameter-tables>
</altamira-package>
"""

_PROGRAM_SOURCE = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. FIGCONST.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-ALPHA        PIC X(10) VALUE SPACES.
       01 WS-NUM          PIC 9(5) VALUE ZERO.
       01 WS-HIGH         PIC X(4) VALUE HIGH-VALUES.
       01 WS-LOW          PIC X(4) VALUE LOW-VALUES.
       01 WS-QUOTE        PIC X(4) VALUE QUOTE.
       01 WS-ESTADO       PIC X.
          88 ESTADO-VACIO VALUE SPACE.
       01 WS-CONTADOR     PIC 9.
          88 CONTADOR-CERO VALUE ZERO.
       PROCEDURE DIVISION.
       MAIN-PARA.
           MOVE SPACES TO WS-ALPHA.
           MOVE ZEROS TO WS-NUM.
           MOVE HIGH-VALUES TO WS-HIGH.
           MOVE LOW-VALUES TO WS-LOW.
           MOVE QUOTES TO WS-QUOTE.
           SET ESTADO-VACIO TO TRUE.
           SET CONTADOR-CERO TO TRUE.
           GOBACK.
"""

_PARAM_DEMO_DDL = b"CREATE TABLE PARAM_DEMO (ID INT);\n"


def _regular_file_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.date_time = (2026, 1, 1, 0, 0, 0)
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def _write_figurative_constants_package(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(_regular_file_info("manifest.xml"), _MANIFEST_XML)
        zf.writestr(_regular_file_info("01-codigo/cobol/FIGCONST.cbl"), _PROGRAM_SOURCE)
        zf.writestr(_regular_file_info("02-parametria/ddl/PARAM_DEMO.sql"), _PARAM_DEMO_DDL)
    return path


def _assert_no_object_identity_leak(text: str, *, where: str) -> None:
    assert "FigurativeConstantImpl" not in text, f"{where} filtra FigurativeConstantImpl"
    assert not _OBJECT_TO_STRING_SHAPE.search(text), (
        f"{where} contiene una forma de Object.toString() (NombreDeClase@hex)"
    )


@pytest.mark.integration
def test_two_independent_pipeline_runs_produce_identical_canonical_json(tmp_path: Path) -> None:
    require_jar()
    zip_path = _write_figurative_constants_package(tmp_path / "figconst.zip")

    dir_a = tmp_path / "run_a"
    dir_b = tmp_path / "run_b"
    dir_a.mkdir()
    dir_b.mkdir()
    settings_a = build_settings(dir_a)
    settings_b = build_settings(dir_b)
    state_a = run_ingestion(zip_path, settings_a)
    state_b = run_ingestion(zip_path, settings_b)

    run_dir_a = settings_a.runs_dir / state_a.run_id
    run_dir_b = settings_b.runs_dir / state_b.run_id

    canonical_a = next((run_dir_a / "artifacts" / "02-canonical").rglob("*.json"))
    canonical_b = next((run_dir_b / "artifacts" / "02-canonical").rglob("*.json"))
    text_a = canonical_a.read_text(encoding="utf-8")
    text_b = canonical_b.read_text(encoding="utf-8")

    # 02-canonical no incluye run_id (confirmado en el contrato
    # CanonicalProgram): dos ejecuciones sobre el mismo ZIP deben producir
    # bytes identicos sin normalizar nada.
    assert text_a == text_b, "el JSON canonico debe ser identico entre dos ejecuciones del JAR"
    _assert_no_object_identity_leak(text_a, where="02-canonical (run A)")

    assert "SPACE" in text_a
    assert "ZERO" in text_a
    assert "HIGH-VALUE" in text_a
    assert "LOW-VALUE" in text_a
    assert "QUOTE" in text_a


@pytest.mark.integration
def test_semantic_effects_and_coverage_are_deterministic_across_runs(tmp_path: Path) -> None:
    """El parser (JAR, dos procesos JVM independientes via `run_ingestion`
    dos veces) ya se prueba deterministico en
    `test_two_independent_pipeline_runs_produce_identical_canonical_json`
    (02-canonical no tiene run_id: comparacion directa sin normalizar).

    Aqui se prueba la PUREZA del analizador Python
    (`compute_semantic_effects_artifact`/`compute_semantic_coverage_report`):
    llamado dos veces sobre el MISMO run_dir debe producir bytes
    identicos. Comparar entre DOS ejecuciones distintas de
    `run_ingestion` no es la prueba correcta para
    `SemanticCoverageReport`: su `artifact_hashes` incluye el hash de
    `01-inventory.json`/`06-candidates.json`/etc., que legitimamente
    embeben `run_id` y por lo tanto varian de corrida a corrida -- esa
    variacion ya esta documentada como cascada inevitable (ver auditoria
    previa de reproducibilidad), no una regresion de esta correccion."""
    require_jar()
    zip_path = _write_figurative_constants_package(tmp_path / "figconst.zip")

    run_dir_settings = tmp_path / "run"
    run_dir_settings.mkdir()
    settings = build_settings(run_dir_settings)
    state = run_ingestion(zip_path, settings)
    run_dir = settings.runs_dir / state.run_id

    effects_first = compute_semantic_effects_artifact(run_dir, state.run_id)
    effects_second = compute_semantic_effects_artifact(run_dir, state.run_id)
    assert effects_first.model_dump(mode="json") == effects_second.model_dump(mode="json"), (
        "compute_semantic_effects_artifact debe ser puro: mismo run_dir, mismo resultado"
    )
    path_effects = write_semantic_effects_artifact(run_dir, effects_first)
    text_effects = path_effects.read_text(encoding="utf-8")
    _assert_no_object_identity_leak(text_effects, where="semantic-effects.json")

    coverage_first = compute_semantic_coverage_report(run_dir, state.run_id)
    coverage_second = compute_semantic_coverage_report(run_dir, state.run_id)
    assert coverage_first.model_dump(mode="json") == coverage_second.model_dump(mode="json"), (
        "compute_semantic_coverage_report debe ser puro: mismo run_dir, mismo resultado"
    )
    path_coverage = write_semantic_coverage_report(run_dir, coverage_first)
    text_coverage = path_coverage.read_text(encoding="utf-8")
    _assert_no_object_identity_leak(text_coverage, where="semantic-coverage.json")

    # SET condicion TO TRUE con constante figurativa: SET_CONDITION_TRUE
    # nunca ASSIGN_LITERAL; condition_values canonicos, sin propagacion.
    consaldo_effects = next(p for p in effects_first.programs if p.program == "FIGCONST").effects
    set_condition_effects = [e for e in consaldo_effects if e.kind == "SET_CONDITION_TRUE"]
    assert {e.condition_values[0] for e in set_condition_effects} == {"SPACE", "ZERO"}
    assert all(e.kind != "ASSIGN_LITERAL" or e.literal != "true" for e in consaldo_effects)
