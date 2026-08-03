# Catálogo unificado de candidatos y evaluación de promoción (Fase 9 de la ampliación semántica)

## Propósito

Construir una capa **puramente diagnóstica** que catalogue, sin
fusionar ni modificar, los candidatos ya detectados por las **tres**
superficies existentes: `CandidateArtifact` V1 (`Q0`, productivo),
`V2ShadowCandidatesArtifact` (Fase 5, experimental) e
`InterproceduralRuleCandidatesArtifact` (Fase 8, experimental).
Identifica equivalencias exactas y relaciones parciales entre
candidatos de fuentes distintas, detecta conflictos demostrables, y
evalúa criterios de preparación para una **eventual** promoción
funcional — **nunca promueve ningún candidato**. Mismo patrón que
`docs/V2_DETECTORS_SHADOW_MODE.md`/
`docs/INTERPROCEDURAL_RULE_DETECTORS_SHADOW.md`: capa adyacente, bajo
demanda, irrelevante para que un run llegue a `COMPLETED`.

## Las tres fuentes

| Fuente | Contrato | Identidad | ¿Qué expone? | ¿Qué NUNCA expone? |
|---|---|---|---|---|
| V1 | `contracts/candidate.py::CandidateArtifact` | `candidate_id` | `decision_id`, `outcome_code`, `paragraph_id`/`paragraph_name`, `source_file`/`line_start` | `call_site_id`, `input_literal`, `target` explícito, evidencia granular (`evidence_ids`) |
| V2 | `contracts/v2_shadow_candidates.py::V2ShadowCandidatesArtifact` | `candidate_id` | `decision_id` (opcional), `target_variable`/`target_qualified_name`, `resolved_literal`, `semantic_effect_ids`/`propagation_fact_ids`, `comparable_v1_candidate_ids`, `V1V2CandidateComparison[]` ya calculadas | `call_site_id`, `input_literal` |
| INTERPROCEDURAL | `contracts/interprocedural_rule_candidates.py::InterproceduralRuleCandidatesArtifact` | `candidate_id` | `call_site_id`, `target`, `input_literal`/`output_literal`, `barriers`, `evidence[]`, `InterproceduralCandidateComparison[]` ya calculadas (`v1_relation`/`v2_relation`) | `decision_id` (siempre `None`: no puede reconstruirse el ID Neo4j-shaped exacto sin `country_code`/`logical_name`/`version`) |

Esta auditoría inicial (Fase 1 del runbook) es la base del diseño de
`UnifiedCandidateReference`: cada campo opcional (`decision_id`,
`call_site_id`, `input_literal`, `target`) queda `None` exactamente
cuando la fuente de origen nunca lo produce — **nunca se fabrica** un
valor sintético para rellenar un hueco.

## Catálogo unificado, no fusión

`UnifiedCandidateReference` (`contracts/candidate_promotion_
assessment.py`) es una **referencia inmutable** hacia el candidato de
origen: `source` + `source_candidate_id` siempre permiten recuperar el
objeto original completo en su propio artefacto. Este módulo **nunca**
reescribe, reclasifica ni "corrige" un candidato de origen — cada
adaptador (`pipeline/candidate_source_adapters.py`) es una función
pura que recibe el artefacto original (o `None`) y jamás lo modifica.

Dos candidatos de fuentes distintas que resultan `EXACT_MATCH` **no se
combinan en un único objeto**: siguen siendo dos
`UnifiedCandidateReference` separadas, vinculadas por una
`CandidateRelation` de solo lectura. Ninguna evidencia específica de
una fuente se pierde por la existencia de una coincidencia con otra.

## Familias funcionales normalizadas

`UnifiedRuleFamily` se asigna **únicamente** cuando la equivalencia
está demostrada por el propio diseño de los contratos existentes —
**nunca** por semejanza textual entre nombres de enum
(`pipeline/candidate_source_adapters.py`):

