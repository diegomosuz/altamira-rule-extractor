"""Tests de `contracts/observability.py` (Fase 15B2-B): validacion pura
de modelos, sin filesystem ni FastAPI."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts.observability import (
    ComponentStatus,
    LoggingConfig,
    MetricsAccessMode,
    MetricsConfig,
    ObservabilityConfig,
    ObservabilityMode,
    default_observability_config,
)


def test_default_config_is_safe() -> None:
    config = default_observability_config()
    assert config.logging.level == "INFO"
    assert config.metrics.mode == ObservabilityMode.DISABLED
    assert config.metrics.access_mode == MetricsAccessMode.DISABLED
    assert config.health.mode == ObservabilityMode.ENABLED
    assert config.readiness.mode == ObservabilityMode.ENABLED


def test_observability_config_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ObservabilityConfig.model_validate({"schema_version": "1.0", "unexpected_field": 1})


def test_logging_config_rejects_unknown_level() -> None:
    with pytest.raises(ValidationError):
        LoggingConfig(level="TRACE")


def test_logging_config_accepts_known_levels() -> None:
    for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        assert LoggingConfig(level=level).level == level


def test_metrics_config_internal_token_requires_env_var_name() -> None:
    with pytest.raises(ValidationError, match="access_token_env_var"):
        MetricsConfig(mode=ObservabilityMode.ENABLED, access_mode=MetricsAccessMode.INTERNAL_TOKEN)


def test_metrics_config_rejects_lowercase_env_var_name() -> None:
    with pytest.raises(ValidationError, match="nombre de variable de entorno"):
        MetricsConfig(
            mode=ObservabilityMode.ENABLED,
            access_mode=MetricsAccessMode.INTERNAL_TOKEN,
            access_token_env_var="not-a-valid-name",
        )


def test_metrics_config_rejects_value_that_looks_like_a_secret() -> None:
    """Defensa explicita: pegar un token real en el YAML versionado debe
    fallar la validacion, nunca aceptarse en silencio."""
    with pytest.raises(ValidationError):
        MetricsConfig(
            mode=ObservabilityMode.ENABLED,
            access_mode=MetricsAccessMode.INTERNAL_TOKEN,
            access_token_env_var="sk-live-abc123",
        )


def test_metrics_config_trusted_proxy_requires_header_and_value_env_var() -> None:
    with pytest.raises(ValidationError, match="TRUSTED_PROXY"):
        MetricsConfig(mode=ObservabilityMode.ENABLED, access_mode=MetricsAccessMode.TRUSTED_PROXY)


def test_metrics_config_trusted_proxy_valid_configuration() -> None:
    config = MetricsConfig(
        mode=ObservabilityMode.ENABLED,
        access_mode=MetricsAccessMode.TRUSTED_PROXY,
        trusted_proxy_marker_header="X-Metrics-Marker",
        trusted_proxy_marker_value_env_var="ALTAMIRA_METRICS_MARKER_VALUE",
    )
    assert config.access_mode == MetricsAccessMode.TRUSTED_PROXY


def test_metrics_config_enabled_requires_non_disabled_access_mode() -> None:
    with pytest.raises(ValidationError, match="access_mode distinto de DISABLED"):
        MetricsConfig(mode=ObservabilityMode.ENABLED, access_mode=MetricsAccessMode.DISABLED)


def test_metrics_config_disabled_mode_allows_disabled_access_mode() -> None:
    config = MetricsConfig()
    assert config.mode == ObservabilityMode.DISABLED
    assert config.access_mode == MetricsAccessMode.DISABLED


def test_component_status_enum_is_closed() -> None:
    assert set(ComponentStatus) == {
        ComponentStatus.READY,
        ComponentStatus.DEGRADED,
        ComponentStatus.NOT_READY,
        ComponentStatus.NOT_APPLICABLE,
        ComponentStatus.UNKNOWN,
    }
