"""E2E hermetico productivo de la deteccion ampliada (Fase 15B3-B1,
seccion 5; extendido en Fase 15B3-C1 con STATE_TRANSITION; extendido en
Fase 15B3-C2-B1 con CALCULATION condicionada -- COMPUTE y un verbo
aritmetico; extendido en Fase 15B3-C2-B2 con CALCULATION incondicional --
mismos dos verbos, ahora tambien productivos SIN Decision envolvente):
"package sintetico versionado -> parser Java real ->
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

# Nueve paragraphs:
# - CHECK-SALDO-PARA: RETURN_CODE directo (Q0/V1 lo detecta, MOVE literal
#   directo a WS-COD-RETORNO -- baseline V1 para probar preservacion).
# - CHECK-PROPAGACION-PARA: RETURN_CODE via propagacion V2 (MOVE literal
#   a un auxiliar, luego MOVE del auxiliar a WS-COD-RETORNO -- Q0 no
#   tiene LEADS_TO directo, solo V2_RETURN_CODE_PROPAGATION lo alcanza).
# - CHECK-INVALIDO-PARA: LEVEL_88_RETURN_CODE (SET condicion-88 TO TRUE,
#   unico VALUE, padre WS-COD-RETORNO).
# - CHECK-TRANSICION-PARA (Fase 15B3-C1): STATE_TRANSITION -- MOVE literal
#   a WS-ESTADO-OPERACION, que matchea la regla status-name de
#   config/semantic-tags.yml (relevancia funcional demostrada).
# - CHECK-INDICADOR-PARA: escribe WS-INDICADOR-INTERNO (NO matchea NINGUNA
#   regla de config/semantic-tags.yml, ni return_code ni status/
#   status_flag) -- solo V2_STATE_CHANGE (UNKNOWN, nunca promovido por
#   enhanced_candidate_integration.py sin semantic_tag) lo detectaria;
#   prueba "UNKNOWN excluido" / target ordinario sin relevancia.
# - CHECK-CALCULO-COMPUTE-PARA (Fase 15B3-C2-B1): CALCULATION condicionado
#   via COMPUTE dentro de un IF.
# - CHECK-CALCULO-MULTIPLY-PARA (Fase 15B3-C2-B1): CALCULATION condicionado
#   via un verbo aritmetico (MULTIPLY...GIVING, no solo COMPUTE) dentro de
#   un IF -- ejemplo recomendado por el enunciado de la fase.
# - CHECK-CALCULO-INCONDICIONAL-PARA: COMPUTE SIN IF/EVALUATE envolvente --
#   desde Fase 15B3-C2-B2 SI llega a 06-candidates.json (decision_id=None/
#   condition=None, nunca fabricados), sin el warning "no productivizado"
#   de 15B3-C2-B1 (que ahora seria falso).
# - CHECK-CALCULO-INCONDICIONAL-ADD-PARA (Fase 15B3-C2-B2): verbo
#   aritmetico (ADD...TO, no solo COMPUTE) SIN IF/EVALUATE envolvente --
#   tambien productivo, prueba que el camino incondicional cubre
#   cualquier StatementKind aritmetico, no solo COMPUTE.
_PROGRAM_SOURCE = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. ENHRULE1.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-SALDO PIC 9(7)V99 VALUE 0.
       01 WS-COD-RETORNO PIC X(4) VALUE SPACES.
          88 COD-SALDO-INVALIDO VALUE 'R003'.
       01 WS-COD-AUX PIC X(4) VALUE SPACES.
       01 WS-ESTADO-OPERACION PIC X(1) VALUE SPACES.
       01 WS-INDICADOR-INTERNO PIC X(1) VALUE SPACES.
       01 WS-MONTO-CALC PIC 9(7)V99 VALUE 100.
       01 WS-TASA-CALC PIC 9(3)V99 VALUE 5.
       01 WS-COMISION-COMPUTE PIC 9(7)V99 VALUE 0.
       01 WS-COMISION-MULTIPLY PIC 9(7)V99 VALUE 0.
       01 WS-COMISION-INCONDICIONAL PIC 9(7)V99 VALUE 0.
       01 WS-COMISION-INCONDICIONAL-ADD PIC 9(7)V99 VALUE 0.
       PROCEDURE DIVISION.
       MAIN-PARA.
           PERFORM CHECK-SALDO-PARA.
           PERFORM CHECK-PROPAGACION-PARA.
           PERFORM CHECK-INVALIDO-PARA.
           PERFORM CHECK-TRANSICION-PARA.
           PERFORM CHECK-INDICADOR-PARA.
           PERFORM CHECK-CALCULO-COMPUTE-PARA.
           PERFORM CHECK-CALCULO-MULTIPLY-PARA.
           PERFORM CHECK-CALCULO-INCONDICIONAL-PARA.
           PERFORM CHECK-CALCULO-INCONDICIONAL-ADD-PARA.
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
       CHECK-TRANSICION-PARA.
           IF WS-SALDO < -1000
               MOVE 'R' TO WS-ESTADO-OPERACION
           END-IF.
       CHECK-INDICADOR-PARA.
           IF WS-SALDO < -2000
               MOVE 'X' TO WS-INDICADOR-INTERNO
           END-IF.
       CHECK-CALCULO-COMPUTE-PARA.
           IF WS-SALDO < -3000
               COMPUTE WS-COMISION-COMPUTE = WS-MONTO-CALC * WS-TASA-CALC
           END-IF.
       CHECK-CALCULO-MULTIPLY-PARA.
           IF WS-SALDO < -4000
               MULTIPLY WS-MONTO-CALC BY WS-TASA-CALC
                   GIVING WS-COMISION-MULTIPLY
           END-IF.
       CHECK-CALCULO-INCONDICIONAL-PARA.
           COMPUTE WS-COMISION-INCONDICIONAL =
               WS-MONTO-CALC + WS-TASA-CALC.
       CHECK-CALCULO-INCONDICIONAL-ADD-PARA.
           ADD WS-MONTO-CALC TO WS-COMISION-INCONDICIONAL-ADD.
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


def _assert_full_downstream(run_dir: Path, candidate_id: str) -> None:
    """ContextPackage/RuleDraft/Guardrail reales para `candidate_id`,
    generados por las etapas productivas (`contexts_built_stage.py`/
    `rule_drafts_generated_stage.py`/`guardrails_applied_stage.py`),
    nunca un pipeline alternativo."""
    artifact_filename = _artifact_filename(candidate_id)

    context_path = run_dir / "artifacts" / "07-context" / artifact_filename
    assert context_path.is_file(), f"no se genero ContextPackage para {candidate_id!r}"
    context_package = ContextPackage.model_validate_json(context_path.read_text(encoding="utf-8"))
    assert context_package.candidate.candidate_id == candidate_id

    rule_draft_path = run_dir / "artifacts" / "08-rule-drafts" / artifact_filename
    assert rule_draft_path.is_file(), f"no se genero RuleDraft para {candidate_id!r}"
    # RuleDraft no expone candidate_id (se identifica por nombre de
    # archivo, ver docstring de _artifact_filename) -- valida que el
    # archivo al menos parsea contra el contrato real.
    RuleDraft.model_validate_json(rule_draft_path.read_text(encoding="utf-8"))

    guardrail_path = run_dir / "artifacts" / "09-guardrails" / artifact_filename
    assert guardrail_path.is_file(), f"no se aplico guardrail a {candidate_id!r}"
    guardrail_candidate = GuardrailCandidateArtifact.model_validate_json(
        guardrail_path.read_text(encoding="utf-8")
    )
    assert guardrail_candidate.candidate_id == candidate_id
    assert guardrail_candidate.guardrail_report.candidate_id == candidate_id
    assert guardrail_candidate.guardrail_report.verdict == GuardrailVerdict.EVIDENCE_VALIDATED


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
    # evidencia valida para ambos, Fase 5 condicion #3) -- desde Fase
    # 15B4-CANDIDATE-QUALITY-2, la integracion productiva reconoce que
    # ambos describen el MISMO hecho (evidence_ids identicos) y conserva
    # unicamente el LEVEL_88_RETURN_CODE (representacion mas especifica),
    # con un warning de corroboracion -- nunca las dos representaciones
    # redundantes.
    invalido_candidates = _by_paragraph("CHECK-INVALIDO-PARA")
    assert len(invalido_candidates) == 1, artifact.candidates
    level88 = invalido_candidates
    assert level88[0].rule_family == UnifiedRuleFamily.LEVEL_88_RETURN_CODE
    assert level88[0].outcome_code == "R003"
    assert level88[0].candidate_source == CandidateSource.V2
    assert any(
        "corroborado por" in warning and "V2_RETURN_CODE_PROPAGATION" in warning
        for warning in artifact.warnings
    ), artifact.warnings

    # --- Caso 15B3-C1: STATE_TRANSITION (CHECK-TRANSICION-PARA, target
    # WS-ESTADO-OPERACION tageado `status`) -------------------------------
    transicion_candidates = _by_paragraph("CHECK-TRANSICION-PARA")
    assert len(transicion_candidates) == 1, artifact.candidates
    transicion = transicion_candidates[0]
    assert transicion.rule_family == UnifiedRuleFamily.STATE_TRANSITION
    assert transicion.candidate_source == CandidateSource.V2
    assert transicion.outcome_code == "R"
    assert transicion.evidence_ids != []
    assert transicion.decision_id

    # --- Caso 3: UNKNOWN excluido (WS-INDICADOR-INTERNO sin semantic_tag) -
    assert not any(c.rule_family == UnifiedRuleFamily.UNKNOWN for c in artifact.candidates)
    assert _by_paragraph("CHECK-INDICADOR-PARA") == []

    # --- Caso 15B3-C2-B1: CALCULATION condicionado, COMPUTE ---------------
    compute_calc_candidates = _by_paragraph("CHECK-CALCULO-COMPUTE-PARA")
    assert len(compute_calc_candidates) == 1, artifact.candidates
    compute_calc = compute_calc_candidates[0]
    assert compute_calc.rule_family == UnifiedRuleFamily.CALCULATION
    assert compute_calc.candidate_source == CandidateSource.V2
    # Fase 3 v1.18.3 (checkpoint correctivo de limites de token): el
    # renderer de expresiones ahora respeta limites de token (antes:
    # ProLeap getText() concatenaba sin separador, "WS-SALDO<-3000").
    assert compute_calc.condition == "WS-SALDO < -3000"
    assert compute_calc.outcome_code is None, "CALCULATION nunca afirma un literal"
    assert compute_calc.evidence_ids != []
    assert compute_calc.decision_id

    # --- Caso 15B3-C2-B1: CALCULATION condicionado, verbo aritmetico
    # (MULTIPLY...GIVING, no solo COMPUTE) ---------------------------------
    multiply_calc_candidates = _by_paragraph("CHECK-CALCULO-MULTIPLY-PARA")
    assert len(multiply_calc_candidates) == 1, artifact.candidates
    multiply_calc = multiply_calc_candidates[0]
    assert multiply_calc.rule_family == UnifiedRuleFamily.CALCULATION
    assert multiply_calc.candidate_source == CandidateSource.V2
    assert multiply_calc.condition == "WS-SALDO < -4000"
    assert multiply_calc.outcome_code is None
    assert multiply_calc.evidence_ids != []
    assert multiply_calc.decision_id

    # --- Caso 15B3-C2-B2: calculo incondicional (COMPUTE) SI llega a
    # 06-candidates.json -- decision_id=None/condition=None NUNCA
    # fabricados, y el warning "no productivizado" de 15B3-C2-B1 ya NO
    # existe (seria falso: el candidato ahora es productivo). ------------
    incondicional_compute_candidates = _by_paragraph("CHECK-CALCULO-INCONDICIONAL-PARA")
    assert len(incondicional_compute_candidates) == 1, artifact.candidates
    incondicional_compute = incondicional_compute_candidates[0]
    assert incondicional_compute.rule_family == UnifiedRuleFamily.CALCULATION
    assert incondicional_compute.candidate_source == CandidateSource.V2
    assert incondicional_compute.decision_id is None
    assert incondicional_compute.condition is None
    assert incondicional_compute.outcome_code is None
    assert incondicional_compute.evidence_ids != []
    assert not any(
        "WS-COMISION-INCONDICIONAL" in w and "CHECK-CALCULO-INCONDICIONAL-PARA" in w
        for w in artifact.warnings
    ), artifact.warnings

    # Verificacion suplementaria: el SemanticEffect citado en
    # evidence_ids es ademas independientemente reconstruible via el
    # mismo analizador puro que detect_calculation ya consulto en
    # memoria durante ESTA misma ejecucion.
    from altamira_extractor.contracts.semantic_effects import SemanticEffectKind
    from altamira_extractor.pipeline.semantic_effects_service import (
        compute_semantic_effects_artifact,
    )

    semantic_effects = compute_semantic_effects_artifact(run_dir, state.run_id)
    incondicional_effects = [
        effect
        for program_effects in semantic_effects.programs
        for effect in program_effects.effects
        if effect.source_reference.paragraph == "CHECK-CALCULO-INCONDICIONAL-PARA"
    ]
    assert len(incondicional_effects) == 1, incondicional_effects
    assert incondicional_effects[0].kind == SemanticEffectKind.COMPUTE_VALUE
    assert incondicional_effects[0].target_data_items == ["WS-COMISION-INCONDICIONAL"]
    assert incondicional_effects[0].effect_id in incondicional_compute.evidence_ids

    # --- Caso 15B3-C2-B2: calculo incondicional via verbo aritmetico
    # (ADD...TO, no solo COMPUTE) -- tambien productivo. ------------------
    incondicional_add_candidates = _by_paragraph("CHECK-CALCULO-INCONDICIONAL-ADD-PARA")
    assert len(incondicional_add_candidates) == 1, artifact.candidates
    incondicional_add = incondicional_add_candidates[0]
    assert incondicional_add.rule_family == UnifiedRuleFamily.CALCULATION
    assert incondicional_add.candidate_source == CandidateSource.V2
    assert incondicional_add.decision_id is None
    assert incondicional_add.condition is None
    assert incondicional_add.outcome_code is None
    assert incondicional_add.evidence_ids != []
    assert not any(
        "WS-COMISION-INCONDICIONAL-ADD" in w and "CHECK-CALCULO-INCONDICIONAL-ADD-PARA" in w
        for w in artifact.warnings
    ), artifact.warnings

    # --- Caso 4: candidatos ampliados con evidence/provenance -------------
    # 7, no 8 (Fase 15B4-CANDIDATE-QUALITY-2): CHECK-INVALIDO-PARA aporta
    # ahora un unico candidato (LEVEL_88_RETURN_CODE, corroborado) en vez
    # de dos representaciones redundantes del mismo hecho.
    enhanced_candidates = [
        c for c in artifact.candidates if c.candidate_source == CandidateSource.V2
    ]
    assert len(enhanced_candidates) == 7, artifact.candidates
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

    # --- Caso 5/6/7: ContextPackage/RuleDraft/guardrail -- tanto para el
    # candidato RETURN_CODE ampliado (propagacion V2) como para el
    # candidato STATE_TRANSITION (Fase 15B3-C1), CALCULATION condicionado
    # (Fase 15B3-C2-B1, COMPUTE y verbo aritmetico) y CALCULATION
    # incondicional (Fase 15B3-C2-B2, COMPUTE y verbo aritmetico) -- no
    # solo para V1. Para los incondicionales, ademas se verifica
    # explicitamente que ContextPackage.decision es None (sin Decision
    # fabricada) y que el RuleDraft/Guardrail son validos igual. ----------
    _assert_full_downstream(run_dir, propagated.candidate_id)
    _assert_full_downstream(run_dir, transicion.candidate_id)
    _assert_full_downstream(run_dir, compute_calc.candidate_id)
    _assert_full_downstream(run_dir, multiply_calc.candidate_id)
    _assert_full_downstream(run_dir, incondicional_compute.candidate_id)
    _assert_full_downstream(run_dir, incondicional_add.candidate_id)

    for unconditional_id in (incondicional_compute.candidate_id, incondicional_add.candidate_id):
        artifact_filename = _artifact_filename(unconditional_id)
        context_path = run_dir / "artifacts" / "07-context" / artifact_filename
        package = ContextPackage.model_validate_json(context_path.read_text(encoding="utf-8"))
        assert package.decision is None
        assert package.candidate.decision_id is None

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
    assert len(ids_1) >= 8


def test_enhanced_flag_disabled_produces_only_v1_candidate(tmp_path: Path) -> None:
    """Contraste directo: el MISMO paquete con
    `enhanced_candidates_enabled=false` explicito produce unicamente el
    candidato V1 (CHECK-SALDO-PARA) -- la propagacion y el nivel 88
    nunca aparecen. (Fase 15B4-CANDIDATE-QUALITY-5E: el default global
    paso a True; este test fija el modo legacy explicitamente.)"""
    require_jar()
    from altamira_extractor.pipeline.runner import run_ingestion

    zip_path = _write_enhanced_package_zip(tmp_path / "package_disabled.zip")
    settings = build_hermetic_settings(
        tmp_path / "hermetic_data_disabled",
        NEO4J_URI=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        NEO4J_USER=os.environ.get("NEO4J_USER", "neo4j"),
        NEO4J_PASSWORD=os.environ.get("NEO4J_PASSWORD", "neo4j"),
        NEO4J_DATABASE=os.environ.get("NEO4J_DATABASE", "neo4j"),
        enhanced_candidates_enabled=False,
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
