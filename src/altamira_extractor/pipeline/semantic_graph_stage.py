"""Orquestacion de la etapa SEMANTIC_GRAPH_BUILT:
SEMANTIC_ENRICHMENT_BUILT -> artifacts/04-semantic-graph.json.

Precondiciones (todas fatales si fallan: `SemanticGraphBuildError`, sin
reintento parcial):

- SEMANTIC_ENRICHMENT_BUILT tiene exactamente una `StageExecution`
  `SUCCEEDED` en `RunState.stages`.
- Existe un `CanonicalProgram` valido por cada `InventoryFile` COBOL,
  consistente con Inventory/RunState (reutiliza la misma verificacion que
  `dependencies_stage.py`/`semantic_enrichment_stage.py`).
- `artifacts/03-dependencies.json` existe, valida como `DependencyArtifact`,
  y coincide en `run_id`/`source_package_hash` con el run actual.
- `artifacts/03b-semantic-enrichment.json` existe, valida como
  `SemanticEnrichmentArtifact`, y coincide en `run_id`/`source_package_hash`
  con el run actual.
- Ninguna referencia (data_item_id de un tag/mapping,
  from_paragraph_id/to_paragraph_id de una dependencia) queda huerfana
  contra el universo reconstruido desde los `CanonicalProgram` actuales
  (verificado dentro de `semantic_graph_builder.build_semantic_graph`).

No invoca Java, no vuelve a leer DDL/CSV/YAML, no rederiva dependencias ni
vuelve a ejecutar SemanticTagger/DomainTermMapper: unicamente lee los tres
artefactos ya persistidos y los traduce.

Idempotencia: se recomputa siempre el artefacto completo (recomputacion
pura, sin subprocess, barata) y se compara estructuralmente contra
`artifacts/04-semantic-graph.json` si ya existe; si son iguales, no se
reescribe. `SemanticGraph` no tiene `run_id` (el JSON Schema no lo admite,
ver contracts/semantic_graph.py), asi que a diferencia de Prompt 6/7 no
hay un campo propio que comparar antes de recomputar: recompute completo +
comparacion estructural es la unica estrategia posible aqui, y cualquier
cambio en los artefactos de entrada produce un resultado distinto por
construccion.
"""

from __future__ import annotations

from pathlib import Path

from ..contracts.canonical import CanonicalProgram
from ..contracts.dependencies import DependencyArtifact
from ..contracts.enums import PipelineStage, StageStatus
from ..contracts.inventory import Inventory
from ..contracts.run_state import StageExecution
from ..contracts.semantic_enrichment import SemanticEnrichmentArtifact
from ..contracts.semantic_graph import SemanticGraph
from .artifact_store import atomic_write_json
from .dependencies_stage import _load_and_validate_canonical_programs
from .errors import DependencyBuildError, SemanticGraphBuildError
from .semantic_graph_builder import build_semantic_graph


def _verify_semantic_enrichment_built_precondition(stages: list[StageExecution]) -> None:
    matches = [stage for stage in stages if stage.stage == PipelineStage.SEMANTIC_ENRICHMENT_BUILT]
    if len(matches) != 1:
        raise SemanticGraphBuildError(
            "SEMANTIC_ENRICHMENT_BUILT debe tener exactamente una StageExecution en RunState; "
            f"se encontraron {len(matches)}"
        )
    if matches[0].status != StageStatus.SUCCEEDED:
        raise SemanticGraphBuildError(
            f"SEMANTIC_ENRICHMENT_BUILT no esta SUCCEEDED (status={matches[0].status.value}); "
            "no se puede construir el grafo semantico sobre un enriquecimiento incompleto"
        )


