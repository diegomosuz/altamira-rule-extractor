"""Tests unitarios del enriquecimiento evidencial SQLCODE (Fase
15B3-C3-C-B): puros, sin Neo4j, sin filesystem, sin JAR. Cubren
exactamente los casos obligatorios de la seccion 28 del enunciado
(A-J)."""

from __future__ import annotations

from altamira_extractor.contracts.candidate import RuleCandidate
from altamira_extractor.contracts.canonical import (
    CanonicalParagraph,
    CanonicalProgram,
    CanonicalSqlAccess,
    CanonicalStatement,
)
from altamira_extractor.contracts.context_package import ContextPackageDecision
from altamira_extractor.contracts.dependencies import DependencyArtifact
from altamira_extractor.contracts.enums import (
    CallTargetKind,
    LocationKind,
    NodeLabel,
    ProgramTerminationKind,
    SourceFormat,
    StatementKind,
    TableAccessOperation,
)
from altamira_extractor.contracts.inventory import Inventory
from altamira_extractor.contracts.manifest import (
    Manifest,
    ManifestApplication,
    ManifestCountry,
    ManifestImplementation,
    ManifestOperation,
    ManifestSource,
)
from altamira_extractor.contracts.semantic_enrichment import SemanticEnrichmentArtifact
from altamira_extractor.pipeline.context_package_builder import (
    _decision_statement_for_candidate,
    _enrich_decision_with_sql_causal_evidence,
    _is_sqlcode_decision,
    _nearest_preceding_operative_exec_sql,
    _sql_causal_evidence_id,
)
from altamira_extractor.pipeline.semantic_graph_builder import build_semantic_graph

_COBOL_PATH = "01-codigo/cobol/PROG.cbl"

_HASH = "a" * 64


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _paragraph(statements: list[CanonicalStatement]) -> CanonicalParagraph:
    """Mismo patron de agregacion que `test_dependency_builder.py`/
    `test_semantic_effects_analyzer.py`: `CanonicalParagraph.
    variables_read/variables_written/sql_access` deben ser la union
    ordenada y sin duplicados de sus statements (invariante del
    contrato)."""
    return CanonicalParagraph(
        name="MAIN-PARA",
        source_text="MAIN-PARA.",
        location_kind=LocationKind.UNKNOWN,
        statements=statements,
        variables_read=_ordered_unique([v for s in statements for v in s.variables_read]),
        variables_written=_ordered_unique([v for s in statements for v in s.variables_written]),
        sql_access=[access for s in statements for access in s.sql_access],
    )


def _statement(statement_id: str, kind: StatementKind, **overrides: object) -> CanonicalStatement:
    defaults: dict[str, object] = {
        "statement_id": statement_id,
        "kind": kind,
        "source_text": statement_id,
        "location_kind": LocationKind.EXACT,
        "source_file": "01-codigo/cobol/PROG.cbl",
        "line_start": 1,
        "line_end": 1,
    }
    defaults.update(overrides)
    return CanonicalStatement(**defaults)  # type: ignore[arg-type]


def _operative_exec_sql(statement_id: str, **overrides: object) -> CanonicalStatement:
    line_start = overrides.get("line_start", 1)
    line_end = overrides.get("line_end", 1)
    access = CanonicalSqlAccess(
        table="CUENTAS",
        operation=TableAccessOperation.READS,
        location_kind=LocationKind.EXACT,
        source_file="01-codigo/cobol/PROG.cbl",
        line_start=line_start,
        line_end=line_end,
    )
    return _statement(statement_id, StatementKind.EXEC_SQL, sql_access=[access], **overrides)


def _unsupported_exec_sql(statement_id: str, **overrides: object) -> CanonicalStatement:
    return _statement(statement_id, StatementKind.EXEC_SQL, sql_access=[], **overrides)


def _sqlcode_if(statement_id: str, **overrides: object) -> CanonicalStatement:
    return _statement(
        statement_id,
        StatementKind.IF,
        expression="SQLCODE=0",
        operands=["SQLCODE"],
        variables_read=["SQLCODE"],
        **overrides,
    )


