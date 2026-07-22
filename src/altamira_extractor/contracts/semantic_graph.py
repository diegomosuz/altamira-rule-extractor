"""Contrato tipado de SemanticGraph — debe ser compatible con
schemas/semantic-graph.schema.json (schema_version 2.0)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from .base import AltamiraBaseModel, Sha256Hex
from .enums import NodeLabel, RelationshipType


class GraphNode(AltamiraBaseModel):
    id: str = Field(min_length=1)
    labels: list[NodeLabel] = Field(min_length=1)
    properties: dict[str, Any] = Field(default_factory=dict)

    @field_validator("labels")
    @classmethod
    def _parameter_table_requires_table_label(cls, labels: list[NodeLabel]) -> list[NodeLabel]:
        if NodeLabel.PARAMETER_TABLE in labels and NodeLabel.TABLE not in labels:
            raise ValueError(
                "un nodo ParameterTable debe llevar tambien el label Table "
                "(docs/NEO4J_METAMODEL.md: 'Debe tener labels Table y ParameterTable')"
            )
        return labels


class GraphRelationship(AltamiraBaseModel):
    type: RelationshipType
    from_id: str = Field(min_length=1)
    to_id: str = Field(min_length=1)
    properties: dict[str, Any] = Field(default_factory=dict)


class SemanticGraph(AltamiraBaseModel):
    schema_version: Literal["2.0"] = "2.0"
    source_package_hash: Sha256Hex
    nodes: list[GraphNode] = Field(default_factory=list)
    relationships: list[GraphRelationship] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
