# Capability Coverage Manifest — Altamira Rule Extractor 1.17

> **Alcance de versión**: el nombre de archivo y el título son
> deliberadamente históricos (fijan la edición en que este manifiesto se
> cerró, Fase 15B4-CANDIDATE-QUALITY-5D/5D-SAFETY) — no se renombra en
> cada release. La matriz permanece vigente sin cambios a través de
> v1.18.0/v1.18.1/v1.18.2: ninguna de esas correcciones agregó, quitó ni
> reclasificó una capacidad de esta lista (todas fueron correcciones de
> confiabilidad del guardrail/LLM o de fidelidad de un campo ya
> existente, nunca de qué candidato se detecta). La fila `STATE_TRANSITION`
> (case `gt-positive-sqlcode-evaluate-state-transition`) tiene una nota
> específica sobre la mejora de v1.18.2 — ver más abajo. Si una futura
> capacidad realmente nueva lo justifica, evaluar un manifiesto v1.18.x
> dedicado en vez de reescribir este archivo.

Artefacto versionado, humano-legible, producido por la Fase
15B4-CANDIDATE-QUALITY-5D (cierre de `P1-CORPUS-GAP-GT`), reconciliado en
15B4-CANDIDATE-QUALITY-5D-SAFETY. Responde una
pregunta concreta por cada capacidad que 1.17 pretende soportar: *¿en qué
categoría cae, y qué evidencia versionada lo demuestra?*

No es un registry ejecutable — es la reconciliación explícita entre "lo que
el código hace" y "lo que el Ground Truth formal + los tests permanentes
prueban". Se actualiza cuando cambia la clasificación de una capacidad, no en
cada commit.

**Default actual del código (Fase 15B4-CANDIDATE-QUALITY-5E)**:
`enhanced_candidates_enabled=true` es el default de
`src/altamira_extractor/config.py` — un run nuevo sin configuración
adicional produce las cinco familias `PRODUCTIVE_RULE` (`RETURN_CODE`
V1/Q0 siempre activo + `RETURN_CODE_PROPAGATION`/`LEVEL_88_RETURN_CODE`/
`STATE_TRANSITION`/`CALCULATION` vía V2). El override explícito
`enhanced_candidates_enabled=false` (modo legacy/conservador, V1/Q0-only)
sigue disponible. Por eso, en la columna **"ENHANCED result"** de la
matriz de abajo describe el comportamiento **out-of-the-box** de 1.17,
tanto en el código como en el despliegue K3s estándar (`deploy/k3s/
configmap.yaml` fija `enhanced_candidates_enabled=true`, alineado desde
Fase 15B4-CANDIDATE-QUALITY-5G — ver `docs/release/RELEASE_PROFILES.md`,
"Enhanced mode"); la columna **"DEFAULT result"** preserva la
nomenclatura histórica de Fase 5D (cuando `false` era el default del
código) y ahora describe el comportamiento bajo el override explícito
`enhanced_candidates_enabled=false`, no el estado "recién instalado" ni
el despliegue K3s estándar.

## Categorías (exactamente 4, sin quinta)

- **PRODUCTIVE_RULE**: produce un `RuleCandidate` con `rule_family` que
  llega a `06-candidates.json` (posiblemente detrás de
  `enhanced_candidates_enabled`).
- **CONTEXT_EVIDENCE_ONLY**: alimenta `ContextPackage`/evidencia/el grafo
  semántico, pero nunca se convierte en candidato/regla por sí sola.
- **PARSER_CANONICAL_ONLY**: se parsea al artefacto canónico pero no se usa
  semánticamente aguas abajo (o es configuración del propio parser).
- **DEFERRED_UNSUPPORTED**: detectado y trazado explícitamente como no
  soportado (nunca pérdida silenciosa).

## Matriz completa