- **V1 → `RETURN_CODE` siempre.** No se basa en `RuleCandidate.
  rule_type` (que en la práctica es siempre `None`: ninguna etapa
  distingue todavía una `Decision` IF/MOVE de una de nivel 88
  SET-TO-TRUE), sino en la **garantía estructural** de
  `queries/v1/q0_candidates.cypher`, que exige `sink.semantic_tag =
  'return_code'` en su único `MATCH` — toda fila que Q0 devuelve, por
  construcción de la consulta, describe una asignación de código de
  retorno.
- **V2**: `RETURN_CODE_RULE → RETURN_CODE`, `LEVEL_88_RETURN_CODE_RULE
  → LEVEL_88_RETURN_CODE`, `STATE_CHANGE_RULE → UNKNOWN` (ver más
  abajo).
- **INTERPROCEDURAL**: `RETURN_CODE_RULE → RETURN_CODE`,
  `BY_REFERENCE_RULE → BY_REFERENCE_OUTPUT`, `STATE_TRANSITION_RULE →
  STATE_TRANSITION`.

### El ejemplo canónico de "nunca por semejanza textual"

`V2RuleType.STATE_CHANGE_RULE` **nunca** se mapea a
`UnifiedRuleFamily.STATE_TRANSITION` pese al parecido de nombre con
`InterproceduralRuleType.STATE_TRANSITION_RULE`: son conceptos
estructuralmente distintos.

- V2 `STATE_CHANGE_RULE` es **intraprograma**, siempre `support=
  PARTIAL` (nunca `DETERMINISTIC`), y se dispara para **cualquier**
  escritura determinística de un data item ordinario dentro de una
  decisión — sin exigir ningún `semantic_tag` de estado.
- Fase 8 `STATE_TRANSITION_RULE` es **interprocedural**, alcanza
  `support=DETERMINISTIC` **únicamente** cuando el actual del caller
  tiene `semantic_tag=status`/`status_flag`.

No hay equivalencia demostrable entre ambos: `STATE_CHANGE_RULE` se
conserva como `UNKNOWN`, lo que a su vez **bloquea** cualquier
disposición `READY_FOR_CONTROLLED_REVIEW`/`ALREADY_COVERED` para ese
candidato (invariante contractual, ver "Disposiciones").

## Relaciones: EXACT_MATCH, RELATED, NO_RELATION, NOT_EVALUATED

`pipeline/candidate_relation_analyzer.py` solo relaciona candidatos
**cruzando fuentes** (V1↔V2, V1↔INTERPROCEDURAL, V2↔INTERPROCEDURAL) y
**solo dentro del mismo `program`** (ambos lados no-`None` e iguales):
comparar candidatos de programas distintos no tiene ningún ancla
funcional demostrable.

Usa como evidencia **preferente** las comparaciones **ya calculadas**
por cada fuente — nunca las recalcula desde cero:

- `V2ShadowCandidatesArtifact.comparisons` (`V1V2CandidateComparison`,
  Fase 5) para pares V1↔V2 — `MATCHED → EXACT_MATCH`,
  `RELATED_NOT_EQUIVALENT → RELATED`.
- `InterproceduralRuleCandidatesArtifact.comparisons`
  (`InterproceduralCandidateComparison.v1_relation`/`v2_relation`,
  Fase 8) para pares INTERPROCEDURAL↔V1/V2 — `MATCHED → EXACT_MATCH`,
  `RELATED → RELATED`.

Dentro del ámbito evaluado (mismo programa, ambas fuentes
disponibles): `NO_RELATION` cuando ninguna comparación/vínculo declara
una coincidencia para ese par específico — nunca cuando una fuente
estuvo ausente. `NOT_EVALUATED` (a nivel de criterio de política, no
como objeto `CandidateRelation`: ver "Fuentes ausentes") es la
respuesta correcta cuando una de las dos fuentes de un par nunca
estuvo disponible — la ausencia de evidencia **nunca** se presenta
como evidencia de ausencia.

Toda relación es semánticamente simétrica (A~B ≡ B~A) pero se
serializa **una única vez**: `relation_id_for()` ordena siempre el par
alfabéticamente antes de calcular el digest/asignar
`left_reference_id`/`right_reference_id` — invariante reforzada por el
propio contrato (`_check_pair_is_ordered`).

## Conflictos demostrables

