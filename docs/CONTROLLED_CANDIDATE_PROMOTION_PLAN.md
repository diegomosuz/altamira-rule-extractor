# Plan de promoción controlada y manifiesto de decisiones (Fase 10 de la ampliación semántica)

## Propósito

Construir un flujo determinístico y auditable que traduzca el
diagnóstico de Fase 9 (`docs/CANDIDATE_PROMOTION_ASSESSMENT.md`) en un
**paquete de revisión humana**, reciba un **manifiesto explícito de
decisiones** de esa revisión, valide cada decisión contra el
assessment de origen, y produzca un **plan de promoción en dry-run**.
Nunca promueve automáticamente ningún candidato; nunca modifica las
fuentes V1 (`Q0`), V2 (shadow) o interprocedurales (shadow). Es una
capa **puramente diagnóstica**, igual que el resto de artefactos de la
ampliación semántica: no agrega un `PipelineStage`, no modifica
`RunState`, no se integra en `ingest`/`resume`.

## Separación assessment / review / decision / plan

Cuatro capas, cada una con su propio contrato y su propio archivo,
nunca fusionadas:

1. **Assessment** (`CandidatePromotionAssessmentArtifact`, Fase 9):
   catálogo unificado, relaciones, conflictos, criterios,
   `PromotionDisposition`. Nunca modificado por Fase 10.
2. **Review package** (`CandidatePromotionReviewPackage`,
   `contracts/candidate_promotion_review.py`): una traducción
   **puramente derivada** del assessment a un formato apto para
   revisión humana — una `CandidateReviewItem` por cada
   `CandidatePromotionAssessment`, con `ReviewEligibility` calculada
   exclusivamente a partir de `PromotionDisposition`. Persiste en
   `diagnostics/candidate-promotion-review-package.json`.
3. **Decision manifest** (`CandidatePromotionDecisionManifest`):
   documento **enteramente humano**, externo al repositorio, nunca
   generado por esta aplicación. Se pasa via `--decisions <path>`;
   nunca se copia al repositorio.
4. **Plan** (`CandidatePromotionPlanArtifact`,
   `contracts/candidate_promotion_plan.py`): resultado de validar el
   manifiesto contra el review package y el assessment, y de aplicar la
   matriz de acciones (`PromotionPlanAction`). Persiste en
   `diagnostics/candidate-promotion-plan.json`. `PROPOSE_SHADOW_
   PROMOTION` es una **propuesta de dry-run**, nunca una promoción
   real — ver más abajo.

Ninguna de las cuatro capas fusiona candidatos ni pierde evidencia: un
`CandidateReviewItem`/`CandidatePromotionPlanItem` siempre conserva
`assessment_id`/`reference_id`/`source`/`source_candidate_id`,
recuperables en su artefacto de origen.

## Revisión humana

El paquete de revisión (`candidate-promotion-review-package <run_id>`)
es el **único** insumo que un humano necesita para decidir: cada
`CandidateReviewItem` incluye identidad, familia, target/literal,
evidencia, relaciones, conflictos, los mismos `PromotionCriterionResult`
del assessment (nunca reinterpretados) y `review_reasons` (un motivo
canónico, `ELIGIBILITY_{eligibility}_FROM_DISPOSITION_{disposition}`,
nunca texto libre).

## Eligibility

`ReviewEligibility` se deriva **exclusivamente** de `PromotionDisposition`
(`pipeline/candidate_promotion_review_generator.py::
_ELIGIBILITY_BY_DISPOSITION`, única fuente de verdad):

| `PromotionDisposition` | `ReviewEligibility` | Decisiones permitidas |
|---|---|---|
| `BASELINE_V1` | `BASELINE` | ninguna — nunca requiere ni acepta una decisión |
| `ALREADY_COVERED` | `ALREADY_COVERED` | `REJECT`/`DEFER` (trazabilidad), nunca `APPROVE` |
| `READY_FOR_CONTROLLED_REVIEW` | `ELIGIBLE` | `APPROVE_FOR_SHADOW_PROMOTION`, `REJECT`, `DEFER` |
| `REVIEW_REQUIRED` | `NOT_ELIGIBLE` | `REJECT`/`DEFER`, nunca `APPROVE` |
| `BLOCKED` / `CONFLICTING` | `BLOCKED` | `REJECT`/`DEFER`, nunca `APPROVE` |
| `NOT_EVALUATED` | `NOT_ELIGIBLE` | únicamente `DEFER` — nunca `REJECT` automático por falta de evidencia |

`NOT_EVALUATED` es el único caso `NOT_ELIGIBLE` que además prohíbe
`REJECT`: la ausencia de una fuente requerida (V1) nunca se traduce en
"la regla es incorrecta" — solo en "hay que esperar a que la fuente
esté disponible".

## Decisiones y reason codes

