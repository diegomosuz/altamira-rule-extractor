"""Contrato tipado del artefacto canonico (02-canonical/), salida del
parser Java. Contiene statements, spans y accesos SQL — nunca copia
al grafo Neo4j 1:1 (CLAUDE.md, seccion Separacion de representaciones).

Ubicacion fuente (source_file/line*) es nullable en DataItem, Paragraph,
Statement y SqlAccess: cuando un elemento proviene de una region del
programa expandida por COPY y ProLeap no permite demostrar de que
archivo fisico vino, no se le atribuye (falsamente) el archivo del
programa principal. `location_kind` distingue:

- EXACT: source_file y linea(s) confiables.
- PREPROCESSED_STREAM: la linea es real dentro del stream ya expandido,
  pero el archivo fisico de origen no puede determinarse; source_file
  debe ser None.
- UNKNOWN: ninguna ubicacion confiable disponible.

CanonicalProgram.source_file (la identidad del programa principal en si)
siempre es conocido y se mantiene obligatorio.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, RelativePath, Sha256Hex
from .enums import BranchKind, LocationKind, SourceFormat, StatementKind, TableAccessOperation


def _ordered_unique(values: Iterable[str]) -> list[str]:
    """Preserva el primer orden de aparicion, sin duplicados."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _check_single_line_location(
    *, source_file: str | None, line: int | None, location_kind: LocationKind
) -> None:
    if location_kind == LocationKind.EXACT:
        if source_file is None or line is None:
            raise ValueError("location_kind=EXACT requiere source_file y line")
    elif location_kind == LocationKind.PREPROCESSED_STREAM:
        if source_file is not None:
            raise ValueError("location_kind=PREPROCESSED_STREAM no puede declarar source_file")
    elif location_kind == LocationKind.UNKNOWN:
        if source_file is not None or line is not None:
            raise ValueError("location_kind=UNKNOWN no puede declarar source_file ni line")


def _check_range_location(
    *,
    source_file: str | None,
    line_start: int | None,
    line_end: int | None,
    location_kind: LocationKind,
) -> None:
    if line_start is not None and line_end is not None and line_end < line_start:
        raise ValueError("line_end no puede ser anterior a line_start")
    if location_kind == LocationKind.EXACT:
        if source_file is None or line_start is None or line_end is None:
            raise ValueError("location_kind=EXACT requiere source_file, line_start y line_end")
    elif location_kind == LocationKind.PREPROCESSED_STREAM:
        if source_file is not None:
            raise ValueError("location_kind=PREPROCESSED_STREAM no puede declarar source_file")
    elif location_kind == LocationKind.UNKNOWN:
        if source_file is not None or line_start is not None or line_end is not None:
            raise ValueError("location_kind=UNKNOWN no puede declarar source_file ni lineas")


class CanonicalDataItem(AltamiraBaseModel):
    name: str = Field(min_length=1)
    qualified_name: str = Field(min_length=1)
    level: int = Field(ge=1, le=88)
    pic: str | None = None
    usage: str | None = None
    source_file: RelativePath | None = None
    line: int | None = Field(default=None, ge=1)
    location_kind: LocationKind

    @model_validator(mode="after")
    def _check_location(self) -> CanonicalDataItem:
        _check_single_line_location(
            source_file=self.source_file, line=self.line, location_kind=self.location_kind
        )
        return self


class CanonicalSqlAccess(AltamiraBaseModel):
    """Acceso SQL conservado como texto/estructura suficiente para derivar
    la relacion directa Paragraph->Table en el grafo semantico."""

    table: str = Field(min_length=1)
    operation: TableAccessOperation
    predicate_text: str | None = None
    host_variables: list[str] = Field(default_factory=list)
    source_file: RelativePath | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    location_kind: LocationKind

    @model_validator(mode="after")
    def _check_location(self) -> CanonicalSqlAccess:
        _check_range_location(
            source_file=self.source_file,
            line_start=self.line_start,
            line_end=self.line_end,
            location_kind=self.location_kind,
        )
        return self


