# Detectores V2 en shadow mode (Fase 5)

## Propósito

Medir, sin tocar el pipeline funcional, qué candidatos adicionales
detectarían analizadores nuevos si se combinan `SemanticGraph`,
`SemanticEffectsArtifact`, `SemanticPropagationArtifact` y
`CandidateArtifact` V1 (`artifacts/06-candidates.json`, salida de Q0). Los
"detectores V2" son analizadores experimentales que consultan esa
evidencia ya calculada por fases anteriores y proponen candidatos
adicionales, comparándolos explícitamente contra lo que Q0 ya detecta.

Fase 8 (`docs/INTERPROCEDURAL_RULE_DETECTORS_SHADOW.md`) extiende esta
misma idea al espacio **interprocedural**: sus candidatos se comparan,
de forma conservadora y de solo lectura, contra `V2ShadowCandidate`
cuando `artifacts/04-semantic-graph.json` está disponible — nunca
modifica este artefacto ni sus detectores.

## Carácter diagnóstico y experimental

`V2ShadowCandidatesArtifact` (`<run_dir>/diagnostics/
v2-candidates-shadow.json`) es un artefacto **NO contractual**,
generado exclusivamente bajo demanda vía:

```bash
python -m altamira_extractor.cli v2-candidates-shadow <run_id> [--json]
```

- No es un `PipelineStage`; nunca se invoca desde `runner.py`/
  `run_ingestion`, la API ni la UI.
- No modifica `run.json` ni `RunState`.
- No modifica ningún `artifacts/01-10`, `diagnostics/semantic-coverage.
  json`, `diagnostics/semantic-effects.json` ni `diagnostics/
  semantic-propagation.json` — `SemanticEffectsArtifact`/
  `SemanticPropagationArtifact` se calculan **en memoria** (mismo patrón
  que `semantic_propagation_service.py`), nunca se leen ni se escriben
  desde/hacia disco.
