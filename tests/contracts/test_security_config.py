"""Tests de `contracts/security_config.py` (Fase 15B1 Parte 16,
seccion CONFIGURACION 1-8, `feat/final-hardening-release`)."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from altamira_extractor.contracts.security_config import (
    ApplicationRole,
    AuthenticationMode,
    OperationalPermission,
    SecurityConfig,
    default_disabled_dev_security_config,
)

_MARKER = SecretStr("marker-value")


def _base_kwargs(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "authentication_mode": AuthenticationMode.TRUSTED_PROXY_HEADERS,
        "trusted_proxy_header_user": "X-Verified-User",
        "trusted_proxy_required_marker_header": "X-Trusted-Proxy",
        "trusted_proxy_required_marker_value": _MARKER,
        "session_cookie_name": "altamira_session",
        "session_cookie_secure": True,
    }
    kwargs.update(overrides)
    return kwargs


# 1. Modo por defecto sin archivo -> DISABLED_DEV, nunca TRUSTED_PROXY_HEADERS.
def test_default_disabled_dev_config_is_safe() -> None:
    config = default_disabled_dev_security_config()
    assert config.authentication_mode == AuthenticationMode.DISABLED_DEV
    assert config.session_cookie_secure is False
    assert config.default_authenticated_role == ApplicationRole.VIEWER


# 2. Solo dos modos permitidos, un tercero es rechazado a nivel de tipo.
def test_only_two_authentication_modes_exist() -> None:
    assert set(AuthenticationMode) == {
        AuthenticationMode.DISABLED_DEV,
        AuthenticationMode.TRUSTED_PROXY_HEADERS,
    }


# 3. Header de identidad no puede ser un header peligroso estandar.
@pytest.mark.parametrize("header", ["Authorization", "Cookie", "Host", "X-Forwarded-For"])
def test_identity_header_rejects_dangerous_names(header: str) -> None:
    with pytest.raises(ValidationError):
        SecurityConfig.model_validate(_base_kwargs(trusted_proxy_header_user=header))


# 4. default_authenticated_role debe ser exactamente VIEWER.
def test_default_authenticated_role_must_be_viewer() -> None:
    with pytest.raises(ValidationError):
        SecurityConfig.model_validate(
            _base_kwargs(default_authenticated_role=ApplicationRole.OPERATOR)
        )


# 5. DISABLED_DEV no puede declarar OPERATOR/ADMIN en trusted_proxy_allowed_roles.
def test_disabled_dev_cannot_declare_privileged_allowed_roles() -> None:
    with pytest.raises(ValidationError):
        SecurityConfig(
            authentication_mode=AuthenticationMode.DISABLED_DEV,
            trusted_proxy_header_user="X-User",
            trusted_proxy_required_marker_header="X-Marker",
            trusted_proxy_required_marker_value=_MARKER,
            trusted_proxy_allowed_roles=[ApplicationRole.OPERATOR],
            session_cookie_name="altamira_session",
            session_cookie_secure=False,
        )


# 6. session_cookie_secure debe ser true fuera de DISABLED_DEV.
def test_secure_cookie_required_outside_disabled_dev() -> None:
    with pytest.raises(ValidationError):
        SecurityConfig.model_validate(_base_kwargs(session_cookie_secure=False))


# 7. group_role_mapping solo puede referenciar roles ya declarados en trusted_proxy_allowed_roles.
def test_group_role_mapping_scoped_to_allowed_roles() -> None:
    with pytest.raises(ValidationError):
        SecurityConfig.model_validate(
            _base_kwargs(
                trusted_proxy_allowed_roles=[ApplicationRole.REVIEWER],
                group_role_mapping={"ops": ApplicationRole.OPERATOR},
            )
        )
    valid = SecurityConfig.model_validate(
        _base_kwargs(
            trusted_proxy_allowed_roles=[ApplicationRole.OPERATOR],
            group_role_mapping={"ops": ApplicationRole.OPERATOR},
        )
    )
    assert valid.group_role_mapping == {"ops": ApplicationRole.OPERATOR}


# 8. diagnostics ordenado y sin duplicados; marker_value nunca se imprime como texto plano.
def test_diagnostics_sorted_unique_and_marker_is_secret() -> None:
    with pytest.raises(ValidationError):
        SecurityConfig.model_validate(_base_kwargs(diagnostics=["b", "a"]))
    with pytest.raises(ValidationError):
        SecurityConfig.model_validate(_base_kwargs(diagnostics=["a", "a"]))
    config = SecurityConfig.model_validate(_base_kwargs())
    assert "marker-value" not in repr(config)
    assert "marker-value" not in str(config)
    assert set(OperationalPermission) == {
        OperationalPermission.VIEW_GOVERNANCE,
        OperationalPermission.DOWNLOAD_ACTIVE_ARTIFACT,
        OperationalPermission.PREPARE_AUTHORIZATION,
        OperationalPermission.ACTIVATE_CANARY,
        OperationalPermission.ACTIVATE_PRIMARY,
        OperationalPermission.EXECUTE_FALLBACK,
        OperationalPermission.EXECUTE_ROLLBACK,
        OperationalPermission.VIEW_AUDIT_LOG,
        OperationalPermission.VIEW_SECURITY_STATUS,
    }