class CanonicalStatement(AltamiraBaseModel):
    """Representacion plana y minima de un statement de Procedure Division.

    No es un AST: IF/EVALUATE anidados y sus ramas se representan como
    statements hermanos adicionales que apuntan a su padre via
    `parent_statement_id` y declaran su `branch_kind` (THEN/ELSE/WHEN/
    WHEN_OTHER), no como una estructura recursiva.

    `statement_id` debe ser deterministico y unico en todo el
    CanonicalProgram (no solo dentro del Paragraph). Formato conceptual
    recomendado (impuesto por el generador Java, no validado aqui via
    regex porque Pydantic no puede verificar determinismo):
    `<program_name>::<paragraph_name>::<traversal_ordinal>::<kind>`.
    """

    statement_id: str = Field(min_length=1)
    kind: StatementKind
    source_text: str
    source_file: RelativePath | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    location_kind: LocationKind
    parent_statement_id: str | None = None
    branch_kind: BranchKind | None = None
    branch_condition: str | None = None
    expression: str | None = None
    normalized_expression: str | None = None
    operands: list[str] = Field(default_factory=list)
    variables_read: list[str] = Field(default_factory=list)
    variables_written: list[str] = Field(default_factory=list)
    target_data_items: list[str] = Field(default_factory=list)
    assigned_literal: str | None = None
    target_paragraphs: list[str] = Field(default_factory=list)
    sql_access: list[CanonicalSqlAccess] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_location(self) -> CanonicalStatement:
        _check_range_location(
            source_file=self.source_file,
            line_start=self.line_start,
            line_end=self.line_end,
            location_kind=self.location_kind,
        )
        return self

    @model_validator(mode="after")
    def _check_branch_requires_parent(self) -> CanonicalStatement:
        if self.branch_kind is not None and self.parent_statement_id is None:
            raise ValueError("branch_kind requiere parent_statement_id")
        return self


class CanonicalParagraph(AltamiraBaseModel):
    name: str = Field(min_length=1)
    source_text: str
    source_file: RelativePath | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    location_kind: LocationKind
    statements: list[CanonicalStatement] = Field(default_factory=list)
    variables_read: list[str] = Field(default_factory=list)
    variables_written: list[str] = Field(default_factory=list)
    sql_access: list[CanonicalSqlAccess] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_location(self) -> CanonicalParagraph:
        _check_range_location(
            source_file=self.source_file,
            line_start=self.line_start,
            line_end=self.line_end,
            location_kind=self.location_kind,
        )
        return self

    @model_validator(mode="after")
    def _check_statement_ids_within_paragraph(self) -> CanonicalParagraph:
        seen: set[str] = set()
        for statement in self.statements:
            if statement.statement_id in seen:
                raise ValueError(
                    f"statement_id duplicado dentro del paragraph {self.name!r}: "
                    f"{statement.statement_id!r}"
                )
            seen.add(statement.statement_id)
        for statement in self.statements:
            if (
                statement.parent_statement_id is not None
                and statement.parent_statement_id not in seen
            ):
                raise ValueError(
                    f"parent_statement_id {statement.parent_statement_id!r} no existe "
                    f"dentro del paragraph {self.name!r}"
                )
        return self

    @model_validator(mode="after")
    def _check_aggregates_match_statements(self) -> CanonicalParagraph:
        expected_read = _ordered_unique(
            variable for statement in self.statements for variable in statement.variables_read
        )
        if self.variables_read != expected_read:
            raise ValueError(
                "variables_read debe ser la union ordenada y sin duplicados de "
                "statements[].variables_read"
            )

        expected_written = _ordered_unique(
            variable for statement in self.statements for variable in statement.variables_written
        )
        if self.variables_written != expected_written:
            raise ValueError(
                "variables_written debe ser la union ordenada y sin duplicados de "
                "statements[].variables_written"
            )

        expected_sql_access = [
            access for statement in self.statements for access in statement.sql_access
        ]
        if self.sql_access != expected_sql_access:
            raise ValueError(
                "sql_access debe ser la agregacion estable de statements[].sql_access"
            )
        return self


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

    @model_validator(mode="after")
    def _check_statement_ids_unique_across_program(self) -> CanonicalProgram:
        owner_by_statement_id: dict[str, str] = {}
        for paragraph in self.paragraphs:
            for statement in paragraph.statements:
                previous_owner = owner_by_statement_id.get(statement.statement_id)
                if previous_owner is not None and previous_owner != paragraph.name:
                    raise ValueError(
                        f"statement_id duplicado entre paragraphs distintos: "
                        f"{statement.statement_id!r} en {previous_owner!r} y {paragraph.name!r}"
                    )
                owner_by_statement_id[statement.statement_id] = paragraph.name
        return self
