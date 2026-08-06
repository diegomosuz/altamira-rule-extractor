# Release readiness funcional (Fase 15B2-A, Parte G)

## Propósito y alcance deliberadamente acotado

`config/release_readiness_policy.yaml` (`ReleaseReadinessPolicy`,
`contracts/release_readiness.py`) evalúa **únicamente** dos señales ya
producidas por este mismo bloque:

1. la reconciliación estática del catálogo semántico (Parte C,
   `pipeline/semantic_coverage_registry.py`);
2. la validación funcional determinística de un run concreto (Parte F,
   `FUNCTIONAL_VALIDATION.md`).

**Nunca** evalúa Docker/Helm/K3s, backup/restore, consolidación de
versión ni el manifiesto final de release — esas piezas son
responsabilidad explícita de un bloque posterior (observabilidad/release
engineering), fuera de FASE 15B2-A.

`ReleaseReadinessDisposition` refleja este límite en su nomenclatura:
**nunca** usa `READY`/`APPROVED` a secas — eso implicaría una aprobación
de release completa que este contrato no puede otorgar.
`FUNCTIONAL_CRITERIA_MET` significa exclusivamente "los criterios
funcionales configurados se cumplieron", nunca "el release está
aprobado" (CLAUDE.md, sección "Candidato, fidelidad y aprobación":
`FUNCTIONALLY_APPROVED` está fuera del alcance V1).

## Motor de criterios cerrado

No es un DSL genérico ni reglas configurables por texto libre.
`ReleaseReadinessCriterionKind` enumera exactamente cinco tipos:

| Kind | Señal | Umbral |
|---|---|---|
| `NO_ERROR_SEVERITY_COVERAGE_ISSUES` | Parte C | binario |
| `NO_MISSING_POSITIVE_CASES` | Parte F | binario |
| `NO_UNEXPECTED_NEGATIVE_CASES` | Parte F | binario |
| `MINIMUM_RECALL` | Parte F (`metrics.recall`) | `minimum_value` |
| `MINIMUM_PRECISION` | Parte F (`metrics.precision`) | `minimum_value` |

Agregar un tipo nuevo exige extender el enum y
`pipeline/release_readiness_evaluator.py` — nunca configurarlo desde el
YAML. Una métrica indefinida (`recall`/`precision=None`, denominador
cero) **nunca se aprueba por defecto**: `MINIMUM_RECALL`/
`MINIMUM_PRECISION` fallan explícitamente con un mensaje que dice por
qué, en vez de tratar `None` como un valor arbitrario.

## Política actual (edición `fase-15b2-a-2026.08.05`)

Los cinco criterios configurados exigen umbral `1.0` (ambigüedad cero):
con un catálogo de ground truth pequeño (2 expectativas `POSITIVE`, 1
caso `NEGATIVE`), cualquier valor de recall/precisión menor a `1.0` ya
implica al menos una regresión concreta y nombrable.

## Aplicabilidad del ground truth (checkpoint correctivo, cierre de Fase 15B2-A)

Cuando `FunctionalValidationReport.dataset_applicability=NOT_APPLICABLE`
(ningún `GroundTruthCase` tiene su fixture set presente en el run
evaluado — el caso de **todos** los paquetes reales de ingeniería hoy),
los cuatro criterios que dependen de `FunctionalValidationReport`
(`NO_MISSING_POSITIVE_CASES`/`NO_UNEXPECTED_NEGATIVE_CASES`/
`MINIMUM_RECALL`/`MINIMUM_PRECISION`) quedan `status=NOT_EVALUATED` —
**nunca** `FAILED`: ausencia de ground truth aplicable no es una
regresión detectable. `NO_ERROR_SEVERITY_COVERAGE_ISSUES` (estructural,
Parte C) se evalúa siempre igual, independientemente del ground truth.

Tres dimensiones de readiness, visibles siempre por separado:

| Campo | Significado |
|---|---|
| `structural_readiness` | Exclusivamente `NO_ERROR_SEVERITY_COVERAGE_ISSUES` — nunca `NOT_EVALUATED`. |
| `engineering_functional_readiness` | Los cuatro criterios funcionales agregados — `NOT_EVALUATED` si el ground truth no aplica. |
| `domain_functional_readiness` | `PENDING_DOMAIN_REVIEW` únicamente cuando `engineering_functional_readiness=PASSED`; `NOT_EVALUATED` en cualquier otro caso. **Nunca** alcanza un valor de aprobación — `CLIENT_APPROVED`/`FUNCTIONALLY_APPROVED` no existen en este contrato (fuera de alcance V1). |

`disposition=NOT_EVALUATED` (nuevo valor) cuando
`engineering_functional_readiness=NOT_EVALUATED` — nunca se degrada a
`FUNCTIONAL_CRITERIA_NOT_MET` solo por falta de señal. Un warning
tipado `GROUND_TRUTH_NOT_AVAILABLE`
(`ReleaseReadinessWarningCode`) se agrega a `warnings[]` en ese caso,
explicando la causa sin inventar un blocker.

## Cómo generarlo

```
python -m altamira_extractor.cli release-readiness-assess <run_id>
python -m altamira_extractor.cli release-readiness-assess <run_id> --json
```

Requiere que `<run_id>` haya alcanzado `PARSED` (`SUCCEEDED`).
Internamente: carga la política, reconcilia el manifiesto estático
(Parte C, independiente de `<run_id>`), recalcula
`FunctionalValidationReport` (Parte F, nunca relee un diagnóstico
persistido) y evalúa cada criterio con el analizador puro
`evaluate_release_readiness`. Persiste `diagnostics/release-readiness-
assessment.json` de forma atómica.

## Tests (Parte J)

- `tests/contracts/test_release_readiness.py`: contrato — coherencia
  `minimum_value` vs `kind`, `disposition` vs resultados, orden
  determinístico.
- `tests/pipeline/test_release_readiness_evaluator.py`: analizador puro
  — los cinco tipos de criterio, escenarios PASS/FAIL, métrica
  indefinida.
- `tests/pipeline/test_release_readiness_service.py`: servicio de
  filesystem — encadenamiento con Partes C/F, errores claros.
- `tests/test_cli_release_readiness_assess.py`: comando CLI.
