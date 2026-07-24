"""Tests del MarkdownRenderer puro (Prompt 13a): sin filesystem, sin
Settings, sin RunState -- solo `render_markdown`/`safe_text` contra
objetos `RuleDraft` construidos en memoria."""

from __future__ import annotations

from altamira_extractor.contracts.enums import EvidenceValidationStatus, FunctionalReviewStatus
from altamira_extractor.contracts.rule_draft import Claim, ClaimField, RuleDraft
from altamira_extractor.pipeline.markdown_renderer import (
    MARKDOWN_RENDERER_VERSION,
    render_markdown,
    safe_text,
)


def _claim(**overrides: object) -> Claim:
    defaults: dict[str, object] = {
        "claim_id": "c1",
        "field": ClaimField.CONDITION,
        "evidence_paths": ["$.decision.expression"],
        "evidence_ids": ["ev-1"],
    }
    defaults.update(overrides)
    return Claim(**defaults)  # type: ignore[arg-type]


def _draft(**overrides: object) -> RuleDraft:
    defaults: dict[str, object] = {
        "schema_version": "2.0",
        "title": "Titulo",
        "context": "Contexto",
        "statement": "Enunciado",
        "condition": "WS-COD = 'R001'",
        "parameters": [],
        "effect": "Efecto",
        "parameter_source": None,
        "traceability": ["ev-1"],
        "limitations": ["Requiere revision funcional"],
        "claims": [_claim()],
        "evidence_validation_status": EvidenceValidationStatus.EVIDENCE_VALIDATED,
        "functional_review_status": FunctionalReviewStatus.NEEDS_FUNCTIONAL_REVIEW,
    }
    defaults.update(overrides)
    return RuleDraft(**defaults)  # type: ignore[arg-type]


def _render(draft: RuleDraft) -> str:
    return render_markdown(draft).decode("utf-8")


# --- version ---


def test_renderer_version_constant() -> None:
    assert MARKDOWN_RENDERER_VERSION == "1.0"


# --- estructura / secciones ---


def test_all_sections_present() -> None:
    text = _render(_draft())
    for heading in (
        "## Contexto",
        "## Regla propuesta",
        "## Condición",
        "## Parámetros",
        "## Efecto",
        "## Fuente paramétrica",
        "## Trazabilidad",
        "## Limitaciones",
        "## Claims y evidencia",
    ):
        assert heading in text


def test_title_is_h1() -> None:
    text = _render(_draft(title="Mi regla"))
    assert text.startswith("# Mi regla\n")


def test_evidence_validation_status_visible_verbatim() -> None:
    text = _render(_draft())
    assert "> Estado de evidencia: EVIDENCE_VALIDATED" in text


def test_functional_review_status_visible_verbatim() -> None:
    text = _render(_draft())
    assert "> Estado de revisión funcional: NEEDS_FUNCTIONAL_REVIEW" in text


def test_disclaimer_visible() -> None:
    text = _render(_draft())
    assert (
        "Este documento es un borrador generado automáticamente y requiere revisión funcional."
        in text
    )


_FORBIDDEN_PHRASES = (
    "regla aprobada",
    "regla confirmada",
    "validada funcionalmente",
    "lista para producción",
)


def test_static_scaffold_never_introduces_forbidden_phrases() -> None:
    text = _render(_draft()).lower()
    for phrase in _FORBIDDEN_PHRASES:
        assert phrase not in text


def test_dynamic_content_with_forbidden_phrases_passes_through_unmodified() -> None:
    # Prompt 13a no reinterpreta ni valida el contenido funcional: si el
    # RuleDraft dinamico contiene una de estas frases, el renderer la
    # proyecta igual (escapada), nunca la rechaza ni la reescribe.
    text = _render(_draft(effect="lista para producción"))
    assert "lista para producción" in text


# --- nulos / listas vacias ---


def test_empty_parameters_list_uses_fixed_convention() -> None:
    text = _render(_draft(parameters=[]))
    section = text.split("## Parámetros\n")[1].split("\n\n")[0]
    assert section == "Sin elementos registrados"


def test_nonempty_parameters_rendered_as_bullets() -> None:
    text = _render(_draft(parameters=["PARM01", "PARM02"]))
    assert "- PARM01" in text
    assert "- PARM02" in text


def test_null_parameter_source_uses_fixed_convention() -> None:
    text = _render(_draft(parameter_source=None))
    section = text.split("## Fuente paramétrica\n")[1].split("\n\n")[0]
    assert section == "No informado"


