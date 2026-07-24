"""Tests de la carga/sustitucion de plantillas de prompts/ (Prompt 12)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from altamira_extractor.pipeline.errors import PromptTemplateError
from altamira_extractor.pipeline.prompt_loader import load_prompt_template, render_prompt


def test_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(PromptTemplateError):
        load_prompt_template(
            tmp_path / "missing.md",
            relative_path="prompts/missing.md",
            expected_placeholder_counts={},
        )


def test_load_non_regular_file_raises(tmp_path: Path) -> None:
    directory = tmp_path / "adir.md"
    directory.mkdir()
    with pytest.raises(PromptTemplateError):
        load_prompt_template(
            directory, relative_path="prompts/adir.md", expected_placeholder_counts={}
        )


def test_load_empty_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "empty.md"
    path.write_text("   \n  ", encoding="utf-8")
    with pytest.raises(PromptTemplateError):
        load_prompt_template(
            path, relative_path="prompts/empty.md", expected_placeholder_counts={}
        )


def test_load_wrong_placeholder_count_raises(tmp_path: Path) -> None:
    path = tmp_path / "user.md"
    path.write_text("hola {{CONTEXT_PACKAGE_JSON}} {{CONTEXT_PACKAGE_JSON}}", encoding="utf-8")
    with pytest.raises(PromptTemplateError):
        load_prompt_template(
            path,
            relative_path="prompts/user.md",
            expected_placeholder_counts={"{{CONTEXT_PACKAGE_JSON}}": 1},
        )


def test_load_correct_placeholder_count_succeeds(tmp_path: Path) -> None:
    path = tmp_path / "user.md"
    content = "hola {{CONTEXT_PACKAGE_JSON}} chau"
    path.write_text(content, encoding="utf-8")
    loaded = load_prompt_template(
        path,
        relative_path="prompts/user.md",
        expected_placeholder_counts={"{{CONTEXT_PACKAGE_JSON}}": 1},
    )
    assert loaded.template_text == content
    assert loaded.relative_path == "prompts/user.md"
    assert loaded.template_hash == hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_load_zero_placeholders_declared_and_present_fails(tmp_path: Path) -> None:
    path = tmp_path / "system.md"
    path.write_text("eres un analista {{UNEXPECTED}}", encoding="utf-8")
    with pytest.raises(PromptTemplateError):
        load_prompt_template(
            path,
            relative_path="prompts/system.md",
            expected_placeholder_counts={"{{UNEXPECTED}}": 0},
        )


def test_render_prompt_substitutes_placeholder() -> None:
    rendered = render_prompt("hola {{X}} chau", {"{{X}}": "mundo"})
    assert rendered.effective_text == "hola mundo chau"
    expected_hash = hashlib.sha256(rendered.effective_text.encode("utf-8")).hexdigest()
    assert rendered.effective_hash == expected_hash


def test_render_prompt_missing_placeholder_raises() -> None:
    with pytest.raises(PromptTemplateError):
        render_prompt("hola sin placeholder", {"{{X}}": "mundo"})


def test_render_prompt_multiple_placeholders() -> None:
    template = "A={{A}} B={{B}} C={{C}}"
    rendered = render_prompt(template, {"{{A}}": "1", "{{B}}": "2", "{{C}}": "3"})
    assert rendered.effective_text == "A=1 B=2 C=3"


def test_render_prompt_never_uses_str_format_semantics() -> None:
    # El JSON embebido tiene llaves: str.format() las interpretaria como
    # campos de reemplazo y rompería. render_prompt debe usar
    # str.replace() y dejar llaves ajenas intactas.
    template = "payload: {{CONTEXT_PACKAGE_JSON}}"
    json_value = '{"a": {"b": 1}}'
    rendered = render_prompt(template, {"{{CONTEXT_PACKAGE_JSON}}": json_value})
    assert rendered.effective_text == f"payload: {json_value}"
