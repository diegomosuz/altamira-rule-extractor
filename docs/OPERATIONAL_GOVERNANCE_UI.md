# Gobierno operativo read-only, auditoría de activación y UI del lane activo (Fase 15A)

Rama: `feat/operational-governance-ui`. Baseline: v1.14.0.

## Propósito

Fases 9-14B construyeron un pipeline productivo V1, un catálogo unified
en shadow mode, un control plane de evaluación de activación (Fase
14A) y una capa de materialización ejecutable content-addressed con
routing atómico, fallback y rollback (Fase 14B). Ninguna de esas fases
ofrece una forma humana de **ver** ese estado: qué lane está activo
ahora mismo, si la generación activa es íntegra, si la cadena de
eventos es auditable, si hay generaciones o eventos huérfanos, qué
artefactos son resolubles. Fase 15A cierra ese hueco con una capa de
**gobierno operativo exclusivamente de lectura**: un read model en
memoria (`OperationalGovernanceOverview`), una API JSON y una UI HTML
que exponen ese estado sin ejecutar jamás una activación, un fallback
o un rollback.

## Arquitectura read-only

Todo el código de Fase 15A cumple, estructuralmente, la misma
disciplina: `pipeline/operational_governance_reader.py` y
`pipeline/operational_governance_group_adapter.py` no contienen
**ninguna** llamada a `mkdir`, escritura atómica, `unlink`, `rename`,
`replace`, adquisición de lock, transición (Fase 14B) ni fallback
ejecutable — verificado tanto por lectura directa del código como por
una prueba ejecutable que bloquea esas operaciones y corre el reader,
el adapter, la API, la página HTML, un fragment HTMX y las descargas
GET/HEAD bajo esos bloqueos (`tests/test_operational_governance_
isolation.py`, Parte 13). Cada handler HTTP nuevo (API y UI) es `GET`
o `HEAD`, nunca `POST`/`PUT`/`PATCH`/`DELETE` — verificado también de
forma ejecutable (`tests/test_api_governance.py::test_no_write_
methods_registered`).

## Relación con Fase 14A / 14B

Fase 15A **consume**, nunca modifica: `diagnostics/unified-activation-
evaluation.json` (Fase 14A, sección "readiness" del overview) y
`activation/active.json` / `activation/generations/*` / `activation/
events/*` (Fase 14B). Nunca recalcula la evaluación de Fase 14A, nunca
construye una generación, nunca escribe un evento, nunca mueve el
puntero activo. Reutiliza, sin modificar, `ActiveArtifactResolver`
(únicamente por su `.store`, nunca por `.resolve()`/`.resolve_path()`
— ver más abajo), `unified_active_lane_router.resolve_active_artifact`
y los contratos de materialización de Fase 14B.

## `status` (`OperationalGovernanceStatus`)

Cinco valores, calculados por el reader a partir del estado real —
nunca declarados por el operador:

- **`NOT_INITIALIZED`**: `activation/active.json` nunca se escribió
  para este run.
- **`HEALTHY_V1`** / **`HEALTHY_UNIFIED`**: el puntero activo es
  legible, el manifiesto de la generación activa es íntegro, la cadena
  de eventos confirmada es `VALID`, y no hay huérfanos ni issues de
  severidad `ERROR`/`BLOCKING`.
- **`DEGRADED`**: la generación activa resuelve correctamente, pero
  existe algún problema real (huérfanos, cadena de eventos no `VALID`,
  un issue `ERROR`) — el lane activo sigue siendo utilizable.
- **`BLOCKED`**: el puntero o el manifiesto de la generación activa no
  son íntegros (corruptos, ausentes, hash no reconciliado) — el
  overview sigue siendo visible (nunca una excepción), pero el lane
  activo no se puede confirmar como resoluble.

`activation/active.json` **existente pero ilegible** cuenta como
`activation_initialized=True` (su sola presencia demuestra que una
transición real ya ocurrió) con `status=BLOCKED` — `activation_
initialized=False` se reserva exclusivamente para la ausencia
legítima (el archivo nunca se escribió).

## Lane activo, generaciones y reachability

`active_lane`/`active_generation_id`/`active_generation_kind`/
`pointer_version`/`previous_generation_id`/`fallback_generation_id`
son una proyección directa de `activation/active.json` — nunca
reinterpretados. Cada generación persistida en disco (activa o no) se
clasifica por `GovernanceGenerationReachability`:

