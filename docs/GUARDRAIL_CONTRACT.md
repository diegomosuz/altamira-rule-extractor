# Contrato del guardrail determinístico

## Propósito

El guardrail (`pipeline/deterministic_guardrail.py`,
`pipeline/guardrails_applied_stage.py`) es la única puerta entre un
`RuleDraft` generado por el LLM y su estado final
`EVIDENCE_VALIDATED`/rechazado. Es puramente determinístico: nunca usa
un LLM ni ninguna forma de similitud/embeddings para decidir si una
afirmación tiene evidencia. Su función es doble y nunca se mezcla:

1. Verificar que cada afirmación de negocio en el `RuleDraft`
   (`condition`/`effect`/`parameters`) esté respaldada por evidencia
   real del `ContextPackage` — nunca un número o fecha que el modelo
   inventó.
2. Corregir, de forma acotada y auditable, defectos puramente técnicos
   (identificadores de infraestructura filtrados a texto libre, campos
   de provenance narrativa sin hecho de negocio) que no reflejan una
   afirmación de negocio no soportada, sin depender de reintentos LLM
   no determinísticos para resolverlos.

## Entradas

- El `RuleDraft` inicial devuelto por `LlmRuleWriter` (o por un
  intento de reparación LLM posterior).
- El `ContextPackage` validado que originó ese `RuleDraft` — única
  fuente de evidencia admisible.
- El `EvidenceCatalog` (alias → evidencia real) construido a partir del
  mismo `ContextPackage`.

## Salidas

- `GuardrailCandidateArtifact` (`contracts/guardrail_candidate.py`),
  persistido en `artifacts/09-guardrails/`: `initial_rule_draft_hash`,
  `final_rule_draft_hash`, `final_rule_draft`, `repair_history`
  (lista de `RepairAttemptRecord`), `guardrail_report`.
- Estado final de evidencia por candidato:
  `EVIDENCE_VALIDATED` o rechazado (`NEEDS_FUNCTIONAL_REVIEW` en
  cualquier caso — ver CLAUDE.md, "Candidato, fidelidad y aprobación":
  ningún candidato V1 se presenta como regla aprobada, con o sin
  `EVIDENCE_VALIDATED`).
- `repair_attempts_used`, expuesto a UI/API: cuenta **exclusivamente**
  intentos LLM reales, nunca una intervención determinística.

## Validación estructural

Antes de evaluar evidencia, el `RuleDraft` debe cumplir su schema
Pydantic (tipos correctos, campos obligatorios presentes,
`traceability`/`limitations` como arrays JSON de al menos 1 elemento
— nunca strings sueltos). Un fallo estructural nunca llega a la
validación de evidencia; se resuelve por el ciclo de reparación
estructural (`rule_structure_repair_system.md`) o determinísticamente
cuando aplica (ver abajo).

## Validación de evidencia

Cada afirmación técnica citable (número, fecha, identificador) en
`condition`/`effect`/`parameters` debe poder rastrearse hasta un
`evidence_id`/`evidence_path` real del `ContextPackage`. Dos
violaciones determinísticas gobiernan esto:

### `unsupported_explicit_number`

Un número explícito en el texto del campo que no aparece, literalmente
(mismo tokenizador que usa el chequeo, nunca aproximado), en ninguna
evidencia citada por el `claim` correspondiente de ese campo.

### `unsupported_explicit_date`

Misma regla, para fechas explícitas.

### `unsupported_explicit_literal`

Un literal de negocio entre comillas (p. ej. `'API'`, `'D203'`) en el
texto del campo que no aparece, literalmente, en evidencia
**autoritativa acotada**: únicamente `$.decision`
(`expression`/`normalized_expression`) o un `$.effects.return_codes[i]`
con `approved_for_rule_text=true` — nunca `code_slice`,
`domain_glossary`, `table_effects` ni ningún otro tipo de evidencia,
aunque contengan el mismo texto.

Las tres violaciones son de severidad ERROR y bloquean
`EVIDENCE_VALIDATED` — nunca se degradan a warning para ningún campo
de negocio.

### Gobernanza field-first (campos gobernados)

