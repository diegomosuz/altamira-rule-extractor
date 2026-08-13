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
    DomainGlossaryEntry,
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


def test_code_slice_and_evidence_source_file_none_matches_schema(
    valid_context_package: ContextPackage, context_package_schema: dict[str, Any]
) -> None:
    """Fase 15B4-CANDIDATE-QUALITY-5A: `source_file=None` en
    `CodeSliceEntry`/`EvidenceEntry` (programas con COPY, Paragraph con
    location_kind != EXACT) es un estado legitimo -- debe seguir
    validando tanto contra el contrato Pydantic como contra
    `context-package.schema.json` (dos validaciones independientes,
    ninguna debe quedar desincronizada de la otra)."""
    payload = valid_context_package.model_dump(mode="json")
    payload["code_slice"][0]["source_file"] = None
    payload["evidence"][0]["source_file"] = None
    restored = ContextPackage.model_validate(payload)
    assert restored.code_slice[0].source_file is None
    assert restored.evidence[0].source_file is None
    assert_matches_schema(restored.model_dump(mode="json"), context_package_schema)


def test_scope_source_file_still_required_non_null(
    valid_context_package: ContextPackage,
) -> None:
    """`ContextPackageScope.source_file` proviene de `Program.source_file`
    (Q1), que permanece siempre conocido incluso en programas con COPY
    (a diferencia de `Paragraph.source_file`) -- nunca se vuelve
    Optional."""
    payload = valid_context_package.model_dump(mode="json")
    payload["scope"]["source_file"] = None
    with pytest.raises(ValidationError):
        ContextPackage.model_validate(payload)


def test_historical_string_source_file_still_valid_after_5a(
    valid_context_package: ContextPackage, context_package_schema: dict[str, Any]
) -> None:
    """Fase 15B4-CANDIDATE-QUALITY-5A-SAFETY: `source_file` volverse
    nullable (Fase 5A) es una relajacion PURAMENTE ADITIVA -- un
    artefacto historico donde `code_slice[].source_file`/
    `evidence[].source_file` son cadenas reales (el caso normal, sin
    COPY) sigue validando exactamente igual, sin migracion, tanto
    contra el contrato Pydantic como contra
    `context-package.schema.json`. `valid_context_package` (fixture
    compartida, usa strings reales) es en si mismo ese caso historico."""
    assert isinstance(valid_context_package.code_slice[0].source_file, str)
    assert isinstance(valid_context_package.evidence[0].source_file, str)
    payload = valid_context_package.model_dump(mode="json")
    restored = ContextPackage.model_validate(payload)
    assert restored == valid_context_package
    assert_matches_schema(restored.model_dump(mode="json"), context_package_schema)


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
        ApplicableParameterRow(  # type: ignore[arg-type]
            parameter_entry_id="pe-1", values={"limite": 1}, approved_for_rule_text=False
        )


def test_context_row_must_be_approved_false() -> None:
    with pytest.raises(ValidationError):
        ContextParameterRow(  # type: ignore[arg-type]
            parameter_entry_id="pe-1", values={"limite": 1}, approved_for_rule_text=True
        )


def test_applicable_row_default_is_approved() -> None:
    row = ApplicableParameterRow(parameter_entry_id="pe-1", values={"limite": 1})
    assert row.approved_for_rule_text is True


def test_applicable_row_requires_parameter_entry_id() -> None:
    with pytest.raises(ValidationError):
        ApplicableParameterRow(values={"limite": 1})  # type: ignore[call-arg]


def test_context_row_requires_parameter_entry_id() -> None:
    with pytest.raises(ValidationError):
        ContextParameterRow(values={"limite": 1})  # type: ignore[call-arg]


def test_unresolved_table_cannot_have_applicable_rows() -> None:
    with pytest.raises(ValidationError):
        ParameterTableContext(
            name="PARM01",
            applicability_status=ApplicabilityStatus.UNRESOLVED,
            applicable_rows=[
                ApplicableParameterRow(parameter_entry_id="pe-1", values={"limite": 1})
            ],
        )


def test_not_applicable_table_cannot_have_applicable_rows() -> None:
    with pytest.raises(ValidationError):
        ParameterTableContext(
            name="PARM01",
            applicability_status=ApplicabilityStatus.NOT_APPLICABLE,
            applicable_rows=[
                ApplicableParameterRow(parameter_entry_id="pe-1", values={"limite": 1})
            ],
        )


def test_unresolved_table_can_have_context_rows() -> None:
    table = ParameterTableContext(
        name="PARM01",
        applicability_status=ApplicabilityStatus.UNRESOLVED,
        context_rows=[ContextParameterRow(parameter_entry_id="pe-1", values={"limite": 1})],
    )
    assert table.applicable_rows == []


