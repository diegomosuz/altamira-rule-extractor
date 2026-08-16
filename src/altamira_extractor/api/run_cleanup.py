"""Limpieza completa de un run ("Limpiar job", Fase v1.17.1, Feature 5):
filesystem del run + Neo4j (solo si el run limpiado es dueno actual del
grafo) + cascada de registros de referencia duplicados que apunten a el
(Feature 6, "Clean Job + Duplicate Interaction").

Ownership de Neo4j: V1 es mono-tenant (ver docstring de
`pipeline/neo4j_repository.py`, "V1 no soporta coexistencia de varios
paquetes Altamira en la misma base") -- el grafo administrado completo
pertenece EXCLUSIVAMENTE al run cuyo `source_package_hash` coincide con
`(:AltamiraGraphLoad {id:"active"}).source_package_hash`. Si no coincide
(otro run ya cargo su propio grafo despues, o este run nunca llego a
SEMANTIC_GRAPH_LOADED), la limpieza de Neo4j es un no-op explicito: NUNCA
se ejecuta un DETACH DELETE amplio "por si acaso".

Orden de operaciones (fail-closed, sin transacciones distribuidas):
Neo4j primero (si falla, se aborta ANTES de tocar el filesystem -- el run
permanece intacto y la operacion es reintentable); filesystem despues
(un `shutil.rmtree` es naturalmente idempotente ante un reintento:
ausente-el-directorio se trata como exito, nunca como error)."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings
from ..contracts.enums import PipelineStage, StageStatus
from ..contracts.run_state import RunState
from ..pipeline.errors import Neo4jError
from ..pipeline.neo4j_repository import Neo4jRepository
from .errors import ApiError, RunAlreadyActiveError, RunCleanupError
from .executor import RunExecutor
from .reads import read_run_state

logger = logging.getLogger(__name__)


def _reached_semantic_graph_loaded(state: RunState) -> bool:
    return any(
        s.stage == PipelineStage.SEMANTIC_GRAPH_LOADED and s.status == StageStatus.SUCCEEDED
        for s in state.stages
    )


@dataclass(frozen=True)
class CleanupResult:
    run_id: str
    neo4j_wiped: bool
    cascade_removed_run_ids: tuple[str, ...]


def _clean_neo4j_if_owned(settings: Settings, state: RunState) -> bool:
    """Devuelve `True` si el grafo administrado fue eliminado, `False` si
    era un no-op (el run no es dueno del grafo activo, o nunca llego a
    SEMANTIC_GRAPH_LOADED). Lanza `RunCleanupError` si Neo4j esta
    configurado pero inalcanzable/rechaza credenciales/falla la consulta
    -- nunca se silencia un fallo real como si fuera "no era dueno"."""
    if state.duplicate_of_run_id is not None:
        # Un registro de referencia NUNCA posee Neo4j por diseno
        # (Feature 6, "DUPLICATE RECORD OWNERSHIP"): comparar por hash
        # aqui seria incorrecto, porque una referencia comparte
        # DELIBERADAMENTE el mismo source_package_hash que su run
        # autoritativo real, que puede seguir existiendo y siendo dueno
        # del grafo -- se descarta antes de siquiera comparar.
        return False
    if state.source_package_hash is None:
        return False
    if not _reached_semantic_graph_loaded(state):
        # Este run nunca llego a SEMANTIC_GRAPH_LOADED (fallo antes, o
        # esta en curso en una etapa temprana): estructuralmente no puede
        # poseer nada en Neo4j. Ni siquiera se intenta conectar -- Neo4j
        # inalcanzable/mal configurado nunca debe bloquear la limpieza de
        # un run que jamas lo toco.
        return False

    repository: Neo4jRepository | None = None
    try:
        repository = Neo4jRepository.connect(settings)
        active = repository.read_active_graph_load()
        if active is None or active.source_package_hash != state.source_package_hash:
            return False
        repository.delete_managed_graph()
        return True
    except Neo4jError as exc:
        raise RunCleanupError(
            f"no se pudo verificar/limpiar el grafo Neo4j para run_id={state.run_id!r}: {exc}"
        ) from exc
    finally:
        if repository is not None:
            repository.close()


def _remove_run_directory(run_dir: Path) -> None:
    """Idempotente: un directorio ya ausente se trata como exito (permite
    reintentar con seguridad tras un fallo previo de Neo4j que dejo el
    filesystem intacto, o un reintento tras un fallo parcial de
    `shutil.rmtree` en si)."""
    if not run_dir.exists() and not run_dir.is_symlink():
        return
    try:
        if run_dir.is_symlink():
            run_dir.unlink()
        else:
            shutil.rmtree(run_dir)
    except OSError as exc:
        raise RunCleanupError(
            f"no se pudo eliminar el directorio del run {run_dir.name!r}: {exc}"
        ) from exc


def _cascade_delete_duplicate_references(settings: Settings, cleaned_run_id: str) -> list[str]:
    """Elimina cualquier registro de referencia (Feature 6) cuyo
    `duplicate_of_run_id` apunte al run recien limpiado -- sin esto, una
    referencia huerfana seguiria bloqueando la re-subida del mismo
    paquete como si aun existiera un run autoritativo (ver "CLEAN JOB +
    DUPLICATE INTERACTION"). Nunca toca un run con otro hash."""
    if not settings.runs_dir.is_dir():
        return []
    removed: list[str] = []
    for entry in settings.runs_dir.iterdir():
        if entry.is_symlink() or not entry.is_dir():
            continue
        try:
            state = read_run_state(entry)
        except ApiError:
            continue
        if state.duplicate_of_run_id != cleaned_run_id:
            continue
        _remove_run_directory(entry)
        removed.append(state.run_id)
    return removed


def clean_run(
    run_dir: Path, run_id: str, settings: Settings, executor: RunExecutor
) -> CleanupResult:
    """Caso de uso completo de "Limpiar job". El backend SIEMPRE
    reverifica que el run no este activo (nunca confia solo en que la UI
    ya deshabilito el boton, ver "ACTIVE-RUN SAFETY")."""
    if executor.is_active(run_id):
        raise RunAlreadyActiveError(
            "no se puede limpiar un run con una ejecucion actualmente en curso"
        )

    state = read_run_state(run_dir)  # RunNotFoundError si run_dir no tiene run.json valido

    neo4j_wiped = _clean_neo4j_if_owned(settings, state)
    _remove_run_directory(run_dir)
    cascade_removed = _cascade_delete_duplicate_references(settings, run_id)

    logger.info(
        "run_id=%s limpiado: neo4j_wiped=%s cascade_removed=%d",
        run_id,
        neo4j_wiped,
        len(cascade_removed),
    )
    return CleanupResult(
        run_id=run_id, neo4j_wiped=neo4j_wiped, cascade_removed_run_ids=tuple(cascade_removed)
    )
