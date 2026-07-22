"""Contrato tipado de manifest.xml (schemas/manifest.xsd).

Representa el elemento raiz <altamira-package>. No parsea XML aqui;
solo tipa la estructura ya resuelta (esa lectura ocurre en una etapa
posterior del pipeline).
"""

from __future__ import annotations

from datetime import date

from pydantic import Field

from .base import AltamiraBaseModel, RelativePath
from .enums import SourceFormat


class ManifestCountry(AltamiraBaseModel):
    code: str = Field(min_length=1)
    name: str = Field(min_length=1)


class ManifestApplication(AltamiraBaseModel):
    name: str = Field(min_length=1)


class ManifestOperation(AltamiraBaseModel):
    logical_name: str = Field(min_length=1)
    description: str | None = None


class ManifestImplementation(AltamiraBaseModel):
    version: str = Field(min_length=1)
    entry_programs: list[str] = Field(min_length=1)


class ManifestSource(AltamiraBaseModel):
    format: SourceFormat
    encoding: str = Field(min_length=1)


class ManifestParameterTable(AltamiraBaseModel):
    name: str = Field(min_length=1)
    ddl: RelativePath | None = None
    snapshot: RelativePath | None = None
    snapshot_date: date | None = None


class Manifest(AltamiraBaseModel):
    """Jerarquia D1 minima declarada por el paquete (sustituta de la
    capa 3 completa en V1, ver docs/METAMODEL_ALIGNMENT.md)."""

    schema_version: str = Field(min_length=1)
    country: ManifestCountry
    application: ManifestApplication
    operation: ManifestOperation
    implementation: ManifestImplementation
    source: ManifestSource
    parameter_tables: list[ManifestParameterTable] = Field(default_factory=list)