- **`ACTIVE`** / **`PREVIOUS`** / **`FALLBACK`**: provienen
  directamente de los tres campos correspondientes del puntero.
- **`HISTORICAL`**: referenciada por algún evento **confirmado** de la
  cadena, pero ya no es active/previous/fallback (p. ej. una
  generación por la que el sistema pasó en un rollback anterior).
- **`ORPHAN`**: persistida en disco sin ninguna referencia, ni del
  puntero ni de ningún evento confirmado — **nunca se elimina**, solo
  se reporta (issue `ORPHAN_GENERATION`).

Una generación cuyo `manifest.json` es ilegible/corrupto se sigue
listando (con `manifest_integrity` distinto de `VALID` y `lane`/`kind`/
`manifest_hash` en `None` — nunca se fabrica un valor para un
manifiesto que no se pudo leer, una deviación deliberada respecto del
enunciado original, documentada en el docstring del contrato).

## Cadena de eventos (event chain)

Reconstrucción seleccionador desde `active.json.latest_event_id`,
siguiendo `previous_event_id` hacia atrás (`pipeline/operational_
governance_reader.py::_walk_confirmed_chain`, Fase 15A Parte 5):

- un `visited: set[str]` evita loops infinitos (`GovernanceEventChain
  Status.CYCLIC`, issue `EVENT_CHAIN_CYCLE`);
- se valida que `sequence` decrezca exactamente en uno al retroceder
  (`EVENT_SEQUENCE_INVALID` si no);
- se valida `run_id` de cada evento contra el run real
  (`EVENT_CHAIN_BROKEN` si no coincide);
- se valida que el evento más reciente confirme la generación activa
  declarada por el puntero (`EVENT_POINTER_MISMATCH` si no);
- se valida que el evento terminal (sin `previous_event_id`) tenga
  `sequence=1`.

Solo los eventos alcanzados por esta caminata son `confirmed=True`;
cualquier otro evento persistido en `activation/events/` es un intento
**no confirmado** (huérfano, `ORPHAN_EVENT`) — preservado, nunca
borrado, nunca presentado como parte del lane activo.

## Huérfanos

Generaciones y eventos huérfanos se **detectan y reportan**
(`GovernanceIssueCode.ORPHAN_GENERATION`/`ORPHAN_EVENT`), pero Fase
15A **nunca** los elimina — no existe ninguna operación de garbage
collection en esta fase (ver Limitaciones).

## Artifacts activos

`GovernanceArtifactSummary` por cada `logical_name` conocido
(`candidates`, `context-packages`, `rule-drafts`, `guardrails`),
resuelto **exclusivamente** vía `unified_active_lane_router.resolve_
active_artifact` — nunca `resolve_with_fallback`. Cinco estados
(`GovernanceArtifactStatus`): `AVAILABLE` (descargable), `NOT_
AVAILABLE_IN_LANE` (ausencia legítima — p. ej. V1 sin `rule-drafts`
porque esa etapa nunca corrió), `MISSING` (el manifiesto referencia un
archivo que no existe), `CORRUPT` (el archivo existe pero su hash no
reconcilia), `BLOCKED` (ni siquiera se pudo determinar cuál de los dos
anteriores aplica, típicamente porque el manifiesto mismo es
ilegible). La subclasificación `MISSING` vs `CORRUPT` la calcula el
reader re-verificando el filesystem real (solo lectura).

## Grupos unified, evidence y provenance

`pipeline/operational_governance_group_adapter.py::build_unified_
group_summaries` lee **únicamente** los 4 archivos materializados
(`candidates.json`, `context-packages.json`, `rule-drafts.json`,
`guardrails.json`) de una generación `UNIFIED` ya persistida, los une
por `group_id`, y proyecta — sin reconstruir, sin invocar ningún
proveedor, sin acceder a Neo4j — `member_ids`/`source_candidate_ids`/
`review_decision_ids`/`evidence_ids`/`evidence_aliases`/`provenance_
references`/`guardrail_status` tal como el evaluador de Fase 14A y la
ejecución downstream de Fase 13 los produjeron. **Nunca elige un
member "ganador"**: todos los `member_ids` de un grupo se preservan.
Una generación `V1` produce una lista vacía (ausencia legítima). El
`ContextPackage`/`RuleDraft` completos **nunca** se exponen — solo IDs
de referencia cruzada (`context_package_record_id`/`rule_draft_
record_id`).

