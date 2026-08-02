"""Distincion estructural GOBACK/STOP RUN/EXIT PROGRAM y su efecto en la
propagacion interprocedural RETURNING/BY REFERENCE (Fase 7b, real ProLeap
+ JAR real, ver docs/INTERPROCEDURAL_PROPAGATION.md).

Complementa `ProgramTerminationClassificationTest.java` (clasificacion
estructural pura, items 1-4/16 de la auditoria de cierre) demostrando el
efecto END-TO-END sobre `InterproceduralPropagationArtifact` (items 5-8):
la distincion solo importa en la practica si efectivamente cambia el
resultado de RETURNING/BY REFERENCE de salida, y esto solo puede
verificarse con canonical JSON producido por el parser Java real (nunca
con `CanonicalStatement` construidos a mano en Python, que ya cubren en
detalle el resto de la matriz de barreras -- ver
`tests/pipeline/test_interprocedural_propagation_analyzer.py`).

No corre en la suite por defecto (marcado `integration`, requiere JAR
real). Nunca invoca un proveedor LLM (esta fase no llega a
RULE_DRAFTS_GENERATED)."""

from __future__ import annotations

import hashlib
import stat
import zipfile
from pathlib import Path

import pytest

from altamira_extractor.pipeline.interprocedural_propagation_service import (
    compute_interprocedural_propagation_artifact,
    write_interprocedural_propagation_artifact,
)
from altamira_extractor.pipeline.runner import run_ingestion

from ..e2e_support import build_settings, require_jar

pytestmark = pytest.mark.integration

_MANIFEST_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<altamira-package schema-version="1.0">
  <country code="AR" name="Argentina"/>
  <application name="TermProp"/>
  <operation logical-name="OP-TERMPROP" description="Terminadores de programa"/>
  <implementation version="1.0">
    <entry-program>TERMCALL</entry-program>
  </implementation>
  <source format="FIXED" encoding="UTF-8"/>
  <parameter-tables>
    <table name="PARAM_DEMO" ddl="02-parametria/ddl/PARAM_DEMO.sql"/>
  </parameter-tables>
</altamira-package>
"""

# TERMCALL invoca a los tres callees; cada uno demuestra un
# program_termination_kind distinto como UNICO terminador final
# incondicional.
_TERMCALL_SOURCE = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. TERMCALL.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-ARG PIC X(10).
       01 WS-RESULT-GOBACK PIC X(10).
       01 WS-RESULT-EXIT PIC X(10).
       01 WS-RESULT-STOP PIC X(10).
       PROCEDURE DIVISION.
       MAIN-PARA.
           MOVE 'SEED' TO WS-ARG
           CALL 'TERMGOBK' USING BY CONTENT WS-ARG RETURNING WS-RESULT-GOBACK
           CALL 'TERMEXIT' USING BY CONTENT WS-ARG RETURNING WS-RESULT-EXIT
           CALL 'TERMSTOP' USING BY REFERENCE WS-ARG RETURNING WS-RESULT-STOP
           GOBACK.
"""

# GOBACK final, incondicional, unico terminador: retorno valido.
_TERMGOBK_SOURCE = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. TERMGOBK.
       DATA DIVISION.
       LINKAGE SECTION.
       01 LK-IN PIC X(10).
       01 LK-RESULT PIC X(10).
       PROCEDURE DIVISION USING LK-IN RETURNING LK-RESULT.
       MAIN-PARA.
           MOVE 'GOBACK-OK' TO LK-RESULT
           GOBACK.
"""

# EXIT PROGRAM final, incondicional, unico terminador: retorno valido,
# identico a GOBACK.
_TERMEXIT_SOURCE = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. TERMEXIT.
       DATA DIVISION.
       LINKAGE SECTION.
       01 LK-IN PIC X(10).
       01 LK-RESULT PIC X(10).
       PROCEDURE DIVISION USING LK-IN RETURNING LK-RESULT.
       MAIN-PARA.
           MOVE 'EXIT-OK' TO LK-RESULT
           EXIT PROGRAM.
"""

