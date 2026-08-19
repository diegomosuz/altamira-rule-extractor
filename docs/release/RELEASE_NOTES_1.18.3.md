# Release notes 1.18.3

Hotfix PATCH (endurecimiento de confiabilidad, sin funcionalidad
nueva). Publicado sobre `v1.18.2`.

## Producto

### 1. Confiabilidad de evidencia de efectos de tabla

`table_effects` distingue `DIRECT` (ocurre en el mismo párrafo que la
decisión del candidato) de `DEPENDENCY_SLICE`/`PROGRAM_CONTEXT`
(alcanzado por dependencia o presente en otra parte del programa, sin
relación demostrada). Solo un efecto `DIRECT` y `approved_for_rule_text`
puede citarse cuando el campo describe explícitamente esa mutación de
datos. La corrección determinística de una cita incorrecta de
`table_effects` (Signal-A) es exacta: solo re-dirige hacia el alias
`DIRECT`/aprobado correcto cuando existe uno inequívoco para la misma
tabla; nunca cuando la señal es ambigua (Signal B ausente por diseño),
y nunca cuando ningún alias aprobado coincide con la tabla que el campo
describe (veto duro). Nunca borra un `Claim`, nunca reescribe el valor
de un campo de negocio.

### 2. Validación fail-closed de literales de negocio entre comillas

Nueva violación determinística `unsupported_explicit_literal`: un
literal de negocio entre comillas (p. ej. `'API'`, `'D203'`) en
`title`/`context`/`statement`/`condition`/`effect`/`parameter_source`/
`parameters` debe aparecer literalmente en evidencia **autoritativa
acotada** — únicamente `$.decision` o un `$.effects.return_codes[i]`
aprobado, nunca `code_slice` ni ningún otro tipo de evidencia, aunque
contenga el mismo texto. Mismo tratamiento fail-closed que los
literales numéricos/fecha ya existentes.

### 3. Gobernanza field-first y cierre del bypass de campo sin `Claim`

La evaluación de hechos explícitos gobernados (número/fecha/literal) es
ahora **field-first**: el campo siempre se evalúa, exista o no un
`Claim` que lo referencie. Antes de esta corrección, un campo sin
ningún `Claim` no se evaluaba — un hecho gobernado podía quedar
completamente sin verificar simplemente porque el modelo omitió crear
el `Claim`. Cerrado sin relajar ninguna otra regla: la ausencia de un
`Claim` nunca vuelve invisible un hecho explícito gobernado.

### 4. Aumento determinístico de ancla de evidencia + guía de reparación explícita

`augment_claims_with_authoritative_anchors` amplía, nunca crea, las
citas de evidencia de un `Claim` ya existente cuando el valor de un
campo ya es correcto pero cita evidencia más débil que la disponible.
Cuando el campo no tiene ningún `Claim` que ampliar, el mensaje de la
violación que recibe el modelo de reparación ahora indica
explícitamente la ruta autoritativa real (`$.decision` o
`$.effects.return_codes[i]`) cuando existe, o declara explícitamente
que ninguna existe — eliminando la necesidad de que el modelo
re-derive esa búsqueda por su cuenta. Enriquecimiento de **mensaje**
únicamente: nunca crea ni modifica un `Claim`.

### 5. Normalización de expresión con límites de token correctos

El parser Java preserva ahora el espaciado correcto entre
palabra-clave/operador/operando al reconstruir la representación
normalizada de `IF`/`EVALUATE`/`COMPUTE` (p. ej. `SQLCODE NOT = 0`, no
`SQLCODENOT=0`) — corrección de extracción, no de guardrail. Un campo
adicional `legacy_expression` (opcional, aditivo, `@JsonInclude(
NON_EMPTY)` en Java / `str | None = None` en Python) preserva la
representación glued **anterior** exacta que producía el generador
antes de esta corrección, exclusivamente para el mecanismo de
compatibilidad de `candidate_id` de V2/enhanced (ver
`pipeline/enhanced_candidate_integration.py`) — nunca se lee para
construir `RuleCandidate.condition`, `ContextPackage.decision.
expression`, `RuleDraft` ni evidencia de guardrail. Es metadata de
compatibilidad de identidad, no una garantía más fuerte de identidad
entre versiones: `candidate_id` sigue sin ser una promesa contractual
de estabilidad indefinida (ver `docs/CONTEXT_PACKAGE_CONTRACT.md`,
"Identidad de `candidate_id`").

