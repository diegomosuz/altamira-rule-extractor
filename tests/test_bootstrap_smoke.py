"""Smoke tests del bootstrap: el paquete importa, la config carga con
defaults y el logging JSON redacta secretos. No cubre logica de
pipeline (todavia no existe)."""

from __future__ import annotations

import json
import logging

from altamira_extractor import __version__
from altamira_extractor.config import Settings, load_settings
from altamira_extractor.logging_setup import configure_logging, redact_text


def test_package_has_version() -> None:
    assert __version__ == "0.1.0"


def test_settings_load_with_defaults(monkeypatch) -> None:
    monkeypatch.delenv("ALTAMIRA_ENVIRONMENT", raising=False)
    settings = load_settings()

    assert isinstance(settings, Settings)
    assert settings.environment == "local"
    assert settings.log_level == "INFO"
    assert str(settings.data_dir) == "data"


def test_settings_override_via_env(monkeypatch) -> None:
    monkeypatch.setenv("ALTAMIRA_LOG_LEVEL", "DEBUG")
    settings = load_settings()

    assert settings.log_level == "DEBUG"


def test_json_logging_emits_valid_json_and_redacts_secret(capsys) -> None:
    logger = configure_logging("INFO")

    logger.info("login attempt", extra={"api_key": "sk-super-secret", "user": "analyst"})

    captured = capsys.readouterr()
    line = captured.err.strip() or captured.out.strip()
    record = json.loads(line)

    assert record["message"] == "login attempt"
    assert record["level"] == "INFO"
    assert record["api_key"] == "***REDACTED***"
    assert record["user"] == "analyst"


def test_redact_text_masks_inline_secret() -> None:
    text = "calling gateway with token=abc123 for country=AR"

    redacted = redact_text(text)

    assert "abc123" not in redacted
    assert "***REDACTED***" in redacted
    assert "country=AR" in redacted


def test_logger_handler_uses_stream_handler() -> None:
    logger = configure_logging("WARNING")

    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0], logging.StreamHandler)
    assert logger.propagate is False
