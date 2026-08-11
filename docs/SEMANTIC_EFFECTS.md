# Efectos semánticos normalizados (Fase 2 de la ampliación semántica)

## Propósito

Este documento describe `SemanticEffectsArtifact`, un artefacto **diagnóstico**
que traduce construcciones COBOL ya presentes en `CanonicalProgram` a un
modelo normalizado de efectos: cada `MOVE`, `SET`, `COMPUTE`, `GO TO`,
`PERFORM` o `EXEC SQL` interpretado se convierte en uno o más `SemanticEffect`
tipados (`ASSIGN_LITERAL`, `COPY_VALUE`, `COMPUTE_VALUE`, `SET_VALUE`,
`CONTROL_TRANSFER`, `EXECUTE_SQL`), o se declara explícitamente conservado
(`PRESERVED_STATEMENT`) o no soportado (`UNSUPPORTED_STATEMENT`) cuando no hay
suficiente evidencia para normalizarlo.

Corresponde a la Fase 2 de la hoja de ruta de ampliación semántica
(`feat/semantic-effects-foundation`), y es **paralelo e independiente** de
`SemanticCoverageReport` (Fase 1, ver `docs/SEMANTIC_COVERAGE.md`):
`semantic-coverage` mide **cuánto** de cada programa recibió interpretación
estructurada; `semantic-effects` expresa **qué** se interpretó, como un efecto
normalizado con procedencia hasta el `CanonicalStatement` que lo originó.
Ambos comparten únicamente `SemanticSupportStatus` (import de una sola
dirección, `semantic_effects.py -> semantic_coverage.py`; nunca al revés — sin
acoplamiento circular), porque la noción de "cuán completa es la
interpretación" es idéntica en ambos casos, no porque compartan ningún otro
aspecto de su modelo.

## Carácter diagnóstico, no contractual

`SemanticEffectsArtifact` **no es** un artefacto `artifacts/01-10`. No
modifica `SemanticGraph`, no crea relaciones Neo4j nuevas, no participa en la
detección de candidatos (`Q0`), no altera `ContextPackage` ni ningún
`RuleDraft`. Es exactamente lo que su nombre indica: una traducción
determinística y de solo lectura sobre `artifacts/02-canonical/` ya
persistido.

Esto implica, explícitamente:

- **No cambia candidatos ni reglas V1.** Nunca lee ni escribe
  `06-candidates.json`, `07-context/`, `08-rule-drafts/`, `09-guardrails/` ni
  `10-rules/`.
- **No es un `PipelineStage`.** No aparece en `RunState.stages`, no bloquea
  ni condiciona ninguna transición del pipeline, y `runner.py`/
  `run_ingestion` nunca lo invocan.
- **No es requisito para `COMPLETED`.** Un run histórico que nunca ejecutó
  `semantic-effects` llega a `COMPLETED` exactamente igual que hoy.
- **No reemplaza revisión funcional.** Que una sentencia produzca un efecto
  `FULLY_SUPPORTED` significa que fue normalizada estructuralmente, nunca que
  la regla de negocio subyacente sea correcta o esté completa.

## Ubicación

```
<run_dir>/diagnostics/semantic-effects.json
```

`diagnostics/` es un directorio **opcional**, externo a `artifacts/`, que
ninguna etapa V1 crea, lee ni valida. Un run histórico simplemente no tiene
este archivo — se comporta exactamente igual que hoy, sin ningún cambio de
comportamiento.

## Cómo generarlo

Exclusivamente bajo demanda, vía CLI:

```
python -m altamira_extractor.cli semantic-effects <run_id>
python -m altamira_extractor.cli semantic-effects <run_id> --json
```

Requiere únicamente que `<run_id>` ya haya alcanzado `PARSED` (`SUCCEEDED`) —
es el primer punto del pipeline en el que existe `artifacts/02-canonical/`, el
único artefacto de entrada que el analizador necesita.

El comando:

