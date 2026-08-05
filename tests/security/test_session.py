"""Tests de `security/session.py` (Fase 15B1 Parte 16, seccion SESION
30-36, `feat/final-hardening-release`)."""

from __future__ import annotations

import dataclasses
import time

from pydantic import SecretStr

from altamira_extractor.security.session import (
    new_session_data,
    sign_session_cookie,
    verify_session_cookie,
)

_SECRET = SecretStr("test-signing-secret")


# 30. new_session_data usa secrets, no random -- session_id/csrf_secret con longitud minima real.
def test_new_session_data_has_non_trivial_random_fields() -> None:
    data = new_session_data()
    assert len(data.session_id) > 16
    assert len(data.csrf_secret) >= 32
    assert data.session_id.startswith("session-")


# 31. Firmar y verificar produce el mismo SessionData (roundtrip).
def test_sign_and_verify_roundtrip() -> None:
    data = new_session_data()
    cookie = sign_session_cookie(data, _SECRET)
    assert verify_session_cookie(cookie, _SECRET, ttl_seconds=3600) == data


# 32. Una firma con otra clave (otro secreto de proceso) se rechaza.
def test_wrong_secret_rejected() -> None:
    data = new_session_data()
    cookie = sign_session_cookie(data, _SECRET)
    assert verify_session_cookie(cookie, SecretStr("other-secret"), ttl_seconds=3600) is None


# 33. Un payload manipulado (tamper) se rechaza -- nunca se confia en la firma parcial.
def test_tampered_cookie_rejected() -> None:
    data = new_session_data()
    cookie = sign_session_cookie(data, _SECRET)
    last_char = cookie[-1]
    tampered = cookie[:-1] + ("0" if last_char != "0" else "1")
    assert verify_session_cookie(tampered, _SECRET, ttl_seconds=3600) is None


# 34. Una cookie expirada (fuera del TTL) se rechaza.
def test_expired_cookie_rejected() -> None:
    data = new_session_data()
    old = dataclasses.replace(data, issued_at=time.time() - 999_999)
    cookie = sign_session_cookie(old, _SECRET)
    assert verify_session_cookie(cookie, _SECRET, ttl_seconds=60) is None


# 35. Entradas malformadas/vacias nunca lanzan -- siempre None.
def test_malformed_cookie_returns_none_never_raises() -> None:
    assert verify_session_cookie("not-a-cookie", _SECRET, ttl_seconds=3600) is None
    assert verify_session_cookie("", _SECRET, ttl_seconds=3600) is None
    assert verify_session_cookie("a.b", _SECRET, ttl_seconds=3600) is None
    assert verify_session_cookie("a.b.c.d", _SECRET, ttl_seconds=3600) is None


# 36. SessionData nunca incluye un campo de identidad -- solo session_id/csrf_secret/issued_at.
def test_session_data_never_carries_identity_fields() -> None:
    data = new_session_data()
    field_names = {f.name for f in dataclasses.fields(data)}
    assert field_names == {"session_id", "csrf_secret", "issued_at"}
