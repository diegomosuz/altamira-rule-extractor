# Artefacto unificado de candidatos en shadow mode (Fase 11)

Rama: `feat/unified-candidate-artifact-shadow`. Baseline: v1.9.0.

## Propósito

Fases 9 y 10 producen, en secuencia: un catálogo unificado de
candidatos V1/V2/interprocedural con relaciones y disposiciones
(`docs/CANDIDATE_PROMOTION_ASSESSMENT.md`), un paquete de revisión
humana, y un plan de promoción en dry-run
(`docs/CONTROLLED_CANDIDATE_PROMOTION_PLAN.md`). Ninguna de esas fases
consolida el resultado en un único artefacto que muestre, lado a lado,
el baseline V1 y las propuestas humanas ya aprobadas.

Fase 11 cierra ese hueco: `diagnostics/unified-candidates-shadow.json`
es un artefacto **diagnóstico, no contractual, generado bajo demanda**,
que:

1. preserva `CandidateArtifact` V1 como baseline inmutable;
2. incorpora como propuestas shadow **únicamente** los items del plan
   de Fase 10 con `action=PROPOSE_SHADOW_PROMOTION` y `status=VALID`;
3. resuelve cada propuesta contra su candidato fuente real (V2 o
   interprocedural);
4. agrupa exactamente las propuestas que Fase 9 ya demostró
   `EXACT_MATCH` entre sí;
5. preserva todos los IDs de candidato fuente, decisiones y
   procedencia;
6. detecta inconsistencias entre el plan y las fuentes actuales;
7. compara el conjunto shadow resultante contra V1;
8. produce un artefacto determinista y auditable;
9. **nunca** modifica `CandidateArtifact` V1;
10. **nunca** alimenta ninguna etapa productiva.

## Las dos líneas (`lanes`), nunca fusionadas

- **`BASELINE_V1`** (`UnifiedBaselineCandidateReference`): una
  referencia inmutable a **cada** candidato V1 real (Q0), exista o no
  un plan item que lo mencione. Adaptación pura
  (`pipeline/unified_shadow_baseline_adapter.py`, mismo principio que
  el adaptador V1 de Fase 9): nunca modifica V1, nunca completa un
  campo ausente con V2/interprocedural, nunca constituye, por sí
  misma, una promoción.
- **`SHADOW_PROPOSAL`** (`UnifiedShadowCandidateGroup`): derivada
  **exclusivamente** de `CandidatePromotionPlanItem` con
  `action=PROPOSE_SHADOW_PROMOTION` y `status=VALID` — cada propuesta
  está necesariamente ligada a una decisión humana
  `APPROVE_FOR_SHADOW_PROMOTION` real (Fase 10), y proviene
  únicamente de V2 o INTERPROCEDURAL (V1 nunca produce un
  `UnifiedShadowSourceMember`, invariante 11 del contrato).

Ambas líneas conviven en el mismo artefacto pero nunca se mezclan: un
grupo shadow no puede "completarse" con datos del baseline, ni el
baseline se reescribe con datos de una propuesta.

## Origen exclusivo: `PROPOSE_SHADOW_PROMOTION` + `VALID`

Todo `CandidatePromotionPlanItem` que no cumpla ambas condiciones se
registra en `excluded_plan_items` con un `UnifiedShadowExclusionReason`
explícito (`BASELINE_ITEM`, `ALREADY_COVERED`, `BLOCKED_ITEM`,
`PENDING_DECISION`, `REJECTED`, `DEFERRED`, `PLAN_ITEM_NOT_VALID`, o un
motivo de resolución fallida — ver más abajo). Nunca se reinterpreta la
decisión humana: la exclusión solo documenta por qué el item no entra
al artefacto shadow como miembro.

`Σ shadow_members + Σ excluded_plan_items == Σ plan_items` siempre —
reconciliado por un validador del contrato
(`UnifiedCandidatesShadowSummary._check_group_counts_reconcile`).

## Resolución del candidato fuente

Un plan item aprobado solo aporta un `UnifiedShadowSourceMember` si su
candidato fuente real (V2 o interprocedural) se localiza y su
identidad coincide con lo que Fase 9 ya afirmó
(`pipeline/unified_shadow_source_resolver.py`):

- `UNKNOWN_SOURCE`: el artefacto fuente (V2/interprocedural) no fue
  provisto en absoluto.
- `SOURCE_CANDIDATE_NOT_FOUND`: el artefacto existe pero
  `source_candidate_id` no aparece en él.
