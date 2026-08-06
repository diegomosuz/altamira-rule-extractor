# Cobertura semántica (Fase 1 de la ampliación semántica)

## Propósito

Este documento describe `SemanticCoverageReport`, un informe **diagnóstico**
que responde una pregunta que hoy la aplicación no puede responder por sí
sola: de todo lo que el parser Java entregó en `CanonicalProgram`, ¿cuánto
recibió efectivamente una interpretación semántica estructurada (grafo,
dependencias, candidatos) y cuánto quedó solo como texto conservado o
explícitamente marcado como no soportado?

Corresponde a la Fase 1 de la hoja de ruta de ampliación semántica descrita
en la auditoría previa (`feat/semantic-expansion-foundation`): observabilidad
y diagnóstico **antes** de tocar ningún detector, cualquier query Cypher o
el parser Java.

## Carácter diagnóstico, no contractual

`SemanticCoverageReport` **no es** un artefacto `artifacts/01-10`. No
participa en ninguna validación de invariantes, no lo lee ningún detector,
no lo lee `ContextPackageBuilder`, no lo lee `RuleDraftGenerationBuilder`,
no lo lee ningún guardrail, y no influye en absoluto en qué candidatos se
detectan o qué reglas se redactan. Es exactamente lo que su nombre indica:
un diagnóstico de solo lectura sobre artefactos V1 ya persistidos.

Esto implica, explícitamente:

- **No cambia candidatos V1.** `CandidateArtifact`/`06-candidates.json` se
  lee, nunca se escribe.
- **No cambia reglas V1.** `RuleDraft`, `GuardrailCandidateArtifact` y los
  Markdown finales nunca se tocan ni se leen.
- **No es un `PipelineStage`.** No aparece en `RunState.stages`, no bloquea
  ni condiciona ninguna transición del pipeline, y `runner.py`/
  `run_ingestion` nunca lo invocan.
- **No reemplaza revisión funcional.** Que una construcción esté
  `FULLY_SUPPORTED` significa que fue interpretada estructuralmente, nunca
  que la regla de negocio subyacente sea correcta o esté completa. La
  responsabilidad de la Fase 1 es medir cobertura de interpretación, no
  validar reglas.

## Ubicación

```
<run_dir>/diagnostics/semantic-coverage.json
```

`diagnostics/` es un directorio **opcional**, externo a `artifacts/`, que
ninguna etapa V1 crea, lee, ni valida. Un run histórico (de antes de que
esta funcionalidad existiera) simplemente no tiene este directorio — se
comporta exactamente igual que hoy, sin ningún cambio de comportamiento.

## Cómo generarlo

Exclusivamente bajo demanda, vía CLI:

```
python -m altamira_extractor.cli semantic-coverage <run_id>
python -m altamira_extractor.cli semantic-coverage <run_id> --json
```

Requiere que `<run_id>` ya haya alcanzado `CANDIDATES_DETECTED`
(`SUCCEEDED`) — es el primer punto del pipeline en el que existen los
cuatro artefactos que el analizador necesita:

- `artifacts/02-canonical/` (uno o más `CanonicalProgram`)
- `artifacts/03-dependencies.json` (`DependencyArtifact`)
- `artifacts/04-semantic-graph.json` (`SemanticGraph`)
- `artifacts/06-candidates.json` (`CandidateArtifact`)

El comando:

1. localiza el run vía `Settings.runs_dir`;
2. carga y valida los cuatro artefactos (nunca los modifica);
3. calcula un hash determinístico de cada uno (`source_artifact_hashes`);
4. ejecuta el analizador puro (`pipeline/semantic_coverage_analyzer.py`:
   sin Neo4j, sin variables de entorno, sin LLM);
5. persiste `diagnostics/semantic-coverage.json` de forma atómica
   (`atomic_write_json`, el mismo primitivo que usa cada etapa V1);
6. imprime un resumen legible; con `--json`, imprime además el reporte
   completo.

