# Validación diferencial del artefacto unificado en shadow mode (Fase 12)

Rama: `feat/unified-shadow-differential-validation`. Baseline: v1.10.0.

## Propósito

Fase 11 produce `diagnostics/unified-candidates-shadow.json`: un
artefacto que consolida el baseline V1 y las propuestas shadow ya
aprobadas por un humano, sin promover nada. Ninguna fase anterior
verifica, de forma determinista y auditable, si ese artefacto cumple
las condiciones **técnicas mínimas** para alimentar, en una fase
posterior, un flujo downstream también en shadow mode.

Fase 12 cierra ese hueco: `diagnostics/unified-shadow-validation-report.json`
es un reporte **diagnóstico, no contractual, generado bajo demanda**,
que:

1. verifica integridad y vigencia de todas las fuentes (V1, V2/
   interprocedural cuando aplican, assessment, review package, plan,
   el propio artefacto unificado);
2. verifica que el baseline V1 esté completo;
3. valida que cada propuesta shadow provenga de una decisión humana
   aprobada real;
4. valida la consistencia interna de members y groups;
5. mide el diferencial estructural contra V1 (reutilizando,
   nunca recalculando, la clasificación que Fase 11 ya produjo);
6. detecta duplicados, conflictos e inconsistencias;
7. evalúa completitud de evidence y provenance;
8. verifica trazabilidad completa hasta el candidato fuente y la
   decisión humana;
9. verifica determinismo;
10. aplica gates discretos y auditables;
11. distingue siempre validez **estructural** de corrección
    **funcional**;
12. **nunca** produce un score numérico arbitrario;
13. **nunca** modifica ni promueve ningún candidato;
14. **nunca** ejecuta `ContextPackage`/`RuleDraft`/guardrails.

## Validez estructural vs. corrección funcional

Esta distinción gobierna todo el módulo y nunca se difumina:

- **Validez estructural** — verificable automáticamente: integridad de
  hashes, consistencia de IDs, existencia de fuentes, provenance,
  evidence, coherencia de agrupación, ausencia de duplicados/
  conflictos, determinismo, reconciliación de conteos.
- **Corrección funcional** — **no** puede inferirse automáticamente:
  si la regla representa correctamente el negocio, si un literal es
  funcionalmente correcto, si una omisión es un falso negativo,
  precisión/recall reales. Ningún gate de Fase 12 afirma esto. El
  issue `FUNCTIONAL_VALIDATION_REQUIRED` (severidad `INFO`) acompaña
  **siempre** un reporte evaluado, como recordatorio explícito de este
  límite — nunca se omite, nunca se convierte en advertencia
  descartable.

`UnifiedShadowValidationDisposition.QUALIFIED_FOR_DOWNSTREAM_SHADOW`
significa **únicamente** que el artefacto superó los gates
estructurales para ejecutar un flujo downstream **en paralelo y sin
impacto productivo**. Nunca significa: regla validada
funcionalmente, candidato promovido, precisión demostrada,
autorización productiva.

## Gates

Doce gates, cada uno exactamente una vez en
`UnifiedShadowValidationReport.gate_results`:

| Gate                              | Qué verifica                                                          |
|------------------------------------|-------------------------------------------------------------------------|
| `SOURCE_INTEGRITY`                | Disponibilidad, versión y cadena de hashes de todas las fuentes.       |
| `BASELINE_COMPLETENESS`           | Todo candidato V1 aparece exactamente una vez en el baseline.          |
| `PLAN_BINDING_INTEGRITY`          | Todo plan item real está contabilizado (miembro o excluido).           |
| `MEMBER_SOURCE_RESOLUTION`        | Cada miembro resuelve contra su candidato fuente real (V2/IP).         |
| `GROUP_INTERNAL_CONSISTENCY`      | Miembros existen, family/target/output/program únicos por grupo.       |
| `BASELINE_DIFFERENTIAL_SAFETY`    | Coherencia interna de la clasificación contra V1 (Fase 11).            |
| `EVIDENCE_COMPLETENESS`           | Evidence estructural no vacía por miembro/grupo.                       |
| `PROVENANCE_COMPLETENESS`         | Provenance de gobierno no vacía y preservada por miembro/grupo.        |
| `DECISION_TRACEABILITY`           | Cadena completa assessment → review item → plan item → decisión.       |
| `SUMMARY_RECONCILIATION`          | El resumen reconcilia exactamente con el contenido real del reporte.   |
| `DETERMINISTIC_SERIALIZATION`     | Ausencia de evidencia demostrable de inestabilidad de serialización.   |
| `DOWNSTREAM_SHADOW_ELIGIBILITY`   | Gate-resumen: ¿al menos un grupo es elegible? Nunca `blocking`.        |