1. localiza el run vía `Settings.runs_dir`;
2. carga y valida `artifacts/02-canonical/` (nunca lo modifica);
3. calcula un hash determinístico del directorio (`source_artifact_hashes`);
4. ejecuta el analizador puro (`pipeline/semantic_effects_analyzer.py`: sin
   Neo4j, sin variables de entorno, sin LLM, sin `SemanticGraph`, sin
   candidatos);
5. persiste `diagnostics/semantic-effects.json` de forma atómica
   (`atomic_write_json`, el mismo primitivo que usa cada etapa V1);
6. imprime un resumen legible; con `--json`, imprime además el artefacto
   completo.

No se invoca automáticamente desde `ingest`, `resume`, la API ni la UI.

## Tipos de efecto (`SemanticEffectKind`)

| Valor | Se produce a partir de | Notas |
|---|---|---|
| `ASSIGN_LITERAL` | `MOVE` de un literal a uno o más destinos | Un efecto por destino cuando hay múltiples (`MULTIPLE_TARGET_ASSIGNMENT`). |
| `COPY_VALUE` | `MOVE` variable-a-variable (uno o varios) | Nunca infiere correspondencia campo a campo entre grupos; lista todo lo declarado. |
| `COMPUTE_VALUE` | `COMPUTE` | La expresión se conserva como texto, nunca se evalúa. |
| `SET_VALUE` | `SET` ordinario, o `SET` no resuelto contra una condición 88 conocida | Nunca distingue `SET` de índice de `SET ... UP/DOWN BY`. |
| `SET_CONDITION_TRUE` / `SET_CONDITION_FALSE` | `SET condición-88 TO TRUE`/`TO FALSE` resuelto estructuralmente (Fase 3, ver `docs/LEVEL_88_SUPPORT.md`) | Siempre `FULLY_SUPPORTED` cuando aparece; `condition_name`/`parent_data_item`/`condition_values` conservan la evidencia; `literal` solo se puebla para un VALUE simple sin THRU. |
| `CONTROL_TRANSFER` | `GO TO`/`PERFORM` | `PERFORM` siempre `PARTIALLY_SUPPORTED` (UNTIL/VARYING pueden no estar representados). |
| `EXECUTE_SQL` | Cada `CanonicalSqlAccess` de un `EXEC SQL` | `predicate_text` se propaga verbatim a `sql_predicate_text`. Desde la Fase 15B3-C3-B, `reads`/`writes` SÍ se pueblan para la forma simple y **completa** de SELECT/INSERT/UPDATE/DELETE (ver "Variables host SQL: dirección" más abajo); permanecen vacíos para JOIN/subconsulta/variable indicadora/sintaxis no reconocida/clasificación parcial (expresión en la lista SELECT). |
| `CALL_PROGRAM` | `CALL` (literal o dinámico), Fase 6 (`docs/INTERPROCEDURAL_CALL_LINKAGE.md`) | Nunca puebla `writes`/`target_data_items`: `BY REFERENCE`/`RETURNING` describen únicamente un efecto **potencial**, capturado exclusivamente en `call_arguments`/`call_returning_data_item`. `FULLY_SUPPORTED` solo para `CALL` literal con todos los argumentos de forma estructural identificable, sin `RETURNING` ni ramas `ON EXCEPTION`; cualquier otra combinación es `PARTIALLY_SUPPORTED`, nunca `UNSUPPORTED` (el `CALL` en sí siempre se reconoce estructuralmente). |
| `PRESERVED_STATEMENT` | `StatementKind.OTHER`, `StatementKind.PROGRAM_TERMINATION` (Fase 7b: `GOBACK`/`STOP RUN`/`EXIT PROGRAM`, ninguno mueve/calcula/asigna datos), `MOVE CORRESPONDING`/grupo no resoluble, o precondiciones faltantes de `COMPUTE`/`GO TO`/`PERFORM`/`EXEC SQL` | Nunca afirma `writes`, `target_data_items` ni `literal`. |
| `UNSUPPORTED_STATEMENT` | Cada entrada de `CanonicalProgram.unsupported_constructs` | Declaración explícita del propio productor del artefacto, no una inferencia de este analizador. |

