"""Tests contractuales de propagacion limitada de constantes y copias
(Fase 4 de la ampliacion semantica, `feat/constant-copy-propagation`):
`SemanticPropagationArtifact` y sus modelos anidados
(`contracts/semantic_propagation.py`). NO contractual respecto a
`artifacts/01-10` -- ver docstring del modulo bajo prueba."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts.enums import StatementKind
from altamira_extractor.contracts.semantic_coverage import SemanticSupportStatus
from altamira_extractor.contracts.semantic_effects import SemanticEffectKind
from altamira_extractor.contracts.semantic_propagation import (
    ProgramSemanticPropagation,
    PropagatedValueFact,
    PropagationBarrier,
    PropagationBarrierReason,
    PropagationFactKind,
    PropagationSourceReference,
    PropagationStep,
    SemanticPropagationArtifact,
    SemanticPropagationSummary,
)

_HASH = "a" * 64


def _source_reference(**overrides: object) -> PropagationSourceReference:
    defaults: dict[str, object] = {
        "program": "PROG1",
        "paragraph": "A",
        "statement_id": "PROG1::A::1::MOVE",
        "statement_kind": StatementKind.MOVE,
    }
    defaults.update(overrides)
    return PropagationSourceReference(**defaults)  # type: ignore[arg-type]


def _step(**overrides: object) -> PropagationStep:
    defaults: dict[str, object] = {
        "statement_id": "PROG1::A::0::MOVE",
        "effect_id": "effect::PROG1::A::PROG1::A::0::MOVE::ASSIGN_LITERAL::0",
        "operation": SemanticEffectKind.ASSIGN_LITERAL,
        "target_variable": "WS-COD-AUX",
        "literal": "0005",
    }
    defaults.update(overrides)
    return PropagationStep(**defaults)  # type: ignore[arg-type]


def _fact(**overrides: object) -> PropagatedValueFact:
    defaults: dict[str, object] = {
        "fact_id": "fact::PROG1::A::root::WS-COD-AUX::PROG1::A::1::MOVE::DIRECT_LITERAL::0",
        "fact_kind": PropagationFactKind.DIRECT_LITERAL,
        "program": "PROG1",
        "paragraph": "A",
        "region_id": "PROG1::A::root",
        "target_variable": "WS-COD-AUX",
        "literal": "0005",
        "source_reference": _source_reference(),
        "derivation_steps": [_step()],
        "derivation_depth": 1,
        "support_status": SemanticSupportStatus.FULLY_SUPPORTED,
        "explanation": "MOVE de literal directo.",
    }
    defaults.update(overrides)
    return PropagatedValueFact(**defaults)  # type: ignore[arg-type]


def _barrier(**overrides: object) -> PropagationBarrier:
    defaults: dict[str, object] = {
        "barrier_id": "barrier::PROG1::A::root::PROG1::A::1::MOVE::COMPUTED_VALUE::0",
        "program": "PROG1",
        "paragraph": "A",
        "region_id": "PROG1::A::root",
        "source_reference": _source_reference(),
        "reason": PropagationBarrierReason.COMPUTED_VALUE,
        "affected_variables": ["WS-A"],
        "clears_entire_environment": False,
        "diagnostic_code": "COMPUTE_EXPRESSION_NOT_EVALUATED",
        "explanation": "COMPUTE no se evalua.",
    }
    defaults.update(overrides)
    return PropagationBarrier(**defaults)  # type: ignore[arg-type]


def _program_propagation(**overrides: object) -> ProgramSemanticPropagation:
    defaults: dict[str, object] = {
        "program": "PROG1",
        "paragraph_count": 1,
        "fact_count": 1,
        "barrier_count": 0,
        "facts": [_fact()],
        "barriers": [],
    }
    defaults.update(overrides)
    return ProgramSemanticPropagation(**defaults)  # type: ignore[arg-type]


def _summary(**overrides: object) -> SemanticPropagationSummary:
    defaults: dict[str, object] = {
        "program_count": 1,
        "paragraph_count": 1,
        "fact_count": 1,
        "direct_literal_count": 1,
        "propagated_literal_count": 0,
        "condition_literal_count": 0,
        "unresolved_copy_count": 0,
        "invalidated_count": 0,
        "blocked_count": 0,
        "barrier_count": 0,
        "counts_by_fact_kind": {PropagationFactKind.DIRECT_LITERAL: 1},
        "counts_by_barrier_reason": {},
    }
    defaults.update(overrides)
    return SemanticPropagationSummary(**defaults)  # type: ignore[arg-type]


def _artifact(**overrides: object) -> SemanticPropagationArtifact:
    defaults: dict[str, object] = {
        "semantic_effects_schema_version": "1.1",
        "semantic_effects_analyzer_version": "1.1",
        "run_id": "run-1",
        "source_package_hash": _HASH,
        "source_artifact_hashes": {"artifacts/02-canonical": _HASH},
        "summary": _summary(),
        "programs": [_program_propagation()],
    }
    defaults.update(overrides)
    return SemanticPropagationArtifact(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# schema_version / analyzer_version: versiones exactas, rechazo de desconocida
# ---------------------------------------------------------------------------


def test_schema_version_is_exactly_1_1() -> None:
    assert _artifact().schema_version == "1.1"


def test_analyzer_version_is_exactly_1_1() -> None:
    assert _artifact().analyzer_version == "1.1"


def test_schema_version_accepts_historical_1_0() -> None:
    artifact = _artifact(schema_version="1.0")
    assert artifact.schema_version == "1.0"


def test_analyzer_version_accepts_historical_1_0() -> None:
    artifact = _artifact(analyzer_version="1.0")
    assert artifact.analyzer_version == "1.0"


def test_schema_version_rejects_other_values() -> None:
    with pytest.raises(ValidationError):
        _artifact(schema_version="1.2")


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


# ---------------------------------------------------------------------------
# Rechazo de campos extra
# ---------------------------------------------------------------------------


def test_artifact_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        SemanticPropagationArtifact.model_validate(
            {**_artifact().model_dump(mode="json"), "unexpected_field": "x"}
        )


def test_fact_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        PropagatedValueFact.model_validate(
            {**_fact().model_dump(mode="json"), "unexpected_field": "x"}
        )


def test_barrier_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        PropagationBarrier.model_validate(
            {**_barrier().model_dump(mode="json"), "unexpected_field": "x"}
        )


def test_source_reference_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        PropagationSourceReference.model_validate(
            {**_source_reference().model_dump(mode="json"), "unexpected_field": "x"}
        )


def test_step_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        PropagationStep.model_validate({**_step().model_dump(mode="json"), "unexpected_field": "x"})


def test_program_propagation_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ProgramSemanticPropagation.model_validate(
            {**_program_propagation().model_dump(mode="json"), "unexpected_field": "x"}
        )


def test_summary_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        SemanticPropagationSummary.model_validate(
            {**_summary().model_dump(mode="json"), "unexpected_field": "x"}
        )


# ---------------------------------------------------------------------------
# Validadores por PropagationFactKind
# ---------------------------------------------------------------------------


def test_direct_literal_requires_literal() -> None:
    with pytest.raises(ValidationError):
        _fact(literal=None)
    ok = _fact(literal="0005")
    assert ok.fact_kind == PropagationFactKind.DIRECT_LITERAL


def test_propagated_literal_requires_literal_and_two_steps() -> None:
    step1 = _step()
    step2 = _step(
        statement_id="PROG1::A::1::MOVE",
        effect_id="effect::PROG1::A::PROG1::A::1::MOVE::COPY_VALUE::0",
        operation=SemanticEffectKind.COPY_VALUE,
        source_variable="WS-COD-AUX",
        target_variable="WS-COD-RETORNO",
    )
    base = {
        "fact_kind": PropagationFactKind.PROPAGATED_LITERAL,
        "target_variable": "WS-COD-RETORNO",
        "source_variable": "WS-COD-AUX",
        "support_status": SemanticSupportStatus.PARTIALLY_SUPPORTED,
    }
    with pytest.raises(ValidationError):
        _fact(**base, literal=None, derivation_steps=[step1, step2], derivation_depth=2)
    with pytest.raises(ValidationError):
        _fact(**base, literal="0005", derivation_steps=[step1], derivation_depth=1)
    ok = _fact(**base, literal="0005", derivation_steps=[step1, step2], derivation_depth=2)
    assert ok.derivation_depth == 2


def test_condition_literal_requires_literal_and_set_condition_true_step() -> None:
    condition_step = _step(operation=SemanticEffectKind.SET_CONDITION_TRUE)
    base = {
        "fact_kind": PropagationFactKind.CONDITION_LITERAL,
        "target_variable": "WS-COD-AUX",
        "support_status": SemanticSupportStatus.FULLY_SUPPORTED,
    }
    with pytest.raises(ValidationError):
        _fact(**base, literal=None, derivation_steps=[condition_step], derivation_depth=1)
    non_condition_step = _step(operation=SemanticEffectKind.ASSIGN_LITERAL)
    with pytest.raises(ValidationError):
        _fact(**base, literal="0005", derivation_steps=[non_condition_step], derivation_depth=1)
    ok = _fact(**base, literal="0005", derivation_steps=[condition_step], derivation_depth=1)
    assert ok.fact_kind == PropagationFactKind.CONDITION_LITERAL


def test_unresolved_copy_requires_source_variable_and_no_literal() -> None:
    base = {
        "fact_kind": PropagationFactKind.UNRESOLVED_COPY,
        "target_variable": "WS-B",
        "support_status": SemanticSupportStatus.PARTIALLY_SUPPORTED,
        "derivation_steps": [],
        "derivation_depth": 0,
    }
    with pytest.raises(ValidationError):
        _fact(**base, source_variable=None, literal=None)
    with pytest.raises(ValidationError):
        _fact(**base, source_variable="WS-A", literal="0005")
    ok = _fact(**base, source_variable="WS-A", literal=None)
    assert ok.source_variable == "WS-A"


def test_invalidated_value_cannot_assert_literal() -> None:
    with pytest.raises(ValidationError):
        _fact(
            fact_kind=PropagationFactKind.INVALIDATED_VALUE,
            literal="0005",
            derivation_steps=[],
            derivation_depth=0,
            support_status=SemanticSupportStatus.PARTIALLY_SUPPORTED,
        )
    ok = _fact(
        fact_kind=PropagationFactKind.INVALIDATED_VALUE,
        literal=None,
        derivation_steps=[],
        derivation_depth=0,
        support_status=SemanticSupportStatus.PARTIALLY_SUPPORTED,
    )
    assert ok.fact_kind == PropagationFactKind.INVALIDATED_VALUE


def test_blocked_propagation_cannot_assert_literal() -> None:
    with pytest.raises(ValidationError):
        _fact(
            fact_kind=PropagationFactKind.BLOCKED_PROPAGATION,
            literal="0005",
            derivation_steps=[],
            derivation_depth=0,
            support_status=SemanticSupportStatus.UNSUPPORTED,
        )
    ok = _fact(
        fact_kind=PropagationFactKind.BLOCKED_PROPAGATION,
        literal=None,
        derivation_steps=[],
        derivation_depth=0,
        support_status=SemanticSupportStatus.UNSUPPORTED,
    )
    assert ok.fact_kind == PropagationFactKind.BLOCKED_PROPAGATION


def test_derivation_depth_must_match_len_derivation_steps() -> None:
    with pytest.raises(ValidationError):
        _fact(derivation_steps=[_step()], derivation_depth=2)
    with pytest.raises(ValidationError):
        _fact(derivation_steps=[_step(), _step()], derivation_depth=1)


# ---------------------------------------------------------------------------
# diagnostic_codes / affected_variables: ordenados, sin duplicados
# ---------------------------------------------------------------------------


def test_fact_diagnostic_codes_rejects_unsorted_or_duplicate() -> None:
    with pytest.raises(ValidationError):
        _fact(diagnostic_codes=["B", "A"])
    with pytest.raises(ValidationError):
        _fact(diagnostic_codes=["A", "A"])
    ok = _fact(diagnostic_codes=["A", "B"])
    assert ok.diagnostic_codes == ["A", "B"]


def test_barrier_affected_variables_rejects_unsorted_or_duplicate() -> None:
    with pytest.raises(ValidationError):
        _barrier(affected_variables=["WS-B", "WS-A"])
    with pytest.raises(ValidationError):
        _barrier(affected_variables=["WS-A", "WS-A"])
    ok = _barrier(affected_variables=["WS-A", "WS-B"])
    assert ok.affected_variables == ["WS-A", "WS-B"]


# ---------------------------------------------------------------------------
# source_file relativo / ausencia de rutas absolutas / ausencia de source_text
# ---------------------------------------------------------------------------


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


def test_fact_has_no_source_text_field() -> None:
    assert "source_text" not in PropagatedValueFact.model_fields
    assert "source_text" not in PropagationSourceReference.model_fields
    assert "source_text" not in PropagationStep.model_fields


def test_artifact_has_no_timestamp_fields() -> None:
    payload = _artifact().to_stable_json()
    for forbidden in ("generated_at", "timestamp", "created_at", "updated_at"):
        assert forbidden not in payload


# ---------------------------------------------------------------------------
# Contadores negativos / coherencia del summary
# ---------------------------------------------------------------------------


def test_summary_rejects_negative_counts() -> None:
    with pytest.raises(ValidationError):
        _summary(counts_by_fact_kind={PropagationFactKind.DIRECT_LITERAL: -1})
    with pytest.raises(ValidationError):
        _summary(counts_by_barrier_reason={PropagationBarrierReason.COMPUTED_VALUE: -1})


def test_summary_counts_by_fact_kind_must_sum_to_fact_count() -> None:
    with pytest.raises(ValidationError):
        _summary(fact_count=2)


def test_summary_counts_by_barrier_reason_must_sum_to_barrier_count() -> None:
    with pytest.raises(ValidationError):
        _summary(barrier_count=1)


def test_summary_named_counts_must_match_counts_by_fact_kind() -> None:
    with pytest.raises(ValidationError):
        _summary(
            direct_literal_count=0,
            counts_by_fact_kind={PropagationFactKind.DIRECT_LITERAL: 1},
        )


def test_program_propagation_counts_must_match_lengths() -> None:
    with pytest.raises(ValidationError):
        _program_propagation(fact_count=2)
    with pytest.raises(ValidationError):
        _program_propagation(barrier_count=1)


def test_artifact_summary_must_match_aggregation_of_programs() -> None:
    with pytest.raises(ValidationError):
        _artifact(summary=_summary(fact_count=99))


def test_artifact_summary_program_count_must_match_programs_length() -> None:
    with pytest.raises(ValidationError):
        _artifact(summary=_summary(program_count=2))


def test_artifact_summary_paragraph_count_must_match_aggregation() -> None:
    with pytest.raises(ValidationError):
        _artifact(summary=_summary(paragraph_count=99))


# ---------------------------------------------------------------------------
# CONDITION_FALSE_VALUE_UNDETERMINED (correccion post-Fase-4: SET condicion
# TO FALSE ya no se clasifica como MULTIPLE_CONDITION_VALUES)
# ---------------------------------------------------------------------------


def test_condition_false_value_undetermined_serializes_exact_value() -> None:
    barrier = _barrier(
        barrier_id="barrier::PROG1::A::root::PROG1::A::1::SET::CONDITION_FALSE_VALUE_UNDETERMINED::0",
        reason=PropagationBarrierReason.CONDITION_FALSE_VALUE_UNDETERMINED,
        diagnostic_code="SET_CONDITION_FALSE_HAS_NO_UNIQUE_PARENT_VALUE",
    )
    assert barrier.reason.value == "CONDITION_FALSE_VALUE_UNDETERMINED"
    payload = barrier.to_stable_json()
    assert '"reason": "CONDITION_FALSE_VALUE_UNDETERMINED"' in payload
    restored = PropagationBarrier.model_validate_json(payload)
    assert restored.reason == PropagationBarrierReason.CONDITION_FALSE_VALUE_UNDETERMINED


def test_condition_false_value_undetermined_round_trip_is_byte_identical() -> None:
    barrier = _barrier(
        barrier_id="barrier::PROG1::A::root::PROG1::A::1::SET::CONDITION_FALSE_VALUE_UNDETERMINED::0",
        reason=PropagationBarrierReason.CONDITION_FALSE_VALUE_UNDETERMINED,
        diagnostic_code="SET_CONDITION_FALSE_HAS_NO_UNIQUE_PARENT_VALUE",
    )
    first = barrier.to_stable_json()
    second = PropagationBarrier.model_validate_json(first).to_stable_json()
    assert first == second


def test_summary_counts_condition_false_value_undetermined_correctly() -> None:
    summary = _summary(
        barrier_count=1,
        counts_by_barrier_reason={PropagationBarrierReason.CONDITION_FALSE_VALUE_UNDETERMINED: 1},
    )
    assert summary.counts_by_barrier_reason[
        PropagationBarrierReason.CONDITION_FALSE_VALUE_UNDETERMINED
    ] == 1


# ---------------------------------------------------------------------------
# Ordenamiento deterministico / deduplicacion
# ---------------------------------------------------------------------------


def test_program_propagation_rejects_unsorted_facts() -> None:
    fact_z = _fact(fact_id="fact::PROG1::A::root::Z::PROG1::A::1::MOVE::DIRECT_LITERAL::0")
    fact_a = _fact(fact_id="fact::PROG1::A::root::A::PROG1::A::1::MOVE::DIRECT_LITERAL::0")
    with pytest.raises(ValidationError):
        _program_propagation(fact_count=2, facts=[fact_z, fact_a])


def test_program_propagation_rejects_duplicate_fact_ids() -> None:
    duplicate = _fact()
    with pytest.raises(ValidationError):
        _program_propagation(fact_count=2, facts=[duplicate, duplicate])


def test_program_propagation_rejects_unsorted_barriers() -> None:
    barrier_z = _barrier(
        barrier_id="barrier::PROG1::A::root::PROG1::A::1::MOVE::COMPUTED_VALUE::9"
    )
    barrier_a = _barrier(
        barrier_id="barrier::PROG1::A::root::PROG1::A::1::MOVE::COMPUTED_VALUE::1"
    )
    with pytest.raises(ValidationError):
        _program_propagation(
            fact_count=1, barrier_count=2, facts=[_fact()], barriers=[barrier_z, barrier_a]
        )


def test_program_propagation_rejects_duplicate_barrier_ids() -> None:
    duplicate = _barrier()
    with pytest.raises(ValidationError):
        _program_propagation(
            fact_count=1, barrier_count=2, facts=[_fact()], barriers=[duplicate, duplicate]
        )


def test_artifact_rejects_unsorted_programs() -> None:
    with pytest.raises(ValidationError):
        _artifact(
            programs=[_program_propagation(program="Z"), _program_propagation(program="A")],
            summary=_summary(program_count=2, fact_count=2, paragraph_count=2),
        )


def test_artifact_rejects_duplicate_program_names() -> None:
    with pytest.raises(ValidationError):
        _artifact(
            programs=[_program_propagation(program="A"), _program_propagation(program="A")],
            summary=_summary(program_count=2, fact_count=2, paragraph_count=2),
        )


# ---------------------------------------------------------------------------
# Serializacion byte a byte estable
# ---------------------------------------------------------------------------


def test_serialization_is_stable_across_two_identical_artifacts() -> None:
    assert _artifact().to_stable_json() == _artifact().to_stable_json()


def test_round_trip_produces_byte_identical_json() -> None:
    artifact = _artifact()
    first = artifact.to_stable_json()
    second = SemanticPropagationArtifact.model_validate_json(first).to_stable_json()
    assert first == second


def test_artifact_requires_at_least_one_source_artifact_hash() -> None:
    with pytest.raises(ValidationError):
        _artifact(source_artifact_hashes={})
