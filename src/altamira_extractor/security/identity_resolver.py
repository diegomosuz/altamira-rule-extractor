"""Resolucion de identidad por request (Fase 15B1,
`feat/final-hardening-release`).

Es el UNICO lugar que traduce headers HTTP (en `TRUSTED_PROXY_HEADERS`)
o la ausencia total de identidad (en `DISABLED_DEV`) a un
`AuthenticatedPrincipal`. Nunca cachea la identidad en la sesion: se
resuelve de nuevo en cada request (ver `security/session.py`).

`DISABLED_DEV` NUNCA lee ningun header de identidad -- ni siquiera para
ignorarlos -- de modo que no hay ninguna ruta de codigo en la que un
header enviado por un cliente pueda influir el principal anonimo."""

from __future__ import annotations

import hmac
from collections.abc import Mapping

from ..api.errors import UnauthenticatedError
from ..contracts.security_config import ApplicationRole, AuthenticationMode, SecurityConfig
from ..contracts.security_identity import AuthenticatedPrincipal, permissions_for_roles

_DEV_MODE_DIAGNOSTIC = "dev_mode_no_identity_provider"
_ANONYMOUS_DEV_PRINCIPAL_ID = "anonymous-dev"

_MAX_MARKER_HEADER_LENGTH = 512
_MAX_USER_HEADER_LENGTH = 320
_MAX_EMAIL_HEADER_LENGTH = 320
_MAX_GROUPS_HEADER_LENGTH = 4096
_MAX_SINGLE_GROUP_LENGTH = 128
_MAX_GROUP_COUNT = 64


def _get_header(headers: Mapping[str, str], name: str) -> str | None:
    """Busqueda case-insensitive que funciona tanto con `dict` planos
    (tests) como con `starlette.datastructures.Headers` (que ya es
    case-insensitive por si mismo, por lo que el fallback nunca se
    ejercita en produccion pero mantiene la funcion pura y testeable)."""
    if name in headers:
        return headers[name]
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


def _reject_control_characters(value: str, *, field_name: str) -> None:
    if "\r" in value or "\n" in value or "\x00" in value:
        raise UnauthenticatedError(f"{field_name} contiene caracteres de control no permitidos")


def _validate_header_value(value: str, *, field_name: str, max_length: int) -> str:
    stripped = value.strip()
    if not stripped:
        raise UnauthenticatedError(f"{field_name} esta vacio")
    _reject_control_characters(value, field_name=field_name)
    if len(value) > max_length:
        raise UnauthenticatedError(f"{field_name} excede la longitud maxima permitida")
    return stripped


def _normalize_principal_id(raw_user: str) -> str:
    return raw_user.strip().lower()


def _resolve_groups(raw_groups: str | None) -> list[str]:
    if raw_groups is None:
        return []
    _validate_header_value(
        raw_groups, field_name="trusted_proxy_header_groups", max_length=_MAX_GROUPS_HEADER_LENGTH
    )
    groups: list[str] = []
    for entry in raw_groups.split(","):
        candidate = entry.strip()
        if not candidate:
            continue
        _reject_control_characters(candidate, field_name="grupo de trusted_proxy_header_groups")
        if len(candidate) > _MAX_SINGLE_GROUP_LENGTH:
            raise UnauthenticatedError(
                "un grupo de trusted_proxy_header_groups excede la longitud maxima"
            )
        if candidate not in groups:
            groups.append(candidate)
    if len(groups) > _MAX_GROUP_COUNT:
        raise UnauthenticatedError("trusted_proxy_header_groups declara demasiados grupos")
    return groups


def _resolve_roles_from_groups(groups: list[str], config: SecurityConfig) -> list[ApplicationRole]:
    """Mapea grupo -> rol EXCLUSIVAMENTE via `config.group_role_mapping`
    -- cualquier header/campo que pretenda enviar roles directamente se
    ignora por construccion: esta funcion nunca lee un header de roles,
    solo nombres de grupo ya resueltos."""
    roles: set[ApplicationRole] = {ApplicationRole.VIEWER}
    for group in groups:
        mapped_role = config.group_role_mapping.get(group)
        if mapped_role is not None:
            roles.add(mapped_role)
    ordering = list(ApplicationRole)
    return sorted(roles, key=ordering.index)


def _resolve_disabled_dev_principal() -> AuthenticatedPrincipal:
    roles = [ApplicationRole.VIEWER]
    return AuthenticatedPrincipal(
        principal_id=_ANONYMOUS_DEV_PRINCIPAL_ID,
        display_name="Modo desarrollo (sin proveedor de identidad)",
        email=None,
        authentication_mode=AuthenticationMode.DISABLED_DEV,
        roles=roles,
        permissions=permissions_for_roles(roles),
        groups=[],
        authenticated=False,
        diagnostics=[_DEV_MODE_DIAGNOSTIC],
    )


def _resolve_trusted_proxy_principal(
    headers: Mapping[str, str], config: SecurityConfig
) -> AuthenticatedPrincipal:
    marker_value = _get_header(headers, config.trusted_proxy_required_marker_header)
    if marker_value is None:
        raise UnauthenticatedError("marker de proxy confiable ausente")
    if len(marker_value) > _MAX_MARKER_HEADER_LENGTH:
        raise UnauthenticatedError("marker de proxy confiable excede la longitud maxima")
    _reject_control_characters(marker_value, field_name="marker de proxy confiable")
    expected_marker = config.trusted_proxy_required_marker_value.get_secret_value()
    if not hmac.compare_digest(marker_value, expected_marker):
        raise UnauthenticatedError("marker de proxy confiable incorrecto")

    raw_user = _get_header(headers, config.trusted_proxy_header_user)
    if raw_user is None:
        raise UnauthenticatedError("header de usuario del proxy confiable ausente")
    user = _validate_header_value(
        raw_user, field_name="trusted_proxy_header_user", max_length=_MAX_USER_HEADER_LENGTH
    )

    email: str | None = None
    if config.trusted_proxy_header_email is not None:
        raw_email = _get_header(headers, config.trusted_proxy_header_email)
        if raw_email is not None:
            email = _validate_header_value(
                raw_email,
                field_name="trusted_proxy_header_email",
                max_length=_MAX_EMAIL_HEADER_LENGTH,
            )

    raw_groups = (
        _get_header(headers, config.trusted_proxy_header_groups)
        if config.trusted_proxy_header_groups is not None
        else None
    )
    groups = _resolve_groups(raw_groups)
    roles = _resolve_roles_from_groups(groups, config)

    return AuthenticatedPrincipal(
        principal_id=_normalize_principal_id(user),
        display_name=user,
        email=email,
        authentication_mode=AuthenticationMode.TRUSTED_PROXY_HEADERS,
        roles=roles,
        permissions=permissions_for_roles(roles),
        groups=groups,
        authenticated=True,
        diagnostics=[],
    )


def resolve_principal(headers: Mapping[str, str], config: SecurityConfig) -> AuthenticatedPrincipal:
    """Punto de entrada unico. Lanza `UnauthenticatedError` (401) si
    `authentication_mode=TRUSTED_PROXY_HEADERS` y la identidad esta
    ausente o es invalida -- nunca construye un `AuthenticatedPrincipal`
    parcial o `authenticated=False` en ese modo."""
    if config.authentication_mode == AuthenticationMode.DISABLED_DEV:
        return _resolve_disabled_dev_principal()
    return _resolve_trusted_proxy_principal(headers, config)


__all__ = ["resolve_principal"]
