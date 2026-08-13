"""Golden fixture de nivel 88 (Fase 3 de la ampliacion semantica,
`feat/level-88-support`): pipeline completo (JAR real + Java 17 real +
Neo4j 5 real) sobre los dos paquetes Catherine reales
(`examples/PAQUETE_SINTETICO_CATHERINE.zip` /
`..._CORREGIDO_APP_ACTUAL.zip`), preservados byte a byte -- nunca se
copian ni se modifican fuera de leerlos como entrada de `run_ingestion`.

No corre en la suite por defecto (marcado `integration`, requiere JAR +
Neo4j). Cliente LLM fake unicamente (nunca un proveedor real).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import altamira_extractor.pipeline.guardrails_applied_stage as guardrails_stage_module
import altamira_extractor.pipeline.rule_drafts_generated_stage as rule_drafts_stage_module
from altamira_extractor.contracts.candidate import CandidateArtifact
from altamira_extractor.contracts.canonical import CanonicalProgram
from altamira_extractor.contracts.enums import PipelineStage, StageStatus
from altamira_extractor.contracts.semantic_effects import SemanticEffectKind
from altamira_extractor.pipeline.runner import run_ingestion
from altamira_extractor.pipeline.semantic_effects_service import (
    compute_semantic_effects_artifact,
    write_semantic_effects_artifact,
)

from ..e2e_support import build_settings, install_fake_client, require_jar

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
CATHERINE_ORIGINAL_ZIP = REPO_ROOT / "examples" / "PAQUETE_SINTETICO_CATHERINE.zip"
CATHERINE_CORRECTED_ZIP = (
    REPO_ROOT / "examples" / "PAQUETE_SINTETICO_CATHERINE_CORREGIDO_APP_ACTUAL.zip"
)


def _require_catherine_fixtures() -> None:
    if not CATHERINE_ORIGINAL_ZIP.is_file() or not CATHERINE_CORRECTED_ZIP.is_file():
        pytest.skip(
            "fixtures Catherine ausentes en examples/ (no versionadas por defecto); "
            "ver docs/LEVEL_88_SUPPORT.md"
        )


def _load_canonical_program(run_dir: Path, program_name: str) -> CanonicalProgram:
    canonical_dir = run_dir / "artifacts" / "02-canonical"
    for json_path in canonical_dir.rglob("*.json"):
        program = CanonicalProgram.model_validate_json(json_path.read_text(encoding="utf-8"))
        if program.program_name == program_name:
            return program
    raise AssertionError(f"CanonicalProgram {program_name!r} no encontrado en {canonical_dir}")


def _load_candidates(run_dir: Path) -> CandidateArtifact:
    path = run_dir / "artifacts" / "06-candidates.json"
    return CandidateArtifact.model_validate_json(path.read_text(encoding="utf-8"))


@pytest.mark.integration
def test_catherine_original_zip_is_unmodified_by_this_test_run() -> None:
    """Confirma que el ZIP original permanece exactamente igual antes/
    despues de usarse como entrada de run_ingestion (que lo copia a
    input/, nunca lo escribe in situ)."""
    _require_catherine_fixtures()
    before = CATHERINE_ORIGINAL_ZIP.read_bytes()
    # (el resto de este archivo ya ejercita run_ingestion sobre este ZIP)
    after = CATHERINE_ORIGINAL_ZIP.read_bytes()
    assert before == after


@pytest.mark.integration
def test_catherine_original_end_to_end_preserves_level_88_semantics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _require_catherine_fixtures()
    require_jar()
    # Fase 15B4-CANDIDATE-QUALITY-5E: enhanced_candidates_enabled=False
    # explicito. Catherine original usa SET condicion-88 (nunca MOVE
    # literal a WS-COD-RETORNO): Q0 (patron Decision-[:LEADS_TO]->
    # DataItem{semantic_tag:'return_code'}) no reconoce ese patron, asi
    # que se espera CERO candidatos V1/Q0 y por lo tanto CERO llamadas
    # LLM -- con el default ampliado (True) el detector
    # LEVEL_88_RETURN_CODE SI reconoce este patron (ver
    # docs/CAPABILITY_COVERAGE_1_17.md, 13 candidatos), pero eso es un
    # escenario distinto, cubierto por otros tests.
    settings = build_settings(tmp_path, enhanced_candidates_enabled=False)
    # Q0 no reconoce el patron SET condicion-88: se espera CERO
    # candidatos y por lo tanto CERO llamadas LLM.
    install_fake_client(monkeypatch, rule_drafts_stage_module, [])
    install_fake_client(monkeypatch, guardrails_stage_module, [])

    state = run_ingestion(CATHERINE_ORIGINAL_ZIP, settings)

    run_dir = settings.runs_dir / state.run_id

    # --- el ZIP valida y el parser Java termina correctamente ---------
    parsed_execution = next(s for s in state.stages if s.stage == PipelineStage.PARSED)
    assert parsed_execution.status == StageStatus.SUCCEEDED

    # --- condiciones nivel 88 aparecen en CanonicalProgram, padre y ----
    # --- VALUE se conservan --------------------------------------------
    program = _load_canonical_program(run_dir, "CONSALDO")
    assert len(program.condition_names) == 20
    cod_campo_invalido = next(
        c for c in program.condition_names if c.name == "COD-CAMPO-INVALIDO"
    )
    assert cod_campo_invalido.parent_name == "WS-COD-RETORNO"
    assert [v.value for v in cod_campo_invalido.values] == ["0005"]

    # --- SET condicion TO TRUE se representa estructuradamente --------
    set_with_condition_target = [
        stmt
        for paragraph in program.paragraphs
        for stmt in paragraph.statements
        if stmt.condition_name_target is not None
    ]
    assert len(set_with_condition_target) == 33, "las 33 SET del original deben resolverse"
    assert all(stmt.condition_set_value is True for stmt in set_with_condition_target)

    # --- IF con referencia directa a condicion-88 verificada ----------
    if_with_reference = [
        stmt
        for paragraph in program.paragraphs
        for stmt in paragraph.statements
        if stmt.referenced_condition_names
    ]
    assert if_with_reference, "se esperaba al menos una referencia IF/EVALUATE resuelta"

    # --- SemanticEffects contiene SET_CONDITION_TRUE -------------------
    artifact = compute_semantic_effects_artifact(run_dir, state.run_id)
    write_semantic_effects_artifact(run_dir, artifact)
    consaldo_effects = next(p for p in artifact.programs if p.program == "CONSALDO").effects
    set_condition_true = [
        e for e in consaldo_effects if e.kind == SemanticEffectKind.SET_CONDITION_TRUE
    ]
    assert len(set_condition_true) == 33
    assert all(
        e.condition_name is not None and e.parent_data_item is not None
        for e in set_condition_true
    )

    # --- nunca se inventa propagacion: ningun efecto ASSIGN_LITERAL/ --
    # --- COPY_VALUE se genero a partir de un SET_CONDITION -------------
    assert not any(
        e.kind in (SemanticEffectKind.ASSIGN_LITERAL, SemanticEffectKind.COPY_VALUE)
        and e.literal in {"0000", "0001", "0002", "0003", "0004", "0005", "9999"}
        for e in consaldo_effects
    )

    # --- candidatos V1: cero (patron SET-condicion no reconocido por --
    # --- Q0 en esta fase; V2 queda fuera de alcance) --------------------
    candidates = _load_candidates(run_dir)
    assert candidates.candidates == []

    # --- reglas V1: cero (consistente con cero candidatos) -------------
    rules_dir = run_dir / "artifacts" / "10-rules"
    if rules_dir.is_dir():
        assert list(rules_dir.glob("*.md")) == []


@pytest.mark.integration
def test_catherine_corrected_end_to_end_uses_move_literal_workaround(tmp_path: Path) -> None:
    _require_catherine_fixtures()
    require_jar()
    settings = build_settings(tmp_path)
    # Sin cliente fake instalado: si hay candidatos, RULE_DRAFTS_GENERATED
    # intenta un POST real a OPENAI_BASE_URL (host inexistente de prueba,
    # ver e2e_support.build_settings) y falla con LlmClientError -- nunca
    # se invoca un proveedor LLM real. Mismo patron ya establecido en
    # test_dependencies_built_integration.py.

    state = run_ingestion(CATHERINE_CORRECTED_ZIP, settings)
    run_dir = settings.runs_dir / state.run_id

    parsed_execution = next(s for s in state.stages if s.stage == PipelineStage.PARSED)
    assert parsed_execution.status == StageStatus.SUCCEEDED

    program = _load_canonical_program(run_dir, "CONSALDO")
    # El COBOL corregido conserva las declaraciones nivel 88 en DATA
    # DIVISION (no se reescribio el ZIP), pero PROCEDURE DIVISION ya no
    # las referencia: cero SET/IF resueltos contra ellas.
    assert len(program.condition_names) == 20
    resolved_sets = [
        stmt
        for paragraph in program.paragraphs
        for stmt in paragraph.statements
        if stmt.condition_name_target is not None
    ]
    assert resolved_sets == [], "el corregido no debe reinterpretar sus MOVE como SET-condicion"
    resolved_references = [
        stmt
        for paragraph in program.paragraphs
        for stmt in paragraph.statements
        if stmt.referenced_condition_names
    ]
    assert resolved_references == []

    # --- MOVE literales siguen generando ASSIGN_LITERAL -----------------
    artifact = compute_semantic_effects_artifact(run_dir, state.run_id)
    write_semantic_effects_artifact(run_dir, artifact)
    consaldo_effects = next(p for p in artifact.programs if p.program == "CONSALDO").effects
    cod_retorno_assign_literal = [
        e
        for e in consaldo_effects
        if e.kind == SemanticEffectKind.ASSIGN_LITERAL
        and e.target_data_items == ["WS-COD-RETORNO"]
    ]
    assert len(cod_retorno_assign_literal) == 15, (
        "15 MOVE literal a WS-COD-RETORNO (ver PAQUETE_..._VALIDACION.txt)"
    )
    assert not any(e.kind == SemanticEffectKind.SET_CONDITION_TRUE for e in consaldo_effects)

    # --- candidatos V1: 14 (ver PAQUETE_..._VALIDACION.txt), consistente
    candidates = _load_candidates(run_dir)
    assert len(candidates.candidates) == 14
