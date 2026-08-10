"""Tests de `pipeline/enhanced_candidate_integration.py` (Fase 15B3-B:
realineacion minima del motor de extraccion de reglas). Reutiliza los
casos obligatorios A/B/C de `test_v2_detectors.py` (via
`tests/pipeline/v2_shadow_helpers.py`) para probar la conversion real
`V2ShadowCandidate -> RuleCandidate` y la deduplicacion contra V1 --
nunca vuelve a probar la logica de deteccion V2 en si (eso ya lo cubre
`test_v2_detectors.py`)."""

from __future__ import annotations

from collections.abc import Sequence

from altamira_extractor.contracts.candidate import CandidateArtifact, RuleCandidate
from altamira_extractor.contracts.candidate_promotion_assessment import (
    CandidateSource,
    UnifiedRuleFamily,
)
from altamira_extractor.contracts.canonical import (
    CanonicalConditionName,
    CanonicalConditionValue,
    CanonicalDataItem,
    CanonicalParagraph,
    CanonicalProgram,
)
from altamira_extractor.contracts.enums import (
    LocationKind,
    NodeLabel,
    RelationshipType,
    SourceFormat,
    StatementKind,
)
from altamira_extractor.contracts.semantic_graph import GraphNode, GraphRelationship, SemanticGraph
from altamira_extractor.pipeline.enhanced_candidate_integration import (
    _ConvertedCandidate,
    _merge_candidates,
    detect_enhanced_candidates,
    functional_identity_key,
)

from .v2_shadow_helpers import (
    HASH,
    SRC,
    build_ctx,
    data_item_node_id,
    decision_node_id_for,
    make_stmt,
    paragraph_node_id,
    program_node_id,
)

_RUN_ID = "run-15b3b"


def _empty_v1_candidates() -> CandidateArtifact:
    return CandidateArtifact(
        run_id=_RUN_ID,
        source_package_hash=HASH,
        semantic_graph_hash=HASH,
        invariants_query_hash=HASH,
        q0_query_hash=HASH,
        candidates=[],
    )


def _program(
    *, program_name: str, data_items: list[CanonicalDataItem], paragraphs, condition_names=()
) -> CanonicalProgram:
    return CanonicalProgram(
        program_name=program_name,
        source_file=SRC,
        source_hash=HASH,
        source_package_hash=HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        data_items=data_items,
        condition_names=list(condition_names),
        paragraphs=paragraphs,
    )


def _data_item(qualified_name: str, *, level: int = 1) -> CanonicalDataItem:
    return CanonicalDataItem(
        name=qualified_name,
        qualified_name=qualified_name,
        level=level,
        location_kind=LocationKind.UNKNOWN,
    )


def _build_graph_with_source_file(
    *,
    program: CanonicalProgram,
    decisions: Sequence[tuple[str, int, str]],
    data_item_tags: dict[str, str | None],
) -> SemanticGraph:
    """Igual que `v2_shadow_helpers.build_semantic_graph`, pero con
    `source_file` en el nodo Paragraph (como produce realmente
    `semantic_graph_builder.py`) -- necesario para que
    `_convert_v2_candidate` pueda construir un `RuleCandidate` completo."""
    program_name = program.program_name
    prog_node_id = program_node_id(program_name)
    nodes: list[GraphNode] = [
        GraphNode(id=prog_node_id, labels=[NodeLabel.PROGRAM], properties={"name": program_name})
    ]
    relationships: list[GraphRelationship] = []

    for paragraph in program.paragraphs:
        para_node_id = paragraph_node_id(program_name, paragraph.name)
        nodes.append(
            GraphNode(
                id=para_node_id,
                labels=[NodeLabel.PARAGRAPH],
                properties={
                    "name": paragraph.name,
                    "line_start": 1,
                    "line_end": 9999,
                    "source_file": program.source_file,
                },
            )
        )
        relationships.append(
            GraphRelationship(
                type=RelationshipType.CONTAINS, from_id=prog_node_id, to_id=para_node_id
            )
        )

    ordinal_by_paragraph: dict[str, int] = {}
    for paragraph_name, line_start, expression in decisions:
        ordinal_by_paragraph[paragraph_name] = ordinal_by_paragraph.get(paragraph_name, 0) + 1
        ordinal = ordinal_by_paragraph[paragraph_name]
        dec_id = decision_node_id_for(program_name, paragraph_name, line_start, ordinal)
        nodes.append(
            GraphNode(
                id=dec_id,
                labels=[NodeLabel.DECISION],
                properties={
                    "line_start": line_start,
                    "line_end": line_start,
                    "expression": expression,
                    "outcome_code": None,
                    "rule_type": None,
                },
            )
        )
        relationships.append(
            GraphRelationship(
                type=RelationshipType.HAS_DECISION,
                from_id=paragraph_node_id(program_name, paragraph_name),
                to_id=dec_id,
            )
        )

    for qualified_name, tag in data_item_tags.items():
        nodes.append(
            GraphNode(
                id=data_item_node_id(program_name, qualified_name),
                labels=[NodeLabel.DATA_ITEM],
                properties={"qualified_name": qualified_name, "semantic_tag": tag},
            )
        )

    return SemanticGraph(
        source_package_hash=HASH,
        nodes=sorted(nodes, key=lambda n: n.id),
        relationships=sorted(relationships, key=lambda r: (r.type.value, r.from_id, r.to_id)),
    )


