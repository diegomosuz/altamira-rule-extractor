"""Tests de `security/csrf.py` (Fase 15B1 Parte 16, seccion CSRF
37-45, `feat/final-hardening-release`)."""

from __future__ import annotations

from pydantic import SecretStr

from altamira_extractor.contracts.security_config import AuthenticationMode, SecurityConfig
from altamira_extractor.security.csrf import CSRF_FORM_FIELD_NAME, generate_csrf_token
from altamira_extractor.security.csrf import _verify_token_value as verify_token_value
from altamira_extractor.security.session import new_session_data


def _config(**overrides: object) -> SecurityConfig:
    base: dict[str, object] = {
        "authentication_mode": AuthenticationMode.DISABLED_DEV,
        "trusted_proxy_header_user": "X-User",
        "trusted_proxy_required_marker_header": "X-Marker",
        "trusted_proxy_required_marker_value": SecretStr("m"),
        "session_cookie_name": "altamira_session",
        "session_cookie_secure": False,
        "csrf_token_ttl_seconds": 60,
    }
    base.update(overrides)
    return SecurityConfig.model_validate(base)


# 37. El token se genera con secrets (nonce no trivial), no random.
def test_token_has_non_trivial_nonce() -> None:
    session = new_session_data()
    token = generate_csrf_token(session, _config())
    nonce = token.split(".", 1)[0]
    assert len(nonce) >= 16


# 38. Un token valido verifica contra la MISMA sesion.
def test_valid_token_verifies() -> None:
    session = new_session_data()
    token = generate_csrf_token(session, _config())
    assert verify_token_value(token, session) is True


# 39. Un token de otra sesion (csrf_secret distinto) se rechaza.
def test_token_from_other_session_rejected() -> None:
    session_a = new_session_data()
    session_b = new_session_data()
    token = generate_csrf_token(session_a, _config())
    assert verify_token_value(token, session_b) is False


# 40. Token ausente/vacio se rechaza.
def test_absent_token_rejected() -> None:
    session = new_session_data()
    assert verify_token_value(None, session) is False
    assert verify_token_value("", session) is False


# 41. Token malformado (formato incorrecto) se rechaza sin lanzar.
def test_malformed_token_rejected() -> None:
    session = new_session_data()
    assert verify_token_value("garbage", session) is False
    assert verify_token_value("a.b.c.d", session) is False


# 42. Token manipulado (firma incorrecta) se rechaza.
def test_tampered_token_rejected() -> None:
    session = new_session_data()
    token = generate_csrf_token(session, _config())
    tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
    assert verify_token_value(tampered, session) is False


# 43. Token expirado (TTL vencido) se rechaza.
def test_expired_token_rejected() -> None:
    session = new_session_data()
    config = _config(csrf_token_ttl_seconds=60)
    token = generate_csrf_token(session, config, now=1000.0)
    assert verify_token_value(token, session, now=1000.0 + 61) is False
    assert verify_token_value(token, session, now=1000.0 + 30) is True


# 44. El nombre del campo de formulario esta centralizado en una constante publica.
def test_form_field_name_is_exported_constant() -> None:
    assert CSRF_FORM_FIELD_NAME == "csrf_token"


# 45. Dos tokens generados para la misma sesion en momentos distintos son distintos
# (nonce aleatorio por generacion, nunca reutilizado).
def test_tokens_are_not_reused_across_generations() -> None:
    session = new_session_data()
    config = _config()
    token_a = generate_csrf_token(session, config)
    token_b = generate_csrf_token(session, config)
    assert token_a != token_b
    assert verify_token_value(token_a, session) is True
    assert verify_token_value(token_b, session) is True