`IF`/`EVALUATE` **nunca generan un efecto artificial**: sus sentencias hijas
(aplanadas por el parser) ya conservan `parent_statement_id`/`branch_kind`, y
el vínculo con la decisión se preserva vía `source_reference` de esas hijas.
Cero efectos para un `IF`/`EVALUATE` no es un error.

`SET_CONDITION_TRUE`/`SET_CONDITION_FALSE` se agregaron en la Fase 3 de la
ampliación semántica (soporte nivel 88, ver `docs/LEVEL_88_SUPPORT.md`).
`CALL_PROGRAM` se agregó en la Fase 6 (fundación interprocedural CALL/
LINKAGE, ver `docs/INTERPROCEDURAL_CALL_LINKAGE.md`). Deliberadamente
**siguen sin existir** `CICS_LINK`, `READ_FILE` ni `WRITE_FILE`: requieren
información que el parser no conserva de forma confiable hoy.

## Variables host SQL: dirección

**Auditoría original** (Fase 15B3-C3-A): `CanonicalSqlAccess.host_variables`
era una **única lista plana**. El extractor Java (`hostVariablesOf`) la
construía con un único regex (`:([A-Za-z][A-Za-z0-9-]*)`) aplicado sobre
**todo** el texto crudo de la sentencia SQL — cláusulas `INTO`, `WHERE`,
`VALUES` y `SET` indistintamente — sin registrar de cuál provino cada
variable. `host_variables` se **conserva sin cambios** por compatibilidad
(el mismo caso real: `EmbeddedSqlExtractorTest.
selectWithHostVariablesAndPredicate`).

**Corrección (Fase 15B3-C3-B)**: `EmbeddedSqlExtractor` ahora segmenta el
texto crudo por palabra clave (`INTO`/`SET`/`VALUES`/`WHERE`) — nunca
gramática SQL, sigue siendo regex sobre delimitadores literales — y
puebla `CanonicalSqlAccess.input_host_variables`/`output_host_variables`/
`predicate_host_variables` cuando esa segmentación es estructuralmente
segura (sin JOIN, sin variable indicadora `:VAR:IND`, sin sintaxis no
reconocida). Reglas por verbo (`EmbeddedSqlExtractorTest`, casos
`selectIntoAssignsOutputAndWherePredicateAssignsInput`/
`insertValuesAssignsInputNeverOutput`/
`updateSetAndWhereAssignInputNeverOutput`/
`deleteWhereAssignsInputNeverOutput`):

```sql
SELECT SALDO INTO :WS-SALDO FROM CUENTAS WHERE ID_CUENTA = :WS-CUENTA-ID
```

produce ahora `output_host_variables=[WS-SALDO]` (INTO),
`input_host_variables=predicate_host_variables=[WS-CUENTA-ID]` (WHERE) —
`operation` (`READS`/`WRITES`/`UPDATES`/`INSERTS`) sigue describiendo
únicamente la operación sobre la **tabla**, nunca la dirección de una
variable individual; INSERT/UPDATE/DELETE **nunca** infieren `writes`
sobre una variable host (una `SET COL=:A` nunca implica que `:A` se
escribe, solo que la columna SQL se actualiza).
`StatementExtractor.convertExecSql` agrega esta dirección directamente a
`CanonicalStatement.variables_read`/`variables_written` (ver
`SqlDirectedDataFlowTest`), lo que habilita `DATA_DEPENDS_ON` entre
Paragraphs a través de SQL sin ningún cambio en `dependency_builder.py`
(agnóstico a `StatementKind`, ver
`test_dependency_builder.py::test_data_dependency_through_exec_sql_select_into`).

