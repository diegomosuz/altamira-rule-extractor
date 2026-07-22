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