- `IDENTITY_MISMATCH`: el candidato existe pero su
  target/output/decision_id/call_site_id/program/paragraph no coincide
  con la referencia de Fase 9.

Cada fallo es un motivo de exclusión distinto, nunca se sustituye un
candidato inválido por uno vacío. El resolutor nunca relee
`source_text`, nunca reinterpreta un `rule_type` por semejanza de
nombre, nunca fabrica un ID ausente.

Para cada candidato resuelto se calcula `source_candidate_hash`
(SHA-256 de `to_stable_json()` del objeto real) — una procedencia
per-candidato que ni Fase 9 ni Fase 10 ofrecían (ambas solo hashean el
artefacto completo).

## Agrupación `EXACT_MATCH`

`pipeline/unified_shadow_candidate_grouper.py` agrupa
`UnifiedShadowSourceMember` en componentes conexos de relaciones
`CandidateRelationKind.EXACT_MATCH` **ya declaradas por Fase 9** —
nunca recalcula semejanza, nunca agrupa por texto, target sin output,
output sin target, family parcial, `RELATED`, o una heurística nueva.

Cada componente conexo se revalida antes de aceptarse como grupo:
misma `UnifiedRuleFamily`, mismo `program`, mismo `target`, mismo
`output_literal` en **todos** sus miembros. Si el componente no es
consistente en estos cuatro ejes (una inconsistencia que, para
relaciones `EXACT_MATCH` genuinamente calculadas por Fase 9, es
estructuralmente casi inalcanzable por transitividad de igualdad — ver
"Limitaciones"), **todos** sus miembros se excluyen con
`INCONSISTENT_EXACT_MATCH_GROUP`: nunca se fabrican varios grupos
válidos parciales, nunca se elige arbitrariamente un subconjunto
consistente.

Ningún miembro se descarta ni se elige como "ganador": todos los
`member_ids` de un grupo conservan igual jerarquía. El identificador
del grupo (`unified_shadow_candidate_id`) se deriva de un hash
determinista sobre `member_ids` ordenados + family + program + target
+ output_literal — nunca de un candidato "representante".

## Comparación contra el baseline V1

`pipeline/unified_shadow_baseline_comparator.py` usa como evidencia
preferente lo ya calculado por Fase 9
(`CandidateRelation.relation_kind` / `CandidateConflict`) — nunca
recalcula semejanzas aproximadas. La única verificación estructural
adicional (no aproximada) es "mismo `(program, target)` con
`output_literal` distinto", idéntica en espíritu a
`CandidateConflictType.SAME_TARGET_CONTRADICTORY_OUTPUT` de Fase 9.

Prioridad: `CONFLICTS_WITH_BASELINE` > `EXACT_BASELINE_MATCH` >
`RELATED_TO_BASELINE` > `NOT_IN_BASELINE` (cuando V1 está disponible),
o `NOT_EVALUATED` cuando V1 está ausente/inválido — **nunca** se
disfraza la ausencia de una fuente como "sin equivalente".

### Regla de seguridad: `DUPLICATE_BASELINE_COVERAGE`

Un plan item `PROPOSE_SHADOW_PROMOTION` cuyo resultado sea
`EXACT_BASELINE_MATCH` **nunca** se incorpora como grupo `VALID`: debe
quedar `status=DUPLICATE_BASELINE_COVERAGE`, con un diagnóstico
explícito. Esto demuestra una desalineación entre el plan (aprobado
como si fuera una propuesta nueva) y las fuentes actuales (V1 ya lo
cubre) — nunca se oculta silenciosamente.

Un grupo `CONFLICTS_WITH_BASELINE` permanece `BLOCKED`, con la misma
lógica: nunca se presenta como una propuesta válida un candidato que
demostrablemente contradice el baseline.

## Exclusiones del plan

Todo plan item se representa exactamente una vez: como
`UnifiedShadowSourceMember` (grupo `VALID`, `DUPLICATE_BASELINE_COVERAGE`
o `BLOCKED`) o como `UnifiedShadowExcludedPlanItem` — nunca en ambos,
nunca en ninguno. El mapeo de exclusión es puramente mecánico:

