"""Casos de uso compartidos de creacion/reanudacion de runs (Prompt 13d).

Extraidos de `api/routers/runs.py::create_run`/`resume_run`: la UI
(`ui/router.py`) necesita exactamente la misma orquestacion que
`POST /api/runs` y `POST /api/runs/{run_id}/resume` (streaming del
upload, `prepare_received` sincrono, `executor.try_submit`) -- en vez de
duplicarla por tercera vez, ambos routers llaman estas mismas funciones.
Devuelven/mutan `RunState`, nunca un modelo de respuesta HTTP: cada
router le da la forma que le corresponde (JSON `RunAcceptedResponse` vs.
redirect HTML). No es una clase `ApplicationService` generica -- son dos
funciones explicitas, del mismo tamano y forma que ya tenian los
handlers originales."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import UploadFile

from ..config import Settings
from ..contracts.enums import PipelineStage
from ..contracts.run_state import RunState
from ..pipeline.runner import prepare_received, run_ingestion
from .duplicate_detection import create_duplicate_reference, find_authoritative_run
from .errors import (
    ArtifactCorruptedError,
    ExecutorAtCapacityError,
    RunAlreadyActiveError,
    RunNotResumableError,
    ServiceUnavailableError,
)
from .executor import RunExecutor
from .uploads import stream_upload_to_incoming


def create_and_submit_run(file: UploadFile, settings: Settings, executor: RunExecutor) -> RunState:
    """Mismo flujo que `create_run` (Prompt 13b): stream a `incoming/`,
    RECEIVED sincrono via `prepare_received`, resto programado en el
    executor. Puede lanzar `ServiceUnavailableError`/
    `ExecutorAtCapacityError`/`RunAlreadyActiveError` (este ultimo
    inalcanzable en la practica para un `run_id` recien generado, se
    maneja por completitud, igual que antes).

    Deteccion de duplicados (Fase v1.17.1, Feature 6): DESPUES de que
    RECEIVED tenga exito real (el `source_package_hash` exacto ya esta
    establecido -- identidad de paquete nunca por filename, ver
    `duplicate_detection.py`), si ya existe un run autoritativo real con
    el MISMO hash, este run recien creado (solo `input/package.zip` +
    `run.json`, nunca sometido al executor) se descarta y se persiste en
    su lugar un registro de referencia liviano que apunta al run
    autoritativo -- nunca se lanza un segundo pipeline identico ni se
    guarda una segunda copia del ZIP (ver "DUPLICATE RECORD OWNERSHIP")."""
    temp_path = stream_upload_to_incoming(file, settings)
    try:
        state = prepare_received(temp_path, settings)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    if state.current_stage == PipelineStage.FAILED:
        raise ServiceUnavailableError(
            "no se pudo inicializar el run (fallo de escritura local en RECEIVED)"
        )

    run_id = state.run_id
    run_dir = settings.runs_dir / run_id
    input_zip_path = run_dir / "input" / "package.zip"

    if state.source_package_hash is not None:
        match = find_authoritative_run(
            settings, executor, state.source_package_hash, exclude_run_id=run_id
        )
        if match is not None:
            shutil.rmtree(run_dir, ignore_errors=True)
            return create_duplicate_reference(
                settings,
                source_package_hash=state.source_package_hash,
                original_run_id=match.run_id,
            )

    result = executor.try_submit(
        run_id, lambda: run_ingestion(input_zip_path, settings, run_id=run_id)
    )
    if result == "at_capacity":
        raise ExecutorAtCapacityError(run_id)
    if result == "already_active":
        raise RunAlreadyActiveError()

    return state


def submit_existing_run(
    run_dir: Path, run_id: str, state: RunState, settings: Settings, executor: RunExecutor
) -> None:
    """Mismo flujo que `resume_run` (Prompt 13b): exige que el run no
    este `COMPLETED`, que `input/package.zip` sea un archivo regular no
    symlink, y programa `run_ingestion` en el executor. No relee
    `RunState`: el llamador ya lo tiene (via `read_run_state`) y decide
    que hacer con el estado post-submit."""
    if state.current_stage == PipelineStage.COMPLETED:
        raise RunNotResumableError()

    input_zip_path = run_dir / "input" / "package.zip"
    if input_zip_path.is_symlink() or not input_zip_path.is_file():
        raise ArtifactCorruptedError("input/package.zip ausente, no regular o symlink")

    result = executor.try_submit(
        run_id, lambda: run_ingestion(input_zip_path, settings, run_id=run_id)
    )
    if result == "already_active":
        raise RunAlreadyActiveError()
    if result == "at_capacity":
        raise ExecutorAtCapacityError(run_id)