# ---------------------------------------------------------------------------
# Caso A: propagacion de literal -> RETURN_CODE_RULE
# ---------------------------------------------------------------------------


def _case_a_program_and_decisions() -> tuple[CanonicalProgram, list[tuple[str, int, str]]]:
    if_stmt = make_stmt(
        statement_id="P1::A::0::IF", kind=StatementKind.IF, line_start=10, expression="CONDICION"
    )
    s1 = make_stmt(
        statement_id="P1::A::1::MOVE",
        target_data_items=["WS-AUX"],
        variables_written=["WS-AUX"],
        assigned_literal="0005",
        parent_statement_id="P1::A::0::IF",
        branch_kind="THEN",
    )
    s2 = make_stmt(
        statement_id="P1::A::2::MOVE",
        target_data_items=["WS-COD-RETORNO"],
        variables_written=["WS-COD-RETORNO"],
        variables_read=["WS-AUX"],
        parent_statement_id="P1::A::0::IF",
        branch_kind="THEN",
    )
    paragraph = CanonicalParagraph(
        name="A",
        source_text="A.",
        location_kind=LocationKind.UNKNOWN,
        statements=[if_stmt, s1, s2],
        variables_read=["WS-AUX"],
        variables_written=["WS-AUX", "WS-COD-RETORNO"],
    )
    program = _program(
        program_name="PROG1",
        data_items=[_data_item("WS-AUX"), _data_item("WS-COD-RETORNO")],
        paragraphs=[paragraph],
    )
    return program, [("A", 10, "CONDICION")]


def test_return_code_propagation_produces_a_promotable_rule_candidate() -> None:
    program, decisions = _case_a_program_and_decisions()
    graph = _build_graph_with_source_file(
        program=program,
        decisions=decisions,
        data_item_tags={"WS-AUX": None, "WS-COD-RETORNO": "return_code"},
    )

    new_candidates, warnings = detect_enhanced_candidates(
        canonical_programs=[program],
        semantic_graph=graph,
        v1_candidates=_empty_v1_candidates(),
        run_id=_RUN_ID,
        source_package_hash=HASH,
    )

    assert warnings == []
    assert len(new_candidates) == 1
    candidate = new_candidates[0]
    assert candidate.candidate_source == CandidateSource.V2
    assert candidate.rule_family == UnifiedRuleFamily.RETURN_CODE
    assert candidate.candidate_id.startswith(f"candidate::enhanced::{HASH}::")
    assert candidate.outcome_code == "0005"
    assert candidate.condition == "CONDICION"
    assert candidate.decision_id == decision_node_id_for("PROG1", "A", 10, 1)
    assert candidate.paragraph_id == paragraph_node_id("PROG1", "A")
    assert candidate.source_file == SRC
    assert candidate.evidence_ids != []
    assert candidate.evidence_ids == sorted(set(candidate.evidence_ids))


# ---------------------------------------------------------------------------
# Caso B: SET condicion-88 TO TRUE -> LEVEL_88_RETURN_CODE_RULE
# ---------------------------------------------------------------------------


