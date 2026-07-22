"""Escritura atomica de artefactos del pipeline (run.json, inventory.json, ...).

Nunca se usa un nombre de temporal fijo (p. ej. "run.tmp"): colisionaria
entre ejecuciones concurrentes. Se usa `tempfile.mkstemp` en el mismo
directorio del destino (mismo volumen, para que `os.replace` sea atomico
tanto en POSIX como en Windows), con write + flush + fsync + replace, y
limpieza del temporal en `finally` si algo fallo antes del replace.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from ..contracts.base import AltamiraBaseModel


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
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def atomic_promote_directory(source_tmp_dir: Path, destination_dir: Path) -> None:
    """Promueve un directorio temporal ya completo a su ubicacion final.

    Se apoya en `os.replace`, atomico en POSIX y en Windows cuando origen
    y destino residen en el mismo volumen. `destination_dir` no debe
    existir todavia (una ejecucion fresca nunca deja restos: los fallos
    limpian el temporal, y el destino final solo se crea por esta via).
    """
    destination_dir.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source_tmp_dir, destination_dir)
