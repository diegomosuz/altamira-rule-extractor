"""Tests del comparador PURO V1/V2 de candidatos interprocedurales (Fase
8 de la ampliacion semantica, `feat/interprocedural-rule-detectors-shadow`):
`pipeline/interprocedural_rule_comparator.py`. Cubre los items 26-32 de
la lista de 40 tests obligatorios de Fase 8, mas los casos de la
auditoria de cierre (Parte 4: dimensiones V1/V2 independientes, sin
doble conteo por dimension; Parte 2: fuentes ausentes nunca fabrican una
comparacion negativa)."""

from __future__ import annotations

from altamira_extractor.contracts.candidate import CandidateArtifact, RuleCandidate
from altamira_extractor.contracts.interprocedural_rule_candidates import (
    InterproceduralCandidateSupport,
    InterproceduralComparisonStatus,
    InterproceduralRelationStatus,
    InterproceduralRuleCandidate,
    InterproceduralRuleEvidence,
    InterproceduralRuleType,
)
from altamira_extractor.contracts.v2_shadow_candidates import (
    V2CandidateSourceReference,
    V2CandidateSupport,
    V2RuleType,
    V2ShadowCandidate,
)
from altamira_extractor.pipeline.interprocedural_rule_comparator import (
    build_comparison,
    build_comparisons,
)
from altamira_extractor.pipeline.interprocedural_rule_detectors import detect_return_code_rule

from .interprocedural_rule_helpers import (
    build_ctx,
    make_call,
    make_data_item,
    make_linkage_item,
    make_move,
    make_paragraph,
    make_program,
)

HASH = "d" * 64


def _base_candidate(**overrides: object) -> InterproceduralRuleCandidate:
    fields: dict[str, object] = dict(
        candidate_id="ipr::interprocedural-return-code-rule::seed",
        detector="interprocedural-return-code-rule",
        rule_type=InterproceduralRuleType.RETURN_CODE_RULE,
        support=InterproceduralCandidateSupport.DETERMINISTIC,
        caller_program="CALLER",
        callee_program="CALLEE",
        caller_paragraph="MAIN",
        call_site_id="callsite::x",
        target="WS-R",
        output_literal="0009",
        evidence=[
            InterproceduralRuleEvidence(
                evidence_id="evidence::x",
                caller_program="CALLER",
                callee_program="CALLEE",
                call_site_id="callsite::x",
                statement_id="CALLER::MAIN::0::CALL",
                output_literal="0009",
            )
        ],
    )
    fields.update(overrides)
    return InterproceduralRuleCandidate(**fields)  # type: ignore[arg-type]


def _v1_candidate(
    *, paragraph_id: str, outcome_code: str, candidate_id: str = "cand::v1"
) -> RuleCandidate:
    return RuleCandidate(
        candidate_id=candidate_id,
        paragraph_id=paragraph_id,
        paragraph_name="MAIN",
        decision_id=f"{paragraph_id}::decision::1::1",
        detector_id="return-code-decision",
        detector_version="1.0",
        detector_score=1.0,
        condition="WS-R = '0009'",
        outcome_code=outcome_code,
        rule_type=None,
        line_start=1,
        source_file="01-codigo/cobol/CALLER.cbl",
        source_package_hash=HASH,
    )


def _v2_candidate(
    *, program: str, target_variable: str, resolved_literal: str, candidate_id: str = "cand::v2"
) -> V2ShadowCandidate:
    return V2ShadowCandidate(
        candidate_id=candidate_id,
        detector_id="V2_RETURN_CODE_PROPAGATION",
        detector_version="1.0",
        rule_type=V2RuleType.RETURN_CODE_RULE,
        support=V2CandidateSupport.DETERMINISTIC,
        detector_score=1.0,
        program=program,
        paragraph="MAIN",
        anchor_statement_id="CALLEE::MAIN::0::MOVE",
        target_variable=target_variable,
        target_qualified_name=target_variable,
        resolved_literal=resolved_literal,
        semantic_effect_ids=["effect::CALLEE::MAIN::CALLEE::MAIN::0::MOVE::ASSIGN_LITERAL::0"],
        propagation_fact_ids=[
            f"fact::CALLEE::MAIN::root::{target_variable}::CALLEE::MAIN::0::MOVE::DIRECT_LITERAL::0"
        ],
        source_references=[
            V2CandidateSourceReference(
                program="CALLEE",
                paragraph="MAIN",
                statement_id="CALLEE::MAIN::0::MOVE",
                effect_id="effect::CALLEE::MAIN::CALLEE::MAIN::0::MOVE::ASSIGN_LITERAL::0",
                fact_id=(
                    f"fact::CALLEE::MAIN::root::{target_variable}::"
                    "CALLEE::MAIN::0::MOVE::DIRECT_LITERAL::0"
                ),
            )
        ],
        reason="Fixture de comparacion V2 (Fase 8, test).",
    )


