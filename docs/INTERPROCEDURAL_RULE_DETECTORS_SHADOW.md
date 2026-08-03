# Detectores de reglas interprocedurales en shadow mode (Fase 8 de la ampliación semántica)

## Propósito

Convertir hechos interprocedurales **ya demostrados** (Fase 6,
`docs/INTERPROCEDURAL_CALL_LINKAGE.md`; Fase 7,
`docs/INTERPROCEDURAL_PROPAGATION.md`) en candidatos experimentales de
regla de negocio: patrones donde el valor de retorno (`RETURNING`) o de
un argumento `BY REFERENCE` cruza una frontera `CALL` de forma
determinística, así como transiciones de estado observables entre
programas. Es una capa **puramente diagnóstica**, igual que
`SemanticCoverageReport`/`SemanticEffectsArtifact`/
`SemanticPropagationArtifact`/`InterproceduralCallLinkageArtifact`/
`InterproceduralPropagationArtifact`/`V2ShadowCandidatesArtifact`: nunca
modifica `SemanticGraph`, Neo4j, `queries/v1/`, candidatos V1 (`Q0`) ni
V2 (shadow), `ContextPackage`, `RuleDraft`, guardrails, ni el ZIP final
de reglas Markdown. No agrega un `PipelineStage`, no modifica
`RunState`, no se integra en `ingest`/`resume`.

Este módulo **nunca** reinterpreta `source_text`, **nunca** vuelve a
evaluar una asignación COBOL desde cero, **nunca** cruza un ciclo/SCC ni
resuelve un `CALL` dinámico (Fase 7 ya bloqueó esos call sites) y
**nunca** infiere la condición de negocio subyacente si no está
estructuralmente demostrada — solo reutiliza hechos ya validados por
`InterproceduralCallLinkageArtifact`/`InterproceduralPropagationArtifact`.

## Modo shadow

Se invoca exclusivamente bajo demanda vía CLI
(`interprocedural-candidates-shadow <run_id>`), nunca desde
`runner.py`/`run_ingestion`. Persiste **únicamente**
`diagnostics/interprocedural-rule-candidates-shadow.json`. Un candidato
de este artefacto **nunca** se presenta como regla oficialmente aprobada
(CLAUDE.md, sección "Candidato, fidelidad y aprobación") ni se promueve
a V1/V2.

## Los tres detectores

Cada detector es una función **pura** `(InterproceduralRuleDetectorContext)
-> list[InterproceduralRuleCandidate]`: nunca accede a filesystem, nunca
accede a Neo4j, nunca muta el contexto ni conoce a otros detectores.
Registrados en `pipeline/interprocedural_rule_detector_registry.py`,
ejecutados en orden alfabético por `detector_id`
(`ordered_detector_ids()`).

### 1. `INTERPROCEDURAL_RETURN_CODE_RULE`

Para cada call site con `returning_binding` resuelto
(`ArgumentBindingStatus.RESOLVED_POSITIONAL`), consulta el
`InterproceduralPropagationFact` de tipo `RETURNING_FACT` del mismo
binding:

- `status=PROPAGATED` → candidato `support=DETERMINISTIC`,
  `output_literal` = el literal demostrado. La evidencia incluye, además
  del hecho de salida, cualquier `ENTRY_FACT` `PROPAGATED` del mismo
  call site como contexto de entrada relevante — **nunca** se infiere
  cuál argumento "causó" el literal de retorno, solo se listan los que
  sí se demostraron.
- `status=BLOCKED` (certeza estructural, p. ej. `STOP RUN` vía
  `NON_RETURNING_TERMINATION`) → candidato `support=BLOCKED`, sin
  `output_literal`, con los `barriers`/`diagnostics` del hecho
  original.
- `status=INVALIDATED`/`UNRESOLVED` → **ningún candidato**: no hay
  certeza estructural ni de determinismo ni de bloqueo.

### 2. `INTERPROCEDURAL_BY_REFERENCE_RULE`

Para cada argumento `BY REFERENCE` resuelto (`RESOLVED_POSITIONAL`),
consulta el hecho `BY_REFERENCE_OUTPUT` del mismo binding, con la misma
partición PROPAGATED/BLOCKED/INVALIDATED-UNRESOLVED que el detector
anterior. Adicionalmente distingue `input_literal` (valor de entrada, si
el `ENTRY_FACT` correspondiente también fue `PROPAGATED`) de
`output_literal` (valor de salida) — nunca los confunde. `BY CONTENT` y
`BY VALUE` nunca generan un hecho de salida (Fase 7), así que este
detector nunca produce un candidato para ellos.

