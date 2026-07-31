"""Tests del analizador PURO de cobertura semantica (Fase 1 de la
ampliacion semantica, checkpoint `feat/semantic-expansion-foundation`):
`pipeline/semantic_coverage_analyzer.py`. Nunca Neo4j, nunca LLM, nunca
filesystem -- todo se construye en memoria."""

from __future__ import annotations

from altamira_extractor.contracts.candidate import CandidateArtifact, RuleCandidate
from altamira_extractor.contracts.canonical import (
    CanonicalDataItem,
    CanonicalParagraph,
    CanonicalProgram,
    CanonicalStatement,
)
from altamira_extractor.contracts.dependencies import DependencyArtifact
from altamira_extractor.contracts.enums import (
    LocationKind,
    NodeLabel,
    RelationshipType,
    SourceFormat,
    StatementKind,
)
from altamira_extractor.contracts.semantic_coverage import (
    CandidateImpact,
    SemanticSupportStatus,
    ZeroCandidateReason,
)
from altamira_extractor.contracts.semantic_graph import GraphNode, GraphRelationship, SemanticGraph
from altamira_extractor.pipeline.semantic_coverage_analyzer import analyze_semantic_coverage

_HASH = "e" * 64
_REQUIRED_HASHES = {
    "artifacts/02-canonical": _HASH,
    "artifacts/03-dependencies.json": _HASH,
    "artifacts/04-semantic-graph.json": _HASH,
    "artifacts/06-candidates.json": _HASH,
}


def _statement(**overrides: object) -> CanonicalStatement:
    defaults: dict[str, object] = {
        "statement_id": "P1::PARA-A::1::MOVE",
        "kind": StatementKind.MOVE,
        "source_text": "MOVE 'X' TO WS-TARGET",
        "location_kind": LocationKind.UNKNOWN,
    }
    defaults.update(overrides)
    return CanonicalStatement(**defaults)  # type: ignore[arg-type]


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _paragraph(
    name: str, statements: list[CanonicalStatement], **overrides: object
) -> CanonicalParagraph:
    defaults: dict[str, object] = {
        "name": name,
        "source_text": f"{name}.",
        "location_kind": LocationKind.UNKNOWN,
        "statements": statements,
        "variables_read": _ordered_unique(
            [v for stmt in statements for v in stmt.variables_read]
        ),
        "variables_written": _ordered_unique(
            [v for stmt in statements for v in stmt.variables_written]
        ),
        "sql_access": [access for stmt in statements for access in stmt.sql_access],
    }
    defaults.update(overrides)
    return CanonicalParagraph(**defaults)  # type: ignore[arg-type]


def _program(
    name: str,
    paragraphs: list[CanonicalParagraph],
    *,
    data_items: list[CanonicalDataItem] | None = None,
    unsupported_constructs: list[str] | None = None,
    source_file: str = "a.cbl",
) -> CanonicalProgram:
    return CanonicalProgram(
        program_name=name,
        source_file=source_file,
        source_hash=_HASH,
        source_package_hash=_HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        data_items=data_items or [],
        paragraphs=paragraphs,
        unsupported_constructs=unsupported_constructs or [],
    )


def _empty_dependency_artifact(run_id: str = "run-1") -> DependencyArtifact:
    return DependencyArtifact(run_id=run_id, source_package_hash=_HASH)


def _empty_candidate_artifact(run_id: str = "run-1") -> CandidateArtifact:
    return CandidateArtifact(
        run_id=run_id,
        source_package_hash=_HASH,
        semantic_graph_hash=_HASH,
        invariants_query_hash=_HASH,
        q0_query_hash=_HASH,
    )


def _candidate(**overrides: object) -> RuleCandidate:
    defaults: dict[str, object] = {
        "candidate_id": "cand-1",
        "paragraph_id": "para1",
        "paragraph_name": "PARA-A",
        "decision_id": "dec1",
        "detector_id": "q0-return-code-decision",
        "detector_version": "1.0",
        "detector_score": 1.0,
        "condition": "WS-FLAG = 1",
        "line_start": 1,
        "source_file": "a.cbl",
        "source_package_hash": _HASH,
    }
    defaults.update(overrides)
    return RuleCandidate(**defaults)  # type: ignore[arg-type]