_CALLER_PARAGRAPH_ID = f"program::AR::APP::CALLER::1.0::{HASH[:12]}::paragraph::MAIN"


def test_26_matched_v1_same_program_and_literal() -> None:
    candidate = _base_candidate()
    v1 = _v1_candidate(paragraph_id=_CALLER_PARAGRAPH_ID, outcome_code="0009")
    comparison = build_comparison(
        candidate, v1_available=True, v1_candidates=[v1], v2_available=True, v2_candidates=[]
    )
    assert comparison.status == InterproceduralComparisonStatus.MATCHED_V1
    assert comparison.v1_relation == InterproceduralRelationStatus.MATCHED
    assert comparison.v2_relation == InterproceduralRelationStatus.NOT_FOUND
    assert comparison.v1_candidate_id == v1.candidate_id
    assert comparison.v2_candidate_id is None
    assert comparison.shared_literal == "0009"


def test_27_related_v1_same_program_different_literal() -> None:
    candidate = _base_candidate()
    v1 = _v1_candidate(paragraph_id=_CALLER_PARAGRAPH_ID, outcome_code="9999")
    comparison = build_comparison(
        candidate, v1_available=True, v1_candidates=[v1], v2_available=True, v2_candidates=[]
    )
    assert comparison.status == InterproceduralComparisonStatus.RELATED_V1
    assert comparison.v1_relation == InterproceduralRelationStatus.RELATED
    assert comparison.v1_candidate_id == v1.candidate_id
    assert comparison.v2_candidate_id is None


def test_28_matched_v2_same_program_target_and_literal() -> None:
    candidate = _base_candidate()
    v2 = _v2_candidate(program="CALLER", target_variable="WS-R", resolved_literal="0009")
    comparison = build_comparison(
        candidate, v1_available=True, v1_candidates=[], v2_available=True, v2_candidates=[v2]
    )
    assert comparison.status == InterproceduralComparisonStatus.MATCHED_V2
    assert comparison.v1_relation == InterproceduralRelationStatus.NOT_FOUND
    assert comparison.v2_relation == InterproceduralRelationStatus.MATCHED
    assert comparison.v2_candidate_id == v2.candidate_id
    assert comparison.v1_candidate_id is None
    assert comparison.shared_target == "WS-R"


def test_29_related_v2_same_program_and_target_different_literal() -> None:
    candidate = _base_candidate()
    v2 = _v2_candidate(program="CALLER", target_variable="WS-R", resolved_literal="9999")
    comparison = build_comparison(
        candidate, v1_available=True, v1_candidates=[], v2_available=True, v2_candidates=[v2]
    )
    assert comparison.status == InterproceduralComparisonStatus.RELATED_V2
    assert comparison.v2_relation == InterproceduralRelationStatus.RELATED
    assert comparison.v2_candidate_id == v2.candidate_id
    assert comparison.v1_candidate_id is None


def test_30_interprocedural_only_when_no_v1_v2_relation_exists() -> None:
    """AMBAS fuentes disponibles, ninguna encuentra relacion --
    INTERPROCEDURAL_ONLY solo es alcanzable cuando ambas se evaluaron
    (regla C de la auditoria de cierre)."""
    candidate = _base_candidate()
    unrelated_v1 = _v1_candidate(
        paragraph_id=f"program::AR::APP::OTHER::1.0::{HASH[:12]}::paragraph::MAIN",
        outcome_code="0009",
    )
    unrelated_v2 = _v2_candidate(program="OTHER", target_variable="WS-R", resolved_literal="0009")
    comparison = build_comparison(
        candidate,
        v1_available=True,
        v1_candidates=[unrelated_v1],
        v2_available=True,
        v2_candidates=[unrelated_v2],
    )
    assert comparison.status == InterproceduralComparisonStatus.INTERPROCEDURAL_ONLY
    assert comparison.v1_relation == InterproceduralRelationStatus.NOT_FOUND
    assert comparison.v2_relation == InterproceduralRelationStatus.NOT_FOUND
    assert comparison.v1_candidate_id is None
    assert comparison.v2_candidate_id is None


