"""Fixtures compartidas de detectores V2 en shadow mode (Fase 5 de la
ampliacion semantica, `feat/v2-detectors-shadow-mode`). NO es un modulo
de test (sin prefijo `test_`): expone builders reutilizados por
`test_v2_detector_context.py`, `test_v2_detectors.py`,
`test_v2_detector_registry.py`, `test_v2_shadow_detector.py` y
`test_v2_shadow_candidates_service.py`. Mismo patron que
`tests/api/conftest.py` (funciones auxiliares importadas directamente,
no fixtures pytest, porque cada caso construye una combinacion distinta
de programa/decision/data items)."""

from __future__ import annotations

from collections.abc import Sequence

from altamira_extractor.contracts.candidate import CandidateArtifact
from altamira_extractor.contracts.canonical import CanonicalProgram, CanonicalStatement
from altamira_extractor.contracts.enums import (
    LocationKind,
    NodeLabel,
    RelationshipType,
    StatementKind,
)
from altamira_extractor.contracts.semantic_graph import GraphNode, GraphRelationship, SemanticGraph
from altamira_extractor.pipeline.semantic_effects_analyzer import analyze_semantic_effects
from altamira_extractor.pipeline.semantic_propagation_analyzer import analyze_semantic_propagation
from altamira_extractor.pipeline.v2_detector_context import (
    V2DetectorContext,
    build_v2_detector_context,
)

HASH = "a" * 64
SRC = "01-codigo/cobol/PROG.cbl"


def program_node_id(program_name: str = "PROG1") -> str:
    return f"program::AR::APP::{program_name}::1.0::{HASH[:12]}"


def paragraph_node_id(program_name: str, paragraph_name: str) -> str:
    return f"{program_node_id(program_name)}::paragraph::{paragraph_name}"


def decision_node_id_for(
    program_name: str, paragraph_name: str, line_start: int, ordinal: int = 1
) -> str:
    return f"{paragraph_node_id(program_name, paragraph_name)}::decision::{line_start}::{ordinal}"


def data_item_node_id(program_name: str, qualified_name: str) -> str:
    return f"{program_node_id(program_name)}::data::{qualified_name}"


def make_stmt(**overrides: object) -> CanonicalStatement:
    """Igual criterio que los smoke tests manuales de Fase 5: cualquier
    `line_start` explicito sube `location_kind` de UNKNOWN a EXACT y
    completa `source_file`/`line_end`, evitando el error de validacion
    'location_kind=UNKNOWN no puede declarar source_file ni lineas'."""
    fields: dict[str, object] = dict(
        statement_id="X",
        kind=StatementKind.MOVE,
        source_text="MOVE",
        location_kind=LocationKind.UNKNOWN,
    )
    fields.update(overrides)
    if "line_start" in fields and fields.get("location_kind") == LocationKind.UNKNOWN:
        fields["location_kind"] = LocationKind.EXACT
        fields.setdefault("source_file", SRC)
        fields.setdefault("line_end", fields["line_start"])
    return CanonicalStatement(**fields)  # type: ignore[arg-type]


def build_semantic_graph(
    *,
    program: CanonicalProgram,
    decisions: Sequence[tuple[str, int, str]] = (),
    data_item_tags: dict[str, str | None] | None = None,
) -> SemanticGraph:
    """Construye un `SemanticGraph` minimo pero fielmente correlacionable
    (mismos IDs/propiedades que `semantic_graph_builder.py` produciria)
    para UN programa: nodos Program/Paragraph/Decision/DataItem mas
    relaciones CONTAINS/HAS_DECISION. `decisions` es
    `(paragraph_name, line_start, expression)`; `data_item_tags` es
    `qualified_name -> semantic_tag` (None si no se etiqueta)."""
    program_name = program.program_name
    prog_node_id = program_node_id(program_name)
    data_item_tags = data_item_tags or {}

    nodes: list[GraphNode] = [
        GraphNode(id=prog_node_id, labels=[NodeLabel.PROGRAM], properties={"name": program_name})
    ]
    relationships: list[GraphRelationship] = []

    for paragraph in program.paragraphs:
        para_node_id = paragraph_node_id(program_name, paragraph.name)
        nodes.append(
            GraphNode(
                id=para_node_id,
                labels=[NodeLabel.PARAGRAPH],
                properties={"name": paragraph.name, "line_start": 1, "line_end": 9999},
            )
        )
        relationships.append(
            GraphRelationship(
                type=RelationshipType.CONTAINS, from_id=prog_node_id, to_id=para_node_id
            )
        )

    ordinal_by_paragraph: dict[str, int] = {}
    for paragraph_name, line_start, expression in decisions:
        ordinal_by_paragraph[paragraph_name] = ordinal_by_paragraph.get(paragraph_name, 0) + 1
        ordinal = ordinal_by_paragraph[paragraph_name]
        dec_id = decision_node_id_for(program_name, paragraph_name, line_start, ordinal)
        nodes.append(
            GraphNode(
                id=dec_id,
                labels=[NodeLabel.DECISION],
                properties={
                    "line_start": line_start,
                    "line_end": line_start,
                    "expression": expression,
                    "outcome_code": None,
                    "rule_type": None,
                },
            )
        )
        relationships.append(
            GraphRelationship(
                type=RelationshipType.HAS_DECISION,
                from_id=paragraph_node_id(program_name, paragraph_name),
                to_id=dec_id,
            )
        )

    for qualified_name, tag in data_item_tags.items():
        nodes.append(
            GraphNode(
                id=data_item_node_id(program_name, qualified_name),
                labels=[NodeLabel.DATA_ITEM],
                properties={"qualified_name": qualified_name, "semantic_tag": tag},
            )
        )

    return SemanticGraph(
        source_package_hash=HASH,
        nodes=sorted(nodes, key=lambda n: n.id),
        relationships=sorted(relationships, key=lambda r: (r.type.value, r.from_id, r.to_id)),
    )


def build_ctx(
    *,
    program: CanonicalProgram,
    decisions: Sequence[tuple[str, int, str]] = (),
    data_item_tags: dict[str, str | None] | None = None,
    v1_candidates: CandidateArtifact | None = None,
    run_id: str = "run1",
) -> V2DetectorContext:
    """Construye el `V2DetectorContext` completo para UN `CanonicalProgram`,
    calculando `SemanticEffectsArtifact`/`SemanticPropagationArtifact` en
    memoria (mismo patron que `v2_shadow_candidates_service.py`)."""
    graph = build_semantic_graph(
        program=program, decisions=decisions, data_item_tags=data_item_tags
    )
    if v1_candidates is None:
        v1_candidates = CandidateArtifact(
            run_id=run_id,
            source_package_hash=HASH,
            semantic_graph_hash=HASH,
            invariants_query_hash=HASH,
            q0_query_hash=HASH,
            candidates=[],
        )
    effects = analyze_semantic_effects(
        canonical_programs=[program],
        run_id=run_id,
        source_package_hash=HASH,
        source_artifact_hashes={"artifacts/02-canonical": HASH},
    )
    propagation = analyze_semantic_propagation(
        canonical_programs=[program],
        semantic_effects=effects,
        run_id=run_id,
        source_package_hash=HASH,
        source_artifact_hashes={"artifacts/02-canonical": HASH},
    )
    return build_v2_detector_context(
        canonical_programs=[program],
        semantic_graph=graph,
        v1_candidates=v1_candidates,
        semantic_effects=effects,
        semantic_propagation=propagation,
    )
