"""Tests de `contracts/security_identity.py` (Fase 15B1 Parte 16,
secciones IDENTIDAD 9-19 y RBAC 20-29, `feat/final-hardening-release`)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts.security_config import (
    ApplicationRole,
    AuthenticationMode,
    OperationalPermission,
)
from altamira_extractor.contracts.security_identity import (
    ROLE_PERMISSIONS,
    AuthenticatedPrincipal,
    permissions_for_roles,
)


def _principal(**overrides: object) -> AuthenticatedPrincipal:
    base: dict[str, object] = {
        "principal_id": "alice",
        "display_name": "Alice",
        "authentication_mode": AuthenticationMode.TRUSTED_PROXY_HEADERS,
        "roles": [ApplicationRole.VIEWER],
        "permissions": permissions_for_roles([ApplicationRole.VIEWER]),
        "groups": [],
        "authenticated": True,
    }
    base.update(overrides)
    return AuthenticatedPrincipal.model_validate(base)


# 20. VIEWER: exactamente VIEW_GOVERNANCE + DOWNLOAD_ACTIVE_ARTIFACT.
def test_viewer_permission_set() -> None:
    assert ROLE_PERMISSIONS[ApplicationRole.VIEWER] == {
        OperationalPermission.VIEW_GOVERNANCE,
        OperationalPermission.DOWNLOAD_ACTIVE_ARTIFACT,
    }


# 21. REVIEWER: VIEWER + PREPARE_AUTHORIZATION + VIEW_AUDIT_LOG, nunca ACTIVATE_*.
def test_reviewer_permission_set() -> None:
    perms = ROLE_PERMISSIONS[ApplicationRole.REVIEWER]
    assert perms == {
        OperationalPermission.VIEW_GOVERNANCE,
        OperationalPermission.DOWNLOAD_ACTIVE_ARTIFACT,
        OperationalPermission.PREPARE_AUTHORIZATION,
        OperationalPermission.VIEW_AUDIT_LOG,
    }
    assert OperationalPermission.ACTIVATE_CANARY not in perms
    assert OperationalPermission.ACTIVATE_PRIMARY not in perms


# 22. OPERATOR: VIEWER + PREPARE_AUTHORIZATION + ACTIVATE_CANARY + EXECUTE_FALLBACK +
# EXECUTE_ROLLBACK + VIEW_AUDIT_LOG + VIEW_SECURITY_STATUS, nunca ACTIVATE_PRIMARY.
def test_operator_permission_set_excludes_primary() -> None:
    perms = ROLE_PERMISSIONS[ApplicationRole.OPERATOR]
    assert OperationalPermission.ACTIVATE_CANARY in perms
    assert OperationalPermission.EXECUTE_FALLBACK in perms
    assert OperationalPermission.EXECUTE_ROLLBACK in perms
    assert OperationalPermission.VIEW_SECURITY_STATUS in perms
    assert OperationalPermission.ACTIVATE_PRIMARY not in perms


# 23. ADMIN: todos los permisos, incluyendo ACTIVATE_PRIMARY.
def test_admin_has_all_permissions() -> None:
    assert ROLE_PERMISSIONS[ApplicationRole.ADMIN] == frozenset(OperationalPermission)


# 24. La matriz vive en un unico modulo: permissions_for_roles es la unica fuente derivada.
def test_permissions_for_roles_is_union_of_matrix() -> None:
    combined = permissions_for_roles([ApplicationRole.VIEWER, ApplicationRole.OPERATOR])
    assert (
        combined
        == ROLE_PERMISSIONS[ApplicationRole.VIEWER] | ROLE_PERMISSIONS[ApplicationRole.OPERATOR]
    )


# 25. has_permission refleja exactamente el conjunto declarado.
def test_has_permission_reflects_declared_set() -> None:
    principal = _principal(
        roles=[ApplicationRole.OPERATOR],
        permissions=permissions_for_roles([ApplicationRole.OPERATOR]),
    )
    assert principal.has_permission(OperationalPermission.ACTIVATE_CANARY)
    assert not principal.has_permission(OperationalPermission.ACTIVATE_PRIMARY)


# 9/26. permissions debe coincidir EXACTAMENTE con permissions_for_roles(roles) -- nunca
# permisos declarados fuera de la matriz central.
def test_permissions_must_match_roles_exactly() -> None:
    with pytest.raises(ValidationError):
        _principal(
            roles=[ApplicationRole.VIEWER],
            permissions=frozenset({OperationalPermission.ACTIVATE_PRIMARY}),
        )


# 10. DISABLED_DEV exige authenticated=false.
def test_disabled_dev_requires_unauthenticated() -> None:
    with pytest.raises(ValidationError):
        _principal(
            authentication_mode=AuthenticationMode.DISABLED_DEV,
            authenticated=True,
            roles=[ApplicationRole.VIEWER],
            permissions=permissions_for_roles([ApplicationRole.VIEWER]),
        )


# 11. DISABLED_DEV exige roles=[VIEWER] exactamente -- nunca otro rol, ni siquiera ademas de VIEWER.
def test_disabled_dev_requires_exactly_viewer_role() -> None:
    with pytest.raises(ValidationError):
        _principal(
            authentication_mode=AuthenticationMode.DISABLED_DEV,
            authenticated=False,
            roles=[ApplicationRole.VIEWER, ApplicationRole.ADMIN],
            permissions=permissions_for_roles([ApplicationRole.VIEWER, ApplicationRole.ADMIN]),
        )


# 12. TRUSTED_PROXY_HEADERS exige authenticated=true -- nunca un principal parcial/anonimo.
def test_trusted_proxy_mode_requires_authenticated() -> None:
    with pytest.raises(ValidationError):
        _principal(
            authentication_mode=AuthenticationMode.TRUSTED_PROXY_HEADERS, authenticated=False
        )


# 13. Un principal DISABLED_DEV valido se construye sin error.
def test_disabled_dev_principal_constructs_cleanly() -> None:
    principal = _principal(
        authentication_mode=AuthenticationMode.DISABLED_DEV,
        authenticated=False,
        roles=[ApplicationRole.VIEWER],
        permissions=permissions_for_roles([ApplicationRole.VIEWER]),
    )
    assert principal.authenticated is False
    assert principal.roles == [ApplicationRole.VIEWER]


# 14. El contrato nunca declara un campo de token/cookie/header crudo/Authorization.
def test_principal_never_exposes_raw_credential_fields() -> None:
    field_names = set(AuthenticatedPrincipal.model_fields)
    forbidden = {"token", "cookie", "authorization", "raw_headers", "password", "session_secret"}
    assert field_names.isdisjoint(forbidden)
