"""CSRF real, con token, para los nuevos endpoints de escritura del
gobierno operativo (Fase 15B1). Complementa -- no reemplaza -- a
`ui/csrf.py::verify_same_origin` (Origin/Referer), que sigue aplicando
sin cambios a las 2 rutas HTML legacy de Prompt 13d.

Metodo elegido: **synchronizer token firmado sin estado en servidor**
(no double-submit-cookie clasico). El campo visible del formulario
(`csrf_token`) es `nonce.expires_at.signature`, donde
`signature = HMAC-SHA256(session.csrf_secret, nonce || expires_at)`.
`session.csrf_secret` vive UNICAMENTE dentro de la cookie de sesion ya
firmada y `HttpOnly` (`security/session.py`) -- nunca se expone en
HTML ni en JS. Esto es mas fuerte que un double-submit clasico: el
valor visible en el DOM no permite, por si solo, derivar el secreto de
sesion (HMAC es de una via), asi que un atacante que solo puede leer/
inyectar el DOM (pero no la cookie `HttpOnly`) no puede forjar un
token valido para otra sesion.

Generado con `secrets.token_urlsafe`, nunca `random`. Comparacion de
firma con `hmac.compare_digest` (tiempo constante). TTL embebido en el
propio token (no requiere almacenamiento server-side ni limpieza).
Nunca se acepta el token por query string: las llamadas de este modulo
reciben el valor ya extraido explicitamente del cuerpo del formulario
por el router -- este modulo nunca lee `request.query_params` ni
`request.url`. Nunca se loguea el valor del token (ver
`security/logging_context.py`)."""

from __future__ import annotations

import hmac
import secrets
import time
from hashlib import sha256

from fastapi import Request

from ..contracts.security_config import SecurityConfig
from ..ui.csrf import CsrfRejectedError, verify_same_origin
from .session import SessionData

CSRF_FORM_FIELD_NAME = "csrf_token"

_TOKEN_PARTS = 3


def _sign(csrf_secret: str, nonce: str, expires_at: int) -> str:
    message = f"{nonce}.{expires_at}".encode()
    return hmac.new(csrf_secret.encode("utf-8"), message, sha256).hexdigest()


def generate_csrf_token(
    session: SessionData, config: SecurityConfig, *, now: float | None = None
) -> str:
    issued = now if now is not None else time.time()
    expires_at = int(issued) + config.csrf_token_ttl_seconds
    nonce = secrets.token_urlsafe(16)
    signature = _sign(session.csrf_secret, nonce, expires_at)
    return f"{nonce}.{expires_at}.{signature}"


def _verify_token_value(
    token: str | None, session: SessionData, *, now: float | None = None
) -> bool:
    """Pura y booleana -- nunca lanza, nunca distingue en el resultado
    entre "ausente", "malformado", "expirado" o "firma incorrecta" (esa
    distincion solo existe en logs/diagnosticos internos, nunca en la
    respuesta al cliente)."""
    if not token:
        return False
    parts = token.split(".", 2)
    if len(parts) != _TOKEN_PARTS:
        return False
    nonce, expires_at_raw, signature = parts
    if not nonce or not signature:
        return False
    try:
        expires_at = int(expires_at_raw)
    except ValueError:
        return False

    expected_signature = _sign(session.csrf_secret, nonce, expires_at)
    if not hmac.compare_digest(signature, expected_signature):
        return False

    current = now if now is not None else time.time()
    return current <= expires_at


def require_csrf(
    request: Request,
    *,
    session: SessionData,
    submitted_token: str | None,
) -> None:
    """Debe llamarse UNICAMENTE desde endpoints `POST` de escritura
    (nunca `GET`/`HEAD`). `submitted_token` debe venir siempre de un
    campo de formulario ya parseado por el router
    (`(await request.form())[CSRF_FORM_FIELD_NAME]`) -- este modulo
    jamas inspecciona la query string. Valida primero Origin/Referer
    (reutilizando `ui/csrf.py` sin modificarlo) y luego el token;
    cualquier fallo produce el mismo `CsrfRejectedError` (403)."""
    verify_same_origin(request)
    if not _verify_token_value(submitted_token, session):
        raise CsrfRejectedError("token CSRF ausente, incorrecto, expirado o de otra sesion")


__all__ = [
    "CSRF_FORM_FIELD_NAME",
    "CsrfRejectedError",
    "generate_csrf_token",
    "require_csrf",
]
