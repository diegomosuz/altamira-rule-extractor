# Ejecución downstream del artefacto unificado en shadow mode (Fase 13)

Rama: `feat/unified-shadow-downstream-pipeline`. Baseline: v1.11.0.

## Propósito

Fase 12 determina si `diagnostics/unified-candidates-shadow.json`
(Fase 11) cumple las condiciones técnicas mínimas para alimentar, en
una fase posterior, un flujo downstream también en shadow mode — pero
nunca ejecuta ese flujo. Fase 13 cierra ese hueco:
`diagnostics/unified-shadow-downstream.json` es un artefacto
**diagnóstico, no contractual, generado bajo demanda**, que ejecuta,
exclusivamente para los grupos con elegibilidad downstream efectiva, el
mismo flujo productivo `ContextPackage → RuleDraft → Guardrails` —
pero **envolviéndolo, nunca reemplazándolo**: cada `ContextPackage`/
`RuleDraft`/`GuardrailReport` embebido es una instancia real y válida
de su contrato productivo respectivo, nunca una versión deformada.

Fase 13 **nunca**:

- modifica ni alimenta el pipeline V1 (`artifacts/06-candidates.json`,
  `PipelineStage`, `RunState`, `runner.py`);
- publica una regla ni promueve ningún candidato;
- escribe en Neo4j;
- invoca un proveedor LLM real — el único proveedor admitido es un
  fake determinista oficial, inyectado explícitamente;
- elige un member "ganador" dentro de un grupo.

## Separación del pipeline V1

El flujo productivo real (`contexts_built_stage.py` →
`rule_drafts_generated_stage.py` → `guardrails_applied_stage.py`)
requiere una transacción Neo4j en vivo, keyed por `RuleCandidate.
paragraph_id`/`decision_id` — no reutilizable tal cual por un ejecutor
que debe ser libre de Neo4j. Fase 13 resuelve esto reutilizando
`SemanticGraph` (`artifacts/04-semantic-graph.json`): el mismo espejo
genérico de propiedad-grafo que se carga en Neo4j, persistido como
archivo. `unified_shadow_context_assembler.py` replica la semántica de
búsqueda por **igualdad exacta** de las queries Q1-Q7 (nunca semejanza
textual) contra ese archivo, en vez de una transacción en vivo.

Las funciones puras reutilizadas **sin modificar** de los módulos
productivos: `evidence_catalog.py::build_evidence_catalog`,
`rule_draft_assembly.py::assemble_rule_draft_with_evidence_catalog`/
`load_rule_draft_schema`, `deterministic_guardrail.py::
evaluate_guardrail`. Ninguna validación existente se relaja.

## Elegibilidad efectiva

Un grupo se ejecuta en Fase 13 si y solo si se cumplen **ambas**
condiciones simultáneamente:

1. `UnifiedShadowGroupValidation.downstream_shadow_eligible=True`
   (elegibilidad **estructural**, Fase 12, por grupo).
2. `UnifiedShadowValidationReport.disposition` en
   `{QUALIFIED_FOR_DOWNSTREAM_SHADOW, QUALIFIED_WITH_WARNINGS}`
   (disposición **de política**, Fase 12, global).

Un grupo estructuralmente elegible bajo una disposición
`REVIEW_REQUIRED`/`BLOCKED`/`NOT_EVALUATED` **nunca** se ejecuta: el
artefacto completo se declara `NOT_EXECUTED` — una respuesta **válida**
de política, nunca un fallo técnico.

## Principio de diseño: envolver, nunca reemplazar

La información de cada grupo proviene de la **unión validada** de
todos sus members (`UnifiedShadowSourceMember`) — nunca de un único
member representante. La identidad principal de cada resultado es
`unified_shadow_candidate_id` (el `group_id`), nunca un
`source_candidate_id` V2/interprocedural individual. Todo
`UnifiedShadowDownstreamGroupResult` preserva:

