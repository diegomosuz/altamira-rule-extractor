# Materialización controlada, routing atómico, fallback y rollback (Fase 14B)

Rama: `feat/controlled-unified-materialization`. Baseline: v1.13.0.

## Propósito

Fase 14A produjo una **evaluación** de si un paquete calificaría para un
canary o un primary trial unified — un artefacto puramente declarativo,
de solo lectura, que nunca activa nada. Fase 14B cierra la brecha entre
"calificaría" y "está activo": construye una capa de **activación
ejecutable y reversible** que, a partir de una autorización explícita y
tipada, materializa resultados unified de forma content-addressed,
activa un lane mediante un puntero atómico, ejecuta fallback real ante
corrupción, y permite rollback explícito — todo sin sobrescribir ni
eliminar destructivamente V1 ni ningún artefacto `artifacts/01-10`
preexistente.

## Relación con Fase 14A

Fase 14B **consume** una evaluación de Fase 14A
(`diagnostics/unified-activation-evaluation.json`) como precondición —
nunca la recalcula, nunca la modifica. El servicio de materialización
(`pipeline/unified_materialization_service.py`) valida que la
autorización corresponda exactamente a esa evaluación
(`run_id`, `activation_evaluation_hash`, `expected_readiness_
disposition`) antes de tocar `activation/`. Si la evaluación cambió
desde que se redactó la autorización, o si la disposición esperada no
coincide, el servicio aborta **antes** de cualquier escritura —
fail-closed. Fase 14A sigue gobernando **qué calificaría**; Fase 14B
gobierna **qué está realmente activo**.

## Autorización declarativa y su limitación de autenticidad

`contracts/unified_materialization_authorization.py::
UnifiedMaterializationAuthorization` es un YAML **externo** (mismo
principio que `--config` en Fase 14A: nunca se copia al repositorio ni
al run, solo su `authorization_hash` se registra). Seis acciones
(`UnifiedMaterializationAction`): `KEEP_V1`, `ACTIVATE_UNIFIED_CANARY`,
`ACTIVATE_UNIFIED_PRIMARY`, `FALLBACK_TO_V1`, `ROLLBACK_TO_PREVIOUS`,
`ROLLBACK_TO_GENERATION`. Cada acción impone reglas de validación
propias (p. ej. `ACTIVATE_UNIFIED_CANARY` exige `expected_readiness_
disposition=READY_FOR_UNIFIED_CANARY`; `KEEP_V1` nunca declara
`approved_group_ids`; `ROLLBACK_TO_GENERATION` exige
`target_generation_id`).

**Limitación explícita, documentada para Fase 15**: las autorizaciones
son **auditables pero no autenticadas**. El sistema registra quién
declaró qué (`review_reference`, `reason_code`, hashes) y produce una
cadena de eventos verificable, pero no valida una firma criptográfica
ni una identidad de operador. Cualquier proceso con acceso de
filesystem al run puede redactar una autorización sintácticamente
válida. La autenticación de autorizaciones queda fuera de alcance de
Fase 14B.

## Generación V1 y generación unified

Una **generación** (`MaterializedGenerationManifest`) es una instantánea
inmutable y content-addressed de un lane:

- **V1** (`pipeline/v1_activation_generation_builder.py`): lee
  `artifacts/06-candidates.json` (único archivo obligatorio) y,
  opcionalmente, los manifiestos de `07-context/`, `08-rule-drafts/` y
  `09-guardrails/` — nunca copia el contenido, solo referencia sus
  hashes. Nunca escribe fuera de `activation/`.
- **unified** (`pipeline/unified_activation_generation_builder.py`):
  consume una `UnifiedActivationEvaluationArtifact` (Fase 14A) y un
  `UnifiedShadowDownstreamArtifact` (Fase 13) ya en memoria — nunca
  toca el filesystem. Para cada `group_id` en
  `authorization.approved_group_ids` exige **simultáneamente**: nivel
  `RULE` en la evaluación, `guardrail_status=PASSED` en esa referencia,
  ejecución `EXECUTED` en el downstream, y el registro real de
  guardrail con `status=PASSED`. Cualquier grupo que no cumpla las
  cuatro condiciones aborta la construcción completa — nunca se
  materializa parcialmente.

## Content addressing

`compute_generation_id(*, lane, kind, run_id, file_hashes)` produce
`generation-{sha256(...).hexdigest()}` a partir de
`[lane, kind, run_id, *"name=digest" ordenados]`. **Deliberadamente
no incluye** `authorization_hash` ni `activation_evaluation_hash`:
esos son metadatos de procedencia, no identidad de contenido. Dos
autorizaciones distintas que producen exactamente el mismo contenido
generan el **mismo** `generation_id` — el manifiesto persistido
primero gana; una "redescubierta" posterior reutiliza sus valores de
procedencia sin sobrescribirlos (`UnifiedActivationStore::
_reconcile_existing_generation`). `compute_event_id` sí incluye
`authorization_hash`, porque dos autorizaciones distintas son, por
definición, dos eventos distintos en la cadena auditable.

