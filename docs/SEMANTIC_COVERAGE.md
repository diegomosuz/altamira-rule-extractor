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
| `PRESERVED_ONLY` | La sentencia no coincide con ninguna de las 8 categorías que el parser interpreta estructuralmente (`StatementKind.OTHER`); se conserva el texto fuente, sin campos estructurados poblados. |
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
- **Nivel 88 se reporta, nunca se interpreta.** Cada `CanonicalDataItem`
  con `level == 88` genera una entrada `PARTIALLY_SUPPORTED` con
  `diagnostic_code=LEVEL_88_SEMANTICS_NOT_MODELED`, indicando
  explícitamente que VALUE, múltiples VALUE, rangos THRU y la variable
  padre no están modelados contractualmente hoy, y que `SET`/`IF` sobre la
  condición no pueden normalizarse con certeza a un predicado equivalente.
  Esta implementación **no agrega** soporte semántico para nivel 88 — esa
  es una fase posterior de la hoja de ruta.
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
