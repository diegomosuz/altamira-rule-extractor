"""Contrato tipado del artefacto 01-inventory.json."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import AltamiraBaseModel, RelativePath, Sha256Hex
from .enums import InventoryFileKind
from .manifest import Manifest


class InventoryFile(AltamiraBaseModel):
    relative_path: RelativePath
    kind: InventoryFileKind
    size_bytes: int = Field(ge=0)
    sha256: Sha256Hex


class Inventory(AltamiraBaseModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    source_package_hash: Sha256Hex
    manifest: Manifest
    files: list[InventoryFile] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