No se invoca automáticamente desde `ingest`, `resume`, la API ni la UI en
esta primera implementación (Decisión de arquitectura §4 de la tarea que
originó este documento).

## `SemanticSupportStatus`

Dimensión **distinta** de `ParseSupportStatus` (usado por
`SemanticEnrichmentArtifact` para DDL/CSV) — nunca se combinan ni se
reutiliza uno por el otro. `ParseSupportStatus` mide si un archivo DDL/CSV
externo pudo interpretarse; `SemanticSupportStatus` mide si una
construcción COBOL, ya presente en `CanonicalProgram`, recibió
interpretación semántica estructurada.

| Valor | Significado |
|---|---|
| `FULLY_SUPPORTED` | La construcción quedó completamente disponible para el grafo/las dependencias (p. ej. un `MOVE` de un literal a un único destino resoluble). |
| `PARTIALLY_SUPPORTED` | Algunos campos se conservan estructuralmente, pero falta información necesaria para una interpretación completa (p. ej. un `MOVE` variable-a-variable: se conservan los nombres, pero no hay propagación de valores). |
| `PRESERVED_ONLY` | La sentencia no coincide con ninguna de las 9 categorías que el parser interpreta estructuralmente (`StatementKind.OTHER`); se conserva el texto fuente, sin campos estructurados poblados. Mismo valor para `StatementKind.PROGRAM_TERMINATION` (Fase 7b: `GOBACK`/`STOP RUN`/`EXIT PROGRAM` SÍ están interpretados estructuralmente, pero ninguno mueve/calcula/asigna datos). |
| `UNSUPPORTED` | El parser/adaptador declaró **explícitamente** esta construcción como no decodificada (`CanonicalProgram.unsupported_constructs`). A diferencia de `PRESERVED_ONLY`, es una declaración explícita del propio productor del artefacto, no una inferencia de este analizador. |

## Interpretación conservadora de `zero_candidate_reason`

`ZeroCandidateReason` explica, de forma conservadora y sin inventar
información, por qué un programa tiene la cantidad de candidatos Q0 que
tiene. **Nunca existe un valor `NO_RULES`**: un análisis estático no puede
afirmar la inexistencia de una regla funcional — solo puede describir por
qué el detector Q0 (que reconoce exclusivamente el patrón
`Decision -[:LEADS_TO]-> DataItem{semantic_tag:'return_code'}`) no encontró
una coincidencia, o si sí la encontró.

| Valor | Cuándo se asigna |
|---|---|
| `CANDIDATES_PRESENT` | `candidate_count > 0`. |
| `NO_DECISIONS` | El programa no tiene ningún nodo `Decision` en el grafo. |
| `DECISIONS_WITHOUT_RESOLVED_EFFECTS` | Hay decisiones, pero ninguna tiene una relación `LEADS_TO` saliente. |
| `RESOLVED_EFFECTS_WITHOUT_Q0_MATCH` | Hay al menos una decisión con `LEADS_TO`, pero cero candidatos Q0 (p. ej. el destino no tiene `semantic_tag='return_code'`). |
| `NO_Q0_MATCH` | Reservado para una categoría más específica que las anteriores cuando los datos son completos pero no encajan en ningún caso previo. Esta implementación no lo asigna nunca (ver limitaciones). |
| `INSUFFICIENT_DIAGNOSTIC_DATA` | El programa no pudo localizarse dentro del `SemanticGraph` (p. ej. inconsistencia entre `CanonicalProgram.program_name` y las propiedades del nodo `Program`), o cualquier otra situación donde el cálculo no es fiable. |

## Limitaciones (deliberadas, de esta primera implementación)

- **No hay propagación de constantes ni de copias.** Una cadena
  `MOVE '0005' TO WS-COD-AUX` seguida de `MOVE WS-COD-AUX TO
  WS-COD-RETORNO` se reporta como dos construcciones independientes (la
  primera `FULLY_SUPPORTED`, la segunda `PARTIALLY_SUPPORTED`); el informe
  nunca afirma que `'0005'` llegó a `WS-COD-RETORNO`, porque el pipeline V1
  no lo puede demostrar.
