"""E2E hermetico de la direccion SQL demostrada (Fase 15B3-C3-B, seccion
23): "package sintetico versionado -> parser Java real -> canonical ->
DATA_DEPENDS_ON -> semantic graph en Neo4j real efimero -> V2 ->
06-candidates.json -> ContextPackage -> RuleDraft -> guardrail",
ejercitando exclusivamente `run_ingestion` y los stages productivos reales
(`runner.py`) -- nunca un pipeline alternativo construido para este test.

Ejemplo trabajado de la seccion 11 del enunciado: EXEC SQL SELECT ESTADO
INTO :WS-ESTADO-DB ... WHERE CUENTA = :WS-CUENTA escribe WS-ESTADO-DB en
CONSULTAR-ESTADO-PARA; IF WS-ESTADO-DB = 'B' MOVE 'R' TO
WS-ESTADO-OPERACION en EVALUAR-ESTADO-PARA lee ese valor -- la regla
resultante sigue siendo STATE_TRANSITION (familia preexistente), NUNCA una
familia SQL nueva. Un tercer paragraph (CONSULTA-JOIN-PARA) ejercita un
JOIN explicito para probar que queda `unsupported` sin producir hechos
parciales falsos (tabla, direccion o dependencia fabricada).

Hermetismo: mismo patron que
`test_hermetic_enhanced_candidates_e2e.py` -- JDK 17 + JAR real
(`require_jar`), Neo4j efimero aislado, `_env_file=None`
(`build_hermetic_settings`), proveedor LLM real bloqueado a nivel de
proceso + red bloqueada salvo localhost/Neo4j
(`hermetic_llm_and_network_guard`)."""

from __future__ import annotations

import hashlib
import os
import stat
import zipfile
from pathlib import Path

import pytest

from altamira_extractor.config import Settings
from altamira_extractor.contracts.candidate import CandidateArtifact
from altamira_extractor.contracts.candidate_promotion_assessment import (
    CandidateSource,
    UnifiedRuleFamily,
)
from altamira_extractor.contracts.canonical import CanonicalProgram
from altamira_extractor.contracts.context_package import ContextPackage
from altamira_extractor.contracts.dependencies import DependencyArtifact
from altamira_extractor.contracts.enums import DependencyType, GuardrailVerdict, NodeLabel
from altamira_extractor.contracts.guardrail_candidate import GuardrailCandidateArtifact
from altamira_extractor.contracts.rule_draft import RuleDraft
from altamira_extractor.contracts.semantic_effects import SemanticEffectKind

from .e2e_support import require_jar
from .hermetic_llm_support import build_hermetic_settings, hermetic_llm_and_network_guard


def _artifact_filename(candidate_id: str) -> str:
    """Misma formula que en `test_hermetic_enhanced_candidates_e2e.py`
    (sha256 del candidate_id), nunca reimportada de un modulo privado."""
    return hashlib.sha256(candidate_id.encode("utf-8")).hexdigest() + ".json"


pytestmark = pytest.mark.integration

_MANIFEST_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<altamira-package schema-version="1.0">
  <country code="AR" name="Argentina"/>
  <application name="Transferencias"/>
  <operation logical-name="OP-SQL-FLOW" description="Direccion SQL 15B3-C3-B"/>
  <implementation version="1.0">
    <entry-program>SQLE2E01</entry-program>
  </implementation>
  <source format="FIXED" encoding="UTF-8"/>
  <parameter-tables>
    <table name="PARAM_DEMO" ddl="02-parametria/ddl/PARAM_DEMO.sql"/>
  </parameter-tables>
