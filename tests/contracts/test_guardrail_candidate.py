"""Tests de GuardrailCandidateArtifact / RepairAttemptRecord
(artifacts/09-guardrails/{hash}.json, Prompt 12).

GuardrailReport (Prompt 2) NO se modifica: estos tests solo cubren el
wrapper nuevo. `final_rule_draft` es SIEMPRE una copia nueva del ultimo
RuleDraft estructuralmente valido con `evidence_validation_status`
actualizado deterministicamente al veredicto real (EVIDENCE_VALIDATED o
REJECTED) — nunca PENDING; `artifacts/08-rule-drafts/` (fuera del
alcance de este contrato) es lo que permanece PENDING e inmutable."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts import (
    EvidenceValidationStatus,
    GuardrailCandidateArtifact,
    GuardrailReport,
    GuardrailVerdict,
    GuardrailViolation,
    RepairAttemptRecord,
    RuleDraft,
    Severity,
)

_VALID_HASH = "a" * 64
_OTHER_HASH = "b" * 64
_NOW = datetime(2026, 7, 22, 12, 0, 0, tzinfo=UTC)


def _hash(draft: RuleDraft) -> str:
    return hashlib.sha256(draft.to_stable_json().encode("utf-8")).hexdigest()


def _pending(draft: RuleDraft) -> RuleDraft:
    return draft.model_copy(update={"evidence_validation_status": EvidenceValidationStatus.PENDING})


def _validated(draft: RuleDraft) -> RuleDraft:
    return draft.model_copy(
        update={"evidence_validation_status": EvidenceValidationStatus.EVIDENCE_VALIDATED}
    )


def _rejected(draft: RuleDraft) -> RuleDraft:
    return draft.model_copy(
        update={"evidence_validation_status": EvidenceValidationStatus.REJECTED}
    )


def _report(**overrides: object) -> GuardrailReport:
    defaults: dict[str, object] = {
        "candidate_id": "cand-1",
        "verdict": GuardrailVerdict.EVIDENCE_VALIDATED,
        "violations": [],
        "repair_attempts": 0,
        "evaluated_at": _NOW,
        "source_package_hash": _VALID_HASH,
    }
    defaults.update(overrides)
    return GuardrailReport(**defaults)  # type: ignore[arg-type]


def _artifact(valid_rule_draft: RuleDraft, **overrides: object) -> GuardrailCandidateArtifact:
    """Construccion coherente por defecto: sin reparaciones, EVIDENCE_VALIDATED,
    con todos los hashes reales (nunca placeholders arbitrarios) para que
    los tests que SI quieren un fallo puedan romper exactamente un
    invariante a la vez."""
    final_draft = _validated(valid_rule_draft)
    defaults: dict[str, object] = {
        "candidate_id": "cand-1",
        "source_package_hash": _VALID_HASH,
        "context_hash": _VALID_HASH,
        "initial_rule_draft_hash": _hash(_pending(valid_rule_draft)),
        "final_rule_draft_hash": _hash(final_draft),
        "final_rule_draft": final_draft,
        "guardrail_report": _report(),
        "repair_history": [],
        "warnings": [],
    }
    defaults.update(overrides)
    return GuardrailCandidateArtifact(**defaults)  # type: ignore[arg-type]


def _repair_attempt(**overrides: object) -> RepairAttemptRecord:
    defaults: dict[str, object] = {
        "attempt_number": 1,
        "structurally_valid": True,
        "response_hash": _VALID_HASH,
        "produced_rule_draft_hash": _OTHER_HASH,
        "error_count_before": 2,
        "error_count_after": 0,
        "warning_count_after": 0,
    }
    defaults.update(overrides)
    return RepairAttemptRecord(**defaults)  # type: ignore[arg-type]


# --- RepairAttemptRecord ---


def test_valid_repair_attempt_structurally_valid() -> None:
    attempt = _repair_attempt()
    assert attempt.structurally_valid is True


def test_valid_repair_attempt_structurally_invalid() -> None:
    attempt = RepairAttemptRecord(
        attempt_number=1,
        structurally_valid=False,
        response_hash=_VALID_HASH,
        produced_rule_draft_hash=None,
        failure_code="structural_validation_failed",
        failure_summary="el payload no valida contra RuleDraft",
        error_count_before=2,
        error_count_after=None,
        warning_count_after=None,
    )
    assert attempt.produced_rule_draft_hash is None


def test_structurally_valid_requires_produced_rule_draft_hash() -> None:
    with pytest.raises(ValidationError):
        _repair_attempt(produced_rule_draft_hash=None)


def test_structurally_valid_cannot_have_failure_fields() -> None:
    with pytest.raises(ValidationError):
        _repair_attempt(failure_code="x")
    with pytest.raises(ValidationError):
        _repair_attempt(failure_summary="x")


def test_structurally_invalid_cannot_have_produced_hash() -> None:
    with pytest.raises(ValidationError):
        RepairAttemptRecord(
            attempt_number=1,
            structurally_valid=False,
            response_hash=_VALID_HASH,
            produced_rule_draft_hash=_OTHER_HASH,
            error_count_before=1,
        )


def test_structurally_invalid_cannot_have_post_evaluation_counts() -> None:
    with pytest.raises(ValidationError):
        RepairAttemptRecord(
            attempt_number=1,
            structurally_valid=False,
            response_hash=_VALID_HASH,
            error_count_before=1,
            error_count_after=0,
        )


def test_failure_summary_is_bounded() -> None:
    with pytest.raises(ValidationError):
        RepairAttemptRecord(
            attempt_number=1,
            structurally_valid=False,
            response_hash=_VALID_HASH,
            failure_code="x",
            failure_summary="a" * 501,
            error_count_before=1,
        )


def test_attempt_number_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        _repair_attempt(attempt_number=0)


# --- GuardrailCandidateArtifact: casos validos ---


def test_valid_artifact_without_repairs(valid_rule_draft: RuleDraft) -> None:
    artifact = _artifact(valid_rule_draft)
    assert artifact.repair_history == []
    assert artifact.guardrail_report.verdict == GuardrailVerdict.EVIDENCE_VALIDATED
    assert artifact.final_rule_draft.evidence_validation_status == (
        EvidenceValidationStatus.EVIDENCE_VALIDATED
    )


def test_artifact_round_trips(valid_rule_draft: RuleDraft) -> None:
    artifact = _artifact(valid_rule_draft)
    restored = GuardrailCandidateArtifact.model_validate_json(artifact.to_stable_json())
    assert restored == artifact


def test_verdict_rejected_with_matching_rejected_status_is_valid(
    valid_rule_draft: RuleDraft,
) -> None:
    # Nueva regla: el contrato PUEDE representar un veredicto REJECTED de
    # forma coherente (aunque GUARDRAILS_APPLIED nunca lo persista en
    # disco: se construye internamente como prueba de consistencia).
    rejected_draft = _rejected(valid_rule_draft)
    report = _report(
        verdict=GuardrailVerdict.REJECTED,
        violations=[
            GuardrailViolation(
                violation_id="v1", rule="x", field=None, message="m", severity=Severity.ERROR
            )
        ],
        repair_attempts=0,
    )
    artifact = GuardrailCandidateArtifact(
        candidate_id="cand-1",
        source_package_hash=_VALID_HASH,
        context_hash=_VALID_HASH,
        initial_rule_draft_hash=_hash(_pending(valid_rule_draft)),
        final_rule_draft_hash=_hash(rejected_draft),
        final_rule_draft=rejected_draft,
        guardrail_report=report,
        repair_history=[],
        warnings=[],
    )
    assert artifact.guardrail_report.verdict == GuardrailVerdict.REJECTED
    assert artifact.final_rule_draft.evidence_validation_status == EvidenceValidationStatus.REJECTED


def test_repair_produces_distinct_final_draft_with_new_content(valid_rule_draft: RuleDraft) -> None:
    repaired_draft = valid_rule_draft.model_copy(update={"title": "Titulo reparado"})
    attempt = _repair_attempt(produced_rule_draft_hash=_hash(_pending(repaired_draft)))
    final_draft = _validated(repaired_draft)
    artifact = GuardrailCandidateArtifact(
        candidate_id="cand-1",
        source_package_hash=_VALID_HASH,
        context_hash=_VALID_HASH,
        initial_rule_draft_hash=_hash(_pending(valid_rule_draft)),
        final_rule_draft_hash=_hash(final_draft),
        final_rule_draft=final_draft,
        guardrail_report=_report(repair_attempts=1),
        repair_history=[attempt],
        warnings=[],
    )
    assert artifact.final_rule_draft.title == "Titulo reparado"
    assert artifact.final_rule_draft.evidence_validation_status == (
        EvidenceValidationStatus.EVIDENCE_VALIDATED
    )
    assert artifact.final_rule_draft_hash != artifact.initial_rule_draft_hash


# --- GuardrailCandidateArtifact: casos invalidos ---


def test_verdict_evidence_validated_rejects_pending_final_draft(
    valid_rule_draft: RuleDraft,
) -> None:
    pending_draft = _pending(valid_rule_draft)
    with pytest.raises(ValidationError):
        GuardrailCandidateArtifact(
            candidate_id="cand-1",
            source_package_hash=_VALID_HASH,
            context_hash=_VALID_HASH,
            initial_rule_draft_hash=_hash(pending_draft),
            final_rule_draft_hash=_hash(pending_draft),
            final_rule_draft=pending_draft,
            guardrail_report=_report(),
            repair_history=[],
            warnings=[],
        )


def test_verdict_evidence_validated_rejects_rejected_final_draft(
    valid_rule_draft: RuleDraft,
) -> None:
    rejected_draft = _rejected(valid_rule_draft)
    with pytest.raises(ValidationError):
        GuardrailCandidateArtifact(
            candidate_id="cand-1",
            source_package_hash=_VALID_HASH,
            context_hash=_VALID_HASH,
            initial_rule_draft_hash=_hash(_pending(valid_rule_draft)),
            final_rule_draft_hash=_hash(rejected_draft),
            final_rule_draft=rejected_draft,
            guardrail_report=_report(),
            repair_history=[],
            warnings=[],
        )


def test_verdict_rejected_rejects_evidence_validated_final_draft(
    valid_rule_draft: RuleDraft,
) -> None:
    final_draft = _validated(valid_rule_draft)
    report = _report(
        verdict=GuardrailVerdict.REJECTED,
        violations=[
            GuardrailViolation(
                violation_id="v1", rule="x", field=None, message="m", severity=Severity.ERROR
            )
        ],
    )
    with pytest.raises(ValidationError):
        GuardrailCandidateArtifact(
            candidate_id="cand-1",
            source_package_hash=_VALID_HASH,
            context_hash=_VALID_HASH,
            initial_rule_draft_hash=_hash(_pending(valid_rule_draft)),
            final_rule_draft_hash=_hash(final_draft),
            final_rule_draft=final_draft,
            guardrail_report=report,
            repair_history=[],
            warnings=[],
        )


def test_functional_review_status_must_be_needs_review(valid_rule_draft: RuleDraft) -> None:
    final_draft = _validated(valid_rule_draft)
    # FunctionalReviewStatus solo declara NEEDS_FUNCTIONAL_REVIEW:
    # model_copy no revalida, asi que permite forzar un valor distinto
    # unicamente para probar el validador defensivo.
    tampered = final_draft.model_copy(
        update={"functional_review_status": "FUNCTIONALLY_APPROVED"}
    )
    with pytest.raises(ValidationError):
        GuardrailCandidateArtifact(
            candidate_id="cand-1",
            source_package_hash=_VALID_HASH,
            context_hash=_VALID_HASH,
            initial_rule_draft_hash=_hash(_pending(valid_rule_draft)),
            final_rule_draft_hash=_hash(tampered),
            final_rule_draft=tampered,
            guardrail_report=_report(),
            repair_history=[],
            warnings=[],
        )


def test_final_rule_draft_hash_must_match_real_object_hash(valid_rule_draft: RuleDraft) -> None:
    with pytest.raises(ValidationError):
        _artifact(valid_rule_draft, final_rule_draft_hash=_OTHER_HASH)


def test_artifact_requires_candidate_id_match(valid_rule_draft: RuleDraft) -> None:
    report = _report(candidate_id="other-candidate")
    with pytest.raises(ValidationError):
        _artifact(valid_rule_draft, guardrail_report=report)


def test_artifact_requires_source_package_hash_match(valid_rule_draft: RuleDraft) -> None:
    report = _report(source_package_hash=_OTHER_HASH)
    with pytest.raises(ValidationError):
        _artifact(valid_rule_draft, guardrail_report=report)


def test_artifact_requires_repair_attempts_consistent_with_history(
    valid_rule_draft: RuleDraft,
) -> None:
    report = _report(repair_attempts=1)
    with pytest.raises(ValidationError):
        _artifact(valid_rule_draft, guardrail_report=report, repair_history=[])


def test_artifact_without_repairs_requires_pending_equivalent_matches_initial(
    valid_rule_draft: RuleDraft,
) -> None:
    final_draft = _validated(valid_rule_draft)
    different_initial_pending = _pending(
        valid_rule_draft.model_copy(update={"title": "Titulo original distinto"})
    )
    with pytest.raises(ValidationError):
        GuardrailCandidateArtifact(
            candidate_id="cand-1",
            source_package_hash=_VALID_HASH,
            context_hash=_VALID_HASH,
            initial_rule_draft_hash=_hash(different_initial_pending),
            final_rule_draft_hash=_hash(final_draft),
            final_rule_draft=final_draft,
            guardrail_report=_report(),
            repair_history=[],
            warnings=[],
        )


def test_artifact_with_repairs_rejects_final_draft_not_matching_last_attempt(
    valid_rule_draft: RuleDraft,
) -> None:
    repaired_draft = valid_rule_draft.model_copy(update={"title": "Titulo reparado"})
    attempt = _repair_attempt(produced_rule_draft_hash=_hash(_pending(repaired_draft)))
    # final_rule_draft NO coincide con el draft que produjo el intento
    # (usa el original en vez del reparado).
    final_draft = _validated(valid_rule_draft)
    with pytest.raises(ValidationError):
        GuardrailCandidateArtifact(
            candidate_id="cand-1",
            source_package_hash=_VALID_HASH,
            context_hash=_VALID_HASH,
            initial_rule_draft_hash=_hash(_pending(valid_rule_draft)),
            final_rule_draft_hash=_hash(final_draft),
            final_rule_draft=final_draft,
            guardrail_report=_report(repair_attempts=1),
            repair_history=[attempt],
            warnings=[],
        )


def test_repair_history_rejects_duplicate_attempt_number(valid_rule_draft: RuleDraft) -> None:
    attempt1 = _repair_attempt(attempt_number=1, produced_rule_draft_hash=_OTHER_HASH)
    attempt2 = _repair_attempt(attempt_number=1, produced_rule_draft_hash=_OTHER_HASH)
    report = _report(repair_attempts=2)
    with pytest.raises(ValidationError):
        _artifact(
            valid_rule_draft,
            guardrail_report=report,
            repair_history=[attempt1, attempt2],
        )


def test_repair_history_must_be_ordered(valid_rule_draft: RuleDraft) -> None:
    attempt1 = _repair_attempt(attempt_number=2, produced_rule_draft_hash=_OTHER_HASH)
    attempt2 = _repair_attempt(attempt_number=1, produced_rule_draft_hash=_OTHER_HASH)
    report = _report(repair_attempts=2)
    with pytest.raises(ValidationError):
        _artifact(
            valid_rule_draft,
            guardrail_report=report,
            repair_history=[attempt1, attempt2],
        )


def test_artifact_warnings_must_be_sorted_and_deduplicated(valid_rule_draft: RuleDraft) -> None:
    with pytest.raises(ValidationError):
        _artifact(valid_rule_draft, warnings=["z", "a"])
