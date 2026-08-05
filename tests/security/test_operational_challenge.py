"""Tests de `security/operational_challenge.py` (Fase 15B1 Parte 16,
seccion WORKFLOW 46-59, `feat/final-hardening-release`)."""

from __future__ import annotations

from pydantic import SecretStr

from altamira_extractor.contracts.operational_authorization_request import (
    OperationalAction,
    PreparedOperationalIntent,
)
from altamira_extractor.contracts.security_config import AuthenticationMode, SecurityConfig
from altamira_extractor.contracts.unified_materialization_authorization import (
    UnifiedMaterializationReasonCode,
)
from altamira_extractor.security.csrf import generate_csrf_token
from altamira_extractor.security.operational_challenge import sign_challenge, verify_challenge
from altamira_extractor.security.session import new_session_data

_HASH = "a" * 64


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


# 46. Un challenge firmado verifica y retorna el MISMO intent (roundtrip).
def test_sign_and_verify_roundtrip() -> None:
    session = new_session_data()
    intent = _intent()
    token = sign_challenge(intent, session)
    assert verify_challenge(token, session) == intent


# 47. El challenge esta ligado a la sesion -- otra sesion no lo verifica.
def test_challenge_bound_to_session() -> None:
    session_a = new_session_data()
    session_b = new_session_data()
    token = sign_challenge(_intent(), session_a)
    assert verify_challenge(token, session_b) is None


# 48. Un challenge manipulado se rechaza.
def test_tampered_challenge_rejected() -> None:
    session = new_session_data()
    token = sign_challenge(_intent(), session)
    # Defecto real de test encontrado y corregido (cierre F15B1,
    # "single-use real"): el ULTIMO caracter de un token base64url que
    # codifica un digest SHA-256 (32 bytes) cae en un grupo con bits no
    # significativos -- alternarlo puede decodificar a los MISMOS bytes
    # por coincidencia (falso negativo intermitente, no una falla de
    # seguridad real). El penultimo caracter esta siempre en un grupo
    # con bits completamente significativos (misma correccion aplicada
    # en `tests/security/test_cryptographic_audit.py::
    # test_a_single_byte_change_invalidates_all_three_signatures`).
    index = -2
    flipped = "0" if token[index] != "0" else "1"
    tampered = token[:index] + flipped + token[index + 1 :]
    assert verify_challenge(tampered, session) is None


# 49. Un challenge expirado (TTL vencido) se rechaza.
def test_expired_challenge_rejected() -> None:
    session = new_session_data()
    token = sign_challenge(_intent(), session, now=1000.0)
    assert verify_challenge(token, session, now=1000.0 + 301) is None
    assert verify_challenge(token, session, now=1000.0 + 100) is not None


# 50. Entradas malformadas/vacias nunca lanzan.
def test_malformed_challenge_returns_none() -> None:
    session = new_session_data()
    assert verify_challenge("", session) is None
    assert verify_challenge("garbage", session) is None
    assert verify_challenge("a.b", session) is None


# 51. La clave de firma del challenge esta derivada (separacion de dominio) del csrf_secret,
# nunca es el csrf_secret crudo -- un token CSRF no es intercambiable con un challenge.
def test_challenge_signing_key_is_domain_separated_from_csrf() -> None:
    session = new_session_data()
    config = SecurityConfig.model_validate(
        {
            "authentication_mode": AuthenticationMode.DISABLED_DEV,
            "trusted_proxy_header_user": "X-User",
            "trusted_proxy_required_marker_header": "X-Marker",
            "trusted_proxy_required_marker_value": SecretStr("m"),
            "session_cookie_name": "altamira_session",
            "session_cookie_secure": False,
        }
    )
    csrf_token = generate_csrf_token(session, config)
    challenge_token = sign_challenge(_intent(), session)
    assert csrf_token != challenge_token
    # Un token CSRF nunca se decodifica como un challenge valido, aunque
    # comparta la misma sesion (formatos y claves derivadas distintas).
    assert verify_challenge(csrf_token, session) is None


# 52. Cambiar cualquier campo del intent produce un challenge cuyo payload difiere
# (el contenido completo esta firmado, no solo un identificador).
def test_changing_intent_content_changes_signed_payload() -> None:
    session = new_session_data()
    token_a = sign_challenge(_intent(review_reference="ticket-1"), session, now=1000.0)
    token_b = sign_challenge(_intent(review_reference="ticket-2"), session, now=1000.0)
    assert token_a != token_b
