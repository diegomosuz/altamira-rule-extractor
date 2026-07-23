"""Tests de InventoryBuilder: clasificacion, hash, tamano y encoding por archivo."""

from __future__ import annotations

import hashlib
from pathlib import Path

from altamira_extractor.config import Settings
from altamira_extractor.contracts.enums import InventoryFileKind, TextEncoding
from altamira_extractor.contracts.inventory import Inventory
from altamira_extractor.pipeline.inventory_builder import build_inventory
from altamira_extractor.pipeline.manifest_loader import parse_and_validate_manifest
from altamira_extractor.pipeline.safe_extractor import extract_package

from .conftest import build_valid_package_zip, default_valid_entries, valid_manifest_xml

_COBOL_PATH = "01-codigo/cobol/ARTRFPROP01.cbl"


def _extract(tmp_path: Path, settings: Settings, *, extra: dict[str, bytes] | None = None) -> Path:
    zip_path = build_valid_package_zip(tmp_path / "package.zip", extra=extra)
    return extract_package(zip_path, tmp_path / "run" / "work", settings)


def _build_inventory_with_cobol_bytes(
    tmp_path: Path, settings: Settings, *, cobol_bytes: bytes, source_encoding: str
) -> tuple[Path, Inventory]:
    manifest_xml = valid_manifest_xml(source_encoding=source_encoding)
    extracted_dir = _extract(
        tmp_path,
        settings,
        extra={"manifest.xml": manifest_xml, _COBOL_PATH: cobol_bytes},
    )
    manifest = parse_and_validate_manifest(manifest_xml, settings.manifest_xsd_path)
    inventory = build_inventory(extracted_dir, "run-1", "a" * 64, manifest)
    return extracted_dir, inventory


def test_classifies_every_extension_by_kind(tmp_path: Path, settings: Settings) -> None:
    extracted_dir = _extract(
        tmp_path,
        settings,
        extra={
            "01-codigo/cobol/OTHER.cob": b"IDENT DIVISION.\n",
            "01-codigo/copybooks/OTHER.copy": b"01 WS.\n",
        },
    )
    manifest = parse_and_validate_manifest(valid_manifest_xml(), settings.manifest_xsd_path)

    inventory = build_inventory(extracted_dir, "run-1", "a" * 64, manifest)

    kinds_by_path = {f.relative_path: f.kind for f in inventory.files}
    assert kinds_by_path["manifest.xml"] == InventoryFileKind.MANIFEST
    assert kinds_by_path["01-codigo/cobol/ARTRFPROP01.cbl"] == InventoryFileKind.COBOL
    assert kinds_by_path["01-codigo/cobol/OTHER.cob"] == InventoryFileKind.COBOL
    assert kinds_by_path["01-codigo/copybooks/CPYTRF01.cpy"] == InventoryFileKind.COPYBOOK
    assert kinds_by_path["01-codigo/copybooks/OTHER.copy"] == InventoryFileKind.COPYBOOK
    assert kinds_by_path["01-codigo/dclgen/DCLTRF01.dcl"] == InventoryFileKind.DCLGEN
    assert kinds_by_path["02-parametria/ddl/PARAM_TRANSFER.sql"] == InventoryFileKind.DDL
    assert (
        kinds_by_path["02-parametria/snapshots/PARAM_TRANSFER_20260515.csv"]
        == InventoryFileKind.SNAPSHOT
    )


def test_txt_file_is_other_with_warning(tmp_path: Path, settings: Settings) -> None:
    extracted_dir = _extract(
        tmp_path, settings, extra={"01-codigo/README.txt": b"notas de la migracion"}
    )
    manifest = parse_and_validate_manifest(valid_manifest_xml(), settings.manifest_xsd_path)

    inventory = build_inventory(extracted_dir, "run-1", "a" * 64, manifest)

    kinds_by_path = {f.relative_path: f.kind for f in inventory.files}
    assert kinds_by_path["01-codigo/README.txt"] == InventoryFileKind.OTHER
    assert any("README.txt" in warning for warning in inventory.warnings)


def test_size_and_hash_are_correct(tmp_path: Path, settings: Settings) -> None:
    extracted_dir = _extract(tmp_path, settings)
    manifest = parse_and_validate_manifest(valid_manifest_xml(), settings.manifest_xsd_path)

    inventory = build_inventory(extracted_dir, "run-1", "a" * 64, manifest)

    by_path = {f.relative_path: f for f in inventory.files}
    expected_bytes = default_valid_entries()["manifest.xml"]
    manifest_entry = by_path["manifest.xml"]
    assert manifest_entry.size_bytes == len(expected_bytes)
    assert manifest_entry.sha256 == hashlib.sha256(expected_bytes).hexdigest()


def test_inventory_embeds_manifest_and_run_metadata(tmp_path: Path, settings: Settings) -> None:
    extracted_dir = _extract(tmp_path, settings)
    manifest = parse_and_validate_manifest(valid_manifest_xml(), settings.manifest_xsd_path)

    inventory = build_inventory(extracted_dir, "run-42", "b" * 64, manifest)

    assert inventory.run_id == "run-42"
    assert inventory.source_package_hash == "b" * 64
    assert inventory.manifest == manifest