### 6. Semántica de rama `EVALUATE`/`WHEN` preservada

La semántica de rama introducida en v1.18.2 (predicado estructurado
por rama, p. ej. `SQLCODE = 100`, en vez del sujeto crudo compartido
del `EVALUATE`) se preserva sin cambios a través de la normalización de
límites de token — verificado explícitamente contra el candidato real
de Ground Truth 15D.

### 7. Compatibilidad de `candidate_id` V1/V2

El mecanismo de compatibilidad de identidad entre el candidato legacy
(V1/Q0) y el candidato enhanced (V2) que fusiona el mismo hecho
detectado por ambos caminos permanece sin cambios — auditado
explícitamente contra el corpus de estrés y los 7 paquetes de
calificación obligatoria (ver "Compatibilidad de `candidate_id`" más
abajo).

### 8. Fiabilidad de `Claim` para hechos explícitos gobernados

Cuando un candidato afirma un literal/número/fecha gobernado en
`statement` (u otro campo de texto libre) respaldado por evidencia
autoritativa real, pero el borrador inicial no le asignó ningún
`Claim`, el escritor ahora recibe una excepción obligatoria explícita:
ese campo DEBE tener un `Claim` citando el alias correspondiente — la
regla general ("crea un `Claim` únicamente cuando exista evidencia")
sigue aplicando sin cambios para el resto del contenido no gobernado.
Nunca se crea un `Claim` de forma determinística: la obligación recae
en el modelo (escritor o reparación), el guardrail solo valida y,
cuando es seguro, amplía citas ya existentes.

### 9. Completitud de `Claim` por campo, incluido `effect`, y multi-campo

Extensión narrow de (8): la obligación de `Claim` para un hecho
gobernado es **por campo, de forma independiente** — un `Claim` creado
para `statement` nunca satisface la misma obligación para `effect` ni
para ningún otro campo, `effect` incluido explícitamente (antes
implícito, ahora explícito en el prompt). Cuando un borrador afirma
hechos gobernados en varios campos a la vez, el escritor debe crear un
`Claim` válido para CADA UNO antes de terminar su respuesta. La
reparación recibe la misma enseñanza: un `Claim` de un campo nunca
resuelve una violación reportada sobre otro, y cuando la lista de
violaciones conocidas abarca más de un campo, todos se corrigen en el
mismo intento de reparación — nunca de forma serial asumiendo que
quedará presupuesto para un intento adicional. El mecanismo de
detección (`deterministic_guardrail.py`) y el ciclo de reparación
(`guardrails_applied_stage.py`) ya se comportaban así antes de esta
corrección — un banco de 15 casos de regresión lo confirma
explícitamente contra el código sin cambios; el hueco real era
exclusivamente que el prompt del escritor nunca lo enseñaba de forma
explícita.

### 10. Invariante positivo de `return_code_effect`

`ContextPackage.effects.return_codes` garantiza de forma incondicional
la presencia del `outcome_code` de un candidato cuando este está
definido — un `ContextPackage` que perdiera ese hecho no se construye.
Esta corrección (originalmente parte del endurecimiento de integridad
determinística) cerró, como efecto colateral verificado, una
limitación histórica documentada de Catherine corregido (al menos un
candidato real sin evidencia `return_code_effect`, causa de una
excepción de calificación conocida). Ver "Cierre de excepción de
calificación" más abajo.

## Calificación y endurecimiento de release

- Cinco ejecuciones consecutivas genuinamente frescas contra
  `gpt-4o-mini` real (temperatura 0, `LLM_REPAIR_ATTEMPTS=2`) sobre el
  corpus de estrés inmutable de 48 reglas, antes y después de (9):
  antes, tres candidatos (`PAGBCH01::1200-CIERRE`,
  `PAGBCH01::1300-CONCILIACION`, `PAGAUX01::1500-PROPAGAR-06`)
  necesitaban reparación de forma reproducible en 5/5 corridas —
  `PAGAUX01::1500-PROPAGAR-06` agotando el presupuesto completo de 2
  intentos cada vez. Después: los cuatro candidatos objetivo alcanzan
  `EVIDENCE_VALIDATED` de primer intento en 19/20 ejecuciones
  combinadas; ningún candidato de la muestra vuelve a consumir 2
  intentos.
