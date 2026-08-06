# Observabilidad (Fase 15B2-B)

## Objetivo y límite duro

Logging estructurado, métricas, health/readiness extendida y
diagnóstico de componentes — **sin modificar el resultado funcional del
pipeline** (Principio A, obligatorio en todo este bloque): ningún
cambio de esta fase altera ingestión, consulta, activación, fallback,
rollback, auditoría ni validación funcional. Toda instrumentación es
aditiva: lee estado ya producido por choke points existentes
(`RunState` final de `run_ingestion`, el único exception handler de
`ApiError`, el desenlace de `execute_operational_action`) en vez de
insertarse dentro de la lógica de negocio.

## Configuración: `config/observability.yaml`

A diferencia de `config/security.yaml` (nunca versionado, su ausencia
condiciona el modo de arranque), `config/observability.yaml` **sí se
versiona**: no contiene secretos, solo nombres de variables de entorno.
Su ausencia o invalidez **nunca** activa un gate global de fail-closed
— `observability/config_loader.py::load_observability_config` siempre
devuelve un `ObservabilityConfig` utilizable (default seguro: logging
JSON activo, métricas deshabilitadas) junto con un `outcome`
(`LOADED`/`MISSING`/`INVALID`) que se reporta únicamente para
diagnóstico.

Contratos (`contracts/observability.py`): `ObservabilityMode`
(`DISABLED`/`ENABLED`), `MetricsAccessMode`
(`DISABLED`/`INTERNAL_TOKEN`/`TRUSTED_PROXY`), `LogFormat` (`JSON`
únicamente), `ComponentStatus`
(`READY`/`DEGRADED`/`NOT_READY`/`NOT_APPLICABLE`/`UNKNOWN`), y los
modelos `LoggingConfig`/`MetricsConfig`/`HealthConfig`/
`ReadinessConfig`/`ObservabilityConfig`.

## Logging estructurado

Ver `docs/STRUCTURED_LOGGING.md`.

## Métricas

Ver `docs/METRICS.md`.

### `GET /internal/metrics`

- Excluido del OpenAPI (`include_in_schema=False`) — nunca aparece en
  `/openapi.json`/`/docs`.
- `Cache-Control: no-store` siempre.
- 404 (nunca 401/403, para no revelar si el endpoint "existe") cuando
  `metrics.mode=DISABLED` o cuando la autorización falla.
- Dos modos de acceso, mutuamente excluyentes:
  - `INTERNAL_TOKEN`: header `X-Altamira-Metrics-Token` comparado en
    tiempo constante (`secrets.compare_digest`) contra el valor de la
    variable de entorno nombrada por `metrics.access_token_env_var`
    (nunca el nombre de la variable en sí es un secreto).
  - `TRUSTED_PROXY`: header nombrado por
    `metrics.trusted_proxy_marker_header`, comparado en tiempo
    constante contra el valor de la variable de entorno nombrada por
    `metrics.trusted_proxy_marker_value_env_var`.
- El token/marcador NUNCA se loguea ni se refleja en la respuesta, ni
  siquiera en caso de fallo de autorización.
- **NO** está en `_MISCONFIGURED_ALLOWED_PATHS` (corregido en el cierre
  correctivo de esta fase): cuando `config/security.yaml` está ausente
  o es inválido, `SecurityMisconfiguredGateMiddleware` bloquea también
  `/internal/metrics` — un token correcto **nunca** alcanza al handler
  en ese estado; la respuesta la produce el gate global (503,
  `security_misconfigured`), igual que cualquier otra ruta. La única
  allowlist del gate global sigue siendo `/health`, `/ready` y
  `/static/*`. Exponer métricas independientemente de la postura de
  seguridad conocida sería una ruta alternativa a información
  operacional — exactamente lo que el gate global existe para impedir.

## Health y Readiness

Ver `docs/HEALTH_AND_READINESS.md`.

## `GET /api/operations/component-diagnostics`

Requiere `OperationalPermission.VIEW_SECURITY_STATUS` (OPERATOR/ADMIN
— permiso definido desde Fase 15B1 pero nunca antes verificado en
ningún endpoint; mismo idioma de chequeo manual inline que el resto del
gobierno operativo, `contracts/security_identity.py::
AuthenticatedPrincipal.has_permission`). Sin el permiso: 403
(`ForbiddenError`, mismo comportamiento que cualquier otra acción
operativa).

11 componentes, cada uno con `component_id`/`status`
(`ComponentStatus`)/`reason_code` (cerrado, ver `api/schemas.py::
ComponentReasonCode`)/`checked_at_utc`/tres booleans
`required_for_ingestion`/`required_for_query`/
`required_for_operational_actions`:

| Componente | Verifica | Nunca expone |
|---|---|---|
| `security_configuration` | `config/security.yaml` cargado | contenido del YAML |
| `parser_jar` | JAR presente | path absoluto |
| `data_root` | `runs_dir` accesible | path absoluto |
| `executor` | `RunExecutor` inicializado | — |
| `neo4j_configuration` | URI con esquema soportado, driver construible | URI/usuario/password |
| `neo4j_connectivity` | `verify_connectivity()` con timeout corto (1s) y de solo lectura | URI/usuario/password/texto de excepción |
| `metrics` | `metrics.mode=ENABLED` | — |
| `logging` | handler JSON configurado | — |
| `functional_validation_configuration` | `ground_truth_path` presente | contenido del catálogo |
| `release_readiness_configuration` | `release_readiness_policy_path` presente | contenido de la política |
| `provider_configuration` | config de proveedor LLM completa (`resolve_llm_profile`, reutilizado del pipeline real) — **nunca prueba conectividad** | hostname/API key/modelo arbitrario |

`neo4j_connectivity` usa un timeout deliberadamente corto (1s, distinto
del timeout de producción de 30s) porque es un handler HTTP síncrono:
nunca debe bloquear una request de diagnóstico. `provider_configuration`
solo puede valer `DISABLED`/`CONFIGURED`/`MISCONFIGURED` (mapeados a
`NOT_APPLICABLE`/`READY`/`NOT_READY` en `status`, con el detalle real en
`reason_code`) — jamás intenta una llamada real al proveedor.

## Instrumentación del pipeline: aditiva, sin tocar `pipeline/runner.py`

`pipeline/` nunca depende de FastAPI (`.claude/rules/python.md`): las
métricas de pipeline se registran **fuera** de `pipeline/runner.py`,
inspeccionando el `RunState` final que ya devuelve `run_ingestion`
dentro de `RunExecutor._wrapped` (`api/executor.py`) — el mismo dato
que producen los choke points `_mark_succeeded`/`_mark_failed`, nunca
una copia paralela de ese estado. Cero cambios de orden de ejecución,
cero llamadas nuevas dentro de `runner.py`, cero metrics persistidas en
`artifacts/01-10`.