- No accede a Neo4j ni ejecuta Cypher.
- No invoca un proveedor LLM.
- Sus candidatos **nunca alimentan** `ContextPackage`, generación de
  `RuleDraft`, guardrails, Markdown, el ZIP final, `CandidateArtifact` V1
  ni el grafo Neo4j — este artefacto y este comando CLI en particular
  siguen siendo puramente diagnósticos. Un `V2ShadowCandidate` nunca se
  presenta como regla aprobada (CLAUDE.md, sección "Candidato, fidelidad
  y aprobación"). Ver "Fase 15B3-B" más abajo: desde esa fase, un
  camino **separado y opt-in** (`CANDIDATES_DETECTED`,
  `enhanced_candidate_integration.py`) reutiliza las mismas funciones
  puras `detect_return_code_propagation`/`detect_level_88_return_code`
  de `v2_detectors.py` para producir `RuleCandidate` V1 reales — pero
  nunca lee ni escribe `v2-candidates-shadow.json`, y nunca a través de
  este comando CLI.
- Q0 (`queries/v1/q0_candidates.cypher`, `pipeline/candidate_detector.py`)
  nunca se modifica ni se reejecuta: se consulta únicamente como
  baseline ya calculado (`ctx.v1_candidates`).

## Registry de detectores

`pipeline/v2_detector_registry.py` mapea `detector_id ->
V2DetectorDefinition` de forma explícita y estática — sin import
dinámico, sin discovery automático, sin plugins. `ordered_detector_ids()`
devuelve los IDs en orden alfabético (nunca el orden de inserción del
`dict`), garantizando una secuencia de ejecución determinística.

## Detectores iniciales

Los tres detectores comparten un principio (`pipeline/v2_detectors.py`):
un `PropagatedValueFact` de tipo `DIRECT_LITERAL`/`PROPAGATED_LITERAL`/
`CONDITION_LITERAL` ya es, por construcción de
`semantic_propagation_analyzer.py` (Fase 4), una cadena sin barrera
intermedia ni invalidación posterior — ningún detector reverifica eso:
filtra por `fact_kind`, nunca reimplementa el análisis de flujo.

### `V2_RETURN_CODE_PROPAGATION`

Decisiones cuyo efecto sobre un `DataItem` con `semantic_tag=return_code`
puede demostrarse vía `SemanticPropagation` (literal directo o cadena de
copias), aunque Q0 no tenga un `LEADS_TO` directo suficiente:

```cobol
IF CONDICION
   MOVE '0005' TO WS-COD-AUX
   MOVE WS-COD-AUX TO WS-COD-RETORNO
END-IF
```

`support=DETERMINISTIC` cuando el `fact_kind` es literal y el target
resuelve a `return_code` bajo una decisión. Si la propagación no resolvió
un literal (`UNRESOLVED_COPY`/`BLOCKED_PROPAGATION`) pero sí hay una
decisión que alcanza el target `return_code`, produce
`support=BLOCKED` con `diagnostic_codes` explicando la ausencia de
prueba — nunca elige un valor arbitrario.

### `V2_LEVEL_88_RETURN_CODE`

`SET condición-88 TO TRUE`, dentro de una decisión, resuelto contra un
único `VALUE`, cuyo padre es un `DataItem` con `semantic_tag=return_code`:

```cobol
01 WS-COD-RETORNO PIC X(4).
   88 COD-CAMPO-INVALIDO VALUE '0005'.

IF ERROR-DE-ENTRADA
   SET COD-CAMPO-INVALIDO TO TRUE
END-IF
```

`support=DETERMINISTIC` cuando el `SemanticEffect` (`SET_CONDITION_TRUE`)
resolvió un único literal. Con múltiples `VALUE`/`THRU` (ambigüedad ya
detectada por Fase 3/4), `support=BLOCKED` con
`diagnostic_codes=[..., "V2_LEVEL_88_VALUE_NOT_UNIQUE"]` — nunca elige un
valor del conjunto.

### `V2_STATE_CHANGE`

Decisiones que cambian deterministicamente un `DataItem` ordinario (no
`return_code`):

```cobol
IF CONDICION
   MOVE 'A' TO WS-ESTADO
END-IF
```

Siempre `support=PARTIAL`, `detector_score=STATE_CHANGE_DETECTOR_SCORE`
(constante `0.7`, documentada en `pipeline/v2_detectors.py`: el cambio
estructural está demostrado con el mismo criterio que
`RETURN_CODE_RULE`, pero su relevancia funcional como regla de negocio
no está garantizada — no toda escritura de un data item ordinario es una
regla). `diagnostic_codes` siempre incluye
`V2_STATE_CHANGE_FUNCTIONAL_RELEVANCE_NOT_GUARANTEED`.

## Solapamiento intencional entre detectores

`V2_RETURN_CODE_PROPAGATION` y `V2_LEVEL_88_RETURN_CODE` pueden detectar
la **misma** decisión funcional (un `SET condición-88 TO TRUE` produce
tanto un `SemanticEffect.SET_CONDITION_TRUE` como un
`PropagatedValueFact.CONDITION_LITERAL`, evidencia válida para ambos
detectores). Esto es intencional, no un bug: cada uno describe la
construcción COBOL desde un ángulo distinto (uno el efecto normalizado,
otro conserva la condición-88 original). **Nunca se fusionan** — Fase 9
exige que ambos candidatos se conserven por separado y se relacionen vía
`RELATED_NOT_EQUIVALENT` en la comparación V1/V2.

## Identificadores deterministas

`candidate_id = f"v2::{detector_id}::{digest}"`, donde `digest` es un
SHA-256 truncado a 24 caracteres hex de una concatenación canónica
(`\x1f`-separada) de `(detector_id, detector_version, program, paragraph,
decision_id-o-"", anchor_statement_id, target_key, resolved_literal-o-"")`
(`pipeline/v2_detectors.py::candidate_id_for`). `comparison_id =
f"comparison::{digest}"`, derivado análogamente de `(program, decision_id,
status)`. Nunca UUID, timestamp ni `hash()` de Python.

## Deduplicación intradetector (nunca interdetector)

Dos observaciones del **mismo** detector que producen idéntica identidad
(mismo `candidate_id`) se fusionan conservando la unión ordenada de
evidencia (`semantic_effect_ids`, `propagation_fact_ids`,
`source_references`, `diagnostic_codes`) — nunca se descarta evidencia
silenciosamente (`pipeline/v2_detectors.py::_merge_candidate_evidence`).
Esta fusión **nunca** ocurre entre detectores distintos (ver sección
anterior).

## Comparación V1/V2

Por cada `decision_id` presente en V1 y/o V2 (`pipeline/
v2_shadow_detector.py::_build_comparisons`):

- **`V1_ONLY`**: Q0 detectó un candidato sin evidencia V2 equivalente.
- **`V2_ONLY`**: uno o más detectores V2 encontraron evidencia que Q0 no
  detecta.
- **`MATCHED`**: mismo `program`/`paragraph`/`decision_id` y el único
  `outcome_code` de V1 coincide exactamente con el conjunto de literales
  resueltos por V2.
- **`RELATED_NOT_EQUIVALENT`**: dos formas válidas — (a) V1 y V2
  coinciden en la decisión pero el literal/`outcome_code` no puede
  verificarse como idéntico; (b) dos detectores V2 **distintos**
  encuentran evidencia sobre el mismo efecto funcional sin equivalente en
  Q0 (V1 vacío, pero entonces se exigen al menos dos
  `v2_candidate_ids`).

## Ausencia de activación funcional (este artefacto/comando)

Ningún `V2ShadowCandidate` persistido en `v2-candidates-shadow.json`
incrementa `detector_score` de un candidato V1, modifica
`CandidateArtifact`, altera `ContextPackage`, cambia el comportamiento
del LLM/guardrails, ni se presenta en el Markdown o el ZIP final.
`support=DETERMINISTIC` describe únicamente que el **detector V2** tiene
evidencia autocontenida suficiente — nunca implica aprobación funcional
(`FUNCTIONALLY_APPROVED` sigue fuera de alcance en V1, CLAUDE.md). Fase
15B3-B (sección siguiente) integra en el pipeline productivo dos de los
tres `V2RuleType` mediante un camino separado que nunca pasa por este
artefacto ni por este comando.

## CLI

```bash
python -m altamira_extractor.cli v2-candidates-shadow <run_id>
python -m altamira_extractor.cli v2-candidates-shadow <run_id> --json
```

Requiere que `RUN_ID` haya alcanzado `PARSED`, `SEMANTIC_GRAPH_BUILT` y
`CANDIDATES_DETECTED` (`SUCCEEDED`). Imprime un resumen (detectores
ejecutados, candidatos V1, candidatos V2, desglose por soporte
`DETERMINISTIC`/`PARTIAL`/`BLOCKED`, desglose por estado de comparación
`MATCHED`/`V1_ONLY`/`V2_ONLY`/`RELATED_NOT_EQUIVALENT`, conteos por
`rule_type`) y la ruta relativa del reporte persistido. `--json` imprime
además el artefacto completo. Errores usan el mismo mecanismo de
saneamiento del resto del CLI: exit code distinto de cero, sin rutas
absolutas, sin stacktrace, sin archivo parcial.

## Determinismo

`schema_version="1.0"`, `analyzer_version="1.0"`. Sin timestamps.
`semantic_effects_schema_version`/`semantic_effects_analyzer_version`/
`semantic_propagation_schema_version`/
`semantic_propagation_analyzer_version` registran la versión de los dos
artefactos calculados en memoria que sirvieron de entrada — nunca se leen
sus archivos de disco, así que estos campos son la única procedencia
disponible. `to_stable_json()` (UTF-8, claves ordenadas, formato legible)
garantiza bytes idénticos entre dos ejecuciones sobre la misma entrada
(`pipeline/v2_shadow_detector.py::run_v2_shadow_detection` es una función
pura sobre `V2DetectorContext`).

## Compatibilidad histórica

Primera versión de este artefacto: no hay forma histórica previa que
preservar. No afecta `CandidateArtifact` V1, `SemanticGraph`,
`GuardrailCandidateArtifact` ni ningún artefacto `artifacts/01-10`
existente — un run que nunca ejecuta `v2-candidates-shadow` se comporta
exactamente igual que antes de esta fase.

## Limitaciones

- No detecta reglas ni candidatos aprobables: es exclusivamente
  diagnóstico y comparativo.
- Solo tres `V2RuleType` están modelados
  (`RETURN_CODE_RULE`/`LEVEL_88_RETURN_CODE_RULE`/`STATE_CHANGE_RULE`):
  `THRESHOLD_RULE`/`CALCULATION_RULE`/`PERSISTENCE_RULE`/
  `ROUTING_RULE`/`AUTHORIZATION_RULE`/`FILE_OUTPUT_RULE`/
  `PARAMETER_DEPENDENT_RULE` quedan deliberadamente fuera hasta que exista
  semántica equivalente a Fase 2/3/4 para esas categorías.
- Hereda todas las limitaciones de `SemanticPropagation` (ver
  `docs/SEMANTIC_PROPAGATION.md`): sin evaluación aritmética, sin alias
  por `REDEFINES`/`OCCURS`, sin propagación entre `paragraph`s, y sin
  propagación de **valores** entre programas (Fase 6, fundación
  interprocedural CALL/LINKAGE, ver
  `docs/INTERPROCEDURAL_CALL_LINKAGE.md`: un `CALL` es siempre una
  barrera de propagación, `CALL_BOUNDARY`, nunca un puente de valores).
  Ningún detector V2 aquí descrito consume
  `InterproceduralCallLinkageArtifact` ni genera un candidato a partir de
  un `SemanticEffect(kind=CALL_PROGRAM)`: esa fundación es puramente
  diagnóstica y paralela, sin ningún punto de integración con este
  registry todavía.
- `V2_STATE_CHANGE` nunca garantiza relevancia funcional: cualquier
  escritura determinista de un data item ordinario dentro de una decisión
  produce un candidato `PARTIAL`, sin importar si ese data item es o no
  relevante para una regla de negocio real.

## Fase 15B3-B: integración productiva opt-in de RETURN_CODE_RULE/LEVEL_88_RETURN_CODE_RULE

"Realineación mínima del motor de extracción de reglas": con
`Settings.enhanced_candidates_enabled=true`
(`ALTAMIRA_ENHANCED_CANDIDATES_ENABLED`), la etapa `CANDIDATES_DETECTED`
(`pipeline/candidates_detected_stage.py`) ejecuta, **después** de Q0 y
en memoria (sin persistir ningún diagnóstico), exactamente
`detect_return_code_propagation`/`detect_level_88_return_code` de este
mismo módulo (`v2_detectors.py`, sin modificar) sobre
`V2DetectorContext` recién construido — mismas funciones puras que usa
`v2-candidates-shadow`, nunca una copia ni una reimplementación. Ver
`pipeline/enhanced_candidate_integration.py`.

Alcance deliberadamente parcial (Fase 15B3-B, momento de esta sección):

- **Integrados**: `RETURN_CODE_RULE`, `LEVEL_88_RETURN_CODE_RULE` —
  ambos anclados a una `Decision` real, compatibles sin cambios con las
  queries Q4/Q5a de `context_package_builder.py` (que exigen un
  `decision_id` correspondiente a un nodo `Decision` existente).
- **Nunca integrado EN FASE 15B3-B** (corregido en Fase 15B3-C1, ver
  nota abajo): `STATE_CHANGE_RULE` — nunca `DETERMINISTIC` (siempre
  `PARTIAL`); CLAUDE.md prohíbe que `PARTIAL` se convierta
  automáticamente en aprobado.
- **Nunca integrado en esta fase**: `InterproceduralRuleType.
  BY_REFERENCE_RULE` (`docs/INTERPROCEDURAL_RULE_DETECTORS_SHADOW.md`)
  — anclado a un `call_site_id`, no a una `Decision`; integrarlo
  exigiría extender Q4/Q5a (o el metamodelo de evidencia D4/D5a), una
  decisión de arquitectura fuera del alcance de una realineación
  mínima. Sigue siendo shadow-only.

Solo se convierten a `RuleCandidate` los `V2ShadowCandidate` con
`support=DETERMINISTIC` **y** `decision_id` resuelto contra un nodo
`Decision` real en `04-semantic-graph.json` — nunca se fabrica
`decision_id`/`condition`/`source_file` sintéticos; si el grafo no
expone lo mínimo necesario, el candidato se descarta con un warning
trazable (nunca falla el run completo por un candidato aislado).

**Deduplicación**: identidad = `decision_id` (ya determinístico,
codifica programa/paragraph/línea/ordinal). Si V1 ya produjo un
candidato para ese `decision_id`, el candidato V2 se descarta (V1
**nunca** se modifica, ni siquiera para fusionarle `evidence_ids`) y se
registra un warning en `CandidateArtifact.warnings`. Dos detectores V2
distintos (p. ej. `V2_RETURN_CODE_PROPAGATION` y
`V2_LEVEL_88_RETURN_CODE` sobre la misma decisión, ver "Solapamiento
intencional" arriba) se conservan como `RuleCandidate` separados — igual
que en el catálogo shadow, nunca se fusionan por compartir
decisión/target/literal.

**Procedencia**: `RuleCandidate` gana cuatro campos nuevos, retro-
compatibles (`contracts/candidate.py`): `candidate_source` (V1 por
defecto), `rule_family` (`RETURN_CODE` por defecto — garantía
estructural de Q0), `support_level` (`Literal["DETERMINISTIC"]` — los
únicos candidatos que llegan aquí), `evidence_ids` (vacío para V1).
Runs históricos sin estos campos siguen siendo válidos.

Con el flag en `False`, `CANDIDATES_DETECTED` es byte-compatible con el
comportamiento anterior a esta fase.

**Nota (Fase 15B4-CANDIDATE-QUALITY-5E)**: desde Fase 15B3-C1/C2 el
alcance integrado creció -- `STATE_CHANGE_RULE` (promovido a
`STATE_TRANSITION` cuando el target tiene `semantic_tag in {status,
status_flag}`) y `CALCULATION_RULE` también se productivizan bajo este
mismo flag. Desde Fase 5E, `enhanced_candidates_enabled=True` es el
**default** (antes `False`) -- el modo `False`/legacy sigue disponible
explícitamente. Ver `docs/CAPABILITY_COVERAGE_1_17.md` para la matriz de
capacidades vigente; esta sección documenta el estado histórico de Fase
15B3-B, no el alcance actual.

## Procedimiento futuro de promoción

Este documento describe exclusivamente el estado shadow-mode (Fase 5)
de `V2ShadowCandidatesArtifact`/`v2-candidates-shadow` en sí. Cualquier
decisión de promover `V2_STATE_CHANGE` o el resto del catálogo shadow a
un rol funcional requiere una decisión de arquitectura documentada
aparte — nunca ocurre implícitamente por ejecutar este comando ni por
acumular evidencia `DETERMINISTIC`.

Fase 9 (`docs/CANDIDATE_PROMOTION_ASSESSMENT.md`) agrega una capa
diagnóstica **posterior**, puramente de solo lectura, que cataloga cada
`V2ShadowCandidate` junto a sus equivalentes V1/interprocedural y
evalúa criterios de preparación (`READY_FOR_CONTROLLED_REVIEW`/
`REVIEW_REQUIRED`/`BLOCKED`/...) sin modificar nunca este artefacto ni
ejecutar la promoción real descrita arriba.