def test_present_parameter_source_rendered_verbatim() -> None:
    text = _render(_draft(parameter_source="PARAM_DEMO"))
    section = text.split("## Fuente paramétrica\n")[1].split("\n\n")[0]
    # '_' se escapa (capacidad estructural real: enfasis): el valor
    # funcional se preserva quitando los backslashes insertados.
    assert section.replace("\\", "") == "PARAM_DEMO"


# --- claims ---


def test_claim_shows_claim_id_field_and_evidence() -> None:
    draft = _draft(
        claims=[
            _claim(
                claim_id="claimxyz",
                field=ClaimField.EFFECT,
                evidence_ids=["eva", "evb"],
                evidence_paths=["$.effects.table_effects[0]"],
            )
        ]
    )
    text = _render(draft)
    assert "### Claim: claimxyz" in text
    assert "- Campo: effect" in text
    assert "- eva" in text
    assert "- evb" in text
    assert "$.effects.table\\_effects\\[0\\]" in text


def test_claims_preserve_persisted_order() -> None:
    draft = _draft(
        claims=[
            _claim(claim_id="second", field=ClaimField.EFFECT),
            _claim(claim_id="first", field=ClaimField.CONDITION),
        ]
    )
    text = _render(draft)
    assert text.index("### Claim: second") < text.index("### Claim: first")


def test_renderer_does_not_reinterpret_evidence() -> None:
    # No debe aparecer ningun texto explicativo generado por el renderer
    # sobre por que la evidencia sustenta el claim -- solo los campos
    # reales del Claim.
    text = _render(_draft())
    assert "sustenta" not in text.lower()
    assert "porque" not in text.lower()


# --- Unicode ---


def test_unicode_preserved() -> None:
    text = _render(_draft(context="Cuenta en años múltiples: ñ, á, 日本語, emoji 🚀"))
    assert "años múltiples: ñ, á, 日本語, emoji 🚀" in text


# --- preservacion literal (numeros, fechas, codigos, tablas) ---
#
# El escapado SOLO inserta backslashes antes de caracteres con capacidad
# estructural (incluye '-', usado en fechas ISO y en codigos con guion):
# el valor renderizado, al quitar esos backslashes, debe reconstruir el
# original exactamente -- ningun digito/letra/separador se pierde, cambia
# de orden o se reinterpreta.


def _strip_backslash_escapes(text: str) -> str:
    return text.replace("\\", "")


def test_iso_date_preserved() -> None:
    text = _render(_draft(effect="Vigente desde 2026-07-24"))
    section = text.split("## Efecto\n")[1].split("\n\n")[0]
    assert _strip_backslash_escapes(section) == "Vigente desde 2026-07-24"


def test_decimal_type_preserved() -> None:
    text = _render(_draft(parameters=["DECIMAL(9,2)"]))
    section = text.split("## Parámetros\n")[1].split("\n\n")[0]
    assert _strip_backslash_escapes(section) == "- DECIMAL(9,2)"


def test_schema_table_name_preserved() -> None:
    text = _render(_draft(effect="Actualiza SCHEMA.TABLE"))
    assert "SCHEMA.TABLE" in text


def test_return_code_preserved() -> None:
    text = _render(_draft(condition="WS-COD = 'PR-RC-001'"))
    section = text.split("## Condición\n")[1].split("\n\n")[0]
    assert _strip_backslash_escapes(section) == "WS-COD = 'PR-RC-001'"


def test_source_file_path_preserved() -> None:
    # evidence_paths exige el prefijo JSONPath "$." (contrato Claim); el
    # nombre de archivo fuente se preserva como texto libre en otro
    # campo (p. ej. traceability), no como evidence_path.
    draft = _draft(traceability=["source/file.cbl"])
    text = _render(draft)
    assert "source/file.cbl" in text


def test_evidence_path_jsonpath_preserved() -> None:
    draft = _draft(claims=[_claim(evidence_paths=["$.effects.return_codes[0]"])])
    text = _render(draft)
    assert "$.effects.return\\_codes\\[0\\]" in text


def test_renderer_does_not_normalize_case_or_identifiers() -> None:
    text = _render(_draft(condition="ws-cod = 'r001'"))
    section = text.split("## Condición\n")[1].split("\n\n")[0]
    assert _strip_backslash_escapes(section) == "ws-cod = 'r001'"
    assert "WS-COD" not in text


# --- rule_type ---


def test_no_rule_type_section() -> None:
    text = _render(_draft())
    assert "rule_type" not in text.lower()
    assert "tipo de regla" not in text.lower()


# --- byte-level determinism ---


def test_output_is_utf8_lf_only_single_trailing_newline() -> None:
    raw = render_markdown(_draft(context="línea con acentos"))
    assert b"\r" not in raw
    assert raw.endswith(b"\n")
    assert not raw.endswith(b"\n\n")
    text = raw.decode("utf-8")
    for line in text.split("\n"):
        assert line == line.rstrip(" \t")