# STOP RUN final, incondicional, unico terminador: certeza estructural
# de que NUNCA retorna control al caller -- bloquea tanto RETURNING como
# BY REFERENCE de salida.
_TERMSTOP_SOURCE = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. TERMSTOP.
       DATA DIVISION.
       LINKAGE SECTION.
       01 LK-IN PIC X(10).
       01 LK-RESULT PIC X(10).
       PROCEDURE DIVISION USING LK-IN RETURNING LK-RESULT.
       MAIN-PARA.
           MOVE 'NEVER-SEEN' TO LK-IN
           MOVE 'NEVER-SEEN' TO LK-RESULT
           STOP RUN.
"""

_PARAM_DEMO_DDL = b"CREATE TABLE PARAM_DEMO (ID INT);\n"


def _regular_file_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.date_time = (2026, 1, 1, 0, 0, 0)
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def _write_package(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(_regular_file_info("manifest.xml"), _MANIFEST_XML)
        zf.writestr(_regular_file_info("01-codigo/cobol/TERMCALL.cbl"), _TERMCALL_SOURCE)
        zf.writestr(_regular_file_info("01-codigo/cobol/TERMGOBK.cbl"), _TERMGOBK_SOURCE)
        zf.writestr(_regular_file_info("01-codigo/cobol/TERMEXIT.cbl"), _TERMEXIT_SOURCE)
        zf.writestr(_regular_file_info("01-codigo/cobol/TERMSTOP.cbl"), _TERMSTOP_SOURCE)
        zf.writestr(_regular_file_info("02-parametria/ddl/PARAM_DEMO.sql"), _PARAM_DEMO_DDL)
    return path


def _run_and_compute(tmp_path: Path, *, label: str):
    require_jar()
    zip_path = _write_package(tmp_path / f"{label}.zip")
    settings = build_settings(tmp_path)
    state = run_ingestion(zip_path, settings)
    assert state.current_stage.value == "PARSED" or state.current_stage.value not in (
        "RECEIVED",
        "VALIDATED",
        "EXTRACTED",
        "INVENTORIED",
    ), f"run no alcanzo PARSED: {state.current_stage.value}"
    run_dir = settings.runs_dir / state.run_id
    artifact = compute_interprocedural_propagation_artifact(run_dir, state.run_id)
    write_interprocedural_propagation_artifact(run_dir, artifact)
    return artifact, run_dir


def _returning_fact(artifact, program: str):
    pa = next(p for p in artifact.program_analyses if p.program == program)
    matches = [
        f for f in pa.exit_facts if f.kind.value in ("RETURNING_FACT", "INVALIDATION")
    ]
    returning = [f for f in matches if f.fact_id.endswith("::return::returning")]
    assert len(returning) == 1, f"esperaba 1 hecho RETURNING para {program}: {returning}"
    return returning[0]


def _by_reference_fact(artifact, program: str):
    pa = next(p for p in artifact.program_analyses if p.program == program)
    candidates = [f for f in pa.exit_facts if not f.fact_id.endswith("::return::returning")]
    assert len(candidates) == 1, f"esperaba 1 hecho BY REFERENCE para {program}: {candidates}"
    return candidates[0]


@pytest.mark.integration
def test_goback_final_permits_deterministic_returning(tmp_path: Path) -> None:
    artifact, _ = _run_and_compute(tmp_path, label="goback")
    ret = _returning_fact(artifact, "TERMGOBK")
    assert ret.status.value == "PROPAGATED"
    assert ret.literal == "GOBACK-OK"
    assert ret.barriers == []


@pytest.mark.integration
def test_exit_program_final_permits_deterministic_returning(tmp_path: Path) -> None:
    artifact, _ = _run_and_compute(tmp_path, label="exitprogram")
    ret = _returning_fact(artifact, "TERMEXIT")
    assert ret.status.value == "PROPAGATED"
    assert ret.literal == "EXIT-OK"
    assert ret.barriers == []


@pytest.mark.integration
def test_stop_run_blocks_returning(tmp_path: Path) -> None:
    artifact, _ = _run_and_compute(tmp_path, label="stoprun")
    ret = _returning_fact(artifact, "TERMSTOP")
    assert ret.status.value == "BLOCKED"
    assert ret.literal is None
    assert ret.barriers == ["NON_RETURNING_TERMINATION"]


@pytest.mark.integration
def test_stop_run_blocks_by_reference_output(tmp_path: Path) -> None:
    artifact, _ = _run_and_compute(tmp_path, label="stoprun-byref")
    output = _by_reference_fact(artifact, "TERMSTOP")
    assert output.status.value == "BLOCKED"
    assert output.literal is None
    assert output.barriers == ["NON_RETURNING_TERMINATION"]
    assert output.kind.value == "BY_REFERENCE_OUTPUT"


@pytest.mark.integration
def test_canonical_json_classifies_all_three_termination_kinds_structurally(
    tmp_path: Path,
) -> None:
    """Confirma, sobre el JSON canonico real (nunca reconstruido a mano),
    que los tres programas terminan con kind=PROGRAM_TERMINATION y el
    program_termination_kind correcto -- puente directo entre la
    clasificacion Java (items 1-3, ya probada en
    ProgramTerminationClassificationTest.java) y el efecto Fase 7 (items
    5-8, arriba)."""
    require_jar()
    zip_path = _write_package(tmp_path / "classify.zip")
    settings = build_settings(tmp_path)
    state = run_ingestion(zip_path, settings)
    run_dir = settings.runs_dir / state.run_id
    canonical_dir = run_dir / "artifacts" / "02-canonical"

    expected = {
        "TERMGOBK.cbl.json": "GOBACK",
        "TERMEXIT.cbl.json": "EXIT_PROGRAM",
        "TERMSTOP.cbl.json": "STOP_RUN",
    }
    found = {p.name for p in canonical_dir.rglob("*.json")}
    for filename, expected_kind in expected.items():
        matches = [p for p in canonical_dir.rglob(filename)]
        assert matches, f"{filename} no encontrado entre {found}"
        text = matches[0].read_text(encoding="utf-8")
        assert '"kind" : "PROGRAM_TERMINATION"' in text
        assert f'"program_termination_kind" : "{expected_kind}"' in text
        # Nunca se filtra un identity-hash de objeto Java ni se depende
        # de source_text para la clasificacion -- ver auditoria de
        # cierre, Parte 2.
        assert "@" not in text.split('"program_termination_kind"')[1][:80]


@pytest.mark.integration
def test_two_independent_pipeline_runs_produce_identical_propagation_artifact(
    tmp_path: Path,
) -> None:
    """Item 17 (auditoria de cierre): dos procesos JVM independientes
    (dos invocaciones separadas de `run_ingestion`, cada una lanzando su
    propio proceso Java real) deben producir un
    InterproceduralPropagationArtifact byte a byte identico -- ninguna
    fuga de identity-hash de objeto Java (que variaria entre JVMs) llega
    hasta program_termination_kind ni hasta el resultado de propagacion."""
    require_jar()
    zip_path_a = _write_package(tmp_path / "det_a.zip")
    zip_path_b = _write_package(tmp_path / "det_b.zip")
    assert hashlib.sha256(zip_path_a.read_bytes()).digest() == hashlib.sha256(
        zip_path_b.read_bytes()
    ).digest()

    run_a_dir = tmp_path / "run_a"
    run_b_dir = tmp_path / "run_b"
    run_a_dir.mkdir()
    run_b_dir.mkdir()
    settings_a = build_settings(run_a_dir)
    settings_b = build_settings(run_b_dir)
    state_a = run_ingestion(zip_path_a, settings_a)
    state_b = run_ingestion(zip_path_b, settings_b)

    run_dir_a = settings_a.runs_dir / state_a.run_id
    run_dir_b = settings_b.runs_dir / state_b.run_id

    artifact_a = compute_interprocedural_propagation_artifact(run_dir_a, state_a.run_id)
    artifact_b = compute_interprocedural_propagation_artifact(run_dir_b, state_b.run_id)

    dump_a = artifact_a.model_dump(mode="json", exclude={"run_id", "source_artifact_hashes"})
    dump_b = artifact_b.model_dump(mode="json", exclude={"run_id", "source_artifact_hashes"})
    assert dump_a == dump_b, (
        "dos ejecuciones JVM independientes sobre el mismo ZIP deben producir el mismo "
        "InterproceduralPropagationArtifact (salvo run_id/source_artifact_hashes, que "
        "legitimamente varian por corrida)"
    )

    ret_goback_a = _returning_fact(artifact_a, "TERMGOBK")
    ret_goback_b = _returning_fact(artifact_b, "TERMGOBK")
    assert ret_goback_a.literal == ret_goback_b.literal == "GOBACK-OK"
