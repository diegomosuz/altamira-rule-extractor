"""Tests del ensamblado en dos pasos de RuleDraft (Prompt 12)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from altamira_extractor.contracts.context_package import (
    BatchContext,
    CodeSliceEntry,
    Completeness,
    ContextPackage,
    ContextPackageCandidate,
    ContextPackageDecision,
    ContextPackageOperation,
    ContextPackageScope,
    DataContext,
    Effects,
    EvidenceEntry,
)
from altamira_extractor.contracts.enums import (
    BatchContextStatus,
    CompletenessStatus,
    EvidenceValidationStatus,
    InclusionReason,
)
from altamira_extractor.pipeline.rule_draft_assembly import (
    RuleDraftAssemblyError,
    assemble_rule_draft,
    check_evidence_references,
    load_rule_draft_schema,
    rule_draft_json_hash,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_SCHEMA_PATH = REPO_ROOT / "schemas" / "rule-draft.schema.json"
_HASH_A = "a" * 64


def _package() -> ContextPackage:
    evidence = EvidenceEntry(
        evidence_id="ev-1",
        kind="decision",
        source_file="cobol/PROG1.cbl",
        line_start=10,
        line_end=10,
        source_package_hash=_HASH_A,
    )
    return ContextPackage(
        schema_version="2.0",
        candidate=ContextPackageCandidate(
            candidate_id="cand-1",
            decision_id="dec-1",
            detector_id="det",
            detector_version="1.0",
            detector_score=1.0,
        ),
        scope=ContextPackageScope(
            country="AR",
            application="Transferencias",
            operation=ContextPackageOperation(logical_name="OP1", description=None),
            program="PROG1",
            program_version="1",
            paragraph="MAIN",
            source_file="cobol/PROG1.cbl",
            line_start=10,
            line_end=10,
            source_package_hash=_HASH_A,
        ),
        code_slice=[
            CodeSliceEntry(
                paragraph_id="p1",
                paragraph="MAIN",
                source_file="cobol/PROG1.cbl",
                source_text="IF WS-COD = 'R001'",
                line_start=10,
                line_end=10,
                inclusion_reason=InclusionReason.CANDIDATE,
                evidence_ids=["ev-1"],
            )
        ],
        data_context=DataContext(parameter_tables=[], transactional_tables_read=[]),
        decision=ContextPackageDecision(
            expression="WS-COD = 'R001'",
            normalized_expression="WS-COD = 'R001'",
            operands=[],
            rule_type=None,
            outcome_code="R001",
            evidence_ids=["ev-1"],
        ),
        effects=Effects(return_codes=[], table_effects=[]),
        batch_context=BatchContext(status=BatchContextStatus.NOT_AVAILABLE, downstream_jobs=[]),
        domain_glossary=[],
        evidence=[evidence],
        completeness=Completeness(
            D1=CompletenessStatus.COMPLETE,
            D2=CompletenessStatus.COMPLETE,
            D3=CompletenessStatus.NOT_AVAILABLE,
            D4=CompletenessStatus.COMPLETE,
            D5=CompletenessStatus.NOT_AVAILABLE,
            D6=CompletenessStatus.NOT_AVAILABLE,
            D7=CompletenessStatus.NOT_AVAILABLE,
        ),
    )


def _valid_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": "Limite de monto",
        "context": "Transferencias en Argentina",
        "statement": "Si el monto supera el limite, se rechaza",
        "condition": "WS-MONTO > WS-LIMITE",
        "parameters": ["limite=1000"],
        "effect": "Se actualiza CUENTAS",
        "parameter_source": "PARM01",
        "traceability": ["ev-1"],
        "limitations": ["Requiere revision funcional"],
        "claims": [
            {
                "claim_id": "claim-1",
                "field": "statement",
                "evidence_paths": ["$.decision.expression"],
                "evidence_ids": ["ev-1"],
            }
        ],
    }
    payload.update(overrides)
    return payload


def _real_schema_validator() -> jsonschema.protocols.Validator:
    schema, _hash = load_rule_draft_schema(REAL_SCHEMA_PATH)
    validator_cls = jsonschema.validators.validator_for(schema)
    return validator_cls(schema)


# --- load_rule_draft_schema ---


def test_load_real_schema_succeeds() -> None:
    schema, schema_hash = load_rule_draft_schema(REAL_SCHEMA_PATH)
    assert schema["title"] == "RuleDraft"
    assert len(schema_hash) == 64


def test_load_missing_schema_raises(tmp_path: Path) -> None:
    with pytest.raises(RuleDraftAssemblyError):
        load_rule_draft_schema(tmp_path / "missing.json")


def test_load_invalid_json_schema_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(RuleDraftAssemblyError):
        load_rule_draft_schema(path)


def test_load_not_a_json_schema_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad-schema.json"
    path.write_text(json.dumps({"type": "not-a-real-type"}), encoding="utf-8")
    with pytest.raises(RuleDraftAssemblyError):
        load_rule_draft_schema(path)


# --- assemble_rule_draft ---


def test_assemble_valid_payload_produces_pending_rule_draft() -> None:
    validator = _real_schema_validator()
    rule_draft = assemble_rule_draft(_valid_payload(), schema_validator=validator)
    assert rule_draft.evidence_validation_status == EvidenceValidationStatus.PENDING
    assert rule_draft.schema_version == "2.0"


@pytest.mark.parametrize(
    "forbidden_key",
    ["schema_version", "evidence_validation_status", "functional_review_status"],
)
def test_assemble_rejects_forbidden_self_assigned_keys(forbidden_key: str) -> None:
    validator = _real_schema_validator()
    payload = _valid_payload(**{forbidden_key: "anything"})
    with pytest.raises(RuleDraftAssemblyError):
        assemble_rule_draft(payload, schema_validator=validator)


def test_assemble_missing_required_field_raises() -> None:
    validator = _real_schema_validator()
    payload = _valid_payload()
    del payload["title"]
    with pytest.raises(RuleDraftAssemblyError):
        assemble_rule_draft(payload, schema_validator=validator)


def test_assemble_extra_field_raises() -> None:
    validator = _real_schema_validator()
    payload = _valid_payload(unexpected_field="nope")
    with pytest.raises(RuleDraftAssemblyError):
        assemble_rule_draft(payload, schema_validator=validator)


def test_assemble_wrong_type_raises() -> None:
    validator = _real_schema_validator()
    payload = _valid_payload(parameters="not-a-list")
    with pytest.raises(RuleDraftAssemblyError):
        assemble_rule_draft(payload, schema_validator=validator)


def test_assemble_claim_with_non_jsonpath_evidence_path_raises() -> None:
    validator = _real_schema_validator()
    payload = _valid_payload(
        claims=[
            {
                "claim_id": "claim-1",
                "field": "statement",
                "evidence_paths": ["decision.expression"],
                "evidence_ids": ["ev-1"],
            }
        ]
    )
    with pytest.raises(RuleDraftAssemblyError):
        assemble_rule_draft(payload, schema_validator=validator)


def test_assemble_never_includes_raw_payload_in_error_message() -> None:
    validator = _real_schema_validator()
    payload = _valid_payload(title="SECRET_MARKER_VALUE_12345")
    del payload["condition"]
    with pytest.raises(RuleDraftAssemblyError) as excinfo:
        assemble_rule_draft(payload, schema_validator=validator)
    assert "SECRET_MARKER_VALUE_12345" not in str(excinfo.value)


# --- validation_errors (checkpoint correctivo: reparacion estructural) ---


def test_missing_field_error_is_exposed_as_structured_issue() -> None:
    validator = _real_schema_validator()
    payload = _valid_payload()
    del payload["title"]
    with pytest.raises(RuleDraftAssemblyError) as excinfo:
        assemble_rule_draft(payload, schema_validator=validator)
    issues = excinfo.value.validation_errors
    assert len(issues) == 1
    assert issues[0].loc == "title"
    assert issues[0].type == "missing"
    assert issues[0].msg


def test_extra_field_error_is_exposed_as_structured_issue() -> None:
    validator = _real_schema_validator()
    payload = _valid_payload(unexpected_field="nope")
    with pytest.raises(RuleDraftAssemblyError) as excinfo:
        assemble_rule_draft(payload, schema_validator=validator)
    issues = excinfo.value.validation_errors
    assert len(issues) == 1
    assert issues[0].loc == "unexpected_field"
    assert issues[0].type == "extra_forbidden"


def test_wrong_type_error_is_exposed_as_structured_issue() -> None:
    validator = _real_schema_validator()
    payload = _valid_payload(parameters="not-a-list")
    with pytest.raises(RuleDraftAssemblyError) as excinfo:
        assemble_rule_draft(payload, schema_validator=validator)
    issues = excinfo.value.validation_errors
    assert len(issues) == 1
    assert issues[0].loc == "parameters"


def test_forbidden_self_assigned_key_is_exposed_as_structured_issue() -> None:
    validator = _real_schema_validator()
    payload = _valid_payload(schema_version="2.0")
    with pytest.raises(RuleDraftAssemblyError) as excinfo:
        assemble_rule_draft(payload, schema_validator=validator)
    issues = excinfo.value.validation_errors
    assert len(issues) == 1
    assert issues[0].loc == "schema_version"
    assert issues[0].type == "forbidden_self_assignment"


def test_validation_errors_never_include_the_invalid_value_itself() -> None:
    validator = _real_schema_validator()
    secret_marker = "SECRET_INVALID_VALUE_MARKER"
    payload = _valid_payload(parameters=secret_marker)
    with pytest.raises(RuleDraftAssemblyError) as excinfo:
        assemble_rule_draft(payload, schema_validator=validator)
    for issue in excinfo.value.validation_errors:
        assert secret_marker not in issue.msg
        assert secret_marker not in issue.loc


def test_str_message_unchanged_for_existing_guardrails_applied_consumer() -> None:
    # GUARDRAILS_APPLIED sigue leyendo unicamente str(exc): agregar
    # validation_errors no puede alterar ese texto.
    validator = _real_schema_validator()
    payload = _valid_payload()
    del payload["title"]
    with pytest.raises(RuleDraftAssemblyError) as excinfo:
        assemble_rule_draft(payload, schema_validator=validator)
    assert str(excinfo.value) == "el payload funcional no valida contra RuleDraft"


# --- check_evidence_references (checkpoint correctivo) ---


def test_evidence_references_valid_against_real_package_has_no_issues() -> None:
    validator = _real_schema_validator()
    package = _package()
    payload = _valid_payload(
        claims=[
            {
                "claim_id": "c1",
                "field": "statement",
                "evidence_paths": ["$.decision.expression"],
                "evidence_ids": ["ev-1"],
            }
        ]
    )
    rule_draft = assemble_rule_draft(payload, schema_validator=validator)
    assert check_evidence_references(rule_draft, package) == ()


def test_evidence_references_unknown_evidence_id_is_flagged() -> None:
    validator = _real_schema_validator()
    package = _package()
    payload = _valid_payload(
        claims=[
            {
                "claim_id": "c1",
                "field": "statement",
                "evidence_paths": ["$.decision.expression"],
                "evidence_ids": ["ev-does-not-exist"],
            }
        ]
    )
    rule_draft = assemble_rule_draft(payload, schema_validator=validator)
    issues = check_evidence_references(rule_draft, package)
    assert len(issues) == 1
    assert issues[0].type == "unknown_evidence_id"
    assert issues[0].loc == "claims.0.evidence_ids.0"


def test_evidence_references_unknown_evidence_path_is_flagged() -> None:
    validator = _real_schema_validator()
    package = _package()
    payload = _valid_payload(
        claims=[
            {
                "claim_id": "c1",
                "field": "statement",
                "evidence_paths": ["$.decision.does_not_exist"],
                "evidence_ids": ["ev-1"],
            }
        ]
    )
    rule_draft = assemble_rule_draft(payload, schema_validator=validator)
    issues = check_evidence_references(rule_draft, package)
    assert len(issues) == 1
    assert issues[0].type == "unknown_evidence_path"
    assert issues[0].loc == "claims.0.evidence_paths.0"


def test_evidence_references_prefix_or_partial_match_is_rejected() -> None:
    validator = _real_schema_validator()
    package = _package()
    # "$.decisio" es un prefijo de "$.decision" (path real), pero nunca
    # se acepta como coincidencia parcial.
    payload = _valid_payload(
        claims=[
            {
                "claim_id": "c1",
                "field": "statement",
                "evidence_paths": ["$.decisio"],
                "evidence_ids": ["ev-1"],
            }
        ]
    )
    rule_draft = assemble_rule_draft(payload, schema_validator=validator)
    issues = check_evidence_references(rule_draft, package)
    assert len(issues) == 1
    assert issues[0].type == "unknown_evidence_path"


def test_evidence_references_never_correct_or_invent_an_id() -> None:
    # check_evidence_references solo reporta: nunca reescribe claims ni
    # sugiere un id "parecido" -- eso queda para el ciclo de reparacion
    # via LLM, no para esta funcion determinista.
    validator = _real_schema_validator()
    package = _package()
    payload = _valid_payload(
        claims=[
            {
                "claim_id": "c1",
                "field": "statement",
                "evidence_paths": ["$.decision.expression"],
                "evidence_ids": ["ev-1-typo"],
            }
        ]
    )
    rule_draft = assemble_rule_draft(payload, schema_validator=validator)
    issues = check_evidence_references(rule_draft, package)
    assert len(issues) == 1
    assert rule_draft.claims[0].evidence_ids == ["ev-1-typo"]


# --- rule_draft_json_hash ---


def test_rule_draft_json_hash_is_deterministic() -> None:
    validator = _real_schema_validator()
    draft_a = assemble_rule_draft(_valid_payload(), schema_validator=validator)
    draft_b = assemble_rule_draft(_valid_payload(), schema_validator=validator)
    assert rule_draft_json_hash(draft_a) == rule_draft_json_hash(draft_b)


def test_rule_draft_json_hash_changes_with_content() -> None:
    validator = _real_schema_validator()
    draft_a = assemble_rule_draft(_valid_payload(), schema_validator=validator)
    draft_b = assemble_rule_draft(_valid_payload(title="Otro titulo"), schema_validator=validator)
    assert rule_draft_json_hash(draft_a) != rule_draft_json_hash(draft_b)
