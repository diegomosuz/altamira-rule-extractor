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
from .enums import (
    BranchKind,
    CallPassingMode,
    CallTargetKind,
    LocationKind,
    ProgramTerminationKind,
    SourceFormat,
    StatementKind,
    TableAccessOperation,
)


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


class CanonicalConditionValue(AltamiraBaseModel):
    """Un unico VALUE (o intervalo VALUE ... THRU ...) de una condicion
    nivel 88 (Fase 3 de la ampliacion semantica). `through_value` es
    `None` para un VALUE simple; solo se declara cuando ProLeap expone un
    intervalo THRU real -- nunca inferido."""

    value: str = Field(min_length=1)
    through_value: str | None = None
    source_file: RelativePath | None = None
    line: int | None = Field(default=None, ge=1)
    location_kind: LocationKind

    @model_validator(mode="after")
    def _check_location(self) -> CanonicalConditionValue:
        _check_single_line_location(
            source_file=self.source_file, line=self.line, location_kind=self.location_kind
        )
        return self


class CanonicalConditionName(AltamiraBaseModel):
    """Una condicion nivel 88 (condition-name) declarada bajo un data item
    padre (Fase 3 de la ampliacion semantica). `values` nunca esta vacia:
    si el parser no pudo demostrar ningun VALUE, la condicion se omite
    del artefacto y se registra en `unsupported_constructs` en su lugar
    (nunca se inventa un valor). `parent_name`/`parent_qualified_name`
    identifican el data item cuyo VALUE satisface la condicion, resuelto
    estructuralmente por el parser -- nunca por coincidencia de texto."""

    name: str = Field(min_length=1)
    qualified_name: str = Field(min_length=1)
    parent_name: str = Field(min_length=1)
    parent_qualified_name: str = Field(min_length=1)
    values: list[CanonicalConditionValue] = Field(min_length=1)
    source_file: RelativePath | None = None
    line: int | None = Field(default=None, ge=1)
    location_kind: LocationKind

    @model_validator(mode="after")
    def _check_location(self) -> CanonicalConditionName:
        _check_single_line_location(
            source_file=self.source_file, line=self.line, location_kind=self.location_kind
        )
        return self


class CanonicalSqlAccess(AltamiraBaseModel):
    """Acceso SQL conservado como texto/estructura suficiente para derivar
    la relacion directa Paragraph->Table en el grafo semantico.

    `input_host_variables`/`output_host_variables`/`predicate_host_variables`/
    `selected_columns` (Fase 15B3-C3-B): aditivos, retrocompatibles con
    `host_variables` (que se conserva sin cambios, lista plana sin
    direccion). Poblados por `EmbeddedSqlExtractor` UNICAMENTE cuando la
    direccion/columna es estructuralmente segura para la forma simple de
    cada verbo (segmentacion por palabra clave INTO/SET/VALUES/WHERE,
    nunca gramatica SQL) -- vacios en cualquier otro caso, nunca inferidos
    de forma dudosa. `output_host_variables`/`selected_columns` preservan
    orden POSICIONAL exacto (nunca deduplicados: la correspondencia
    columna->host-variable depende de la posicion). `input_host_variables`/
    `predicate_host_variables` son ordenados-sin-duplicados, igual que
    `host_variables`. `has_indicator_variables=True` (variable indicadora
    ":VAR:IND" detectada) fuerza los cuatro campos nuevos a listas vacias
    -- nunca se le asigna direccion a una variable indicadora."""

    table: str = Field(min_length=1)
    operation: TableAccessOperation
    predicate_text: str | None = None
    host_variables: list[str] = Field(default_factory=list)
    input_host_variables: list[str] = Field(default_factory=list)
    output_host_variables: list[str] = Field(default_factory=list)
    predicate_host_variables: list[str] = Field(default_factory=list)
    selected_columns: list[str] = Field(default_factory=list)
    has_indicator_variables: bool = False
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

    @model_validator(mode="after")
    def _check_host_variable_names_not_empty(self) -> CanonicalSqlAccess:
        all_names = (
            *self.host_variables,
            *self.input_host_variables,
            *self.output_host_variables,
            *self.predicate_host_variables,
            *self.selected_columns,
        )
        if any(not name for name in all_names):
            raise ValueError(
                "CanonicalSqlAccess: host_variables/selected_columns nunca admite un "
                "nombre vacio"
            )
        return self

    @model_validator(mode="after")
    def _check_selected_columns_matches_output_arity(self) -> CanonicalSqlAccess:
        if self.selected_columns and len(self.selected_columns) != len(self.output_host_variables):
            raise ValueError(
                "CanonicalSqlAccess: selected_columns solo puede declararse cuando su "
                "longitud coincide exactamente con output_host_variables (correspondencia "
                "posicional demostrada) -- nunca un pairing parcial/dudoso"
            )
        return self

    @model_validator(mode="after")
    def _check_indicator_variables_never_carry_direction(self) -> CanonicalSqlAccess:
        if self.has_indicator_variables and (
            self.input_host_variables
            or self.output_host_variables
            or self.predicate_host_variables
            or self.selected_columns
        ):
            raise ValueError(
                "CanonicalSqlAccess: has_indicator_variables=True nunca puede coexistir con "
                "input_host_variables/output_host_variables/predicate_host_variables/"
                "selected_columns poblados -- una variable indicadora nunca recibe direccion"
            )
        return self


