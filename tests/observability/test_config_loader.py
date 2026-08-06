"""Tests de `observability/config_loader.py` (Fase 15B2-B): mismo
idioma LOADED/MISSING/INVALID que `security_config_loader.py`, pero sin
gate global de fail-closed -- `config` siempre es utilizable."""

from __future__ import annotations

from pathlib import Path

import pytest

from altamira_extractor.contracts.observability import ObservabilityMode
from altamira_extractor.observability.config_loader import (
    ObservabilityConfigOutcome,
    load_observability_config,
)


def test_missing_file_returns_missing_with_safe_default(tmp_path: Path) -> None:
    result = load_observability_config(tmp_path / "does-not-exist.yaml")
    assert result.outcome == ObservabilityConfigOutcome.MISSING
    assert result.error is None
    assert result.config.metrics.mode == ObservabilityMode.DISABLED


def test_invalid_yaml_returns_invalid_with_safe_default(tmp_path: Path) -> None:
    path = tmp_path / "observability.yaml"
    path.write_text("not: [valid, yaml", encoding="utf-8")
    result = load_observability_config(path)
    assert result.outcome == ObservabilityConfigOutcome.INVALID
    assert result.error is not None
    assert result.config.metrics.mode == ObservabilityMode.DISABLED


def test_valid_schema_violation_returns_invalid_with_safe_default(tmp_path: Path) -> None:
    path = tmp_path / "observability.yaml"
    path.write_text("schema_version: '1.0'\nlogging:\n  level: NOT_A_LEVEL\n", encoding="utf-8")
    result = load_observability_config(path)
    assert result.outcome == ObservabilityConfigOutcome.INVALID
    assert result.config.logging.level == "INFO"


def test_valid_file_loads_successfully(tmp_path: Path) -> None:
    path = tmp_path / "observability.yaml"
    path.write_text(
        "\n".join(
            [
                "schema_version: '1.0'",
                "logging:",
                "  format: JSON",
                "  level: DEBUG",
                "metrics:",
                "  mode: DISABLED",
                "  access_mode: DISABLED",
            ]
        ),
        encoding="utf-8",
    )
    result = load_observability_config(path)
    assert result.outcome == ObservabilityConfigOutcome.LOADED
    assert result.error is None
    assert result.config.logging.level == "DEBUG"


def test_symlink_is_treated_as_missing(tmp_path: Path) -> None:
    real_file = tmp_path / "real-observability.yaml"
    real_file.write_text("schema_version: '1.0'\n", encoding="utf-8")
    symlink_path = tmp_path / "observability-symlink.yaml"
    try:
        symlink_path.symlink_to(real_file)
    except OSError:
        pytest.skip("symlinks no soportados en este entorno (requiere privilegio en Windows)")
    result = load_observability_config(symlink_path)
    assert result.outcome == ObservabilityConfigOutcome.MISSING


def test_real_config_file_at_repo_root_loads_successfully() -> None:
    """El `config/observability.yaml` versionado y realmente usado por
    `api/app.py::create_app` debe ser valido contra su propio contrato."""
    repo_root = Path(__file__).resolve().parents[2]
    result = load_observability_config(repo_root / "config" / "observability.yaml")
    assert result.outcome == ObservabilityConfigOutcome.LOADED
