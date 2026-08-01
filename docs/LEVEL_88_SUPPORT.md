# Soporte nativo de condiciones nivel 88 (Fase 3 de la ampliación semántica)

## Qué es una condition-name nivel 88

En COBOL, una entrada de nivel 88 no declara una variable propia: declara un
**nombre de condición** ligado al valor actual del data item que la precede
inmediatamente en la DATA DIVISION (su "padre"). Por ejemplo:

```cobol
01 WS-COD-RETORNO PIC X(4).
   88 COD-OPERACION-VALIDA VALUE '0000'.
   88 COD-CAMPO-INVALIDO VALUE '0005'.
   88 COD-RANGO-ERROR VALUE '0010' THRU '0019'.
```

`COD-CAMPO-INVALIDO` es verdadero exactamente cuando `WS-COD-RETORNO` vale
`'0005'`. `SET COD-CAMPO-INVALIDO TO TRUE` es la forma idiomática de escribir
`'0005'` en `WS-COD-RETORNO` sin repetir el literal; `IF COD-CAMPO-INVALIDO`
es la forma idiomática de comparar `WS-COD-RETORNO` contra ese mismo valor.

Antes de esta fase, el parser conservaba la condición como un
`CanonicalDataItem` genérico (`level=88`, sin `pic`, sin `usage`) y `SET
condición TO TRUE` se veía indistinguible de cualquier otro `SET`: el
`assigned_literal` capturado era el texto crudo `"true"`/`"false"`, nunca el
valor real de la condición. Esta fase agrega captura **nativa y trazable**
de esa semántica, sin reescribir el COBOL como `MOVE` y sin inventar nada
que ProLeap no pueda demostrar.

## Qué información captura el parser

### Padre y VALUE (`CanonicalProgram.condition_names`)

Cada condición nivel 88 con evidencia completa aparece en una lista nueva y
separada, `CanonicalProgram.condition_names: list[CanonicalConditionName]`:

```json
{
  "name": "COD-CAMPO-INVALIDO",
  "qualified_name": "WS-COD-RETORNO.COD-CAMPO-INVALIDO",
  "parent_name": "WS-COD-RETORNO",
  "parent_qualified_name": "WS-COD-RETORNO",
  "values": [{"value": "0005", "through_value": null, "location_kind": "EXACT", ...}],
  "location_kind": "EXACT", ...
}
```

`parent_name`/`parent_qualified_name` se resuelven vía
`DataDescriptionEntry.getParentDataDescriptionEntryGroup()` de ProLeap —
verificado empíricamente contra un programa de prueba controlado: para una
condición nivel 88 este método devuelve el data item padre inmediato
(elemental o grupo), nunca un ancestro de grupo más lejano. Nunca se infiere
por coincidencia de texto ni por posición relativa en el archivo.

### Múltiples VALUE

```cobol
88 COD-MULTI-VALOR VALUE '01' '02' '03'.
```

produce **tres** entradas independientes en `values`, cada una con
`through_value=null` — verificado empíricamente que ProLeap expone cada
literal de una cláusula VALUE multi-valor como un `ValueInterval` propio
dentro de `ValueClause.getValueIntervals()`.

### THRU

```cobol
88 COD-RANGO-ERROR VALUE '0010' THRU '0019'.
```

produce una única entrada `{"value": "0010", "through_value": "0019"}`.
`through_value` es `null` para un VALUE simple y solo se declara cuando
ProLeap expone un intervalo THRU real (`ValueInterval.getToValueStmt() !=
null`) — nunca inferido.

### Condición sin padre o sin VALUE demostrable

Si ProLeap no puede resolver el padre o ningún VALUE de una condición, esa
condición **se omite** de `condition_names` (nunca se inventa) y se agrega
una entrada a `CanonicalProgram.unsupported_constructs` describiendo el
motivo puntual. `CanonicalConditionName.values` nunca está vacía por
contrato: si no hay evidencia demostrable, la condición simplemente no
aparece.

## `SET condición TO TRUE`/`TO FALSE`

Verificado empíricamente (no adivinado) contra un programa de prueba
controlado ejecutando el parser real: ProLeap representa
`SET condición TO TRUE`/`TO FALSE` con el mismo `SetStatement`/`SetTo`
genérico que cualquier otro `SET`, pero el valor del lado derecho es un
`LiteralValueStmt` genérico cuyo texto crudo es literalmente `"TRUE"` o
`"FALSE"` (respetando el case del código fuente) — no existe un
`ValueStmt`/`ValueType` booleano dedicado alcanzable desde esta forma
gramatical en esta versión de ProLeap.