def _graph_with_program(
    program_name: str,
    *,
    paragraph_ids: list[str],
    decisions_by_paragraph: dict[str, list[str]] | None = None,
    leads_to_by_decision: dict[str, str] | None = None,
) -> SemanticGraph:
    decisions_by_paragraph = decisions_by_paragraph or {}
    leads_to_by_decision = leads_to_by_decision or {}

    nodes = [GraphNode(id="prog1", labels=[NodeLabel.PROGRAM], properties={"name": program_name})]
    relationships = []
    for para_id in paragraph_ids:
        nodes.append(GraphNode(id=para_id, labels=[NodeLabel.PARAGRAPH], properties={}))
        relationships.append(
            GraphRelationship(type=RelationshipType.CONTAINS, from_id="prog1", to_id=para_id)
        )
        for decision_id in decisions_by_paragraph.get(para_id, []):
            nodes.append(GraphNode(id=decision_id, labels=[NodeLabel.DECISION], properties={}))
            relationships.append(
                GraphRelationship(
                    type=RelationshipType.HAS_DECISION, from_id=para_id, to_id=decision_id
                )
            )
            sink_id = leads_to_by_decision.get(decision_id)
            if sink_id is not None:
                nodes.append(GraphNode(id=sink_id, labels=[NodeLabel.DATA_ITEM], properties={}))
                relationships.append(
                    GraphRelationship(
                        type=RelationshipType.LEADS_TO, from_id=decision_id, to_id=sink_id
                    )
                )

    nodes.sort(key=lambda node: node.id)
    return SemanticGraph(source_package_hash=_HASH, nodes=nodes, relationships=relationships)


def _analyze(
    programs: list[CanonicalProgram],
    *,
    dependency_artifact: DependencyArtifact | None = None,
    semantic_graph: SemanticGraph | None = None,
    candidate_artifact: CandidateArtifact | None = None,
):
    return analyze_semantic_coverage(
        canonical_programs=programs,
        dependency_artifact=dependency_artifact or _empty_dependency_artifact(),
        semantic_graph=semantic_graph or SemanticGraph(source_package_hash=_HASH),
        candidate_artifact=candidate_artifact or _empty_candidate_artifact(),
        run_id="run-1",
        source_package_hash=_HASH,
        source_artifact_hashes=_REQUIRED_HASHES,
    )


def _only_construct(report, construct_name: str):
    matches = [
        entry
        for program in report.programs
        for entry in program.construct_coverage
        if entry.construct_name == construct_name
    ]
    assert len(matches) == 1, f"esperaba exactamente 1 entrada para {construct_name!r}: {matches}"
    return matches[0]


# ---------------------------------------------------------------------------
# Clasificacion por StatementKind (Fase 3)
# ---------------------------------------------------------------------------


def test_if_is_fully_supported() -> None:
    program = _program("P1", [_paragraph("A", [_statement(kind=StatementKind.IF)])])
    report = _analyze([program])
    coverage = _only_construct(report, "IF")
    assert coverage.support_status == SemanticSupportStatus.FULLY_SUPPORTED


def test_evaluate_is_fully_supported() -> None:
    program = _program("P1", [_paragraph("A", [_statement(kind=StatementKind.EVALUATE)])])
    report = _analyze([program])
    coverage = _only_construct(report, "EVALUATE")
    assert coverage.support_status == SemanticSupportStatus.FULLY_SUPPORTED


def test_go_to_is_fully_supported() -> None:
    program = _program(
        "P1", [_paragraph("A", [_statement(kind=StatementKind.GO_TO, target_paragraphs=["B"])])]
    )
    report = _analyze([program])
    coverage = _only_construct(report, "GO_TO")
    assert coverage.support_status == SemanticSupportStatus.FULLY_SUPPORTED


def test_move_literal_direct_is_fully_supported() -> None:
    stmt = _statement(assigned_literal="0005", target_data_items=["WS-COD-AUX"])
    program = _program("P1", [_paragraph("A", [stmt])])
    report = _analyze([program])
    coverage = _only_construct(report, "MOVE")
    assert coverage.support_status == SemanticSupportStatus.FULLY_SUPPORTED
    assert coverage.diagnostic_code == "MOVE_LITERAL_DIRECT"
    assert coverage.candidate_impact == CandidateImpact.NONE


def test_move_variable_to_variable_is_partially_supported() -> None:
    stmt = _statement(variables_read=["WS-COD-AUX"], target_data_items=["WS-COD-RETORNO"])
    program = _program("P1", [_paragraph("A", [stmt])])
    report = _analyze([program])
    coverage = _only_construct(report, "MOVE")
    assert coverage.support_status == SemanticSupportStatus.PARTIALLY_SUPPORTED
    assert coverage.diagnostic_code == "MOVE_VARIABLE_TO_VARIABLE"


