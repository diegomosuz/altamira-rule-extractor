"""Logging JSON estructurado con redaccion de secretos (Fase 15B2-B:
endurecido con un conjunto cerrado de campos y un catalogo cerrado de
`event_name`).

Ninguna etapa del pipeline debe imprimir claves, tokens o passwords en
los logs (ver .claude/rules/security.md). Este modulo centraliza el
formateo JSON y la redaccion para que todos los componentes lo
reutilicen en lugar de loguear manualmente.

Dos capas de defensa, no una sola (Principio D de la Fase 15B2-B: "no
confiar unicamente en regex de redaccion"): (1) `_ALLOWED_EXTRA_FIELDS`
es una lista blanca CERRADA -- cualquier campo extra que no este en
ella se descarta antes de serializar, nunca se propaga automaticamente
como hacia la version previa de este modulo; (2) `redact_value`/
`redact_text` siguen aplicando sobre lo que SI queda, como defensa en
profundidad. `principal_id` es, deliberadamente, un campo que NUNCA
aparece en `_ALLOWED_EXTRA_FIELDS`."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

_REDACTED = "***REDACTED***"
_UNKNOWN_EVENT = "unknown_event"

_SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|api[_-]?key|secret|token|authorization)", re.IGNORECASE
)

_SENSITIVE_INLINE_PATTERN = re.compile(
    r"(?P<prefix>(?:password|api[_-]?key|secret|token|authorization)\s*[:=]\s*)"
    r"(?P<value>[^\s,;]+)",
    re.IGNORECASE,
)

_STANDARD_LOG_RECORD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)


class LogEventName(StrEnum):
    """Catalogo cerrado de `event_name` (Fase 15B2-B, Seccion 5): un
    evento estructurado nunca lleva un `event_name` arbitrario elegido
    por el llamador -- solo uno de estos valores. Cualquier otro string
    se normaliza a `unknown_event` en `JsonFormatter` (nunca se
    serializa un `event_name` fuera de catalogo)."""

    HTTP_REQUEST_COMPLETED = "http_request_completed"
    UNEXPECTED_ERROR = "unexpected_error"
    PIPELINE_RUN_STARTED = "pipeline_run_started"
    PIPELINE_RUN_COMPLETED = "pipeline_run_completed"
    PIPELINE_STAGE_TRANSITION = "pipeline_stage_transition"
    EXECUTOR_CAPACITY_REJECTED = "executor_capacity_rejected"
    FUNCTIONAL_VALIDATION_COMPLETED = "functional_validation_completed"
    OPERATIONAL_ACTION_COMPLETED = "operational_action_completed"
    SECURITY_DENIAL = "security_denial"
    AUDIT_EVENT_REGISTRATION_FAILED = "audit_event_registration_failed"
    BACKGROUND_RUN_FAILED = "background_run_failed"


# Conjunto cerrado de campos `extra` admitidos en el JSON emitido.
# Deliberadamente NO incluye: principal_id, raw path/query/body/headers/
# cookies, nombres de usuario, ni ningun identificador de credencial.
# `run_id`/`candidate_id` SI se admiten aqui (a diferencia de las
# etiquetas Prometheus, un log no es una serie temporal de alta
# cardinalidad) para preservar trazabilidad operativa.
_ALLOWED_EXTRA_FIELDS = frozenset(
    {
        "event_name",
        "correlation_id",
        "http_method",
        "http_route",
        "http_status_code",
        "duration_ms",
        "outcome",
        "error_code",
        "exception_type",
        "run_id",
        "stage",
        "component_id",
        "status",
        "reason_code",
        "action_type",
    }
)


def redact_value(key: str, value: Any) -> Any:
    """Reemplaza el valor por un marcador si la clave parece sensible."""
    if _SENSITIVE_KEY_PATTERN.search(key):
        return _REDACTED
    return value


def redact_text(text: str) -> str:
    """Redacta pares clave=valor sensibles embebidos en un mensaje libre."""
    return _SENSITIVE_INLINE_PATTERN.sub(lambda m: f"{m.group('prefix')}{_REDACTED}", text)


class JsonFormatter(logging.Formatter):
    """Formatea cada LogRecord como una linea JSON con secretos redactados
    y solo los campos `extra` de la lista blanca cerrada."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp_utc": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }

        extras = {
            key: redact_value(key, value)
            for key, value in record.__dict__.items()
            if key in _ALLOWED_EXTRA_FIELDS
        }
        if "event_name" in extras and extras["event_name"] not in set(LogEventName):
            extras["event_name"] = _UNKNOWN_EVENT
        payload.update(extras)

        if record.exc_info:
            payload["exc_info"] = redact_text(self.formatException(record.exc_info))

        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def log_event(
    logger: logging.Logger,
    level: int,
    event_name: LogEventName,
    **fields: Any,
) -> None:
    """Emite un evento estructurado con forma cerrada: `message` queda
    fijo al valor de `event_name` (nunca texto libre, Seccion 5:
    "catalogo cerrado de event_name en lugar de message"); cualquier
    campo en `fields` que no este en `_ALLOWED_EXTRA_FIELDS` se
    descarta en `JsonFormatter`, nunca aqui (un unico punto de
    aplicacion de la lista blanca)."""
    logger.log(level, event_name.value, extra={"event_name": event_name.value, **fields})


def configure_logging(level: str = "INFO") -> logging.Logger:
    """Configura el logger raiz de la aplicacion con salida JSON a stdout.

    Idempotente: puede invocarse multiples veces (API app factory + CLI
    entrypoint, potencialmente en el mismo proceso durante tests) sin
    duplicar handlers -- cada llamada limpia los handlers previos antes
    de agregar uno nuevo."""
    root = logging.getLogger("altamira_extractor")
    root.setLevel(level)
    root.handlers.clear()

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.propagate = False

    return root


__all__ = [
    "JsonFormatter",
    "LogEventName",
    "configure_logging",
    "log_event",
    "redact_text",
    "redact_value",
]