El parser Java detecta esta forma exclusivamente cuando: (1) hay un único
`TO` y un único valor; (2) ese valor, comparado sin distinguir mayúsculas,
es exactamente `TRUE` o `FALSE`; (3) el target resuelve, por nombre simple,
contra **exactamente una** condición conocida en `CanonicalProgram.
condition_names`. Cuando las tres condiciones se cumplen,
`CanonicalStatement` gana dos campos:

- `condition_name_target: str | None` — el nombre de la condición.
- `condition_set_value: bool | None` — `true`/`false` según TO TRUE/FALSE.

Ambos siempre están presentes juntos o ausentes juntos (validador de
contrato). `variables_read`/`variables_written`/`target_data_items`/
`assigned_literal` se conservan exactamente como antes: **nunca se
convierte el SET en un MOVE**, solo se agrega información estructural
adicional.

`SET FALSE` está soportado de forma simétrica a `TRUE`: se verificó
empíricamente que la gramática de ProLeap 2.4.0 lo expone de la misma
forma. Si la resolución falla (p. ej. nombre ambiguo entre condiciones bajo
padres distintos — ver "Homónimos" más abajo), ambos campos quedan en
`None` y el `SET` se trata como antes.

### SET ordinario y SET de índice nunca se confunden

`SET WS-INDICE TO 1` o `SET WS-IDX TO 5` nunca activan esta detección: el
valor del lado derecho no es literalmente `TRUE`/`FALSE`, así que
`condition_name_target`/`condition_set_value` permanecen `None` sin
importar el nombre del target.

## Referencias desde IF y EVALUATE

`CanonicalStatement.referenced_condition_names: list[str]` (ordenado, sin
duplicados) registra condition-names referenciados de forma **directa y
verificable**:

- **IF**: verificado empíricamente que `IfStatement.getCondition()` expone
  un árbol estructurado (`ConditionValueStmt` → `CombinableCondition`/
  `AndOrCondition` → `SimpleCondition` → `ConditionNameReference`) donde una
  referencia a condition-name es un tipo de nodo **distinto** de una
  comparación (`RelationConditionValueStmt`) — la gramática de ProLeap ya
  desambigua esto en tiempo de parseo. Se camina ese árbol (código nuevo,
  deliberadamente separado de `ValueReferences.java`, que alimenta
  `operands`/`variables_read`/`expression` y cuyo comportamiento no se
  toca) y cada nombre resuelto se verifica contra `condition_names` antes
  de incluirse. Para condiciones compuestas (`IF A AND B`) se conservan
  todas las referencias verificables encontradas en el árbol AND/OR, sin
  reconstruir precedencia u operadores.
- **EVALUATE**: verificado empíricamente que `WHEN condición` **no** usa
  el mismo árbol que IF — ProLeap lo expone como un `CallValueStmt`
  genérico (un identificador cualquiera), estructuralmente idéntico a
  `WHEN cualquier-otra-variable`. La única forma de confirmar que ese
  identificador es realmente una condición nivel 88 es el nombre
  coincidiendo con `condition_names`; por eso la referencia se adjunta al
  primer statement hijo de la rama `WHEN` (mismo mecanismo que ya usa
  `branch_condition`), nunca inventada.

**Nunca se infiere que toda variable leída en un IF/EVALUATE sea una
condición 88**: la verificación contra `condition_names` es obligatoria en
ambos casos, y `referenced_condition_names` nunca se deriva de
`operands`/`variables_read` (que no distinguen condition-names de
variables ordinarias).

## Limitaciones (deliberadas, de esta implementación)

- **Sin propagación.** Un `SemanticEffect` describe exclusivamente la
  sentencia que lo originó. `SET COD-X TO TRUE` seguido de `MOVE
  WS-COD-AUX TO WS-COD-RETORNO` produce dos efectos independientes; el
  valor `'0005'` de la condición nunca "viaja" hacia el segundo efecto.
- **Homónimos sin calificación IN/OF.** Si el mismo nombre simple de
  condición aparece bajo padres distintos (`88 COD-X` bajo dos grupos
  diferentes), el parser V1 no lo resuelve por nombre simple:
  `condition_name_target`/`referenced_condition_names` quedan sin poblar
  para esas referencias ambiguas en vez de adivinar a cuál padre
  pertenecen. `ConditionNameReference` expone calificación `IN`/`OF`
  estructuralmente, pero esta versión no la usa para desambiguar.
  `CanonicalProgram.condition_names` sí acepta homónimos bajo padres
  distintos (se distinguen por `qualified_name`, único por contrato).
