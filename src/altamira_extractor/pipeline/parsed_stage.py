"""Orquestacion de la etapa PARSED: INVENTORIED -> artifacts/02-canonical/.

Procesa, en orden deterministico por `relative_path`, cada
`InventoryFile` con `kind == COBOL` (incluye `.cbl` y `.cob`, sin
reescanear el filesystem ni reclasificar nada: Inventory es la unica
fuente de verdad). No agrega concurrencia, colas ni reintentos
automaticos: es un bucle secuencial simple sobre `ProLeapParserClient`.

Idempotencia: nunca confia unicamente en `RunState`. Para cada programa,
si ya existe un artefacto en `artifacts/02-canonical/` que valida contra
`CanonicalProgram` y coincide en `source_file`/`source_hash`/
`source_package_hash`, se reutiliza sin volver a invocar el JAR; en caso
contrario (ausente, corrupto o inconsistente) se elimina y se reprocesa.
Esto repara automaticamente tanto una etapa `SUCCEEDED` con artefactos
incompletos como una etapa `FAILED` que solo necesita reintentar los
programas pendientes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..contracts.canonical import CanonicalProgram
from ..contracts.enums import InventoryFileKind
from ..contracts.inventory import Inventory
from .parser_client import ParseFailure, ProLeapParserClient


@dataclass(frozen=True)
class ParsedStageOutcome:
    succeeded: bool
    warnings: list[str]
    error: str | None


def _copybook_relative_dirs(inventory: Inventory) -> list[str]:
    """Directorios (relativos, POSIX) con al menos un copybook, unicos y
    ordenados deterministicamente por su propio valor."""
    dirs = {
        PurePosixPath(file.relative_path).parent.as_posix()
        for file in inventory.files
        if file.kind == InventoryFileKind.COPYBOOK
    }
    return sorted(dirs)


def _canonical_output_path(canonical_dir: Path, cobol_relative_path: str) -> Path:
    """`01-codigo/cobol/modulo/PROGRAMA.cbl` ->
    `<canonical_dir>/01-codigo/cobol/modulo/PROGRAMA.cbl.json`.

    Conserva la extension original antes de `.json` (evita colisiones
    entre `.cbl` y `.cob` con el mismo stem) y replica el arbol de
    directorios de origen (evita colisiones entre programas con el mismo
    basename en directorios distintos). Nunca se deriva de PROGRAM-ID.
    """
    return canonical_dir / f"{cobol_relative_path}.json"


def _load_valid_existing_artifact(
    path: Path,
    *,
    expected_source_file: str,
    expected_sha256: str,
    expected_package_hash: str,
) -> CanonicalProgram | None:
    if not path.is_file():
        return None
    try:
        canonical = CanonicalProgram.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    if canonical.source_file != expected_source_file:
        return None
    if canonical.source_hash != expected_sha256:
        return None
    if canonical.source_package_hash != expected_package_hash:
        return None
    return canonical


def run_parsed_stage(
    *,
    run_root: Path,
    extracted_dir: Path,
    canonical_dir: Path,
    inventory: Inventory,
    source_package_hash: str,
    client: ProLeapParserClient,
) -> ParsedStageOutcome:
    """Ejecuta PARSED sobre todos los `InventoryFile` COBOL de `inventory`.

    Puede propagar `ParserUnavailableError` / `ParserContractViolationError`
    (fallos fatales de la etapa completa: el llamador debe abortar, no
    seguir procesando programas). Los fallos recuperables por programa
    (exit 3, timeout, encoding sin resolver) se acumulan en el resultado.
    No verifica la existencia del JAR aqui arriba, de una sola vez: si
    TODOS los artefactos ya son validos y consistentes (cache-hit total),
    no se invoca al parser para ningun programa, y el JAR puede incluso
    no existir sin que la etapa falle (ver `ProLeapParserClient.parse_program`,
    que si lo verifica, pero solo cuando realmente va a invocarlo).
    """
    cobol_files = sorted(
        (file for file in inventory.files if file.kind == InventoryFileKind.COBOL),
        key=lambda file: file.relative_path,
    )
    copybook_dirs = [extracted_dir / rel for rel in _copybook_relative_dirs(inventory)]

    warnings: list[str] = []
    failed_source_files: list[str] = []

    for file in cobol_files:
        input_path = extracted_dir / file.relative_path
        output_final_path = _canonical_output_path(canonical_dir, file.relative_path)

        existing = _load_valid_existing_artifact(
            output_final_path,
            expected_source_file=file.relative_path,
            expected_sha256=file.sha256,
            expected_package_hash=source_package_hash,
        )
        if existing is not None:
            continue

        if output_final_path.exists():
            output_final_path.unlink()

        outcome = client.parse_program(
            input_path=input_path,
            output_final_path=output_final_path,
            run_root=run_root,
            extracted_dir=extracted_dir,
            canonical_dir=canonical_dir,
            source_package_hash=source_package_hash,
            source_file=file.relative_path,
            expected_sha256=file.sha256,
            source_format=inventory.manifest.source.format,
            detected_encoding=file.detected_encoding,
            copybook_dirs=copybook_dirs,
        )

        if isinstance(outcome, ParseFailure):
            warnings.append(outcome.message)
            failed_source_files.append(file.relative_path)

    if failed_source_files:
        error = (
            f"{len(failed_source_files)} programa(s) COBOL fallaron en PARSED: "
            + ", ".join(failed_source_files)
        )
        return ParsedStageOutcome(succeeded=False, warnings=warnings, error=error)

    return ParsedStageOutcome(succeeded=True, warnings=warnings, error=None)
