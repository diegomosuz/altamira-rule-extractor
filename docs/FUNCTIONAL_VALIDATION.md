# Validación funcional determinística (Fase 15B2-A, Parte F)

## Propósito

Compara `FunctionalGroundTruthSet` (`FUNCTIONAL_GROUND_TRUTH.md`, Parte E)
contra los `UnifiedCandidateReference` reales de un run
(`CandidatePromotionAssessmentArtifact.candidate_references`, Fase 9) y
produce `FunctionalValidationReport` (`contracts/functional_validation.py`),
persistido en `<run_dir>/diagnostics/functional-validation-report.json`.

## Matching exclusivamente determinístico

Decisión arquitectónica obligatoria: **nunca** LLM, nunca embeddings,
nunca similitud semántica. `pipeline/functional_validation_matcher.py`
compara únicamente `(rule_family, program, paragraph opcional)` como
igualdad exacta de string tras `.strip()` — la única normalización
permitida. No existe un umbral de similitud ni un score continuo de
coincidencia: un candidato coincide o no coincide.

## Cómo generarlo

```
python -m altamira_extractor.cli functional-validate <run_id>
python -m altamira_extractor.cli functional-validate <run_id> --json
```

Requiere que `<run_id>` haya alcanzado `PARSED` (`SUCCEEDED`) —
`compute_candidate_promotion_assessment_artifact` (Fase 9) se recalcula
en memoria en cada invocación; el comando nunca relee un
`diagnostics/candidate-promotion-assessment.json` persistido.

## Aplicabilidad (checkpoint correctivo, cierre de Fase 15B2-A)

Antes de comparar un `GroundTruthCase` contra `candidate_references`,
`pipeline/functional_validation_matcher.py::compute_case_applicability`
decide si el fixture set del caso está realmente presente en el run
evaluado: sha256 de **cada** `GroundTruthFixtureReference` del caso
contra el sha256 de **cada** archivo real bajo `run_dir/work/extracted/`
(el paquete tal como fue ingerido, calculado por
`functional_validation_service.py::_compute_run_fixture_hashes`). Un
caso interprocedural (p. ej. `BY_REFERENCE_OUTPUT`, caller+callee) exige
**todas** sus fixtures presentes, no basta con una.

- `applicability=NOT_APPLICABLE` → `outcome=NOT_EVALUATED`: el caso
  **nunca** llega a comparar `candidate_references` — no puede
  convertirse en `MISSING`/`CONFIRMED_ABSENT` por casualidad, y nunca
  contribuye a `metrics` (ni como TP/FP/FN/TN, ni como sustituto de
  cero).
- `applicability=APPLICABLE` → el caso se evalúa normalmente (ver
  abajo).

Esto corrige el defecto original: antes, **todos** los casos del
catálogo se evaluaban contra **cualquier** run, incluyendo los cinco
paquetes reales de ingeniería (Catherine, CLIENTES_EMPRESAS,
PRESTAMOS_EMPRESAS, CONSULTA_SALDOS) que nunca podrían contener las
fixtures sintéticas — sus tres casos `POSITIVE` aparecían
sistemáticamente `MISSING` (falsos negativos estructurales, nunca una
regresión real) y penalizaban `recall`/release readiness sin
fundamento.

`dataset_applicability` (a nivel de `FunctionalValidationReport`) es
`APPLICABLE` si **al menos un** caso del catálogo lo es para este run,
si no `NOT_APPLICABLE`. `dataset_disposition` resume el resultado
agregado: `NOT_EVALUATED` (dataset no aplicable), `PASS_ENGINEERING`
(todos los casos aplicables `POSITIVE` `MATCHED` y todos los
`NEGATIVE` aplicables `CONFIRMED_ABSENT`) o `FAIL_ENGINEERING`.
`PASS_ENGINEERING` **nunca** implica aprobación de dominio (CLAUDE.md:
`FUNCTIONALLY_APPROVED` fuera de alcance V1).

## Resultado por caso (casos `APPLICABLE`)

- `kind=POSITIVE`: cada `GroundTruthExpectedRule` produce un
  `ExpectedRuleMatchResult` (`MATCHED` si `matched_count >=
  minimum_count`, si no `MISSING`) — nunca un resultado intermedio.
- `kind=NEGATIVE`: `CONFIRMED_ABSENT` si ningún `UnifiedCandidateReference`
  del programa tiene `rule_family != UNKNOWN`; si no,
  `UNEXPECTED_CANDIDATES`, con los `unified_reference_id` involucrados
  listados explícitamente (nunca ocultos).

## Granularidad honesta de precisión/recall/F1

Este catálogo de ground truth es un conjunto de aserciones puntuales
(*spot-check*), **nunca** una etiqueta exhaustiva de cada candidato real
como verdadero/falso positivo — construir eso exigiría etiquetar
manualmente todos los candidatos de todos los runs, fuera de alcance de
este bloque. Por eso:

- **TP/FN** se computan a nivel de **expectation** (cada
  `GroundTruthExpectedRule` de un caso `POSITIVE` es una unidad).
- **FP/TN** se computan a nivel de **caso completo** (cada `GroundTruthCase`
  `NEGATIVE` es una unidad — sin desglose por candidato individual).

Un `precision=1.0` de este reporte significa **"todas las aserciones
verificadas se cumplieron"**, nunca "el detector nunca produce falsos
positivos en general". `precision`/`recall`/`f1_score` son `None`
(nunca `0.0`/`1.0` arbitrario) cuando su denominador real es cero — p.
ej. `precision` sin ningún caso `NEGATIVE` en el catálogo no tiene un
valor matemáticamente definido.

## Ejemplo real (ejecutado en esta misma fase)

Contra un run real que no contiene ninguna de las fixtures del catálogo
(p. ej. cualquiera de los cinco paquetes reales de ingeniería), el
resultado correcto y esperado es: `dataset_applicability=NOT_APPLICABLE`,
`dataset_disposition=NOT_EVALUATED`, y **todos** los casos en
`outcome=NOT_EVALUATED` (checkpoint correctivo — antes de la corrección
de aplicabilidad, esto se reportaba incorrectamente como `MISSING`,
contando el paquete equivocado como una regresión). Contra el run que sí
ingirió las fixtures reales de `config/ground_truth/fixtures/`
(`examples/PAQUETE_SINTETICO_GROUND_TRUTH_FASE_15B2A.zip`, que incluye
`gt_return_code_001.cbl`/`gt_level88_return_code_001.cbl`/
`gt_negative_001.cbl` pero **no** las fixtures caller/callee de
`BY_REFERENCE_OUTPUT`, que viven en su propio paquete dedicado — ver
`tests/pipeline/test_ground_truth_by_reference_output_integration.py`):
tres casos `APPLICABLE` (`MATCHED`/`MATCHED`/`CONFIRMED_ABSENT`) y uno
`NOT_APPLICABLE` (`BY_REFERENCE_OUTPUT`, su fixture set no está en ese
ZIP).

## Tests (Parte J)

- `tests/contracts/test_functional_validation.py`: contrato — coherencia
  `outcome` vs conteos, fórmulas de precisión/recall/F1, orden
  determinístico, casos con denominador cero.
- `tests/pipeline/test_functional_validation_matcher.py`: analizador
  puro — matching exacto, escenarios `MATCHED`/`MISSING`/
  `CONFIRMED_ABSENT`/`UNEXPECTED_CANDIDATES`, cálculo de métricas.
- `tests/pipeline/test_functional_validation_service.py`: servicio de
  filesystem — encadenamiento con Fase 9, errores claros.
- `tests/test_cli_functional_validate.py`: comando CLI.
