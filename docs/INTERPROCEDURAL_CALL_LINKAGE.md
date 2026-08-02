# Fundación interprocedural CALL/LINKAGE (Fase 6 de la ampliación semántica)

## Propósito

Capturar y analizar, de forma determinística y estructural, las
construcciones interprocedurales de COBOL presentes en un paquete: `CALL`
literal y dinámico, argumentos `USING` (`BY REFERENCE`/`BY CONTENT`/`BY
VALUE`), `RETURNING`/`GIVING`, `LINKAGE SECTION`, `PROCEDURE DIVISION
USING`/`RETURNING`, la asociación posicional actual-formal entre un call
site y la interfaz del programa invocado, la resolución de ese programa
dentro del **mismo paquete**, los programas externos o ausentes, las
llamadas ambiguas, y la recursión (directa e indirecta, vía ciclos en el
call graph).

Es una capa **paralela y diagnóstica**: nunca modifica `SemanticGraph`,
Neo4j, `queries/v1/q0_candidates.cypher`, los candidatos V1 (`Q0`) ni V2
(shadow), `ContextPackage`, `RuleDraft`, los guardrails ni el ZIP final de
reglas Markdown. No hay propagación de **valores** entre programas: un
`CALL` es siempre una barrera conservadora, nunca un puente para que un
valor conocido en el caller "entre" al callee o viceversa.

Fase 7 (`docs/INTERPROCEDURAL_PROPAGATION.md`) y Fase 8
(`docs/INTERPROCEDURAL_RULE_DETECTORS_SHADOW.md`) consumen la resolución
de programa, los bindings actual-formal y el call graph de este
artefacto como entrada de solo lectura — ninguna de las dos modifica
este analizador ni su artefacto.

## `CALL` literal vs. dinámico

```cobol
CALL 'CALLEEP' USING ...        *> literal: CallTargetKind.LITERAL
CALL WS-PROGRAM-NAME USING ...  *> dinamico: CallTargetKind.DYNAMIC
```

- **Literal**: el nombre del programa es un literal alfanumérico —
  siempre identificable estructuralmente, candidato a resolución interna
  (ver más abajo).
- **Dinámico**: el target es un identificador cuyo valor en tiempo de
  ejecución decide el programa invocado. **Nunca** se resuelve usando el
  valor propagado de ese identificador (`docs/SEMANTIC_PROPAGATION.md`,
  sección "CALL como barrera interprocedural"): la propagación de
  constantes y la resolución de programa son capas completamente
  independientes, aunque ambas puedan "conocer" el mismo valor en algún
  punto del análisis.
- **`UNKNOWN`** (`CallTargetKind.UNKNOWN`): el parser no pudo identificar
  el target de forma estructural (caso residual, defensivo). Se trata con
  las mismas reglas de resolución que un `CALL` dinámico
  (`UNRESOLVED_DYNAMIC`), nunca se inventa un nombre.

## Argumentos `USING`: `BY REFERENCE`/`BY CONTENT`/`BY VALUE`

Cada argumento posicional de `CALL ... USING` se captura como un
`CanonicalCallArgument` (`ordinal`, `expression`, `data_item_name`/
`literal`, `passing_mode`, `omitted`). `BY REFERENCE A B` produce **dos**
argumentos posicionales, no uno — ProLeap agrupa por frase (`BY REFERENCE`/
`BY CONTENT`/`BY VALUE`), pero cada elemento dentro de la frase es un
argumento independiente en la posición real de `USING`.

`OMITTED` (únicamente válido en `BY REFERENCE`) se captura como
`omitted=True`, sin `data_item_name`/`literal` — nunca se inventa una
identidad para un argumento explícitamente omitido.

## `RETURNING`/`GIVING`

ProLeap expone `RETURNING` y `GIVING` como el **mismo** nodo de AST
(`CallGivingPhraseContext`, con ambos terminales `GIVING()`/`RETURNING()`
disponibles sobre el mismo contexto): el parser Java los captura de forma
idéntica en `CanonicalStatement.call_returning_data_item`, sin distinguir
cuál palabra clave usó el código fuente (no hay diferencia semántica real
entre ambas). El receptor **nunca** se trata como un valor conocido tras
el `CALL` (ver barrera de propagación).

## `LINKAGE SECTION`

