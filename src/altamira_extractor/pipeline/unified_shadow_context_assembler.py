"""Ensamblador PURO de un `ContextPackage` shadow (Fase 13 Parte 6,
`feat/unified-shadow-downstream-pipeline`).

Construye un `ContextPackage` REAL y valido (contrato productivo,
NUNCA deformado) a partir de una `ShadowGroupContextView` (Parte 5) y
`SemanticGraph` (`04-semantic-graph.json`, YA CARGADO por el
servicio) -- localiza Program/Paragraph/Decision por IGUALDAD EXACTA
de propiedades (`name`/`outcome_code`), exactamente el mismo principio
que las queries Q1-Q7 productivas, pero contra el archivo ya
persistido en vez de una transaccion Neo4j en vivo (el ejecutor de
Fase 13, Parte 9, es puro: sin Neo4j).

Solo D1 (scope), D2 (code_slice) y D4 (decision) se derivan con
fidelidad real desde `SemanticGraph`. D3 (data_context), D5 (effects),
D6 (batch_context) y D7 (domain_glossary) quedan VACIOS pero VALIDOS
(el contrato productivo permite explicitamente listas vacias en estas
dimensiones) -- Fase 13 nunca completa evidence desde texto aproximado
ni fabrica una tabla/efecto/glosario que no pueda verificar.

Un grupo cuyos members no comparten un unico `paragraph` (nunca se
"elige" uno), o cuyo Program/Paragraph/Decision correspondiente no
existe en `SemanticGraph`, produce `ContextAssemblyError` -- aislado
por grupo (Parte 9), nunca fabricado ni propagado a otros grupos.

Puro: sin filesystem, sin Neo4j, sin LLM, nunca muta sus argumentos."""

from __future__ import annotations

from ..contracts.context_package import (
    BatchContext,
    CodeSliceEntry,
    Completeness,
    ContextPackage,
    ContextPackageCandidate,
    ContextPackageDecision,
    ContextPackageOperation,
    ContextPackageScope,
    DataContext,
    Effects,
    EvidenceEntry,
)
from ..contracts.enums import (
    BatchContextStatus,
    CandidateStatus,
    CompletenessStatus,
    InclusionReason,
    NodeLabel,
    RelationshipType,
)
from ..contracts.semantic_graph import GraphNode, SemanticGraph
from .unified_shadow_context_adapter import ShadowGroupContextView


class ContextAssemblyError(Exception):
    """Fallo AISLADO de ensamblar el `ContextPackage` de UN grupo --
    nunca afecta a otros grupos (Fase 13 Parte 6/9)."""


def _find_program_node(graph: SemanticGraph, *, program_name: str) -> GraphNode:
    candidates = [
        node
        for node in graph.nodes
        if NodeLabel.PROGRAM in node.labels and node.properties.get("name") == program_name
    ]
    if len(candidates) != 1:
        raise ContextAssemblyError(
            f"no se encontro exactamente un Program con name={program_name!r} en "
            f"SemanticGraph ({len(candidates)} encontrados)"
        )
    return candidates[0]