Para `title`/`context`/`statement`/`condition`/`effect`/
`parameter_source`/`parameters` (nunca `traceability`/`limitations`,
que conservan su mecanismo propio), la evaluación es **field-first**:
el campo siempre se evalúa exista o no un `claim` que lo referencie. Un
campo sin ningún `claim` produce una evidencia vacía, así que cualquier
número/fecha/literal gobernado en su texto viola de inmediato — la
ausencia de un `claim` nunca vuelve invisible un hecho explícito
gobernado.

Esta evaluación es **por campo, de forma independiente**: un `claim`
que respalda `statement` nunca satisface una violación sobre
`condition`, `effect` ni ningún otro campo — cada campo con un hecho
gobernado necesita su propio `claim`.

## Alias de evidencia

Cada elemento de evidencia tiene un alias corto y estable
(`EvidenceCatalog`) usado para trazabilidad interna y para que el
`RuleDraft` cite qué evidencia respalda cada `claim`. Un alias es un
identificador **técnico/de transporte**, nunca texto de negocio: nunca
debe aparecer literalmente dentro de `condition`/`effect`/
`traceability`/`limitations` como si fuera parte de la explicación. El
guardrail rechaza (`alias_leaked_into_free_text`) cualquier campo cuyo
texto libre contenga un alias crudo — corregible únicamente vía el
ciclo de reparación LLM existente (`rule_writer_user.md`/
`rule_structure_repair_system.md` dan guía explícita para evitarlo y
para corregirlo).

## Comportamiento fail-closed en campos de negocio

`condition`, `effect` y `parameters` **nunca** se corrigen
automáticamente reescribiendo o inventando su valor. Ante una
violación no soportada, el guardrail:

