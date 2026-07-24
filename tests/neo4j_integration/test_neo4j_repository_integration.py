"""Integracion real de Neo4jRepository contra un servidor Neo4j 5.

Requiere ALTAMIRA_TEST_NEO4J_URI/_USER/_PASSWORD(/_DATABASE) en el
entorno (ver tests/neo4j_integration/conftest.py); se salta
descriptivamente si faltan. Nunca gestiona Docker por si mismo.

No se prueba coexistencia de multiples paquetes Altamira (V1 no la
soporta: ver docstring de neo4j_repository.py) — solo reemplazo
transaccional completo del subgrafo administrado, preservacion de datos
ajenos (no administrados), e idempotencia de la carga.
"""

from __future__ import annotations

import json

import neo4j
import pytest

from altamira_extractor.config import Settings
from altamira_extractor.contracts.enums import NodeLabel, RelationshipType
from altamira_extractor.contracts.semantic_graph import GraphNode, GraphRelationship, SemanticGraph
from altamira_extractor.pipeline.errors import GraphLoadError, Neo4jAuthenticationError
from altamira_extractor.pipeline.neo4j_repository import Neo4jRepository, compute_edge_key

pytestmark = [pytest.mark.integration, pytest.mark.neo4j_integration]

_HASH_A = "a" * 64
_HASH_B = "b" * 64


