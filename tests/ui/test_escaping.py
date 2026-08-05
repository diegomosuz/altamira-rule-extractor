"""Escapado seguro (Prompt 13d): autoescape de Jinja2, ausencia de
`|safe`/`Markup`/`render_template_string`, ausencia de HTML/JS inline en
los templates, y contenido no confiable (COBOL/SQL/CSV/LLM/DomainTerm/
paths/errores) siempre representado como texto escapado."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from altamira_extractor.ui import STATIC_DIR, TEMPLATES_DIR

_TEMPLATE_FILES: list[Path] = sorted(TEMPLATES_DIR.glob("*.html"))
_ROUTER_SOURCE = (TEMPLATES_DIR.parent / "router.py").read_text(encoding="utf-8")

_ON_ATTR_RE = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)
_SCRIPT_TAG_RE = re.compile(r"<script\b([^>]*)>", re.IGNORECASE)


def test_at_least_the_expected_templates_exist() -> None:
    names = {p.name for p in _TEMPLATE_FILES}
    assert names == {
        "base.html",
        "upload.html",
        "runs.html",
        "run_status.html",
        "_status_fragment.html",
        "candidates.html",
        "context.html",
        "rule.html",
        "guardrail.html",
        "download.html",
        "error.html",
        # Modernizacion UI: macros compartidas (breadcrumb/badge de estado
        # e iconos SVG inline) -- nunca logica nueva, solo presentacion.
        "_icons.html",
        "_components.html",
        # Gobierno operativo read-only (Fase 15A).
        "governance.html",
        "_governance_summary.html",
        "_governance_artifacts.html",
        "_governance_events.html",
        "_governance_generations.html",
        "_governance_groups.html",
        "_governance_issues.html",
        # Acciones operativas controladas (Fase 15B1).
        "governance_actions.html",
        "governance_action_form.html",
        "governance_action_confirm.html",
        "governance_action_result.html",
        "governance_audit.html",
    }


@pytest.mark.parametrize("template_path", _TEMPLATE_FILES, ids=lambda p: p.name)
def test_template_never_uses_safe_filter_or_markup(template_path: Path) -> None:
    text = template_path.read_text(encoding="utf-8")
    assert "|safe" not in text
    assert "| safe" not in text
    assert "Markup(" not in text
    assert "render_template_string" not in text


@pytest.mark.parametrize("template_path", _TEMPLATE_FILES, ids=lambda p: p.name)
def test_template_never_has_inline_script_content(template_path: Path) -> None:
    text = template_path.read_text(encoding="utf-8")
    for match in _SCRIPT_TAG_RE.finditer(text):
        attrs = match.group(1)
        assert "src=" in attrs, f"<script> inline (sin src=) en {template_path}"


@pytest.mark.parametrize("template_path", _TEMPLATE_FILES, ids=lambda p: p.name)
def test_template_never_has_inline_style_tag(template_path: Path) -> None:
    text = template_path.read_text(encoding="utf-8").lower()
    assert "<style" not in text
    assert 'style="' not in text


@pytest.mark.parametrize("template_path", _TEMPLATE_FILES, ids=lambda p: p.name)
def test_template_never_has_inline_event_handlers(template_path: Path) -> None:
    text = template_path.read_text(encoding="utf-8")
    match = _ON_ATTR_RE.search(text)
    assert match is None, f"handler on* encontrado en {template_path}: {match}"


@pytest.mark.parametrize("template_path", _TEMPLATE_FILES, ids=lambda p: p.name)
def test_template_never_uses_hx_on(template_path: Path) -> None:
    text = template_path.read_text(encoding="utf-8").lower()
    assert "hx-on" not in text


@pytest.mark.parametrize("template_path", _TEMPLATE_FILES, ids=lambda p: p.name)
def test_template_never_has_javascript_uri(template_path: Path) -> None:
    text = template_path.read_text(encoding="utf-8").lower()
    assert "javascript:" not in text


@pytest.mark.parametrize("template_path", _TEMPLATE_FILES, ids=lambda p: p.name)
def test_template_never_hardcodes_a_js_value_hint(template_path: Path) -> None:
    # HTMX admite valores `js:...` en algunos atributos (evaluados via
    # allowEval) -- prohibido explicitamente por el plan autorizado.
    text = template_path.read_text(encoding="utf-8")
    assert "js:" not in text


def test_router_source_never_uses_safe_or_markup_or_render_template_string() -> None:
    assert "|safe" not in _ROUTER_SOURCE
    assert "Markup(" not in _ROUTER_SOURCE
    assert "render_template_string" not in _ROUTER_SOURCE
    assert "autoescape=False" not in _ROUTER_SOURCE


def test_base_template_declares_htmx_config_without_eval_or_indicator_styles() -> None:
    base = (TEMPLATES_DIR / "base.html").read_text(encoding="utf-8")
    assert 'name="htmx-config"' in base
    assert '"allowEval": false' in base
    assert '"includeIndicatorStyles": false' in base


def test_htmx_script_tag_has_no_inline_nonce_or_eval_flags_in_markup() -> None:
    base = (TEMPLATES_DIR / "base.html").read_text(encoding="utf-8")
    assert 'src="' in base
    # El propio archivo vendorizado nunca se reescribe/comenta (Prompt
    # 13d, seccion 7): confirmado en test_routes.py via SHA-256; aqui
    # solo se confirma que el template lo referencia como archivo local.
    assert (STATIC_DIR / "htmx.min.js").is_file()