`pipeline/candidate_conflict_analyzer.py` detecta **únicamente**
contradicciones estructurales, agrupando por un ancla funcional
compartida **siempre junto con `target`**:

- `SAME_DECISION_DIFFERENT_OUTPUT`: mismo `(decision_id, target)`,
  `output_literal` distintos.
- `SAME_CALL_SITE_DIFFERENT_OUTPUT`: mismo `(call_site_id, target)`,
  `output_literal` distintos.
- `SAME_TARGET_CONTRADICTORY_OUTPUT`: mismo `(program, target)`,
  `output_literal` distintos.
- `INCOMPATIBLE_RULE_FAMILY`: mismo `decision_id`, dos
  `UnifiedRuleFamily` distintas y ninguna `UNKNOWN`.
- `INVALID_PROVENANCE`: una referencia V2/INTERPROCEDURAL con
  `original_support=DETERMINISTIC` pero sin ningún `evidence_id` —
  incumple, por sí sola, el contrato de su propia fuente (única
  excepción documentada al patrón "conflicto entre dos referencias":
  `CandidateConflict.reference_ids` admite un único elemento
  exclusivamente para este tipo).

**Nota de diseño (encontrada y corregida durante la integración real,
Fase 13):** agrupar `SAME_CALL_SITE_DIFFERENT_OUTPUT` por
`call_site_id` en solitario (sin `target`) producía un falso conflicto
cuando un mismo `CALL` demuestra, legítimamente, un valor `RETURNING`
sobre un target y un valor `BY REFERENCE` sobre **otro** target
simultáneamente — dos canales de salida independientes de la misma
llamada, nunca valores en competencia por el mismo hecho. La
agrupación exige ahora `target` compartido en ambos anclajes
(`decision_id`/`call_site_id`), igual que ya lo exigía
`SAME_TARGET_CONTRADICTORY_OUTPUT` por diseño. Ver
`pipeline/candidate_conflict_analyzer.py::_group_by_anchor_and_target`
y `tests/pipeline/test_candidate_conflict_analyzer.py::
test_same_call_site_different_target_is_never_a_conflict`.

**Nunca** se declara un conflicto por: ausencia de literal (una
referencia sin `output_literal` simplemente no participa en la
comparación del grupo), ausencia de `decision_id`/`call_site_id`/
`target` (esa referencia no entra en ese agrupamiento específico),
soporte parcial, diferencia de provenance, orden de detección o fuente
no disponible.

Los conflictos se calculan **antes** que las relaciones
(`candidate_promotion_assessment_analyzer.py`): todo par ya
identificado como conflictivo se reclasifica con prioridad absoluta a
`CandidateRelationKind.CONFLICT`, nunca a `EXACT_MATCH`/`RELATED`.

## Criterios de preparación (`PromotionCriterionKind`)

Doce criterios discretos, evaluados para cada referencia V2/
INTERPROCEDURAL (para V1 todos quedan `NOT_APPLICABLE`, ver
"Disposiciones"): `DETERMINISTIC_SUPPORT`, `COMPLETE_PROVENANCE`,
`TARGET_AVAILABLE`, `OUTPUT_LITERAL_AVAILABLE`, `NO_BARRIERS`,
`NO_CONFLICTS`, `V1_COMPARISON_AVAILABLE`, `V2_COMPARISON_AVAILABLE`,
`INTERPROCEDURAL_COMPARISON_AVAILABLE`, `INDEPENDENT_CORROBORATION`,
`SUPPORTED_RULE_FAMILY`, `SOURCE_ARTIFACT_VALID`. Cada uno es un juicio
`PASS`/`FAIL`/`NOT_APPLICABLE`/`NOT_EVALUATED` sobre un hecho
estructural ya demostrado — **nunca** un promedio, peso o umbral
arbitrario (ver "Ausencia de puntuación arbitraria").

## Disposiciones (`PromotionDisposition`)

Política declarativa, determinística y versionada
(`pipeline/candidate_promotion_policy.py`, `policy_version="1.0"`):

- **`BASELINE_V1`**: siempre, para toda referencia `source=V1` —
  nunca se evalúa para promoción, puede seguir participando en
  relaciones/conflictos.
