"""Tests del analizador PURO orquestador (Fase 8 de la ampliacion
semantica, `feat/interprocedural-rule-detectors-shadow`):
`pipeline/interprocedural_rule_detector.py::analyze_interprocedural_rule_candidates`.
Verifica el ensamblaje completo del artefacto (detectores + comparador +
summary) contra los analizadores reales de Fase 2/4/6/7."""

from __future__ import annotations

from altamira_extractor.contracts.interprocedural_rule_candidates import (
    InterproceduralComparisonStatus,
    InterproceduralRuleType,
)
from altamira_extractor.pipeline.interprocedural_rule_detector import (
    analyze_interprocedural_rule_candidates,
)

from .interprocedural_rule_helpers import (
    HASH,
    analyze_all,
    make_call,
    make_data_item,
    make_linkage_item,
    make_move,
    make_paragraph,
    make_program,
)


def _returning_pair():
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-R")],
        paragraphs=[
            make_paragraph(
                "MAIN", [make_call("CALLER::MAIN::0::CALL", call_returning_data_item="WS-R")]
            )
        ],
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-R")],
        entry_returning_data_item="LK-R",
        paragraphs=[
            make_paragraph(
                "MAIN", [make_move("CALLEE::MAIN::0::MOVE", target="LK-R", literal="0009")]
            )
        ],
    )
    return caller, callee


def _analyze(programs, run_id="run1"):
    interprocedural_propagation, effects, propagation, linkage = analyze_all(
        programs, run_id=run_id
    )
    return analyze_interprocedural_rule_candidates(
        canonical_programs=programs,
        v1_candidates=None,
        v2_candidates=None,
        semantic_effects=effects,
        semantic_propagation=propagation,
        interprocedural_call_linkage=linkage,
        interprocedural_propagation=interprocedural_propagation,
        semantic_enrichment=None,
        run_id=run_id,
        source_package_hash=HASH,
        source_artifact_hashes={"artifacts/02-canonical": HASH},
    )


def test_artifact_assembles_candidates_and_comparisons_consistently() -> None:
    """v1_candidates=v2_candidates=None (Fase 8, sin fuentes) -- la
    comparacion nunca finge INTERPROCEDURAL_ONLY: queda NOT_EVALUATED
    (auditoria de cierre, regla D)."""
    caller, callee = _returning_pair()
    artifact = _analyze([caller, callee])
    assert artifact.schema_version == "1.0"
    assert artifact.analyzer_version == "1.0"
    assert len(artifact.candidates) == 1
    assert len(artifact.comparisons) == 1
    assert artifact.candidates[0].rule_type == InterproceduralRuleType.RETURN_CODE_RULE
    assert artifact.comparisons[0].status == InterproceduralComparisonStatus.NOT_EVALUATED
    assert artifact.summary.candidate_count == 1
    assert artifact.summary.deterministic_count == 1
    assert artifact.summary.not_evaluated_count == 1
    assert artifact.summary.interprocedural_only_count == 0


def test_artifact_diagnostics_trace_absent_optional_sources() -> None:
    caller, callee = _returning_pair()
    artifact = _analyze([caller, callee])
    assert "V1_CANDIDATES_UNAVAILABLE_ARTIFACTS_06_CANDIDATES_JSON_ABSENT" in artifact.diagnostics
    assert "V2_SHADOW_CANDIDATES_UNAVAILABLE" in artifact.diagnostics
    assert "STATE_TRANSITION_RULE_DETECTOR_SKIPPED_NO_SEMANTIC_ENRICHMENT" in artifact.diagnostics


def test_artifact_is_byte_identical_for_identical_inputs() -> None:
    caller, callee = _returning_pair()
    artifact_1 = _analyze([caller, callee])
    artifact_2 = _analyze([caller, callee])
    assert artifact_1.model_dump_json() == artifact_2.model_dump_json()


def test_artifact_is_empty_but_valid_when_no_call_sites_exist() -> None:
    lonely = make_program("LONELY")
    artifact = _analyze([lonely])
    assert artifact.candidates == []
    assert artifact.comparisons == []
    assert artifact.summary.candidate_count == 0
    assert artifact.summary.deterministic_count == 0
    assert artifact.summary.blocked_count == 0


def test_source_artifact_hashes_and_schema_versions_are_threaded_through() -> None:
    caller, callee = _returning_pair()
    artifact = _analyze([caller, callee])
    assert artifact.source_artifact_hashes == {"artifacts/02-canonical": HASH}
    assert artifact.canonical_schema_versions == ["1.2"]
    assert artifact.semantic_effects_schema_version
    assert artifact.semantic_propagation_schema_version
    assert artifact.interprocedural_call_linkage_schema_version == "1.0"
    assert artifact.interprocedural_propagation_schema_version == "1.0"