def _find_hierarchy(
    graph: SemanticGraph, *, program_node: GraphNode
) -> tuple[str, str, str, str | None]:
    """`(country_code, application_name, operation_logical_name,
    operation_description)` siguiendo `EXECUTES_VIA` <- Operation,
    `HAS_OPERATION` <- Application, `HAS_APPLICATION` <- Country --
    lookup por ID exacto de relacion, nunca por semejanza textual."""
    operation_rel = next(
        (
            r
            for r in graph.relationships
            if r.type == RelationshipType.EXECUTES_VIA and r.to_id == program_node.id
        ),
        None,
    )
    if operation_rel is None:
        raise ContextAssemblyError(f"Program {program_node.id!r} no tiene relacion EXECUTES_VIA")
    operation_node = next((n for n in graph.nodes if n.id == operation_rel.from_id), None)
    if operation_node is None:
        raise ContextAssemblyError(
            f"Operation {operation_rel.from_id!r} no existe en SemanticGraph"
        )

    application_rel = next(
        (
            r
            for r in graph.relationships
            if r.type == RelationshipType.HAS_OPERATION and r.to_id == operation_node.id
        ),
        None,
    )
    if application_rel is None:
        raise ContextAssemblyError(
            f"Operation {operation_node.id!r} no tiene relacion HAS_OPERATION"
        )
    application_node = next((n for n in graph.nodes if n.id == application_rel.from_id), None)
    if application_node is None:
        raise ContextAssemblyError(
            f"Application {application_rel.from_id!r} no existe en SemanticGraph"
        )

    country_rel = next(
        (
            r
            for r in graph.relationships
            if r.type == RelationshipType.HAS_APPLICATION and r.to_id == application_node.id
        ),
        None,
    )
    if country_rel is None:
        raise ContextAssemblyError(
            f"Application {application_node.id!r} no tiene relacion HAS_APPLICATION"
        )
    country_node = next((n for n in graph.nodes if n.id == country_rel.from_id), None)
    if country_node is None:
        raise ContextAssemblyError(f"Country {country_rel.from_id!r} no existe en SemanticGraph")

    country_code = country_node.properties.get("code")
    application_name = application_node.properties.get("name")
    operation_logical_name = operation_node.properties.get("logical_name")
    operation_description = operation_node.properties.get("description")
    if (
        not isinstance(country_code, str)
        or not isinstance(application_name, str)
        or not isinstance(operation_logical_name, str)
    ):
        raise ContextAssemblyError(
            "jerarquia country/application/operation incompleta en SemanticGraph"
        )
    return (
        country_code,
        application_name,
        operation_logical_name,
        operation_description if isinstance(operation_description, str) else None,
    )


def _find_paragraph_node(
    graph: SemanticGraph, *, program_node: GraphNode, paragraph_name: str
) -> GraphNode:
    contains_ids = {
        r.to_id
        for r in graph.relationships
        if r.type == RelationshipType.CONTAINS and r.from_id == program_node.id
    }
    candidates = [
        node
        for node in graph.nodes
        if node.id in contains_ids
        and NodeLabel.PARAGRAPH in node.labels
        and node.properties.get("name") == paragraph_name
    ]
    if len(candidates) != 1:
        raise ContextAssemblyError(
            f"no se encontro exactamente un Paragraph name={paragraph_name!r} bajo "
            f"Program {program_node.id!r} ({len(candidates)} encontrados)"
        )
    return candidates[0]


def _find_decision_node(
    graph: SemanticGraph, *, paragraph_node: GraphNode, outcome_code: str
) -> GraphNode:
    decision_ids = {
        r.to_id
        for r in graph.relationships
        if r.type == RelationshipType.HAS_DECISION and r.from_id == paragraph_node.id
    }
    candidates = [
        node
        for node in graph.nodes
        if node.id in decision_ids
        and NodeLabel.DECISION in node.labels
        and node.properties.get("outcome_code") == outcome_code
    ]
    if len(candidates) != 1:
        raise ContextAssemblyError(
            f"no se encontro exactamente una Decision con outcome_code={outcome_code!r} bajo "
            f"Paragraph {paragraph_node.id!r} ({len(candidates)} encontrados)"
        )
    return candidates[0]


