"""Tests unitarios de dependency_builder: puros, sin filesystem ni JAR."""

from __future__ import annotations

from altamira_extractor.contracts.canonical import (
    CanonicalDataItem,
    CanonicalParagraph,
    CanonicalProgram,
    CanonicalStatement,
)
from altamira_extractor.contracts.dependencies import _dependency_sort_key
from altamira_extractor.contracts.enums import (
    BranchKind,
    DependencyEvidenceRole,
    DependencyType,
    LocationKind,
    SourceFormat,
    StatementKind,
)
from altamira_extractor.pipeline import dependency_builder as db
from altamira_extractor.pipeline.dependency_builder import (
    ProgramIdentity,
    ProgramInput,
    VariableResolution,
    VariableResolutionFailure,
    build_dependencies,
    paragraph_id,
    resolve_variable,
)

VALID_HASH = "a" * 64
IDENTITY = ProgramIdentity(country_code="AR", logical_name="OP-TRF-PROPIA", version="1.0")


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _statement(
    *,
    statement_id: str,
    kind: StatementKind,
    line_start: int = 1,
    line_end: int = 1,
    location_kind: LocationKind = LocationKind.EXACT,
    variables_read: list[str] | None = None,
    variables_written: list[str] | None = None,
    target_paragraphs: list[str] | None = None,
    parent_statement_id: str | None = None,
    branch_kind: BranchKind | None = None,
    branch_condition: str | None = None,
) -> CanonicalStatement:
    source_file = "01-codigo/cobol/PROG.cbl" if location_kind == LocationKind.EXACT else None
    return CanonicalStatement(
        statement_id=statement_id,
        kind=kind,
        source_text=f"{statement_id} source",
        source_file=source_file,
        line_start=line_start if location_kind != LocationKind.UNKNOWN else None,
        line_end=line_end if location_kind != LocationKind.UNKNOWN else None,
        location_kind=location_kind,
        parent_statement_id=parent_statement_id,
        branch_kind=branch_kind,
        branch_condition=branch_condition,
        variables_read=variables_read or [],
        variables_written=variables_written or [],
        target_paragraphs=target_paragraphs or [],
    )


def _paragraph(name: str, statements: list[CanonicalStatement]) -> CanonicalParagraph:
    return CanonicalParagraph(
        name=name,
        source_text=f"{name}.",
        source_file="01-codigo/cobol/PROG.cbl",
        line_start=1,
        line_end=1,
        location_kind=LocationKind.EXACT,
        statements=statements,
        variables_read=_ordered_unique([v for s in statements for v in s.variables_read]),
        variables_written=_ordered_unique([v for s in statements for v in s.variables_written]),
        sql_access=[],
    )


def _data_item(name: str, qualified_name: str | None = None) -> CanonicalDataItem:
    return CanonicalDataItem(
        name=name,
        qualified_name=qualified_name or name,
        level=1,
        location_kind=LocationKind.EXACT,
        source_file="01-codigo/cobol/PROG.cbl",
        line=1,
    )


def _program(
    *,
    program_name: str = "PROG1",
    paragraphs: list[CanonicalParagraph],
    data_items: list[CanonicalDataItem] | None = None,
    source_hash: str = "b" * 64,
) -> CanonicalProgram:
    return CanonicalProgram(
        program_name=program_name,
        source_file="01-codigo/cobol/PROG.cbl",
        source_hash=source_hash,
        source_package_hash=VALID_HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        data_items=data_items or [],
        paragraphs=paragraphs,
    )


def _index_for(items: list[CanonicalDataItem]) -> db._SymbolIndex:
    program = _program(paragraphs=[], data_items=items)
    return db._build_symbol_index(program)


# --- Identidad ---


def test_program_id_includes_all_identity_components() -> None:
    pid = IDENTITY.program_id(program_name="PROG1", source_hash="abc123def456" + "0" * 52)
    assert pid == "program::AR::OP-TRF-PROPIA::PROG1::1.0::abc123def456"


def test_two_versions_of_same_program_produce_different_ids() -> None:
    other_version = ProgramIdentity(country_code="AR", logical_name="OP-TRF-PROPIA", version="2.0")
    pid_v1 = IDENTITY.program_id(program_name="PROG1", source_hash="a" * 64)
    pid_v2 = other_version.program_id(program_name="PROG1", source_hash="a" * 64)
    assert pid_v1 != pid_v2

    pid_recompiled = IDENTITY.program_id(program_name="PROG1", source_hash="c" * 64)
    assert pid_v1 != pid_recompiled


