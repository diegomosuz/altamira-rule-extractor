"""Contrato tipado del artefacto canonico (02-canonical/), salida del
parser Java. Contiene statements, spans y accesos SQL — nunca copia
al grafo Neo4j 1:1 (CLAUDE.md, seccion Separacion de representaciones)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import AltamiraBaseModel, RelativePath, Sha256Hex
from .enums import SourceFormat, TableAccessOperation


class CanonicalDataItem(AltamiraBaseModel):
    name: str = Field(min_length=1)
    qualified_name: str = Field(min_length=1)
    level: int = Field(ge=1, le=88)
    pic: str | None = None
    usage: str | None = None
    source_file: RelativePath
    line: int = Field(ge=1)


class CanonicalSqlAccess(AltamiraBaseModel):
    """Acceso SQL conservado como texto/estructura suficiente para derivar
    la relacion directa Paragraph->Table en el grafo semantico."""

    table: str = Field(min_length=1)
    operation: TableAccessOperation
    predicate_text: str | None = None
    host_variables: list[str] = Field(default_factory=list)
    source_file: RelativePath
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)


class CanonicalParagraph(AltamiraBaseModel):
    name: str = Field(min_length=1)
    source_text: str
    source_file: RelativePath
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    sql_access: list[CanonicalSqlAccess] = Field(default_factory=list)


class CanonicalProgram(AltamiraBaseModel):
    schema_version: Literal["1.0"] = "1.0"
    program_name: str = Field(min_length=1)
    source_file: RelativePath
    source_hash: Sha256Hex
    source_package_hash: Sha256Hex
    source_format: SourceFormat
    encoding: str = Field(min_length=1)
    data_items: list[CanonicalDataItem] = Field(default_factory=list)
    paragraphs: list[CanonicalParagraph] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    unsupported_constructs: list[str] = Field(default_factory=list)