`CanonicalProgram.linkage_data_items: list[CanonicalLinkageDataItem]` es
una lista **separada** de `data_items` (que sigue representando
exclusivamente `WORKING-STORAGE`): `LINKAGE` describe la interfaz
potencial de un programa, no su almacenamiento propio, y mezclarlos
alteraría la superficie V1 ya estable (`SemanticGraphBuilder` no consume
este campo). Reutiliza los mismos helpers de extracción que
`WORKING-STORAGE` (`dataItemName`/`qualifiedNameOf`/`pictureOf`/
`usageOf`), ya que `LinkageSection` comparte la interfaz
`DataDescriptionEntryContainer` de ProLeap.

## `PROCEDURE DIVISION USING`/`RETURNING`

`CanonicalProgram.entry_parameters: list[CanonicalEntryParameter]`
conserva los parámetros formales en **orden fuente exacto**, cada uno
resuelto contra `linkage_data_items` cuando es posible
(`linkage_item_qualified_name`): coincidencia exacta de `qualified_name`
primero, luego coincidencia única de nombre simple; si ninguna resuelve
(ausente, u homónimo ambiguo), `linkage_item_qualified_name` queda `None`
— **nunca** se inventa una definición. `entry_returning_data_item`
conserva el nombre del receptor de `PROCEDURE DIVISION ... RETURNING`.

ProLeap solo permite `BY REFERENCE` (default) o `BY VALUE` como forma de
un parámetro formal (nunca `BY CONTENT`, grammaticalmente inválido ahí) —
confirmado por auditoría de la gramática
(`ProcedureDivisionUsingParameterContext` no tiene rama `byContent`).

## Resolución de programa (Fase 11)

Contra los `CanonicalProgram` del **mismo paquete**, nunca contra
filesystem, classpath ni red:

| `resolution_status` | Cuándo |
|---|---|
| `RESOLVED_INTERNAL` | Target literal cuyo `called_program_name` coincide con **exactamente un** `CanonicalProgram.program_name` del paquete. |
| `UNRESOLVED_DYNAMIC` | Target dinámico o `UNKNOWN` — nunca resuelto vía propagación. |
| `UNRESOLVED_MISSING_PROGRAM` | Target literal sin ningún `CanonicalProgram` con ese nombre en el paquete. |
| `AMBIGUOUS_PROGRAM` | Target literal que coincide con **más de un** `CanonicalProgram` (dos versiones del mismo `PROGRAM-ID` presentes simultáneamente) — nunca se elige arbitrariamente. |

`AMBIGUOUS_PROGRAM` está actualmente **inalcanzable end-to-end** a través
del flujo real `analyze_semantic_effects -> analyze_interprocedural_call_linkage`,
porque `SemanticEffectsArtifact` ya exige `program_name` único entre
`canonical_programs` en su propio contrato (`_check_programs_unique`): dos
`CanonicalProgram` homónimos nunca llegan juntos hasta el analizador
interprocedural en la forma actual del pipeline. La lógica de detección
(`_resolve_program`) se prueba de forma aislada
(`tests/pipeline/test_interprocedural_call_linkage_analyzer.py::
test_resolve_program_reports_ambiguous_when_two_programs_share_a_name`) y
se conserva como salvaguarda explícita para cuando el paquete permita
representar múltiples versiones de un mismo programa en un mismo run.

## Bindings actual-formal (Fase 12): siempre posicionales

**Nunca** se asocia un argumento con un parámetro formal por semejanza de
nombres — únicamente por posición ordinal contra
`ProgramInterface.parameters` del callee resuelto:

| `ArgumentBindingStatus` | Cuándo |
|---|---|
| `RESOLVED_POSITIONAL` | Posición N del call site casa con el parámetro formal N, y este resuelve contra `LINKAGE`. |
| `ACTUAL_UNRESOLVED` | El argumento real no tiene forma estructural identificable (ni identificador, ni literal, ni `OMITTED`). |
| `AMBIGUOUS_ACTUAL` | El argumento real es un nombre simple homónimo dentro del propio caller (`data_items` + `linkage_data_items`), sin `qualified_name` que lo desambigüe. |
| `FORMAL_UNRESOLVED` | El callee no se resolvió (`callee_resolved=False`), o el parámetro formal N no resolvió contra `LINKAGE`. |
| `MISSING_ACTUAL` | El call site tiene menos argumentos reales que parámetros formales del callee resuelto. |
| `EXTRA_ACTUAL` | El call site tiene más argumentos reales que parámetros formales del callee resuelto. |

`RETURNING` se asocia de forma independiente (Fase 12, regla 10): el
mismo esquema `InterproceduralArgumentBinding`, pero nunca cuenta como un
argumento posicional más de `USING`.

