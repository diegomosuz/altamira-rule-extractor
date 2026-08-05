"""Tests de `contracts/operational_authorization_request.py` (Fase
15B1 Parte 16, seccion OPERACIONES 83-94, `feat/final-hardening-release`)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts.operational_authorization_request import (
    OperationalAction,
    OperationalAuthorizationRequest,
    PreparedOperationalIntent,
)
from altamira_extractor.contracts.unified_materialization_authorization import (
    UnifiedMaterializationAction,
    UnifiedMaterializationReasonCode,
)

_HASH = "a" * 64


def _request(**overrides: object) -> OperationalAuthorizationRequest:
    base: dict[str, object] = {
        "run_id": "run-1",
        "action": OperationalAction.ACTIVATE_UNIFIED_CANARY,
        "operator_principal_id": "alice",
        "distinct_reviewer_required": False,
        "expected_active_pointer_hash": _HASH,
        "reason_code": UnifiedMaterializationReasonCode.CANARY_APPROVED,
        "review_reference": "ticket-1",
        "approved_group_ids": ["group-a"],
    }
    base.update(overrides)
    return OperationalAuthorizationRequest.model_validate(base)


def _intent(**overrides: object) -> PreparedOperationalIntent:
    base: dict[str, object] = {
        "run_id": "run-1",
        "action": OperationalAction.ACTIVATE_UNIFIED_CANARY,
        "prepared_by_principal_id": "reviewer-1",
        "distinct_reviewer_required": False,
        "expected_active_pointer_hash": _HASH,
        "reason_code": UnifiedMaterializationReasonCode.CANARY_APPROVED,
        "review_reference": "ticket-1",
        "approved_group_ids": ["group-a"],
    }
    base.update(overrides)
    return PreparedOperationalIntent.model_validate(base)


# 83. KEEP_V1 no forma parte de OperationalAction (excluido deliberadamente).
def test_keep_v1_excluded_from_operational_action() -> None:
    assert "KEEP_V1" not in {a.value for a in OperationalAction}
    assert len(list(OperationalAction)) == 5


# 84. review_reference rechaza CR/LF (inyeccion).
def test_review_reference_rejects_control_characters() -> None:
    with pytest.raises(ValidationError):
        _request(review_reference="line1\r\nX-Injected: evil")


# 85. review_reference rechaza espacios en los bordes.
def test_review_reference_rejects_leading_trailing_whitespace() -> None:
    with pytest.raises(ValidationError):
        _request(review_reference="  padded  ")


# 86. ROLLBACK_TO_GENERATION exige target_generation_id.
def test_rollback_to_generation_requires_target() -> None:
    with pytest.raises(ValidationError):
        _request(
            action=OperationalAction.ROLLBACK_TO_GENERATION,
            approved_group_ids=[],
            target_generation_id=None,
        )


# 87. target_generation_id solo es valido con ROLLBACK_TO_GENERATION.
def test_target_generation_id_forbidden_outside_rollback_to_generation() -> None:
    with pytest.raises(ValidationError):
        _request(target_generation_id="generation-x")


# 88. approved_group_ids solo en ACTIVATE_UNIFIED_CANARY/PRIMARY.
def test_approved_group_ids_scoped_to_materializing_actions() -> None:
    with pytest.raises(ValidationError):
        _request(action=OperationalAction.FALLBACK_TO_V1)


# 89. approved_group_ids debe estar ordenado y sin duplicados.
def test_approved_group_ids_sorted_and_unique() -> None:
    with pytest.raises(ValidationError):
        _request(approved_group_ids=["group-b", "group-a"])
    with pytest.raises(ValidationError):
        _request(approved_group_ids=["group-a", "group-a"])


# 90. distinct_reviewer_required=true exige reviewer_principal_id.
def test_distinct_reviewer_required_needs_reviewer_id() -> None:
    with pytest.raises(ValidationError):
        _request(distinct_reviewer_required=True, reviewer_principal_id=None)


# 91. reviewer_principal_id (normalizado) no puede coincidir con operator_principal_id.
def test_reviewer_cannot_equal_operator_normalized() -> None:
    with pytest.raises(ValidationError):
        _request(
            action=OperationalAction.ACTIVATE_UNIFIED_PRIMARY,
            distinct_reviewer_required=True,
            reviewer_principal_id="  ALICE  ",
            operator_principal_id="alice",
        )


# 92. reviewer_principal_id solo es valido cuando distinct_reviewer_required=true.
def test_reviewer_id_forbidden_when_not_required() -> None:
    with pytest.raises(ValidationError):
        _request(distinct_reviewer_required=False, reviewer_principal_id="bob")


# 93. PreparedOperationalIntent nunca declara operator_principal_id (se resuelve en execute).
def test_prepared_intent_has_no_operator_field() -> None:
    assert "operator_principal_id" not in PreparedOperationalIntent.model_fields
    assert "prepared_by_principal_id" in PreparedOperationalIntent.model_fields


# 94. to_authorization_request construye la solicitud final resolviendo el operador desde
# la identidad autenticada de ejecucion, revalidando anti-autoaprobacion.
def test_to_authorization_request_builds_final_request_and_reapplies_validators() -> None:
    intent = _intent(
        action=OperationalAction.ACTIVATE_UNIFIED_PRIMARY,
        distinct_reviewer_required=True,
        prepared_by_principal_id="reviewer-1",
    )
    final = intent.to_authorization_request("operator-1")
    assert final.operator_principal_id == "operator-1"
    assert final.reviewer_principal_id == "reviewer-1"

    with pytest.raises(ValidationError):
        intent.to_authorization_request("reviewer-1")


def test_operational_action_maps_onto_unified_materialization_action_values() -> None:
    for action in OperationalAction:
        assert action.value in {m.value for m in UnifiedMaterializationAction}
