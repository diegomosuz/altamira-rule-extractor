"""SafeExtractor: extraccion transaccional, entrada por entrada.

Nunca usa `ZipFile.extractall()`. Cada entrada se re-verifica (path,
colision, tipo, cifrado, extension) inmediatamente antes de escribirse,
con exactamente las mismas reglas que PackageValidator ya aplico sobre
el listado — defensa en profundidad, no una reimplementacion distinta.

La extraccion es transaccional: escribe primero a un directorio temporal
hermano (`work/extracted.tmp-<token>`) y solo lo promueve a
`work/extracted/` si termina sin errores. Ante cualquier fallo se limpia
el temporal por completo, se preserva el error original (agregando,
como nota adicional, cualquier fallo de limpieza) y nunca se llega a
construir `artifacts/01-inventory.json`.

Durante el streaming se cuenta cada byte realmente escrito: los limites
declarados en el ZipInfo no son de fiar por si solos (una entrada puede
mentir sobre su tamano), asi que se aborta apenas el conteo real supera
el limite individual o acumulado, o si al terminar no coincide con el
tamano declarado.
"""

from __future__ import annotations

import secrets
import shutil
import zipfile
from pathlib import Path

from ..config import Settings
from .artifact_store import atomic_promote_directory
from .errors import ExtractionError, ZipSecurityError
from .zip_entries import classify_entry_type, extension_of, is_encrypted
from .zip_paths import collision_key, normalize_zip_entry_path

_CHUNK_SIZE = 1024 * 1024


def extract_package(zip_path: Path, work_dir: Path, settings: Settings) -> Path:
    """Extrae `zip_path` a `work_dir/extracted` de forma transaccional."""
    final_dir = work_dir / "extracted"
    if final_dir.exists():
        raise ExtractionError(
            f"{final_dir} ya existe; no se extrae sobre un directorio previo"
        )

    tmp_dir = work_dir / f"extracted.tmp-{secrets.token_hex(8)}"
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        tmp_dir.mkdir(parents=False, exist_ok=False)
        _extract_entries(zip_path, tmp_dir, settings)
        atomic_promote_directory(tmp_dir, final_dir)
    except Exception as exc:
        cleanup_note = _cleanup(tmp_dir)
        message = str(exc) if str(exc) else repr(exc)
        if cleanup_note:
            message = f"{message}; {cleanup_note}"
        raise ExtractionError(message) from exc

    return final_dir


def _cleanup(tmp_dir: Path) -> str | None:
    try:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
    except OSError as cleanup_exc:
        return f"fallo la limpieza del temporal {tmp_dir}: {cleanup_exc}"
    return None


def _safe_destination(tmp_root: Path, normalized: str) -> Path:
    destination = tmp_root / normalized
    resolved_root = tmp_root.resolve()
    resolved_destination = destination.resolve()
    if not resolved_destination.is_relative_to(resolved_root):
        raise ZipSecurityError(
            f"la ruta resuelta de {normalized!r} escapa del directorio de extraccion"
        )
    return destination


def _extract_entries(zip_path: Path, tmp_dir: Path, settings: Settings) -> None:
    seen_keys: dict[str, str] = {}
    total_written = 0

    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            normalized = normalize_zip_entry_path(info.filename)

            key = collision_key(normalized)
            if key in seen_keys and seen_keys[key] != normalized:
                raise ZipSecurityError(
                    f"colision de nombres con {seen_keys[key]!r}: {normalized!r}"
                )
            seen_keys[key] = normalized

            entry_type = classify_entry_type(info, normalized)
            destination = _safe_destination(tmp_dir, normalized)

            if entry_type == "dir":
                destination.mkdir(parents=True, exist_ok=True)
                continue

            if is_encrypted(info):
                raise ZipSecurityError(f"entrada cifrada no permitida: {normalized!r}")

            extension = extension_of(normalized)
            if extension not in settings.allowed_extensions:
                raise ZipSecurityError(
                    f"extension no permitida {extension!r}: {normalized!r}"
                )

            destination.parent.mkdir(parents=True, exist_ok=True)
            total_written = _stream_entry(
                zf, info, normalized, destination, settings, total_written
            )


def _stream_entry(
    zf: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    normalized: str,
    destination: Path,
    settings: Settings,
    total_written_so_far: int,
) -> int:
    written = 0
    total_written = total_written_so_far
    with zf.open(info) as source, destination.open("xb") as target:
        while True:
            chunk = source.read(_CHUNK_SIZE)
            if not chunk:
                break
            written += len(chunk)
            total_written += len(chunk)
            if written > settings.max_single_entry_uncompressed_bytes:
                raise ZipSecurityError(
                    f"entrada {normalized!r} escribio {written} bytes reales, supera "
                    f"el limite individual {settings.max_single_entry_uncompressed_bytes}"
                )
            if total_written > settings.max_total_uncompressed_bytes:
                raise ZipSecurityError(
                    "la extraccion acumulada real supera el limite total "
                    f"{settings.max_total_uncompressed_bytes} bytes"
                )
            target.write(chunk)

    if written != info.file_size:
        raise ZipSecurityError(
            f"entrada {normalized!r} escribio {written} bytes reales, declaraba "
            f"{info.file_size}: tamano declarado inconsistente"
        )
    return total_written