Los 11 primeros son `required=True`/`blocking=True` — un `FAIL`
sistémico ahí puede forzar `disposition=BLOCKED` (vía un issue
`BLOCKING` asociado). `DOWNSTREAM_SHADOW_ELIGIBILITY` es
`required=True`/`blocking=False`: es un **síntoma** agregado de los
resultados por grupo, nunca la causa de un bloqueo — su propio `FAIL`
solo refleja "cero grupos elegibles", nunca añade severidad `BLOCKING`
por sí mismo.

Un gate falla (`FAIL`) cuando al menos un issue de severidad `ERROR` o
`BLOCKING` se le asoció; `WARNING`/`INFO` nunca hacen fallar un gate.
`DETERMINISTIC_SERIALIZATION` se demuestra **fuera** del analizador —
doble ejecución del servicio/tests con los mismos insumos, nunca
autocomparación dentro de una única llamada (ver "Determinismo").

## Issues: código, severidad, catálogo cerrado

Cada `UnifiedShadowValidationIssue` tiene un `code` de un catálogo
`StrEnum` cerrado (30 valores) con severidad **por defecto** fija
(`INFO`/`WARNING`/`ERROR`/`BLOCKING`) — nunca decidida ad-hoc por cada
validador. `message_code` deriva 1:1 de `code`
(`MSG_{code}`) y nunca se usa para decidir lógica, solo `code`/
`severity` lo hacen.

- **`BLOCKING`** (fuerza `disposition=BLOCKED`): fuente ausente/
  inválida, hash desactualizado o inconsistente, roundtrip de
  serialización fallido, baseline incompleto, plan item no
  contabilizado, miembro sin fuente/identidad/aprobación,
  `GROUP_DUPLICATES_BASELINE`, `GROUP_CONFLICTS_WITH_BASELINE`,
  `GROUP_BASELINE_NOT_EVALUATED`, resumen inconsistente,
  serialización no determinista.
- **`ERROR`** (bloquea solo la elegibilidad de ESE grupo, nunca fuerza
  `BLOCKED` global): miembro sin evidence/provenance, familias/
  targets/outputs múltiples dentro de un grupo, alcance inconsistente,
  familia `UNKNOWN`, grupo `BLOCKED` por soporte.
- **`WARNING`**: `GROUP_RELATED_TO_BASELINE` (relacionado pero no
  equivalente — nunca tratado como conflicto), `NO_VALID_SHADOW_GROUPS`.
- **`INFO`**: `GROUP_NOT_IN_BASELINE` (informativo — nunca implica
  corrección funcional), `FUNCTIONAL_VALIDATION_REQUIRED` (siempre
  presente cuando la evaluación se completa).

## Diferencial contra V1

`pipeline/unified_shadow_differential_validator.py` clasifica cada
grupo usando **exclusivamente** `comparison_to_v1` — el resultado que
Fase 11 ya calculó a partir de evidencia real de Fase 9
(`CandidateRelation`/`CandidateConflict`). Nunca recalcula semejanza,
nunca inventa un conflicto:

| `comparison_to_v1`         | Issue                          | Severidad  | Elegible |
|------------------------------|----------------------------------|------------|----------|
| `EXACT_BASELINE_MATCH`       | `GROUP_DUPLICATES_BASELINE`      | `BLOCKING` | Nunca    |
| `CONFLICTS_WITH_BASELINE`    | `GROUP_CONFLICTS_WITH_BASELINE`  | `BLOCKING` | Nunca    |
| `RELATED_TO_BASELINE`        | `GROUP_RELATED_TO_BASELINE`      | `WARNING`  | Nunca    |
| `NOT_IN_BASELINE`            | `GROUP_NOT_IN_BASELINE`          | `INFO`     | Posible  |
| `NOT_EVALUATED`               | `GROUP_BASELINE_NOT_EVALUATED`   | `BLOCKING` | Nunca    |

