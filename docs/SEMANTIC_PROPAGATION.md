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

- propagación de valores **entre programas**: `CALL` se modela como una
  barrera conservadora (ver sección "CALL como barrera interprocedural",
  Fase 6), pero nunca cruza a otro `CanonicalProgram` ni analiza el
  cuerpo del subprograma invocado;
- `EXEC CICS`, `LINK`/`XCTL`, `COMMAREA`, `CHANNEL`/`CONTAINER`
  (fuera de alcance de toda la fundación interprocedural, no solo de
  esta propagación);
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

## CALL como barrera interprocedural (Fase 6)

`CALL_PROGRAM` (`SemanticEffect.kind`, ver `docs/SEMANTIC_EFFECTS.md`)
nunca propaga un valor a través de una llamada, nunca analiza el cuerpo
del subprograma invocado y nunca cruza de `CanonicalProgram` — el
`CanonicalProgram` completo del callee, si existe en el paquete, es
irrelevante para esta fase (esa resolución es responsabilidad exclusiva
de `docs/INTERPROCEDURAL_CALL_LINKAGE.md`, un artefacto distinto y
posterior). Reglas de invalidación, por `CallPassingMode` del argumento:

- **`BY REFERENCE`** (y `UNKNOWN` con identidad conocida): invalida la
  variable — el subprograma podría modificarla, y su cuerpo nunca se
  analiza — `INVALIDATED_VALUE`, diagnóstico
  `CALL_ARGUMENT_BY_REFERENCE_INVALIDATED`.
- **`BY CONTENT`/`BY VALUE`**: **nunca** invalida solo por el paso (el
  caller conserva su propia copia intacta) ni propaga información
  **desde** el callee — el valor conocido del caller, si existía antes
  del `CALL`, sigue siendo válido después.
- **`RETURNING`**: siempre invalida al receptor — nunca se inventa el
  valor que devolvería el subprograma — `INVALIDATED_VALUE`, diagnóstico
  `CALL_RETURNING_INVALIDATED`.
- Un argumento `USING` sin forma estructural identificable (ni
  identificador, ni literal, ni `OMITTED`) limpia el entorno
  **completo** de la región, no solo su propia posición: no hay nada
  conservador que razonar sobre un argumento sin forma reconocible
  (diagnóstico `CALL_ARGUMENT_SHAPE_UNRESOLVED`).
- Un `CALL` dinámico (`CallTargetKind.DYNAMIC`) sigue exactamente las
  mismas reglas de invalidación de argumentos/`RETURNING` que un `CALL`
  literal, más el diagnóstico `CALL_DYNAMIC_TARGET_UNRESOLVED` — el
  identificador del target dinámico **nunca** se usa para "resolver" el
  programa invocado, ni siquiera cuando su valor está propagado y
  conocido en el entorno en ese punto (la propagación de constantes y la
  resolución de programa, Fase 11 de `docs/INTERPROCEDURAL_CALL_LINKAGE.md`,
  son completamente independientes).
- Un `CALL` sin ningún argumento `BY REFERENCE`/`UNKNOWN` identificable
  ni `RETURNING` nunca registra una `PropagationBarrier` vacía: sigue
  siendo conceptualmente una barrera (nunca se propaga *a través* de
  ella), pero sin ningún efecto observable que valga la pena persistir.

Todo lo anterior describe el comportamiento **intraprograma** de
`semantic_propagation_analyzer.py`, sin cambios en Fase 7. Un módulo
separado y puramente diagnóstico, `docs/INTERPROCEDURAL_PROPAGATION.md`,
lee los `PropagatedValueFact` aquí producidos (como entrada de solo
lectura, junto con `InterproceduralCallLinkageArtifact`) para propagar
valores literales **entre** programas bajo condiciones estrictas por
`CallPassingMode` — sin alterar este artefacto ni este analizador.

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
condición — ver sección anterior), `CALL_BOUNDARY` (Fase 6, ver sección
"CALL como barrera interprocedural").

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

`schema_version`/`analyzer_version` son `"1.0"` (forma histórica, previa
a la barrera `CALL_BOUNDARY`) o `"1.1"` (Fase 6: agrega el motivo de
barrera `CALL_BOUNDARY`, sin cambiar ningún campo del modelo — la forma
de `PropagationBarrier`/`PropagatedValueFact` es idéntica en ambas
versiones, solo se amplía el conjunto de valores válidos de
`PropagationBarrierReason`). El contrato acepta ambos valores en
lectura. `semantic_effects_schema_version`/`semantic_effects_analyzer_version`
registran la versión del `SemanticEffectsArtifact` calculado en memoria
que sirvió de entrada — nunca se lee `diagnostics/semantic-effects.json`
del disco, así que este campo es la única procedencia disponible.

