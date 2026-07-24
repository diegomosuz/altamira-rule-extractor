"""Carga y sustitucion de las plantillas de `prompts/` (Prompt 12).

Mismo principio que `cypher_query_loader.py`: los placeholders `{{...}}`
nunca se sustituyen via `str.format()` (el JSON embebido contiene llaves
que `format()` interpretaria como campos de reemplazo) — siempre via
`str.replace()` de un token explicito, y solo despues de validar que el
numero de ocurrencias coincide exactamente con lo esperado."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .errors import PromptTemplateError


@dataclass(frozen=True)
class LoadedPromptTemplate:
    relative_path: str
    template_text: str
    template_hash: str


@dataclass(frozen=True)
class RenderedPrompt:
    effective_text: str
    effective_hash: str


def load_prompt_template(
    path: Path,
    *,
    relative_path: str,
    expected_placeholder_counts: dict[str, int],
) -> LoadedPromptTemplate:
    """Lee `path`, valida que sea un archivo regular no vacio, y exige
    exactamente el numero de ocurrencias declarado para cada placeholder
    en `expected_placeholder_counts` (p. ej. `{"{{CONTEXT_PACKAGE_JSON}}": 1}`)."""
    if not path.exists():
        raise PromptTemplateError(f"no se encontro la plantilla de prompt ({path.name})")
    if not path.is_file():
        raise PromptTemplateError(
            f"la plantilla de prompt ({path.name}) no es un archivo regular"
        )

    raw_bytes = path.read_bytes()
    if not raw_bytes.strip():
        raise PromptTemplateError(f"la plantilla de prompt ({path.name}) esta vacia")

    template_text = raw_bytes.decode("utf-8")
    template_hash = hashlib.sha256(raw_bytes).hexdigest()

    for placeholder, expected_count in expected_placeholder_counts.items():
        occurrences = template_text.count(placeholder)
        if occurrences != expected_count:
            raise PromptTemplateError(
                f"la plantilla de prompt ({path.name}) tiene {occurrences} ocurrencias de "
                f"{placeholder!r}, se esperaban exactamente {expected_count}"
            )

    return LoadedPromptTemplate(
        relative_path=relative_path, template_text=template_text, template_hash=template_hash
    )


def render_prompt(template_text: str, substitutions: dict[str, str]) -> RenderedPrompt:
    """Sustituye cada placeholder de `substitutions` EXACTAMENTE via
    `str.replace()` (nunca `str.format()`) y calcula el SHA-256 del texto
    resultante."""
    effective_text = template_text
    for placeholder, value in substitutions.items():
        if placeholder not in effective_text:
            raise PromptTemplateError(f"placeholder {placeholder!r} no encontrado para sustituir")
        effective_text = effective_text.replace(placeholder, value)
    effective_hash = hashlib.sha256(effective_text.encode("utf-8")).hexdigest()
    return RenderedPrompt(effective_text=effective_text, effective_hash=effective_hash)