def test_same_paragraph_name_in_different_programs_does_not_collide() -> None:
    pid_a = IDENTITY.program_id(program_name="PROGA", source_hash="a" * 64)
    pid_b = IDENTITY.program_id(program_name="PROGB", source_hash="a" * 64)
    assert paragraph_id(pid_a, "MAIN-PARA") != paragraph_id(pid_b, "MAIN-PARA")


# --- Resolucion de variables ---


def test_resolve_variable_qualified_exact() -> None:
    index = _index_for([_data_item("WS-FIELD", "WS-GROUP.WS-FIELD")])
    result = resolve_variable("WS-GROUP.WS-FIELD", index)
    assert isinstance(result, VariableResolution)
    assert result.derivation_rule == "QUALIFIED"
    assert result.qualified_name == "WS-GROUP.WS-FIELD"


def test_resolve_variable_simple_name_unique() -> None:
    index = _index_for([_data_item("WS-FIELD", "WS-GROUP.WS-FIELD")])
    result = resolve_variable("WS-FIELD", index)
    assert isinstance(result, VariableResolution)
    assert result.derivation_rule == "SIMPLE_NAME"
    assert result.qualified_name == "WS-GROUP.WS-FIELD"


def test_resolve_variable_simple_name_ambiguous() -> None:
    index = _index_for(
        [_data_item("WS-FIELD", "GROUP-A.WS-FIELD"), _data_item("WS-FIELD", "GROUP-B.WS-FIELD")]
    )
    result = resolve_variable("WS-FIELD", index)
    assert isinstance(result, VariableResolutionFailure)
    assert result.reason == "AMBIGUOUS"


def test_resolve_variable_unresolved() -> None:
    index = _index_for([_data_item("WS-OTHER")])
    result = resolve_variable("WS-MISSING", index)
    assert isinstance(result, VariableResolutionFailure)
    assert result.reason == "UNRESOLVED"


def test_two_data_items_same_name_different_qualified_name_resolve_individually() -> None:
    index = _index_for(
        [_data_item("WS-FIELD", "GROUP-A.WS-FIELD"), _data_item("WS-FIELD", "GROUP-B.WS-FIELD")]
    )
    result_a = resolve_variable("GROUP-A.WS-FIELD", index)
    result_b = resolve_variable("GROUP-B.WS-FIELD", index)
    assert isinstance(result_a, VariableResolution)
    assert result_a.qualified_name == "GROUP-A.WS-FIELD"
    assert isinstance(result_b, VariableResolution)
    assert result_b.qualified_name == "GROUP-B.WS-FIELD"
    # el nombre simple compartido sigue siendo ambiguo
    assert isinstance(resolve_variable("WS-FIELD", index), VariableResolutionFailure)


# --- DATA_DEPENDS_ON ---


def test_data_dependency_qualified_match_between_paragraphs() -> None:
    write_stmt = _statement(
        statement_id="P1::A::0::MOVE",
        kind=StatementKind.MOVE,
        variables_written=["WS-FLAG"],
        line_start=5,
        line_end=5,
    )
    para_a = _paragraph("PARA-A", [write_stmt])
    read_stmt = _statement(
        statement_id="P1::B::0::IF",
        kind=StatementKind.IF,
        variables_read=["WS-FLAG"],
        line_start=20,
        line_end=20,
    )
    para_b = _paragraph("PARA-B", [read_stmt])
    program = _program(paragraphs=[para_a, para_b], data_items=[_data_item("WS-FLAG")])

    deps, warnings = build_dependencies(
        [ProgramInput(program, IDENTITY)], source_package_hash=VALID_HASH
    )

    data_deps = [d for d in deps if d.dependency_type == DependencyType.DATA_DEPENDS_ON]
    assert len(data_deps) == 1
    dep = data_deps[0]
    program_id_ = IDENTITY.program_id(program_name="PROG1", source_hash=program.source_hash)
    assert dep.from_paragraph_id == paragraph_id(program_id_, "PARA-A")
    assert dep.to_paragraph_id == paragraph_id(program_id_, "PARA-B")
    assert dep.variables == ["WS-FLAG"]
    assert dep.confidence == 0.8
    assert dep.derivation_rule == "PARAGRAPH_WRITE_READ_QUALIFIED_MATCH"
    assert dep.dependency_depth == 1
    assert not warnings


