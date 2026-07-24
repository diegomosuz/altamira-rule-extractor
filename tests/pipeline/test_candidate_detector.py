"""Tests de CandidateDetector (Q0, Prompt 10a) con un repositorio Neo4j
falso minimo (solo expone `run_candidate_query`; el comportamiento real
de Neo4j esta cubierto en test_neo4j_repository.py y
tests/neo4j_integration/).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from altamira_extractor.contracts.candidate import RuleCandidate
from altamira_extractor.pipeline.candidate_detector import (
    DETECTOR_ID,
    DETECTOR_SCORE,
    DETECTOR_VERSION,
    candidate_id_for,
    detect_candidates,
    load_q0_query,
)
from altamira_extractor.pipeline.errors import CandidateDetectionError

_HASH_A = "a" * 64


class _FakeCandidateRepository:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.call_count = 0
        self.received_kwargs: dict[str, Any] | None = None

    def run_candidate_query(
        self, cypher_text: str, *, package_hash: str
    ) -> list[dict[str, Any]]:
        self.call_count += 1
        self.received_kwargs = {"cypher_text": cypher_text, "package_hash": package_hash}
        return self.rows


def _row(**overrides: object) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "paragraph_id": "program::AR::op::PROG::1::abc123::paragraph::MAIN",
        "paragraph_name": "MAIN",
        "decision_id": "program::AR::op::PROG::1::abc123::paragraph::MAIN::decision::10::1",
        "condition": "WS-COD-RESULT = 'R001'",
        "outcome": "R001",
        "rule_type": None,
        "line_start": 10,
        "source_file": "01-codigo/cobol/PROG.cbl",
    }
    defaults.update(overrides)
    return defaults


# --- candidate_id_for ---


def test_candidate_id_includes_detector_id_version_package_hash_and_decision_id() -> None:
    candidate_id = candidate_id_for(source_package_hash=_HASH_A, decision_id="dec-1")
    assert candidate_id == f"candidate::{DETECTOR_ID}::{DETECTOR_VERSION}::{_HASH_A}::dec-1"


def test_candidate_id_differs_across_packages_for_the_same_decision_id() -> None:
    other_hash = "b" * 64
    first = candidate_id_for(source_package_hash=_HASH_A, decision_id="dec-1")
    second = candidate_id_for(source_package_hash=other_hash, decision_id="dec-1")
    assert first != second


# --- load_q0_query ---


def test_load_q0_query_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(CandidateDetectionError):
        load_q0_query(tmp_path / "no-existe.cypher")


def test_load_q0_query_empty_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "q0.cypher"
    path.write_text("   \n  ", encoding="utf-8")
    with pytest.raises(CandidateDetectionError):
        load_q0_query(path)


def test_load_q0_query_hash_is_deterministic_and_sensitive_to_content(tmp_path: Path) -> None:
    import hashlib

    path = tmp_path / "q0.cypher"
    path.write_text("MATCH (n) RETURN n;", encoding="utf-8")
    text, digest = load_q0_query(path)

    assert text == "MATCH (n) RETURN n;"
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()

    path.write_text("MATCH (n) RETURN n LIMIT 1;", encoding="utf-8")
    _, second_digest = load_q0_query(path)
    assert second_digest != digest


# --- detect_candidates: caso feliz, nulos, evidencia conservada ---


def test_single_row_produces_one_candidate_with_expected_fields() -> None:
    repository = _FakeCandidateRepository([_row()])

    candidates = detect_candidates(repository, q0_cypher_text="MATCH ...", package_hash=_HASH_A)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.candidate_id == candidate_id_for(
        source_package_hash=_HASH_A, decision_id=_row()["decision_id"]
    )
    assert candidate.paragraph_id == _row()["paragraph_id"]
    assert candidate.paragraph_name == "MAIN"
    assert candidate.decision_id == _row()["decision_id"]
    assert candidate.detector_id == DETECTOR_ID
    assert candidate.detector_version == DETECTOR_VERSION
    assert candidate.detector_score == DETECTOR_SCORE
    assert candidate.condition == "WS-COD-RESULT = 'R001'"
    assert candidate.outcome_code == "R001"
    assert candidate.rule_type is None
    assert candidate.line_start == 10
    assert candidate.source_file == "01-codigo/cobol/PROG.cbl"
    assert candidate.source_package_hash == _HASH_A


def test_nullable_outcome_and_rule_type_are_preserved_as_none() -> None:
    repository = _FakeCandidateRepository([_row(outcome=None, rule_type=None)])

    candidates = detect_candidates(repository, q0_cypher_text="MATCH ...", package_hash=_HASH_A)

    assert candidates[0].outcome_code is None
    assert candidates[0].rule_type is None


def test_no_rows_produces_empty_candidate_list() -> None:
    # Cubre, a nivel de mapeo, los tres casos que Q0 ya excluye en Cypher
    # (DataItem sin semantic_tag='return_code', Decision sin LEADS_TO,
    # LEADS_TO hacia un DataItem con otro tag): ninguno produce fila, asi
    # que CandidateDetector simplemente recibe una lista vacia. La
    # verificacion de que Q0 realmente filtra cada caso por separado
    # vive en tests/neo4j_integration/ (requiere Neo4j real).
    repository = _FakeCandidateRepository([])

    candidates = detect_candidates(repository, q0_cypher_text="MATCH ...", package_hash=_HASH_A)

    assert candidates == []


def test_run_candidate_query_is_called_exactly_once_regardless_of_row_count() -> None:
    rows = [
        _row(decision_id=f"dec-{i}", paragraph_id=f"para-{i}", paragraph_name=f"PARA-{i}")
        for i in range(5)
    ]
    repository = _FakeCandidateRepository(rows)

    detect_candidates(repository, q0_cypher_text="MATCH ...", package_hash=_HASH_A)

    assert repository.call_count == 1
    assert repository.received_kwargs == {
        "cypher_text": "MATCH ...",
        "package_hash": _HASH_A,
    }


# --- multiplicidad: LEADS_TO/Decisions/Paragraphs ---


def test_multiple_leads_to_from_the_same_decision_consolidate_into_one_candidate() -> None:
    # Q0 no expone el sink DataItem: dos LEADS_TO desde la misma Decision
    # hacia distintos return codes producen filas identicas.
    repository = _FakeCandidateRepository([_row(), _row()])

    candidates = detect_candidates(repository, q0_cypher_text="MATCH ...", package_hash=_HASH_A)

    assert len(candidates) == 1


def test_multiple_decisions_in_the_same_paragraph_produce_separate_candidates() -> None:
    rows = [
        _row(decision_id="dec-1"),
        _row(decision_id="dec-2"),
    ]
    repository = _FakeCandidateRepository(rows)

    candidates = detect_candidates(repository, q0_cypher_text="MATCH ...", package_hash=_HASH_A)

    assert len(candidates) == 2
    assert {c.decision_id for c in candidates} == {"dec-1", "dec-2"}
    assert candidates[0].candidate_id != candidates[1].candidate_id


def test_multiple_paragraphs_produce_separate_candidates() -> None:
    rows = [
        _row(paragraph_id="para-1", decision_id="dec-1"),
        _row(paragraph_id="para-2", decision_id="dec-2"),
    ]
    repository = _FakeCandidateRepository(rows)

    candidates = detect_candidates(repository, q0_cypher_text="MATCH ...", package_hash=_HASH_A)

    assert len(candidates) == 2
    assert {c.paragraph_id for c in candidates} == {"para-1", "para-2"}


def test_exact_duplicate_rows_consolidate_deterministically() -> None:
    row = _row()
    repository = _FakeCandidateRepository([dict(row), dict(row), dict(row)])

    candidates = detect_candidates(repository, q0_cypher_text="MATCH ...", package_hash=_HASH_A)

    assert len(candidates) == 1


def test_inconsistent_rows_for_the_same_decision_are_fatal() -> None:
    rows = [_row(condition="A"), _row(condition="B")]
    repository = _FakeCandidateRepository(rows)

    with pytest.raises(CandidateDetectionError):
        detect_candidates(repository, q0_cypher_text="MATCH ...", package_hash=_HASH_A)


def test_candidates_are_sorted_deterministically_by_candidate_id() -> None:
    rows = [_row(decision_id="dec-z"), _row(decision_id="dec-a")]
    repository = _FakeCandidateRepository(rows)

    candidates = detect_candidates(repository, q0_cypher_text="MATCH ...", package_hash=_HASH_A)

    assert [c.candidate_id for c in candidates] == sorted(c.candidate_id for c in candidates)


def test_row_missing_required_column_is_fatal() -> None:
    row = _row()
    del row["source_file"]
    repository = _FakeCandidateRepository([row])

    with pytest.raises(CandidateDetectionError):
        detect_candidates(repository, q0_cypher_text="MATCH ...", package_hash=_HASH_A)


def test_row_with_invalid_line_start_is_fatal() -> None:
    repository = _FakeCandidateRepository([_row(line_start=0)])  # RuleCandidate exige >= 1

    with pytest.raises(CandidateDetectionError):
        detect_candidates(repository, q0_cypher_text="MATCH ...", package_hash=_HASH_A)


def test_detector_score_is_always_the_constant_regardless_of_row_content() -> None:
    repository = _FakeCandidateRepository([_row(), _row(decision_id="dec-2")])

    candidates = detect_candidates(repository, q0_cypher_text="MATCH ...", package_hash=_HASH_A)

    assert all(candidate.detector_score == 1.0 for candidate in candidates)
    # detector_score nunca se deriva de semantic_confidence: ese campo ni
    # siquiera esta disponible en las filas de Q0 (Q0 no toca DataItem),
    # ni existe en absoluto en RuleCandidate.
    assert "semantic_confidence" not in RuleCandidate.model_fields


def test_candidate_status_is_always_detected_candidate() -> None:
    from altamira_extractor.contracts.enums import CandidateStatus

    repository = _FakeCandidateRepository([_row()])
    candidates = detect_candidates(repository, q0_cypher_text="MATCH ...", package_hash=_HASH_A)
    assert candidates[0].status == CandidateStatus.DETECTED_CANDIDATE


def test_no_source_text_is_ever_copied() -> None:
    # RuleCandidate no tiene un campo source_text: confirma que el mapeo
    # no lo agrega aunque una fila (hipoteticamente) lo incluyera.
    row = _row()
    row["source_text"] = "IDENTIFICATION DIVISION."
    repository = _FakeCandidateRepository([row])

    candidates = detect_candidates(repository, q0_cypher_text="MATCH ...", package_hash=_HASH_A)

    assert not hasattr(candidates[0], "source_text")
    assert "source_text" not in RuleCandidate.model_fields