def test_blocked_candidate_never_compared() -> None:
    candidate = _base_candidate(
        support=InterproceduralCandidateSupport.BLOCKED,
        output_literal=None,
        barriers=["NON_RETURNING_TERMINATION"],
        evidence=[
            InterproceduralRuleEvidence(
                evidence_id="evidence::blocked",
                caller_program="CALLER",
                callee_program="CALLEE",
                call_site_id="callsite::x",
                statement_id="CALLER::MAIN::0::CALL",
            )
        ],
    )
    v1 = _v1_candidate(paragraph_id=_CALLER_PARAGRAPH_ID, outcome_code="0009")
    comparison = build_comparison(
        candidate, v1_available=True, v1_candidates=[v1], v2_available=True, v2_candidates=[]
    )
    assert comparison.status == InterproceduralComparisonStatus.BLOCKED
    assert comparison.v1_relation == InterproceduralRelationStatus.NOT_EVALUATED
    assert comparison.v2_relation == InterproceduralRelationStatus.NOT_EVALUATED
    assert comparison.v1_candidate_id is None
    assert comparison.v2_candidate_id is None


def test_31_matched_takes_priority_and_never_double_counts() -> None:
    """Un candidato con match V1 Y V2 posibles siempre produce EXACTAMENTE
    una comparacion (MATCHED_V1 tiene prioridad, ver
    `interprocedural_rule_comparator.py::build_comparison`) -- nunca dos.
    La relacion V2 secundaria NO se pierde: se conserva en
    `v2_relation`/`v2_candidate_id`, evitando doble conteo en el summary
    (particionado por `status`, no por dimension) sin perder informacion."""
    candidate = _base_candidate()
    v1 = _v1_candidate(paragraph_id=_CALLER_PARAGRAPH_ID, outcome_code="0009")
    v2 = _v2_candidate(program="CALLER", target_variable="WS-R", resolved_literal="0009")
    comparisons = build_comparisons(
        [candidate],
        v1_candidates_artifact=CandidateArtifact(
            run_id="run1",
            source_package_hash=HASH,
            semantic_graph_hash=HASH,
            invariants_query_hash=HASH,
            q0_query_hash=HASH,
            candidates=[v1],
        ),
        v2_candidates=[v2],
    )
    assert len(comparisons) == 1
    comparison = comparisons[0]
    assert comparison.status == InterproceduralComparisonStatus.MATCHED_V1
    assert comparison.v1_relation == InterproceduralRelationStatus.MATCHED
    assert comparison.v1_candidate_id == v1.candidate_id
    assert comparison.v2_relation == InterproceduralRelationStatus.MATCHED
    assert comparison.v2_candidate_id == v2.candidate_id


def test_match_v1_and_related_v2_simultaneously() -> None:
    """match V1 + related V2 (Parte 4, caso de test explicito): V2
    comparte target pero con un literal distinto -- RELATED, no
    MATCHED -- mientras V1 SI coincide exactamente."""
    candidate = _base_candidate()
    v1 = _v1_candidate(paragraph_id=_CALLER_PARAGRAPH_ID, outcome_code="0009")
    v2 = _v2_candidate(program="CALLER", target_variable="WS-R", resolved_literal="9999")
    comparison = build_comparison(
        candidate, v1_available=True, v1_candidates=[v1], v2_available=True, v2_candidates=[v2]
    )
    assert comparison.status == InterproceduralComparisonStatus.MATCHED_V1
    assert comparison.v1_relation == InterproceduralRelationStatus.MATCHED
    assert comparison.v2_relation == InterproceduralRelationStatus.RELATED
    assert comparison.v1_candidate_id == v1.candidate_id
    assert comparison.v2_candidate_id == v2.candidate_id


def test_related_v1_and_match_v2_simultaneously() -> None:
    """related V1 + match V2 (Parte 4, caso de test explicito): V1
    comparte programa pero con un outcome_code distinto -- RELATED --
    mientras V2 SI coincide exactamente, asi que la clasificacion
    principal es MATCHED_V2 (prioridad V1 > V2 solo aplica cuando V1
    tambien es MATCHED)."""
    candidate = _base_candidate()
    v1 = _v1_candidate(paragraph_id=_CALLER_PARAGRAPH_ID, outcome_code="9999")
    v2 = _v2_candidate(program="CALLER", target_variable="WS-R", resolved_literal="0009")
    comparison = build_comparison(
        candidate, v1_available=True, v1_candidates=[v1], v2_available=True, v2_candidates=[v2]
    )
    assert comparison.status == InterproceduralComparisonStatus.MATCHED_V2
    assert comparison.v1_relation == InterproceduralRelationStatus.RELATED
    assert comparison.v2_relation == InterproceduralRelationStatus.MATCHED
    assert comparison.v1_candidate_id == v1.candidate_id
    assert comparison.v2_candidate_id == v2.candidate_id