def test_data_dependency_through_exec_sql_select_into() -> None:
    """Fase 15B3-C3-B, seccion 10 (obligatorio): dependency_builder.py NO
    se modifica -- DATA_DEPENDS_ON a traves de SQL debe funcionar
    automaticamente porque StatementExtractor.convertExecSql ya puebla
    CanonicalStatement.variables_read/variables_written para un EXEC_SQL
    con direccion demostrada (dependency_builder es agnostico a
    StatementKind, solo lee esos dos campos). Caso E2E obligatorio del
    enunciado: Paragraph A hace SELECT SALDO INTO :WS-SALDO WHERE
    CUENTA=:WS-CUENTA; Paragraph B hace IF WS-SALDO < WS-LIMITE."""
    select_stmt = _statement(
        statement_id="P1::A::0::EXEC_SQL",
        kind=StatementKind.EXEC_SQL,
        variables_read=["WS-CUENTA"],
        variables_written=["WS-SALDO"],
        line_start=5,
        line_end=9,
    )
    para_a = _paragraph("CONSULTAR-SALDO-PARA", [select_stmt])
    if_stmt = _statement(
        statement_id="P1::B::0::IF",
        kind=StatementKind.IF,
        variables_read=["WS-SALDO", "WS-LIMITE"],
        line_start=20,
        line_end=20,
    )
    para_b = _paragraph("EVALUAR-SALDO-PARA", [if_stmt])
    program = _program(
        paragraphs=[para_a, para_b],
        data_items=[_data_item("WS-SALDO"), _data_item("WS-CUENTA"), _data_item("WS-LIMITE")],
    )

    deps, warnings = build_dependencies(
        [ProgramInput(program, IDENTITY)], source_package_hash=VALID_HASH
    )

    data_deps = [d for d in deps if d.dependency_type == DependencyType.DATA_DEPENDS_ON]
    assert len(data_deps) == 1
    dep = data_deps[0]
    program_id_ = IDENTITY.program_id(program_name="PROG1", source_hash=program.source_hash)
    # Sentido verificado (nunca asumido por el nombre, ver seccion 10):
    # from_paragraph_id es quien ESCRIBE (origen del dato), to_paragraph_id
    # es quien LEE (consumidor) -- mismo sentido que
    # test_data_dependency_qualified_match_between_paragraphs, coherente
    # con Q2 (data_origin)-[:DATA_DEPENDS_ON*]->(sink).
    assert dep.from_paragraph_id == paragraph_id(program_id_, "CONSULTAR-SALDO-PARA")
    assert dep.to_paragraph_id == paragraph_id(program_id_, "EVALUAR-SALDO-PARA")
    assert dep.variables == ["WS-SALDO"]
    assert not warnings


def test_data_dependency_absent_when_exec_sql_direction_partially_unresolved() -> None:
    """Correccion pre-commit posterior a la entrega inicial de C3-B: un
    EXEC SQL con una expresion en la lista SELECT (p. ej.
    ``SELECT :WS-FACTOR * SALDO INTO :WS-RESULTADO FROM CUENTAS
    WHERE ID = :WS-ID``) deja WS-FACTOR fuera de input/output -- el gate
    de completitud de StatementExtractor.convertExecSql (Java) NUNCA puebla
    CanonicalStatement.variables_read/variables_written en ese caso
    (ambos quedan vacios, se replica aqui). dependency_builder.py NO se
    modifica: al recibir variables_written=[] para el EXEC SQL, nunca
    construye una arista DATA_DEPENDS_ON hacia el Paragraph B que lee
    WS-RESULTADO -- publicar esa arista habria sido un lineage parcial y
    enganoso (WS-FACTOR nunca se contabilizo)."""
    select_expression_stmt = _statement(
        statement_id="P1::A::0::EXEC_SQL",
        kind=StatementKind.EXEC_SQL,
        variables_read=[],
        variables_written=[],
        line_start=5,
        line_end=9,
    )
    para_a = _paragraph("CONSULTAR-FACTOR-PARA", [select_expression_stmt])
    if_stmt = _statement(
        statement_id="P1::B::0::IF",
        kind=StatementKind.IF,
        variables_read=["WS-RESULTADO", "WS-LIMITE"],
        line_start=20,
        line_end=20,
    )
    para_b = _paragraph("EVALUAR-RESULTADO-PARA", [if_stmt])
    program = _program(
        paragraphs=[para_a, para_b],
        data_items=[_data_item("WS-RESULTADO"), _data_item("WS-LIMITE")],
    )

    deps, warnings = build_dependencies(
        [ProgramInput(program, IDENTITY)], source_package_hash=VALID_HASH
    )

    data_deps = [d for d in deps if d.dependency_type == DependencyType.DATA_DEPENDS_ON]
    assert data_deps == []
    assert not warnings


