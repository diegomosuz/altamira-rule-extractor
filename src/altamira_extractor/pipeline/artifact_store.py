"""Escritura atomica de artefactos del pipeline (run.json, inventory.json, ...).

Nunca se usa un nombre de temporal fijo (p. ej. "run.tmp"): colisionaria
entre ejecuciones concurrentes. Se usa `tempfile.mkstemp` en el mismo
directorio del destino (mismo volumen, para que `os.replace` sea atomico
tanto en POSIX como en Windows), con write + flush + fsync + replace, y
limpieza del temporal en `finally` si algo fallo antes del replace.

Prompt 14b.1 / checkpoint correctivo (defecto real de estabilizacion de
baseline): en Windows, `os.replace` puede fallar con `PermissionError`
cuando otro proceso/hilo tiene el destino abierto en ese instante exacto
(p. ej. la UI haciendo polling de `run.json` mientras el executor lo
persiste concurrentemente) -- `MoveFileEx` exige poder abrir el destino
en modo exclusivo por una fraccion de segundo, y una lectura concurrente
sin `FILE_SHARE_DELETE` lo bloquea transitoriamente. Verificado
empiricamente en Windows nativo (no solo en teoria): bajo un lector en
bucle cerrado sin ninguna espera entre lecturas (una contencion mas
agresiva que el polling real de la UI, que ocurre cada 3s -- ver
`ui/templates/_status_fragment.html`), el presupuesto FIJO anterior de
6 intentos / ~310ms totales SI se agota de forma reproducible (no es un
caso hipotetico). `_replace_with_retry` reintenta unicamente errores de
reemplazo reconocidos como transitorios (WinError 5/32/33: acceso
denegado, violacion de uso compartido, violacion de bloqueo -- ver
`_is_transient_replace_error`), con una politica de DEADLINE monotonico
(`time.monotonic()`) y backoff exponencial acotado, nunca con un numero
fijo de intentos. En sistemas no-Windows, `PermissionError.winerror` no
existe (siempre `None`): ningun `PermissionError` real de POSIX se
reintenta jamas, se propaga de inmediato sin envolver -- un permiso
insuficiente en POSIX es permanente, no una carrera transitoria de
milisegundos, y nunca debe tratarse como reintentable "por si acaso".
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from ..contracts.base import AltamiraBaseModel
from .errors import AtomicWriteError

# WinError 5 (ERROR_ACCESS_DENIED) fue reproducido empiricamente en
# Windows nativo bajo contencion real (ver docstring del modulo). WinError
# 32 (ERROR_SHARING_VIOLATION) y 33 (ERROR_LOCK_VIOLATION) se incluyen por
# ser la misma familia documentada de fallos transitorios de
# MoveFileEx/ReplaceFile por otro handle abierto sobre el destino, pero
# NO fueron reproducidos empiricamente en este entorno -- si en el futuro
# se observa que surgen con un tipo de excepcion distinto de
# PermissionError, esta clasificacion debe revisarse.
_TRANSIENT_REPLACE_WINERRORS = frozenset({5, 32, 33})

# Deadline por defecto para artefactos ordinarios: > 3x el presupuesto fijo
# anterior (310ms) que se demostro insuficiente bajo contencion real, y
# ampliamente por debajo del intervalo de polling de la UI (3s) para que
# un artefacto ordinario nunca sea el cuello de botella percibido por el
# cliente.
_DEFAULT_REPLACE_DEADLINE_SECONDS = 1.0
_INITIAL_BACKOFF_SECONDS = 0.01
_MAX_BACKOFF_SECONDS = 0.16
_BACKOFF_MULTIPLIER = 2.0


def _is_transient_replace_error(exc: OSError) -> bool:
    """`winerror` es un atributo exclusivo de Windows (ausente/`None` en
    POSIX): esta funcion nunca clasifica un `PermissionError` de POSIX
    como transitorio, sin necesidad de ramificar por `sys.platform`."""
    return getattr(exc, "winerror", None) in _TRANSIENT_REPLACE_WINERRORS


def _replace_with_retry(tmp_path: Path, path: Path, *, deadline_seconds: float) -> None:
    deadline = time.monotonic() + deadline_seconds
    backoff = _INITIAL_BACKOFF_SECONDS
    attempts = 0
    last_error: PermissionError | None = None

    while True:
        attempts += 1
        try:
            os.replace(tmp_path, path)
            return
        except PermissionError as exc:
            if not _is_transient_replace_error(exc):
                raise  # error permanente (o POSIX real): nunca se reintenta ni se envuelve.
            last_error = exc
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0:
                break
            time.sleep(min(backoff, _MAX_BACKOFF_SECONDS, remaining))
            backoff = min(backoff * _BACKOFF_MULTIPLIER, _MAX_BACKOFF_SECONDS)

    assert last_error is not None  # el bucle siempre corrio >= 1 vez
    raise AtomicWriteError(
        f"no se pudo reemplazar {path.name!r} de forma atomica: se agoto el deadline de "
        f"{deadline_seconds:.3f}s ({attempts} intento(s)) reintentando un error transitorio "
        f"de reemplazo (WinError {last_error.winerror})"
    ) from last_error


def _fsync_directory_best_effort(directory: Path) -> None:
    """Durabilidad adicional en POSIX: fsync del directorio padre asegura
    que la entrada renombrada por `os.replace` sobreviva a un crash
    inmediatamente posterior. Windows no soporta abrir un directorio con
    `os.open`/`os.fsync` de esta forma -- no se intenta alli. Best effort:
    un fallo aqui (p. ej. filesystem que no lo soporta) nunca invalida una
    escritura que ya se completo correctamente."""
    if os.name != "posix":
        return
    try:
        dir_fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def atomic_write_json(
    path: Path,
    model: AltamiraBaseModel,
    *,
    max_wait_seconds: float = _DEFAULT_REPLACE_DEADLINE_SECONDS,
) -> None:
    """Serializa `model` a JSON estable y lo escribe atomicamente en `path`.

    `max_wait_seconds` (keyword-only, con default): deadline total para
    `_replace_with_retry`. La firma posicional (`path`, `model`) no
    cambia -- codigo existente que la llama sin este parametro se
    comporta igual que antes, solo con una politica de reintento mas
    robusta (deadline monotonico en vez de intentos fijos)."""
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
        _replace_with_retry(tmp_path, path, deadline_seconds=max_wait_seconds)
        _fsync_directory_best_effort(path.parent)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                # Best effort: nunca oculta el error original de
                # _replace_with_retry si el agotamiento del deadline fue
                # lo que en realidad fallo esta operacion.
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
