"""PipelineRunner minimo: RECEIVED -> VALIDATED -> EXTRACTED -> INVENTORIED
-> PARSED -> DEPENDENCIES_BUILT -> SEMANTIC_ENRICHMENT_BUILT ->
SEMANTIC_GRAPH_BUILT -> SEMANTIC_GRAPH_LOADED -> GRAPH_VALIDATED ->
CANDIDATES_DETECTED -> CONTEXTS_BUILT -> RULE_DRAFTS_GENERATED ->
GUARDRAILS_APPLIED.

No es un framework generico de pipelines: es una funcion lineal que
ejecuta exactamente estas catorce etapas, persiste RunState atomicamente
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
- SEMANTIC_GRAPH_LOADED: nunca "salta" la carga (no hay optimizacion de
  skip-load); delega en
  `semantic_graph_load_stage.run_semantic_graph_load_stage`, que siempre
  reemplaza transaccionalmente el subgrafo Neo4j administrado por
  Altamira (`_altamira_managed=true`) con el contenido actual de
  `artifacts/04-semantic-graph.json` (ver ese modulo). V1 no soporta
  coexistencia de varios paquetes Altamira en la misma base Neo4j (ver
  docstring de `neo4j_repository.py`).
- GRAPH_VALIDATED: delega en
  `graph_validated_stage.run_graph_validated_stage`, que detecta drift
  entre el artefacto y el estado real de Neo4j antes de ejecutar
  `queries/v1/invariants.cypher` y persistir
  `artifacts/05-invariants.json` (ver ese modulo).
- CANDIDATES_DETECTED: nunca "salta" la deteccion (mismo principio que
  SEMANTIC_GRAPH_LOADED: no hay optimizacion de skip); delega en
  `candidates_detected_stage.run_candidates_detected_stage`, que
  reejecuta precondiciones y `queries/v1/q0_candidates.cypher` en cada
  corrida y reconstruye `artifacts/06-candidates.json` solo si el
  resultado recomputado difiere del existente. Es de solo lectura sobre
  el grafo: nunca repara drift ni vuelve a ejecutar
  `invariants.cypher`/`GraphInvariantValidator` (ver ese modulo).
- CONTEXTS_BUILT: tampoco "salta" la construccion; delega en
  `contexts_built_stage.run_contexts_built_stage`, que ejecuta Q1-Q7
  para cada `RuleCandidate` dentro de una unica transaccion de lectura y
  reconstruye `artifacts/07-context/` (con su `context-manifest.json`)
  solo si el resultado recomputado difiere del existente. Tambien de
  solo lectura: nunca reejecuta Q0/CandidateDetector ni
  `invariants.cypher` (ver ese modulo).
- RULE_DRAFTS_GENERATED: etapa ATOMICA para el conjunto completo de
  candidatos (Prompt 12); delega en
  `rule_drafts_generated_stage.run_rule_drafts_generated_stage`, que
  nunca promueve un exito parcial: si CUALQUIER candidato produce una
  respuesta inicial estructuralmente invalida, descarta el directorio
  temporal completo y conserva intacta la salida canonica anterior (ver
  ese modulo). El fast-path de reutilizacion de
  `artifacts/08-rule-drafts/` nunca depende del estado anterior en
  RunState, solo del contenido real en disco.
- GUARDRAILS_APPLIED: etapa ATOMICA y FAIL-FAST (Prompt 12); delega en
  `guardrails_applied_stage.run_guardrails_applied_stage`, que procesa
  candidatos secuencialmente y se detiene de inmediato en el primer
  REJECTED definitivo (tras agotar `LLM_REPAIR_ATTEMPTS`), sin llamar al
  modelo para los candidatos restantes ni promover un
  `artifacts/09-guardrails/` parcial (ver ese modulo). Nunca sobrescribe
  `artifacts/08-rule-drafts/` (evidencia inmutable de la primera
  respuesta aceptada).

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
from typing import Literal

from ..config import Settings
from ..contracts.enums import PipelineStage, StageStatus
from ..contracts.inventory import Inventory
from ..contracts.run_state import RunState, StageExecution
from ..contracts.run_state_recovery import (
    EMERGENCY_RECORD_PREFIX,
    RunStatePersistenceFailureRecord,
)
from .artifact_store import atomic_write_json
from .candidates_detected_stage import run_candidates_detected_stage
from .contexts_built_stage import run_contexts_built_stage
from .dependencies_stage import run_dependencies_built_stage
from .errors import (
    AtomicWriteError,
    CandidateDetectionError,
    ContextBuildError,
    DependencyBuildError,
    ExtractionError,
    GraphLoadError,
    GraphValidationError,
    GuardrailError,
    MarkdownRenderError,
    PackageValidationError,
    ParserContractViolationError,
    ParserUnavailableError,
    PipelineError,
    RuleDraftGenerationError,
    RunConflictError,
    RunStatePersistenceError,
    SemanticEnrichmentBuildError,
    SemanticGraphBuildError,
)
from .graph_validated_stage import run_graph_validated_stage
from .guardrails_applied_stage import run_guardrails_applied_stage
from .inventory_builder import build_inventory
from .package_validator import ValidatedPackage, validate_package
from .parsed_stage import run_parsed_stage
from .parser_client import ProLeapParserClient
from .rule_drafts_generated_stage import run_rule_drafts_generated_stage
from .rules_rendered_stage import run_rules_rendered_stage
from .safe_extractor import extract_package
from .semantic_enrichment_stage import run_semantic_enrichment_stage
from .semantic_graph_load_stage import run_semantic_graph_load_stage
from .semantic_graph_stage import run_semantic_graph_stage

_COPY_CHUNK_SIZE = 1024 * 1024

# Deadline critico para la transicion terminal de run.json (checkpoint
# correctivo: defecto real de estabilizacion de baseline). Mas tolerante
# que el deadline por defecto de un artefacto ordinario
# (artifact_store._DEFAULT_REPLACE_DEADLINE_SECONDS, 1.0s): run.json es
# el UNICO artefacto sometido a polling activo del cliente
# (ui/templates/_status_fragment.html hace `hx-trigger="every 3s"`), asi
# que 3.0s -- exactamente un ciclo de polling -- es el limite superior
# razonable para que una transicion terminal alcance a persistirse antes
# de que el cliente vuelva a preguntar, sin llegar a ser un reintento sin
# limite.
_RUN_STATE_CRITICAL_DEADLINE_SECONDS = 3.0


def _emergency_record_filename() -> str:
    """Nombre nuevo e inmutable por cada intento (timestamp de
    microsegundo + sufijo aleatorio, mismo patron que `generate_run_id`):
    nunca colisiona con un artefacto de emergencia previo ni con un
    lector que pudiera tener un nombre anterior abierto -- el `os.replace`
    de `atomic_write_json` promueve siempre a un destino que nadie vio
    antes, por lo que la misma carrera de `WinError 5` que afecta a
    `run.json` es estructuralmente imposible aqui."""
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    return f"{EMERGENCY_RECORD_PREFIX}{timestamp}-{secrets.token_hex(6)}.json"


def _synthesize_emergency_state(
    state: RunState, *, stage: PipelineStage, sanitized_message: str
) -> RunState:
    """Vista terminal FAILED para el artefacto de emergencia. Se aplica
    SIEMPRE, incluso si la transicion que no pudo persistirse era en
    realidad un SUCCEEDED: sin `run.json` durable no hay forma de afirmar
    que ese exito quedo registrado, y el proyecto nunca presenta un
    desenlace incierto como si fuera exitoso."""
    finished_at = _now()
    failed_execution = StageExecution(
        stage=stage,
        status=StageStatus.FAILED,
        started_at=finished_at,
        finished_at=finished_at,
        duration_seconds=0.0,
        error=sanitized_message,
    )
    stages = [s for s in state.stages if s.stage != stage]
    stages.append(failed_execution)
    return state.model_copy(
        update={
            "current_stage": PipelineStage.FAILED,
            "stages": stages,
            "updated_at": finished_at,
        }
    )


def _write_emergency_record(
    run_dir: Path,
    state: RunState,
    *,
    stage: PipelineStage,
    transition: Literal["FAILED", "SUCCEEDED"],
    cause: BaseException,
) -> Path:
    """Escribe (best effort, atomicamente, a un nombre nuevo) el
    artefacto NO contractual de `contracts/run_state_recovery.py`.
    Nunca incluye `str(cause)` textual (revelaria la ruta destino del
    reemplazo fallido): el mensaje es fijo y generico, solo el
    `type(cause).__name__` identifica la causa tecnica."""
    sanitized_message = (
        "no se pudo persistir run.json tras agotar la politica critica de reemplazo "
        f"atomico ({type(cause).__name__})"
    )
    emergency_state = _synthesize_emergency_state(
        state, stage=stage, sanitized_message=sanitized_message
    )
    record = RunStatePersistenceFailureRecord(
        run_id=state.run_id,
        timestamp_utc=_now(),
        stage=stage,
        transition=transition,
        error_type=type(cause).__name__,
        error_message=sanitized_message,
        attempted_state=emergency_state,
    )
    record_path = run_dir / _emergency_record_filename()
    atomic_write_json(record_path, record, max_wait_seconds=_RUN_STATE_CRITICAL_DEADLINE_SECONDS)
    return record_path


def _cleanup_emergency_records(run_dir: Path) -> None:
    """Best effort: se invoca solo cuando `run.json` ACABA de persistirse
    correctamente en un estado terminal. Un fallo al borrar un artefacto
    obsoleto nunca invalida el `run.json` ya persistido -- `read_run_state`
    de todas formas ignora cualquier artefacto de emergencia mas antiguo
    que un `run.json` terminal (ver `contracts/run_state_recovery.py`)."""
    try:
        candidates = list(run_dir.glob(f"{EMERGENCY_RECORD_PREFIX}*.json"))
    except OSError:
        return
    for candidate in candidates:
        try:
            candidate.unlink()
        except OSError:
            pass


def _persist_run_state(
    state: RunState,
    run_json_path: Path,
    *,
    stage: PipelineStage,
    transition: Literal["FAILED", "SUCCEEDED"],
) -> RunState:
    """Punto UNICO de persistencia critica de `run.json` (Fase 4): tanto
    `_mark_failed` como `_mark_succeeded` pasan exclusivamente por aqui
    -- ninguna otra llamada dispersa a `atomic_write_json(run_json_path,
    ...)` existe en el modulo. Si `_replace_with_retry` agota su deadline
    critico, escribe (best effort) el artefacto durable de emergencia y
    SIEMPRE relanza `RunStatePersistenceError`: nunca devuelve como si el
    estado terminal hubiera quedado persistido. `_mark_failed` nunca se
    reinvoca aqui (evitaria una recursion sobre el mismo fallo): quien
    atrapa esta excepcion mas arriba (`api/executor.py`, `cli.py::_guard`,
    o el manejador generico de `api/app.py` para el camino sincrono de
    RECEIVED) decide como traducirla."""
    try:
        atomic_write_json(
            run_json_path, state, max_wait_seconds=_RUN_STATE_CRITICAL_DEADLINE_SECONDS
        )
    except AtomicWriteError as exc:
        run_dir = run_json_path.parent
        emergency_path: Path | None = None
        try:
            emergency_path = _write_emergency_record(
                run_dir, state, stage=stage, transition=transition, cause=exc
            )
        except OSError:
            # Fallo tambien la evidencia de emergencia: no hay NINGUN
            # artefacto durable. emergency_path=None se lo dice
            # explicitamente a quien atrape RunStatePersistenceError mas
            # arriba (ver docstring de esa excepcion).
            emergency_path = None
        raise RunStatePersistenceError(
            f"no se pudo persistir run.json para run_id={state.run_id!r} "
            f"etapa={stage.value} transicion={transition}",
            run_id=state.run_id,
            stage=stage,
            transition=transition,
            emergency_artifact_path=emergency_path,
            cause=exc,
        ) from exc

    if state.current_stage in (PipelineStage.FAILED, PipelineStage.COMPLETED):
        _cleanup_emergency_records(run_json_path.parent)
    return state


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
    return _persist_run_state(state, run_json_path, stage=stage, transition="FAILED")


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
    return _persist_run_state(state, run_json_path, stage=stage, transition="SUCCEEDED")


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


def prepare_received(
    source_zip: Path, settings: Settings, run_id: str | None = None
) -> RunState:
    """Ejecuta UNICAMENTE RECEIVED: genera `run_id` si hace falta, relee o
    inicializa `RunState`, y persiste `input/package.zip` + su
    `StageExecution`. Publica (sin guion bajo) porque la API (Prompt 13b)
    la reutiliza para dejar el run persistido de forma sincrona antes de
    programar el resto de `run_ingestion` en background — nunca duplica
    esta logica, y `run_ingestion` (CLI) llama a esta MISMA funcion, asi
    que el comportamiento de ambos caminos es identico por construccion.

    Devuelve el `RunState` resultante (puede ser FAILED si la copia
    fallo); el llamador decide como continuar."""
    if run_id is None:
        run_id = generate_run_id()

    run_dir = settings.runs_dir / run_id
    run_json_path = run_dir / "run.json"
    input_zip_path = run_dir / "input" / "package.zip"

    state = _load_or_init_state(run_json_path, run_id, "input/package.zip")
    return _run_received(state, source_zip, input_zip_path, run_json_path)


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


def _run_semantic_graph_loaded(
    state: RunState, semantic_graph_path: Path, settings: Settings, run_json_path: Path
) -> RunState:
    """Ejecuta SEMANTIC_GRAPH_LOADED (Neo4jRepository, Prompt 9 del
    runbook): siempre reemplaza transaccionalmente el subgrafo Neo4j
    administrado por Altamira con el contenido actual de
    `artifacts/04-semantic-graph.json` (ver semantic_graph_load_stage.py).
    No hay optimizacion de skip-load: recargar siempre permite reparar
    drift o modificaciones manuales en Neo4j."""
    started_at = _now()
    try:
        result = run_semantic_graph_load_stage(
            run_stages=state.stages,
            semantic_graph_path=semantic_graph_path,
            settings=settings,
        )
    except GraphLoadError as exc:
        return _mark_failed(
            state, run_json_path, PipelineStage.SEMANTIC_GRAPH_LOADED, started_at, str(exc)
        )

    return _mark_succeeded(
        state,
        run_json_path,
        PipelineStage.SEMANTIC_GRAPH_LOADED,
        started_at,
        warnings=[
            f"cargados {result.node_count} nodos / {result.relationship_count} relaciones "
            f"(Neo4j {result.server_version}, database {result.database!r})"
        ],
    )


def _run_graph_validated(
    state: RunState,
    semantic_graph_path: Path,
    semantic_enrichment_path: Path,
    invariants_path: Path,
    settings: Settings,
    run_json_path: Path,
) -> RunState:
    """Ejecuta GRAPH_VALIDATED (GraphInvariantValidator, Prompt 9 del
    runbook): detecta drift entre el artefacto y el estado real de Neo4j,
    ejecuta `queries/v1/invariants.cypher`, y persiste
    `artifacts/05-invariants.json`. Un invariante ERROR incumplido marca
    la etapa como FAILED (ver graph_validated_stage.py)."""
    started_at = _now()
    try:
        warnings = run_graph_validated_stage(
            run_id=state.run_id,
            source_package_hash=state.source_package_hash or "",
            run_stages=state.stages,
            semantic_graph_path=semantic_graph_path,
            semantic_enrichment_path=semantic_enrichment_path,
            invariants_cypher_path=settings.invariants_cypher_path,
            invariants_path=invariants_path,
            settings=settings,
        )
    except GraphValidationError as exc:
        return _mark_failed(
            state, run_json_path, PipelineStage.GRAPH_VALIDATED, started_at, str(exc)
        )

    return _mark_succeeded(
        state, run_json_path, PipelineStage.GRAPH_VALIDATED, started_at, warnings=warnings
    )


def _run_candidates_detected(
    state: RunState,
    semantic_graph_path: Path,
    semantic_enrichment_path: Path,
    invariants_path: Path,
    candidates_path: Path,
    settings: Settings,
    run_json_path: Path,
) -> RunState:
    """Ejecuta CANDIDATES_DETECTED (CandidateDetector/Q0, Prompt 10a del
    runbook): reejecuta precondiciones y Q0 en cada corrida, y persiste
    `artifacts/06-candidates.json` solo si el resultado recomputado
    difiere del existente (ver candidates_detected_stage.py)."""
    started_at = _now()
    try:
        warnings = run_candidates_detected_stage(
            run_id=state.run_id,
            source_package_hash=state.source_package_hash or "",
            run_stages=state.stages,
            semantic_graph_path=semantic_graph_path,
            semantic_enrichment_path=semantic_enrichment_path,
            invariants_path=invariants_path,
            q0_cypher_path=settings.q0_candidates_cypher_path,
            candidates_path=candidates_path,
            settings=settings,
        )
    except CandidateDetectionError as exc:
        return _mark_failed(
            state, run_json_path, PipelineStage.CANDIDATES_DETECTED, started_at, str(exc)
        )

    return _mark_succeeded(
        state, run_json_path, PipelineStage.CANDIDATES_DETECTED, started_at, warnings=warnings
    )


def _run_contexts_built(
    state: RunState,
    semantic_graph_path: Path,
    candidates_path: Path,
    context_dir: Path,
    settings: Settings,
    run_json_path: Path,
) -> RunState:
    """Ejecuta CONTEXTS_BUILT (ContextPackageBuilder/Q1-Q7, Prompt 10b del
    runbook): reejecuta precondiciones y Q1-Q7 en cada corrida, y
    reconstruye `artifacts/07-context/` solo si el resultado recomputado
    difiere del existente (ver contexts_built_stage.py)."""
    started_at = _now()
    try:
        warnings = run_contexts_built_stage(
            run_id=state.run_id,
            source_package_hash=state.source_package_hash or "",
            run_stages=state.stages,
            semantic_graph_path=semantic_graph_path,
            candidates_path=candidates_path,
            context_dir=context_dir,
            settings=settings,
        )
    except ContextBuildError as exc:
        return _mark_failed(
            state, run_json_path, PipelineStage.CONTEXTS_BUILT, started_at, str(exc)
        )

    return _mark_succeeded(
        state, run_json_path, PipelineStage.CONTEXTS_BUILT, started_at, warnings=warnings
    )


def _run_rule_drafts_generated(
    state: RunState,
    context_dir: Path,
    rule_draft_dir: Path,
    settings: Settings,
    run_json_path: Path,
) -> RunState:
    """Ejecuta RULE_DRAFTS_GENERATED (LlmRuleWriter, Prompt 12 del
    runbook): etapa atomica para el conjunto completo de candidatos —
    delega en `rule_drafts_generated_stage.run_rule_drafts_generated_stage`,
    que nunca promueve un exito parcial (ver ese modulo)."""
    started_at = _now()
    try:
        warnings = run_rule_drafts_generated_stage(
            run_id=state.run_id,
            source_package_hash=state.source_package_hash or "",
            run_stages=state.stages,
            context_dir=context_dir,
            rule_draft_dir=rule_draft_dir,
            settings=settings,
        )
    except RuleDraftGenerationError as exc:
        return _mark_failed(
            state, run_json_path, PipelineStage.RULE_DRAFTS_GENERATED, started_at, str(exc)
        )

    return _mark_succeeded(
        state, run_json_path, PipelineStage.RULE_DRAFTS_GENERATED, started_at, warnings=warnings
    )


def _run_guardrails_applied(
    state: RunState,
    context_dir: Path,
    rule_draft_dir: Path,
    guardrail_dir: Path,
    settings: Settings,
    run_json_path: Path,
) -> RunState:
    """Ejecuta GUARDRAILS_APPLIED (DeterministicGuardrail + repair loop,
    Prompt 12 del runbook): etapa atomica y fail-fast — delega en
    `guardrails_applied_stage.run_guardrails_applied_stage`, que se
    detiene en el primer candidato REJECTED definitivo (ver ese modulo)."""
    started_at = _now()
    try:
        warnings = run_guardrails_applied_stage(
            run_id=state.run_id,
            source_package_hash=state.source_package_hash or "",
            run_stages=state.stages,
            context_dir=context_dir,
            rule_draft_dir=rule_draft_dir,
            guardrail_dir=guardrail_dir,
            settings=settings,
        )
    except GuardrailError as exc:
        return _mark_failed(
            state, run_json_path, PipelineStage.GUARDRAILS_APPLIED, started_at, str(exc)
        )

    return _mark_succeeded(
        state, run_json_path, PipelineStage.GUARDRAILS_APPLIED, started_at, warnings=warnings
    )


def _run_rules_rendered(
    state: RunState,
    guardrail_dir: Path,
    rules_dir: Path,
    run_json_path: Path,
) -> RunState:
    """Ejecuta la transicion GUARDRAILS_APPLIED -> COMPLETED (Prompt 13a):
    delega en `rules_rendered_stage.run_rules_rendered_stage`, que
    renderiza cada `GuardrailCandidateArtifact.final_rule_draft` a
    Markdown y persiste `artifacts/10-rules/`. No agrega un
    `PipelineStage` nuevo: el resultado se registra como
    `StageExecution(stage=COMPLETED, ...)`, simetrico a cualquier otra
    etapa real (ver docstring de `rules_rendered_stage.py`)."""
    started_at = _now()
    try:
        warnings = run_rules_rendered_stage(
            run_id=state.run_id,
            source_package_hash=state.source_package_hash or "",
            run_stages=state.stages,
            guardrail_dir=guardrail_dir,
            rules_dir=rules_dir,
        )
    except MarkdownRenderError as exc:
        return _mark_failed(state, run_json_path, PipelineStage.COMPLETED, started_at, str(exc))

    return _mark_succeeded(
        state, run_json_path, PipelineStage.COMPLETED, started_at, warnings=warnings
    )


def run_ingestion(source_zip: Path, settings: Settings, run_id: str | None = None) -> RunState:
    """Ejecuta RECEIVED -> VALIDATED -> EXTRACTED -> INVENTORIED -> PARSED
    -> DEPENDENCIES_BUILT -> SEMANTIC_ENRICHMENT_BUILT -> SEMANTIC_GRAPH_BUILT
    -> SEMANTIC_GRAPH_LOADED -> GRAPH_VALIDATED -> CANDIDATES_DETECTED ->
    CONTEXTS_BUILT -> RULE_DRAFTS_GENERATED -> GUARDRAILS_APPLIED ->
    COMPLETED para `source_zip`.

    Si `run_id` se omite, se genera uno nuevo. Si se pasa un `run_id` de
    una ejecucion previa cuyo RECEIVED ya tuvo exito, `source_zip` se
    ignora y se continua desde el estado persistido (reanudacion minima).
    """
    state = prepare_received(source_zip, settings, run_id)
    run_id = state.run_id
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
    invariants_path = run_dir / "artifacts" / "05-invariants.json"
    candidates_path = run_dir / "artifacts" / "06-candidates.json"
    context_dir = run_dir / "artifacts" / "07-context"
    rule_draft_dir = run_dir / "artifacts" / "08-rule-drafts"
    guardrail_dir = run_dir / "artifacts" / "09-guardrails"
    rules_dir = run_dir / "artifacts" / "10-rules"

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

    state = _run_semantic_graph_built(
        state,
        inventory_path,
        canonical_dir,
        dependencies_path,
        semantic_enrichment_path,
        semantic_graph_path,
        run_json_path,
    )
    if state.current_stage == PipelineStage.FAILED:
        return state

    state = _run_semantic_graph_loaded(state, semantic_graph_path, settings, run_json_path)
    if state.current_stage == PipelineStage.FAILED:
        return state

    state = _run_graph_validated(
        state,
        semantic_graph_path,
        semantic_enrichment_path,
        invariants_path,
        settings,
        run_json_path,
    )
    if state.current_stage == PipelineStage.FAILED:
        return state

    state = _run_candidates_detected(
        state,
        semantic_graph_path,
        semantic_enrichment_path,
        invariants_path,
        candidates_path,
        settings,
        run_json_path,
    )
    if state.current_stage == PipelineStage.FAILED:
        return state

    state = _run_contexts_built(
        state, semantic_graph_path, candidates_path, context_dir, settings, run_json_path
    )
    if state.current_stage == PipelineStage.FAILED:
        return state

    state = _run_rule_drafts_generated(
        state, context_dir, rule_draft_dir, settings, run_json_path
    )
    if state.current_stage == PipelineStage.FAILED:
        return state

    state = _run_guardrails_applied(
        state, context_dir, rule_draft_dir, guardrail_dir, settings, run_json_path
    )
    if state.current_stage == PipelineStage.FAILED:
        return state

    return _run_rules_rendered(state, guardrail_dir, rules_dir, run_json_path)
