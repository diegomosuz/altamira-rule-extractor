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

logger = logging.getLogger(__name__)

SubmitResult = Literal["submitted", "already_active", "at_capacity"]


class RunExecutor:
    def __init__(self, max_workers: int) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._max_workers = max_workers
        self._lock = threading.Lock()
        self._active_run_ids: set[str] = set()

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
                return "at_capacity"
            self._active_run_ids.add(run_id)

        def _wrapped() -> None:
            try:
                fn()
            except Exception:
                logger.exception("fallo no controlado ejecutando run_id=%s en background", run_id)
            finally:
                with self._lock:
                    self._active_run_ids.discard(run_id)

        self._executor.submit(_wrapped)
        return "submitted"

    def shutdown(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait)