Los identificadores usan un separador de guión simple
(`generation-{hex}`, `event-{hex}`), no `::` — el host de desarrollo
monta `/workspace` como bind mount de una ruta Windows, y `:` es
inválido en nombres de archivo/directorio de NTFS.

**Campos exactos de `compute_generation_id`:**

- **Incluidos** (en este orden, unidos con el separador `\x1f`):
  `lane.value`, `kind.value`, `run_id`, y luego cada par
  `"{logical_name}={sha256}"` de `file_hashes`, ordenado por
  `sorted(file_hashes.items())` (orden alfabético de `logical_name` —
  el orden de inserción del `dict` de entrada nunca importa).
- **Excluidos**: `authorization_hash`, `activation_evaluation_hash`,
  `source_package_hash`, `byte_size` de cada archivo, `relative_path`
  de cada archivo, cualquier timestamp, cualquier campo de
  `fallback_generation_id`/`event_id`. Ninguno de estos participa del
  hash de identidad — son metadatos de procedencia o de validación,
  nunca identidad de contenido.
- **Representación canónica**: cadena única `\x1f`-delimitada (`\x1f`,
  el carácter de control "Unit Separator", elegido precisamente porque
  no puede aparecer en un `logical_name` ni en un digest hexadecimal,
  evitando cualquier ambigüedad de concatenación).
- **Algoritmo**: SHA-256 sobre esa cadena codificada en UTF-8,
  `hexdigest()`, prefijado con el literal `"generation-"`.

**Semántica: *run-scoped content-addressed generation*.** `run_id`
participa deliberadamente de la identidad — `generation_id` direcciona
contenido **dentro de un run dado**, nunca un identificador global
comparable entre runs distintos. El mismo contenido exacto materializado
bajo dos `run_id` diferentes produce, por diseño, dos `generation_id`
diferentes (confirmado por
`tests/contracts/test_unified_activation_materialization.py::
TestGenerationIdDeterminism::test_different_run_id_different_id`). Esto
es intencional, no una limitación: cada run tiene su propio espacio de
generaciones, aislado de cualquier otro run, aunque ambos procesen
paquetes COBOL idénticos.

## Punto de commit atómico

Secuencia de `UnifiedActivationStore.persist_generation`: escribir a un
directorio temporal (`generations/.tmp-{pid}-{token}/`) → escribir cada
archivo de datos y releer+validar hash/tamaño inmediatamente → escribir
`manifest.json` y releer+validarlo → `atomic_promote_directory` (mismo
primitivo reutilizado de `pipeline/artifact_store.py`) hacia
`generations/<generation_id>/`. El destino solo se verifica ausente
justo antes de promover — el lock de escritor único hace esta condición
de carrera inalcanzable en operación normal, pero se verifica de todas
formas.

El **único** punto de commit real del sistema es la escritura atómica
de `active.json` — todo lo anterior (generación completa pero no
referenciada) puede existir en disco sin que ningún lane la considere
activa. Un fallo **antes** de ese punto deja el puntero anterior
intacto; un fallo **en** ese punto (durante el `os.replace` atómico)
también deja el puntero anterior intacto, nunca uno parcial.

## `active.json`, cadena de eventos y lanes

`activation/active.json` (`ActiveActivationPointer`): `active_lane`,
`active_generation_id`, `previous_generation_id`, `fallback_
generation_id` (siempre apunta a una generación V1 válida),
`pointer_version` (incrementa exactamente en 1 por transición),
`latest_event_id`. Cada transición produce un
`ActivationTransitionEvent` inmutable en `activation/events/<event_id>.
json`, enlazado a su predecesor vía `previous_event_id` — la cadena
completa es reconstruible remontando desde `latest_event_id` hasta el
evento raíz (`sequence=1`, `previous_event_id=None`).

Tres preguntas distintas, igual que en Fase 14A:

- **`requested_lane`** (implícito en la acción de la autorización):
  qué lane pide el operador.
- **`active_lane`**: qué lane está realmente activo — el único que
  importa para routing real.
- **`fallback_lane`**: siempre V1 (`fallback_generation_id`), listo
  para un fallback real si el lane activo se corrompe.

## Router (solo lectura, opt-in)