- **`ALREADY_COVERED`**: existe `EXACT_MATCH` con V1 y ningún
  conflicto invalida esa referencia.
- **`READY_FOR_CONTROLLED_REVIEW`**: soporte determinístico, familia
  soportada (nunca `UNKNOWN`), provenance completo, `target` y
  `output_literal` presentes, cero barreras, cero conflictos, V1 y la
  fuente de corroboración relevante (V2↔INTERPROCEDURAL, mutuamente)
  estuvieron disponibles, existe corroboración **independiente exacta**
  de una fuente no-V1, y la referencia **no** está ya cubierta por V1.
- **`REVIEW_REQUIRED`**: candidato sólido (determinístico, completo,
  cero conflictos) pero sin esa corroboración independiente —
  **incluye explícitamente** el caso "interprocedural-only" (sin
  relación real V1/V2 demostrable).
- **`BLOCKED`**: soporte parcial/bloqueado, barreras, provenance
  incompleto, `target`/`output_literal` ausente, familia `UNKNOWN`, o
  artefacto de origen inválido.
- **`CONFLICTING`**: existe al menos un `CandidateConflict` que
  involucra la referencia.
- **`NOT_EVALUATED`**: **únicamente** cuando V1 — la única fuente cuya
  ausencia impide siquiera decidir `ALREADY_COVERED` vs. el resto — no
  estuvo disponible. **Nunca** se confunde con `REVIEW_REQUIRED`/
  `BLOCKED`.

`RecommendedAction` es un catálogo **estable** en correspondencia 1:1
con `PromotionDisposition` (`recommended_action_for()`) — nunca texto
libre generado dinámicamente.

## Ausencia de puntuación arbitraria

Ningún campo de este módulo es un score numérico de confianza. Cada
`PromotionCriterionResult` es un juicio discreto sobre un hecho
estructural ya demostrado por una de las tres fuentes (o por su
ausencia/invalidez) — nunca un promedio, peso o umbral configurable.
La disposición final es el resultado de un árbol de decisión
determinístico, no de sumar/ponderar criterios.

## `READY_FOR_CONTROLLED_REVIEW` no es promoción

Ninguna disposición de este módulo — ni siquiera
`READY_FOR_CONTROLLED_REVIEW` — implica que un candidato pasó a ser
una regla oficialmente aprobada. `PromotionDisposition` **nunca**
incluye `PROMOTED`/`AUTO_PROMOTED` (invariante contractual, verificada
también por test). La promoción real de un candidato interprocedural/
V2 a V1 permanece **fuera de alcance** de Fase 9 (ver "Deuda futura").

## Fuentes ausentes

`SourceAvailability` distingue tres estados: `AVAILABLE`,
`NOT_AVAILABLE` (el artefacto nunca se generó/cargó — legítimo,
`prerequisites` de esa fuente ausentes en disco) e `INVALID` (el
artefacto existe, o sus prerequisitos existen, pero el cómputo falló —
**nunca se oculta el error**, bloquea las evaluaciones dependientes vía
criterios `FAIL`, distinto de `NOT_EVALUATED`).

El servicio (`pipeline/candidate_promotion_assessment_service.py`)
determina esto verificando la presencia de los **archivos
prerequisito** de cada fuente antes de intentar el cómputo:

| Fuente | Prerequisito verificado | Diagnóstico si ausente | Diagnóstico si inválido |
|---|---|---|---|
| V1 | `artifacts/06-candidates.json` | `V1_CANDIDATES_UNAVAILABLE_ARTIFACTS_06_CANDIDATES_JSON_ABSENT` | `V1_CANDIDATES_INVALID_ARTIFACTS_06_CANDIDATES_JSON_MALFORMED` |
| V2 | `artifacts/04-semantic-graph.json` + `artifacts/06-candidates.json` | `V2_SHADOW_CANDIDATES_UNAVAILABLE_PREREQUISITES_ABSENT` | `V2_SHADOW_CANDIDATES_INVALID_COMPUTATION_FAILED` |
| INTERPROCEDURAL | `artifacts/02-canonical/` | `INTERPROCEDURAL_RULE_CANDIDATES_UNAVAILABLE_PREREQUISITES_ABSENT` | `INTERPROCEDURAL_RULE_CANDIDATES_INVALID_COMPUTATION_FAILED` |