def test_deterministic_same_input_same_bytes() -> None:
    draft = _draft()
    assert render_markdown(draft) == render_markdown(draft)


# --- safe_text: escapado ---


def test_ampersand_literal_is_escaped() -> None:
    assert safe_text("Tom & Jerry") == "Tom &amp; Jerry"


def test_text_already_containing_entity_is_not_double_escaped_downstream() -> None:
    # El '&' original SIEMPRE se escapa primero: un valor que ya
    # contenia literalmente "&lt;" (no una entidad real, solo esos
    # caracteres) se vuelve completamente inerte.
    assert safe_text("&lt;") == "&amp;lt;"


def test_script_tag_neutralized() -> None:
    result = safe_text("<script>alert(1)</script>")
    assert "<script>" not in result
    assert result == "&lt;script&gt;alert\\(1\\)&lt;/script&gt;"


def test_closing_script_tag_neutralized() -> None:
    result = safe_text("</script>")
    assert result == "&lt;/script&gt;"
    assert "<" not in result and ">" not in result


def test_markdown_link_neutralized() -> None:
    result = safe_text("[texto](https://evil.example/x)")
    assert result == "\\[texto\\]\\(https://evil.example/x\\)"


def test_markdown_image_neutralized() -> None:
    result = safe_text("![alt](https://evil.example/x.png)")
    assert result == "\\!\\[alt\\]\\(https://evil.example/x.png\\)"


def test_html_with_attributes_neutralized() -> None:
    result = safe_text('<img src="x" onerror="alert(1)">')
    assert "<" not in result and ">" not in result
    assert result.startswith("&lt;img")


def test_heading_marker_neutralized() -> None:
    assert safe_text("# Titulo falso") == "\\# Titulo falso"


def test_blockquote_marker_neutralized() -> None:
    # '>' se neutraliza via entidad HTML, no via backslash.
    assert safe_text("> cita falsa") == "&gt; cita falsa"


def test_unordered_list_dash_neutralized() -> None:
    assert safe_text("- item falso") == "\\- item falso"


def test_unordered_list_plus_neutralized() -> None:
    assert safe_text("+ item falso") == "\\+ item falso"


def test_unordered_list_asterisk_neutralized() -> None:
    assert safe_text("* item falso") == "\\* item falso"


def test_ordered_list_marker_neutralized() -> None:
    assert safe_text("1. paso falso") == "1\\. paso falso"
    assert safe_text("23. paso falso") == "23\\. paso falso"


def test_decimal_looking_value_not_treated_as_ordered_list() -> None:
    # "1.5" no tiene un espacio/fin de cadena tras el punto en esa
    # posicion critica: no se escapa el punto (preservacion literal).
    assert safe_text("1.5") == "1.5"


def test_table_pipe_neutralized() -> None:
    assert safe_text("a | b") == "a \\| b"


def test_horizontal_rule_neutralized() -> None:
    assert safe_text("---") == "\\-\\-\\-"
    assert safe_text("***") == "\\*\\*\\*"
    assert safe_text("___") == "\\_\\_\\_"


def test_backtick_neutralized() -> None:
    assert safe_text("`code`") == "\\`code\\`"


def test_triple_backtick_fence_neutralized() -> None:
    assert (
        safe_text("```python\nprint(1)\n```")
        == "\\`\\`\\`python\\nprint\\(1\\)\\n\\`\\`\\`"
    )


def test_backslash_itself_is_escaped() -> None:
    assert safe_text("C:\\path\\to\\file") == "C:\\\\path\\\\to\\\\file"


def test_internal_crlf_converted_to_literal() -> None:
    assert safe_text("linea1\r\nlinea2") == "linea1\\nlinea2"


def test_internal_cr_converted_to_literal() -> None:
    assert safe_text("linea1\rlinea2") == "linea1\\nlinea2"


def test_internal_tab_converted_to_literal() -> None:
    assert safe_text("col1\tcol2") == "col1\\tcol2"


def test_outer_whitespace_stripped() -> None:
    assert safe_text("  \t texto \t  ") == "texto"


def test_no_double_escape_of_generated_entities() -> None:
    # Un '&' que YA formaba "&amp;" en el origen se escapa una sola vez
    # como cualquier '&': nunca se re-escanea el resultado.
    result = safe_text("&amp;")
    assert result == "&amp;amp;"
    assert "&amp;amp;amp;" not in result


def test_colon_slash_comma_semicolon_quotes_not_escaped() -> None:
    assert safe_text("a:b/c,d;e'f\"g@h%i") == "a:b/c,d;e'f\"g@h%i"
