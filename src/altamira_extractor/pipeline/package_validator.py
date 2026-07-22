"""PackageValidator: valida un paquete ZIP Altamira completo antes de extraer.

Orden obligatorio (no se reordena sin razon documentada):

1. tamano del archivo ZIP;
2. apertura e inspeccion de todos los ZipInfo (sin descomprimir);
3. paths;
4. colisiones;
5. tipos de entrada;
6. cifrado;
7. extensiones;
8. cantidad de entradas;
9. tamanos declarados;
10. ratios de compresion declarados;
11. recien aqui, integridad CRC (`testzip`);
12. manifest.xml: XSD + contrato Pydantic + cruce contra el listado real del ZIP;
13. estructura minima del paquete.

Ninguna entrada se descomprime en esta etapa: todo se resuelve con el
listado del directorio central (`infolist`) y, para manifest.xml
unicamente, una lectura acotada por su propio limite de tamano.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings
from ..contracts.manifest import Manifest
from .errors import PackageValidationError, ZipSecurityError
from .manifest_loader import parse_and_validate_manifest, validate_manifest_paths_against_zip
from .zip_entries import classify_entry_type, extension_of, is_encrypted
from .zip_paths import collision_key, normalize_zip_entry_path

MANIFEST_ENTRY_NAME = "manifest.xml"
COBOL_STRUCTURE_PREFIX = "01-codigo/"
COBOL_EXTENSIONS = frozenset({".cbl", ".cob"})
PARAMETRIA_STRUCTURE_PREFIX = "02-parametria/"
DDL_STRUCTURE_PREFIX = "02-parametria/ddl/"
SNAPSHOT_STRUCTURE_PREFIX = "02-parametria/snapshots/"
DDL_EXTENSIONS = frozenset({".sql", ".ddl"})
SNAPSHOT_EXTENSIONS = frozenset({".csv"})


@dataclass(frozen=True)
class ValidatedPackage:
    """Resultado de una validacion exitosa: listo para extraer."""

    manifest: Manifest
    normalized_file_entries: frozenset[str]
    entry_count: int
    total_uncompressed_bytes: int


def _reject(reason: str, normalized: str) -> ZipSecurityError:
    return ZipSecurityError(f"{reason}: {normalized!r}")


def _validate_paths(infos: list[zipfile.ZipInfo]) -> list[tuple[zipfile.ZipInfo, str]]:
    return [(info, normalize_zip_entry_path(info.filename)) for info in infos]


def _validate_collisions(entries: list[tuple[zipfile.ZipInfo, str]]) -> None:
    seen: dict[str, str] = {}
    for _info, normalized in entries:
        key = collision_key(normalized)
        if key in seen:
            raise _reject(
                f"colision de nombres (insensible a mayusculas/Unicode) con {seen[key]!r}",
                normalized,
            )
        seen[key] = normalized


def _validate_types(
    entries: list[tuple[zipfile.ZipInfo, str]],
) -> tuple[list[tuple[zipfile.ZipInfo, str]], frozenset[str]]:
    file_entries: list[tuple[zipfile.ZipInfo, str]] = []
    dir_paths: set[str] = set()
    for info, normalized in entries:
        if classify_entry_type(info, normalized) == "dir":
            dir_paths.add(normalized)
        else:
            file_entries.append((info, normalized))
    return file_entries, frozenset(dir_paths)


def _validate_encryption(file_entries: list[tuple[zipfile.ZipInfo, str]]) -> None:
    for info, normalized in file_entries:
        if is_encrypted(info):
            raise _reject("entrada cifrada no permitida", normalized)


def _validate_extensions(
    file_entries: list[tuple[zipfile.ZipInfo, str]], allowed_extensions: frozenset[str]
) -> None:
    for _info, normalized in file_entries:
        extension = extension_of(normalized)
        if extension not in allowed_extensions:
            raise _reject(f"extension no permitida {extension!r}", normalized)


def _validate_count(total_entries: int, settings: Settings) -> None:
    if total_entries > settings.max_entry_count:
        raise PackageValidationError(
            f"el paquete tiene {total_entries} entradas, supera el limite "
            f"{settings.max_entry_count}"
        )


def _validate_declared_sizes(
    file_entries: list[tuple[zipfile.ZipInfo, str]], settings: Settings
) -> int:
    total = 0
    for info, normalized in file_entries:
        if info.file_size > settings.max_single_entry_uncompressed_bytes:
            raise PackageValidationError(
                f"entrada {normalized!r} declara {info.file_size} bytes sin comprimir, "
                f"supera el limite {settings.max_single_entry_uncompressed_bytes}"
            )
        total += info.file_size
        if total > settings.max_total_uncompressed_bytes:
            raise PackageValidationError(
                f"el tamano total sin comprimir declarado supera el limite "
                f"{settings.max_total_uncompressed_bytes}"
            )
    return total


def _validate_ratios(
    file_entries: list[tuple[zipfile.ZipInfo, str]], settings: Settings
) -> None:
    for info, normalized in file_entries:
        if info.compress_size == 0:
            if info.file_size > 0:
                raise PackageValidationError(
                    f"entrada {normalized!r} declara tamano comprimido 0 pero tamano "
                    f"sin comprimir {info.file_size}: metadata inconsistente"
                )
            continue
        ratio = info.file_size / info.compress_size
        if ratio > settings.max_compression_ratio:
            raise PackageValidationError(
                f"entrada {normalized!r} tiene ratio de compresion {ratio:.1f}, "
                f"supera el limite {settings.max_compression_ratio}"
            )


def _validate_minimum_structure(normalized_file_paths: frozenset[str]) -> None:
    if MANIFEST_ENTRY_NAME not in normalized_file_paths:
        raise PackageValidationError(f"falta {MANIFEST_ENTRY_NAME!r} en la raiz del paquete")

    has_cobol = any(
        path.startswith(COBOL_STRUCTURE_PREFIX) and extension_of(path) in COBOL_EXTENSIONS
        for path in normalized_file_paths
    )
    if not has_cobol:
        raise PackageValidationError(
            f"el paquete no contiene ningun programa COBOL (.cbl/.cob) bajo "
            f"{COBOL_STRUCTURE_PREFIX!r}"
        )

    has_parametria = any(
        (path.startswith(DDL_STRUCTURE_PREFIX) and extension_of(path) in DDL_EXTENSIONS)
        or (
            path.startswith(SNAPSHOT_STRUCTURE_PREFIX)
            and extension_of(path) in SNAPSHOT_EXTENSIONS
        )
        for path in normalized_file_paths
    )
    if not has_parametria:
        raise PackageValidationError(
            f"el paquete no contiene ningun DDL o snapshot de parametria bajo "
            f"{PARAMETRIA_STRUCTURE_PREFIX!r}"
        )


def validate_package(zip_path: Path, settings: Settings) -> ValidatedPackage:
    """Ejecuta todas las validaciones del ZIP, en orden, sin extraer nada."""
    package_size = zip_path.stat().st_size
    if package_size > settings.max_package_size_bytes:
        raise PackageValidationError(
            f"el paquete pesa {package_size} bytes, supera el limite "
            f"{settings.max_package_size_bytes}"
        )
    if package_size == 0:
        raise PackageValidationError("el paquete esta vacio")

    if not zipfile.is_zipfile(zip_path):
        raise PackageValidationError("el archivo no es un ZIP valido")

    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        if not infos:
            raise PackageValidationError("el ZIP no contiene entradas")

        all_entries = _validate_paths(infos)
        _validate_collisions(all_entries)
        file_entries, _dir_paths = _validate_types(all_entries)
        _validate_encryption(file_entries)
        _validate_extensions(file_entries, settings.allowed_extensions)
        _validate_count(len(all_entries), settings)
        total_uncompressed = _validate_declared_sizes(file_entries, settings)
        _validate_ratios(file_entries, settings)

        bad_entry = zf.testzip()
        if bad_entry is not None:
            raise PackageValidationError(f"entrada corrupta (CRC invalido): {bad_entry!r}")

        normalized_file_paths = frozenset(normalized for _info, normalized in file_entries)
        _validate_minimum_structure(normalized_file_paths)

        manifest_bytes = zf.read(MANIFEST_ENTRY_NAME)

    manifest = parse_and_validate_manifest(manifest_bytes, settings.manifest_xsd_path)
    validate_manifest_paths_against_zip(manifest, normalized_file_paths)

    return ValidatedPackage(
        manifest=manifest,
        normalized_file_entries=normalized_file_paths,
        entry_count=len(all_entries),
        total_uncompressed_bytes=total_uncompressed,
    )
