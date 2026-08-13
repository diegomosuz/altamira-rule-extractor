"""Tests de `ui/presentation.py::program_name_from_source_file`."""

from __future__ import annotations

from altamira_extractor.ui.presentation import program_name_from_source_file


def test_program_name_from_source_file_extracts_stem() -> None:
    assert program_name_from_source_file("01-codigo/cobol/CLEGAR01.cbl") == "CLEGAR01"


def test_program_name_from_source_file_falls_back_to_full_value_on_empty_string() -> None:
    assert program_name_from_source_file("") == ""


def test_program_name_from_source_file_none_renders_honest_placeholder() -> None:
    """Fase 15B4-CANDIDATE-QUALITY-5A: `source_file=None` (RuleCandidate
    de un programa con COPY) debe mostrarse como un placeholder honesto
    en la UI -- nunca el texto literal "None"."""
    assert program_name_from_source_file(None) == "(origen no determinable)"