## Guardrails

`guardrail_status` (`PASSED`/`REJECTED`/`NOT_EVALUATED`) proviene
directamente de `UnifiedActivationUnifiedReference.guardrail_status`
(Fase 14A) — nunca recalculado.

## API

```
GET  /api/runs/{run_id}/governance
GET  /api/runs/{run_id}/governance/generations
GET  /api/runs/{run_id}/governance/generations/{generation_id}
GET  /api/runs/{run_id}/governance/events
GET  /api/runs/{run_id}/governance/groups[?generation_id=...]
GET  /api/runs/{run_id}/governance/artifacts/{logical_name}
HEAD /api/runs/{run_id}/governance/artifacts/{logical_name}
```

`logical_name` es un `StrEnum` cerrado (`GovernanceLogicalName`) —
FastAPI rechaza cualquier otro valor con 422 antes de que el handler
se ejecute. `generation_id` se valida contra
`^generation-[0-9a-f]{64}$` antes de usarse para construir una ruta.
Ningún endpoint acepta upload, autorización, API key, endpoint ni
modelo de proveedor. Ninguno escribe.

## UI

```
GET /ui/runs/{run_id}/governance
GET /ui/runs/{run_id}/governance/{summary,artifacts,events,generations,groups,issues}-fragment
```

Enlace "Gobierno operativo" agregado al detalle del run
(`run_status.html`). La página completa (`governance.html`) incluye
las 6 secciones (resumen, artifacts, historial, generaciones, grupos,
issues) vía `{% include %}` de los mismos fragments servidos de forma
independiente — la página funciona **completa sin HTMX** (cada
fragment es, a su vez, una URL válida por sí misma). El banner
read-only y el aviso de ausencia de autenticación son **siempre**
visibles, independientemente del estado del run.

## HTMX

Usado únicamente para mejorar filtros (`lane`, `generation_kind`,
`reachability`, `issue_severity`, `guardrail_status`): los formularios
de filtro son `<form method="get">` nativos (funcionan sin JavaScript,
recargando la página completa con los query params aplicados);
`hx-get`/`hx-target`/`hx-swap="outerHTML"` sustituyen esa recarga
completa por un refresco del fragment correspondiente cuando HTMX está
disponible. Ningún filtro acepta nombres de archivo ni paths. Ningún
fragment modifica estado.

## Descarga segura

`GET`/`HEAD .../artifacts/{logical_name}`: `Content-Type: application/
json`, `Content-Disposition: attachment; filename="{run_id}-{logical_
name}.json"` (ambos componentes validados/enumerados, sin CR/LF
posible), `ETag` con el `sha256` real entre comillas, `Cache-Control:
no-store` (nunca se cachea un artefacto que podría cambiar de lane).
404 para ausencia legítima (`NOT_AVAILABLE_IN_LANE`) o run/activación
inexistente; 409 para corrupción (`BLOCKED`/`MISSING`/`CORRUPT`) — la
API **nunca** repara automáticamente ante una descarga.

## No fallback por GET, no reparación

Ninguna lectura (reader, adapter, API, UI, fragment, descarga) ejecuta
jamás `resolve_with_fallback` ni ninguna de las 7 funciones de
transición de Fase 14B. Una generación o artefacto corrupto se
**reporta**, nunca se repara ni se sobrescribe — confirmado tanto por
tests unitarios como por la prueba de aislamiento ejecutable (Parte
13) y por el ciclo de integración real (Parte 14, estado C: la
corrupción se detecta y se reporta sin que ninguna lectura de gobierno
dispare el fallback; el fallback real solo ocurre cuando otro
consumidor optado explícitamente lo ejecuta).

## Ausencia de autenticación (histórico Fase 15A) → identidad delegada (Fase 15B1)

Al cierre de Fase 15A esta aplicación no implementaba ningún control de
acceso. **Fase 15B1** (`docs/SECURITY_AUTHORIZATION_AND_AUDIT.md`) agrega
identidad delegada (`DISABLED_DEV`/`TRUSTED_PROXY_HEADERS`) y RBAC sobre
las acciones de escritura descritas más abajo. Este overview read-only
(`OperationalGovernanceOverview`, construido por
`pipeline/operational_governance_reader.py`) sigue siendo de solo lectura
y sigue sin exigir identidad para consultarse por API JSON directa — los
issues `USER_AUTHENTICATION_NOT_AVAILABLE`/`WRITE_OPERATIONS_DISABLED`
que emite reflejan el estado de **este lector**, no el de la superficie de
escritura nueva: la UI HTML (`/ui/runs/{run_id}/governance*`) sí exige
identidad/RBAC desde Fase 15B1 (ver más abajo).

