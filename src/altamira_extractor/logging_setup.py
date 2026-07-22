"""Logging JSON con redaccion de secretos.

Ninguna etapa del pipeline debe imprimir claves, tokens o passwords en
los logs (ver .claude/rules/security.md). Este modulo centraliza el
formateo JSON y la redaccion para que todos los componentes lo
reutilicen en lugar de loguear manualmente.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

_REDACTED = "***REDACTED***"

_SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|api[_-]?key|secret|token|authorization)", re.IGNORECASE
)

_SENSITIVE_INLINE_PATTERN = re.compile(
    r"(?P<prefix>(?:password|api[_-]?key|secret|token|authorization)\s*[:=]\s*)"
    r"(?P<value>[^\s,;]+)",
    re.IGNORECASE,
)

_STANDARD_LOG_RECORD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)


def redact_value(key: str, value: Any) -> Any:
    """Reemplaza el valor por un marcador si la clave parece sensible."""
    if _SENSITIVE_KEY_PATTERN.search(key):
        return _REDACTED
    return value


def redact_text(text: str) -> str:
    """Redacta pares clave=valor sensibles embebidos en un mensaje libre."""
    return _SENSITIVE_INLINE_PATTERN.sub(lambda m: f"{m.group('prefix')}{_REDACTED}", text)


class JsonFormatter(logging.Formatter):
    """Formatea cada LogRecord como una linea JSON con secretos redactados."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }

        extras = {
            key: redact_value(key, value)
            for key, value in record.__dict__.items()
            if key not in _STANDARD_LOG_RECORD_ATTRS
        }
        payload.update(extras)

        if record.exc_info:
            payload["exc_info"] = redact_text(self.formatException(record.exc_info))

        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def configure_logging(level: str = "INFO") -> logging.Logger:
    """Configura el logger raiz de la aplicacion con salida JSON a stdout."""
    root = logging.getLogger("altamira_extractor")
    root.setLevel(level)
    root.handlers.clear()

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.propagate = False

    return root
