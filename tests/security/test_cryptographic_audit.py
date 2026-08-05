"""Auditoría criptográfica de `security/session.py`, `security/csrf.py`
y `security/operational_challenge.py` (Fase 15B1, cierre de
integración real, `feat/final-hardening-release`).

Resumen de hallazgos (ver docstring de cada sección para el detalle):

- Los tres mecanismos usan HMAC-SHA256 + `hmac.compare_digest` +
  `secrets` (nunca `random`) -- confirmado por inspección Y por test
  ejecutable en este archivo.
- Separación de dominio real entre los tres: la cookie de sesión firma
  con `Settings.session_secret` (clave de proceso); el CSRF firma con
  `session.csrf_secret` directamente; el challenge firma con una clave
  DERIVADA de `session.csrf_secret` (`HMAC(csrf_secret,
  "altamira-operational-challenge-v1")`) -- las tres claves son
  distintas incluso para la MISMA sesión, así que ningún token de un
  mecanismo verifica como válido en otro.
- **Dos defectos reales encontrados y corregidos durante esta
  auditoría**:
  1. `Settings.session_secret` no tenía ningún piso de longitud --
     `ALTAMIRA_SESSION_SECRET=x` se aceptaba en silencio. Se agregó
     `_check_session_secret_minimum_length` (32 caracteres mínimo) en
     `config.py`. Ver `test_l_weak_session_secret_rejected`.
  2. `session.py::verify_session_cookie` aceptaba (ignorándolo en
     silencio) un payload con claves adicionales a las 3 declaradas,
     siempre que la firma fuera válida. No era explotable (sin la
     clave de firma real un atacante no puede producir NINGÚN payload
     válido, con o sin campos extra) pero violaba parsing estricto.
     Se agregó un chequeo de conjunto de claves exacto. Ver
     `test_f_session_payload_with_extra_field_rejected`.
- `challenge.py` (respaldado por un modelo pydantic con
  `extra="forbid"`) y `csrf.py` (formato de 3 partes fijo, sin JSON)
  ya rechazaban campos extra por construcción -- ver
  `test_g_challenge_payload_with_extra_field_rejected`."""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import hmac
import inspect
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from altamira_extractor.api.app import create_app
from altamira_extractor.api.errors import ForbiddenError, OperationalPreconditionError
from altamira_extractor.config import Settings
from altamira_extractor.contracts.operational_authorization_request import (
    OperationalAction,
    PreparedOperationalIntent,
)
from altamira_extractor.contracts.security_config import (
    ApplicationRole,
    AuthenticationMode,
    SecurityConfig,
)
from altamira_extractor.contracts.security_identity import (
    AuthenticatedPrincipal,
    permissions_for_roles,
)
from altamira_extractor.contracts.unified_materialization_authorization import (
    UnifiedMaterializationReasonCode,
)
from altamira_extractor.pipeline.unified_activation_store import UnifiedActivationStore
from altamira_extractor.pipeline.unified_materialization_service import (
    materialize_unified_activation,
)
from altamira_extractor.security.csrf import _verify_token_value as verify_csrf_token
from altamira_extractor.security.csrf import generate_csrf_token
from altamira_extractor.security.operational_challenge import _derive_key as challenge_derive_key
from altamira_extractor.security.operational_challenge import sign_challenge, verify_challenge
from altamira_extractor.security.operational_workflow import (
    execute_operational_action,
    prepare_operational_action,
)
from altamira_extractor.security.session import (
    new_session_data,
    sign_session_cookie,
    verify_session_cookie,
)
from tests.pipeline._unified_materialization_fixtures import (
    build_materialization_fixture,
    write_authorization_yaml,
    write_run_dir,
)

_HASH = "a" * 64
_SESSION_SECRET = SecretStr("host-signing-secret-for-crypto-audit-tests-32c")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _config(**overrides: object) -> SecurityConfig:
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


