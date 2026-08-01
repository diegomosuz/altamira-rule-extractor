"""Tests contractuales de detectores V2 en shadow mode (Fase 5 de la
ampliacion semantica, `feat/v2-detectors-shadow-mode`):
`V2ShadowCandidatesArtifact` y sus modelos anidados
(`contracts/v2_shadow_candidates.py`). NO contractual respecto a
`artifacts/01-10` -- ver docstring del modulo bajo prueba."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts.v2_shadow_candidates import (
    V1V2CandidateComparison,
    V1V2ComparisonStatus,
    V2CandidateSourceReference,
    V2CandidateSupport,
    V2DetectorExecution,
    V2RuleType,
    V2ShadowCandidate,
    V2ShadowCandidatesArtifact,
    V2ShadowSummary,
)

_HASH = "a" * 64


def _source_reference(**overrides: object) -> V2CandidateSourceReference:
    defaults: dict[str, object] = {
        "program": "PROG1",
        "paragraph": "A",
        "statement_id": "PROG1::A::1::MOVE",
        "effect_id": "effect::PROG1::A::PROG1::A::1::MOVE::ASSIGN_LITERAL::0",
        "fact_id": "fact::PROG1::A::root::WS-COD-RETORNO::PROG1::A::1::MOVE::DIRECT_LITERAL::0",
        "decision_id": "PROG1::A::paragraph::A::decision::10::1",
    }
    defaults.update(overrides)
    return V2CandidateSourceReference(**defaults)  # type: ignore[arg-type]


def _deterministic_candidate(**overrides: object) -> V2ShadowCandidate:
    defaults: dict[str, object] = {
        "candidate_id": "v2::V2_RETURN_CODE_PROPAGATION::" + "0" * 24,
        "detector_id": "V2_RETURN_CODE_PROPAGATION",
        "detector_version": "1.0",
        "rule_type": V2RuleType.RETURN_CODE_RULE,
        "support": V2CandidateSupport.DETERMINISTIC,
        "detector_score": 1.0,
        "program": "PROG1",
        "paragraph": "A",
        "anchor_statement_id": "PROG1::A::1::MOVE",
        "decision_id": "PROG1::A::paragraph::A::decision::10::1",
        "target_variable": "WS-COD-RETORNO",
        "target_qualified_name": "WS-COD-RETORNO",
        "resolved_literal": "0005",
        "semantic_effect_ids": ["effect::PROG1::A::PROG1::A::1::MOVE::ASSIGN_LITERAL::0"],
        "propagation_fact_ids": [
            "fact::PROG1::A::root::WS-COD-RETORNO::PROG1::A::1::MOVE::DIRECT_LITERAL::0"
        ],
        "source_references": [_source_reference()],
        "reason": "MOVE de literal directo bajo una Decision, hacia un DataItem return_code.",
    }
    defaults.update(overrides)
    return V2ShadowCandidate(**defaults)  # type: ignore[arg-type]


def _blocked_candidate(**overrides: object) -> V2ShadowCandidate:
    defaults: dict[str, object] = {
        "candidate_id": "v2::V2_RETURN_CODE_PROPAGATION::" + "1" * 24,
        "detector_id": "V2_RETURN_CODE_PROPAGATION",
        "detector_version": "1.0",
        "rule_type": V2RuleType.RETURN_CODE_RULE,
        "support": V2CandidateSupport.BLOCKED,
        "detector_score": 0.0,
        "program": "PROG1",
        "paragraph": "A",
        "anchor_statement_id": "PROG1::A::2::MOVE",
        "decision_id": "PROG1::A::paragraph::A::decision::10::1",
        "target_variable": "WS-COD-RETORNO",
        "resolved_literal": None,
        "diagnostic_codes": ["V2_RETURN_CODE_UNRESOLVED_COPY"],
        "reason": "Copia sin origen resoluble hacia un DataItem return_code.",
    }
    defaults.update(overrides)
    return V2ShadowCandidate(**defaults)  # type: ignore[arg-type]


def _execution(**overrides: object) -> V2DetectorExecution:
    defaults: dict[str, object] = {
        "detector_id": "V2_RETURN_CODE_PROPAGATION",
        "detector_version": "1.0",
        "rule_type": V2RuleType.RETURN_CODE_RULE,
        "candidate_count": 1,
        "blocked_count": 0,
        "candidates": [_deterministic_candidate()],
    }
    defaults.update(overrides)
    return V2DetectorExecution(**defaults)  # type: ignore[arg-type]


def _comparison(**overrides: object) -> V1V2CandidateComparison:
    defaults: dict[str, object] = {
        "comparison_id": "comparison::" + "0" * 24,
        "status": V1V2ComparisonStatus.V2_ONLY,
        "v1_candidate_ids": [],
        "v2_candidate_ids": ["v2::V2_RETURN_CODE_PROPAGATION::" + "0" * 24],
        "program": "PROG1",
        "paragraph": "A",
        "reason": "Detector V2 encontro un candidato sin equivalente en Q0 (V1).",
    }
    defaults.update(overrides)
    return V1V2CandidateComparison(**defaults)  # type: ignore[arg-type]


def _summary(**overrides: object) -> V2ShadowSummary:
    defaults: dict[str, object] = {
        "detector_count": 1,
        "v1_candidate_count": 0,
        "v2_candidate_count": 1,
        "deterministic_count": 1,
        "partial_count": 0,
        "blocked_count": 0,
        "matched_count": 0,
        "v1_only_count": 0,
        "v2_only_count": 1,
        "related_not_equivalent_count": 0,
        "counts_by_rule_type": {V2RuleType.RETURN_CODE_RULE: 1},
        "counts_by_detector": {"V2_RETURN_CODE_PROPAGATION": 1},
    }
    defaults.update(overrides)
    return V2ShadowSummary(**defaults)  # type: ignore[arg-type]


def _artifact(**overrides: object) -> V2ShadowCandidatesArtifact:
    defaults: dict[str, object] = {
        "run_id": "run-1",
        "source_package_hash": _HASH,
        "source_artifact_hashes": {"artifacts/02-canonical": _HASH},
        "semantic_effects_schema_version": "1.1",
        "semantic_effects_analyzer_version": "1.1",
        "semantic_propagation_schema_version": "1.0",
        "semantic_propagation_analyzer_version": "1.0",
        "summary": _summary(),
        "executions": [_execution()],
        "comparisons": [_comparison()],
    }
    defaults.update(overrides)
    return V2ShadowCandidatesArtifact(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# schema_version / analyzer_version: versiones exactas, rechazo de desconocida
# ---------------------------------------------------------------------------


def test_schema_version_is_exactly_1_0() -> None:
    assert _artifact().schema_version == "1.0"


def test_analyzer_version_is_exactly_1_0() -> None:
    assert _artifact().analyzer_version == "1.0"


def test_schema_version_rejects_other_values() -> None:
    with pytest.raises(ValidationError):
        _artifact(schema_version="1.1")


def test_analyzer_version_rejects_other_values() -> None:
    with pytest.raises(ValidationError):
        _artifact(analyzer_version="2.0")


def test_semantic_effects_versions_accept_1_0_and_1_1() -> None:
    schema_artifact = _artifact(semantic_effects_schema_version="1.0")
    assert schema_artifact.semantic_effects_schema_version == "1.0"
    analyzer_artifact = _artifact(semantic_effects_analyzer_version="1.0")
    assert analyzer_artifact.semantic_effects_analyzer_version == "1.0"


def test_semantic_effects_versions_reject_unknown_value() -> None:
    with pytest.raises(ValidationError):
        _artifact(semantic_effects_schema_version="2.0")
    with pytest.raises(ValidationError):
        _artifact(semantic_effects_analyzer_version="2.0")


def test_semantic_propagation_versions_reject_unknown_value() -> None:
    with pytest.raises(ValidationError):
        _artifact(semantic_propagation_schema_version="2.0")
    with pytest.raises(ValidationError):
        _artifact(semantic_propagation_analyzer_version="2.0")


# ---------------------------------------------------------------------------
# Rechazo de campos extra
# ---------------------------------------------------------------------------


def test_artifact_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        V2ShadowCandidatesArtifact.model_validate(
            {**_artifact().model_dump(mode="json"), "unexpected_field": "x"}
        )


def test_candidate_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        V2ShadowCandidate.model_validate(
            {**_deterministic_candidate().model_dump(mode="json"), "unexpected_field": "x"}
        )


def test_source_reference_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        V2CandidateSourceReference.model_validate(
            {**_source_reference().model_dump(mode="json"), "unexpected_field": "x"}
        )


def test_execution_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        V2DetectorExecution.model_validate(
            {**_execution().model_dump(mode="json"), "unexpected_field": "x"}
        )


def test_comparison_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        V1V2CandidateComparison.model_validate(
            {**_comparison().model_dump(mode="json"), "unexpected_field": "x"}
        )


def test_summary_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        V2ShadowSummary.model_validate(
            {**_summary().model_dump(mode="json"), "unexpected_field": "x"}
        )


# ---------------------------------------------------------------------------
# detector_score fuera de rango
# ---------------------------------------------------------------------------


def test_detector_score_rejects_values_out_of_0_1_range() -> None:
    with pytest.raises(ValidationError):
        _deterministic_candidate(detector_score=1.1)
    with pytest.raises(ValidationError):
        _deterministic_candidate(detector_score=-0.1)


# ---------------------------------------------------------------------------
# Coherencia DETERMINISTIC / PARTIAL / BLOCKED
# ---------------------------------------------------------------------------


def test_deterministic_requires_score_1_0() -> None:
    with pytest.raises(ValidationError):
        _deterministic_candidate(detector_score=0.9)


def test_deterministic_requires_resolved_literal() -> None:
    with pytest.raises(ValidationError):
        _deterministic_candidate(resolved_literal=None)


def test_deterministic_requires_at_least_one_semantic_effect_id() -> None:
    with pytest.raises(ValidationError):
        _deterministic_candidate(semantic_effect_ids=[])


def test_deterministic_requires_at_least_one_propagation_fact_id() -> None:
    with pytest.raises(ValidationError):
        _deterministic_candidate(propagation_fact_ids=[])


def test_deterministic_requires_at_least_one_source_reference() -> None:
    with pytest.raises(ValidationError):
        _deterministic_candidate(source_references=[])


def test_blocked_cannot_assert_resolved_literal() -> None:
    with pytest.raises(ValidationError):
        _blocked_candidate(resolved_literal="0005")


def test_blocked_requires_at_least_one_diagnostic_code() -> None:
    with pytest.raises(ValidationError):
        _blocked_candidate(diagnostic_codes=[])


def test_partial_allows_missing_literal_and_empty_diagnostics() -> None:
    candidate = _deterministic_candidate(
        candidate_id="v2::V2_STATE_CHANGE::" + "2" * 24,
        detector_id="V2_STATE_CHANGE",
        detector_version="1.0",
        rule_type=V2RuleType.STATE_CHANGE_RULE,
        support=V2CandidateSupport.PARTIAL,
        detector_score=0.7,
    )
    assert candidate.support == V2CandidateSupport.PARTIAL


# ---------------------------------------------------------------------------
# candidate_id / comparison_id: no vacios, ordenamiento y unicidad
# ---------------------------------------------------------------------------


def test_candidate_id_cannot_be_empty() -> None:
    with pytest.raises(ValidationError):
        _deterministic_candidate(candidate_id="")


def test_comparison_id_cannot_be_empty() -> None:
    with pytest.raises(ValidationError):
        _comparison(comparison_id="")


def test_execution_rejects_unsorted_candidates() -> None:
    candidate_z = _deterministic_candidate(candidate_id="v2::V2_RETURN_CODE_PROPAGATION::z")
    candidate_a = _deterministic_candidate(candidate_id="v2::V2_RETURN_CODE_PROPAGATION::a")
    with pytest.raises(ValidationError):
        _execution(candidate_count=2, candidates=[candidate_z, candidate_a])


def test_execution_rejects_duplicate_candidate_ids() -> None:
    duplicate = _deterministic_candidate()
    with pytest.raises(ValidationError):
        _execution(candidate_count=2, candidates=[duplicate, duplicate])


def test_artifact_rejects_unsorted_executions() -> None:
    execution_z = _execution(
        detector_id="Z_DETECTOR",
        candidates=[
            _deterministic_candidate(
                candidate_id="v2::Z_DETECTOR::" + "8" * 24, detector_id="Z_DETECTOR"
            )
        ],
    )
    execution_a = _execution(
        detector_id="A_DETECTOR",
        candidates=[
            _deterministic_candidate(
                candidate_id="v2::A_DETECTOR::" + "9" * 24, detector_id="A_DETECTOR"
            )
        ],
    )
    with pytest.raises(ValidationError):
        _artifact(
            executions=[execution_z, execution_a],
            summary=_summary(
                detector_count=2,
                v2_candidate_count=2,
                deterministic_count=2,
                counts_by_rule_type={V2RuleType.RETURN_CODE_RULE: 2},
                counts_by_detector={"Z_DETECTOR": 1, "A_DETECTOR": 1},
            ),
        )


def test_artifact_rejects_duplicate_detector_ids_across_executions() -> None:
    duplicate = _execution()
    with pytest.raises(ValidationError):
        _artifact(
            executions=[duplicate, duplicate],
            summary=_summary(detector_count=2, v2_candidate_count=2, deterministic_count=2),
        )


def test_artifact_rejects_unsorted_comparisons() -> None:
    comparison_z = _comparison(comparison_id="comparison::" + "f" * 24)
    comparison_a = _comparison(comparison_id="comparison::" + "0" * 24)
    with pytest.raises(ValidationError):
        _artifact(comparisons=[comparison_z, comparison_a])


def test_artifact_rejects_duplicate_comparison_ids() -> None:
    duplicate = _comparison()
    with pytest.raises(ValidationError):
        _artifact(comparisons=[duplicate, duplicate])


# ---------------------------------------------------------------------------
# Invariante post-auditoria Catherine corregido (Parte 4): ningun
# candidate_id V1 ni V2 puede aparecer en mas de una comparacion --
# garantiza que matched/v1_only/v2_only/related_not_equivalent
# particionan los candidatos sin doble conteo.
# ---------------------------------------------------------------------------


def test_artifact_rejects_same_v2_candidate_id_in_two_comparisons() -> None:
    shared_v2_id = "v2::V2_RETURN_CODE_PROPAGATION::" + "0" * 24
    comparison_one = _comparison(
        comparison_id="comparison::" + "1" * 24,
        status=V1V2ComparisonStatus.V2_ONLY,
        v1_candidate_ids=[],
        v2_candidate_ids=[shared_v2_id],
    )
    comparison_two = _comparison(
        comparison_id="comparison::" + "2" * 24,
        status=V1V2ComparisonStatus.RELATED_NOT_EQUIVALENT,
        v1_candidate_ids=[],
        v2_candidate_ids=sorted(["v2::V2_LEVEL_88_RETURN_CODE::" + "3" * 24, shared_v2_id]),
    )
    with pytest.raises(ValidationError, match="aparece en mas de una comparacion"):
        _artifact(
            comparisons=[comparison_one, comparison_two],
            summary=_summary(
                v2_only_count=1, related_not_equivalent_count=1, deterministic_count=1
            ),
        )


def test_artifact_rejects_same_v1_candidate_id_in_two_comparisons() -> None:
    shared_v1_id = "candidate::q0-return-code-decision::1.0::" + "a" * 64 + "::decision-x"
    comparison_one = _comparison(
        comparison_id="comparison::" + "1" * 24,
        status=V1V2ComparisonStatus.V1_ONLY,
        v1_candidate_ids=[shared_v1_id],
        v2_candidate_ids=[],
    )
    comparison_two = _comparison(
        comparison_id="comparison::" + "2" * 24,
        status=V1V2ComparisonStatus.MATCHED,
        v1_candidate_ids=[shared_v1_id],
        v2_candidate_ids=["v2::V2_RETURN_CODE_PROPAGATION::" + "0" * 24],
    )
    with pytest.raises(ValidationError, match="aparece en mas de una comparacion"):
        _artifact(
            comparisons=[comparison_one, comparison_two],
            summary=_summary(v1_only_count=1, matched_count=1, v2_only_count=0),
        )


def test_artifact_accepts_disjoint_candidate_ids_across_comparisons() -> None:
    comparison_one = _comparison(
        comparison_id="comparison::" + "1" * 24,
        status=V1V2ComparisonStatus.V2_ONLY,
        v1_candidate_ids=[],
        v2_candidate_ids=["v2::V2_RETURN_CODE_PROPAGATION::" + "0" * 24],
    )
    comparison_two = _comparison(
        comparison_id="comparison::" + "2" * 24,
        status=V1V2ComparisonStatus.V1_ONLY,
        v1_candidate_ids=["candidate::q0-return-code-decision::1.0::" + "a" * 64 + "::decision-y"],
        v2_candidate_ids=[],
    )
    artifact = _artifact(
        comparisons=[comparison_one, comparison_two],
        summary=_summary(v2_only_count=1, v1_only_count=1, v1_candidate_count=1),
    )
    assert len(artifact.comparisons) == 2


# ---------------------------------------------------------------------------
# V1V2ComparisonStatus: coherencia MATCHED/V1_ONLY/V2_ONLY/RELATED_NOT_EQUIVALENT
# ---------------------------------------------------------------------------


def test_matched_requires_both_sides_non_empty() -> None:
    with pytest.raises(ValidationError):
        _comparison(status=V1V2ComparisonStatus.MATCHED, v1_candidate_ids=[])
    with pytest.raises(ValidationError):
        _comparison(
            status=V1V2ComparisonStatus.MATCHED,
            v1_candidate_ids=["candidate::x"],
            v2_candidate_ids=[],
        )


def test_v1_only_requires_v2_empty() -> None:
    with pytest.raises(ValidationError):
        _comparison(
            status=V1V2ComparisonStatus.V1_ONLY,
            v1_candidate_ids=["candidate::x"],
        )


def test_v2_only_requires_v1_empty() -> None:
    with pytest.raises(ValidationError):
        _comparison(
            status=V1V2ComparisonStatus.V2_ONLY,
            v1_candidate_ids=["candidate::x"],
        )


def test_related_not_equivalent_requires_v2_non_empty() -> None:
    with pytest.raises(ValidationError):
        _comparison(status=V1V2ComparisonStatus.RELATED_NOT_EQUIVALENT, v2_candidate_ids=[])


def test_related_not_equivalent_without_v1_requires_at_least_two_v2_ids() -> None:
    with pytest.raises(ValidationError):
        _comparison(
            status=V1V2ComparisonStatus.RELATED_NOT_EQUIVALENT,
            v1_candidate_ids=[],
            v2_candidate_ids=["v2::A::" + "0" * 24],
        )
    ok = _comparison(
        status=V1V2ComparisonStatus.RELATED_NOT_EQUIVALENT,
        v1_candidate_ids=[],
        v2_candidate_ids=["v2::A::" + "0" * 24, "v2::B::" + "1" * 24],
    )
    assert ok.status == V1V2ComparisonStatus.RELATED_NOT_EQUIVALENT


def test_related_not_equivalent_with_v1_allows_single_v2_id() -> None:
    ok = _comparison(
        status=V1V2ComparisonStatus.RELATED_NOT_EQUIVALENT,
        v1_candidate_ids=["candidate::x"],
        v2_candidate_ids=["v2::A::" + "0" * 24],
    )
    assert ok.status == V1V2ComparisonStatus.RELATED_NOT_EQUIVALENT


# ---------------------------------------------------------------------------
# Listas ordenadas y sin duplicados dentro de un candidato/comparacion
# ---------------------------------------------------------------------------


def test_candidate_semantic_effect_ids_rejects_unsorted_or_duplicate() -> None:
    with pytest.raises(ValidationError):
        _deterministic_candidate(semantic_effect_ids=["effect::b", "effect::a"])
    with pytest.raises(ValidationError):
        _deterministic_candidate(semantic_effect_ids=["effect::a", "effect::a"])


def test_candidate_source_references_rejects_duplicate() -> None:
    duplicate = _source_reference()
    with pytest.raises(ValidationError):
        _deterministic_candidate(source_references=[duplicate, duplicate])


def test_comparison_v1_candidate_ids_rejects_unsorted() -> None:
    with pytest.raises(ValidationError):
        _comparison(
            status=V1V2ComparisonStatus.MATCHED,
            v1_candidate_ids=["candidate::b", "candidate::a"],
        )


# ---------------------------------------------------------------------------
# Ausencia de source_text / timestamps; rutas relativas
# ---------------------------------------------------------------------------


def test_source_reference_has_no_source_text_field() -> None:
    assert "source_text" not in V2CandidateSourceReference.model_fields


def test_candidate_has_no_source_text_field() -> None:
    assert "source_text" not in V2ShadowCandidate.model_fields


def test_artifact_has_no_timestamp_fields() -> None:
    payload = _artifact().to_stable_json()
    for forbidden in ("generated_at", "timestamp", "created_at", "updated_at"):
        assert forbidden not in payload


def test_source_reference_rejects_absolute_source_file() -> None:
    with pytest.raises(ValidationError):
        _source_reference(source_file="C:/absolute/path.cbl")
    with pytest.raises(ValidationError):
        _source_reference(source_file="/absolute/path.cbl")


def test_source_reference_accepts_relative_source_file() -> None:
    reference = _source_reference(source_file="01-codigo/cobol/a.cbl", line_start=1, line_end=1)
    assert reference.source_file == "01-codigo/cobol/a.cbl"


def test_source_reference_rejects_line_end_before_line_start() -> None:
    with pytest.raises(ValidationError):
        _source_reference(source_file="a.cbl", line_start=5, line_end=1)


# ---------------------------------------------------------------------------
# V2ShadowSummary: contadores negativos y coherencia de agregacion
# ---------------------------------------------------------------------------


def test_summary_rejects_negative_counts_by_rule_type() -> None:
    with pytest.raises(ValidationError):
        _summary(counts_by_rule_type={V2RuleType.RETURN_CODE_RULE: -1})


def test_summary_rejects_negative_counts_by_detector() -> None:
    with pytest.raises(ValidationError):
        _summary(counts_by_detector={"V2_RETURN_CODE_PROPAGATION": -1})


def test_summary_counts_by_rule_type_must_sum_to_v2_candidate_count() -> None:
    with pytest.raises(ValidationError):
        _summary(v2_candidate_count=2)


def test_summary_counts_by_detector_must_sum_to_v2_candidate_count() -> None:
    with pytest.raises(ValidationError):
        _summary(counts_by_detector={"V2_RETURN_CODE_PROPAGATION": 2}, v2_candidate_count=2)


def test_summary_support_counts_must_sum_to_v2_candidate_count() -> None:
    with pytest.raises(ValidationError):
        _summary(deterministic_count=0, partial_count=0, blocked_count=0)


def test_summary_requires_at_least_one_comparison_when_candidates_exist() -> None:
    with pytest.raises(ValidationError):
        _summary(matched_count=0, v1_only_count=0, v2_only_count=0, related_not_equivalent_count=0)


def test_summary_allows_zero_comparisons_when_no_candidates_at_all() -> None:
    empty = _summary(
        detector_count=0,
        v1_candidate_count=0,
        v2_candidate_count=0,
        deterministic_count=0,
        partial_count=0,
        blocked_count=0,
        matched_count=0,
        v1_only_count=0,
        v2_only_count=0,
        related_not_equivalent_count=0,
        counts_by_rule_type={},
        counts_by_detector={},
    )
    assert empty.v2_candidate_count == 0


# ---------------------------------------------------------------------------
# V2ShadowCandidatesArtifact: coherencia de summary contra executions/comparisons
# ---------------------------------------------------------------------------


def test_artifact_summary_detector_count_must_match_executions_length() -> None:
    with pytest.raises(ValidationError):
        _artifact(summary=_summary(detector_count=2))


def test_artifact_summary_v2_candidate_count_must_match_real_aggregation() -> None:
    with pytest.raises(ValidationError):
        _artifact(summary=_summary(v2_candidate_count=99))


def test_artifact_summary_counts_by_rule_type_must_match_real_aggregation() -> None:
    with pytest.raises(ValidationError):
        _artifact(summary=_summary(counts_by_rule_type={V2RuleType.STATE_CHANGE_RULE: 1}))


def test_artifact_summary_counts_by_detector_must_match_real_aggregation() -> None:
    with pytest.raises(ValidationError):
        _artifact(summary=_summary(counts_by_detector={"OTHER_DETECTOR": 1}))


def test_artifact_summary_matched_count_must_match_real_comparisons() -> None:
    with pytest.raises(ValidationError):
        _artifact(summary=_summary(matched_count=1, v2_only_count=0))


def test_artifact_requires_at_least_one_source_artifact_hash() -> None:
    with pytest.raises(ValidationError):
        _artifact(source_artifact_hashes={})


# ---------------------------------------------------------------------------
# Serializacion byte a byte estable
# ---------------------------------------------------------------------------


def test_serialization_is_stable_across_two_identical_artifacts() -> None:
    assert _artifact().to_stable_json() == _artifact().to_stable_json()


def test_round_trip_produces_byte_identical_json() -> None:
    artifact = _artifact()
    first = artifact.to_stable_json()
    second = V2ShadowCandidatesArtifact.model_validate_json(first).to_stable_json()
    assert first == second
