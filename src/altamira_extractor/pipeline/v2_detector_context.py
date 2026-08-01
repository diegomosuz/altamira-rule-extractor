"""Contexto puro de deteccion V2 (Fase 5 de la ampliacion semantica,
`feat/v2-detectors-shadow-mode`).

`V2DetectorContext` se construye UNA VEZ (`build_v2_detector_context`) y se
comparte, de solo lectura, entre todos los detectores registrados
(`v2_detector_registry.py`). Nunca accede a filesystem ni a Neo4j, nunca
modifica ninguno de los artefactos de entrada, y nunca inventa una
entidad ausente: cuando una referencia cruzada (statement<->grafo,
data item<->semantic_tag) no puede demostrarse, los indices simplemente
no la contienen -- los detectores deben tratar esa ausencia como
"no detectable", nunca como motivo para adivinar.

Cruce de identidad Neo4j<->CanonicalProgram (ver auditoria de Fase 1,
docs/V2_DETECTORS_SHADOW_MODE.md): `GraphNode.id` para Program/Paragraph/
Decision/DataItem se deriva en `identifiers.py` a partir de
`ProgramIdentity` (pais/nombre logico/version, provenientes del Manifest
via Inventory), que `CanonicalProgram` NO expone -- por eso este modulo
nunca recalcula esos IDs: los toma tal cual del `SemanticGraph` ya
persistido y los correlaciona con `CanonicalProgram`/`CanonicalParagraph`/
`CanonicalStatement` unicamente por PROPIEDADES ya almacenadas en cada
`GraphNode` (`properties['name']` para Program/Paragraph,
`properties['line_start']`/`properties['expression']` para Decision,
`properties['qualified_name']` para DataItem) y por el PREFIJO comun de
ID entre un Program y sus descendientes (`semantic_graph_builder.py`
construye cada ID de Paragraph/Decision/DataItem concatenando el ID de su
Program como prefijo -- `paragraph_id`/`data_item_id` en
`identifiers.py`)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from ..contracts.candidate import CandidateArtifact, RuleCandidate
from ..contracts.canonical import CanonicalProgram, CanonicalStatement
from ..contracts.enums import NodeLabel, RelationshipType
from ..contracts.semantic_effects import SemanticEffect, SemanticEffectsArtifact
from ..contracts.semantic_graph import GraphNode, GraphRelationship, SemanticGraph
from ..contracts.semantic_propagation import PropagatedValueFact, SemanticPropagationArtifact


@dataclass(frozen=True)
class V2DetectorContext:
    """Vista de solo lectura, construida una unica vez, sobre todos los
    artefactos que un detector V2 puede consultar."""

    canonical_programs: tuple[CanonicalProgram, ...]
    semantic_graph: SemanticGraph
    v1_candidates: CandidateArtifact
    semantic_effects: SemanticEffectsArtifact
    semantic_propagation: SemanticPropagationArtifact

    # --- indices CanonicalProgram --------------------------------------
    program_by_name: dict[str, CanonicalProgram]
    statement_by_id: dict[str, CanonicalStatement]
    statement_paragraph_name: dict[str, str]
    statement_program_name: dict[str, str]

    # --- indices SemanticEffects/SemanticPropagation --------------------
    effect_by_id: dict[str, SemanticEffect]
    fact_by_id: dict[str, PropagatedValueFact]
    effects_by_source_statement: dict[str, list[SemanticEffect]]
    facts_by_source_statement: dict[str, list[PropagatedValueFact]]
    facts_by_target: dict[tuple[str, str], list[PropagatedValueFact]]

    # --- indices SemanticGraph (Neo4j-shaped, correlacionados por
    # propiedades/prefijo de ID, nunca recalculados) ---------------------
    program_node_by_name: dict[str, GraphNode]
    paragraph_node_by_key: dict[tuple[str, str], GraphNode]
    decision_nodes_by_paragraph_key: dict[tuple[str, str], list[GraphNode]]
    decision_node_by_id: dict[str, GraphNode]
    data_item_node_by_key: dict[tuple[str, str], GraphNode]
    data_item_node_by_id: dict[str, GraphNode]
    data_item_nodes_by_semantic_tag: dict[str, list[GraphNode]]
    has_decision_relationships: list[GraphRelationship]
    leads_to_by_decision_id: dict[str, list[GraphRelationship]]
    program_name_by_paragraph_node_id: dict[str, str]

    # --- indices CandidateArtifact V1 -----------------------------------
    v1_candidates_by_id: dict[str, RuleCandidate]
    v1_candidates_by_decision_id: dict[str, list[RuleCandidate]]

    def program_name_for_v1_candidate(self, candidate: RuleCandidate) -> str | None:
        """Resuelve el `program` de un `RuleCandidate` V1 (que no lo
        expone directamente) via el prefijo de `paragraph_id` -- nunca
        recalculando el ID, solo comparando contra los `GraphNode.id` de
        Program ya cargados (ver docstring del modulo)."""
        return self.program_name_by_paragraph_node_id.get(candidate.paragraph_id)

    def decision_statement_for_node(
        self, *, program_name: str, paragraph_name: str, decision_node: GraphNode
    ) -> CanonicalStatement | None:
        """Correlaciona un nodo `Decision` del `SemanticGraph` con el
        `CanonicalStatement` (IF/EVALUATE) real que lo origino, por
        `line_start` dentro del mismo paragraph -- unica propiedad que
        `semantic_graph_builder.py` conserva sin ambiguedad util aqui
        (`expression` tambien coincide, pero `line_start` alcanza y es
        mas barato de comparar)."""
        program = self.program_by_name.get(program_name)
        if program is None:
            return None
        line_start = decision_node.properties.get("line_start")
        if line_start is None:
            return None
        for paragraph in program.paragraphs:
            if paragraph.name != paragraph_name:
                continue
            for statement in paragraph.statements:
                if statement.kind.value not in ("IF", "EVALUATE"):
                    continue
                if statement.line_start == line_start:
                    return statement
        return None

    def is_statement_under(self, *, statement_id: str, ancestor_statement_id: str) -> bool:
        """True si `statement_id` es descendiente (o el propio) de
        `ancestor_statement_id` siguiendo `parent_statement_id` -- nunca
        cruza de un paragraph a otro porque `parent_statement_id` solo
        enlaza statements del MISMO paragraph (CanonicalParagraph.statements)."""
        current: str | None = statement_id
        visited: set[str] = set()
        while current is not None:
            if current == ancestor_statement_id:
                return True
            if current in visited:
                return False
            visited.add(current)
            statement = self.statement_by_id.get(current)
            if statement is None:
                return False
            current = statement.parent_statement_id
        return False


def _index_statements(
    programs: Sequence[CanonicalProgram],
) -> tuple[dict[str, CanonicalStatement], dict[str, str], dict[str, str]]:
    statement_by_id: dict[str, CanonicalStatement] = {}
    statement_paragraph_name: dict[str, str] = {}
    statement_program_name: dict[str, str] = {}
    for program in programs:
        for paragraph in program.paragraphs:
            for statement in paragraph.statements:
                statement_by_id[statement.statement_id] = statement
                statement_paragraph_name[statement.statement_id] = paragraph.name
                statement_program_name[statement.statement_id] = program.program_name
    return statement_by_id, statement_paragraph_name, statement_program_name


def _index_effects(
    semantic_effects: SemanticEffectsArtifact,
) -> tuple[dict[str, SemanticEffect], dict[str, list[SemanticEffect]]]:
    effect_by_id: dict[str, SemanticEffect] = {}
    effects_by_source_statement: dict[str, list[SemanticEffect]] = defaultdict(list)
    for program_effects in semantic_effects.programs:
        for effect in program_effects.effects:
            effect_by_id[effect.effect_id] = effect
            effects_by_source_statement[effect.source_reference.statement_id].append(effect)
    return effect_by_id, dict(effects_by_source_statement)


def _index_facts(
    semantic_propagation: SemanticPropagationArtifact,
) -> tuple[
    dict[str, PropagatedValueFact],
    dict[str, list[PropagatedValueFact]],
    dict[tuple[str, str], list[PropagatedValueFact]],
]:
    fact_by_id: dict[str, PropagatedValueFact] = {}
    facts_by_source_statement: dict[str, list[PropagatedValueFact]] = defaultdict(list)
    facts_by_target: dict[tuple[str, str], list[PropagatedValueFact]] = defaultdict(list)
    for program_propagation in semantic_propagation.programs:
        for fact in program_propagation.facts:
            fact_by_id[fact.fact_id] = fact
            facts_by_source_statement[fact.source_reference.statement_id].append(fact)
            target_key = fact.target_qualified_name or fact.target_variable
            facts_by_target[(program_propagation.program, target_key)].append(fact)
    return fact_by_id, dict(facts_by_source_statement), dict(facts_by_target)


def _index_graph(
    semantic_graph: SemanticGraph,
) -> tuple[
    dict[str, GraphNode],
    dict[tuple[str, str], GraphNode],
    dict[tuple[str, str], list[GraphNode]],
    dict[str, GraphNode],
    dict[tuple[str, str], GraphNode],
    dict[str, GraphNode],
    dict[str, list[GraphNode]],
    list[GraphRelationship],
    dict[str, list[GraphRelationship]],
    dict[str, str],
]:
    program_node_by_name: dict[str, GraphNode] = {}
    for node in semantic_graph.nodes:
        if NodeLabel.PROGRAM in node.labels:
            name = node.properties.get("name")
            if isinstance(name, str):
                program_node_by_name[name] = node

    paragraph_node_by_key: dict[tuple[str, str], GraphNode] = {}
    paragraph_program_name_by_id: dict[str, str] = {}
    for program_name, program_node in program_node_by_name.items():
        prefix = f"{program_node.id}::paragraph::"
        for node in semantic_graph.nodes:
            if NodeLabel.PARAGRAPH not in node.labels:
                continue
            if not node.id.startswith(prefix):
                continue
            paragraph_name = node.properties.get("name")
            if isinstance(paragraph_name, str):
                paragraph_node_by_key[(program_name, paragraph_name)] = node
                paragraph_program_name_by_id[node.id] = program_name

    decision_nodes_by_paragraph_key: dict[tuple[str, str], list[GraphNode]] = defaultdict(list)
    decision_node_by_id: dict[str, GraphNode] = {}
    for (program_name, paragraph_name), paragraph_node in paragraph_node_by_key.items():
        prefix = f"{paragraph_node.id}::decision::"
        for node in semantic_graph.nodes:
            if NodeLabel.DECISION not in node.labels:
                continue
            if not node.id.startswith(prefix):
                continue
            decision_nodes_by_paragraph_key[(program_name, paragraph_name)].append(node)
            decision_node_by_id[node.id] = node

    data_item_node_by_key: dict[tuple[str, str], GraphNode] = {}
    data_item_node_by_id: dict[str, GraphNode] = {}
    data_item_nodes_by_semantic_tag: dict[str, list[GraphNode]] = defaultdict(list)
    for program_name, program_node in program_node_by_name.items():
        prefix = f"{program_node.id}::data::"
        for node in semantic_graph.nodes:
            if NodeLabel.DATA_ITEM not in node.labels:
                continue
            if not node.id.startswith(prefix):
                continue
            data_item_node_by_id[node.id] = node
            qualified_name = node.properties.get("qualified_name")
            if isinstance(qualified_name, str):
                data_item_node_by_key[(program_name, qualified_name)] = node
            semantic_tag = node.properties.get("semantic_tag")
            if isinstance(semantic_tag, str):
                data_item_nodes_by_semantic_tag[semantic_tag].append(node)

    has_decision_relationships = [
        relationship
        for relationship in semantic_graph.relationships
        if relationship.type == RelationshipType.HAS_DECISION
    ]
    leads_to_by_decision_id: dict[str, list[GraphRelationship]] = defaultdict(list)
    for relationship in semantic_graph.relationships:
        if relationship.type == RelationshipType.LEADS_TO:
            leads_to_by_decision_id[relationship.from_id].append(relationship)

    return (
        program_node_by_name,
        paragraph_node_by_key,
        dict(decision_nodes_by_paragraph_key),
        decision_node_by_id,
        data_item_node_by_key,
        data_item_node_by_id,
        dict(data_item_nodes_by_semantic_tag),
        has_decision_relationships,
        dict(leads_to_by_decision_id),
        paragraph_program_name_by_id,
    )


def build_v2_detector_context(
    *,
    canonical_programs: Sequence[CanonicalProgram],
    semantic_graph: SemanticGraph,
    v1_candidates: CandidateArtifact,
    semantic_effects: SemanticEffectsArtifact,
    semantic_propagation: SemanticPropagationArtifact,
) -> V2DetectorContext:
    """Construye `V2DetectorContext` una unica vez. Nunca lee filesystem,
    nunca accede a Neo4j, nunca modifica ninguno de los argumentos."""
    statement_by_id, statement_paragraph_name, statement_program_name = _index_statements(
        canonical_programs
    )
    effect_by_id, effects_by_source_statement = _index_effects(semantic_effects)
    fact_by_id, facts_by_source_statement, facts_by_target = _index_facts(semantic_propagation)
    (
        program_node_by_name,
        paragraph_node_by_key,
        decision_nodes_by_paragraph_key,
        decision_node_by_id,
        data_item_node_by_key,
        data_item_node_by_id,
        data_item_nodes_by_semantic_tag,
        has_decision_relationships,
        leads_to_by_decision_id,
        program_name_by_paragraph_node_id,
    ) = _index_graph(semantic_graph)

    v1_candidates_by_decision_id: dict[str, list[RuleCandidate]] = defaultdict(list)
    for candidate in v1_candidates.candidates:
        v1_candidates_by_decision_id[candidate.decision_id].append(candidate)

    return V2DetectorContext(
        canonical_programs=tuple(canonical_programs),
        semantic_graph=semantic_graph,
        v1_candidates=v1_candidates,
        semantic_effects=semantic_effects,
        semantic_propagation=semantic_propagation,
        program_by_name={program.program_name: program for program in canonical_programs},
        statement_by_id=statement_by_id,
        statement_paragraph_name=statement_paragraph_name,
        statement_program_name=statement_program_name,
        effect_by_id=effect_by_id,
        fact_by_id=fact_by_id,
        effects_by_source_statement=effects_by_source_statement,
        facts_by_source_statement=facts_by_source_statement,
        facts_by_target=facts_by_target,
        program_node_by_name=program_node_by_name,
        paragraph_node_by_key=paragraph_node_by_key,
        decision_nodes_by_paragraph_key=decision_nodes_by_paragraph_key,
        decision_node_by_id=decision_node_by_id,
        data_item_node_by_key=data_item_node_by_key,
        data_item_node_by_id=data_item_node_by_id,
        data_item_nodes_by_semantic_tag=data_item_nodes_by_semantic_tag,
        has_decision_relationships=has_decision_relationships,
        leads_to_by_decision_id=leads_to_by_decision_id,
        program_name_by_paragraph_node_id=program_name_by_paragraph_node_id,
        v1_candidates_by_id={
            candidate.candidate_id: candidate for candidate in v1_candidates.candidates
        },
        v1_candidates_by_decision_id=dict(v1_candidates_by_decision_id),
    )
