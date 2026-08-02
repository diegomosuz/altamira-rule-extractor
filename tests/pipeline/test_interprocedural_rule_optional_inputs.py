"""Auditoria de cierre de Fase 8 (Parte 2): matriz de combinaciones ante
la ausencia de cada input opcional
(`CandidateArtifact`/`SemanticGraph`->`V2ShadowCandidatesArtifact`/
`SemanticEnrichmentArtifact`) pasado a
`analyze_interprocedural_rule_candidates`
(`pipeline/interprocedural_rule_detector.py`). Verifica, para cada
combinacion, las reglas A-F de la auditoria:

A. La ausencia de V1 no demuestra que no exista equivalente V1.
B. La ausencia de V2 no demuestra que no exista equivalente V2.
C. INTERPROCEDURAL_ONLY solo se usa cuando V1 y V2 estuvieron
   realmente disponibles y no se encontro candidato comparable.
D. Si V1 o V2 no estan disponibles: nunca se fabrica una comparacion
   negativa, esa dimension queda sin evaluar, se emite un diagnostico
   explicito.
E. La ausencia de SemanticEnrichment deshabilita UNICAMENTE
   STATE_TRANSITION_RULE, nunca RETURN_CODE_RULE ni BY_REFERENCE_RULE,
   y emite un diagnostico explicito del detector no evaluado.
F. El summary nunca confunde cero matches (interprocedural_only) con
   fuente ausente (not_evaluated)."""

from __future__ import annotations

from altamira_extractor.contracts.enums import CallPassingMode
from altamira_extractor.contracts.interprocedural_rule_candidates import (
    InterproceduralComparisonStatus,
    InterproceduralRelationStatus,
    InterproceduralRuleType,
)
from altamira_extractor.contracts.v2_shadow_candidates import V2ShadowCandidatesArtifact
from altamira_extractor.pipeline.interprocedural_rule_detector import (
    analyze_interprocedural_rule_candidates,
)

from .interprocedural_rule_helpers import (
    HASH,
    analyze_all,
    make_call,
    make_call_arg,
    make_data_item,
    make_entry_param,
    make_linkage_item,
    make_move,
    make_paragraph,
    make_program,
    make_semantic_enrichment,
    make_v1_candidates,
)


def _full_fixture():
    """caller/callee que produce simultaneamente un RETURN_CODE_RULE, un
    BY_REFERENCE_RULE y (con SemanticEnrichment) un STATE_TRANSITION_RULE
    deterministico -- para que la matriz de combinaciones ejercite los
    tres detectores a la vez."""
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-R"), make_data_item("WS-STATUS")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_move("CALLER::MAIN::0::MOVE", target="WS-STATUS", literal="PENDING"),
                    make_call("CALLER::MAIN::1::CALL", call_returning_data_item="WS-R"),
                    make_call(
                        "CALLER::MAIN::2::CALL",
                        call_arguments=[make_call_arg("WS-STATUS", CallPassingMode.REFERENCE)],
                    ),
                ],
            )
        ],
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-R"), make_linkage_item("LK-STATUS")],
        entry_returning_data_item="LK-R",
        entry_parameters=[make_entry_param("LK-STATUS")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_move("CALLEE::MAIN::0::MOVE", target="LK-R", literal="0009"),
                    make_move("CALLEE::MAIN::1::MOVE", target="LK-STATUS", literal="APPROVED"),
                ],
            )
        ],
    )
    return caller, callee