1. Primero intenta ampliar la evidencia citada (ver "Aumento
   determinístico de ancla de evidencia" abajo) — únicamente cuando
   existe un ancla autoritativa real que ya, verificablemente, contiene
   el token exacto.
2. Si eso no resuelve la violación, delega al ciclo de reparación LLM
   (máximo 2 intentos, `CLAUDE.md`).
3. Si ninguno de los dos resuelve la violación, el candidato queda
   rechazado — nunca se relaja el guardrail para forzar un
   `EVIDENCE_VALIDATED`.

## Intervención determinística de `traceability`

`traceability` es, por contrato (`rule_writer_system.md`/
`rule_writer_user.md`/`rule_repair_system.md`), exclusivamente una
explicación narrativa de qué evidencia respalda la regla — nunca un
hecho de negocio. Por eso, y solo para este campo, el guardrail puede
corregir determinísticamente en lugar de depender de un reintento LLM:

- **Sanitización parcial** (`sanitize_traceability_number_date_violations`):
  si **todas** las violaciones ERROR vigentes son
  `unsupported_explicit_number`/`unsupported_explicit_date` sobre
  `field="traceability"`, elimina únicamente los elementos del array
  que efectivamente contienen el número/fecha ofensivo. Se rehúsa (no
  aplica) si eso dejaría el array vacío.
- **Reconstrucción determinística** (`reconstruct_traceability_deterministically`):
  fallback final, solo cuando la sanitización parcial no puede
  aplicar (el único elemento ofensivo es todo el contenido).
  Reemplaza `traceability` por una oración fija en español, sin
  dígitos ni alias de evidencia — estructuralmente imposible de volver
  a violar `unsupported_explicit_number`/`unsupported_explicit_date`.

Ninguna de las dos toca `condition`/`effect`/`parameters`/
`outcome_code`/`parameter_source`. Ninguna se aplica ante una violación
mixta (negocio + traceability) — en ese caso todo se delega al ciclo
de reparación LLM existente, sin cambios.

## Aumento determinístico de ancla de evidencia

`augment_claims_with_authoritative_anchors` resuelve un caso distinto:
el valor del campo de negocio **ya es correcto** (el número/fecha que
el modelo escribió es un hecho real), pero el `claim` de ese campo citó
evidencia más débil en vez del ancla autoritativa que ya lo prueba.
Nunca reescribe ni reformula el texto del campo — únicamente **amplía**
`claims[].evidence_paths`/`evidence_ids` para incluir el ancla
autoritativa (`$.decision`, o `$.effects.return_codes[i]` cuando
`approved_for_rule_text=true`) que ya, verificablemente y con el mismo
tokenizador que el chequeo de violación, contiene el token exacto.

Reglas estrictas:

- Todo-o-nada por candidato: si algún token ofensivo no tiene ancla
  autoritativa resoluble, **ninguno** se amplía — el candidato sigue
  fallando cerrado.
- Nunca se aplica a `traceability` (mecanismo propio, ver arriba) ni a
  `table_effects` (`TableEffect` no tiene un campo con número/fecha).
- Nunca inventa una evidencia: el token debe existir literalmente en
  el ancla candidata.

## Guía de reparación con ancla explícita

Cuando una violación `unsupported_explicit_number`/`_date`/`_literal`
no puede resolverse por el aumento determinístico anterior (porque el
campo no tiene ningún `claim` que ampliar), el **mensaje** de la
violación que recibe el modelo de reparación LLM incluye la misma
búsqueda de ancla autoritativa ya resuelta: indica explícitamente la
ruta (`$.decision` o `$.effects.return_codes[i]`) cuando existe, o
declara explícitamente que ninguna evidencia autoritativa respalda el
valor cuando no existe — en ese caso la única corrección válida es
eliminar el valor, nunca sustituirlo. Esto es enriquecimiento de
**texto del mensaje** únicamente: nunca crea ni modifica un `claim` por
sí mismo, nunca toca ningún campo de `RuleDraft`.

## Creación determinística de `Claim`: explícitamente rechazada

Ningún mecanismo determinístico de este guardrail **crea** un `claim`
donde no existía ninguno — únicamente puede **ampliar** las citas de
evidencia de un `claim` ya existente (ver arriba). Aunque una sola
ancla autoritativa (p. ej. `$.decision`) respalde un token gobernado
específico, atribuir automáticamente todo el campo a esa ancla podría
afirmar falsamente que el resto del contenido del campo también está
respaldado. Por eso la responsabilidad de crear el `claim` inicial
sigue siendo exclusivamente del modelo (writer o reparación) — el
guardrail solo valida y, cuando es seguro, amplía.

## Qué pueden cambiar las intervenciones determinísticas

- El contenido de `traceability` (únicamente los dos mecanismos
  descritos arriba, en ese orden de preferencia).
- Las listas `claims[].evidence_paths`/`evidence_ids` de un campo de
  negocio — nunca su valor de texto.

## Qué NO pueden cambiar

- El valor de `title`, `context`, `statement`, `condition`, `effect`,
  `parameters`, `parameter_source`, `outcome_code`.
- El `outcome_code` o cualquier hecho estructural derivado del
  `ContextPackage`.
- Ningún `claim` no puede crearse desde cero (ver "Creación
  determinística de `Claim`" arriba).
- La severidad de `unsupported_explicit_number`/
  `unsupported_explicit_date` para ningún campo de negocio — nunca se
  degrada a warning ni se desactiva.
- El schema de ningún contrato persistido.

## Semántica de fallo

Un candidato que agota el ciclo de reparación LLM (2 intentos) sin
resolver sus violaciones queda rechazado — nunca se presenta como
`EVIDENCE_VALIDATED` por defecto ni por timeout. Un rechazo real de
negocio (afirmación genuinamente no soportada) permanece rechazado
indefinidamente hasta que el `ContextPackage` la respalde o el modelo
produzca un `RuleDraft` distinto que no la incluya.

## Auditabilidad y procedencia

Toda intervención determinística se registra como un
`RepairAttemptRecord` (`contracts/guardrail_candidate.py`) con
`response_hash=None` — el mismo campo, ya opcional en el contrato
desde antes de v1.18.2, sin cambio de schema. Esto distingue,
íntegramente en el historial persistido (`repair_history`), una
corrección determinística (cero llamadas LLM) de un intento LLM real
(`response_hash` presente). `repair_attempts_used` (expuesto a UI/API)
cuenta únicamente los segundos — una intervención determinística nunca
consume presupuesto de `LLM_REPAIR_ATTEMPTS` ni aparece como un
reintento ante el operador.