- **Nivel 88: modelado nativamente cuando es demostrable (`analyzer_
  version="1.1"`, Fase 3, ver `docs/LEVEL_88_SUPPORT.md`).** Una condición
  nivel 88 con padre y al menos un VALUE capturados en
  `CanonicalProgram.condition_names` genera una entrada
  `LEVEL_88_CONDITION_NAME_MODELED`/`FULLY_SUPPORTED`
  (`diagnostic_code=LEVEL_88_CONDITION_FULLY_MODELED`). El diagnóstico
  previo, `LEVEL_88_SEMANTICS_NOT_MODELED`, se mantiene exclusivamente
  para el residual: una condición preservada como `CanonicalDataItem
  (level=88)` cuyo padre y/o VALUE el parser no pudo demostrar. `SET`
  resuelto contra una condición conocida genera
  `SET_CONDITION_RESOLVED`/`FULLY_SUPPORTED` en vez del
  `SET_TARGET_KIND_AMBIGUOUS`/`PARTIALLY_SUPPORTED` anterior; referencias
  `IF`/`EVALUATE` verificadas contra `condition_names` se cuentan aparte
  en `CONDITION_NAME_REFERENCE_RESOLVED`. `analyzer_version` subió a
  `"1.1"` (`schema_version` sin cambios: la forma del contrato es la
  misma). Reportes históricos con `analyzer_version="1.0"` siguen
  cargando.
- **`candidate_impact` es categórico, nunca numérico.** Los valores
  (`NONE`/`LOW`/`MEDIUM`/`HIGH`/`UNKNOWN`) son un juicio determinístico y
  documentado por tipo de construcción (ver
  `pipeline/semantic_coverage_analyzer.py`), nunca una estimación de
  "cuántas reglas se perdieron" — esa cantidad es indemostrable por
  análisis estático.
- **`NO_Q0_MATCH` no se usa en esta versión.** Se reserva para una futura
  revisión del algoritmo de clasificación si aparece un caso intermedio
  entre `DECISIONS_WITHOUT_RESOLVED_EFFECTS` y
  `RESOLVED_EFFECTS_WITHOUT_Q0_MATCH` que amerite su propia categoría.
- **`source_references` está acotado** a
  `MAX_SOURCE_REFERENCES_PER_CONSTRUCT` (5) ocurrencias por construcción;
  `occurrence_count` siempre refleja el total real. El informe nunca
  contiene `source_text` completo ni código COBOL — solo
  programa/párrafo/`statement_id`/archivo relativo/línea.

## Compatibilidad con runs históricos

Un run que nunca ejecutó `semantic-coverage` no tiene `diagnostics/` y se
comporta exactamente igual que antes de que esta funcionalidad existiera:
ningún lector V1 (`read_run_state`, la API, la UI, el CLI) sabe de su
existencia ni la requiere.

## Idempotencia y hashing

El cálculo es puro y determinístico: dado el mismo contenido de
`artifacts/02-canonical/`, `03-dependencies.json`, `04-semantic-graph.json`
y `06-candidates.json`, dos ejecuciones de `semantic-coverage` producen
**bytes idénticos** en `diagnostics/semantic-coverage.json` (sin
timestamps de ningún tipo). `source_artifact_hashes` registra el hash
SHA-256 de cada artefacto de entrada consumido (para `02-canonical/`, un
hash determinístico del directorio completo: rutas relativas ordenadas +
hash de cada archivo, nunca mtimes, nunca rutas absolutas) — permite
detectar si el informe quedó desactualizado respecto a los artefactos que
lo originaron.

## Tests

- `tests/contracts/test_semantic_coverage.py` — contrato: versiones,
  serialización estable, coherencia de contadores, límites, campos extra.
