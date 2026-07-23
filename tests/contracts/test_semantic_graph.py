"""Tests de SemanticGraph, incluida compatibilidad con
schemas/semantic-graph.schema.json."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts import (
    GraphNode,
    GraphRelationship,
    NodeLabel,
    RelationshipType,
    SemanticGraph,
)

from .conftest import assert_matches_schema


def test_valid_semantic_graph_matches_schema(
    valid_semantic_graph: SemanticGraph, semantic_graph_schema: dict[str, Any]
) -> None:
    assert_matches_schema(valid_semantic_graph.model_dump(mode="json"), semantic_graph_schema)


def test_semantic_graph_round_trips(valid_semantic_graph: SemanticGraph) -> None:
    restored = SemanticGraph.model_validate_json(valid_semantic_graph.to_stable_json())
    assert restored == valid_semantic_graph


def test_semantic_graph_rejects_wrong_schema_version(valid_semantic_graph: SemanticGraph) -> None:
    payload = valid_semantic_graph.model_dump(mode="json")
    payload["schema_version"] = "1.0"
    with pytest.raises(ValidationError):
        SemanticGraph.model_validate(payload)


def test_semantic_graph_rejects_additional_properties(valid_semantic_graph: SemanticGraph) -> None:
    payload = valid_semantic_graph.model_dump(mode="json")
    payload["extra_top_level_field"] = True
    with pytest.raises(ValidationError):
        SemanticGraph.model_validate(payload)


def test_semantic_graph_rejects_invalid_package_hash(valid_semantic_graph: SemanticGraph) -> None:
    payload = valid_semantic_graph.model_dump(mode="json")
    payload["source_package_hash"] = "not-a-valid-hash"
    with pytest.raises(ValidationError):
        SemanticGraph.model_validate(payload)


def test_graph_node_rejects_label_not_in_metamodel() -> None:
    with pytest.raises(ValidationError):
        GraphNode(id="n1", labels=["SqlStatement"], properties={})  # type: ignore[list-item]


def test_graph_node_rejects_duplicate_labels() -> None:
    with pytest.raises(ValidationError, match="duplicados"):
        GraphNode(id="n1", labels=[NodeLabel.PROGRAM, NodeLabel.PROGRAM], properties={})


def test_graph_relationship_rejects_type_not_in_metamodel() -> None:
    with pytest.raises(ValidationError):
        GraphRelationship(type="DELETES", from_id="a", to_id="b", properties={})  # type: ignore[arg-type]


def test_parameter_table_without_table_label_is_rejected() -> None:
    with pytest.raises(ValidationError):
        GraphNode(
            id="table::AR::default::PARM01",
            labels=[NodeLabel.PARAMETER_TABLE],
            properties={},
        )


def test_parameter_table_with_table_label_is_valid() -> None:
    node = GraphNode(
        id="table::AR::default::PARM01",
        labels=[NodeLabel.TABLE, NodeLabel.PARAMETER_TABLE],
        properties={},
    )
    assert NodeLabel.TABLE in node.labels


def test_data_depends_on_relationship_is_allowed() -> None:
    relationship = GraphRelationship(
        type=RelationshipType.DATA_DEPENDS_ON, from_id="p1", to_id="p2", properties={}
    )
    assert relationship.type == RelationshipType.DATA_DEPENDS_ON


# --- Invariantes agregados para el Prompt 8 ---

PROGRAM_ID = "program::AR::op::PROG::1::abc123"
PARAGRAPH_ID = f"{PROGRAM_ID}::paragraph::PARA-1"
PARAGRAPH_ID_2 = f"{PROGRAM_ID}::paragraph::PARA-2"
TABLE_ID = "table::AR::default::PARM01"


def _program_node(**overrides: object) -> GraphNode:
    defaults: dict[str, object] = {
        "id": PROGRAM_ID,
        "labels": [NodeLabel.PROGRAM],
        "properties": {},
    }
    defaults.update(overrides)
    return GraphNode(**defaults)  # type: ignore[arg-type]


def _paragraph_node(node_id: str = PARAGRAPH_ID, **overrides: object) -> GraphNode:
    defaults: dict[str, object] = {
        "id": node_id,
        "labels": [NodeLabel.PARAGRAPH],
        "properties": {},
    }
    defaults.update(overrides)
    return GraphNode(**defaults)  # type: ignore[arg-type]


def test_duplicate_node_id_rejected() -> None:
    node = _program_node()
    with pytest.raises(ValidationError, match="GraphNode.id duplicado"):
        SemanticGraph(
            schema_version="2.0",
            source_package_hash="a" * 64,
            nodes=[node, _paragraph_node(node_id=PROGRAM_ID)],
        )


def test_orphan_relationship_endpoint_rejected() -> None:
    with pytest.raises(ValidationError, match="from_id inexistente"):
        SemanticGraph(
            schema_version="2.0",
            source_package_hash="a" * 64,
            nodes=[_paragraph_node()],
            relationships=[
                GraphRelationship(
                    type=RelationshipType.CONTAINS,
                    from_id="does-not-exist",
                    to_id=PARAGRAPH_ID,
                    properties={},
                )
            ],
        )


def test_exact_duplicate_relationship_rejected() -> None:
    rel = GraphRelationship(
        type=RelationshipType.CONTAINS, from_id=PROGRAM_ID, to_id=PARAGRAPH_ID, properties={}
    )
    with pytest.raises(ValidationError, match="exactamente duplicada"):
        SemanticGraph(
            schema_version="2.0",
            source_package_hash="a" * 64,
            nodes=[_program_node(), _paragraph_node()],
            relationships=[rel, rel],
        )


def test_parameter_table_without_table_label_rejected_inside_graph() -> None:
    with pytest.raises(ValidationError):
        GraphNode(id=TABLE_ID, labels=[NodeLabel.PARAMETER_TABLE], properties={})


def test_endpoint_label_incompatible_with_relationship_type_rejected() -> None:
    with pytest.raises(ValidationError, match="label de origen permitido"):
        SemanticGraph(
            schema_version="2.0",
            source_package_hash="a" * 64,
            nodes=[_program_node(), _paragraph_node()],
            relationships=[
                GraphRelationship(
                    # CONTAINS exige origen Program, no Paragraph.
                    type=RelationshipType.CONTAINS,
                    from_id=PARAGRAPH_ID,
                    to_id=PROGRAM_ID,
                    properties={},
                )
            ],
        )


def test_nodes_out_of_order_rejected() -> None:
    with pytest.raises(ValidationError, match="nodes no esta ordenado"):
        SemanticGraph(
            schema_version="2.0",
            source_package_hash="a" * 64,
            nodes=[_paragraph_node(node_id=PARAGRAPH_ID_2), _paragraph_node(node_id=PARAGRAPH_ID)],
        )


def test_relationships_out_of_order_rejected() -> None:
    first = GraphRelationship(
        type=RelationshipType.CONTAINS, from_id=PROGRAM_ID, to_id=PARAGRAPH_ID_2, properties={}
    )
    second = GraphRelationship(
        type=RelationshipType.CONTAINS, from_id=PROGRAM_ID, to_id=PARAGRAPH_ID, properties={}
    )
    with pytest.raises(ValidationError, match="relationships no esta ordenado"):
        SemanticGraph(
            schema_version="2.0",
            source_package_hash="a" * 64,
            nodes=[
                _program_node(),
                _paragraph_node(node_id=PARAGRAPH_ID),
                _paragraph_node(node_id=PARAGRAPH_ID_2),
            ],
            relationships=[first, second],
        )


def test_warnings_duplicated_rejected() -> None:
    with pytest.raises(ValidationError, match="warnings contiene duplicados"):
        SemanticGraph(
            schema_version="2.0",
            source_package_hash="a" * 64,
            warnings=["same", "same"],
        )


def test_warnings_unsorted_rejected() -> None:
    with pytest.raises(ValidationError, match="warnings no esta ordenado"):
        SemanticGraph(
            schema_version="2.0",
            source_package_hash="a" * 64,
            warnings=["zeta", "alpha"],
        )


def test_batch_job_allowed_as_reads_origin_even_though_unpopulated_in_v1() -> None:
    # El metamodelo completo permite BatchJob -[:READS]-> Table aunque V1
    # no construya BatchJob: el contrato no debe bloquear ese uso futuro.
    batch_job = GraphNode(id="batch::AR::CRON::JOB1", labels=[NodeLabel.BATCH_JOB], properties={})
    table = GraphNode(id=TABLE_ID, labels=[NodeLabel.TABLE], properties={})
    graph = SemanticGraph(
        schema_version="2.0",
        source_package_hash="a" * 64,
        nodes=[batch_job, table],
        relationships=[
            GraphRelationship(
                type=RelationshipType.READS, from_id=batch_job.id, to_id=table.id, properties={}
            )
        ],
    )
    assert len(graph.relationships) == 1


def test_two_relationships_differing_only_in_properties_are_both_valid_and_ordered() -> None:
    # Dos LEADS_TO entre el mismo par de nodos pero con properties
    # distintas (p. ej. dos branches distintas) no son un duplicado exacto.
    decision = GraphNode(
        id=f"{PARAGRAPH_ID}::decision::10::1", labels=[NodeLabel.DECISION], properties={}
    )
    data_item = GraphNode(
        id=f"{PROGRAM_ID}::data::WS-COD", labels=[NodeLabel.DATA_ITEM], properties={}
    )
    graph = SemanticGraph(
        schema_version="2.0",
        source_package_hash="a" * 64,
        nodes=[data_item, decision],
        relationships=[
            GraphRelationship(
                type=RelationshipType.LEADS_TO,
                from_id=decision.id,
                to_id=data_item.id,
                properties={"statement_id": "s1"},
            ),
            GraphRelationship(
                type=RelationshipType.LEADS_TO,
                from_id=decision.id,
                to_id=data_item.id,
                properties={"statement_id": "s2"},
            ),
        ],
    )
    assert len(graph.relationships) == 2
