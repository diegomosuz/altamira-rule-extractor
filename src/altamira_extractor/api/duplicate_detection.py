"""Deteccion de paquetes duplicados por hash exacto (Fase v1.17.1,
Feature 6): identidad de paquete = `source_package_hash` EXACTO, nunca
filename -- mismo bytes con distinto nombre es el MISMO paquete; mismo
nombre con bytes distintos es un paquete DISTINTO. Reutiliza el hash ya
calculado sincronicamente por `pipeline/runner.py::_copy_and_hash` en
RECEIVED (SHA-256): este modulo nunca vuelve a hashear nada, solo
compara `RunState.source_package_hash` ya persistidos.

Un registro de referencia (`RunState.duplicate_of_run_id is not None`)
nunca cuenta como run autoritativo: solo un run "real" (que de verdad
intento ejecutar el pipeline) puede serlo, evitando que una cadena de
referencias apunte a otra referencia."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from ..config import Settings
from ..contracts.enums import PipelineStage, StageStatus
from ..contracts.run_state import RunState, StageExecution
from ..pipeline.artifact_store import atomic_write_json
from ..pipeline.runner import generate_run_id
from .errors import ApiError
from .executor import RunExecutor
from .reads import read_run_state


@dataclass(frozen=True)
class AuthoritativeRunMatch:
    """Un run REAL (nunca una referencia) que ya posee el mismo
    `source_package_hash` exacto que un nuevo upload. `is_active`: hay
    una ejecucion en curso ahora mismo para este `run_id` (Feature 6,
    caso A). Si no esta activo, `state.current_stage` distingue COMPLETED
    (caso B, "YA PROCESADO") de FAILED (caso C, con `source_package_hash`
    ya establecido -- ver docstring de `find_authoritative_run`)."""

    run_id: str
    state: RunState
    is_active: bool


def find_authoritative_run(
    settings: Settings,
    executor: RunExecutor,
    source_package_hash: str,
    *,
    exclude_run_id: str | None = None,
) -> AuthoritativeRunMatch | None:
    """Busca, entre todos los runs persistidos, el run autoritativo real
    cuyo `source_package_hash` coincide exactamente.

    "Autoritativo" (candidato a duplicado) es, en orden de prioridad:

    1. Activo ahora mismo (`executor.is_active`): el paquete ya se esta
       procesando -- caso A.
    2. `COMPLETED`: ya fue procesado por completo -- caso B ("YA
       PROCESADO").
    3. `FAILED` con `source_package_hash` ya establecido: el fallo
       ocurrio DESPUES de que RECEIVED tuviera exito real (un fallo EN
       RECEIVED nunca establece `source_package_hash` -- ver
       `runner.py::_run_received` -- asi que nunca puede llegar a este
       caso; ningun caso especial adicional hace falta). Se trata como
       autoritativo (caso C) porque ya existe infraestructura para este
       paquete: la accion correcta es referenciarlo y dejar que el
       analista use `Reanudar` sobre el run real, nunca lanzar un
       segundo pipeline identico en paralelo.

    Si existen varios candidatos con el mismo hash, se prefiere el de
    mayor prioridad (activo > completado > fallido) y, dentro de la
    misma prioridad, el `run_id` mas reciente (los `run_id` son
    timestamp-prefijados y ordenables lexicograficamente, ver
    `runner.py::generate_run_id`)."""
    if not settings.runs_dir.is_dir():
        return None

    best_active: RunState | None = None
    best_completed: RunState | None = None
    best_failed: RunState | None = None

    for entry in settings.runs_dir.iterdir():
        if entry.is_symlink() or not entry.is_dir():
            continue
        if entry.name == exclude_run_id:
            continue
        try:
            state = read_run_state(entry)
        except ApiError:
            continue
        if state.duplicate_of_run_id is not None:
            continue
        if state.source_package_hash != source_package_hash:
            continue

        if executor.is_active(state.run_id):
            if best_active is None or state.run_id > best_active.run_id:
                best_active = state
        elif state.current_stage == PipelineStage.COMPLETED:
            if best_completed is None or state.run_id > best_completed.run_id:
                best_completed = state
        elif state.current_stage == PipelineStage.FAILED:
            if best_failed is None or state.run_id > best_failed.run_id:
                best_failed = state

    if best_active is not None:
        return AuthoritativeRunMatch(run_id=best_active.run_id, state=best_active, is_active=True)
    if best_completed is not None:
        return AuthoritativeRunMatch(
            run_id=best_completed.run_id, state=best_completed, is_active=False
        )
    if best_failed is not None:
        return AuthoritativeRunMatch(run_id=best_failed.run_id, state=best_failed, is_active=False)
    return None


def create_duplicate_reference(
    settings: Settings, *, source_package_hash: str, original_run_id: str
) -> RunState:
    """Crea un registro de referencia liviano (Feature 6): su propio
    `run_id`/directorio, conteniendo UNICAMENTE `run.json` -- nunca
    `input/`, `work/` ni `artifacts/` (nunca una segunda copia del ZIP
    subido, nunca un segundo pipeline real, ver "DUPLICATE RECORD
    OWNERSHIP"). `current_stage=RECEIVED` con una unica `StageExecution`
    SUCCEEDED es literalmente verdadero: lo unico que ocurrio fue
    reconocer de inmediato la coincidencia de hash, nunca se fabrica una
    etapa que no ocurrio. `package_filename` reutiliza el mismo literal
    convencional que cualquier run real (`prepare_received`) -- ningun
    run, real o de referencia, persiste el nombre original elegido por
    el analista."""
    run_id = generate_run_id()
    run_dir = settings.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    now = datetime.now(UTC)
    state = RunState(
        run_id=run_id,
        package_filename="input/package.zip",
        source_package_hash=source_package_hash,
        current_stage=PipelineStage.RECEIVED,
        stages=[
            StageExecution(
                stage=PipelineStage.RECEIVED,
                status=StageStatus.SUCCEEDED,
                started_at=now,
                finished_at=now,
                duration_seconds=0.0,
            )
        ],
        created_at=now,
        updated_at=now,
        duplicate_of_run_id=original_run_id,
    )
    atomic_write_json(run_dir / "run.json", state)
    return state