### 3. `INTERPROCEDURAL_STATE_TRANSITION_RULE`

Construido **exclusivamente** sobre bindings `BY REFERENCE` (nunca
`RETURNING`, que carece de semántica "antes/después" significativa).
Genera un candidato solo cuando **todas** estas condiciones se cumplen:

1. El `ENTRY_FACT` (valor de entrada) y el `BY_REFERENCE_OUTPUT` (valor
   de salida) del mismo binding son ambos `PROPAGATED`.
2. `entry_fact.literal != exit_fact.literal` — una transición genuina,
   nunca una identidad.
3. El actual del caller tiene `semantic_tag` (Fase 3b,
   `SemanticEnrichmentArtifact`, cargado desde disco) igual a `status` o
   `status_flag` — los únicos dos valores de
   `config/semantic-tags.yml.allowed_tags` cuyo nombre describe un
   estado genérico de negocio (a diferencia de `return_code`/`sqlcode`,
   que son códigos de retorno, no estados). **Nunca** se agrega un valor
   nuevo aquí ni se infiere el tag por el nombre de la variable
   (`STATUS`/`STATE`/`CODE`) — solo se reutiliza el tag ya asignado por
   el pipeline existente.

Si `SemanticEnrichmentArtifact` no está disponible (`semantic_enrichment
is None`, run que no alcanzó `SEMANTIC_ENRICHMENT_BUILT`), el detector
devuelve `[]` de inmediato — nunca degrada a una heurística alternativa.

## Evidencia y provenance

Cada `InterproceduralRuleEvidence` conserva la cadena completa
caller → call site → binding → callee → output → caller:
`caller_program`, `callee_program`, `call_site_id`, `statement_id`,
`binding_id`, `propagation_fact_ids` (siempre incluye el
`InterproceduralPropagationFact.fact_id` real, más opcionalmente
`source_fact_ids` de `SemanticPropagationArtifact`, Fase 4 — un
namespace distinto, nunca confundido con el anterior),
`semantic_effect_ids` (recuperados cruzando `source_fact_ids` contra
`SemanticPropagationArtifact.programs[].facts[].derivation_steps[].
effect_id`, nunca inventando una relación no demostrada),
`actual_name`/`formal_name`, `input_literal`/`output_literal` y
`source_references`. Ningún candidato puede carecer de evidencia
(invariante contractual).

`decision_id` es siempre `None` en un candidato interprocedural: no
puede reconstruirse el `decision_id` Neo4j-shaped exacto (requiere
`country_code`/`logical_name`/`version` de Manifest/Inventory, ausentes
del analizador) — limitación deliberada, nunca fabricada.

## Comparación con V1 y V2

Componente **separado** de los detectores
(`pipeline/interprocedural_rule_comparator.py`): ningún detector compara
candidatos directamente. Cada `InterproceduralRuleCandidate` recibe
**exactamente una** `InterproceduralCandidateComparison` (invariante
contractual). `RuleCandidate` (V1) no expone una variable objetivo
directamente — el `program_name` real se extrae de `paragraph_id`
(formato Neo4j-shaped) por **posición textual fija** (índice 3 tras
dividir por `"::"`, ver `pipeline/identifiers.py::ProgramIdentity.
program_id`), nunca recalculando el ID.

### Dimensiones independientes (`v1_relation`/`v2_relation`)