def test_same_parameter_entry_id_cannot_be_in_both_applicable_and_context_rows() -> None:
    with pytest.raises(ValidationError):
        ParameterTableContext(
            name="PARM01",
            applicability_status=ApplicabilityStatus.PARTIAL,
            applicable_rows=[
                ApplicableParameterRow(parameter_entry_id="pe-1", values={"limite": 1})
            ],
            context_rows=[ContextParameterRow(parameter_entry_id="pe-1", values={"limite": 1})],
        )


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


# --- DomainGlossaryEntry.data_item_id (Prompt 10b) ---


def _domain_glossary_entry(**overrides: object) -> DomainGlossaryEntry:
    defaults: dict[str, object] = {
        "data_item_id": "program::AR::op::PROG::1::abc123::data::WS-MONTO",
        "technical_name": "WS-MONTO",
        "semantic_tag": "amount",
        "domain_term_id": "term::1.0::requested_amount",
        "functional_name": "importe solicitado",
        "definition": "Importe solicitado por el cliente",
        "entity_type": "monetary_amount",
        "source_kind": "CURATED_CONFIG",
        "authoritative_source": "V1 controlled glossary",
        "confidence": 1.0,
        "evidence_ids": ["ev-1"],
    }
    defaults.update(overrides)
    return DomainGlossaryEntry(**defaults)  # type: ignore[arg-type]


def test_domain_glossary_entry_requires_data_item_id() -> None:
    with pytest.raises(ValidationError):
        DomainGlossaryEntry(
            technical_name="WS-MONTO",
            semantic_tag="amount",
            domain_term_id="term::1.0::requested_amount",
            functional_name="importe solicitado",
            definition="Importe solicitado por el cliente",
            entity_type="monetary_amount",
            source_kind="CURATED_CONFIG",
            authoritative_source="V1 controlled glossary",
            confidence=1.0,
            evidence_ids=["ev-1"],
        )  # type: ignore[call-arg]


def test_domain_glossary_entry_valid_with_data_item_id() -> None:
    entry = _domain_glossary_entry()
    assert entry.data_item_id == "program::AR::op::PROG::1::abc123::data::WS-MONTO"


def test_domain_glossary_entry_data_item_id_cannot_be_empty() -> None:
    with pytest.raises(ValidationError):
        _domain_glossary_entry(data_item_id="")


def test_full_payload_with_new_fields_matches_schema(
    valid_context_package: ContextPackage, context_package_schema: dict[str, Any]
) -> None:
    assert_matches_schema(valid_context_package.model_dump(mode="json"), context_package_schema)
    dumped = valid_context_package.model_dump(mode="json")
    applicable_row = dumped["data_context"]["parameter_tables"][0]["applicable_rows"][0]
    assert applicable_row["parameter_entry_id"]
    glossary_entry = dumped["domain_glossary"][0]
    assert glossary_entry["data_item_id"]


# ---------------------------------------------------------------------------
# Fase 15B3-C2-B2: decision/candidate.decision_id opcionales, UNICAMENTE
# ambos presentes o ambos ausentes (CALCULATION incondicional) -- nunca un
# estado mixto.
# ---------------------------------------------------------------------------


def test_historical_context_package_with_decision_present_matches_schema_unchanged(
    valid_context_package: ContextPackage, context_package_schema: dict[str, Any]
) -> None:
    """El formato historico (decision/candidate.decision_id siempre
    presentes) sigue parseando y validando contra el schema exactamente
    igual -- el contrato no se relaja para el caso comun."""
    assert valid_context_package.decision is not None
    assert valid_context_package.candidate.decision_id is not None
    assert_matches_schema(valid_context_package.model_dump(mode="json"), context_package_schema)
    restored = ContextPackage.model_validate_json(valid_context_package.to_stable_json())
    assert restored == valid_context_package


def test_context_package_decision_id_present_without_decision_is_invalid(
    valid_context_package: ContextPackage,
) -> None:
    payload = valid_context_package.model_dump(mode="json")
    payload["decision"] = None
    with pytest.raises(ValidationError):
        ContextPackage(**payload)


def test_context_package_decision_present_without_decision_id_is_invalid(
    valid_context_package: ContextPackage,
) -> None:
    payload = valid_context_package.model_dump(mode="json")
    payload["candidate"]["decision_id"] = None
    with pytest.raises(ValidationError):
        ContextPackage(**payload)


def test_context_package_unconditional_calculation_both_absent_is_valid_and_matches_schema(
    valid_context_package: ContextPackage, context_package_schema: dict[str, Any]
) -> None:
    payload = valid_context_package.model_dump(mode="json")
    payload["decision"] = None
    payload["candidate"]["decision_id"] = None
    package = ContextPackage(**payload)
    assert package.decision is None
    assert package.candidate.decision_id is None
    assert_matches_schema(package.model_dump(mode="json"), context_package_schema)
    restored = ContextPackage.model_validate_json(package.to_stable_json())
    assert restored == package