`NOT_EVALUATED` (V1 ausente/inválido al momento de comparar) **nunca**
se confunde con `NOT_IN_BASELINE` (V1 disponible, sin equivalente):
son estados semánticamente distintos con severidad distinta.

## Elegibilidad downstream

`pipeline/unified_shadow_validation_policy.py::is_group_downstream_eligible`
exige **todas** estas condiciones simultáneamente — nunca una mayoría,
nunca un puntaje:

`group_status == VALID` **y** `comparison_to_v1 == NOT_IN_BASELINE`
**y** family ≠ `UNKNOWN` **y** support ≠ `BLOCKED` **y** resolución de
fuente completa para todos los miembros **y** evidence completo **y**
provenance completo **y** trazabilidad de decisión completa **y** cero
issues `ERROR`/`BLOCKING` asociados al grupo.

## Dispositions

Derivación estrictamente secuencial y auditable
(`unified_shadow_validation_policy.py::derive_disposition`) — nunca
suma de puntos, nunca porcentaje, nunca umbral dinámico:

1. **`NOT_EVALUATED`**: una fuente requerida está ausente o inválida
   — se evalúa primero, sin importar el resto. El reporte **igual se
   genera y persiste** (nunca un crash del servicio).
2. **`BLOCKED`**: existe al menos un issue `BLOCKING`.
3. **`QUALIFIED_FOR_DOWNSTREAM_SHADOW`** / **`QUALIFIED_WITH_WARNINGS`**:
   todos los gates requeridos `PASS`, cero `BLOCKING`, al menos un
   grupo elegible — con (`QUALIFIED_WITH_WARNINGS`) o sin
   (`QUALIFIED_FOR_DOWNSTREAM_SHADOW`) advertencias.
4. **`REVIEW_REQUIRED`**: en cualquier otro caso — integridad global
   válida, cero corrupción estructural, pero ningún grupo queda
   elegible (por ejemplo: cero grupos en el artefacto, o todos
   relacionados/duplicados/conflictivos con V1).

## Evidence, provenance y trazabilidad de decisión

`pipeline/unified_shadow_evidence_validator.py` distingue dos
dimensiones que nunca se fusionan:

- **Evidence estructural** (`evidence_ids`): señales técnicas
  concretas (`effect::…`/`fact::…`/`evidence::…`) que sustentan la
  detección — nunca una afirmación de corrección funcional.
- **Provenance de gobierno** (`provenance_references`): la cadena
  administrativa hasta el candidato fuente y la decisión humana — de
  qué statement/programa proviene, nunca si la regla es correcta.

La trazabilidad de decisión verifica la cadena **completa**:
`assessment_id` existe en el assessment real, el `review_item_id`
existe en el review package real (con `assessment_id`/`reference_id`
coincidentes), el `plan_item_id` existe en el plan real, y
`review_decision_id` está presente. Cualquier eslabón roto es
`PLAN_BINDING_MISMATCH`, nunca silenciosamente ignorado.

## Determinismo

Dos ejecuciones sobre los mismos artefactos de entrada producen
**bytes idénticos**: `issue_id` se deriva por SHA-256 sobre
`code`+`gate`+referencias ordenadas (nunca UUID, timestamp ni
`hash()` de Python), todas las listas se ordenan, `atomic_write_json`
serializa con `to_stable_json()` (claves ordenadas). El servicio nunca
demuestra determinismo comparando el artefacto consigo mismo: (1)
carga los bytes originales del artefacto unificado, (2) calcula su
hash estable, (3) ejecuta el analizador, (4) serializa vía
`to_stable_json()`, (5) una segunda ejecución con los mismos insumos
produce bytes idénticos — verificado con una recarga completa desde
disco en cada prueba de determinismo, nunca reutilizando objetos en
memoria de una ejecución anterior.

`UnifiedShadowValidationIssueCode.UNIFIED_ARTIFACT_HASH_MISMATCH` es
una verificación **dedicada** de roundtrip: recalcula el hash del
artefacto unificado ya cargado en memoria (`to_stable_json()`) y lo
compara contra el hash que el servicio calculó sobre los **bytes
crudos originales** del archivo — distinto de
`SOURCE_HASH_MISMATCH` (deriva entre artefactos, no de serialización).

