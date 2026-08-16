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
    assert __version__ == "1.18.0"


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


def test_json_logging_emits_valid_json_and_allows_known_field(capsys) -> None:
    logger = configure_logging("INFO")

    logger.info("login attempt", extra={"run_id": "run-1"})

    captured = capsys.readouterr()
    line = captured.err.strip() or captured.out.strip()
    record = json.loads(line)

    assert record["message"] == "login attempt"
    assert record["level"] == "INFO"
    assert record["run_id"] == "run-1"


def test_json_logging_redacts_inline_secret_in_message(capsys) -> None:
    """Defensa en profundidad (Seccion 5, Principio D): incluso un
    `message` de texto libre nunca deja pasar un patron `token=.../
    password=...` embebido -- la lista blanca de campos `extra` no es
    la unica capa."""
    logger = configure_logging("INFO")

    logger.info("calling gateway with token=abc123-should-be-redacted")

    captured = capsys.readouterr()
    line = captured.err.strip() or captured.out.strip()
    record = json.loads(line)

    assert "abc123-should-be-redacted" not in record["message"]
    assert "***REDACTED***" in record["message"]


def test_json_logging_drops_extra_fields_outside_the_closed_allowlist(capsys) -> None:
    """Fase 15B2-B (Seccion 5): un campo `extra` que no pertenece a
    `_ALLOWED_EXTRA_FIELDS` se descarta, nunca se serializa
    automaticamente -- a diferencia del comportamiento previo a este
    cierre, que propagaba cualquier campo no estandar de LogRecord."""
    logger = configure_logging("INFO")

    logger.info("evento", extra={"principal_id": "user-42", "user": "analyst"})

    captured = capsys.readouterr()
    line = captured.err.strip() or captured.out.strip()
    record = json.loads(line)

    assert "principal_id" not in record
    assert "user" not in record


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
