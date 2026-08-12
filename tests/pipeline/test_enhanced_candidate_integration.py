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
    suppress_superseded_v1_return_code_ghosts,
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


def assert_only_state_change_discard_warnings(warnings: list[str], *, targets: list[str]) -> None:
    """Fase 15B3-C1: `detect_enhanced_candidates` ahora tambien ejecuta
    `V2_STATE_CHANGE` sobre todo el programa -- cualquier target escrito
    deterministicamente pero sin semantic_tag de relevancia funcional
    (`status`/`status_flag`) produce un warning de descarte trazable,
    nunca un candidato. Los tests de RETURN_CODE/LEVEL_88_RETURN_CODE que
    reutilizan fixtures con targets intermedios sin tag (p. ej. WS-AUX)
    deben esperar exactamente estos warnings -- nunca `warnings == []`
    llanamente, ni un match de texto fragil sobre el hash del
    candidate_id interno."""
    assert len(warnings) == len(targets), warnings
    for warning, target in zip(sorted(warnings), sorted(targets), strict=True):
        assert "V2_STATE_CHANGE" in warning
        assert f"target {target!r}" in warning
        assert "permanece shadow" in warning


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

    # WS-AUX (intermediario de la propagacion, sin semantic_tag) tambien es
    # visto por V2_STATE_CHANGE -- se descarta (sin relevancia funcional
    # demostrada) con un warning trazable, nunca como STATE_TRANSITION.
    assert_only_state_change_discard_warnings(warnings, targets=["WS-AUX"])
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
    disparan ambos sobre la misma Decision, con `evidence_ids`
    identicos -- a nivel de DETECTOR (`v2_detectors.py`) ambos siguen
    produciendo su propio `V2ShadowCandidate` por separado (ver
    `test_v2_detectors.py::test_case_b_return_code_propagation_also_fires_and_is_related_not_merged`,
    sin cambios), pero desde Fase 15B4-CANDIDATE-QUALITY-2 la
    INTEGRACION productiva (`enhanced_candidate_integration.py`) los
    reconoce como el mismo hecho de negocio (evidencia exactamente
    igual) y conserva unicamente el LEVEL_88_RETURN_CODE, con un
    warning de corroboracion -- nunca las dos representaciones
    redundantes en `06-candidates.json`."""
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

    assert len(new_candidates) == 1
    assert len(warnings) == 1
    assert "corroborado por" in warnings[0]
    candidate = new_candidates[0]
    assert candidate.rule_family == UnifiedRuleFamily.LEVEL_88_RETURN_CODE
    decision_id = decision_node_id_for("PROG1", "PARA", 10, 1)
    assert candidate.outcome_code == "0005"
    assert candidate.decision_id == decision_id


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

    # WS-AUX (sin semantic_tag) produce ademas un warning de descarte de
    # V2_STATE_CHANGE (Fase 15B3-C1) -- se separa del warning de fusion D.
    merge_warnings = [w for w in warnings if "V2_STATE_CHANGE" not in w]
    state_change_warnings = [w for w in warnings if "V2_STATE_CHANGE" in w]
    assert len(merge_warnings) == 1
    assert v1_candidate.candidate_id in merge_warnings[0]
    assert "V2_RETURN_CODE_PROPAGATION" in merge_warnings[0]
    assert_only_state_change_discard_warnings(state_change_warnings, targets=["WS-AUX"])


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

    assert_only_state_change_discard_warnings(warnings, targets=["WS-AUX"])
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
    """Familias genuinamente distintas (no la pareja RETURN_CODE/
    LEVEL_88_RETURN_CODE, que desde Fase 15B4-CANDIDATE-QUALITY-2 tiene
    su propia excepcion estrecha -- ver
    test_level88_return_code_pair_with_identical_evidence_merges_into_level88
    mas abajo) -- deben seguir siendo reglas distintas."""
    item1 = _converted(rule_family=UnifiedRuleFamily.RETURN_CODE, source_v2_candidate_id="v2::a::1")
    item2 = _converted(
        rule_family=UnifiedRuleFamily.STATE_TRANSITION, source_v2_candidate_id="v2::b::1"
    )
    assert item1.key != item2.key

    candidates, _ = _merge_candidates(
        [item1, item2], _empty_v1_candidates(), source_package_hash=HASH
    )

    assert len(candidates) == 2
    assert {c.rule_family for c in candidates} == {
        UnifiedRuleFamily.RETURN_CODE,
        UnifiedRuleFamily.STATE_TRANSITION,
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
# Fase 15B4-CANDIDATE-QUALITY-2: corroboracion estrecha
# LEVEL_88_RETURN_CODE / RETURN_CODE (corrige la duplicacion demostrada en
# Fase 15B4-CANDIDATE-QUALITY-1 para el paquete real CONSALDO -- 13 hechos
# funcionales producian 26 RuleCandidate).
# ---------------------------------------------------------------------------


def test_level88_return_code_pair_with_identical_evidence_merges_into_level88() -> None:
    """Caso positivo: mismo program_id/paragraph_id/decision_id/
    condition/outcome_code y evidence_ids EXACTAMENTE iguales -> se
    fusiona en un unico candidato, conservando la identidad (key ->
    candidate_id) del LEVEL_88_RETURN_CODE original -- nunca una
    tercera identidad."""
    level88 = _converted(
        rule_family=UnifiedRuleFamily.LEVEL_88_RETURN_CODE,
        detector_id="V2_LEVEL_88_RETURN_CODE",
        source_v2_candidate_id="v2::level88::1",
        evidence_ids=("effect::1", "fact::1"),
    )
    return_code = _converted(
        rule_family=UnifiedRuleFamily.RETURN_CODE,
        detector_id="V2_RETURN_CODE_PROPAGATION",
        source_v2_candidate_id="v2::rc::1",
        evidence_ids=("effect::1", "fact::1"),
    )

    candidates, warnings = _merge_candidates(
        [return_code, level88], _empty_v1_candidates(), source_package_hash=HASH
    )

    assert len(candidates) == 1
    merged = candidates[0]
    assert merged.rule_family == UnifiedRuleFamily.LEVEL_88_RETURN_CODE
    assert merged.candidate_id == f"candidate::enhanced::{HASH}::{level88.key}"
    assert merged.evidence_ids == ["effect::1", "fact::1"]
    assert len(warnings) == 1
    assert "v2::level88::1" in warnings[0]
    assert "V2_RETURN_CODE_PROPAGATION" in warnings[0]
    assert "v2::rc::1" in warnings[0]


def test_level88_return_code_pair_with_different_evidence_never_merges() -> None:
    """Mismos campos funcionales, evidence_ids distintos -> nunca se
    fusiona (protege contra fusion sin evidencia identica real)."""
    level88 = _converted(
        rule_family=UnifiedRuleFamily.LEVEL_88_RETURN_CODE,
        detector_id="V2_LEVEL_88_RETURN_CODE",
        source_v2_candidate_id="v2::level88::1",
        evidence_ids=("effect::1", "fact::1"),
    )
    return_code = _converted(
        rule_family=UnifiedRuleFamily.RETURN_CODE,
        detector_id="V2_RETURN_CODE_PROPAGATION",
        source_v2_candidate_id="v2::rc::1",
        evidence_ids=("effect::2", "fact::2"),
    )

    candidates, warnings = _merge_candidates(
        [return_code, level88], _empty_v1_candidates(), source_package_hash=HASH
    )

    assert len(candidates) == 2
    assert {c.rule_family for c in candidates} == {
        UnifiedRuleFamily.LEVEL_88_RETURN_CODE,
        UnifiedRuleFamily.RETURN_CODE,
    }
    assert warnings == []


def test_level88_return_code_pair_with_different_decision_never_merges() -> None:
    """Misma evidencia (construida sinteticamente), decision_id
    distinto -> nunca se fusiona -- protege contra fusion basada
    unicamente en evidencia."""
    level88 = _converted(
        decision_id="dec-1",
        rule_family=UnifiedRuleFamily.LEVEL_88_RETURN_CODE,
        detector_id="V2_LEVEL_88_RETURN_CODE",
        source_v2_candidate_id="v2::level88::1",
        evidence_ids=("effect::1", "fact::1"),
    )
    return_code = _converted(
        decision_id="dec-2",
        rule_family=UnifiedRuleFamily.RETURN_CODE,
        detector_id="V2_RETURN_CODE_PROPAGATION",
        source_v2_candidate_id="v2::rc::1",
        evidence_ids=("effect::1", "fact::1"),
    )

    candidates, warnings = _merge_candidates(
        [return_code, level88], _empty_v1_candidates(), source_package_hash=HASH
    )

    assert len(candidates) == 2
    assert warnings == []


def test_level88_return_code_pair_with_different_outcome_never_merges() -> None:
    """Mismo contexto estructural, outcome_code/effect distinto ->
    nunca se fusiona."""
    level88 = _converted(
        outcome_code="0005",
        rule_family=UnifiedRuleFamily.LEVEL_88_RETURN_CODE,
        detector_id="V2_LEVEL_88_RETURN_CODE",
        source_v2_candidate_id="v2::level88::1",
        evidence_ids=("effect::1", "fact::1"),
    )
    return_code = _converted(
        outcome_code="9999",
        rule_family=UnifiedRuleFamily.RETURN_CODE,
        detector_id="V2_RETURN_CODE_PROPAGATION",
        source_v2_candidate_id="v2::rc::1",
        evidence_ids=("effect::1", "fact::1"),
    )

    candidates, warnings = _merge_candidates(
        [return_code, level88], _empty_v1_candidates(), source_package_hash=HASH
    )

    assert len(candidates) == 2
    assert warnings == []


def test_other_family_pair_with_identical_evidence_never_merges() -> None:
    """Dos candidatos V2 con evidencia identica pero una pareja de
    familias que NO es exactamente (LEVEL_88_RETURN_CODE, RETURN_CODE)
    -- nunca se fusionan. Demuestra que no se creo un mecanismo de
    dedup generico cross-family."""
    return_code = _converted(
        rule_family=UnifiedRuleFamily.RETURN_CODE,
        detector_id="V2_RETURN_CODE_PROPAGATION",
        source_v2_candidate_id="v2::rc::1",
        evidence_ids=("effect::1", "fact::1"),
    )
    state_transition = _converted(
        rule_family=UnifiedRuleFamily.STATE_TRANSITION,
        detector_id="V2_STATE_CHANGE",
        source_v2_candidate_id="v2::st::1",
        evidence_ids=("effect::1", "fact::1"),
    )

    candidates, warnings = _merge_candidates(
        [return_code, state_transition], _empty_v1_candidates(), source_package_hash=HASH
    )

    assert len(candidates) == 2
    assert {c.rule_family for c in candidates} == {
        UnifiedRuleFamily.RETURN_CODE,
        UnifiedRuleFamily.STATE_TRANSITION,
    }
    assert warnings == []


def test_return_code_without_level88_sibling_remains_productive() -> None:
    """RETURN_CODE (via propagacion) sin ningun candidato hermano
    LEVEL_88_RETURN_CODE sobre el mismo hecho -- sigue siendo
    productivo, sin cambios. El fix nunca elimina la familia
    RETURN_CODE general."""
    return_code = _converted(
        rule_family=UnifiedRuleFamily.RETURN_CODE,
        detector_id="V2_RETURN_CODE_PROPAGATION",
        source_v2_candidate_id="v2::rc::1",
        evidence_ids=("effect::1", "fact::1"),
    )

    candidates, warnings = _merge_candidates(
        [return_code], _empty_v1_candidates(), source_package_hash=HASH
    )

    assert len(candidates) == 1
    assert candidates[0].rule_family == UnifiedRuleFamily.RETURN_CODE
    assert candidates[0].evidence_ids == ["effect::1", "fact::1"]
    assert warnings == []


# ---------------------------------------------------------------------------
# Fase 15B4-CANDIDATE-QUALITY-3B: V1 RETURN_CODE ghost superseded por
# refinamientos V2 V2_RETURN_CODE_PROPAGATION deterministicos (corrige la
# duplicacion demostrada en Fase 15B4-CANDIDATE-QUALITY-3: CORREGIDO 14->27,
# 13 pares V1-ghost/V2-real sobre la MISMA decision, nunca fusionados por
# functional_identity_key porque el V1 ghost tiene effect="").
# ---------------------------------------------------------------------------


def _rule_candidate(
    *,
    candidate_id: str,
    paragraph_id: str = "program::AR::op::PROG1::1::abc123::paragraph::A",
    decision_id: str | None = "dec-1",
    condition: str | None = "CONDICION",
    outcome_code: str | None = None,
    rule_family: UnifiedRuleFamily = UnifiedRuleFamily.RETURN_CODE,
    detector_id: str = "q0-return-code-decision",
    candidate_source: CandidateSource = CandidateSource.V1,
    evidence_ids: list[str] | None = None,
) -> RuleCandidate:
    return RuleCandidate(
        candidate_id=candidate_id,
        paragraph_id=paragraph_id,
        paragraph_name="A",
        decision_id=decision_id,
        detector_id=detector_id,
        detector_version="1.0",
        detector_score=1.0,
        condition=condition,
        outcome_code=outcome_code,
        rule_type=None,
        line_start=10,
        source_file=SRC,
        source_package_hash=HASH,
        candidate_source=candidate_source,
        rule_family=rule_family,
        evidence_ids=sorted(evidence_ids) if evidence_ids else [],
    )


def _v1_ghost(
    candidate_id: str = "candidate::q0-return-code-decision::1.0::dec-1",
) -> RuleCandidate:
    return _rule_candidate(candidate_id=candidate_id)


def _v2_refinement(candidate_id: str, *, outcome_code: str, evidence_id: str) -> RuleCandidate:
    return _rule_candidate(
        candidate_id=candidate_id,
        outcome_code=outcome_code,
        detector_id="V2_RETURN_CODE_PROPAGATION",
        candidate_source=CandidateSource.V2,
        evidence_ids=[evidence_id],
    )


def test_ghost_a_single_v1_ghost_with_single_v2_refinement_suppresses_ghost() -> None:
    ghost = _v1_ghost()
    refinement = _v2_refinement("candidate::enhanced::x1", outcome_code="0005", evidence_id="e1")

    result, warnings = suppress_superseded_v1_return_code_ghosts([ghost, refinement])

    assert [c.candidate_id for c in result] == [refinement.candidate_id]
    assert len(warnings) == 1
    assert ghost.candidate_id in warnings[0]
    assert refinement.candidate_id in warnings[0]


def test_ghost_b_single_v1_ghost_with_two_distinct_v2_outcomes_keeps_both_never_merges() -> None:
    ghost = _v1_ghost()
    r1 = _v2_refinement("candidate::enhanced::x1", outcome_code="0001", evidence_id="e1")
    r2 = _v2_refinement("candidate::enhanced::x2", outcome_code="9999", evidence_id="e2")

    result, warnings = suppress_superseded_v1_return_code_ghosts([ghost, r1, r2])

    assert sorted(c.candidate_id for c in result) == sorted([r1.candidate_id, r2.candidate_id])
    assert not any(c.candidate_id == ghost.candidate_id for c in result)
    assert len(warnings) == 1


def test_ghost_c_v1_with_concrete_effect_is_never_suppressed() -> None:
    v1_concrete = _v1_ghost()
    v1_concrete = v1_concrete.model_copy(update={"outcome_code": "0005"})
    refinement = _v2_refinement("candidate::enhanced::x1", outcome_code="0005", evidence_id="e1")

    result, warnings = suppress_superseded_v1_return_code_ghosts([v1_concrete, refinement])

    assert sorted(c.candidate_id for c in result) == sorted(
        [v1_concrete.candidate_id, refinement.candidate_id]
    )
    assert warnings == []


def test_ghost_d_v1_ghost_without_any_v2_refinement_remains() -> None:
    ghost = _v1_ghost()

    result, warnings = suppress_superseded_v1_return_code_ghosts([ghost])

    assert [c.candidate_id for c in result] == [ghost.candidate_id]
    assert warnings == []


def test_ghost_e_v1_ghost_with_v2_other_family_is_never_suppressed() -> None:
    ghost = _v1_ghost()
    other_family_v2 = _rule_candidate(
        candidate_id="candidate::enhanced::x1",
        outcome_code="0005",
        rule_family=UnifiedRuleFamily.LEVEL_88_RETURN_CODE,
        detector_id="V2_LEVEL_88_RETURN_CODE",
        candidate_source=CandidateSource.V2,
        evidence_ids=["e1"],
    )

    result, warnings = suppress_superseded_v1_return_code_ghosts([ghost, other_family_v2])

    assert sorted(c.candidate_id for c in result) == sorted(
        [ghost.candidate_id, other_family_v2.candidate_id]
    )
    assert warnings == []


def test_ghost_f_same_decision_different_paragraph_is_never_suppressed() -> None:
    ghost = _v1_ghost()
    refinement_other_paragraph = _v2_refinement(
        "candidate::enhanced::x1", outcome_code="0005", evidence_id="e1"
    ).model_copy(
        update={"paragraph_id": "program::AR::op::PROG1::1::abc123::paragraph::OTHER"}
    )

    result, warnings = suppress_superseded_v1_return_code_ghosts(
        [ghost, refinement_other_paragraph]
    )

    assert sorted(c.candidate_id for c in result) == sorted(
        [ghost.candidate_id, refinement_other_paragraph.candidate_id]
    )
    assert warnings == []


def test_ghost_g_v2_without_evidence_is_never_suppressed() -> None:
    ghost = _v1_ghost()
    v2_no_evidence = _rule_candidate(
        candidate_id="candidate::enhanced::x1",
        outcome_code="0005",
        detector_id="V2_RETURN_CODE_PROPAGATION",
        candidate_source=CandidateSource.V2,
        evidence_ids=[],
    )

    result, warnings = suppress_superseded_v1_return_code_ghosts([ghost, v2_no_evidence])

    assert sorted(c.candidate_id for c in result) == sorted(
        [ghost.candidate_id, v2_no_evidence.candidate_id]
    )
    assert warnings == []


def test_ghost_cross_program_same_paragraph_and_decision_text_never_suppresses() -> None:
    """Fase 15B4-CANDIDATE-QUALITY-3B-SAFETY-CHECK: aunque dos programas
    distintos usaran el MISMO nombre textual de paragraph/decision
    (p. ej. ambos "MAIN"), `identifiers.py::paragraph_id` embebe el
    `program_id` completo (que a su vez incluye `source_hash[:12]`)
    como prefijo literal, y ningun componente de `program_id`/
    `paragraph_name` puede contener el separador "::" (identificadores
    COBOL: solo alfanumerico y guion) -- dos programas reales NUNCA
    producen el mismo `paragraph_id`/`decision_id`. Se construyen aqui
    con `paragraph_id`/`decision_id` realistas de DOS programas
    distintos (mismo sufijo "paragraph::MAIN", prefijo `program_id`
    distinto) para demostrar que el predicado de la Seccion 2 nunca los
    trata como la misma decision."""
    ghost = _v1_ghost().model_copy(
        update={
            "paragraph_id": "program::AR::op::PROGRAM_A::1.0::aaaaaaaaaaaa::paragraph::MAIN",
            "decision_id": (
                "program::AR::op::PROGRAM_A::1.0::aaaaaaaaaaaa::paragraph::MAIN"
                "::decision::10::1"
            ),
        }
    )
    refinement_other_program = _v2_refinement(
        "candidate::enhanced::x1", outcome_code="0005", evidence_id="e1"
    ).model_copy(
        update={
            "paragraph_id": "program::AR::op::PROGRAM_B::1.0::bbbbbbbbbbbb::paragraph::MAIN",
            "decision_id": (
                "program::AR::op::PROGRAM_B::1.0::bbbbbbbbbbbb::paragraph::MAIN"
                "::decision::10::1"
            ),
        }
    )

    result, warnings = suppress_superseded_v1_return_code_ghosts(
        [ghost, refinement_other_program]
    )

    assert sorted(c.candidate_id for c in result) == sorted(
        [ghost.candidate_id, refinement_other_program.candidate_id]
    )
    assert warnings == []


# ---------------------------------------------------------------------------
# STATE_CHANGE_RULE nunca se promueve
# ---------------------------------------------------------------------------


def test_state_change_only_case_never_produces_a_candidate() -> None:
    """Caso C de `test_v2_detectors.py`: WS-AUX no es `return_code` ni
    tiene semantic_tag `status`/`status_flag` -- `V2_STATE_CHANGE` (Fase
    15B3-C1: ahora si se invoca) lo detecta pero se descarta sin
    relevancia funcional demostrada, nunca se promueve."""
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
    assert_only_state_change_discard_warnings(warnings, targets=["WS-AUX"])


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

    # Sin source_file en el nodo Paragraph, TODO candidato ancla a ese
    # paragraph se descarta -- incluye tanto el de RETURN_CODE_PROPAGATION
    # (WS-COD-RETORNO) como el de V2_STATE_CHANGE (WS-AUX, Fase 15B3-C1).
    assert new_candidates == []
    assert len(warnings) == 2
    assert all("line_start/source_file" in warning for warning in warnings)


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


# ---------------------------------------------------------------------------
# Fase 15B3-C1: STATE_TRANSITION (generalizacion de reglas de decision,
# estado y flags) -- casos obligatorios de la seccion 7/12 del cierre.
# ---------------------------------------------------------------------------


def _run_detection(
    program: CanonicalProgram, decisions: list[tuple[str, int, str]], *, tags: dict[str, str | None]
) -> tuple[list[RuleCandidate], list[str]]:
    graph = _build_graph_with_source_file(program=program, decisions=decisions, data_item_tags=tags)
    return detect_enhanced_candidates(
        canonical_programs=[program],
        semantic_graph=graph,
        v1_candidates=_empty_v1_candidates(),
        run_id=_RUN_ID,
        source_package_hash=HASH,
    )


# --- Caso A: IF simple -----------------------------------------------------


def test_if_simple_move_to_status_target_produces_state_transition() -> None:
    if_stmt = make_stmt(
        statement_id="P1::A::0::IF", kind=StatementKind.IF, line_start=10, expression="A = 'S'"
    )
    move_stmt = make_stmt(
        statement_id="P1::A::1::MOVE",
        target_data_items=["WS-ESTADO"],
        variables_written=["WS-ESTADO"],
        assigned_literal="R",
        parent_statement_id="P1::A::0::IF",
        branch_kind="THEN",
    )
    paragraph = CanonicalParagraph(
        name="A",
        source_text="A.",
        location_kind=LocationKind.UNKNOWN,
        statements=[if_stmt, move_stmt],
        variables_written=["WS-ESTADO"],
    )
    program = _program(
        program_name="PROG1", data_items=[_data_item("WS-ESTADO")], paragraphs=[paragraph]
    )

    new_candidates, warnings = _run_detection(
        program, [("A", 10, "A = 'S'")], tags={"WS-ESTADO": "status"}
    )

    assert warnings == []
    assert len(new_candidates) == 1
    candidate = new_candidates[0]
    assert candidate.rule_family == UnifiedRuleFamily.STATE_TRANSITION
    assert candidate.candidate_source == CandidateSource.V2
    assert candidate.condition == "A = 'S'"
    assert candidate.outcome_code == "R"
    assert candidate.paragraph_name == "A"
    assert candidate.evidence_ids != []
    assert candidate.decision_id == decision_node_id_for("PROG1", "A", 10, 1)


# --- Caso B: IF / ELSE -------------------------------------------------


def test_if_else_produces_two_state_transition_candidates_for_different_effects() -> None:
    if_stmt = make_stmt(
        statement_id="P1::A::0::IF", kind=StatementKind.IF, line_start=10, expression="A = 'S'"
    )
    then_move = make_stmt(
        statement_id="P1::A::1::MOVE",
        target_data_items=["WS-ESTADO"],
        variables_written=["WS-ESTADO"],
        assigned_literal="A",
        parent_statement_id="P1::A::0::IF",
        branch_kind="THEN",
    )
    else_move = make_stmt(
        statement_id="P1::A::2::MOVE",
        target_data_items=["WS-ESTADO"],
        variables_written=["WS-ESTADO"],
        assigned_literal="R",
        parent_statement_id="P1::A::0::IF",
        branch_kind="ELSE",
    )
    paragraph = CanonicalParagraph(
        name="A",
        source_text="A.",
        location_kind=LocationKind.UNKNOWN,
        statements=[if_stmt, then_move, else_move],
        variables_written=["WS-ESTADO"],
    )
    program = _program(
        program_name="PROG1", data_items=[_data_item("WS-ESTADO")], paragraphs=[paragraph]
    )

    new_candidates, warnings = _run_detection(
        program, [("A", 10, "A = 'S'")], tags={"WS-ESTADO": "status"}
    )

    assert warnings == []
    assert len(new_candidates) == 2
    assert {c.outcome_code for c in new_candidates} == {"A", "R"}
    assert all(c.rule_family == UnifiedRuleFamily.STATE_TRANSITION for c in new_candidates)
    assert all(c.condition == "A = 'S'" for c in new_candidates)
    assert len({c.candidate_id for c in new_candidates}) == 2


# --- Caso C: nested IF -------------------------------------------------


def test_nested_if_preserves_the_real_inner_condition_never_a_combined_one() -> None:
    outer_if = make_stmt(
        statement_id="P1::A::0::IF", kind=StatementKind.IF, line_start=10, expression="A = 'S'"
    )
    inner_if = make_stmt(
        statement_id="P1::A::1::IF",
        kind=StatementKind.IF,
        line_start=11,
        expression="B > 10",
        parent_statement_id="P1::A::0::IF",
        branch_kind="THEN",
    )
    inner_move = make_stmt(
        statement_id="P1::A::2::MOVE",
        target_data_items=["WS-ESTADO"],
        variables_written=["WS-ESTADO"],
        assigned_literal="M",
        parent_statement_id="P1::A::1::IF",
        branch_kind="THEN",
    )
    paragraph = CanonicalParagraph(
        name="A",
        source_text="A.",
        location_kind=LocationKind.UNKNOWN,
        statements=[outer_if, inner_if, inner_move],
        variables_written=["WS-ESTADO"],
    )
    program = _program(
        program_name="PROG1", data_items=[_data_item("WS-ESTADO")], paragraphs=[paragraph]
    )

    new_candidates, warnings = _run_detection(
        program,
        [("A", 10, "A = 'S'"), ("A", 11, "B > 10")],
        tags={"WS-ESTADO": "status"},
    )

    assert warnings == []
    assert len(new_candidates) == 1
    candidate = new_candidates[0]
    assert candidate.condition == "B > 10"
    assert candidate.outcome_code == "M"
    # Ordinal 2: segunda decision declarada para el paragraph "A" (linea 10
    # primero, linea 11 segundo) -- ver _build_graph_with_source_file.
    assert candidate.decision_id == decision_node_id_for("PROG1", "A", 11, 2)


# --- Caso D: EVALUATE / WHEN --------------------------------------------


def test_evaluate_when_produces_state_transition_per_branch() -> None:
    evaluate_stmt = make_stmt(
        statement_id="P1::A::0::EVALUATE",
        kind=StatementKind.EVALUATE,
        line_start=10,
        expression="WS-RIESGO",
    )
    when1_move = make_stmt(
        statement_id="P1::A::1::MOVE",
        target_data_items=["WS-ESTADO"],
        variables_written=["WS-ESTADO"],
        assigned_literal="A",
        parent_statement_id="P1::A::0::EVALUATE",
        branch_kind="WHEN",
    )
    when2_move = make_stmt(
        statement_id="P1::A::2::MOVE",
        target_data_items=["WS-ESTADO"],
        variables_written=["WS-ESTADO"],
        assigned_literal="M",
        parent_statement_id="P1::A::0::EVALUATE",
        branch_kind="WHEN",
    )
    paragraph = CanonicalParagraph(
        name="A",
        source_text="A.",
        location_kind=LocationKind.UNKNOWN,
        statements=[evaluate_stmt, when1_move, when2_move],
        variables_written=["WS-ESTADO"],
    )
    program = _program(
        program_name="PROG1", data_items=[_data_item("WS-ESTADO")], paragraphs=[paragraph]
    )

    new_candidates, warnings = _run_detection(
        program, [("A", 10, "WS-RIESGO")], tags={"WS-ESTADO": "status"}
    )

    assert warnings == []
    assert len(new_candidates) == 2
    assert {c.outcome_code for c in new_candidates} == {"A", "M"}
    assert all(c.condition == "WS-RIESGO" for c in new_candidates)


# --- Caso E: WHEN OTHER --------------------------------------------------


def test_when_other_branch_produces_a_distinguishable_state_transition() -> None:
    evaluate_stmt = make_stmt(
        statement_id="P1::A::0::EVALUATE",
        kind=StatementKind.EVALUATE,
        line_start=10,
        expression="WS-RIESGO",
    )
    other_move = make_stmt(
        statement_id="P1::A::1::MOVE",
        target_data_items=["WS-ESTADO"],
        variables_written=["WS-ESTADO"],
        assigned_literal="D",
        parent_statement_id="P1::A::0::EVALUATE",
        branch_kind="WHEN_OTHER",
    )
    paragraph = CanonicalParagraph(
        name="A",
        source_text="A.",
        location_kind=LocationKind.UNKNOWN,
        statements=[evaluate_stmt, other_move],
        variables_written=["WS-ESTADO"],
    )
    program = _program(
        program_name="PROG1", data_items=[_data_item("WS-ESTADO")], paragraphs=[paragraph]
    )

    new_candidates, warnings = _run_detection(
        program, [("A", 10, "WS-RIESGO")], tags={"WS-ESTADO": "status"}
    )

    assert warnings == []
    assert len(new_candidates) == 1
    assert new_candidates[0].outcome_code == "D"


# --- Caso F: condicion compuesta -----------------------------------------


def test_compound_condition_is_preserved_verbatim_never_reparsed() -> None:
    if_stmt = make_stmt(
        statement_id="P1::A::0::IF",
        kind=StatementKind.IF,
        line_start=10,
        expression="A = 'S' AND B > 100",
    )
    move_stmt = make_stmt(
        statement_id="P1::A::1::MOVE",
        target_data_items=["WS-ESTADO"],
        variables_written=["WS-ESTADO"],
        assigned_literal="R",
        parent_statement_id="P1::A::0::IF",
        branch_kind="THEN",
    )
    paragraph = CanonicalParagraph(
        name="A",
        source_text="A.",
        location_kind=LocationKind.UNKNOWN,
        statements=[if_stmt, move_stmt],
        variables_written=["WS-ESTADO"],
    )
    program = _program(
        program_name="PROG1", data_items=[_data_item("WS-ESTADO")], paragraphs=[paragraph]
    )

    new_candidates, warnings = _run_detection(
        program, [("A", 10, "A = 'S' AND B > 100")], tags={"WS-ESTADO": "status"}
    )

    assert warnings == []
    assert len(new_candidates) == 1
    assert new_candidates[0].condition == "A = 'S' AND B > 100"


# --- Caso G: SET condition-name/88 TO TRUE (flag funcional) -------------


def test_set_level_88_flag_with_status_flag_tag_produces_state_transition() -> None:
    condition = CanonicalConditionName(
        name="REQUIERE-AUTORIZACION",
        qualified_name="WS-ESTADO-AUTORIZACION.REQUIERE-AUTORIZACION",
        parent_name="WS-ESTADO-AUTORIZACION",
        parent_qualified_name="WS-ESTADO-AUTORIZACION",
        values=[CanonicalConditionValue(value="S", location_kind=LocationKind.UNKNOWN)],
        location_kind=LocationKind.UNKNOWN,
    )
    if_stmt = make_stmt(
        statement_id="P1::A::0::IF",
        kind=StatementKind.IF,
        line_start=10,
        expression="WS-MONTO > WS-LIMITE",
    )
    set_stmt = make_stmt(
        statement_id="P1::A::1::SET",
        kind=StatementKind.SET,
        target_data_items=["REQUIERE-AUTORIZACION"],
        variables_written=["REQUIERE-AUTORIZACION"],
        condition_name_target="REQUIERE-AUTORIZACION",
        condition_set_value=True,
        parent_statement_id="P1::A::0::IF",
        branch_kind="THEN",
    )
    paragraph = CanonicalParagraph(
        name="A",
        source_text="A.",
        location_kind=LocationKind.UNKNOWN,
        statements=[if_stmt, set_stmt],
        variables_written=["REQUIERE-AUTORIZACION"],
    )
    program = _program(
        program_name="PROG1",
        data_items=[_data_item("WS-ESTADO-AUTORIZACION")],
        paragraphs=[paragraph],
        condition_names=[condition],
    )

    new_candidates, warnings = _run_detection(
        program,
        [("A", 10, "WS-MONTO > WS-LIMITE")],
        tags={"WS-ESTADO-AUTORIZACION": "status_flag"},
    )

    assert warnings == []
    assert len(new_candidates) == 1
    candidate = new_candidates[0]
    assert candidate.rule_family == UnifiedRuleFamily.STATE_TRANSITION
    assert candidate.outcome_code == "S"
    assert candidate.condition == "WS-MONTO > WS-LIMITE"


# --- Caso H: propagacion simple existente --------------------------------


def test_propagation_via_intermediate_variable_produces_state_transition() -> None:
    if_stmt = make_stmt(
        statement_id="P1::A::0::IF", kind=StatementKind.IF, line_start=10, expression="COND"
    )
    aux_move = make_stmt(
        statement_id="P1::A::1::MOVE",
        target_data_items=["WS-AUX-ESTADO"],
        variables_written=["WS-AUX-ESTADO"],
        assigned_literal="R",
        parent_statement_id="P1::A::0::IF",
        branch_kind="THEN",
    )
    final_move = make_stmt(
        statement_id="P1::A::2::MOVE",
        target_data_items=["WS-ESTADO"],
        variables_written=["WS-ESTADO"],
        variables_read=["WS-AUX-ESTADO"],
        parent_statement_id="P1::A::0::IF",
        branch_kind="THEN",
    )
    paragraph = CanonicalParagraph(
        name="A",
        source_text="A.",
        location_kind=LocationKind.UNKNOWN,
        statements=[if_stmt, aux_move, final_move],
        variables_read=["WS-AUX-ESTADO"],
        variables_written=["WS-AUX-ESTADO", "WS-ESTADO"],
    )
    program = _program(
        program_name="PROG1",
        data_items=[_data_item("WS-AUX-ESTADO"), _data_item("WS-ESTADO")],
        paragraphs=[paragraph],
    )

    new_candidates, warnings = _run_detection(
        program,
        [("A", 10, "COND")],
        tags={"WS-AUX-ESTADO": None, "WS-ESTADO": "status"},
    )

    # WS-AUX-ESTADO (intermediario, sin semantic_tag) tambien es visto por
    # V2_STATE_CHANGE -- descartado, nunca promovido.
    assert_only_state_change_discard_warnings(warnings, targets=["WS-AUX-ESTADO"])
    assert len(new_candidates) == 1
    candidate = new_candidates[0]
    assert candidate.rule_family == UnifiedRuleFamily.STATE_TRANSITION
    assert candidate.outcome_code == "R"


def test_propagation_barrier_never_invents_a_value() -> None:
    """Si la propagacion no puede demostrar el literal (WS-ENTRADA nunca
    recibe un valor conocido), no debe producirse NINGUN candidato --
    nunca se inventa un valor ni se emite un warning fabricado."""
    if_stmt = make_stmt(
        statement_id="P1::A::0::IF", kind=StatementKind.IF, line_start=10, expression="COND"
    )
    move_from_unresolved = make_stmt(
        statement_id="P1::A::1::MOVE",
        target_data_items=["WS-ESTADO"],
        variables_written=["WS-ESTADO"],
        variables_read=["WS-ENTRADA"],
        parent_statement_id="P1::A::0::IF",
        branch_kind="THEN",
    )
    paragraph = CanonicalParagraph(
        name="A",
        source_text="A.",
        location_kind=LocationKind.UNKNOWN,
        statements=[if_stmt, move_from_unresolved],
        variables_read=["WS-ENTRADA"],
        variables_written=["WS-ESTADO"],
    )
    program = _program(
        program_name="PROG1",
        data_items=[_data_item("WS-ENTRADA"), _data_item("WS-ESTADO")],
        paragraphs=[paragraph],
    )

    new_candidates, warnings = _run_detection(
        program, [("A", 10, "COND")], tags={"WS-ESTADO": "status"}
    )

    assert new_candidates == []
    assert warnings == []


# --- Caso multiples efectos (seccion 8 del cierre) -----------------------


def test_single_decision_with_two_functional_targets_produces_two_rules() -> None:
    if_stmt = make_stmt(
        statement_id="P1::A::0::IF",
        kind=StatementKind.IF,
        line_start=10,
        expression="WS-SALDO < WS-MONTO",
    )
    move1 = make_stmt(
        statement_id="P1::A::1::MOVE",
        target_data_items=["WS-ESTADO-OPERACION"],
        variables_written=["WS-ESTADO-OPERACION"],
        assigned_literal="R",
        parent_statement_id="P1::A::0::IF",
        branch_kind="THEN",
    )
    move2 = make_stmt(
        statement_id="P1::A::2::MOVE",
        target_data_items=["WS-ESTADO-BLOQUEO"],
        variables_written=["WS-ESTADO-BLOQUEO"],
        assigned_literal="S",
        parent_statement_id="P1::A::0::IF",
        branch_kind="THEN",
    )
    paragraph = CanonicalParagraph(
        name="A",
        source_text="A.",
        location_kind=LocationKind.UNKNOWN,
        statements=[if_stmt, move1, move2],
        variables_written=["WS-ESTADO-OPERACION", "WS-ESTADO-BLOQUEO"],
    )
    program = _program(
        program_name="PROG1",
        data_items=[_data_item("WS-ESTADO-OPERACION"), _data_item("WS-ESTADO-BLOQUEO")],
        paragraphs=[paragraph],
    )

    new_candidates, warnings = _run_detection(
        program,
        [("A", 10, "WS-SALDO < WS-MONTO")],
        tags={"WS-ESTADO-OPERACION": "status", "WS-ESTADO-BLOQUEO": "status"},
    )

    assert warnings == []
    assert len(new_candidates) == 2
    assert {(c.paragraph_name, c.outcome_code) for c in new_candidates} == {
        ("A", "R"),
        ("A", "S"),
    }
    assert all(c.rule_family == UnifiedRuleFamily.STATE_TRANSITION for c in new_candidates)
    decision_id = decision_node_id_for("PROG1", "A", 10, 1)
    assert all(c.decision_id == decision_id for c in new_candidates)
    # No deduplicar solo por decision_id: dos candidate_id distintos.
    assert len({c.candidate_id for c in new_candidates}) == 2


# --- Casos negativos: sin relevancia funcional demostrada ----------------


def _ordinary_target_program(
    target_name: str,
) -> tuple[CanonicalProgram, list[tuple[str, int, str]]]:
    if_stmt = make_stmt(
        statement_id="P1::A::0::IF", kind=StatementKind.IF, line_start=10, expression="COND"
    )
    move_stmt = make_stmt(
        statement_id="P1::A::1::MOVE",
        target_data_items=[target_name],
        variables_written=[target_name],
        assigned_literal="0",
        parent_statement_id="P1::A::0::IF",
        branch_kind="THEN",
    )
    paragraph = CanonicalParagraph(
        name="A",
        source_text="A.",
        location_kind=LocationKind.UNKNOWN,
        statements=[if_stmt, move_stmt],
        variables_written=[target_name],
    )
    program = _program(
        program_name="PROG1", data_items=[_data_item(target_name)], paragraphs=[paragraph]
    )
    return program, [("A", 10, "COND")]


def test_ordinary_target_without_semantic_tag_never_becomes_state_transition() -> None:
    """Fixture obligatoria #7: target ordinario (WS-SALDO, sin
    semantic_tag alguno) -- nunca STATE_TRANSITION, aunque la escritura
    sea deterministica."""
    program, decisions = _ordinary_target_program("WS-SALDO")

    new_candidates, warnings = _run_detection(program, decisions, tags={"WS-SALDO": None})

    assert new_candidates == []
    assert_only_state_change_discard_warnings(warnings, targets=["WS-SALDO"])


def test_ambiguous_named_target_never_becomes_state_transition() -> None:
    """Fixture obligatoria #8 (corregida, correccion pre-commit 15B3-C1):
    tras el fix de regex en config/semantic-tags.yml, `status-name` y
    `status-flag-name` son mutuamente NO ambiguas -- `IND-ESTADO` e
    `INDICADOR-ESTADO` son ahora patrones POSITIVOS explicitos de
    `status_flag` (ver tests/pipeline/test_semantic_tagger.py), no un
    caso de ambiguedad. `WS-INDICADOR-PROCESO` contiene el prefijo
    INDICADOR pero no el sufijo `-ESTADO` exigido por `status-flag-name`,
    ni ESTADO/STATUS/STATE exigido por `status-name` -- no matchea
    ninguna regla, el SemanticTagger real no le asigna tag, y por lo
    tanto nunca se promueve a STATE_TRANSITION."""
    program, decisions = _ordinary_target_program("WS-INDICADOR-PROCESO")

    new_candidates, warnings = _run_detection(
        program, decisions, tags={"WS-INDICADOR-PROCESO": None}
    )

    assert new_candidates == []
    assert_only_state_change_discard_warnings(warnings, targets=["WS-INDICADOR-PROCESO"])


# ---------------------------------------------------------------------------
# Fase 15B3-C2-B1: CALCULATION (COMPUTE/ADD/SUBTRACT/MULTIPLY/DIVIDE
# condicionados) -- productizacion hasta RuleCandidate. Fase 15B3-C2-B2:
# extiende el mismo camino al caso incondicional (sin Decision
# envolvente), tambien productivo desde esta fase.
# ---------------------------------------------------------------------------


def test_conditioned_compute_produces_a_calculation_rule_candidate() -> None:
    if_stmt = make_stmt(
        statement_id="P1::A::0::IF", kind=StatementKind.IF, line_start=10,
        expression="WS-TIPO = 'I'",
    )
    compute_stmt = make_stmt(
        statement_id="P1::A::1::COMPUTE",
        kind=StatementKind.COMPUTE,
        target_data_items=["WS-COMISION"],
        variables_written=["WS-COMISION"],
        variables_read=["WS-MONTO", "WS-TASA"],
        expression="WS-MONTO * WS-TASA",
        parent_statement_id="P1::A::0::IF",
        branch_kind="THEN",
    )
    paragraph = CanonicalParagraph(
        name="A", source_text="A.", location_kind=LocationKind.UNKNOWN,
        statements=[if_stmt, compute_stmt],
        variables_read=["WS-MONTO", "WS-TASA"], variables_written=["WS-COMISION"],
    )
    program = _program(
        program_name="PROG1",
        data_items=[_data_item("WS-COMISION"), _data_item("WS-MONTO"), _data_item("WS-TASA")],
        paragraphs=[paragraph],
    )

    new_candidates, warnings = _run_detection(
        program, [("A", 10, "WS-TIPO = 'I'")], tags={}
    )

    assert warnings == []
    assert len(new_candidates) == 1
    candidate = new_candidates[0]
    assert candidate.rule_family == UnifiedRuleFamily.CALCULATION
    assert candidate.candidate_source == CandidateSource.V2
    assert candidate.condition == "WS-TIPO = 'I'"
    assert candidate.outcome_code is None, "CALCULATION nunca afirma un literal como outcome_code"
    assert candidate.decision_id == decision_node_id_for("PROG1", "A", 10, 1)
    assert candidate.evidence_ids != []


def test_conditioned_arithmetic_verb_also_produces_a_calculation_rule_candidate() -> None:
    """No solo COMPUTE: MULTIPLY (ejemplo del enunciado, seccion 22)
    tambien produce un CALCULATION productivo end-to-end."""
    if_stmt = make_stmt(
        statement_id="P1::A::0::IF", kind=StatementKind.IF, line_start=10,
        expression="WS-TIPO = 'I'",
    )
    multiply_stmt = make_stmt(
        statement_id="P1::A::1::MULTIPLY",
        kind=StatementKind.MULTIPLY,
        target_data_items=["WS-COMISION"],
        variables_written=["WS-COMISION"],
        variables_read=["WS-MONTO", "WS-TASA"],
        expression=None,
        parent_statement_id="P1::A::0::IF",
        branch_kind="THEN",
    )
    paragraph = CanonicalParagraph(
        name="A", source_text="A.", location_kind=LocationKind.UNKNOWN,
        statements=[if_stmt, multiply_stmt],
        variables_read=["WS-MONTO", "WS-TASA"], variables_written=["WS-COMISION"],
    )
    program = _program(
        program_name="PROG1",
        data_items=[_data_item("WS-COMISION"), _data_item("WS-MONTO"), _data_item("WS-TASA")],
        paragraphs=[paragraph],
    )

    new_candidates, warnings = _run_detection(
        program, [("A", 10, "WS-TIPO = 'I'")], tags={}
    )

    assert warnings == []
    assert len(new_candidates) == 1
    assert new_candidates[0].rule_family == UnifiedRuleFamily.CALCULATION


def test_unconditional_calculation_produces_a_calculation_rule_candidate_with_no_decision() -> (
    None
):
    """Fase 15B3-C2-B2: sin Decision envolvente, el calculo SI se
    productiviza -- un unico RuleCandidate(rule_family=CALCULATION,
    decision_id=None, condition=None), sin ningun warning de descarte
    (la senal "no productivizado" de 15B3-C2-B1 queda obsoleta: el
    candidato ahora si llega a 06-candidates.json)."""
    compute_stmt = make_stmt(
        statement_id="P1::A::1::COMPUTE",
        kind=StatementKind.COMPUTE,
        target_data_items=["WS-COMISION"],
        variables_written=["WS-COMISION"],
        variables_read=["WS-MONTO", "WS-TASA"],
        expression="WS-MONTO * WS-TASA",
    )
    paragraph = CanonicalParagraph(
        name="A", source_text="A.", location_kind=LocationKind.UNKNOWN,
        statements=[compute_stmt],
        variables_read=["WS-MONTO", "WS-TASA"], variables_written=["WS-COMISION"],
    )
    program = _program(
        program_name="PROG1",
        data_items=[_data_item("WS-COMISION"), _data_item("WS-MONTO"), _data_item("WS-TASA")],
        paragraphs=[paragraph],
    )

    new_candidates, warnings = _run_detection(program, [], tags={})

    assert warnings == []
    assert len(new_candidates) == 1
    candidate = new_candidates[0]
    assert candidate.rule_family == UnifiedRuleFamily.CALCULATION
    assert candidate.candidate_source == CandidateSource.V2
    assert candidate.decision_id is None
    assert candidate.condition is None
    assert candidate.outcome_code is None
    assert candidate.evidence_ids == ["effect::PROG1::A::P1::A::1::COMPUTE::COMPUTE_VALUE::0"]


def test_unconditional_calculation_cross_program_never_collides() -> None:
    """Correccion pre-commit 15B3-C2-B2, seccion 1 (test obligatorio):
    DOS programas distintos (PROGRAM-A/PROGRAM-B) con paragraph_id local
    ("A"), statement_id local ("P1::A::1::COMPUTE"), target y formula
    TEXTUALMENTE identicos -- nunca deben colisionar en un unico
    candidato ni compartir candidate_id. `program_id` (parametro
    explicito de `functional_identity_key_for_unconditional_calculation`,
    nunca una inferencia implicita) es el campo que lo garantiza."""

    def _make_program_and_graph(program_name: str) -> tuple[CanonicalProgram, SemanticGraph]:
        compute_stmt = make_stmt(
            statement_id="P1::A::1::COMPUTE",
            kind=StatementKind.COMPUTE,
            target_data_items=["WS-COMISION"],
            variables_written=["WS-COMISION"],
            variables_read=["WS-MONTO", "WS-TASA"],
            expression="WS-MONTO * WS-TASA",
        )
        paragraph = CanonicalParagraph(
            name="A", source_text="A.", location_kind=LocationKind.UNKNOWN,
            statements=[compute_stmt],
            variables_read=["WS-MONTO", "WS-TASA"], variables_written=["WS-COMISION"],
        )
        program = _program(
            program_name=program_name,
            data_items=[_data_item("WS-COMISION"), _data_item("WS-MONTO"), _data_item("WS-TASA")],
            paragraphs=[paragraph],
        )
        graph = _build_graph_with_source_file(program=program, decisions=[], data_item_tags={})
        return program, graph

    program_a, graph_a = _make_program_and_graph("PROGRAM-A")
    program_b, graph_b = _make_program_and_graph("PROGRAM-B")
    merged_graph = SemanticGraph(
        source_package_hash=HASH,
        nodes=sorted([*graph_a.nodes, *graph_b.nodes], key=lambda n: n.id),
        relationships=sorted(
            [*graph_a.relationships, *graph_b.relationships],
            key=lambda r: (r.type.value, r.from_id, r.to_id),
        ),
    )

    new_candidates, warnings = detect_enhanced_candidates(
        canonical_programs=[program_a, program_b],
        semantic_graph=merged_graph,
        v1_candidates=_empty_v1_candidates(),
        run_id=_RUN_ID,
        source_package_hash=HASH,
    )

    assert warnings == []
    assert len(new_candidates) == 2, new_candidates
    candidate_ids = {candidate.candidate_id for candidate in new_candidates}
    assert len(candidate_ids) == 2, (
        "dos programas distintos con la misma forma local (paragraph/statement/target/"
        "formula) nunca deben colisionar en un unico candidato"
    )
    for candidate in new_candidates:
        assert candidate.rule_family == UnifiedRuleFamily.CALCULATION
        assert candidate.decision_id is None
        assert candidate.condition is None


def test_unconditional_calculation_identity_independent_of_evidence_ids_order() -> None:
    """Correccion pre-commit 15B3-C2-B2, seccion 2: `source_statement_id`
    se obtiene DIRECTAMENTE de `V2ShadowCandidate.anchor_statement_id`
    (`CanonicalStatement.statement_id` real, ver `v2_detectors.
    detect_calculation`) -- nunca parseado de `evidence_ids`/
    `effect_id`. Prueba la consecuencia directa: la identidad
    (`_ConvertedCandidate.key`) de un CALCULATION incondicional NUNCA
    depende del contenido u orden de `semantic_effect_ids`/
    `evidence_ids` -- construye dos V2ShadowCandidate identicos salvo
    por `semantic_effect_ids` y verifica que ambos producen el MISMO
    `key` (mientras que `evidence_ids` en el `_ConvertedCandidate`
    resultante SI difiere, confirmando que evidence y key son
    independientes, no que el test este comparando dos copias
    identicas)."""
    from altamira_extractor.pipeline.enhanced_candidate_integration import (
        _convert_unconditional_calculation,
    )
    from altamira_extractor.pipeline.semantic_effects_analyzer import analyze_semantic_effects
    from altamira_extractor.pipeline.semantic_propagation_analyzer import (
        analyze_semantic_propagation,
    )
    from altamira_extractor.pipeline.v2_detector_context import build_v2_detector_context
    from altamira_extractor.pipeline.v2_detectors import detect_calculation

    compute_stmt = make_stmt(
        statement_id="P1::A::1::COMPUTE",
        kind=StatementKind.COMPUTE,
        target_data_items=["WS-COMISION"],
        variables_written=["WS-COMISION"],
        variables_read=["WS-MONTO", "WS-TASA"],
        expression="WS-MONTO * WS-TASA",
    )
    paragraph = CanonicalParagraph(
        name="A", source_text="A.", location_kind=LocationKind.UNKNOWN,
        statements=[compute_stmt],
        variables_read=["WS-MONTO", "WS-TASA"], variables_written=["WS-COMISION"],
    )
    program = _program(
        program_name="PROG1",
        data_items=[_data_item("WS-COMISION"), _data_item("WS-MONTO"), _data_item("WS-TASA")],
        paragraphs=[paragraph],
    )
    graph = _build_graph_with_source_file(program=program, decisions=[], data_item_tags={})
    effects = analyze_semantic_effects(
        canonical_programs=[program], run_id=_RUN_ID, source_package_hash=HASH,
        source_artifact_hashes={"artifacts/02-canonical": HASH},
    )
    propagation = analyze_semantic_propagation(
        canonical_programs=[program], semantic_effects=effects, run_id=_RUN_ID,
        source_package_hash=HASH, source_artifact_hashes={"artifacts/02-canonical": HASH},
    )
    ctx = build_v2_detector_context(
        canonical_programs=[program], semantic_graph=graph, v1_candidates=_empty_v1_candidates(),
        semantic_effects=effects, semantic_propagation=propagation,
    )

    candidates = detect_calculation(ctx)
    assert len(candidates) == 1
    base_candidate = candidates[0]
    assert base_candidate.decision_id is None

    # Copia deliberadamente con evidencia DISTINTA (nunca un reordenamiento
    # trivial del mismo contenido: un id sintetico completamente ajeno) --
    # si la identidad dependiera de evidence_ids, esto produciria un `key`
    # distinto.
    variant_candidate = base_candidate.model_copy(
        update={"semantic_effect_ids": ["effect::zzz-unrelated-fake-id"]}
    )

    base_converted, base_reason = _convert_unconditional_calculation(base_candidate, ctx)
    variant_converted, variant_reason = _convert_unconditional_calculation(variant_candidate, ctx)

    assert base_reason is None
    assert variant_reason is None
    assert base_converted is not None
    assert variant_converted is not None
    assert base_converted.key == variant_converted.key, (
        "la identidad de un CALCULATION incondicional nunca debe depender de "
        "semantic_effect_ids/evidence_ids"
    )
    assert base_converted.evidence_ids != variant_converted.evidence_ids


def test_unconditional_calculation_two_targets_produce_two_distinct_candidates() -> None:
    """Analogo incondicional de
    `test_single_decision_two_calculation_targets_produce_two_distinct_candidates_never_merged`:
    `functional_identity_key_for_unconditional_calculation` incluye el
    target explicitamente, asi que un COMPUTE con dos destinos nunca
    colisiona en un unico candidato."""
    compute_stmt = make_stmt(
        statement_id="P1::A::1::COMPUTE",
        kind=StatementKind.COMPUTE,
        target_data_items=["WS-A-TARGET", "WS-B-TARGET"],
        variables_written=["WS-A-TARGET", "WS-B-TARGET"],
        variables_read=["WS-X", "WS-Y"],
        expression="WS-X + WS-Y",
    )
    paragraph = CanonicalParagraph(
        name="A", source_text="A.", location_kind=LocationKind.UNKNOWN,
        statements=[compute_stmt],
        variables_read=["WS-X", "WS-Y"], variables_written=["WS-A-TARGET", "WS-B-TARGET"],
    )
    program = _program(
        program_name="PROG1",
        data_items=[
            _data_item("WS-A-TARGET"), _data_item("WS-B-TARGET"),
            _data_item("WS-X"), _data_item("WS-Y"),
        ],
        paragraphs=[paragraph],
    )

    new_candidates, warnings = _run_detection(program, [], tags={})

    assert warnings == []
    assert len(new_candidates) == 2
    candidate_ids = {candidate.candidate_id for candidate in new_candidates}
    assert len(candidate_ids) == 2
    for candidate in new_candidates:
        assert candidate.rule_family == UnifiedRuleFamily.CALCULATION
        assert candidate.decision_id is None
        assert candidate.condition is None


def test_unconditional_calculation_identity_key_properties() -> None:
    """Propiedades (A)-(E) exigidas por la correccion pre-commit
    15B3-C2-B2 (seccion 1) para
    `functional_identity_key_for_unconditional_calculation`."""
    from altamira_extractor.pipeline.enhanced_candidate_integration import (
        functional_identity_key_for_unconditional_calculation as key_fn,
    )

    # (A) mismo program+statement+target+formula -> mismo id.
    key_a1 = key_fn(
        program_id="program::AR::op::PROG1::1::abc123",
        paragraph_id="program::AR::op::PROG1::1::abc123::paragraph::A",
        source_statement_id="P::A::1::COMPUTE",
        target="WS-COMISION", formula="WS-MONTO * WS-TASA",
    )
    key_a2 = key_fn(
        program_id="program::AR::op::PROG1::1::abc123",
        paragraph_id="program::AR::op::PROG1::1::abc123::paragraph::A",
        source_statement_id="P::A::1::COMPUTE",
        target="WS-COMISION", formula="WS-MONTO * WS-TASA",
    )
    assert key_a1 == key_a2

    # (B) statements distintos, misma formula -> ids distintos.
    key_b = key_fn(
        program_id="program::AR::op::PROG1::1::abc123",
        paragraph_id="program::AR::op::PROG1::1::abc123::paragraph::A",
        source_statement_id="P::A::2::COMPUTE",
        target="WS-COMISION", formula="WS-MONTO * WS-TASA",
    )
    assert key_a1 != key_b

    # (C) multi-target -> id distinto por target.
    key_c = key_fn(
        program_id="program::AR::op::PROG1::1::abc123",
        paragraph_id="program::AR::op::PROG1::1::abc123::paragraph::A",
        source_statement_id="P::A::1::COMPUTE",
        target="WS-OTRO-TARGET", formula="WS-MONTO * WS-TASA",
    )
    assert key_a1 != key_c

    # (D) dos corridas identicas -> mismo id (ya cubierto por (A), reafirmado
    # explicitamente aqui como propiedad independiente del enunciado).
    assert key_fn(
        program_id="program::AR::op::PROG1::1::abc123",
        paragraph_id="program::AR::op::PROG1::1::abc123::paragraph::A",
        source_statement_id="P::A::1::COMPUTE",
        target="WS-COMISION", formula="WS-MONTO * WS-TASA",
    ) == key_a1

    # (E) correccion pre-commit 15B3-C2-B2, seccion 1: dos PROGRAMAS
    # distintos con paragraph_id/source_statement_id/target/formula
    # LOCALMENTE identicos (mismo texto, ignorando el prefijo de
    # programa) -> ids distintos. program_id es el campo que lo
    # garantiza EXPLICITAMENTE, nunca una inferencia sobre el formato
    # interno de paragraph_id.
    key_program_a = key_fn(
        program_id="program::AR::op::PROGRAM-A::1::aaa111",
        paragraph_id="paragraph::P-CALC",
        source_statement_id="stmt::same-local-shape",
        target="WS-TOTAL", formula="WS-X + WS-Y",
    )
    key_program_b = key_fn(
        program_id="program::AR::op::PROGRAM-B::1::bbb222",
        paragraph_id="paragraph::P-CALC",
        source_statement_id="stmt::same-local-shape",
        target="WS-TOTAL", formula="WS-X + WS-Y",
    )
    assert key_program_a != key_program_b, (
        "dos programas distintos con la misma forma local (paragraph_id/"
        "source_statement_id/target/formula) nunca deben colisionar"
    )


def test_single_decision_two_calculation_targets_produce_two_distinct_candidates_never_merged() -> (
    None
):
    """Seccion 16 (obligatorio): `COMPUTE A B = X + Y` bajo la MISMA
    Decision -- dos targets reales deben producir dos `RuleCandidate`
    con `candidate_id` DISTINTO (el target esta incluido explicitamente
    en el `effect` pasado a `functional_identity_key`, nunca colisiona
    por `resolved_literal=None` compartido)."""
    if_stmt = make_stmt(
        statement_id="P1::A::0::IF", kind=StatementKind.IF, line_start=10,
        expression="WS-TIPO = 'I'",
    )
    compute_stmt = make_stmt(
        statement_id="P1::A::1::COMPUTE",
        kind=StatementKind.COMPUTE,
        target_data_items=["WS-A-TARGET", "WS-B-TARGET"],
        variables_written=["WS-A-TARGET", "WS-B-TARGET"],
        variables_read=["WS-X", "WS-Y"],
        expression="WS-X + WS-Y",
        parent_statement_id="P1::A::0::IF",
        branch_kind="THEN",
    )
    paragraph = CanonicalParagraph(
        name="A", source_text="A.", location_kind=LocationKind.UNKNOWN,
        statements=[if_stmt, compute_stmt],
        variables_read=["WS-X", "WS-Y"], variables_written=["WS-A-TARGET", "WS-B-TARGET"],
    )
    program = _program(
        program_name="PROG1",
        data_items=[
            _data_item("WS-A-TARGET"), _data_item("WS-B-TARGET"),
            _data_item("WS-X"), _data_item("WS-Y"),
        ],
        paragraphs=[paragraph],
    )

    new_candidates, warnings = _run_detection(
        program, [("A", 10, "WS-TIPO = 'I'")], tags={}
    )

    assert warnings == []
    assert len(new_candidates) == 2
    candidate_ids = {candidate.candidate_id for candidate in new_candidates}
    assert len(candidate_ids) == 2, "targets distintos nunca deben fusionarse en un solo candidato"
    for candidate in new_candidates:
        assert candidate.rule_family == UnifiedRuleFamily.CALCULATION


def test_same_decision_same_target_same_formula_deduplicates_to_one_candidate() -> None:
    """Complemento de la seccion 16: MISMA Decision + MISMO target +
    MISMA formula debe deduplicarse a un unico `RuleCandidate` (no
    generar dos filas identicas por casualidad de construccion)."""
    key_a = functional_identity_key(
        paragraph_id="P::paragraph::A",
        decision_id="DEC1",
        condition="WS-TIPO = 'I'",
        effect="target=WS-COMISION\x1fformula=WS-MONTO * WS-TASA",
        rule_family=UnifiedRuleFamily.CALCULATION,
    )
    key_b = functional_identity_key(
        paragraph_id="P::paragraph::A",
        decision_id="DEC1",
        condition="WS-TIPO = 'I'",
        effect="target=WS-COMISION\x1fformula=WS-MONTO * WS-TASA",
        rule_family=UnifiedRuleFamily.CALCULATION,
    )
    key_different_target = functional_identity_key(
        paragraph_id="P::paragraph::A",
        decision_id="DEC1",
        condition="WS-TIPO = 'I'",
        effect="target=WS-OTRO\x1fformula=WS-MONTO * WS-TASA",
        rule_family=UnifiedRuleFamily.CALCULATION,
    )
    assert key_a == key_b, "misma Decision + mismo target + misma formula debe deduplicarse"
    assert key_a != key_different_target, "target distinto nunca debe colisionar"


def test_state_transition_semantic_tags_match_qualification_adapter() -> None:
    """Fase 15B3-C8-FIX-1 (correccion de layering): este modulo
    (pipeline PRODUCTIVO) y `candidate_source_adapters.py` (tooling de
    QUALIFICATION/Fase 9) mantienen DOS copias independientes de
    `_STATE_TRANSITION_SEMANTIC_TAGS` -- deliberadamente, para que el
    productivo nunca dependa de qualification -- pero deben seguir
    EXACTAMENTE la misma regla de negocio (`semantic_tag in {status,
    status_flag}`). Este test es la UNICA garantia de paridad entre
    ambas copias; su fallo indica que divergieron y deben corregirse
    juntas."""
    from altamira_extractor.pipeline import (
        candidate_source_adapters,
        enhanced_candidate_integration,
    )

    assert (
        enhanced_candidate_integration._STATE_TRANSITION_SEMANTIC_TAGS
        == candidate_source_adapters._STATE_TRANSITION_SEMANTIC_TAGS
        == frozenset({"status", "status_flag"})
    )
    for tag in ("status", "status_flag", "return_code", "indicator", None):
        productive_result = tag in enhanced_candidate_integration._STATE_TRANSITION_SEMANTIC_TAGS
        qualification_result = candidate_source_adapters.is_functional_state_transition_tag(tag)
        assert productive_result == qualification_result, (
            f"gate diverge para tag={tag!r}: productivo={productive_result}, "
            f"qualification={qualification_result}"
        )