def _sqlcode_evaluate(statement_id: str, **overrides: object) -> CanonicalStatement:
    return _statement(
        statement_id,
        StatementKind.EVALUATE,
        expression="SQLCODE",
        operands=["SQLCODE"],
        variables_read=["SQLCODE"],
        **overrides,
    )


# --- _is_sqlcode_decision -----------------------------------------------


def test_is_sqlcode_decision_exact_identifier_true() -> None:
    assert _is_sqlcode_decision(_sqlcode_if("D1")) is True


def test_is_sqlcode_decision_case_insensitive() -> None:
    stmt = _statement("D1", StatementKind.IF, operands=["sqlcode"])
    assert _is_sqlcode_decision(stmt) is True


def test_is_sqlcode_decision_rejects_substring_ws_sqlcode_flag() -> None:
    """Caso H del enunciado: WS-SQLCODE-FLAG nunca califica."""
    stmt = _statement("D1", StatementKind.IF, operands=["WS-SQLCODE-FLAG"])
    assert _is_sqlcode_decision(stmt) is False


def test_is_sqlcode_decision_rejects_substring_sqlcode_aux() -> None:
    stmt = _statement("D1", StatementKind.IF, operands=["SQLCODE-AUX"])
    assert _is_sqlcode_decision(stmt) is False


def test_is_sqlcode_decision_rejects_non_decision_kind() -> None:
    stmt = _statement("S1", StatementKind.MOVE, variables_read=["SQLCODE"])
    assert _is_sqlcode_decision(stmt) is False


# --- _nearest_preceding_operative_exec_sql: casos A-J --------------------


def test_case_a_exec_sql_immediately_before_decision_is_proven() -> None:
    exec_sql = _operative_exec_sql("S1")
    decision = _sqlcode_if("D1")
    statements = [exec_sql, decision]
    linkage = _nearest_preceding_operative_exec_sql(statements, decision)
    assert linkage.status == "PROVEN"
    assert linkage.exec_sql_statement is not None
    assert linkage.exec_sql_statement.statement_id == "S1"


def test_case_b_move_between_exec_sql_and_decision_still_proven() -> None:
    exec_sql = _operative_exec_sql("S1")
    move = _statement("S2", StatementKind.MOVE, target_data_items=["B"])
    decision = _sqlcode_if("D1")
    statements = [exec_sql, move, decision]
    linkage = _nearest_preceding_operative_exec_sql(statements, decision)
    assert linkage.status == "PROVEN"
    assert linkage.exec_sql_statement is not None
    assert linkage.exec_sql_statement.statement_id == "S1"


def test_case_c_two_exec_sql_links_to_the_nearer_one_never_the_first() -> None:
    s1 = _operative_exec_sql("S1")
    s2 = _operative_exec_sql("S2")
    decision = _sqlcode_if("D1")
    statements = [s1, s2, decision]
    linkage = _nearest_preceding_operative_exec_sql(statements, decision)
    assert linkage.status == "PROVEN"
    assert linkage.exec_sql_statement is not None
    assert linkage.exec_sql_statement.statement_id == "S2"


def test_case_d_call_between_exec_sql_and_decision_is_ambiguous() -> None:
    exec_sql = _operative_exec_sql("S1")
    call = _statement(
        "S2",
        StatementKind.CALL,
        call_target_kind=CallTargetKind.LITERAL,
        called_program_name="OTRO",
    )
    decision = _sqlcode_if("D1")
    statements = [exec_sql, call, decision]
    linkage = _nearest_preceding_operative_exec_sql(statements, decision)
    assert linkage.status == "AMBIGUOUS"
    assert linkage.exec_sql_statement is None


def test_case_e_perform_between_exec_sql_and_decision_is_ambiguous() -> None:
    exec_sql = _operative_exec_sql("S1")
    perform = _statement("S2", StatementKind.PERFORM, target_paragraphs=["P-X"])
    decision = _sqlcode_if("D1")
    statements = [exec_sql, perform, decision]
    linkage = _nearest_preceding_operative_exec_sql(statements, decision)
    assert linkage.status == "AMBIGUOUS"


