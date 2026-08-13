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

Desde Fase 15B4-CANDIDATE-QUALITY-5E, `enhanced_candidates_enabled = true`
es el **default del código** (`src/altamira_extractor/config.py`), tras
cerrar el corpus Ground Truth formal con FP=0/FN=0/precision=recall=
f1=1.0 (ver `docs/CAPABILITY_COVERAGE_1_17.md`). Habilita cuatro familias
productivas adicionales sobre `RETURN_CODE` V1/Q0 (siempre activo, fuera
del flag): `RETURN_CODE_PROPAGATION`, `LEVEL_88_RETURN_CODE`,
`STATE_TRANSITION` (gate adicional: target con
`semantic_tag ∈ {status, status_flag}`) y `CALCULATION`. El modo
`enhanced_candidates_enabled=false` (legacy/conservador, comportamiento
V1/Q0-only) sigue disponible como override explícito.

**Alineado desde Fase 15B4-CANDIDATE-QUALITY-5G**: los manifests de
`deploy/k3s/` (`configmap.yaml`) fijan
`ALTAMIRA_ENHANCED_CANDIDATES_ENABLED: "true"`, igual que el default del
código — el despliegue K3s estándar de release ya no desactiva las
capacidades productivas certificadas de 1.17. La variable se mantiene
explícita en el ConfigMap (nunca se omite) para que el manifiesto siga
siendo reproducible. Un operador puede seguir fijando
`ALTAMIRA_ENHANCED_CANDIDATES_ENABLED=false` explícitamente (edición del
ConfigMap o override operacional) para el modo legacy/conservador —
override siempre soportado, nunca eliminado. (Historial: entre Fases
15B4-B Sección 0.C y 5F, el ConfigMap fijaba `"false"` como decisión de
producto anterior a 5E; esa discrepancia quedó documentada en 5F y se
resolvió en 5G.)