def test_move_multiple_targets_is_partially_supported() -> None:
    stmt = _statement(assigned_literal="X", target_data_items=["A", "B", "C"])
    program = _program("P1", [_paragraph("A", [stmt])])
    report = _analyze([program])
    coverage = _only_construct(report, "MOVE")
    assert coverage.support_status == SemanticSupportStatus.PARTIALLY_SUPPORTED
    assert coverage.diagnostic_code == "MOVE_LITERAL_MULTIPLE_TARGETS"


def test_set_is_always_partially_supported() -> None:
    program = _program("P1", [_paragraph("A", [_statement(kind=StatementKind.SET)])])
    report = _analyze([program])
    coverage = _only_construct(report, "SET")
    assert coverage.support_status == SemanticSupportStatus.PARTIALLY_SUPPORTED
    assert coverage.diagnostic_code == "SET_TARGET_KIND_AMBIGUOUS"


def test_compute_is_partially_supported() -> None:
    program = _program("P1", [_paragraph("A", [_statement(kind=StatementKind.COMPUTE)])])
    report = _analyze([program])
    coverage = _only_construct(report, "COMPUTE")
    assert coverage.support_status == SemanticSupportStatus.PARTIALLY_SUPPORTED


def test_perform_is_partially_supported() -> None:
    program = _program("P1", [_paragraph("A", [_statement(kind=StatementKind.PERFORM)])])
    report = _analyze([program])
    coverage = _only_construct(report, "PERFORM")
    assert coverage.support_status == SemanticSupportStatus.PARTIALLY_SUPPORTED


def test_exec_sql_is_partially_supported() -> None:
    program = _program("P1", [_paragraph("A", [_statement(kind=StatementKind.EXEC_SQL)])])
    report = _analyze([program])
    coverage = _only_construct(report, "EXEC_SQL")
    assert coverage.support_status == SemanticSupportStatus.PARTIALLY_SUPPORTED


def test_other_is_preserved_only_never_unsupported() -> None:
    program = _program("P1", [_paragraph("A", [_statement(kind=StatementKind.OTHER)])])
    report = _analyze([program])
    coverage = _only_construct(report, "OTHER")
    assert coverage.support_status == SemanticSupportStatus.PRESERVED_ONLY


def test_unsupported_constructs_produce_unsupported_entries() -> None:
    program = _program(
        "P1",
        [_paragraph("A", [_statement(kind=StatementKind.OTHER)])],
        unsupported_constructs=[
            "GobackStatement en paragraph A no decodificado estructuralmente "
            "(kind=OTHER, source_text conservado)"
        ],
    )
    report = _analyze([program])
    coverage = _only_construct(report, "GobackStatement")
    assert coverage.support_status == SemanticSupportStatus.UNSUPPORTED
    assert coverage.occurrence_count == 1
    assert report.programs[0].unsupported_construct_count == 1
    assert report.programs[0].unsupported_count == 1


# ---------------------------------------------------------------------------
# Nivel 88 (Fase 5)
# ---------------------------------------------------------------------------


def test_level_88_data_item_is_partially_supported_and_counted() -> None:
    item = CanonicalDataItem(
        name="COND-X", qualified_name="COND-X", level=88, location_kind=LocationKind.UNKNOWN
    )
    program = _program("P1", [_paragraph("A", [_statement()])], data_items=[item])
    report = _analyze([program])
    assert report.programs[0].level_88_data_item_count == 1
    coverage = _only_construct(report, "LEVEL_88_CONDITION_NAME")
    assert coverage.support_status == SemanticSupportStatus.PARTIALLY_SUPPORTED
    assert coverage.diagnostic_code == "LEVEL_88_SEMANTICS_NOT_MODELED"


def test_no_level_88_items_means_zero_count_and_no_entry() -> None:
    program = _program("P1", [_paragraph("A", [_statement()])])
    report = _analyze([program])
    assert report.programs[0].level_88_data_item_count == 0
    names = {entry.construct_name for entry in report.programs[0].construct_coverage}
    assert "LEVEL_88_CONDITION_NAME" not in names


# ---------------------------------------------------------------------------
# Multiples programas / agregacion global
# ---------------------------------------------------------------------------