## Flujo de datos potencial (`PotentialDataFlow`, Fase 10)

Describe lo que el `passing_mode` declarado **permite** contractualmente
— nunca lo que el callee realmente hace (su cuerpo **nunca** se analiza en
esta fase). Regla fija, única fuente de verdad en
`potential_flow_for_passing_mode()`:

| `passing_mode` | `potential_flow` |
|---|---|
| `BY REFERENCE` | `INPUT_OUTPUT` |
| `BY CONTENT` / `BY VALUE` | `INPUT_ONLY` |
| `RETURNING` | `OUTPUT_ONLY` (independiente de `passing_mode`, que no aplica a `RETURNING`) |
| `UNKNOWN` | `UNKNOWN` |

## Recursión y ciclos (Fase 13)

`ProgramCallEdge` agrega **todos** los `InterproceduralCallSite`
`RESOLVED_INTERNAL` entre el mismo par `(caller, callee)` — nunca expande
recursivamente cuerpos. `recursive=True` marca un self-loop
(`caller_program == callee_program`) tanto en el call site como en el
edge.

Los ciclos de 2+ programas (`A -> B -> A`, `A -> B -> C -> A`, ...) se
detectan sobre el call graph directo mediante **Tarjan iterativo**
(nunca recursivo: un ciclo real en un paquete grande produciría
`RecursionError` con una implementación recursiva). Un self-loop **nunca**
aparece como `ProgramCallCycle` (`programs: list[str]` exige mínimo 2
elementos distintos) — es exclusivamente `recursive=True` en el call
site/edge correspondiente.

**Semántica exacta de `ProgramCallCycle` (auditada explícitamente tras
la implementación inicial)**: el modelo representa **componentes
fuertemente conexas** (SCC) de tamaño ≥ 2, **nunca** una enumeración de
ciclos elementales/concretos. Esto tiene tres consecuencias directas,
verificadas con tests aislados
(`tests/pipeline/test_interprocedural_call_linkage_analyzer.py`,
sección "6.1 Auditoria de release engineering"):

1. `edge_ids` es el conjunto de **todas** las aristas internas a la SCC
   (`caller` y `callee` ambos miembros) — incluye deliberadamente el
   self-loop de un programa que se llama a sí mismo Y participa además
   de un ciclo de 2+ programas, aunque ese self-loop no "cierre" el
   ciclo por sí mismo. En ese caso, `part_of_cycle=True` también se
   propaga al self-edge/self-call-site correspondiente — por la misma
   regla de membresía a la SCC, no porque el self-loop sea
   conceptualmente parte del ciclo mínimo entre los otros programas.
2. Una SCC de 3+ programas con más aristas internas que las
   estrictamente necesarias para un único ciclo elemental (un "chord",
   p. ej. `A->B->C->A` más `B->A`) produce **un único**
   `ProgramCallCycle` con las 4 aristas — nunca se enumeran los
   múltiples ciclos elementales que esa SCC podría contener en teoría
   de grafos.
3. `cycle_count` cuenta **componentes fuertemente conexas de tamaño ≥
   2**, no "ciclos" en sentido estricto de teoría de grafos.
   `recursive_call_count` (self-calls) y `cycle_count` (SCCs) cuentan
   poblaciones disjuntas a nivel de summary y nunca se solapan como
   número, pero **sí** pueden coexistir sobre el mismo programa (un
   programa puede ser simultáneamente autorrecursivo y miembro de una
   SCC ≥2, como en el caso 1 de arriba) — ambos hechos siguen siendo
   reconstruibles sin ambigüedad de forma independiente: la recursión
   directa vía `recursive=True`, el ciclo de 2+ programas vía
   `cycles[].programs`. La única ambigüedad real está en `edge_ids`
   (no en `programs`): un lector no puede asumir que cada elemento de
   `edge_ids` participa del ciclo elemental mínimo sin cruzarlo también
   contra `recursive`/el conteo real de programas distintos.

`part_of_cycle=True` se propaga a cada call site/edge cuyo caller y
callee estén ambos dentro de la misma componente fuertemente conexa —
misma regla de membresía que `edge_ids`, nunca una regla distinta para
call sites/edges que para el objeto `ProgramCallCycle` en sí.

## `CALL` como barrera de propagación