Auditoría de cierre (Fase 8, hardening): la relación con V1 y con V2 se
calcula de forma **independiente** para cada candidato
(`InterproceduralRelationStatus`: `MATCHED`, `RELATED`, `NOT_FOUND`,
`NOT_EVALUATED`), evaluada solo si `support != BLOCKED` (un candidato
`BLOCKED` recibe siempre `v1_relation=v2_relation=NOT_EVALUATED`,
`status=BLOCKED`: no demostró ningún valor, nunca se compara).
`NOT_EVALUATED` significa que la fuente correspondiente
(`CandidateArtifact` V1 o `V2ShadowCandidatesArtifact`) **nunca estuvo
disponible** para este run — distinto de `NOT_FOUND`, que significa que
la fuente **sí** se evaluó por completo y no encontró relación alguna.
Esta distinción existe precisamente para que la ausencia de una fuente
nunca se disfrace de "sin equivalente" (ver "Semántica ante fuentes
opcionales ausentes" más abajo).

`InterproceduralCandidateComparison.status` (`InterproceduralComparisonStatus`)
es la clasificación **principal**, única y con prioridad, derivada
determinísticamente de ambas dimensiones
(`contracts/interprocedural_rule_candidates.py::derive_comparison_status`,
única fuente de verdad compartida con el validador de coherencia del
contrato — el comparador nunca reimplementa esta lógica por su cuenta):

1. **`MATCHED_V1`** ⟺ `v1_relation=MATCHED` (mismo programa y mismo
   `outcome_code` que un `RuleCandidate` V1).
2. **`MATCHED_V2`** ⟺ `v1_relation!=MATCHED` y `v2_relation=MATCHED`
   (mismo programa, mismo `target` y mismo `resolved_literal` que un
   `V2ShadowCandidate`).
3. **`RELATED_V1`** ⟺ `v1_relation=RELATED` y `v2_relation!=MATCHED`.
4. **`RELATED_V2`** ⟺ `v1_relation` no es `MATCHED`/`RELATED` y
   `v2_relation=RELATED`.
5. **`INTERPROCEDURAL_ONLY`** ⟺ `v1_relation=NOT_FOUND` **y**
   `v2_relation=NOT_FOUND` — regla C de la auditoría: solo alcanzable
   cuando **ambas** fuentes estuvieron realmente disponibles y ninguna
   encontró candidato comparable.
6. **`NOT_EVALUATED`** ⟺ cualquier otro caso (ninguna fuente encontró
   `MATCHED`/`RELATED` y al menos una nunca estuvo disponible) — nunca
   se fabrica una comparación negativa en ausencia de una fuente real
   (regla D de la auditoría).

`v1_candidate_id`/`v2_candidate_id` **ya no son mutuamente
excluyentes**: cada uno refleja únicamente su propia dimensión. Un
candidato puede tener `status=MATCHED_V1` (V1 tiene prioridad) mientras
`v2_relation=MATCHED` también, con su propio `v2_candidate_id` — esa
información secundaria nunca se pierde, solo no determina la
clasificación principal (evita doble conteo en el summary, que
particiona por `status`, no por dimensión).

Nunca se invoca V1/V2 para comparar por semejanza textual — solo por
identidad estructural ya demostrada (programa, target, literal). Ni
`CandidateArtifact` V1 ni `V2ShadowCandidatesArtifact` se modifican
jamás: se usan exclusivamente como baseline de solo lectura.

### Semántica ante fuentes opcionales ausentes

| Fuente ausente | `v1_relation`/`v2_relation` | `status` posibles | Diagnóstico en el artefacto |
|---|---|---|---|
| `CandidateArtifact` V1 (`artifacts/06-candidates.json`) | `v1_relation=NOT_EVALUATED` siempre | `MATCHED_V2`/`RELATED_V2` (si V2 sí encuentra algo) o `NOT_EVALUATED` | `V1_CANDIDATES_UNAVAILABLE_ARTIFACTS_06_CANDIDATES_JSON_ABSENT` |
| `V2ShadowCandidatesArtifact` (`artifacts/04-semantic-graph.json` ausente, o V1 ausente — V2 depende de ambos) | `v2_relation=NOT_EVALUATED` siempre | `MATCHED_V1`/`RELATED_V1` (si V1 sí encuentra algo) o `NOT_EVALUATED` | `V2_SHADOW_CANDIDATES_UNAVAILABLE` |
| Ambas | `v1_relation=v2_relation=NOT_EVALUATED` | siempre `NOT_EVALUATED` | ambos diagnósticos anteriores |
| Ninguna (ambas disponibles) | `NOT_FOUND` en ambas cuando no hay relación | `INTERPROCEDURAL_ONLY` es alcanzable | ninguno |

El servicio siempre continúa (la ausencia de V1/V2 nunca es un error,
ver "Servicio y CLI"); los detectores que no dependen de V1/V2
(`RETURN_CODE_RULE`/`BY_REFERENCE_RULE`/`STATE_TRANSITION_RULE`, todos
ellos) se ejecutan igual — la ausencia de V1/V2 solo afecta la
**comparación**, nunca la detección. `SemanticEnrichmentArtifact`
ausente sigue el mismo principio pero afecta a un DETECTOR, no a una
comparación: deshabilita únicamente `STATE_TRANSITION_RULE`
(`detect_state_transition_rule` devuelve `[]` de inmediato), nunca
`RETURN_CODE_RULE` ni `BY_REFERENCE_RULE`, y agrega el diagnóstico
`STATE_TRANSITION_RULE_DETECTOR_SKIPPED_NO_SEMANTIC_ENRICHMENT` al
artefacto.

## Determinismo

`candidate_id`/`evidence_id`/`comparison_id` se derivan de SHA-256 sobre
las partes relevantes (`detector_id`, `call_site_id`, `binding_id`,
literales, etc.), truncado a 24 caracteres hex — nunca UUID, timestamp
ni `hash()` de Python (mismo patrón que Fase 5/6/7). `candidates`/
`comparisons` siempre se ordenan por su ID antes de construir el
artefacto final; el orden de entrada de `canonical_programs` nunca
afecta la salida. Dos ejecuciones sobre los mismos artefactos de entrada
producen bytes idénticos.

## Bloqueo

Un candidato `support=BLOCKED` documenta una certeza estructural de que
la propagación **no puede** demostrar un valor (p. ej. `STOP RUN` vía
`NON_RETURNING_TERMINATION`) — nunca se crea un candidato `BLOCKED`
simplemente porque exista una `CALL` bloqueada: se crea únicamente
cuando el call site **sí** declara un patrón de regla identificable
(`returning_binding`/argumento `BY REFERENCE` resuelto,
`ArgumentBindingStatus.RESOLVED_POSITIONAL`) pero falta la condición
necesaria para demostrar su valor con certeza (el hecho de salida de
Fase 7 es explícitamente `BLOCKED`).

Un call site con `CALL` dinámico, programa ausente, programa ambiguo,
self-call o miembro de un ciclo (SCC) nunca llega siquiera a tener un
binding `RESOLVED_POSITIONAL` — Fase 6/7 ya bloqueó ese call site por
completo (`blocked_call_sites`) antes de que exista un patrón
demostrable, así que estos detectores simplemente no encuentran un
`returning_binding`/`BY_REFERENCE_OUTPUT` resuelto y **no generan
ningún candidato** — ni siquiera `BLOCKED`. `summary.blocked_count`
cuenta exclusivamente candidatos con `support=BLOCKED` (nunca call
sites ni hechos): puede ser `0` aunque existan múltiples call sites
bloqueados en `InterproceduralCallLinkageArtifact`/
`InterproceduralPropagationArtifact` — eso es esperado, no un error de
reconciliación.

### Trazabilidad de bloqueos que nunca llegan a candidato

Un call site bloqueado (o indeterminado) que nunca produce candidato
**no desaparece en silencio**: `blocked_call_site_diagnostics`
(`pipeline/interprocedural_rule_detectors.py`) agrega al
`diagnostics` del artefacto, para cada call site con un patrón
potencial (RETURNING o BY REFERENCE) que ningún detector cubrió:

- `BLOCKED_CALL_SITE_NO_CANDIDATE::<call_site_id>::<barrier>` — el
  propio call site está bloqueado a nivel de Fase 6/7 (`DYNAMIC_CALL`,
  `RECURSION`, `CYCLE`, `MISSING_PROGRAM`, `AMBIGUOUS_PROGRAM`): "existe
  patrón potencial, pero bloqueado antes de que Fase 7 pueda siquiera
  intentar demostrar un valor".
- `NO_CANDIDATE_UNRESOLVED_VALUE::<call_site_id>` — el call site SÍ está
  resuelto/elegible, pero Fase 7 dejó el hecho `INVALIDATED`/
  `UNRESOLVED` (ni un valor único demostrable ni un bloqueo con certeza
  estructural): "existe patrón, pero no pudo evaluarse".

Un call site **sin** ningún patrón de regla (ni RETURNING ni BY
REFERENCE) nunca se traza — "no existe patrón de regla" es silencio
correcto, no una omisión.

## Ausencia de promoción y de Neo4j

Ningún candidato de este artefacto alimenta `ContextPackage`, la
generación LLM, guardrails, Markdown, el ZIP final, `CandidateArtifact`
V1, `V2ShadowCandidatesArtifact` ni Neo4j. El analizador no consulta
`SemanticGraph` en ningún punto (a diferencia de `V2DetectorContext`,
que sí la carga): la correlación de programas con V1/V2 se resuelve
íntegramente por análisis de cadenas sobre IDs ya existentes (ver
"Comparación con V1 y V2"), nunca contactando Neo4j.

## Servicio y CLI

`pipeline/interprocedural_rule_candidates_service.py` localiza el run,
carga `CanonicalProgram[]` desde `artifacts/02-canonical/` (único
requisito duro: `PARSED` `SUCCEEDED`), calcula
`SemanticEffectsArtifact`/`SemanticPropagationArtifact`/
`InterproceduralCallLinkageArtifact`/`InterproceduralPropagationArtifact`
**en memoria** (nunca lee ni escribe sus respectivos
`diagnostics/*.json`), y carga **opcionalmente** de disco:

- `artifacts/06-candidates.json` (`CandidateArtifact` V1) — si está
  ausente, la comparación V1 se reduce a `INTERPROCEDURAL_ONLY`/
  `RELATED_V2`/`MATCHED_V2` según corresponda.
- `artifacts/04-semantic-graph.json` — usado **únicamente** para
  derivar `V2ShadowCandidatesArtifact` en memoria (mismos
  `build_v2_detector_context`/`run_v2_shadow_detection` que
  `v2-candidates-shadow`, nunca reimplementados); si está ausente, la
  comparación V2 se omite.
- `artifacts/03b-semantic-enrichment.json`
  (`SemanticEnrichmentArtifact`) — usado únicamente para
  `STATE_TRANSITION_RULE`; si está ausente, ese detector simplemente no
  produce candidatos.

La ausencia de cualquiera de los tres **nunca** es un error. Persiste
**únicamente** `diagnostics/interprocedural-rule-candidates-shadow.json`
de forma atómica (`atomic_write_json`); nunca modifica `run.json`,
ningún `artifacts/01-10`, `CandidateArtifact` V1 ni `SemanticGraph`.

```bash
python -m altamira_extractor.cli interprocedural-candidates-shadow <run_id>
python -m altamira_extractor.cli interprocedural-candidates-shadow <run_id> --json
```

Imprime `run_id`, `candidates_total`, `deterministic`, `partial`,
`blocked`, el conteo por cada `InterproceduralRuleType`, `matched_v1`,
`matched_v2`, `related_v1`, `related_v2`, `interprocedural_only`,
`not_evaluated` y la ruta relativa del reporte; `--json` imprime además
el artefacto completo en JSON estable. No se invoca automáticamente
desde `ingest`, `resume`, la API ni la UI.

`not_evaluated` (auditoría de cierre, Fase 8) es **distinto** de
`interprocedural_only`: el primero cuenta candidatos donde al menos una
fuente (V1 o V2) nunca estuvo disponible para el run; el segundo cuenta
candidatos donde **ambas** fuentes se evaluaron por completo y ninguna
encontró relación — ver "Comparación con V1 y V2 — dimensiones
independientes" más abajo.

## Alcance explícitamente excluido

- Propagación sobre ciclos o fixed point: idéntico límite heredado de
  Fase 7.
- Resolución de `CALL` dinámico: idéntico límite heredado de Fase 6/7.
- Inferencia de programas ausentes.
- Efectos secundarios no demostrados estructuralmente.
- Generación de reglas vía LLM, promoción de candidatos, `ContextPackage`,
  `RuleDraft`, guardrails o reglas Markdown.
- Neo4j, UI, CICS, COMMAREA, `LINK`/`XCTL`, `REDEFINES` complejo,
  análisis de memoria física: mismos límites heredados de Fase 6/7.

## Limitaciones

- `decision_id` siempre `None` (ver "Evidencia y provenance").
- `STATE_TRANSITION_RULE` depende de `SemanticEnrichmentArtifact` ya
  persistido en disco (Fase 3b) — un run que nunca alcanzó
  `SEMANTIC_ENRICHMENT_BUILT` nunca produce candidatos de este tipo,
  aunque la transición sea estructuralmente demostrable.
- La comparación V2 depende de que `artifacts/04-semantic-graph.json`
  exista en disco — un run que no alcanzó `SEMANTIC_GRAPH_BUILT` nunca
  compara contra V2 (se reduce a `INTERPROCEDURAL_ONLY`/`MATCHED_V1`/
  `RELATED_V1`).
- Hereda íntegramente las limitaciones ya documentadas de Fase 6
  (`docs/INTERPROCEDURAL_CALL_LINKAGE.md`) y Fase 7
  (`docs/INTERPROCEDURAL_PROPAGATION.md`): sin fixed point, sin
  evaluación simbólica, sin inferencia por nombre/`PICTURE`/comentario,
  `AMBIGUOUS_PROGRAM` inalcanzable end-to-end (probado en aislamiento).
- No detecta reglas oficialmente aprobadas: es exclusivamente
  diagnóstico, igual que el resto de artefactos de la ampliación
  semántica.

## Ver también

Fase 9 (`docs/CANDIDATE_PROMOTION_ASSESSMENT.md`) agrega una capa
diagnóstica **posterior**, puramente de solo lectura, que cataloga cada
`InterproceduralRuleCandidate` junto a sus equivalentes V1/V2 y evalúa
criterios de preparación para una eventual revisión funcional — sin
modificar nunca este artefacto ni promover ningún candidato.
