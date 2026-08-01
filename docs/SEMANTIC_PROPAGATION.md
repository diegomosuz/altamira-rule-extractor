# Propagación limitada de constantes y copias (Fase 4)

## Propósito

Demostrar, de forma conservadora y trazable, cadenas de asignación dentro
de un único `paragraph`:

```cobol
MOVE '0005' TO WS-COD-AUX
MOVE WS-COD-AUX TO WS-COD-RETORNO
```

`WS-COD-AUX` recibe directamente el literal `'0005'`; el segundo `MOVE`
copia ese valor. `semantic_propagation_analyzer.py` demuestra que
`WS-COD-RETORNO` también vale `'0005'`, conservando la cadena completa de
procedencia (`derivation_steps`).

También demuestra, bajo condiciones estrictas, condiciones nivel 88 con un
único `VALUE`:

```cobol
01 WS-COD-AUX PIC X(4).
   88 COD-AUX-INVALIDO VALUE '0005'.

SET COD-AUX-INVALIDO TO TRUE
MOVE WS-COD-AUX TO WS-COD-RETORNO
```

## Carácter diagnóstico

`SemanticPropagationArtifact` (`<run_dir>/diagnostics/
semantic-propagation.json`) es un artefacto **NO contractual**, generado
exclusivamente bajo demanda vía:

```bash
python -m altamira_extractor.cli semantic-propagation <run_id> [--json]
```

- No es un `PipelineStage`.
- No es necesario para que un run alcance `COMPLETED`.
- Nunca modifica `run.json`, `artifacts/01-10`,
  `diagnostics/semantic-coverage.json` ni `diagnostics/semantic-effects.json`
  (este último se calcula **en memoria** como entrada intermedia, nunca se
  lee ni se escribe desde disco).
- Esta fase **todavía no usa la propagación para detectar reglas**: no
  crea candidatos, no modifica `SemanticGraph`, no toca Neo4j, no cambia
  Q0 ni las relaciones `LEADS_TO`.

## Alcance: intraparrafo, nunca entre paragraphs

La propagación es **intraprograma e intraparrafo**: cada `paragraph`
comienza con un entorno de valores conocidos vacío, y ese entorno nunca
cruza a otro `paragraph` (ver caso 13 de
`tests/pipeline/test_semantic_propagation_analyzer.py`). Un `PERFORM`
nunca "entra" al cuerpo del párrafo destino ni asume que el control
regresa con el entorno intacto.

Explícitamente **fuera de alcance** en esta fase:

- análisis interprocedural (`CALL`, `EXEC CICS`);
- evaluación aritmética (`COMPUTE` siempre invalida sus targets);
- alias por `REDEFINES`, `OCCURS`, subscripts, reference modification;
- propagación a través de `EXEC SQL` o archivos;
- `PERFORM ... UNTIL/VARYING/THRU` (se trata como una barrera opaca, sin
  modelar el bucle).

## Análisis de ramas y merge conservador

La propagación reconstruye la jerarquía real de `IF`/`EVALUATE` a partir
de `parent_statement_id`/`branch_kind` (`CanonicalStatement` es una lista
plana pre-order, nunca un árbol — ver auditoría de Fase 1 en el historial
del proyecto). Reglas:

1. Cada rama (`THEN`/`ELSE`/`WHEN`/`WHEN_OTHER`) se analiza con un **clon**
   independiente del entorno de entrada — nunca se mezclan directamente
   los valores de dos ramas distintas.
2. Al salir de la decisión, se invalida cualquier variable escrita en **una
   o más** ramas; solo sobreviven los valores que ninguna rama tocó.
3. Nunca se elige una rama como "la que ocurrió": no hay ejecución
   simbólica de condiciones.
4. Un `EVALUATE` con varios `WHEN` (todos comparten `branch_kind=WHEN`) se
   distingue por adyacencia real en la lista de statements — nunca por
   `branch_kind` como clave de agrupación, que fusionaría ramas distintas
   incorrectamente. La señal estructural real es `branch_condition`
   (poblado únicamente en el primer statement de cada rama `WHEN` por el
   parser Java): together with a `branch_kind` change, marca el comienzo
   de una rama nueva.
5. `GO TO` y `PERFORM` son barreras: limpian el entorno posterior y nunca
   propagan a través del cuerpo o retorno. El cuerpo inline de un
   `PERFORM` (statements con `parent_statement_id` apuntando al `PERFORM`
   pero `branch_kind=null`) nunca se visita.

