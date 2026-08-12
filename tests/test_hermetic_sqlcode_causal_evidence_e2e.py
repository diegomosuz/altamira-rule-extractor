"""E2E hermetico del enriquecimiento evidencial SQLCODE (Fase 15B3-C3-C-B,
seccion 29): "package sintetico versionado -> parser Java real -> canonical
-> semantic graph en Neo4j real efimero -> V2 -> 06-candidates.json ->
ContextPackage (con evidencia causal SQLCODE) -> RuleDraft -> guardrail",
ejercitando exclusivamente `run_ingestion` y los stages productivos reales
(`runner.py`) -- nunca un pipeline alternativo construido para este test.

Caso obligatorio del enunciado: EXEC SQL SELECT ESTADO INTO :WS-ESTADO-DB
... WHERE CUENTA = :WS-CUENTA, inmediatamente seguido de EVALUATE SQLCODE
(WHEN 0/WHEN +100/WHEN OTHER, cada uno MOVE a WS-ESTADO-OPERACION),
SQLCODE NUNCA declarado (mismo patron que el corpus real Catherine). La
regla resultante sigue siendo STATE_TRANSITION (familia preexistente,
NUNCA una familia SQL nueva); el enriquecimiento SOLO agrega un
evidence_id adicional a `ContextPackageDecision.evidence_ids`, sin tocar
candidate_id/condition/effect.

Hermetismo: mismo patron que `test_hermetic_sql_directed_data_flow_e2e.py`
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
    """Misma formula que en `test_hermetic_sql_directed_data_flow_e2e.py`
    (sha256 del candidate_id), nunca reimportada de un modulo privado."""
    return hashlib.sha256(candidate_id.encode("utf-8")).hexdigest() + ".json"


pytestmark = pytest.mark.integration

_MANIFEST_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<altamira-package schema-version="1.0">
  <country code="AR" name="Argentina"/>
  <application name="Transferencias"/>
  <operation logical-name="OP-SQLCODE-CAUSAL" description="SQLCODE causal evidence 15B3-C3-C-B"/>
  <implementation version="1.0">
    <entry-program>SQLCDE2E1</entry-program>
  </implementation>
  <source format="FIXED" encoding="UTF-8"/>
  <parameter-tables>
    <table name="PARAM_DEMO" ddl="02-parametria/ddl/PARAM_DEMO.sql"/>
  </parameter-tables>
</altamira-package>
"""

# Un unico paragraph: EXEC SQL SELECT...INTO inmediatamente seguido de
# EVALUATE SQLCODE (SQLCODE nunca declarado, igual que el corpus real
# Catherine, ver auditoria 15B3-C3-C-A seccion 27).
_PROGRAM_SOURCE = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. SQLCDE2E1.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-CUENTA             PIC X(10).
       01 WS-ESTADO-DB          PIC X(1).
       01 WS-ESTADO-OPERACION   PIC X(1) VALUE SPACES.
       PROCEDURE DIVISION.
       MAIN-PARA.
           EXEC SQL
               SELECT ESTADO
               INTO :WS-ESTADO-DB
               FROM CUENTAS
               WHERE CUENTA = :WS-CUENTA
           END-EXEC.
           EVALUATE SQLCODE
               WHEN 0
                   MOVE 'A' TO WS-ESTADO-OPERACION
               WHEN +100
                   MOVE 'N' TO WS-ESTADO-OPERACION
               WHEN OTHER
                   MOVE 'E' TO WS-ESTADO-OPERACION
           END-EVALUATE.
