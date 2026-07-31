"""Artefacto NO contractual: evidencia terminal durable para cuando
`run.json` (contrato en `contracts/run_state.py`, sin cambios) no pudo
persistirse tras agotar la politica critica de reemplazo atomico
(`pipeline.errors.AtomicWriteError`).

Nunca reemplaza ni reinterpreta el contrato de `run.json`: es un archivo
adyacente, de nombre unico e inmutable
(`run-state-persistence-failure-<timestamp>-<sufijo>.json`), que
`api/reads.py::read_run_state` consulta UNICAMENTE cuando `run.json` esta
ausente/invalido o su `current_stage` no es terminal (`FAILED`/
`COMPLETED`) y este artefacto es mas reciente que `run.json.updated_at`.
Un `run.json` terminal siempre prevalece, sin importar que artefactos de
emergencia (obsoletos) existan.

Contenido deliberadamente saneado (nunca prompts, respuestas LLM,
contenido COBOL, credenciales, stacktrace completo ni paths absolutos
sensibles mas alla de lo que `RunState`/`StageExecution.error` ya
toleran hoy en `run.json`): `error_message` es un mensaje generico fijo,
nunca `str(excepcion)` textual (que incluiria la ruta destino del
reemplazo fallido)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import AltamiraBaseModel
from .enums import PipelineStage
from .run_state import RunState

# Prefijo compartido entre el escritor (pipeline.runner) y el lector
# (api.reads): nombre nuevo e inmutable por intento (ver
# pipeline.runner._emergency_record_filename), nunca "run.json" ni un
# nombre fijo que un lector concurrente pudiera tener abierto.
EMERGENCY_RECORD_PREFIX = "run-state-persistence-failure-"


class RunStatePersistenceFailureRecord(AltamiraBaseModel):
    """Snapshot minimo para reconstruir una vista terminal FAILED cuando
    `run.json` mismo no pudo escribirse. `attempted_state` es el
    `RunState` completo (ya valido contra su propio contrato) que se
    intento persistir, con su transicion final sobrescrita a FAILED por
    quien construye este registro (`pipeline.runner`) -- nunca se afirma
    un SUCCEEDED que no llego a quedar durable."""

    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    timestamp_utc: datetime
    stage: PipelineStage
    transition: Literal["FAILED", "SUCCEEDED"]
    error_type: str
    error_message: str
    attempted_state: RunState