## Constantes y copias

- **`ASSIGN_LITERAL`** (`MOVE literal TO target`): `DIRECT_LITERAL`,
  preservando la forma lexical canónica (incluidas constantes figurativas
  como `ZERO`/`SPACE`/`HIGH-VALUE`, ya normalizadas por el parser Java —
  ver `docs/LEVEL_88_SUPPORT.md`). Nunca se expanden a bytes físicos.
- **`COPY_VALUE`** (`MOVE origen TO destino`, un único origen y un único
  destino): si el origen tiene un literal conocido en el entorno actual,
  `PROPAGATED_LITERAL`, con `derivation_steps` acumulando **toda** la
  cadena (sin límite artificial de dos saltos: una cadena de N copias
  produce N pasos). Si el origen no tiene valor conocido, `UNRESOLVED_COPY`.
  Un `MOVE` con múltiples orígenes/destinos nunca adivina una
  correspondencia campo a campo: cada destino queda invalidado.
- **`SET_CONDITION_TRUE`** propaga al data item padre **únicamente**
  cuando la condición tiene un único `VALUE` sin `THRU` (la misma señal
  que ya usa `semantic_effects_analyzer.py` para decidir si puebla
  `SemanticEffect.literal`): `CONDITION_LITERAL`. Con múltiples `VALUE` o
  `THRU`, `BLOCKED_PROPAGATION` (nunca se elige un valor arbitrario del
  conjunto). El `SemanticEffect` original (`SET_CONDITION_TRUE`) nunca se
  modifica ni se reemplaza por un `ASSIGN_LITERAL` sintético.
- **`SET_CONDITION_FALSE`** nunca infiere un valor: negar una condición
  de nivel 88 solo establece que el data item padre **no** satisface
  el/los `VALUE` declarado(s) — nunca define cuál de los posibles
  valores alternativos compatibles quedó realmente asignado. Siempre
  `BLOCKED_PROPAGATION`, con razón de barrera propia,
  `CONDITION_FALSE_VALUE_UNDETERMINED` (nunca `MULTIPLE_CONDITION_VALUES`
  ni `CONDITION_VALUE_RANGE`: esas dos quedan reservadas exclusivamente a
  `SET_CONDITION_TRUE` con varios `VALUE`/`THRU` real — la causa del
  bloqueo aquí es la negación en sí misma, sin importar si la condición
  declara uno, varios `VALUE`, o un `THRU`), invalidando cualquier valor
  previamente conocido del padre. Si el padre no puede resolverse contra
  `CanonicalProgram.data_items`, nunca se inventa un nombre ni un
  literal: el diagnóstico de resolución queda igualmente conservador.
- **`SET_VALUE`** (SET ordinario: índice, puntero, `UP/DOWN BY`, ...)
  nunca propaga por defecto: invalida sus targets.
- **`COMPUTE_VALUE`** siempre invalida sus targets (`INVALIDATED_VALUE`,
  razón `COMPUTED_VALUE`): nunca se evalúa la expresión ni se hace folding
  aritmético.
- **`EXECUTE_SQL`**: `CanonicalSqlAccess.host_variables` no distingue
  dirección de entrada/salida (ver `docs/SEMANTIC_EFFECTS.md`), así que
  cada host variable se invalida de forma conservadora, con barrera
  `SQL_HOST_DIRECTION_UNKNOWN`. Nunca se parsea el SQL ni se examina
  `predicate_text`.
- **`PRESERVED_STATEMENT`/`UNSUPPORTED_STATEMENT`** (incluye `OTHER`,
  `GOBACK`, `MOVE CORRESPONDING`, cualquier construcción no interpretada
  estructuralmente): nunca se asume ausencia de side effects — limpian el
  entorno **completo** de la región actual.

## Barreras