**Corrección pre-commit posterior a la entrega inicial de C3-B (clasificación
parcial)**: `output_host_variables`/`input_host_variables` reflejan
únicamente `INTO`/`WHERE`/`SET`/`VALUES` — nunca una expresión dentro de la
lista de columnas del `SELECT`. Una sentencia como
`SELECT :WS-FACTOR * SALDO INTO :WS-RESULTADO FROM CUENTAS WHERE ID=:WS-ID`
deja `WS-FACTOR` presente en `host_variables` (legacy) pero **fuera** de
`input_host_variables ∪ output_host_variables`. Publicar
`variables_read=[WS-ID]`/`variables_written=[WS-RESULTADO]` en ese caso
fabricaría un `DATA_DEPENDS_ON` incompleto (la dependencia real vía
`WS-FACTOR` quedaría invisible sin ningún aviso). Por eso
`StatementExtractor.convertExecSql` exige que **toda** entrada de
`host_variables` esté contenida en esa unión antes de promoverla a
`CanonicalStatement.variables_read`/`variables_written`
(`isDirectionFullyResolved`, `SqlDirectedDataFlowTest.
selectExpressionWithUnresolvedHostVariableNeverPromotesPartialDirection`);
si algún access de la sentencia queda incompleto, `variables_read`/
`variables_written` quedan **vacíos para toda la sentencia** (nunca se
mezclan accesses completos e incompletos). `_normalize_exec_sql` replica el
mismo cálculo sobre `CanonicalSqlAccess` y agrega
`diagnostic_code=SQL_HOST_VARIABLE_PARTIALLY_UNRESOLVED` — distinto de
`SQL_HOST_VARIABLE_DIRECTION_UNRESOLVED` (que implica que **ninguna**
variable tiene dirección): aquí sí hay variables dirigidas, pero no todas
(`test_semantic_effects_analyzer.py::
test_exec_sql_select_expression_unresolved_var_publishes_no_partial_direction`,
`test_dependency_builder.py::
test_data_dependency_absent_when_exec_sql_direction_partially_unresolved`).
`EmbeddedSqlExtractor` **no cambió**: sigue sin interpretar expresiones,
funciones (`COALESCE`, etc.) ni gramática SQL — el fix es exclusivamente de
completitud en la promoción de la dirección ya extraída, nunca una
ampliación del subconjunto soportado.

**Cuándo permanece sin resolver**: JOIN explícito (la sentencia completa
se declara `unsupported`, nunca una tabla parcial — ver más abajo),
variable indicadora (`:VAR:IND`, degrada TODA la sentencia a dirección no
resuelta más `diagnostic_code=
SQL_INDICATOR_VARIABLE_DIRECTION_UNSUPPORTED`), subconsulta, cursor, SQL
dinámico, o cualquier forma no reconocida por los delimitadores de
palabra clave. En esos casos, `reads`/`writes` permanecen **vacíos** y
`diagnostic_code=SQL_HOST_VARIABLE_DIRECTION_UNRESOLVED` se agrega (un
validador de contrato impide que ambos coexistan). Sin parser SQL nuevo,
sin gramática, sin inferencia por adivinación: la dirección se declara
solo cuando el delimitador de palabra clave la demuestra.

## JOIN explícito: corrección de correctness (Fase 15B3-C3-B)

**Auditoría 15B3-C3-A** encontró una violación real de no-silent-loss:
`FROM CUENTAS A JOIN MOVIMIENTOS B ON ...` (sin coma) producía
`table=CUENTAS`, `interpreted=true` — la tabla `MOVIMIENTOS` se perdía
silenciosamente, sin warning, presentando un resultado parcial como si
fuera completo. **Corregido**: `EmbeddedSqlExtractor` detecta cualquier
variante de `JOIN` (`INNER`/`LEFT`/`RIGHT`/`FULL`) en la cláusula `FROM`
y devuelve la sentencia completa como `unsupported` — nunca una tabla
parcial (`EmbeddedSqlExtractorTest.
selectWithExplicitJoinIsUnsupportedNeverPartialTable` y variantes por
tipo de JOIN). El caso ya soportado de múltiples tablas separadas por
coma (`FROM CUENTAS A, MOVIMIENTOS B`) no se ve afectado.

## `SemanticSupportStatus`