**Fase 7b (distinción GOBACK/STOP RUN/EXIT PROGRAM)**: `schema_version`/
`analyzer_version` NO subieron — a diferencia de `SemanticEffectsArtifact`
(que sí cambió su lógica de clasificación, ver `docs/SEMANTIC_EFFECTS.md`),
`semantic_propagation_analyzer.py` **no se modificó en absoluto**: ya
despachaba `PRESERVED_STATEMENT` (el `SemanticEffectKind` que tanto
`OTHER` como `PROGRAM_TERMINATION` producen) a `_handle_unknown_effect`
desde antes de esta fase. Cero cambios de código, cero cambio de
comportamiento — confirmado con el JAR real contra un baseline v1.5.0
aislado: `artifacts/03b-semantic-enrichment.json` byte a byte idéntico
(salvo `run_id`) para PROGRULE1 y ambos paquetes Catherine.

## Uso posterior por los detectores V2 (Fase 5)

`V2ShadowCandidatesArtifact` (`docs/V2_DETECTORS_SHADOW_MODE.md`; el
comando CLI `v2-candidates-shadow` en sí sigue siendo exclusivamente
diagnóstico y bajo demanda) calcula este artefacto **en memoria** (nunca
lee ni escribe `diagnostics/semantic-propagation.json`) y filtra sus
`PropagatedValueFact` por `fact_kind` para proponer candidatos
(`V2_RETURN_CODE_PROPAGATION`, `V2_STATE_CHANGE`) — nunca reimplementa el
análisis de flujo descrito en este documento ni modifica ninguno de los
modelos aquí definidos. **Estado actual (Fase 15B4-CANDIDATE-QUALITY-5E)**:
estas mismas funciones también se ejecutan dentro de `CANDIDATES_DETECTED`
(`enhanced_candidate_integration.py`) para producir `RuleCandidate`
productivos reales (`RETURN_CODE_PROPAGATION`, `STATE_TRANSITION` cuando el
target tiene `semantic_tag ∈ {status, status_flag}`) en
`06-candidates.json` cuando `enhanced_candidates_enabled=true` (default
desde 5E) — ya no son puramente "experimentales" en ese camino. Ver
`docs/CAPABILITY_COVERAGE_1_17.md`.

## Limitaciones

- No detecta reglas ni candidatos: es exclusivamente diagnóstico.
- No modela caída natural entre párrafos, excepciones, `SQLCODE`, `FILE
  STATUS` ni `GOBACK` con side effects reales.
- Un `MOVE` con múltiples fuentes/destinos nunca adivina la
  correspondencia campo a campo.
- La ausencia de prueba suficiente siempre produce `UNRESOLVED_COPY`/
  `INVALIDATED_VALUE`/`BLOCKED_PROPAGATION`, nunca una inferencia
  optimista.

## `declared_value` (Fase 15B3-C5-B) NUNCA es un seed de propagación

`CanonicalDataItem.declared_value` (una cláusula `VALUE` simple de DATA
DIVISION, sin `THRU` ni múltiples intervalos) es **declaración**, nunca
**ejecución**: `semantic_propagation_analyzer.py` no lo lee, no lo usa
como valor inicial de ningún `_Environment`, y no lo modifica — cero
cambios de código en este módulo. Tres conceptos deliberadamente
distintos:

- **`DECLARED_INITIAL_VALUE`**: lo único que `declared_value` puede
  demostrar — el texto literal de la cláusula `VALUE` tal como aparece
  en el código fuente.
- **`EFFECTIVE_RUNTIME_VALUE`**: el valor real del DataItem en un punto
  de ejecución dado — nunca demostrado por `declared_value` ni por este
  analizador.
- **`PROVEN_CONSTANT_VALUE`**: que `DECLARED_INITIAL_VALUE ==
  EFFECTIVE_RUNTIME_VALUE` esté garantizado — requeriría CFG/análisis
  interprocedural que este proyecto no implementa; fuera de alcance.

Un `MOVE`/`SET`/`COMPUTE` posterior sobre el mismo DataItem **nunca
invalida** `declared_value` (no hay nada que invalidar: `declared_value`
nunca afirmó ser el valor efectivo) y, simétricamente, `declared_value`
**nunca** alimenta `DIRECT_LITERAL`/`PROPAGATED_LITERAL`/`ASSIGN_LITERAL`
ni ningún otro `PropagationFactKind`. El enriquecimiento evidencial que
consume `declared_value` (`context_package_builder.
_enrich_decision_with_declared_value_evidence`, `EvidenceEntry(kind=
"declared_value_context")`) vive enteramente fuera de este módulo, en
`CONTEXTS_BUILT` — una etapa posterior y completamente independiente de
`SemanticPropagationArtifact`.
