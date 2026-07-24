"""MarkdownRenderer (Prompt 13a): convierte un `RuleDraft` EVIDENCE_VALIDATED
(el `final_rule_draft` de un `GuardrailCandidateArtifact` persistido en
`artifacts/09-guardrails/`) a un documento Markdown determinístico y
portable.

Principios (CLAUDE.md / correcciones de autorizacion del Prompt 13a):
- El renderer no interpreta ni enriquece: cada seccion es una proyeccion
  literal de un campo real de `RuleDraft` (no existe `rule_type` en el
  contrato, por lo que no se agrega ninguna seccion para el).
- Todo el contenido de texto libre de `RuleDraft` se trata como no
  confiable: se escapa antes de insertarse en el documento.
  `evidence_validation_status`, `functional_review_status` y
  `Claim.field` son enums Pydantic (no texto libre): no requieren
  escapado.
- El escapado neutraliza sintaxis Markdown/HTML sin normalizar el valor:
  no cambia mayusculas/minusculas, separadores decimales, formatos de
  fecha ni identificadores. No se escapa globalmente ':' '/' ',' ';'
  comillas/apostrofes '@' '%' (degradarian paths, fechas, codigos y
  nombres de tabla sin necesidad real).
- Determinismo byte a byte: UTF-8, LF exclusivo, un unico salto de linea
  final, sin espacios/tabs finales en ninguna linea.

Claims are rendered in their persisted RuleDraft order.
"""

from __future__ import annotations

import re

from ..contracts.rule_draft import Claim, RuleDraft

MARKDOWN_RENDERER_VERSION = "1.0"

_NO_VALUE = "No informado"
_EMPTY_LIST = "Sin elementos registrados"

_MANDATORY_DISCLAIMER = (
    "Este documento es un borrador generado automáticamente y requiere revisión funcional."
)

# Caracteres con capacidad estructural real en este contexto (backslash se
# maneja aparte, siempre primero, para no re-escapar los backslashes que
# insertamos nosotros mismos despues). Deliberadamente NO incluye
# ':' '/' ',' ';' comillas/apostrofes '@' '%' '<' '>' -- '<'/'>' se
# neutralizan via entidades HTML (nunca via backslash), el resto no tiene
# capacidad estructural real en Markdown/CommonMark.
_BACKSLASH_ESCAPE_CHARS = "`#*_[]()!|{}+-"
_BACKSLASH_ESCAPE_RE = re.compile("[" + re.escape(_BACKSLASH_ESCAPE_CHARS) + "]")

# Un valor que, al comienzo de linea, coincide con "digitos + '.' + espacio
# (o fin de cadena)" puede abrir una lista ordenada CommonMark (p. ej.
# "1. algo"). Es la UNICA razon para tocar un '.': nunca se escapa
# globalmente (degradaria fechas/decimales como "1.5").
_ORDERED_LIST_START_RE = re.compile(r"^(\d{1,9})\.(?=[ \t]|$)")


