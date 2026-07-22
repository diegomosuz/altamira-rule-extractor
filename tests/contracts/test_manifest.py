"""Tests de Manifest contra el contrato de schemas/manifest.xsd."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts import (
    Manifest,
    ManifestApplication,
    ManifestCountry,
    ManifestImplementation,
    ManifestOperation,
    ManifestParameterTable,
    ManifestSource,
    SourceFormat,
)


def test_valid_manifest_round_trips(valid_manifest: Manifest) -> None:
    dumped = valid_manifest.to_stable_json()
    restored = Manifest.model_validate_json(dumped)
    assert restored == valid_manifest


def test_manifest_requires_country() -> None:
    with pytest.raises(ValidationError):
        Manifest.model_validate(
            {
                "schema_version": "1.0",
                "application": {"name": "Transferencias"},
                "operation": {"logical_name": "OP-TRF-PROPIA", "description": None},
                "implementation": {"version": "3.2", "entry_programs": ["ARTRFPROP01"]},
                "source": {"format": "FIXED", "encoding": "CP037"},
            }
        )


def test_manifest_rejects_additional_properties(valid_manifest: Manifest) -> None:
    payload = valid_manifest.model_dump(mode="json")
    payload["unexpected_field"] = "not allowed"
    with pytest.raises(ValidationError):
        Manifest.model_validate(payload)


def test_manifest_rejects_invalid_source_format(valid_manifest: Manifest) -> None:
    payload = valid_manifest.model_dump(mode="json")
    payload["source"]["format"] = "EBCDIC"
    with pytest.raises(ValidationError):
        Manifest.model_validate(payload)


def test_manifest_implementation_requires_at_least_one_entry_program() -> None:
    with pytest.raises(ValidationError):
        ManifestImplementation(version="3.2", entry_programs=[])


def test_manifest_parameter_table_rejects_absolute_ddl_path() -> None:
    with pytest.raises(ValidationError):
        ManifestParameterTable(name="PARM01", ddl="/etc/PARM01.sql")


def test_manifest_accepts_multiple_entry_programs() -> None:
    manifest = Manifest(
        schema_version="1.0",
        country=ManifestCountry(code="AR", name="Argentina"),
        application=ManifestApplication(name="Transferencias"),
        operation=ManifestOperation(logical_name="OP-TRF-PROPIA", description=None),
        implementation=ManifestImplementation(version="3.2", entry_programs=["PROG1", "PROG2"]),
        source=ManifestSource(format=SourceFormat.AUTO, encoding="AUTO"),
    )
    assert manifest.implementation.entry_programs == ["PROG1", "PROG2"]
