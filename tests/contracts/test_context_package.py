"""Tests de ContextPackage, incluida compatibilidad con
schemas/context-package.schema.json."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts import (
    ApplicabilityStatus,
    ApplicableParameterRow,
    AttributionScope,
    ContextPackage,
    ContextPackageDecision,
    ContextParameterRow,
    ParameterTableContext,
    TableEffect,
    TableEffectOperation,
)

from .conftest import assert_matches_schema


def test_valid_context_package_matches_schema(
    valid_context_package: ContextPackage, context_package_schema: dict[str, Any]
) -> None:
    assert_matches_schema(valid_context_package.model_dump(mode="json"), context_package_schema)


def test_context_package_round_trips(valid_context_package: ContextPackage) -> None:
    restored = ContextPackage.model_validate_json(valid_context_package.to_stable_json())
    assert restored == valid_context_package


def test_context_package_requires_scope(valid_context_package: ContextPackage) -> None:
    payload = valid_context_package.model_dump(mode="json")
    del payload["scope"]
    with pytest.raises(ValidationError):
        ContextPackage.model_validate(payload)


def test_context_package_rejects_additional_properties(
    valid_context_package: ContextPackage,
) -> None:
    payload = valid_context_package.model_dump(mode="json")
    payload["unexpected"] = "nope"
    with pytest.raises(ValidationError):
        ContextPackage.model_validate(payload)


def test_context_package_rejects_wrong_schema_version(
    valid_context_package: ContextPackage,
) -> None:
    payload = valid_context_package.model_dump(mode="json")
    payload["schema_version"] = "1.9"
    with pytest.raises(ValidationError):
        ContextPackage.model_validate(payload)


def test_context_package_rejects_invalid_applicability_enum(
    valid_context_package: ContextPackage,
) -> None:
    payload = valid_context_package.model_dump(mode="json")
    payload["data_context"]["parameter_tables"][0]["applicability_status"] = "MAYBE"
    with pytest.raises(ValidationError):
        ContextPackage.model_validate(payload)


def test_context_package_rejects_invalid_source_package_hash(
    valid_context_package: ContextPackage,
) -> None:
    payload = valid_context_package.model_dump(mode="json")
    payload["scope"]["source_package_hash"] = "too-short"
    with pytest.raises(ValidationError):
        ContextPackage.model_validate(payload)


# --- decision.rule_type: str | None (StatementKind.IF/EVALUATE no lo demuestra) ---


def test_decision_rule_type_none_is_valid_and_matches_schema(
    valid_context_package: ContextPackage, context_package_schema: dict[str, Any]
) -> None:
    package_with_null_rule_type = valid_context_package.model_copy(
        update={"decision": valid_context_package.decision.model_copy(update={"rule_type": None})}
    )
    assert package_with_null_rule_type.decision.rule_type is None
    assert_matches_schema(
        package_with_null_rule_type.model_dump(mode="json"), context_package_schema
    )


def test_decision_rule_type_with_real_value_is_still_valid() -> None:
    decision = ContextPackageDecision(
        expression="WS-MONTO > WS-LIMITE",
        normalized_expression="WS-MONTO > WS-LIMITE",
        operands=["WS-MONTO", "WS-LIMITE"],
        rule_type="threshold-comparison",
        outcome_code="R001",
        evidence_ids=["ev-1"],
    )
    assert decision.rule_type == "threshold-comparison"


# --- filas parametricas aprobadas / no aprobadas ---------------------------


def test_applicable_row_must_be_approved_true() -> None:
    with pytest.raises(ValidationError):
        ApplicableParameterRow(values={"limite": 1}, approved_for_rule_text=False)  # type: ignore[arg-type]


def test_context_row_must_be_approved_false() -> None:
    with pytest.raises(ValidationError):
        ContextParameterRow(values={"limite": 1}, approved_for_rule_text=True)  # type: ignore[arg-type]


def test_applicable_row_default_is_approved() -> None:
    row = ApplicableParameterRow(values={"limite": 1})
    assert row.approved_for_rule_text is True


def test_unresolved_table_cannot_have_applicable_rows() -> None:
    with pytest.raises(ValidationError):
        ParameterTableContext(
            name="PARM01",
            applicability_status=ApplicabilityStatus.UNRESOLVED,
            applicable_rows=[ApplicableParameterRow(values={"limite": 1})],
        )


def test_not_applicable_table_cannot_have_applicable_rows() -> None:
    with pytest.raises(ValidationError):
        ParameterTableContext(
            name="PARM01",
            applicability_status=ApplicabilityStatus.NOT_APPLICABLE,
            applicable_rows=[ApplicableParameterRow(values={"limite": 1})],
        )


def test_unresolved_table_can_have_context_rows() -> None:
    table = ParameterTableContext(
        name="PARM01",
        applicability_status=ApplicabilityStatus.UNRESOLVED,
        context_rows=[ContextParameterRow(values={"limite": 1})],
    )
    assert table.applicable_rows == []


# --- efecto PROGRAM_CONTEXT --------------------------------------------------


def test_program_context_effect_cannot_be_approved() -> None:
    with pytest.raises(ValidationError):
        TableEffect(
            table="LOG_AUDITORIA",
            operation=TableEffectOperation.INSERTS,
            attribution_scope=AttributionScope.PROGRAM_CONTEXT,
            approved_for_rule_text=True,
            evidence_ids=["ev-1"],
        )


def test_program_context_effect_not_approved_is_valid() -> None:
    effect = TableEffect(
        table="LOG_AUDITORIA",
        operation=TableEffectOperation.INSERTS,
        attribution_scope=AttributionScope.PROGRAM_CONTEXT,
        approved_for_rule_text=False,
        evidence_ids=["ev-1"],
    )
    assert effect.approved_for_rule_text is False


def test_direct_effect_can_be_approved() -> None:
    effect = TableEffect(
        table="CUENTAS",
        operation=TableEffectOperation.UPDATES,
        attribution_scope=AttributionScope.DIRECT,
        approved_for_rule_text=True,
        evidence_ids=["ev-1"],
    )
    assert effect.approved_for_rule_text is True


def test_dependency_slice_effect_can_be_approved() -> None:
    effect = TableEffect(
        table="CUENTAS",
        operation=TableEffectOperation.UPDATES,
        attribution_scope=AttributionScope.DEPENDENCY_SLICE,
        approved_for_rule_text=True,
        evidence_ids=["ev-1"],
    )
    assert effect.approved_for_rule_text is True