def _analyze(*, with_v1: bool, with_v2: bool, with_enrichment: bool, run_id: str = "run1"):
    caller, callee = _full_fixture()
    programs = [caller, callee]
    interprocedural_propagation, effects, propagation, linkage = analyze_all(
        programs, run_id=run_id
    )
    v1_candidates = make_v1_candidates(run_id=run_id) if with_v1 else None
    # V2ShadowCandidatesArtifact real (Fase 5) esta fuera de alcance de
    # este modulo (requeriria SemanticGraph real): se representa aqui
    # unicamente por su AUSENCIA (None) o por un artefacto real vacio
    # construido igual que en test_interprocedural_rule_comparator.py --
    # la dimension bajo prueba es la disponibilidad, no el contenido.
    v2_candidates = None
    if with_v2:
        v2_candidates = V2ShadowCandidatesArtifact(
            run_id=run_id,
            source_package_hash=HASH,
            source_artifact_hashes={"artifacts/02-canonical": HASH},
            semantic_effects_schema_version=effects.schema_version,
            semantic_effects_analyzer_version=effects.analyzer_version,
            semantic_propagation_schema_version=propagation.schema_version,
            semantic_propagation_analyzer_version=propagation.analyzer_version,
            summary={
                "detector_count": 0,
                "v1_candidate_count": 0,
                "v2_candidate_count": 0,
                "deterministic_count": 0,
                "partial_count": 0,
                "blocked_count": 0,
                "matched_count": 0,
                "v1_only_count": 0,
                "v2_only_count": 0,
                "related_not_equivalent_count": 0,
                "counts_by_rule_type": {},
            },
            executions=[],
            comparisons=[],
        )
    semantic_enrichment = (
        make_semantic_enrichment(
            program=caller, qualified_name="WS-STATUS", semantic_tag="status", run_id=run_id
        )
        if with_enrichment
        else None
    )
    return analyze_interprocedural_rule_candidates(
        canonical_programs=programs,
        v1_candidates=v1_candidates,
        v2_candidates=v2_candidates,
        semantic_effects=effects,
        semantic_propagation=propagation,
        interprocedural_call_linkage=linkage,
        interprocedural_propagation=interprocedural_propagation,
        semantic_enrichment=semantic_enrichment,
        run_id=run_id,
        source_package_hash=HASH,
        source_artifact_hashes={"artifacts/02-canonical": HASH},
    )


# --- 1. Ausencia de CandidateArtifact V1 (== artifacts/06-candidates.json) --


def test_v1_absent_service_continues_all_detectors_run() -> None:
    artifact = _analyze(with_v1=False, with_v2=True, with_enrichment=True)
    rule_types = {c.rule_type for c in artifact.candidates}
    assert rule_types == {
        InterproceduralRuleType.RETURN_CODE_RULE,
        InterproceduralRuleType.BY_REFERENCE_RULE,
        InterproceduralRuleType.STATE_TRANSITION_RULE,
    }
    assert all(
        c.v1_relation == InterproceduralRelationStatus.NOT_EVALUATED for c in artifact.comparisons
    )
    assert "V1_CANDIDATES_UNAVAILABLE_ARTIFACTS_06_CANDIDATES_JSON_ABSENT" in artifact.diagnostics
    # regla A: nunca se afirma que no existe equivalente V1 -- ninguna
    # comparacion queda en INTERPROCEDURAL_ONLY solo por V1 ausente.
    assert not any(
        c.status == InterproceduralComparisonStatus.INTERPROCEDURAL_ONLY
        for c in artifact.comparisons
    )


# --- 2/3. Ausencia de SemanticGraph -> V2ShadowCandidatesArtifact ----------


def test_v2_absent_service_continues_all_detectors_run() -> None:
    artifact = _analyze(with_v1=True, with_v2=False, with_enrichment=True)
    rule_types = {c.rule_type for c in artifact.candidates}
    assert rule_types == {
        InterproceduralRuleType.RETURN_CODE_RULE,
        InterproceduralRuleType.BY_REFERENCE_RULE,
        InterproceduralRuleType.STATE_TRANSITION_RULE,
    }
    assert all(
        c.v2_relation == InterproceduralRelationStatus.NOT_EVALUATED for c in artifact.comparisons
    )
    assert "V2_SHADOW_CANDIDATES_UNAVAILABLE" in artifact.diagnostics
    # regla B: nunca se afirma que no existe equivalente V2.
    assert not any(
        c.status == InterproceduralComparisonStatus.INTERPROCEDURAL_ONLY
        for c in artifact.comparisons
    )


# --- 4/7. Ausencia de SemanticEnrichmentArtifact ---------------------------


