"""Tests contractuales del informe de cobertura semantica (Fase 1 de la
ampliacion semantica, checkpoint `feat/semantic-expansion-foundation`):
`SemanticCoverageReport` y sus modelos anidados
(`contracts/semantic_coverage.py`). NO contractual respecto a
`artifacts/01-10` -- ver docstring del modulo bajo prueba."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts.enums import LocationKind, StatementKind
from altamira_extractor.contracts.semantic_coverage import (
    MAX_SOURCE_REFERENCES_PER_CONSTRUCT,
    REQUIRED_SOURCE_ARTIFACT_KEYS,
    CandidateImpact,
    ConstructCoverage,
    ProgramSemanticCoverage,
    SemanticCoverageReport,
    SemanticCoverageSourceReference,
    SemanticCoverageSummary,
    SemanticSupportStatus,
    ZeroCandidateReason,
)

_HASH = "a" * 64


def _source_reference(**overrides: object) -> SemanticCoverageSourceReference:
    defaults: dict[str, object] = {"program": "PROG1"}
    defaults.update(overrides)
    return SemanticCoverageSourceReference(**defaults)  # type: ignore[arg-type]


def _construct_coverage(**overrides: object) -> ConstructCoverage:
    defaults: dict[str, object] = {
        "construct_name": "MOVE",
        "support_status": SemanticSupportStatus.FULLY_SUPPORTED,
        "occurrence_count": 1,
        "diagnostic_code": "MOVE_LITERAL_DIRECT",
        "explanation": "MOVE literal directo.",
        "candidate_impact": CandidateImpact.NONE,
        "source_references": [_source_reference()],
    }
    defaults.update(overrides)
    return ConstructCoverage(**defaults)  # type: ignore[arg-type]


def _program_coverage(**overrides: object) -> ProgramSemanticCoverage:
    defaults: dict[str, object] = {
        "program": "PROG1",
        "statement_count": 1,
        "fully_supported_count": 1,
        "partially_supported_count": 0,
        "preserved_only_count": 0,
        "unsupported_count": 0,
        "statement_counts_by_kind": {StatementKind.MOVE: 1},
        "decision_count": 0,
        "decisions_with_resolved_effect_count": 0,
        "decisions_without_resolved_effect_count": 0,
        "candidate_count": 0,
        "zero_candidate_reason": ZeroCandidateReason.NO_DECISIONS,
        "level_88_data_item_count": 0,
        "unsupported_construct_count": 0,
        "construct_coverage": [_construct_coverage()],
        "diagnostics": [],
    }
    defaults.update(overrides)
    return ProgramSemanticCoverage(**defaults)  # type: ignore[arg-type]


def _summary(**overrides: object) -> SemanticCoverageSummary:
    defaults: dict[str, object] = {
        "program_count": 1,
        "statement_count": 1,
        "fully_supported_count": 1,
        "partially_supported_count": 0,
        "preserved_only_count": 0,
        "unsupported_count": 0,
        "statement_counts_by_kind": {StatementKind.MOVE: 1},
        "decision_count": 0,
        "decisions_with_resolved_effect_count": 0,
        "decisions_without_resolved_effect_count": 0,
        "candidate_count": 0,
        "level_88_data_item_count": 0,
        "unsupported_construct_count": 0,
    }
    defaults.update(overrides)
    return SemanticCoverageSummary(**defaults)  # type: ignore[arg-type]


def _report(**overrides: object) -> SemanticCoverageReport:
    defaults: dict[str, object] = {
        "run_id": "run-1",
        "source_package_hash": _HASH,
        "source_artifact_hashes": {key: _HASH for key in REQUIRED_SOURCE_ARTIFACT_KEYS},
        "summary": _summary(),
        "programs": [_program_coverage()],
    }
    defaults.update(overrides)
    return SemanticCoverageReport(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# schema_version / analyzer_version
# ---------------------------------------------------------------------------


def test_schema_version_is_exactly_1_0() -> None:
    assert _report().schema_version == "1.0"


def test_analyzer_version_defaults_to_1_2() -> None:
    assert _report().analyzer_version == "1.2"


def test_analyzer_version_accepts_historical_1_0() -> None:
    # Fase 3 (soporte nivel 88) subio analyzer_version a "1.1" sin
    # cambiar schema_version: reportes historicos con "1.0" (logica de
    # clasificacion previa a nivel 88) deben seguir cargando.
    assert _report(analyzer_version="1.0").analyzer_version == "1.0"


def test_schema_version_rejects_other_values() -> None:
    with pytest.raises(ValidationError):
        _report(schema_version="2.0")


def test_analyzer_version_rejects_other_values() -> None:
    with pytest.raises(ValidationError):
        _report(analyzer_version="1.3")


# ---------------------------------------------------------------------------
# Serializacion estable / igualdad byte a byte
# ---------------------------------------------------------------------------


def test_serialization_is_stable_across_two_identical_reports() -> None:
    report_a = _report()
    report_b = _report()
    assert report_a.to_stable_json() == report_b.to_stable_json()


def test_round_trip_produces_byte_identical_json() -> None:
    report = _report()
    first = report.to_stable_json()
    second = SemanticCoverageReport.model_validate_json(first).to_stable_json()
    assert first == second


def test_serialization_never_contains_timestamp_like_keys() -> None:
    payload = _report().to_stable_json()
    for forbidden in ("generated_at", "timestamp", "created_at", "updated_at"):
        assert forbidden not in payload


# ---------------------------------------------------------------------------
# Rechazo de contadores negativos
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field_name",
    [
        "statement_count",
        "fully_supported_count",
        "partially_supported_count",
        "preserved_only_count",
        "unsupported_count",
        "decision_count",
        "decisions_with_resolved_effect_count",
        "decisions_without_resolved_effect_count",
        "candidate_count",
        "level_88_data_item_count",
        "unsupported_construct_count",
    ],
)
def test_program_coverage_rejects_negative_counters(field_name: str) -> None:
    with pytest.raises(ValidationError):
        _program_coverage(**{field_name: -1})


def test_construct_coverage_rejects_zero_or_negative_occurrence_count() -> None:
    with pytest.raises(ValidationError):
        _construct_coverage(occurrence_count=0)


# ---------------------------------------------------------------------------
# Coherencia entre total y contadores por estado
# ---------------------------------------------------------------------------


def test_program_coverage_rejects_incoherent_statement_status_partition() -> None:
    with pytest.raises(ValidationError):
        _program_coverage(statement_count=5)  # status counts still sum to 1


def test_program_coverage_rejects_incoherent_decision_partition() -> None:
    with pytest.raises(ValidationError):
        _program_coverage(decision_count=3, decisions_with_resolved_effect_count=1)


def test_program_coverage_rejects_unsupported_count_mismatch() -> None:
    with pytest.raises(ValidationError):
        _program_coverage(unsupported_count=1, unsupported_construct_count=0)


def test_program_coverage_rejects_zero_candidate_reason_incoherent_with_candidate_count() -> None:
    with pytest.raises(ValidationError):
        _program_coverage(candidate_count=1, zero_candidate_reason=ZeroCandidateReason.NO_DECISIONS)
    with pytest.raises(ValidationError):
        _program_coverage(
            candidate_count=0, zero_candidate_reason=ZeroCandidateReason.CANDIDATES_PRESENT
        )


def test_summary_must_match_sum_of_programs() -> None:
    with pytest.raises(ValidationError):
        _report(summary=_summary(statement_count=99))


def test_summary_program_count_must_match_programs_length() -> None:
    with pytest.raises(ValidationError):
        _report(summary=_summary(program_count=2))


# ---------------------------------------------------------------------------
# Rechazo de campos extra
# ---------------------------------------------------------------------------


def test_report_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        SemanticCoverageReport.model_validate(
            {**_report().model_dump(mode="json"), "unexpected_field": "x"}
        )


def test_program_coverage_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ProgramSemanticCoverage.model_validate(
            {**_program_coverage().model_dump(mode="json"), "unexpected_field": "x"}
        )


def test_construct_coverage_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ConstructCoverage.model_validate(
            {**_construct_coverage().model_dump(mode="json"), "unexpected_field": "x"}
        )


def test_source_reference_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        SemanticCoverageSourceReference.model_validate(
            {**_source_reference().model_dump(mode="json"), "unexpected_field": "x"}
        )


# ---------------------------------------------------------------------------
# source_file relativo
# ---------------------------------------------------------------------------


def test_source_reference_rejects_absolute_source_file() -> None:
    with pytest.raises(ValidationError):
        _source_reference(source_file="C:/absolute/path.cbl")
    with pytest.raises(ValidationError):
        _source_reference(source_file="/absolute/path.cbl")


def test_source_reference_accepts_relative_source_file() -> None:
    reference = _source_reference(
        source_file="01-codigo/cobol/a.cbl", line=1, location_kind=LocationKind.EXACT
    )
    assert reference.source_file == "01-codigo/cobol/a.cbl"


# ---------------------------------------------------------------------------
# Limite de source_references
# ---------------------------------------------------------------------------


def test_construct_coverage_enforces_max_source_references() -> None:
    too_many = [_source_reference() for _ in range(MAX_SOURCE_REFERENCES_PER_CONSTRUCT + 1)]
    with pytest.raises(ValidationError):
        _construct_coverage(occurrence_count=len(too_many), source_references=too_many)


def test_construct_coverage_accepts_exactly_the_max_source_references() -> None:
    exactly_max = [_source_reference() for _ in range(MAX_SOURCE_REFERENCES_PER_CONSTRUCT)]
    coverage = _construct_coverage(occurrence_count=len(exactly_max), source_references=exactly_max)
    assert len(coverage.source_references) == MAX_SOURCE_REFERENCES_PER_CONSTRUCT


def test_construct_coverage_occurrence_count_can_exceed_reference_count() -> None:
    coverage = _construct_coverage(occurrence_count=1000, source_references=[_source_reference()])
    assert coverage.occurrence_count == 1000
    assert len(coverage.source_references) == 1


def test_construct_coverage_rejects_more_references_than_occurrences() -> None:
    with pytest.raises(ValidationError):
        _construct_coverage(
            occurrence_count=1, source_references=[_source_reference(), _source_reference()]
        )


# ---------------------------------------------------------------------------
# Ordenamiento deterministico / deduplicacion
# ---------------------------------------------------------------------------


def test_report_rejects_unsorted_programs() -> None:
    with pytest.raises(ValidationError):
        _report(
            programs=[_program_coverage(program="Z"), _program_coverage(program="A")],
            summary=_summary(program_count=2, statement_count=2, fully_supported_count=2),
        )


def test_report_rejects_duplicate_program_names() -> None:
    with pytest.raises(ValidationError):
        _report(
            programs=[_program_coverage(program="A"), _program_coverage(program="A")],
            summary=_summary(program_count=2, statement_count=2, fully_supported_count=2),
        )


def test_program_coverage_rejects_unsorted_construct_coverage() -> None:
    with pytest.raises(ValidationError):
        _program_coverage(
            construct_coverage=[
                _construct_coverage(
                    construct_name="SET", diagnostic_code="SET_TARGET_KIND_AMBIGUOUS"
                ),
                _construct_coverage(construct_name="MOVE"),
            ]
        )


def test_report_requires_all_source_artifact_keys() -> None:
    with pytest.raises(ValidationError):
        _report(source_artifact_hashes={"artifacts/02-canonical": _HASH})


# ---------------------------------------------------------------------------
# ZeroCandidateReason nunca tiene NO_RULES
# ---------------------------------------------------------------------------


def test_zero_candidate_reason_never_has_a_no_rules_value() -> None:
    assert not hasattr(ZeroCandidateReason, "NO_RULES")
    assert "NO_RULES" not in {member.value for member in ZeroCandidateReason}