Importante: los prerequisitos de V2 verificados por Fase 9
(`04-semantic-graph.json`/`06-candidates.json`) son **necesarios pero
no suficientes** — `compute_v2_shadow_candidates_artifact` (Fase 5)
también exige internamente `artifacts/02-canonical/` y que
`SEMANTIC_GRAPH_BUILT`/`CANDIDATES_DETECTED` hayan `SUCCEEDED` en
`RunState.stages`. Si esos archivos están presentes pero esas
condiciones no se cumplen, V2 se marca `INVALID` (nunca
`NOT_AVAILABLE`): el error nunca se oculta, aunque el prerequisito
verificado por Fase 9 sí exista.

La ausencia de cualquiera de las tres fuentes **nunca** es un error
fatal para el servicio: se registra, se agrega un diagnóstico
explícito, y el análisis continúa con las fuentes restantes. La única
excepción es la ausencia de `run.json`/el propio `run_dir`, o que
`PARSED` (`SUCCEEDED`) no se haya alcanzado — la única precondición
dura del servicio.

## Determinismo

`unified_reference_id`/`relation_id`/`conflict_id`/`assessment_id` se
derivan de SHA-256 sobre las partes relevantes, truncado a 24
caracteres hex — nunca UUID, timestamp ni `hash()` de Python (mismo
patrón que Fase 5/6/7/8). `candidate_references`/`relations`/
`conflicts`/`assessments` siempre se ordenan por su ID antes de
construir el artefacto final; el orden de entrada de los tres
artefactos de origen nunca afecta la salida. Dos ejecuciones sobre los
mismos artefactos de entrada producen bytes idénticos — verificado por
tests unitarios y por la integración no-regresión real (Fase 14).

## CLI

```bash
python -m altamira_extractor.cli candidate-promotion-assessment <run_id>
python -m altamira_extractor.cli candidate-promotion-assessment <run_id> --json
```

Requiere que `RUN_ID` ya haya alcanzado `PARSED` (`SUCCEEDED`). Imprime
`run_id`, conteos por fuente, `references_total`, `exact_matches`,
`related`, `conflicts`, conteos por disposición
(`baseline_v1`/`already_covered`/`ready_for_controlled_review`/
`review_required`/`blocked`/`conflicting`/`not_evaluated`),
disponibilidad por fuente, y la ruta relativa del reporte; `--json`
imprime además el artefacto completo en JSON estable. Persiste
**únicamente** `diagnostics/candidate-promotion-assessment.json`. No se
invoca automáticamente desde `ingest`, `resume`, la API ni la UI; no
agrega un `PipelineStage`; no modifica `RunState`.

## Limitaciones

- Depende íntegramente de las limitaciones ya documentadas de V1
  (Q0), Fase 5 (V2 shadow) y Fase 8 (interprocedural shadow) — no
  reinterpreta ni corrige ninguna de ellas.
- Las relaciones solo se evalúan **dentro del mismo programa**: dos
  candidatos funcionalmente equivalentes en programas distintos nunca
  se relacionan (ningún ancla estructural lo demuestra).
- `INDEPENDENT_CORROBORATION` exige coincidencia **exacta**
  (`EXACT_MATCH`), nunca `RELATED` — una relación parcial nunca es
  suficiente para `READY_FOR_CONTROLLED_REVIEW`.
- No detecta reglas oficialmente aprobadas: es exclusivamente
  diagnóstico, igual que el resto de artefactos de la ampliación
  semántica.

## Deuda futura: promoción real

Fase 9 **nunca** promueve un candidato V2/interprocedural a V1 ni
genera `ContextPackage`/`RuleDraft`/reglas Markdown a partir de un
candidato `READY_FOR_CONTROLLED_REVIEW`. Un eventual flujo de
promoción real (que consuma este diagnóstico para decidir cuáles
candidatos experimentales pasan a alimentar el pipeline V1 productivo)
queda explícitamente **fuera de alcance**, documentado aquí como deuda
técnica para una fase futura — nunca implementado como atajo dentro de
esta.