def test_detected_encoding_utf8(tmp_path: Path, settings: Settings) -> None:
    _extracted, inventory = _build_inventory_with_cobol_bytes(
        tmp_path,
        settings,
        cobol_bytes="       01 WS-NOMBRE PIC X(20) VALUE 'JOSÉ'.\n".encode(),
        source_encoding="AUTO",
    )

    by_path = {f.relative_path: f for f in inventory.files}
    assert by_path[_COBOL_PATH].detected_encoding == TextEncoding.UTF_8


def test_detected_encoding_windows_1252_declared(tmp_path: Path, settings: Settings) -> None:
    # 0x93/0x94 = comillas curvas cp1252, invalido como UTF-8.
    _extracted, inventory = _build_inventory_with_cobol_bytes(
        tmp_path,
        settings,
        cobol_bytes=b"           DISPLAY \x93HOLA\x94.\n",
        source_encoding="WINDOWS-1252",
    )

    by_path = {f.relative_path: f for f in inventory.files}
    assert by_path[_COBOL_PATH].detected_encoding == TextEncoding.WINDOWS_1252
    assert not inventory.warnings


def test_detected_encoding_iso_8859_1_declared(tmp_path: Path, settings: Settings) -> None:
    _extracted, inventory = _build_inventory_with_cobol_bytes(
        tmp_path,
        settings,
        cobol_bytes="           MOVE 'NÚMERO' TO WS-CAMPO.\n".encode("iso-8859-1"),
        source_encoding="ISO-8859-1",
    )

    by_path = {f.relative_path: f for f in inventory.files}
    assert by_path[_COBOL_PATH].detected_encoding == TextEncoding.ISO_8859_1
    assert not inventory.warnings


def test_detected_encoding_unresolved_declared_incompatible(
    tmp_path: Path, settings: Settings
) -> None:
    # 0x81 es indefinido en cp1252: el archivo no puede decodificarse con el
    # encoding declarado explicitamente por el manifest.
    _extracted, inventory = _build_inventory_with_cobol_bytes(
        tmp_path,
        settings,
        cobol_bytes=b"           MOVE \x81 TO WS-CAMPO.\n",
        source_encoding="WINDOWS-1252",
    )

    by_path = {f.relative_path: f for f in inventory.files}
    assert by_path[_COBOL_PATH].detected_encoding is None
    assert any(
        _COBOL_PATH in warning and "WINDOWS-1252" in warning for warning in inventory.warnings
    )


def test_detected_encoding_auto_ambiguous_stays_unresolved(
    tmp_path: Path, settings: Settings
) -> None:
    # 0xE9 cae en 0xA0-0xFF: identico en cp1252 e ISO-8859-1, sin evidencia
    # para desambiguar bajo AUTO.
    _extracted, inventory = _build_inventory_with_cobol_bytes(
        tmp_path,
        settings,
        cobol_bytes=b"           MOVE \xe9 TO WS-CAMPO.\n",
        source_encoding="AUTO",
    )

    by_path = {f.relative_path: f for f in inventory.files}
    assert by_path[_COBOL_PATH].detected_encoding is None
    assert any(_COBOL_PATH in warning and "AUTO" in warning for warning in inventory.warnings)


def test_cob_extension_is_kind_cobol_and_gets_detected_encoding(
    tmp_path: Path, settings: Settings
) -> None:
    extracted_dir = _extract(
        tmp_path,
        settings,
        extra={"01-codigo/cobol/OTHER.cob": b"       IDENTIFICATION DIVISION.\n"},
    )
    manifest = parse_and_validate_manifest(valid_manifest_xml(), settings.manifest_xsd_path)

    inventory = build_inventory(extracted_dir, "run-1", "a" * 64, manifest)

    by_path = {f.relative_path: f for f in inventory.files}
    other_cob = by_path["01-codigo/cobol/OTHER.cob"]
    assert other_cob.kind == InventoryFileKind.COBOL
    assert other_cob.detected_encoding == TextEncoding.UTF_8


def test_default_fixture_manifest_produces_no_encoding_warnings(
    tmp_path: Path, settings: Settings
) -> None:
    # valid_manifest_xml() por defecto declara UTF-8 (no CP037): todo el
    # contenido de los fixtures normales es ASCII puro, por lo que no debe
    # aparecer ningun warning de encoding.
    extracted_dir = _extract(tmp_path, settings)
    manifest = parse_and_validate_manifest(valid_manifest_xml(), settings.manifest_xsd_path)

    inventory = build_inventory(extracted_dir, "run-1", "a" * 64, manifest)

    assert inventory.warnings == []
    assert all(f.detected_encoding == TextEncoding.UTF_8 for f in inventory.files)


def test_unsupported_declared_encoding_produces_warning_for_every_file(
    tmp_path: Path, settings: Settings
) -> None:
    manifest_xml = valid_manifest_xml(source_encoding="CP037")
    extracted_dir = _extract(tmp_path, settings, extra={"manifest.xml": manifest_xml})
    manifest = parse_and_validate_manifest(manifest_xml, settings.manifest_xsd_path)

    inventory = build_inventory(extracted_dir, "run-1", "a" * 64, manifest)

    assert inventory.warnings
    assert all(f.detected_encoding is None for f in inventory.files)
    assert any("CP037" in warning for warning in inventory.warnings)
