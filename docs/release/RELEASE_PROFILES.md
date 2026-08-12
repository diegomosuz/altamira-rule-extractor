# Perfiles de release (Fase 15B4-B)

Este documento es la fuente de verdad de qué entrega cada perfil. Nunca usar
la palabra "opcional" respecto al LLM sin la calificación exacta de este
documento (confirmado empíricamente en Fase 15B4-A2: sin proveedor, un run
real **nunca** alcanza `COMPLETED`).

## Clasificación exacta: EXTRACTION_OPTIONAL

El LLM es opcional únicamente para la extracción determinística. **No** es
opcional para el pipeline completo. Confirmado por ejecución hermética real
(paquete con candidatos, sin proveedor configurado):

- `FINAL_STAGE = RULE_DRAFTS_GENERATED` (`FAILED`)
- `ERROR = "perfil LLM invalido: LLM_PROVIDER no esta definido"`
- `RULE_DRAFT_COUNT = 0`, `GUARDRAIL_COUNT = 0`
- Artefactos finales: hasta `07-context` inclusive, nunca `08-rule-drafts/09-guardrails/10-rules`.

No existe ningún fallback determinístico de `RuleDraft` (plantilla, bypass,
etc.) — confirmado por auditoría de código
(`pipeline/rule_drafts_generated_stage.py`). No se implementa uno en este
release.

## Perfil A — DETERMINISTIC_OFFLINE

**No requiere**: Internet, proveedor LLM, API key.

**Stage final esperado: `CONTEXTS_BUILT`.**

Incluye: ingestión, validación, extracción, parseo, canónico, efectos
semánticos, propagación, grafo semántico, candidatos, ContextPackages.

**No incluye**: RuleDraft, Guardrail final, exportación `10-rules/` — porque
`RuleDraft` requiere LLM por diseño actual, no por una limitación de este
release de empaquetado.

Este es el perfil correcto para un cliente air-gapped sin proveedor LLM
propio. Es un perfil **legítimo y completo en su propio alcance**, nunca una
versión "degradada" del perfil B — simplemente entrega un producto distinto
(extracción determinística de candidatos y contexto, sin redacción de reglas
en lenguaje natural).

## Perfil B — FULL_LLM

**Requiere**: proveedor LLM configurado y alcanzable (`LLM_PROVIDER` +
credenciales + red de salida hacia el proveedor).

**Stage final esperado: `COMPLETED`.**

Incluye además: RuleDraft, Guardrail, artefactos finales de reglas
(`10-rules/`).

Producción requiere un proveedor aprobado y alcanzable. Este perfil **nunca**
se ejecuta contra un proveedor real durante qualification/CI — únicamente
contra el arnés fake/stub hermético ya usado en todo el desarrollo de este
producto (`tests/hermetic_llm_support.py`).

## Matriz de funcionalidad air-gapped (sin LLM, sin Internet)

| Función | Disponible | Por qué |
|---|---|---|
| Ingesta de ZIP | Sí | Determinístico, sin red |
| Validación / extracción | Sí | Determinístico |
| Parsing (Java/ProLeap) | Sí | Subproceso local |
| Canónico | Sí | Determinístico |
| Efectos semánticos / propagación | Sí | Determinístico |
| Grafo semántico (Neo4j) | Sí | Solo Neo4j local, sin red externa |
| Extracción de candidatos | Sí | Q0/V2 determinísticos |
| ContextPackages | Sí | Determinístico |
| RuleDraft | **No** | Requiere `resolve_llm_profile` real |
| Guardrail | **No** | Nunca se alcanza sin RuleDraft previo |
| Exportación final (`10-rules/`) | **No** | Depende de RuleDraft + Guardrail |

## Security Profile — independiente del perfil de producto (Fase 15B4-B2)

**La autenticación/perímetro es idéntica en ambos perfiles de producto.**
`DETERMINISTIC_OFFLINE` significa exclusivamente "sin Internet / sin LLM" —
**nunca** "sin autenticación". Confundir ambos fue un defecto real de la
primera versión de este release (15B4-B), corregido aquí:

| | K3s release (`deploy/k3s/`) | Desarrollo local (`config/security.example.yaml`, `docker-compose.yml`) |
|---|---|---|
| `authentication_mode` | **`TRUSTED_PROXY_HEADERS`** | `DISABLED_DEV` |
| `session_cookie_secure` | `true` (proxy termina TLS) | `false` |

`DISABLED_DEV` (principal anónimo de solo lectura, sin identidad real) queda
reservado **exclusivamente** para desarrollo local — **nunca** aparece en
`deploy/k3s/secret.example.yaml`, sin importar el perfil de producto elegido
(A o B).

`config/security.yaml` completo (incluyendo el secreto real
`trusted_proxy_required_marker_value` en modo `TRUSTED_PROXY_HEADERS`) vive
en `deploy/k3s/secret.example.yaml`, nunca en `configmap.yaml` — el loader
carga el archivo como una unidad, nunca fusiona ConfigMap+Secret.

**Falla visible por diseño, sin tocar `/ready`**: los placeholders de
`NEO4J_PASSWORD` (7 caracteres) y `ALTAMIRA_SESSION_SECRET` (27 caracteres)
en `secret.example.yaml` son **deliberadamente** más cortos que sus mínimos
reales (8 y 32 respectivamente) — aplicar el template sin reemplazarlos
produce un fallo de arranque obvio (`CrashLoopBackOff`), nunca un secreto
"técnicamente válido" pero público quedando activo en silencio. El marcador
compartido del proxy (`trusted_proxy_required_marker_value`) se apoya en la
comparación de tiempo constante ya existente
(`security/identity_resolver.py`, `hmac.compare_digest`): un valor no
reemplazado nunca concede acceso anónimo, solo rechaza cada request como no
autenticado hasta que el operador configure el mismo valor en el proxy real.

## Enhanced mode

`enhanced_candidates_enabled = false` es el default de este release y de
todos los manifests de `deploy/k3s/`. Sigue siendo opt-in — habilita
detección adicional (Level-88, cálculos, transición de estado) con un costo
documentado de carga de revisión en patrones de escritura múltiple por
decisión (ver Fase 15B3-C8-FIX-2-A). No se cambia en este release.
