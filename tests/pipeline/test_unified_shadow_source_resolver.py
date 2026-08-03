"""Tests del resolutor puro de candidatos fuente (Fase 11 Parte 5,
`pipeline/unified_shadow_source_resolver.py`)."""

from __future__ import annotations

import pytest

from altamira_extractor.pipeline.unified_shadow_source_resolver import (
    SourceResolutionFailureReason,
    SourceResolutionResult,
    resolve_source_candidate,
)

from .unified_candidates_shadow_helpers import (
    CAND_HASH,
    RUN_ID,
    interprocedural_artifact,
    interprocedural_candidate,
    interprocedural_reference,
    v1_artifact,
    v1_candidate,
    v1_reference,
    v2_artifact,
    v2_candidate,
    v2_reference,
)


def test_source_resolution_result_requires_exactly_one_of_hash_or_reason() -> None:
    with pytest.raises(ValueError, match="exactamente uno"):
        SourceResolutionResult()
    with pytest.raises(ValueError, match="exactamente uno"):
        SourceResolutionResult(
            source_candidate_hash=CAND_HASH,
            failure_reason=SourceResolutionFailureReason.UNKNOWN_SOURCE,
        )


def test_resolve_v1_success() -> None:
    candidate = v1_candidate(candidate_id="candidate::1", outcome_code="R001")
    artifact = v1_artifact(candidates=[candidate], run_id=RUN_ID)
    reference = v1_reference(
        source_candidate_id="candidate::1",
        decision_id=candidate.decision_id,
        output_literal="R001",
    )
    result = resolve_source_candidate(
        reference=reference, v1_artifact=artifact, v2_artifact=None, interprocedural_artifact=None
    )
    assert result.is_success
    assert result.source_candidate_hash is not None


def test_resolve_v1_not_found() -> None:
    artifact = v1_artifact(candidates=[], run_id=RUN_ID)
    reference = v1_reference(source_candidate_id="candidate::missing")
    result = resolve_source_candidate(
        reference=reference, v1_artifact=artifact, v2_artifact=None, interprocedural_artifact=None
    )
    assert not result.is_success
    assert result.failure_reason == SourceResolutionFailureReason.SOURCE_CANDIDATE_NOT_FOUND


def test_resolve_v1_identity_mismatch() -> None:
    candidate = v1_candidate(candidate_id="candidate::1", outcome_code="R001")
    artifact = v1_artifact(candidates=[candidate], run_id=RUN_ID)
    reference = v1_reference(source_candidate_id="candidate::1", output_literal="R999")
    result = resolve_source_candidate(
        reference=reference, v1_artifact=artifact, v2_artifact=None, interprocedural_artifact=None
    )
    assert not result.is_success
    assert result.failure_reason == SourceResolutionFailureReason.IDENTITY_MISMATCH


def test_resolve_v1_artifact_absent() -> None:
    reference = v1_reference(source_candidate_id="candidate::1")
    result = resolve_source_candidate(
        reference=reference, v1_artifact=None, v2_artifact=None, interprocedural_artifact=None
    )
    assert not result.is_success
    assert result.failure_reason == SourceResolutionFailureReason.UNKNOWN_SOURCE


def test_resolve_v2_success() -> None:
    candidate = v2_candidate(candidate_id="v2::1", target_variable="WS-X", resolved_literal="R001")
    artifact = v2_artifact(candidates=[candidate], run_id=RUN_ID)
    reference = v2_reference(source_candidate_id="v2::1", target="WS-X", output_literal="R001")
    result = resolve_source_candidate(
        reference=reference, v1_artifact=None, v2_artifact=artifact, interprocedural_artifact=None
    )
    assert result.is_success


def test_resolve_v2_not_found() -> None:
    artifact = v2_artifact(candidates=[], run_id=RUN_ID)
    reference = v2_reference(source_candidate_id="v2::missing")
    result = resolve_source_candidate(
        reference=reference, v1_artifact=None, v2_artifact=artifact, interprocedural_artifact=None
    )
    assert not result.is_success
    assert result.failure_reason == SourceResolutionFailureReason.SOURCE_CANDIDATE_NOT_FOUND


