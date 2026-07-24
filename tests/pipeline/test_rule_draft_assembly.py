"""Tests del ensamblado en dos pasos de RuleDraft (Prompt 12)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from altamira_extractor.contracts.enums import EvidenceValidationStatus
from altamira_extractor.pipeline.rule_draft_assembly import (
    RuleDraftAssemblyError,
    assemble_rule_draft,
    load_rule_draft_schema,
    rule_draft_json_hash,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_SCHEMA_PATH = REPO_ROOT / "schemas" / "rule-draft.schema.json"


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