def assemble_shadow_context_package(
    view: ShadowGroupContextView,
    *,
    semantic_graph: SemanticGraph,
    source_package_hash: str,
) -> ContextPackage:
    """Punto de entrada puro. Lanza `ContextAssemblyError` (aislado por
    grupo) si el grupo no tiene un unico `paragraph` unanime entre
    members, o si Program/Paragraph/Decision correspondiente no existe
    en `SemanticGraph` -- NUNCA fabrica un valor ausente, NUNCA elige
    un `paragraph`/member "ganador" cuando hay mas de uno."""
    if view.target is None or view.output_literal is None:
        raise ContextAssemblyError(
            f"grupo {view.group_id!r}: target/output_literal ausentes, no se puede "
            "ensamblar D4 sin fabricar un valor"
        )
    if len(view.paragraphs) != 1:
        raise ContextAssemblyError(
            f"grupo {view.group_id!r}: se requiere exactamente un paragraph unanime entre "
            f"members ({len(view.paragraphs)} distintos) para D1/D2/D4 sin elegir un 'ganador'"
        )
    if not view.evidence_ids:
        raise ContextAssemblyError(
            f"grupo {view.group_id!r}: cero evidence_ids, no se puede construir D-evidence "
            "(ContextPackage.evidence exige al menos una entrada)"
        )
    paragraph_name = view.paragraphs[0]

    program_node = _find_program_node(semantic_graph, program_name=view.program)
    country_code, application_name, operation_logical_name, operation_description = _find_hierarchy(
        semantic_graph, program_node=program_node
    )
    paragraph_node = _find_paragraph_node(
        semantic_graph, program_node=program_node, paragraph_name=paragraph_name
    )
    decision_node = _find_decision_node(
        semantic_graph, paragraph_node=paragraph_node, outcome_code=view.output_literal
    )

    program_version = program_node.properties.get("version")
    paragraph_source_file = paragraph_node.properties.get("source_file")
    paragraph_source_text = paragraph_node.properties.get("source_text")
    paragraph_line_start = paragraph_node.properties.get("line_start")
    paragraph_line_end = paragraph_node.properties.get("line_end")
    decision_expression = decision_node.properties.get("expression")
    decision_normalized = decision_node.properties.get("normalized_expression")
    if (
        not isinstance(program_version, str)
        or not isinstance(paragraph_source_file, str)
        or not isinstance(paragraph_source_text, str)
        or not isinstance(paragraph_line_start, int)
        or not isinstance(paragraph_line_end, int)
        or not isinstance(decision_expression, str)
        or not isinstance(decision_normalized, str)
    ):
        raise ContextAssemblyError(
            f"grupo {view.group_id!r}: propiedades de Program/Paragraph/Decision "
            "incompletas en SemanticGraph"
        )

    scope = ContextPackageScope(
        country=country_code,
        application=application_name,
        operation=ContextPackageOperation(
            logical_name=operation_logical_name, description=operation_description
        ),
        program=view.program,
        program_version=program_version,
        paragraph=paragraph_name,
        source_file=paragraph_source_file,
        line_start=paragraph_line_start,
        line_end=paragraph_line_end,
        source_package_hash=source_package_hash,
    )

    code_slice = [
        CodeSliceEntry(
            paragraph_id=paragraph_node.id,
            paragraph=paragraph_name,
            source_file=paragraph_source_file,
            source_text=paragraph_source_text,
            line_start=paragraph_line_start,
            line_end=paragraph_line_end,
            inclusion_reason=InclusionReason.CANDIDATE,
            evidence_ids=list(view.evidence_ids),
        )
    ]

    decision = ContextPackageDecision(
        expression=decision_expression,
        normalized_expression=decision_normalized,
        operands=[],
        rule_type=None,
        outcome_code=view.output_literal,
        evidence_ids=list(view.evidence_ids),
    )

    evidence_entries = [
        EvidenceEntry(
            evidence_id=evidence_id,
            kind="unified_shadow_group_evidence",
            source_file=paragraph_source_file,
            source_package_hash=source_package_hash,
        )
        for evidence_id in view.evidence_ids
    ]

    candidate = ContextPackageCandidate(
        candidate_id=view.group_id,
        decision_id=decision_node.id,
        detector_id="unified-shadow-downstream-executor",
        detector_version="1.0",
        detector_score=1.0,
        status=CandidateStatus.DETECTED_CANDIDATE,
    )

    completeness = Completeness(
        D1=CompletenessStatus.COMPLETE,
        D2=CompletenessStatus.COMPLETE,
        D3=CompletenessStatus.NOT_AVAILABLE,
        D4=CompletenessStatus.COMPLETE,
        D5=CompletenessStatus.NOT_AVAILABLE,
        D6=CompletenessStatus.NOT_AVAILABLE,
        D7=CompletenessStatus.NOT_AVAILABLE,
    )

    return ContextPackage(
        candidate=candidate,
        scope=scope,
        code_slice=code_slice,
        data_context=DataContext(),
        decision=decision,
        effects=Effects(),
        batch_context=BatchContext(status=BatchContextStatus.NOT_AVAILABLE),
        domain_glossary=[],
        evidence=evidence_entries,
        completeness=completeness,
    )