def test_data_dependency_confidence_seven_for_unique_simple_name_match() -> None:
    write_stmt = _statement(
        statement_id="P1::A::0::MOVE",
        kind=StatementKind.MOVE,
        variables_written=["WS-FLAG"],
        line_start=5,
        line_end=5,
    )
    para_a = _paragraph("PARA-A", [write_stmt])
    read_stmt = _statement(
        statement_id="P1::B::0::IF",
        kind=StatementKind.IF,
        variables_read=["WS-FLAG"],
        line_start=20,
        line_end=20,
    )
    para_b = _paragraph("PARA-B", [read_stmt])
    program = _program(
        paragraphs=[para_a, para_b], data_items=[_data_item("WS-FLAG", "WS-GROUP.WS-FLAG")]
    )

    deps, _warnings = build_dependencies(
        [ProgramInput(program, IDENTITY)], source_package_hash=VALID_HASH
    )

    dep = next(d for d in deps if d.dependency_type == DependencyType.DATA_DEPENDS_ON)
    assert dep.confidence == 0.7
    assert dep.derivation_rule == "PARAGRAPH_WRITE_READ_UNIQUE_NAME_MATCH"
    assert dep.variables == ["WS-GROUP.WS-FLAG"]


def test_same_paragraph_write_and_read_produces_no_self_dependency() -> None:
    stmt_write = _statement(
        statement_id="P1::A::0::MOVE", kind=StatementKind.MOVE, variables_written=["WS-FLAG"]
    )
    stmt_read = _statement(
        statement_id="P1::A::1::IF", kind=StatementKind.IF, variables_read=["WS-FLAG"]
    )
    para_a = _paragraph("PARA-A", [stmt_write, stmt_read])
    program = _program(paragraphs=[para_a], data_items=[_data_item("WS-FLAG")])

    deps, _warnings = build_dependencies(
        [ProgramInput(program, IDENTITY)], source_package_hash=VALID_HASH
    )

    assert deps == []


def test_data_cycle_a_to_b_and_b_to_a_conserved() -> None:
    stmt_a_write = _statement(
        statement_id="P1::A::0::MOVE",
        kind=StatementKind.MOVE,
        variables_written=["X"],
        line_start=5,
        line_end=5,
    )
    stmt_a_read = _statement(
        statement_id="P1::A::1::IF",
        kind=StatementKind.IF,
        variables_read=["Y"],
        line_start=6,
        line_end=6,
    )
    para_a = _paragraph("PARA-A", [stmt_a_write, stmt_a_read])

    stmt_b_write = _statement(
        statement_id="P1::B::0::MOVE",
        kind=StatementKind.MOVE,
        variables_written=["Y"],
        line_start=20,
        line_end=20,
    )
    stmt_b_read = _statement(
        statement_id="P1::B::1::IF",
        kind=StatementKind.IF,
        variables_read=["X"],
        line_start=21,
        line_end=21,
    )
    para_b = _paragraph("PARA-B", [stmt_b_write, stmt_b_read])

    program = _program(
        paragraphs=[para_a, para_b], data_items=[_data_item("X"), _data_item("Y")]
    )

    deps, _warnings = build_dependencies(
        [ProgramInput(program, IDENTITY)], source_package_hash=VALID_HASH
    )

    data_deps = [d for d in deps if d.dependency_type == DependencyType.DATA_DEPENDS_ON]
    assert len(data_deps) == 2
    program_id_ = IDENTITY.program_id(program_name="PROG1", source_hash=program.source_hash)
    a_id = paragraph_id(program_id_, "PARA-A")
    b_id = paragraph_id(program_id_, "PARA-B")
    pairs = {(d.from_paragraph_id, d.to_paragraph_id) for d in data_deps}
    assert (a_id, b_id) in pairs
    assert (b_id, a_id) in pairs


