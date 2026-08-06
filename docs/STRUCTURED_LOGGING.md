# Logging estructurado (Fase 15B2-B)

## Formato

`logging_setup.py::configure_logging(level)` configura el logger raíz
de la aplicación (`altamira_extractor`) con un único `StreamHandler` a
stdout/stderr y `JsonFormatter`. Es **idempotente**: cada llamada limpia
los handlers previos antes de agregar uno nuevo — invocarla varias veces
en el mismo proceso (API app factory + CLI, o entre tests) nunca
duplica líneas de log.

Se invoca desde dos puntos de entrada, nunca más:

- `api/app.py::create_app` (sincrónicamente, antes de construir la
  instancia de `FastAPI`).
- `cli.py` (`@app.callback() def _bootstrap_logging()`, se ejecuta antes
  de cualquier subcomando Typer).

## Forma cerrada de cada línea

Cada línea JSON contiene siempre `timestamp_utc`, `level`, `logger`,
`message`, y opcionalmente `exc_info` (solo si el `LogRecord` trae
`exc_info` adjunto — ningún llamador de este bloque lo hace).

Los campos `extra` están sujetos a una **lista blanca cerrada**
(`_ALLOWED_EXTRA_FIELDS` en `logging_setup.py`): `event_name`,
`correlation_id`, `http_method`, `http_route`, `http_status_code`,
`duration_ms`, `outcome`, `error_code`, `exception_type`, `run_id`,
`stage`, `component_id`, `status`, `reason_code`, `action_type`.
Cualquier otro campo pasado vía `extra={...}` se **descarta antes de
serializar** — a diferencia del comportamiento previo a este cierre,
que propagaba automáticamente cualquier atributo no estándar de
`LogRecord`. `principal_id` deliberadamente NUNCA pertenece a esta
lista.

## Catálogo cerrado de `event_name`

`LogEventName` (StrEnum) enumera los eventos estructurados soportados:
`http_request_completed`, `unexpected_error`,
`pipeline_run_started`/`pipeline_run_completed`,
`pipeline_stage_transition`, `executor_capacity_rejected`,
`functional_validation_completed`, `operational_action_completed`,
`security_denial`, `audit_event_registration_failed`,
`background_run_failed`. Un `event_name` fuera de catálogo se normaliza
a `unknown_event` en `JsonFormatter` — nunca se serializa un valor
arbitrario elegido por el llamador.

El helper `log_event(logger, level, event_name, **fields)` es el punto
de entrada recomendado para emitir un evento estructurado: fija
`message` al propio `event_name` (nunca texto libre) y pasa `fields`
como `extra` (sujetos igualmente a la lista blanca).

## Dos capas de defensa (nunca solo regex)

1. **Lista blanca de campos** (primaria): un campo no admitido nunca
   llega a serializarse, sin importar su contenido.
2. `redact_value`/`redact_text` (defensa en profundidad): sobre lo que
   SI se serializa, cualquier valor cuya clave matchee un patrón
   sensible (`password`, `api_key`, `secret`, `token`, `authorization`)
   se reemplaza por `***REDACTED***`; `redact_text` aplica el mismo
   reemplazo a pares `clave=valor` embebidos en el `message` libre.

## Eliminación de `logger.exception(...)` en rutas de producción

Ningún camino de producción llama `logger.exception(...)` ni adjunta
`exc_info=True` (auditado en `api/app.py`, `api/executor.py`,
`ui/governance_actions_router.py`): un traceback nunca se sirializa.
El catch-all de la API (`api/app.py::_handle_unexpected_error`) emite
`event_name=unexpected_error` con `error_code=internal_error`,
`exception_type=type(exc).__name__`, `correlation_id`, `http_route`
(plantilla, nunca path crudo) y `http_status_code=500` — nunca
`str(exc)` ni el traceback. `ApiError`/`ExecutorAtCapacityError` ya
llevaban `code`/`message` cerrados y sanitizados desde Prompt 13b
(auditado sin cambios en esta fase).