def _normalize_line_endings(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _escape_html_entities(text: str) -> str:
    # Orden fijo: '&' primero. '<'/'>' -> '&lt;'/'&gt;' no introducen
    # nuevos '&', asi que no hay riesgo de doble escape de las entidades
    # que generamos.
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text


def _escape_markdown_structural_chars(text: str) -> str:
    # Backslash primero y en un paso separado: si se hiciera junto con el
    # resto, re-escaparia los backslashes que esos reemplazos acaban de
    # insertar.
    text = text.replace("\\", "\\\\")
    text = _BACKSLASH_ESCAPE_RE.sub(lambda m: "\\" + m.group(0), text)
    return text


def _escape_leading_ordered_list_marker(text: str) -> str:
    match = _ORDERED_LIST_START_RE.match(text)
    if match is None:
        return text
    digits = match.group(1)
    return f"{digits}\\.{text[match.end():]}"


def safe_text(value: str) -> str:
    """Convierte cualquier valor de texto libre de `RuleDraft` en texto
    seguro para insertar como UNA linea de Markdown.

    Orden fijo (cada paso opera sobre el resultado del anterior):
    1. normaliza CRLF/CR a LF;
    2. recorta espacios/tabs exteriores (whitespace horizontal, nunca
       saltos de linea);
    3. escapa entidades HTML ('&' antes que '<'/'>', sin doble escape);
    4. escapa backslash y los caracteres con capacidad estructural real
       en Markdown (backtick, '#', '*', '_', '[', ']', '(', ')', '!',
       '|', '{', '}', '+', '-');
    5. convierte saltos de linea y tabs internos a su representacion
       visible literal ('\\n', '\\t' de dos caracteres) -- nunca una
       linea real nueva ni un tab real;
    6. neutraliza unicamente un '.' inicial cuando podria abrir una
       lista ordenada ('1. ', '23.' seguido de fin de cadena, etc.).

    No normaliza mayusculas/minusculas, formato de fecha, separador
    decimal ni identificadores: solo neutraliza sintaxis de
    presentacion.
    """
    text = _normalize_line_endings(value)
    text = text.strip(" \t")
    text = _escape_html_entities(text)
    text = _escape_markdown_structural_chars(text)
    text = text.replace("\n", "\\n").replace("\t", "\\t")
    text = _escape_leading_ordered_list_marker(text)
    return text


def _bullet_list(values: list[str]) -> list[str]:
    if not values:
        return [_EMPTY_LIST]
    return [f"- {safe_text(v)}" for v in values]


def _claim_section(claim: Claim) -> list[str]:
    lines = [f"### Claim: {safe_text(claim.claim_id)}"]
    lines.append(f"- Campo: {safe_text(claim.field.value)}")
    if claim.evidence_ids:
        lines.append("- evidence_ids:")
        lines.extend(f"  - {safe_text(evidence_id)}" for evidence_id in claim.evidence_ids)
    else:
        # No alcanzable via Claim.evidence_ids (min_length=1 en el
        # contrato), pero se documenta explicitamente la convencion.
        lines.append(f"- evidence_ids: {_EMPTY_LIST}")
    if claim.evidence_paths:
        lines.append("- evidence_paths:")
        lines.extend(f"  - {safe_text(path)}" for path in claim.evidence_paths)
    else:
        # No alcanzable via Claim.evidence_paths (min_length=1), idem.
        lines.append(f"- evidence_paths: {_EMPTY_LIST}")
    return lines


def render_markdown(rule_draft: RuleDraft) -> bytes:
    """Renderiza `rule_draft` (el `final_rule_draft` EVIDENCE_VALIDATED de
    un `GuardrailCandidateArtifact`) a los bytes UTF-8 finales del
    documento: LF exclusivo, un unico '\\n' final, ninguna linea con
    espacios/tabs finales."""
    lines: list[str] = []
    lines.append(f"# {safe_text(rule_draft.title)}")
    lines.append("")
    lines.append(f"> Estado de evidencia: {rule_draft.evidence_validation_status.value}")
    lines.append(f"> Estado de revisión funcional: {rule_draft.functional_review_status.value}")
    lines.append("")
    lines.append(_MANDATORY_DISCLAIMER)
    lines.append("")
    lines.append("## Contexto")
    lines.append(safe_text(rule_draft.context))
    lines.append("")
    lines.append("## Regla propuesta")
    lines.append(safe_text(rule_draft.statement))
    lines.append("")
    lines.append("## Condición")
    lines.append(safe_text(rule_draft.condition))
    lines.append("")
    lines.append("## Parámetros")
    lines.extend(_bullet_list(rule_draft.parameters))
    lines.append("")
    lines.append("## Efecto")
    lines.append(safe_text(rule_draft.effect))
    lines.append("")
    lines.append("## Fuente paramétrica")
    lines.append(
        _NO_VALUE if rule_draft.parameter_source is None else safe_text(rule_draft.parameter_source)
    )
    lines.append("")
    lines.append("## Trazabilidad")
    lines.extend(_bullet_list(rule_draft.traceability))
    lines.append("")
    lines.append("## Limitaciones")
    lines.extend(_bullet_list(rule_draft.limitations))
    lines.append("")
    lines.append("## Claims y evidencia")
    for claim in rule_draft.claims:
        lines.extend(_claim_section(claim))
        lines.append("")

    while lines and lines[-1] == "":
        lines.pop()

    return ("\n".join(lines) + "\n").encode("utf-8")