## Integridad de fuentes: nunca un crash

A diferencia de Fase 11 (que **produce** un artefacto nuevo y por eso
exige sus fuentes de forma dura), Fase 12 **valida** lo que ya existe
— la ausencia de una fuente es en sí misma una respuesta estructural
válida (`NOT_EVALUATED`), nunca una excepción de servicio. Solo tres
condiciones son fallos duros del servicio (el reporte no se genera,
exit code distinto de cero en el CLI): el run no existe, `run.json`
es inválido, o el run no alcanzó `PARSED` (`SUCCEEDED`). Todo lo demás
— V1/V2/interprocedural/assessment/review package/plan/artefacto
unificado ausente o inválido — se refleja como hallazgo estructural
dentro de un reporte que igual se persiste.

## Integración real

Validado con el parser Java real y Neo4j real, reutilizando
**exactamente** el mismo escenario real de Fase 11 (`CALLER10`
llamando a `CALLEE10`, un candidato V2 `V2_RETURN_CODE_PROPAGATION`
vía `PROPAGATED_LITERAL` — `MOVE 'R001' TO WS-TEMP` seguido de `MOVE
WS-TEMP TO WS-COD-RETORNO` dentro de la misma rama del `IF`,
invisible para Q0 — y un candidato interprocedural
`RETURN_CODE_RULE`): el pipeline completo (`ingest` →
`candidate-promotion-assessment` → `candidate-promotion-review-package`
→ `candidate-promotion-plan` → `unified-candidates-shadow` →
`unified-shadow-validate`) produjo, con datos 100% reales:

- cero candidatos V1 equivalentes, cero conflictos;
- 2 `shadow_members` (V2 + interprocedural), `EXACT_MATCH` real;
- 1 `shadow_group` `VALID`/`NOT_IN_BASELINE`;
- los 12 gates `PASS`;
- cero issues `BLOCKING`/`ERROR`;
- el grupo `downstream_shadow_eligible=True`;
- disposition `QUALIFIED_FOR_DOWNSTREAM_SHADOW`;
- los únicos issues presentes: `GROUP_NOT_IN_BASELINE` (`INFO`) y
  `FUNCTIONAL_VALIDATION_REQUIRED` (`INFO`) — nunca una afirmación de
  precisión ni de promoción real;
- determinismo confirmado byte a byte en dos ejecuciones consecutivas
  del servicio completo (recarga desde disco en cada una).

Test automatizado:
`tests/parser_integration/test_unified_shadow_validation_integration.py::test_real_two_equivalent_proposals_qualify_for_downstream_shadow`.

## Casos negativos aislados

`tests/pipeline/test_unified_shadow_validation_negative_cases.py`
demuestra, sobre una variante sintética del escenario real donde
`MEMBER_SOURCE_RESOLUTION` pasa genuinamente (candidato V2 real cuya
identidad coincide con la referencia de Fase 9), siete escenarios
aislados:

- **A** — Duplicado exacto con V1 → `GROUP_DUPLICATES_BASELINE`,
  nunca elegible, `disposition=BLOCKED`.
- **B** — Conflicto con V1 → `GROUP_CONFLICTS_WITH_BASELINE`, nunca
  elegible, `disposition=BLOCKED`.
- **C** — Relacionado (no equivalente) con V1 → advertencia, nunca
  auto-elegible, nunca tratado como conflicto,
  `disposition=REVIEW_REQUIRED`.
- **D** — V1 no evaluable → `GROUP_BASELINE_NOT_EVALUATED`, nunca
  confundido con `NOT_IN_BASELINE`, nunca elegible.
- **E** — Evidence incompleta → `EVIDENCE_COMPLETENESS` falla, grupo
  nunca elegible.
- **F** — Provenance incompleta → `PROVENANCE_COMPLETENESS` falla,
  grupo nunca elegible.
- **G** — Hash de V1 desactualizado → `SOURCE_INTEGRITY` falla,
  `disposition=NOT_EVALUATED`, nunca corregido silenciosamente.

## CLI

```
python -m altamira_extractor.cli unified-shadow-validate <run_id> [--json]
```