Una `PropagationBarrier` nunca es un error del analizador: es la
representación explícita de un límite conservador (`reason` +
`affected_variables` + `clears_entire_environment`). Motivos usados:
`CONTROL_FLOW_BOUNDARY` (`GO TO`), `LOOP_OR_PERFORM_BOUNDARY` (`PERFORM`),
`AMBIGUOUS_SYMBOL` (nombre simple homónimo), `UNKNOWN_SIDE_EFFECT` (`SET`
ordinario, `OTHER`, copia multi-campo), `UNSUPPORTED_STATEMENT`,
`COMPUTED_VALUE`, `SQL_HOST_DIRECTION_UNKNOWN`,
`MULTIPLE_CONDITION_VALUES` (exclusivo de `SET_CONDITION_TRUE` con una
condición que **realmente** declara más de un `VALUE`),
`CONDITION_VALUE_RANGE` (exclusivo de `SET_CONDITION_TRUE` con `VALUE
... THRU ...`), `CONDITION_FALSE_VALUE_UNDETERMINED` (exclusivo de
`SET_CONDITION_FALSE`, sin importar cuántos `VALUE` declare la
condición — ver sección anterior).

## Resolución de símbolos

Orden de preferencia: `qualified_name` exacto; si no coincide, `name`
simple **únicamente** cuando es único en todo el `CanonicalProgram`.
Cualquier ambigüedad (nombre simple compartido por dos data items bajo
padres distintos) bloquea la propagación (`AMBIGUOUS_DATA_ITEM_REFERENCE`)
— nunca se elige el primer match. Sin normalización de mayúsculas/
minúsculas: ningún punto ya auditado del pipeline (parser Java ni
analizadores Python) normaliza el casing de un identificador COBOL, así
que la resolución compara nombres exactamente como los conserva
`CanonicalProgram`. `REDEFINES`, `OCCURS`, subscripts, reference
modification y `LINKAGE` quedan fuera de alcance: nunca se resuelven como
alias.

## CLI

```bash
python -m altamira_extractor.cli semantic-propagation <run_id>
python -m altamira_extractor.cli semantic-propagation <run_id> --json
```

Requiere únicamente que `RUN_ID` haya alcanzado `PARSED` (`SUCCEEDED`).
Imprime un resumen (programas, párrafos, facts totales, desglose por
`PropagationFactKind`, barreras) y la ruta relativa del reporte
persistido. `--json` imprime además el artefacto completo. Errores usan
el mismo mecanismo de saneamiento del resto del CLI: exit code distinto
de cero, sin rutas absolutas, sin stacktrace, sin archivo parcial.

## Determinismo

`fact_id`/`barrier_id` se derivan de una concatenación legible y estable
de `program`/`paragraph`/`region_id`/`target_variable`/
`source_statement_id`/`fact_kind` (o `reason`)/un ordinal estable — nunca
UUID, timestamp, `hash()` de Python ni orden accidental de `dict`. El
artefacto no tiene timestamps; `to_stable_json()` (UTF-8, claves
ordenadas, formato legible) garantiza bytes idénticos entre dos
ejecuciones sobre la misma entrada.

## Compatibilidad histórica

`schema_version`/`analyzer_version` son `"1.0"` (primera versión de este
artefacto: no hay forma histórica previa que preservar).
`semantic_effects_schema_version`/`semantic_effects_analyzer_version`
registran la versión del `SemanticEffectsArtifact` calculado en memoria
que sirvió de entrada — nunca se lee `diagnostics/semantic-effects.json`
del disco, así que este campo es la única procedencia disponible.

## Uso posterior por los detectores V2 (Fase 5)

`V2ShadowCandidatesArtifact` (`docs/V2_DETECTORS_SHADOW_MODE.md`,
exclusivamente diagnóstico y bajo demanda) calcula este artefacto **en
memoria** (nunca lee ni escribe `diagnostics/semantic-propagation.json`)
y filtra sus `PropagatedValueFact` por `fact_kind` para proponer
candidatos experimentales (`V2_RETURN_CODE_PROPAGATION`,
`V2_STATE_CHANGE`) — nunca reimplementa el análisis de flujo descrito en
este documento ni modifica ninguno de los modelos aquí definidos.

## Limitaciones

- No detecta reglas ni candidatos: es exclusivamente diagnóstico.
- No modela caída natural entre párrafos, excepciones, `SQLCODE`, `FILE
  STATUS` ni `GOBACK` con side effects reales.
- Un `MOVE` con múltiples fuentes/destinos nunca adivina la
  correspondencia campo a campo.
- La ausencia de prueba suficiente siempre produce `UNRESOLVED_COPY`/
  `INVALIDATED_VALUE`/`BLOCKED_PROPAGATION`, nunca una inferencia
  optimista.
