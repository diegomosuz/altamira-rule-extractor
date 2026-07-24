"""Tests de ContextDirectoryManifest (artifacts/07-context/context-manifest.json,
Prompt 10b). Sin JSON Schema propio (igual que InvariantArtifact/CandidateArtifact)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts import ContextDirectoryManifest, ContextRecord, QueryRecord

_VALID_HASH = "a" * 64

_LOGICAL_QUERIES = ("Q1", "Q2", "Q3A", "Q3B", "Q4", "Q5A", "Q5B", "Q6", "Q7")


def _query_records() -> list[QueryRecord]:
    return [
        QueryRecord(
            logical_query=name,  # type: ignore[arg-type]
            relative_path=f"queries/v1/{name.lower()}.cypher",
            template_hash=_VALID_HASH,
            effective_query_hash=_VALID_HASH,
        )
        for name in _LOGICAL_QUERIES
    ]


def _manifest(**overrides: object) -> ContextDirectoryManifest:
    defaults: dict[str, object] = {
        "run_id": "run-1",
        "source_package_hash": _VALID_HASH,
        "semantic_graph_hash": _VALID_HASH,
        "candidate_artifact_hash": _VALID_HASH,
        "q0_query_hash": _VALID_HASH,
        "invariants_query_hash": _VALID_HASH,
        "context_schema_hash": _VALID_HASH,
        "dependency_depth": 4,
        "max_code_slice_paragraphs": 200,
        "max_transactional_tables": 100,
        "max_parameter_entries_per_context": 1000,
        "query_records": _query_records(),
        "context_records": [],
        "context_count": 0,
        "warnings": [],
    }
    defaults.update(overrides)
    return ContextDirectoryManifest(**defaults)  # type: ignore[arg-type]


def test_manifest_with_no_candidates_is_valid() -> None:
    manifest = _manifest()
    assert manifest.context_count == 0
    assert manifest.context_records == []
    assert len(manifest.query_records) == 9


def test_manifest_round_trips() -> None:
    manifest = _manifest()
    restored = ContextDirectoryManifest.model_validate_json(manifest.to_stable_json())
    assert restored == manifest


def test_manifest_with_context_records_is_valid() -> None:
    records = [
        ContextRecord(
            candidate_id="candidate::det::1.0::" + _VALID_HASH + "::dec-1",
            paragraph_id="para-1",
            decision_id="dec-1",
            relative_filename="aa" * 32 + ".json",
            context_hash=_VALID_HASH,
        )
    ]
    manifest = _manifest(context_records=records, context_count=1)
    assert manifest.context_count == 1


def test_query_records_must_contain_exactly_the_nine_logical_names() -> None:
    incomplete = _query_records()[:-1]  # falta Q7
    with pytest.raises(ValidationError):
        _manifest(query_records=incomplete)


def test_query_records_rejects_duplicate_logical_query() -> None:
    records = _query_records()
    records.append(records[0])
    with pytest.raises(ValidationError):
        _manifest(query_records=records)


def test_query_records_must_be_sorted() -> None:
    records = list(reversed(_query_records()))
    with pytest.raises(ValidationError):
        _manifest(query_records=records)


def test_context_records_must_be_sorted_by_candidate_id() -> None:
    first = ContextRecord(
        candidate_id="candidate::det::1.0::" + _VALID_HASH + "::dec-2",
        paragraph_id="para-2",
        decision_id="dec-2",
        relative_filename="aa" * 32 + ".json",
        context_hash=_VALID_HASH,
    )
    second = ContextRecord(
        candidate_id="candidate::det::1.0::" + _VALID_HASH + "::dec-1",
        paragraph_id="para-1",
        decision_id="dec-1",
        relative_filename="bb" * 32 + ".json",
        context_hash=_VALID_HASH,
    )
    with pytest.raises(ValidationError):
        _manifest(context_records=[first, second], context_count=2)


def test_context_records_rejects_duplicate_candidate_id() -> None:
    record = ContextRecord(
        candidate_id="candidate::det::1.0::" + _VALID_HASH + "::dec-1",
        paragraph_id="para-1",
        decision_id="dec-1",
        relative_filename="aa" * 32 + ".json",
        context_hash=_VALID_HASH,
    )
    other = record.model_copy(update={"relative_filename": "bb" * 32 + ".json"})
    with pytest.raises(ValidationError):
        _manifest(context_records=[record, other], context_count=2)


def test_context_records_rejects_duplicate_relative_filename() -> None:
    record = ContextRecord(
        candidate_id="candidate::det::1.0::" + _VALID_HASH + "::dec-1",
        paragraph_id="para-1",
        decision_id="dec-1",
        relative_filename="aa" * 32 + ".json",
        context_hash=_VALID_HASH,
    )
    other = ContextRecord(
        candidate_id="candidate::det::1.0::" + _VALID_HASH + "::dec-2",
        paragraph_id="para-2",
        decision_id="dec-2",
        relative_filename="aa" * 32 + ".json",
        context_hash=_VALID_HASH,
    )
    with pytest.raises(ValidationError):
        _manifest(context_records=[record, other], context_count=2)


def test_context_count_must_match_len_of_context_records() -> None:
    with pytest.raises(ValidationError):
        _manifest(context_records=[], context_count=1)


def test_warnings_must_be_sorted_and_deduplicated() -> None:
    with pytest.raises(ValidationError):
        _manifest(warnings=["z", "a"])
    with pytest.raises(ValidationError):
        _manifest(warnings=["a", "a"])


def test_dependency_depth_out_of_range_is_invalid() -> None:
    with pytest.raises(ValidationError):
        _manifest(dependency_depth=0)
    with pytest.raises(ValidationError):
        _manifest(dependency_depth=11)