class CanonicalCallArgument(AltamiraBaseModel):
    """Un argumento posicional de `CALL ... USING` (Fase 6, fundacion
    interprocedural). `expression` es SIEMPRE una representacion
    normalizada minima (nombre del data item, texto del literal, o
    `"ADDRESS OF <nombre>"`) -- nunca `source_text` completo. `omitted`
    es `True` unicamente cuando ProLeap demuestra estructuralmente
    `OMITTED` (BY REFERENCE); en ese caso `data_item_name`/
    `qualified_data_item_name`/`literal` deben ser `None`."""

    ordinal: int = Field(ge=1)
    expression: str = Field(min_length=1)
    data_item_name: str | None = None
    qualified_data_item_name: str | None = None
    literal: str | None = None
    passing_mode: CallPassingMode
    omitted: bool = False
    source_file: RelativePath | None = None
    line: int | None = Field(default=None, ge=1)
    location_kind: LocationKind

    @model_validator(mode="after")
    def _check_location(self) -> CanonicalCallArgument:
        _check_single_line_location(
            source_file=self.source_file, line=self.line, location_kind=self.location_kind
        )
        return self

    @model_validator(mode="after")
    def _check_omitted_coherence(self) -> CanonicalCallArgument:
        if self.omitted and (
            self.data_item_name is not None
            or self.qualified_data_item_name is not None
            or self.literal is not None
        ):
            raise ValueError(
                "omitted=True no puede declarar data_item_name/qualified_data_item_name/literal"
            )
        return self

    @model_validator(mode="after")
    def _check_literal_and_data_item_are_mutually_exclusive(self) -> CanonicalCallArgument:
        if self.literal is not None and (
            self.data_item_name is not None or self.qualified_data_item_name is not None
        ):
            raise ValueError(
                "un argumento no puede declarar literal y data_item_name/"
                "qualified_data_item_name simultaneamente"
            )
        return self


class CanonicalEntryParameter(AltamiraBaseModel):
    """Un parametro formal de `PROCEDURE DIVISION USING` (Fase 6). Cuando
    no puede resolverse contra `CanonicalProgram.linkage_data_items`
    (homonimo ambiguo o ausente), `linkage_item_qualified_name` queda
    `None` -- el nombre se conserva igual, nunca se inventa una
    definicion (ver `unsupported_constructs` del programa para el
    diagnostico correspondiente)."""

    ordinal: int = Field(ge=1)
    name: str = Field(min_length=1)
    qualified_name: str | None = None
    linkage_item_qualified_name: str | None = None
    passing_mode: CallPassingMode | None = None
    source_file: RelativePath | None = None
    line: int | None = Field(default=None, ge=1)
    location_kind: LocationKind

    @model_validator(mode="after")
    def _check_location(self) -> CanonicalEntryParameter:
        _check_single_line_location(
            source_file=self.source_file, line=self.line, location_kind=self.location_kind
        )
        return self