def test_resolve_v2_identity_mismatch() -> None:
    candidate = v2_candidate(candidate_id="v2::1", target_variable="WS-X", resolved_literal="R001")
    artifact = v2_artifact(candidates=[candidate], run_id=RUN_ID)
    reference = v2_reference(source_candidate_id="v2::1", target="WS-Y", output_literal="R001")
    result = resolve_source_candidate(
        reference=reference, v1_artifact=None, v2_artifact=artifact, interprocedural_artifact=None
    )
    assert not result.is_success
    assert result.failure_reason == SourceResolutionFailureReason.IDENTITY_MISMATCH


def test_resolve_v2_artifact_absent() -> None:
    reference = v2_reference(source_candidate_id="v2::1")
    result = resolve_source_candidate(
        reference=reference, v1_artifact=None, v2_artifact=None, interprocedural_artifact=None
    )
    assert not result.is_success
    assert result.failure_reason == SourceResolutionFailureReason.UNKNOWN_SOURCE


def test_resolve_interprocedural_success() -> None:
    candidate = interprocedural_candidate(
        candidate_id="ipr::1", target="WS-X", output_literal="R001"
    )
    artifact = interprocedural_artifact(candidates=[candidate], run_id=RUN_ID)
    reference = interprocedural_reference(
        source_candidate_id="ipr::1", target="WS-X", output_literal="R001"
    )
    result = resolve_source_candidate(
        reference=reference,
        v1_artifact=None,
        v2_artifact=None,
        interprocedural_artifact=artifact,
    )
    assert result.is_success


def test_resolve_interprocedural_not_found() -> None:
    artifact = interprocedural_artifact(candidates=[], run_id=RUN_ID)
    reference = interprocedural_reference(source_candidate_id="ipr::missing")
    result = resolve_source_candidate(
        reference=reference,
        v1_artifact=None,
        v2_artifact=None,
        interprocedural_artifact=artifact,
    )
    assert not result.is_success
    assert result.failure_reason == SourceResolutionFailureReason.SOURCE_CANDIDATE_NOT_FOUND


def test_resolve_interprocedural_identity_mismatch() -> None:
    candidate = interprocedural_candidate(
        candidate_id="ipr::1", target="WS-X", output_literal="R001"
    )
    artifact = interprocedural_artifact(candidates=[candidate], run_id=RUN_ID)
    reference = interprocedural_reference(
        source_candidate_id="ipr::1", target="WS-X", output_literal="R999"
    )
    result = resolve_source_candidate(
        reference=reference,
        v1_artifact=None,
        v2_artifact=None,
        interprocedural_artifact=artifact,
    )
    assert not result.is_success
    assert result.failure_reason == SourceResolutionFailureReason.IDENTITY_MISMATCH


def test_resolve_interprocedural_artifact_absent() -> None:
    reference = interprocedural_reference(source_candidate_id="ipr::1")
    result = resolve_source_candidate(
        reference=reference, v1_artifact=None, v2_artifact=None, interprocedural_artifact=None
    )
    assert not result.is_success
    assert result.failure_reason == SourceResolutionFailureReason.UNKNOWN_SOURCE


def test_resolver_never_mutates_inputs() -> None:
    candidate = v2_candidate(candidate_id="v2::1", target_variable="WS-X", resolved_literal="R001")
    artifact = v2_artifact(candidates=[candidate], run_id=RUN_ID)
    before = artifact.to_stable_json()
    reference = v2_reference(source_candidate_id="v2::1", target="WS-X", output_literal="R001")
    resolve_source_candidate(
        reference=reference, v1_artifact=None, v2_artifact=artifact, interprocedural_artifact=None
    )
    assert artifact.to_stable_json() == before
