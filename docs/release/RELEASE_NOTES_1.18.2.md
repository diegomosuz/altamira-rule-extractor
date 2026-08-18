# Release notes 1.18.2

Hotfix PATCH (corrección de producción, sin funcionalidad nueva).
Publicado sobre `v1.18.1`.

## Corregido

Cinco defectos reales de confiabilidad de `RULE_DRAFTS_GENERATED`/
`GUARDRAILS_APPLIED` contra proveedores LLM reales, encontrados durante
aceptación manual multi-paquete y calificación de release, corregidos
en orden:

1. **Fuga de identificadores técnicos hacia `traceability`** (candidato
   real `84c4489628e321519c07611a`, `gpt-4o-mini`). El guardrail
   determinístico `unsupported_explicit_number`/`unsupported_explicit_date`
   funciona correctamente y debe seguir rechazando hechos de negocio no
   soportados en `condition`/`effect`/`parameters` — pero el mismo
   chequeo también se aplicaba a `traceability`, un campo que por
   contrato nunca porta un hecho de negocio, solo una explicación
   narrativa de qué evidencia respalda la regla. Corrección
   determinística acotada: `sanitize_traceability_number_date_violations`
   elimina, sin invocar al modelo, únicamente los elementos de
   `traceability` que efectivamente contienen el número/fecha no
   soportado — nunca reescribe, nunca inventa texto. Se aplica solo
   cuando **todas** las violaciones ERROR vigentes son de ese tipo sobre
   `field="traceability"`; cualquier violación de negocio (o mixta) se
   deja intacta para el ciclo de reparación LLM existente.
2. **Reconstrucción determinística cuando la sanitización parcial no
   puede aplicar** (candidato recurrente `4000-VALIDAR-PRODUCTO` de
   Catherine Corregido). Cuando la violación ocupa el único elemento de
   `traceability` (removerlo dejaría el campo vacío, y el schema exige
   al menos 1 elemento), depender del reintento LLM no era confiable en
   la práctica. `reconstruct_traceability_deterministically` reemplaza
   `traceability`, únicamente en ese caso límite, por una oración fija
   en español sin dígitos ni alias de evidencia — estructuralmente
   imposible de volver a violar. Nunca toca `condition`/`effect`/
   `parameters`/`outcome_code`.
3. **Fuga de alias de evidencia hacia `effect`** (candidatos reales de
   Clientes Empresas y Prestamos Empresas). `effect` es un campo de
   negocio en lenguaje natural; el modelo a veces escribía el alias de
   evidencia crudo (o el `outcome_code` desnudo) en su lugar. Corregido
   con guía e instrucciones explícitas en `rule_writer_user.md`/
   `rule_structure_repair_system.md`: un alias que respalda un campo en
   `evidence_refs` nunca reemplaza el valor del campo.
4. **Campos prohibidos ecoados durante la reparación** — a diferencia de
   `rule_structure_repair_system.md`, `rule_repair_system.md` nunca
   listaba los campos que el pipeline asigna en Python
   (`schema_version`, `evidence_validation_status`,
   `functional_review_status`). El modelo los veía en el borrador de
   referencia y los repetía en su propia respuesta, causando rechazo
   estructural en cada intento de reparación sin importar la violación
   real. Corregido listando los mismos campos prohibidos en ambos
   prompts.
5. **Corrección de hechos de negocio ya conocidos por FIERN** (candidatos
   reales de Clientes Empresas y Ground Truth 15D). El mismo paquete
   podía fallar y luego pasar en una simple re-ejecución: el número que
   el modelo escribía en un campo de negocio (p. ej. `"30"` en `effect`,
   `"1000"` en `condition`) ya era, en ambos casos, un hecho auténtico
   presente en `ContextPackage.decision.expression` — el modelo
   simplemente no había citado esa evidencia en el `claim` del campo,
   citando evidencia más débil en su lugar.
   `augment_claims_with_authoritative_anchors` nunca toca el *valor* de
   ningún campo de negocio: únicamente amplía las citas de evidencia de
   un `claim` para incluir el ancla autoritativa (`$.decision`, o
   `$.effects.return_codes[i]` cuando `approved_for_rule_text=true`) que
   ya, verificablemente, contiene el token exacto que el modelo escribió.
   Todo-o-nada por candidato: si algún token ofensivo no tiene ancla
   autoritativa resoluble, ninguno se amplía y el candidato sigue
   fallando cerrado por el ciclo de reparación LLM existente —
   `unsupported_explicit_number`/`unsupported_explicit_date` nunca se
   debilita.

Las cinco correcciones reutilizan el mecanismo de procedencia
determinística ya existente (`RepairAttemptRecord.response_hash=None`,
campo ya opcional en el contrato) para distinguir una intervención
determinística de un intento LLM real — sin cambio de schema en ningún
caso. Los contadores expuestos a UI/API (`repair_attempts_used`)
cuentan exclusivamente intentos LLM reales.

## Semántica de rama EVALUATE/WHEN

Defecto de extracción determinística, separado de las correcciones de
guardrail anteriores: `EVALUATE SQLCODE WHEN +100` perdía el literal de
comparación de la rama antes de llegar al `ContextPackage` — el nodo
`Decision` del grafo representa el sujeto completo del `EVALUATE`
(p. ej. `"SQLCODE"`), compartido entre todas sus ramas `WHEN`, nunca el
predicado de una rama específica. El literal `+100` solo existía en
`code_slice` (texto fuente crudo mostrado al LLM como contexto), nunca
en un campo estructurado — el guardrail solo podía validar la
afirmación del modelo por coincidencia incidental de texto.

