# Contrato del paquete contextual

## Estructura

```text
candidate
scope              D1
code_slice         D2
data_context       D3
decision           D4
effects            D5
batch_context      D6
domain_glossary    D7
evidence
completeness
```

## Candidate

- candidate_id
- decision_id
- detector_id
- detector_version
- detector_score
- status = DETECTED_CANDIDATE

### Identidad de `candidate_id`

`candidate_id` es determinístico para la misma implementación y la
misma entrada de paquete — nunca UUID, timestamp ni orden de
ejecución. No es una garantía contractual de estabilidad indefinida
entre versiones de producto: cuando la semántica de detección de un
candidato se corrige (p. ej. el texto de `condition` se vuelve más
preciso), el release correspondiente puede preservar el identificador
anterior donde es seguro hacerlo, o hacerlo evolucionar cuando
preservarlo colisionaría dos candidatos genuinamente distintos bajo el
mismo identificador — nunca lo segundo por accidente. Ver
`docs/release/RELEASE_NOTES_1.18.2.md`, "Compatibilidad de
`candidate_id`", para el caso real más reciente de esta política.

## D1 - Scope

- country
- application
- operation
- program
- version
- paragraph
- source_file
- line range
- source_package_hash

## D2 - Code slice

Incluye el candidato y hasta cuatro niveles configurables de:

- DATA_DEPENDS_ON
- CONTROL_DEPENDS_ON

Cada párrafo incluye razón de inclusión y evidencia.

## D3 - Data context

### Tablas transaccionales

Lista de tablas leídas con evidencia.

### Paramétricas

Cada tabla incluye:

- snapshot_date;
- predicates;
- resolved predicates;
- unresolved predicates;
- applicability_status;
- rows approved for rule text;
- rows context only.

`applicable_rows` solo contiene filas con aplicabilidad demostrada.

## D4 - Decision

- expresión original;
- expresión normalizada;
- operandos;
- rule_type;
- outcome_code.

### Semántica de rama `EVALUATE`/`WHEN`

El nodo `Decision` del grafo Neo4j representa el sujeto completo de un
`EVALUATE` (p. ej. `SQLCODE`), compartido entre todas sus ramas `WHEN`
— nunca un nodo por rama. Sin embargo, `expresión`/`expresión
normalizada` en D4 se resuelven por **candidato**, no por `Decision`
compartida: cuando la rama a la que ese candidato específico está
anclado es una comparación directa contra un literal puro, D4 expone
un predicado normalizado y estructurado — por ejemplo
`SQLCODE = 100` — en vez del sujeto crudo.

Reglas:

- `WHEN OTHER` permanece incondicional (sin predicado propio), como
  corresponde: no es una comparación.
- Formas no soportadas con seguridad (`EVALUATE TRUE` con
  condition-name, rangos `THRU`, `EVALUATE ... ALSO ...`, comparación
  contra otra variable) exponen el sujeto crudo compartido del
  `EVALUATE` — nunca un texto reconstruido de forma ambigua ni
  inventado.
- `code_slice` (D2) es contexto/provenance para el LLM, nunca el
  mecanismo autoritativo para reconstruir la semántica de una rama:
  cuando D4 puede exponer un predicado estructurado, ese predicado —
  no una coincidencia textual en `code_slice` — es la evidencia que
  respalda el `claim` del campo `condition` en el `RuleDraft`.

Ver `docs/release/RELEASE_NOTES_1.18.2.md`, "Semántica de rama
EVALUATE/WHEN", para el defecto real que motivó esta distinción.

## D5 - Effects

- códigos de retorno;
- efectos sobre tablas;
- tipo de operación;
- attribution_scope;
- approved_for_rule_text.

No afirmar como efecto directo un elemento `PROGRAM_CONTEXT`.

## D6 - Batch

En V1:

```json
{
  "status": "NOT_AVAILABLE",
  "downstream_jobs": []
}
```

## D7 - Glossary

Cada término contiene:

- technical_name;
- semantic_tag;
- domain_term_id;
- functional_name;
- definition;
- entity_type;
- source_kind;
- authoritative_source;
- confidence.

## Evidence

Cada afirmación técnica debe poder referenciar un evidence_id.

## Completeness

Estado por dimensión:

- COMPLETE
- PARTIAL
- NOT_AVAILABLE
- ERROR

La completitud no equivale a aprobación funcional.
