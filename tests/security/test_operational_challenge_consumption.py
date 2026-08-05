"""Consumo atomico single-use de challenges operativos (Fase 15B1,
cierre "single-use real", Parte 3).

Reutiliza el mismo patron de fixtures que `test_operational_workflow.py`
(`_bootstrap_run`/`_principal`/`_trusted_config`) para no duplicar la
logica de bootstrap de un run real V1 -- aqui el foco es exclusivamente
la garantia STATEFUL de un solo uso, independiente de la revalidacion
de pointer hash (que Fase 15B1 ya tenia y que sigue siendo una
proteccion INDEPENDIENTE, no reemplazada)."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr

from altamira_extractor.api.errors import OperationalPreconditionError, ServiceUnavailableError
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
from altamira_extractor.pipeline.unified_activation_store import UnifiedActivationStore
from altamira_extractor.pipeline.unified_materialization_service import (
    materialize_unified_activation,
)
from altamira_extractor.security import operational_challenge_consumption as consumption_module
from altamira_extractor.security import operational_workflow as workflow_module
from altamira_extractor.security.operational_challenge_consumption import (
    ChallengeAlreadyConsumedError,
    ChallengeConsumptionError,
    compute_challenge_hash,
    consume_challenge_atomically,
    is_challenge_consumed,
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


def _principal(principal_id: str, roles: list[ApplicationRole]) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        principal_id=principal_id,
        display_name=principal_id,
        authentication_mode=AuthenticationMode.TRUSTED_PROXY_HEADERS,
        roles=roles,
        permissions=permissions_for_roles(roles),
        groups=[],
        authenticated=True,
    )


def _trusted_config(**overrides: object) -> SecurityConfig:
    base: dict[str, object] = {
        "authentication_mode": AuthenticationMode.TRUSTED_PROXY_HEADERS,
        "trusted_proxy_header_user": "X-User",
        "trusted_proxy_required_marker_header": "X-Marker",
        "trusted_proxy_required_marker_value": SecretStr("m"),
        "session_cookie_name": "altamira_session",
        "session_cookie_secure": True,
    }
    base.update(overrides)
    return SecurityConfig.model_validate(base)


def _prepare_canary(
    run_dir: Path,
    run_id: str,
    groups: list[str],
    principal: AuthenticatedPrincipal,
    session: object,
) -> str:
    _intent, token = prepare_operational_action(
        run_dir=run_dir,
        run_id=run_id,
        action=OperationalAction.ACTIVATE_UNIFIED_CANARY,
        principal=principal,
        security_config=_trusted_config(),
        session=session,  # type: ignore[arg-type]
        reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
        review_reference="ticket-single-use",
        approved_group_ids=groups,
        target_generation_id=None,
    )
    return token


def _active_generation_id(run_dir: Path) -> str:
    pointer = UnifiedActivationStore(run_dir).read_active_pointer()
    assert pointer is not None
    return pointer.active_generation_id


# ---------------------------------------------------------------------------
# 1. Primer execute valido.
# ---------------------------------------------------------------------------
def test_01_first_execute_succeeds(tmp_path: Path) -> None:
    run_dir, run_id, groups = _bootstrap_run(tmp_path)
    operator = _principal("operator-1", [ApplicationRole.OPERATOR])
    session = new_session_data()
    token = _prepare_canary(run_dir, run_id, groups, operator, session)

    result = execute_operational_action(
        run_dir=run_dir, run_id=run_id, challenge_token=token, principal=operator, session=session
    )
    assert result.active_lane == MaterializedActivationLane.UNIFIED
    assert is_challenge_consumed(run_dir, compute_challenge_hash(token))


# ---------------------------------------------------------------------------
# 2. Replay despues de exito.
# ---------------------------------------------------------------------------
def test_02_replay_after_success_rejected(tmp_path: Path) -> None:
    run_dir, run_id, groups = _bootstrap_run(tmp_path)
    operator = _principal("operator-1", [ApplicationRole.OPERATOR])
    session = new_session_data()
    token = _prepare_canary(run_dir, run_id, groups, operator, session)

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


# ---------------------------------------------------------------------------
# 3. Replay despues de operacion fallida.
# ---------------------------------------------------------------------------
def test_03_replay_after_failed_operation_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir, run_id, groups = _bootstrap_run(tmp_path)
    operator = _principal("operator-1", [ApplicationRole.OPERATOR])
    session = new_session_data()
    token = _prepare_canary(run_dir, run_id, groups, operator, session)

    def _boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("fallo simulado de materializacion")

    monkeypatch.setattr(workflow_module, "materialize_unified_activation", _boom)
    with pytest.raises(RuntimeError):
        execute_operational_action(
            run_dir=run_dir,
            run_id=run_id,
            challenge_token=token,
            principal=operator,
            session=session,
        )
    monkeypatch.undo()

    # El challenge quedo consumido AUNQUE la operacion fallara -- el
    # paso 7 (consumo) ocurre antes del paso 9 (ejecutar), por diseno.
    with pytest.raises(OperationalPreconditionError):
        execute_operational_action(
            run_dir=run_dir,
            run_id=run_id,
            challenge_token=token,
            principal=operator,
            session=session,
        )


# ---------------------------------------------------------------------------
# 4. Replay despues de lost update (pointer cambio entre prepare y execute).
# ---------------------------------------------------------------------------
def test_04_replay_after_lost_update_rejected(tmp_path: Path) -> None:
    run_dir, run_id, groups = _bootstrap_run(tmp_path)
    operator = _principal("operator-1", [ApplicationRole.OPERATOR])
    session = new_session_data()
    token = _prepare_canary(run_dir, run_id, groups, operator, session)

    # Otra transicion real avanza el pointer ANTES del execute de arriba
    # -- el challenge preparado queda con `expected_active_pointer_hash`
    # desactualizado (lost update).
    other_session = new_session_data()
    other_token = _prepare_canary(run_dir, run_id, groups, operator, other_session)
    execute_operational_action(
        run_dir=run_dir,
        run_id=run_id,
        challenge_token=other_token,
        principal=operator,
        session=other_session,
    )

    with pytest.raises(OperationalPreconditionError):
        execute_operational_action(
            run_dir=run_dir,
            run_id=run_id,
            challenge_token=token,
            principal=operator,
            session=session,
        )
    # Consumido pese al lost update -- un segundo intento con el MISMO
    # challenge recibe el error de "ya consumido", no otro lost update.
    with pytest.raises(OperationalPreconditionError) as excinfo:
        execute_operational_action(
            run_dir=run_dir,
            run_id=run_id,
            challenge_token=token,
            principal=operator,
            session=session,
        )
    assert "consumido" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 5/6. Rollback a una generacion YA activa: idempotente (pointer no
# cambia) -- el challenge igual se consume una sola vez.
# ---------------------------------------------------------------------------
def test_05_and_06_idempotent_rollback_consumes_challenge_once(tmp_path: Path) -> None:
    run_dir, run_id, _groups = _bootstrap_run(tmp_path)
    admin = _principal("admin-1", [ApplicationRole.ADMIN])
    session = new_session_data()
    already_active = _active_generation_id(run_dir)

    _intent, token = prepare_operational_action(
        run_dir=run_dir,
        run_id=run_id,
        action=OperationalAction.ROLLBACK_TO_GENERATION,
        principal=admin,
        security_config=_trusted_config(),
        session=session,
        reason_code=UnifiedMaterializationReasonCode.OPERATOR_ROLLBACK,
        review_reference="ticket-idempotent-rollback",
        approved_group_ids=[],
        target_generation_id=already_active,
    )

    pointer_before = UnifiedActivationStore(run_dir).read_active_pointer()
    assert pointer_before is not None

    result = execute_operational_action(
        run_dir=run_dir, run_id=run_id, challenge_token=token, principal=admin, session=session
    )
    assert result.idempotent is True
    assert result.generation_id == already_active

    pointer_after = UnifiedActivationStore(run_dir).read_active_pointer()
    assert pointer_after is not None
    assert pointer_after.pointer_version == pointer_before.pointer_version

    # 6. Replay del MISMO challenge tras una transicion idempotente
    # (pointer sin cambios) -- sigue rechazado por consumo, no porque
    # el pointer haya cambiado (aqui NO cambio).
    with pytest.raises(OperationalPreconditionError):
        execute_operational_action(
            run_dir=run_dir,
            run_id=run_id,
            challenge_token=token,
            principal=admin,
            session=session,
        )


# ---------------------------------------------------------------------------
# 7/8/9. Doble submit concurrente: exactamente un ganador, exactamente
# una llamada a materializacion, exactamente un evento tecnico nuevo.
# ---------------------------------------------------------------------------
def test_07_08_09_concurrent_double_submit_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir, run_id, groups = _bootstrap_run(tmp_path)
    operator = _principal("operator-1", [ApplicationRole.OPERATOR])
    session = new_session_data()
    token = _prepare_canary(run_dir, run_id, groups, operator, session)

    pointer_before = UnifiedActivationStore(run_dir).read_active_pointer()
    assert pointer_before is not None
    event_before = pointer_before.latest_event_id

    call_count = 0
    call_lock = threading.Lock()
    real_materialize = materialize_unified_activation

    def _counting_materialize(*args: object, **kwargs: object) -> object:
        nonlocal call_count
        with call_lock:
            call_count += 1
        return real_materialize(*args, **kwargs)  # type: ignore[arg-type]

    barrier = threading.Barrier(2)

    def _attempt() -> str:
        barrier.wait(timeout=5)
        try:
            execute_operational_action(
                run_dir=run_dir,
                run_id=run_id,
                challenge_token=token,
                principal=operator,
                session=session,
            )
        except OperationalPreconditionError:
            return "rejected"
        return "executed"

    monkeypatch.setattr(workflow_module, "materialize_unified_activation", _counting_materialize)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: _attempt(), range(2)))

    assert sorted(outcomes) == ["executed", "rejected"]
    # 8. exactamente una llamada al servicio de materializacion.
    assert call_count == 1

    # 9. exactamente un evento tecnico nuevo en la cadena de activacion.
    pointer_after = UnifiedActivationStore(run_dir).read_active_pointer()
    assert pointer_after is not None
    assert pointer_after.latest_event_id != event_before
    assert pointer_after.pointer_version == pointer_before.pointer_version + 1


# ---------------------------------------------------------------------------
# 10. Intentos posteriores reciben 409 (OperationalPreconditionError).
# ---------------------------------------------------------------------------
def test_10_subsequent_attempts_receive_409(tmp_path: Path) -> None:
    run_dir, run_id, groups = _bootstrap_run(tmp_path)
    operator = _principal("operator-1", [ApplicationRole.OPERATOR])
    session = new_session_data()
    token = _prepare_canary(run_dir, run_id, groups, operator, session)

    execute_operational_action(
        run_dir=run_dir, run_id=run_id, challenge_token=token, principal=operator, session=session
    )
    for _ in range(3):
        with pytest.raises(OperationalPreconditionError) as excinfo:
            execute_operational_action(
                run_dir=run_dir,
                run_id=run_id,
                challenge_token=token,
                principal=operator,
                session=session,
            )
        assert excinfo.value.status_code == 409


# ---------------------------------------------------------------------------
# 11/12/13. El token, el CSRF y la cookie nunca se persisten en el
# registro de consumo -- solo el hash del token.
# ---------------------------------------------------------------------------
def test_11_12_13_token_csrf_cookie_never_persisted(tmp_path: Path) -> None:
    run_dir, run_id, groups = _bootstrap_run(tmp_path)
    operator = _principal("operator-1", [ApplicationRole.OPERATOR])
    session = new_session_data()
    token = _prepare_canary(run_dir, run_id, groups, operator, session)

    execute_operational_action(
        run_dir=run_dir, run_id=run_id, challenge_token=token, principal=operator, session=session
    )

    consumed_dir = run_dir / "audit" / "consumed-challenges"
    files = list(consumed_dir.glob("*.json"))
    assert len(files) == 1
    raw_text = files[0].read_text(encoding="utf-8")
    document = json.loads(raw_text)

    assert token not in raw_text
    assert session.csrf_secret not in raw_text
    assert session.session_id not in raw_text
    assert set(document.keys()) == {
        "schema_version",
        "challenge_hash",
        "run_id",
        "principal_id",
        "operational_action",
        "consumed_at_utc",
    }
    assert document["challenge_hash"] == compute_challenge_hash(token)


# ---------------------------------------------------------------------------
# 14/15/16. Un principal, accion o run distinto NO puede "reclamar"
# como propio un challenge_hash ya consumido -- el consumo esta
# indexado exclusivamente por hash, nunca por identidad declarada.
# ---------------------------------------------------------------------------
def test_14_15_16_mismatched_principal_action_run_cannot_reconsume(tmp_path: Path) -> None:
    run_dir, run_id, _groups = _bootstrap_run(tmp_path)

    consume_challenge_atomically(
        run_dir,
        challenge_token="same-token-value",
        run_id=run_id,
        principal_id="reviewer-1",
        operational_action=OperationalAction.ACTIVATE_UNIFIED_CANARY,
    )

    # 14. principal distinto.
    with pytest.raises(ChallengeAlreadyConsumedError):
        consume_challenge_atomically(
            run_dir,
            challenge_token="same-token-value",
            run_id=run_id,
            principal_id="operator-9",
            operational_action=OperationalAction.ACTIVATE_UNIFIED_CANARY,
        )
    # 15. action distinta.
    with pytest.raises(ChallengeAlreadyConsumedError):
        consume_challenge_atomically(
            run_dir,
            challenge_token="same-token-value",
            run_id=run_id,
            principal_id="reviewer-1",
            operational_action=OperationalAction.FALLBACK_TO_V1,
        )
    # 16. run distinto (mismo texto de token, run_id diferente declarado
    # -- el hash del token es identico porque se deriva SOLO del texto
    # del token, nunca del run_id declarado; el registro ya existe bajo
    # ESTE run_dir y bloquea igual).
    with pytest.raises(ChallengeAlreadyConsumedError):
        consume_challenge_atomically(
            run_dir,
            challenge_token="same-token-value",
            run_id="otro-run-id",
            principal_id="reviewer-1",
            operational_action=OperationalAction.ACTIVATE_UNIFIED_CANARY,
        )


# ---------------------------------------------------------------------------
# 17. El consumo por si mismo no modifica activation/.
# ---------------------------------------------------------------------------
def test_17_consumption_alone_does_not_touch_activation(tmp_path: Path) -> None:
    run_dir, run_id, _groups = _bootstrap_run(tmp_path)
    activation_dir = run_dir / "activation"
    before = {
        p.relative_to(activation_dir).as_posix(): p.read_bytes()
        for p in sorted(activation_dir.rglob("*"))
        if p.is_file()
    }

    consume_challenge_atomically(
        run_dir,
        challenge_token="isolated-token",
        run_id=run_id,
        principal_id="reviewer-1",
        operational_action=OperationalAction.ACTIVATE_UNIFIED_CANARY,
    )

    after = {
        p.relative_to(activation_dir).as_posix(): p.read_bytes()
        for p in sorted(activation_dir.rglob("*"))
        if p.is_file()
    }
    assert after == before


# ---------------------------------------------------------------------------
# 18. Fallo de escritura del registro no ejecuta la operacion.
# ---------------------------------------------------------------------------
def test_18_write_failure_does_not_execute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_dir, run_id, groups = _bootstrap_run(tmp_path)
    operator = _principal("operator-1", [ApplicationRole.OPERATOR])
    session = new_session_data()
    token = _prepare_canary(run_dir, run_id, groups, operator, session)

    pointer_before = UnifiedActivationStore(run_dir).read_active_pointer()
    assert pointer_before is not None

    def _boom_fdopen(*args: object, **kwargs: object) -> object:
        raise OSError("disco simulado sin espacio")

    monkeypatch.setattr(os, "fdopen", _boom_fdopen)
    with pytest.raises(ServiceUnavailableError):
        execute_operational_action(
            run_dir=run_dir,
            run_id=run_id,
            challenge_token=token,
            principal=operator,
            session=session,
        )

    pointer_after = UnifiedActivationStore(run_dir).read_active_pointer()
    assert pointer_after is not None
    assert pointer_after.pointer_version == pointer_before.pointer_version
    assert pointer_after.active_generation_id == pointer_before.active_generation_id


# ---------------------------------------------------------------------------
# 19. Un registro existente nunca se sobrescribe.
# ---------------------------------------------------------------------------
def test_19_existing_record_never_overwritten(tmp_path: Path) -> None:
    run_dir, run_id, _groups = _bootstrap_run(tmp_path)

    consume_challenge_atomically(
        run_dir,
        challenge_token="fixed-token",
        run_id=run_id,
        principal_id="reviewer-1",
        operational_action=OperationalAction.ACTIVATE_UNIFIED_CANARY,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )
    record_path = (
        run_dir / "audit" / "consumed-challenges" / f"{compute_challenge_hash('fixed-token')}.json"
    )
    original_bytes = record_path.read_bytes()

    with pytest.raises(ChallengeAlreadyConsumedError):
        consume_challenge_atomically(
            run_dir,
            challenge_token="fixed-token",
            run_id=run_id,
            principal_id="someone-else",
            operational_action=OperationalAction.FALLBACK_TO_V1,
            clock=lambda: datetime(2030, 1, 1, tzinfo=UTC),
        )

    assert record_path.read_bytes() == original_bytes


# ---------------------------------------------------------------------------
# 20. Paths inseguros rechazados.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "unsafe_hash",
    [
        "../../etc/passwd",
        "..",
        ".",
        "",
        "a" * 63,
        "a" * 65,
        "not-hex-chars-not-hex-chars-not-hex-chars-not-hex-chars-not-he",
        "with/slash" + "a" * 54,
        "with\\backslash" + "a" * 50,
    ],
)
def test_20_unsafe_challenge_hash_rejected(tmp_path: Path, unsafe_hash: str) -> None:
    run_dir, _run_id, _groups = _bootstrap_run(tmp_path)
    with pytest.raises(ChallengeConsumptionError):
        consumption_module._record_path(run_dir, unsafe_hash)  # noqa: SLF001
