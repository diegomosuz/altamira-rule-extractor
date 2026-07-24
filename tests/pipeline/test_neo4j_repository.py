"""Tests de Neo4jRepository con un driver/sesion falsos (sin Neo4j real).

La integracion contra un servidor Neo4j real vive en
tests/neo4j_integration/ (marcada `integration` + `neo4j_integration`,
sujeta a las variables de entorno ALTAMIRA_TEST_NEO4J_*). Aqui se
verifica el comportamiento de Neo4jRepository de forma aislada: cada
consulta Cypher generada por el modulo tiene una forma textual fija y
conocida (no hay entrada dinamica en labels/tipos de relacion mas alla de
NodeLabel/RelationshipType), asi que el "fake" clasifica cada consulta
por una subcadena distintiva y la ejecuta contra un grafo en memoria —
no es un interprete Cypher generico, es un doble de prueba para nuestro
propio codigo.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
from neo4j.exceptions import AuthError, ClientError, ServiceUnavailable

from altamira_extractor.config import Settings
from altamira_extractor.contracts.enums import NodeLabel, RelationshipType
from altamira_extractor.contracts.semantic_graph import (
    GraphNode,
    GraphRelationship,
    SemanticGraph,
)
from altamira_extractor.pipeline.errors import (
    GraphLoadError,
    Neo4jAuthenticationError,
    Neo4jConfigurationError,
    Neo4jQueryError,
    Neo4jTimeoutError,
    Neo4jUnavailableError,
    Neo4jUnsupportedVersionError,
)
from altamira_extractor.pipeline.neo4j_repository import (
    METADATA_LABEL,
    Neo4jRepository,
    _validated_label_clause,
    _validated_relationship_type,
    compute_edge_key,
)

_HASH_A = "a" * 64
_HASH_B = "b" * 64


# --- Fake driver/session/transaction: grafo en memoria, dispatch por forma textual ---


class _FakeRecord(dict[str, Any]):
    """Imita neo4j.Record: soporta acceso por clave y por indice posicional."""

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class _FakeResult:
    def __init__(self, records: _FakeRecord | list[_FakeRecord] | None) -> None:
        if records is None:
            self._records: list[_FakeRecord] = []
        elif isinstance(records, list):
            self._records = records
        else:
            self._records = [records]

    def single(self) -> _FakeRecord | None:
        return self._records[0] if self._records else None

    def consume(self) -> None:
        return None

    def __iter__(self) -> Any:
        return iter(self._records)


@dataclass
class _FakeGraphStore:
    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    relationships: dict[str, dict[str, Any]] = field(default_factory=dict)
    schema_statements: list[str] = field(default_factory=list)
    server_version_value: str = "5.24.0"


def _extract_node_labels(query: str) -> list[str]:
    match = re.search(r"MERGE \(n((?::[A-Za-z]+)+)", query)
    assert match is not None, f"no se pudo extraer labels de: {query!r}"
    return [part for part in match.group(1).split(":") if part]


def _extract_relationship_type(query: str) -> str:
    match = re.search(r"-\[r:([A-Za-z_]+) ", query)
    assert match is not None, f"no se pudo extraer el tipo de relacion de: {query!r}"
    return match.group(1)


def _classify(query: str) -> str:
    normalized = " ".join(query.split())
    if "dbms.components()" in normalized:
        return "server_version"
    if normalized.startswith("CREATE CONSTRAINT") or normalized.startswith("CREATE INDEX"):
        return "schema"
    if "DETACH DELETE m" in normalized:
        return "delete_metadata"
    if "DETACH DELETE n" in normalized:
        return "delete_managed"
    if "AS created" in normalized:
        return "merge_relationships"
    if "SET n = row.properties" in normalized:
        return "merge_nodes"
    if "SET m = $properties" in normalized:
        return "merge_metadata"
    if "RETURN count(n)" in normalized:
        return "count_nodes"
    if "RETURN count(r)" in normalized:
        return "count_relationships"
    if "collect(expected_id) AS missing" in normalized:
        return "missing_ids"
    if "collect(n.id) AS extra" in normalized:
        return "extra_ids"
    if "collect(expected_key) AS missing" in normalized:
        return "missing_edges"
    if "AS extra" in normalized and "collect(r." in normalized:
        return "extra_edges"
    if "MATCH (t:ParameterTable)" in normalized:
        return "bad_parameter_tables"
    if "RETURN m.semantic_graph_hash" in normalized:
        return "metadata_hash"
    if "RETURN m AS m" in normalized:
        return "read_metadata"
    return "unknown"


def _is_managed(node: dict[str, Any]) -> bool:
    return bool(node["properties"].get("_altamira_managed"))


def _h_delete_metadata(store: _FakeGraphStore, query: str, kwargs: dict[str, Any]) -> None:
    store.nodes.pop(kwargs["id"], None)
    return None


def _h_delete_managed(store: _FakeGraphStore, query: str, kwargs: dict[str, Any]) -> None:
    deleted = {node_id for node_id, node in store.nodes.items() if _is_managed(node)}
    for node_id in deleted:
        store.nodes.pop(node_id, None)
    for edge_key in [
        key
        for key, rel in store.relationships.items()
        if rel["from_id"] in deleted or rel["to_id"] in deleted
    ]:
        store.relationships.pop(edge_key, None)
    return None


def _h_merge_nodes(store: _FakeGraphStore, query: str, kwargs: dict[str, Any]) -> None:
    labels = _extract_node_labels(query)
    for row in kwargs["rows"]:
        properties = dict(row["properties"])
        properties["id"] = row["id"]
        properties["_altamira_managed"] = True
        properties["_artifact_hash"] = kwargs["semantic_graph_hash"]
        store.nodes[row["id"]] = {"labels": set(labels), "properties": properties}
    return None


def _h_merge_relationships(store: _FakeGraphStore, query: str, kwargs: dict[str, Any]) -> Any:
    rel_type = _extract_relationship_type(query)
    created = 0
    for row in kwargs["rows"]:
        from_node = store.nodes.get(row["from_id"])
        to_node = store.nodes.get(row["to_id"])
        if from_node and _is_managed(from_node) and to_node and _is_managed(to_node):
            properties = dict(row["properties"])
            properties["_edge_key"] = row["edge_key"]
            properties["_altamira_managed"] = True
            properties["_artifact_hash"] = kwargs["semantic_graph_hash"]
            store.relationships[row["edge_key"]] = {
                "type": rel_type,
                "from_id": row["from_id"],
                "to_id": row["to_id"],
                "properties": properties,
            }
            created += 1
    return _FakeRecord(created=created)


def _h_merge_metadata(store: _FakeGraphStore, query: str, kwargs: dict[str, Any]) -> None:
    properties = dict(kwargs["properties"])
    properties["id"] = kwargs["id"]
    properties["_altamira_managed"] = True
    store.nodes[kwargs["id"]] = {"labels": {METADATA_LABEL}, "properties": properties}
    return None


def _h_count_nodes(store: _FakeGraphStore, query: str, kwargs: dict[str, Any]) -> _FakeRecord:
    count = sum(
        1
        for node in store.nodes.values()
        if _is_managed(node) and METADATA_LABEL not in node["labels"]
    )
    return _FakeRecord(value=count)


def _h_count_relationships(
    store: _FakeGraphStore, query: str, kwargs: dict[str, Any]
) -> _FakeRecord:
    return _FakeRecord(value=len(store.relationships))


def _h_missing_ids(store: _FakeGraphStore, query: str, kwargs: dict[str, Any]) -> _FakeRecord:
    managed_ids = {node_id for node_id, node in store.nodes.items() if _is_managed(node)}
    missing = [node_id for node_id in kwargs["ids"] if node_id not in managed_ids]
    return _FakeRecord(missing=missing)


def _h_extra_ids(store: _FakeGraphStore, query: str, kwargs: dict[str, Any]) -> _FakeRecord:
    expected = set(kwargs["ids"])
    extra = [
        node_id
        for node_id, node in store.nodes.items()
        if _is_managed(node) and METADATA_LABEL not in node["labels"] and node_id not in expected
    ]
    return _FakeRecord(extra=extra)


def _h_missing_edges(store: _FakeGraphStore, query: str, kwargs: dict[str, Any]) -> _FakeRecord:
    missing = [key for key in kwargs["keys"] if key not in store.relationships]
    return _FakeRecord(missing=missing)


def _h_extra_edges(store: _FakeGraphStore, query: str, kwargs: dict[str, Any]) -> _FakeRecord:
    expected = set(kwargs["keys"])
    extra = [key for key in store.relationships if key not in expected]
    return _FakeRecord(extra=extra)


def _h_bad_parameter_tables(
    store: _FakeGraphStore, query: str, kwargs: dict[str, Any]
) -> _FakeRecord:
    bad = [
        node_id
        for node_id, node in store.nodes.items()
        if "ParameterTable" in node["labels"]
        and _is_managed(node)
        and "Table" not in node["labels"]
    ]
    return _FakeRecord(value=bad)


def _h_metadata_hash(store: _FakeGraphStore, query: str, kwargs: dict[str, Any]) -> _FakeRecord:
    node = store.nodes.get(kwargs["id"])
    value = node["properties"].get("semantic_graph_hash") if node else None
    return _FakeRecord(value=value)


def _h_read_metadata(
    store: _FakeGraphStore, query: str, kwargs: dict[str, Any]
) -> _FakeRecord | None:
    node = store.nodes.get(kwargs["id"])
    if node is None:
        return None
    return _FakeRecord(m=_FakeRecord(**node["properties"]))


def _h_server_version(store: _FakeGraphStore, query: str, kwargs: dict[str, Any]) -> _FakeRecord:
    return _FakeRecord(version=store.server_version_value)


def _h_schema(store: _FakeGraphStore, query: str, kwargs: dict[str, Any]) -> None:
    store.schema_statements.append(" ".join(query.split()))
    return None


_HANDLERS: dict[str, Callable[[_FakeGraphStore, str, dict[str, Any]], Any]] = {
    "delete_metadata": _h_delete_metadata,
    "delete_managed": _h_delete_managed,
    "merge_nodes": _h_merge_nodes,
    "merge_relationships": _h_merge_relationships,
    "merge_metadata": _h_merge_metadata,
    "count_nodes": _h_count_nodes,
    "count_relationships": _h_count_relationships,
    "missing_ids": _h_missing_ids,
    "extra_ids": _h_extra_ids,
    "missing_edges": _h_missing_edges,
    "extra_edges": _h_extra_edges,
    "bad_parameter_tables": _h_bad_parameter_tables,
    "metadata_hash": _h_metadata_hash,
    "read_metadata": _h_read_metadata,
    "server_version": _h_server_version,
    "schema": _h_schema,
}

_Override = Any


def _execute(
    store: _FakeGraphStore, overrides: dict[str, _Override], query: str, kwargs: dict[str, Any]
) -> _FakeResult:
    shape = _classify(query)
    if shape in overrides:
        override = overrides[shape]
        result = override(store, query, kwargs) if callable(override) else override
        return _FakeResult(result)
    if shape == "unknown":
        raise AssertionError(f"forma de consulta no reconocida por el fake: {query!r}")
    return _FakeResult(_HANDLERS[shape](store, query, kwargs))


class _FakeTransaction:
    def __init__(self, store: _FakeGraphStore, overrides: dict[str, _Override]) -> None:
        self._store = store
        self._overrides = overrides

    def run(self, query: str, **kwargs: Any) -> _FakeResult:
        return _execute(self._store, self._overrides, query, kwargs)


class _FakeSession:
    def __init__(self, store: _FakeGraphStore, overrides: dict[str, _Override]) -> None:
        self._store = store
        self._overrides = overrides

    def run(self, query: str, **kwargs: Any) -> _FakeResult:
        return _execute(self._store, self._overrides, query, kwargs)

    def execute_write(self, fn: Callable[..., Any], *args: Any) -> Any:
        nodes_snapshot = copy.deepcopy(self._store.nodes)
        relationships_snapshot = copy.deepcopy(self._store.relationships)
        tx = _FakeTransaction(self._store, self._overrides)
        try:
            return fn(tx, *args)
        except Exception:
            self._store.nodes = nodes_snapshot
            self._store.relationships = relationships_snapshot
            raise

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


class _FakeDriver:
    def __init__(
        self,
        store: _FakeGraphStore,
        *,
        overrides: dict[str, _Override] | None = None,
        connectivity_error: BaseException | None = None,
    ) -> None:
        self._store = store
        self._overrides = overrides or {}
        self._connectivity_error = connectivity_error

    def verify_connectivity(self) -> None:
        if self._connectivity_error is not None:
            raise self._connectivity_error

    def session(self, *, database: str) -> _FakeSession:
        return _FakeSession(self._store, self._overrides)

    def close(self) -> None:
        return None


def _repository(
    store: _FakeGraphStore | None = None,
    *,
    overrides: dict[str, _Override] | None = None,
    connectivity_error: BaseException | None = None,
    batch_size: int = 500,
) -> tuple[Neo4jRepository, _FakeGraphStore]:
    store = store or _FakeGraphStore()
    driver = _FakeDriver(store, overrides=overrides, connectivity_error=connectivity_error)
    repository = Neo4jRepository(driver, database="neo4j", batch_size=batch_size)  # type: ignore[arg-type]
    return repository, store


def _country_application_graph(source_package_hash: str = _HASH_A) -> SemanticGraph:
    country = GraphNode(id="country::AR", labels=[NodeLabel.COUNTRY], properties={"code": "AR"})
    application = GraphNode(
        id="application::AR::TRF", labels=[NodeLabel.APPLICATION], properties={"name": "TRF"}
    )
    relationship = GraphRelationship(
        type=RelationshipType.HAS_APPLICATION,
        from_id=country.id,
        to_id=application.id,
        properties={},
    )
    return SemanticGraph(
        source_package_hash=source_package_hash,
        nodes=sorted([country, application], key=lambda n: n.id),
        relationships=[relationship],
    )


def _parameter_table_graph(source_package_hash: str = _HASH_A) -> SemanticGraph:
    table = GraphNode(
        id="table::AR::default::PARAM_TRANSFER",
        labels=[NodeLabel.TABLE, NodeLabel.PARAMETER_TABLE],
        properties={"name": "PARAM_TRANSFER"},
    )
    entry = GraphNode(
        id="parameter::x::20260515::abc123456789",
        labels=[NodeLabel.PARAMETER_ENTRY],
        properties={"row_hash": "abc123456789"},
    )
    relationship = GraphRelationship(
        type=RelationshipType.HAS_ENTRY, from_id=table.id, to_id=entry.id, properties={}
    )
    return SemanticGraph(
        source_package_hash=source_package_hash,
        nodes=sorted([table, entry], key=lambda n: n.id),
        relationships=[relationship],
    )


# --- compute_edge_key ---


def test_compute_edge_key_is_deterministic() -> None:
    relationship = GraphRelationship(
        type=RelationshipType.HAS_APPLICATION,
        from_id="country::AR",
        to_id="application::AR::TRF",
        properties={"b": 2, "a": 1},
    )
    same_relationship_different_order = GraphRelationship(
        type=RelationshipType.HAS_APPLICATION,
        from_id="country::AR",
        to_id="application::AR::TRF",
        properties={"a": 1, "b": 2},
    )
    assert compute_edge_key(relationship) == compute_edge_key(same_relationship_different_order)


def test_compute_edge_key_differs_on_properties() -> None:
    base = GraphRelationship(
        type=RelationshipType.HAS_APPLICATION,
        from_id="country::AR",
        to_id="application::AR::TRF",
        properties={"a": 1},
    )
    changed = GraphRelationship(
        type=RelationshipType.HAS_APPLICATION,
        from_id="country::AR",
        to_id="application::AR::TRF",
        properties={"a": 2},
    )
    assert compute_edge_key(base) != compute_edge_key(changed)


def test_compute_edge_key_differs_on_endpoints() -> None:
    base = GraphRelationship(
        type=RelationshipType.HAS_APPLICATION,
        from_id="country::AR",
        to_id="application::AR::TRF",
        properties={},
    )
    other_target = GraphRelationship(
        type=RelationshipType.HAS_APPLICATION,
        from_id="country::AR",
        to_id="application::AR::OTRA",
        properties={},
    )
    assert compute_edge_key(base) != compute_edge_key(other_target)


# --- label/type validation (defensa contra interpolacion dinamica) ---


def test_validated_label_clause_accepts_only_known_labels() -> None:
    assert _validated_label_clause(("ParameterTable", "Table")) == ":ParameterTable:Table"


def test_validated_label_clause_rejects_unknown_label() -> None:
    with pytest.raises(GraphLoadError):
        _validated_label_clause(("ParameterTable", "Robado' } ) DETACH DELETE (n"))


def test_validated_relationship_type_rejects_unknown_type() -> None:
    with pytest.raises(GraphLoadError):
        _validated_relationship_type("READS]->() // DROP")


# --- connect() ---


def test_connect_rejects_empty_or_unsupported_uri_scheme() -> None:
    settings = Settings(NEO4J_URI="")
    with pytest.raises(Neo4jConfigurationError):
        Neo4jRepository.connect(settings)

    settings = Settings(NEO4J_URI="http://localhost:7474")
    with pytest.raises(Neo4jConfigurationError):
        Neo4jRepository.connect(settings)


def test_connect_builds_a_lazy_driver_for_a_supported_scheme() -> None:
    settings = Settings(NEO4J_URI="bolt://localhost:7687")
    repository = Neo4jRepository.connect(settings)
    try:
        assert isinstance(repository, Neo4jRepository)
    finally:
        repository.close()


# --- verify_connectivity() ---


def test_verify_connectivity_classifies_auth_error() -> None:
    repository, _ = _repository(connectivity_error=AuthError("credenciales invalidas"))
    with pytest.raises(Neo4jAuthenticationError):
        repository.verify_connectivity()


def test_verify_connectivity_classifies_service_unavailable() -> None:
    error = ServiceUnavailable("no hay ruta al servidor")  # type: ignore[no-untyped-call]
    repository, _ = _repository(connectivity_error=error)
    with pytest.raises(Neo4jUnavailableError):
        repository.verify_connectivity()


def test_verify_connectivity_classifies_timeout() -> None:
    repository, _ = _repository(connectivity_error=TimeoutError("tiempo agotado"))
    with pytest.raises(Neo4jTimeoutError):
        repository.verify_connectivity()


def test_verify_connectivity_classifies_unclassified_error_as_unavailable() -> None:
    repository, _ = _repository(connectivity_error=RuntimeError("algo distinto"))
    with pytest.raises(Neo4jUnavailableError):
        repository.verify_connectivity()


def test_verify_connectivity_succeeds_without_error() -> None:
    repository, _ = _repository()
    repository.verify_connectivity()  # no debe lanzar


# --- server_version() ---


def test_server_version_accepts_major_5() -> None:
    store = _FakeGraphStore(server_version_value="5.24.0")
    repository, _ = _repository(store)
    assert repository.server_version() == "5.24.0"


def test_server_version_rejects_major_4() -> None:
    store = _FakeGraphStore(server_version_value="4.4.9")
    repository, _ = _repository(store)
    with pytest.raises(Neo4jUnsupportedVersionError):
        repository.server_version()


def test_server_version_rejects_malformed_version_string() -> None:
    store = _FakeGraphStore(server_version_value="not-a-version")
    repository, _ = _repository(store)
    with pytest.raises(Neo4jUnsupportedVersionError):
        repository.server_version()


def test_server_version_wraps_cypher_error() -> None:
    repository, _ = _repository(
        overrides={"server_version": lambda *_: (_ for _ in ()).throw(ClientError("boom"))}
    )
    with pytest.raises(Neo4jQueryError):
        repository.server_version()


# --- ensure_schema() ---


def test_ensure_schema_uses_if_not_exists_for_every_node_label_and_metadata() -> None:
    repository, store = _repository()
    repository.ensure_schema()

    for label in NodeLabel:
        assert any(
            f"FOR (n:{label.value})" in statement and "IF NOT EXISTS" in statement
            for statement in store.schema_statements
        ), f"falta constraint IF NOT EXISTS para {label.value}"
    assert any(
        f"FOR (m:{METADATA_LABEL})" in statement and "IF NOT EXISTS" in statement
        for statement in store.schema_statements
    )
    assert any(
        "DataItem" in statement and "semantic_tag" in statement
        for statement in store.schema_statements
    )
    assert not any("apoc" in statement.lower() for statement in store.schema_statements)


# --- load_graph(): carga transaccional, SET exacto, batching, ParameterTable, reemplazo ---


def test_load_graph_creates_nodes_relationships_and_metadata() -> None:
    repository, store = _repository()
    graph = _country_application_graph()

    result = repository.load_graph(graph, semantic_graph_hash=_HASH_A, server_version="5.24.0")

    assert result.node_count == 2
    assert result.relationship_count == 1
    assert store.nodes["country::AR"]["properties"]["_altamira_managed"] is True
    assert store.nodes["country::AR"]["properties"]["_artifact_hash"] == _HASH_A
    assert len(store.relationships) == 1
    (relationship,) = store.relationships.values()
    assert relationship["properties"]["_altamira_managed"] is True
    metadata = store.nodes["active"]["properties"]
    assert metadata["semantic_graph_hash"] == _HASH_A
    assert metadata["node_count"] == 2
    assert metadata["relationship_count"] == 1


def test_load_graph_parameter_table_keeps_both_labels() -> None:
    repository, store = _repository()
    graph = _parameter_table_graph()

    repository.load_graph(graph, semantic_graph_hash=_HASH_A, server_version="5.24.0")

    table_node = store.nodes["table::AR::default::PARAM_TRANSFER"]
    assert table_node["labels"] == {"Table", "ParameterTable"}


def test_load_graph_uses_exact_set_and_discards_stale_properties() -> None:
    repository, store = _repository()
    first_graph = _country_application_graph()
    repository.load_graph(first_graph, semantic_graph_hash=_HASH_A, server_version="5.24.0")
    assert store.nodes["country::AR"]["properties"]["code"] == "AR"

    country = GraphNode(id="country::AR", labels=[NodeLabel.COUNTRY], properties={"other": "x"})
    application = GraphNode(
        id="application::AR::TRF", labels=[NodeLabel.APPLICATION], properties={"name": "TRF"}
    )
    relationship = GraphRelationship(
        type=RelationshipType.HAS_APPLICATION,
        from_id=country.id,
        to_id=application.id,
        properties={},
    )
    second_graph = SemanticGraph(
        source_package_hash=_HASH_B,
        nodes=sorted([country, application], key=lambda n: n.id),
        relationships=[relationship],
    )
    repository.load_graph(second_graph, semantic_graph_hash=_HASH_B, server_version="5.24.0")

    # SET n = row.properties reemplaza por completo: "code" no sobrevive.
    assert "code" not in store.nodes["country::AR"]["properties"]
    assert store.nodes["country::AR"]["properties"]["other"] == "x"


def test_load_graph_replaces_managed_subgraph_completely(monkeypatch: pytest.MonkeyPatch) -> None:
    repository, store = _repository()
    first_graph = _country_application_graph()
    repository.load_graph(first_graph, semantic_graph_hash=_HASH_A, server_version="5.24.0")
    assert set(store.nodes) == {"country::AR", "application::AR::TRF", "active"}

    only_table = GraphNode(
        id="table::AR::default::OTRA_TABLA", labels=[NodeLabel.TABLE], properties={}
    )
    replacement_graph = SemanticGraph(
        source_package_hash=_HASH_B, nodes=[only_table], relationships=[]
    )
    repository.load_graph(replacement_graph, semantic_graph_hash=_HASH_B, server_version="5.24.0")

    assert "country::AR" not in store.nodes
    assert "application::AR::TRF" not in store.nodes
    assert "table::AR::default::OTRA_TABLA" in store.nodes


def test_load_graph_never_touches_foreign_non_managed_nodes() -> None:
    store = _FakeGraphStore()
    store.nodes["foreign::1"] = {
        "labels": {"SomeForeignLabel"},
        "properties": {"id": "foreign::1"},
    }
    repository, store = _repository(store)

    repository.load_graph(
        _country_application_graph(), semantic_graph_hash=_HASH_A, server_version="5.24.0"
    )

    assert "foreign::1" in store.nodes
    assert store.nodes["foreign::1"]["properties"] == {"id": "foreign::1"}


def test_load_graph_batches_node_merge_calls_by_batch_size(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    store = _FakeGraphStore()

    def _counting_merge_nodes(store: _FakeGraphStore, query: str, kwargs: dict[str, Any]) -> None:
        calls.append(len(kwargs["rows"]))
        return _h_merge_nodes(store, query, kwargs)

    repository, store = _repository(
        store, overrides={"merge_nodes": _counting_merge_nodes}, batch_size=1
    )
    graph = _country_application_graph()

    repository.load_graph(graph, semantic_graph_hash=_HASH_A, server_version="5.24.0")

    # 2 nodos con label_clause distinto cada uno (Country, Application):
    # cada grupo tiene 1 nodo, batch_size=1 -> 1 llamada por grupo.
    assert calls == [1, 1]


def test_load_graph_aborts_on_relationship_count_mismatch() -> None:
    store = _FakeGraphStore()
    repository, store = _repository(
        store, overrides={"merge_relationships": lambda *_: _FakeRecord(created=0)}
    )
    graph = _country_application_graph()

    with pytest.raises(GraphLoadError):
        repository.load_graph(graph, semantic_graph_hash=_HASH_A, server_version="5.24.0")


def test_load_graph_rolls_back_completely_on_failure() -> None:
    store = _FakeGraphStore()
    store.nodes["foreign::1"] = {"labels": {"Foreign"}, "properties": {"id": "foreign::1"}}
    repository, store = _repository(
        store, overrides={"merge_relationships": lambda *_: _FakeRecord(created=0)}
    )
    graph = _country_application_graph()

    with pytest.raises(GraphLoadError):
        repository.load_graph(graph, semantic_graph_hash=_HASH_A, server_version="5.24.0")

    # rollback completo: el store queda exactamente como antes de la transaccion.
    assert set(store.nodes) == {"foreign::1"}
    assert store.relationships == {}


@pytest.mark.parametrize(
    "override_shape,override_value",
    [
        ("count_nodes", _FakeRecord(value=999)),
        ("count_relationships", _FakeRecord(value=999)),
        ("missing_ids", _FakeRecord(missing=["algun::id"])),
        ("extra_ids", _FakeRecord(extra=["algun::id::inesperado"])),
        ("missing_edges", _FakeRecord(missing=["algun-edge-key"])),
        ("extra_edges", _FakeRecord(extra=["algun-edge-key-inesperado"])),
        ("bad_parameter_tables", _FakeRecord(value=["table::x"])),
        ("metadata_hash", _FakeRecord(value="hash-equivocado")),
    ],
)
def test_load_graph_verification_failure_aborts_before_commit(
    override_shape: str, override_value: _FakeRecord
) -> None:
    repository, store = _repository(overrides={override_shape: override_value})
    graph = _country_application_graph()

    with pytest.raises(GraphLoadError):
        repository.load_graph(graph, semantic_graph_hash=_HASH_A, server_version="5.24.0")


def test_load_graph_wraps_auth_and_unavailable_errors() -> None:
    store = _FakeGraphStore()

    class _AuthSession(_FakeSession):
        def execute_write(self, fn: Callable[..., Any], *args: Any) -> Any:
            raise AuthError("rechazado")

    class _AuthDriver(_FakeDriver):
        def session(self, *, database: str) -> _FakeSession:
            return _AuthSession(self._store, self._overrides)

    fake_driver = _AuthDriver(store)
    repository = Neo4jRepository(
        fake_driver,  # type: ignore[arg-type]
        database="neo4j",
        batch_size=500,
    )
    with pytest.raises(Neo4jAuthenticationError):
        repository.load_graph(
            _country_application_graph(), semantic_graph_hash=_HASH_A, server_version="5.24.0"
        )


# --- read_active_graph_load() / compute_drift() ---


def test_read_active_graph_load_returns_none_when_absent() -> None:
    repository, _ = _repository()
    assert repository.read_active_graph_load() is None


def test_read_active_graph_load_returns_recorded_metadata() -> None:
    repository, _ = _repository()
    graph = _country_application_graph()
    repository.load_graph(graph, semantic_graph_hash=_HASH_A, server_version="5.24.0")

    active = repository.read_active_graph_load()

    assert active is not None
    assert active.semantic_graph_hash == _HASH_A
    assert active.source_package_hash == graph.source_package_hash
    assert active.node_count == 2
    assert active.relationship_count == 1
    assert active.server_version == "5.24.0"
    assert active.database == "neo4j"


def test_compute_drift_is_clean_immediately_after_load() -> None:
    repository, _ = _repository()
    graph = _country_application_graph()
    repository.load_graph(graph, semantic_graph_hash=_HASH_A, server_version="5.24.0")

    drift = repository.compute_drift(graph)

    assert drift.is_clean
    assert drift.missing_ids == []
    assert drift.extra_ids == []
    assert drift.missing_edge_keys == []
    assert drift.extra_edge_keys == []


def test_compute_drift_detects_manual_deletion() -> None:
    repository, store = _repository()
    graph = _country_application_graph()
    repository.load_graph(graph, semantic_graph_hash=_HASH_A, server_version="5.24.0")

    del store.nodes["application::AR::TRF"]
    for key in list(store.relationships):
        store.relationships.pop(key)

    drift = repository.compute_drift(graph)

    assert not drift.is_clean
    assert "application::AR::TRF" in drift.missing_ids
    assert len(drift.missing_edge_keys) == 1


def test_compute_drift_detects_manual_addition() -> None:
    repository, store = _repository()
    graph = _country_application_graph()
    repository.load_graph(graph, semantic_graph_hash=_HASH_A, server_version="5.24.0")

    store.nodes["intruso::1"] = {
        "labels": {"Country"},
        "properties": {"id": "intruso::1", "_altamira_managed": True},
    }

    drift = repository.compute_drift(graph)

    assert not drift.is_clean
    assert "intruso::1" in drift.extra_ids


# --- run_invariants(): delega la ejecucion, mapea filas, clasifica errores ---


def test_run_invariants_maps_rows_to_dicts() -> None:
    rows = [
        _FakeRecord(code="ORPHAN_DECISION", severity="WARNING", entity_id="d::1", message="msg"),
    ]
    repository, _ = _repository(overrides={"unknown": rows})

    result = repository.run_invariants(
        "UNION ALL fake cypher",
        package_hash=_HASH_A,
        allowed_semantic_tags=["ACCOUNT_NUMBER"],
        allowed_relationship_signatures=[],
    )

    assert result == [
        {"code": "ORPHAN_DECISION", "severity": "WARNING", "entity_id": "d::1", "message": "msg"}
    ]


def test_run_invariants_wraps_cypher_error() -> None:
    def _raise(*_args: object, **_kwargs: object) -> Any:
        raise ClientError("sintaxis invalida")

    repository, _ = _repository(overrides={"unknown": _raise})

    with pytest.raises(Neo4jQueryError):
        repository.run_invariants(
            "UNION ALL fake cypher",
            package_hash=_HASH_A,
            allowed_semantic_tags=[],
            allowed_relationship_signatures=[],
        )