| Acción/estado del plan item                  | Motivo de exclusión              |
|-----------------------------------------------|-----------------------------------|
| `status=INVALID_DECISION`                     | `PLAN_ITEM_NOT_VALID`             |
| `action=KEEP_BASELINE`                        | `BASELINE_ITEM`                   |
| `action=SKIP_ALREADY_COVERED`                 | `ALREADY_COVERED`                 |
| `action=BLOCK`                                | `BLOCKED_ITEM`                    |
| `action=PENDING_REVIEW`                       | `PENDING_DECISION`                |
| `action=REJECT`                               | `REJECTED`                        |
| `action=DEFER`                                | `DEFERRED`                        |
| resolución del candidato fuente fallida       | `UNKNOWN_SOURCE` / `SOURCE_CANDIDATE_NOT_FOUND` / `IDENTITY_MISMATCH` |
| binding plan↔review item inconsistente        | `IDENTITY_MISMATCH`               |
| referencia de assessment inválida/ausente     | `SOURCE_CANDIDATE_NOT_FOUND`      |
| componente `EXACT_MATCH` inconsistente        | `INCONSISTENT_EXACT_MATCH_GROUP`  |

## Determinismo

Dos ejecuciones sobre los mismos artefactos de entrada
(`CandidateArtifact` V1, `V2ShadowCandidatesArtifact`,
`InterproceduralRuleCandidatesArtifact`, assessment, review package,
plan) producen **bytes idénticos**: todos los IDs se derivan por
SHA-256 sobre entradas canónicas (nunca UUID, timestamp ni `hash()` de
Python), todas las listas se ordenan, `atomic_write_json` serializa con
`to_stable_json()` (claves ordenadas). Verificado con paquetes reales
en la integración de esta fase (ver "Integración real") y en el
suite de no-regresión sobre 7 paquetes reales.

## Integración real

Validado con el parser Java real y Neo4j real sobre un paquete
sintético (`CALLER10`/`CALLEE10` invocando entre sí, más `SALDOCON1`
como fuente adicional de baseline V1): el pipeline completo
(`ingest` → `candidate-promotion-assessment` →
`candidate-promotion-review-package` → `candidate-promotion-plan` →
`unified-candidates-shadow`) produjo, con datos 100% reales:

- 3 candidatos V1 baseline (`BASELINE_V1` → `KEEP_BASELINE`).
- Candidatos V2 en conflicto real (`SAME_TARGET_CONTRADICTORY_OUTPUT`
  entre 3 candidatos de `SALDOCON1`; `INCOMPATIBLE_RULE_FAMILY` entre
  dos candidatos V2 de `CALLER10` derivados de la misma sentencia
  `SET ... TO TRUE`) → disposición `CONFLICTING` → acción `BLOCK`.
- 1 candidato V2 `BLOCKED` real (`MOVE` de una variable dinámica, sin
  literal resoluble) → acción `BLOCK`.
- 1 candidato interprocedural real, `DETERMINISTIC`, con
  `EXACT_MATCH` real contra un candidato V2 (vía
  `InterproceduralCandidateComparison.v2_relation`, Fase 8) →
  disposición `READY_FOR_CONTROLLED_REVIEW` → aprobado por un
  manifiesto de decisión real → `PROPOSE_SHADOW_PROMOTION`/`VALID` →
  un grupo shadow `VALID`, `NOT_IN_BASELINE`.
- El artefacto final reconcilia 10 plan items = 1 miembro shadow + 9
  excluidos (3 `BASELINE_ITEM`, 6 `BLOCKED_ITEM`).
- Determinismo confirmado byte a byte en dos ejecuciones consecutivas.

El escenario contractual completo de Parte 14 (dos propuestas
*equivalentes* — una V2, una interprocedural — agrupadas en un único
`UnifiedShadowCandidateGroup`) está cubierto por
`tests/pipeline/test_unified_candidates_shadow_analyzer.py::test_two_equivalent_proposals_produce_single_valid_group`,
construido a mano sobre el analizador real (mismo patrón que "casos
aislados de integración o contractuales" usado por Fases 9/10 para
escenarios de baja probabilidad estructural): en el paquete COBOL real
de esta fase, lograr que **ambas** mitades de un par `EXACT_MATCH`
terminen simultáneamente aprobables (ninguna en conflicto) resultó
estructuralmente esquivo — ver "Limitaciones" para el detalle exacto
de por qué un candidato V2 `DETERMINISTIC` visible para el detector
tiende a también serlo para Q0 (`ALREADY_COVERED`) o a colisionar con
otro detector V2 sobre la misma sentencia (`CONFLICTING`).

## CLI

```
python -m altamira_extractor.cli unified-candidates-shadow <run_id> [--json]
```