Corregido en el límite semántico correcto, en dos capas:

- **Parser Java**: para una rama `WHEN` que compara el sujeto contra un
  literal puro, se construye un predicado limpio (`"SQLCODE = 100"`, o
  `<>`/`OR`-unido para formas negadas/múltiples) usando las utilidades
  de extracción de literales ya existentes (`ValueReferences`). Para
  formas no soportadas con seguridad (`EVALUATE TRUE` con
  condition-name, rangos `THRU`, `EVALUATE ... ALSO ...`, comparación
  contra otra variable) el predicado cae de vuelta al sujeto crudo del
  `EVALUATE` — nunca un volcado de texto ANTLR sin procesar, nunca
  `null`.
- **Python**: `RuleCandidate.condition` y `ContextPackageDecision`
  prefieren, cuando existe, el predicado específico de la rama del
  propio candidato por sobre el sujeto compartido del nodo `Decision`
  del grafo — mismo patrón ya establecido para `outcome_code`.

`WHEN OTHER` permanece incondicional, sin predicado, como corresponde
(no es una comparación). Ningún nodo/relación del grafo cambió; sin
cambio de contrato. Verificado en vivo contra el paquete real: el
`claim` del campo `condition` en el `RuleDraft` cita `$.decision`
(evidencia estructurada), nunca `code_slice`.

## Compatibilidad de `candidate_id`

La corrección de rama `EVALUATE`/`WHEN` corrige el *valor* de
`condition` para los candidatos anclados a una rama con predicado
literal — pero `candidate_id` deriva de una función de identidad que
incluye el texto de `condition`, así que un candidato cuyo texto se
corrigió también cambiaría de identificador, incluso cuando nada sobre
qué candidato representa era ambiguo.

`candidate_id` es determinístico dentro de la misma implementación y
la misma entrada de paquete — no hay ni hubo nunca una garantía
contractual de estabilidad entre versiones de producto. Dicho esto,
v1.18.2 preserva deliberadamente el `candidate_id` de v1.18.1 en todo
caso donde hacerlo es seguro: cuando el candidato ya era distinguible
de cualquier otro (mismo párrafo/decisión/familia) por su
`outcome_code`, revierte al identificador legacy exacto. El texto de
`condition` corregido se usa como discriminador de identidad
únicamente cuando es necesario para evitar que dos ramas `WHEN`
genuinamente distintas colisionen en un mismo candidato (caso
adversarial verificado con test de regresión: dos ramas distintas que
asignan el mismo literal de salida).

Medido contra los 7 paquetes obligatorios de calificación (68
candidatos reales, comparación directa v1.18.1 vs. v1.18.2): 66
candidatos mantienen exactamente el mismo `candidate_id`; los 2 que
cambian son las ramas `SQLCODE WHEN 0`/`WHEN +100` de Ground Truth
Fase 15D, cuyo `condition` corregido pasó a ser el propio
discriminador de identidad de esa rama.

## Calificación y endurecimiento de release

Distinto de los cambios de comportamiento de producto anteriores: este
release recibió calificación de confiabilidad reforzada como parte de
su propio proceso de cierre, sin cambiar ningún comportamiento
adicional del pipeline:

- 10 ejecuciones consecutivas genuinamente frescas (`run_id` distinto
  cada vez, nunca reanudadas) contra `gpt-4o-mini` real para Ground
  Truth 15D y para Clientes Empresas, además de Catherine Corregido.
- Ejecuciones únicas frescas para Catherine, Prestamos Empresas,
  Consulta Saldos y Ground Truth 15B2A.
- Auditoría adversarial a nivel de fuente (sin LLM, sin red) contra
  `ContextPackage` reales preservados de los incidentes que motivaron
  cada corrección.
- Comparación de identidad de candidato contra una reconstrucción real
  de `v1.18.1` (mismo commit publicado, misma imagen reconstruida) para
  medir el impacto exacto del cambio de `candidate_id`, en vez de
  inferirlo.

Ninguna de estas actividades de calificación modificó código de
producto por sí misma — documentan la evidencia detrás de las
correcciones ya descritas arriba.

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
- Sin cambio de metamodelo del grafo Neo4j (`docs/NEO4J_METAMODEL.md`
  sin modificar).
- Sin cambios en la corrección de integridad determinista de v1.17
  (checkpoint 9999) ni en las correcciones de prompt de v1.18.1: ambas
  preservadas y verificadas sin modificación.
- Sin cambios de UI/branding/versión de contrato OpenAPI (permanece en
  `1.0`, independiente de la versión de release).
- `candidate_id` puede diferir de v1.18.1 únicamente para candidatos
  anclados a una rama `EVALUATE`/`WHEN` cuyo `condition` se corrigió y
  que además requería el nuevo discriminador para evitar colisión —
  ver "Compatibilidad de `candidate_id`" arriba. Ningún otro candidato
  cambia de identificador.

## Versión

- `pyproject.toml`, `parser/pom.xml` y
  `src/altamira_extractor/__init__.py::__version__`: `1.18.2`.
- Tag de release: `v1.18.2`, commit `179c077013c998f62f49106f79e200fcdfea7912`.