| Capability | Classification | GT positive | GT negative | Permanent test | DEFAULT result | ENHANCED result | Release 1.17 status |
|---|---|---|---|---|---|---|---|
| RETURN_CODE V1/Q0 | PRODUCTIVE_RULE | `gt-positive-declared-value-return-code` | — | `tests/test_formal_ground_truth_corpus_e2e.py` | MATCHED | MATCHED | CLOSED |
| RETURN_CODE_PROPAGATION V2 | PRODUCTIVE_RULE (gated) | `gt-positive-return-code-if-else` (minimum_count=2 exacto, dos ramas con literal distinto verificado: MOVE 99/MOVE 0, ver 5D-SAFETY §3) | — | idem | MISSING (honesto — V1 solo produce 1 ghost coarse, nunca 2 por-branch) | MATCHED (matched_count=2) | CLOSED (5D-SAFETY) |
| LEVEL_88_RETURN_CODE | PRODUCTIVE_RULE (gated) | `gt-positive-level88-return-code-nested-set` | — | idem | MISSING | MATCHED | CLOSED |
| STATE_TRANSITION | PRODUCTIVE_RULE (gated + semantic_tag) | `gt-positive-state-transition-if-status-target`, `gt-positive-sql-select-into-state-transition`, `gt-positive-sqlcode-evaluate-state-transition` (minimum_count=3 exacto, EVALUATE con 3 branches WHEN 0/+100/OTHER → 'A'/'N'/'E', tres hechos distintos verificados, ver 5D-SAFETY §3) | `gt-negative-state-transition-nonfunctional-indicator-name` | idem | MISSING | MATCHED | CLOSED (5D-SAFETY) |
| CALCULATION — COMPUTE | PRODUCTIVE_RULE (gated) | `gt-positive-calculation-if-compute-multiplication`, `gt-positive-calculation-unconditional-compute-multiplication` | — | idem | MISSING | MATCHED | CLOSED |
| CALCULATION — ADD | PRODUCTIVE_RULE (gated) | `gt-positive-calculation-unconditional-add` | — | idem | MISSING | MATCHED | CLOSED (5D) |
| CALCULATION — SUBTRACT | PRODUCTIVE_RULE (gated) | `gt-positive-calculation-if-subtract` | — | idem | MISSING | MATCHED | CLOSED (5D) |
| CALCULATION — MULTIPLY | PRODUCTIVE_RULE (gated) | `gt-positive-calculation-unconditional-multiply-giving` | — | idem | MISSING | MATCHED | CLOSED |
| CALCULATION — DIVIDE | PRODUCTIVE_RULE (gated) | `gt-positive-calculation-unconditional-divide-giving` | — | idem | MISSING | MATCHED | CLOSED (5D) |
| CALCULATION — ADD/SUBTRACT CORRESPONDING | DEFERRED_UNSUPPORTED | — (no GT positivo: nunca se productiviza) | — | `parser/src/test` (StatementExtractor, `ctx.unsupported()`) | N/A | N/A | CLOSED (parser-level trace) |
| CALCULATION — ROUNDED / ON SIZE ERROR | DEFERRED_UNSUPPORTED | — | — | `parser/src/test` (`ctx.unsupported()`) | N/A | N/A | CLOSED |
| CALCULATION — DIVIDE...REMAINDER | DEFERRED_UNSUPPORTED (solo el remainder; el cociente sigue productivo) | — | — | `parser/src/test` (`ctx.unsupported()`, target_data_items excluye remainder) | N/A | N/A | CLOSED |
| SQL SELECT/INSERT/UPDATE/DELETE | CONTEXT_EVIDENCE_ONLY | — (nunca RuleCandidate) | — | `tests/pipeline/test_semantic_graph_builder.py` (relaciones READS/WRITES/UPDATES/INSERTS), `gt-positive-sql-select-into-state-transition` (prueba no-interferencia) | N/A | N/A | CLOSED |
| SQLCODE causal linkage | CONTEXT_EVIDENCE_ONLY | — | — | `gt-positive-sqlcode-evaluate-state-transition` (prueba no-interferencia), tests de `context_package_builder.py` | N/A | N/A | CLOSED — v1.18.2 mejora la fidelidad de la rama `EVALUATE SQLCODE WHEN` (predicado estructurado por rama, p. ej. `SQLCODE = 100`, en vez del sujeto crudo compartido); no cambia esta clasificación ni el conteo de candidatos detectados, ver `docs/release/RELEASE_NOTES_1.18.2.md` |
| Declared VALUE provenance | CONTEXT_EVIDENCE_ONLY | — | — | `tests/test_hermetic_declared_value_provenance_e2e.py`, `gt-positive-declared-value-return-code` | N/A | N/A | CLOSED |
| GO TO | CONTEXT_EVIDENCE_ONLY | — | — | `docs/SEMANTIC_PROPAGATION.md` §5 + tests de `dependency_builder.py` (CONTROL_DEPENDS_ON) | N/A | N/A | CLOSED |
| PERFORM | CONTEXT_EVIDENCE_ONLY | — | — | idem | N/A | N/A | CLOSED |
| DomainTerm | CONTEXT_EVIDENCE_ONLY | — | — | `tests/pipeline/test_domain_term_mapper.py` | N/A | N/A | CLOSED |
| ParameterTable | CONTEXT_EVIDENCE_ONLY | — | — | `tests/pipeline/test_semantic_graph_builder.py` (dual-label Table+ParameterTable) | N/A | N/A | CLOSED |
| DATA_DEPENDS_ON / CONTROL_DEPENDS_ON | CONTEXT_EVIDENCE_ONLY | — | — | `tests/pipeline/test_dependency_builder.py` | N/A | N/A | CLOSED |
| COPY | PARSER/PROVENANCE CAPABILITY SUPPORTING PRODUCTIVE RULES (nunca RuleFamily) | — (deliberadamente sin regla GT "COPY") | — | E2E permanente existente (Fase 5A, provenance-safe fix: candidatos con fuente preprocesada se preservan, `source_file=None` honesto, nunca crash/falsa atribución) | N/A | N/A | CLOSED — evidencia por test, no por regla GT artificial |
| LINKAGE SECTION | PARSER_CANONICAL_ONLY (feeds CONTEXT_EVIDENCE_ONLY interprocedural) | — | — | `parser/src/test` (`extractLinkageDataItems`) | N/A | N/A | CLOSED |
| CALL / BY_REFERENCE_OUTPUT | DEFERRED_UNSUPPORTED (interprocedural, shadow-only por diseño arquitectónico — nunca productizado) | **NINGUNO en el catálogo productivo** (5D-SAFETY §1: vive exclusivamente en `config/ground_truth/shadow_interprocedural.yaml`, catálogo separado, `gt-positive-by-reference-output-unconditional-move`, solo vía `--source promotion-assessment`) | — | `tests/pipeline/test_ground_truth_by_reference_output_integration.py` (shadow, catálogo dedicado), `tests/test_formal_ground_truth_corpus_e2e.py` (catálogo productivo ya ni siquiera referencia esta familia) | N/A (ausente del catálogo productivo) | N/A (ausente del catálogo productivo) | CLOSED (5D-SAFETY) — una capacidad DEFERRED_UNSUPPORTED nunca es un expected positive business-rule fact del release gate productivo |
| STOP RUN / GOBACK / EXIT PROGRAM | CONTEXT_EVIDENCE_ONLY | — | — | Consumidor PRODUCTIVO real confirmado (5D-SAFETY §5): `context_package_builder.py::_SQL_CAUSAL_BARRIER_KINDS` trata `StatementKind.PROGRAM_TERMINATION` como barrera para el enriquecimiento SQLCODE causal (D4) — afecta el artefacto `07-context/*.json` real de candidatos productivos. Permanent test: `tests/pipeline/test_sqlcode_causal_evidence.py::test_program_termination_between_exec_sql_and_decision_is_ambiguous`. (`tests/parser_integration/test_program_termination_propagation_integration.py` cubre ADEMÁS el consumidor shadow/interprocedural, no el único requisito) | N/A | N/A | CLOSED (5D-SAFETY, evidencia reforzada) |
| CICS | DEFERRED_UNSUPPORTED | — | — | `tests/pipeline/test_display_exec_cics_observation_integration.py` | N/A | N/A | CLOSED |
| Procedure file I/O (READ/WRITE/OPEN/CLOSE/REWRITE/DELETE/START) | DEFERRED_UNSUPPORTED | — | — | Reauditado en 5D-SAFETY §6: `StatementExtractor.java::convertOne` despacha a `convertOther` (fallback GENÉRICO, verbo-agnóstico) para TODO statement sin `StatementKind` dedicado — `convertOther` SIEMPRE llama `ctx.unsupported(...)` y preserva `source_text` (nunca pérdida silenciosa), incondicionalmente. `FileSectionAndFileControlUnsupportedTest.java::fileSectionAndFileControlAreDetectedAndTracedNeverProductivized` ya asserta `≥3` diagnósticos `kind=OTHER` para OPEN/READ/CLOSE via este mecanismo exacto. WRITE/REWRITE/DELETE/START pasan por el MISMO código (ningún caso especial en `convertOne`) — no requieren fixture verbo-específico adicional. | N/A | N/A | CLOSED (5D-SAFETY, mecanismo genérico verificado — ver matriz §6 abajo) |
| FD / FILE SECTION | DEFERRED_UNSUPPORTED | — | — | Fase 5B (`extractFileSectionAndFileControlUnsupported`), `tests/test_hermetic_file_section_control_e2e.py` | N/A | N/A | CLOSED |
| FILE-CONTROL / SELECT ASSIGN | DEFERRED_UNSUPPORTED | — | — | idem | N/A | N/A | CLOSED |
| Source format FIXED | PARSER_CANONICAL_ONLY | — | — | `parser/src/test/.../ProLeapCobolParserTest.java` | N/A | N/A | CLOSED |
| Source format TANDEM | PARSER_CANONICAL_ONLY | — | — | idem | N/A | N/A | CLOSED |
| Source format AUTO | PARSER_CANONICAL_ONLY (resuelve únicamente a FIXED) | — | — | idem | N/A | N/A | CLOSED |
| Source format FREE | DEFERRED_UNSUPPORTED (rechazo explícito, exit code 3) | — | — | `ProLeapCobolParserTest.java`, `MainRunTest.java` | N/A | N/A | CLOSED |

