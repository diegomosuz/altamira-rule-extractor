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
| `SET_VALUE` | `SET` | Nunca distingue `SET` ordinario de condición-88 `TO TRUE/FALSE` ni de `SET ... UP/DOWN BY`. |
| `CONTROL_TRANSFER` | `GO TO`/`PERFORM` | `PERFORM` siempre `PARTIALLY_SUPPORTED` (UNTIL/VARYING pueden no estar representados). |
| `EXECUTE_SQL` | Cada `CanonicalSqlAccess` de un `EXEC SQL` | `predicate_text` nunca se copia al efecto; `reads`/`writes` permanecen vacíos salvo evidencia inequívoca (ver "Variables host SQL: dirección no verificable" más abajo). |
| `PRESERVED_STATEMENT` | `StatementKind.OTHER`, `MOVE CORRESPONDING`/grupo no resoluble, o precondiciones faltantes de `COMPUTE`/`GO TO`/`PERFORM`/`EXEC SQL` | Nunca afirma `writes`, `target_data_items` ni `literal`. |
| `UNSUPPORTED_STATEMENT` | Cada entrada de `CanonicalProgram.unsupported_constructs` | Declaración explícita del propio productor del artefacto, no una inferencia de este analizador. |

`IF`/`EVALUATE` **nunca generan un efecto artificial**: sus sentencias hijas
(aplanadas por el parser) ya conservan `parent_statement_id`/`branch_kind`, y
el vínculo con la decisión se preserva vía `source_reference` de esas hijas.
Cero efectos para un `IF`/`EVALUATE` no es un error.

Deliberadamente **no** existen todavía `SET_CONDITION_TRUE`,
`SET_CONDITION_FALSE`, `CALL_PROGRAM`, `CICS_LINK`, `READ_FILE` ni
`WRITE_FILE`: requieren información que el parser no conserva de forma
confiable hoy.

## Variables host SQL: dirección no verificable

**Auditoría** (`CanonicalSqlAccess`, `EmbeddedSqlExtractor.java`,
`EmbeddedSqlExtractorTest`): `CanonicalSqlAccess.host_variables` es una
**única lista plana**. El extractor Java (`hostVariablesOf`) la construye
con un único regex (`:([A-Za-z][A-Za-z0-9-]*)`) aplicado sobre **todo** el
texto crudo de la sentencia SQL — cláusulas `INTO`, `WHERE`, `VALUES` y
`SET` indistintamente — sin registrar de cuál provino cada variable. El
caso real que lo demuestra (`EmbeddedSqlExtractorTest.
selectWithHostVariablesAndPredicate`):

```sql
SELECT SALDO INTO :WS-SALDO FROM CUENTAS WHERE ID_CUENTA = :WS-CUENTA-ID
```

produce `host_variables = [WS-SALDO, WS-CUENTA-ID]` en una sola lista: `WS-
SALDO` es **salida** (`INTO`), `WS-CUENTA-ID` es **entrada** (`WHERE`), y
el contrato no distingue cuál es cuál. `operation` (`READS`/`WRITES`/
`UPDATES`/`INSERTS`) describe la operación sobre la **tabla** (mapeo
léxico de la palabra clave SQL: `SELECT`→`READS`, `INSERT`→`INSERTS`,
`UPDATE`→`UPDATES`, `DELETE`→`WRITES`), nunca la dirección de una variable
individual — usarla para inferir `reads`/`writes` de las variables host
sería una inferencia semántica no respaldada por el contrato, exactamente
el tipo de "no inventar" que este proyecto prohíbe.

**Diseño aplicado**: `SemanticEffect.sql_host_variables: list[str]` es un
campo neutral (ordenado, sin duplicados, validado por contrato) donde se
conservan todas las variables host de cada `CanonicalSqlAccess`.
`reads`/`writes` de un efecto `EXECUTE_SQL` permanecen **vacíos** mientras
no exista evidencia estructural inequívoca de dirección; cuando hay
variables host, se agrega `diagnostic_code=
SQL_HOST_VARIABLE_DIRECTION_UNRESOLVED`. Un validador de contrato impide
que ambos coexistan (`reads`/`writes` poblados junto con ese diagnóstico)
para que esta clase de inferencia no pueda reintroducirse silenciosamente.
No se agregó ningún parser SQL nuevo ni regex adicional sobre
`predicate_text`/`source_text`: la limitación se declara, no se resuelve
por adivinación.

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

## Nivel 88: se reporta, nunca se interpreta

Cuando un `SET` escribe sobre un `target_data_items` cuyo nombre coincide
—por comparación directa, nunca inferencia— con un `CanonicalDataItem` de
`level == 88` del mismo programa, el efecto `SET_VALUE` resultante agrega
`diagnostic_code=LEVEL_88_VALUE_NOT_AVAILABLE`. El analizador **nunca**
infiere la variable padre, el valor `VALUE`, si la semántica es `TRUE` o
`FALSE`, ni un rango `THRU`: ningún campo del contrato los expresa. Si no se
puede verificar por nombre que el target es nivel 88, no se afirma nada al
respecto.

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

95 tests en total (`pytest -k "semantic_effect or semantic-effect"`):

- `tests/contracts/test_semantic_effects.py` (38) — contrato: versiones,
  serialización estable, validadores de coherencia por `SemanticEffectKind`,
  contadores, orden determinístico, campos extra, `sql_host_variables`
  (default vacío, orden/unicidad, incompatibilidad con `reads`/`writes`
  cuando la dirección es no resuelta).
- `tests/pipeline/test_semantic_effects_analyzer.py` (31) — analizador puro:
  las cinco variantes de `MOVE`, `SET` (incluido nivel 88 verificable),
  `COMPUTE`, `GO TO`, `PERFORM`, `EXEC SQL` (SELECT/INSERT/UPDATE/DELETE,
  sin inferir dirección, `sql_host_variables` ordenado y sin duplicados,
  `predicate_text` nunca copiado), `IF`/`EVALUATE` sin efecto artificial,
  `OTHER`, `unsupported_constructs`, múltiples programas, determinismo, y
  el caso obligatorio de la cadena de dos `MOVE` sin propagación.
- `tests/pipeline/test_semantic_effects_service.py` (16) — servicio de
  filesystem: creación, determinismo, errores claros, no modificación de
  artefactos de entrada.
- `tests/test_cli_semantic_effects.py` (10) — comando CLI: salida legible,
  `--json`, códigos de salida, saneamiento de errores, no regresión
  explícita sobre `03-dependencies.json`/`04-semantic-graph.json`/
  `06-candidates.json`/`07-context/`/`08-rule-drafts/`/`09-guardrails/`/
  `10-rules/`.
