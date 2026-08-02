# Propagación interprocedural conservadora en shadow mode (Fase 7 de la ampliación semántica)

## Propósito

Propagar, de forma determinística y conservadora, valores literales
**entre programas** a través de fronteras `CALL` con target literal y
resolución interna (`RESOLVED_INTERNAL`), apoyándose exclusivamente en
hechos ya demostrados por fases previas:

- `SemanticPropagationArtifact` (Fase 4, `docs/SEMANTIC_PROPAGATION.md`):
  `PropagatedValueFact` intraprograma ya probados.
- `InterproceduralCallLinkageArtifact` (Fase 6,
  `docs/INTERPROCEDURAL_CALL_LINKAGE.md`): resolución de programa,
  bindings actual-formal posicionales, call graph, recursión/SCC.

Este módulo **nunca** reinterpreta `source_text`, **nunca** vuelve a
evaluar una asignación COBOL desde cero, y **nunca** infiere un valor a
partir de nombres, `PICTURE`, comentarios o heurísticas — solo reutiliza
hechos ya validados por los analizadores intraprograma existentes.

Es una capa **paralela y puramente diagnóstica**, igual que
`SemanticCoverageReport`/`SemanticEffectsArtifact`/
`SemanticPropagationArtifact`/`InterproceduralCallLinkageArtifact`: nunca
modifica `SemanticGraph`, Neo4j, `queries/v1/`, candidatos V1 (`Q0`) ni V2
(shadow), `ContextPackage`, `RuleDraft`, guardrails, ni el ZIP final de
reglas Markdown. No agrega un `PipelineStage`, no modifica `RunState`, no
se integra en `ingest`/`resume`.

## Relación con `docs/INTERPROCEDURAL_CALL_LINKAGE.md`