- **No hay candidatos V2.** Esta fase captura y normaliza semántica
  únicamente; ni `queries/v1/q0_candidates.cypher` ni
  `candidate_detector.py` se modificaron. Un programa que usa
  exclusivamente `SET condición TO TRUE` (nunca `MOVE literal a
  WS-COD-RETORNO`) sigue produciendo **cero candidatos Q0**, exactamente
  igual que antes de esta fase — la detección de candidatos basada en
  condition-names queda fuera de alcance.
- **CALL, EXEC CICS, LINKAGE SECTION, REDEFINES, OCCURS** siguen sin
  interpretarse; nada de esta fase los toca.
- **No se interpretan las condiciones en sí** más allá de identificar la
  referencia: no se reconstruye qué hace verdadera o falsa a una
  condición compuesta, no se evalúan rangos THRU, no se resuelven
  figurative constants dentro de VALUE.

## Compatibilidad histórica

`CanonicalProgram.condition_names` y los tres campos nuevos de
`CanonicalStatement` (`condition_name_target`, `condition_set_value`,
`referenced_condition_names`) son opcionales/con valor por defecto: un
`CanonicalProgram` JSON histórico sin esas claves carga sin cambios
(`condition_names=[]`, `condition_name_target=None`,
`condition_set_value=None`, `referenced_condition_names=[]`).
`schema_version` permanece en `"1.0"` — la forma del contrato no requirió
un cambio incompatible, solo campos aditivos con default seguro.

Para programas **sin** ninguna condición nivel 88, `02-canonical/` gana
únicamente la clave `"condition_names": []` (una vez por programa, no una
vez por data item — ver diseño en el código) respecto al comportamiento
previo a esta fase; ningún artefacto aguas abajo (`03-dependencies.json` en
adelante) cambia, porque `dependency_builder.py`, `semantic_graph_builder.py`
y `candidate_detector.py` nunca se modificaron y nunca leen los campos
nuevos.

## Relación con SemanticCoverage y SemanticEffects

- **`SemanticEffectsArtifact`** (`docs/SEMANTIC_EFFECTS.md`) gana dos
  tipos nuevos: `SET_CONDITION_TRUE`/`SET_CONDITION_FALSE`. Se producen
  únicamente cuando `condition_name_target`/`condition_set_value` ya
  fueron resueltos por el parser Java; siempre `FULLY_SUPPORTED` cuando
  aparecen (condición, padre y al menos un VALUE ya están garantizados
  por construcción). Para una condición con exactamente un VALUE literal
  simple, el efecto incluye ese valor normalizado en `literal` — el
  `kind` sigue siendo `SET_CONDITION_TRUE`/`FALSE`, nunca se reclasifica
  como `ASSIGN_LITERAL`. Condiciones con múltiples VALUE o rango THRU
  agregan `diagnostic_code=CONDITION_HAS_MULTIPLE_OR_RANGE_VALUES` y
  dejan `literal=None` (el detalle completo queda en
  `condition_values`). SET ordinario, sin resolver, sigue siendo
  `SET_VALUE`.
- **`SemanticCoverageReport`** (`docs/SEMANTIC_COVERAGE.md`,
  `analyzer_version` subió a `"1.1"`, `schema_version` sin cambios)
  distingue ahora: condición 88 totalmente modelada
  (`LEVEL_88_CONDITION_NAME_MODELED`/`LEVEL_88_CONDITION_FULLY_MODELED`,
  `FULLY_SUPPORTED`) vs. condición 88 preservada pero no interpretable
  (`LEVEL_88_CONDITION_NAME`/`LEVEL_88_SEMANTICS_NOT_MODELED`, sin
  cambios, reservado para el residual sin padre/VALOR demostrable); `SET`
  resuelto contra una condición (`SET_CONDITION_RESOLVED`,
  `FULLY_SUPPORTED`) vs. `SET` ambiguo (`SET_TARGET_KIND_AMBIGUOUS`, sin
  cambios); y una dimensión nueva e independiente,
  `CONDITION_NAME_REFERENCE_RESOLVED`, para cada IF/EVALUATE con al menos
  una referencia verificada.
