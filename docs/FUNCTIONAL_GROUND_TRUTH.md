# Ground truth funcional versionado (Fase 15B2-A, Parte E)

## Propósito

`config/ground_truth/synthetic_engineering.yaml` (`FunctionalGroundTruthSet`,
`contracts/functional_ground_truth.py`) es un catálogo **versionado** de
expectativas funcionales concretas: para una fixture COBOL sintética
concreta, qué candidato (o ausencia de candidato) debe producir el
pipeline real. Es la base sobre la que Parte F (`FUNCTIONAL_VALIDATION.md`)
mide precisión/recall de forma determinística.

## Tres decisiones arquitectónicas obligatorias

1. **Nunca ground truth para paquetes reales de ingeniería.** Catherine
   (original/corregido), CLIENTES_EMPRESAS, PRESTAMOS_EMPRESAS,
   CONSULTA_SALDOS no tienen ninguna expectativa versionada aquí, y no la
   tendrán salvo que exista una expectativa explícita, versionada y ya
   revisada por dominio — ninguna existe hoy. Este catálogo es
   **exclusivamente sintético**.
2. **Independiente de la implementación evaluada.** Cada expectativa de
   este catálogo fue derivada leyendo directamente el código fuente de
   los detectores reales (`candidate_detector.py`, `v2_detectors.py`,
   `interprocedural_rule_detectors.py`) y las reglas de
   `config/semantic-tags.yml` — **nunca** ejecutando el pipeline y
   copiando su salida como "expectativa". Ejecutar el pipeline real fue
   usado únicamente como verificación posterior (ver "Verificación"
   abajo), nunca como fuente de la expectativa misma.
3. **Matching determinístico y exacto (nunca LLM/embeddings/similitud
   semántica).** Ver `FUNCTIONAL_VALIDATION.md`.

## Estructura

- `GroundTruthCase`: una o más fixtures sintéticas (`fixtures`, una
  lista de `GroundTruthFixtureReference` — nunca un campo singular: un
  caso interprocedural genuino, p. ej. `BY_REFERENCE_OUTPUT`, necesita
  caller **y** callee en el mismo paquete, dos archivos distintos), bajo
  `config/ground_truth/fixtures/`, siempre nuevas — nunca reutiliza una
  fixture del parser Java) + su(s) expectativa(s). Cada
  `GroundTruthFixtureReference.sha256` fija el ground truth a los
  **bytes exactos** de esa fixture: si el `.cbl` cambia, el caso queda
  huérfano de forma detectable, nunca valida en silencio contra un
  archivo distinto del revisado.
- `kind=POSITIVE`: la fixture debe producir al menos `minimum_count`
  candidatos de `rule_family` en `program`/`paragraph` — declarado via
  uno o más `GroundTruthExpectedRule`.
- `kind=NEGATIVE`: la fixture nunca debe producir ningún candidato de
  ninguna `rule_family` (excepto `UNKNOWN`) en `program` — sin
  `expected_rules` (la ausencia ES la expectativa completa). `program`
  vive a nivel de `GroundTruthCase` (no solo dentro de cada
  `expected_rules`): para un caso interprocedural, es el programa
  **caller** (`candidate_source_adapters.py::program=candidate.
  caller_program`) — un caso `NEGATIVE` necesita saber contra qué
  programa verificar ausencia sin re-parsear la fixture.

## Aplicabilidad (checkpoint correctivo, cierre de Fase 15B2-A)

