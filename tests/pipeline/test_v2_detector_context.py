"""Tests del contexto puro de deteccion V2 (Fase 4 de la ampliacion
semantica, `feat/v2-detectors-shadow-mode`): `pipeline/v2_detector_context.py`.
Cubre la correlacion SemanticGraph<->CanonicalProgram por propiedades y
prefijo de ID (nunca recalculo de ProgramIdentity) y los helpers
`decision_statement_for_node`/`is_statement_under`/
`program_name_for_v1_candidate`."""

from __future__ import annotations

from altamira_extractor.contracts.candidate import CandidateArtifact, RuleCandidate
from altamira_extractor.contracts.canonical import CanonicalParagraph, CanonicalProgram
from altamira_extractor.contracts.enums import (
    CandidateStatus,
    LocationKind,
    SourceFormat,
    StatementKind,
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


def _simple_program() -> CanonicalProgram:
    if_stmt = make_stmt(
        statement_id="P1::A::0::IF", kind=StatementKind.IF, line_start=10, expression="CONDICION"
    )
    inner = make_stmt(
        statement_id="P1::A::1::MOVE", target_data_items=["WS-COD-RETORNO"],
        variables_written=["WS-COD-RETORNO"], assigned_literal="0005",
        parent_statement_id="P1::A::0::IF", branch_kind="THEN",
    )
    paragraph = CanonicalParagraph(
        name="A", source_text="A.", location_kind=LocationKind.UNKNOWN,
        statements=[if_stmt, inner], variables_written=["WS-COD-RETORNO"],
    )
    return CanonicalProgram(
        program_name="PROG1", source_file=SRC, source_hash=HASH, source_package_hash=HASH,
        source_format=SourceFormat.FIXED, encoding="UTF-8", paragraphs=[paragraph],
    )


def _ctx():
    program = _simple_program()
    return build_ctx(
        program=program, decisions=[("A", 10, "CONDICION")],
        data_item_tags={"WS-COD-RETORNO": "return_code"},
    )


# ---------------------------------------------------------------------------
# Indices correlacionados por propiedad/prefijo (nunca por recalculo de ID)
# ---------------------------------------------------------------------------


def test_program_node_by_name_is_indexed() -> None:
    ctx = _ctx()
    assert "PROG1" in ctx.program_node_by_name
    assert ctx.program_node_by_name["PROG1"].id == program_node_id("PROG1")


def test_paragraph_node_by_key_is_indexed_by_program_and_paragraph_name() -> None:
    ctx = _ctx()
    node = ctx.paragraph_node_by_key[("PROG1", "A")]
    assert node.id == paragraph_node_id("PROG1", "A")


def test_decision_nodes_by_paragraph_key_finds_the_decision() -> None:
    ctx = _ctx()
    nodes = ctx.decision_nodes_by_paragraph_key[("PROG1", "A")]
    assert len(nodes) == 1
    assert nodes[0].id == decision_node_id_for("PROG1", "A", 10, 1)
    assert ctx.decision_node_by_id[nodes[0].id] is nodes[0]


def test_data_item_node_by_key_and_semantic_tag_index() -> None:
    ctx = _ctx()
    node = ctx.data_item_node_by_key[("PROG1", "WS-COD-RETORNO")]
    assert node.id == data_item_node_id("PROG1", "WS-COD-RETORNO")
    assert ctx.data_item_node_by_id[node.id] is node
    assert node in ctx.data_item_nodes_by_semantic_tag["return_code"]


def test_program_name_by_paragraph_node_id_reverse_index() -> None:
    ctx = _ctx()
    node = ctx.paragraph_node_by_key[("PROG1", "A")]
    assert ctx.program_name_by_paragraph_node_id[node.id] == "PROG1"


def test_absent_data_item_is_simply_missing_from_indices_never_invented() -> None:
    ctx = _ctx()
    assert ("PROG1", "WS-DOES-NOT-EXIST") not in ctx.data_item_node_by_key


# ---------------------------------------------------------------------------
# decision_statement_for_node: correlacion Decision(GraphNode) -> CanonicalStatement
# ---------------------------------------------------------------------------


def test_decision_statement_for_node_matches_by_line_start() -> None:
    ctx = _ctx()
    decision_node = ctx.decision_nodes_by_paragraph_key[("PROG1", "A")][0]
    statement = ctx.decision_statement_for_node(
        program_name="PROG1", paragraph_name="A", decision_node=decision_node
    )
    assert statement is not None
    assert statement.statement_id == "P1::A::0::IF"
    assert statement.kind == StatementKind.IF


def test_decision_statement_for_node_returns_none_for_unknown_program() -> None:
    ctx = _ctx()
    decision_node = ctx.decision_nodes_by_paragraph_key[("PROG1", "A")][0]
    assert (
        ctx.decision_statement_for_node(
            program_name="OTHER", paragraph_name="A", decision_node=decision_node
        )
        is None
    )


# ---------------------------------------------------------------------------
# is_statement_under: cadena parent_statement_id, nunca cruza de paragraph
# ---------------------------------------------------------------------------


def test_is_statement_under_true_for_direct_child() -> None:
    ctx = _ctx()
    assert ctx.is_statement_under(
        statement_id="P1::A::1::MOVE", ancestor_statement_id="P1::A::0::IF"
    )


def test_is_statement_under_true_for_the_statement_itself() -> None:
    ctx = _ctx()
    assert ctx.is_statement_under(
        statement_id="P1::A::0::IF", ancestor_statement_id="P1::A::0::IF"
    )


def test_is_statement_under_false_for_unrelated_statement() -> None:
    ctx = _ctx()
    assert not ctx.is_statement_under(
        statement_id="P1::A::0::IF", ancestor_statement_id="P1::A::1::MOVE"
    )


def test_is_statement_under_false_for_unknown_statement_id() -> None:
    ctx = _ctx()
    assert not ctx.is_statement_under(
        statement_id="does-not-exist", ancestor_statement_id="P1::A::0::IF"
    )


# ---------------------------------------------------------------------------
# program_name_for_v1_candidate: resuelve el programa de un RuleCandidate V1
# ---------------------------------------------------------------------------


def test_program_name_for_v1_candidate_resolves_via_paragraph_node_prefix() -> None:
    ctx = _ctx()
    paragraph_node = ctx.paragraph_node_by_key[("PROG1", "A")]
    decision_node = ctx.decision_nodes_by_paragraph_key[("PROG1", "A")][0]
    v1_candidate = RuleCandidate(
        candidate_id="candidate::q0-return-code-decision::1.0::" + HASH + "::" + decision_node.id,
        paragraph_id=paragraph_node.id, paragraph_name="A", decision_id=decision_node.id,
        detector_id="q0-return-code-decision", detector_version="1.0", detector_score=1.0,
        status=CandidateStatus.DETECTED_CANDIDATE, condition="CONDICION", outcome_code="0005",
        line_start=10, source_file=SRC, source_package_hash=HASH,
    )
    assert ctx.program_name_for_v1_candidate(v1_candidate) == "PROG1"


def test_program_name_for_v1_candidate_returns_none_for_unknown_paragraph() -> None:
    ctx = _ctx()
    v1_candidate = RuleCandidate(
        candidate_id="candidate::q0-return-code-decision::1.0::" + HASH + "::unknown",
        paragraph_id="does-not-exist", paragraph_name="A", decision_id="does-not-exist::decision",
        detector_id="q0-return-code-decision", detector_version="1.0", detector_score=1.0,
        status=CandidateStatus.DETECTED_CANDIDATE, condition="CONDICION", outcome_code="0005",
        line_start=10, source_file=SRC, source_package_hash=HASH,
    )
    assert ctx.program_name_for_v1_candidate(v1_candidate) is None


# ---------------------------------------------------------------------------
# v1_candidates_by_id / v1_candidates_by_decision_id
# ---------------------------------------------------------------------------


def test_v1_candidates_indices_are_built_from_the_artifact() -> None:
    decision_node = _ctx().decision_nodes_by_paragraph_key[("PROG1", "A")][0]
    paragraph_node = _ctx().paragraph_node_by_key[("PROG1", "A")]
    v1_candidate = RuleCandidate(
        candidate_id="candidate::q0-return-code-decision::1.0::" + HASH + "::" + decision_node.id,
        paragraph_id=paragraph_node.id, paragraph_name="A", decision_id=decision_node.id,
        detector_id="q0-return-code-decision", detector_version="1.0", detector_score=1.0,
        status=CandidateStatus.DETECTED_CANDIDATE, condition="CONDICION", outcome_code="0005",
        line_start=10, source_file=SRC, source_package_hash=HASH,
    )
    v1_candidates = CandidateArtifact(
        run_id="run1", source_package_hash=HASH, semantic_graph_hash=HASH,
        invariants_query_hash=HASH, q0_query_hash=HASH, candidates=[v1_candidate],
    )
    program = _simple_program()
    ctx = build_ctx(
        program=program, decisions=[("A", 10, "CONDICION")],
        data_item_tags={"WS-COD-RETORNO": "return_code"}, v1_candidates=v1_candidates,
    )
    assert ctx.v1_candidates_by_id[v1_candidate.candidate_id] is v1_candidate
    assert ctx.v1_candidates_by_decision_id[decision_node.id] == [v1_candidate]