- `tests/pipeline/test_semantic_coverage_analyzer.py` — analizador puro:
  clasificación por `StatementKind`, nivel 88, decisiones/Q0, determinismo,
  el caso explícito de la cadena de dos `MOVE`.
- `tests/pipeline/test_semantic_coverage_service.py` — servicio de
  filesystem: creación, determinismo, errores claros, no modificación de
  artefactos de entrada.
- `tests/test_cli_semantic_coverage.py` — comando CLI: salida legible,
  `--json`, códigos de salida, saneamiento de errores, no regresión
  explícita sobre `06-candidates.json`/`07-context/`/`08-rule-drafts/`/
  `09-guardrails/`/`10-rules/`.

## Manifiesto estático de cobertura semántica (Fase 15B2-A, Partes B/C/D)

Todo lo anterior de este documento describe `SemanticCoverageReport`: qué
pasó en **un run concreto**. Esta sección describe un concepto **distinto
y complementario**, agregado en la Fase 15B2-A: qué construcciones COBOL
declara soportar el **producto**, independientemente de cualquier run, y
cómo se observa/reconcilia esa declaración contra la realidad.

### `SemanticCoverageManifest` — `config/semantic_coverage.yaml`

Manifiesto **versionado**, cargado como `SemanticCoverageManifest`
(`contracts/semantic_coverage.py`). Para cada construcción COBOL conocida
(`construct_id`, p. ej. `IF`, `ADD`, `LEVEL_88_CONDITION`,
`CALL_DYNAMIC`), declara su estado en cada una de **once capas
arquitectónicas** (`SemanticCoverageLayer`), separadas deliberadamente
para nunca conflatar "ProLeap reconoce la construcción" con "el pipeline
la interpreta":

`PROLEAP_PARSER` → `JAVA_STATEMENT_EXTRACTION` → `CANONICAL_REPRESENTATION`
→ `SEMANTIC_GRAPH` → `DATA_FLOW` → `CONTROL_FLOW` → `INTERPROCEDURAL` →
`DETECTOR` → `EVIDENCE` → `PROVENANCE` → `FUNCTIONAL_VALIDATION`.

Cada capa declara un `SemanticCoverageStatus` (`SUPPORTED`,
`PARTIALLY_SUPPORTED`, `RECOGNIZED_NOT_INTERPRETED`, `NOT_SUPPORTED`,
`NOT_APPLICABLE`, `UNKNOWN`) y, cuando afirma `SUPPORTED`, **exige** al
menos una `SemanticCoverageEvidenceReference` verificable (un test real,
una fixture, una revisión de dominio) — el contrato rechaza en
`model_validate()` cualquier afirmación de soporte sin evidencia
adjunta.

`RECOGNIZED_NOT_INTERPRETED` es el estado más importante de distinguir:
ProLeap reconoce genuinamente ADD/SUBTRACT/MULTIPLY/DIVIDE/STRING/
UNSTRING/INSPECT/SEARCH/SORT/MERGE en su gramática (evidencia real,
confirmada por inspección directa del JAR resuelto), pero
`StatementExtractor.convertOne()` no tiene un `case` dedicado para
ninguno de ellos: caen en `convertOther()` (`kind=OTHER`), un **bucket
técnico**, nunca una capacidad funcional (ver "Motivo de diseño" abajo).

El manifiesto se edita **a mano**, nunca se genera desde la salida de un
run — su propósito es declarar lo que el código *puede* hacer, verificado
línea por línea contra el código fuente real (Java y Python), no lo que
hizo en un caso concreto.

### Reconciliación ejecutable — `pipeline/semantic_coverage_registry.py`

`SemanticDetectorCoverage.detector_id`/`SemanticRuleFamilyCoverage.
rule_family` son campos `str` libres (no `Enum`): un id mal escrito o
inventado solo se detecta ejecutando este módulo, nunca en
`model_validate()`. `reconcile_manifest(manifest)` compara cada
`detector_id`/`rule_family` citado contra los tres registries reales
(`candidate_detector.DETECTOR_ID`, `V2_DETECTOR_REGISTRY`,
`INTERPROCEDURAL_RULE_DETECTOR_REGISTRY`, `UnifiedRuleFamily`) y produce
`SemanticCoverageIssue[]`:

