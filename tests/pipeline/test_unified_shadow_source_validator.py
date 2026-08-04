"""Tests del validador PURO de integridad de fuentes (Fase 12 Parte 4,
`feat/unified-shadow-differential-validation`)."""

from __future__ import annotations

from altamira_extractor.contracts.candidate_promotion_assessment import SourceAvailability
from altamira_extractor.contracts.unified_shadow_validation import UnifiedShadowValidationIssueCode
from altamira_extractor.pipeline.unified_shadow_source_validator import (
    LoadedSource,
    validate_source_integrity,
)

from ._unified_shadow_validation_fixtures import HASH, golden_path, stable_hash

Code = UnifiedShadowValidationIssueCode


def _base_kwargs(gp: object) -> dict[str, object]:
    return dict(
        run_id=gp.v1.run_id,  # type: ignore[attr-defined]
        source_package_hash=HASH,
        v1=LoadedSource(artifact=gp.v1, availability=SourceAvailability.AVAILABLE),  # type: ignore[attr-defined]
        v2=LoadedSource(artifact=None, availability=SourceAvailability.NOT_AVAILABLE),
        interprocedural=LoadedSource(artifact=None, availability=SourceAvailability.NOT_AVAILABLE),
        assessment=LoadedSource(artifact=gp.assessment, availability=SourceAvailability.AVAILABLE),  # type: ignore[attr-defined]
        review_package=LoadedSource(
            artifact=gp.review_package,
            availability=SourceAvailability.AVAILABLE,  # type: ignore[attr-defined]
        ),
        plan=LoadedSource(artifact=gp.plan, availability=SourceAvailability.AVAILABLE),  # type: ignore[attr-defined]
        unified_shadow=LoadedSource(
            artifact=gp.unified_shadow,
            availability=SourceAvailability.AVAILABLE,  # type: ignore[attr-defined]
        ),
        candidate_v1_artifact_hash=HASH,
        v2_artifact_hash=None,
        interprocedural_artifact_hash=None,
        assessment_artifact_hash=HASH,
        review_package_hash=HASH,
        promotion_plan_hash=HASH,
        unified_candidates_shadow_hash=stable_hash(gp.unified_shadow),  # type: ignore[attr-defined]
    )


def test_all_sources_available_and_hashes_consistent_passes() -> None:
    gp = golden_path()
    result = validate_source_integrity(**_base_kwargs(gp))
    assert result.gate_passed is True
    assert result.required_source_missing is False
    assert result.findings == ()


def test_v1_not_available_is_required_source_missing() -> None:
    gp = golden_path()
    kwargs = _base_kwargs(gp)
    kwargs["v1"] = LoadedSource(artifact=None, availability=SourceAvailability.NOT_AVAILABLE)
    result = validate_source_integrity(**kwargs)
    assert result.required_source_missing is True
    assert any(f.code == Code.SOURCE_ARTIFACT_MISSING for f in result.findings)


def test_v1_invalid_is_required_source_missing_with_invalid_code() -> None:
    gp = golden_path()
    kwargs = _base_kwargs(gp)
    kwargs["v1"] = LoadedSource(artifact=None, availability=SourceAvailability.INVALID)
    result = validate_source_integrity(**kwargs)
    assert result.required_source_missing is True
    assert any(f.code == Code.SOURCE_ARTIFACT_INVALID for f in result.findings)


def test_unified_shadow_not_available_is_required_source_missing() -> None:
    gp = golden_path()
    kwargs = _base_kwargs(gp)
    kwargs["unified_shadow"] = LoadedSource(
        artifact=None, availability=SourceAvailability.NOT_AVAILABLE
    )
    result = validate_source_integrity(**kwargs)
    assert result.required_source_missing is True


def test_v2_not_required_when_unified_shadow_does_not_declare_it() -> None:
    gp = golden_path()  # golden_path never declares v2_artifact_hash
    result = validate_source_integrity(**_base_kwargs(gp))
    assert result.required_source_missing is False
    assert result.gate_passed is True


def test_v2_required_when_unified_shadow_declares_its_hash() -> None:
    gp = golden_path()
    unified_shadow_with_v2 = gp.unified_shadow.model_copy(update={"v2_artifact_hash": HASH})
    kwargs = _base_kwargs(gp)
    kwargs["unified_shadow"] = LoadedSource(
        artifact=unified_shadow_with_v2, availability=SourceAvailability.AVAILABLE
    )
    kwargs["v2_artifact_hash"] = HASH
    kwargs["unified_candidates_shadow_hash"] = stable_hash(unified_shadow_with_v2)
    result = validate_source_integrity(**kwargs)
    assert result.required_source_missing is True
    assert any(f.code == Code.SOURCE_ARTIFACT_MISSING for f in result.findings)


def test_run_id_mismatch_produces_source_hash_mismatch() -> None:
    gp = golden_path()
    kwargs = _base_kwargs(gp)
    kwargs["run_id"] = "20260101T000000000000-bbbbbbbb"
    result = validate_source_integrity(**kwargs)
    assert result.required_source_missing is False
    assert result.gate_passed is False
    assert any(f.code == Code.SOURCE_HASH_MISMATCH for f in result.findings)


def test_review_package_hash_chain_mismatch_detected() -> None:
    gp = golden_path()
    kwargs = _base_kwargs(gp)
    kwargs["review_package_hash"] = "b" * 64
    result = validate_source_integrity(**kwargs)
    assert result.gate_passed is False
    assert any(f.code == Code.SOURCE_HASH_MISMATCH for f in result.findings)


def test_stale_v1_hash_in_assessment_detected() -> None:
    gp = golden_path()
    kwargs = _base_kwargs(gp)
    kwargs["candidate_v1_artifact_hash"] = "c" * 64
    result = validate_source_integrity(**kwargs)
    assert result.gate_passed is False
    assert any(f.code == Code.SOURCE_HASH_MISMATCH for f in result.findings)


def test_unified_artifact_hash_mismatch_detected_as_dedicated_finding() -> None:
    gp = golden_path()
    kwargs = _base_kwargs(gp)
    kwargs["unified_candidates_shadow_hash"] = "d" * 64
    result = validate_source_integrity(**kwargs)
    assert result.required_source_missing is False
    assert result.gate_passed is False
    codes = {f.code for f in result.findings}
    assert Code.UNIFIED_ARTIFACT_HASH_MISMATCH in codes
    assert Code.SOURCE_HASH_MISMATCH not in codes