Mismo enum que `SemanticCoverageReport` (`FULLY_SUPPORTED` /
`PARTIALLY_SUPPORTED` / `PRESERVED_ONLY` / `UNSUPPORTED`), aplicado aquí a
nivel de cada `SemanticEffect` individual en vez de a nivel de construcción
agregada.

## Ausencia deliberada de propagación

Un `SemanticEffect` describe **exclusivamente** la sentencia que lo originó.
Una cadena

```cobol
MOVE '0005' TO WS-COD-AUX
MOVE WS-COD-AUX TO WS-COD-RETORNO
```

produce **dos** efectos independientes:

1. `ASSIGN_LITERAL{target=WS-COD-AUX, literal="0005"}` (`FULLY_SUPPORTED`)
2. `COPY_VALUE{source=WS-COD-AUX, target=WS-COD-RETORNO}` (`PARTIALLY_SUPPORTED`)

Nunca un tercer efecto, nunca un `ASSIGN_LITERAL` sobre `WS-COD-RETORNO`,
nunca el literal `'0005'` agregado al segundo efecto. Ningún campo del
contrato existe para expresar una cadena de asignaciones o un valor que
"atraviesa" más de un efecto. Cubierto explícitamente por
`test_two_hop_move_chain_produces_no_propagation`
(`tests/pipeline/test_semantic_effects_analyzer.py`).

Esta ausencia deliberada es exactamente lo que la Fase 4 (`feat/constant-
copy-propagation`, ver `docs/SEMANTIC_PROPAGATION.md`) cubre en una capa
**separada y opcional**: `semantic_propagation_analyzer.py` consume estos
mismos dos `SemanticEffect` (sin modificarlos ni reemplazarlos) y agrega
`SemanticPropagationArtifact` (`diagnostics/semantic-propagation.json`)
con la conclusión de que `WS-COD-RETORNO` puede demostrarse igual a
`'0005'`. `SemanticEffectsArtifact` en sí mismo nunca cambia por la
existencia de esa capa.

## Nivel 88: capturado nativamente cuando es demostrable (Fase 3)

Desde la Fase 3 (`docs/LEVEL_88_SUPPORT.md`), un `SET condición TO TRUE`/
`TO FALSE` que el parser Java resolvió estructuralmente contra
`CanonicalProgram.condition_names` (`CanonicalStatement.
condition_name_target`/`condition_set_value` poblados) produce
`SET_CONDITION_TRUE`/`SET_CONDITION_FALSE`, siempre `FULLY_SUPPORTED`, con
`condition_name`/`parent_data_item`/`condition_values` conservados.

El diagnóstico previo, `LEVEL_88_VALUE_NOT_AVAILABLE`, se mantiene
exclusivamente para el caso **residual**: un `SET` cuyo `target_data_items`
coincide por nombre con un `CanonicalDataItem` de `level == 88`, pero que
el parser Java **no** pudo resolver como `TO TRUE`/`TO FALSE` contra una
condición única de `condition_names` (p. ej. un nombre ambiguo entre
condiciones homónimas bajo padres distintos — ver limitaciones en
`docs/LEVEL_88_SUPPORT.md`). En ese caso el efecto sigue siendo `SET_VALUE`
y el analizador **nunca** infiere la variable padre, el VALUE, ni un rango
THRU por sí mismo: la comparación de nombre es la única señal, nunca una
inferencia adicional.

## Identificadores determinísticos

Cada `effect_id` se deriva exclusivamente de `program`, `paragraph`,
`source_statement_id`, el `SemanticEffectKind` y un ordinal estable dentro de
la sentencia (`f"effect::{program}::{paragraph}::{statement_id}::{kind}::
{ordinal}"`). Nunca UUID aleatorio, nunca timestamp, nunca `hash()` de
Python. La misma entrada produce siempre el mismo `effect_id` y los mismos
bytes.

## Compatibilidad con runs históricos

Un run que nunca ejecutó `semantic-effects` no tiene
`diagnostics/semantic-effects.json` y se comporta exactamente igual que antes
de que esta funcionalidad existiera: ningún lector V1 (`read_run_state`, la
API, la UI, el CLI) sabe de su existencia ni la requiere.