Ver `docs/SEMANTIC_PROPAGATION.md`, sección "CALL como barrera
interprocedural", para el detalle completo de invalidación por
`passing_mode` (`BY REFERENCE` invalida, `BY CONTENT`/`BY VALUE` preserva,
`RETURNING` invalida al receptor) y los 4 casos obligatorios A-D
(`tests/pipeline/test_semantic_propagation_call_barrier.py`).

## Ausencia deliberada de propagación interprocedural

Ningún valor conocido en un programa se usa jamás para razonar sobre
otro programa:

- un argumento `BY REFERENCE`/`BY CONTENT`/`BY VALUE` **nunca** entrega
  su valor conocido (si lo hubiera) al análisis del callee — el cuerpo
  del callee nunca se examina desde este módulo;
- `RETURNING` **nunca** se puebla con un valor inventado — el efecto real
  del subprograma invocado es desconocido por definición;
- un `CALL` dinámico **nunca** resuelve su target usando el valor
  propagado del identificador;
- no hay evaluación simbólica interprocedural, ni side effects reales del
  subprograma, ni alias de memoria vía `REDEFINES`/`OCCURS` a través de
  una frontera de `CALL`.

Esto describe exclusivamente el comportamiento de **este** analizador
(`interprocedural_call_linkage_analyzer.py`), sin cambios en Fase 7. Un
módulo separado y puramente diagnóstico,
`docs/INTERPROCEDURAL_PROPAGATION.md`, consume el resultado ya calculado
de `InterproceduralCallLinkageArtifact` (resolución de programa, bindings,
call graph, SCC) como entrada de solo lectura para propagar, bajo
condiciones estrictas por `CallPassingMode`, valores literales
deterministas a través de un subconjunto de call sites elegibles — sin
modificar este artefacto ni este analizador.

## Ausencia de cambios a Neo4j/`SemanticGraph`/candidatos

`InterproceduralCallLinkageArtifact` es puramente diagnóstico
(`<run_dir>/diagnostics/interprocedural-call-linkage.json`), calculado
bajo demanda y **exclusivamente en memoria** salvo por su propia
persistencia. Nunca:

- escribe ni lee `artifacts/01-10` (ver Fase 20/21, no-regresión probada
  con comparación de árbol completo antes/después);
- modifica `SemanticGraph`, ninguna relación Neo4j, ni las consultas
  `queries/v1/*.cypher`;
- agrega candidatos `V1` (`Q0`) ni `V2` (shadow, ver
  `docs/V2_DETECTORS_SHADOW_MODE.md`) — un `CALL` nunca se convierte en un
  `RuleCandidate`/`V2ShadowCandidate` por sí mismo en esta fase;
- alimenta `ContextPackage`, `RuleDraft`, guardrails ni el ZIP final de
  reglas.

## CLI

```bash
python -m altamira_extractor.cli semantic-interprocedural <run_id>
python -m altamira_extractor.cli semantic-interprocedural <run_id> --json
```

Requiere únicamente que `<run_id>` haya alcanzado `PARSED` (`SUCCEEDED`):
es el primer punto del pipeline en que existe `artifacts/02-canonical/`,
el único artefacto de entrada. Calcula `SemanticEffectsArtifact` **en
memoria** (nunca lee ni escribe `diagnostics/semantic-effects.json`),
ejecuta el analizador puro, persiste
`diagnostics/interprocedural-call-linkage.json` de forma atómica, e
imprime un resumen legible (programas, interfaces, call sites, desglose
por `resolution_status`, bindings resueltos/no resueltos, llamadas
recursivas, ciclos) más la ruta relativa del reporte. `--json` imprime
además el artefacto completo. No se invoca automáticamente desde
`ingest`, `resume`, la API ni la UI.

## Determinismo

Todos los identificadores son derivados, nunca UUID/timestamp/`hash()`
de Python ni orden accidental de `dict`:

- `callsite::{program}::{paragraph}::{statement_id}`
- `binding::{call_site_id}::{ordinal|"returning"}`
- `edge::{caller}::{callee}`
- `cycle::{sha256(sorted(programs))[:16]}`

`interfaces`/`call_sites`/`call_edges`/`cycles` siempre se ordenan
deterministamente (por `program`/`call_site_id`/`edge_id`/`cycle_id`)
antes de construir el artefacto final — el orden de `canonical_programs`
recibido nunca afecta la salida. `to_stable_json()` (UTF-8, claves
ordenadas, formato legible, sin timestamps) garantiza bytes idénticos
entre ejecuciones sobre la misma entrada.

