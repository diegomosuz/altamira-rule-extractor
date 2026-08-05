"""Principal autenticado y matriz RBAC central (Fase 15B1,
`feat/final-hardening-release`).

`AuthenticatedPrincipal` es el UNICO contrato de identidad que routers/
templates consultan -- nunca un header crudo, nunca un token, nunca una
cookie. La matriz de permisos por rol vive EXCLUSIVAMENTE en este
modulo (`ROLE_PERMISSIONS`/`permissions_for_role`) -- ningun router ni
template debe reimplementar o duplicar esta regla."""

from __future__ import annotations

from pydantic import Field, model_validator

from .base import AltamiraBaseModel
from .security_config import ApplicationRole, AuthenticationMode, OperationalPermission

_VIEWER_PERMISSIONS: frozenset[OperationalPermission] = frozenset(
    {
        OperationalPermission.VIEW_GOVERNANCE,
        OperationalPermission.DOWNLOAD_ACTIVE_ARTIFACT,
    }
)
_REVIEWER_PERMISSIONS: frozenset[OperationalPermission] = _VIEWER_PERMISSIONS | frozenset(
    {
        OperationalPermission.PREPARE_AUTHORIZATION,
        OperationalPermission.VIEW_AUDIT_LOG,
    }
)
_OPERATOR_PERMISSIONS: frozenset[OperationalPermission] = _VIEWER_PERMISSIONS | frozenset(
    {
        OperationalPermission.PREPARE_AUTHORIZATION,
        OperationalPermission.ACTIVATE_CANARY,
        OperationalPermission.EXECUTE_FALLBACK,
        OperationalPermission.EXECUTE_ROLLBACK,
        OperationalPermission.VIEW_AUDIT_LOG,
        OperationalPermission.VIEW_SECURITY_STATUS,
    }
)
_ADMIN_PERMISSIONS: frozenset[OperationalPermission] = frozenset(OperationalPermission) | {
    OperationalPermission.ACTIVATE_PRIMARY
}

ROLE_PERMISSIONS: dict[ApplicationRole, frozenset[OperationalPermission]] = {
    ApplicationRole.VIEWER: _VIEWER_PERMISSIONS,
    ApplicationRole.REVIEWER: _REVIEWER_PERMISSIONS,
    ApplicationRole.OPERATOR: _OPERATOR_PERMISSIONS,
    ApplicationRole.ADMIN: _ADMIN_PERMISSIONS,
}


def permissions_for_roles(roles: list[ApplicationRole]) -> frozenset[OperationalPermission]:
    """Union de permisos de todos los roles declarados -- un principal
    puede tener mas de un rol (p. ej. varios grupos mapeados); nunca se
    resta un permiso, solo se agrega."""
    result: set[OperationalPermission] = set()
    for role in roles:
        result |= ROLE_PERMISSIONS[role]
    return frozenset(result)


class AuthenticatedPrincipal(AltamiraBaseModel):
    """Identidad resuelta de UNA request -- nunca token, cookie, header
    `Authorization`, header de identidad crudo, IP completa ni user
    agent completo. `authenticated=False` UNICAMENTE en `DISABLED_DEV`
    (principal anonimo de solo lectura)."""

    principal_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    email: str | None = None
    authentication_mode: AuthenticationMode
    roles: list[ApplicationRole] = Field(default_factory=list)
    permissions: frozenset[OperationalPermission] = Field(default_factory=frozenset)
    groups: list[str] = Field(default_factory=list)
    authenticated: bool
    diagnostics: list[str] = Field(default_factory=list)

    def has_permission(self, permission: OperationalPermission) -> bool:
        return permission in self.permissions

    @model_validator(mode="after")
    def _check_permissions_match_roles(self) -> AuthenticatedPrincipal:
        expected = permissions_for_roles(self.roles)
        if self.permissions != expected:
            raise ValueError(
                "permissions no coincide con la union de ROLE_PERMISSIONS para "
                f"roles={[r.value for r in self.roles]} -- nunca se declaran permisos "
                "fuera de la matriz central"
            )
        return self

    @model_validator(mode="after")
    def _check_disabled_dev_is_never_authenticated_or_privileged(self) -> AuthenticatedPrincipal:
        if self.authentication_mode == AuthenticationMode.DISABLED_DEV:
            if self.authenticated:
                raise ValueError("authentication_mode=DISABLED_DEV exige authenticated=false")
            if self.roles != [ApplicationRole.VIEWER]:
                raise ValueError("authentication_mode=DISABLED_DEV exige roles=[VIEWER]")
        return self

    @model_validator(mode="after")
    def _check_trusted_mode_requires_authenticated(self) -> AuthenticatedPrincipal:
        if (
            self.authentication_mode == AuthenticationMode.TRUSTED_PROXY_HEADERS
            and not self.authenticated
        ):
            raise ValueError(
                "authentication_mode=TRUSTED_PROXY_HEADERS exige authenticated=true -- una "
                "identidad invalida/ausente debe producir 401 antes de construir un principal, "
                "nunca un principal authenticated=false"
            )
        return self


__all__ = [
    "ROLE_PERMISSIONS",
    "AuthenticatedPrincipal",
    "permissions_for_roles",
]
