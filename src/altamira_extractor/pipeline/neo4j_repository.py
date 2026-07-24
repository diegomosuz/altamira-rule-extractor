"""Neo4jRepository (Prompt 9): conexion, esquema (constraints/indices) y
carga transaccional del SemanticGraph administrado por Altamira.

V1 no soporta coexistencia de varios paquetes Altamira en la misma base:
los IDs actuales (Country/Application/Operation compartidos entre
paquetes; Program/Paragraph/DataItem/Decision derivados de source_hash
del COBOL, no de source_package_hash; Table/ParameterTable derivables de
nombre/schema/snapshot) no garantizan ownership exclusivo por paquete, y
`source_package_hash` es una propiedad escalar, no una lista. "Multi-
package coexistence requires package-scoped identities or an explicit
ownership model and is not supported by the current semantic graph
contract."

Por eso una base Neo4j contiene un unico grafo semantico Altamira activo:
cada carga reemplaza COMPLETAMENTE el subgrafo marcado con la propiedad
interna `_altamira_managed=true` (nunca toca nodos/relaciones ajenos a la
aplicacion), dentro de una unica transaccion (limpieza + reconstruccion +
verificacion), de forma que cualquier fallo produce rollback completo y
nunca queda una carga parcial marcada como exitosa.

Metadata operativa: un unico nodo `(:AltamiraGraphLoad {id: "active"})`
(label constante definida aqui, nunca proveniente de NodeLabel ni de
entrada externa) registra evidencia de la ultima carga exitosa. No es un
GraphNode del metamodelo: se excluye explicitamente de los conteos
semanticos y no participa en relaciones de ownership.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol

import neo4j
from neo4j.exceptions import AuthError, ClientError, CypherSyntaxError, ServiceUnavailable

from ..config import Settings
from ..contracts.enums import NodeLabel, RelationshipType
from ..contracts.semantic_graph import GraphNode, GraphRelationship, SemanticGraph
from .errors import (
    GraphLoadError,
    Neo4jAuthenticationError,
    Neo4jConfigurationError,
    Neo4jQueryError,
    Neo4jTimeoutError,
    Neo4jUnavailableError,
    Neo4jUnsupportedVersionError,
)

METADATA_LABEL = "AltamiraGraphLoad"
_METADATA_ID = "active"
_MANAGED_FLAG = "_altamira_managed"
_ARTIFACT_HASH_PROPERTY = "_artifact_hash"
_EDGE_KEY_PROPERTY = "_edge_key"
_SUPPORTED_SERVER_MAJOR_VERSION = 5
_SUPPORTED_URI_SCHEMES = ("bolt://", "bolt+s://", "neo4j://", "neo4j+s://")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compute_edge_key(relationship: GraphRelationship) -> str:
    """SHA-256 determinístico de (type, from_id, to_id, properties) — usa
    exactamente las properties del artefacto, sin metadata interna."""
    payload = {
        "type": relationship.type.value,
        "from_id": relationship.from_id,
        "to_id": relationship.to_id,
        "properties": relationship.properties,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class GraphLoadResult:
    node_count: int
    relationship_count: int
    server_version: str
    database: str


@dataclass(frozen=True)
class ActiveGraphLoad:
    """Lectura del nodo `(:AltamiraGraphLoad {id: "active"})`."""

    semantic_graph_hash: str
    source_package_hash: str
    node_count: int
    relationship_count: int
    server_version: str
    database: str


@dataclass(frozen=True)
class GraphDrift:
    """Diferencias entre el `SemanticGraph` actual y el estado real de
    Neo4j (usado por GRAPH_VALIDATED para detectar drift)."""

    missing_ids: list[str]
    extra_ids: list[str]
    missing_edge_keys: list[str]
    extra_edge_keys: list[str]

    @property
    def is_clean(self) -> bool:
        return not (
            self.missing_ids or self.extra_ids or self.missing_edge_keys or self.extra_edge_keys
        )


class _CypherRunner(Protocol):
    """Interfaz comun de `neo4j.Session`/`neo4j.ManagedTransaction`: ambos
    exponen `.run(query, **params)`, suficiente para las consultas de
    lectura compartidas entre carga y verificacion."""

    def run(self, query: str, **kwargs: Any) -> neo4j.Result: ...


def _chunk(items: list[Any], size: int) -> Iterator[list[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _group_nodes_by_labels(nodes: list[GraphNode]) -> dict[tuple[str, ...], list[GraphNode]]:
    groups: dict[tuple[str, ...], list[GraphNode]] = {}
    for node in nodes:
        key = tuple(sorted(label.value for label in node.labels))
        groups.setdefault(key, []).append(node)
    return groups


def _group_relationships_by_type(
    relationships: list[GraphRelationship],
) -> dict[str, list[GraphRelationship]]:
    groups: dict[str, list[GraphRelationship]] = {}
    for relationship in relationships:
        groups.setdefault(relationship.type.value, []).append(relationship)
    return groups


_ALLOWED_NODE_LABELS = frozenset(label.value for label in NodeLabel)
_ALLOWED_RELATIONSHIP_TYPES = frozenset(member.value for member in RelationshipType)


def _validated_label_clause(labels: tuple[str, ...]) -> str:
    """Construye `:Label1:Label2` solo a partir de labels ya validados
    contra NodeLabel — nunca desde properties/JSON/entrada libre."""
    for label in labels:
        if label not in _ALLOWED_NODE_LABELS:
            raise GraphLoadError(
                f"label no permitido detectado antes de construir Cypher: {label!r}"
            )
    return "".join(f":{label}" for label in labels)


def _validated_relationship_type(value: str) -> str:
    if value not in _ALLOWED_RELATIONSHIP_TYPES:
        raise GraphLoadError(
            f"tipo de relacion no permitido detectado antes de construir Cypher: {value!r}"
        )
    return value


def _looks_like_timeout(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    return "timeout" in type(exc).__name__.lower() or "timeout" in str(exc).lower()


def _single_record(runner: _CypherRunner, query: str, **params: object) -> neo4j.Record:
    record = runner.run(query, **params).single()
    if record is None:
        raise Neo4jQueryError(
            "la consulta no devolvio ningun resultado (se esperaba exactamente uno)"
        )
    return record


def _single_value(runner: _CypherRunner, query: str, **params: object) -> Any:
    return _single_record(runner, query, **params)[0]


class Neo4jRepository:
    def __init__(self, driver: neo4j.Driver, *, database: str, batch_size: int) -> None:
        self._driver = driver
        self._database = database
        self._batch_size = batch_size

    @classmethod
    def connect(cls, settings: Settings) -> Neo4jRepository:
        uri = settings.neo4j_uri
        if not uri or not uri.startswith(_SUPPORTED_URI_SCHEMES):
            raise Neo4jConfigurationError(
                "neo4j_uri vacio o con esquema no soportado "
                f"(se espera uno de {_SUPPORTED_URI_SCHEMES})"
            )
        try:
            driver = neo4j.GraphDatabase.driver(
                uri,
                auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
                connection_timeout=settings.neo4j_connection_timeout_seconds,
                max_transaction_retry_time=settings.neo4j_max_transaction_retry_time_seconds,
            )
        except Exception as exc:
            raise Neo4jConfigurationError(
                f"no se pudo construir el driver Neo4j: {type(exc).__name__}"
            ) from exc
        return cls(
            driver, database=settings.neo4j_database, batch_size=settings.neo4j_load_batch_size
        )

    def close(self) -> None:
        self._driver.close()

    def __enter__(self) -> Neo4jRepository:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # --- Conectividad / version ---

    def verify_connectivity(self) -> None:
        try:
            self._driver.verify_connectivity()
        except AuthError as exc:
            raise Neo4jAuthenticationError("credenciales rechazadas por el servidor Neo4j") from exc
        except ServiceUnavailable as exc:
            raise Neo4jUnavailableError("el servidor Neo4j no esta disponible") from exc
        except Exception as exc:
            if _looks_like_timeout(exc):
                raise Neo4jTimeoutError("tiempo de espera agotado al conectar con Neo4j") from exc
            raise Neo4jUnavailableError(
                f"fallo de conectividad no clasificado: {type(exc).__name__}"
            ) from exc

    def server_version(self) -> str:
        try:
            with self._driver.session(database=self._database) as session:
                version = _single_value(
                    session,
                    "CALL dbms.components() YIELD versions RETURN versions[0] AS version",
                )
        except AuthError as exc:
            raise Neo4jAuthenticationError("credenciales rechazadas por el servidor Neo4j") from exc
        except ServiceUnavailable as exc:
            raise Neo4jUnavailableError("el servidor Neo4j no esta disponible") from exc
        except (ClientError, CypherSyntaxError) as exc:
            raise Neo4jQueryError(
                f"no se pudo obtener la version del servidor: {type(exc).__name__}"
            ) from exc
        if version is None:
            raise Neo4jQueryError("dbms.components() no devolvio resultados")
        version_text = str(version)
        major = version_text.split(".")[0]
        if not major.isdigit() or int(major) != _SUPPORTED_SERVER_MAJOR_VERSION:
            raise Neo4jUnsupportedVersionError(
                f"version de servidor no soportada: {version_text} (se requiere major "
                f"{_SUPPORTED_SERVER_MAJOR_VERSION})"
            )
        return version_text

    # --- Esquema ---

    def ensure_schema(self) -> None:
        statements = [
            f"CREATE CONSTRAINT altamira_{label.value.lower()}_id IF NOT EXISTS "
            f"FOR (n:{label.value}) REQUIRE n.id IS UNIQUE"
            for label in NodeLabel
        ]
        statements.append(
            f"CREATE CONSTRAINT altamira_graph_load_id IF NOT EXISTS "
            f"FOR (m:{METADATA_LABEL}) REQUIRE m.id IS UNIQUE"
        )
        for label in (
            NodeLabel.PROGRAM,
            NodeLabel.PARAGRAPH,
            NodeLabel.DATA_ITEM,
            NodeLabel.DECISION,
            NodeLabel.PARAMETER_TABLE,
            NodeLabel.PARAMETER_ENTRY,
        ):
            statements.append(
                f"CREATE INDEX altamira_{label.value.lower()}_source_package_hash IF NOT EXISTS "
                f"FOR (n:{label.value}) ON (n.source_package_hash)"
            )
        statements.append(
            "CREATE INDEX altamira_data_item_semantic_tag IF NOT EXISTS "
            "FOR (n:DataItem) ON (n.semantic_tag)"
        )
        try:
            with self._driver.session(database=self._database) as session:
                for statement in statements:
                    session.run(statement).consume()
        except AuthError as exc:
            raise Neo4jAuthenticationError("credenciales rechazadas por el servidor Neo4j") from exc
        except ServiceUnavailable as exc:
            raise Neo4jUnavailableError("el servidor Neo4j no esta disponible") from exc
        except (ClientError, CypherSyntaxError) as exc:
            raise Neo4jQueryError(
                f"fallo creando constraints/indices: {type(exc).__name__}"
            ) from exc

    # --- Carga transaccional ---

    def load_graph(
        self, graph: SemanticGraph, *, semantic_graph_hash: str, server_version: str
    ) -> GraphLoadResult:
        try:
            with self._driver.session(database=self._database) as session:
                return session.execute_write(
                    _load_transaction,
                    graph,
                    semantic_graph_hash,
                    server_version,
                    self._database,
                    self._batch_size,
                )
        except AuthError as exc:
            raise Neo4jAuthenticationError("credenciales rechazadas por el servidor Neo4j") from exc
        except ServiceUnavailable as exc:
            raise Neo4jUnavailableError("el servidor Neo4j no esta disponible") from exc
        except (ClientError, CypherSyntaxError) as exc:
            raise Neo4jQueryError(
                f"fallo ejecutando la carga transaccional: {type(exc).__name__}"
            ) from exc

    # --- Lectura para GRAPH_VALIDATED ---

    def read_active_graph_load(self) -> ActiveGraphLoad | None:
        try:
            with self._driver.session(database=self._database) as session:
                record = session.run(
                    f"MATCH (m:{METADATA_LABEL} {{id: $id}}) RETURN m AS m", id=_METADATA_ID
                ).single()
        except AuthError as exc:
            raise Neo4jAuthenticationError("credenciales rechazadas por el servidor Neo4j") from exc
        except ServiceUnavailable as exc:
            raise Neo4jUnavailableError("el servidor Neo4j no esta disponible") from exc
        except (ClientError, CypherSyntaxError) as exc:
            raise Neo4jQueryError(
                f"fallo leyendo AltamiraGraphLoad: {type(exc).__name__}"
            ) from exc
        if record is None:
            return None
        node = record["m"]
        return ActiveGraphLoad(
            semantic_graph_hash=str(node["semantic_graph_hash"]),
            source_package_hash=str(node["source_package_hash"]),
            node_count=int(node["node_count"]),
            relationship_count=int(node["relationship_count"]),
            server_version=str(node["server_version"]),
            database=str(node["database"]),
        )

    def compute_drift(self, graph: SemanticGraph) -> GraphDrift:
        expected_ids = [node.id for node in graph.nodes]
        expected_edge_keys = [
            compute_edge_key(relationship) for relationship in graph.relationships
        ]
        try:
            with self._driver.session(database=self._database) as session:
                missing_ids, extra_ids, missing_edges, extra_edges = _diff_ids_and_edges(
                    session, expected_ids, expected_edge_keys
                )
        except AuthError as exc:
            raise Neo4jAuthenticationError("credenciales rechazadas por el servidor Neo4j") from exc
        except ServiceUnavailable as exc:
            raise Neo4jUnavailableError("el servidor Neo4j no esta disponible") from exc
        except (ClientError, CypherSyntaxError) as exc:
            raise Neo4jQueryError(f"fallo calculando drift: {type(exc).__name__}") from exc
        return GraphDrift(
            missing_ids=missing_ids,
            extra_ids=extra_ids,
            missing_edge_keys=missing_edges,
            extra_edge_keys=extra_edges,
        )

    # --- Invariantes ---

    def run_invariants(
        self,
        cypher_text: str,
        *,
        package_hash: str,
        allowed_semantic_tags: list[str],
        allowed_relationship_signatures: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        try:
            with self._driver.session(database=self._database) as session:
                result = session.run(
                    cypher_text,
                    package_hash=package_hash,
                    allowed_semantic_tags=allowed_semantic_tags,
                    allowed_relationship_signatures=allowed_relationship_signatures,
                )
                records = [
                    {
                        "code": str(record["code"]),
                        "severity": str(record["severity"]),
                        "entity_id": str(record["entity_id"]),
                        "message": str(record["message"]),
                    }
                    for record in result
                ]
        except AuthError as exc:
            raise Neo4jAuthenticationError("credenciales rechazadas por el servidor Neo4j") from exc
        except ServiceUnavailable as exc:
            raise Neo4jUnavailableError("el servidor Neo4j no esta disponible") from exc
        except (ClientError, CypherSyntaxError) as exc:
            raise Neo4jQueryError(
                f"fallo ejecutando invariants.cypher: {type(exc).__name__}"
            ) from exc
        return records

    # --- Deteccion de candidatos (Q0) ---

    def run_candidate_query(
        self, cypher_text: str, *, package_hash: str
    ) -> list[dict[str, Any]]:
        """Ejecuta una consulta de deteccion de candidatos (Q0), parametrizada
        unicamente por `package_hash`. Devuelve cada fila como un dict
        columna->valor sin interpretar su forma: `CandidateDetector` es quien
        mapea las filas a `RuleCandidate`, no este repositorio."""
        try:
            with self._driver.session(database=self._database) as session:
                result = session.run(cypher_text, package_hash=package_hash)
                rows = [dict(record) for record in result]
        except AuthError as exc:
            raise Neo4jAuthenticationError("credenciales rechazadas por el servidor Neo4j") from exc
        except ServiceUnavailable as exc:
            raise Neo4jUnavailableError("el servidor Neo4j no esta disponible") from exc
        except (ClientError, CypherSyntaxError) as exc:
            raise Neo4jQueryError(
                f"fallo ejecutando q0_candidates.cypher: {type(exc).__name__}"
            ) from exc
        return rows


def _diff_ids_and_edges(
    runner: _CypherRunner, expected_ids: list[str], expected_edge_keys: list[str]
) -> tuple[list[str], list[str], list[str], list[str]]:
    missing_ids = _single_record(
        runner,
        f"""
        UNWIND $ids AS expected_id
        OPTIONAL MATCH (n {{id: expected_id}}) WHERE n.{_MANAGED_FLAG} = true
        WITH expected_id, n WHERE n IS NULL
        RETURN collect(expected_id) AS missing
        """,
        ids=expected_ids,
    )["missing"]

    extra_ids = _single_record(
        runner,
        f"""
        MATCH (n) WHERE n.{_MANAGED_FLAG} = true AND NOT n:{METADATA_LABEL}
          AND NOT n.id IN $ids
        RETURN collect(n.id) AS extra
        """,
        ids=expected_ids,
    )["extra"]

    missing_edges = _single_record(
        runner,
        f"""
        UNWIND $keys AS expected_key
        OPTIONAL MATCH ()-[r {{{_EDGE_KEY_PROPERTY}: expected_key}}]->()
        WHERE r.{_MANAGED_FLAG} = true
        WITH expected_key, r WHERE r IS NULL
        RETURN collect(expected_key) AS missing
        """,
        keys=expected_edge_keys,
    )["missing"]

    extra_edges = _single_record(
        runner,
        f"""
        MATCH ()-[r]->() WHERE r.{_MANAGED_FLAG} = true
          AND NOT r.{_EDGE_KEY_PROPERTY} IN $keys
        RETURN collect(r.{_EDGE_KEY_PROPERTY}) AS extra
        """,
        keys=expected_edge_keys,
    )["extra"]

    return list(missing_ids), list(extra_ids), list(missing_edges), list(extra_edges)


def _load_transaction(
    tx: neo4j.ManagedTransaction,
    graph: SemanticGraph,
    semantic_graph_hash: str,
    server_version: str,
    database: str,
    batch_size: int,
) -> GraphLoadResult:
    # 1. elimina el AltamiraGraphLoad activo anterior.
    tx.run(f"MATCH (m:{METADATA_LABEL} {{id: $id}}) DETACH DELETE m", id=_METADATA_ID).consume()

    # 2. elimina todos los nodos administrados (DETACH DELETE tambien
    #    elimina sus relaciones). Nunca toca nodos sin _altamira_managed.
    tx.run(f"MATCH (n) WHERE n.{_MANAGED_FLAG} = true DETACH DELETE n").consume()

    # 3. carga todos los nodos semanticos, agrupados por combinacion
    #    exacta y ordenada de labels (ya validados contra NodeLabel).
    for label_tuple, nodes in _group_nodes_by_labels(graph.nodes).items():
        label_clause = _validated_label_clause(label_tuple)
        query = f"""
        UNWIND $rows AS row
        MERGE (n{label_clause} {{id: row.id}})
        SET n = row.properties
        SET n.id = row.id
        SET n.{_MANAGED_FLAG} = true
        SET n.{_ARTIFACT_HASH_PROPERTY} = $semantic_graph_hash
        """
        for batch in _chunk(nodes, batch_size):
            rows = [{"id": node.id, "properties": node.properties} for node in batch]
            tx.run(query, rows=rows, semantic_graph_hash=semantic_graph_hash).consume()

    # 4. carga todas las relaciones, agrupadas por RelationshipType (ya
    #    validado). Exige que ambos endpoints ya existan y esten
    #    administrados; si el conteo creado no coincide con el batch
    #    esperado, aborta (el endpoint faltante indica inconsistencia).
    for rel_type_value, relationships in _group_relationships_by_type(graph.relationships).items():
        validated_type = _validated_relationship_type(rel_type_value)
        query = f"""
        UNWIND $rows AS row
        MATCH (a {{id: row.from_id}}) WHERE a.{_MANAGED_FLAG} = true
        MATCH (b {{id: row.to_id}}) WHERE b.{_MANAGED_FLAG} = true
        MERGE (a)-[r:{validated_type} {{{_EDGE_KEY_PROPERTY}: row.edge_key}}]->(b)
        SET r = row.properties
        SET r.{_EDGE_KEY_PROPERTY} = row.edge_key
        SET r.{_MANAGED_FLAG} = true
        SET r.{_ARTIFACT_HASH_PROPERTY} = $semantic_graph_hash
        RETURN count(r) AS created
        """
        for batch in _chunk(relationships, batch_size):
            rows = [
                {
                    "from_id": relationship.from_id,
                    "to_id": relationship.to_id,
                    "edge_key": compute_edge_key(relationship),
                    "properties": relationship.properties,
                }
                for relationship in batch
            ]
            record = tx.run(query, rows=rows, semantic_graph_hash=semantic_graph_hash).single()
            created = int(record["created"]) if record is not None else 0
            if created != len(batch):
                raise GraphLoadError(
                    f"se esperaban {len(batch)} relaciones {validated_type}, se crearon "
                    f"{created} (algun endpoint from_id/to_id no existe o no esta administrado)"
                )

    # 5. crea AltamiraGraphLoad (metadata operativa, fuera del metamodelo).
    tx.run(
        f"""
        MERGE (m:{METADATA_LABEL} {{id: $id}})
        SET m = $properties
        SET m.id = $id
        SET m.{_MANAGED_FLAG} = true
        """,
        id=_METADATA_ID,
        properties={
            "semantic_graph_hash": semantic_graph_hash,
            "source_package_hash": graph.source_package_hash,
            "node_count": len(graph.nodes),
            "relationship_count": len(graph.relationships),
            "server_version": server_version,
            "database": database,
        },
    ).consume()

    # 6. verifica el resultado antes del commit.
    _verify_load(tx, graph, semantic_graph_hash)

    return GraphLoadResult(
        node_count=len(graph.nodes),
        relationship_count=len(graph.relationships),
        server_version=server_version,
        database=database,
    )


def _verify_load(
    tx: neo4j.ManagedTransaction, graph: SemanticGraph, semantic_graph_hash: str
) -> None:
    actual_node_count = _single_value(
        tx, f"MATCH (n) WHERE n.{_MANAGED_FLAG} = true AND NOT n:{METADATA_LABEL} RETURN count(n)"
    )
    if actual_node_count != len(graph.nodes):
        raise GraphLoadError(
            f"conteo de nodos administrados no coincide: esperado {len(graph.nodes)}, "
            f"real {actual_node_count}"
        )

    actual_relationship_count = _single_value(
        tx, f"MATCH ()-[r]->() WHERE r.{_MANAGED_FLAG} = true RETURN count(r)"
    )
    if actual_relationship_count != len(graph.relationships):
        raise GraphLoadError(
            f"conteo de relaciones administradas no coincide: esperado "
            f"{len(graph.relationships)}, real {actual_relationship_count}"
        )

    expected_ids = [node.id for node in graph.nodes]
    expected_edge_keys = [compute_edge_key(relationship) for relationship in graph.relationships]
    missing_ids, extra_ids, missing_edges, extra_edges = _diff_ids_and_edges(
        tx, expected_ids, expected_edge_keys
    )
    if missing_ids:
        raise GraphLoadError(f"IDs esperados ausentes tras la carga: {sorted(missing_ids)[:5]}")
    if extra_ids:
        raise GraphLoadError(
            f"IDs administrados inesperados tras la carga: {sorted(extra_ids)[:5]}"
        )
    if missing_edges:
        raise GraphLoadError("edge_key esperados ausentes tras la carga")
    if extra_edges:
        raise GraphLoadError("edge_key administrados inesperados tras la carga")

    bad_parameter_tables = _single_value(
        tx,
        f"MATCH (t:ParameterTable) WHERE t.{_MANAGED_FLAG} = true AND NOT t:Table "
        "RETURN collect(t.id)",
    )
    if bad_parameter_tables:
        raise GraphLoadError(
            f"ParameterTable sin label Table tras la carga: {sorted(bad_parameter_tables)[:5]}"
        )

    metadata_hash = _single_value(
        tx,
        f"MATCH (m:{METADATA_LABEL} {{id: $id}}) RETURN m.semantic_graph_hash",
        id=_METADATA_ID,
    )
    if metadata_hash != semantic_graph_hash:
        raise GraphLoadError("el nodo AltamiraGraphLoad no refleja el semantic_graph_hash cargado")