- todos los `member_ids` del grupo (ninguno se descarta);
- todos los `source_candidate_ids` de sus members;
- todas las `review_decision_ids` (las aprobaciones humanas reales,
  Fase 10) de sus members.

Cuando el `ContextPackage` productivo exige una estructura no
representable a partir de esa unión (p. ej. una dimensión que ningún
member puede sustentar), el resultado es dejar esa dimensión
legítimamente vacía (el contrato productivo lo permite: D3/D5/D6/D7
por defecto son listas vacías) — **nunca** fabricar un valor ausente,
**nunca** deformar el contrato productivo con un wrapper parcial.

## Shadow `ContextPackage`

`unified_shadow_context_adapter.py::adapt_group_to_context_view`
construye una vista mínima (`ShadowGroupContextView`) de un grupo:
`program`, `paragraphs` (únicos entre members), `target`,
`output_literal`, `member_ids`, `source_candidate_ids`,
`review_decision_ids`, `evidence_ids`, `provenance_references`. Nunca
falla por sí mismo — la ambigüedad (p. ej. paragraphs distintos entre
members del mismo grupo) es responsabilidad exclusiva del ensamblador.

`unified_shadow_context_assembler.py::assemble_shadow_context_package`
resuelve, contra `SemanticGraph`, la jerarquía completa
Country→Application→Operation→Program→Paragraph→Decision por igualdad
EXACTA de propiedades (`name`/`outcome_code`). Solo D1 (scope), D2
(code_slice) y D4 (decision) se derivan con fidelidad real; D3
(data_context), D5 (effects), D6 (batch_context) y D7
(domain_glossary) quedan legítimamente vacíos. Falla
(`ContextAssemblyError`, aislado por grupo) si: el grupo no tiene un
único `paragraph` unánime entre members, `target`/`output_literal`
ausentes, cero `evidence_ids`, o el Program/Paragraph/Decision
correspondiente no existe en `SemanticGraph`.

## Shadow `RuleDraft` y el fake determinista

`unified_shadow_draft_generator.py::DeterministicFakeDraftProvider` es
el **único** proveedor admitido — verificado por identidad EXACTA de
tipo (`type(provider) is DeterministicFakeDraftProvider`, nunca
`isinstance`, para que ni siquiera una subclase pueda colarse). Nunca
lee configuración de proveedor real, nunca hace red, nunca lee `.env`,
nunca evalúa calidad lingüística ni funcional. Genera una respuesta
JSON determinista **exclusivamente** a partir del `ContextPackage`/
`EvidenceCatalog` reales, referenciando todos los alias de evidencia
disponibles.

`generate_shadow_rule_draft` valida el payload generado contra el
**mismo** `assemble_rule_draft_with_evidence_catalog` productivo, sin
modificarla.

### Hallazgo honesto: alias inventado y `DRAFT_GENERATION_FAILED`

Porque esa función productiva rechaza **cualquier** alias de evidencia
no resuelto durante la asamblea misma (antes de que exista un
`RuleDraft` válido), un alias inventado por el fake **nunca** produce
un `RuleDraft` con `evidence_aliases_unresolved` no vacío: la
generación completa falla como `DraftGenerationError`, y el grupo
correspondiente queda `DRAFT_GENERATION_FAILED` — **nunca** llega a
Guardrails. Esta fase eligió deliberadamente esta consecuencia honesta
en vez de introducir una ruta alternativa más permisiva solo para que
un alias inválido alcanzara Guardrails, porque nunca relaja una
validación productiva existente. Ver
`tests/pipeline/test_unified_shadow_downstream_negative_cases.py::test_case_c_invented_alias_fails_draft_assembly_never_reaches_guardrails`.

## Guardrails

