"""Tests del adaptador puro de baseline V1 (Fase 11 Parte 7,
`pipeline/unified_shadow_baseline_adapter.py`)."""

from __future__ import annotations

import pytest

from altamira_extractor.pipeline.errors import UnifiedCandidatesShadowError
from altamira_extractor.pipeline.unified_shadow_baseline_adapter import (
    adapt_v1_baseline_candidates,
    baseline_reference_id_for,
)

from .unified_candidates_shadow_helpers import RUN_ID, v1_artifact, v1_candidate


def test_adapt_empty_v1_returns_empty_list() -> None:
    artifact = v1_artifact(candidates=[], run_id=RUN_ID)
    result = adapt_v1_baseline_candidates(artifact, source_artifact_hash="a" * 64)
    assert result == []


def test_adapt_v1_candidate_maps_fields() -> None:
    candidate = v1_candidate(
        candidate_id="candidate::1", program="CALLER", paragraph="MAIN", outcome_code="R001"
    )
    artifact = v1_artifact(candidates=[candidate], run_id=RUN_ID)
    [reference] = adapt_v1_baseline_candidates(artifact, source_artifact_hash="a" * 64)
    assert reference.source_candidate_id == "candidate::1"
    assert reference.program == "CALLER"
    assert reference.paragraph == "MAIN"
    assert reference.output_literal == "R001"
    assert reference.decision_id == candidate.decision_id
    assert reference.source_artifact_hash == "a" * 64


def test_baseline_reference_id_is_deterministic() -> None:
    assert baseline_reference_id_for("candidate::1") == baseline_reference_id_for("candidate::1")
    assert baseline_reference_id_for("candidate::1") != baseline_reference_id_for("candidate::2")


def test_adapt_result_sorted_by_baseline_reference_id() -> None:
    c1 = v1_candidate(candidate_id="candidate::zzz")
    c2 = v1_candidate(candidate_id="candidate::aaa")
    artifact = v1_artifact(candidates=sorted([c1, c2], key=lambda c: c.candidate_id), run_id=RUN_ID)
    result = adapt_v1_baseline_candidates(artifact, source_artifact_hash="a" * 64)
    ids = [r.baseline_reference_id for r in result]
    assert ids == sorted(ids)


def test_adapt_every_v1_candidate_appears_regardless_of_plan() -> None:
    """El baseline es independiente del plan: el adaptador ni siquiera
    recibe un plan como argumento."""
    candidates = [v1_candidate(candidate_id=f"candidate::{i}") for i in range(5)]
    artifact = v1_artifact(candidates=candidates, run_id=RUN_ID)
    result = adapt_v1_baseline_candidates(artifact, source_artifact_hash="a" * 64)
    assert len(result) == 5
    assert {r.source_candidate_id for r in result} == {c.candidate_id for c in candidates}


def test_adapt_malformed_paragraph_id_raises_instead_of_fabricating() -> None:
    candidate = v1_candidate(candidate_id="candidate::1").model_copy(
        update={"paragraph_id": "not-a-valid-paragraph-id"}
    )
    artifact = v1_artifact(candidates=[candidate], run_id=RUN_ID)
    with pytest.raises(UnifiedCandidatesShadowError):
        adapt_v1_baseline_candidates(artifact, source_artifact_hash="a" * 64)


def test_adapter_never_mutates_v1_artifact() -> None:
    candidate = v1_candidate(candidate_id="candidate::1")
    artifact = v1_artifact(candidates=[candidate], run_id=RUN_ID)
    before = artifact.to_stable_json()
    adapt_v1_baseline_candidates(artifact, source_artifact_hash="a" * 64)
    assert artifact.to_stable_json() == before
