"""Construccion del ZIP de descarga (Prompt 13b).

Sirve EXCLUSIVAMENTE `artifacts/10-rules/`: `rules-manifest.json` y cada
`.md` declarado por `RulesDirectoryManifest`. Nunca `run.json`,
`package.zip`, `work/extracted`, artefactos intermedios (canonical,
dependencies, semantic-graph, candidates, context, drafts) ni
`09-guardrails`. Valida estrictamente el directorio ANTES de armar el
ZIP: cualquier archivo adicional, subdirectorio, symlink o hash
incorrecto es `ArtifactCorruptedError` (500) -- una salida marcada
COMPLETED con ese tipo de desviacion indica corrupcion o manipulacion,
nunca se excluye en silencio."""

from __future__ import annotations

import hashlib
import os
import tempfile
import zipfile
from pathlib import Path

from ..contracts.rules_manifest import RulesDirectoryManifest
from .errors import ArtifactCorruptedError

_RULES_MANIFEST_FILENAME = "rules-manifest.json"


def _scan_directory_strict(dir_path: Path) -> set[str]:
    names: set[str] = set()
    for entry in dir_path.iterdir():
        if entry.is_symlink():
            raise ArtifactCorruptedError(
                f"artifacts/10-rules/ contiene un symlink no permitido ({entry.name!r})"
            )
        if entry.is_dir():
            raise ArtifactCorruptedError(
                f"artifacts/10-rules/ contiene un subdirectorio no permitido ({entry.name!r})"
            )
        if not entry.is_file():
            raise ArtifactCorruptedError(
                f"artifacts/10-rules/ contiene una entrada que no es un archivo regular "
                f"({entry.name!r})"
            )
        names.add(entry.name)
    return names


def build_rules_zip(run_dir: Path, run_id: str) -> Path:
    """Construye el ZIP en un archivo temporal FUERA de
    `artifacts/10-rules/` (directorio temporal del sistema) sin cargar
    el contenido completo en memoria (`zipfile.ZipFile.write` transmite
    por chunks internamente). El llamador es responsable de borrar el
    archivo devuelto (via `BackgroundTask`) despues de enviar la
    respuesta."""
    rules_dir = run_dir / "artifacts" / "10-rules"
    manifest_path = rules_dir / _RULES_MANIFEST_FILENAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ArtifactCorruptedError(f"{_RULES_MANIFEST_FILENAME} ausente o invalido")
    try:
        manifest = RulesDirectoryManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
    except ValueError as exc:
        raise ArtifactCorruptedError(f"{_RULES_MANIFEST_FILENAME} invalido") from exc

    for record in manifest.records:
        if "/" in record.relative_filename or "\\" in record.relative_filename:
            raise ArtifactCorruptedError(
                f"relative_filename de {record.candidate_id!r} no es hijo directo de "
                "artifacts/10-rules/"
            )

    expected_filenames = {record.relative_filename for record in manifest.records}
    actual_filenames = _scan_directory_strict(rules_dir)
    if actual_filenames != expected_filenames | {_RULES_MANIFEST_FILENAME}:
        raise ArtifactCorruptedError(
            "artifacts/10-rules/ no coincide exactamente con rules-manifest.json "
            "(archivo adicional o faltante)"
        )

    for record in manifest.records:
        markdown_path = rules_dir / record.relative_filename
        actual_hash = hashlib.sha256(markdown_path.read_bytes()).hexdigest()
        if actual_hash != record.markdown_hash:
            raise ArtifactCorruptedError(f"markdown_hash de {record.candidate_id!r} no coincide")

    fd, tmp_name = tempfile.mkstemp(prefix=f".{run_id}-rules-", suffix=".zip")
    os.close(fd)
    tmp_path = Path(tmp_name)
    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(manifest_path, arcname=_RULES_MANIFEST_FILENAME)
        for record in manifest.records:
            archive.write(rules_dir / record.relative_filename, arcname=record.relative_filename)

    return tmp_path
