"""Executor local acotado (Prompt 13b): unica pieza de estado mutable de
la API, encapsulada en una instancia (no un global de modulo) creada por
`api/app.py::create_app` dentro del `lifespan` y expuesta via
`Depends(get_executor)` -- coherente con "sin estado global mutable"
(python.md): el estado vive en la instancia de la app, no en el modulo.

La coordinacion es UNICAMENTE por proceso: dos procesos Uvicorn no
comparten este registro. V1 requiere un unico worker (ver docstring de
`api/app.py`). No hay cola externa (Celery/Redis/Kafka): el registro de
`run_id` activos ES la unica cola, deliberadamente acotada a
`max_workers` -- nunca se delega en la cola interna, ilimitada, de
`ThreadPoolExecutor` (un `submit()` solo ocurre despues de reservar un
lugar en el registro)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from ..contracts.run_state import RunState
from ..logging_setup import LogEventName, log_event
from ..observability.metrics import ObservabilityRegistry
from ..pipeline.errors import RunStatePersistenceError

logger = logging.getLogger(__name__)

SubmitResult = Literal["submitted", "already_active", "at_capacity"]


class RunExecutor:
    def __init__(self, max_workers: int, *, metrics: ObservabilityRegistry | None = None) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._max_workers = max_workers
        self._lock = threading.Lock()
        self._active_run_ids: set[str] = set()
        self._metrics = metrics

    def is_active(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._active_run_ids

    def try_submit(self, run_id: str, fn: Callable[[], object]) -> SubmitResult:
        """Reserva un lugar para `run_id` en el registro (bajo `_lock`) y
        SOLO entonces llama `executor.submit` -- el registro, acotado a
        `max_workers`, es la cola real; el `ThreadPoolExecutor` nunca
        acumula trabajo mas alla de eso."""
        with self._lock:
            if run_id in self._active_run_ids:
                return "already_active"
            if len(self._active_run_ids) >= self._max_workers:
                if self._metrics is not None:
                    self._metrics.inc_executor_capacity_rejection()
                return "at_capacity"
            self._active_run_ids.add(run_id)
            if self._metrics is not None:
                self._metrics.set_executor_active_runs(len(self._active_run_ids))

        def _wrapped() -> None:
            try:
                result = fn()
                if self._metrics is not None and isinstance(result, RunState):
                    self._metrics.observe_pipeline_run(result)
            except RunStatePersistenceError as exc:
                # Checkpoint correctivo (defecto real de estabilizacion de
                # baseline): distinto de un fallo generico -- run.json en
                # si no pudo persistirse para esta transicion terminal.
                # Nunca se trata igual que `except Exception` de mas
                # abajo: aqui SI se confirma (o se desmiente) que existe
                # evidencia durable del desenlace antes de decidir el
                # nivel de severidad del log. Nunca incluye credenciales,
                # prompts ni un path absoluto mas alla del propio
                # `run_id` (ya publico) y el nombre corto del artefacto.
                if exc.emergency_artifact_path is not None:
                    logger.warning(
                        "no se pudo persistir run.json para run_id=%s etapa=%s "
                        "transicion=%s; el run quedara FAILED via el artefacto de "
                        "emergencia %s",
                        exc.run_id,
                        exc.stage.value,
                        exc.transition,
                        exc.emergency_artifact_path.name,
                    )
                else:
                    logger.critical(
                        "no se pudo persistir run.json NI el artefacto de emergencia "
                        "para run_id=%s etapa=%s transicion=%s: sin ninguna evidencia "
                        "durable del desenlace de este run (causa=%s)",
                        exc.run_id,
                        exc.stage.value,
                        exc.transition,
                        type(exc.cause).__name__,
                    )
            except Exception as exc:
                log_event(
                    logger,
                    logging.ERROR,
                    LogEventName.BACKGROUND_RUN_FAILED,
                    run_id=run_id,
                    exception_type=type(exc).__name__,
                )
            finally:
                with self._lock:
                    self._active_run_ids.discard(run_id)
                    if self._metrics is not None:
                        self._metrics.set_executor_active_runs(len(self._active_run_ids))

        self._executor.submit(_wrapped)
        return "submitted"

    def shutdown(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait)