Requiere que el run haya alcanzado `PARSED` (`SUCCEEDED`), que
`artifacts/06-candidates.json` (V1) exista, y que
`diagnostics/candidate-promotion-review-package.json` y
`diagnostics/candidate-promotion-plan.json` ya existan —
**nunca** los regenera. Persiste únicamente
`diagnostics/unified-candidates-shadow.json`. Nunca acepta decisiones
por CLI, nunca modifica ningún otro comando existente.

## Por qué permanece "shadow"

Igual que Fase 9/10: `PROPOSE_SHADOW_PROMOTION` sigue sin ser una
promoción real. El artefacto de Fase 11 es una consolidación
**diagnóstica** de propuestas ya aprobadas por un humano — nunca
escribe en Neo4j, nunca genera `ContextPackage`/`RuleDraft`/reglas
Markdown, nunca reemplaza `artifacts/06-candidates.json`, nunca crea un
`CandidateArtifact` productivo nuevo.

## Limitaciones

- La verificación estructural de conflicto "mismo `(program, target)`
  con `output_literal` distinto" **nunca puede disparar contra V1**
  con el adaptador baseline actual: `UnifiedBaselineCandidateReference.target`
  es siempre `None` (Q0/`RuleCandidate` no tiene concepto de target),
  el mismo motivo por el que `SAME_TARGET_CONTRADICTORY_OUTPUT` de Fase
  9 tampoco puede disparar contra V1. Verificado con una prueba
  unitaria dedicada del comparador (target inyectado manualmente en un
  baseline de prueba) que confirma la lógica en sí es correcta.
- `decision_reference_id` (cadena de supersesión de una decisión
  humana) nunca se puede poblar a partir del plan: `CandidatePromotionPlanItem`
  (Fase 10) no conserva el campo `decision_reference` original del
  manifiesto — ese dato solo existe en el manifiesto humano, que Fase
  11 nunca lee. El campo queda `None` en todo miembro shadow.
  Documentado como limitación conocida, no como error.
- Un componente `EXACT_MATCH` genuinamente inconsistente (family/
  program/target/output distintos entre miembros) es estructuralmente
  casi inalcanzable con datos reales de Fase 9, dado que `EXACT_MATCH`
  ya exige esa igualdad par a par y la igualdad es transitiva —
  `INCONSISTENT_EXACT_MATCH_GROUP`/`UnifiedShadowGroupStatus.INCONSISTENT_MEMBERS`
  quedan como una salvaguarda defensiva (nunca confiar ciegamente en
  una garantía ajena), ejercitada en los tests con una relación
  `EXACT_MATCH` deliberadamente inconsistente construida a mano.
- Un candidato V2 con soporte `DETERMINISTIC` originado por una
  asignación directa de literal bajo una decisión (`MOVE literal TO
  target`) es, en la práctica, **siempre** también visible para Q0
  (V1) — ambos usan la misma señal estructural de "decisión → literal
  en el mismo párrafo" — por lo que termina en disposición
  `ALREADY_COVERED`, nunca `READY_FOR_CONTROLLED_REVIEW`. Un candidato
  V2 `DETERMINISTIC` que además sea invisible para Q0 requiere una vía
  que Q0 no recorre (verificado con un patrón real: `SET
  condition-name TO TRUE` sobre un nivel 88, que Q0 no resuelve por
  no originarse en un `MOVE` con literal directo, pero que sí produce
  un candidato V2 `LEVEL_88_RETURN_CODE_RULE`).

## Próxima fase

Fase 11 tampoco resuelve la promoción real (deuda documentada desde
Fase 9/10): un eventual flujo que tome un grupo shadow `VALID` y lo
promueva a `CandidateArtifact` V1 productivo permanece fuera de
alcance, para una fase futura. Fase 12
(`docs/UNIFIED_SHADOW_DIFFERENTIAL_VALIDATION.md`) da el primer paso
hacia esa promoción futura: valida, de forma determinista y
estructural (nunca funcional), si este artefacto está listo para
alimentar un flujo downstream también en shadow mode.

## Ver también

- `docs/CANDIDATE_PROMOTION_ASSESSMENT.md` (Fase 9): catálogo unificado,
  relaciones, conflictos, disposiciones.
- `docs/CONTROLLED_CANDIDATE_PROMOTION_PLAN.md` (Fase 10): paquete de
  revisión humana y plan de promoción en dry-run.
- `docs/UNIFIED_SHADOW_DIFFERENTIAL_VALIDATION.md` (Fase 12):
  validación diferencial estructural de este artefacto contra V1.