`CandidatePromotionDecision` es un documento humano: `reviewer_reference`/
`reason_code`/`decision_reference` **nunca** se generan automáticamente.
Catálogo cerrado de `reason_code` (`DecisionReasonCode`):
`EVIDENCE_CONFIRMED`, `BUSINESS_RULE_CONFIRMED`, `DUPLICATE_RULE`,
`INSUFFICIENT_EVIDENCE`, `INCORRECT_TARGET`, `INCORRECT_LITERAL`,
`CONFLICT_REQUIRES_RESOLUTION`, `SOURCE_NOT_AVAILABLE`, `OUT_OF_SCOPE`,
`DEFERRED_FOR_DOMAIN_REVIEW`. `APPROVE_FOR_SHADOW_PROMOTION` solo
acepta `EVIDENCE_CONFIRMED`/`BUSINESS_RULE_CONFIRMED` — cualquier otro
`reason_code` para `APPROVE` es rechazado en la construcción misma del
contrato (nunca llega siquiera al validador del plan).

### Supersesión de decisiones (`decision_reference`)

Como máximo una decisión activa por `review_item_id`. Dos decisiones
para el mismo item solo se resuelven cuando una declara
`decision_reference` apuntando exactamente a la otra (una corrección
explícita y auditable del propio humano) — la referenciada queda
`SUPERSEDED`. Cualquier otro caso (3+ decisiones, o 2 sin una cadena
limpia) se **rechaza íntegramente**: nunca se elige implícitamente "la
última".

## Hashes y prevención de decisiones obsoletas

Cada decisión lleva `assessment_artifact_hash`; el manifiesto completo
lleva `review_package_hash`/`assessment_artifact_hash`/`run_id`. El
constructor del plan (`pipeline/candidate_promotion_plan_builder.py`)
verifica, ANTES de procesar cualquier decisión individual:

1. El assessment **recalculado ahora** coincide con el
   `assessment_artifact_hash` que el review package declara (si no, el
   review package está desactualizado — hay que regenerarlo).
2. El review package **recalculado ahora** coincide con el
   `review_package_hash` que el manifiesto declara (si no, el
   manifiesto no corresponde al review package actual).
3. `assessment_artifact_hash`/`run_id` del manifiesto coinciden con el
   assessment/review package actuales.

Cualquier discrepancia aborta la construcción **completa** (nunca un
plan parcial): un manifiesto obsoleto nunca se procesa parcialmente.
Los hashes se calculan siempre con `to_stable_json()` (claves
ordenadas) — nunca con la serialización compacta por defecto de
Pydantic, que es sensible al orden de inserción de un `dict` y
produciría un hash distinto tras un roundtrip por disco (defecto real
encontrado y corregido durante la integración de esta fase).

Por decisión individual, además: `review_item_id`/`assessment_id`/
`reference_id`/`assessment_artifact_hash` deben coincidir exactamente
con el review item que reclama — una decisión "apuntando" a un item
por `candidate_id` solamente, sin verificar estos campos, nunca se
acepta.

## `PROPOSE_SHADOW_PROMOTION` no es promoción

Ninguna acción del plan — ni siquiera `PROPOSE_SHADOW_PROMOTION` —
crea un candidato nuevo, modifica `CandidateArtifact` V1,
`V2ShadowCandidatesArtifact` ni `InterproceduralRuleCandidatesArtifact`,
ni escribe en Neo4j. Es exclusivamente una **propuesta de dry-run**:
el registro auditable de que un humano aprobó un candidato
`READY_FOR_CONTROLLED_REVIEW` para una eventual promoción real, que
permanece **fuera de alcance** de esta fase (ver "Próxima fase").

## Plan actions

`PromotionPlanAction`/`PromotionPlanItemStatus` resultantes, por
disposición y decisión:

| Disposition | Sin decisión | `APPROVE` | `REJECT` | `DEFER` |
|---|---|---|---|---|
| `BASELINE_V1` | `KEEP_BASELINE` / `NO_DECISION_REQUIRED` | inválida (global, nunca en el item) | inválida | inválida |
| `ALREADY_COVERED` | `SKIP_ALREADY_COVERED` / `NO_DECISION_REQUIRED` | `INVALID_DECISION` (accion sigue fija) | válida, accion sigue fija | válida, accion sigue fija |
| `READY_FOR_CONTROLLED_REVIEW` | `PENDING_REVIEW` / `PENDING_DECISION` | `PROPOSE_SHADOW_PROMOTION` / `VALID` | `REJECT` / `VALID` | `DEFER` / `VALID` |
| `REVIEW_REQUIRED` | `PENDING_REVIEW` / `PENDING_DECISION` | `INVALID_DECISION` (cae a `PENDING_REVIEW`) | `REJECT` / `VALID` | `DEFER` / `VALID` |
| `BLOCKED` / `CONFLICTING` | `BLOCK` / `NO_DECISION_REQUIRED` | `INVALID_DECISION` (cae a `BLOCK`) | válida, accion sigue `BLOCK` | válida, accion sigue `BLOCK` |
| `NOT_EVALUATED` | `DEFER` / `NO_DECISION_REQUIRED` | `INVALID_DECISION` (cae a `DEFER`) | `INVALID_DECISION` (cae a `DEFER`) | `DEFER` / `VALID` |

