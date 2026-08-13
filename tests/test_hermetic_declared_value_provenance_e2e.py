"""E2E hermetico de declared value provenance (Fase 15B3-C5-B, seccion
27): "package sintetico versionado -> parser Java real -> canonical ->
semantic graph en Neo4j real efimero -> V1/Q0 -> 06-candidates.json ->
ContextPackage (con evidencia declared_value_context) -> RuleDraft ->
guardrail", ejercitando exclusivamente `run_ingestion` y los stages
productivos reales (`runner.py`) -- nunca un pipeline alternativo
construido para este test.

Caso obligatorio del enunciado (identico al fixture de ground truth
`config/ground_truth/fixtures/gt_declared_value_return_code_001.cbl`):
`01 WS-RIESGO-MAXIMO PIC 9 VALUE 3.` seguido de
`IF WS-RIESGO > WS-RIESGO-MAXIMO MOVE 'PE04' TO WS-COD-RETORNO END-IF`.
La regla resultante es RETURN_CODE via Q0/V1 (`enhanced_candidates_enabled`
NUNCA se activa en este test -- prueba que declared_value provenance NO
depende de la deteccion ampliada); el enriquecimiento SOLO agrega un
evidence_id adicional a `ContextPackageDecision.evidence_ids`, sin tocar
candidate_id/condition/effect/rule_family.

Hermetismo: mismo patron que `test_hermetic_sqlcode_causal_evidence_e2e.py`
-- JDK 17 + JAR real (`require_jar`), Neo4j efimero aislado, `_env_file=None`
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
from altamira_extractor.contracts.context_package import ContextPackage
from altamira_extractor.contracts.enums import GuardrailVerdict
from altamira_extractor.contracts.guardrail_candidate import GuardrailCandidateArtifact
from altamira_extractor.contracts.rule_draft import RuleDraft
from altamira_extractor.pipeline.evidence_catalog import build_evidence_catalog

from .e2e_support import require_jar
from .hermetic_llm_support import build_hermetic_settings, hermetic_llm_and_network_guard


def _artifact_filename(candidate_id: str) -> str:
    return hashlib.sha256(candidate_id.encode("utf-8")).hexdigest() + ".json"


pytestmark = pytest.mark.integration

_MANIFEST_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<altamira-package schema-version="1.0">
  <country code="AR" name="Argentina"/>
  <application name="Riesgo"/>
  <operation logical-name="OP-DECLARED-VALUE" description="Declared value provenance 15B3-C5-B"/>
  <implementation version="1.0">
    <entry-program>GTDVAL01</entry-program>
  </implementation>
  <source format="FIXED" encoding="UTF-8"/>
  <parameter-tables>
    <table name="PARAM_DEMO" ddl="02-parametria/ddl/PARAM_DEMO.sql"/>
  </parameter-tables>
</altamira-package>
"""

_PROGRAM_SOURCE = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. GTDVAL01.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-RIESGO PIC 9 VALUE 1.
       01 WS-RIESGO-MAXIMO PIC 9 VALUE 3.
       01 WS-COD-RETORNO PIC X(4) VALUE SPACES.
       PROCEDURE DIVISION.
       MAIN-PARA.
           IF WS-RIESGO > WS-RIESGO-MAXIMO
               MOVE 'PE04' TO WS-COD-RETORNO
           END-IF
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


def _write_declared_value_package_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(_regular_file_info("manifest.xml"), _MANIFEST_XML)
        zf.writestr(_regular_file_info("01-codigo/cobol/GTDVAL01.cbl"), _PROGRAM_SOURCE)
        zf.writestr(_regular_file_info("02-parametria/ddl/PARAM_DEMO.sql"), _PARAM_DEMO_DDL)
    return path


