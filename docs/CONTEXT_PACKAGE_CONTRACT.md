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