## `schema_version` vs. `analyzer_version`

Dos campos independientes, con significados distintos:

- **`schema_version`** versiona la **forma** serializada del artefacto (qué
  campos existen en `SemanticEffect`/`SemanticEffectKind`).
- **`analyzer_version`** versiona la **lógica de interpretación** del
  analizador (`pipeline/semantic_effects_analyzer.py`), incluso cuando la
  forma no cambia.

La Fase 3 de la ampliación semántica (soporte nivel 88,
`docs/LEVEL_88_SUPPORT.md`) subió **ambos** a `"1.1"` — a diferencia de
`SemanticCoverageReport` (Fase 8, más abajo), donde solo `analyzer_version`
subió: aquí la forma también cambió (`SemanticEffect` ganó `condition_name`/
`parent_data_item`/`condition_values`; `SemanticEffectKind` ganó
`SET_CONDITION_TRUE`/`SET_CONDITION_FALSE`), y la lógica de `SET` cambió de
forma inequívoca (un `SET condición-88 TO TRUE/FALSE` resuelto ya no cae en
el `SET_VALUE` genérico de la Fase 2).

**Compatibilidad con `semantic-effects.json` de la Fase 2**: el contrato
acepta `schema_version`/`analyzer_version` `"1.0"` en lectura — un artefacto
generado por el analizador anterior a la Fase 3 sigue cargando sin cambios
(los campos nuevos son opcionales, con default `None`/lista vacía). Un
analizador nuevo **siempre** emite `"1.1"` para ambos campos en cualquier
artefacto que genere, independientemente de si el programa analizado usa o
no nivel 88 — a diferencia de `CanonicalProgram.schema_version` (condicional
por programa, ver `docs/LEVEL_88_SUPPORT.md`): aquí la versión describe la
**capacidad del analizador**, no el contenido de un artefacto individual.

**Fase 6 (fundación interprocedural CALL/LINKAGE)**: subió **ambos** campos
de nuevo, a `"1.2"` — mismo patrón que la Fase 3: la forma cambió
(`SemanticEffect` ganó `call_target_kind`/`called_program_name`/
`called_program_expression`/`call_arguments`/`call_returning_data_item`;
`SemanticEffectKind` ganó `CALL_PROGRAM`) y la lógica de interpretación de
`StatementKind.CALL` cambió de forma inequívoca (antes de la Fase 6,
`CALL` caía en `PRESERVED_STATEMENT`/`UNSUPPORTED_STATEMENT` genérico). El
contrato acepta `"1.0"`, `"1.1"` y `"1.2"` en lectura; un analizador nuevo
**siempre** emite `"1.2"` para ambos campos, independientemente de si el
programa analizado usa o no `CALL`.

**Fase 7b (distinción GOBACK/STOP RUN/EXIT PROGRAM,
`docs/INTERPROCEDURAL_PROPAGATION.md`)**: `schema_version`/`analyzer_version`
**NO subieron** — decisión deliberada, a diferencia de las Fases 3 y 6. La
forma no cambió (ningún campo nuevo en `SemanticEffect`/`SemanticEffectKind`:
`PROGRAM_TERMINATION` reutiliza `PRESERVED_STATEMENT`, ya existente desde la
Fase 2). El único cambio observable es el texto de `diagnostic_codes`/
`explanation` (`STATEMENT_TEXT_PRESERVED_WITHOUT_SEMANTIC_EFFECT` →
`PROGRAM_TERMINATION_HAS_NO_DATA_EFFECT`) — nunca `kind`/`support_status`,
que son los ÚNICOS campos que cualquier consumidor real inspecciona
programáticamente (`semantic_propagation_analyzer.py::_handle_unknown_effect`
despacha exclusivamente por `effect.kind`, nunca por el contenido de
`diagnostic_codes`). Justificado con un test dedicado
(`tests/pipeline/test_semantic_effects_analyzer.py::
test_program_termination_is_functionally_identical_to_other_for_downstream_consumers`)
y confirmado end-to-end con el JAR real contra un baseline v1.5.0 aislado:
`artifacts/03b-semantic-enrichment.json` (que consume estos efectos) es
byte a byte idéntico entre v1.5.0 y la rama actual, salvo `run_id`, para
PROGRULE1 y ambos paquetes Catherine (todos terminan en `GOBACK`/`STOP RUN`).