def test_go_to_between_exec_sql_and_decision_is_ambiguous() -> None:
    exec_sql = _operative_exec_sql("S1")
    go_to = _statement("S2", StatementKind.GO_TO, target_paragraphs=["P-X"])
    decision = _sqlcode_if("D1")
    statements = [exec_sql, go_to, decision]
    linkage = _nearest_preceding_operative_exec_sql(statements, decision)
    assert linkage.status == "AMBIGUOUS"


def test_program_termination_between_exec_sql_and_decision_is_ambiguous() -> None:
    exec_sql = _operative_exec_sql("S1")
    stop_run = _statement(
        "S2",
        StatementKind.PROGRAM_TERMINATION,
        program_termination_kind=ProgramTerminationKind.STOP_RUN,
    )
    decision = _sqlcode_if("D1")
    statements = [exec_sql, stop_run, decision]
    linkage = _nearest_preceding_operative_exec_sql(statements, decision)
    assert linkage.status == "AMBIGUOUS"


def test_case_f_no_preceding_exec_sql_is_not_available() -> None:
    move = _statement("S1", StatementKind.MOVE, target_data_items=["A"])
    decision = _sqlcode_if("D1")
    statements = [move, decision]
    linkage = _nearest_preceding_operative_exec_sql(statements, decision)
    assert linkage.status == "NOT_AVAILABLE"
    assert linkage.exec_sql_statement is None


def test_case_g_unsupported_exec_sql_nearest_is_never_proven() -> None:
    """EXEC SQL INCLUDE SQLCA (unsupported, sql_access=[]) mas cercano:
    nunca se salta hacia un EXEC SQL operativo anterior."""
    operative = _operative_exec_sql("S1")
    include_sqlca = _unsupported_exec_sql("S2")
    decision = _sqlcode_if("D1")
    statements = [operative, include_sqlca, decision]
    linkage = _nearest_preceding_operative_exec_sql(statements, decision)
    assert linkage.status == "AMBIGUOUS"
    assert linkage.exec_sql_statement is None


def test_case_i_one_exec_sql_links_to_three_consecutive_decisions() -> None:
    exec_sql = _operative_exec_sql("S1")
    d1 = _sqlcode_if("D1")
    d2 = _sqlcode_if("D2")
    d3 = _sqlcode_if("D3")
    statements = [exec_sql, d1, d2, d3]
    for decision in (d1, d2, d3):
        linkage = _nearest_preceding_operative_exec_sql(statements, decision)
        assert linkage.status == "PROVEN", decision.statement_id
        assert linkage.exec_sql_statement is not None
        assert linkage.exec_sql_statement.statement_id == "S1"


def test_case_i_barrier_inside_one_intervening_if_degrades_only_from_there() -> None:
    """1 EXEC SQL -> IF#1 (con un CALL en su rama THEN) -> IF#2 SQLCODE:
    el subarbol de IF#1 contiene un CALL -> AMBIGUOUS para IF#2, aunque
    IF#1 en si mismo no es SQLCODE-related."""
    exec_sql = _operative_exec_sql("S1")
    if1 = _statement("D0", StatementKind.IF, expression="X=1", operands=["X"])
    call_inside = _statement(
        "D0-1",
        StatementKind.CALL,
        parent_statement_id="D0",
        call_target_kind=CallTargetKind.LITERAL,
        called_program_name="OTRO",
    )
    d2 = _sqlcode_if("D2")
    statements = [exec_sql, if1, call_inside, d2]
    linkage = _nearest_preceding_operative_exec_sql(statements, d2)
    assert linkage.status == "AMBIGUOUS"


