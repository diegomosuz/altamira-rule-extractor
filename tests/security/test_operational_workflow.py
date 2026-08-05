"""Tests del workflow prepare/confirm/execute (Fase 15B1 Parte 8,
`feat/final-hardening-release`).

Reutiliza `tests/pipeline/_unified_materialization_fixtures.py`
(Fase 14B) para bootstrapear un run con una evaluacion real
`READY_FOR_UNIFIED_CANARY` y un `active.json` V1 ya inicializado -- el
workflow de gobierno opera sobre runs YA inicializados (tipicamente via
el CLI de Fase 14B), no bootstrapea un run desde cero
(`expected_active_pointer_hash` es obligatorio, Parte 7)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import SecretStr

from altamira_extractor.api.errors import ForbiddenError, OperationalPreconditionError
from altamira_extractor.contracts.operational_authorization_request import OperationalAction
from altamira_extractor.contracts.security_config import (
    ApplicationRole,
    AuthenticationMode,
    SecurityConfig,
    default_disabled_dev_security_config,
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
    read_active_pointer_hash,
    read_challenge_for_confirm,
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
    principal_id: str, roles: list[ApplicationRole], *, authenticated: bool = True
) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        principal_id=principal_id,
        display_name=principal_id,
        authentication_mode=AuthenticationMode.TRUSTED_PROXY_HEADERS,
        roles=roles,
        permissions=permissions_for_roles(roles),
        groups=[],
        authenticated=authenticated,
    )


def _dev_principal() -> AuthenticatedPrincipal:
    roles = [ApplicationRole.VIEWER]
    return AuthenticatedPrincipal(
        principal_id="anonymous-dev",
        display_name="dev",
        authentication_mode=AuthenticationMode.DISABLED_DEV,
        roles=roles,
        permissions=permissions_for_roles(roles),
        groups=[],
        authenticated=False,
        diagnostics=["dev_mode_no_identity_provider"],
    )


def _trusted_config() -> SecurityConfig:
    return SecurityConfig(
        authentication_mode=AuthenticationMode.TRUSTED_PROXY_HEADERS,
        trusted_proxy_header_user="X-User",
        trusted_proxy_required_marker_header="X-Marker",
        trusted_proxy_required_marker_value=SecretStr("m"),
        session_cookie_name="altamira_session",
        session_cookie_secure=True,
    )


def test_prepare_requires_permission(tmp_path: Path) -> None:
    run_dir, run_id, groups = _bootstrap_run(tmp_path)
    viewer = _principal("viewer-1", [ApplicationRole.VIEWER])
    session = new_session_data()
    with pytest.raises(ForbiddenError):
        prepare_operational_action(
            run_dir=run_dir,
            run_id=run_id,
            action=OperationalAction.ACTIVATE_UNIFIED_CANARY,
            principal=viewer,
            security_config=_trusted_config(),
            session=session,
            reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
            review_reference="ticket-1",
            approved_group_ids=groups,
            target_generation_id=None,
        )


def test_prepare_then_execute_activates_canary(tmp_path: Path) -> None:
    run_dir, run_id, groups = _bootstrap_run(tmp_path)
    reviewer = _principal("reviewer-1", [ApplicationRole.REVIEWER])
    operator = _principal("operator-1", [ApplicationRole.OPERATOR])
    session = new_session_data()

    intent, token = prepare_operational_action(
        run_dir=run_dir,
        run_id=run_id,
        action=OperationalAction.ACTIVATE_UNIFIED_CANARY,
        principal=reviewer,
        security_config=_trusted_config(),
        session=session,
        reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
        review_reference="ticket-1",
        approved_group_ids=groups,
        target_generation_id=None,
    )
    assert intent.distinct_reviewer_required is False

    read_back = read_challenge_for_confirm(token, session)
    assert read_back == intent

    result = execute_operational_action(
        run_dir=run_dir,
        run_id=run_id,
        challenge_token=token,
        principal=operator,
        session=session,
    )
    assert result.active_lane == MaterializedActivationLane.UNIFIED


def test_execute_rejects_stale_pointer(tmp_path: Path) -> None:
    run_dir, run_id, groups = _bootstrap_run(tmp_path)
    operator = _principal("operator-1", [ApplicationRole.OPERATOR])
    session = new_session_data()

    _intent, token = prepare_operational_action(
        run_dir=run_dir,
        run_id=run_id,
        action=OperationalAction.ACTIVATE_UNIFIED_CANARY,
        principal=operator,
        security_config=_trusted_config(),
        session=session,
        reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
        review_reference="ticket-1",
        approved_group_ids=groups,
        target_generation_id=None,
    )

    # Ejecuta una vez, avanzando el puntero -- el MISMO challenge
    # reutilizado despues debe fallar por pointer desactualizado (esto
    # es, en la practica, la garantia de "un solo uso").
    execute_operational_action(
        run_dir=run_dir, run_id=run_id, challenge_token=token, principal=operator, session=session
    )
    with pytest.raises(OperationalPreconditionError):
        execute_operational_action(
            run_dir=run_dir,
            run_id=run_id,
            challenge_token=token,
            principal=operator,
            session=session,
        )


def test_execute_rejects_cross_session_challenge(tmp_path: Path) -> None:
    run_dir, run_id, groups = _bootstrap_run(tmp_path)
    operator = _principal("operator-1", [ApplicationRole.OPERATOR])
    session_a = new_session_data()
    session_b = new_session_data()

    _intent, token = prepare_operational_action(
        run_dir=run_dir,
        run_id=run_id,
        action=OperationalAction.ACTIVATE_UNIFIED_CANARY,
        principal=operator,
        security_config=_trusted_config(),
        session=session_a,
        reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
        review_reference="ticket-1",
        approved_group_ids=groups,
        target_generation_id=None,
    )
    with pytest.raises(OperationalPreconditionError):
        execute_operational_action(
            run_dir=run_dir,
            run_id=run_id,
            challenge_token=token,
            principal=operator,
            session=session_b,
        )


def test_distinct_reviewer_required_disabled_in_dev_mode(tmp_path: Path) -> None:
    run_dir, run_id, groups = _bootstrap_run(tmp_path)
    dev = _dev_principal()
    session = new_session_data()

    trusted_but_primary_required = SecurityConfig(
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
            principal=dev,
            security_config=trusted_but_primary_required,
            session=session,
            reason_code=UnifiedMaterializationReasonCode.PRIMARY_TRIAL_APPROVED,
            review_reference="ticket-primary",
            approved_group_ids=groups,
            target_generation_id=None,
        )


def test_self_approval_rejected_for_distinct_reviewer_action(tmp_path: Path) -> None:
    run_dir, run_id, groups = _bootstrap_run(tmp_path)
    same_person = _principal("same-1", [ApplicationRole.ADMIN])
    session = new_session_data()
    config = _trusted_config().model_copy(update={"require_distinct_reviewer_for_primary": True})

    _intent, token = prepare_operational_action(
        run_dir=run_dir,
        run_id=run_id,
        action=OperationalAction.ACTIVATE_UNIFIED_PRIMARY,
        principal=same_person,
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
            principal=same_person,
            session=session,
        )


def test_default_disabled_dev_config_is_usable_for_read_only(tmp_path: Path) -> None:
    run_dir, run_id, _groups = _bootstrap_run(tmp_path)
    _pointer, pointer_hash = read_active_pointer_hash(run_dir)
    assert pointer_hash is not None
    assert default_disabled_dev_security_config().authentication_mode == (
        AuthenticationMode.DISABLED_DEV
    )
