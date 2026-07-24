"""Orquestacion de la etapa SEMANTIC_GRAPH_LOADED:
SEMANTIC_GRAPH_BUILT -> Neo4j (subgrafo administrado por Altamira).

Fuente exclusiva: `artifacts/04-semantic-graph.json`. No reconstruye el
grafo desde artefactos anteriores, no invoca el JAR, no rederiva
dependencias ni vuelve a ejecutar SemanticTagger/DomainTermMapper —
unicamente lee el artefacto ya persistido y lo traduce a Neo4j via
`Neo4jRepository`.

Idempotencia: la carga SIEMPRE reemplaza transaccionalmente el subgrafo
administrado (`_altamira_managed=true`) por el contenido actual del
artefacto. Deliberadamente NO se implementa una optimizacion de "skip
load" basada en RunState o en el nodo `AltamiraGraphLoad`: recargar
siempre permite reparar drift o modificaciones manuales en Neo4j sin
depender de una senal que podria estar obsoleta.

Cualquier `Neo4jError` (configuracion, autenticacion, servidor no
disponible, timeout, version no soportada, error de consulta) se
traduce aqui a `GraphLoadError`: son jerarquias de excepcion distintas
(`Neo4jError` no hereda de `GraphLoadError`) y `runner.py` solo atrapa
`GraphLoadError` para esta etapa, igual que las etapas anteriores solo
exponen su propio tipo de error de dominio al runner.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..config import Settings
from ..contracts.enums import PipelineStage, StageStatus
from ..contracts.run_state import StageExecution
from ..contracts.semantic_graph import SemanticGraph
from .errors import GraphLoadError, Neo4jError
from .neo4j_repository import GraphLoadResult, Neo4jRepository


def _verify_semantic_graph_built_precondition(stages: list[StageExecution]) -> None:
    matches = [stage for stage in stages if stage.stage == PipelineStage.SEMANTIC_GRAPH_BUILT]
    if len(matches) != 1:
        raise GraphLoadError(
            "SEMANTIC_GRAPH_BUILT debe tener exactamente una StageExecution en RunState; "
            f"se encontraron {len(matches)}"
        )
    if matches[0].status != StageStatus.SUCCEEDED:
        raise GraphLoadError(
            f"SEMANTIC_GRAPH_BUILT no esta SUCCEEDED (status={matches[0].status.value}); no "
            "se puede cargar un grafo semantico incompleto"
        )


def load_and_validate_semantic_graph(semantic_graph_path: Path) -> tuple[SemanticGraph, str]:
    """Relee `04-semantic-graph.json`, calcula su SHA-256 real y lo valida
    contra `SemanticGraph`. Reutilizado tambien por `graph_validated_stage.py`
    (misma verificacion, misma fuente unica de verdad)."""
    if not semantic_graph_path.is_file():
        raise GraphLoadError(f"no se encontro {semantic_graph_path.name}")
    raw_bytes = semantic_graph_path.read_bytes()
    semantic_graph_hash = hashlib.sha256(raw_bytes).hexdigest()
    try:
        graph = SemanticGraph.model_validate_json(raw_bytes.decode("utf-8"))
    except ValueError as exc:
        raise GraphLoadError(
            f"{semantic_graph_path.name} no valida contra SemanticGraph: {exc}"
        ) from exc
    return graph, semantic_graph_hash


def run_semantic_graph_load_stage(
    *,
    run_stages: list[StageExecution],
    semantic_graph_path: Path,
    settings: Settings,
) -> GraphLoadResult:
    """Ejecuta SEMANTIC_GRAPH_LOADED: verifica conectividad y version del
    servidor, crea el esquema (constraints/indices, `IF NOT EXISTS`) y
    reemplaza transaccionalmente el subgrafo administrado por Altamira.
    """
    _verify_semantic_graph_built_precondition(run_stages)
    graph, semantic_graph_hash = load_and_validate_semantic_graph(semantic_graph_path)

    repository: Neo4jRepository | None = None
    try:
        repository = Neo4jRepository.connect(settings)
        repository.verify_connectivity()
        server_version = repository.server_version()
        repository.ensure_schema()
        return repository.load_graph(
            graph, semantic_graph_hash=semantic_graph_hash, server_version=server_version
        )
    except Neo4jError as exc:
        raise GraphLoadError(str(exc)) from exc
    finally:
        if repository is not None:
            repository.close()