- `UNKNOWN_DETECTOR_ID`/`UNKNOWN_RULE_FAMILY` (severidad `ERROR`): el
  manifiesto cita algo que no existe en el código real.
- `UNDOCUMENTED_DETECTOR` (severidad `WARNING`): un detector real nunca
  citado por ningún `construct_id` del manifiesto.

Sin discovery dinámico, sin plugins — mismo patrón que
`v2_detector_registry.py`/`interprocedural_rule_detector_registry.py`.
`check_manifest_reconciled(manifest)` levanta `SemanticCoverageRegistryError`
si el manifiesto quedó desincronizado del código instalado actualmente.

### Observaciones por-run — `diagnostics/semantic-coverage-observations.json`

Un **tercer** artefacto, distinto de `SemanticCoverageReport` (agrega por
`StatementKind` genérico) y de `SemanticCoverageManifest` (declara
capacidad, nunca depende de un run): ata cada `construct_id` del
catálogo a lo que **realmente se observó** en `artifacts/02-canonical/`
de un run concreto.

Granularidad **honesta**, nunca inflada: varios `construct_id` comparten
el mismo `java_statement_kind` (p. ej. `IF`/`ELSE`/`CONDITIONS_COMPOUND`
comparten `kind=IF`) — reciben el mismo `occurrence_count`, y
`shared_java_statement_kind_construct_ids` lo declara explícitamente en
vez de fingir una precisión que `CanonicalStatement.kind` no ofrece. Para
el bucket `OTHER`, `unsupported_identities` extrae un prefijo
**sanitizado** de cada mensaje en `CanonicalProgram.unsupported_constructs`
(nunca el mensaje completo) y lo asocia a un `construct_id` únicamente
vía una tabla curada manualmente contra evidencia JAR real
(`_PARSER_CLASS_TO_CONSTRUCT_ID`); una identidad no reconocida se
persiste con `construct_id=null`, nunca con una asociación adivinada.

Generar:

```
python -m altamira_extractor.cli semantic-coverage-report <run_id>
python -m altamira_extractor.cli semantic-coverage-report <run_id> --json
```

Requiere que `<run_id>` haya alcanzado `PARSED` (`SUCCEEDED`) — solo
necesita `artifacts/02-canonical/`, a diferencia de `semantic-coverage`
(que también requiere `03-dependencies.json`/`04-semantic-graph.json`/
`06-candidates.json`, disponibles recién en `CANDIDATES_DETECTED`). El
comando imprime también, siempre, los `SemanticCoverageIssue` de la
reconciliación estática (independiente de `<run_id>`).

### Motivo de diseño: por qué `RECOGNIZED_NOT_INTERPRETED` importa

Presentar ADD/SUBTRACT/STRING/etc. como "no soportado" a secas ocultaría
que ProLeap sí los reconoce (el problema es específico de
`StatementExtractor`, no de la gramática). Presentarlos como "soportado"
a secas sería una afirmación falsa: ningún dato estructurado se extrae de
ellos hoy. `RECOGNIZED_NOT_INTERPRETED` nombra exactamente el estado
real, capa por capa, sin optimismo ni pesimismo — el mismo principio que
gobierna `SemanticSupportStatus` arriba, aplicado ahora a nivel de
producto en vez de nivel de run.

### Uso previsto: `semantic-coverage-report` sobre datos reales

Ejecutar `semantic-coverage-report` contra un run real ya reveló, en esta
misma fase, dos construcciones ausentes del catálogo (`DISPLAY`,
`EXEC CICS`): la herramienta está diseñada exactamente para esto —
convertir "no sabemos qué falta" en una lista concreta y accionable,
nunca para ocultar la ausencia.