def _case_b_program_and_decisions() -> tuple[CanonicalProgram, list[tuple[str, int, str]]]:
    condition = CanonicalConditionName(
        name="COD-CAMPO-INVALIDO",
        qualified_name="WS-COD-RETORNO.COD-CAMPO-INVALIDO",
        parent_name="WS-COD-RETORNO",
        parent_qualified_name="WS-COD-RETORNO",
        values=[CanonicalConditionValue(value="0005", location_kind=LocationKind.UNKNOWN)],
        location_kind=LocationKind.UNKNOWN,
    )
    if_stmt = make_stmt(
        statement_id="P1::PARA::0::IF",
        kind=StatementKind.IF,
        line_start=10,
        expression="ERROR-DE-ENTRADA",
    )
    s1 = make_stmt(
        statement_id="P1::PARA::1::SET",
        kind=StatementKind.SET,
        target_data_items=["COD-CAMPO-INVALIDO"],
        variables_written=["COD-CAMPO-INVALIDO"],
        condition_name_target="COD-CAMPO-INVALIDO",
        condition_set_value=True,
        parent_statement_id="P1::PARA::0::IF",
        branch_kind="THEN",
    )
    paragraph = CanonicalParagraph(
        name="PARA",
        source_text="PARA.",
        location_kind=LocationKind.UNKNOWN,
        statements=[if_stmt, s1],
        variables_written=["COD-CAMPO-INVALIDO"],
    )
    program = _program(
        program_name="PROG1",
        data_items=[_data_item("WS-COD-RETORNO")],
        paragraphs=[paragraph],
        condition_names=[condition],
    )
    return program, [("PARA", 10, "ERROR-DE-ENTRADA")]


def test_level_88_produces_a_promotable_rule_candidate() -> None:
    """Caso B: `V2_RETURN_CODE_PROPAGATION` (CONDITION_LITERAL como
    evidencia valida, Fase 5 condicion #3) y `V2_LEVEL_88_RETURN_CODE`
    disparan ambos sobre la misma Decision -- Fase 9 exige conservar
    ambos por separado (nunca fusionar solo por compartir decision/
    target/literal si vienen de detectores distintos, ver
    `test_v2_detectors.py::test_case_b_return_code_propagation_also_fires_and_is_related_not_merged`)."""
    program, decisions = _case_b_program_and_decisions()
    graph = _build_graph_with_source_file(
        program=program,
        decisions=decisions,
        data_item_tags={"WS-COD-RETORNO": "return_code"},
    )

    new_candidates, warnings = detect_enhanced_candidates(
        canonical_programs=[program],
        semantic_graph=graph,
        v1_candidates=_empty_v1_candidates(),
        run_id=_RUN_ID,
        source_package_hash=HASH,
    )

    assert warnings == []
    assert len(new_candidates) == 2
    families = {candidate.rule_family for candidate in new_candidates}
    assert families == {UnifiedRuleFamily.RETURN_CODE, UnifiedRuleFamily.LEVEL_88_RETURN_CODE}
    decision_id = decision_node_id_for("PROG1", "PARA", 10, 1)
    for candidate in new_candidates:
        assert candidate.outcome_code == "0005"
        assert candidate.decision_id == decision_id
    assert len({candidate.candidate_id for candidate in new_candidates}) == 2


# ---------------------------------------------------------------------------
# Deduplicacion contra V1 (Fase 15B3-B1, regla D: identidad funcional
# completa, no solo decision_id)
# ---------------------------------------------------------------------------