def test_data_dependency_conserves_writer_and_reader_evidence() -> None:
    write_stmt = _statement(
        statement_id="P1::A::0::MOVE",
        kind=StatementKind.MOVE,
        variables_written=["WS-FLAG"],
        line_start=5,
        line_end=5,
    )
    para_a = _paragraph("PARA-A", [write_stmt])
    read_stmt = _statement(
        statement_id="P1::B::0::IF",
        kind=StatementKind.IF,
        variables_read=["WS-FLAG"],
        line_start=20,
        line_end=20,
    )
    para_b = _paragraph("PARA-B", [read_stmt])
    program = _program(paragraphs=[para_a, para_b], data_items=[_data_item("WS-FLAG")])

    deps, _warnings = build_dependencies(
        [ProgramInput(program, IDENTITY)], source_package_hash=VALID_HASH
    )

    dep = next(d for d in deps if d.dependency_type == DependencyType.DATA_DEPENDS_ON)
    roles = {e.role for e in dep.evidence}
    assert DependencyEvidenceRole.WRITER in roles
    assert DependencyEvidenceRole.READER in roles
    writer_evidence = next(e for e in dep.evidence if e.role == DependencyEvidenceRole.WRITER)
    reader_evidence = next(e for e in dep.evidence if e.role == DependencyEvidenceRole.READER)
    assert writer_evidence.statement_id == "P1::A::0::MOVE"
    assert reader_evidence.statement_id == "P1::B::0::IF"
    assert writer_evidence.original_variable == "WS-FLAG"
    assert writer_evidence.resolved_qualified_name == "WS-FLAG"


def test_ambiguous_or_unresolved_variables_produce_no_data_dependency() -> None:
    write_stmt = _statement(
        statement_id="P1::A::0::MOVE", kind=StatementKind.MOVE, variables_written=["WS-MISSING"]
    )
    para_a = _paragraph("PARA-A", [write_stmt])
    read_stmt = _statement(
        statement_id="P1::B::0::IF", kind=StatementKind.IF, variables_read=["WS-MISSING"]
    )
    para_b = _paragraph("PARA-B", [read_stmt])
    program = _program(paragraphs=[para_a, para_b], data_items=[])

    deps, warnings = build_dependencies(
        [ProgramInput(program, IDENTITY)], source_package_hash=VALID_HASH
    )

    assert deps == []
    assert any("WS-MISSING" in w for w in warnings)


# --- CONTROL_DEPENDS_ON ---


def test_control_go_to() -> None:
    stmt = _statement(
        statement_id="P1::A::0::GO_TO",
        kind=StatementKind.GO_TO,
        target_paragraphs=["PARA-B"],
        line_start=5,
        line_end=5,
    )
    para_a = _paragraph("PARA-A", [stmt])
    para_b = _paragraph("PARA-B", [])
    program = _program(paragraphs=[para_a, para_b])

    deps, warnings = build_dependencies(
        [ProgramInput(program, IDENTITY)], source_package_hash=VALID_HASH
    )

    control_deps = [d for d in deps if d.dependency_type == DependencyType.CONTROL_DEPENDS_ON]
    assert len(control_deps) == 1
    assert control_deps[0].control_construct == "GO_TO"
    assert control_deps[0].confidence == 1.0
    assert control_deps[0].derivation_rule == "EXPLICIT_CONTROL_TARGET"
    assert not warnings


def test_control_perform() -> None:
    stmt = _statement(
        statement_id="P1::A::0::PERFORM",
        kind=StatementKind.PERFORM,
        target_paragraphs=["PARA-B"],
        line_start=5,
        line_end=5,
    )
    para_a = _paragraph("PARA-A", [stmt])
    para_b = _paragraph("PARA-B", [])
    program = _program(paragraphs=[para_a, para_b])

    deps, _warnings = build_dependencies(
        [ProgramInput(program, IDENTITY)], source_package_hash=VALID_HASH
    )

    control_deps = [d for d in deps if d.dependency_type == DependencyType.CONTROL_DEPENDS_ON]
    assert len(control_deps) == 1
    assert control_deps[0].control_construct == "PERFORM"