## Operaciones de escritura: CLI (histórico) + UI controlada (Fase 15B1)

Al cierre de Fase 15A, activación/fallback/rollback reales eran
exclusivamente responsabilidad de la CLI de Fase 14B
(`unified-activation-materialize`, `unified-activation-rollback`) con
autorización explícita vía YAML — issue `WRITE_OPERATIONS_DISABLED`, que
esta pantalla read-only sigue emitiendo sin cambios. **Fase 15B1** agrega
un segundo camino, sin modificar el CLI ni este lector: la UI de acciones
operativas (`/ui/runs/{run_id}/governance/actions`), con identidad
delegada, RBAC, CSRF, workflow de dos pasos (prepare/confirm/execute) y
auditoría append-only propia — ver `docs/SECURITY_AUTHORIZATION_AND_AUDIT.md`.
Ambos caminos invocan, sin modificarlo, el mismo
`pipeline/unified_materialization_service.py::materialize_unified_activation`
de Fase 14B.

## Consumidores legacy

`api/reads.py`, `api/downloads.py` y `api/routers/runs.py` (lectura
V1 directa e histórica) **no se modificaron**: siguen sirviendo
exactamente el mismo contenido V1 que antes de esta fase. La
gobernanza es una capa **nueva y opt-in**, montada en paralelo
(`api/routers/governance.py`, `ui/router.py`) — nunca reemplaza ni
redirige el flujo productivo existente.

## `ActiveArtifactResolver` opt-in

Fase 15A reutiliza `ActiveArtifactResolver` **únicamente** por su
propiedad `.store` (para obtener un `UnifiedActivationStore` ya
validado) — nunca llama a `.resolve()` ni a `.resolve_path()`, porque
ambos invocan internamente el fallback ejecutable
(`resolve_with_fallback`), incompatible con la semántica de un `GET`.
La resolución real de artifacts pasa siempre por `unified_active_
lane_router.resolve_active_artifact` (puramente de lectura).

## Limitaciones

- No existe autenticación de usuario (ver arriba) — heredada de Fases
  anteriores, no resuelta por esta fase.
- No existe garbage collection de generaciones/eventos huérfanos.
- No existe recuperación automática de lock stale (heredado de Fase
  14B) — esta fase nunca adquiere el lock, por lo que no le compete.
- La integración con la API/UI de gobierno es, en sí misma, la única
  superficie nueva de esta fase — ningún endpoint legacy fue
  modificado ni siquiera para agregar un enlace de conveniencia, salvo
  el enlace explícito "Gobierno operativo" en el detalle del run.
- La validación mostrada es estructural (issue `FUNCTIONAL_
  VALIDATION_NOT_AVAILABLE`, siempre presente): nunca equivale a una
  validación funcional ni a una aprobación de negocio.

## Próxima fase (Fase 15B)

Autenticación real (ver `docs/CONTROLLED_UNIFIED_MATERIALIZATION.md`,
sección Limitaciones — Fase 15), una política explícita de garbage
collection para generaciones/eventos huérfanos, y una eventual
integración opt-in de `ActiveArtifactResolver` en consumidores
productivos reales (más allá de esta capa de gobierno) quedan
reservadas a una fase futura.

## Ver también

- `docs/CONTROLLED_UNIFIED_MATERIALIZATION.md` (Fase 14B):
  materialización, puntero activo, cadena de eventos y router
  read-only que esta fase audita y expone.
- `docs/CONTROLLED_UNIFIED_ACTIVATION.md` (Fase 14A): evaluación de
  activación cuyo resultado se proyecta en la sección "readiness" del
  overview.
- `docs/SECURITY_AUTHORIZATION_AND_AUDIT.md` (Fase 15B1): identidad
  delegada, RBAC, CSRF y workflow controlado que agrega la superficie de
  escritura de esta pantalla (`/ui/runs/{run_id}/governance/actions*`),
  sin modificar este lector read-only ni el overview que construye.
