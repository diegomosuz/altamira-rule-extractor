# Release notes 1.18.1

Hotfix PATCH (corrección de producción, sin funcionalidad nueva).
Publicado sobre `v1.18.0`.

## Corregido

- **RULE_DRAFTS_GENERATED/GUARDRAILS_APPLIED no llegaba a COMPLETED con
  proveedor LLM real** (reproducido con `gpt-4o-mini` y con
  `gpt-4.1-2025-04-14`; nunca ocurría con el LLM fake usado en
  qualification, que construye el `RuleDraft` directamente desde el
  `ContextPackage` real en vez de generar texto libre). Causa raíz,
  confirmada con evidencia real de dos ejecuciones distintas contra
  Catherine (candidatos `46ea0b26e92111fecf9dd353` con
  `gpt-4.1-2025-04-14`, `24ae8ff4dd3f15b3373eea1d` con `gpt-4o-mini`):
  `prompts/rule_writer_user.md` propio del producto:
  1. Nunca declaraba explícitamente que `traceability`/`limitations`
     debían ser arrays JSON — su propio ejemplo mostraba el valor como
     string suelto sin corchetes — lo que producía una reparación
     estructural sistemática (13/13 candidatos reales, con ambos
     modelos) en cada ejecución real.
  2. Su ejemplo de `traceability` ("Basado en la decisión del párrafo X
     del programa Y") enseñaba a nombrar el párrafo COBOL de origen
     (identificador con prefijo numérico, convención estándar en este
     corpus: `0000-PRINCIPAL`, `2000-VALIDAR-ENTRADA`, etc.) citando
     únicamente la evidencia de la decisión, que por diseño nunca
     incluye el identificador del párrafo. Esto disparaba de forma
     determinista el guardrail `unsupported_explicit_number`, y el
     prompt de reparación de guardrail no daba una guía accionable para
     resolverlo — con `LLM_TEMPERATURE=0` y una violación sin cambios,
     el modelo regeneraba el mismo borrador rechazado, agotando
     `LLM_REPAIR_ATTEMPTS` de forma garantizada.
- Corrección exclusivamente de prompt: `rule_writer_user.md` ahora tipa
  explícitamente `parameters`/`traceability`/`limitations` como arrays
  con un ejemplo entre corchetes, instruye citar toda la evidencia
  necesaria para respaldar cualquier identificador numérico nombrado
  (o evitar nombrarlo si no hay evidencia disponible), y aclara que la
  limitación obligatoria de "revisión funcional" es procedimental y
  nunca debe llevar un claim con evidencia. `rule_repair_system.md` da
  guía explícita y accionable para violaciones
  `unsupported_explicit_number`/`unsupported_explicit_date`. Ningún
  guardrail determinístico, el diseño de alias de evidencia, ni ningún
  contrato persistido fueron modificados ni relajados.
- Verificado con `gpt-4o-mini` real: reparación estructural bajó de
  13/13 a 0/13 candidatos; el rechazo de guardrail que bloqueaba el
  pipeline dejó de ocurrir. Catherine alcanza `COMPLETED` con los 13
  candidatos en `EVIDENCE_VALIDATED`, reproducido dos veces consecutivas
  a través de la API real (ejecución fresca, limpieza de la ejecución
  autoritativa, y una segunda ejecución genuinamente fresca) — nunca
  con LLM fake ni stub para esta verificación.

## Compatibilidad

- Sin cambios de schema/contrato persistido: `RuleDraft`,
  `ContextPackage`, `EvidenceCatalog`, `GuardrailCandidateArtifact` sin
  modificar.
- Sin cambios en semántica determinista de extracción, candidatos,
  contexto, ni en la corrección de integridad determinista de v1.18.0
  (`4d80bcd`): preservación de `outcome_code`/`decision_id`/
  `candidate_id`/condición verificada explícitamente sin cambios.
- Sin cambios de UI/branding/versión de contrato OpenAPI (permanece en
  `1.0`, independiente de la versión de release).

## Versión

- `pyproject.toml`, `parser/pom.xml` y
  `src/altamira_extractor/__init__.py::__version__`: `1.18.1`.
- Tag de release (creado en una fase posterior, nunca aquí):
  `v1.18.1`.