def test_go_to_and_perform_to_same_target_remain_separate() -> None:
    go_to = _statement(
        statement_id="P1::A::0::GO_TO",
        kind=StatementKind.GO_TO,
        target_paragraphs=["PARA-B"],
        line_start=5,
        line_end=5,
    )
    perform = _statement(
        statement_id="P1::A::1::PERFORM",
        kind=StatementKind.PERFORM,
        target_paragraphs=["PARA-B"],
        line_start=6,
        line_end=6,
    )
    para_a = _paragraph("PARA-A", [go_to, perform])
    para_b = _paragraph("PARA-B", [])
    program = _program(paragraphs=[para_a, para_b])

    deps, _warnings = build_dependencies(
        [ProgramInput(program, IDENTITY)], source_package_hash=VALID_HASH
    )

    control_deps = [d for d in deps if d.dependency_type == DependencyType.CONTROL_DEPENDS_ON]
    assert len(control_deps) == 2
    assert {d.control_construct for d in control_deps} == {"GO_TO", "PERFORM"}


def test_go_to_multiple_targets() -> None:
    stmt = _statement(
        statement_id="P1::A::0::GO_TO",
        kind=StatementKind.GO_TO,
        target_paragraphs=["PARA-B", "PARA-C"],
        line_start=5,
        line_end=5,
    )
    para_a = _paragraph("PARA-A", [stmt])
    para_b = _paragraph("PARA-B", [])
    para_c = _paragraph("PARA-C", [])
    program = _program(paragraphs=[para_a, para_b, para_c])

    deps, _warnings = build_dependencies(
        [ProgramInput(program, IDENTITY)], source_package_hash=VALID_HASH
    )

    control_deps = [d for d in deps if d.dependency_type == DependencyType.CONTROL_DEPENDS_ON]
    assert len(control_deps) == 2
    program_id_ = IDENTITY.program_id(program_name="PROG1", source_hash=program.source_hash)
    targets = {d.to_paragraph_id for d in control_deps}
    assert paragraph_id(program_id_, "PARA-B") in targets
    assert paragraph_id(program_id_, "PARA-C") in targets


def test_control_target_not_found_warns_without_dependency() -> None:
    stmt = _statement(
        statement_id="P1::A::0::PERFORM",
        kind=StatementKind.PERFORM,
        target_paragraphs=["PARA-MISSING"],
        line_start=5,
        line_end=5,
    )
    para_a = _paragraph("PARA-A", [stmt])
    program = _program(paragraphs=[para_a])

    deps, warnings = build_dependencies(
        [ProgramInput(program, IDENTITY)], source_package_hash=VALID_HASH
    )

    assert deps == []
    assert any("PARA-MISSING" in w for w in warnings)


def test_control_target_ambiguous_warns_without_dependency() -> None:
    stmt = _statement(
        statement_id="P1::A::0::PERFORM",
        kind=StatementKind.PERFORM,
        target_paragraphs=["PARA-B"],
        line_start=5,
        line_end=5,
    )
    para_a = _paragraph("PARA-A", [stmt])
    para_b1 = _paragraph("PARA-B", [])
    para_b2 = _paragraph("PARA-B", [])
    program = _program(paragraphs=[para_a, para_b1, para_b2])

    deps, warnings = build_dependencies(
        [ProgramInput(program, IDENTITY)], source_package_hash=VALID_HASH
    )

    assert deps == []
    assert any("ambigua" in w for w in warnings)


def test_control_nested_in_if_conserves_branch_evidence() -> None:
    if_stmt = _statement(
        statement_id="P1::A::0::IF", kind=StatementKind.IF, line_start=5, line_end=8
    )
    perform_stmt = _statement(
        statement_id="P1::A::1::PERFORM",
        kind=StatementKind.PERFORM,
        target_paragraphs=["PARA-B"],
        line_start=6,
        line_end=6,
        parent_statement_id="P1::A::0::IF",
        branch_kind=BranchKind.THEN,
    )
    para_a = _paragraph("PARA-A", [if_stmt, perform_stmt])
    para_b = _paragraph("PARA-B", [])
    program = _program(paragraphs=[para_a, para_b])

    deps, _warnings = build_dependencies(
        [ProgramInput(program, IDENTITY)], source_package_hash=VALID_HASH
    )

    control_deps = [d for d in deps if d.dependency_type == DependencyType.CONTROL_DEPENDS_ON]
    assert len(control_deps) == 1
    evidence = control_deps[0].evidence[0]
    assert evidence.parent_statement_id == "P1::A::0::IF"
    assert evidence.branch_kind == BranchKind.THEN


