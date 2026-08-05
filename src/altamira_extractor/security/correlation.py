"""Correlation ID por request (Fase 15B1 Parte 15).

Bajo `TRUSTED_PROXY_HEADERS`, acepta un header EXTERNO ya inyectado por
el proxy confiable (formato acotado, validado); en cualquier otro caso
-- o si el header esta ausente/mal formado -- genera un UUID4 nuevo.
NUNCA se confia en este header en `DISABLED_DEV`: sin un proxy
confiable delante, cualquier cliente podria falsificarlo."""

from __future__ import annotations

import re
import uuid

from fastapi import Request

from ..contracts.security_config import AuthenticationMode, SecurityConfig

CORRELATION_HEADER_NAME = "X-Correlation-Id"
_CORRELATION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


_REQUEST_STATE_ATTR = "altamira_correlation_id"


def resolve_correlation_id(request: Request, config: SecurityConfig) -> str:
    if config.authentication_mode == AuthenticationMode.TRUSTED_PROXY_HEADERS:
        candidate = request.headers.get(CORRELATION_HEADER_NAME)
        if candidate is not None and _CORRELATION_ID_RE.match(candidate):
            return candidate
    return str(uuid.uuid4())


def get_correlation_id(request: Request) -> str:
    """Lee el `correlation_id` ya resuelto UNA vez por
    `CorrelationLoggingMiddleware` (`api/app.py`) para esta request --
    nunca lo vuelve a calcular (evitaria un UUID distinto al ya
    devuelto en el header de respuesta y logueado)."""
    existing = getattr(request.state, _REQUEST_STATE_ATTR, None)
    if isinstance(existing, str):
        return existing
    return str(uuid.uuid4())


def set_correlation_id(request: Request, correlation_id: str) -> None:
    setattr(request.state, _REQUEST_STATE_ATTR, correlation_id)


__all__ = [
    "CORRELATION_HEADER_NAME",
    "get_correlation_id",
    "resolve_correlation_id",
    "set_correlation_id",
]
