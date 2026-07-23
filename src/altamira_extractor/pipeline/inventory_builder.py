"""InventoryBuilder: construye Inventory a partir de work/extracted.

Se ejecuta unicamente despues de que SafeExtractor promovio el
directorio final (ya validado y seguro): aqui solo se hashea, mide y
clasifica cada archivo, sin repetir controles de seguridad.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..contracts.enums import InventoryFileKind
from ..contracts.inventory import Inventory, InventoryFile
from ..contracts.manifest import Manifest
from .encoding_detector import detect_file_encoding
from .zip_entries import extension_of

_CHUNK_SIZE = 1024 * 1024
_MANIFEST_RELATIVE_PATH = "manifest.xml"

_EXTENSION_TO_KIND: dict[str, InventoryFileKind] = {
    ".cbl": InventoryFileKind.COBOL,
    ".cob": InventoryFileKind.COBOL,
    ".cpy": InventoryFileKind.COPYBOOK,
    ".copy": InventoryFileKind.COPYBOOK,
    ".dcl": InventoryFileKind.DCLGEN,
    ".sql": InventoryFileKind.DDL,
    ".ddl": InventoryFileKind.DDL,
    ".csv": InventoryFileKind.SNAPSHOT,
}


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _classify(relative_path: str, warnings: list[str]) -> InventoryFileKind:
    if relative_path == _MANIFEST_RELATIVE_PATH:
        return InventoryFileKind.MANIFEST

    kind = _EXTENSION_TO_KIND.get(extension_of(relative_path))
    if kind is not None:
        return kind

    warnings.append(f"archivo sin kind especifico, clasificado OTHER: {relative_path!r}")
    return InventoryFileKind.OTHER


def build_inventory(
    extracted_dir: Path,
    run_id: str,
    source_package_hash: str,
    manifest: Manifest,
) -> Inventory:
    """Recorre `extracted_dir` y construye el artefacto Inventory tipado."""
    files: list[InventoryFile] = []
    warnings: list[str] = []
    declared_encoding = manifest.source.encoding

    for path in sorted(extracted_dir.rglob("*")):
        if path.is_dir():
            continue

        relative_path = path.relative_to(extracted_dir).as_posix()
        detected_encoding, encoding_warning = detect_file_encoding(
            path.read_bytes(), declared_encoding=declared_encoding, relative_path=relative_path
        )
        if encoding_warning is not None:
            warnings.append(encoding_warning)

        files.append(
            InventoryFile(
                relative_path=relative_path,
                kind=_classify(relative_path, warnings),
                size_bytes=path.stat().st_size,
                sha256=_hash_file(path),
                detected_encoding=detected_encoding,
            )
        )

    return Inventory(
        run_id=run_id,
        source_package_hash=source_package_hash,
        manifest=manifest,
        files=files,
        warnings=warnings,
    )