`unified_shadow_guardrail_runner.py::run_shadow_guardrails` reutiliza
`deterministic_guardrail.py::evaluate_guardrail` sin modificarla —
exactamente los mismos chequeos que el flujo V1 (evidencia inventada,
consistencia numérica/de fechas, redacción de datos sensibles,
`approved_for_rule_text`, contexto de batch). `candidate_id` del
reporte es siempre el `group_id`, nunca un `source_candidate_id`
individual. Un rechazo (`GuardrailVerdict.REJECTED`) **nunca** publica
una regla ni elimina el draft — se registra como
`UnifiedShadowDownstreamExecutionStatus.GUARDRAIL_REJECTED`, con la
vista shadow del reporte y sus violaciones preservadas.

### Sin `evaluated_at`: ningún timestamp en el artefacto persistido

`GuardrailReport.evaluated_at: datetime` es un campo productivo
obligatorio que este contrato NUNCA modifica ni relaja. Pero Fase 13
exige que el artefacto persistido no contenga NINGÚN timestamp, ni
siquiera uno derivado de `run_id`. La resolución (auditoría de
seguridad de cierre de Fase 13): `run_shadow_guardrails` construye el
`GuardrailReport` productivo real en memoria (invocando
`evaluate_guardrail` sin alterarlo) usando un valor **sentinela fijo**
(epoch, `1970-01-01T00:00:00Z`, nunca la hora real ni un valor
derivado de `run_id`) únicamente para satisfacer ese campo obligatorio
— pero ese objeto **nunca se persiste directamente**.
`unified_shadow_guardrail_runner.py::to_shadow_view` lo proyecta a
`UnifiedShadowGuardrailReportView` (Fase 13 Parte 3 del contrato),
que preserva `candidate_id`/`verdict`/`violations`/`repair_attempts`/
`source_package_hash` y **excluye `evaluated_at` por completo**. Es
esa vista, embebida en `UnifiedShadowGuardrailRecord.guardrail_result`,
la única representación de un resultado de guardrail que este artefacto
persiste. Verificado recursivamente contra el JSON real (cero claves
`evaluated_at`/`created_at`/`updated_at`/`generated_at`/`timestamp`/
terminadas en `_at`, cero valores ISO-8601) en
`tests/pipeline/test_unified_shadow_downstream_executor.py::
TestArtifactHasNoTimestamps` y
`tests/pipeline/test_unified_shadow_guardrail_runner.py::
TestShadowViewExcludesTimestamps`.

## Ejecutor puro y aislamiento de errores por grupo

`unified_shadow_downstream_executor.py::run_unified_shadow_downstream`
orquesta, por cada grupo con elegibilidad efectiva: adaptador →
ensamblador de contexto → generador de draft → guardrails. Es puro:
sin filesystem, sin red, sin Neo4j, nunca muta sus argumentos.

Antes de procesar cualquier grupo, valida la **consistencia global**
entre las fuentes recibidas: `run_id` coincide entre `unified_shadow`/
`validation_report`/el `run_id` esperado; `source_package_hash`
coincide; `candidate_v1_artifact_hash`/`assessment_artifact_hash`/
`review_package_hash`/`promotion_plan_hash` coinciden entre ambas
fuentes; y, crucialmente, el hash **real** (recalculado) del
`unified_shadow` recibido coincide con el
`unified_candidates_shadow_hash` que el `validation_report` afirma
haber validado (detecta un artefacto **obsoleto**). Cualquier
inconsistencia es un fallo técnico **global**
(`UnifiedShadowDownstreamExecutorError`) — nunca aislable por grupo,
nunca produce un artefacto parcial.

El fallo de UN grupo (`ContextAssemblyError`/`DraftGenerationError`,
ambos tipados y aislados) **nunca** afecta a otros grupos ni
interrumpe la ejecución completa — se registra como
`CONTEXT_ASSEMBLY_FAILED`/`DRAFT_GENERATION_FAILED` en el
`UnifiedShadowDownstreamGroupResult` de ESE grupo únicamente. Ver
`tests/pipeline/test_unified_shadow_downstream_executor.py::TestPerGroupFailureIsolation`
y el caso negativo G.

## Disposición global

Derivada exclusivamente de los `execution_status` de todos los grupos
elegibles — nunca de un puntaje:

