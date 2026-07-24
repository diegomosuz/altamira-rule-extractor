"""Integracion real de GraphInvariantValidator / GRAPH_VALIDATED contra
un servidor Neo4j 5 (ejecuta el `queries/v1/invariants.cypher` REAL del
repositorio, no un doble).

Requiere ALTAMIRA_TEST_NEO4J_URI/_USER/_PASSWORD(/_DATABASE) en el
entorno; se salta descriptivamente si faltan.

No se prueba coexistencia de multiples paquetes Altamira (V1 no la
soporta).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import neo4j
import pytest

from altamira_extractor.config import Settings
from altamira_extractor.contracts.enums import (
    NodeLabel,
    PipelineStage,
    RelationshipType,
    StageStatus,
)
from altamira_extractor.contracts.run_state import StageExecution
from altamira_extractor.contracts.semantic_enrichment import SemanticEnrichmentArtifact
from altamira_extractor.contracts.semantic_graph import GraphNode, GraphRelationship, SemanticGraph
from altamira_extractor.pipeline.errors import GraphValidationError
from altamira_extractor.pipeline.graph_invariant_validator import run_invariants
from altamira_extractor.pipeline.graph_validated_stage import run_graph_validated_stage
from altamira_extractor.pipeline.neo4j_repository import Neo4jRepository

pytestmark = [pytest.mark.integration, pytest.mark.neo4j_integration]

_HASH_A = "a" * 64
_REPO_ROOT = Path(__file__).resolve().parents[2]
_INVARIANTS_CYPHER_PATH = _REPO_ROOT / "queries" / "v1" / "invariants.cypher"
_SEMANTIC_TAGS_PATH = _REPO_ROOT / "config" / "semantic-tags.yml"


def _semantic_tags_config_hash() -> str:
    return hashlib.sha256(_SEMANTIC_TAGS_PATH.read_bytes()).hexdigest()


def _valid_graph(source_package_hash: str = _HASH_A) -> SemanticGraph:
    country = GraphNode(id="country::AR", labels=[NodeLabel.COUNTRY], properties={})
    application = GraphNode(
        id="application::AR::TRF", labels=[NodeLabel.APPLICATION], properties={}
    )
    operation = GraphNode(
        id="operation::AR::TRF::OP1", labels=[NodeLabel.OPERATION], properties={}
    )
    program = GraphNode(
        id="program::AR::OP1::PROG1::1::abcdef123456",
        labels=[NodeLabel.PROGRAM],
        properties={"source_package_hash": source_package_hash},
    )
    paragraph = GraphNode(
        id=f"{program.id}::paragraph::MAIN",
        labels=[NodeLabel.PARAGRAPH],
        properties={
            "source_text": "MOVE 1 TO WS-VAR.",
            "source_package_hash": source_package_hash,
        },
    )
    data_item = GraphNode(
        id=f"{program.id}::data::WS-VAR",
        labels=[NodeLabel.DATA_ITEM],
        properties={"source_package_hash": source_package_hash},
    )
    decision = GraphNode(
        id=f"{paragraph.id}::decision::10::0",
        labels=[NodeLabel.DECISION],
        properties={"source_package_hash": source_package_hash},
    )
    parameter_table = GraphNode(
        id="table::AR::default::PARAM_TRANSFER",
        labels=[NodeLabel.TABLE, NodeLabel.PARAMETER_TABLE],
        properties={"source_package_hash": source_package_hash},
    )
    parameter_entry = GraphNode(
        id="parameter::PARAM_TRANSFER::20260515::abc123456789",
        labels=[NodeLabel.PARAMETER_ENTRY],
        properties={"source_package_hash": source_package_hash},
    )

    nodes = sorted(
        [country, application, operation, program, paragraph, data_item, decision,
         parameter_table, parameter_entry],
        key=lambda n: n.id,
    )
    rel_properties = {"source_package_hash": source_package_hash}
    relationships = [
        GraphRelationship(
            type=RelationshipType.HAS_APPLICATION,
            from_id=country.id, to_id=application.id, properties=rel_properties,
        ),
        GraphRelationship(
            type=RelationshipType.HAS_OPERATION,
            from_id=application.id, to_id=operation.id, properties=rel_properties,
        ),
        GraphRelationship(
            type=RelationshipType.EXECUTES_VIA,
            from_id=operation.id, to_id=program.id, properties=rel_properties,
        ),
        GraphRelationship(
            type=RelationshipType.CONTAINS,
            from_id=program.id, to_id=paragraph.id, properties=rel_properties,
        ),
        GraphRelationship(
            type=RelationshipType.USES,
            from_id=paragraph.id, to_id=data_item.id, properties=rel_properties,
        ),
        GraphRelationship(
            type=RelationshipType.HAS_DECISION,
            from_id=paragraph.id, to_id=decision.id, properties=rel_properties,
        ),
        GraphRelationship(
            type=RelationshipType.HAS_ENTRY,
            from_id=parameter_table.id, to_id=parameter_entry.id, properties=rel_properties,
        ),
    ]
    properties_json = json.dumps(rel_properties, sort_keys=True, separators=(",", ":"))
    relationships.sort(key=lambda r: (r.type.value, r.from_id, r.to_id, properties_json))
    return SemanticGraph(
        source_package_hash=source_package_hash, nodes=nodes, relationships=relationships
    )


def _run_invariants_for(
    repository: Neo4jRepository, package_hash: str = _HASH_A
) -> list[str]:
    violations, _ = run_invariants(
        repository,
        invariants_cypher_path=_INVARIANTS_CYPHER_PATH,
        package_hash=package_hash,
        semantic_tags_path=_SEMANTIC_TAGS_PATH,
        expected_semantic_tags_config_hash=_semantic_tags_config_hash(),
    )
    return [v.code for v in violations]


def test_valid_graph_produces_zero_violations(
    neo4j_test_settings: Settings, clean_managed_graph: None
) -> None:
    repository = Neo4jRepository.connect(neo4j_test_settings)
    try:
        repository.ensure_schema()
        graph = _valid_graph()
        repository.load_graph(graph, semantic_graph_hash=_HASH_A, server_version="5.24.0")

        codes = _run_invariants_for(repository)

        assert codes == []
    finally:
        repository.close()


def test_domain_term_without_mapping_is_not_a_violation(
    neo4j_test_settings: Settings, clean_managed_graph: None
) -> None:
    repository = Neo4jRepository.connect(neo4j_test_settings)
    try:
        repository.ensure_schema()
        graph = _valid_graph()
        unused_term = GraphNode(
            id="term::v1::UNUSED_TERM", labels=[NodeLabel.DOMAIN_TERM], properties={}
        )
        graph_with_term = SemanticGraph(
            source_package_hash=graph.source_package_hash,
            nodes=sorted([*graph.nodes, unused_term], key=lambda n: n.id),
            relationships=graph.relationships,
        )
        repository.load_graph(
            graph_with_term, semantic_graph_hash=_HASH_A, server_version="5.24.0"
        )

        codes = _run_invariants_for(repository)

        assert codes == []
    finally:
        repository.close()


def test_decision_without_leads_to_but_with_has_decision_is_not_a_violation(
    neo4j_test_settings: Settings, clean_managed_graph: None
) -> None:
    # _valid_graph() ya construye exactamente este caso: una Decision con
    # HAS_DECISION entrante y ningun LEADS_TO saliente (Prompt 8: se crea
    # una Decision por cada IF/EVALUATE aunque no tenga asignacion
    # resoluble). Confirma explicitamente que no produce violacion.
    repository = Neo4jRepository.connect(neo4j_test_settings)
    try:
        repository.ensure_schema()
        graph = _valid_graph()
        repository.load_graph(graph, semantic_graph_hash=_HASH_A, server_version="5.24.0")

        codes = _run_invariants_for(repository)

        assert "ORPHAN_DECISION" not in codes
    finally:
        repository.close()


def test_parameter_table_losing_table_label_is_detected(
    neo4j_driver: neo4j.Driver, neo4j_test_settings: Settings, clean_managed_graph: None
) -> None:
    repository = Neo4jRepository.connect(neo4j_test_settings)
    try:
        repository.ensure_schema()
        graph = _valid_graph()
        repository.load_graph(graph, semantic_graph_hash=_HASH_A, server_version="5.24.0")

        with neo4j_driver.session(database=neo4j_test_settings.neo4j_database) as session:
            session.run(
                "MATCH (t:ParameterTable {id: $id}) REMOVE t:Table",
                id="table::AR::default::PARAM_TRANSFER",
            ).consume()

        codes = _run_invariants_for(repository)

        assert "PARAMETER_TABLE_WITHOUT_TABLE_LABEL" in codes
    finally:
        repository.close()


def test_invalid_relationship_endpoint_is_detected(
    neo4j_driver: neo4j.Driver, neo4j_test_settings: Settings, clean_managed_graph: None
) -> None:
    repository = Neo4jRepository.connect(neo4j_test_settings)
    try:
        repository.ensure_schema()
        graph = _valid_graph()
        repository.load_graph(graph, semantic_graph_hash=_HASH_A, server_version="5.24.0")

        with neo4j_driver.session(database=neo4j_test_settings.neo4j_database) as session:
            # DataItem no es un origen permitido para READS (solo
            # Paragraph/BatchJob): relacion fuera del metamodelo, creada
            # manualmente para simular una carga corrupta.
            session.run(
                """
                MATCH (di:DataItem {id: $data_item_id})
                MATCH (t:Table {id: $table_id})
                CREATE (di)-[r:READS]->(t)
                SET r._altamira_managed = true
                SET r.source_package_hash = $package_hash
                """,
                data_item_id="program::AR::OP1::PROG1::1::abcdef123456::data::WS-VAR",
                table_id="table::AR::default::PARAM_TRANSFER",
                package_hash=_HASH_A,
            ).consume()

        codes = _run_invariants_for(repository)

        assert "INVALID_RELATIONSHIP_ENDPOINT" in codes
    finally:
        repository.close()


def _stage(stage: PipelineStage, status: StageStatus) -> StageExecution:
    now = datetime.now(UTC)
    return StageExecution(stage=stage, status=status, started_at=now, finished_at=now)


def test_graph_validated_stage_end_to_end_blocks_on_real_error(
    neo4j_driver: neo4j.Driver,
    neo4j_test_settings: Settings,
    clean_managed_graph: None,
    tmp_path: Path,
) -> None:
    repository = Neo4jRepository.connect(neo4j_test_settings)
    try:
        repository.ensure_schema()
        graph = _valid_graph()
        graph_path = tmp_path / "04-semantic-graph.json"
        graph_path.write_text(graph.model_dump_json(), encoding="utf-8")
        semantic_graph_hash = hashlib.sha256(graph_path.read_bytes()).hexdigest()
        repository.load_graph(
            graph, semantic_graph_hash=semantic_graph_hash, server_version="5.24.0"
        )

        with neo4j_driver.session(database=neo4j_test_settings.neo4j_database) as session:
            session.run(
                "MATCH (t:ParameterTable {id: $id}) REMOVE t:Table",
                id="table::AR::default::PARAM_TRANSFER",
            ).consume()
    finally:
        repository.close()

    enrichment_path = tmp_path / "03b-semantic-enrichment.json"
    enrichment_path.write_text(
        SemanticEnrichmentArtifact(
            run_id="run-1",
            source_package_hash=_HASH_A,
            semantic_tags_config_hash=_semantic_tags_config_hash(),
            domain_glossary_config_hash="d" * 64,
        ).model_dump_json(),
        encoding="utf-8",
    )
    invariants_path = tmp_path / "05-invariants.json"

    with pytest.raises(GraphValidationError):
        run_graph_validated_stage(
            run_id="run-1",
            source_package_hash=_HASH_A,
            run_stages=[_stage(PipelineStage.SEMANTIC_GRAPH_LOADED, StageStatus.SUCCEEDED)],
            semantic_graph_path=graph_path,
            semantic_enrichment_path=enrichment_path,
            invariants_cypher_path=_INVARIANTS_CYPHER_PATH,
            invariants_path=invariants_path,
            settings=neo4j_test_settings,
        )

    from altamira_extractor.contracts.invariants import InvariantArtifact

    artifact = InvariantArtifact.model_validate_json(invariants_path.read_text(encoding="utf-8"))
    assert artifact.graph_validated is False
    assert any(v.code == "PARAMETER_TABLE_WITHOUT_TABLE_LABEL" for v in artifact.violations)
