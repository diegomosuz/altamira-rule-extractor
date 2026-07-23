"""PipelineRunner minimo: RECEIVED -> VALIDATED -> EXTRACTED -> INVENTORIED
-> PARSED -> DEPENDENCIES_BUILT -> SEMANTIC_ENRICHMENT_BUILT ->
SEMANTIC_GRAPH_BUILT.

No es un framework generico de pipelines: es una funcion lineal que
ejecuta exactamente estas ocho etapas, persiste RunState atomicamente
en cada transicion, y aplica una idempotencia explicita y acotada:

- RECEIVED: si ya hay una copia valida (`input/package.zip`) marcada
  SUCCEEDED, no se vuelve a copiar ni a hashear (y `source_zip` se
  ignora). Si el run_id es nuevo pero ya existe un `input/package.zip`
  sin RunState asociado, se rechaza para no sobrescribir una ejecucion
  ajena.
- VALIDATED: no persiste artefacto propio (solo alimenta a las etapas
  siguientes), por lo que recalcularla en cada llamada es deliberado y
  no tiene efectos secundarios.
- EXTRACTED: si `work/extracted` ya existe y quedo SUCCEEDED, no se
  vuelve a extraer. Si una ejecucion previa quedo a medias, se limpian
  los temporales `work/extracted.tmp-*` antes de reintentar.
- INVENTORIED: si `artifacts/01-inventory.json` ya existe, se valida
  antes de reutilizarlo; si esta corrupto se reconstruye.
- PARSED: nunca se salta solo porque `RunState` diga SUCCEEDED; delega en
  `parsed_stage.run_parsed_stage`, que revalida cada artefacto canonico
  contra Inventory antes de reutilizarlo o reprocesarlo (ver ese modulo).
- DEPENDENCIES_BUILT: tampoco se salta por `RunState`; delega en
  `dependencies_stage.run_dependencies_built_stage`, que revalida que
  PARSED este realmente completo y que cada CanonicalProgram siga siendo
  consistente antes de reutilizar o reconstruir por completo
  `artifacts/03-dependencies.json` (ver ese modulo).
- SEMANTIC_ENRICHMENT_BUILT: tampoco se salta por `RunState`; delega en
  `semantic_enrichment_stage.run_semantic_enrichment_stage`, que revalida
  DEPENDENCIES_BUILT, CanonicalProgram, y la integridad de cada DDL/CSV
  declarado en Manifest antes de reutilizar o reconstruir por completo
  `artifacts/03b-semantic-enrichment.json` (ver ese modulo).
- SEMANTIC_GRAPH_BUILT: tampoco se salta por `RunState`; delega en
  `semantic_graph_stage.run_semantic_graph_stage`, que revalida
  SEMANTIC_ENRICHMENT_BUILT, CanonicalProgram, y que `03-dependencies.json`/
  `03b-semantic-enrichment.json` sigan siendo consistentes con el run
  actual antes de reutilizar o reconstruir por completo
  `artifacts/04-semantic-graph.json` (ver ese modulo).

En ningun caso se duplican StageExecution: cada etapa tiene a lo sumo un
registro en RunState.stages, que se reemplaza en los reintentos.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from ..config import Settings
from ..contracts.enums import PipelineStage, StageStatus
from ..contracts.inventory import Inventory
from ..contracts.run_state import RunState, StageExecution
from .artifact_store import atomic_write_json
from .dependencies_stage import run_dependencies_built_stage
from .errors import (
    DependencyBuildError,
    ExtractionError,
    PackageValidationError,
    ParserContractViolationError,
    ParserUnavailableError,
    PipelineError,
    RunConflictError,
    SemanticEnrichmentBuildError,
    SemanticGraphBuildError,
)
from .inventory_builder import build_inventory
from .package_validator import ValidatedPackage, validate_package
from .parsed_stage import run_parsed_stage
from .parser_client import ProLeapParserClient
from .safe_extractor import extract_package
from .semantic_enrichment_stage import run_semantic_enrichment_stage
from .semantic_graph_stage import run_semantic_graph_stage

_COPY_CHUNK_SIZE = 1024 * 1024


def generate_run_id() -> str:
    """run_id con precision de microsegundo + sufijo aleatorio (evita colisiones)."""
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    return f"{timestamp}-{secrets.token_hex(4)}"


def _now() -> datetime:
    return datetime.now(UTC)


def _upsert_stage(state: RunState, execution: StageExecution) -> None:
    stages = list(state.stages)
    for index, existing in enumerate(stages):
        if existing.stage == execution.stage:
            stages[index] = execution
            state.stages = stages
            return
    stages.append(execution)
    state.stages = stages


def _stage_succeeded(state: RunState, stage: PipelineStage) -> bool:
    return any(s.stage == stage and s.status == StageStatus.SUCCEEDED for s in state.stages)


def _mark_failed(
    state: RunState, run_json_path: Path, stage: PipelineStage, started_at: datetime, error: str
) -> RunState:
    finished_at = _now()
    _upsert_stage(
        state,
        StageExecution(
            stage=stage,
            status=StageStatus.FAILED,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=(finished_at - started_at).total_seconds(),
            error=error,
        ),
    )
    state.current_stage = PipelineStage.FAILED
    state.updated_at = finished_at
    atomic_write_json(run_json_path, state)
    return state


def _mark_succeeded(
    state: RunState,
    run_json_path: Path,
    stage: PipelineStage,
    started_at: datetime,
    warnings: list[str] | None = None,
) -> RunState:
    finished_at = _now()
    _upsert_stage(
        state,
        StageExecution(
            stage=stage,
            status=StageStatus.SUCCEEDED,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=(finished_at - started_at).total_seconds(),
            warnings=warnings or [],
        ),
    )
    state.current_stage = stage
    state.updated_at = finished_at
    atomic_write_json(run_json_path, state)
    return state


def _load_or_init_state(run_json_path: Path, run_id: str, package_filename: str) -> RunState:
    if run_json_path.is_file():
        return RunState.model_validate_json(run_json_path.read_text(encoding="utf-8"))

    now = _now()
    return RunState(
        run_id=run_id,
        package_filename=package_filename,
        current_stage=PipelineStage.RECEIVED,
        stages=[],
        created_at=now,
        updated_at=now,
    )


def _copy_and_hash(source: Path, destination: Path) -> str:
    """Copia `source` a `destination` calculando el SHA-256 de los bytes escritos.

    El hash se deriva de lo que realmente se persiste (no de una lectura
    posterior del origen): protege contra que el archivo externo cambie
    durante la copia.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()

    fd, tmp_name = tempfile.mkstemp(
        prefix=".package.", suffix=".zip.tmp", dir=str(destination.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with source.open("rb") as src, os.fdopen(fd, "wb") as dst:
            while True:
                chunk = src.read(_COPY_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(tmp_path, destination)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    return digest.hexdigest()


def _run_received(
    state: RunState, source_zip: Path, input_zip_path: Path, run_json_path: Path
) -> RunState:
    if _stage_succeeded(state, PipelineStage.RECEIVED) and input_zip_path.is_file():
        # ya recibido: source_zip se ignora deliberadamente. Se corrige
        # current_stage (podria venir en FAILED de un intento previo que
        # fallo en una etapa POSTERIOR, p. ej. PARSED): de lo contrario el
        # chequeo de "current_stage == FAILED" en run_ingestion cortaria
        # la reanudacion antes de volver a intentar las etapas siguientes.
        state.current_stage = PipelineStage.RECEIVED
        return state

    is_fresh_run = len(state.stages) == 0
    if input_zip_path.exists():
        if is_fresh_run:
            raise RunConflictError(
                f"{input_zip_path} ya existe pero no hay RunState previo para este run_id; "
                "no se sobrescribe una ejecucion ajena"
            )
        input_zip_path.unlink()  # residuo de un intento RECEIVED fallido: se limpia y reintenta.

    started_at = _now()
    try:
        package_hash = _copy_and_hash(source_zip, input_zip_path)
    except OSError as exc:
        return _mark_failed(state, run_json_path, PipelineStage.RECEIVED, started_at, str(exc))

    state.source_package_hash = package_hash
    return _mark_succeeded(state, run_json_path, PipelineStage.RECEIVED, started_at)


def _run_validated(
    state: RunState, input_zip_path: Path, settings: Settings, run_json_path: Path
) -> tuple[RunState, ValidatedPackage | None]:
    started_at = _now()
    try:
        validated = validate_package(input_zip_path, settings)
    except PackageValidationError as exc:
        state = _mark_failed(state, run_json_path, PipelineStage.VALIDATED, started_at, str(exc))
        return state, None

    state = _mark_succeeded(state, run_json_path, PipelineStage.VALIDATED, started_at)
    return state, validated


def _run_extracted(
    state: RunState,
    input_zip_path: Path,
    work_dir: Path,
    extracted_dir: Path,
    settings: Settings,
    run_json_path: Path,
) -> RunState:
    if _stage_succeeded(state, PipelineStage.EXTRACTED) and extracted_dir.is_dir():
        state.current_stage = PipelineStage.EXTRACTED  # ver comentario en _run_received.
        return state

    if work_dir.is_dir():
        # work_dir puede no existir aun (primer intento) o, en un fallo previo
        # ajeno a esta etapa, existir como algo que no es un directorio; en
        # ambos casos no hay residuos propios de EXTRACTED que limpiar.
        for stray in work_dir.glob("extracted.tmp-*"):
            shutil.rmtree(stray, ignore_errors=True)
        if extracted_dir.exists():
            shutil.rmtree(extracted_dir)

    started_at = _now()
    try:
        extract_package(input_zip_path, work_dir, settings)
    except ExtractionError as exc:
        return _mark_failed(state, run_json_path, PipelineStage.EXTRACTED, started_at, str(exc))

    return _mark_succeeded(state, run_json_path, PipelineStage.EXTRACTED, started_at)


def _run_inventoried(
    state: RunState,
    extracted_dir: Path,
    inventory_path: Path,
    validated: ValidatedPackage | None,
    run_json_path: Path,
) -> RunState:
    if inventory_path.is_file():
        try:
            existing = Inventory.model_validate_json(inventory_path.read_text(encoding="utf-8"))
        except ValueError:
            existing = None
        if existing is not None and existing.run_id == state.run_id:
            if _stage_succeeded(state, PipelineStage.INVENTORIED):
                state.current_stage = PipelineStage.INVENTORIED  # ver _run_received.
                return state
            return _mark_succeeded(state, run_json_path, PipelineStage.INVENTORIED, _now())
        inventory_path.unlink()  # inventario previo invalido o de otro run: se descarta.

    if validated is None:
        # VALIDATED fallo o no se ejecuto en esta llamada: no hay Manifest disponible.
        raise PipelineError("no se puede construir el inventario sin un ValidatedPackage")

    started_at = _now()
    try:
        inventory = build_inventory(
            extracted_dir, state.run_id, state.source_package_hash or "", validated.manifest
        )
        atomic_write_json(inventory_path, inventory)
    except OSError as exc:
        return _mark_failed(state, run_json_path, PipelineStage.INVENTORIED, started_at, str(exc))

    return _mark_succeeded(
        state, run_json_path, PipelineStage.INVENTORIED, started_at, warnings=inventory.warnings
    )


def _run_parsed(
    state: RunState,
    run_dir: Path,
    extracted_dir: Path,
    canonical_dir: Path,
    inventory_path: Path,
    settings: Settings,
    run_json_path: Path,
) -> RunState:
    """Ejecuta PARSED: invoca el parser Java sobre cada programa COBOL del
    inventario y persiste `artifacts/02-canonical/`.

    Nunca se salta por completo en base a `RunState`: `run_parsed_stage`
    siempre revalida cada artefacto contra Inventory antes de decidir si
    reutilizarlo o reprocesarlo (ver docstring de ese modulo)."""
    started_at = _now()
    inventory = Inventory.model_validate_json(inventory_path.read_text(encoding="utf-8"))
    client = ProLeapParserClient(
        java_bin=settings.java_bin,
        jar_path=settings.parser_jar_path,
        timeout_seconds=settings.parser_timeout_seconds,
    )
    try:
        outcome = run_parsed_stage(
            run_root=run_dir,
            extracted_dir=extracted_dir,
            canonical_dir=canonical_dir,
            inventory=inventory,
            source_package_hash=state.source_package_hash or "",
            client=client,
        )
    except (ParserUnavailableError, ParserContractViolationError) as exc:
        return _mark_failed(state, run_json_path, PipelineStage.PARSED, started_at, str(exc))

    if not outcome.succeeded:
        return _mark_failed(
            state,
            run_json_path,
            PipelineStage.PARSED,
            started_at,
            outcome.error or "PARSED fallo sin detalle",
        )

    return _mark_succeeded(
        state, run_json_path, PipelineStage.PARSED, started_at, warnings=outcome.warnings
    )


def _run_dependencies_built(
    state: RunState,
    inventory_path: Path,
    canonical_dir: Path,
    dependencies_path: Path,
    run_json_path: Path,
) -> RunState:
    """Ejecuta DEPENDENCIES_BUILT: nunca se salta por completo en base a
    `RunState`. `run_dependencies_built_stage` siempre revalida las
    precondiciones (PARSED realmente completo, CanonicalProgram validos y
    consistentes) y reutiliza `artifacts/03-dependencies.json` solo si
    sigue siendo consistente con los artefactos actuales; en caso
    contrario lo reconstruye entero (ver dependencies_stage.py)."""
    started_at = _now()
    inventory = Inventory.model_validate_json(inventory_path.read_text(encoding="utf-8"))
    try:
        warnings = run_dependencies_built_stage(
            run_id=state.run_id,
            source_package_hash=state.source_package_hash or "",
            run_stages=state.stages,
            inventory=inventory,
            canonical_dir=canonical_dir,
            dependencies_path=dependencies_path,
        )
    except DependencyBuildError as exc:
        return _mark_failed(
            state, run_json_path, PipelineStage.DEPENDENCIES_BUILT, started_at, str(exc)
        )

    return _mark_succeeded(
        state, run_json_path, PipelineStage.DEPENDENCIES_BUILT, started_at, warnings=warnings
    )


def _run_semantic_enrichment_built(
    state: RunState,
    inventory_path: Path,
    extracted_dir: Path,
    canonical_dir: Path,
    semantic_enrichment_path: Path,
    settings: Settings,
    run_json_path: Path,
) -> RunState:
    """Ejecuta SEMANTIC_ENRICHMENT_BUILT (ParameterLoader + SemanticTagger +
    DomainTermMapper, Prompt 7 del runbook): nunca se salta por completo en
    base a `RunState`. `run_semantic_enrichment_stage` siempre revalida las
    precondiciones (DEPENDENCIES_BUILT realmente completo, CanonicalProgram
    validos, DDL/CSV declarados integros) y reutiliza
    `artifacts/03b-semantic-enrichment.json` solo si el resultado
    recomputado es identico al existente; en caso contrario lo reconstruye
    entero (ver semantic_enrichment_stage.py)."""
    started_at = _now()
    inventory = Inventory.model_validate_json(inventory_path.read_text(encoding="utf-8"))
    try:
        warnings = run_semantic_enrichment_stage(
            run_id=state.run_id,
            source_package_hash=state.source_package_hash or "",
            run_stages=state.stages,
            inventory=inventory,
            extracted_dir=extracted_dir,
            canonical_dir=canonical_dir,
            settings=settings,
            semantic_enrichment_path=semantic_enrichment_path,
        )
    except SemanticEnrichmentBuildError as exc:
        return _mark_failed(
            state, run_json_path, PipelineStage.SEMANTIC_ENRICHMENT_BUILT, started_at, str(exc)
        )

    return _mark_succeeded(
        state,
        run_json_path,
        PipelineStage.SEMANTIC_ENRICHMENT_BUILT,
        started_at,
        warnings=warnings,
    )


def _run_semantic_graph_built(
    state: RunState,
    inventory_path: Path,
    canonical_dir: Path,
    dependencies_path: Path,
    semantic_enrichment_path: Path,
    semantic_graph_path: Path,
    run_json_path: Path,
) -> RunState:
    """Ejecuta SEMANTIC_GRAPH_BUILT (SemanticGraphBuilder, Prompt 8 del
    runbook): nunca se salta por completo en base a `RunState`.
    `run_semantic_graph_stage` siempre revalida las precondiciones
    (SEMANTIC_ENRICHMENT_BUILT realmente completo, CanonicalProgram
    validos, `03-dependencies.json`/`03b-semantic-enrichment.json`
    consistentes con el run actual) y reutiliza
    `artifacts/04-semantic-graph.json` solo si el resultado recomputado es
    identico al existente; en caso contrario lo reconstruye entero (ver
    semantic_graph_stage.py)."""
    started_at = _now()
    inventory = Inventory.model_validate_json(inventory_path.read_text(encoding="utf-8"))
    try:
        warnings = run_semantic_graph_stage(
            run_id=state.run_id,
            source_package_hash=state.source_package_hash or "",
            run_stages=state.stages,
            inventory=inventory,
            canonical_dir=canonical_dir,
            dependencies_path=dependencies_path,
            semantic_enrichment_path=semantic_enrichment_path,
            semantic_graph_path=semantic_graph_path,
        )
    except SemanticGraphBuildError as exc:
        return _mark_failed(
            state, run_json_path, PipelineStage.SEMANTIC_GRAPH_BUILT, started_at, str(exc)
        )

    return _mark_succeeded(
        state, run_json_path, PipelineStage.SEMANTIC_GRAPH_BUILT, started_at, warnings=warnings
    )


def run_ingestion(source_zip: Path, settings: Settings, run_id: str | None = None) -> RunState:
    """Ejecuta RECEIVED -> VALIDATED -> EXTRACTED -> INVENTORIED -> PARSED
    -> DEPENDENCIES_BUILT -> SEMANTIC_ENRICHMENT_BUILT -> SEMANTIC_GRAPH_BUILT
    para `source_zip`.

    Si `run_id` se omite, se genera uno nuevo. Si se pasa un `run_id` de
    una ejecucion previa cuyo RECEIVED ya tuvo exito, `source_zip` se
    ignora y se continua desde el estado persistido (reanudacion minima).
    """
    if run_id is None:
        run_id = generate_run_id()

    run_dir = settings.runs_dir / run_id
    run_json_path = run_dir / "run.json"
    input_zip_path = run_dir / "input" / "package.zip"
    work_dir = run_dir / "work"
    extracted_dir = work_dir / "extracted"
    inventory_path = run_dir / "artifacts" / "01-inventory.json"
    canonical_dir = run_dir / "artifacts" / "02-canonical"
    dependencies_path = run_dir / "artifacts" / "03-dependencies.json"
    semantic_enrichment_path = run_dir / "artifacts" / "03b-semantic-enrichment.json"
    semantic_graph_path = run_dir / "artifacts" / "04-semantic-graph.json"

    state = _load_or_init_state(run_json_path, run_id, "input/package.zip")

    state = _run_received(state, source_zip, input_zip_path, run_json_path)
    if state.current_stage == PipelineStage.FAILED:
        return state

    state, validated = _run_validated(state, input_zip_path, settings, run_json_path)
    if state.current_stage == PipelineStage.FAILED:
        return state

    state = _run_extracted(state, input_zip_path, work_dir, extracted_dir, settings, run_json_path)
    if state.current_stage == PipelineStage.FAILED:
        return state

    state = _run_inventoried(state, extracted_dir, inventory_path, validated, run_json_path)
    if state.current_stage == PipelineStage.FAILED:
        return state

    state = _run_parsed(
        state, run_dir, extracted_dir, canonical_dir, inventory_path, settings, run_json_path
    )
    if state.current_stage == PipelineStage.FAILED:
        return state

    state = _run_dependencies_built(
        state, inventory_path, canonical_dir, dependencies_path, run_json_path
    )
    if state.current_stage == PipelineStage.FAILED:
        return state

    state = _run_semantic_enrichment_built(
        state,
        inventory_path,
        extracted_dir,
        canonical_dir,
        semantic_enrichment_path,
        settings,
        run_json_path,
    )
    if state.current_stage == PipelineStage.FAILED:
        return state

    return _run_semantic_graph_built(
        state,
        inventory_path,
        canonical_dir,
        dependencies_path,
        semantic_enrichment_path,
        semantic_graph_path,
        run_json_path,
    )
