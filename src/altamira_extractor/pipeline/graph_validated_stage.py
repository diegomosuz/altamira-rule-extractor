"""Orquestacion de la etapa GRAPH_VALIDATED:
SEMANTIC_GRAPH_LOADED -> artifacts/05-invariants.json.

Antes de ejecutar `invariants.cypher`, detecta drift entre
`04-semantic-graph.json` y el estado real de Neo4j (archivo cambiado
despues de cargar, base modificada manualmente, carga distinta, metadata
obsoleta, relaciones agregadas/eliminadas fuera de banda) comparando el
`SemanticGraph` releido contra el nodo `AltamiraGraphLoad` activo y
recalculando IDs/`_edge_key`. Nunca intenta reparar: la reparacion
pertenece a SEMANTIC_GRAPH_LOADED.

Un invariante ERROR incumplido bloquea `GRAPH_VALIDATED` (persiste
`05-invariants.json` con `graph_validated=false` y falla la etapa); solo
WARNING o ningun incumplimiento permite `graph_validated=true`.

Cualquier `GraphLoadError` al releer `04-semantic-graph.json` (el archivo
desaparecio o quedo corrupto despues de SEMANTIC_GRAPH_LOADED) y
cualquier `Neo4jError` (configuracion, autenticacion, servidor no
disponible, timeout, version no soportada, error de consulta) se
traducen aqui a `GraphValidationError`: `runner.py` solo atrapa
`GraphValidationError` para esta etapa, igual que las demas etapas solo
exponen su propio tipo de error de dominio al runner.
"""

from __future__ import annotations

from pathlib import Path

from ..config import Settings
from ..contracts.enums import PipelineStage, Severity, StageStatus
from ..contracts.invariants import InvariantArtifact
from ..contracts.run_state import StageExecution
from ..contracts.semantic_enrichment import SemanticEnrichmentArtifact
from .artifact_store import atomic_write_json
from .errors import GraphLoadError, GraphValidationError, Neo4jError
from .graph_invariant_validator import run_invariants
from .neo4j_repository import Neo4jRepository
from .semantic_graph_load_stage import load_and_validate_semantic_graph


def _verify_semantic_graph_loaded_precondition(stages: list[StageExecution]) -> None:
    matches = [stage for stage in stages if stage.stage == PipelineStage.SEMANTIC_GRAPH_LOADED]
    if len(matches) != 1:
        raise GraphValidationError(
            "SEMANTIC_GRAPH_LOADED debe tener exactamente una StageExecution en RunState; "
            f"se encontraron {len(matches)}"
        )
    if matches[0].status != StageStatus.SUCCEEDED:
        raise GraphValidationError(
            f"SEMANTIC_GRAPH_LOADED no esta SUCCEEDED (status={matches[0].status.value}); no "
            "se puede validar un grafo que no se cargo con exito"
        )


def _read_semantic_tags_config_hash(semantic_enrichment_path: Path) -> str:
    if not semantic_enrichment_path.is_file():
        raise GraphValidationError(f"no se encontro {semantic_enrichment_path.name}")
    try:
        artifact = SemanticEnrichmentArtifact.model_validate_json(
            semantic_enrichment_path.read_text(encoding="utf-8")
        )
    except ValueError as exc:
        raise GraphValidationError(
            f"{semantic_enrichment_path.name} no valida contra SemanticEnrichmentArtifact: {exc}"
        ) from exc
    return artifact.semantic_tags_config_hash


def run_graph_validated_stage(
    *,
    run_id: str,
    source_package_hash: str,
    run_stages: list[StageExecution],
    semantic_graph_path: Path,
    semantic_enrichment_path: Path,
    invariants_cypher_path: Path,
    invariants_path: Path,
    settings: Settings,
) -> list[str]:
    """Ejecuta GRAPH_VALIDATED y devuelve los warnings (resumen para
    RunState; el detalle completo vive en `invariants_path`)."""
    _verify_semantic_graph_loaded_precondition(run_stages)

    try:
        graph, semantic_graph_hash = load_and_validate_semantic_graph(semantic_graph_path)
    except GraphLoadError as exc:
        raise GraphValidationError(
            f"04-semantic-graph.json ya no se puede releer/validar: {exc}"
        ) from exc
    semantic_tags_config_hash = _read_semantic_tags_config_hash(semantic_enrichment_path)

    repository: Neo4jRepository | None = None
    try:
        repository = Neo4jRepository.connect(settings)
        repository.verify_connectivity()

        active = repository.read_active_graph_load()
        if active is None:
            raise GraphValidationError(
                "no existe un nodo AltamiraGraphLoad activo en Neo4j; SEMANTIC_GRAPH_LOADED "
                "no dejo evidencia de carga"
            )
        if active.semantic_graph_hash != semantic_graph_hash:
            raise GraphValidationError(
                "semantic_graph_hash no coincide con la carga activa (el artefacto cambio "
                "despues de cargar, o Neo4j contiene una carga distinta)"
            )
        if active.source_package_hash != source_package_hash:
            raise GraphValidationError(
                "source_package_hash no coincide con la carga activa"
            )
        if active.node_count != len(graph.nodes):
            raise GraphValidationError(
                f"node_count de la carga activa ({active.node_count}) no coincide con "
                f"04-semantic-graph.json ({len(graph.nodes)})"
            )
        if active.relationship_count != len(graph.relationships):
            raise GraphValidationError(
                f"relationship_count de la carga activa ({active.relationship_count}) no "
                f"coincide con 04-semantic-graph.json ({len(graph.relationships)})"
            )

        drift = repository.compute_drift(graph)
        if not drift.is_clean:
            raise GraphValidationError(
                "drift detectado entre 04-semantic-graph.json y el estado real de Neo4j "
                f"(faltantes: {len(drift.missing_ids)} nodos/{len(drift.missing_edge_keys)} "
                f"relaciones; inesperados: {len(drift.extra_ids)} nodos/"
                f"{len(drift.extra_edge_keys)} relaciones)"
            )

        violations, invariants_query_hash = run_invariants(
            repository,
            invariants_cypher_path=invariants_cypher_path,
            package_hash=source_package_hash,
            semantic_tags_path=settings.semantic_tags_path,
            expected_semantic_tags_config_hash=semantic_tags_config_hash,
        )
    except Neo4jError as exc:
        raise GraphValidationError(str(exc)) from exc
    finally:
        if repository is not None:
            repository.close()

    error_count = sum(1 for violation in violations if violation.severity == Severity.ERROR)
    warning_count = sum(1 for violation in violations if violation.severity == Severity.WARNING)
    graph_validated = error_count == 0

    warning_summary = sorted(
        {
            f"{violation.code}: {violation.message} ({violation.entity_id})"
            for violation in violations
            if violation.severity == Severity.WARNING
        }
    )

    artifact = InvariantArtifact(
        run_id=run_id,
        source_package_hash=source_package_hash,
        semantic_graph_hash=semantic_graph_hash,
        invariants_query_hash=invariants_query_hash,
        neo4j_database=active.database,
        neo4j_server_version=active.server_version,
        node_count=active.node_count,
        relationship_count=active.relationship_count,
        violations=violations,
        error_count=error_count,
        warning_count=warning_count,
        graph_validated=graph_validated,
        warnings=warning_summary,
    )
    atomic_write_json(invariants_path, artifact)

    if not graph_validated:
        raise GraphValidationError(
            f"{error_count} invariante(s) de severidad ERROR incumplido(s); ver "
            f"{invariants_path.name}"
        )

    return list(artifact.warnings)