def test_semantic_enrichment_absent_disables_only_state_transition_rule() -> None:
    artifact = _analyze(with_v1=True, with_v2=True, with_enrichment=False)
    rule_types = {c.rule_type for c in artifact.candidates}
    # regla E: RETURN_CODE_RULE y BY_REFERENCE_RULE nunca se ven afectados.
    assert InterproceduralRuleType.RETURN_CODE_RULE in rule_types
    assert InterproceduralRuleType.BY_REFERENCE_RULE in rule_types
    assert InterproceduralRuleType.STATE_TRANSITION_RULE not in rule_types
    assert "STATE_TRANSITION_RULE_DETECTOR_SKIPPED_NO_SEMANTIC_ENRICHMENT" in artifact.diagnostics


def test_semantic_enrichment_present_enables_state_transition_rule() -> None:
    artifact = _analyze(with_v1=True, with_v2=True, with_enrichment=True)
    rule_types = {c.rule_type for c in artifact.candidates}
    assert InterproceduralRuleType.STATE_TRANSITION_RULE in rule_types
    assert (
        "STATE_TRANSITION_RULE_DETECTOR_SKIPPED_NO_SEMANTIC_ENRICHMENT" not in artifact.diagnostics
    )


# --- 5/6. Combinaciones completas (ambas fuentes; ninguna; solo una) -------


def test_both_v1_and_v2_available_interprocedural_only_is_reachable() -> None:
    """regla C: solo con AMBAS fuentes disponibles y evaluadas puede
    aparecer INTERPROCEDURAL_ONLY (aqui, con V1/V2 vacios -- evaluados,
    sin candidatos relacionados)."""
    artifact = _analyze(with_v1=True, with_v2=True, with_enrichment=True)
    assert any(
        c.status == InterproceduralComparisonStatus.INTERPROCEDURAL_ONLY
        for c in artifact.comparisons
    )
    assert artifact.summary.interprocedural_only_count > 0
    assert artifact.summary.not_evaluated_count == 0
    assert (
        "V1_CANDIDATES_UNAVAILABLE_ARTIFACTS_06_CANDIDATES_JSON_ABSENT" not in artifact.diagnostics
    )
    assert "V2_SHADOW_CANDIDATES_UNAVAILABLE" not in artifact.diagnostics


def test_neither_v1_nor_v2_available_never_interprocedural_only() -> None:
    artifact = _analyze(with_v1=False, with_v2=False, with_enrichment=True)
    assert not any(
        c.status == InterproceduralComparisonStatus.INTERPROCEDURAL_ONLY
        for c in artifact.comparisons
    )
    assert artifact.summary.interprocedural_only_count == 0
    assert artifact.summary.not_evaluated_count == len(artifact.comparisons)
    assert "V1_CANDIDATES_UNAVAILABLE_ARTIFACTS_06_CANDIDATES_JSON_ABSENT" in artifact.diagnostics
    assert "V2_SHADOW_CANDIDATES_UNAVAILABLE" in artifact.diagnostics


def test_all_three_optional_sources_absent_service_still_produces_valid_artifact() -> None:
    """regla F combinada: incluso sin V1/V2/SemanticEnrichment, el
    servicio sigue produciendo un artefacto valido (summary reconciliado
    por el propio contrato) con los dos detectores independientes de V1/V2
    activos."""
    artifact = _analyze(with_v1=False, with_v2=False, with_enrichment=False)
    rule_types = {c.rule_type for c in artifact.candidates}
    assert InterproceduralRuleType.RETURN_CODE_RULE in rule_types
    assert InterproceduralRuleType.BY_REFERENCE_RULE in rule_types
    assert InterproceduralRuleType.STATE_TRANSITION_RULE not in rule_types
    assert artifact.summary.candidate_count == len(artifact.candidates)
    assert artifact.summary.not_evaluated_count == artifact.summary.candidate_count
    assert artifact.summary.interprocedural_only_count == 0
    assert len(artifact.diagnostics) == 3


# --- F. El summary nunca confunde cero matches con fuente ausente ----------


def test_summary_distinguishes_zero_matches_from_source_unavailable() -> None:
    both_available_no_matches = _analyze(with_v1=True, with_v2=True, with_enrichment=True)
    neither_available = _analyze(with_v1=False, with_v2=False, with_enrichment=True)

    assert both_available_no_matches.summary.interprocedural_only_count > 0
    assert both_available_no_matches.summary.not_evaluated_count == 0

    assert neither_available.summary.interprocedural_only_count == 0
    assert neither_available.summary.not_evaluated_count > 0