- **`SemanticPropagationArtifact`** (Fase 4, `docs/SEMANTIC_PROPAGATION.md`)
  usa `SET_CONDITION_TRUE` con `literal` poblado (condición con exactamente
  un VALUE, sin THRU) para demostrar que el data item padre recibe ese
  valor (`CONDITION_LITERAL`), y esa conclusión puede propagarse a un
  `MOVE` posterior del mismo padre dentro del mismo paragraph
  (`PROPAGATED_LITERAL`). `SET_CONDITION_TRUE` con múltiples VALUE/THRU
  bloquea la propagación (`BLOCKED_PROPAGATION`) en vez de elegir un
  valor del conjunto. `SET_CONDITION_FALSE` nunca produce un
  `CONDITION_LITERAL`. Esta capa es enteramente opcional y posterior:
  nunca modifica `condition_names`, `SET_CONDITION_TRUE`/`FALSE`, ni
  ningún campo de `CanonicalProgram`.
- **`V2ShadowCandidatesArtifact`** (Fase 5, `docs/
  V2_DETECTORS_SHADOW_MODE.md`, exclusivamente diagnóstico y bajo
  demanda) agrega el detector `V2_LEVEL_88_RETURN_CODE`, que consume el
  mismo `CONDITION_LITERAL` para proponer un candidato experimental
  cuando el padre de la condición es un `DataItem` con
  `semantic_tag=return_code`. Nunca modifica `condition_names`,
  `SemanticEffectsArtifact` ni `SemanticPropagationArtifact`, y sus
  candidatos nunca alimentan `CandidateArtifact` V1 ni la generación de
  reglas.

## Uso de Catherine como golden fixture

`examples/PAQUETE_SINTETICO_CATHERINE.zip` (preservado byte a byte) es el
golden fixture principal: un programa CICS/DB2 real con 20 condiciones
nivel 88 bajo 8 padres distintos, 33 sentencias `SET condición TO TRUE` (sin
`SET ... TO FALSE`), y referencias `IF`/`EVALUATE` directas. Confirma en
`tests/parser_integration/test_level_88_catherine_integration.py`
(marcado `integration`, requiere JAR + Neo4j reales) que: el ZIP valida y
el parser termina correctamente; las 20 condiciones aparecen con padre y
VALUE correctos; las 33 SET se resuelven a `SET_CONDITION_TRUE`; hay
referencias IF/EVALUATE verificadas; `SemanticEffectsArtifact` contiene los
`SET_CONDITION_TRUE` esperados; candidatos y reglas V1 permanecen en cero
(patrón esperado, ver más abajo).

`examples/PAQUETE_SINTETICO_CATHERINE_CORREGIDO_APP_ACTUAL.zip` es el
fixture de compatibilidad del workaround basado en `MOVE`: mismo programa
reescrito para reemplazar `SET condición TO TRUE`/`IF condición` por `MOVE
literal a WS-COD-RETORNO`/`IF WS-COD-RETORNO = 'literal'`, conservando (sin
usar) las declaraciones nivel 88 originales en DATA DIVISION. El mismo test
de integración confirma que: sigue procesándose; `condition_names` sigue
capturando las 20 declaraciones (aunque no se referencian); ningún `SET`
del corregido resuelve `condition_name_target` (no se reinterpretan sus
`MOVE` como condiciones); los 15 `MOVE` literales a `WS-COD-RETORNO` siguen
generando `ASSIGN_LITERAL`; y Q0 sigue detectando 14 candidatos (cifra
confirmada por `PAQUETE_SINTETICO_CATHERINE_CORREGIDO_VALIDACION.txt` y
verificada empíricamente contra el pipeline real).

`examples/cobol1.jcl` se auditó y resultó ser una copia byte-idéntica del
COBOL ya incluido en `PAQUETE_SINTETICO_CATHERINE.zip` (no es JCL real, no
tiene sintaxis `//JOB`/`//EXEC`): no aporta información nueva, así que no
se incorporó a ningún test.

## Construcciones todavía no soportadas

- `CALL`, `EXEC CICS`, `LINKAGE SECTION`, `REDEFINES`, `OCCURS`.
- Calificación `IN`/`OF` para desambiguar condition-names homónimos.
- Candidatos V2 basados en `SET condición TO TRUE`/referencias
  IF/EVALUATE a condition-names (Q0 sigue siendo exclusivamente el patrón
  `Decision -[:LEADS_TO]-> DataItem{semantic_tag:'return_code'}`).
- Evaluación de expresiones booleanas compuestas (`IF A AND B`): se
  conservan las referencias individuales verificables, nunca la
  estructura AND/OR completa ni su resultado.
