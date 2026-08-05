"""Challenge firmado, sin estado en servidor, del workflow prepare/
confirm/execute (Fase 15B1 Parte 8).

Deliberadamente STATELESS (consistente con `.claude/rules/python.md`:
"no usar estado global mutable") mientras conserva, en la practica, las
mismas garantias que un token de un solo uso persistido:

- Ligado a la sesion: firmado con una clave DERIVADA de
  `session.csrf_secret` (nunca reutilizada tal cual -- Ver `_derive_key`,
  separacion de dominio por proposito) -- un challenge de una sesion
  nunca es aceptado por otra.
- TTL corto embebido (`_CHALLENGE_TTL_SECONDS`), independiente del TTL
  de CSRF.
- Ligado por contenido a TODOS los campos criticos de la intencion
  (`PreparedOperationalIntent` completo va firmado dentro del token) --
  si CUALQUIERA cambia entre `prepare` y `confirm`/`execute`, el
  workflow (`operational_workflow.py`) lo detecta al revalidar contra
  el estado real, no hace falta compararlo aqui.
- "Un solo uso" en la practica: `materialize_unified_activation`
  (Fase 14B) es idempotente para el MISMO estado y avanza
  `active.json.pointer_version` en cada ejecucion real -- repetir el
  MISMO challenge tras una ejecucion exitosa encuentra
  `expected_active_pointer_hash` desactualizado y es rechazado por la
  revalidacion de estado del workflow, nunca por un registro
  server-side de "ya usado" (que exigiria el estado global mutable que
  este proyecto prohibe)."""

from __future__ import annotations

import base64
import hmac
import time
from hashlib import sha256

from ..contracts.operational_authorization_request import PreparedOperationalIntent
from .session import SessionData

_CHALLENGE_TTL_SECONDS = 300
_DOMAIN_SEPARATION_LABEL = b"altamira-operational-challenge-v1"
_TOKEN_PARTS = 3


def _derive_key(csrf_secret: str) -> bytes:
    """Nunca reutiliza `csrf_secret` tal cual como clave HMAC de otro
    proposito -- deriva una clave especifica de este uso (separacion de
    dominio) para que un challenge y un token CSRF de la misma sesion
    no sean intercambiables entre si."""
    return hmac.new(csrf_secret.encode("utf-8"), _DOMAIN_SEPARATION_LABEL, sha256).digest()


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes | None:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except (ValueError, TypeError):
        return None


def sign_challenge(
    intent: PreparedOperationalIntent, session: SessionData, *, now: float | None = None
) -> str:
    issued = now if now is not None else time.time()
    expires_at = int(issued) + _CHALLENGE_TTL_SECONDS
    payload = intent.to_stable_json().encode("utf-8")
    key = _derive_key(session.csrf_secret)
    signature = hmac.new(key, payload + f".{expires_at}".encode(), sha256).digest()
    return f"{_b64url_encode(payload)}.{expires_at}.{_b64url_encode(signature)}"


def verify_challenge(
    token: str, session: SessionData, *, now: float | None = None
) -> PreparedOperationalIntent | None:
    """`None` para cualquier token ausente/malformado/con firma
    invalida/expirado/de otra sesion -- nunca lanza, nunca distingue el
    motivo en el resultado (esa distincion queda solo en diagnosticos
    internos, nunca en la respuesta al cliente)."""
    if not token:
        return None
    parts = token.split(".", 2)
    if len(parts) != _TOKEN_PARTS:
        return None
    payload_b64, expires_at_raw, signature_b64 = parts
    payload = _b64url_decode(payload_b64)
    signature = _b64url_decode(signature_b64)
    if payload is None or signature is None:
        return None
    try:
        expires_at = int(expires_at_raw)
    except ValueError:
        return None

    key = _derive_key(session.csrf_secret)
    expected_signature = hmac.new(key, payload + f".{expires_at}".encode(), sha256).digest()
    if not hmac.compare_digest(signature, expected_signature):
        return None

    current = now if now is not None else time.time()
    if current > expires_at:
        return None

    try:
        return PreparedOperationalIntent.model_validate_json(payload)
    except ValueError:
        return None


__all__ = ["sign_challenge", "verify_challenge"]