def test_case_j_evaluate_sqlcode_links_to_the_evaluate_statement_itself() -> None:
    exec_sql = _operative_exec_sql("S1")
    evaluate = _sqlcode_evaluate("D1")
    when_zero = _statement(
        "D1-1",
        StatementKind.MOVE,
        parent_statement_id="D1",
        target_data_items=["WS-ESTADO"],
    )
    statements = [exec_sql, evaluate, when_zero]
    assert _is_sqlcode_decision(evaluate) is True
    assert _is_sqlcode_decision(when_zero) is False
    linkage = _nearest_preceding_operative_exec_sql(statements, evaluate)
    assert linkage.status == "PROVEN"
    assert linkage.exec_sql_statement is not None
    assert linkage.exec_sql_statement.statement_id == "S1"


# --- _decision_statement_for_candidate ------------------------------------


def _candidate(**overrides: object) -> RuleCandidate:
    defaults: dict[str, object] = {
        "candidate_id": f"candidate::det::1.0::{_HASH}::dec-1",
        "paragraph_id": "program::PROG::paragraph::MAIN-PARA",
        "paragraph_name": "MAIN-PARA",
        "decision_id": "program::PROG::paragraph::MAIN-PARA::decision::5::1",
        "detector_id": "det",
        "detector_version": "1.0",
        "detector_score": 1.0,
        "condition": "SQLCODE=0",
        "outcome_code": "R001",
        "rule_type": None,
        "line_start": 1,
        "source_file": "01-codigo/cobol/PROG.cbl",
        "source_package_hash": _HASH,
    }
    defaults.update(overrides)
    return RuleCandidate(**defaults)  # type: ignore[arg-type]


def test_decision_statement_for_candidate_resolves_by_ordinal_and_line_start() -> None:
    exec_sql = _operative_exec_sql("S1", line_start=3, line_end=3)
    decision = _sqlcode_if("D1", line_start=5, line_end=5)
    paragraph = _paragraph([exec_sql, decision])
    candidate = _candidate(
        decision_id="program::PROG::paragraph::MAIN-PARA::decision::5::1"
    )
    resolved = _decision_statement_for_candidate(paragraph, candidate)
    assert resolved is not None
    assert resolved.statement_id == "D1"


def test_decision_statement_for_candidate_rejects_line_start_mismatch() -> None:
    decision = _sqlcode_if("D1", line_start=99, line_end=99)
    paragraph = _paragraph([decision])
    candidate = _candidate(
        decision_id="program::PROG::paragraph::MAIN-PARA::decision::5::1"
    )
    assert _decision_statement_for_candidate(paragraph, candidate) is None


def test_decision_statement_for_candidate_none_for_malformed_id() -> None:
    paragraph = _paragraph([])
    candidate = _candidate(decision_id="dec-1")
    assert _decision_statement_for_candidate(paragraph, candidate) is None


# --- _enrich_decision_with_sql_causal_evidence ----------------------------


def _base_decision() -> ContextPackageDecision:
    return ContextPackageDecision(
        expression="SQLCODE=0",
        normalized_expression="SQLCODE=0",
        operands=["SQLCODE"],
        outcome_code=None,
        evidence_ids=["evidence::original-decision"],
    )


def test_enrich_adds_causal_evidence_when_proven() -> None:
    exec_sql = _operative_exec_sql("S1", line_start=3, line_end=6)
    decision_stmt = _sqlcode_if("D1", line_start=8, line_end=8)
    paragraph = _paragraph([exec_sql, decision_stmt])
    candidate = _candidate(
        decision_id="program::PROG::paragraph::MAIN-PARA::decision::8::1"
    )
    decision = _base_decision()
    evidence = list(decision.evidence_ids)

    enriched_decision, enriched_evidence = _enrich_decision_with_sql_causal_evidence(
        decision, [], candidate, {(candidate.source_file, candidate.paragraph_name): paragraph}
    )

    assert enriched_decision.evidence_ids != evidence
    assert set(evidence).issubset(enriched_decision.evidence_ids)
    assert len(enriched_decision.evidence_ids) == len(evidence) + 1
    assert len(enriched_evidence) == 1
    causal = enriched_evidence[0]
    assert causal.kind == "sql_causal_context"
    assert causal.evidence_id in enriched_decision.evidence_ids
    assert causal.details is not None
    assert causal.details["exec_sql_statement_id"] == "S1"