def _declared_value_settings(tmp_path: Path, *, run_label: str) -> Settings:
    # enhanced_candidates_enabled NUNCA se activa aqui (explicito): prueba
    # que declared_value provenance enriquece una regla RETURN_CODE
    # producida por el path V1/Q0, sin ninguna dependencia de la
    # deteccion ampliada. (Fase 15B4-CANDIDATE-QUALITY-5E: el default
    # global paso a True; se fija False explicitamente para preservar
    # el aislamiento que este test documenta.)
    return build_hermetic_settings(
        tmp_path / f"hermetic_data_{run_label}",
        NEO4J_URI=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        NEO4J_USER=os.environ.get("NEO4J_USER", "neo4j"),
        NEO4J_PASSWORD=os.environ.get("NEO4J_PASSWORD", "neo4j"),
        NEO4J_DATABASE=os.environ.get("NEO4J_DATABASE", "neo4j"),
        enhanced_candidates_enabled=False,
    )


def _run(tmp_path: Path, *, run_label: str):
    from altamira_extractor.pipeline.runner import run_ingestion

    zip_path = _write_declared_value_package_zip(tmp_path / f"package_{run_label}.zip")
    settings = _declared_value_settings(tmp_path, run_label=run_label)
    with hermetic_llm_and_network_guard():
        state = run_ingestion(source_zip=zip_path, settings=settings)
    return state, settings