def test_v2_candidate_matching_v1_functional_key_merges_into_v1_identity() -> None:
    """Regla D: cuando V1 y V2 describen la MISMA regla (misma
    decision+condicion+efecto+familia), el resultado es una sola entrada
    que REEMPLAZA -- por `candidate_id` -- al candidato V1 original con
    `evidence_ids` fusionados; no un descarte silencioso ni una entrada
    adicional."""
    program, decisions = _case_a_program_and_decisions()
    graph = _build_graph_with_source_file(
        program=program,
        decisions=decisions,
        data_item_tags={"WS-AUX": None, "WS-COD-RETORNO": "return_code"},
    )
    decision_id = decision_node_id_for("PROG1", "A", 10, 1)
    v1_candidate = RuleCandidate(
        candidate_id=f"candidate::q0-return-code-decision::1.0::{HASH}::{decision_id}",
        paragraph_id=paragraph_node_id("PROG1", "A"),
        paragraph_name="A",
        decision_id=decision_id,
        detector_id="q0-return-code-decision",
        detector_version="1.0",
        detector_score=1.0,
        condition="CONDICION",
        outcome_code="0005",
        rule_type=None,
        line_start=10,
        source_file=SRC,
        source_package_hash=HASH,
    )
    v1_candidates = CandidateArtifact(
        run_id=_RUN_ID,
        source_package_hash=HASH,
        semantic_graph_hash=HASH,
        invariants_query_hash=HASH,
        q0_query_hash=HASH,
        candidates=[v1_candidate],
    )

    result, warnings = detect_enhanced_candidates(
        canonical_programs=[program],
        semantic_graph=graph,
        v1_candidates=v1_candidates,
        run_id=_RUN_ID,
        source_package_hash=HASH,
    )

    assert len(result) == 1
    merged = result[0]
    assert merged.candidate_id == v1_candidate.candidate_id
    assert merged.detector_id == v1_candidate.detector_id
    assert merged.candidate_source == CandidateSource.V1
    assert merged.evidence_ids != []
    assert len(warnings) == 1
    assert v1_candidate.candidate_id in warnings[0]
    assert "V2_RETURN_CODE_PROPAGATION" in warnings[0]


def test_v1_rule_candidates_are_never_mutated_in_place_by_dedup() -> None:
    """Regla D: la fusion produce una COPIA (`model_copy`); el objeto
    `RuleCandidate` V1 original -- tal como vive en
    `v1_candidates.candidates` -- nunca se muta en su lugar."""
    program, decisions = _case_a_program_and_decisions()
    graph = _build_graph_with_source_file(
        program=program,
        decisions=decisions,
        data_item_tags={"WS-AUX": None, "WS-COD-RETORNO": "return_code"},
    )
    decision_id = decision_node_id_for("PROG1", "A", 10, 1)
    v1_candidate = RuleCandidate(
        candidate_id=f"candidate::q0-return-code-decision::1.0::{HASH}::{decision_id}",
        paragraph_id=paragraph_node_id("PROG1", "A"),
        paragraph_name="A",
        decision_id=decision_id,
        detector_id="q0-return-code-decision",
        detector_version="1.0",
        detector_score=1.0,
        condition="CONDICION",
        outcome_code="0005",
        rule_type=None,
        line_start=10,
        source_file=SRC,
        source_package_hash=HASH,
    )
    v1_candidates = CandidateArtifact(
        run_id=_RUN_ID,
        source_package_hash=HASH,
        semantic_graph_hash=HASH,
        invariants_query_hash=HASH,
        q0_query_hash=HASH,
        candidates=[v1_candidate],
    )

    detect_enhanced_candidates(
        canonical_programs=[program],
        semantic_graph=graph,
        v1_candidates=v1_candidates,
        run_id=_RUN_ID,
        source_package_hash=HASH,
    )

    assert v1_candidates.candidates == [v1_candidate]
    assert v1_candidate.evidence_ids == []
    assert v1_candidate.candidate_source == CandidateSource.V1


def test_v2_candidate_with_different_decision_is_added_not_deduplicated() -> None:
    """Contraste con el caso D: cuando V1 no tiene NINGUN candidato para
    la decision detectada por V2, el resultado es un candidato nuevo
    (candidate_source=V2), nunca un descarte."""
    program, decisions = _case_a_program_and_decisions()
    graph = _build_graph_with_source_file(
        program=program,
        decisions=decisions,
        data_item_tags={"WS-AUX": None, "WS-COD-RETORNO": "return_code"},
    )
    v1_candidate = RuleCandidate(
        candidate_id=f"candidate::q0-return-code-decision::1.0::{HASH}::other-decision",
        paragraph_id=paragraph_node_id("PROG1", "OTHER"),
        paragraph_name="OTHER",
        decision_id="other-decision",
        detector_id="q0-return-code-decision",
        detector_version="1.0",
        detector_score=1.0,
        condition="OTRA-CONDICION",
        outcome_code="9999",
        rule_type=None,
        line_start=99,
        source_file=SRC,
        source_package_hash=HASH,
    )
    v1_candidates = CandidateArtifact(
        run_id=_RUN_ID,
        source_package_hash=HASH,
        semantic_graph_hash=HASH,
        invariants_query_hash=HASH,
        q0_query_hash=HASH,
        candidates=[v1_candidate],
    )

    result, warnings = detect_enhanced_candidates(
        canonical_programs=[program],
        semantic_graph=graph,
        v1_candidates=v1_candidates,
        run_id=_RUN_ID,
        source_package_hash=HASH,
    )

    assert warnings == []
    assert len(result) == 1
    assert result[0].candidate_source == CandidateSource.V2
    assert result[0].candidate_id != v1_candidate.candidate_id