`fixtures` es, además de identificación cerrada de qué archivos
respaldan un caso, la base de su **aplicabilidad** a un run concreto:
Parte F (`FUNCTIONAL_VALIDATION.md`) solo evalúa un `GroundTruthCase`
contra un run cuando el sha256 de **cada** fixture declarada aquí está
presente entre los archivos que ese run realmente ingirió. Un caso cuyo
fixture set no está en el run queda `NOT_EVALUATED`, nunca `MISSING` —
esto es precisamente por qué el catálogo puede seguir siendo
"exclusivamente sintético" (decisión #1) sin que evaluarlo contra los
paquetes reales de ingeniería contamine sus métricas: esos runs
simplemente no aplican, en vez de fallar por definición.

## Catálogo actual (edición `fase-15b4-5d-safety-2026.08.13`)

14 casos (2 `NEGATIVE`, 12 `POSITIVE`) en
`config/ground_truth/synthetic_engineering.yaml`:

| case_id | kind | rule_family | fixture |
|---|---|---|---|
| `gt-negative-state-transition-nonfunctional-indicator-name` | NEGATIVE | — | `gt_state_transition_negative_001.cbl` |
| `gt-negative-untagged-counter-decision` | NEGATIVE | — | `gt_negative_001.cbl` |
| `gt-positive-calculation-if-compute-multiplication` | POSITIVE | `CALCULATION` | `gt_calculation_001.cbl` |
| `gt-positive-calculation-if-subtract` | POSITIVE | `CALCULATION` | `gt_calculation_subtract_001.cbl` |
| `gt-positive-calculation-unconditional-add` | POSITIVE | `CALCULATION` | `gt_calculation_add_001.cbl` |
| `gt-positive-calculation-unconditional-compute-multiplication` | POSITIVE | `CALCULATION` | `gt_calculation_unconditional_001.cbl` |
| `gt-positive-calculation-unconditional-divide-giving` | POSITIVE | `CALCULATION` | `gt_calculation_divide_001.cbl` |
| `gt-positive-calculation-unconditional-multiply-giving` | POSITIVE | `CALCULATION` | `gt_calculation_unconditional_002.cbl` |
| `gt-positive-declared-value-return-code` | POSITIVE | `RETURN_CODE` | `gt_declared_value_return_code_001.cbl` |
| `gt-positive-level88-return-code-nested-set` | POSITIVE | `LEVEL_88_RETURN_CODE` | `gt_level88_return_code_001.cbl` |
| `gt-positive-return-code-if-else` | POSITIVE | `RETURN_CODE` | `gt_return_code_001.cbl` |
| `gt-positive-sql-select-into-state-transition` | POSITIVE | `STATE_TRANSITION` | `gt_sql_select_into_state_transition_001.cbl` |
| `gt-positive-sqlcode-evaluate-state-transition` | POSITIVE | `STATE_TRANSITION` | `gt_sqlcode_evaluate_state_transition_001.cbl` |
| `gt-positive-state-transition-if-status-target` | POSITIVE | `STATE_TRANSITION` | `gt_state_transition_001.cbl` |

Cada `derivation_notes` cita las líneas/funciones exactas del detector
real que justifican la expectativa, y, cuando aplica, el resultado de
la verificación contra el pipeline real (ver abajo).

`gt-positive-sqlcode-evaluate-state-transition` cubre específicamente
un `EVALUATE SQLCODE WHEN +100`/`WHEN 0`/`WHEN OTHER` — el mismo patrón
detrás del defecto de semántica de rama corregido en v1.18.2 (ver
`docs/release/RELEASE_NOTES_1.18.2.md`, "Semántica de rama
EVALUATE/WHEN"); su expectativa versionada no cambió, ya que la
corrección afecta el *texto* de `condition` expuesto en D4, nunca qué
candidato/`rule_family` debe producirse.

`BY_REFERENCE_OUTPUT` no tiene ningún caso en esta edición del
catálogo (una edición anterior de este documento describía uno; ya no
está presente en `config/ground_truth/synthetic_engineering.yaml`).

## Cobertura deliberadamente parcial

Ninguna familia productiva carece hoy de al menos un caso `POSITIVE`
en el catálogo (`RETURN_CODE`, `LEVEL_88_RETURN_CODE`, `CALCULATION` y
`STATE_TRANSITION` — ver tabla arriba). `BY_REFERENCE_OUTPUT`
(interprocedural, diagnóstico/shadow — ver
`docs/INTERPROCEDURAL_RULE_DETECTORS_SHADOW.md`) es la única familia
sin un caso versionado en esta edición.

## Verificación (no fuente de la expectativa — ver decisión #2)

Cada fixture fue parseada con el JAR real de ProLeap
(`parser/target/altamira-cobol-parser.jar parse`) para confirmar que la
forma de `CanonicalStatement` (branch_kind, target_data_items,
condition_name_target/condition_set_value, parent_statement_id,
condition_names[].values) coincide con lo que la expectativa asume —
**antes** de que exista ningún candidato real que pudiera sesgar la
expectativa. Esto de-riesga la derivación sin violar la decisión #2: se
verificó la forma del `CanonicalProgram`, nunca la salida de un
detector.

## Tests (Parte J)

- `tests/contracts/test_functional_ground_truth.py`: contrato — orden
  determinístico, `kind` vs `expected_rules` coherente, campos extra
  rechazados, `program` obligatorio en todo caso.
- `tests/pipeline/test_functional_ground_truth_loading.py`: carga real
  del YAML, hashes de fixtures verificados contra los archivos reales en
  `config/ground_truth/fixtures/`.
