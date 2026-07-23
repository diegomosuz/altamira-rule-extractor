"""Subconjunto acotado de DDL DB2 (`CREATE TABLE`) — no un parser SQL
general. Reconoce exactamente:

    CREATE TABLE [schema.]nombre ( col_def, col_def, ..., [constraint] );

por columna: nombre (entrecomillado o no), tipo declarado (con
precision/escala como texto crudo), `NOT NULL` y `PRIMARY KEY` (inline o
como constraint de tabla). Cualquier otra construccion (`ALTER TABLE`,
`CREATE VIEW`, procedimientos, triggers, backticks, corchetes de SQL
Server, `FOREIGN KEY`/`CHECK`/`UNIQUE`/`CONSTRAINT`/`DEFAULT`) no se
interpreta: si aparece dentro de una tabla por lo demas reconocible, esa
tabla queda `PARTIAL` (se conservan las columnas que si se pudieron
leer); si el `CREATE TABLE` en si no tiene la forma esperada, o la tabla
declarada por Manifest no se puede resolver de forma unica, queda
`UNSUPPORTED`.

El primer paso (`_strip_comments_and_split_statements`) es un scanner de
caracter por caracter consciente de comillas simples (literales) y
dobles (identificadores): nunca se eliminan comentarios con una
sustitucion regex ciega que podria borrar texto real dentro de un
literal o de un identificador entrecomillado.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..contracts.enums import ParseSupportStatus
from ..contracts.semantic_enrichment import ParameterColumnDefinition

_CREATE_TABLE_RE = re.compile(r"^\s*CREATE\s+TABLE\s+", re.IGNORECASE)
_NOT_NULL_RE = re.compile(r"\bNOT\s+NULL\b", re.IGNORECASE)
_NULL_RE = re.compile(r"\bNULL\b", re.IGNORECASE)
_PRIMARY_KEY_RE = re.compile(r"\bPRIMARY\s+KEY\b", re.IGNORECASE)
_DEFAULT_RE = re.compile(r"\bDEFAULT\b", re.IGNORECASE)
_TABLE_LEVEL_PK_RE = re.compile(r"^\s*PRIMARY\s+KEY\s*\((?P<cols>.*)\)\s*$", re.IGNORECASE)
_TABLE_CONSTRAINT_START_RE = re.compile(
    r"^\s*(PRIMARY\s+KEY|FOREIGN\s+KEY|CHECK|UNIQUE|CONSTRAINT)\b", re.IGNORECASE
)


def normalize_ddl_identifier(name: str) -> str:
    return name.strip().upper()


@dataclass(frozen=True)
class ResolvedCreateTable:
    """Una sentencia `CREATE TABLE` ya localizada dentro del DDL."""

    schema_name: str | None
    table_name: str
    body: str  # contenido entre los parentesis exteriores, sin parsear aun


@dataclass(frozen=True)
class ParsedDdlTable:
    columns: list[ParameterColumnDefinition]
    support_status: ParseSupportStatus


def _strip_comments_and_split_statements(ddl_text: str) -> list[str]:
    """Elimina comentarios `--` y `/* */` respetando literales `'...'` e
    identificadores `"..."`, y separa por `;` de nivel superior."""
    statements: list[str] = []
    buffer: list[str] = []
    i = 0
    n = len(ddl_text)
    in_single_quote = False
    in_double_quote = False

    while i < n:
        char = ddl_text[i]

        if in_single_quote:
            if char == "'":
                if i + 1 < n and ddl_text[i + 1] == "'":
                    buffer.append("''")
                    i += 2
                    continue
                in_single_quote = False
            buffer.append(char)
            i += 1
            continue

        if in_double_quote:
            if char == '"':
                if i + 1 < n and ddl_text[i + 1] == '"':
                    buffer.append('""')
                    i += 2
                    continue
                in_double_quote = False
            buffer.append(char)
            i += 1
            continue

        if char == "'":
            in_single_quote = True
            buffer.append(char)
            i += 1
            continue
        if char == '"':
            in_double_quote = True
            buffer.append(char)
            i += 1
            continue
        if ddl_text[i : i + 2] == "--":
            newline_index = ddl_text.find("\n", i)
            i = n if newline_index == -1 else newline_index
            continue
        if ddl_text[i : i + 2] == "/*":
            end_index = ddl_text.find("*/", i + 2)
            buffer.append(" ")
            i = n if end_index == -1 else end_index + 2
            continue
        if char == ";":
            statements.append("".join(buffer))
            buffer = []
            i += 1
            continue

        buffer.append(char)
        i += 1

    remainder = "".join(buffer).strip()
    if remainder:
        statements.append(remainder)

    return statements


def _read_one_identifier(value: str) -> tuple[str, str]:
    """Lee un identificador (entrecomillado o no) desde el inicio de
    `value` (ya sin espacios a la izquierda). Devuelve `(identificador,
    resto_sin_consumir)`."""
    value = value.lstrip()
    if value.startswith('"'):
        end = 1
        chars: list[str] = []
        while end < len(value):
            if value[end] == '"':
                if end + 1 < len(value) and value[end + 1] == '"':
                    chars.append('"')
                    end += 2
                    continue
                end += 1
                break
            chars.append(value[end])
            end += 1
        return "".join(chars), value[end:]
    match = re.match(r"[A-Za-z0-9_]+", value)
    if not match:
        return "", value
    return match.group(0), value[match.end() :]


def _parse_qualified_identifier(text: str) -> tuple[str | None, str, str]:
    """Extrae `(schema_or_None, identificador, resto_sin_consumir)` desde
    el inicio de `text`. Soporta identificadores entre comillas dobles y
    la forma `schema.tabla`."""
    remaining = text.lstrip()
    first, rest = _read_one_identifier(remaining)
    rest_stripped = rest.lstrip()
    if rest_stripped.startswith("."):
        second, rest2 = _read_one_identifier(rest_stripped[1:])
        return first, second, rest2
    return None, first, rest


def find_create_table_statements(ddl_text: str) -> list[ResolvedCreateTable]:
    """Localiza todas las sentencias `CREATE TABLE` reconocibles del DDL
    (puede haber mas de una). Sentencias que no matchean la forma
    esperada (`ALTER TABLE`, `CREATE VIEW`, etc.) se ignoran en silencio:
    no cuentan como error, simplemente no son candidatas."""
    tables: list[ResolvedCreateTable] = []
    for statement in _strip_comments_and_split_statements(ddl_text):
        if not _CREATE_TABLE_RE.match(statement):
            continue
        after_keywords = _CREATE_TABLE_RE.sub("", statement, count=1)
        schema_name, table_name, rest = _parse_qualified_identifier(after_keywords)
        if not table_name:
            continue
        paren_start = rest.find("(")
        if paren_start == -1:
            continue
        depth = 0
        body: str | None = None
        for idx in range(paren_start, len(rest)):
            if rest[idx] == "(":
                depth += 1
            elif rest[idx] == ")":
                depth -= 1
                if depth == 0:
                    body = rest[paren_start + 1 : idx]
                    break
        if body is None:
            continue
        tables.append(
            ResolvedCreateTable(schema_name=schema_name, table_name=table_name, body=body)
        )
    return tables


def resolve_declared_table(
    candidates: list[ResolvedCreateTable], *, declared_name: str
) -> tuple[ResolvedCreateTable | None, str | None]:
    """Resuelve cual `CREATE TABLE` corresponde a `declared_name` (el
    nombre que Manifest.parameter_tables declara). Devuelve
    `(tabla_resuelta, warning)`; `warning` es `None` solo si se resolvio
    de forma unica. Nunca elige arbitrariamente la primera sentencia."""
    normalized_declared = normalize_ddl_identifier(declared_name)
    declared_is_qualified = "." in normalized_declared

    def qualified_form(candidate: ResolvedCreateTable) -> str:
        table = normalize_ddl_identifier(candidate.table_name)
        if candidate.schema_name:
            return f"{normalize_ddl_identifier(candidate.schema_name)}.{table}"
        return table

    exact_matches = [c for c in candidates if qualified_form(c) == normalized_declared]
    if len(exact_matches) == 1:
        return exact_matches[0], None
    if len(exact_matches) > 1:
        return None, (
            f"multiples sentencias CREATE TABLE coinciden con el nombre calificado "
            f"{declared_name!r}"
        )

    if not declared_is_qualified:
        terminal_matches = [
            c for c in candidates if normalize_ddl_identifier(c.table_name) == normalized_declared
        ]
        if len(terminal_matches) == 1:
            return terminal_matches[0], None
        if len(terminal_matches) > 1:
            return None, (
                f"multiples sentencias CREATE TABLE coinciden con el nombre terminal "
                f"{declared_name!r} en distintos schemas"
            )

    return None, f"no se encontro CREATE TABLE para la tabla declarada {declared_name!r}"


def _split_top_level(body: str) -> list[str]:
    """Separa `body` por comas de nivel superior (fuera de parentesis y de
    literales/identificadores entrecomillados) — evita romper tipos como
    `DECIMAL(9,2)` en la coma interna."""
    items: list[str] = []
    buffer: list[str] = []
    depth = 0
    in_single_quote = False
    in_double_quote = False
    i = 0
    n = len(body)

    while i < n:
        char = body[i]

        if in_single_quote:
            if char == "'":
                if i + 1 < n and body[i + 1] == "'":
                    buffer.append("''")
                    i += 2
                    continue
                in_single_quote = False
            buffer.append(char)
            i += 1
            continue

        if in_double_quote:
            if char == '"':
                if i + 1 < n and body[i + 1] == '"':
                    buffer.append('""')
                    i += 2
                    continue
                in_double_quote = False
            buffer.append(char)
            i += 1
            continue

        if char == "'":
            in_single_quote = True
            buffer.append(char)
        elif char == '"':
            in_double_quote = True
            buffer.append(char)
        elif char == "(":
            depth += 1
            buffer.append(char)
        elif char == ")":
            depth -= 1
            buffer.append(char)
        elif char == "," and depth == 0:
            items.append("".join(buffer))
            buffer = []
        else:
            buffer.append(char)
        i += 1

    tail = "".join(buffer).strip()
    if tail:
        items.append(tail)
    return [item.strip() for item in items if item.strip()]


def _parse_column_definition(item: str, ordinal: int) -> ParameterColumnDefinition | None:
    _schema_ignored, name, rest = _parse_qualified_identifier(item)
    if not name:
        return None
    working = rest

    nullable: bool | None = None
    if _NOT_NULL_RE.search(working):
        nullable = False
        working = _NOT_NULL_RE.sub(" ", working)
    elif _NULL_RE.search(working):
        nullable = True
        working = _NULL_RE.sub(" ", working)

    is_primary_key: bool | None = None
    if _PRIMARY_KEY_RE.search(working):
        is_primary_key = True
        working = _PRIMARY_KEY_RE.sub(" ", working)

    declared_type = " ".join(working.split())
    if not declared_type:
        return None

    return ParameterColumnDefinition(
        original_name=name,
        normalized_name=normalize_ddl_identifier(name),
        declared_type=declared_type,
        nullable=nullable,
        is_primary_key=is_primary_key,
        ordinal=ordinal,
    )


def parse_columns_from_body(
    body: str, *, warnings: list[str], table_label: str
) -> ParsedDdlTable:
    items = _split_top_level(body)

    column_items: list[str] = []
    table_level_pk_columns: list[str] = []
    has_unsupported_constraint = False

    for item in items:
        pk_match = _TABLE_LEVEL_PK_RE.match(item)
        if pk_match:
            for raw_col in pk_match.group("cols").split(","):
                _, col_name, _ = _parse_qualified_identifier(raw_col.strip())
                if col_name:
                    table_level_pk_columns.append(normalize_ddl_identifier(col_name))
            continue
        if _TABLE_CONSTRAINT_START_RE.match(item):
            has_unsupported_constraint = True
            warnings.append(
                f"{table_label}: constraint no interpretada ignorada "
                f"(FOREIGN KEY/CHECK/UNIQUE/CONSTRAINT no soportadas): {item[:60]!r}"
            )
            continue
        column_items.append(item)

    columns: list[ParameterColumnDefinition] = []
    has_unparseable_item = False
    for ordinal, item in enumerate(column_items, start=1):
        column = _parse_column_definition(item, ordinal)
        if column is None:
            has_unparseable_item = True
            warnings.append(f"{table_label}: no se pudo interpretar la columna #{ordinal}")
            continue
        if _DEFAULT_RE.search(column.declared_type):
            has_unsupported_constraint = True
            warnings.append(
                f"{table_label}: columna {column.original_name!r} tiene una clausula DEFAULT "
                "no interpretada"
            )
        columns.append(column)

    if table_level_pk_columns:
        pk_set = set(table_level_pk_columns)
        columns = [
            column.model_copy(update={"is_primary_key": True})
            if column.normalized_name in pk_set
            else column
            for column in columns
        ]

    if not columns:
        return ParsedDdlTable(columns=[], support_status=ParseSupportStatus.UNSUPPORTED)
    if has_unsupported_constraint or has_unparseable_item:
        return ParsedDdlTable(columns=columns, support_status=ParseSupportStatus.PARTIAL)
    return ParsedDdlTable(columns=columns, support_status=ParseSupportStatus.SUPPORTED)


def parse_ddl_for_table(
    ddl_text: str, *, declared_table_name: str, table_label: str, warnings: list[str]
) -> ParsedDdlTable:
    """Punto de entrada: localiza y resuelve la tabla declarada dentro de
    `ddl_text`, y parsea sus columnas segun el subconjunto soportado."""
    candidates = find_create_table_statements(ddl_text)
    resolved, resolution_warning = resolve_declared_table(
        candidates, declared_name=declared_table_name
    )
    if resolved is None:
        if resolution_warning:
            warnings.append(f"{table_label}: {resolution_warning}")
        return ParsedDdlTable(columns=[], support_status=ParseSupportStatus.UNSUPPORTED)
    return parse_columns_from_body(resolved.body, warnings=warnings, table_label=table_label)
