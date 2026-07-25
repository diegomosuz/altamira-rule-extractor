"""Recepcion segura del upload multipart (Prompt 13b).

Nunca usa `UploadFile.filename` (nombre original del cliente) ni su
`Content-Type` declarado para nada relacionado a un path: siempre
escribe a un archivo temporal regular dentro de `settings.incoming_dir`
con un nombre generado por el servidor. Mide el tamano DURANTE el
streaming (nunca bufferiza el upload completo en memoria antes de
medir) y rechaza antes de programar cualquier ejecucion. El validador
real del contenido del ZIP sigue siendo `pipeline/package_validator.py`
(etapa VALIDATED, sin cambios) -- este modulo solo transporta bytes con
seguridad."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import UploadFile

from ..config import Settings
from .errors import EmptyUploadError, UploadTooLargeError

_UPLOAD_CHUNK_SIZE = 1024 * 1024


def stream_upload_to_incoming(upload: UploadFile, settings: Settings) -> Path:
    """Transmite `upload` a un archivo temporal regular dentro de
    `settings.incoming_dir`. Nunca deriva el nombre desde
    `upload.filename`. Levanta `EmptyUploadError` (archivo vacio) o
    `UploadTooLargeError` (excede `settings.max_package_size_bytes`) y
    limpia el temporal parcial antes de propagar."""
    settings.incoming_dir.mkdir(parents=True, exist_ok=True)
    temp_path = settings.incoming_dir / f"{uuid.uuid4().hex}.upload"

    total_bytes = 0
    try:
        with temp_path.open("wb") as destination:
            while True:
                chunk = upload.file.read(_UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > settings.max_package_size_bytes:
                    raise UploadTooLargeError(
                        "el upload excede el limite configurado "
                        f"({settings.max_package_size_bytes} bytes)"
                    )
                destination.write(chunk)
    except BaseException:
        if temp_path.exists():
            temp_path.unlink()
        raise

    if total_bytes == 0:
        temp_path.unlink()
        raise EmptyUploadError()

    return temp_path
