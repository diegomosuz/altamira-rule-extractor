"""Configuracion de seguridad tipada (Fase 15B1,
`feat/final-hardening-release`).

`SecurityConfig` describe COMO se resuelve la identidad de un usuario
(nunca el secreto de firma de sesion, que vive exclusivamente en
`Settings`/variables de entorno -- ver `security/session.py`) y que
puede hacer cada rol. Se carga desde un YAML EXTERNO opcional
(`config/security.yaml`, nunca versionado; `config/security.example.yaml`
es el unico ejemplo seguro versionado) -- la AUSENCIA de ese archivo
es, deliberadamente, el default mas seguro posible: `DISABLED_DEV`.

Dos modos, nunca mas hasta una fase futura (Fase 15B2): `DISABLED_DEV`
(sin identidad real, principal anonimo de solo lectura) y
`TRUSTED_PROXY_HEADERS` (identidad inyectada por un reverse proxy
confiable -- ver `docs/REVERSE_PROXY_SECURITY_REQUIREMENTS.md`, unica
forma de despliegue segura para ese modo)."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, SecretStr, model_validator

from .base import AltamiraBaseModel

_DANGEROUS_HEADER_NAMES = frozenset(
    {
        "authorization",
        "cookie",
        "host",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
        "x-forwarded-port",
        "content-length",
        "content-type",
        "transfer-encoding",
    }
)


class AuthenticationMode(StrEnum):
    DISABLED_DEV = "DISABLED_DEV"
    TRUSTED_PROXY_HEADERS = "TRUSTED_PROXY_HEADERS"


class ApplicationRole(StrEnum):
    """Orden ascendente de privilegio -- `VIEWER` es siempre el minimo,
    nunca hay un rol por debajo. `default_authenticated_role` (mas
    abajo) esta anclado estructuralmente a este minimo."""

    VIEWER = "VIEWER"
    REVIEWER = "REVIEWER"
    OPERATOR = "OPERATOR"
    ADMIN = "ADMIN"


class OperationalPermission(StrEnum):
    VIEW_GOVERNANCE = "VIEW_GOVERNANCE"
    DOWNLOAD_ACTIVE_ARTIFACT = "DOWNLOAD_ACTIVE_ARTIFACT"
    PREPARE_AUTHORIZATION = "PREPARE_AUTHORIZATION"
    ACTIVATE_CANARY = "ACTIVATE_CANARY"
    ACTIVATE_PRIMARY = "ACTIVATE_PRIMARY"
    EXECUTE_FALLBACK = "EXECUTE_FALLBACK"
    EXECUTE_ROLLBACK = "EXECUTE_ROLLBACK"
    VIEW_AUDIT_LOG = "VIEW_AUDIT_LOG"
    VIEW_SECURITY_STATUS = "VIEW_SECURITY_STATUS"


def _normalize_header_name(value: str) -> str:
    return value.strip().lower()


def _check_not_dangerous_header(value: str, *, field_name: str) -> str:
    if not value or value.strip() != value:
        raise ValueError(f"{field_name} invalido (vacio o con espacios en los bordes): {value!r}")
    if _normalize_header_name(value) in _DANGEROUS_HEADER_NAMES:
        raise ValueError(
            f"{field_name} no puede ser un header estandar peligroso (Authorization/Cookie/"
            f"Host/X-Forwarded-*): {value!r}"
        )
    return value


class SecurityConfig(AltamiraBaseModel):
    """Contrato de configuracion de seguridad -- NUNCA contiene un
    secreto real (el `trusted_proxy_required_marker_value` es
    `SecretStr` como defensa en profundidad contra impresion/log
    accidental, pero el archivo `config/security.example.yaml`
    versionado nunca lleva un valor real)."""

    schema_version: Literal["1.0"] = "1.0"
    authentication_mode: AuthenticationMode

    trusted_proxy_header_user: str = Field(min_length=1)
    trusted_proxy_header_email: str | None = Field(default=None, min_length=1)
    trusted_proxy_header_groups: str | None = Field(default=None, min_length=1)
    trusted_proxy_required_marker_header: str = Field(min_length=1)
    trusted_proxy_required_marker_value: SecretStr

    trusted_proxy_allowed_roles: list[ApplicationRole] = Field(default_factory=list)
    group_role_mapping: dict[str, ApplicationRole] = Field(default_factory=dict)
    default_authenticated_role: ApplicationRole = ApplicationRole.VIEWER

    session_cookie_name: str = Field(min_length=1)
    session_cookie_secure: bool
    session_cookie_httponly: Literal[True] = True
    session_cookie_samesite: Literal["lax", "strict"] = "strict"
    csrf_enabled: Literal[True] = True
    csrf_token_ttl_seconds: int = Field(default=3600, ge=60, le=86400)
    session_ttl_seconds: int = Field(default=28800, ge=60, le=86400)

    require_distinct_reviewer_for_primary: bool = True
    require_distinct_reviewer_for_rollback: bool = False

    audit_enabled: Literal[True] = True
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_identity_headers_never_dangerous(self) -> SecurityConfig:
        _check_not_dangerous_header(
            self.trusted_proxy_header_user, field_name="trusted_proxy_header_user"
        )
        if self.trusted_proxy_header_email is not None:
            _check_not_dangerous_header(
                self.trusted_proxy_header_email, field_name="trusted_proxy_header_email"
            )
        if self.trusted_proxy_header_groups is not None:
            _check_not_dangerous_header(
                self.trusted_proxy_header_groups, field_name="trusted_proxy_header_groups"
            )
        _check_not_dangerous_header(
            self.trusted_proxy_required_marker_header,
            field_name="trusted_proxy_required_marker_header",
        )
        return self

    @model_validator(mode="after")
    def _check_default_authenticated_role_is_viewer(self) -> SecurityConfig:
        """`default_authenticated_role` maximo VIEWER -- dado que
        `VIEWER` ya es el minimo posible del enum, esto exige
        exactamente `VIEWER` en todo momento, en ambos modos."""
        if self.default_authenticated_role != ApplicationRole.VIEWER:
            raise ValueError("default_authenticated_role debe ser VIEWER (el minimo posible)")
        return self

    @model_validator(mode="after")
    def _check_disabled_dev_never_grants_privilege_implicitly(self) -> SecurityConfig:
        if self.authentication_mode == AuthenticationMode.DISABLED_DEV and (
            ApplicationRole.OPERATOR in self.trusted_proxy_allowed_roles
            or ApplicationRole.ADMIN in self.trusted_proxy_allowed_roles
        ):
            raise ValueError(
                "authentication_mode=DISABLED_DEV no puede declarar OPERATOR/ADMIN en "
                "trusted_proxy_allowed_roles (nunca se usan en este modo, pero declararlos "
                "es una senal de configuracion peligrosa)"
            )
        return self

    @model_validator(mode="after")
    def _check_secure_cookie_outside_dev(self) -> SecurityConfig:
        if (
            self.authentication_mode != AuthenticationMode.DISABLED_DEV
            and not self.session_cookie_secure
        ):
            raise ValueError(
                "session_cookie_secure debe ser true salvo en authentication_mode=DISABLED_DEV"
            )
        return self

    @model_validator(mode="after")
    def _check_group_mapping_values_within_allowed_roles(self) -> SecurityConfig:
        if not self.trusted_proxy_allowed_roles:
            return self
        allowed = set(self.trusted_proxy_allowed_roles)
        for group, role in self.group_role_mapping.items():
            if role not in allowed:
                raise ValueError(
                    f"group_role_mapping[{group!r}]={role.value} no esta en "
                    "trusted_proxy_allowed_roles"
                )
        return self

    @model_validator(mode="after")
    def _check_diagnostics_sorted_and_unique(self) -> SecurityConfig:
        if self.diagnostics != sorted(set(self.diagnostics)):
            raise ValueError("diagnostics debe estar ordenado y sin duplicados")
        return self


def default_disabled_dev_security_config() -> SecurityConfig:
    """Configuracion segura por defecto cuando no existe
    `config/security.yaml` -- la AUSENCIA de configuracion explicita
    nunca habilita `TRUSTED_PROXY_HEADERS`."""
    return SecurityConfig(
        authentication_mode=AuthenticationMode.DISABLED_DEV,
        trusted_proxy_header_user="X-Altamira-Verified-User",
        trusted_proxy_required_marker_header="X-Altamira-Trusted-Proxy",
        trusted_proxy_required_marker_value=SecretStr("dev-marker-not-enforced"),
        session_cookie_name="altamira_session",
        session_cookie_secure=False,
    )


__all__ = [
    "ApplicationRole",
    "AuthenticationMode",
    "OperationalPermission",
    "SecurityConfig",
    "default_disabled_dev_security_config",
]
