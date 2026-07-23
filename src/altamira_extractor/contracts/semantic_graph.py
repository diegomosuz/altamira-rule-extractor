"""Contrato tipado de SemanticGraph — debe ser compatible con
schemas/semantic-graph.schema.json (schema_version 2.0).

Los invariantes de este modulo (IDs unicos, referencias sin huerfanos,
endpoints permitidos por tipo de relacion, orden deterministico, ausencia
de duplicados exactos) representan el metamodelo COMPLETO documentado en
docs/NEO4J_METAMODEL.md — incluidas combinaciones no pobladas todavia por
ningun builder de V1 (p. ej. BatchJob como origen de READS/WRITES/
UPDATES/INSERTS) — para no bloquear un uso futuro valido del metamodelo
que hoy simplemente no se construye. La deduplicacion SEMANTICA
especifica de cada tipo de relacion (consolidar evidencia de accesos SQL
repetidos, distinguir GO_TO de PERFORM, etc.) es responsabilidad del
builder (`pipeline/semantic_graph_builder.py`), no de este contrato: aqui
solo se rechazan duplicados EXACTOS (mismo type/from_id/to_id/properties).
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .base import AltamiraBaseModel, Sha256Hex
from .enums import NodeLabel, RelationshipType

# Endpoints permitidos por tipo de relacion, segun el metamodelo COMPLETO
# (docs/NEO4J_METAMODEL.md secciones 1-2), no solo lo que V1 puebla. Un
# nodo puede tener varios labels (p. ej. Table+ParameterTable): basta que
# al menos uno de sus labels pertenezca al conjunto permitido.
_RELATIONSHIP_ENDPOINTS: dict[
    RelationshipType, tuple[frozenset[NodeLabel], frozenset[NodeLabel]]
] = {
    RelationshipType.HAS_APPLICATION: (
        frozenset({NodeLabel.COUNTRY}),
        frozenset({NodeLabel.APPLICATION}),
    ),
    RelationshipType.HAS_OPERATION: (
        frozenset({NodeLabel.APPLICATION}),
        frozenset({NodeLabel.OPERATION}),
    ),
    RelationshipType.EXECUTES_VIA: (
        frozenset({NodeLabel.OPERATION}),
        frozenset({NodeLabel.PROGRAM}),
    ),
    RelationshipType.CONTAINS: (
        frozenset({NodeLabel.PROGRAM}),
        frozenset({NodeLabel.PARAGRAPH}),
    ),
    RelationshipType.HAS_DECISION: (
        frozenset({NodeLabel.PARAGRAPH}),
        frozenset({NodeLabel.DECISION}),
    ),
    RelationshipType.LEADS_TO: (
        frozenset({NodeLabel.DECISION}),
        frozenset({NodeLabel.DATA_ITEM}),
    ),
    RelationshipType.USES: (
        frozenset({NodeLabel.PARAGRAPH}),
        frozenset({NodeLabel.DATA_ITEM}),
    ),
    # .claude/rules/neo4j.md: "Las relaciones READS/WRITES/UPDATES/INSERTS
    # nacen de Paragraph o BatchJob" — BatchJob no se puebla en V1, pero el
    # contrato no debe impedir su uso futuro valido.
    RelationshipType.READS: (
        frozenset({NodeLabel.PARAGRAPH, NodeLabel.BATCH_JOB}),
        frozenset({NodeLabel.TABLE}),
    ),
    RelationshipType.WRITES: (
        frozenset({NodeLabel.PARAGRAPH, NodeLabel.BATCH_JOB}),
        frozenset({NodeLabel.TABLE}),
    ),
    RelationshipType.UPDATES: (
        frozenset({NodeLabel.PARAGRAPH, NodeLabel.BATCH_JOB}),
        frozenset({NodeLabel.TABLE}),
    ),
    RelationshipType.INSERTS: (
        frozenset({NodeLabel.PARAGRAPH, NodeLabel.BATCH_JOB}),
        frozenset({NodeLabel.TABLE}),
    ),
    RelationshipType.HAS_ENTRY: (
        frozenset({NodeLabel.PARAMETER_TABLE}),
        frozenset({NodeLabel.PARAMETER_ENTRY}),
    ),
    RelationshipType.PRECEDED_BY: (
        frozenset({NodeLabel.BATCH_JOB}),
        frozenset({NodeLabel.BATCH_JOB}),
    ),
    RelationshipType.TRIGGERS: (
        frozenset({NodeLabel.BATCH_JOB}),
        frozenset({NodeLabel.BATCH_JOB}),
    ),
    RelationshipType.HAS_DOMAIN_TERM: (
        frozenset({NodeLabel.DATA_ITEM}),
        frozenset({NodeLabel.DOMAIN_TERM}),
    ),
    RelationshipType.DATA_DEPENDS_ON: (
        frozenset({NodeLabel.PARAGRAPH}),
        frozenset({NodeLabel.PARAGRAPH}),
    ),
    RelationshipType.CONTROL_DEPENDS_ON: (
        frozenset({NodeLabel.PARAGRAPH}),
        frozenset({NodeLabel.PARAGRAPH}),
    ),
}


def _canonical_properties_json(properties: dict[str, Any]) -> str:
    return json.dumps(properties, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class GraphNode(AltamiraBaseModel):
    id: str = Field(min_length=1)
    labels: list[NodeLabel] = Field(min_length=1)
    properties: dict[str, Any] = Field(default_factory=dict)

    @field_validator("labels")
    @classmethod
    def _check_labels(cls, labels: list[NodeLabel]) -> list[NodeLabel]:
        if len(labels) != len(set(labels)):
            raise ValueError("labels no puede contener duplicados")
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

    @model_validator(mode="after")
    def _check_nodes_unique_and_sorted(self) -> SemanticGraph:
        ids = [node.id for node in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("GraphNode.id duplicado")
        if ids != sorted(ids):
            raise ValueError("nodes no esta ordenado deterministicamente por id")
        return self

    @model_validator(mode="after")
    def _check_relationships(self) -> SemanticGraph:
        node_by_id = {node.id: node for node in self.nodes}
        seen_exact: set[tuple[str, str, str, str]] = set()
        sort_keys: list[tuple[str, str, str, str]] = []

        for relationship in self.relationships:
            from_node = node_by_id.get(relationship.from_id)
            to_node = node_by_id.get(relationship.to_id)
            if from_node is None:
                raise ValueError(
                    f"relacion {relationship.type.value} referencia from_id inexistente: "
                    f"{relationship.from_id!r}"
                )
            if to_node is None:
                raise ValueError(
                    f"relacion {relationship.type.value} referencia to_id inexistente: "
                    f"{relationship.to_id!r}"
                )

            allowed_from, allowed_to = _RELATIONSHIP_ENDPOINTS[relationship.type]
            if allowed_from.isdisjoint(from_node.labels):
                raise ValueError(
                    f"relacion {relationship.type.value}: from_id {relationship.from_id!r} "
                    f"(labels={[label.value for label in from_node.labels]}) no tiene un label "
                    f"de origen permitido ({sorted(label.value for label in allowed_from)})"
                )
            if allowed_to.isdisjoint(to_node.labels):
                raise ValueError(
                    f"relacion {relationship.type.value}: to_id {relationship.to_id!r} "
                    f"(labels={[label.value for label in to_node.labels]}) no tiene un label "
                    f"de destino permitido ({sorted(label.value for label in allowed_to)})"
                )

            properties_json = _canonical_properties_json(relationship.properties)
            key = (
                relationship.type.value,
                relationship.from_id,
                relationship.to_id,
                properties_json,
            )
            if key in seen_exact:
                raise ValueError(
                    f"relacion exactamente duplicada: {relationship.type.value} "
                    f"{relationship.from_id!r} -> {relationship.to_id!r}"
                )
            seen_exact.add(key)
            sort_keys.append(key)

        if sort_keys != sorted(sort_keys):
            raise ValueError("relationships no esta ordenado deterministicamente")
        return self

    @model_validator(mode="after")
    def _check_warnings(self) -> SemanticGraph:
        if len(self.warnings) != len(set(self.warnings)):
            raise ValueError("warnings contiene duplicados")
        if self.warnings != sorted(self.warnings):
            raise ValueError("warnings no esta ordenado deterministicamente")
        return self