def test_enrich_adds_nothing_when_ambiguous() -> None:
    """Seccion 30 del enunciado: CALL intermedio -> no se agrega
    evidencia causal falsa, la Decision/evidence original se conservan
    intactas."""
    exec_sql = _operative_exec_sql("S1")
    call = _statement(
        "S2",
        StatementKind.CALL,
        call_target_kind=CallTargetKind.LITERAL,
        called_program_name="OTRO",
    )
    decision_stmt = _sqlcode_if("D1", line_start=1, line_end=1)
    paragraph = _paragraph([exec_sql, call, decision_stmt])
    candidate = _candidate(
        decision_id="program::PROG::paragraph::MAIN-PARA::decision::1::1"
    )
    decision = _base_decision()

    enriched_decision, enriched_evidence = _enrich_decision_with_sql_causal_evidence(
        decision, [], candidate, {(candidate.source_file, candidate.paragraph_name): paragraph}
    )

    assert enriched_decision == decision
    assert enriched_evidence == []


def test_enrich_adds_nothing_when_decision_is_not_sqlcode_related() -> None:
    exec_sql = _operative_exec_sql("S1")
    decision_stmt = _statement(
        "D1", StatementKind.IF, expression="WS-SALDO<0", operands=["WS-SALDO"], line_start=1
    )
    paragraph = _paragraph([exec_sql, decision_stmt])
    candidate = _candidate(
        decision_id="program::PROG::paragraph::MAIN-PARA::decision::1::1"
    )
    decision = _base_decision()

    enriched_decision, enriched_evidence = _enrich_decision_with_sql_causal_evidence(
        decision, [], candidate, {(candidate.source_file, candidate.paragraph_name): paragraph}
    )

    assert enriched_decision == decision
    assert enriched_evidence == []


def test_enrich_adds_nothing_when_paragraph_not_in_canonical_index() -> None:
    decision = _base_decision()
    candidate = _candidate()
    enriched_decision, enriched_evidence = _enrich_decision_with_sql_causal_evidence(
        decision, [], candidate, {}
    )
    assert enriched_decision == decision
    assert enriched_evidence == []


def test_sql_causal_evidence_id_is_deterministic_and_candidate_independent() -> None:
    """Seccion 15 del enunciado: dos Decisions causadas por el mismo
    EXEC SQL citan el mismo evidence_id (no depende de candidate_id)."""
    id_1 = _sql_causal_evidence_id(source_package_hash=_HASH, exec_sql_statement_id="S1")
    id_2 = _sql_causal_evidence_id(source_package_hash=_HASH, exec_sql_statement_id="S1")
    id_3 = _sql_causal_evidence_id(source_package_hash=_HASH, exec_sql_statement_id="S2")
    assert id_1 == id_2
    assert id_1 != id_3
    assert id_1.startswith("evidence::")


# --- Invariante obligatoria (correccion pre-commit): _decision_statement_
# for_candidate DEBE resolver exactamente el mismo CanonicalStatement que
# semantic_graph_builder asigno a un decision_id PRODUCTIVO real -- nunca
# se prueba una copia paralela del mismo algoritmo, se prueba contra el
# ID que el propio grafo real genero. ------------------------------------