`pipeline/unified_active_lane_router.py::resolve_active_artifact` y
`pipeline/active_artifact_resolver.py::ActiveArtifactResolver` son el
**único** punto de entrada para que un consumidor lea el artefacto
"activo" de un lane, por `logical_name` (`candidates`,
`context-packages`, `rule-drafts`, `guardrails`). Es explícitamente
**opt-in**: ningún consumidor productivo existente (API, CLI de
lectura, descarga de reglas) fue modificado para usarlo — Fase 14B
nunca afirma que `unified` es globalmente productivo. El router nunca
hace fuzzy fallback ni oculta corrupción: cualquier archivo ausente,
manifiesto corrupto o hash no coincidente se tipa explícitamente
(`ActivationResolutionStatus`), nunca se silencia ni se adivina.

`ActivationResolutionStatus` tiene **cuatro** valores, no tres: además
de `RESOLVED`/`FALLBACK_APPLIED`/`BLOCKED` del enunciado original, se
agregó `NOT_AVAILABLE_IN_LANE` — decisión de diseño explícita para
distinguir una ausencia **legítima** (el lane activo simplemente no
tiene ese artefacto, p. ej. V1 sin `rule-drafts` porque esa etapa nunca
se ejecutó) de una corrupción real (`BLOCKED`). Documentado en el
docstring del enum y del modelo.

## Fallback real

`pipeline/unified_active_lane_service.py::resolve_with_fallback` — el
único camino que ejecuta un fallback real (no solo lo reporta). Nunca
aplica fallback por error de invocación del consumidor (`logical_name`
desconocido) ni por configuración mal formada: **solo** por integridad
o disponibilidad real del contenido referenciado (hash no coincidente,
archivo ausente, manifiesto corrupto). El fallback siempre apunta a
`fallback_generation_id` (V1), nunca a una generación unified anterior.
La generación corrupta **nunca se borra** — permanece en disco como
evidencia, orphan pero preservada.

## Rollback explícito

Dos funciones (`pipeline/unified_activation_transition.py`):
`rollback_to_previous` (vuelve a `previous_generation_id` del puntero
actual) y `rollback_to_generation` (vuelve a un `target_generation_id`
explícito, con validación de path-safety sobre el ID suministrado por
el operador antes de usarlo para construir una ruta de filesystem). Solo
se puede hacer rollback a una generación **completa** — nunca a una
generación parcial o inexistente; el store la relee y revalida antes de
aceptar la transición.

## Idempotencia