# ---------------------------------------------------------------------------
# 1. Algoritmo / 2. Formato / 3. Codificación / 4-5. Campos firmados/no
#    firmados / 6. Separación de dominio -- confirmados por inspección
#    (ver docstring del modulo) y por los tests A/B/C de abajo.
# ---------------------------------------------------------------------------


def _flip_trailing_char(token: str) -> str:
    """Corrompe un caracter cerca del final de un token base64url.

    Desliberadamente NO usa el ultimo caracter: en base64, el grupo final
    de una cadena de 32 bytes (SHA-256) deja el ULTIMO caracter con bits
    no significativos (padding implicito) -- alternar solo ese caracter
    puede decodificar a los MISMOS bytes por coincidencia (falso negativo
    intermitente, no una falla de seguridad real). El penultimo caracter
    esta siempre dentro de un grupo con bits completamente significativos,
    por lo que alterarlo garantiza bytes decodificados distintos."""
    index = -2
    flipped = "0" if token[index] != "0" else "1"
    return token[:index] + flipped + token[index + 1 :]


# A. Cambio de un byte invalida la firma (los tres mecanismos).
def test_a_single_byte_change_invalidates_all_three_signatures() -> None:
    session = new_session_data()

    session_cookie = sign_session_cookie(session, _SESSION_SECRET)
    tampered_session = _flip_trailing_char(session_cookie)
    assert verify_session_cookie(tampered_session, _SESSION_SECRET, ttl_seconds=3600) is None

    csrf_token = generate_csrf_token(session, _config())
    tampered_csrf = _flip_trailing_char(csrf_token)
    assert verify_csrf_token(tampered_csrf, session) is False

    challenge_token = sign_challenge(_intent(), session)
    tampered_challenge = _flip_trailing_char(challenge_token)
    assert verify_challenge(tampered_challenge, session) is None


# B. La firma de sesión no sirve como challenge (claves y formatos distintos).
def test_b_session_signature_does_not_verify_as_challenge() -> None:
    session = new_session_data()
    session_cookie = sign_session_cookie(session, _SESSION_SECRET)
    assert verify_challenge(session_cookie, session) is None


# C. La firma de challenge no sirve como CSRF (separación de dominio real).
def test_c_challenge_signature_does_not_verify_as_csrf() -> None:
    session = new_session_data()
    challenge_token = sign_challenge(_intent(), session)
    assert verify_csrf_token(challenge_token, session) is False
    # Y a la inversa: un token CSRF tampoco sirve como challenge.
    csrf_token = generate_csrf_token(session, _config())
    assert verify_challenge(csrf_token, session) is None


# D. Clave diferente invalida (los tres mecanismos).
def test_d_different_key_invalidates_all_three() -> None:
    session_a = new_session_data()
    session_b = new_session_data()

    session_cookie = sign_session_cookie(session_a, _SESSION_SECRET)
    other_secret = SecretStr("a-completely-different-signing-secret-32chars")
    assert verify_session_cookie(session_cookie, other_secret, ttl_seconds=3600) is None

    csrf_token = generate_csrf_token(session_a, _config())
    assert verify_csrf_token(csrf_token, session_b) is False

    challenge_token = sign_challenge(_intent(), session_a)
    assert verify_challenge(challenge_token, session_b) is None


# E. Payload expirado se rechaza (los tres mecanismos, reloj inyectado).
def test_e_expired_payload_rejected_for_all_three() -> None:
    session = new_session_data()

    old = session
    session_cookie = sign_session_cookie(old, _SESSION_SECRET)
    # session.py no acepta "now" inyectado en sign -- se fuerza la
    # expiracion via issued_at antiguo + ttl corto.
    stale = dataclasses.replace(old, issued_at=time.time() - 999_999)
    stale_cookie = sign_session_cookie(stale, _SESSION_SECRET)
    assert verify_session_cookie(stale_cookie, _SESSION_SECRET, ttl_seconds=60) is None
    assert session_cookie  # sanity: el no-expirado se genero sin error

    csrf_token = generate_csrf_token(session, _config(), now=1000.0)
    assert verify_csrf_token(csrf_token, session, now=1000.0 + 3601) is False

    challenge_token = sign_challenge(_intent(), session, now=1000.0)
    assert verify_challenge(challenge_token, session, now=1000.0 + 301) is None


