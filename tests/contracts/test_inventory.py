"""Tests del contrato Inventory/InventoryFile, en particular detected_encoding."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts import Inventory, InventoryFile, InventoryFileKind, TextEncoding
from altamira_extractor.contracts.manifest import Manifest

VALID_HASH = "a" * 64


def _inventory_file(**overrides: object) -> InventoryFile:
    defaults: dict[str, object] = {
        "relative_path": "01-codigo/cobol/PROG.cbl",
        "kind": InventoryFileKind.COBOL,
        "size_bytes": 10,
        "sha256": VALID_HASH,
    }
    defaults.update(overrides)
    return InventoryFile.model_validate(defaults)


def test_detected_encoding_defaults_to_none() -> None:
    file = _inventory_file()
    assert file.detected_encoding is None


def test_detected_encoding_accepts_canonical_values() -> None:
    for value in (TextEncoding.UTF_8, TextEncoding.WINDOWS_1252, TextEncoding.ISO_8859_1):
        file = _inventory_file(detected_encoding=value)
        assert file.detected_encoding == value


def test_detected_encoding_rejects_arbitrary_values() -> None:
    with pytest.raises(ValidationError):
        _inventory_file(detected_encoding="CP037")


def test_inventory_round_trips_with_detected_encoding(valid_manifest: Manifest) -> None:
    inventory = Inventory(
        run_id="run-1",
        source_package_hash=VALID_HASH,
        manifest=valid_manifest,
        files=[
            _inventory_file(detected_encoding=TextEncoding.WINDOWS_1252),
            _inventory_file(
                relative_path="01-codigo/cobol/OTHER.cbl",
                detected_encoding=None,
            ),
        ],
        warnings=["ejemplo de warning"],
    )

    dumped = inventory.to_stable_json()
    restored = Inventory.model_validate_json(dumped)

    assert restored == inventory
    assert restored.files[0].detected_encoding == TextEncoding.WINDOWS_1252
    assert restored.files[1].detected_encoding is None


def test_legacy_inventory_json_without_detected_encoding_still_parses(
    valid_manifest: Manifest,
) -> None:
    """Un 01-inventory.json escrito antes de agregar detected_encoding debe
    seguir siendo legible: el campo ausente se resuelve al default None."""
    inventory_with_field = Inventory(
        run_id="run-1",
        source_package_hash=VALID_HASH,
        manifest=valid_manifest,
        files=[_inventory_file()],
        warnings=[],
    )
    payload = inventory_with_field.model_dump(mode="json")
    del payload["files"][0]["detected_encoding"]

    restored = Inventory.model_validate(payload)

    assert restored.files[0].detected_encoding is None