## Procedure file I/O — matriz de verbos (5D-SAFETY §6)

| Verb | UNSUPPORTED_TRACE_IMPLEMENTED | Permanent test | Status |
|---|---|---|---|
| OPEN | YES (`convertOther`, genérico) | `FileSectionAndFileControlUnsupportedTest` (≥3 diagnósticos `kind=OTHER`, incluye OPEN) | CLOSED |
| READ | YES (idem) | idem | CLOSED |
| CLOSE | YES (idem) | idem | CLOSED |
| WRITE | YES (mismo mecanismo genérico, sin caso especial en `convertOne`) | Cubierto por el mismo mecanismo verificado arriba — no requiere fixture verbo-específico | CLOSED |
| REWRITE | YES (idem) | idem | CLOSED |
| DELETE | YES (idem) | idem | CLOSED |
| START | YES (idem) | idem | CLOSED |

## Resultado de la regla de gap (Sección 29)

29 capacidades auditadas. **Las 29 caen exactamente en una de las 4
categorías con evidencia CLOSED** — 0 capacidades `OPEN`. La clasificación
`OPEN` de procedure file I/O reportada en 5D fue revisada en 5D-SAFETY §6 y
corregida a CLOSED: el mecanismo de trazado (`convertOther`) es genérico y
verbo-agnóstico, ya demostrado por el test permanente existente para
OPEN/READ/CLOSE — WRITE/REWRITE/DELETE/START pasan por el mismo código sin
excepción, por lo que no constituyen un gap real.

