"""Resolucion determinista de aplicabilidad parametrica (Q3, Prompt 10b).

No es un parser SQL general: reconoce exclusivamente una gramatica V1
cerrada sobre `predicate_text` (evidencia cruda de `READS` ya persistida
en el grafo, nunca vuelta a leer desde CSV/SemanticEnrichmentArtifact):

    predicate  ::= comparison (' AND ' comparison)*
    comparison ::= ['('] column '=' literal [')']
    column     ::= identifier | '"' identifier '"'
    literal    ::= string | number

No soportado (cualquier construccion fuera de esta gramatica dentro de un
`predicate_text` no vacio hace que ESA comparacion quede sin resolver,
nunca EXACT por acercamiento): OR, BETWEEN, IN, LIKE, funciones,
subconsultas, expresiones aritmeticas, variables host como valor
resuelto (`:VAR`, `?`), parametros dinamicos.

No usa LLM ni matching semantico libre: solo regex ancladas sobre la
gramatica cerrada anterior.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..contracts.enums import ApplicabilityStatus
from .identifiers import normalize_identifier

_IDENTIFIER = r'(?:"[A-Za-z_][A-Za-z0-9_]*"|[A-Za-z_][A-Za-z0-9_]*)'
_STRING_LITERAL = r"'(?:[^']|'')*'"
_NUMBER_LITERAL = r"[+-]?\d+(?:\.\d+)?"
_COMPARISON_RE = re.compile(
    rf"^\s*\(?\s*(?P<column>{_IDENTIFIER})\s*=\s*"
    rf"(?P<literal>{_STRING_LITERAL}|{_NUMBER_LITERAL})\s*\)?\s*$"
)


@dataclass(frozen=True)
class ResolvedComparison:
    column: str
    value: str | float


@dataclass(frozen=True)
class PredicateResolution:
    """Clasificacion de UNA fila de `access_evidence` (un `predicate_text`)."""

    status: ApplicabilityStatus
    resolved: tuple[ResolvedComparison, ...]


def _strip_outer_parens(text: str) -> str:
    stripped = text.strip()
    while stripped.startswith("(") and stripped.endswith(")"):
        depth = 0
        balanced_to_end = True
        for index, char in enumerate(stripped):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0 and index != len(stripped) - 1:
                    balanced_to_end = False
                    break
        if not balanced_to_end:
            break
        stripped = stripped[1:-1].strip()
    return stripped


def _unquote_column(column: str) -> str:
    if column.startswith('"') and column.endswith('"'):
        return column[1:-1]
    return column


def _parse_literal(literal: str) -> str | float:
    if literal.startswith("'"):
        return literal[1:-1].replace("''", "'")
    return float(literal)


def _split_top_level_and(text: str) -> list[str]:
    """Divide por ' AND ' de nivel superior, respetando literales string
    (con `''` como comilla escapada) y parentesis balanceados."""
    conjuncts: list[str] = []
    depth = 0
    in_string = False
    current: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if in_string:
            current.append(char)
            if char == "'":
                if text[index + 1 : index + 2] == "'":
                    current.append("'")
                    index += 2
                    continue
                in_string = False
            index += 1
            continue
        if char == "'":
            in_string = True
            current.append(char)
            index += 1
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if depth == 0 and text[index : index + 5].upper() == " AND ":
            conjuncts.append("".join(current))
            current = []
            index += 5
            continue
        current.append(char)
        index += 1
    conjuncts.append("".join(current))
    return conjuncts


def resolve_predicate_row(
    predicate_text: str | None, host_variables_json: str | None
) -> PredicateResolution:
    """Clasifica una unica fila de evidencia de acceso (un `predicate_text`)."""
    if not predicate_text or not predicate_text.strip():
        return PredicateResolution(status=ApplicabilityStatus.UNRESOLVED, resolved=())

    # `host_variables_json` no cambia la clasificacion por si solo: una
    # comparacion contra una host variable (p. ej. `COD = :WS-COD`) ya no
    # coincide con la gramatica de literal string/numerico y por lo tanto
    # queda sin resolver a traves del propio regex, sin necesitar
    # inspeccionar host_variables_json aparte. Se recibe el parametro para
    # dejar la firma explicita sobre que evidencia esta disponible, no
    # para reinterpretarla (parametro deliberadamente no usado).

    conjuncts = _split_top_level_and(_strip_outer_parens(predicate_text))
    resolved: list[ResolvedComparison] = []
    any_unresolved = False

    for conjunct in conjuncts:
        candidate = _strip_outer_parens(conjunct)
        match = _COMPARISON_RE.match(candidate)
        if match is None:
            any_unresolved = True
            continue
        literal_text = match.group("literal")
        column = normalize_identifier(_unquote_column(match.group("column")))
        resolved.append(ResolvedComparison(column=column, value=_parse_literal(literal_text)))

    if resolved and not any_unresolved:
        return PredicateResolution(status=ApplicabilityStatus.EXACT, resolved=tuple(resolved))
    if resolved and any_unresolved:
        return PredicateResolution(status=ApplicabilityStatus.PARTIAL, resolved=tuple(resolved))
    return PredicateResolution(status=ApplicabilityStatus.UNRESOLVED, resolved=())


def aggregate_applicability(
    row_resolutions: list[PredicateResolution],
) -> ApplicabilityStatus:
    """Combina la clasificacion de todas las evidencias de acceso a una
    misma ParameterTable (CLAUDE.md: no asignar EXACT solo por existir la
    tabla; una tabla sin ninguna evidencia usable es UNRESOLVED)."""
    if not row_resolutions:
        return ApplicabilityStatus.UNRESOLVED
    statuses = {resolution.status for resolution in row_resolutions}
    if statuses == {ApplicabilityStatus.EXACT}:
        return ApplicabilityStatus.EXACT
    if statuses == {ApplicabilityStatus.UNRESOLVED}:
        return ApplicabilityStatus.UNRESOLVED
    return ApplicabilityStatus.PARTIAL


def entry_matches_comparisons(
    normalized_row: dict[str, Any], comparisons: tuple[ResolvedComparison, ...]
) -> bool:
    """Determina si un ParameterEntry (ya deserializado de
    `normalized_row_json`) satisface TODAS las comparaciones resueltas de
    una fila de evidencia EXACT."""
    normalized_entry = {
        normalize_identifier(str(key)): value for key, value in normalized_row.items()
    }
    for comparison in comparisons:
        if comparison.column not in normalized_entry:
            return False
        entry_value = normalized_entry[comparison.column]
        if isinstance(comparison.value, float):
            try:
                if float(entry_value) != comparison.value:
                    return False
            except (TypeError, ValueError):
                return False
        elif str(entry_value).strip() != str(comparison.value).strip():
            return False
    return True