</altamira-package>
"""

# Tres paragraphs:
# - CONSULTAR-ESTADO-PARA: EXEC SQL SELECT ESTADO INTO :WS-ESTADO-DB FROM
#   CUENTAS WHERE CUENTA = :WS-CUENTA -- direccion demostrada, output ->
#   WS-ESTADO-DB, input/predicate -> WS-CUENTA.
# - EVALUAR-ESTADO-PARA: IF WS-ESTADO-DB = 'B' MOVE 'R' TO
#   WS-ESTADO-OPERACION -- lee el valor cargado por SQL, WS-ESTADO-OPERACION
#   matchea status-name (config/semantic-tags.yml) -> STATE_TRANSITION.
# - CONSULTA-JOIN-PARA: JOIN explicito (CUENTAS/MOVIMIENTOS) -- debe quedar
#   unsupported, sin tabla parcial ni direccion fabricada.
_PROGRAM_SOURCE = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. SQLE2E01.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-CUENTA           PIC X(10).
       01 WS-ESTADO-DB        PIC X(1).
       01 WS-ESTADO-OPERACION PIC X(1) VALUE SPACES.
       PROCEDURE DIVISION.
       MAIN-PARA.
           PERFORM CONSULTAR-ESTADO-PARA.
           PERFORM EVALUAR-ESTADO-PARA.
           PERFORM CONSULTA-JOIN-PARA.
           GOBACK.
       CONSULTAR-ESTADO-PARA.
           EXEC SQL
               SELECT ESTADO
               INTO :WS-ESTADO-DB
               FROM CUENTAS
               WHERE CUENTA = :WS-CUENTA
           END-EXEC.
       EVALUAR-ESTADO-PARA.
           IF WS-ESTADO-DB = 'B'
               MOVE 'R' TO WS-ESTADO-OPERACION
           END-IF.
       CONSULTA-JOIN-PARA.
           EXEC SQL
               SELECT A.SALDO
               FROM CUENTAS A JOIN MOVIMIENTOS B
               ON A.ID = B.ID
           END-EXEC.
"""


_PARAM_DEMO_DDL = b"CREATE TABLE PARAM_DEMO (ID INT);\n"


def _regular_file_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.date_time = (2026, 1, 1, 0, 0, 0)
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def _write_sql_flow_package_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(_regular_file_info("manifest.xml"), _MANIFEST_XML)
        zf.writestr(_regular_file_info("01-codigo/cobol/SQLE2E01.cbl"), _PROGRAM_SOURCE)
        zf.writestr(_regular_file_info("02-parametria/ddl/PARAM_DEMO.sql"), _PARAM_DEMO_DDL)
    return path


def _sql_flow_settings(tmp_path: Path, *, run_label: str) -> Settings:
    return build_hermetic_settings(
        tmp_path / f"hermetic_data_{run_label}",
        NEO4J_URI=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        NEO4J_USER=os.environ.get("NEO4J_USER", "neo4j"),
        NEO4J_PASSWORD=os.environ.get("NEO4J_PASSWORD", "neo4j"),
        NEO4J_DATABASE=os.environ.get("NEO4J_DATABASE", "neo4j"),
        enhanced_candidates_enabled=True,
    )


def _run(tmp_path: Path, *, run_label: str):
    from altamira_extractor.pipeline.runner import run_ingestion

    zip_path = _write_sql_flow_package_zip(tmp_path / f"package_{run_label}.zip")
    settings = _sql_flow_settings(tmp_path, run_label=run_label)
    with hermetic_llm_and_network_guard():
        state = run_ingestion(source_zip=zip_path, settings=settings)
    return state, settings