def test_multiple_programs_are_each_reported_separately() -> None:
    program_a = _program("PROG-A", [_paragraph("X", [_statement()])], source_file="a.cbl")
    program_b = _program(
        "PROG-B", [_paragraph("Y", [_statement(kind=StatementKind.COMPUTE)])], source_file="b.cbl"
    )
    report = _analyze([program_b, program_a])  # orden de entrada invertido a proposito
    assert [program.program for program in report.programs] == ["PROG-A", "PROG-B"]


def test_summary_aggregates_without_losing_program_detail() -> None:
    program_a = _program("PROG-A", [_paragraph("X", [_statement()])], source_file="a.cbl")
    program_b = _program(
        "PROG-B", [_paragraph("Y", [_statement(kind=StatementKind.COMPUTE)])], source_file="b.cbl"
    )
    report = _analyze([program_a, program_b])
    assert report.summary.program_count == 2
    assert report.summary.statement_count == 2
    assert len(report.programs) == 2
    assert report.programs[0].statement_count == 1
    assert report.programs[1].statement_count == 1


# ---------------------------------------------------------------------------
# Decisiones / Q0 (Fase 4)
# ---------------------------------------------------------------------------


def test_decision_with_leads_to_counts_as_resolved_effect() -> None:
    program = _program("P1", [_paragraph("A", [_statement(kind=StatementKind.IF)])])
    graph = _graph_with_program(
        "P1",
        paragraph_ids=["para1"],
        decisions_by_paragraph={"para1": ["dec1"]},
        leads_to_by_decision={"dec1": "item1"},
    )
    report = _analyze([program], semantic_graph=graph)
    coverage = report.programs[0]
    assert coverage.decision_count == 1
    assert coverage.decisions_with_resolved_effect_count == 1
    assert coverage.decisions_without_resolved_effect_count == 0


def test_decision_without_leads_to_counts_as_unresolved_effect() -> None:
    program = _program("P1", [_paragraph("A", [_statement(kind=StatementKind.IF)])])
    graph = _graph_with_program(
        "P1", paragraph_ids=["para1"], decisions_by_paragraph={"para1": ["dec1"]}
    )
    report = _analyze([program], semantic_graph=graph)
    coverage = report.programs[0]
    assert coverage.decision_count == 1
    assert coverage.decisions_with_resolved_effect_count == 0
    assert coverage.decisions_without_resolved_effect_count == 1
    assert coverage.zero_candidate_reason == ZeroCandidateReason.DECISIONS_WITHOUT_RESOLVED_EFFECTS


def test_candidates_present_reason() -> None:
    program = _program("P1", [_paragraph("A", [_statement(kind=StatementKind.IF)])])
    graph = _graph_with_program(
        "P1",
        paragraph_ids=["para1"],
        decisions_by_paragraph={"para1": ["dec1"]},
        leads_to_by_decision={"dec1": "item1"},
    )
    candidates = CandidateArtifact(
        run_id="run-1",
        source_package_hash=_HASH,
        semantic_graph_hash=_HASH,
        invariants_query_hash=_HASH,
        q0_query_hash=_HASH,
        candidates=[_candidate(paragraph_id="para1")],
    )
    report = _analyze([program], semantic_graph=graph, candidate_artifact=candidates)
    coverage = report.programs[0]
    assert coverage.candidate_count == 1
    assert coverage.zero_candidate_reason == ZeroCandidateReason.CANDIDATES_PRESENT


def test_zero_decisions_reason() -> None:
    program = _program("P1", [_paragraph("A", [_statement()])])
    graph = _graph_with_program("P1", paragraph_ids=["para1"])
    report = _analyze([program], semantic_graph=graph)
    coverage = report.programs[0]
    assert coverage.decision_count == 0
    assert coverage.zero_candidate_reason == ZeroCandidateReason.NO_DECISIONS


def test_resolved_effects_without_q0_match_reason() -> None:
    program = _program("P1", [_paragraph("A", [_statement(kind=StatementKind.IF)])])
    graph = _graph_with_program(
        "P1",
        paragraph_ids=["para1"],
        decisions_by_paragraph={"para1": ["dec1"]},
        leads_to_by_decision={"dec1": "item1"},
    )
    report = _analyze([program], semantic_graph=graph)  # cero candidatos
    coverage = report.programs[0]
    assert coverage.decisions_with_resolved_effect_count == 1
    assert coverage.candidate_count == 0
    assert coverage.zero_candidate_reason == ZeroCandidateReason.RESOLVED_EFFECTS_WITHOUT_Q0_MATCH


