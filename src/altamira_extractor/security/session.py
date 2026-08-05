"""Sesion minima firmada (Fase 15B1) -- SOLO para continuidad de CSRF y
del challenge prepare/confirm/execute, NUNCA para identidad: la
identidad se resuelve EN CADA REQUEST desde headers confiables (ver
`identity_resolver.py`), nunca se copia a la cookie.

Deliberadamente sin `itsdangerous`/`SessionMiddleware` de Starlette
(no es una dependencia ya presente en este proyecto, y el payload que
se necesita firmar es minimo): HMAC-SHA256 sobre JSON estable,
`secrets.compare_digest` para verificar -- nunca `random`, nunca una
comparacion `==` directa de la firma."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

from pydantic import SecretStr

_SIGNATURE_ALGO = hashlib.sha256


@dataclass(frozen=True)
class SessionData:
    """Payload minimo de la cookie de sesion -- UNICAMENTE un
    `session_id` (para ligar el challenge/CSRF a esta sesion) y un
    `csrf_secret` propio de la sesion (nunca compartido entre
    sesiones). Ningun campo de identidad."""

    session_id: str
    csrf_secret: str
    issued_at: float


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes | None:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except (ValueError, TypeError):
        return None


def new_session_data() -> SessionData:
    return SessionData(
        session_id=f"session-{secrets.token_hex(16)}",
        csrf_secret=secrets.token_hex(32),
        issued_at=time.time(),
    )


def sign_session_cookie(data: SessionData, secret: SecretStr) -> str:
    payload = json.dumps(
        {
            "session_id": data.session_id,
            "csrf_secret": data.csrf_secret,
            "issued_at": data.issued_at,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    signature = hmac.new(
        secret.get_secret_value().encode("utf-8"), payload, _SIGNATURE_ALGO
    ).digest()
    return f"{_b64url_encode(payload)}.{_b64url_encode(signature)}"


def verify_session_cookie(
    cookie_value: str, secret: SecretStr, *, ttl_seconds: int
) -> SessionData | None:
    """`None` para cualquier cookie ausente/malformada/con firma
    invalida/expirada -- nunca lanza, el llamador simplemente crea una
    sesion nueva en ese caso."""
    parts = cookie_value.split(".", 1)
    if len(parts) != 2:
        return None
    payload_b64, signature_b64 = parts
    payload = _b64url_decode(payload_b64)
    signature = _b64url_decode(signature_b64)
    if payload is None or signature is None:
        return None

    expected_signature = hmac.new(
        secret.get_secret_value().encode("utf-8"), payload, _SIGNATURE_ALGO
    ).digest()
    if not hmac.compare_digest(signature, expected_signature):
        return None

    try:
        document = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(document, dict):
        return None
    # Defensa en profundidad (auditoria criptografica de cierre, Fase
    # 15B1): un payload con claves inesperadas se rechaza -- aunque la
    # firma ya cubre los bytes completos (una clave extra sin la clave
    # de firma real es indistinguible de cualquier otra manipulacion,
    # ya rechazada arriba), un parser estricto nunca ignora en silencio
    # contenido no declarado de un payload por lo demas ya autenticado.
    if set(document.keys()) != {"session_id", "csrf_secret", "issued_at"}:
        return None
    session_id = document.get("session_id")
    csrf_secret = document.get("csrf_secret")
    issued_at = document.get("issued_at")
    if (
        not isinstance(session_id, str)
        or not session_id
        or not isinstance(csrf_secret, str)
        or not csrf_secret
        or not isinstance(issued_at, (int, float))
    ):
        return None

    if time.time() - float(issued_at) > ttl_seconds:
        return None

    return SessionData(session_id=session_id, csrf_secret=csrf_secret, issued_at=float(issued_at))


__all__ = [
    "SessionData",
    "new_session_data",
    "sign_session_cookie",
    "verify_session_cookie",
]
