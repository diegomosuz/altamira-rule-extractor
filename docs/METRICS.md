# Métricas Prometheus (Fase 15B2-B)

## `ObservabilityRegistry`: una instancia por app, nunca un colector global

`observability/metrics.py::ObservabilityRegistry` crea su propio
`prometheus_client.CollectorRegistry()` en el constructor y vive en
`app.state.observability` (poblado en `api/app.py::create_app`'s
`lifespan`, igual patrón que `RunExecutor`/`Settings`). Nunca hay un
`Counter`/`Histogram`/`Gauge` de módulo registrado contra el
`CollectorRegistry` global de `prometheus_client`: la suite de tests
construye una app FastAPI nueva por cada función de test
(`tests/api/conftest.py`), y un colector de módulo lanzaría
`ValueError: Duplicated timeseries` en la segunda instancia de
`TestClient` de la misma sesión de pytest.

## Métricas expuestas

| Métrica | Tipo | Labels | Origen |
|---|---|---|---|
| `altamira_http_requests_total` | Counter | `http_method`, `http_route`, `http_status_code` | `CorrelationLoggingMiddleware` |
| `altamira_http_request_duration_seconds` | Histogram | `http_method`, `http_route` | `CorrelationLoggingMiddleware` |
| `altamira_pipeline_runs_total` | Counter | `final_stage` (`COMPLETED`/`FAILED`) | `RunExecutor._wrapped` (post-hoc sobre el `RunState` final) |
| `altamira_pipeline_stage_total` | Counter | `stage`, `status` | idem |
| `altamira_pipeline_stage_duration_seconds` | Histogram | `stage` | idem |
| `altamira_executor_active_runs` | Gauge | (sin labels) | `RunExecutor.try_submit`/`_wrapped` |
| `altamira_executor_capacity_rejections_total` | Counter | (sin labels) | `RunExecutor.try_submit` (resultado `at_capacity`) |
| `altamira_operational_actions_total` | Counter | `action_type`, `outcome` | `ui/governance_actions_router.py::governance_execute_ui` (canary/primary/fallback/rollback) |
| `altamira_security_denials_total` | Counter | `reason_code` | `api/app.py::_handle_api_error` (único choke point: TODO `ApiError` de la app pasa por ahí) |

## Cardinalidad: nunca un identificador libre como label

Ningún label anterior admite `run_id`, `candidate_id`, `generation_id`,
`program`, `paragraph`, `principal_id`, `correlation_id`, `filename`,
nombre de paquete, path crudo, query string, nombre de modelo LLM
arbitrario ni mensaje de error libre. `http_route` es la **plantilla**
registrada por FastAPI (p. ej. `/api/runs/{run_id}`), nunca el path
resuelto. Cualquier valor recibido que no pertenezca al conjunto
cerrado esperado por cada label se normaliza a `UNKNOWN` en
`ObservabilityRegistry` antes de usarse.

Ningún método de `ObservabilityRegistry` puede lanzar: cada uno envuelve
su cuerpo en `try/except Exception` y solo loguea una advertencia en
caso de fallo — instrumentar nunca puede romper el request/pipeline que
está midiendo.

## Alcance deliberadamente acotado: sin métrica de validación funcional

No existe `altamira_functional_validation_total` (eliminada en el
cierre correctivo de esta fase, Sección 3, tras auditoría: la métrica
original no tenía ningún productor observable dentro del proceso API).
La validación funcional (`functional-validate`/`functional-validate-
dataset`) es exclusivamente un comando CLI de un solo disparo (Fase
15B2-A); un proceso CLI que termina inmediatamente no tiene ningún
`/internal/metrics` que lo sirva, y conectar un `ObservabilityRegistry`
efímero ahí no tendría consumidor real — sería una métrica nominal,
siempre en cero, que nunca refleja actividad real.

Esta capacidad queda **NOT_APPLICABLE** hasta que exista un punto de
ejecución observable dentro del proceso API (p. ej. un futuro endpoint
HTTP de validación funcional) cuyo `ObservabilityRegistry` sea el mismo
que expone `/internal/metrics` — en ese momento se agregaría la métrica
con un productor real, nunca antes.

## `GET /internal/metrics`

Ver `docs/OBSERVABILITY.md` para el endpoint (modo de acceso, control de
caché, exclusión de OpenAPI). Este documento cubre únicamente qué se
mide y por qué; el "cómo se protege" vive en la config de
observabilidad.
