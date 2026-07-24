"""Tests del loader versionado de Q1-Q7 (Prompt 10b)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from altamira_extractor.pipeline.cypher_query_loader import (
    DEPTH_PLACEHOLDER,
    load_context_query,
)
from altamira_extractor.pipeline.errors import ContextBuildError

_RELATIVE_PATH = "queries/v1/q1_scope.cypher"


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ContextBuildError):
        load_context_query(
            tmp_path / "no-existe.cypher",
            relative_path=_RELATIVE_PATH,
            logical_query="Q1",
            expected_placeholder_count=0,
            dependency_depth=4,
        )


def test_directory_instead_of_file_raises(tmp_path: Path) -> None:
    directory = tmp_path / "not-a-file.cypher"
    directory.mkdir()
    with pytest.raises(ContextBuildError):
        load_context_query(
            directory,
            relative_path=_RELATIVE_PATH,
            logical_query="Q1",
            expected_placeholder_count=0,
            dependency_depth=4,
        )


def test_empty_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "q1.cypher"
    path.write_text("   \n", encoding="utf-8")
    with pytest.raises(ContextBuildError):
        load_context_query(
            path,
            relative_path=_RELATIVE_PATH,
            logical_query="Q1",
            expected_placeholder_count=0,
            dependency_depth=4,
        )


def test_no_placeholder_query_effective_equals_template(tmp_path: Path) -> None:
    path = tmp_path / "q1.cypher"
    path.write_text("MATCH (n) RETURN n;", encoding="utf-8")
    loaded = load_context_query(
        path,
        relative_path=_RELATIVE_PATH,
        logical_query="Q1",
        expected_placeholder_count=0,
        dependency_depth=4,
    )
    assert loaded.effective_text == "MATCH (n) RETURN n;"
    assert loaded.template_hash == loaded.effective_query_hash
    assert loaded.template_hash == hashlib.sha256(path.read_bytes()).hexdigest()
    assert loaded.relative_path == _RELATIVE_PATH
    assert loaded.logical_query == "Q1"


def test_placeholder_substitution_matches_expected_count(tmp_path: Path) -> None:
    path = tmp_path / "q2.cypher"
    path.write_text(f"MATCH (a)-[:R*1..{DEPTH_PLACEHOLDER}]->(b) RETURN a;", encoding="utf-8")
    loaded = load_context_query(
        path,
        relative_path="queries/v1/q2_code_slice.cypher",
        logical_query="Q2",
        expected_placeholder_count=1,
        dependency_depth=7,
    )
    assert loaded.effective_text == "MATCH (a)-[:R*1..7]->(b) RETURN a;"
    assert loaded.template_hash != loaded.effective_query_hash


def test_wrong_placeholder_count_raises(tmp_path: Path) -> None:
    path = tmp_path / "q2.cypher"
    path.write_text(f"MATCH (a)-[:R*1..{DEPTH_PLACEHOLDER}]->(b) RETURN a;", encoding="utf-8")
    with pytest.raises(ContextBuildError):
        load_context_query(
            path,
            relative_path="queries/v1/q2_code_slice.cypher",
            logical_query="Q2",
            expected_placeholder_count=2,
            dependency_depth=4,
        )


def test_zero_occurrences_when_none_expected_is_fine(tmp_path: Path) -> None:
    path = tmp_path / "q1.cypher"
    path.write_text("MATCH (n) RETURN n;", encoding="utf-8")
    loaded = load_context_query(
        path,
        relative_path=_RELATIVE_PATH,
        logical_query="Q1",
        expected_placeholder_count=0,
        dependency_depth=4,
    )
    assert DEPTH_PLACEHOLDER not in loaded.effective_text


def test_does_not_use_general_format_on_curly_braces(tmp_path: Path) -> None:
    # Q3a/Q5b contienen literales de mapa Cypher `{...}`: un str.format()
    # general fallaria/corromperia el texto. Confirma que sobreviven intactos.
    path = tmp_path / "q3a.cypher"
    text = f"RETURN {{col: 1, other: 2}} AS m, 1..{DEPTH_PLACEHOLDER} AS depth;"
    path.write_text(text, encoding="utf-8")
    loaded = load_context_query(
        path,
        relative_path="queries/v1/q3a_parameter_context.cypher",
        logical_query="Q3A",
        expected_placeholder_count=1,
        dependency_depth=4,
    )
    assert loaded.effective_text == "RETURN {col: 1, other: 2} AS m, 1..4 AS depth;"