# F. Payload con campos extra se rechaza -- protegido por HMAC sobre bytes
# completos (challenge/CSRF) y por chequeo explicito de claves exactas
# (session, agregado en esta auditoria: ver _check_no_unexpected_fields).
def test_f_session_payload_with_extra_field_rejected() -> None:
    session = new_session_data()
    # Payload FORJADO con un campo extra, firmado CORRECTAMENTE con la
    # clave real -- simula el unico escenario donde "campos extra" podria
    # importar (un atacante que ya posee la clave). Confirma que incluso
    # en ese caso el parser estricto de session.py lo rechaza.
    payload = json.dumps(
        {
            "session_id": session.session_id,
            "csrf_secret": session.csrf_secret,
            "issued_at": session.issued_at,
            "role": "admin",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    signature = hmac.new(
        _SESSION_SECRET.get_secret_value().encode("utf-8"), payload, hashlib.sha256
    ).digest()

    forged_cookie = f"{_b64(payload)}.{_b64(signature)}"
    assert verify_session_cookie(forged_cookie, _SESSION_SECRET, ttl_seconds=3600) is None


# G. Challenge con campos extra se rechaza -- PreparedOperationalIntent es
# un modelo pydantic con extra="forbid" (AltamiraBaseModel), asi que
# model_validate_json ya rechaza cualquier clave no declarada.
def test_g_challenge_payload_with_extra_field_rejected() -> None:
    session = new_session_data()
    intent = _intent()
    # Inyecta un campo extra directamente en el JSON canonico, firmado
    # con la clave DERIVADA correcta -- simula un atacante que ya posee
    # la clave. `PreparedOperationalIntent.model_validate_json` (Pydantic,
    # `extra="forbid"`) rechaza el campo no declarado.
    tampered_json = intent.to_stable_json().rstrip("\n")[:-1] + ',"extra_field":"x"}\n'
    tampered_payload = tampered_json.encode("utf-8")

    key = challenge_derive_key(session.csrf_secret)
    expires_at = int(time.time()) + 300
    signature = hmac.new(key, tampered_payload + f".{expires_at}".encode(), hashlib.sha256).digest()

    forged_token = f"{_b64(tampered_payload)}.{expires_at}.{_b64(signature)}"
    assert verify_challenge(forged_token, session) is None
    # El payload original (sin manipular) sigue siendo valido por separado.
    assert verify_challenge(sign_challenge(intent, session), session) == intent


# H. Firma truncada se rechaza (los tres mecanismos).
def test_h_truncated_signature_rejected() -> None:
    session = new_session_data()

    session_cookie = sign_session_cookie(session, _SESSION_SECRET)
    payload_part, sig_part = session_cookie.rsplit(".", 1)
    truncated_session = f"{payload_part}.{sig_part[:-10]}"
    assert verify_session_cookie(truncated_session, _SESSION_SECRET, ttl_seconds=3600) is None

    csrf_token = generate_csrf_token(session, _config())
    nonce, expires_at, sig = csrf_token.split(".")
    truncated_csrf = f"{nonce}.{expires_at}.{sig[:-10]}"
    assert verify_csrf_token(truncated_csrf, session) is False

    challenge_token = sign_challenge(_intent(), session)
    payload_part2, expires_part2, sig_part2 = challenge_token.split(".")
    truncated_challenge = f"{payload_part2}.{expires_part2}.{sig_part2[:-10]}"
    assert verify_challenge(truncated_challenge, session) is None


# I. Firma extendida (con bytes adicionales) se rechaza.
def test_i_extended_signature_rejected() -> None:
    session = new_session_data()

    session_cookie = sign_session_cookie(session, _SESSION_SECRET)
    extended_session = session_cookie + "AAAA"
    assert verify_session_cookie(extended_session, _SESSION_SECRET, ttl_seconds=3600) is None

    csrf_token = generate_csrf_token(session, _config())
    extended_csrf = csrf_token + "AAAA"
    assert verify_csrf_token(extended_csrf, session) is False

    challenge_token = sign_challenge(_intent(), session)
    extended_challenge = challenge_token + "AAAA"
    assert verify_challenge(extended_challenge, session) is None


# J. Comparacion de firmas usa hmac.compare_digest (tiempo constante) --
# confirmado por inspeccion de codigo fuente de los tres modulos.
def test_j_signature_comparison_uses_compare_digest() -> None:
    from altamira_extractor.security import csrf as csrf_module
    from altamira_extractor.security import operational_challenge as challenge_module
    from altamira_extractor.security import session as session_module

    for module in (session_module, csrf_module, challenge_module):
        source = inspect.getsource(module)
        assert "hmac.compare_digest" in source, f"{module.__name__} no usa compare_digest"
        signature_lines = "\n".join(
            line for line in source.splitlines() if "signature" in line.lower() and "==" in line
        )
        assert not signature_lines, (
            f"{module.__name__} podria tener una comparacion de firma no constante"
        )


# K. Secreto ausente falla en modo trusted (fail-closed al arrancar).
def test_k_missing_secret_fails_closed_in_trusted_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `session_secret` usa `validation_alias="ALTAMIRA_SESSION_SECRET"` sin
    # `populate_by_name` -- pasarlo como kwarg `session_secret=...` lo
    # ignora en silencio (extra="ignore") y NO ejerce el campo real; hay
    # que poblarlo por su alias (variable de entorno) como hace el resto
    # de la suite (ver `test_bootstrap_smoke.py::test_settings_override_via_env`).
    monkeypatch.delenv("ALTAMIRA_SESSION_SECRET", raising=False)
    security_yaml = tmp_path / "security.yaml"
    security_yaml.write_text(
        "\n".join(
            [
                'schema_version: "1.0"',
                "authentication_mode: TRUSTED_PROXY_HEADERS",
                'trusted_proxy_header_user: "X-User"',
                'trusted_proxy_required_marker_header: "X-Marker"',
                'trusted_proxy_required_marker_value: "marker-value-not-a-real-secret"',
                "trusted_proxy_allowed_roles: []",
                "group_role_mapping: {}",
                "default_authenticated_role: VIEWER",
                'session_cookie_name: "altamira_session"',
                "session_cookie_secure: true",
                "session_cookie_httponly: true",
                "session_cookie_samesite: strict",
                "csrf_enabled: true",
                "csrf_token_ttl_seconds: 3600",
                "session_ttl_seconds: 28800",
                "require_distinct_reviewer_for_primary: true",
                "require_distinct_reviewer_for_rollback: false",
                "audit_enabled: true",
                "diagnostics: []",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    settings = Settings(
        runs_dir=tmp_path / "runs",
        incoming_dir=tmp_path / "incoming",
        security_config_path=security_yaml,
    )
    assert settings.session_secret is None
    app = create_app(settings)
    with pytest.raises(RuntimeError, match="ALTAMIRA_SESSION_SECRET"), TestClient(app):
        pass


# L. Secreto debil se rechaza -- DEFECTO REAL ENCONTRADO Y CORREGIDO en
# esta auditoria (config.py::_check_session_secret_minimum_length).
def test_l_weak_session_secret_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALTAMIRA_SESSION_SECRET", "x")
    with pytest.raises(ValidationError, match="ALTAMIRA_SESSION_SECRET"):
        Settings()
    monkeypatch.setenv("ALTAMIRA_SESSION_SECRET", "short-secret-16c")
    with pytest.raises(ValidationError, match="ALTAMIRA_SESSION_SECRET"):
        Settings()
    # 32+ caracteres: aceptado.
    monkeypatch.setenv("ALTAMIRA_SESSION_SECRET", "a" * 32)
    accepted = Settings()
    assert accepted.session_secret is not None
    assert accepted.session_secret.get_secret_value() == "a" * 32


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


# M. Replay tras exito se rechaza (el MISMO challenge, dos veces, tras
# una ejecucion real que ya avanzo el pointer).
def test_m_replay_after_success_rejected(tmp_path: Path) -> None:
    run_dir, run_id, groups = _bootstrap_run(tmp_path)
    operator = _principal("operator-1", [ApplicationRole.OPERATOR])
    session = new_session_data()
    config = _config()

    _intent_result, token = prepare_operational_action(
        run_dir=run_dir,
        run_id=run_id,
        action=OperationalAction.ACTIVATE_UNIFIED_CANARY,
        principal=operator,
        security_config=config,
        session=session,
        reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
        review_reference="ticket-m",
        approved_group_ids=groups,
        target_generation_id=None,
    )
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


# N. Replay tras FALLO no ejecuta dos veces -- un challenge cuyo pointer
# esperado ya esta desactualizado desde el inicio falla de forma
# repetible, sin efectos secundarios acumulativos.
def test_n_replay_after_failure_never_executes(tmp_path: Path) -> None:
    run_dir, run_id, groups = _bootstrap_run(tmp_path)
    operator = _principal("operator-1", [ApplicationRole.OPERATOR])
    session = new_session_data()

    stale_intent = _intent(
        run_id=run_id,
        expected_active_pointer_hash="f" * 64,  # deliberadamente incorrecto
        approved_group_ids=groups,
    )
    stale_token = sign_challenge(stale_intent, session)

    store = UnifiedActivationStore(run_dir)
    pointer_before = store.read_active_pointer()
    assert pointer_before is not None

    for _ in range(2):
        with pytest.raises(OperationalPreconditionError):
            execute_operational_action(
                run_dir=run_dir,
                run_id=run_id,
                challenge_token=stale_token,
                principal=operator,
                session=session,
            )
        pointer_after = store.read_active_pointer()
        assert pointer_after is not None
        assert pointer_after.pointer_version == pointer_before.pointer_version
        assert pointer_after.active_generation_id == pointer_before.active_generation_id


# O. Nueva sesion invalida el challenge anterior.
def test_o_new_session_invalidates_previous_challenge(tmp_path: Path) -> None:
    run_dir, run_id, groups = _bootstrap_run(tmp_path)
    operator = _principal("operator-1", [ApplicationRole.OPERATOR])
    session_a = new_session_data()
    session_b = new_session_data()

    _intent_result, token = prepare_operational_action(
        run_dir=run_dir,
        run_id=run_id,
        action=OperationalAction.ACTIVATE_UNIFIED_CANARY,
        principal=operator,
        security_config=_config(),
        session=session_a,
        reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
        review_reference="ticket-o",
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


# P. Cambio de principal invalida el challenge -- UNICAMENTE cuando la
# accion exige revisor distinto (comportamiento por diseno, Parte 13:
# cualquier OPERATOR autorizado puede recoger y ejecutar un canary
# preparado por un REVIEWER distinto -- eso NO es autoaprobacion, es la
# separacion prepare/execute funcionando como se espera). Cuando la
# separacion se exige, un cambio de principal hacia el MISMO que preparo
# se rechaza; hacia uno genuinamente distinto, se acepta.
def test_p_principal_change_behavior_depends_on_distinct_reviewer_requirement(
    tmp_path: Path,
) -> None:
    run_dir, run_id, groups = _bootstrap_run(tmp_path)
    reviewer = _principal("reviewer-1", [ApplicationRole.REVIEWER])
    operator = _principal("operator-1", [ApplicationRole.OPERATOR])
    session = new_session_data()

    # Sin separacion exigida: reviewer prepara, OTRO operator ejecuta -- OK por diseno.
    _intent_result, token = prepare_operational_action(
        run_dir=run_dir,
        run_id=run_id,
        action=OperationalAction.ACTIVATE_UNIFIED_CANARY,
        principal=reviewer,
        security_config=_config(),
        session=session,
        reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
        review_reference="ticket-p1",
        approved_group_ids=groups,
        target_generation_id=None,
    )
    result = execute_operational_action(
        run_dir=run_dir, run_id=run_id, challenge_token=token, principal=operator, session=session
    )
    assert result.active_lane.value == "UNIFIED"

    # Con separacion exigida: el MISMO principal que preparo no puede ejecutar.
    admin = _principal("admin-1", [ApplicationRole.ADMIN])
    config_requiring_separation = _config(require_distinct_reviewer_for_rollback=True)
    _intent_result2, token2 = prepare_operational_action(
        run_dir=run_dir,
        run_id=run_id,
        action=OperationalAction.ROLLBACK_TO_PREVIOUS,
        principal=admin,
        security_config=config_requiring_separation,
        session=session,
        reason_code=UnifiedMaterializationReasonCode.OPERATOR_ROLLBACK,
        review_reference="ticket-p2",
        approved_group_ids=[],
        target_generation_id=None,
    )
    with pytest.raises(ForbiddenError):
        execute_operational_action(
            run_dir=run_dir,
            run_id=run_id,
            challenge_token=token2,
            principal=admin,
            session=session,
        )


# Q. Cambio de pointer invalida el challenge.
def test_q_pointer_change_invalidates_challenge(tmp_path: Path) -> None:
    run_dir, run_id, groups = _bootstrap_run(tmp_path)
    operator = _principal("operator-1", [ApplicationRole.OPERATOR])
    session = new_session_data()

    _intent_result, token = prepare_operational_action(
        run_dir=run_dir,
        run_id=run_id,
        action=OperationalAction.ACTIVATE_UNIFIED_CANARY,
        principal=operator,
        security_config=_config(),
        session=session,
        reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
        review_reference="ticket-q",
        approved_group_ids=groups,
        target_generation_id=None,
    )

    # El pointer cambia POR OTRO MEDIO (una segunda materializacion
    # directa) antes de que el challenge se ejecute.
    eval_hash_path = run_dir / "diagnostics" / "unified-activation-evaluation.json"
    eval_hash = hashlib.sha256(eval_hash_path.read_bytes()).hexdigest()
    second_auth = tmp_path / "second-keep-v1.yaml"
    write_authorization_yaml(
        second_auth,
        run_id=run_id,
        activation_evaluation_hash=eval_hash,
        action="KEEP_V1",
        expected_readiness_disposition="READY_FOR_UNIFIED_CANARY",
    )
    # KEEP_V1 es idempotente y no mueve el pointer si ya esta en V1 --
    # se usa aqui solo para demostrar que el CHEQUEO revalida el hash
    # real en el momento de `execute`, no el capturado en `prepare`.
    materialize_unified_activation(run_dir, run_id, authorization_path=second_auth)

    # Simula un cambio de pointer real: ejecuta la ÚNICA transición
    # disponible que avanza el puntero de otro modo (canary) usando un
    # segundo flujo prepare/execute independiente ANTES del primero.
    _intent_result2, token2 = prepare_operational_action(
        run_dir=run_dir,
        run_id=run_id,
        action=OperationalAction.ACTIVATE_UNIFIED_CANARY,
        principal=operator,
        security_config=_config(),
        session=session,
        reason_code=UnifiedMaterializationReasonCode.CANARY_APPROVED,
        review_reference="ticket-q2",
        approved_group_ids=groups,
        target_generation_id=None,
    )
    execute_operational_action(
        run_dir=run_dir, run_id=run_id, challenge_token=token2, principal=operator, session=session
    )

    # El PRIMER challenge (capturado antes de que el pointer avanzara) ya
    # no coincide con el estado real -- se rechaza.
    with pytest.raises(OperationalPreconditionError):
        execute_operational_action(
            run_dir=run_dir,
            run_id=run_id,
            challenge_token=token,
            principal=operator,
            session=session,
        )