def test_insufficient_diagnostic_data_when_program_node_missing_from_graph() -> None:
    program = _program("P1", [_paragraph("A", [_statement()])])
    report = _analyze([program], semantic_graph=SemanticGraph(source_package_hash=_HASH))
    coverage = report.programs[0]
    assert coverage.zero_candidate_reason == ZeroCandidateReason.INSUFFICIENT_DIAGNOSTIC_DATA
    assert coverage.candidate_count == 0
    assert coverage.decision_count == 0
    assert any("graph_program_node_not_found" in note for note in coverage.diagnostics)


# ---------------------------------------------------------------------------
# Determinismo / referencias limitadas / sin source_text
# ---------------------------------------------------------------------------


def test_analyzer_output_is_order_independent_from_input_program_order() -> None:
    program_a = _program("PROG-A", [_paragraph("X", [_statement()])], source_file="a.cbl")
    program_b = _program("PROG-B", [_paragraph("Y", [_statement()])], source_file="b.cbl")
    report_forward = _analyze([program_a, program_b])
    report_backward = _analyze([program_b, program_a])
    assert report_forward.to_stable_json() == report_backward.to_stable_json()


def test_source_references_are_capped_but_occurrence_count_is_not() -> None:
    statements = [
        _statement(
            statement_id=f"P1::A::{i}::MOVE", assigned_literal="X", target_data_items=[f"W{i}"]
        )
        for i in range(10)
    ]
    program = _program("P1", [_paragraph("A", statements)])
    report = _analyze([program])
    coverage = _only_construct(report, "MOVE")
    assert coverage.occurrence_count == 10
    assert len(coverage.source_references) <= 5


def test_report_never_contains_full_source_text() -> None:
    long_source = "MOVE 'X' TO WS-TARGET " + ("padding " * 50)
    stmt = _statement(
        source_text=long_source, assigned_literal="X", target_data_items=["WS-TARGET"]
    )
    program = _program("P1", [_paragraph("A", [stmt])])
    report = _analyze([program])
    payload = report.to_stable_json()
    assert long_source not in payload
    assert "padding" not in payload


# ---------------------------------------------------------------------------
# Caso explicito del enunciado: cadena de dos MOVE
# ---------------------------------------------------------------------------


def test_two_hop_move_chain_never_invents_propagation() -> None:
    first = _statement(
        statement_id="P1::A::1::MOVE",
        source_text="MOVE '0005' TO WS-COD-AUX",
        assigned_literal="0005",
        target_data_items=["WS-COD-AUX"],
    )
    second = _statement(
        statement_id="P1::A::2::MOVE",
        source_text="MOVE WS-COD-AUX TO WS-COD-RETORNO",
        variables_read=["WS-COD-AUX"],
        target_data_items=["WS-COD-RETORNO"],
    )
    program = _program("P1", [_paragraph("A", [first, second])])
    graph = SemanticGraph(
        source_package_hash=_HASH,
        nodes=[GraphNode(id="prog1", labels=[NodeLabel.PROGRAM], properties={"name": "P1"})],
    )
    report = _analyze([program], semantic_graph=graph)

    move_entries = [
        entry for entry in report.programs[0].construct_coverage if entry.construct_name == "MOVE"
    ]
    codes = {entry.diagnostic_code for entry in move_entries}
    assert "MOVE_LITERAL_DIRECT" in codes
    assert "MOVE_VARIABLE_TO_VARIABLE" in codes

    literal_entry = next(e for e in move_entries if e.diagnostic_code == "MOVE_LITERAL_DIRECT")
    variable_entry = next(
        e for e in move_entries if e.diagnostic_code == "MOVE_VARIABLE_TO_VARIABLE"
    )
    assert literal_entry.support_status == SemanticSupportStatus.FULLY_SUPPORTED
    assert variable_entry.support_status == SemanticSupportStatus.PARTIALLY_SUPPORTED

    # Nunca se afirma que '0005' llego a WS-COD-RETORNO: ninguna referencia
    # ni explicacion del segundo MOVE menciona el literal del primero.
    payload = report.to_stable_json()
    assert "'0005'" not in payload
    assert "0005" not in payload

    # Ningun LEADS_TO ni candidato inventado.
    assert report.summary.decision_count == 0
    assert report.summary.candidate_count == 0