def test_v1_absent_never_fabricates_a_negative_comparison() -> None:
    """Ausencia de V1 (Parte 2/4): nunca INTERPROCEDURAL_ONLY -- la
    dimension V1 queda NOT_EVALUATED, nunca NOT_FOUND."""
    candidate = _base_candidate()
    v2 = _v2_candidate(program="OTHER", target_variable="WS-OTHER", resolved_literal="0009")
    comparison = build_comparison(
        candidate, v1_available=False, v1_candidates=[], v2_available=True, v2_candidates=[v2]
    )
    assert comparison.status == InterproceduralComparisonStatus.NOT_EVALUATED
    assert comparison.v1_relation == InterproceduralRelationStatus.NOT_EVALUATED
    assert comparison.v2_relation == InterproceduralRelationStatus.NOT_FOUND
    assert comparison.v1_candidate_id is None


def test_v2_absent_never_fabricates_a_negative_comparison() -> None:
    """Ausencia de V2 (Parte 2/4): nunca INTERPROCEDURAL_ONLY -- la
    dimension V2 queda NOT_EVALUATED, nunca NOT_FOUND."""
    candidate = _base_candidate()
    v1 = _v1_candidate(
        paragraph_id=f"program::AR::APP::OTHER::1.0::{HASH[:12]}::paragraph::MAIN",
        outcome_code="9999",
    )
    comparison = build_comparison(
        candidate, v1_available=True, v1_candidates=[v1], v2_available=False, v2_candidates=[]
    )
    assert comparison.status == InterproceduralComparisonStatus.NOT_EVALUATED
    assert comparison.v1_relation == InterproceduralRelationStatus.NOT_FOUND
    assert comparison.v2_relation == InterproceduralRelationStatus.NOT_EVALUATED
    assert comparison.v2_candidate_id is None


def test_neither_source_available_is_not_evaluated_never_interprocedural_only() -> None:
    """Ninguna fuente disponible (Parte 4, caso de test explicito):
    ambas dimensiones NOT_EVALUATED, status NOT_EVALUATED -- nunca
    INTERPROCEDURAL_ONLY (eso exigiria ambas evaluadas y NOT_FOUND)."""
    candidate = _base_candidate()
    comparison = build_comparison(
        candidate, v1_available=False, v1_candidates=[], v2_available=False, v2_candidates=[]
    )
    assert comparison.status == InterproceduralComparisonStatus.NOT_EVALUATED
    assert comparison.v1_relation == InterproceduralRelationStatus.NOT_EVALUATED
    assert comparison.v2_relation == InterproceduralRelationStatus.NOT_EVALUATED
    assert comparison.v1_candidate_id is None
    assert comparison.v2_candidate_id is None


def test_build_comparisons_none_means_source_unavailable_not_empty_list() -> None:
    """`build_comparisons` distingue `v2_candidates=None` (fuente nunca
    disponible) de `v2_candidates=[]` (fuente disponible, cero
    candidatos) -- ambas producen una lista Python vacia internamente,
    pero solo la primera debe marcar v2_relation=NOT_EVALUATED."""
    candidate = _base_candidate()
    v1_artifact = CandidateArtifact(
        run_id="run1",
        source_package_hash=HASH,
        semantic_graph_hash=HASH,
        invariants_query_hash=HASH,
        q0_query_hash=HASH,
        candidates=[],
    )

    unavailable = build_comparisons(
        [candidate], v1_candidates_artifact=v1_artifact, v2_candidates=None
    )
    assert unavailable[0].v2_relation == InterproceduralRelationStatus.NOT_EVALUATED
    assert unavailable[0].status == InterproceduralComparisonStatus.NOT_EVALUATED

    available_but_empty = build_comparisons(
        [candidate], v1_candidates_artifact=v1_artifact, v2_candidates=[]
    )
    assert available_but_empty[0].v2_relation == InterproceduralRelationStatus.NOT_FOUND
    assert available_but_empty[0].status == InterproceduralComparisonStatus.INTERPROCEDURAL_ONLY


def test_32_summary_reconciles_with_real_detector_output() -> None:
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
    ctx = build_ctx([caller, callee])
    candidates = detect_return_code_rule(ctx)
    comparisons = build_comparisons(candidates, v1_candidates_artifact=None, v2_candidates=None)
    assert len(comparisons) == len(candidates)
    assert {c.interprocedural_candidate_id for c in comparisons} == {
        c.candidate_id for c in candidates
    }
    assert all(c.status == InterproceduralComparisonStatus.NOT_EVALUATED for c in comparisons)


def test_comparison_never_modifies_v1_v2_inputs() -> None:
    candidate = _base_candidate()
    v1 = _v1_candidate(paragraph_id=_CALLER_PARAGRAPH_ID, outcome_code="0009")
    v2 = _v2_candidate(program="CALLER", target_variable="WS-R", resolved_literal="9999")
    v1_before = v1.model_copy(deep=True)
    v2_before = v2.model_copy(deep=True)
    build_comparison(
        candidate, v1_available=True, v1_candidates=[v1], v2_available=True, v2_candidates=[v2]
    )
    assert v1 == v1_before
    assert v2 == v2_before