def test_declared_value_provenance_reaches_return_code_rule_end_to_end(tmp_path: Path) -> None:
    require_jar()
    state, settings = _run(tmp_path, run_label="main")

    assert state.current_stage.value == "COMPLETED", [
        (s.stage.value, s.status.value, s.error) for s in state.stages
    ]
    assert all(s.status.value != "FAILED" for s in state.stages)

    run_dir = settings.runs_dir / state.run_id

    # --- (A) CanonicalDataItem.declared_value == "3" ------------------------
    from altamira_extractor.contracts.canonical import CanonicalProgram

    canonical_path = next((run_dir / "artifacts" / "02-canonical").rglob("GTDVAL01.cbl.json"))
    program = CanonicalProgram.model_validate_json(canonical_path.read_text(encoding="utf-8"))
    riesgo_maximo = next(item for item in program.data_items if item.name == "WS-RIESGO-MAXIMO")
    assert riesgo_maximo.declared_value == "3"

    # --- (B)/(C) candidate_id estable, familia RETURN_CODE (default V1/Q0,
    # sin enhanced_candidates_enabled) ---------------------------------------
    candidates_path = run_dir / "artifacts" / "06-candidates.json"
    artifact = CandidateArtifact.model_validate_json(candidates_path.read_text(encoding="utf-8"))
    assert len(artifact.candidates) >= 1, artifact.candidates
    candidate = artifact.candidates[0]
    assert candidate.rule_family == UnifiedRuleFamily.RETURN_CODE
    assert candidate.candidate_source == CandidateSource.V1
    assert candidate.decision_id

    # --- (D) condition original, sin sustitucion por 3 -----------------------
    assert candidate.condition == "WS-RIESGO>WS-RIESGO-MAXIMO"

    # --- (E) effect original --------------------------------------------------
    assert candidate.outcome_code == "PE04"

    # --- ContextPackage/RuleDraft/guardrail -----------------------------------
    artifact_filename = _artifact_filename(candidate.candidate_id)
    context_path = run_dir / "artifacts" / "07-context" / artifact_filename
    assert context_path.is_file()
    context_package = ContextPackage.model_validate_json(context_path.read_text(encoding="utf-8"))
    assert context_package.candidate.candidate_id == candidate.candidate_id
    assert context_package.decision is not None
    assert context_package.decision.expression == "WS-RIESGO>WS-RIESGO-MAXIMO"

    # --- (F) ContextPackageDecision.evidence_ids incluye declared_value_context --
    assert len(context_package.decision.evidence_ids) >= 2, context_package.decision.evidence_ids
    known_evidence_ids = {entry.evidence_id for entry in context_package.evidence}
    for evidence_id in context_package.decision.evidence_ids:
        assert evidence_id in known_evidence_ids, evidence_id

    # Ambos operandos de la Decision (WS-RIESGO VALUE 1, WS-RIESGO-MAXIMO
    # VALUE 3) tienen declared_value -- el enriquecimiento no privilegia a
    # ninguno, agrega evidencia por cada operand resuelto sin ambiguedad.
    declared_entries = [
        e for e in context_package.evidence if e.kind == "declared_value_context"
    ]
    assert len(declared_entries) == 2, context_package.evidence
    declared_by_qualified_name = {
        e.details["data_item_qualified_name"]: e for e in declared_entries if e.details
    }
    assert declared_by_qualified_name["WS-RIESGO"].details["declared_value"] == "1"
    declared = declared_by_qualified_name["WS-RIESGO-MAXIMO"]
    assert declared.evidence_id in context_package.decision.evidence_ids

    # --- (D) la evidencia cita la ubicacion REAL de la declaracion del
    # DataItem (riesgo_maximo, ya leido en (A)), nunca candidate.source_file
    # como fallback -- coinciden en este fixture (programa de un solo
    # archivo, sin COPY), pero el campo consultado es data_item.source_file/
    # line, verificado explicitamente contra el CanonicalDataItem real ------
    assert declared.source_file == riesgo_maximo.source_file
    assert declared.line_start == riesgo_maximo.line
    assert declared.line_end == riesgo_maximo.line

    # --- (G)/(H)/(I) la evidencia identifica el DataItem y su declaracion ------
    assert declared.details is not None
    assert declared.details["data_item_qualified_name"] == "WS-RIESGO-MAXIMO"
    assert declared.details["declared_value"] == "3"
    assert declared.details["program_name"] == "GTDVAL01"
    assert declared.details["value_semantics"] == "DECLARED_INITIAL_VALUE"
    assert declared.details["effective_runtime_value_proven"] is False

    # --- (K) RuleDraft valido ---------------------------------------------
    rule_draft_path = run_dir / "artifacts" / "08-rule-drafts" / artifact_filename
    assert rule_draft_path.is_file()
    RuleDraft.model_validate_json(rule_draft_path.read_text(encoding="utf-8"))

    # --- Guardrail valido ---------------------------------------------------
    guardrail_path = run_dir / "artifacts" / "09-guardrails" / artifact_filename
    assert guardrail_path.is_file()
    guardrail_candidate = GuardrailCandidateArtifact.model_validate_json(
        guardrail_path.read_text(encoding="utf-8")
    )
    assert guardrail_candidate.candidate_id == candidate.candidate_id
    assert guardrail_candidate.guardrail_report.verdict == GuardrailVerdict.EVIDENCE_VALIDATED

    # --- (J) EvidenceCatalog incluye la evidencia (sin ningun cambio de
    # codigo en evidence_catalog.py: mecanismo generico/reflexivo) -----------
    catalog = build_evidence_catalog(context_package)
    declared_aliases = [
        entry for entry in catalog.entries if entry.evidence_id == declared.evidence_id
    ]
    assert len(declared_aliases) == 1, catalog.entries
    assert declared_aliases[0].kind == "decision"


def test_declared_value_provenance_repeated_run_produces_same_ids(tmp_path: Path) -> None:
    """(L) Dos ejecuciones del mismo paquete (dos run_id distintos)
    producen exactamente los mismos candidate_id y el mismo evidence_id
    declared_value, en el mismo orden determinista."""
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
    assert len(ids_1) >= 1

    def _declared_value_evidence_id(settings: Settings, run_id: str, candidate_id: str) -> str:
        run_dir = settings.runs_dir / run_id
        context_path = run_dir / "artifacts" / "07-context" / _artifact_filename(candidate_id)
        package = ContextPackage.model_validate_json(context_path.read_text(encoding="utf-8"))
        declared = [
            e
            for e in package.evidence
            if e.kind == "declared_value_context"
            and e.details
            and e.details["data_item_qualified_name"] == "WS-RIESGO-MAXIMO"
        ]
        assert len(declared) == 1
        return declared[0].evidence_id

    id_1 = _declared_value_evidence_id(settings_1, state_1.run_id, ids_1[0])
    id_2 = _declared_value_evidence_id(settings_2, state_2.run_id, ids_2[0])
    assert id_1 == id_2
