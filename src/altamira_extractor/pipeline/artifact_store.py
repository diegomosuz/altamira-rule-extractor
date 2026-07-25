"""Escritura atomica de artefactos del pipeline (run.json, inventory.json, ...).

Nunca se usa un nombre de temporal fijo (p. ej. "run.tmp"): colisionaria
entre ejecuciones concurrentes. Se usa `tempfile.mkstemp` en el mismo
directorio del destino (mismo volumen, para que `os.replace` sea atomico
tanto en POSIX como en Windows), con write + flush + fsync + replace, y
limpieza del temporal en `finally` si algo fallo antes del replace.

Prompt 14b.1: en Windows, `os.replace` puede fallar con
`PermissionError [WinError 5] Access is denied` cuando otro proceso/hilo
tiene el destino abierto para lectura en ese instante exacto (p. ej. la
UI haciendo polling de `run.json` mientras el executor lo persiste
concurrentemente) -- `MoveFileEx` exige poder abrir el destino en modo
exclusivo por una fraccion de segundo, y una lectura concurrente sin
`FILE_SHARE_DELETE` lo bloquea transitoriamente. Es una carrera de
milisegundos, no una falla real de permisos: `_replace_with_retry`
reintenta unicamente `PermissionError`, con un numero fijo y acotado de
intentos y backoff corto (no configurable, no depende de `Settings`).
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from ..contracts.base import AltamiraBaseModel

# Intento inicial + hasta 5 reintentos (10, 20, 40, 80 y 160 ms): tiempo
# total maximo de espera acotado a 310 ms. Solo PermissionError dispara
# un reintento; cualquier otro OSError se propaga de inmediato.
_REPLACE_MAX_ATTEMPTS = 6
_REPLACE_RETRY_DELAYS_SECONDS = (0.01, 0.02, 0.04, 0.08, 0.16)


def _replace_with_retry(tmp_path: Path, path: Path) -> None:
    last_error: PermissionError | None = None
    for attempt in range(_REPLACE_MAX_ATTEMPTS):
        try:
            os.replace(tmp_path, path)
            return
        except PermissionError as exc:
            last_error = exc
            if attempt < len(_REPLACE_RETRY_DELAYS_SECONDS):
                time.sleep(_REPLACE_RETRY_DELAYS_SECONDS[attempt])
    assert last_error is not None  # el bucle siempre corrio >= 1 vez
    raise last_error


def atomic_write_json(path: Path, model: AltamiraBaseModel) -> None:
    """Serializa `model` a JSON estable y lo escribe atomicamente en `path`."""
    payload = model.to_stable_json()
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                # Best effort: nunca oculta el PermissionError original
                # de _replace_with_retry si el agotamiento de intentos
                # fue lo que en realidad fallo esta operacion.
                pass


def atomic_promote_directory(source_tmp_dir: Path, destination_dir: Path) -> None:
    """Promueve un directorio temporal ya completo a su ubicacion final.

    Se apoya en `os.replace`, atomico en POSIX y en Windows cuando origen
    y destino residen en el mismo volumen. `destination_dir` no debe
    existir todavia (una ejecucion fresca nunca deja restos: los fallos
    limpian el temporal, y el destino final solo se crea por esta via).
    """
    destination_dir.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source_tmp_dir, destination_dir)