# ---------------------------------------------------------------------------
# Reglas A-E de identidad funcional (Fase 15B3-B1), probadas directamente
# contra la funcion pura de fusion `_merge_candidates`
# ---------------------------------------------------------------------------


def _converted(
    *,
    paragraph_id: str = "program::AR::op::PROG1::1::abc123::paragraph::A",
    decision_id: str = "dec-1",
    condition: str = "CONDICION",
    outcome_code: str | None = "0005",
    rule_family: UnifiedRuleFamily = UnifiedRuleFamily.RETURN_CODE,
    detector_id: str = "V2_RETURN_CODE_PROPAGATION",
    detector_version: str = "1.0",
    detector_score: float = 1.0,
    line_start: int = 10,
    source_file: str = SRC,
    evidence_ids: tuple[str, ...] = (),
    source_v2_candidate_id: str = "v2::x::1",
) -> _ConvertedCandidate:
    key = functional_identity_key(
        paragraph_id=paragraph_id,
        decision_id=decision_id,
        condition=condition,
        effect=outcome_code or "",
        rule_family=rule_family,
    )
    return _ConvertedCandidate(
        key=key,
        paragraph_id=paragraph_id,
        paragraph_name="A",
        decision_id=decision_id,
        condition=condition,
        outcome_code=outcome_code,
        rule_family=rule_family,
        detector_id=detector_id,
        detector_version=detector_version,
        detector_score=detector_score,
        line_start=line_start,
        source_file=source_file,
        evidence_ids=evidence_ids,
        source_v2_candidate_id=source_v2_candidate_id,
    )


def test_rule_a_same_decision_condition_effect_family_merge_into_one() -> None:
    item1 = _converted(
        evidence_ids=("effect::1",),
        detector_id="V2_RETURN_CODE_PROPAGATION",
        source_v2_candidate_id="v2::a::1",
    )
    item2 = _converted(
        evidence_ids=("fact::1",),
        detector_id="V2_LEVEL_88_RETURN_CODE",
        source_v2_candidate_id="v2::b::1",
    )
    assert item1.key == item2.key

    candidates, warnings = _merge_candidates(
        [item1, item2], _empty_v1_candidates(), source_package_hash=HASH
    )

    assert len(candidates) == 1
    assert candidates[0].evidence_ids == ["effect::1", "fact::1"]
    assert warnings == []


def test_rule_b_same_decision_different_effect_are_distinct_rules() -> None:
    item1 = _converted(outcome_code="0005", source_v2_candidate_id="v2::a::1")
    item2 = _converted(outcome_code="0006", source_v2_candidate_id="v2::b::1")
    assert item1.key != item2.key

    candidates, _ = _merge_candidates(
        [item1, item2], _empty_v1_candidates(), source_package_hash=HASH
    )

    assert len(candidates) == 2
    assert {c.outcome_code for c in candidates} == {"0005", "0006"}


def test_rule_c_same_decision_different_family_are_distinct_rules() -> None:
    item1 = _converted(rule_family=UnifiedRuleFamily.RETURN_CODE, source_v2_candidate_id="v2::a::1")
    item2 = _converted(
        rule_family=UnifiedRuleFamily.LEVEL_88_RETURN_CODE, source_v2_candidate_id="v2::b::1"
    )
    assert item1.key != item2.key

    candidates, _ = _merge_candidates(
        [item1, item2], _empty_v1_candidates(), source_package_hash=HASH
    )

    assert len(candidates) == 2
    assert {c.rule_family for c in candidates} == {
        UnifiedRuleFamily.RETURN_CODE,
        UnifiedRuleFamily.LEVEL_88_RETURN_CODE,
    }