1. **`NOT_EXECUTED`**: cero grupos con elegibilidad efectiva (ya sea
   porque ningún grupo es estructuralmente elegible, o porque la
   disposición de validación no es `QUALIFIED_*`).
2. **`BLOCKED`**: al menos un grupo con un fallo de *pipeline*
   (`CONTEXT_ASSEMBLY_FAILED`, `DRAFT_GENERATION_FAILED` o
   `TECHNICAL_FAILURE` — los tres comparten tratamiento: representan
   que el flujo shadow en sí mismo no pudo procesar el grupo, a
   diferencia de un `GUARDRAIL_REJECTED`, una ejecución exitosa cuyo
   contenido fue rechazado).
3. **`COMPLETED_WITH_REJECTIONS`**: al menos un `GUARDRAIL_REJECTED`,
   cero fallos de pipeline.
4. **`COMPLETED`**: todos los grupos elegibles `EXECUTED` con
   guardrail `PASSED`.

## Qué significa `COMPLETED`

**Únicamente**: el flujo shadow completo (contexto → draft → guardrail)
se ejecutó sin fallos técnicos ni rechazos de guardrail para todos los
grupos elegibles. **Nunca** significa: regla validada funcionalmente,
candidato promovido, precisión demostrada, autorización productiva.
Igual que en Fases 5-12, "shadow" implica sin impacto productivo: cero
escritura en Neo4j, cero regla publicada, cero `ContextPackage`/
`RuleDraft` productivos generados.

## Determinismo

Dos ejecuciones sobre las mismas fuentes producen bytes idénticos: el
artefacto no contiene ningún timestamp (ver seccion anterior), todas las
listas se ordenan, `record_id` es una función determinista del
`group_id` (`context::{group_id}`, `draft::{group_id}`,
`guardrail::{group_id}`), `atomic_write_json` serializa con
`to_stable_json()`. Verificado en tests unitarios (recarga desde
objetos en memoria) y en la integración real (recarga completa desde
disco, dos invocaciones consecutivas de la CLI real vía subprocess).

## CLI

```
python -m altamira_extractor.cli unified-shadow-downstream <run_id> [--json]
```

Requiere que `run_id` haya alcanzado `PARSED` (`SUCCEEDED`) y que
`diagnostics/unified-candidates-shadow.json` **y**
`diagnostics/unified-shadow-validation-report.json` existan y sean
válidos — ninguno es opcional, ninguno se regenera si está ausente.
Persiste **únicamente** `diagnostics/unified-shadow-downstream.json`.
Nunca acepta opciones de proveedor, API key, endpoint, modelo ni
manifiesto — el proveedor es siempre `DETERMINISTIC_FAKE`, sin
excepción, sin bandera para activar uno real.

Exit code 0 cuando el artefacto se genera exitosamente — incluso si la
disposición es `NOT_EXECUTED` o `BLOCKED`; exit code distinto de cero
únicamente ante un fallo técnico real (run inexistente, `run.json`
inválido, etapa insuficiente, fuente principal ausente/inválida, hash
obsoleto, canónico requerido ausente, fallo de escritura) — sin
traceback, sin ruta absoluta, sin archivo parcial.

## Integración real

Validado con el parser Java real y Neo4j real, reutilizando
**exactamente** el mismo escenario real de Fases 9-12 (`CALLER10`
llamando a `CALLEE10`): el pipeline completo (`ingest` →
`candidate-promotion-assessment` → `candidate-promotion-review-package`
→ `candidate-promotion-plan` → `unified-candidates-shadow` →
`unified-shadow-validate` → `unified-shadow-downstream`) produjo, con
datos 100% reales:

- 1 grupo elegible (`VALID`/`NOT_IN_BASELINE`, 2 members: V2 +
  interprocedural);
- 1 `ContextPackage` shadow real, con las 8 evidencias resueltas;
- 1 `RuleDraft` shadow real, generado por el fake, alias determinista;
- 1 `GuardrailReport` `PASSED`, cero violaciones;
- ambos `source_candidate_id` y ambas `review_decision_id`
  preservados;
