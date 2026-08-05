"""Tests de `contracts/operational_audit.py` (Fase 15B1 Parte 16,
seccion AUDITORIA 66-82, `feat/final-hardening-release`)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts.operational_audit import (
    AuditAction,
    AuditOutcome,
    OperationalAuditActivePointer,
    OperationalAuditEvent,
    compute_audit_event_id,
    outcome_for_action,
)
from altamira_extractor.contracts.operational_authorization_request import OperationalAction
from altamira_extractor.contracts.security_config import ApplicationRole, AuthenticationMode
from altamira_extractor.contracts.unified_materialization_authorization import (
    UnifiedMaterializationReasonCode,
)


def _event_id(**kwargs: object) -> str:
    defaults: dict[str, object] = {
        "run_id": "run-1",
        "sequence": 1,
        "action": AuditAction.LOGIN_CONTEXT_RESOLVED,
        "outcome": AuditOutcome.ALLOWED,
        "principal_id": "alice",
        "correlation_id": "corr-1",
        "previous_audit_event_id": None,
        "operational_action": None,
        "activation_event_id": None,
        "reason_code": None,
        "error_code": None,
    }
    defaults.update(kwargs)
    return compute_audit_event_id(**defaults)  # type: ignore[arg-type]


def _event(**overrides: object) -> OperationalAuditEvent:
    base: dict[str, object] = {
        "audit_event_id": _event_id(),
        "run_id": "run-1",
        "sequence": 1,
        "previous_audit_event_id": None,
        "action": AuditAction.LOGIN_CONTEXT_RESOLVED,
        "outcome": AuditOutcome.ALLOWED,
        "principal_id": "alice",
        "authentication_mode": AuthenticationMode.DISABLED_DEV,
        "correlation_id": "corr-1",
    }
    base.update(overrides)
    return OperationalAuditEvent.model_validate(base)


# 66. audit_event_id es determinista y EXCLUYE occurred_at_utc/diagnostics.
def test_audit_event_id_excludes_timestamp_and_diagnostics() -> None:
    id_a = _event_id()
    id_b = _event_id()
    assert id_a == id_b


# 67. Cambiar cualquier campo incluido produce un id distinto.
@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "run-2"),
        ("sequence", 2),
        ("principal_id", "bob"),
        ("correlation_id", "corr-2"),
    ],
)
def test_audit_event_id_changes_with_included_fields(field: str, value: object) -> None:
    assert _event_id(**{field: value}) != _event_id()


# 68. outcome_for_action es la correspondencia 1:1 unica -- ejemplos de cada categoria.
@pytest.mark.parametrize(
    "action,expected",
    [
        (AuditAction.LOGIN_CONTEXT_RESOLVED, AuditOutcome.ALLOWED),
        (AuditAction.AUTHORIZATION_REJECTED, AuditOutcome.DENIED),
        (AuditAction.ACTIVATION_CANARY_SUCCEEDED, AuditOutcome.SUCCEEDED),
        (AuditAction.OPERATION_FAILED, AuditOutcome.FAILED),
        (AuditAction.CSRF_REJECTED, AuditOutcome.DENIED),
    ],
)
def test_outcome_for_action_mapping(action: AuditAction, expected: AuditOutcome) -> None:
    assert outcome_for_action(action) == expected


# 69. sequence=1 no puede declarar previous_audit_event_id.
def test_sequence_one_forbids_previous_event() -> None:
    with pytest.raises(ValidationError):
        _event(sequence=1, previous_audit_event_id="audit-x")


# 70. sequence>1 exige previous_audit_event_id.
def test_sequence_above_one_requires_previous_event() -> None:
    with pytest.raises(ValidationError):
        _event(sequence=2, previous_audit_event_id=None)


# 71. outcome debe coincidir exactamente con la accion.
def test_outcome_mismatch_rejected() -> None:
    with pytest.raises(ValidationError):
        _event(action=AuditAction.LOGIN_CONTEXT_RESOLVED, outcome=AuditOutcome.DENIED)


# 72. DENIED/FAILED exigen error_code; ALLOWED/SUCCEEDED lo prohiben.
def test_error_code_required_for_denied_and_failed_only() -> None:
    with pytest.raises(ValidationError):
        _event(action=AuditAction.ACCESS_DENIED, outcome=AuditOutcome.DENIED, error_code=None)
    with pytest.raises(ValidationError):
        _event(
            action=AuditAction.LOGIN_CONTEXT_RESOLVED,
            outcome=AuditOutcome.ALLOWED,
            error_code="x",
        )


# 73. ACTIVATION_CANARY_SUCCEEDED exige operational_action + reason_code + activation_event_id.
def test_activation_succeeded_requires_full_context() -> None:
    _event(
        action=AuditAction.ACTIVATION_CANARY_SUCCEEDED,
        outcome=AuditOutcome.SUCCEEDED,
        operational_action=OperationalAction.ACTIVATE_UNIFIED_CANARY,
        reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
        activation_event_id="event-x",
    )
    with pytest.raises(ValidationError):
        _event(
            action=AuditAction.ACTIVATION_CANARY_SUCCEEDED,
            outcome=AuditOutcome.SUCCEEDED,
            operational_action=OperationalAction.ACTIVATE_UNIFIED_CANARY,
            reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
            activation_event_id=None,
        )


# 74. OPERATION_FAILED exige operational_action pero reason_code es OPCIONAL.
def test_operation_failed_reason_code_is_optional() -> None:
    _event(
        action=AuditAction.OPERATION_FAILED,
        outcome=AuditOutcome.FAILED,
        operational_action=OperationalAction.ACTIVATE_UNIFIED_CANARY,
        error_code="operational_precondition_failed",
    )
    _event(
        action=AuditAction.OPERATION_FAILED,
        outcome=AuditOutcome.FAILED,
        operational_action=OperationalAction.ACTIVATE_UNIFIED_CANARY,
        reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
        error_code="operational_precondition_failed",
    )
    with pytest.raises(ValidationError):
        _event(action=AuditAction.OPERATION_FAILED, outcome=AuditOutcome.FAILED, error_code="x")


# 75. ACCESS_DENIED/CSRF_REJECTED/LOGIN_CONTEXT_RESOLVED nunca declaran operational_action.
def test_context_free_actions_forbid_operational_context() -> None:
    with pytest.raises(ValidationError):
        _event(
            action=AuditAction.ACCESS_DENIED,
            outcome=AuditOutcome.DENIED,
            operational_action=OperationalAction.ACTIVATE_UNIFIED_CANARY,
            error_code="forbidden",
        )


# 76. activation_event_id solo en las 4 acciones *_SUCCEEDED.
def test_activation_event_id_scoped_to_succeeded_actions() -> None:
    with pytest.raises(ValidationError):
        _event(
            action=AuditAction.ACTIVATION_CANARY_REQUESTED,
            outcome=AuditOutcome.ALLOWED,
            operational_action=OperationalAction.ACTIVATE_UNIFIED_CANARY,
            reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
            activation_event_id="event-x",
        )


# 77. roles no puede contener duplicados.
def test_roles_must_be_unique() -> None:
    with pytest.raises(ValidationError):
        _event(roles=[ApplicationRole.VIEWER, ApplicationRole.VIEWER])


# 78. diagnostics debe estar ordenado y sin duplicados.
def test_diagnostics_sorted_and_unique() -> None:
    with pytest.raises(ValidationError):
        _event(diagnostics=["b", "a"])


# 79. occurred_at_utc valida el formato ISO-8601 UTC (con sufijo Z).
def test_occurred_at_utc_format_validated() -> None:
    _event(occurred_at_utc="2026-08-04T12:00:00.000000Z")
    with pytest.raises(ValidationError):
        _event(occurred_at_utc="2026-08-04 12:00:00")


# 80. El evento nunca declara cookies/token CSRF/headers crudos/password/manifest completo.
def test_event_never_exposes_forbidden_fields() -> None:
    field_names = set(OperationalAuditEvent.model_fields)
    forbidden = {
        "cookie",
        "csrf_token",
        "raw_headers",
        "authorization_token",
        "session_secret",
        "api_key",
        "password",
        "manifest",
    }
    assert field_names.isdisjoint(forbidden)


# 81. OperationalAuditActivePointer exige pointer_version == event_count.
def test_active_pointer_requires_matching_counts() -> None:
    OperationalAuditActivePointer(
        run_id="run-1", pointer_version=2, latest_audit_event_id="audit-x", event_count=2
    )
    with pytest.raises(ValidationError):
        OperationalAuditActivePointer(
            run_id="run-1", pointer_version=2, latest_audit_event_id="audit-x", event_count=3
        )


# 82. Las 14 acciones y 4 outcomes son un catalogo cerrado exacto.
def test_action_and_outcome_catalogs_are_closed() -> None:
    assert len(list(AuditAction)) == 14
    assert set(AuditOutcome) == {
        AuditOutcome.ALLOWED,
        AuditOutcome.DENIED,
        AuditOutcome.SUCCEEDED,
        AuditOutcome.FAILED,
    }
