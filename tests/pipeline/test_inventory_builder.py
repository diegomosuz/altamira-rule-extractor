"""Tests de InventoryBuilder: clasificacion, hash y tamano por archivo."""

from __future__ import annotations

import hashlib
from pathlib import Path

from altamira_extractor.config import Settings
from altamira_extractor.contracts.enums import InventoryFileKind
from altamira_extractor.pipeline.inventory_builder import build_inventory
from altamira_extractor.pipeline.manifest_loader import parse_and_validate_manifest
from altamira_extractor.pipeline.safe_extractor import extract_package

from .conftest import build_valid_package_zip, default_valid_entries, valid_manifest_xml


def _extract(tmp_path: Path, settings: Settings, *, extra: dict[str, bytes] | None = None) -> Path:
    zip_path = build_valid_package_zip(tmp_path / "package.zip", extra=extra)
    return extract_package(zip_path, tmp_path / "run" / "work", settings)


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