class CanonicalLinkageDataItem(AltamiraBaseModel):
    """Un data item de LINKAGE SECTION (Fase 6), modelado por una ruta
    separada de `CanonicalProgram.data_items` (que sigue representando
    exclusivamente WORKING-STORAGE): LINKAGE describe la interfaz
    potencial de un programa, no su almacenamiento propio, y mezclarlos
    alteraria la superficie V1 ya estable (`SemanticGraphBuilder` no
    consume este campo)."""

    name: str = Field(min_length=1)
    qualified_name: str = Field(min_length=1)
    level: int = Field(ge=1, le=88)
    parent_qualified_name: str | None = None
    pic: str | None = None
    usage: str | None = None
    source_file: RelativePath | None = None
    line: int | None = Field(default=None, ge=1)
    location_kind: LocationKind

    @model_validator(mode="after")
    def _check_location(self) -> CanonicalLinkageDataItem:
        _check_single_line_location(
            source_file=self.source_file, line=self.line, location_kind=self.location_kind
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
    condition_name_target: str | None = None
    condition_set_value: bool | None = None
    referenced_condition_names: list[str] = Field(default_factory=list)
    call_target_kind: CallTargetKind | None = None
    called_program_name: str | None = None
    called_program_expression: str | None = None
    call_arguments: list[CanonicalCallArgument] = Field(default_factory=list)
    call_returning_data_item: str | None = None
    call_has_on_exception: bool = False
    call_has_not_on_exception: bool = False
    program_termination_kind: ProgramTerminationKind | None = None

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

    @model_validator(mode="after")
    def _check_program_termination_kind_matches_kind(self) -> CanonicalStatement:
        if self.kind == StatementKind.PROGRAM_TERMINATION:
            if self.program_termination_kind is None:
                raise ValueError(
                    "kind=PROGRAM_TERMINATION requiere program_termination_kind"
                )
        elif self.program_termination_kind is not None:
            raise ValueError(
                "program_termination_kind solo es valido para kind=PROGRAM_TERMINATION"
            )
        return self

    @model_validator(mode="after")
    def _check_condition_set_coherence(self) -> CanonicalStatement:
        has_target = self.condition_name_target is not None
        has_value = self.condition_set_value is not None
        if has_target != has_value:
            raise ValueError(
                "condition_name_target y condition_set_value deben estar ambos presentes o "
                "ambos ausentes (SET condition-name TO TRUE/FALSE resuelto de forma completa "
                "o no resuelto en absoluto)"
            )
        return self

    @model_validator(mode="after")
    def _check_referenced_condition_names_sorted_and_unique(self) -> CanonicalStatement:
        if self.referenced_condition_names != sorted(set(self.referenced_condition_names)):
            raise ValueError(
                "referenced_condition_names debe estar ordenado alfabeticamente y sin duplicados"
            )
        return self

    @model_validator(mode="after")
    def _check_call_fields_only_on_call_statements(self) -> CanonicalStatement:
        """Fase 6 (fundacion interprocedural): los campos estructurados
        de `CALL` nunca contaminan un statement de otro `kind` -- evita
        que un futuro bug de extraccion filtre metadata de llamada hacia
        un MOVE/SET/IF."""
        if self.kind == StatementKind.CALL:
            if self.call_target_kind is None:
                raise ValueError("kind=CALL requiere call_target_kind")
        else:
            if (
                self.call_target_kind is not None
                or self.called_program_name is not None
                or self.called_program_expression is not None
                or self.call_arguments
                or self.call_returning_data_item is not None
                or self.call_has_on_exception
                or self.call_has_not_on_exception
            ):
                raise ValueError(
                    f"kind={self.kind.value} no puede declarar campos estructurados de CALL"
                )
        return self

    @model_validator(mode="after")
    def _check_call_target_kind_coherence(self) -> CanonicalStatement:
        if self.call_target_kind == CallTargetKind.LITERAL:
            if self.called_program_name is None:
                raise ValueError("call_target_kind=LITERAL requiere called_program_name")
            if self.called_program_expression is not None:
                raise ValueError(
                    "call_target_kind=LITERAL no puede declarar called_program_expression"
                )
        elif self.call_target_kind == CallTargetKind.DYNAMIC:
            if self.called_program_expression is None:
                raise ValueError("call_target_kind=DYNAMIC requiere called_program_expression")
            if self.called_program_name is not None:
                raise ValueError("call_target_kind=DYNAMIC no puede declarar called_program_name")
        elif self.call_target_kind == CallTargetKind.UNKNOWN:
            if self.called_program_name is not None or self.called_program_expression is not None:
                raise ValueError(
                    "call_target_kind=UNKNOWN no puede declarar called_program_name ni "
                    "called_program_expression"
                )
        return self

    @model_validator(mode="after")
    def _check_call_arguments_ordinals(self) -> CanonicalStatement:
        ordinals = [argument.ordinal for argument in self.call_arguments]
        if ordinals != list(range(1, len(ordinals) + 1)):
            raise ValueError(
                "call_arguments debe estar ordenado por ordinal consecutivo empezando en 1"
            )
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
    """`schema_version` (Fase 3 de la ampliacion semantica -- soporte
    nivel 88, ver docs/LEVEL_88_SUPPORT.md): `"1.0"` para la forma
    historica (sin ninguna extension de nivel 88 realmente presente);
    `"1.1"` en cuanto el parser Java detecta `condition_names` no vacia o
    algun `CanonicalStatement` con `condition_name_target`/
    `referenced_condition_names` poblados; `"1.2"` en cuanto aparece
    CALL/LINKAGE (Fase 6, ver docs/INTERPROCEDURAL_CALL_LINKAGE.md);
    `"1.3"` en cuanto aparece algun `CanonicalStatement` con
    `kind=PROGRAM_TERMINATION` (GOBACK/STOP RUN/EXIT PROGRAM, Fase 7b,
    ver docs/INTERPROCEDURAL_PROPAGATION.md) -- version tipica de
    cualquier programa COBOL completo real, ya que casi todos terminan
    en GOBACK o STOP RUN. El contrato acepta los cuatro valores en
    lectura; el parser decide cual emitir por programa (nunca este
    modulo, que solo valida)."""

    schema_version: Literal["1.0", "1.1", "1.2", "1.3"] = "1.0"
    program_name: str = Field(min_length=1)
    source_file: RelativePath
    source_hash: Sha256Hex
    source_package_hash: Sha256Hex
    source_format: SourceFormat
    encoding: str = Field(min_length=1)
    data_items: list[CanonicalDataItem] = Field(default_factory=list)
    condition_names: list[CanonicalConditionName] = Field(default_factory=list)
    paragraphs: list[CanonicalParagraph] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    unsupported_constructs: list[str] = Field(default_factory=list)
    linkage_data_items: list[CanonicalLinkageDataItem] = Field(default_factory=list)
    entry_parameters: list[CanonicalEntryParameter] = Field(default_factory=list)
    entry_returning_data_item: str | None = None

    @model_validator(mode="after")
    def _check_condition_names_unique(self) -> CanonicalProgram:
        qualified_names = [condition.qualified_name for condition in self.condition_names]
        if len(qualified_names) != len(set(qualified_names)):
            raise ValueError("condition_names contiene qualified_name duplicado")
        return self

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

    @model_validator(mode="after")
    def _check_linkage_data_items_unique(self) -> CanonicalProgram:
        qualified_names = [item.qualified_name for item in self.linkage_data_items]
        if len(qualified_names) != len(set(qualified_names)):
            raise ValueError("linkage_data_items contiene qualified_name duplicado")
        return self

    @model_validator(mode="after")
    def _check_entry_parameters_ordinals(self) -> CanonicalProgram:
        ordinals = [parameter.ordinal for parameter in self.entry_parameters]
        if ordinals != list(range(1, len(ordinals) + 1)):
            raise ValueError(
                "entry_parameters debe estar ordenado por ordinal consecutivo empezando en 1"
            )
        return self