def _load_dependency_artifact(
    dependencies_path: Path, *, run_id: str, source_package_hash: str
) -> DependencyArtifact:
    if not dependencies_path.is_file():
        raise SemanticGraphBuildError(
            f"no se encontro {dependencies_path.name}: DEPENDENCIES_BUILT no dejo artefacto"
        )
    try:
        artifact = DependencyArtifact.model_validate_json(
            dependencies_path.read_text(encoding="utf-8")
        )
    except ValueError as exc:
        raise SemanticGraphBuildError(
            f"{dependencies_path.name} no valida contra DependencyArtifact: {exc}"
        ) from exc
    if artifact.run_id != run_id:
        raise SemanticGraphBuildError(
            f"{dependencies_path.name}: run_id no coincide con el run actual"
        )
    if artifact.source_package_hash != source_package_hash:
        raise SemanticGraphBuildError(
            f"{dependencies_path.name}: source_package_hash no coincide con el run actual"
        )
    return artifact


def _load_semantic_enrichment_artifact(
    semantic_enrichment_path: Path, *, run_id: str, source_package_hash: str
) -> SemanticEnrichmentArtifact:
    if not semantic_enrichment_path.is_file():
        raise SemanticGraphBuildError(
            f"no se encontro {semantic_enrichment_path.name}: SEMANTIC_ENRICHMENT_BUILT no dejo "
            "artefacto"
        )
    try:
        artifact = SemanticEnrichmentArtifact.model_validate_json(
            semantic_enrichment_path.read_text(encoding="utf-8")
        )
    except ValueError as exc:
        raise SemanticGraphBuildError(
            f"{semantic_enrichment_path.name} no valida contra SemanticEnrichmentArtifact: {exc}"
        ) from exc
    if artifact.run_id != run_id:
        raise SemanticGraphBuildError(
            f"{semantic_enrichment_path.name}: run_id no coincide con el run actual"
        )
    if artifact.source_package_hash != source_package_hash:
        raise SemanticGraphBuildError(
            f"{semantic_enrichment_path.name}: source_package_hash no coincide con el run actual"
        )
    return artifact


def run_semantic_graph_stage(
    *,
    run_id: str,
    source_package_hash: str,
    run_stages: list[StageExecution],
    inventory: Inventory,
    canonical_dir: Path,
    dependencies_path: Path,
    semantic_enrichment_path: Path,
    semantic_graph_path: Path,
) -> list[str]:
    """Ejecuta SEMANTIC_GRAPH_BUILT y devuelve los warnings (resumen para
    RunState; el detalle completo vive en `semantic_graph_path`)."""
    _verify_semantic_enrichment_built_precondition(run_stages)

    try:
        programs: list[CanonicalProgram] = _load_and_validate_canonical_programs(
            canonical_dir=canonical_dir,
            inventory=inventory,
            source_package_hash=source_package_hash,
        )
    except DependencyBuildError as exc:
        # Mismo chequeo de precondicion que DEPENDENCIES_BUILT/
        # SEMANTIC_ENRICHMENT_BUILT ya aplicaron sobre CanonicalProgram; se
        # reexpone con el tipo de excepcion propio de esta etapa.
        raise SemanticGraphBuildError(str(exc)) from exc

    dependency_artifact = _load_dependency_artifact(
        dependencies_path, run_id=run_id, source_package_hash=source_package_hash
    )
    enrichment_artifact = _load_semantic_enrichment_artifact(
        semantic_enrichment_path, run_id=run_id, source_package_hash=source_package_hash
    )

    fresh_graph = build_semantic_graph(
        inventory=inventory,
        programs=programs,
        dependency_artifact=dependency_artifact,
        enrichment_artifact=enrichment_artifact,
        source_package_hash=source_package_hash,
    )

    if semantic_graph_path.is_file():
        try:
            existing = SemanticGraph.model_validate_json(
                semantic_graph_path.read_text(encoding="utf-8")
            )
        except ValueError:
            existing = None
        if existing is not None and existing == fresh_graph:
            return list(existing.warnings)

    atomic_write_json(semantic_graph_path, fresh_graph)
    return list(fresh_graph.warnings)