## Versionado (`schema_version`/`analyzer_version` 1.0; `canonical` hasta 1.3)

`InterproceduralCallLinkageArtifact.schema_version`/`analyzer_version`
son `"1.0"` (primera versión: no hay forma histórica previa que
preservar). `canonical_schema_versions` registra el conjunto ordenado y
sin duplicados de `CanonicalProgram.schema_version` realmente presentes
entre los programas analizados — un paquete puede mezclar `"1.0"`/`"1.1"`/
`"1.2"`/`"1.3"` según cada programa use o no nivel 88/`CALL`/`LINKAGE`/
`GOBACK`-`STOP RUN`-`EXIT PROGRAM` (Fase 7b, ver
`docs/INTERPROCEDURAL_PROPAGATION.md`).
`semantic_effects_schema_version`/`semantic_effects_analyzer_version`
registran la versión del `SemanticEffectsArtifact` calculado en memoria
que sirvió de entrada.

`CanonicalProgram.schema_version` sube a `"1.2"` (Fase 6) cuando el
parser Java detecta cualquier señal de `CALL`/`LINKAGE SECTION`/
`PROCEDURE DIVISION USING`/`RETURNING` realmente presente, y a `"1.3"`
(Fase 7b) en cuanto aparece algún `StatementKind.PROGRAM_TERMINATION`
(supersede a `"1.2"` igual que `"1.2"` supersede a `"1.1"`); se mantiene
en `"1.1"`/`"1.0"` para programas sin ninguna de esas construcciones (ver
Fase 21, no-regresión). Ambos campos nuevos con `@JsonInclude(NON_EMPTY)`
en Java garantizan que un programa sin `CALL`/`LINKAGE` produzca JSON
byte-idéntico al de antes de esta fase.

## Alcance explícitamente excluido

- `EXEC CICS` (`LINK`/`XCTL`, `COMMAREA`, `CHANNEL`/`CONTAINER`): fuera de
  alcance de toda la fundación interprocedural, no solo de `CALL`
  estándar.
- `ENTRY` adicional (múltiples puntos de entrada por programa).
- Llamadas mediante punteros (`PROCEDURE-POINTER`, `CALL identifier`
  a través de una variable de tipo puntero, más allá del caso simple ya
  cubierto de `CALL identifier`).
- Invocación real de programas: nunca se ejecuta código del paquete.
- Evaluación simbólica interprocedural o propagación de constantes entre
  programas.
- Side effects reales del subprograma invocado.
- Alias de memoria mediante `REDEFINES`/`OCCURS` a través de una
  frontera de `CALL`.
- `FILE SECTION`.
- Excepciones completas de `CALL`: solo se conserva un indicador
  estructurado de presencia de `ON EXCEPTION`/`NOT ON EXCEPTION`
  (`CALL_EXCEPTION_BRANCHES_NOT_MODELED`), nunca el control flow completo
  de esas ramas.
- Promoción a detectores V2: un `CALL` nunca genera automáticamente un
  candidato V2 shadow por el solo hecho de existir esta fundación.
- Cambios al grafo Neo4j: ver sección anterior.

## Limitaciones

- Fixtures Java (`InterproceduralCallLinkageExtractionTest.java`) cubren
  los constructos de mayor riesgo/valor (CALL literal con `USING`/
  `RETURNING` mixto, CALL dinámico, CALL a programa ausente, LINKAGE
  SECTION completa, `PROCEDURE DIVISION USING`/`RETURNING`) sobre 2
  fixtures dedicadas (`interprocedural-caller.cbl`/
  `interprocedural-callee.cbl`), no 20 fixtures separadas por
  construcción individual — decisión de alcance explícita, priorizando
  cobertura real sobre Java (ProLeap) frente a repetición de casos ya
  cubiertos exhaustivamente a nivel de contrato y analizador Python
  (`tests/contracts/test_interprocedural_call_linkage.py`,
  `tests/pipeline/test_interprocedural_call_linkage_analyzer.py`).
- `AMBIGUOUS_PROGRAM` está implementado y probado de forma aislada, pero
  actualmente inalcanzable end-to-end (ver sección "Resolución de
  programa").
- No hay resolución de `CALL` a través de `COPY`/`REPLACE` más allá de lo
  que ya resuelve el preprocesador estándar del parser.
- No detecta reglas ni candidatos: es exclusivamente diagnóstico, igual
  que `SemanticEffectsArtifact`/`SemanticPropagationArtifact`/
  `SemanticCoverageReport`.