def test_rule_d_v1_and_v2_same_rule_merge_into_v1_identity_pure() -> None:
    item = _converted(evidence_ids=("effect::1", "fact::1"))
    v1_candidate = RuleCandidate(
        candidate_id=f"candidate::q0-return-code-decision::1.0::{HASH}::{item.decision_id}",
        paragraph_id=item.paragraph_id,
        paragraph_name="A",
        decision_id=item.decision_id,
        detector_id="q0-return-code-decision",
        detector_version="1.0",
        detector_score=1.0,
        condition=item.condition,
        outcome_code=item.outcome_code,
        rule_type=None,
        line_start=item.line_start,
        source_file=item.source_file,
        source_package_hash=HASH,
    )
    artifact = CandidateArtifact(
        run_id=_RUN_ID,
        source_package_hash=HASH,
        semantic_graph_hash=HASH,
        invariants_query_hash=HASH,
        q0_query_hash=HASH,
        candidates=[v1_candidate],
    )

    candidates, warnings = _merge_candidates([item], artifact, source_package_hash=HASH)

    assert len(candidates) == 1
    merged = candidates[0]
    assert merged.candidate_id == v1_candidate.candidate_id
    assert merged.detector_id == v1_candidate.detector_id
    assert merged.evidence_ids == ["effect::1", "fact::1"]
    assert artifact.candidates == [v1_candidate]
    assert v1_candidate.evidence_ids == []
    assert len(warnings) == 1
    assert v1_candidate.candidate_id in warnings[0]
    assert item.detector_id in warnings[0]


def test_rule_e_input_order_never_affects_result() -> None:
    item_a = _converted(
        decision_id="dec-a", evidence_ids=("e1",), source_v2_candidate_id="v2::x::1"
    )
    item_b = _converted(
        decision_id="dec-b",
        outcome_code="0006",
        evidence_ids=("e2",),
        source_v2_candidate_id="v2::x::2",
    )
    item_c = _converted(
        decision_id="dec-a",
        evidence_ids=("e3",),
        detector_id="V2_LEVEL_88_RETURN_CODE",
        source_v2_candidate_id="v2::y::1",
    )
    assert item_a.key == item_c.key
    assert item_a.key != item_b.key

    forward = _merge_candidates(
        [item_a, item_b, item_c], _empty_v1_candidates(), source_package_hash=HASH
    )
    backward = _merge_candidates(
        [item_c, item_b, item_a], _empty_v1_candidates(), source_package_hash=HASH
    )
    shuffled = _merge_candidates(
        [item_b, item_c, item_a], _empty_v1_candidates(), source_package_hash=HASH
    )

    assert forward == backward == shuffled
    candidates, warnings = forward
    assert len(candidates) == 2
    assert warnings == []


def test_rule_e_v1_candidate_order_never_affects_result() -> None:
    item = _converted(decision_id="dec-a")
    other_v1 = RuleCandidate(
        candidate_id=f"candidate::q0-return-code-decision::1.0::{HASH}::dec-z",
        paragraph_id=item.paragraph_id,
        paragraph_name="A",
        decision_id="dec-z",
        detector_id="q0-return-code-decision",
        detector_version="1.0",
        detector_score=1.0,
        condition="OTRA",
        outcome_code="0001",
        rule_type=None,
        line_start=1,
        source_file=SRC,
        source_package_hash=HASH,
    )
    matching_v1 = RuleCandidate(
        candidate_id=f"candidate::q0-return-code-decision::1.0::{HASH}::{item.decision_id}",
        paragraph_id=item.paragraph_id,
        paragraph_name="A",
        decision_id=item.decision_id,
        detector_id="q0-return-code-decision",
        detector_version="1.0",
        detector_score=1.0,
        condition=item.condition,
        outcome_code=item.outcome_code,
        rule_type=None,
        line_start=item.line_start,
        source_file=item.source_file,
        source_package_hash=HASH,
    )

    forward = _merge_candidates(
        [item],
        CandidateArtifact(
            run_id=_RUN_ID,
            source_package_hash=HASH,
            semantic_graph_hash=HASH,
            invariants_query_hash=HASH,
            q0_query_hash=HASH,
            candidates=sorted([other_v1, matching_v1], key=lambda c: c.candidate_id),
        ),
        source_package_hash=HASH,
    )
    reversed_order = _merge_candidates(
        [item],
        CandidateArtifact(
            run_id=_RUN_ID,
            source_package_hash=HASH,
            semantic_graph_hash=HASH,
            invariants_query_hash=HASH,
            q0_query_hash=HASH,
            candidates=sorted([matching_v1, other_v1], key=lambda c: c.candidate_id),
        ),
        source_package_hash=HASH,
    )

    assert forward == reversed_order