- disposición `COMPLETED`;
- bytes idénticos en dos ejecuciones consecutivas del servicio
  (recarga completa desde disco).

Test automatizado:
`tests/parser_integration/test_unified_shadow_downstream_integration.py::test_real_two_equivalent_proposals_complete_downstream_shadow`.

## Casos negativos aislados

`tests/pipeline/test_unified_shadow_downstream_negative_cases.py`
demuestra siete escenarios aislados (A-G):

- **A** — Disposición de validación `REVIEW_REQUIRED` → cero drafts,
  `NOT_EXECUTED`.
- **B** — Disposición de validación `BLOCKED` → cero drafts,
  `NOT_EXECUTED`.
- **C** — Alias inventado por el fake → `DRAFT_GENERATION_FAILED` (ver
  "Hallazgo honesto" arriba — diverge deliberadamente de la redacción
  literal original).
- **D** — Evidencia insuficiente (cero `evidence_ids`) →
  `CONTEXT_ASSEMBLY_FAILED`.
- **E** — Hash obsoleto → `UnifiedShadowDownstreamExecutorError`,
  ningún artefacto parcial.
- **F** — Proveedor distinto al fake → rechazado antes de generar
  cualquier draft.
- **G** — El fallo aislado de un grupo nunca impide que otro grupo
  elegible se ejecute; la disposición global refleja la causa tipada
  específica (`BLOCKED` para un fallo de pipeline,
  `COMPLETED_WITH_REJECTIONS` para un rechazo de guardrail — ver
  `tests/contracts/test_unified_shadow_downstream.py`).

## Ausencia de validación funcional y de publicación

Igual que Fases 5-12: Fase 13 nunca afirma que una regla generada
representa correctamente el negocio, nunca reemplaza revisión humana
funcional, nunca publica ni promueve nada. `COMPLETED` es una señal
puramente técnica: el flujo pudo ejecutarse sin fallos ni rechazos —
nunca un juicio de calidad ni de corrección.

## Limitaciones

- La cobertura del escenario real está limitada al mismo paquete
  sintético `CALLER10`/`CALLEE10`/`STOPPER10` reutilizado de Fases
  9-12 — no se reconstruyó un segundo escenario COBOL real con
  múltiples grupos genuinamente independientes; el aislamiento
  multi-grupo (caso G) se verifica con una variante sintética de dos
  grupos, uno de ellos deliberadamente sin `SemanticGraph`
  correspondiente.
- `evidence_aliases_unresolved` existe en
  `UnifiedShadowRuleDraftRecord` por completitud/trazabilidad del
  intento, pero es estructuralmente inalcanzable en un
  `DraftGenerationResult` retornado exitosamente (ver "Hallazgo
  honesto").
- El schema de `RuleDraft` (`schemas/rule-draft.schema.json`) se
  reutiliza sin cambios; cualquier limitación ya documentada de la
  asamblea de drafts productiva aplica igual aquí.

## Próxima fase

Fase 13 tampoco decide qué hacer con un artefacto `COMPLETED` (¿debe
alimentar una cola de revisión humana? ¿un dashboard?) — esa decisión
de producto permanece fuera de alcance para una fase futura (Fase 14).

## Ver también

- `docs/UNIFIED_SHADOW_DIFFERENTIAL_VALIDATION.md` (Fase 12): reporte
  de validación diferencial, gates, disposiciones, elegibilidad
  estructural.
- `docs/UNIFIED_CANDIDATES_SHADOW.md` (Fase 11): artefacto unificado
  de candidatos en shadow mode, línea `BASELINE_V1`/`SHADOW_PROPOSAL`.
- `docs/CONTEXT_PACKAGE_CONTRACT.md`: contrato productivo de
  `ContextPackage`, reutilizado sin deformar.
- `docs/PACKAGE_CONTRACT.md`: contrato productivo de `RuleDraft`.