def test_perform_with_two_endpoints_does_not_synthesize_intermediate_paragraphs() -> None:
    stmt = _statement(
        statement_id="P1::A::0::PERFORM",
        kind=StatementKind.PERFORM,
        target_paragraphs=["PARA-B", "PARA-D"],
        line_start=5,
        line_end=5,
    )
    para_a = _paragraph("PARA-A", [stmt])
    para_b = _paragraph("PARA-B", [])
    para_c = _paragraph("PARA-C", [])
    para_d = _paragraph("PARA-D", [])
    program = _program(paragraphs=[para_a, para_b, para_c, para_d])

    deps, warnings = build_dependencies(
        [ProgramInput(program, IDENTITY)], source_package_hash=VALID_HASH
    )

    control_deps = [d for d in deps if d.dependency_type == DependencyType.CONTROL_DEPENDS_ON]
    assert len(control_deps) == 2
    program_id_ = IDENTITY.program_id(program_name="PROG1", source_hash=program.source_hash)
    targets = {d.to_paragraph_id for d in control_deps}
    assert paragraph_id(program_id_, "PARA-B") in targets
    assert paragraph_id(program_id_, "PARA-D") in targets
    assert paragraph_id(program_id_, "PARA-C") not in targets
    assert any("THRU" in w for w in warnings)


def test_self_reference_control_omitted_with_warning() -> None:
    stmt = _statement(
        statement_id="P1::A::0::GO_TO",
        kind=StatementKind.GO_TO,
        target_paragraphs=["PARA-A"],
        line_start=5,
        line_end=5,
    )
    para_a = _paragraph("PARA-A", [stmt])
    program = _program(paragraphs=[para_a])

    deps, warnings = build_dependencies(
        [ProgramInput(program, IDENTITY)], source_package_hash=VALID_HASH
    )

    assert deps == []
    assert any("auto-dependencia" in w for w in warnings)


# --- Aislamiento entre programas y determinismo ---


def test_no_cross_program_dependencies() -> None:
    stmt_a = _statement(
        statement_id="P1::A::0::PERFORM",
        kind=StatementKind.PERFORM,
        target_paragraphs=["SHARED-PARA"],
        line_start=5,
        line_end=5,
    )
    para_a = _paragraph("PARA-A", [stmt_a])
    program1 = _program(program_name="PROG1", paragraphs=[para_a], source_hash="b" * 64)

    para_shared = _paragraph("SHARED-PARA", [])
    program2 = _program(program_name="PROG2", paragraphs=[para_shared], source_hash="c" * 64)

    deps, warnings = build_dependencies(
        [ProgramInput(program1, IDENTITY), ProgramInput(program2, IDENTITY)],
        source_package_hash=VALID_HASH,
    )

    assert deps == []
    assert any("SHARED-PARA" in w for w in warnings)


def test_output_is_deterministically_ordered() -> None:
    stmt_to_c = _statement(
        statement_id="P1::A::0::GO_TO",
        kind=StatementKind.GO_TO,
        target_paragraphs=["PARA-C"],
        line_start=5,
        line_end=5,
    )
    stmt_to_b = _statement(
        statement_id="P1::A::1::PERFORM",
        kind=StatementKind.PERFORM,
        target_paragraphs=["PARA-B"],
        line_start=6,
        line_end=6,
    )
    para_a = _paragraph("PARA-A", [stmt_to_c, stmt_to_b])
    para_b = _paragraph("PARA-B", [])
    para_c = _paragraph("PARA-C", [])
    program = _program(paragraphs=[para_a, para_b, para_c])

    deps, _warnings = build_dependencies(
        [ProgramInput(program, IDENTITY)], source_package_hash=VALID_HASH
    )

    assert deps == sorted(deps, key=_dependency_sort_key)


def test_repeated_build_is_idempotent_and_stable() -> None:
    stmt = _statement(
        statement_id="P1::A::0::PERFORM",
        kind=StatementKind.PERFORM,
        target_paragraphs=["PARA-B"],
        line_start=5,
        line_end=5,
    )
    para_a = _paragraph("PARA-A", [stmt])
    para_b = _paragraph("PARA-B", [])
    program = _program(paragraphs=[para_a, para_b])

    deps1, warnings1 = build_dependencies(
        [ProgramInput(program, IDENTITY)], source_package_hash=VALID_HASH
    )
    deps2, warnings2 = build_dependencies(
        [ProgramInput(program, IDENTITY)], source_package_hash=VALID_HASH
    )

    assert deps1 == deps2
    assert warnings1 == warnings2
