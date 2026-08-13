"""E2E hermetico del corpus Ground Truth formal completo (Fase
15B4-CANDIDATE-QUALITY-5D, cierre de P1-CORPUS-GAP-GT; corregido en
5D-SAFETY).

Ejecuta `examples/PAQUETE_SINTETICO_GROUND_TRUTH_FASE_15D.zip` (14
programas, exactamente 1:1 con los 14 casos de `config/ground_truth/
synthetic_engineering.yaml` -- el catalogo PRODUCTIVO, que desde
5D-SAFETY seccion 1 ya NO declara BY_REFERENCE_OUTPUT, capacidad
DEFERRED_UNSUPPORTED shadow-only que nunca reaparece en 06-candidates.json;
ese caso vive en su propio catalogo separado
`config/ground_truth/shadow_interprocedural.yaml`, ejercitado por
`tests/pipeline/test_ground_truth_by_reference_output_integration.py`) a
traves del pipeline productivo REAL (`run_ingestion`) y valida el
resultado -- via `compute_functional_validation_report`, el default
PRODUCTIVO que lee unicamente `06-candidates.json` -- contra el catalogo
REAL versionado, nunca contra expectativas hardcodeadas en el test.

Reemplaza la dependencia de scripts ad hoc/scratch para esta evidencia
(Seccion 22/23 de 5D): reproducible desde repo limpio, sin timestamps ni
IDs aleatorios en la semantica esperada.

Se ejecuta en DOS modos (`enhanced_candidates_enabled` False/True) porque
Ground Truth describe la semantica esperada del PROGRAMA, no lo que la
configuracion actual logra detectar (Seccion 16): DEFAULT debe mostrar
MISSING para las familias V2 (gateadas por el flag, apagado por defecto);
ENHANCED debe alcanzar precision=1.0/recall=1.0/f1=1.0 POR CONSTRUCCION
(5D-SAFETY seccion 9) -- el catalogo productivo, ahora, contiene
UNICAMENTE capacidades PRODUCTIVE_RULE reales, asi que no hace falta
ninguna exclusion manual/post-hoc del denominador.

5D-SAFETY-2 (cierre de BRANCH_EXPECTATION_NOT_EXACT): los dos casos
multi-branch (`gt-positive-return-code-if-else`,
`gt-positive-sqlcode-evaluate-state-transition`) ya NO usan un unico
`minimum_count` amplio -- cada branch/outcome deliberado tiene su propia
`GroundTruthExpectedRule` con `expected_output_literal` exacto (reutiliza
`UnifiedCandidateReference.output_literal`, campo ya existente y
estable). 15 expectations fact-level en total (antes 12 case-level) --
metricas ahora exactas por hecho funcional, no solo por caso."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from altamira_extractor.contracts.functional_validation import MatchOutcome
from altamira_extractor.pipeline.functional_validation_service import (
    compute_functional_validation_report,
)
from altamira_extractor.pipeline.runner import run_ingestion

from .e2e_support import require_jar
from .hermetic_llm_support import build_hermetic_settings, hermetic_llm_and_network_guard

pytestmark = pytest.mark.integration

_PACKAGE_PATH = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "PAQUETE_SINTETICO_GROUND_TRUTH_FASE_15D.zip"
)
_GROUND_TRUTH_PATH = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "ground_truth"
    / "synthetic_engineering.yaml"
)

# Casos POSITIVE cuya familia (V2: LEVEL_88_RETURN_CODE/STATE_TRANSITION/
# CALCULATION) solo se productiviza con enhanced_candidates_enabled=True --
# en DEFAULT deben quedar MISSING honestamente, nunca un TP fabricado.
_ENHANCED_ONLY_CASE_IDS = frozenset(
    {
        "gt-positive-calculation-if-compute-multiplication",
        "gt-positive-calculation-if-subtract",
        "gt-positive-calculation-unconditional-add",
        "gt-positive-calculation-unconditional-compute-multiplication",
        "gt-positive-calculation-unconditional-divide-giving",
        "gt-positive-calculation-unconditional-multiply-giving",
        "gt-positive-level88-return-code-nested-set",
        "gt-positive-sql-select-into-state-transition",
        "gt-positive-sqlcode-evaluate-state-transition",
        "gt-positive-state-transition-if-status-target",
    }
)

def _build_settings(tmp_path: Path, *, enhanced: bool) -> object:
    return build_hermetic_settings(
        tmp_path,
        NEO4J_URI=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        NEO4J_USER=os.environ.get("NEO4J_USER", "neo4j"),
        NEO4J_PASSWORD=os.environ.get("NEO4J_PASSWORD", "neo4j"),
        NEO4J_DATABASE=os.environ.get("NEO4J_DATABASE", "neo4j"),
        enhanced_candidates_enabled=enhanced,
    )


def _run_and_qualify(tmp_path: Path, *, enhanced: bool):
    require_jar()
    settings = _build_settings(tmp_path, enhanced=enhanced)
    with hermetic_llm_and_network_guard():
        state = run_ingestion(source_zip=_PACKAGE_PATH, settings=settings)
    run_dir = settings.runs_dir / state.run_id  # type: ignore[attr-defined]
    report = compute_functional_validation_report(run_dir, state.run_id, _GROUND_TRUTH_PATH)
    return state, report


def test_formal_ground_truth_corpus_default(tmp_path: Path) -> None:
    """Seccion 16: DEFAULT debe mostrar MISSING para toda familia V2 --
    nunca un TP fabricado por una capacidad apagada por configuracion."""
    state, report = _run_and_qualify(tmp_path, enhanced=False)

    assert state.current_stage.value == "COMPLETED"
    by_case_id = {c.case_id: c for c in report.case_results}

    for case_id in _ENHANCED_ONLY_CASE_IDS:
        case = by_case_id[case_id]
        assert case.applicability.value == "APPLICABLE", case_id
        assert case.outcome == MatchOutcome.MISSING, case_id

    # 5D-SAFETY seccion 3: gt-positive-return-code-if-else ahora exige
    # minimum_count=2 (dos hechos distintos, uno por branch). V1/Q0 por
    # si solo (DEFAULT, sin V2_RETURN_CODE_PROPAGATION) produce UNICAMENTE
    # el candidato ghost coarse (outcome_code=None, 1 fila) -- nunca 2
    # candidatos por-branch. Honesto MISSING en DEFAULT: la precision
    # completa de este caso especifico solo la alcanza V2/ENHANCED.
    assert by_case_id["gt-positive-return-code-if-else"].outcome == MatchOutcome.MISSING
    # gt-positive-declared-value-return-code es single-branch
    # (minimum_count=1, sin cambios) -- MATCHED incluso en DEFAULT.
    assert by_case_id["gt-positive-declared-value-return-code"].outcome == MatchOutcome.MATCHED
    assert (
        by_case_id["gt-negative-untagged-counter-decision"].outcome
        == MatchOutcome.CONFIRMED_ABSENT
    )
    assert (
        by_case_id["gt-negative-state-transition-nonfunctional-indicator-name"].outcome
        == MatchOutcome.CONFIRMED_ABSENT
    )


def test_formal_ground_truth_corpus_enhanced(tmp_path: Path) -> None:
    """Seccion 9 (5D-SAFETY, criterio de cierre) + 5D-SAFETY-2 (fact-level
    exacto, cierre de BRANCH_EXPECTATION_NOT_EXACT): ENHANCED debe
    alcanzar FP=0/FN=0/precision=1.0/recall=1.0/f1=1.0 POR CONSTRUCCION --
    CADA una de las 15 expectations fact-level (una por hecho funcional
    deliberado, incluidos los branches individuales de los dos casos
    multi-branch) en MATCHED con matched_count EXACTO=1, ambos NEGATIVE
    en CONFIRMED_ABSENT. Sin exclusiones manuales/post-hoc: el catalogo ya
    contiene UNICAMENTE capacidades PRODUCTIVE_RULE. Cualquier FN/FP/
    duplicado inesperado aqui es evidencia de PRODUCT DEFECT o GT DEFECT,
    nunca algo que deba ocultarse ajustando el catalogo."""
    state, report = _run_and_qualify(tmp_path, enhanced=True)

    assert state.current_stage.value == "COMPLETED"

    expectation_count = 0
    for case in report.case_results:
        if case.kind.value == "NEGATIVE":
            assert case.outcome == MatchOutcome.CONFIRMED_ABSENT, case.case_id
            continue
        assert case.outcome == MatchOutcome.MATCHED, case.case_id
        for expectation in case.expectation_results:
            assert expectation.outcome == MatchOutcome.MATCHED, expectation.expectation_id
            assert expectation.matched_count == 1, expectation.expectation_id
            expectation_count += 1
    assert expectation_count == 15

    metrics = report.metrics
    assert metrics.false_positive_count == 0
    assert metrics.false_negative_count == 0
    assert metrics.precision == 1.0
    assert metrics.recall == 1.0
    assert metrics.f1_score == 1.0