def _sort_graph(
    source_package_hash: str,
    nodes: list[GraphNode],
    relationships: list[GraphRelationship],
) -> SemanticGraph:
    sorted_nodes = sorted(nodes, key=lambda n: n.id)
    sorted_relationships = sorted(
        relationships,
        key=lambda r: (
            r.type.value,
            r.from_id,
            r.to_id,
            json.dumps(r.properties, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        ),
    )
    return SemanticGraph(
        source_package_hash=source_package_hash,
        nodes=sorted_nodes,
        relationships=sorted_relationships,
    )


def _full_graph(source_package_hash: str = _HASH_A) -> SemanticGraph:
    country = GraphNode(id="country::AR", labels=[NodeLabel.COUNTRY], properties={"code": "AR"})
    application = GraphNode(
        id="application::AR::TRF", labels=[NodeLabel.APPLICATION], properties={"name": "TRF"}
    )
    operation = GraphNode(
        id="operation::AR::TRF::OP1", labels=[NodeLabel.OPERATION], properties={"name": "OP1"}
    )
    program = GraphNode(
        id="program::AR::OP1::PROG1::1::abcdef123456",
        labels=[NodeLabel.PROGRAM],
        properties={"name": "PROG1", "metadata_json": json.dumps({"k": "v"})},
    )
    paragraph = GraphNode(
        id=f"{program.id}::paragraph::MAIN", labels=[NodeLabel.PARAGRAPH], properties={}
    )
    data_item = GraphNode(
        id=f"{program.id}::data::WS-VAR", labels=[NodeLabel.DATA_ITEM], properties={}
    )
    decision = GraphNode(
        id=f"{paragraph.id}::decision::10::0", labels=[NodeLabel.DECISION], properties={}
    )
    table = GraphNode(
        id="table::AR::default::CUENTAS", labels=[NodeLabel.TABLE], properties={}
    )
    parameter_table = GraphNode(
        id="table::AR::default::PARAM_TRANSFER",
        labels=[NodeLabel.TABLE, NodeLabel.PARAMETER_TABLE],
        properties={"name": "PARAM_TRANSFER"},
    )
    parameter_entry = GraphNode(
        id="parameter::PARAM_TRANSFER::20260515::abc123456789",
        labels=[NodeLabel.PARAMETER_ENTRY],
        properties={"row_hash": "abc123456789"},
    )
    batch_job_1 = GraphNode(
        id="batch::AR::CTRLM::JOB1", labels=[NodeLabel.BATCH_JOB], properties={}
    )
    batch_job_2 = GraphNode(
        id="batch::AR::CTRLM::JOB2", labels=[NodeLabel.BATCH_JOB], properties={}
    )
    domain_term = GraphNode(
        id="term::v1::ACCOUNT_NUMBER", labels=[NodeLabel.DOMAIN_TERM], properties={}
    )

    nodes = [
        country, application, operation, program, paragraph, data_item, decision, table,
        parameter_table, parameter_entry, batch_job_1, batch_job_2, domain_term,
    ]
    relationships = [
        GraphRelationship(
            type=RelationshipType.HAS_APPLICATION,
            from_id=country.id, to_id=application.id, properties={},
        ),
        GraphRelationship(
            type=RelationshipType.HAS_OPERATION,
            from_id=application.id, to_id=operation.id, properties={},
        ),
        GraphRelationship(
            type=RelationshipType.EXECUTES_VIA,
            from_id=operation.id, to_id=program.id, properties={},
        ),
        GraphRelationship(
            type=RelationshipType.CONTAINS, from_id=program.id, to_id=paragraph.id, properties={}
        ),
        GraphRelationship(
            type=RelationshipType.USES, from_id=paragraph.id, to_id=data_item.id, properties={}
        ),
        GraphRelationship(
            type=RelationshipType.HAS_DECISION,
            from_id=paragraph.id, to_id=decision.id, properties={},
        ),
        GraphRelationship(
            type=RelationshipType.LEADS_TO, from_id=decision.id, to_id=data_item.id, properties={}
        ),
        GraphRelationship(
            type=RelationshipType.READS, from_id=paragraph.id, to_id=table.id, properties={}
        ),
        GraphRelationship(
            type=RelationshipType.READS,
            from_id=batch_job_1.id, to_id=table.id, properties={"via": "batch"},
        ),
        GraphRelationship(
            type=RelationshipType.HAS_ENTRY,
            from_id=parameter_table.id, to_id=parameter_entry.id, properties={},
        ),
        GraphRelationship(
            type=RelationshipType.PRECEDED_BY,
            from_id=batch_job_2.id, to_id=batch_job_1.id, properties={},
        ),
        GraphRelationship(
            type=RelationshipType.TRIGGERS,
            from_id=batch_job_1.id, to_id=batch_job_2.id, properties={},
        ),
        GraphRelationship(
            type=RelationshipType.HAS_DOMAIN_TERM,
            from_id=data_item.id, to_id=domain_term.id, properties={},
        ),
    ]
    return _sort_graph(source_package_hash, nodes, relationships)


def test_load_graph_creates_every_node_label_and_round_trips_properties(
    neo4j_driver: neo4j.Driver, neo4j_test_settings: Settings, clean_managed_graph: None
) -> None:
    repository = Neo4jRepository.connect(neo4j_test_settings)
    try:
        repository.ensure_schema()
        graph = _full_graph()
        result = repository.load_graph(
            graph, semantic_graph_hash=_HASH_A, server_version="5.24.0"
        )
        assert result.node_count == len(graph.nodes)
        assert result.relationship_count == len(graph.relationships)

        with neo4j_driver.session(database=neo4j_test_settings.neo4j_database) as session:
            for label in NodeLabel:
                count = session.run(
                    f"MATCH (n:{label.value}) WHERE n._altamira_managed = true "
                    "RETURN count(n) AS c"
                ).single()["c"]
                assert count >= 1, f"no se creo ningun nodo con label {label.value}"

            program_node = session.run(
                "MATCH (n:Program {id: $id}) RETURN n AS n",
                id="program::AR::OP1::PROG1::1::abcdef123456",
            ).single()["n"]
            assert program_node["metadata_json"] == json.dumps({"k": "v"})

            parameter_table_labels = session.run(
                "MATCH (t {id: $id}) RETURN labels(t) AS labels",
                id="table::AR::default::PARAM_TRANSFER",
            ).single()["labels"]
            assert set(parameter_table_labels) >= {"Table", "ParameterTable"}

            active = repository.read_active_graph_load()
            assert active is not None
            assert active.node_count == len(graph.nodes)
            assert active.relationship_count == len(graph.relationships)
    finally:
        repository.close()


def test_repeated_load_replaces_obsolete_elements_and_keeps_foreign_data(
    neo4j_driver: neo4j.Driver, neo4j_test_settings: Settings, clean_managed_graph: None
) -> None:
    with neo4j_driver.session(database=neo4j_test_settings.neo4j_database) as session:
        # MERGE (no CREATE): idempotente si el test se corre varias veces
        # contra el mismo servidor persistente.
        session.run(
            "MERGE (n:ForeignLabel {id: 'foreign::1'}) SET n.note = 'ajeno a Altamira'"
        ).consume()

    repository = Neo4jRepository.connect(neo4j_test_settings)
    try:
        repository.ensure_schema()
        first_country = GraphNode(id="country::AR", labels=[NodeLabel.COUNTRY], properties={})
        second_country = GraphNode(id="country::BR", labels=[NodeLabel.COUNTRY], properties={})
        first_graph = _sort_graph(_HASH_A, [first_country], [])
        repository.load_graph(first_graph, semantic_graph_hash=_HASH_A, server_version="5.24.0")

        second_graph = _sort_graph(_HASH_B, [second_country], [])
        repository.load_graph(second_graph, semantic_graph_hash=_HASH_B, server_version="5.24.0")

        with neo4j_driver.session(database=neo4j_test_settings.neo4j_database) as session:
            managed_ids = [
                record["id"]
                for record in session.run(
                    "MATCH (n) WHERE n._altamira_managed = true AND NOT n:AltamiraGraphLoad "
                    "RETURN n.id AS id"
                )
            ]
            assert managed_ids == ["country::BR"]

            foreign = session.run(
                "MATCH (n:ForeignLabel {id: 'foreign::1'}) RETURN n.note AS note"
            ).single()
            assert foreign is not None
            assert foreign["note"] == "ajeno a Altamira"
    finally:
        repository.close()
        # limpieza del fixture ajeno propio: no le corresponde a
        # clean_managed_graph (esta fuera de _altamira_managed a proposito).
        with neo4j_driver.session(database=neo4j_test_settings.neo4j_database) as session:
            session.run("MATCH (n:ForeignLabel {id: 'foreign::1'}) DETACH DELETE n").consume()


def test_load_graph_rolls_back_completely_on_broken_relationship_endpoint(
    neo4j_driver: neo4j.Driver, neo4j_test_settings: Settings, clean_managed_graph: None
) -> None:
    repository = Neo4jRepository.connect(neo4j_test_settings)
    try:
        repository.ensure_schema()
        first_graph = _sort_graph(
            _HASH_A, [GraphNode(id="country::AR", labels=[NodeLabel.COUNTRY], properties={})], []
        )
        repository.load_graph(first_graph, semantic_graph_hash=_HASH_A, server_version="5.24.0")

        # SemanticGraph valida referencias al construir: para probar el
        # rollback real de la transaccion ante un endpoint inexistente se
        # usa model_construct (sin validadores) — simula una inconsistencia
        # que el contrato normalmente ya impide, defensa en profundidad de
        # Neo4jRepository.
        broken_relationship = GraphRelationship(
            type=RelationshipType.HAS_APPLICATION,
            from_id="country::AR",
            to_id="application::NO-EXISTE",
            properties={},
        )
        broken_graph = SemanticGraph.model_construct(
            schema_version="2.0",
            source_package_hash=_HASH_B,
            nodes=[GraphNode(id="country::AR", labels=[NodeLabel.COUNTRY], properties={})],
            relationships=[broken_relationship],
            warnings=[],
        )

        with pytest.raises(GraphLoadError):
            repository.load_graph(
                broken_graph, semantic_graph_hash=_HASH_B, server_version="5.24.0"
            )

        active = repository.read_active_graph_load()
        assert active is not None
        assert active.semantic_graph_hash == _HASH_A

        with neo4j_driver.session(database=neo4j_test_settings.neo4j_database) as session:
            count = session.run(
                "MATCH (n) WHERE n._altamira_managed = true AND NOT n:AltamiraGraphLoad "
                "RETURN count(n) AS c"
            ).single()["c"]
        assert count == 1
    finally:
        repository.close()


def test_compute_drift_detects_manual_tampering(
    neo4j_driver: neo4j.Driver, neo4j_test_settings: Settings, clean_managed_graph: None
) -> None:
    repository = Neo4jRepository.connect(neo4j_test_settings)
    try:
        repository.ensure_schema()
        graph = _sort_graph(
            _HASH_A,
            [
                GraphNode(id="country::AR", labels=[NodeLabel.COUNTRY], properties={}),
                GraphNode(id="application::AR::TRF", labels=[NodeLabel.APPLICATION], properties={}),
            ],
            [
                GraphRelationship(
                    type=RelationshipType.HAS_APPLICATION,
                    from_id="country::AR",
                    to_id="application::AR::TRF",
                    properties={},
                )
            ],
        )
        repository.load_graph(graph, semantic_graph_hash=_HASH_A, server_version="5.24.0")

        assert repository.compute_drift(graph).is_clean

        with neo4j_driver.session(database=neo4j_test_settings.neo4j_database) as session:
            session.run(
                "MATCH (n:Application {id: 'application::AR::TRF'}) DETACH DELETE n"
            ).consume()

        drift = repository.compute_drift(graph)
        assert not drift.is_clean
        assert "application::AR::TRF" in drift.missing_ids
    finally:
        repository.close()


def test_invalid_credentials_raise_authentication_error(
    neo4j_test_settings: Settings, clean_managed_graph: None
) -> None:
    bad_settings = Settings(
        data_dir=neo4j_test_settings.data_dir,
        runs_dir=neo4j_test_settings.runs_dir,
        incoming_dir=neo4j_test_settings.incoming_dir,
        NEO4J_URI=neo4j_test_settings.neo4j_uri,
        NEO4J_USER=neo4j_test_settings.neo4j_user,
        NEO4J_PASSWORD="definitely-the-wrong-password",
        NEO4J_DATABASE=neo4j_test_settings.neo4j_database,
    )
    repository = Neo4jRepository.connect(bad_settings)
    try:
        with pytest.raises(Neo4jAuthenticationError):
            repository.verify_connectivity()
    finally:
        repository.close()


def test_ensure_schema_is_idempotent(
    neo4j_test_settings: Settings, clean_managed_graph: None
) -> None:
    repository = Neo4jRepository.connect(neo4j_test_settings)
    try:
        repository.ensure_schema()
        repository.ensure_schema()  # segunda vez: IF NOT EXISTS no debe fallar.
    finally:
        repository.close()


def test_compute_edge_key_matches_persisted_edge_key(
    neo4j_driver: neo4j.Driver, neo4j_test_settings: Settings, clean_managed_graph: None
) -> None:
    repository = Neo4jRepository.connect(neo4j_test_settings)
    try:
        repository.ensure_schema()
        relationship = GraphRelationship(
            type=RelationshipType.HAS_APPLICATION,
            from_id="country::AR",
            to_id="application::AR::TRF",
            properties={"note": "x"},
        )
        graph = _sort_graph(
            _HASH_A,
            [
                GraphNode(id="country::AR", labels=[NodeLabel.COUNTRY], properties={}),
                GraphNode(id="application::AR::TRF", labels=[NodeLabel.APPLICATION], properties={}),
            ],
            [relationship],
        )
        repository.load_graph(graph, semantic_graph_hash=_HASH_A, server_version="5.24.0")

        with neo4j_driver.session(database=neo4j_test_settings.neo4j_database) as session:
            persisted_key = session.run(
                "MATCH ()-[r:HAS_APPLICATION]->() RETURN r._edge_key AS key"
            ).single()["key"]
        assert persisted_key == compute_edge_key(relationship)
    finally:
        repository.close()
