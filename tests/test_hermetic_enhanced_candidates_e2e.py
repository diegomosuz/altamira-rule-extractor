"""E2E hermetico productivo de la deteccion ampliada (Fase 15B3-B1,
seccion 5): "package sintetico versionado -> parser Java real ->
canonical -> semantic graph en Neo4j real efimero -> V1 + V2 ->
06-candidates.json -> ContextPackage -> RuleDraft -> guardrail",
ejercitando exclusivamente `run_ingestion` y los stages productivos
reales (`runner.py`) -- nunca un pipeline alternativo construido para
este test.

Hermetismo: JDK 17 + JAR real (`require_jar`), Neo4j efimero aislado
(`NEO4J_URI`/`NEO4J_USER`/`NEO4J_PASSWORD`/`NEO4J_DATABASE` desde el
entorno del proceso, igual que `test_hermetic_harness_regression.py`),
`_env_file=None` (`build_hermetic_settings`), proveedor LLM real
bloqueado a nivel de proceso + red bloqueada salvo localhost/Neo4j
(`hermetic_llm_and_network_guard`), respuesta LLM sintetica generica
(`_DeterministicFakeClient`, construida desde el `ContextPackage` REAL
de cada candidato, nunca un fixture fijo por-candidato)."""

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
from altamira_extractor.contracts.enums import CandidateStatus, GuardrailVerdict
from altamira_extractor.contracts.guardrail_candidate import GuardrailCandidateArtifact
from altamira_extractor.contracts.rule_draft import RuleDraft

from .e2e_support import require_jar
from .hermetic_llm_support import build_hermetic_settings, hermetic_llm_and_network_guard


def _artifact_filename(candidate_id: str) -> str:
    """Misma formula que `contexts_built_stage.py::_context_filename`/
    `rule_drafts_generated_stage.py::_rule_draft_filename`/
    `guardrails_applied_stage.py::_guardrail_filename` (identica en las
    tres etapas) -- nunca reimportada de un modulo privado, solo la
    formula (sha256 del candidate_id) replicada."""
    return hashlib.sha256(candidate_id.encode("utf-8")).hexdigest() + ".json"


pytestmark = pytest.mark.integration

_MANIFEST_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<altamira-package schema-version="1.0">
  <country code="AR" name="Argentina"/>
  <application name="Transferencias"/>
  <operation logical-name="OP-ENH-RULES" description="Reglas ampliadas 15B3-B1"/>
  <implementation version="1.0">
    <entry-program>ENHRULE1</entry-program>
  </implementation>
  <source format="FIXED" encoding="UTF-8"/>
  <parameter-tables>
    <table name="PARAM_DEMO" ddl="02-parametria/ddl/PARAM_DEMO.sql"/>
  </parameter-tables>
</altamira-package>
"""

# Cuatro paragraphs, cuatro casos:
# - CHECK-SALDO-PARA: RETURN_CODE directo (Q0/V1 lo detecta, MOVE literal
#   directo a WS-COD-RETORNO -- baseline V1 para probar preservacion).
# - CHECK-PROPAGACION-PARA: RETURN_CODE via propagacion V2 (MOVE literal
#   a un auxiliar, luego MOVE del auxiliar a WS-COD-RETORNO -- Q0 no
#   tiene LEADS_TO directo, solo V2_RETURN_CODE_PROPAGATION lo alcanza).
# - CHECK-INVALIDO-PARA: LEVEL_88_RETURN_CODE (SET condicion-88 TO TRUE,
#   unico VALUE, padre WS-COD-RETORNO).
# - CHECK-ESTADO-PARA: escribe WS-ESTADO (NO matchea el regex
#   return_code de config/semantic-tags.yml) -- solo V2_STATE_CHANGE
#   (UNKNOWN, nunca invocado por enhanced_candidate_integration.py) lo
#   detectaria; prueba "UNKNOWN excluido".
_PROGRAM_SOURCE = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. ENHRULE1.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-SALDO PIC 9(7)V99 VALUE 0.
       01 WS-COD-RETORNO PIC X(4) VALUE SPACES.
          88 COD-SALDO-INVALIDO VALUE 'R003'.
       01 WS-COD-AUX PIC X(4) VALUE SPACES.
       01 WS-ESTADO PIC X(1) VALUE SPACES.
       PROCEDURE DIVISION.
       MAIN-PARA.
           PERFORM CHECK-SALDO-PARA.
           PERFORM CHECK-PROPAGACION-PARA.
           PERFORM CHECK-INVALIDO-PARA.
           PERFORM CHECK-ESTADO-PARA.
           GOBACK.
       CHECK-SALDO-PARA.
           IF WS-SALDO < 0
               MOVE 'R001' TO WS-COD-RETORNO
           END-IF.
       CHECK-PROPAGACION-PARA.
           IF WS-SALDO = 0
               MOVE 'R002' TO WS-COD-AUX
               MOVE WS-COD-AUX TO WS-COD-RETORNO
           END-IF.
       CHECK-INVALIDO-PARA.
           IF WS-SALDO > 999999
               SET COD-SALDO-INVALIDO TO TRUE
           END-IF.
       CHECK-ESTADO-PARA.
           IF WS-SALDO < -1000
               MOVE 'X' TO WS-ESTADO
           END-IF.
"""