def _real_graph_for(paragraph: CanonicalParagraph) -> tuple[CanonicalProgram, object]:
    program = CanonicalProgram(
        program_name="PROG1",
        source_file=_COBOL_PATH,
        source_hash=_HASH,
        source_package_hash=_HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        paragraphs=[paragraph],
    )
    inventory = Inventory(
        run_id="run-1",
        source_package_hash=_HASH,
        manifest=Manifest(
            schema_version="1.0",
            country=ManifestCountry(code="AR", name="Argentina"),
            application=ManifestApplication(name="App"),
            operation=ManifestOperation(logical_name="OP", description="d"),
            implementation=ManifestImplementation(version="1.0", entry_programs=["PROG1"]),
            source=ManifestSource(format=SourceFormat.FIXED, encoding="UTF-8"),
            parameter_tables=[],
        ),
        files=[],
    )
    graph = build_semantic_graph(
        inventory=inventory,
        programs=[program],
        dependency_artifact=DependencyArtifact(run_id="run-1", source_package_hash=_HASH),
        enrichment_artifact=SemanticEnrichmentArtifact(
            run_id="run-1",
            source_package_hash=_HASH,
            semantic_tags_config_hash=_HASH,
            domain_glossary_config_hash=_HASH,
        ),
        source_package_hash=_HASH,
    )
    return program, graph


def _real_decision_candidate(graph, ordinal: int = 0) -> RuleCandidate:
    """`ordinal`-esimo nodo Decision REAL del grafo (orden de aparicion
    en `graph.nodes`, ya deterministico)."""
    decision_nodes = sorted(n.id for n in graph.nodes if NodeLabel.DECISION in n.labels)
    real_decision_id = decision_nodes[ordinal]
    paragraph_id = real_decision_id.rsplit("::decision::", 1)[0]
    return _candidate(
        paragraph_id=paragraph_id,
        paragraph_name="MAIN-PARA",
        decision_id=real_decision_id,
        source_file=_COBOL_PATH,
    )


def test_shared_identity_resolves_real_if_decision_id() -> None:
    if_stmt = _statement(
        "PROG1::MAIN-PARA::0::IF",
        StatementKind.IF,
        expression="X=1",
        operands=["X"],
        line_start=5,
        line_end=5,
    )
    paragraph = _paragraph([if_stmt])
    _, graph = _real_graph_for(paragraph)
    candidate = _real_decision_candidate(graph)

    resolved = _decision_statement_for_candidate(paragraph, candidate)
    assert resolved is not None
    assert resolved.statement_id == if_stmt.statement_id


def test_shared_identity_resolves_real_evaluate_decision_id() -> None:
    evaluate_stmt = _sqlcode_evaluate("PROG1::MAIN-PARA::0::EVALUATE", line_start=5, line_end=5)
    paragraph = _paragraph([evaluate_stmt])
    _, graph = _real_graph_for(paragraph)
    candidate = _real_decision_candidate(graph)

    resolved = _decision_statement_for_candidate(paragraph, candidate)
    assert resolved is not None
    assert resolved.statement_id == evaluate_stmt.statement_id


def test_shared_identity_resolves_real_nested_decision_id() -> None:
    """IF anidado dentro de la rama de otro IF: ambos son Decision
    (ordinal 1 y 2, mismo orden que `paragraph.statements`) -- el
    resuelto debe distinguir correctamente cada uno."""
    outer_if = _statement(
        "PROG1::MAIN-PARA::0::IF",
        StatementKind.IF,
        expression="Y=1",
        operands=["Y"],
        line_start=5,
        line_end=5,
    )
    inner_if = _statement(
        "PROG1::MAIN-PARA::1::IF",
        StatementKind.IF,
        expression="SQLCODE=0",
        operands=["SQLCODE"],
        variables_read=["SQLCODE"],
        parent_statement_id=outer_if.statement_id,
        line_start=6,
        line_end=6,
    )
    paragraph = _paragraph([outer_if, inner_if])
    _, graph = _real_graph_for(paragraph)

    outer_candidate = _real_decision_candidate(graph, ordinal=0)
    inner_candidate = _real_decision_candidate(graph, ordinal=1)

    resolved_outer = _decision_statement_for_candidate(paragraph, outer_candidate)
    resolved_inner = _decision_statement_for_candidate(paragraph, inner_candidate)
    assert resolved_outer is not None and resolved_outer.statement_id == outer_if.statement_id
    assert resolved_inner is not None and resolved_inner.statement_id == inner_if.statement_id
    assert _is_sqlcode_decision(resolved_inner) is True
    assert _is_sqlcode_decision(resolved_outer) is False