Una decisión inválida **nunca se ignora en silencio**: para
`BASELINE_V1` (la única disposición que rechaza cualquier decisión de
plano) se registra en `diagnostics` a nivel de artefacto; para el
resto, el `CandidatePromotionPlanItem` conserva `decision_id`/
`decision`/`reason_code`/`reviewer_reference` con
`status=INVALID_DECISION` y `blocking_reasons` explicando el motivo —
la `action` siempre cae al valor seguro por defecto, nunca a lo que la
decisión inválida solicitaba.

## Seguridad de rutas

`--decisions <path>` apunta a un archivo **externo** al repositorio (el
manifiesto humano) — nunca se copia al repositorio, nunca se asume
dentro de un directorio del run. El servicio
(`pipeline/candidate_promotion_plan_service.py::_resolve_decisions_path`)
rechaza: rutas vacías, symlinks, cualquier cosa que no sea un archivo
regular existente. El contenido se trata como JSON no confiable
(python.md): se valida contra `CandidatePromotionDecisionManifest`
antes de influir en cualquier decisión del plan.

## Determinismo

`review_item_id`/`plan_item_id` se derivan de SHA-256 truncado a 24
caracteres hex sobre `reference_id`/`review_item_id` respectivamente —
nunca UUID/timestamp/`hash()` de Python. `review_items`/`plan_items`
siempre se ordenan por su propio ID; el orden de `manifest.decisions`
en la entrada nunca afecta el resultado. Dos ejecuciones sobre los
mismos assessment/review package/manifest producen bytes idénticos —
verificado por tests unitarios y por la integración no-regresión real
(Fase 10, 6 paquetes).

## CLI

```bash
python -m altamira_extractor.cli candidate-promotion-review-package <run_id> [--json]
python -m altamira_extractor.cli candidate-promotion-plan <run_id> --decisions <path> [--json]
```

`candidate-promotion-plan` **requiere** que el review package ya exista
(ejecutar primero `candidate-promotion-review-package`). Ambos
requieren únicamente que `RUN_ID` haya alcanzado `PARSED` (`SUCCEEDED`).
Ninguno se invoca automáticamente desde `ingest`, `resume`, la API ni
la UI; ninguno agrega un `PipelineStage`; `RunState` permanece intacto.

## Limitaciones

- Depende íntegramente de las limitaciones ya documentadas del
  assessment de Fase 9 (`docs/CANDIDATE_PROMOTION_ASSESSMENT.md`) — no
  reinterpreta ni corrige ninguna de ellas.
- La resolución de decisiones duplicadas por `decision_reference` solo
  cubre cadenas de longitud 2 (una decisión que supersede exactamente
  a otra); cualquier caso más complejo se rechaza íntegramente como
  ambiguo, nunca se intenta resolver con una heurística.
- El manifiesto de decisiones es responsabilidad íntegra del proceso
  humano: esta fase no ofrece una UI de revisión, solo el contrato y la
  validación.

## Próxima fase: promoción real

Fase 10 **nunca** promueve un candidato V2/interprocedural a V1 real,
nunca genera `ContextPackage`/`RuleDraft`/reglas Markdown a partir de
un `PROPOSE_SHADOW_PROMOTION`, nunca escribe en Neo4j. Un eventual flujo
de promoción real (que consuma este plan para decidir qué candidatos
aprobados pasan a alimentar el pipeline V1 productivo) queda
explícitamente **fuera de alcance**, documentado aquí como deuda
técnica para una fase futura — nunca implementado como atajo dentro de
esta.

Fase 11 (`docs/UNIFIED_CANDIDATES_SHADOW.md`) consume este plan (los
items `PROPOSE_SHADOW_PROMOTION`/`VALID`) para consolidar, en un unico
artefacto diagnostico, el baseline V1 inmutable junto a las propuestas
shadow ya aprobadas por un humano — agrupando exclusivamente las que
Fase 9 ya demostro `EXACT_MATCH` entre si. Sigue sin ser una promocion
real: Fase 11 tampoco escribe en Neo4j ni genera reglas.

Fase 12 (`docs/UNIFIED_SHADOW_DIFFERENTIAL_VALIDATION.md`) valida, de
forma determinista y puramente estructural, si el artefacto de Fase 11
esta listo para alimentar un eventual flujo downstream tambien en
shadow mode — sigue sin ser una promocion real ni una validacion
funcional.
