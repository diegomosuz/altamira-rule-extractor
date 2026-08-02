"""Tests contractuales de los efectos semanticos normalizados (Fase 2 de
la ampliacion semantica, checkpoint `feat/semantic-effects-foundation`):
`SemanticEffectsArtifact` y sus modelos anidados
(`contracts/semantic_effects.py`). NO contractual respecto a
`artifacts/01-10` -- ver docstring del modulo bajo prueba."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts.enums import LocationKind, StatementKind, TableAccessOperation
from altamira_extractor.contracts.semantic_coverage import SemanticSupportStatus
from altamira_extractor.contracts.semantic_effects import (
    REQUIRED_SOURCE_ARTIFACT_KEYS,
    ProgramSemanticEffects,
    SemanticEffect,
    SemanticEffectKind,
    SemanticEffectsArtifact,
    SemanticEffectSourceReference,
    SemanticEffectsSummary,
)

_HASH = "a" * 64


def _source_reference(**overrides: object) -> SemanticEffectSourceReference:
    defaults: dict[str, object] = {
        "program": "PROG1",
        "paragraph": "A",
        "statement_id": "PROG1::A::1::MOVE",
        "statement_kind": StatementKind.MOVE,
        "location_kind": LocationKind.UNKNOWN,
    }
    defaults.update(overrides)
    return SemanticEffectSourceReference(**defaults)  # type: ignore[arg-type]


def _effect(**overrides: object) -> SemanticEffect:
    defaults: dict[str, object] = {
        "effect_id": "effect::PROG1::A::PROG1::A::1::MOVE::ASSIGN_LITERAL::0",
        "kind": SemanticEffectKind.ASSIGN_LITERAL,
        "support_status": SemanticSupportStatus.FULLY_SUPPORTED,
        "source_reference": _source_reference(),
        "writes": ["W"],
        "target_data_items": ["W"],
        "literal": "X",
        "explanation": "MOVE literal directo.",
    }
    defaults.update(overrides)
    return SemanticEffect(**defaults)  # type: ignore[arg-type]


def _program_effects(**overrides: object) -> ProgramSemanticEffects:
    defaults: dict[str, object] = {
        "program": "PROG1",
        "effect_count": 1,
        "effects": [_effect()],
    }
    defaults.update(overrides)
    return ProgramSemanticEffects(**defaults)  # type: ignore[arg-type]


def _summary(**overrides: object) -> SemanticEffectsSummary:
    defaults: dict[str, object] = {
        "program_count": 1,
        "effect_count": 1,
        "counts_by_kind": {SemanticEffectKind.ASSIGN_LITERAL: 1},
        "counts_by_support_status": {SemanticSupportStatus.FULLY_SUPPORTED: 1},
    }
    defaults.update(overrides)
    return SemanticEffectsSummary(**defaults)  # type: ignore[arg-type]


def _artifact(**overrides: object) -> SemanticEffectsArtifact:
    defaults: dict[str, object] = {
        "run_id": "run-1",
        "source_package_hash": _HASH,
        "source_artifact_hashes": {key: _HASH for key in REQUIRED_SOURCE_ARTIFACT_KEYS},
        "summary": _summary(),
        "programs": [_program_effects()],
    }
    defaults.update(overrides)
    return SemanticEffectsArtifact(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# schema_version / analyzer_version
# ---------------------------------------------------------------------------


def test_schema_version_defaults_to_1_2() -> None:
    assert _artifact().schema_version == "1.2"


def test_analyzer_version_defaults_to_1_2() -> None:
    assert _artifact().analyzer_version == "1.2"


def test_schema_version_accepts_historical_1_0() -> None:
    # Fase 3 (soporte nivel 88) subio schema_version a "1.1" (la FORMA
    # cambio: SemanticEffect gano condition_name/parent_data_item/
    # condition_values, SemanticEffectKind gano SET_CONDITION_TRUE/FALSE);
    # un semantic-effects.json generado por el analizador de la Fase 2
    # ("1.0") debe seguir cargando.
    assert _artifact(schema_version="1.0").schema_version == "1.0"


def test_analyzer_version_accepts_historical_1_0() -> None:
    assert _artifact(analyzer_version="1.0").analyzer_version == "1.0"


def test_schema_version_rejects_other_values() -> None:
    with pytest.raises(ValidationError):
        _artifact(schema_version="2.0")


def test_analyzer_version_rejects_other_values() -> None:
    with pytest.raises(ValidationError):
        _artifact(analyzer_version="1.3")


# ---------------------------------------------------------------------------
# Serializacion estable / igualdad byte a byte / sin timestamps
# ---------------------------------------------------------------------------


def test_serialization_is_stable_across_two_identical_artifacts() -> None:
    assert _artifact().to_stable_json() == _artifact().to_stable_json()


def test_round_trip_produces_byte_identical_json() -> None:
    artifact = _artifact()
    first = artifact.to_stable_json()
    second = SemanticEffectsArtifact.model_validate_json(first).to_stable_json()
    assert first == second


def test_serialization_never_contains_timestamp_like_keys() -> None:
    payload = _artifact().to_stable_json()
    for forbidden in ("generated_at", "timestamp", "created_at", "updated_at"):
        assert forbidden not in payload


# ---------------------------------------------------------------------------
# Rechazo de campos extra
# ---------------------------------------------------------------------------


def test_artifact_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        SemanticEffectsArtifact.model_validate(
            {**_artifact().model_dump(mode="json"), "unexpected_field": "x"}
        )


def test_effect_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        SemanticEffect.model_validate(
            {**_effect().model_dump(mode="json"), "unexpected_field": "x"}
        )


def test_source_reference_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        SemanticEffectSourceReference.model_validate(
            {**_source_reference().model_dump(mode="json"), "unexpected_field": "x"}
        )


def test_program_effects_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ProgramSemanticEffects.model_validate(
            {**_program_effects().model_dump(mode="json"), "unexpected_field": "x"}
        )


def test_summary_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        SemanticEffectsSummary.model_validate(
            {**_summary().model_dump(mode="json"), "unexpected_field": "x"}
        )


# ---------------------------------------------------------------------------
# Validadores de coherencia por SemanticEffectKind
# ---------------------------------------------------------------------------


def test_assign_literal_requires_literal_and_target() -> None:
    with pytest.raises(ValidationError):
        _effect(literal=None)
    with pytest.raises(ValidationError):
        _effect(target_data_items=[])


def test_copy_value_requires_source_and_target() -> None:
    base = {
        "kind": SemanticEffectKind.COPY_VALUE,
        "support_status": SemanticSupportStatus.PARTIALLY_SUPPORTED,
        "literal": None,
    }
    with pytest.raises(ValidationError):
        _effect(**base, reads=["S"], writes=["T"], source_data_items=[], target_data_items=["T"])
    with pytest.raises(ValidationError):
        _effect(**base, reads=["S"], writes=["T"], source_data_items=["S"], target_data_items=[])
    ok = _effect(
        **base, reads=["S"], writes=["T"], source_data_items=["S"], target_data_items=["T"]
    )
    assert ok.kind == SemanticEffectKind.COPY_VALUE


def test_compute_value_requires_expression_and_target() -> None:
    base = {
        "kind": SemanticEffectKind.COMPUTE_VALUE,
        "support_status": SemanticSupportStatus.PARTIALLY_SUPPORTED,
        "literal": None,
    }
    with pytest.raises(ValidationError):
        _effect(**base, writes=["T"], target_data_items=["T"], expression=None)
    with pytest.raises(ValidationError):
        _effect(**base, writes=["T"], target_data_items=[], expression="A + B")
    ok = _effect(**base, writes=["T"], target_data_items=["T"], expression="A + B")
    assert ok.expression == "A + B"


def test_control_transfer_requires_control_targets() -> None:
    with pytest.raises(ValidationError):
        _effect(
            kind=SemanticEffectKind.CONTROL_TRANSFER,
            support_status=SemanticSupportStatus.FULLY_SUPPORTED,
            literal=None,
            target_data_items=[],
            writes=[],
            control_targets=[],
        )
    ok = _effect(
        kind=SemanticEffectKind.CONTROL_TRANSFER,
        support_status=SemanticSupportStatus.FULLY_SUPPORTED,
        literal=None,
        target_data_items=[],
        writes=[],
        control_targets=["OTHER-PARA"],
    )
    assert ok.control_targets == ["OTHER-PARA"]


def test_execute_sql_requires_sql_operation() -> None:
    with pytest.raises(ValidationError):
        _effect(
            kind=SemanticEffectKind.EXECUTE_SQL,
            support_status=SemanticSupportStatus.PARTIALLY_SUPPORTED,
            literal=None,
            target_data_items=[],
            writes=[],
            sql_operation=None,
        )
    ok = _effect(
        kind=SemanticEffectKind.EXECUTE_SQL,
        support_status=SemanticSupportStatus.PARTIALLY_SUPPORTED,
        literal=None,
        target_data_items=[],
        writes=[],
        sql_operation=TableAccessOperation.READS,
        sql_tables=["CUSTOMER"],
    )
    assert ok.sql_operation == TableAccessOperation.READS


# ---------------------------------------------------------------------------
# sql_host_variables: campo neutral, direccion no inferida sin evidencia
# ---------------------------------------------------------------------------


def test_sql_host_variables_defaults_to_empty_list() -> None:
    effect = _effect(
        kind=SemanticEffectKind.EXECUTE_SQL,
        support_status=SemanticSupportStatus.PARTIALLY_SUPPORTED,
        literal=None,
        target_data_items=[],
        writes=[],
        sql_operation=TableAccessOperation.READS,
        sql_tables=["CUSTOMER"],
    )
    assert effect.sql_host_variables == []


def test_sql_host_variables_rejects_unsorted_values() -> None:
    with pytest.raises(ValidationError):
        _effect(
            kind=SemanticEffectKind.EXECUTE_SQL,
            support_status=SemanticSupportStatus.PARTIALLY_SUPPORTED,
            literal=None,
            target_data_items=[],
            writes=[],
            sql_operation=TableAccessOperation.READS,
            sql_tables=["CUSTOMER"],
            sql_host_variables=["WS-B", "WS-A"],
        )


def test_sql_host_variables_rejects_duplicates() -> None:
    with pytest.raises(ValidationError):
        _effect(
            kind=SemanticEffectKind.EXECUTE_SQL,
            support_status=SemanticSupportStatus.PARTIALLY_SUPPORTED,
            literal=None,
            target_data_items=[],
            writes=[],
            sql_operation=TableAccessOperation.READS,
            sql_tables=["CUSTOMER"],
            sql_host_variables=["WS-A", "WS-A"],
        )


def test_sql_host_variables_accepts_sorted_unique_values() -> None:
    effect = _effect(
        kind=SemanticEffectKind.EXECUTE_SQL,
        support_status=SemanticSupportStatus.PARTIALLY_SUPPORTED,
        literal=None,
        target_data_items=[],
        writes=[],
        sql_operation=TableAccessOperation.READS,
        sql_tables=["CUSTOMER"],
        sql_host_variables=["WS-A", "WS-B"],
    )
    assert effect.sql_host_variables == ["WS-A", "WS-B"]


def test_execute_sql_rejects_reads_or_writes_when_direction_unresolved() -> None:
    with pytest.raises(ValidationError):
        _effect(
            kind=SemanticEffectKind.EXECUTE_SQL,
            support_status=SemanticSupportStatus.PARTIALLY_SUPPORTED,
            literal=None,
            target_data_items=[],
            reads=["WS-A"],
            writes=[],
            sql_operation=TableAccessOperation.READS,
            sql_tables=["CUSTOMER"],
            sql_host_variables=["WS-A"],
            diagnostic_codes=["SQL_HOST_VARIABLE_DIRECTION_UNRESOLVED"],
        )
    with pytest.raises(ValidationError):
        _effect(
            kind=SemanticEffectKind.EXECUTE_SQL,
            support_status=SemanticSupportStatus.PARTIALLY_SUPPORTED,
            literal=None,
            target_data_items=[],
            reads=[],
            writes=["WS-A"],
            sql_operation=TableAccessOperation.READS,
            sql_tables=["CUSTOMER"],
            sql_host_variables=["WS-A"],
            diagnostic_codes=["SQL_HOST_VARIABLE_DIRECTION_UNRESOLVED"],
        )


def test_preserved_statement_cannot_assert_writes_target_or_literal() -> None:
    base = {
        "kind": SemanticEffectKind.PRESERVED_STATEMENT,
        "support_status": SemanticSupportStatus.PRESERVED_ONLY,
    }
    with pytest.raises(ValidationError):
        _effect(**base, writes=["W"], target_data_items=[], literal=None)
    with pytest.raises(ValidationError):
        _effect(**base, writes=[], target_data_items=["W"], literal=None)
    with pytest.raises(ValidationError):
        _effect(**base, writes=[], target_data_items=[], literal="X")
    ok = _effect(**base, writes=[], target_data_items=[], literal=None)
    assert ok.kind == SemanticEffectKind.PRESERVED_STATEMENT


def test_unsupported_statement_requires_diagnostic_codes() -> None:
    base = {
        "kind": SemanticEffectKind.UNSUPPORTED_STATEMENT,
        "support_status": SemanticSupportStatus.UNSUPPORTED,
        "literal": None,
        "writes": [],
        "target_data_items": [],
    }
    with pytest.raises(ValidationError):
        _effect(**base, diagnostic_codes=[])
    ok = _effect(**base, diagnostic_codes=["DECLARED_UNSUPPORTED_BY_PRODUCER"])
    assert ok.diagnostic_codes == ["DECLARED_UNSUPPORTED_BY_PRODUCER"]


# ---------------------------------------------------------------------------
# source_file relativo / orden de lineas
# ---------------------------------------------------------------------------


def test_source_reference_rejects_absolute_source_file() -> None:
    with pytest.raises(ValidationError):
        _source_reference(source_file="C:/absolute/path.cbl")
    with pytest.raises(ValidationError):
        _source_reference(source_file="/absolute/path.cbl")


def test_source_reference_accepts_relative_source_file() -> None:
    reference = _source_reference(
        source_file="01-codigo/cobol/a.cbl",
        line_start=1,
        line_end=1,
        location_kind=LocationKind.EXACT,
    )
    assert reference.source_file == "01-codigo/cobol/a.cbl"


def test_source_reference_rejects_line_end_before_line_start() -> None:
    with pytest.raises(ValidationError):
        _source_reference(
            source_file="a.cbl", line_start=5, line_end=1, location_kind=LocationKind.EXACT
        )


# ---------------------------------------------------------------------------
# Contadores / coherencia de counts_by_kind y counts_by_support_status
# ---------------------------------------------------------------------------


def test_summary_rejects_negative_counts() -> None:
    with pytest.raises(ValidationError):
        _summary(counts_by_kind={SemanticEffectKind.ASSIGN_LITERAL: -1})
    with pytest.raises(ValidationError):
        _summary(counts_by_support_status={SemanticSupportStatus.FULLY_SUPPORTED: -1})


def test_summary_counts_by_kind_must_sum_to_effect_count() -> None:
    with pytest.raises(ValidationError):
        _summary(effect_count=2)


def test_program_effects_effect_count_must_match_len_effects() -> None:
    with pytest.raises(ValidationError):
        _program_effects(effect_count=2)


def test_artifact_summary_must_match_aggregation_of_programs() -> None:
    with pytest.raises(ValidationError):
        _artifact(summary=_summary(effect_count=99))


def test_artifact_summary_program_count_must_match_programs_length() -> None:
    with pytest.raises(ValidationError):
        _artifact(summary=_summary(program_count=2))


def test_artifact_requires_all_source_artifact_keys() -> None:
    with pytest.raises(ValidationError):
        _artifact(source_artifact_hashes={})


# ---------------------------------------------------------------------------
# Ordenamiento deterministico / deduplicacion
# ---------------------------------------------------------------------------


def test_program_effects_rejects_unsorted_effects() -> None:
    effect_z = _effect(
        effect_id="effect::PROG1::A::PROG1::A::9::MOVE::ASSIGN_LITERAL::0",
        source_reference=_source_reference(statement_id="PROG1::A::9::MOVE"),
    )
    effect_a = _effect(
        effect_id="effect::PROG1::A::PROG1::A::1::MOVE::ASSIGN_LITERAL::0",
        source_reference=_source_reference(statement_id="PROG1::A::1::MOVE"),
    )
    with pytest.raises(ValidationError):
        _program_effects(effect_count=2, effects=[effect_z, effect_a])


def test_program_effects_rejects_duplicate_effect_ids() -> None:
    duplicate = _effect()
    with pytest.raises(ValidationError):
        _program_effects(effect_count=2, effects=[duplicate, duplicate])


def test_artifact_rejects_unsorted_programs() -> None:
    with pytest.raises(ValidationError):
        _artifact(
            programs=[_program_effects(program="Z"), _program_effects(program="A")],
            summary=_summary(program_count=2, effect_count=2),
        )


def test_artifact_rejects_duplicate_program_names() -> None:
    with pytest.raises(ValidationError):
        _artifact(
            programs=[_program_effects(program="A"), _program_effects(program="A")],
            summary=_summary(program_count=2, effect_count=2),
        )


# ---------------------------------------------------------------------------
# Nunca source_text completo, nunca ruta absoluta
# ---------------------------------------------------------------------------


def test_effect_has_no_source_text_field() -> None:
    assert "source_text" not in SemanticEffect.model_fields
    assert "source_text" not in SemanticEffectSourceReference.model_fields
