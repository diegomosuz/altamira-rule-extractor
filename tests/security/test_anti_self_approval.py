"""Tests dedicados de prevencion de autoaprobacion (Fase 15B1 Parte 13,
`feat/final-hardening-release`).

Complementa (no duplica) los tests de
`test_operational_workflow.py::test_distinct_reviewer_required_disabled_in_dev_mode`/
`test_self_approval_rejected_for_distinct_reviewer_action` -- aqui se
cubren especificamente los items no probados alli: comparacion
normalizada de `principal_id`, insensibilidad a `display_name`/`email`,
el caso de exito con revisor realmente distinto, y la separacion
exigida tambien para rollback."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import SecretStr

from altamira_extractor.api.errors import ForbiddenError
from altamira_extractor.contracts.operational_authorization_request import OperationalAction
from altamira_extractor.contracts.security_config import (
    ApplicationRole,
    AuthenticationMode,
    SecurityConfig,
)
from altamira_extractor.contracts.security_identity import (
    AuthenticatedPrincipal,
    permissions_for_roles,
)
from altamira_extractor.contracts.unified_activation_materialization import (
    MaterializedActivationLane,
)
from altamira_extractor.contracts.unified_materialization_authorization import (
    UnifiedMaterializationReasonCode,
)
from altamira_extractor.pipeline.unified_materialization_service import (
    materialize_unified_activation,
)
from altamira_extractor.security.operational_workflow import (
    execute_operational_action,
    prepare_operational_action,
)
from altamira_extractor.security.session import new_session_data
from tests.pipeline._unified_materialization_fixtures import (
    build_materialization_fixture,
    write_authorization_yaml,
    write_run_dir,
)


def _bootstrap_run(tmp_path: Path) -> tuple[Path, str, list[str]]:
    fx = build_materialization_fixture()
    run_dir = write_run_dir(tmp_path, fx)
    eval_hash_path = run_dir / "diagnostics" / "unified-activation-evaluation.json"
    eval_hash = hashlib.sha256(eval_hash_path.read_bytes()).hexdigest()
    auth_path = tmp_path / "bootstrap-authorization.yaml"
    write_authorization_yaml(
        auth_path,
        run_id=fx.run_id,
        activation_evaluation_hash=eval_hash,
        action="KEEP_V1",
        expected_readiness_disposition="READY_FOR_UNIFIED_CANARY",
    )
    materialize_unified_activation(run_dir, fx.run_id, authorization_path=auth_path)
    return run_dir, fx.run_id, fx.approved_group_ids


def _principal(
    principal_id: str,
    roles: list[ApplicationRole],
    *,
    display_name: str | None = None,
    email: str | None = None,
) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        principal_id=principal_id,
        display_name=display_name or principal_id,
        email=email,
        authentication_mode=AuthenticationMode.TRUSTED_PROXY_HEADERS,
        roles=roles,
        permissions=permissions_for_roles(roles),
        groups=[],
        authenticated=True,
    )


def _config_requiring_primary_separation() -> SecurityConfig:
    return SecurityConfig(
        authentication_mode=AuthenticationMode.TRUSTED_PROXY_HEADERS,
        trusted_proxy_header_user="X-User",
        trusted_proxy_required_marker_header="X-Marker",
        trusted_proxy_required_marker_value=SecretStr("m"),
        session_cookie_name="altamira_session",
        session_cookie_secure=True,
        require_distinct_reviewer_for_primary=True,
        require_distinct_reviewer_for_rollback=True,
    )


def _trusted_config_without_separation() -> SecurityConfig:
    return SecurityConfig(
        authentication_mode=AuthenticationMode.TRUSTED_PROXY_HEADERS,
        trusted_proxy_header_user="X-User",
        trusted_proxy_required_marker_header="X-Marker",
        trusted_proxy_required_marker_value=SecretStr("m"),
        session_cookie_name="altamira_session",
        session_cookie_secure=True,
    )


# 60. Distinct reviewer required + mismo principal -> rechazado.
def test_same_principal_id_rejected_for_primary(tmp_path: Path) -> None:
    run_dir, run_id, groups = _bootstrap_run(tmp_path)
    person = _principal("alice", [ApplicationRole.ADMIN])
    session = new_session_data()
    config = _config_requiring_primary_separation()

    _intent, token = prepare_operational_action(
        run_dir=run_dir,
        run_id=run_id,
        action=OperationalAction.ACTIVATE_UNIFIED_PRIMARY,
        principal=person,
        security_config=config,
        session=session,
        reason_code=UnifiedMaterializationReasonCode.PRIMARY_TRIAL_APPROVED,
        review_reference="ticket-primary",
        approved_group_ids=groups,
        target_generation_id=None,
    )
    with pytest.raises(ForbiddenError):
        execute_operational_action(
            run_dir=run_dir, run_id=run_id, challenge_token=token, principal=person, session=session
        )


# 61. Distinct reviewer required + principal realmente distinto -> exito.
# Usa ROLLBACK_TO_PREVIOUS (tambien exige separacion en esta config) en
# vez de ACTIVATE_UNIFIED_PRIMARY: la fixture sintetica de este archivo
# produce `READY_FOR_UNIFIED_CANARY`, no `READY_FOR_PRIMARY_TRIAL` --
# construir un escenario real con esa segunda disposicion pertenece a
# los fixtures de Fase 14A, fuera de alcance de este test dedicado a
# autoaprobacion. El rollback no depende de la disposicion de
# readiness, solo de la separacion de identidad, que es lo que este
# item verifica.
def test_genuinely_distinct_principal_succeeds_for_rollback(tmp_path: Path) -> None:
    run_dir, run_id, groups = _bootstrap_run(tmp_path)
    operator = _principal("carol", [ApplicationRole.OPERATOR])
    no_separation_config = _trusted_config_without_separation()
    session = new_session_data()

    # Primero activa canary (sin exigir revisor distinto) para tener una
    # generacion "anterior" real a la que hacer rollback.
    canary_intent, canary_token = prepare_operational_action(
        run_dir=run_dir,
        run_id=run_id,
        action=OperationalAction.ACTIVATE_UNIFIED_CANARY,
        principal=operator,
        security_config=no_separation_config,
        session=session,
        reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
        review_reference="ticket-canary",
        approved_group_ids=groups,
        target_generation_id=None,
    )
    assert canary_intent.distinct_reviewer_required is False
    execute_operational_action(
        run_dir=run_dir,
        run_id=run_id,
        challenge_token=canary_token,
        principal=operator,
        session=session,
    )

    reviewer = _principal("alice", [ApplicationRole.REVIEWER])
    admin = _principal("bob", [ApplicationRole.ADMIN])
    config = _config_requiring_primary_separation()

    _intent, token = prepare_operational_action(
        run_dir=run_dir,
        run_id=run_id,
        action=OperationalAction.ROLLBACK_TO_PREVIOUS,
        principal=reviewer,
        security_config=config,
        session=session,
        reason_code=UnifiedMaterializationReasonCode.OPERATOR_ROLLBACK,
        review_reference="ticket-rollback",
        approved_group_ids=[],
        target_generation_id=None,
    )
    assert _intent.distinct_reviewer_required is True
    result = execute_operational_action(
        run_dir=run_dir, run_id=run_id, challenge_token=token, principal=admin, session=session
    )
    assert result.active_lane == MaterializedActivationLane.V1


# 62. Comparacion normalizada de principal_id (mismo id, distinto casing/espacios) -> rechazado.
def test_principal_id_comparison_is_normalized(tmp_path: Path) -> None:
    run_dir, run_id, groups = _bootstrap_run(tmp_path)
    preparer = _principal("Alice.Smith", [ApplicationRole.ADMIN])
    executor = _principal("  alice.smith  ", [ApplicationRole.ADMIN])
    session = new_session_data()
    config = _config_requiring_primary_separation()

    _intent, token = prepare_operational_action(
        run_dir=run_dir,
        run_id=run_id,
        action=OperationalAction.ACTIVATE_UNIFIED_PRIMARY,
        principal=preparer,
        security_config=config,
        session=session,
        reason_code=UnifiedMaterializationReasonCode.PRIMARY_TRIAL_APPROVED,
        review_reference="ticket-primary",
        approved_group_ids=groups,
        target_generation_id=None,
    )
    with pytest.raises(ForbiddenError):
        execute_operational_action(
            run_dir=run_dir,
            run_id=run_id,
            challenge_token=token,
            principal=executor,
            session=session,
        )


# 63. Variar SOLO display_name/email (mismo principal_id) no basta para satisfacer la distincion.
def test_varying_display_name_or_email_alone_is_insufficient(tmp_path: Path) -> None:
    run_dir, run_id, groups = _bootstrap_run(tmp_path)
    preparer = _principal("alice", [ApplicationRole.ADMIN], display_name="Alice A.")
    executor = _principal(
        "alice", [ApplicationRole.ADMIN], display_name="Alice B.", email="alice@example.com"
    )
    session = new_session_data()
    config = _config_requiring_primary_separation()

    _intent, token = prepare_operational_action(
        run_dir=run_dir,
        run_id=run_id,
        action=OperationalAction.ACTIVATE_UNIFIED_PRIMARY,
        principal=preparer,
        security_config=config,
        session=session,
        reason_code=UnifiedMaterializationReasonCode.PRIMARY_TRIAL_APPROVED,
        review_reference="ticket-primary",
        approved_group_ids=groups,
        target_generation_id=None,
    )
    with pytest.raises(ForbiddenError):
        execute_operational_action(
            run_dir=run_dir,
            run_id=run_id,
            challenge_token=token,
            principal=executor,
            session=session,
        )


# 64. DISABLED_DEV deshabilita por completo la activacion primary cuando exige separacion.
def test_disabled_dev_disables_primary_when_separation_required(tmp_path: Path) -> None:
    run_dir, run_id, groups = _bootstrap_run(tmp_path)
    roles = [ApplicationRole.VIEWER]
    dev_principal = AuthenticatedPrincipal(
        principal_id="anonymous-dev",
        display_name="dev",
        authentication_mode=AuthenticationMode.DISABLED_DEV,
        roles=roles,
        permissions=permissions_for_roles(roles),
        groups=[],
        authenticated=False,
        diagnostics=["dev_mode_no_identity_provider"],
    )
    session = new_session_data()
    config = SecurityConfig(
        authentication_mode=AuthenticationMode.DISABLED_DEV,
        trusted_proxy_header_user="X-User",
        trusted_proxy_required_marker_header="X-Marker",
        trusted_proxy_required_marker_value=SecretStr("m"),
        session_cookie_name="altamira_session",
        session_cookie_secure=False,
        require_distinct_reviewer_for_primary=True,
    )
    with pytest.raises(ForbiddenError):
        prepare_operational_action(
            run_dir=run_dir,
            run_id=run_id,
            action=OperationalAction.ACTIVATE_UNIFIED_PRIMARY,
            principal=dev_principal,
            security_config=config,
            session=session,
            reason_code=UnifiedMaterializationReasonCode.PRIMARY_TRIAL_APPROVED,
            review_reference="ticket-primary",
            approved_group_ids=groups,
            target_generation_id=None,
        )


# 65. DISABLED_DEV deshabilita rollback-con-separacion cuando el modo lo exige.
def test_disabled_dev_disables_rollback_when_separation_required(tmp_path: Path) -> None:
    run_dir, run_id, _groups = _bootstrap_run(tmp_path)
    roles = [ApplicationRole.VIEWER]
    dev_principal = AuthenticatedPrincipal(
        principal_id="anonymous-dev",
        display_name="dev",
        authentication_mode=AuthenticationMode.DISABLED_DEV,
        roles=roles,
        permissions=permissions_for_roles(roles),
        groups=[],
        authenticated=False,
        diagnostics=["dev_mode_no_identity_provider"],
    )
    session = new_session_data()
    config = SecurityConfig(
        authentication_mode=AuthenticationMode.DISABLED_DEV,
        trusted_proxy_header_user="X-User",
        trusted_proxy_required_marker_header="X-Marker",
        trusted_proxy_required_marker_value=SecretStr("m"),
        session_cookie_name="altamira_session",
        session_cookie_secure=False,
        require_distinct_reviewer_for_rollback=True,
    )
    with pytest.raises(ForbiddenError):
        prepare_operational_action(
            run_dir=run_dir,
            run_id=run_id,
            action=OperationalAction.ROLLBACK_TO_PREVIOUS,
            principal=dev_principal,
            security_config=config,
            session=session,
            reason_code=UnifiedMaterializationReasonCode.OPERATOR_ROLLBACK,
            review_reference="ticket-rollback",
            approved_group_ids=[],
            target_generation_id=None,
        )