Requiere únicamente que el run haya alcanzado `PARSED` (`SUCCEEDED`).
Persiste **únicamente** `diagnostics/unified-shadow-validation-report.json`.
Exit code 0 cuando el reporte se genera exitosamente — incluso si la
disposition es `REVIEW_REQUIRED`, `BLOCKED` o `NOT_EVALUATED`; exit
code distinto de cero únicamente ante un fallo técnico real (run
inexistente, `run.json` inválido, etapa insuficiente, fallo de
escritura). Nunca regenera un plan, una decisión humana ni el
artefacto unificado ausentes. Nunca modifica ningún otro comando
existente.

## Por qué `QUALIFIED_FOR_DOWNSTREAM_SHADOW` no es autorización productiva

Igual que "shadow" en Fases 5–11: esta fase nunca escribe en Neo4j,
nunca genera `ContextPackage`/`RuleDraft`/reglas Markdown, nunca
modifica `CandidateArtifact` V1 ni el artefacto unificado, nunca
promueve ningún candidato. `QUALIFIED_FOR_DOWNSTREAM_SHADOW` autoriza
únicamente la ejecución **en paralelo, sin impacto productivo** de un
flujo downstream que Fase 12 todavía no implementa (ver "Próxima
fase") — nunca certifica que la regla represente correctamente el
negocio, nunca reemplaza revisión humana funcional.

## Limitaciones

- La verificación de completitud del baseline reutiliza
  `adapt_v1_baseline_candidates` (Fase 11) para regenerar la lista
  esperada — hereda, por diseño, cualquier limitación ya documentada
  de ese adaptador (ver `docs/UNIFIED_CANDIDATES_SHADOW.md`,
  "Limitaciones").
- `DETERMINISTIC_SERIALIZATION` es estructuralmente un gate que
  **siempre** `PASS` dentro de una única llamada al analizador (la
  autocomparación de un objeto consigo mismo es, por diseño, siempre
  determinista y por tanto no demuestra nada) — su verificación real
  ocurre **fuera** del analizador, en los tests de determinismo del
  servicio (doble ejecución con recarga completa desde disco). Esto es
  intencional, no una omisión: la propia especificación de esta fase
  exige "nunca intentar demostrar determinismo comparando el artefacto
  consigo mismo".
- La cobertura del escenario real está limitada al mismo paquete
  sintético `CALLER10`/`CALLEE10`/`STOPPER10` reutilizado de Fase 11 —
  no se reconstruyó un segundo escenario COBOL real con un grupo
  `CONFLICTS_WITH_BASELINE`/`RELATED_TO_BASELINE` genuino; esos casos
  se cubren con datos sintéticos aislados (ver "Casos negativos
  aislados"), consistente con el patrón ya establecido en Fases 9–11
  para escenarios estructuralmente esquivos con datos 100% reales.

## Próxima fase

Fase 13 (`feat/unified-shadow-downstream-pipeline`,
`docs/UNIFIED_SHADOW_DOWNSTREAM_PIPELINE.md`) cierra esta deuda: ejecuta,
para los grupos `downstream_shadow_eligible=True` bajo una disposición
`QUALIFIED_*`, el mismo flujo productivo `ContextPackage → RuleDraft →
Guardrails` — envuelto en shadow mode, con el fake determinista oficial
como único proveedor, nunca publicando ninguna regla.

## Ver también

- `docs/UNIFIED_SHADOW_DOWNSTREAM_PIPELINE.md` (Fase 13): ejecución
  downstream shadow de los grupos que este reporte califica.
- `docs/CONTROLLED_UNIFIED_ACTIVATION.md` (Fase 14A): control plane de
  activación, canary y comparación V1/unified, que exige esta
  disposición como uno de sus gates de canary/primary trial.
- `docs/UNIFIED_CANDIDATES_SHADOW.md` (Fase 11): artefacto unificado
  de candidatos en shadow mode, línea `BASELINE_V1`/`SHADOW_PROPOSAL`.
- `docs/CANDIDATE_PROMOTION_ASSESSMENT.md` (Fase 9): catálogo
  unificado, relaciones, conflictos, disposiciones.
- `docs/CONTROLLED_CANDIDATE_PROMOTION_PLAN.md` (Fase 10): paquete de
  revisión humana y plan de promoción en dry-run.
