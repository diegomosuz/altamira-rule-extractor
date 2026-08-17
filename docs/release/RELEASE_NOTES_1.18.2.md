# Release notes 1.18.2

Hotfix PATCH (corrección de producción, sin funcionalidad nueva).
Publicado sobre `v1.18.1`.

## Corregido

- **Recurrencia de `GUARDRAILS_APPLIED` agotando `LLM_REPAIR_ATTEMPTS`
  con proveedor LLM real**, ya con las correcciones de prompt de
  v1.18.1 aplicadas (candidato real `84c4489628e321519c07611a`,
  `gpt-4o-mini`, distinto de los dos candidatos que motivaron v1.18.1 —
  confirma que el defecto no era específico de un candidato). Causa
  raíz confirmada comparando los tres incidentes reales conocidos: el
  guardrail determinístico `unsupported_explicit_number`/
  `unsupported_explicit_date` funciona correctamente y **debe** seguir
  rechazando hechos de negocio no soportados en campos como
  `condition`/`effect`/`parameters` — pero el mismo chequeo también
  se aplica a `traceability`, un campo que (por el propio contrato ya
  documentado en `rule_writer_system.md`/`rule_writer_user.md`/
  `rule_repair_system.md` desde v1.18.1) nunca porta un hecho de
  negocio, solo una explicación narrativa breve de qué evidencia
  respalda la regla. Los tres incidentes reales (v1.18.0 con
  `gpt-4.1-2025-04-14` y `gpt-4o-mini`, v1.18.1 con `gpt-4o-mini`)
  fueron todos fugas de identificadores técnicos hacia `traceability`
  vía dos mecanismos distintos (nombre de párrafo COBOL; descripción
  auto-generada del catálogo de alias de evidencia,
  `"decision (archivo:línea-línea)"`, copiada verbatim), nunca una
  afirmación de negocio no soportada. Depender de reintentos LLM para
  corregir una fuga puramente técnica es intrínsecamente poco
  confiable: verificado empíricamente que el mismo candidato, con el
  mismo `ContextPackage`, puede fallar en una ejecución real y pasar
  limpio en otra — con `LLM_TEMPERATURE=0` — dado el comportamiento
  real conocido de no determinismo del proveedor incluso a temperatura
  fija.
- Corrección determinística acotada, no un ajuste adicional de prompt:
  `deterministic_guardrail.py::sanitize_traceability_number_date_violations`
  elimina, sin invocar al modelo, únicamente los elementos de
  `traceability` que efectivamente contienen el número/fecha no
  soportado — nunca reescribe, nunca inventa texto, nunca sustituye
  evidencia. Se aplica **solo** cuando **todas** las violaciones ERROR
  vigentes son `unsupported_explicit_number`/`unsupported_explicit_date`
  sobre `field="traceability"`; cualquier violación sobre un campo de
  negocio (o mixta) se deja intacta para el ciclo de reparación LLM
  existente, sin cambios, preservando el fallo cerrado ante hechos de
  negocio genuinamente no soportados. Si la sanitización dejaría
  `traceability` vacío (el schema exige al menos 1 elemento), se
  rehúsa y delega al ciclo de reparación LLM existente sin cambios.
  `condition`/`effect`/`parameters`/`parameter_source` nunca son
  tocados por esta función.
- Provenance: cuando la sanitización resuelve el candidato antes de
  cualquier llamada LLM, se registra como un `RepairAttemptRecord` con
  `response_hash=None` (campo ya opcional en el contrato, sin cambio de
  schema) — distinguible de un intento LLM real, preserva íntegra la
  cadena de provenance/integridad ya existente en
  `GuardrailCandidateArtifact`, y nunca consume presupuesto real de
  `LLM_REPAIR_ATTEMPTS` en el caso común (la sanitización resuelve
  todas las violaciones antes de que el ciclo de reparación LLM
  siquiera se evalúe). Los contadores expuestos a UI/API
  (`repair_attempts_used`) cuentan exclusivamente intentos LLM reales,
  nunca la sanitización determinística.
- Verificado contra el forense real: 3 violaciones reales sobre el
  candidato `84c4489628e321519c07611a` → 0 tras sanitización, sin tocar
  ningún campo de negocio. Auditoría adversarial a nivel de fuente (sin
  LLM, sin red) contra los 13 `ContextPackage` reales del paquete
  Catherine que produjo el fallo: 52/52 escenarios correctos (fuga de
  nombre de párrafo, fuga verbatim de descripción de catálogo, caso
  irreparable de un único elemento totalmente ofensivo que debe
  delegar a reparación LLM, caso limpio sin violación). Qualification
  reforzada: **5 ejecuciones consecutivas genuinamente frescas**
  (run_id distinto cada vez) de Catherine completa contra `gpt-4o-mini`
  real, cada una `COMPLETED` con sus 13 candidatos en
  `EVIDENCE_VALIDATED` — **65/65 candidatos totales, 0 rechazos de
  guardrail, 0 intentos de reparación LLM consumidos**.

## Compatibilidad

- Sin cambios de schema persistido en `RuleDraft`, `ContextPackage`,
  `EvidenceCatalog`, `GuardrailReport`: `GuardrailCandidateArtifact`/
  `RepairAttemptRecord` (`contracts/guardrail_candidate.py`) tampoco
  cambiaron de forma — `RepairAttemptRecord.response_hash` ya era
  opcional antes de este release.
- Sin cambios en el comportamiento del guardrail para ningún campo de
  negocio: `condition`/`effect`/`parameters`/`parameter_source` siguen
  fallando cerrado ante cualquier número/fecha no soportado,
  exactamente como antes.
- Sin cambios en la corrección de integridad determinista de v1.17
  (checkpoint 9999) ni en las correcciones de prompt de v1.18.1: ambas
  preservadas y verificadas sin modificación.
- Sin cambios de UI/branding/versión de contrato OpenAPI (permanece en
  `1.0`, independiente de la versión de release).

## Versión

- `pyproject.toml`, `parser/pom.xml` y
  `src/altamira_extractor/__init__.py::__version__`: `1.18.2`.
- Tag de release (creado en una fase posterior, nunca aquí):
  `v1.18.2`.