- Una ejecución adicional fresca del mismo corpus de 48 reglas contra
  la imagen final exacta de release: 8 programas, 48 candidatos, 48
  contextos, 48 borradores, 48 reglas finales, `COMPLETED`, 48/48
  `EVIDENCE_VALIDATED`.
- Los 7 paquetes de calificación obligatoria (Catherine, Catherine
  Corregido, Clientes Empresas, Prestamos Empresas, Consulta Saldos,
  Ground Truth 15B2A, Ground Truth 15D — 68 candidatos reales)
  ejecutados frescos contra la imagen final exacta: los 7 alcanzan
  `COMPLETED` con 100% de candidatos `EVIDENCE_VALIDATED`.
- Comparación de identidad de candidato contra una reconstrucción real
  de v1.18.2 (misma imagen publicada) sobre esos mismos 68 candidatos:
  ver "Compatibilidad de `candidate_id`" abajo.

Ninguna de estas actividades de calificación modificó código de
producto por sí misma.

## Compatibilidad de `candidate_id`

Medido contra los 7 paquetes obligatorios de calificación (68
candidatos reales, comparación directa v1.18.2 vs. v1.18.3, misma
imagen publicada de v1.18.2 reconstruida): **68 de 68 candidatos
mantienen exactamente el mismo `candidate_id`** — ningún candidato
cambia de identificador. Consistente con que ninguna corrección de
v1.18.3 tocó detección de candidatos, extracción del parser (más allá
del espaciado de expresión, que no participa en la función de
identidad) ni el mecanismo de compatibilidad V1/V2 heredado de
v1.18.2.

## Cierre de excepción de calificación (Catherine)

El test de calificación `tests/parser_integration/
test_catherine_no_regression_integration.py` documentaba una
limitación conocida y preexistente: al menos un candidato real de
Catherine corregido cuyo `ContextPackage` carecía de evidencia
`kind='return_code_effect'`, causa exacta por la que el fake oficial no
lograba completar `RULE_DRAFTS_GENERATED` para ese paquete. Esa
limitación dejó de reproducir — el commit `4d80bcd` ("fix: enforce
deterministic context integrity") ya garantiza de forma incondicional
la preservación de `return_code_effect` cuando `outcome_code` está
definido (ver "Invariante positivo de `return_code_effect`" arriba).
El test se actualizó para afirmar el invariante positivo actual en vez
de la limitación histórica — verificado en vivo contra el paquete real:
todos los candidatos con `outcome_code` definido preservan la
evidencia. La excepción de calificación conocida queda cerrada: 0
excepciones conocidas en la suite de integración.

## Compatibilidad

- Sin cambios de schema persistido en `RuleDraft`, `ContextPackage`,
  `EvidenceCatalog`, `GuardrailReport`, `GuardrailCandidateArtifact`.
- `CanonicalStatement.legacy_expression`/`legacyExpression`: campo
  opcional/aditivo nuevo, sin bump de versión de schema canónico
  (política del proyecto: un bump aplica a capacidades semánticas
  nuevas, no a cualquier campo aditivo; `canonical.py` no tiene un
  `schemas/*.json` externo versionado, a diferencia de context-package/
  rule-draft/semantic-graph).
- `GUARDRAIL_VERSION` permanece en el valor ya calificado por
  implementación (`deterministic_guardrail.py`), sin cambios.
- Sin cambio de metamodelo del grafo Neo4j.
- Sin cambio de UI/branding/versión de contrato OpenAPI (permanece en
  `1.0`, independiente de la versión de release).
- `candidate_id` no cambia para ningún candidato de los 7 paquetes de
  calificación obligatoria medidos — ver "Compatibilidad de
  `candidate_id`" arriba.

## Versión

- `pyproject.toml`, `parser/pom.xml` y
  `src/altamira_extractor/__init__.py::__version__`: `1.18.3`.
