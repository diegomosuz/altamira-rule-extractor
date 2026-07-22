"""Tests de ManifestLoader: XML seguro, XSD, mapeo y cruce contra el ZIP."""

from __future__ import annotations

from pathlib import Path

import pytest

from altamira_extractor.config import load_settings
from altamira_extractor.contracts.enums import SourceFormat
from altamira_extractor.pipeline.errors import ManifestValidationError
from altamira_extractor.pipeline.manifest_loader import (
    parse_and_validate_manifest,
    validate_manifest_paths_against_zip,
)

from .conftest import DEFAULT_MANIFEST_DDL, DEFAULT_MANIFEST_SNAPSHOT, valid_manifest_xml

XSD_PATH = load_settings().manifest_xsd_path


def test_parses_valid_manifest() -> None:
    manifest = parse_and_validate_manifest(valid_manifest_xml(), XSD_PATH)
    assert manifest.country.code == "AR"
    assert manifest.source.format == SourceFormat.FIXED
    assert manifest.implementation.entry_programs == ["ARTRFPROP01"]


def test_rejects_doctype_declaration() -> None:
    malicious = b"""<?xml version="1.0"?>
<!DOCTYPE altamira-package [
  <!ENTITY xxe SYSTEM "file:///etc/passwd">
]>
<altamira-package schema-version="1.0">
  <country code="AR" name="Argentina"/>
  <application name="Transferencias"/>
  <operation logical-name="OP-TRF-PROPIA"/>
  <implementation version="3.2">
    <entry-program>ARTRFPROP01</entry-program>
  </implementation>
  <source format="FIXED" encoding="CP037"/>
</altamira-package>
"""
    with pytest.raises(ManifestValidationError):
        parse_and_validate_manifest(malicious, XSD_PATH)


def test_rejects_xml_missing_required_attribute() -> None:
    invalid = b"""<?xml version="1.0"?>
<altamira-package schema-version="1.0">
  <country name="Argentina"/>
  <application name="Transferencias"/>
  <operation logical-name="OP-TRF-PROPIA"/>
  <implementation version="3.2">
    <entry-program>ARTRFPROP01</entry-program>
  </implementation>
  <source format="FIXED" encoding="CP037"/>
</altamira-package>
"""
    with pytest.raises(ManifestValidationError):
        parse_and_validate_manifest(invalid, XSD_PATH)


def test_rejects_invalid_source_format_enum() -> None:
    invalid = b"""<?xml version="1.0"?>
<altamira-package schema-version="1.0">
  <country code="AR" name="Argentina"/>
  <application name="Transferencias"/>
  <operation logical-name="OP-TRF-PROPIA"/>
  <implementation version="3.2">
    <entry-program>ARTRFPROP01</entry-program>
  </implementation>
  <source format="EBCDIC" encoding="CP037"/>
</altamira-package>
"""
    with pytest.raises(ManifestValidationError):
        parse_and_validate_manifest(invalid, XSD_PATH)


def test_rejects_malformed_xml() -> None:
    with pytest.raises(ManifestValidationError):
        parse_and_validate_manifest(b"<not-even-xml", XSD_PATH)


def test_missing_xsd_file_raises_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ManifestValidationError, match="XSD"):
        parse_and_validate_manifest(valid_manifest_xml(), tmp_path / "does-not-exist.xsd")


def test_validate_manifest_paths_accepts_existing_entries() -> None:
    manifest = parse_and_validate_manifest(valid_manifest_xml(), XSD_PATH)
    zip_entries = frozenset({DEFAULT_MANIFEST_DDL, DEFAULT_MANIFEST_SNAPSHOT, "manifest.xml"})
    validate_manifest_paths_against_zip(manifest, zip_entries)  # no debe lanzar


def test_validate_manifest_paths_rejects_missing_ddl() -> None:
    manifest = parse_and_validate_manifest(valid_manifest_xml(), XSD_PATH)
    zip_entries = frozenset({DEFAULT_MANIFEST_SNAPSHOT, "manifest.xml"})
    with pytest.raises(ManifestValidationError, match="no existe"):
        validate_manifest_paths_against_zip(manifest, zip_entries)


def test_validate_manifest_paths_rejects_wrong_snapshot_extension() -> None:
    manifest = parse_and_validate_manifest(
        valid_manifest_xml(snapshot_path="02-parametria/snapshots/PARAM_TRANSFER.txt"),
        XSD_PATH,
    )
    zip_entries = frozenset(
        {DEFAULT_MANIFEST_DDL, "02-parametria/snapshots/PARAM_TRANSFER.txt", "manifest.xml"}
    )
    with pytest.raises(ManifestValidationError, match="extension"):
        validate_manifest_paths_against_zip(manifest, zip_entries)
