"""Tests de `security/identity_resolver.py` (Fase 15B1 Parte 16,
secciones IDENTIDAD 9-19, `feat/final-hardening-release`)."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from altamira_extractor.api.errors import UnauthenticatedError
from altamira_extractor.contracts.security_config import (
    ApplicationRole,
    AuthenticationMode,
    SecurityConfig,
    default_disabled_dev_security_config,
)
from altamira_extractor.security.identity_resolver import resolve_principal

_MARKER_HEADER = "X-Trusted-Proxy"
_MARKER_VALUE = "marker-secret-123"
_USER_HEADER = "X-Verified-User"
_EMAIL_HEADER = "X-Verified-Email"
_GROUPS_HEADER = "X-Verified-Groups"


def _trusted_config(**overrides: object) -> SecurityConfig:
    base: dict[str, object] = {
        "authentication_mode": AuthenticationMode.TRUSTED_PROXY_HEADERS,
        "trusted_proxy_header_user": _USER_HEADER,
        "trusted_proxy_header_email": _EMAIL_HEADER,
        "trusted_proxy_header_groups": _GROUPS_HEADER,
        "trusted_proxy_required_marker_header": _MARKER_HEADER,
        "trusted_proxy_required_marker_value": SecretStr(_MARKER_VALUE),
        "trusted_proxy_allowed_roles": [
            ApplicationRole.REVIEWER,
            ApplicationRole.OPERATOR,
            ApplicationRole.ADMIN,
        ],
        "group_role_mapping": {"ops-team": ApplicationRole.OPERATOR},
        "session_cookie_name": "altamira_session",
        "session_cookie_secure": True,
    }
    base.update(overrides)
    return SecurityConfig.model_validate(base)


_VALID_HEADERS = {
    _MARKER_HEADER: _MARKER_VALUE,
    _USER_HEADER: "jane.doe",
    _EMAIL_HEADER: "jane@example.com",
    _GROUPS_HEADER: "ops-team",
}


# 9. DISABLED_DEV nunca lee headers de identidad -- siempre el mismo anonimo.
def test_disabled_dev_never_reads_headers() -> None:
    config = default_disabled_dev_security_config()
    principal = resolve_principal(_VALID_HEADERS, config)
    assert principal.authenticated is False
    assert principal.principal_id == "anonymous-dev"
    assert principal.roles == [ApplicationRole.VIEWER]


# 10. DISABLED_DEV muestra un diagnostico de "modo dev" para el banner de la UI.
def test_disabled_dev_has_banner_diagnostic() -> None:
    config = default_disabled_dev_security_config()
    principal = resolve_principal({}, config)
    assert principal.diagnostics == ["dev_mode_no_identity_provider"]


# 11. TRUSTED_PROXY_HEADERS con marker+usuario validos resuelve un principal autenticado.
def test_trusted_proxy_happy_path() -> None:
    config = _trusted_config()
    principal = resolve_principal(_VALID_HEADERS, config)
    assert principal.authenticated is True
    assert principal.principal_id == "jane.doe"
    assert principal.email == "jane@example.com"
    assert ApplicationRole.OPERATOR in principal.roles
    assert ApplicationRole.VIEWER in principal.roles


# 12. principal_id se normaliza (strip + lower).
def test_principal_id_is_normalized() -> None:
    config = _trusted_config()
    headers = {**_VALID_HEADERS, _USER_HEADER: "  Jane.DOE  "}
    principal = resolve_principal(headers, config)
    assert principal.principal_id == "jane.doe"


# 13. marker header ausente -> 401.
def test_missing_marker_header_raises_401() -> None:
    config = _trusted_config()
    headers = {k: v for k, v in _VALID_HEADERS.items() if k != _MARKER_HEADER}
    with pytest.raises(UnauthenticatedError):
        resolve_principal(headers, config)


# 14. marker header incorrecto -> 401.
def test_wrong_marker_value_raises_401() -> None:
    config = _trusted_config()
    headers = {**_VALID_HEADERS, _MARKER_HEADER: "wrong-value"}
    with pytest.raises(UnauthenticatedError):
        resolve_principal(headers, config)


# 15. header de usuario ausente -> 401.
def test_missing_user_header_raises_401() -> None:
    config = _trusted_config()
    headers = {k: v for k, v in _VALID_HEADERS.items() if k != _USER_HEADER}
    with pytest.raises(UnauthenticatedError):
        resolve_principal(headers, config)


# 16. CR/LF en el header de usuario -> 401 (defensa contra inyeccion).
def test_crlf_in_user_header_raises_401() -> None:
    config = _trusted_config()
    headers = {**_VALID_HEADERS, _USER_HEADER: "jane\r\nX-Injected: evil"}
    with pytest.raises(UnauthenticatedError):
        resolve_principal(headers, config)


# 17. Un valor excesivamente largo en el header de usuario -> 401.
def test_excessively_long_user_header_raises_401() -> None:
    config = _trusted_config()
    headers = {**_VALID_HEADERS, _USER_HEADER: "a" * 400}
    with pytest.raises(UnauthenticatedError):
        resolve_principal(headers, config)


# 18. Un header de "roles" directo enviado por el cliente se ignora por construccion --
# los roles solo se derivan de group_role_mapping, nunca de un campo de rol crudo.
def test_direct_role_header_is_ignored() -> None:
    config = _trusted_config()
    headers = {**_VALID_HEADERS, "X-Roles": "ADMIN"}
    principal = resolve_principal(headers, config)
    assert ApplicationRole.ADMIN not in principal.roles


# 19. Nunca se persiste el header crudo en el principal -- solo campos ya normalizados.
def test_resolved_principal_never_stores_raw_headers() -> None:
    config = _trusted_config()
    principal = resolve_principal(_VALID_HEADERS, config)
    field_names = set(type(principal).model_fields)
    assert "raw_headers" not in field_names
    assert "headers" not in field_names


def test_excessive_group_count_raises_401() -> None:
    config = _trusted_config()
    many_groups = ",".join(f"group-{i}" for i in range(200))
    headers = {**_VALID_HEADERS, _GROUPS_HEADER: many_groups}
    with pytest.raises(UnauthenticatedError):
        resolve_principal(headers, config)


def test_marker_comparison_uses_constant_time_and_rejects_wrong_length() -> None:
    config = _trusted_config()
    headers = {**_VALID_HEADERS, _MARKER_HEADER: _MARKER_VALUE + "x"}
    with pytest.raises(UnauthenticatedError):
        resolve_principal(headers, config)
