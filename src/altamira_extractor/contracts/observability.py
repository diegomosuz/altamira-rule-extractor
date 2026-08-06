"""Configuracion de observabilidad tipada (Fase 15B2-B).

`ObservabilityConfig` describe COMO se exponen logging estructurado,
metricas Prometheus, `/health` y `/ready` -- nunca afecta el resultado
funcional del pipeline (Principio A de la Fase 15B2-B: "Observabilidad
no debe modificar el resultado funcional"). Se carga desde un YAML
VERSIONADO (`config/observability.yaml`, a diferencia de
`config/security.yaml`): no hay secretos en este contrato, solo el
NOMBRE de la variable de entorno que contiene el token de `/internal/
metrics` (`metrics.access_token_env_var`), nunca el token mismo.

A diferencia de `SecurityConfig`, la ausencia o invalidez de este
archivo NUNCA activa un gate global de fail-closed: `ObservabilityMode`
por defecto es siempre seguro (logging JSON activo, metricas
deshabilitadas) -- ver `observability/config_loader.py`."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel

_ENV_VAR_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

_ALLOWED_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class ObservabilityMode(StrEnum):
    DISABLED = "DISABLED"
    ENABLED = "ENABLED"


class MetricsAccessMode(StrEnum):
    DISABLED = "DISABLED"
    INTERNAL_TOKEN = "INTERNAL_TOKEN"
    TRUSTED_PROXY = "TRUSTED_PROXY"


class LogFormat(StrEnum):
    JSON = "JSON"


class ComponentStatus(StrEnum):
    READY = "READY"
    DEGRADED = "DEGRADED"
    NOT_READY = "NOT_READY"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


class LoggingConfig(AltamiraBaseModel):
    format: LogFormat = LogFormat.JSON
    level: str = Field(default="INFO")

    @model_validator(mode="after")
    def _check_level_is_known(self) -> LoggingConfig:
        if self.level.upper() not in _ALLOWED_LOG_LEVELS:
            raise ValueError(
                f"logging.level invalido: {self.level!r} (permitidos: "
                f"{sorted(_ALLOWED_LOG_LEVELS)})"
            )
        return self


class MetricsConfig(AltamiraBaseModel):
    """`access_token_env_var` es el NOMBRE de una variable de entorno
    (nunca el token en si) -- validado con un patron de identificador de
    variable de entorno para evitar que alguien pegue un secreto real
    directamente en este YAML versionado por error."""

    mode: ObservabilityMode = ObservabilityMode.DISABLED
    access_mode: MetricsAccessMode = MetricsAccessMode.DISABLED
    access_token_env_var: str | None = Field(default=None)
    trusted_proxy_marker_header: str | None = Field(default=None, min_length=1)
    trusted_proxy_marker_value_env_var: str | None = Field(default=None)

    @model_validator(mode="after")
    def _check_env_var_names_look_like_names(self) -> MetricsConfig:
        for field_name, value in (
            ("access_token_env_var", self.access_token_env_var),
            ("trusted_proxy_marker_value_env_var", self.trusted_proxy_marker_value_env_var),
        ):
            if value is not None and not _ENV_VAR_NAME_RE.match(value):
                raise ValueError(
                    f"metrics.{field_name} debe ser un nombre de variable de entorno "
                    f"(MAYUSCULAS/digitos/guion bajo), nunca un valor de secreto: {value!r}"
                )
        return self

    @model_validator(mode="after")
    def _check_access_mode_consistency(self) -> MetricsConfig:
        if self.access_mode == MetricsAccessMode.INTERNAL_TOKEN and not self.access_token_env_var:
            raise ValueError(
                "metrics.access_mode=INTERNAL_TOKEN requiere access_token_env_var definido"
            )
        if self.access_mode == MetricsAccessMode.TRUSTED_PROXY and (
            not self.trusted_proxy_marker_header or not self.trusted_proxy_marker_value_env_var
        ):
            raise ValueError(
                "metrics.access_mode=TRUSTED_PROXY requiere trusted_proxy_marker_header y "
                "trusted_proxy_marker_value_env_var definidos"
            )
        if (
            self.mode == ObservabilityMode.ENABLED
            and self.access_mode == MetricsAccessMode.DISABLED
        ):
            raise ValueError(
                "metrics.mode=ENABLED requiere access_mode distinto de DISABLED "
                "(de lo contrario el endpoint quedaria inutilizable)"
            )
        return self


class HealthConfig(AltamiraBaseModel):
    mode: ObservabilityMode = ObservabilityMode.ENABLED


class ReadinessConfig(AltamiraBaseModel):
    mode: ObservabilityMode = ObservabilityMode.ENABLED


class ObservabilityConfig(AltamiraBaseModel):
    schema_version: Literal["1.0"] = "1.0"
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    health: HealthConfig = Field(default_factory=HealthConfig)
    readiness: ReadinessConfig = Field(default_factory=ReadinessConfig)


def default_observability_config() -> ObservabilityConfig:
    """Configuracion segura por defecto cuando `config/observability.yaml`
    esta ausente o es invalida: logging JSON activo, metricas
    deshabilitadas (nunca se expone `/internal/metrics` sin decision
    explicita), health/readiness siempre activos."""
    return ObservabilityConfig()


__all__ = [
    "ComponentStatus",
    "HealthConfig",
    "LogFormat",
    "LoggingConfig",
    "MetricsAccessMode",
    "MetricsConfig",
    "ObservabilityConfig",
    "ObservabilityMode",
    "ReadinessConfig",
    "default_observability_config",
]