# ---------------------------------------------------------------------------
# STATE_CHANGE_RULE nunca se promueve
# ---------------------------------------------------------------------------


def test_state_change_only_case_never_produces_a_candidate() -> None:
    """Caso C de `test_v2_detectors.py`: WS-AUX no es `return_code`, asi
    que solo `V2_STATE_CHANGE` (PARTIAL, nunca promovible) lo detecta --
    `detect_enhanced_candidates` no lo invoca en absoluto."""
    if_stmt = make_stmt(
        statement_id="P1::A::0::IF", kind=StatementKind.IF, line_start=10, expression="CONDICION"
    )
    s1 = make_stmt(
        statement_id="P1::A::1::MOVE",
        target_data_items=["WS-AUX"],
        variables_written=["WS-AUX"],
        assigned_literal="0005",
        parent_statement_id="P1::A::0::IF",
        branch_kind="THEN",
    )
    paragraph = CanonicalParagraph(
        name="A",
        source_text="A.",
        location_kind=LocationKind.UNKNOWN,
        statements=[if_stmt, s1],
        variables_written=["WS-AUX"],
    )
    program = _program(
        program_name="PROG1",
        data_items=[_data_item("WS-AUX")],
        paragraphs=[paragraph],
    )
    graph = _build_graph_with_source_file(
        program=program,
        decisions=[("A", 10, "CONDICION")],
        data_item_tags={"WS-AUX": None},
    )

    new_candidates, warnings = detect_enhanced_candidates(
        canonical_programs=[program],
        semantic_graph=graph,
        v1_candidates=_empty_v1_candidates(),
        run_id=_RUN_ID,
        source_package_hash=HASH,
    )

    assert new_candidates == []
    assert warnings == []


# ---------------------------------------------------------------------------
# Robustez frente a un grafo incompleto (nunca fabrica datos sinteticos)
# ---------------------------------------------------------------------------


def test_paragraph_node_without_source_file_is_discarded_with_warning() -> None:
    """`v2_shadow_helpers.build_ctx`/`build_semantic_graph` (compartido
    por otros tests V2) no incluye `source_file` en el nodo Paragraph --
    `_convert_v2_candidate` debe descartar el candidato con un warning
    trazable, nunca fabricar un `source_file` sintetico ni lanzar."""
    program, decisions = _case_a_program_and_decisions()
    ctx = build_ctx(
        program=program,
        decisions=decisions,
        data_item_tags={"WS-AUX": None, "WS-COD-RETORNO": "return_code"},
    )

    new_candidates, warnings = detect_enhanced_candidates(
        canonical_programs=[program],
        semantic_graph=ctx.semantic_graph,
        v1_candidates=_empty_v1_candidates(),
        run_id=_RUN_ID,
        source_package_hash=HASH,
    )

    assert new_candidates == []
    assert len(warnings) == 1
    assert "line_start/source_file" in warnings[0]


# ---------------------------------------------------------------------------
# Determinismo
# ---------------------------------------------------------------------------


def test_result_is_deterministic_across_repeated_calls() -> None:
    program, decisions = _case_a_program_and_decisions()
    graph = _build_graph_with_source_file(
        program=program,
        decisions=decisions,
        data_item_tags={"WS-AUX": None, "WS-COD-RETORNO": "return_code"},
    )

    first = detect_enhanced_candidates(
        canonical_programs=[program],
        semantic_graph=graph,
        v1_candidates=_empty_v1_candidates(),
        run_id=_RUN_ID,
        source_package_hash=HASH,
    )
    second = detect_enhanced_candidates(
        canonical_programs=[program],
        semantic_graph=graph,
        v1_candidates=_empty_v1_candidates(),
        run_id=_RUN_ID,
        source_package_hash=HASH,
    )

    assert first == second
