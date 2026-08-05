"""Dependencias FastAPI de identidad/sesion (Fase 15B1 Parte 4/5).

UNICO lugar donde `security/` toca `fastapi.Request`/`Response` para
resolver principal + sesion en cada request -- el resto del paquete
(`identity_resolver.py`, `session.py`, `csrf.py`, `operational_
workflow.py`) permanece framework-agnostico y testeable sin
`TestClient`.

`get_session` NUNCA fija la cookie directamente sobre un objeto
`Response` inyectado: cuando un handler retorna su propio `Response`
(p. ej. `TemplateResponse`, `RedirectResponse`), ese objeto reemplaza
por completo al `Response` que FastAPI inyecta en las dependencias, y
cualquier `set_cookie` hecho sobre el inyectado se pierde en silencio
-- un bug sutil y real de FastAPI si no se tiene en cuenta. En su lugar,
`get_session` guarda la sesion (nueva o releida) en `request.state.
altamira_session`, y `SessionCookieMiddleware` (`api/app.py`) la fija
SIEMPRE sobre la respuesta final real, sea cual sea su tipo."""

from __future__ import annotations

from typing import cast

from fastapi import Request
from pydantic import SecretStr

from ..api.errors import SecurityMisconfiguredError
from ..contracts.security_config import SecurityConfig
from ..contracts.security_identity import AuthenticatedPrincipal
from .identity_resolver import resolve_principal
from .session import SessionData, new_session_data, verify_session_cookie

_REQUEST_STATE_ATTR = "altamira_session"


def _check_not_misconfigured(request: Request) -> None:
    """Fail-closed (cierre Fase 15B1, "DISABLED_DEV explicito"): si
    `config/security.yaml` esta ausente o es invalido, NINGUNA ruta que
    dependa de identidad/sesion entrega datos -- nunca se construye un
    principal anonimo por defecto. Ver `api/app.py::create_app` (fija
    `app.state.security_misconfigured` en el lifespan) y
    `security/security_config_loader.py`."""
    if getattr(request.app.state, "security_misconfigured", False):
        raise SecurityMisconfiguredError()


def get_security_config(request: Request) -> SecurityConfig:
    _check_not_misconfigured(request)
    return cast(SecurityConfig, request.app.state.security_config)


def get_session_secret(request: Request) -> SecretStr:
    return cast(SecretStr, request.app.state.session_secret)


def get_principal(request: Request) -> AuthenticatedPrincipal:
    config = get_security_config(request)
    return resolve_principal(request.headers, config)


def get_session(request: Request) -> SessionData:
    """Lee la cookie de sesion firmada; si esta ausente/invalida/
    expirada, crea una nueva -- nunca falla, nunca lanza. Solo lleva
    `session_id`/`csrf_secret`: nunca copia identidad a la cookie. La
    sesion resuelta se guarda en `request.state` para que
    `SessionCookieMiddleware` la persista en la respuesta final."""
    config = get_security_config(request)
    secret = get_session_secret(request)
    cookie_value = request.cookies.get(config.session_cookie_name)
    session: SessionData | None = None
    if cookie_value is not None:
        session = verify_session_cookie(
            cookie_value, secret, ttl_seconds=config.session_ttl_seconds
        )
    if session is None:
        session = new_session_data()
    setattr(request.state, _REQUEST_STATE_ATTR, session)
    return session


def pending_session(request: Request) -> SessionData | None:
    """Usado exclusivamente por `SessionCookieMiddleware` -- `None` si
    ningun handler de esta request invoco `get_session` (p. ej. rutas
    de solo lectura sin formulario)."""
    return cast(SessionData | None, getattr(request.state, _REQUEST_STATE_ATTR, None))


__all__ = [
    "get_principal",
    "get_security_config",
    "get_session",
    "get_session_secret",
    "pending_session",
]