"""

_PARAM_DEMO_DDL = b"CREATE TABLE PARAM_DEMO (ID INT);\n"


def _regular_file_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.date_time = (2026, 1, 1, 0, 0, 0)
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def _write_sqlcode_package_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(_regular_file_info("manifest.xml"), _MANIFEST_XML)
        zf.writestr(_regular_file_info("01-codigo/cobol/SQLCDE2E1.cbl"), _PROGRAM_SOURCE)
        zf.writestr(_regular_file_info("02-parametria/ddl/PARAM_DEMO.sql"), _PARAM_DEMO_DDL)
    return path


def _sqlcode_settings(tmp_path: Path, *, run_label: str) -> Settings:
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

    zip_path = _write_sqlcode_package_zip(tmp_path / f"package_{run_label}.zip")
    settings = _sqlcode_settings(tmp_path, run_label=run_label)
    with hermetic_llm_and_network_guard():
        state = run_ingestion(source_zip=zip_path, settings=settings)
    return state, settings


def test_sqlcode_causal_evidence_reaches_state_transition_rule_end_to_end(
    tmp_path: Path,
) -> None:
    require_jar()
    state, settings = _run(tmp_path, run_label="main")

    assert state.current_stage.value == "COMPLETED", [
        (s.stage.value, s.status.value, s.error) for s in state.stages
    ]
    assert all(s.status.value != "FAILED" for s in state.stages)

    run_dir = settings.runs_dir / state.run_id

    # --- (A)/(B)/(C) 06-candidates.json: EXEC SQL interpretado, Decision
    # EVALUATE existente, STATE_TRANSITION con la familia preexistente ---
    candidates_path = run_dir / "artifacts" / "06-candidates.json"
    artifact = CandidateArtifact.model_validate_json(candidates_path.read_text(encoding="utf-8"))
    transicion_candidates = [c for c in artifact.candidates if c.paragraph_name == "MAIN-PARA"]
    assert len(transicion_candidates) >= 1, artifact.candidates
    transicion = transicion_candidates[0]
    assert transicion.rule_family == UnifiedRuleFamily.STATE_TRANSITION
    assert transicion.candidate_source == CandidateSource.V2
    assert transicion.decision_id
    assert transicion.condition

    # --- (D) candidate_id sigue la formula deterministica de siempre --
    # el enriquecimiento SQLCODE vive exclusivamente en
    # context_package_builder.py/contexts_built_stage.py (Fase
    # 15B3-C3-C-B), invocado en CONTEXTS_BUILT -- una etapa
    # estrictamente POSTERIOR a CANDIDATES_DETECTED, que ya escribio
    # 06-candidates.json de forma inmutable. Ni candidate_detector.py ni
    # v2_detectors.py ni enhanced_candidate_integration.py (que
    # construyen candidate_id) fueron tocados por esta fase -- prueba
    # arquitectonica, no solo de valor. La determinacion real de
    # invariancia es empirica: ver test_..._repeated_run_produces_
    # same_ids (requisito L), que corre el pipeline COMPLETO dos veces y
    # confirma el mismo candidate_id.
    assert transicion.candidate_id.startswith("candidate::enhanced::")

    # --- ContextPackage/RuleDraft/guardrail -------------------------------
    artifact_filename = _artifact_filename(transicion.candidate_id)
    context_path = run_dir / "artifacts" / "07-context" / artifact_filename
    assert context_path.is_file()
    context_package = ContextPackage.model_validate_json(context_path.read_text(encoding="utf-8"))
    assert context_package.candidate.candidate_id == transicion.candidate_id
    assert context_package.decision is not None

    # --- (E) condition/effect sin modificaciones (identicos a lo ya
    # producido por el candidato, nunca reformulados por el enriquecimiento) --
    # decision.outcome_code viene del nodo Decision (EVALUATE completo,
    # multiples WHEN con outcomes distintos -- comportamiento V1
    # preexistente, no relacionado con C3-C-B) y NO tiene por que
    # coincidir con transicion.outcome_code (por-candidato, anclado a un
    # unico branch); lo que C3-C-B garantiza es que ninguno de los dos
    # cambia por el enriquecimiento SQLCODE.
    assert context_package.decision.expression == transicion.condition
    assert context_package.decision.expression == "SQLCODE"

    # --- (F)/(G) ContextPackageDecision.evidence_ids: evidencia propia +
    # evidencia causal del EXEC SQL, ambas presentes en ContextPackage.evidence --
    assert len(context_package.decision.evidence_ids) >= 2, context_package.decision.evidence_ids
    known_evidence_ids = {entry.evidence_id for entry in context_package.evidence}
    for evidence_id in context_package.decision.evidence_ids:
        assert evidence_id in known_evidence_ids, evidence_id

    causal_entries = [e for e in context_package.evidence if e.kind == "sql_causal_context"]
    assert len(causal_entries) == 1, context_package.evidence
    causal = causal_entries[0]
    assert causal.evidence_id in context_package.decision.evidence_ids

    # --- (H) la evidencia SQL identifica el statement_id real del EXEC SQL --
    assert causal.details is not None
    assert causal.details["statement_kind"] == "EXEC_SQL"
    assert "MAIN-PARA" in str(causal.details["exec_sql_statement_id"])
    assert "SELECT" in causal.details["source_text"].upper()

    # --- (I) RuleDraft valido ---------------------------------------------
    rule_draft_path = run_dir / "artifacts" / "08-rule-drafts" / artifact_filename
    assert rule_draft_path.is_file()
    RuleDraft.model_validate_json(rule_draft_path.read_text(encoding="utf-8"))

    # --- (J) Guardrail valido -----------------------------------------------
    guardrail_path = run_dir / "artifacts" / "09-guardrails" / artifact_filename
    assert guardrail_path.is_file()
    guardrail_candidate = GuardrailCandidateArtifact.model_validate_json(
        guardrail_path.read_text(encoding="utf-8")
    )
    assert guardrail_candidate.candidate_id == transicion.candidate_id
    assert guardrail_candidate.guardrail_report.verdict == GuardrailVerdict.EVIDENCE_VALIDATED

    # --- (K) EvidenceCatalog incluye la evidencia SQL (sin ningun cambio
    # de codigo en evidence_catalog.py: mecanismo generico/reflexivo, ver
    # auditoria 15B3-C3-C-A seccion 20) --------------------------------
    catalog = build_evidence_catalog(context_package)
    causal_aliases = [entry for entry in catalog.entries if entry.evidence_id == causal.evidence_id]
    assert len(causal_aliases) == 1, catalog.entries
    # _kind_of_anchor_path deriva el "tipo" del ULTIMO segmento con nombre
    # del path DEL CONTENEDOR ($.decision), no del campo evidence_ids en
    # si (ver evidence_catalog.py::_kind_of_anchor_path).
    assert causal_aliases[0].kind == "decision"


def test_sqlcode_causal_evidence_repeated_run_produces_same_ids(tmp_path: Path) -> None:
    """(L) Dos ejecuciones del mismo paquete (dos run_id distintos)
    producen exactamente los mismos candidate_id y el mismo evidence_id
    causal SQLCODE, en el mismo orden determinista."""
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

    def _causal_evidence_id(settings: Settings, run_id: str, candidate_id: str) -> str:
        run_dir = settings.runs_dir / run_id
        context_path = run_dir / "artifacts" / "07-context" / _artifact_filename(candidate_id)
        package = ContextPackage.model_validate_json(context_path.read_text(encoding="utf-8"))
        causal = [e for e in package.evidence if e.kind == "sql_causal_context"]
        assert len(causal) == 1
        return causal[0].evidence_id

    causal_id_1 = _causal_evidence_id(settings_1, state_1.run_id, ids_1[0])
    causal_id_2 = _causal_evidence_id(settings_2, state_2.run_id, ids_2[0])
    assert causal_id_1 == causal_id_2