_PARAM_DEMO_DDL = b"CREATE TABLE PARAM_DEMO (ID INT);\n"


def _regular_file_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.date_time = (2026, 1, 1, 0, 0, 0)
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def _write_enhanced_package_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(_regular_file_info("manifest.xml"), _MANIFEST_XML)
        zf.writestr(_regular_file_info("01-codigo/cobol/ENHRULE1.cbl"), _PROGRAM_SOURCE)
        zf.writestr(_regular_file_info("02-parametria/ddl/PARAM_DEMO.sql"), _PARAM_DEMO_DDL)
    return path


def _enhanced_settings(tmp_path: Path, *, run_label: str) -> Settings:
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

    zip_path = _write_enhanced_package_zip(tmp_path / f"package_{run_label}.zip")
    settings = _enhanced_settings(tmp_path, run_label=run_label)
    with hermetic_llm_and_network_guard():
        state = run_ingestion(source_zip=zip_path, settings=settings)
    return state, settings


def test_enhanced_pipeline_end_to_end_reaches_completed_with_v1_and_v2_candidates(
    tmp_path: Path,
) -> None:
    require_jar()
    state, settings = _run(tmp_path, run_label="main")

    assert state.current_stage.value == "COMPLETED", [
        (s.stage.value, s.status.value, s.error) for s in state.stages
    ]
    assert all(s.status.value != "FAILED" for s in state.stages)

    run_dir = settings.runs_dir / state.run_id
    candidates_path = run_dir / "artifacts" / "06-candidates.json"
    artifact = CandidateArtifact.model_validate_json(candidates_path.read_text(encoding="utf-8"))

    def _by_paragraph(name: str) -> list:
        return [c for c in artifact.candidates if c.paragraph_name == name]

    # --- Caso 1: RETURN_CODE por propagacion V2 (CHECK-PROPAGACION-PARA:
    # MOVE literal a un auxiliar, luego auxiliar -> WS-COD-RETORNO; Q0 no
    # tiene LEADS_TO directo, solo V2 lo alcanza) --------------------------
    propagation_candidates = _by_paragraph("CHECK-PROPAGACION-PARA")
    assert len(propagation_candidates) == 1, artifact.candidates
    propagated = propagation_candidates[0]
    assert propagated.rule_family == UnifiedRuleFamily.RETURN_CODE
    assert propagated.candidate_source == CandidateSource.V2
    assert propagated.outcome_code == "R002"

    # --- Caso 2: LEVEL_88_RETURN_CODE (CHECK-INVALIDO-PARA) ---------------
    # V2_RETURN_CODE_PROPAGATION y V2_LEVEL_88_RETURN_CODE disparan AMBOS
    # sobre el mismo SET condicion-88 TO TRUE (CONDITION_LITERAL es
    # evidencia valida para ambos, Fase 5 condicion #3) -- se conservan
    # como reglas separadas (familia distinta, regla C de la Fase
    # 15B3-B1), nunca fusionadas entre si.
    invalido_candidates = _by_paragraph("CHECK-INVALIDO-PARA")
    assert len(invalido_candidates) == 2, artifact.candidates
    invalido_families = {c.rule_family for c in invalido_candidates}
    assert invalido_families == {
        UnifiedRuleFamily.RETURN_CODE,
        UnifiedRuleFamily.LEVEL_88_RETURN_CODE,
    }
    level88 = [
        c for c in invalido_candidates if c.rule_family == UnifiedRuleFamily.LEVEL_88_RETURN_CODE
    ]
    assert len(level88) == 1
    assert level88[0].outcome_code == "R003"
    assert level88[0].candidate_source == CandidateSource.V2
    assert all(c.outcome_code == "R003" for c in invalido_candidates)

    # --- Caso 3: UNKNOWN excluido (WS-ESTADO nunca return_code) -----------
    assert not any(c.rule_family == UnifiedRuleFamily.UNKNOWN for c in artifact.candidates)
    assert _by_paragraph("CHECK-ESTADO-PARA") == []

    # --- Caso 4: candidatos ampliados con evidence/provenance -------------
    enhanced_candidates = [
        c for c in artifact.candidates if c.candidate_source == CandidateSource.V2
    ]
    assert len(enhanced_candidates) == 3, artifact.candidates
    for enhanced_candidate in enhanced_candidates:
        assert enhanced_candidate.evidence_ids != []
        assert enhanced_candidate.source_file == "01-codigo/cobol/ENHRULE1.cbl"
        assert enhanced_candidate.line_start >= 1

    # V1 baseline preservado Y fusionado (regla D: CHECK-SALDO-PARA es
    # visible para Q0 directamente, PERO V2_RETURN_CODE_PROPAGATION
    # TAMBIEN lo alcanza via DIRECT_LITERAL -- misma decision/condicion/
    # efecto/familia, se fusiona en una sola entrada que conserva la
    # identidad V1, con evidence_ids enriquecidos).
    saldo_candidates = _by_paragraph("CHECK-SALDO-PARA")
    assert len(saldo_candidates) == 1, artifact.candidates
    v1_direct = saldo_candidates[0]
    assert v1_direct.candidate_source == CandidateSource.V1
    assert v1_direct.outcome_code == "R001"
    assert v1_direct.detector_id == "q0-return-code-decision"
    assert v1_direct.evidence_ids != []
    assert any(
        "V2_RETURN_CODE_PROPAGATION" in w and v1_direct.candidate_id in w for w in artifact.warnings
    )

    # --- Caso 8: ningun candidato es FUNCTIONALLY_APPROVED automatico -----
    assert all(c.status == CandidateStatus.DETECTED_CANDIDATE for c in artifact.candidates)

    # --- Caso 5/6/7: ContextPackage/RuleDraft/guardrail para el candidato
    # ampliado (propagacion V2), no solo para el candidato V1 baseline ----
    enhanced_id = propagated.candidate_id
    artifact_filename = _artifact_filename(enhanced_id)

    context_path = run_dir / "artifacts" / "07-context" / artifact_filename
    assert context_path.is_file(), "no se genero ContextPackage para el candidato V2 ampliado"
    context_package = ContextPackage.model_validate_json(context_path.read_text(encoding="utf-8"))
    assert context_package.candidate.candidate_id == enhanced_id

    rule_draft_path = run_dir / "artifacts" / "08-rule-drafts" / artifact_filename
    assert rule_draft_path.is_file(), "no se genero RuleDraft para el candidato V2 ampliado"
    # RuleDraft no expone candidate_id (se identifica por nombre de
    # archivo, ver docstring de _artifact_filename) -- valida que el
    # archivo al menos parsea contra el contrato real.
    RuleDraft.model_validate_json(rule_draft_path.read_text(encoding="utf-8"))

    guardrail_path = run_dir / "artifacts" / "09-guardrails" / artifact_filename
    assert guardrail_path.is_file(), "no se aplico guardrail al candidato V2 ampliado"
    guardrail_candidate = GuardrailCandidateArtifact.model_validate_json(
        guardrail_path.read_text(encoding="utf-8")
    )
    assert guardrail_candidate.candidate_id == enhanced_id
    assert guardrail_candidate.guardrail_report.candidate_id == enhanced_id
    assert guardrail_candidate.guardrail_report.verdict == GuardrailVerdict.EVIDENCE_VALIDATED

    rules_dir = run_dir / "artifacts" / "10-rules"
    assert len(list(rules_dir.glob("*.md"))) >= 1