def test_sql_directed_data_flow_reaches_state_transition_rule_end_to_end(
    tmp_path: Path,
) -> None:
    require_jar()
    state, settings = _run(tmp_path, run_label="main")

    assert state.current_stage.value == "COMPLETED", [
        (s.stage.value, s.status.value, s.error) for s in state.stages
    ]
    assert all(s.status.value != "FAILED" for s in state.stages)

    run_dir = settings.runs_dir / state.run_id

    # --- (C) CanonicalStatement: direccion SQL demostrada -----------------
    canonical_files = sorted((run_dir / "artifacts" / "02-canonical").glob("**/*.json"))
    assert len(canonical_files) == 1, canonical_files
    program = CanonicalProgram.model_validate_json(
        canonical_files[0].read_text(encoding="utf-8")
    )
    paragraphs_by_name = {p.name: p for p in program.paragraphs}

    consultar = paragraphs_by_name["CONSULTAR-ESTADO-PARA"]
    sql_statements = [s for s in consultar.statements if s.sql_access]
    assert len(sql_statements) == 1, consultar.statements
    sql_statement = sql_statements[0]
    assert sql_statement.variables_written == ["WS-ESTADO-DB"]
    assert sql_statement.variables_read == ["WS-CUENTA"]
    access = sql_statement.sql_access[0]
    assert access.output_host_variables == ["WS-ESTADO-DB"]
    assert access.input_host_variables == ["WS-CUENTA"]
    assert access.predicate_host_variables == ["WS-CUENTA"]

    # --- (G) JOIN: unsupported, nunca tabla/direccion parcial fabricada ---
    join_para = paragraphs_by_name["CONSULTA-JOIN-PARA"]
    assert all(not s.sql_access for s in join_para.statements), join_para.statements
    assert any(
        "CONSULTA-JOIN-PARA" in message and "EXEC SQL" in message
        for message in program.unsupported_constructs
    ), program.unsupported_constructs

    # --- (D) SemanticEffect EXECUTE_SQL: misma direccion ya demostrada ----
    from altamira_extractor.pipeline.semantic_effects_service import (
        compute_semantic_effects_artifact,
    )

    semantic_effects = compute_semantic_effects_artifact(run_dir, state.run_id)
    sql_effects = [
        effect
        for program_effects in semantic_effects.programs
        for effect in program_effects.effects
        if effect.kind == SemanticEffectKind.EXECUTE_SQL
        and effect.source_reference.paragraph == "CONSULTAR-ESTADO-PARA"
    ]
    assert len(sql_effects) == 1, sql_effects
    assert sql_effects[0].writes == ["WS-ESTADO-DB"]
    assert sql_effects[0].reads == ["WS-CUENTA"]
    assert sql_effects[0].sql_predicate_text is not None
    assert "WS-CUENTA" in sql_effects[0].sql_predicate_text

    join_sql_effects = [
        effect
        for program_effects in semantic_effects.programs
        for effect in program_effects.effects
        if effect.kind == SemanticEffectKind.EXECUTE_SQL
        and effect.source_reference.paragraph == "CONSULTA-JOIN-PARA"
    ]
    assert join_sql_effects == [], "JOIN nunca debe producir un EXECUTE_SQL con tabla/direccion"

    # --- (E) DATA_DEPENDS_ON cruza de la sentencia SQL al consumidor -------
    dependencies_path = run_dir / "artifacts" / "03-dependencies.json"
    dependency_artifact = DependencyArtifact.model_validate_json(
        dependencies_path.read_text(encoding="utf-8")
    )
    sql_to_decision = [
        dep
        for dep in dependency_artifact.dependencies
        if dep.dependency_type == DependencyType.DATA_DEPENDS_ON
        and dep.from_paragraph_id.endswith("::paragraph::CONSULTAR-ESTADO-PARA")
        and dep.to_paragraph_id.endswith("::paragraph::EVALUAR-ESTADO-PARA")
    ]
    assert len(sql_to_decision) == 1, dependency_artifact.dependencies
    assert "WS-ESTADO-DB" in sql_to_decision[0].variables

    # --- (G, complemento) grafo semantico: sin nodo Table para el JOIN ----
    from altamira_extractor.contracts.semantic_graph import SemanticGraph

    graph_path = run_dir / "artifacts" / "04-semantic-graph.json"
    semantic_graph = SemanticGraph.model_validate_json(graph_path.read_text(encoding="utf-8"))
    # PARAM_DEMO existe siempre como nodo Table/ParameterTable (declarado en
    # manifest.xml, requerido por VALIDATED independientemente de si el SQL
    # lo referencia) -- MOVIMIENTOS (la tabla del JOIN unsupported) nunca
    # debe aparecer.
    table_names = {
        node.properties.get("name")
        for node in semantic_graph.nodes
        if NodeLabel.TABLE in node.labels
    }
    assert table_names == {"CUENTAS", "PARAM_DEMO"}, table_names

    # --- (A)/(B)/(F) 06-candidates.json: sigue siendo STATE_TRANSITION ----
    candidates_path = run_dir / "artifacts" / "06-candidates.json"
    artifact = CandidateArtifact.model_validate_json(candidates_path.read_text(encoding="utf-8"))
    transicion_candidates = [
        c for c in artifact.candidates if c.paragraph_name == "EVALUAR-ESTADO-PARA"
    ]
    assert len(transicion_candidates) == 1, artifact.candidates
    transicion = transicion_candidates[0]
    assert transicion.rule_family == UnifiedRuleFamily.STATE_TRANSITION
    assert transicion.candidate_source == CandidateSource.V2
    assert transicion.outcome_code == "R"
    assert transicion.evidence_ids != []
    assert transicion.decision_id

    # Ni CONSULTAR-ESTADO-PARA (solo EXEC SQL, sin IF/EVALUATE) ni
    # CONSULTA-JOIN-PARA (JOIN unsupported) producen Decision alguna: el
    # unico candidato del run es el STATE_TRANSITION ya verificado.
    assert len(artifact.candidates) == 1, artifact.candidates

    # --- ContextPackage/RuleDraft/guardrail para el candidato SQL->STATE_TRANSITION
    artifact_filename = _artifact_filename(transicion.candidate_id)
    context_path = run_dir / "artifacts" / "07-context" / artifact_filename
    assert context_path.is_file()
    context_package = ContextPackage.model_validate_json(context_path.read_text(encoding="utf-8"))
    assert context_package.candidate.candidate_id == transicion.candidate_id

    rule_draft_path = run_dir / "artifacts" / "08-rule-drafts" / artifact_filename
    assert rule_draft_path.is_file()
    RuleDraft.model_validate_json(rule_draft_path.read_text(encoding="utf-8"))

    guardrail_path = run_dir / "artifacts" / "09-guardrails" / artifact_filename
    assert guardrail_path.is_file()
    guardrail_candidate = GuardrailCandidateArtifact.model_validate_json(
        guardrail_path.read_text(encoding="utf-8")
    )
    assert guardrail_candidate.candidate_id == transicion.candidate_id
    assert guardrail_candidate.guardrail_report.verdict == GuardrailVerdict.EVIDENCE_VALIDATED


def test_sql_directed_data_flow_repeated_run_produces_same_candidate_ids(
    tmp_path: Path,
) -> None:
    """(H) Dos ejecuciones del mismo paquete (dos run_id distintos)
    producen exactamente los mismos candidate_id, en el mismo orden."""
    require_jar()
    state_1, settings_1 = _run(tmp_path, run_label="rep1")
    state_2, settings_2 = _run(tmp_path, run_label="rep2")

    assert state_1.current_stage.value == "COMPLETED"
    assert state_2.current_stage.value == "COMPLETED"

    artifact_1 = CandidateArtifact.model_validate_json(
        (settings_1.runs_dir / state_1.run_id / "artifacts" / "06-candidates.json").read_text(
            encoding="utf-8"
        )
    )
    artifact_2 = CandidateArtifact.model_validate_json(
        (settings_2.runs_dir / state_2.run_id / "artifacts" / "06-candidates.json").read_text(
            encoding="utf-8"
        )
    )

    ids_1 = [c.candidate_id for c in artifact_1.candidates]
    ids_2 = [c.candidate_id for c in artifact_2.candidates]
    assert ids_1 == ids_2
    assert ids_1 == sorted(ids_1)
    assert len(ids_1) >= 1