Toda transición hacia el **mismo** `generation_id` que ya está activo
retorna el puntero/evento actual sin efectos nuevos
(`TransitionResult.idempotent=True`) — verificado explícitamente **antes**
de cualquier guarda estructural por acción (p. ej. "`INITIALIZE_V1`
exige ausencia de puntero previo"), para que reintentar la misma
autorización sobre un run ya inicializado nunca falle. `unified-
activation-materialize` expone este resultado como
`IDEMPOTENT_NO_CHANGE`.

## Lock transitorio y recuperación manual

`activation/.activation.lock` (contenido: PID únicamente),
`os.open(..., O_CREAT | O_EXCL | O_WRONLY)` — falla inmediatamente ante
contención (`UnifiedActivationLockError`), se libera en un bloque
`finally`. **No existe recuperación automática de lock stale** en esta
fase — decisión deliberada. Procedimiento manual: confirmar que ningún
proceso vivo tiene ese PID, luego eliminar
`activation/.activation.lock` a mano antes de reintentar. Un lock stale
nunca corrompe el estado: ninguna escritura parcial es referenciable
mientras el lock está sostenido.

## Generaciones y eventos orphan

Una generación completa pero nunca referenciada por ningún puntero
(p. ej. tras un fallo entre `persist_generation` y la escritura de
`active.json`, o una generación corrupta tras un fallback) es un
**orphan seguro**: ocupa espacio en disco pero no es alcanzable ni
activable por ningún consumidor. Fase 14B **nunca** elimina
generaciones ni eventos — ninguna lógica de garbage collection existe
en esta fase, por diseño explícito del enunciado original.

## Corrupción

Toda corrupción detectable (hash no coincidente, archivo ausente,
manifiesto ilegible) se tipa explícitamente
(`BLOCKED`/`FALLBACK_APPLIED` según el punto de detección) — nunca una
excepción no capturada, nunca un resultado silenciosamente vacío. El
router y el servicio de fallback distinguen "el manifiesto mismo es
ilegible" (no se sabe qué archivo se esperaba) de "el manifiesto es
legible pero el archivo referenciado no coincide" — ambos casos quedan
tipados, con la información disponible en cada uno.

## Proveedor fake-only y ausencia de publicación externa

Fase 14B **nunca** inicializa un proveedor LLM real, **nunca** hace una
llamada de red, **nunca** publica ni promueve una regla externamente.
Verificado no solo por ausencia funcional sino **estáticamente**
(análisis AST de los ocho módulos del control plane de materialización,
confirmando que ninguno importa `socket`, `requests`, `httpx` como
cliente real, ni un SDK de proveedor) y **dinámicamente** (una prueba
ejecutable bloquea `socket.socket.connect`/`socket.create_connection`/
lectura de variables de proveedor/lectura de `.env` y ejecuta un ciclo
de materialización completo bajo ese bloqueo). `provider_policy` en la
autorización solo acepta `DETERMINISTIC_FAKE_ONLY` en esta fase.

## CLI

```
python -m altamira_extractor.cli unified-activation-materialize <run_id> --authorization <path> [--json]
python -m altamira_extractor.cli unified-activation-status <run_id> [--json]
python -m altamira_extractor.cli unified-activation-resolve <run_id> <logical_name> [--json]
python -m altamira_extractor.cli unified-activation-rollback <run_id> --authorization <path> [--json]
```

`unified-activation-rollback` pre-valida (`peek_authorization_action` +
`action_is_rollback`) que la autorización declare una acción de
rollback **antes** de ejecutar cualquier transición — rechaza cualquier
otra acción sin tocar `activation/`. Ningún comando acepta proveedor,
API key, endpoint, modelo, ni una decisión humana ad-hoc por línea de
comandos — todo proviene exclusivamente del YAML de autorización
validado. `unified-activation-status`/`unified-activation-resolve`
usan `ActiveArtifactResolver` (Parte 14), nunca acceden al store
directamente.

## Integración con consumidores

`ActiveArtifactResolver` es una integración **opt-in, aditiva y
explícita** — ningún lector productivo existente (`api/reads.py`,
`api/downloads.py`, la UI) fue modificado en esta fase. `RuleDrafts`
(`08-rule-drafts/`) no tiene hoy ningún consumidor productivo directo
(solo la siguiente etapa del pipeline lo usa internamente); la
generación V1 sigue referenciando su manifiesto porque existe en disco,
aunque ningún lector actual lo consuma. Ninguna parte de Fase 14B
afirma, implícita o explícitamente, que `unified` es un lane
globalmente productivo.

## Limitaciones

- Las autorizaciones son auditables pero **no autenticadas** — sin
  firma criptográfica, sin identidad de operador verificada (ver
  arriba).
- No existe recuperación automática de lock stale — procedimiento
  manual únicamente.
- No existe eliminación de generaciones/eventos orphan en esta fase.
- La integración con consumidores productivos (API, UI) es opt-in y
  nunca se activó por defecto — permanece fuera de alcance.
- No hay autenticación, API HTTP ni UI para materialización — solo CLI
  y servicios de aplicación.

## Próxima fase (Fase 15) — parcialmente entregado en Fase 15B1

Al cierre de esta fase (14B), "identidad de operador verificada" e
"integración productiva con consumidores reales (API/UI)" quedaban
reservadas a una fase futura. **Fase 15B1**
(`docs/SECURITY_AUTHORIZATION_AND_AUDIT.md`) entrega ambas, sin modificar
ningún componente de esta fase: identidad delegada (`TRUSTED_PROXY_
HEADERS`) + RBAC + workflow prepare/confirm/execute que invoca, sin
cambios, `pipeline/unified_materialization_service.py::materialize_
unified_activation`; y auditoría append-only de QUIÉN autorizó/ejecutó
cada transición (`OperationalAuditEvent`, arbol `audit/` separado de
`activation/`), complementaria a la cadena de `ActivationTransitionEvent`
de esta fase. La firma criptográfica de la autorización en sí (más allá
de la identidad delegada del operador) y la política de retención/
eliminación de generaciones/eventos orphan siguen sin resolverse.

## Ver también

- `docs/CONTROLLED_UNIFIED_ACTIVATION.md` (Fase 14A): evaluación
  declarativa de disponibilidad que esta fase consume como
  precondición de toda materialización.
- `docs/UNIFIED_SHADOW_DOWNSTREAM_PIPELINE.md` (Fase 13): ejecución
  downstream real cuyo resultado (`ContextPackage`/`RuleDraft`/
  `GuardrailReport`) esta fase materializa bajo `activation/` cuando
  una autorización lo aprueba.
- `docs/OPERATIONAL_GOVERNANCE_UI.md` (Fase 15A): capa read-only (API +
  UI) que audita y expone el puntero activo, las generaciones, la
  cadena de eventos y los artifacts resolubles que esta fase produce
  — sin ejecutar jamás una activación, un fallback ni un rollback.
- `docs/SECURITY_AUTHORIZATION_AND_AUDIT.md` (Fase 15B1): identidad,
  RBAC, CSRF y workflow controlado que invoca `materialize_unified_
  activation` de esta fase desde la UI, más la auditoría append-only de
  operador/revisor/resultado.
