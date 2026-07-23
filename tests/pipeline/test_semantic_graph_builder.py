"""Tests unitarios de semantic_graph_builder.build_semantic_graph: puro,
sin filesystem ni JAR. Construye Inventory/CanonicalProgram/
DependencyArtifact/SemanticEnrichmentArtifact directamente en memoria."""

from __future__ import annotations

import json
from collections.abc import Iterable

import pytest

from altamira_extractor.contracts.canonical import (
    CanonicalDataItem,
    CanonicalParagraph,
    CanonicalProgram,
    CanonicalSqlAccess,
    CanonicalStatement,
)
from altamira_extractor.contracts.dependencies import (
    DependencyArtifact,
    DependencyEvidence,
    ParagraphDependency,
)
from altamira_extractor.contracts.enums import (
    BranchKind,
    DependencyEvidenceRole,
    DependencyType,
    LocationKind,
    NodeLabel,
    ParseSupportStatus,
    RelationshipType,
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
from altamira_extractor.contracts.semantic_enrichment import (
    DataItemDomainTermMapping,
    DataItemSemanticTag,
    DomainTermRecord,
    ParameterColumnDefinition,
    ParameterEntryRecord,
    ParameterTableRecord,
    SemanticEnrichmentArtifact,
    SemanticTagRuleMatch,
)
from altamira_extractor.pipeline.errors import SemanticGraphBuildError
from altamira_extractor.pipeline.semantic_graph_builder import build_semantic_graph

VALID_HASH = "a" * 64
PROGRAM_SOURCE_HASH = "b" * 64
COBOL_PATH = "01-codigo/cobol/PROG1.cbl"

PROGRAM_ID = f"program::AR::OP-TRF-PROPIA::PROG1::1.0::{PROGRAM_SOURCE_HASH[:12]}"


def _ordered_unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _manifest() -> Manifest:
    return Manifest(
        schema_version="1.0",
        country=ManifestCountry(code="AR", name="Argentina"),
        application=ManifestApplication(name="Transferencias"),
        operation=ManifestOperation(logical_name="OP-TRF-PROPIA", description="Transferencia"),
        implementation=ManifestImplementation(version="1.0", entry_programs=["PROG1"]),
        source=ManifestSource(format=SourceFormat.FIXED, encoding="UTF-8"),
        parameter_tables=[],
    )


def _inventory() -> Inventory:
    return Inventory(run_id="run-1", source_package_hash=VALID_HASH, manifest=_manifest(), files=[])


def _data_item(
    name: str, *, qualified_name: str | None = None, pic: str | None = None
) -> CanonicalDataItem:
    return CanonicalDataItem(
        name=name,
        qualified_name=qualified_name or name,
        level=1,
        pic=pic,
        source_file=COBOL_PATH,
        line=1,
        location_kind=LocationKind.EXACT,
    )


def _statement(
    statement_id: str,
    kind: StatementKind,
    *,
    parent_statement_id: str | None = None,
    branch_kind: BranchKind | None = None,
    branch_condition: str | None = None,
    expression: str | None = None,
    normalized_expression: str | None = None,
    operands: list[str] | None = None,
    variables_read: list[str] | None = None,
    variables_written: list[str] | None = None,
    target_data_items: list[str] | None = None,
    assigned_literal: str | None = None,
    target_paragraphs: list[str] | None = None,
    sql_access: list[CanonicalSqlAccess] | None = None,
    line_start: int = 1,
    line_end: int | None = None,
) -> CanonicalStatement:
    return CanonicalStatement(
        statement_id=statement_id,
        kind=kind,
        source_text="X",
        source_file=COBOL_PATH,
        line_start=line_start,
        line_end=line_end if line_end is not None else line_start,
        location_kind=LocationKind.EXACT,
        parent_statement_id=parent_statement_id,
        branch_kind=branch_kind,
        branch_condition=branch_condition,
        expression=expression,
        normalized_expression=normalized_expression,
        operands=operands or [],
        variables_read=variables_read or [],
        variables_written=variables_written or [],
        target_data_items=target_data_items or [],
        assigned_literal=assigned_literal,
        target_paragraphs=target_paragraphs or [],
        sql_access=sql_access or [],
    )


def _sql_access(
    table: str,
    operation: TableAccessOperation,
    *,
    predicate_text: str | None = None,
    host_variables: list[str] | None = None,
    line_start: int = 1,
    line_end: int | None = None,
) -> CanonicalSqlAccess:
    return CanonicalSqlAccess(
        table=table,
        operation=operation,
        predicate_text=predicate_text,
        host_variables=host_variables or [],
        source_file=COBOL_PATH,
        line_start=line_start,
        line_end=line_end if line_end is not None else line_start,
        location_kind=LocationKind.EXACT,
    )


def _paragraph(name: str, statements: list[CanonicalStatement]) -> CanonicalParagraph:
    return CanonicalParagraph(
        name=name,
        source_text=f"{name}.",
        source_file=COBOL_PATH,
        line_start=1,
        line_end=99,
        location_kind=LocationKind.EXACT,
        statements=statements,
        variables_read=_ordered_unique(v for s in statements for v in s.variables_read),
        variables_written=_ordered_unique(v for s in statements for v in s.variables_written),
        sql_access=[a for s in statements for a in s.sql_access],
    )


def _program(
    data_items: list[CanonicalDataItem], paragraphs: list[CanonicalParagraph]
) -> CanonicalProgram:
    return CanonicalProgram(
        program_name="PROG1",
        source_file=COBOL_PATH,
        source_hash=PROGRAM_SOURCE_HASH,
        source_package_hash=VALID_HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        data_items=data_items,
        paragraphs=paragraphs,
    )


def _empty_dependency_artifact(
    dependencies: list[ParagraphDependency] | None = None,
) -> DependencyArtifact:
    return DependencyArtifact(
        run_id="run-1", source_package_hash=VALID_HASH, dependencies=dependencies or []
    )


def _empty_enrichment_artifact(**overrides: object) -> SemanticEnrichmentArtifact:
    defaults: dict[str, object] = {
        "run_id": "run-1",
        "source_package_hash": VALID_HASH,
        "semantic_tags_config_hash": VALID_HASH,
        "domain_glossary_config_hash": VALID_HASH,
    }
    defaults.update(overrides)
    return SemanticEnrichmentArtifact(**defaults)  # type: ignore[arg-type]


def _build(
    programs: list[CanonicalProgram],
    *,
    dependency_artifact: DependencyArtifact | None = None,
    enrichment_artifact: SemanticEnrichmentArtifact | None = None,
):
    return build_semantic_graph(
        inventory=_inventory(),
        programs=programs,
        dependency_artifact=dependency_artifact or _empty_dependency_artifact(),
        enrichment_artifact=enrichment_artifact or _empty_enrichment_artifact(),
        source_package_hash=VALID_HASH,
    )


def _node(graph, node_id: str):
    return next(n for n in graph.nodes if n.id == node_id)


def _relationships(
    graph, rel_type: RelationshipType, from_id: str | None = None, to_id: str | None = None
):
    result = [r for r in graph.relationships if r.type == rel_type]
    if from_id is not None:
        result = [r for r in result if r.from_id == from_id]
    if to_id is not None:
        result = [r for r in result if r.to_id == to_id]
    return result


# --- Jerarquia D1 ---


def test_builds_hierarchy_nodes_and_relationships() -> None:
    graph = _build([_program([], [])])

    country = _node(graph, "country::AR")
    assert country.labels == [NodeLabel.COUNTRY]
    assert country.properties["code"] == "AR"
    assert country.properties["source_package_hash"] == VALID_HASH

    application = _node(graph, "application::AR::TRANSFERENCIAS")
    assert application.properties["country_code"] == "AR"

    operation = _node(graph, "operation::AR::TRANSFERENCIAS::OP-TRF-PROPIA")
    assert operation.properties["logical_name"] == "OP-TRF-PROPIA"

    program = _node(graph, PROGRAM_ID)
    assert program.labels == [NodeLabel.PROGRAM]
    assert program.properties["version"] == "1.0"
    assert program.properties["source_package_hash"] == VALID_HASH

    assert _relationships(graph, RelationshipType.HAS_APPLICATION, "country::AR", application.id)
    assert _relationships(graph, RelationshipType.HAS_OPERATION, application.id, operation.id)
    assert _relationships(graph, RelationshipType.EXECUTES_VIA, operation.id, PROGRAM_ID)


def test_builds_paragraph_node_and_contains() -> None:
    program = _program([], [_paragraph("MAIN-PARA", [])])
    graph = _build([program])

    para_id = f"{PROGRAM_ID}::paragraph::MAIN-PARA"
    paragraph_node = _node(graph, para_id)
    assert paragraph_node.labels == [NodeLabel.PARAGRAPH]
    assert paragraph_node.properties["name"] == "MAIN-PARA"
    assert _relationships(graph, RelationshipType.CONTAINS, PROGRAM_ID, para_id)


# --- DataItem sin/con semantic_tag ---


def test_data_item_without_tag_has_null_semantic_properties() -> None:
    program = _program([_data_item("WS-UNTAGGED")], [])
    graph = _build([program])

    item = _node(graph, f"{PROGRAM_ID}::data::WS-UNTAGGED")
    assert item.properties["semantic_tag"] is None
    assert item.properties["semantic_confidence"] is None
    assert item.properties["semantic_evidence_json"] is None


def test_data_item_with_tag_has_semantic_properties_populated() -> None:
    program = _program([_data_item("WS-IMPORTE")], [])
    item_id = f"{PROGRAM_ID}::data::WS-IMPORTE"
    tag = DataItemSemanticTag(
        data_item_id=item_id,
        program_id=PROGRAM_ID,
        source_file=COBOL_PATH,
        original_name="WS-IMPORTE",
        qualified_name="WS-IMPORTE",
        semantic_tag="amount",
        semantic_confidence=0.8,
        evidence=[SemanticTagRuleMatch(rule_id="r1", tag="amount", base_confidence=0.8)],
    )
    enrichment = _empty_enrichment_artifact(data_item_tags=[tag])
    graph = _build([program], enrichment_artifact=enrichment)

    item = _node(graph, item_id)
    assert item.properties["semantic_tag"] == "amount"
    assert item.properties["semantic_confidence"] == 0.8
    evidence = json.loads(item.properties["semantic_evidence_json"])
    assert evidence[0]["rule_id"] == "r1"


def test_orphan_data_item_tag_reference_is_fatal() -> None:
    program = _program([_data_item("WS-REAL")], [])
    tag = DataItemSemanticTag(
        data_item_id=f"{PROGRAM_ID}::data::WS-DOES-NOT-EXIST",
        program_id=PROGRAM_ID,
        source_file=COBOL_PATH,
        original_name="WS-DOES-NOT-EXIST",
        qualified_name="WS-DOES-NOT-EXIST",
        semantic_tag="amount",
        semantic_confidence=0.8,
        evidence=[SemanticTagRuleMatch(rule_id="r1", tag="amount", base_confidence=0.8)],
    )
    enrichment = _empty_enrichment_artifact(data_item_tags=[tag])
    with pytest.raises(SemanticGraphBuildError):
        _build([program], enrichment_artifact=enrichment)


# --- Decision / LEADS_TO ---


def test_if_without_leads_to_still_creates_decision_node() -> None:
    if_stmt = _statement(
        "S1", StatementKind.IF, expression="WS-X > 0", line_start=10, line_end=12
    )
    program = _program([], [_paragraph("MAIN-PARA", [if_stmt])])
    graph = _build([program])

    para_id = f"{PROGRAM_ID}::paragraph::MAIN-PARA"
    decision_id = f"{para_id}::decision::10::1"
    decision = _node(graph, decision_id)
    assert decision.labels == [NodeLabel.DECISION]
    assert decision.properties["expression"] == "WS-X > 0"
    assert decision.properties["outcome_code"] is None
    assert decision.properties["rule_type"] is None
    assert _relationships(graph, RelationshipType.HAS_DECISION, para_id, decision_id)
    assert _relationships(graph, RelationshipType.LEADS_TO, decision_id) == []


def test_evaluate_creates_decision_node() -> None:
    evaluate_stmt = _statement(
        "S1", StatementKind.EVALUATE, expression="WS-TIPO", line_start=20, line_end=25
    )
    program = _program([], [_paragraph("MAIN-PARA", [evaluate_stmt])])
    graph = _build([program])

    para_id = f"{PROGRAM_ID}::paragraph::MAIN-PARA"
    decision = _node(graph, f"{para_id}::decision::20::1")
    assert decision.labels == [NodeLabel.DECISION]


def test_direct_assignment_generates_leads_to() -> None:
    if_stmt = _statement("S1", StatementKind.IF, expression="WS-X > 0", line_start=10)
    then_stmt = _statement(
        "S2",
        StatementKind.MOVE,
        parent_statement_id="S1",
        branch_kind=BranchKind.THEN,
        branch_condition="WS-X > 0",
        assigned_literal="R001",
        target_data_items=["WS-COD"],
        line_start=11,
    )
    program = _program(
        [_data_item("WS-COD")], [_paragraph("MAIN-PARA", [if_stmt, then_stmt])]
    )
    graph = _build([program])

    para_id = f"{PROGRAM_ID}::paragraph::MAIN-PARA"
    decision_id = f"{para_id}::decision::10::1"
    item_id = f"{PROGRAM_ID}::data::WS-COD"
    leads_to = _relationships(graph, RelationshipType.LEADS_TO, decision_id, item_id)
    assert len(leads_to) == 1
    assert leads_to[0].properties["assigned_literal"] == "R001"
    assert leads_to[0].properties["branch_kind"] == "THEN"
    assert leads_to[0].properties["statement_id"] == "S2"

    decision = _node(graph, decision_id)
    assert decision.properties["outcome_code"] == "R001"


def test_assignment_in_nested_decision_belongs_only_to_nearest_decision() -> None:
    outer_if = _statement("S1", StatementKind.IF, expression="A", line_start=10)
    outer_then = _statement(
        "S2",
        StatementKind.MOVE,
        parent_statement_id="S1",
        branch_kind=BranchKind.THEN,
        assigned_literal="R001",
        target_data_items=["WS-COD"],
        line_start=11,
    )
    inner_if = _statement(
        "S3",
        StatementKind.IF,
        parent_statement_id="S1",
        branch_kind=BranchKind.ELSE,
        expression="B",
        line_start=15,
    )
    inner_then = _statement(
        "S4",
        StatementKind.MOVE,
        parent_statement_id="S3",
        branch_kind=BranchKind.THEN,
        assigned_literal="R002",
        target_data_items=["WS-COD"],
        line_start=16,
    )
    program = _program(
        [_data_item("WS-COD")],
        [_paragraph("MAIN-PARA", [outer_if, outer_then, inner_if, inner_then])],
    )
    graph = _build([program])

    para_id = f"{PROGRAM_ID}::paragraph::MAIN-PARA"
    outer_decision_id = f"{para_id}::decision::10::1"
    inner_decision_id = f"{para_id}::decision::15::2"
    item_id = f"{PROGRAM_ID}::data::WS-COD"

    outer_leads_to = _relationships(graph, RelationshipType.LEADS_TO, outer_decision_id, item_id)
    inner_leads_to = _relationships(graph, RelationshipType.LEADS_TO, inner_decision_id, item_id)

    assert len(outer_leads_to) == 1
    assert outer_leads_to[0].properties["assigned_literal"] == "R001"
    assert len(inner_leads_to) == 1
    assert inner_leads_to[0].properties["assigned_literal"] == "R002"
    # La asignación anidada no se duplica hacia la decisión externa.
    outer_statement_id = outer_leads_to[0].properties["statement_id"]
    inner_statement_id = inner_leads_to[0].properties["statement_id"]
    assert outer_statement_id != inner_statement_id


def test_outcome_code_none_with_multiple_distinct_literals() -> None:
    if_stmt = _statement("S1", StatementKind.IF, expression="A", line_start=10)
    then_stmt = _statement(
        "S2",
        StatementKind.MOVE,
        parent_statement_id="S1",
        branch_kind=BranchKind.THEN,
        assigned_literal="R001",
        target_data_items=["WS-COD"],
        line_start=11,
    )
    else_stmt = _statement(
        "S3",
        StatementKind.MOVE,
        parent_statement_id="S1",
        branch_kind=BranchKind.ELSE,
        assigned_literal="R002",
        target_data_items=["WS-COD"],
        line_start=12,
    )
    program = _program(
        [_data_item("WS-COD")], [_paragraph("MAIN-PARA", [if_stmt, then_stmt, else_stmt])]
    )
    graph = _build([program])

    decision = _node(graph, f"{PROGRAM_ID}::paragraph::MAIN-PARA::decision::10::1")
    assert decision.properties["outcome_code"] is None


def test_leads_to_ambiguous_target_produces_warning_no_relationship() -> None:
    if_stmt = _statement("S1", StatementKind.IF, expression="A", line_start=10)
    then_stmt = _statement(
        "S2",
        StatementKind.MOVE,
        parent_statement_id="S1",
        branch_kind=BranchKind.THEN,
        assigned_literal="R001",
        target_data_items=["WS-COD"],
        line_start=11,
    )
    # Dos DataItem con el mismo nombre simple: WS-COD queda ambiguo.
    program = _program(
        [
            _data_item("WS-COD", qualified_name="GROUP-A.WS-COD"),
            _data_item("WS-COD", qualified_name="GROUP-B.WS-COD"),
        ],
        [_paragraph("MAIN-PARA", [if_stmt, then_stmt])],
    )
    graph = _build([program])

    assert _relationships(graph, RelationshipType.LEADS_TO) == []
    assert any("ambigua" in w for w in graph.warnings)


def test_two_leads_to_same_decision_and_data_item_stay_separate_and_ordered() -> None:
    # EVALUATE con dos WHEN que asignan literales DISTINTOS al MISMO
    # DataItem: (type, from_id, to_id) coincide para ambas relaciones, asi
    # que el orden final debe depender del discriminante semantico
    # (statement_id/assigned_literal), no solo de esos tres componentes.
    evaluate_stmt = _statement("S1", StatementKind.EVALUATE, expression="WS-TIPO", line_start=10)
    when_a = _statement(
        "S3",
        StatementKind.MOVE,
        parent_statement_id="S1",
        branch_kind=BranchKind.WHEN,
        assigned_literal="R002",
        target_data_items=["WS-COD"],
        line_start=12,
    )
    when_b = _statement(
        "S2",
        StatementKind.MOVE,
        parent_statement_id="S1",
        branch_kind=BranchKind.WHEN,
        assigned_literal="R001",
        target_data_items=["WS-COD"],
        line_start=11,
    )
    program = _program(
        [_data_item("WS-COD")], [_paragraph("MAIN-PARA", [evaluate_stmt, when_a, when_b])]
    )
    graph = _build([program])

    decision_id = f"{PROGRAM_ID}::paragraph::MAIN-PARA::decision::10::1"
    item_id = f"{PROGRAM_ID}::data::WS-COD"
    leads_to = _relationships(graph, RelationshipType.LEADS_TO, decision_id, item_id)
    assert len(leads_to) == 2
    literals = {r.properties["assigned_literal"] for r in leads_to}
    assert literals == {"R001", "R002"}
    statement_ids = {r.properties["statement_id"] for r in leads_to}
    assert statement_ids == {"S2", "S3"}

    # El orden en graph.relationships (no solo el subconjunto filtrado) es
    # el mismo que produce sorted() sobre la clave completa del contrato:
    # se verifica reconstruyendo el grafo desde su propio JSON estable y
    # comprobando que sigue validando (el validador exige orden total).
    from altamira_extractor.contracts.semantic_graph import SemanticGraph

    restored = SemanticGraph.model_validate_json(graph.to_stable_json())
    assert restored == graph


# --- USES ---


def test_uses_read() -> None:
    stmt = _statement("S1", StatementKind.MOVE, variables_read=["WS-X"])
    program = _program([_data_item("WS-X")], [_paragraph("MAIN-PARA", [stmt])])
    graph = _build([program])

    para_id = f"{PROGRAM_ID}::paragraph::MAIN-PARA"
    item_id = f"{PROGRAM_ID}::data::WS-X"
    rels = _relationships(graph, RelationshipType.USES, para_id, item_id)
    assert len(rels) == 1
    assert json.loads(rels[0].properties["access_modes_json"]) == ["READ"]


def test_uses_write() -> None:
    stmt = _statement("S1", StatementKind.MOVE, variables_written=["WS-X"])
    program = _program([_data_item("WS-X")], [_paragraph("MAIN-PARA", [stmt])])
    graph = _build([program])

    rels = _relationships(graph, RelationshipType.USES)
    assert json.loads(rels[0].properties["access_modes_json"]) == ["WRITE"]


def test_uses_read_and_write_consolidated_into_one_relationship() -> None:
    stmt1 = _statement("S1", StatementKind.MOVE, variables_read=["WS-X"])
    stmt2 = _statement("S2", StatementKind.MOVE, variables_written=["WS-X"])
    program = _program([_data_item("WS-X")], [_paragraph("MAIN-PARA", [stmt1, stmt2])])
    graph = _build([program])

    rels = _relationships(graph, RelationshipType.USES)
    assert len(rels) == 1
    assert json.loads(rels[0].properties["access_modes_json"]) == ["READ", "WRITE"]
    evidence = json.loads(rels[0].properties["evidence_json"])
    assert len(evidence) == 2
    assert {e["statement_id"] for e in evidence} == {"S1", "S2"}


def test_uses_ambiguous_reference_produces_warning_no_relationship() -> None:
    stmt = _statement("S1", StatementKind.MOVE, variables_read=["WS-X"])
    program = _program(
        [
            _data_item("WS-X", qualified_name="GROUP-A.WS-X"),
            _data_item("WS-X", qualified_name="GROUP-B.WS-X"),
        ],
        [_paragraph("MAIN-PARA", [stmt])],
    )
    graph = _build([program])

    assert _relationships(graph, RelationshipType.USES) == []
    assert any("ambigua" in w for w in graph.warnings)


def test_uses_unresolved_reference_produces_warning_no_relationship() -> None:
    stmt = _statement("S1", StatementKind.MOVE, variables_read=["WS-GHOST"])
    program = _program([], [_paragraph("MAIN-PARA", [stmt])])
    graph = _build([program])

    assert _relationships(graph, RelationshipType.USES) == []
    assert any("no resuelta" in w for w in graph.warnings)


# --- SQL -> READS/WRITES/UPDATES/INSERTS ---


def test_sql_unqualified_table_creates_operational_table() -> None:
    access = _sql_access("CUENTAS", TableAccessOperation.READS, predicate_text="WHERE ID = :ID")
    stmt = _statement("S1", StatementKind.EXEC_SQL, sql_access=[access])
    program = _program([], [_paragraph("MAIN-PARA", [stmt])])
    graph = _build([program])

    table_id = "table::AR::DEFAULT::CUENTAS"
    table_node = _node(graph, table_id)
    assert table_node.labels == [NodeLabel.TABLE]
    para_id = f"{PROGRAM_ID}::paragraph::MAIN-PARA"
    rels = _relationships(graph, RelationshipType.READS, para_id, table_id)
    assert len(rels) == 1
    assert rels[0].properties["sql_operation"] == "READS"
    assert rels[0].properties["predicate_text"] == "WHERE ID = :ID"


def test_sql_schema_qualified_table() -> None:
    access = _sql_access("BANKSCHEMA.CUENTAS", TableAccessOperation.UPDATES)
    stmt = _statement("S1", StatementKind.EXEC_SQL, sql_access=[access])
    program = _program([], [_paragraph("MAIN-PARA", [stmt])])
    graph = _build([program])

    table_id = "table::AR::BANKSCHEMA::CUENTAS"
    table_node = _node(graph, table_id)
    assert table_node.properties["schema_name"] == "BANKSCHEMA"
    assert table_node.properties["name"] == "CUENTAS"
    para_id = f"{PROGRAM_ID}::paragraph::MAIN-PARA"
    rels = _relationships(graph, RelationshipType.UPDATES, para_id, table_id)
    assert len(rels) == 1
    assert rels[0].properties["sql_operation"] == "UPDATES"


@pytest.mark.parametrize(
    ("operation", "relationship_type"),
    [
        (TableAccessOperation.READS, RelationshipType.READS),
        (TableAccessOperation.WRITES, RelationshipType.WRITES),
        (TableAccessOperation.UPDATES, RelationshipType.UPDATES),
        (TableAccessOperation.INSERTS, RelationshipType.INSERTS),
    ],
)
def test_sql_operation_maps_to_matching_relationship_type(
    operation: TableAccessOperation, relationship_type: RelationshipType
) -> None:
    access = _sql_access("CUENTAS", operation)
    stmt = _statement("S1", StatementKind.EXEC_SQL, sql_access=[access])
    program = _program([], [_paragraph("MAIN-PARA", [stmt])])
    graph = _build([program])

    table_id = "table::AR::DEFAULT::CUENTAS"
    para_id = f"{PROGRAM_ID}::paragraph::MAIN-PARA"
    rels = _relationships(graph, relationship_type, para_id, table_id)
    assert len(rels) == 1
    assert rels[0].properties["sql_operation"] == operation.value
    # Ninguna otra relacion SQL se crea para este acceso.
    all_sql_types = {
        RelationshipType.READS,
        RelationshipType.WRITES,
        RelationshipType.UPDATES,
        RelationshipType.INSERTS,
    }
    other_types = all_sql_types - {relationship_type}
    for other in other_types:
        assert _relationships(graph, other, para_id, table_id) == []


def test_sql_quoted_identifier_with_internal_dot() -> None:
    access = _sql_access('"WEIRD.NAME"', TableAccessOperation.READS)
    stmt = _statement("S1", StatementKind.EXEC_SQL, sql_access=[access])
    program = _program([], [_paragraph("MAIN-PARA", [stmt])])
    graph = _build([program])

    table_id = "table::AR::DEFAULT::WEIRD.NAME"
    table_node = _node(graph, table_id)
    assert table_node.properties["name"] == "WEIRD.NAME"
    assert table_node.properties["schema_name"] is None


def test_sql_repeated_access_consolidated_with_evidence() -> None:
    access1 = _sql_access("CUENTAS", TableAccessOperation.READS, line_start=10)
    access2 = _sql_access("CUENTAS", TableAccessOperation.READS, line_start=20)
    stmt1 = _statement("S1", StatementKind.EXEC_SQL, sql_access=[access1], line_start=10)
    stmt2 = _statement("S2", StatementKind.EXEC_SQL, sql_access=[access2], line_start=20)
    program = _program([], [_paragraph("MAIN-PARA", [stmt1, stmt2])])
    graph = _build([program])

    rels = _relationships(graph, RelationshipType.READS)
    assert len(rels) == 1
    evidence = json.loads(rels[0].properties["evidence_json"])
    assert len(evidence) == 2
    assert {e["statement_id"] for e in evidence} == {"S1", "S2"}
    # Primaria deterministica: la de menor line_start.
    assert rels[0].properties["line_start"] == 10


def test_no_sql_statement_label_anywhere() -> None:
    access = _sql_access("CUENTAS", TableAccessOperation.INSERTS)
    stmt = _statement("S1", StatementKind.EXEC_SQL, sql_access=[access])
    program = _program([], [_paragraph("MAIN-PARA", [stmt])])
    graph = _build([program])

    all_label_values = {label.value for node in graph.nodes for label in node.labels}
    assert "SqlStatement" not in all_label_values


# --- ParameterTable / correlacion SQL ---


def _parameter_table_record(
    name: str,
    *,
    table_id: str,
    parameter_table_id: str,
    entries: list[ParameterEntryRecord] | None = None,
) -> ParameterTableRecord:
    return ParameterTableRecord(
        table_id=table_id,
        parameter_table_id=parameter_table_id,
        name=name,
        normalized_name=name.upper(),
        country_code="AR",
        ddl_relative_path="02-parametria/ddl/X.sql",
        snapshot_relative_path="02-parametria/snapshots/X.csv",
        snapshot_date=None,
        ddl_hash=VALID_HASH,
        snapshot_hash=VALID_HASH,
        ddl_support_status=ParseSupportStatus.SUPPORTED,
        snapshot_support_status=ParseSupportStatus.SUPPORTED,
        columns=[
            ParameterColumnDefinition(
                original_name="ID", normalized_name="ID", declared_type="INTEGER", ordinal=1
            )
        ],
        entries=entries or [],
    )


def test_parameter_table_node_uses_parameter_table_id_with_dual_labels() -> None:
    parameter_table_id = f"parameter::table::AR::DEFAULT::PARAM_TRANSFER::unknown::{'c' * 12}"
    record = _parameter_table_record(
        "PARAM_TRANSFER",
        table_id="table::AR::DEFAULT::PARAM_TRANSFER",
        parameter_table_id=parameter_table_id,
    )
    enrichment = _empty_enrichment_artifact(parameter_tables=[record])
    graph = _build([_program([], [])], enrichment_artifact=enrichment)

    node = _node(graph, parameter_table_id)
    assert set(node.labels) == {NodeLabel.TABLE, NodeLabel.PARAMETER_TABLE}
    # No debe existir un segundo nodo Table con table_id para esta tabla.
    assert all(n.id != "table::AR::DEFAULT::PARAM_TRANSFER" for n in graph.nodes)


def test_has_entry_from_parameter_table_id() -> None:
    parameter_table_id = f"parameter::table::AR::DEFAULT::PARAM_TRANSFER::unknown::{'c' * 12}"
    entry = ParameterEntryRecord(
        parameter_entry_id=f"{parameter_table_id}::entry::1::{'d' * 12}",
        row_number=1,
        row_hash="e" * 64,
        raw_row={"ID": "1"},
        normalized_row={"ID": "1"},
    )
    record = _parameter_table_record(
        "PARAM_TRANSFER",
        table_id="table::AR::DEFAULT::PARAM_TRANSFER",
        parameter_table_id=parameter_table_id,
        entries=[entry],
    )
    enrichment = _empty_enrichment_artifact(parameter_tables=[record])
    graph = _build([_program([], [])], enrichment_artifact=enrichment)

    entry_node = _node(graph, entry.parameter_entry_id)
    assert entry_node.labels == [NodeLabel.PARAMETER_ENTRY]
    assert json.loads(entry_node.properties["raw_row_json"]) == {"ID": "1"}
    rels = _relationships(
        graph, RelationshipType.HAS_ENTRY, parameter_table_id, entry.parameter_entry_id
    )
    assert len(rels) == 1


def test_sql_correlated_to_parameter_table_points_to_parameter_table_id() -> None:
    parameter_table_id = f"parameter::table::AR::DEFAULT::PARAM_TRANSFER::unknown::{'c' * 12}"
    record = _parameter_table_record(
        "PARAM_TRANSFER",
        table_id="table::AR::DEFAULT::PARAM_TRANSFER",
        parameter_table_id=parameter_table_id,
    )
    enrichment = _empty_enrichment_artifact(parameter_tables=[record])

    access = _sql_access("PARAM_TRANSFER", TableAccessOperation.READS)
    stmt = _statement("S1", StatementKind.EXEC_SQL, sql_access=[access])
    program = _program([], [_paragraph("MAIN-PARA", [stmt])])
    graph = _build([program], enrichment_artifact=enrichment)

    para_id = f"{PROGRAM_ID}::paragraph::MAIN-PARA"
    rels = _relationships(graph, RelationshipType.READS, para_id, parameter_table_id)
    assert len(rels) == 1
    assert all(n.id != "table::AR::DEFAULT::PARAM_TRANSFER" for n in graph.nodes)


def test_ambiguous_unqualified_sql_reference_not_assigned_arbitrarily() -> None:
    record_a = _parameter_table_record(
        "PARM",
        table_id="table::AR::DEFAULT::PARM::a",
        parameter_table_id=f"parameter::table::AR::DEFAULT::PARM::unknown::{'a' * 12}",
    )
    record_b = _parameter_table_record(
        "PARM",
        table_id="table::AR::DEFAULT::PARM::b",
        parameter_table_id=f"parameter::table::AR::DEFAULT::PARM::unknown::{'b' * 12}",
    )
    enrichment = _empty_enrichment_artifact(parameter_tables=[record_a, record_b])

    access = _sql_access("PARM", TableAccessOperation.READS)
    stmt = _statement("S1", StatementKind.EXEC_SQL, sql_access=[access])
    program = _program([], [_paragraph("MAIN-PARA", [stmt])])
    graph = _build([program], enrichment_artifact=enrichment)

    operational_table_id = "table::AR::DEFAULT::PARM"
    assert any(n.id == operational_table_id for n in graph.nodes)
    para_id = f"{PROGRAM_ID}::paragraph::MAIN-PARA"
    rels = _relationships(graph, RelationshipType.READS, para_id, operational_table_id)
    assert len(rels) == 1
    assert any("ambigua" in w for w in graph.warnings)


# --- DomainTerm / HAS_DOMAIN_TERM ---


def _domain_term(term_id: str = "term::1.0::REQUESTED_AMOUNT") -> DomainTermRecord:
    return DomainTermRecord(
        domain_term_id=term_id,
        functional_key="requested_amount",
        normalized_functional_key="REQUESTED_AMOUNT",
        functional_name="importe solicitado",
        definition="Monto solicitado",
        entity_type="monetary_amount",
        authoritative_source="V1 controlled glossary",
        source_kind="CURATED_CONFIG",
        catalog_version="1.0",
        semantic_tags=["amount"],
    )


def test_domain_term_node_confidence_always_none() -> None:
    enrichment = _empty_enrichment_artifact(domain_terms=[_domain_term()])
    graph = _build([_program([], [])], enrichment_artifact=enrichment)

    term = _node(graph, "term::1.0::REQUESTED_AMOUNT")
    assert term.properties["confidence"] is None


def test_has_domain_term_relationship_carries_mapping_confidence() -> None:
    program = _program([_data_item("WS-IMPORTE")], [])
    item_id = f"{PROGRAM_ID}::data::WS-IMPORTE"
    mapping = DataItemDomainTermMapping(
        data_item_id=item_id,
        semantic_tag="amount",
        domain_term_id="term::1.0::REQUESTED_AMOUNT",
        derivation_rule="SEMANTIC_TAG_GLOSSARY_MATCH",
        confidence=0.8,
        evidence="semantic_tag 'amount' declarado en match.semantic_tags de term",
    )
    tag = DataItemSemanticTag(
        data_item_id=item_id,
        program_id=PROGRAM_ID,
        source_file=COBOL_PATH,
        original_name="WS-IMPORTE",
        qualified_name="WS-IMPORTE",
        semantic_tag="amount",
        semantic_confidence=0.8,
        evidence=[SemanticTagRuleMatch(rule_id="r1", tag="amount", base_confidence=0.8)],
    )
    enrichment = _empty_enrichment_artifact(
        domain_terms=[_domain_term()],
        data_item_tags=[tag],
        data_item_domain_term_mappings=[mapping],
    )
    graph = _build([program], enrichment_artifact=enrichment)

    rels = _relationships(
        graph, RelationshipType.HAS_DOMAIN_TERM, item_id, "term::1.0::REQUESTED_AMOUNT"
    )
    assert len(rels) == 1
    assert rels[0].properties["confidence"] == 0.8
    assert rels[0].properties["derivation_rule"] == "SEMANTIC_TAG_GLOSSARY_MATCH"


def test_orphan_domain_term_mapping_reference_is_fatal() -> None:
    # El programa actual ya NO tiene WS-GHOST (p. ej. quedo desincronizado
    # tras un reprocesamiento), pero el artefacto de Prompt 7 en si mismo
    # es internamente consistente (el mapping referencia un data_item_id
    # que SI existe entre sus propios data_item_tags): la unica forma de
    # detectar la desincronizacion es comparando contra el CanonicalProgram
    # actual, que es exactamente lo que hace el builder.
    program = _program([], [])
    ghost_item_id = f"{PROGRAM_ID}::data::WS-GHOST"
    tag = DataItemSemanticTag(
        data_item_id=ghost_item_id,
        program_id=PROGRAM_ID,
        source_file=COBOL_PATH,
        original_name="WS-GHOST",
        qualified_name="WS-GHOST",
        semantic_tag="amount",
        semantic_confidence=0.8,
        evidence=[SemanticTagRuleMatch(rule_id="r1", tag="amount", base_confidence=0.8)],
    )
    mapping = DataItemDomainTermMapping(
        data_item_id=ghost_item_id,
        semantic_tag="amount",
        domain_term_id="term::1.0::REQUESTED_AMOUNT",
        derivation_rule="SEMANTIC_TAG_GLOSSARY_MATCH",
        confidence=0.8,
        evidence="x",
    )
    enrichment = _empty_enrichment_artifact(
        domain_terms=[_domain_term()],
        data_item_tags=[tag],
        data_item_domain_term_mappings=[mapping],
    )
    with pytest.raises(SemanticGraphBuildError):
        _build([program], enrichment_artifact=enrichment)


# --- DependencyArtifact -> DATA_DEPENDS_ON / CONTROL_DEPENDS_ON ---


def test_data_depends_on_literal_mapping() -> None:
    program = _program(
        [], [_paragraph("PARA-A", []), _paragraph("PARA-B", [])]
    )
    from_id = f"{PROGRAM_ID}::paragraph::PARA-A"
    to_id = f"{PROGRAM_ID}::paragraph::PARA-B"
    dependency = ParagraphDependency(
        dependency_type=DependencyType.DATA_DEPENDS_ON,
        from_paragraph_id=from_id,
        to_paragraph_id=to_id,
        variables=["WS-FLAG"],
        control_construct=None,
        dependency_depth=1,
        confidence=0.8,
        derivation_rule="PARAGRAPH_WRITE_READ_QUALIFIED_MATCH",
        source_file=COBOL_PATH,
        line_start=5,
        line_end=5,
        location_kind=LocationKind.EXACT,
        source_package_hash=VALID_HASH,
        evidence=[
            DependencyEvidence(
                role=DependencyEvidenceRole.WRITER,
                statement_id="S1",
                statement_kind=StatementKind.MOVE,
                source_text="MOVE 1 TO WS-FLAG.",
                source_file=COBOL_PATH,
                line_start=5,
                line_end=5,
                location_kind=LocationKind.EXACT,
                original_variable="WS-FLAG",
                resolved_qualified_name="WS-FLAG",
            ),
            DependencyEvidence(
                role=DependencyEvidenceRole.READER,
                statement_id="S2",
                statement_kind=StatementKind.IF,
                source_text="IF WS-FLAG = 1",
                source_file=COBOL_PATH,
                line_start=8,
                line_end=8,
                location_kind=LocationKind.EXACT,
                original_variable="WS-FLAG",
                resolved_qualified_name="WS-FLAG",
            ),
        ],
    )
    dependency_artifact = _empty_dependency_artifact([dependency])
    graph = _build([program], dependency_artifact=dependency_artifact)

    rels = _relationships(graph, RelationshipType.DATA_DEPENDS_ON, from_id, to_id)
    assert len(rels) == 1
    assert json.loads(rels[0].properties["variables_json"]) == ["WS-FLAG"]
    evidence = json.loads(rels[0].properties["evidence_json"])
    assert len(evidence) == 2
    assert rels[0].properties["source_package_hash"] == VALID_HASH


def test_control_depends_on_literal_mapping() -> None:
    program = _program([], [_paragraph("PARA-A", []), _paragraph("PARA-B", [])])
    from_id = f"{PROGRAM_ID}::paragraph::PARA-A"
    to_id = f"{PROGRAM_ID}::paragraph::PARA-B"
    dependency = ParagraphDependency(
        dependency_type=DependencyType.CONTROL_DEPENDS_ON,
        from_paragraph_id=from_id,
        to_paragraph_id=to_id,
        variables=[],
        control_construct="PERFORM",
        dependency_depth=1,
        confidence=1.0,
        derivation_rule="EXPLICIT_CONTROL_TARGET",
        source_file=COBOL_PATH,
        line_start=5,
        line_end=5,
        location_kind=LocationKind.EXACT,
        source_package_hash=VALID_HASH,
        evidence=[
            DependencyEvidence(
                role=DependencyEvidenceRole.CONTROL,
                statement_id="S1",
                statement_kind=StatementKind.PERFORM,
                source_text="PERFORM PARA-B.",
                source_file=COBOL_PATH,
                line_start=5,
                line_end=5,
                location_kind=LocationKind.EXACT,
                original_target="PARA-B",
            )
        ],
    )
    dependency_artifact = _empty_dependency_artifact([dependency])
    graph = _build([program], dependency_artifact=dependency_artifact)

    rels = _relationships(graph, RelationshipType.CONTROL_DEPENDS_ON, from_id, to_id)
    assert len(rels) == 1
    assert rels[0].properties["control_construct"] == "PERFORM"


def _control_dependency(
    *, from_id: str, to_id: str, construct: str, statement_id: str
) -> ParagraphDependency:
    return ParagraphDependency(
        dependency_type=DependencyType.CONTROL_DEPENDS_ON,
        from_paragraph_id=from_id,
        to_paragraph_id=to_id,
        variables=[],
        control_construct=construct,
        dependency_depth=1,
        confidence=1.0,
        derivation_rule="EXPLICIT_CONTROL_TARGET",
        source_file=COBOL_PATH,
        line_start=5,
        line_end=5,
        location_kind=LocationKind.EXACT,
        source_package_hash=VALID_HASH,
        evidence=[
            DependencyEvidence(
                role=DependencyEvidenceRole.CONTROL,
                statement_id=statement_id,
                statement_kind=(
                    StatementKind.PERFORM if construct == "PERFORM" else StatementKind.GO_TO
                ),
                source_text=f"{construct} PARA-B.",
                source_file=COBOL_PATH,
                line_start=5,
                line_end=5,
                location_kind=LocationKind.EXACT,
                original_target="PARA-B",
            )
        ],
    )


def test_go_to_and_perform_between_same_paragraphs_stay_separate_and_ordered() -> None:
    # (type, from_id, to_id) coincide para GO_TO y PERFORM hacia el mismo
    # destino: deben permanecer como dos relaciones CONTROL_DEPENDS_ON
    # distintas, distinguidas por control_construct, en orden determinista.
    program = _program([], [_paragraph("PARA-A", []), _paragraph("PARA-B", [])])
    from_id = f"{PROGRAM_ID}::paragraph::PARA-A"
    to_id = f"{PROGRAM_ID}::paragraph::PARA-B"
    go_to = _control_dependency(from_id=from_id, to_id=to_id, construct="GO_TO", statement_id="S1")
    perform = _control_dependency(
        from_id=from_id, to_id=to_id, construct="PERFORM", statement_id="S2"
    )
    # DependencyArtifact exige orden deterministico propio: GO_TO < PERFORM.
    dependency_artifact = _empty_dependency_artifact([go_to, perform])
    graph = _build([program], dependency_artifact=dependency_artifact)

    rels = _relationships(graph, RelationshipType.CONTROL_DEPENDS_ON, from_id, to_id)
    assert len(rels) == 2
    constructs = [r.properties["control_construct"] for r in rels]
    assert constructs == sorted(constructs)
    assert set(constructs) == {"GO_TO", "PERFORM"}


def test_orphan_paragraph_dependency_reference_is_fatal() -> None:
    program = _program([], [_paragraph("PARA-A", [])])
    dependency = ParagraphDependency(
        dependency_type=DependencyType.CONTROL_DEPENDS_ON,
        from_paragraph_id=f"{PROGRAM_ID}::paragraph::PARA-A",
        to_paragraph_id=f"{PROGRAM_ID}::paragraph::DOES-NOT-EXIST",
        variables=[],
        control_construct="PERFORM",
        dependency_depth=1,
        confidence=1.0,
        derivation_rule="EXPLICIT_CONTROL_TARGET",
        source_file=COBOL_PATH,
        line_start=5,
        line_end=5,
        location_kind=LocationKind.EXACT,
        source_package_hash=VALID_HASH,
        evidence=[
            DependencyEvidence(
                role=DependencyEvidenceRole.CONTROL,
                statement_id="S1",
                statement_kind=StatementKind.PERFORM,
                source_text="PERFORM X.",
                source_file=COBOL_PATH,
                line_start=5,
                line_end=5,
                location_kind=LocationKind.EXACT,
                original_target="X",
            )
        ],
    )
    dependency_artifact = _empty_dependency_artifact([dependency])
    with pytest.raises(SemanticGraphBuildError):
        _build([program], dependency_artifact=dependency_artifact)


# --- Propiedades: sin dicts/listas anidadas, source_package_hash presente ---


def _all_properties(graph) -> list[dict[str, object]]:
    return [node.properties for node in graph.nodes] + [
        rel.properties for rel in graph.relationships
    ]


def test_no_nested_dicts_or_lists_in_properties() -> None:
    if_stmt = _statement("S1", StatementKind.IF, expression="A", line_start=10)
    then_stmt = _statement(
        "S2",
        StatementKind.MOVE,
        parent_statement_id="S1",
        branch_kind=BranchKind.THEN,
        assigned_literal="R001",
        target_data_items=["WS-COD"],
        line_start=11,
    )
    access = _sql_access("CUENTAS", TableAccessOperation.READS, host_variables=["WS-ID"])
    sql_stmt = _statement("S3", StatementKind.EXEC_SQL, sql_access=[access])
    program = _program(
        [_data_item("WS-COD")],
        [_paragraph("MAIN-PARA", [if_stmt, then_stmt, sql_stmt])],
    )
    dependency = ParagraphDependency(
        dependency_type=DependencyType.CONTROL_DEPENDS_ON,
        from_paragraph_id=f"{PROGRAM_ID}::paragraph::MAIN-PARA",
        to_paragraph_id=f"{PROGRAM_ID}::paragraph::MAIN-PARA-2",
        variables=[],
        control_construct="PERFORM",
        dependency_depth=1,
        confidence=1.0,
        derivation_rule="EXPLICIT_CONTROL_TARGET",
        source_file=COBOL_PATH,
        line_start=5,
        line_end=5,
        location_kind=LocationKind.EXACT,
        source_package_hash=VALID_HASH,
        evidence=[
            DependencyEvidence(
                role=DependencyEvidenceRole.CONTROL,
                statement_id="S9",
                statement_kind=StatementKind.PERFORM,
                source_text="PERFORM X.",
                source_file=COBOL_PATH,
                line_start=5,
                line_end=5,
                location_kind=LocationKind.EXACT,
                original_target="X",
            )
        ],
    )
    program2 = _program([], [_paragraph("MAIN-PARA-2", [])])
    graph = _build(
        [program, program2], dependency_artifact=_empty_dependency_artifact([dependency])
    )

    for properties in _all_properties(graph):
        for key, value in properties.items():
            if isinstance(value, dict | list):
                pytest.fail(f"property {key!r} contiene un dict/list anidado: {value!r}")


def test_source_package_hash_present_on_all_nodes_and_relationships() -> None:
    program = _program([_data_item("WS-X")], [_paragraph("MAIN-PARA", [])])
    graph = _build([program])

    for properties in _all_properties(graph):
        assert "source_package_hash" in properties


def test_relative_paths_only_in_semantic_path_properties() -> None:
    program = _program([_data_item("WS-X")], [_paragraph("MAIN-PARA", [])])
    graph = _build([program])

    for node in graph.nodes:
        for key in ("source_file", "ddl_file", "snapshot_file"):
            value = node.properties.get(key)
            if value is not None:
                assert not value.startswith("/")
                assert ":\\" not in value


# --- Orden deterministico total, independiente del orden de los inputs ---


def test_output_identical_regardless_of_data_item_order() -> None:
    items_forward = [_data_item("WS-A"), _data_item("WS-B"), _data_item("WS-C")]
    items_reversed = list(reversed(items_forward))

    graph_forward = _build([_program(items_forward, [])])
    graph_reversed = _build([_program(items_reversed, [])])

    assert graph_forward.to_stable_json() == graph_reversed.to_stable_json()


def test_output_identical_regardless_of_paragraph_order() -> None:
    paragraphs_forward = [
        _paragraph("PARA-A", []),
        _paragraph("PARA-B", []),
        _paragraph("PARA-C", []),
    ]
    paragraphs_reversed = list(reversed(paragraphs_forward))

    graph_forward = _build([_program([], paragraphs_forward)])
    graph_reversed = _build([_program([], paragraphs_reversed)])

    assert graph_forward.to_stable_json() == graph_reversed.to_stable_json()


def _independent_program(name: str, source_hash: str) -> CanonicalProgram:
    return CanonicalProgram(
        program_name=name,
        source_file=f"01-codigo/cobol/{name}.cbl",
        source_hash=source_hash,
        source_package_hash=VALID_HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        data_items=[],
        paragraphs=[_paragraph("MAIN-PARA", [])],
    )


def test_output_identical_regardless_of_program_list_order() -> None:
    program_a = _independent_program("PROGA", "c" * 64)
    program_b = _independent_program("PROGB", "d" * 64)

    graph_forward = _build([program_a, program_b])
    graph_reversed = _build([program_b, program_a])

    assert graph_forward.to_stable_json() == graph_reversed.to_stable_json()


def test_output_identical_regardless_of_leads_to_branch_order() -> None:
    evaluate_stmt = _statement("S1", StatementKind.EVALUATE, expression="WS-TIPO", line_start=10)
    when_a = _statement(
        "S2",
        StatementKind.MOVE,
        parent_statement_id="S1",
        branch_kind=BranchKind.WHEN,
        assigned_literal="R001",
        target_data_items=["WS-COD"],
        line_start=11,
    )
    when_b = _statement(
        "S3",
        StatementKind.MOVE,
        parent_statement_id="S1",
        branch_kind=BranchKind.WHEN,
        assigned_literal="R002",
        target_data_items=["WS-COD"],
        line_start=12,
    )
    program_forward = _program(
        [_data_item("WS-COD")], [_paragraph("MAIN-PARA", [evaluate_stmt, when_a, when_b])]
    )
    program_reversed = _program(
        [_data_item("WS-COD")], [_paragraph("MAIN-PARA", [evaluate_stmt, when_b, when_a])]
    )

    graph_forward = _build([program_forward])
    graph_reversed = _build([program_reversed])

    assert graph_forward.to_stable_json() == graph_reversed.to_stable_json()
