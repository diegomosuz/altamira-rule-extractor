"""Tests de GuardrailReport."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts import (
    GuardrailReport,
    GuardrailVerdict,
    GuardrailViolation,
    Severity,
)

NOW = datetime(2026, 7, 22, 12, 0, 0, tzinfo=UTC)
VALID_HASH = "c" * 64


def test_evidence_validated_with_no_violations_is_valid() -> None:
    report = GuardrailReport(
        candidate_id="cand-1",
        verdict=GuardrailVerdict.EVIDENCE_VALIDATED,
        violations=[],
        repair_attempts=0,
        evaluated_at=NOW,
        source_package_hash=VALID_HASH,
    )
    assert report.verdict == GuardrailVerdict.EVIDENCE_VALIDATED


def test_evidence_validated_with_only_warnings_is_valid() -> None:
    report = GuardrailReport(
        candidate_id="cand-1",
        verdict=GuardrailVerdict.EVIDENCE_VALIDATED,
        violations=[
            GuardrailViolation(
                violation_id="v1",
                rule="unused_field",
                message="campo no usado",
                severity=Severity.WARNING,
            ),
        ],
        repair_attempts=1,
        evaluated_at=NOW,
        source_package_hash=VALID_HASH,
    )
    assert report.violations[0].severity == Severity.WARNING


def test_evidence_validated_with_error_violation_is_rejected() -> None:
    with pytest.raises(ValidationError):
        GuardrailReport(
            candidate_id="cand-1",
            verdict=GuardrailVerdict.EVIDENCE_VALIDATED,
            violations=[
                GuardrailViolation(
                    violation_id="v1",
                    rule="invented_table",
                    message="tabla inventada",
                    severity=Severity.ERROR,
                ),
            ],
            repair_attempts=2,
            evaluated_at=NOW,
            source_package_hash=VALID_HASH,
        )


def test_rejected_requires_at_least_one_error() -> None:
    with pytest.raises(ValidationError):
        GuardrailReport(
            candidate_id="cand-1",
            verdict=GuardrailVerdict.REJECTED,
            violations=[],
            repair_attempts=2,
            evaluated_at=NOW,
            source_package_hash=VALID_HASH,
        )


def test_repair_attempts_cannot_exceed_two() -> None:
    with pytest.raises(ValidationError):
        GuardrailReport(
            candidate_id="cand-1",
            verdict=GuardrailVerdict.REJECTED,
            violations=[
                GuardrailViolation(
                    violation_id="v1",
                    rule="invented_table",
                    message="tabla inventada",
                    severity=Severity.ERROR,
                ),
            ],
            repair_attempts=3,
            evaluated_at=NOW,
            source_package_hash=VALID_HASH,
        )


def test_guardrail_report_rejects_additional_properties() -> None:
    with pytest.raises(ValidationError):
        GuardrailReport.model_validate(
            {
                "candidate_id": "cand-1",
                "verdict": "EVIDENCE_VALIDATED",
                "violations": [],
                "repair_attempts": 0,
                "evaluated_at": NOW.isoformat(),
                "source_package_hash": VALID_HASH,
                "extra": True,
            }
        )