**Regenerar el diagnóstico sobre un run histórico**: `semantic-effects.json`
no es un artefacto versionado por run ni migrado in situ — volver a ejecutar
`semantic-effects <run_id>` simplemente sobrescribe el archivo con un
artefacto `"1.2"` nuevo, calculado desde `artifacts/02-canonical/` tal como
existe hoy (si ese canónico es `schema_version="1.2"` con `CALL`/`LINKAGE
SECTION`, el nuevo `semantic-effects.json` reflejará `CALL_PROGRAM`; si es
`"1.1"` con condiciones nivel 88, reflejará `SET_CONDITION_TRUE`/`FALSE`;
si es `"1.0"` histórico, seguirá viendo `SET_VALUE` genérico como siempre).

## Idempotencia y hashing

El cálculo es puro y determinístico: dado el mismo contenido de
`artifacts/02-canonical/`, dos ejecuciones de `semantic-effects` producen
**bytes idénticos** en `diagnostics/semantic-effects.json` (sin timestamps de
ningún tipo). `source_artifact_hashes["artifacts/02-canonical"]` registra un
hash determinístico del directorio completo (rutas relativas ordenadas + hash
de cada archivo, nunca mtimes, nunca rutas absolutas) — permite detectar si
el artefacto quedó desactualizado respecto a los datos que lo originaron.

## Relación con V1: solo diagnóstico, todavía no alimenta nada

Esta primera implementación de `semantic-effects` **no alimenta** ningún
detector, `ContextPackageBuilder`, generación de `RuleDraft` ni guardrail.
Es un artefacto de observabilidad, pensado para auditar qué tan
completamente se normaliza cada programa antes de decidir si (y cómo) un
futuro checkpoint lo conecta al resto del pipeline.

## Tests

Ver también `docs/LEVEL_88_SUPPORT.md` para los tests específicos de
`SET_CONDITION_TRUE`/`SET_CONDITION_FALSE` (Fase 3):

- `tests/contracts/test_semantic_effects.py` — contrato: versiones,
  serialización estable, validadores de coherencia por `SemanticEffectKind`
  (incluido `SET_CONDITION_TRUE`/`FALSE`), contadores, orden determinístico,
  campos extra, `sql_host_variables` (default vacío, orden/unicidad,
  incompatibilidad con `reads`/`writes` cuando la dirección es no resuelta).
- `tests/pipeline/test_semantic_effects_analyzer.py` — analizador puro:
  las cinco variantes de `MOVE`, `SET` (ordinario, resuelto contra una
  condición 88, y el residual no resuelto), `COMPUTE`, `GO TO`, `PERFORM`,
  `EXEC SQL` (SELECT/INSERT/UPDATE/DELETE, sin inferir dirección,
  `sql_host_variables` ordenado y sin duplicados, `predicate_text` nunca
  copiado), `IF`/`EVALUATE` sin efecto artificial, `OTHER`,
  `unsupported_constructs`, múltiples programas, determinismo, el caso
  obligatorio de la cadena de dos `MOVE` sin propagación, y el caso
  obligatorio `SET condición-88 TO TRUE` + `MOVE` de la variable padre sin
  propagación.
- `tests/pipeline/test_semantic_effects_service.py` (16) — servicio de
  filesystem: creación, determinismo, errores claros, no modificación de
  artefactos de entrada.
- `tests/test_cli_semantic_effects.py` (10) — comando CLI: salida legible,
  `--json`, códigos de salida, saneamiento de errores, no regresión
  explícita sobre `03-dependencies.json`/`04-semantic-graph.json`/
  `06-candidates.json`/`07-context/`/`08-rule-drafts/`/`09-guardrails/`/
  `10-rules/`.