`docs/INTERPROCEDURAL_CALL_LINKAGE.md` describe (sección "Ausencia
deliberada de propagación interprocedural") que **su propio analizador**
(`interprocedural_call_linkage_analyzer.py`) nunca traslada un valor entre
programas — eso sigue siendo exactamente cierto y sin cambios: Fase 7 no
modifica ese analizador ni su artefacto. Lo que agrega esta fase es un
**módulo nuevo y separado**, `interprocedural_propagation_analyzer.py`,
que **consume** (como entrada de solo lectura) el resultado ya calculado
de `InterproceduralCallLinkageArtifact` — resolución de programa, bindings
posicionales, call graph, SCC — para decidir, con reglas explícitas por
`CallPassingMode`, cuándo un valor literal ya demostrado en el caller
puede describirse como conocido también en el callee (o viceversa al
retorno). La fundación CALL/LINKAGE de Fase 6 sigue siendo, en sí misma,
una barrera; Fase 7 es un análisis adicional que razona *a través* de esa
barrera bajo condiciones estrictas, sin alterarla.

De forma simétrica, `docs/SEMANTIC_PROPAGATION.md` (sección "CALL como
barrera interprocedural") sigue describiendo con exactitud el
comportamiento **intraprograma** de `semantic_propagation_analyzer.py`:
un `CALL` sigue invalidando `BY REFERENCE`/`RETURNING` y preservando `BY
CONTENT`/`BY VALUE` dentro del propio programa, exactamente igual que
antes de esta fase. Fase 7 no cambia ese comportamiento; solo lee sus
`PropagatedValueFact` ya producidos como una de sus dos fuentes de
valores conocidos (ver "Búsqueda de valor conocido" más abajo).

## Modelo semántico por modo de paso

Solo se analiza un `CALL` cuando el call site es **elegible** (ver
"Elegibilidad de call site"). Para cada argumento posicional del binding
resuelto (`ArgumentBindingStatus.RESOLVED_POSITIONAL`):

### Entrada al callee (`direction=CALLER_TO_CALLEE`, `kind=ENTRY_FACT`)

| `CallPassingMode` | Comportamiento |
|---|---|
| `BY CONTENT` | Si el actual tiene un literal conocido, se traslada al formal como hecho de entrada. Nunca hay alias: el callee nunca puede modificar el actual del caller a través de este argumento — no se genera ningún hecho de retorno para él. |
| `BY VALUE` | Idéntico a `BY CONTENT` en todo: mismo traslado de entrada, misma ausencia total de hecho de retorno. |
| `BY REFERENCE` | Si el actual tiene un literal conocido, se traslada al formal como hecho de entrada (diagnóstico `ENTRY_BY_REFERENCE_POTENTIALLY_MUTABLE` cuando `PROPAGATED`) — pero se trata como un **hecho de entrada**, nunca como una certeza que sobrevive intacta: el binding se marca potencialmente mutable, y el valor final tras el `CALL` se resuelve por separado (ver "Salida al caller"). |
| `UNKNOWN` | Nunca se propaga. Se registra un único hecho `BLOCKED` (`barrier=UNKNOWN_PASSING_MODE`) y no se genera ningún hecho de retorno. |

### Salida al caller (`direction=CALLEE_TO_CALLER`)

| Origen | Comportamiento |
|---|---|
| `RETURNING` (`kind=RETURNING_FACT`/`INVALIDATION`) | Solo cuando el call site tiene `returning_binding`. Si el callee tiene **certeza estructural** de que nunca retorna control al caller (terminador final `STOP_RUN`/`UNKNOWN`, ver "Terminadores de programa" abajo), el hecho es `status=BLOCKED`/`barrier=NON_RETURNING_TERMINATION` — nunca se intenta siquiera buscar un valor. En cualquier otro caso, se traslada el valor únicamente si el formal receptor (`entry_returning_data_item` del callee) termina, de forma demostrable y determinística, en un único literal — en cuyo caso `status=PROPAGATED`. Si no puede demostrarse (no tocado con valor distinto, tocado en más de una rama, invalidado, etc.), se invalida el receptor real en el caller (`status=INVALIDATED`, `kind=INVALIDATION`) — nunca se inventa el valor de retorno. |
| `BY REFERENCE` (`kind=BY_REFERENCE_OUTPUT`/`INVALIDATION`) | Misma regla de certeza estructural (`BLOCKED`/`NON_RETURNING_TERMINATION`) que `RETURNING`. En cualquier otro caso, se traslada el valor final únicamente si existe un único literal demostrable para el formal correspondiente al final del callee; si no puede demostrarse, se invalida el argumento real correspondiente en el caller. |
| `BY CONTENT` / `BY VALUE` | **Nunca** generan un hecho de salida — por definición no hay forma de que el callee modifique el argumento real del caller a través de estos modos. |

Nunca se infiere un valor a partir de nombres, `PICTURE`, comentarios ni
`source_text`: todo literal usado proviene de un `PropagatedValueFact`
(Fase 4) o de un `InterproceduralPropagationFact` de entrada ya
demostrado (Fase 7, propagación entre programas).

## Elegibilidad de call site y barreras

Un call site solo se analiza (genera hechos por argumento) cuando:

```text
target_kind == LITERAL
AND resolution_status == RESOLVED_INTERNAL
AND not recursive
AND not part_of_cycle
```

Si no se cumple, el call site completo queda bloqueado: no se genera
**ningún** hecho por argumento — solo se registra su `call_site_id` en
`blocked_call_sites` del `InterproceduralProgramAnalysis` del programa
caller, más un diagnóstico `BLOCKED_CALL_SITES_INCLUDE_<BARRIER>`. Orden
de prioridad para decidir el `InterproceduralPropagationBarrier`:

1. Target no literal → `DYNAMIC_CALL`.
2. `recursive=True` (self-call) → `RECURSION`.
3. `part_of_cycle=True` (miembro de una SCC de 2+ programas) → `CYCLE`.
4. `resolution_status=UNRESOLVED_MISSING_PROGRAM` → `MISSING_PROGRAM`.
5. `resolution_status=AMBIGUOUS_PROGRAM` → `AMBIGUOUS_PROGRAM`.
6. Cualquier otro estado no `RESOLVED_INTERNAL` → `DYNAMIC_CALL`
   (defensivo, inalcanzable en la práctica).

Para un call site elegible, cada binding individual puede aun así
bloquearse por su propio `ArgumentBindingStatus`:

| `ArgumentBindingStatus` | `InterproceduralPropagationBarrier` |
|---|---|
| `ACTUAL_UNRESOLVED` / `AMBIGUOUS_ACTUAL` | `UNRESOLVED_ACTUAL` |
| `FORMAL_UNRESOLVED` | `UNRESOLVED_FORMAL` |
| `MISSING_ACTUAL` | `MISSING_ARGUMENT` |
| `EXTRA_ACTUAL` | `EXTRA_ARGUMENT` |
| `UNSUPPORTED_ARGUMENT` | `UNSUPPORTED_CONTROL_FLOW` (defensivo) |

`NON_DETERMINISTIC_VALUE` y `NESTED_CALL_UNRESOLVED` están definidos en
el contrato para uso futuro/defensivo pero no son producidos por la
lógica actual del analizador (no hay evaluación de expresiones no
literales ni resolución de llamadas anidadas en esta fase).

`NON_RETURNING_TERMINATION` (Fase 7b) es distinta de las anteriores: no
bloquea un call site completo ni un binding de entrada, sino
específicamente un hecho de **salida** (`RETURNING_FACT`/
`BY_REFERENCE_OUTPUT`) cuando el callee tiene certeza estructural de que
nunca retorna control al caller — ver "Terminadores de programa".

## Orden topológico determinista (sin fixed point sobre ciclos)

`_topological_order` construye el orden de procesamiento con **Kahn**
sobre el subgrafo del call graph formado únicamente por `ProgramCallEdge`
con `not edge.recursive and not edge.part_of_cycle` — las aristas de
recursión/ciclo quedan fuera de toda restricción de orden. Empates
(varios nodos listos simultáneamente) se resuelven siempre eligiendo el
alfabéticamente menor, nunca el orden de llegada. Programas inalcanzables
por ninguna arista de esa forma (aislados, o exclusivamente dentro de una
SCC) se agregan al final en orden alfabético, como resguardo defensivo.

**No hay iteración hasta convergencia**: cada programa se procesa
exactamente una vez, en ese orden fijo. Un ciclo de 2+ programas nunca se
resuelve parcial ni progresivamente — sus call sites internos quedan
bloqueados por completo (`barrier=CYCLE`), sin excepción.

## Búsqueda de valor conocido (`_known_literal_at`)

Mecanismo central, de dos niveles, usado en cada punto donde el
analizador necesita "¿qué valor tiene esta variable en este momento del
programa?":

**Nivel 1 — intraprograma**: busca entre los `PropagatedValueFact` ya
producidos por `SemanticPropagationArtifact` (Fase 4) para
`(program, paragraph, variable)`, filtrando por:

- **Alcance de rama**: solo hechos cuyo `parent_statement_id` coincide
  exactamente con la rama inmediata del punto de consulta (o ambos son
  de nivel superior) — nunca se considera visible, dentro de una rama
  anidada, un valor establecido en una rama ancestro distinta. Es una
  simplificación deliberadamente conservadora: nunca produce un falso
  positivo, a costa de descartar algunos casos verdaderos.
- **Posición**: solo hechos con ordinal estrictamente anterior al punto
  de consulta (índice pre-order plano por programa, construido una vez).

Entre los hechos que superan ambos filtros, gana el de ordinal más alto
(el más reciente). Si su `fact_kind` es `DIRECT_LITERAL`,
`PROPAGATED_LITERAL` o `CONDITION_LITERAL` y tiene literal, ese literal es
el valor conocido. Cualquier otro `fact_kind`
(`INVALIDATED_VALUE`/`UNRESOLVED_COPY`/`BLOCKED_PROPAGATION`) significa
"sin literal, y detenerse ahí" — **nunca** cae al nivel 2 en ese caso: un
valor explícitamente invalidado o no resuelto en el propio programa nunca
se "rescata" acudiendo a un valor de entrada entre programas.

**Nivel 2 — interprocedural**: solo se consulta cuando el nivel 1 no
encontró **ningún** hecho aplicable (ninguna mutación conocida en el
programa hasta ese punto). Consulta `_EntryEnvironment`, un mapa por
programa de `qualified_name` formal/`LINKAGE` → literal, poblado
incrementalmente a medida que los programas se procesan en orden
topológico. Un valor solo se registra ahí cuando **todos** los
`ENTRY_FACT` elegibles que llegan a ese formal (posiblemente desde
múltiples callers distintos) coinciden exactamente en el mismo literal;
cualquier discrepancia suprime esa clave de forma permanente — nunca hay
unión de valores posibles, nunca hay fixed point — **pero nunca en
silencio**: `_EntryEnvironment.conflicted_keys(program)` expone
exactamente qué claves quedaron en conflicto, y
`analyze_interprocedural_propagation` emite un diagnóstico explícito
`MULTIPLE_CALLER_VALUES_FOR_<key_variable>` en el
`InterproceduralProgramAnalysis` del programa cuyo formal recibió valores
en conflicto. La discrepancia sigue siendo rastreable sin campos nuevos:
cada `ENTRY_FACT` `PROPAGATED` individual (uno por caller) conserva su
propio `literal`/`source_fact_ids`/`caller_program`, así que filtrar
`entry_facts` por `formal_name` reconstruye ambos valores en conflicto y
su procedencia exacta. Cualquier lectura posterior de esa clave vía nivel
2 (p. ej. un tercer programa al que el callee reenvía el formal sin
tocarlo) produce `UNRESOLVED` — nunca un literal inventado — y eso se ve
reflejado en `summary.counts_by_status`.

## Propagación actual → formal (entrada)

Para cada binding `RESOLVED_POSITIONAL` elegible, se consulta el valor
conocido del **actual** en el punto exacto del call site dentro del
caller (`_known_literal_at` sobre el programa/párrafo/ordinal del call
site). Si existe, se traslada al formal según la tabla de "Modelo
semántico por modo de paso"; si no existe, el hecho de entrada queda
`UNRESOLVED` (no es un bloqueo por barrera — simplemente no hay literal
que trasladar).

## Propagación formal → actual (RETURNING / BY REFERENCE)

Para `RETURNING` y para cada binding `BY REFERENCE`, se consulta el valor
final del **formal** correspondiente dentro del callee, mediante una
búsqueda de "salida" acotada exclusivamente al **primer párrafo** del
callee (`program.paragraphs[0]`) y con `parent_scope=None` (solo nivel
superior) — un valor establecido condicionalmente dentro de un `IF`/
`EVALUATE` nunca puede demostrarse determinístico al retorno, por diseño.
Si se demuestra un único literal, se traslada al actual/receptor real en
el caller (`status=PROPAGATED`); si no, se invalida (`status=INVALIDATED`,
`kind=INVALIDATION`).

### Terminadores de programa (`_effective_exit_cutoff`, Fase 7b)

`GOBACK`, `STOP RUN` y `EXIT PROGRAM` se capturan como
`StatementKind.PROGRAM_TERMINATION` con un `program_termination_kind`
(`GOBACK`/`EXIT_PROGRAM`/`STOP_RUN`/`UNKNOWN`) determinado
**exclusivamente** vía la API estructurada de ProLeap
(`StopStatementContext.RUN()`/`ExitStatementContext.PROGRAM()`, ambos
`TerminalNode` no nulos únicamente para esa forma exacta) — nunca
inspeccionando `source_text`. `STOP <literal>` (forma no-`RUN` de `STOP`)
y cualquier otra forma residual se clasifican `UNKNOWN`. Un `EXIT` simple
(sin `PROGRAM`) **nunca** es un terminador de programa — es un marcador
no-operativo (típicamente destino de un `GO TO`) y permanece
`kind=OTHER`, sin cambios respecto a antes de esta fase.

Estos tres statements tienen implicaciones de retorno-al-caller
radicalmente distintas según la semántica estándar de COBOL:

- **`GOBACK`/`EXIT PROGRAM`**: retornan control normalmente al caller
  cuando el programa fue invocado vía `CALL` desde otro programa del
  mismo paquete — un punto de retorno válido.
- **`STOP RUN`**: termina el run unit **completo**, incluso si el
  statement pertenece a un programa invocado vía `CALL` — **nunca**
  retorna control al caller. Afirmar un valor `PROPAGATED` a través de
  un `STOP RUN` describiría un estado que, en ejecución real, el caller
  jamás observa.

`_effective_exit_cutoff(statements)` decide, para el **primer párrafo**
del callee, si existe un único terminador final que califica como punto
de retorno:

```text
qualifies = (
    exactamente 1 statement con kind=PROGRAM_TERMINATION en toda la lista
    AND ese unico terminador es el ULTIMO statement de la lista
    AND parent_statement_id is None (top-level, incondicional)
)
```

- **No califica** (ausente, condicional — dentro de `IF`/`EVALUATE`/
  `PERFORM` inline —, no-final, o hay 2+ terminadores en cualquier
  posición): **nunca se recorta nada**, y **nunca se elige uno
  arbitrariamente** entre varios candidatos. La búsqueda de salida sigue
  con el corte sin modificar; si eso deja la última mutación conocida
  fuera de alcance o invalidada (típico: `_handle_unknown_effect` de
  Fase 4 ya invalidó el entorno), el resultado es el `INVALIDATED`
  existente — "bloqueo como no determinístico" por ausencia de prueba,
  nunca un `BLOCKED` estructural (esa barrera se reserva para certeza).
- **Califica y es `GOBACK`/`EXIT_PROGRAM`**: se recorta **exactamente**
  ese único statement del corte de búsqueda — nunca más (una sentencia
  `OTHER` genuinamente invalidante justo antes, p. ej. un `MOVE
  CORRESPONDING`, sigue invalidando con normalidad, ya que solo se
  recorta el terminador mismo).
- **Califica y es `STOP_RUN`/`UNKNOWN`**: certeza estructural de no
  retorno — el hecho de salida es `status=BLOCKED`,
  `barriers=[NON_RETURNING_TERMINATION]`, sin siquiera intentar la
  búsqueda de un literal.

Un patrón real con dos o más terminadores (p. ej. `IF X GOBACK ELSE
STOP RUN END-IF`, o un terminador seguido de código muerto) nunca elige
uno de los caminos posibles arbitrariamente — la condición
`exactamente 1 terminador en toda la lista` excluye automáticamente
cualquier escenario ambiguo, dejando el resultado en `INVALIDATED` (sin
evidencia de un único valor determinístico), nunca un `PROPAGATED`
inventado.

Este diseño (y el bug original — un `_effective_exit_ordinal` previo que
trataba CUALQUIER `OTHER` final como terminador seguro, sin distinguir
`STOP RUN` de `GOBACK`) fue descubierto y corregido mediante prueba de
integración real (JAR Java real, no fixtures sintéticas a mano) — ver
`tests/parser_integration/test_program_termination_propagation_integration.py`
(items reales: clasificación de los tres tipos, `RETURNING`/`BY
REFERENCE` con `GOBACK`/`EXIT PROGRAM` exitosos, `STOP RUN` bloqueado en
ambos casos, determinismo entre dos JVM independientes) y
`tests/pipeline/test_interprocedural_propagation_analyzer.py` (batería
completa de casos estructurales: condicional, no-final, múltiples
terminadores, anidado en `IF`/`EVALUATE`/`PERFORM` inline, invalidación
inmediatamente anterior).

## Identificadores deterministas

```text
fact::{call_site_id}::entry::{ordinal}
fact::{call_site_id}::return::returning
fact::{call_site_id}::return::{ordinal}
```

Nunca UUID, timestamp ni `hash()` de Python. `program_analyses`/`facts`/
`diagnostics` siempre se ordenan deterministamente (`program`/`fact_id`/
alfabético) antes de construir el artefacto final — el orden de entrada
de `canonical_programs` nunca afecta la salida. `to_stable_json()`
(UTF-8, claves ordenadas, formato legible, sin timestamps, sin rutas
absolutas, sin `source_text` completo) garantiza bytes idénticos entre
ejecuciones sobre la misma entrada.

## Contrato

`src/altamira_extractor/contracts/interprocedural_propagation.py`:

- `InterproceduralFactKind`: `ENTRY_FACT`, `RETURNING_FACT`,
  `BY_REFERENCE_OUTPUT`, `INVALIDATION`.
- `InterproceduralPropagationDirection`: `CALLER_TO_CALLEE`/
  `CALLEE_TO_CALLER`.
- `InterproceduralPropagationStatus`: `PROPAGATED`, `BLOCKED`,
  `INVALIDATED`, `UNRESOLVED`.
- `InterproceduralPropagationBarrier`: ver "Elegibilidad de call site y
  barreras".
- `InterproceduralPropagationFact`: un hecho puntual, validado para que
  `literal` solo exista con `status=PROPAGATED`, `barriers` solo con
  `status=BLOCKED`, y `kind`/`direction` sean mutuamente consistentes
  (`ENTRY_FACT` siempre `CALLER_TO_CALLEE`; `RETURNING_FACT`/
  `BY_REFERENCE_OUTPUT`/`INVALIDATION` siempre `CALLEE_TO_CALLER`).
- `InterproceduralProgramAnalysis`: resultado por programa-como-callee
  (`entry_facts`/`exit_facts`) más `blocked_call_sites` (call sites hechos
  DESDE ese programa, bloqueados a nivel completo).
- `InterproceduralPropagationSummary`: conteos agregados, validados contra
  el contenido real (partición call-site elegible/bloqueado, conteos por
  `status`/`barrier`/`kind`).
- `InterproceduralPropagationArtifact`: contenedor persistido, sin
  timestamps ni rutas absolutas; valida que `facts` sea exactamente la
  unión ordenada de `program_analyses[].entry_facts/exit_facts`, y que
  `summary` coincida exactamente con el contenido real.

Reutiliza `InterproceduralSourceReference` de `contracts/
interprocedural_call_linkage.py` (sin duplicar el modelo).

## Servicio y CLI

`src/altamira_extractor/pipeline/interprocedural_propagation_service.py`
localiza el run, carga `CanonicalProgram[]` desde `artifacts/02-canonical/`
(único artefacto de entrada leído de disco), calcula
`SemanticEffectsArtifact`/`SemanticPropagationArtifact`/
`InterproceduralCallLinkageArtifact` **en memoria** (nunca lee ni escribe
sus respectivos `diagnostics/*.json`), ejecuta el analizador puro, y
persiste **únicamente** `diagnostics/interprocedural-propagation.json` de
forma atómica (`atomic_write_json`). Requiere solo que el run haya
alcanzado `PARSED` (`SUCCEEDED`) — el primer punto del pipeline en que
existe `artifacts/02-canonical/`. Nunca modifica `run.json` ni ningún
`artifacts/01-10`.

```bash
python -m altamira_extractor.cli semantic-interprocedural-propagation <run_id>
python -m altamira_extractor.cli semantic-interprocedural-propagation <run_id> --json
```

Imprime `run_id`, `programs`, `call_sites`, `eligible_calls`,
`propagated_calls`, `blocked_calls`, `entry_facts`, `returning_facts`,
`by_reference_outputs`, `invalidations` y la ruta relativa del reporte;
`--json` imprime además el artefacto completo en JSON estable. No se
invoca automáticamente desde `ingest`, `resume`, la API ni la UI.

## Versionado

`schema_version`/`analyzer_version` son `"1.0"` (sin cambios en Fase 7b:
el `NON_RETURNING_TERMINATION` nuevo es un valor adicional dentro de un
enum ya existente, `InterproceduralPropagationBarrier`, no un campo
nuevo — la FORMA del contrato no cambió). `interprocedural_analysis_
schema_version`/`interprocedural_analysis_analyzer_version` registran la
versión de `InterproceduralCallLinkageArtifact` calculado en memoria (sin
cambios: Fase 7b no toca ese analizador); `semantic_effects_schema_
version` acepta `"1.0"`/`"1.1"`/`"1.2"` (sin cambios: `PROGRAM_
TERMINATION` reutiliza `SemanticEffectKind.PRESERVED_STATEMENT`, ya
existente, sin nuevo campo ni bump); `semantic_propagation_schema_
version` acepta `"1.0"`/`"1.1"` (sin cambios: `semantic_propagation_
analyzer.py` no se modificó, `PRESERVED_STATEMENT` ya despachaba a
`_handle_unknown_effect` desde antes de esta fase). Los tres siguen
siendo la única procedencia disponible, ya que ninguno de esos artefactos
se lee de disco.

`CanonicalProgram.schema_version` (entrada, no de este artefacto) sube a
`"1.3"` cuando el parser detecta algún `StatementKind.PROGRAM_
TERMINATION` (Fase 7b, ver `docs/LEVEL_88_SUPPORT.md`/
`docs/INTERPROCEDURAL_CALL_LINKAGE.md` para el resto de la escala) — la
inmensa mayoría de programas COBOL reales terminan en `GOBACK`/`STOP
RUN`, así que `"1.3"` es, en la práctica, la versión típica de un
programa completo.

## Compatibilidad canónica frente a v1.5.0

Los programas con `GOBACK`, `STOP RUN` o `EXIT PROGRAM` **ya no son byte a
byte idénticos** al canónico que producía v1.5.0 — esto es intencional y
esperado, no una regresión. Para esos programas, el cambio está
**limitado exactamente** a:

- `StatementKind.OTHER` → `StatementKind.PROGRAM_TERMINATION` (solo para
  el/los statement(s) reclasificado(s), nunca para el resto);
- `program_termination_kind` poblado (antes ni existía el campo);
- `schema_version` del programa → `"1.3"`;
- omisión de campos vacíos conforme a `@JsonInclude(NON_EMPTY)`, igual
  que en toda extensión aditiva previa (Fase 3/6);
- `unsupported_constructs` se reduce: el statement reclasificado ya no
  aparece ahí (antes, `convertOther` reportaba cada `GOBACK`/`STOP RUN`/
  `EXIT PROGRAM` como "no decodificado estructuralmente"; ahora estos SÍ
  están decodificados, así que la entrada correspondiente desaparece).
  Esta última diferencia no fue enumerada explícitamente en la
  especificación original, pero es una consecuencia estructural
  inevitable de la reclasificación — documentada aquí por transparencia.

**Nunca cambia**: `paragraph`, `ordinal` (posición dentro del párrafo),
`branch_kind`, `source reference` (`source_file`/`line_start`/
`line_end`/`location_kind`), `variables_read`, `variables_written`, ni
el orden/cardinalidad de statements. La referencia `parent_statement_id`
de cualquier hijo de un terminador reclasificado sigue apuntando a la
**misma posición** (nunca a un ID inventado ni a `null`).

### `statement_id`: el sufijo cambia, el prefijo posicional no

`statement_id` sigue el formato preexistente (anterior a Fase 7b, sin
cambios en esta fase) `<program>::<paragraph>::<ordinal>::<kind>`
(`StatementExtractor.nextId`, lado Java) — el sufijo de `kind` **siempre**
formó parte del ID, para cualquier `StatementKind`. Reclasificar
`OTHER`→`PROGRAM_TERMINATION` cambia inevitablemente ese sufijo (p. ej.
`P::PARA::5::OTHER` → `P::PARA::5::PROGRAM_TERMINATION`), pero el
**prefijo posicional** `<program>::<paragraph>::<ordinal>` — la
identidad real de "qué statement es este", independiente de su
clasificación — permanece **exactamente igual**. Todo consumidor
downstream que necesite correlacionar un statement entre la forma
antigua y la nueva debe hacerlo por esa identidad posicional, nunca por
igualdad textual completa del `statement_id`.

Auditado explícitamente (ver `tests/contracts/test_canonical_program.py`,
sección "Forma anterior vs forma nueva"): el prefijo es idéntico, solo el
sufijo/`kind`/`program_termination_kind` cambian, ningún otro campo
estructural se ve afectado, el orden y la cardinalidad de statements no
cambian, y el mapping posicional antiguo→nuevo es biunívoco. Confirmado
también con el JAR real contra un baseline v1.5.0 aislado (worktree
`git worktree add --detach <tmp> v1.5.0`), sobre 12 programas reales
(incluyendo Catherine original/corregido y los paquetes multiprograma de
Fase 6/7): **cero diferencias inesperadas** — los únicos programas que
SÍ conservan bytes idénticos son los que no contienen ningún
`GOBACK`/`STOP RUN`/`EXIT PROGRAM` en absoluto.

**Deuda arquitectónica registrada, no resuelta en esta fase**:
`statement_id` incorpora una clasificación semántica mutable (`kind`);
evaluar desacoplar identidad posicional y `kind` en una fase específica
de migración de IDs. Un cambio así afectaría los 11 `StatementKind`
existentes (no solo `PROGRAM_TERMINATION`) y requeriría su propia
auditoría de compatibilidad — fuera de alcance de este cierre.

### Downstream: solo hashes derivados de `run_id`, nunca contenido funcional

Confirmado con corridas reales completas (PROGRULE1, Catherine original,
Catherine corregido) comparando el código base v1.5.0 contra el actual,
mismo ZIP de entrada: `artifacts/03-dependencies.json` a
`10-rules/` y `diagnostics/v2-candidates-shadow.json` son idénticos en
**todo** su contenido funcional (nodos/relaciones del grafo, candidate
IDs, context packages, reglas, comparaciones MATCHED/V1_ONLY/V2_ONLY/
RELATED_NOT_EQUIVALENT) — las únicas diferencias observadas son
`run_id` y hashes que lo incorporan transitivamente (`candidate_artifact_
hash`, `context_manifest_hash`, `*_manifest_hash`, `source_artifact_
hashes`) o un timestamp de ejecución (`guardrail_report.evaluated_at`),
todos legítimamente distintos entre dos corridas separadas. Ningún
`PROGRAM_TERMINATION` genera un candidato V1/V2 nuevo, un nodo/relación
nuevo en `SemanticGraph`, ni una regla nueva — `dependency_builder.py`/
`semantic_graph_builder.py`/`v2_detectors.py` nunca consultan
`StatementKind.PROGRAM_TERMINATION` (mismo comportamiento que con
`OTHER` antes de esta fase: ninguno de los dos kinds coincide con las
comprobaciones específicas de esos módulos, que solo reconocen
`IF`/`EVALUATE`/`GO_TO`/`PERFORM`/`SET`).

## Alcance explícitamente excluido

- Fixed point sobre ciclos: un SCC de 2+ programas (o un self-call)
  bloquea por completo los call sites involucrados, sin excepción y sin
  iteración parcial.
- Evaluación simbólica de expresiones no literales, aritmética, o
  cualquier forma de ejecución/interpretación del programa.
- Inferencia de valores a partir de nombres, `PICTURE`, comentarios o
  `source_text`.
- Resolución de `CALL` dinámico usando un valor propagado del
  identificador target (sigue siendo responsabilidad exclusiva —y
  ausente— de Fase 6, ver `docs/INTERPROCEDURAL_CALL_LINKAGE.md`).
- `EXEC CICS` (`LINK`/`XCTL`), llamadas mediante punteros, `ENTRY`
  adicional, `FILE SECTION`: mismos límites heredados de Fase 6.
- Promoción a candidatos V1/V2, `ContextPackage`, `RuleDraft`,
  guardrails o reglas: es exclusivamente diagnóstico.
- Cambios a `SemanticGraph`/Neo4j/`queries/v1/`.

## Limitaciones

- El nivel 1 de búsqueda de valor conocido nunca considera visible, en
  una rama anidada, un valor establecido en una rama ancestro distinta
  (ver "Búsqueda de valor conocido") — simplificación deliberadamente
  conservadora, puede descartar casos verdaderos pero nunca produce un
  falso positivo.
- La búsqueda de valor de salida (RETURNING/BY REFERENCE) se limita al
  primer párrafo del callee y a nivel superior únicamente: un valor
  establecido de forma incondicional pero fuera del primer párrafo, o
  dentro de una rama, nunca se demuestra determinístico en esta fase,
  aunque en la práctica lo sea.
- El entorno de entrada interprocedural (`_EntryEnvironment`) usa
  consenso estricto entre múltiples callers: basta un único caller con un
  valor distinto (o sin valor conocido en absoluto para un argumento en
  esa posición) para suprimir permanentemente esa clave — no hay unión
  de valores posibles. A diferencia de versiones anteriores de esta
  fase, el conflicto nunca es silencioso (diagnóstico explícito
  `MULTIPLE_CALLER_VALUES_FOR_<key_variable>`, ver "Búsqueda de valor
  conocido"), pero sigue sin haber marcado estructural de "múltiples
  valores conocidos" en el propio `InterproceduralPropagationFact` — solo
  en `InterproceduralProgramAnalysis.diagnostics`.
- `_effective_exit_cutoff` recorta como máximo un único terminador final
  (`GOBACK`/`EXIT PROGRAM`, nunca `STOP RUN`) — ver "Terminadores de
  programa". Un patrón real con una sentencia `OTHER` genuinamente
  invalidante justo antes de ese terminador (por ejemplo, un `MOVE
  CORRESPONDING`) sigue invalidando el entorno completo, incluso si esa
  sentencia intermedia no toca la variable de interés — decisión
  deliberada: es más seguro reportar `INVALIDATED`/`UNRESOLVED` de más
  que `PROPAGATED` de menos.
- `STOP <literal>` (forma no-`RUN` de `STOP`) se clasifica
  `program_termination_kind=UNKNOWN`, con el mismo efecto de bloqueo que
  `STOP_RUN` (`NON_RETURNING_TERMINATION`) — nunca se asume equivalente
  a `GOBACK`/`EXIT PROGRAM` sin evidencia estructural de que retorne.
- Un `EXIT` simple (sin `PROGRAM`) nunca se trata como terminador de
  programa — permanece `kind=OTHER`, idéntico a su comportamiento antes
  de esta fase (es un marcador no-operativo en COBOL, no un mecanismo de
  retorno).
- `AMBIGUOUS_PROGRAM` es, igual que en Fase 6, inalcanzable end-to-end a
  través del flujo real del pipeline (ver
  `docs/INTERPROCEDURAL_CALL_LINKAGE.md`, sección "Resolución de
  programa") — la lógica de bloqueo correspondiente se prueba de forma
  aislada.
- No hay resolución de `CALL` a través de `COPY`/`REPLACE` más allá de lo
  que ya resuelve el preprocesador estándar del parser.
- No detecta reglas ni candidatos: es exclusivamente diagnóstico, igual
  que el resto de artefactos de la ampliación semántica.
