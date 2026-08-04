"""Tests de `UnifiedMaterializationAuthorization` (Fase 14B Parte 3/15
items 1-12, `feat/controlled-unified-materialization`)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts.unified_activation_evaluation import (
    UnifiedActivationReadinessDisposition,
)
from altamira_extractor.contracts.unified_materialization_authorization import (
    UnifiedMaterializationAction,
    UnifiedMaterializationAuthorization,
    UnifiedMaterializationReasonCode,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
RUN_ID = "20260101T000000000000-aaaaaaaa"


def _auth(**overrides: object) -> UnifiedMaterializationAuthorization:
    base: dict[str, object] = {
        "run_id": RUN_ID,
        "activation_evaluation_hash": HASH_A,
        "expected_readiness_disposition": UnifiedActivationReadinessDisposition.V1_ONLY_READY,
        "action": UnifiedMaterializationAction.KEEP_V1,
        "reason_code": UnifiedMaterializationReasonCode.KEEP_BASELINE,
        "review_reference": "reviewer-ref",
    }
    base.update(overrides)
    return UnifiedMaterializationAuthorization(**base)  # type: ignore[arg-type]


# 1. autorizacion KEEP_V1 valida.
def test_keep_v1_valid() -> None:
    auth = _auth()
    assert auth.action == UnifiedMaterializationAction.KEEP_V1
    assert auth.materialization_enabled is True


# 2. autorizacion canary valida.
def test_canary_valid() -> None:
    auth = _auth(
        action=UnifiedMaterializationAction.ACTIVATE_UNIFIED_CANARY,
        expected_readiness_disposition=UnifiedActivationReadinessDisposition.READY_FOR_UNIFIED_CANARY,
        reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
        approved_group_ids=["group::a"],
        fallback_authorized=True,
    )
    assert auth.action == UnifiedMaterializationAction.ACTIVATE_UNIFIED_CANARY


# 3. autorizacion primary valida.
def test_primary_valid() -> None:
    auth = _auth(
        action=UnifiedMaterializationAction.ACTIVATE_UNIFIED_PRIMARY,
        expected_readiness_disposition=UnifiedActivationReadinessDisposition.READY_FOR_PRIMARY_TRIAL,
        reason_code=UnifiedMaterializationReasonCode.PRIMARY_TRIAL_APPROVED,
        approved_group_ids=["group::a"],
        fallback_authorized=True,
    )
    assert auth.action == UnifiedMaterializationAction.ACTIVATE_UNIFIED_PRIMARY


# 4. autorizacion fallback valida.
def test_fallback_valid() -> None:
    auth = _auth(
        action=UnifiedMaterializationAction.FALLBACK_TO_V1,
        reason_code=UnifiedMaterializationReasonCode.ACTIVE_GENERATION_INVALID,
        fallback_authorized=True,
    )
    assert auth.fallback_authorized is True


# 5. autorizacion rollback valida.
def test_rollback_valid() -> None:
    auth = _auth(
        action=UnifiedMaterializationAction.ROLLBACK_TO_PREVIOUS,
        reason_code=UnifiedMaterializationReasonCode.OPERATOR_ROLLBACK,
        rollback_authorized=True,
    )
    assert auth.rollback_authorized is True

    auth2 = _auth(
        action=UnifiedMaterializationAction.ROLLBACK_TO_GENERATION,
        reason_code=UnifiedMaterializationReasonCode.OPERATOR_ROLLBACK,
        rollback_authorized=True,
        target_generation_id="generation-abc",
    )
    assert auth2.target_generation_id == "generation-abc"


# 6. provider diferente rechazado.
def test_different_provider_rejected() -> None:
    with pytest.raises(ValidationError):
        UnifiedMaterializationAuthorization.model_validate(
            {
                **_auth().model_dump(mode="json"),
                "provider_policy": "PRODUCT_PROVIDER_EXPLICITLY_AUTHORIZED",
            }
        )


# 7. materialization false rechazado.
def test_materialization_false_rejected() -> None:
    with pytest.raises(ValidationError):
        UnifiedMaterializationAuthorization.model_validate(
            {**_auth().model_dump(mode="json"), "materialization_enabled": False}
        )


# 8. run_id incorrecto -- validado a nivel de SERVICIO (Parte 12), no del
# contrato aislado; aqui se confirma que el contrato acepta cualquier
# run_id no vacio (la comparacion contra el run real ocurre despues).
def test_run_id_field_requires_non_empty() -> None:
    with pytest.raises(ValidationError):
        UnifiedMaterializationAuthorization.model_validate(
            {**_auth().model_dump(mode="json"), "run_id": ""}
        )


# 9. evaluation hash incorrecto -- el contrato exige formato sha256 hex;
# la comparacion contra el hash REAL ocurre en el servicio (Parte 12).
def test_evaluation_hash_must_be_valid_sha256() -> None:
    with pytest.raises(ValidationError):
        UnifiedMaterializationAuthorization.model_validate(
            {**_auth().model_dump(mode="json"), "activation_evaluation_hash": "not-a-hash"}
        )


# 10. readiness incorrecta (canary sin la disposicion correspondiente).
def test_canary_wrong_readiness_rejected() -> None:
    with pytest.raises(ValidationError):
        _auth(
            action=UnifiedMaterializationAction.ACTIVATE_UNIFIED_CANARY,
            expected_readiness_disposition=UnifiedActivationReadinessDisposition.V1_ONLY_READY,
            reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
            approved_group_ids=["group::a"],
            fallback_authorized=True,
        )


def test_primary_wrong_readiness_rejected() -> None:
    with pytest.raises(ValidationError):
        _auth(
            action=UnifiedMaterializationAction.ACTIVATE_UNIFIED_PRIMARY,
            expected_readiness_disposition=UnifiedActivationReadinessDisposition.READY_FOR_UNIFIED_CANARY,
            reason_code=UnifiedMaterializationReasonCode.PRIMARY_TRIAL_APPROVED,
            approved_group_ids=["group::a"],
            fallback_authorized=True,
        )


# 11. approved groups incompletos -- KEEP_V1/FALLBACK/ROLLBACK nunca
# pueden declarar approved_group_ids (solo tiene sentido para
# materializaciones unified nuevas).
def test_keep_v1_cannot_declare_approved_groups() -> None:
    with pytest.raises(ValidationError):
        _auth(approved_group_ids=["group::a"])


def test_fallback_cannot_declare_approved_groups() -> None:
    with pytest.raises(ValidationError):
        _auth(
            action=UnifiedMaterializationAction.FALLBACK_TO_V1,
            reason_code=UnifiedMaterializationReasonCode.ACTIVE_GENERATION_INVALID,
            fallback_authorized=True,
            approved_group_ids=["group::a"],
        )


def test_canary_requires_at_least_declared_groups_field() -> None:
    """El contrato no exige minimo 1 (podria ser un canary de 0 grupos
    en un escenario degenerado); la NO-vacuidad real se valida en el
    constructor de generacion (Parte 7)."""
    auth = _auth(
        action=UnifiedMaterializationAction.ACTIVATE_UNIFIED_CANARY,
        expected_readiness_disposition=UnifiedActivationReadinessDisposition.READY_FOR_UNIFIED_CANARY,
        reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
        approved_group_ids=[],
        fallback_authorized=True,
    )
    assert auth.approved_group_ids == []


# 12. target generation requerido (ROLLBACK_TO_GENERATION).
def test_rollback_to_generation_requires_target() -> None:
    with pytest.raises(ValidationError):
        _auth(
            action=UnifiedMaterializationAction.ROLLBACK_TO_GENERATION,
            reason_code=UnifiedMaterializationReasonCode.OPERATOR_ROLLBACK,
            rollback_authorized=True,
        )


def test_target_generation_forbidden_outside_rollback_to_generation() -> None:
    with pytest.raises(ValidationError):
        _auth(
            action=UnifiedMaterializationAction.ROLLBACK_TO_PREVIOUS,
            reason_code=UnifiedMaterializationReasonCode.OPERATOR_ROLLBACK,
            rollback_authorized=True,
            target_generation_id="generation-abc",
        )


class TestAdditionalInvariants:
    def test_rollback_without_flag_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _auth(
                action=UnifiedMaterializationAction.ROLLBACK_TO_PREVIOUS,
                reason_code=UnifiedMaterializationReasonCode.OPERATOR_ROLLBACK,
                rollback_authorized=False,
            )

    def test_fallback_without_flag_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _auth(
                action=UnifiedMaterializationAction.FALLBACK_TO_V1,
                reason_code=UnifiedMaterializationReasonCode.ACTIVE_GENERATION_INVALID,
                fallback_authorized=False,
            )

    def test_approved_group_ids_must_be_sorted_and_unique(self) -> None:
        with pytest.raises(ValidationError):
            _auth(
                action=UnifiedMaterializationAction.ACTIVATE_UNIFIED_CANARY,
                expected_readiness_disposition=UnifiedActivationReadinessDisposition.READY_FOR_UNIFIED_CANARY,
                reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
                approved_group_ids=["group::b", "group::a"],
                fallback_authorized=True,
            )

    def test_no_operational_fields(self) -> None:
        fields = set(UnifiedMaterializationAuthorization.model_fields)
        forbidden_substrings = ("path", "key", "endpoint", "model", "token", "secret")
        for field_name in fields:
            lowered = field_name.lower()
            for forbidden in forbidden_substrings:
                assert forbidden not in lowered, f"{field_name!r} parece configuracion operativa"

    def test_review_reference_required_non_empty(self) -> None:
        with pytest.raises(ValidationError):
            _auth(review_reference="")