## Historial

- 2026-08-05 (Fase 15B2-A): catálogo inicial, 12 casos.
- 2026-08-13 (Fase 5D, cierre `P1-CORPUS-GAP-GT`): +3 casos CALCULATION
  (ADD/SUBTRACT/DIVIDE), matriz de clasificación completa de 29
  capacidades, paquete versionado
  `examples/PAQUETE_SINTETICO_GROUND_TRUTH_FASE_15D.zip`, test permanente
  `tests/test_formal_ground_truth_corpus_e2e.py` (reemplaza dependencia de
  scripts ad hoc para esta evidencia).
- 2026-08-13 (Fase 5D-SAFETY, reconciliación de semántica del release):
  BY_REFERENCE_OUTPUT removido del catálogo productivo (vive en
  `config/ground_truth/shadow_interprocedural.yaml`, catálogo shadow-only
  separado) — ENHANCED alcanza FP=0/FN=0/precision=1.0/recall=1.0/f1=1.0
  **por construcción**, sin exclusión manual. `minimum_count` corregido a
  2/3 para los dos casos multi-branch deliberados (cada branch un hecho
  verificado, nunca duplicado). Termination statements reconfirmado
  CONTEXT_EVIDENCE_ONLY con evidencia de consumidor productivo real
  (`_SQL_CAUSAL_BARRIER_KINDS`) + test permanente preciso. Procedure file
  I/O reclasificado OPEN→CLOSED (mecanismo genérico verificado).