def test_enhanced_pipeline_repeated_run_produces_same_candidate_ids_and_order(
    tmp_path: Path,
) -> None:
    """Caso 9: ejecutar el mismo paquete dos veces (dos run_id distintos)
    produce exactamente los mismos candidate_id, en el mismo orden."""
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
    assert len(ids_1) >= 3


def test_enhanced_flag_disabled_produces_only_v1_candidate(tmp_path: Path) -> None:
    """Contraste directo: el MISMO paquete con
    `enhanced_candidates_enabled=false` (default) produce unicamente el
    candidato V1 (CHECK-SALDO-PARA) -- la propagacion y el nivel 88
    nunca aparecen."""
    require_jar()
    from altamira_extractor.pipeline.runner import run_ingestion

    zip_path = _write_enhanced_package_zip(tmp_path / "package_disabled.zip")
    settings = build_hermetic_settings(
        tmp_path / "hermetic_data_disabled",
        NEO4J_URI=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        NEO4J_USER=os.environ.get("NEO4J_USER", "neo4j"),
        NEO4J_PASSWORD=os.environ.get("NEO4J_PASSWORD", "neo4j"),
        NEO4J_DATABASE=os.environ.get("NEO4J_DATABASE", "neo4j"),
    )
    assert settings.enhanced_candidates_enabled is False

    with hermetic_llm_and_network_guard():
        state = run_ingestion(source_zip=zip_path, settings=settings)

    assert state.current_stage.value == "COMPLETED"
    artifact = CandidateArtifact.model_validate_json(
        (settings.runs_dir / state.run_id / "artifacts" / "06-candidates.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(artifact.candidates) == 1
    assert artifact.candidates[0].outcome_code == "R001"
    assert artifact.candidates[0].candidate_source == CandidateSource.V1
