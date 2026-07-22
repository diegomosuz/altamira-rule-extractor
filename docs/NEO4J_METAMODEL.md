# Metamodelo Neo4j V1

## 1. Nodos

### Country - C3, D1

Obligatorias:

- id
- code
- name

### Application - C3, D1

- id
- name
- country_code

### Operation - C3, D1

- id
- logical_name
- description
- country_code
- application_name

### Program - C1, D1/D5

- id
- name
- version
- source_file
- source_hash
- source_package_hash
- source_format
- encoding

### Paragraph - C1, D1/D2/D4

- id
- name
- source_text
- line_start
- line_end
- source_file
- source_package_hash

### DataItem - C1, D3/D4/D5/D7

- id
- name
- qualified_name
- level
- pic
- usage
- semantic_tag
- semantic_confidence
- semantic_evidence_json
- source_file
- line
- source_package_hash

### Decision - C1, D4/D5

- id
- expression
- normalized_expression
- operands
- outcome_code
- rule_type
- line_start
- line_end
- source_package_hash

### Table - C1/C2, D3/D5

- id
- name
- schema_name
- country_code
- source_package_hash cuando sea una vista específica de la ingesta

### ParameterTable - C2, D3

Debe tener labels `Table` y `ParameterTable`.

- id
- name
- snapshot_date
- ddl_file
- snapshot_file
- snapshot_hash
- source_package_hash

### ParameterEntry - C2, D3

- id
- row_number
- row_hash
- raw_row_json
- valores escalares normalizados como propiedades adicionales
- source_package_hash

### BatchJob - C4, D6

- id
- name
- scheduler
- schedule
- window_start
- window_end
- country_code

No se puebla en V1.

### DomainTerm - transversal, D7

- id
- functional_name
- definition
- entity_type
- authoritative_source
- source_kind
- catalog_version
- confidence

## 2. Relaciones

### Jerarquía

```text
Country -[:HAS_APPLICATION]-> Application
Application -[:HAS_OPERATION]-> Operation
Operation -[:EXECUTES_VIA]-> Program
```

### Código

```text
Program -[:CONTAINS]-> Paragraph
Paragraph -[:HAS_DECISION]-> Decision
Decision -[:LEADS_TO]-> DataItem
Paragraph -[:USES]-> DataItem
```

### CPG reducido

```text
Paragraph -[:DATA_DEPENDS_ON]-> Paragraph
Paragraph -[:CONTROL_DEPENDS_ON]-> Paragraph
```

La dirección es `origen que influye -> párrafo dependiente`.

### Datos

```text
Paragraph -[:READS]-> Table
Paragraph -[:WRITES|UPDATES|INSERTS]-> Table
ParameterTable -[:HAS_ENTRY]-> ParameterEntry
BatchJob -[:READS]-> Table
```

### Batch

```text
BatchJob -[:PRECEDED_BY]-> BatchJob
BatchJob -[:TRIGGERS]-> BatchJob
```

### Glosario

```text
DataItem -[:HAS_DOMAIN_TERM]-> DomainTerm
```

## 3. Propiedades de evidencia en relaciones

Las relaciones derivadas de código deben incluir cuando aplique:

- source_file
- line_start
- line_end
- source_package_hash
- derivation_rule
- confidence

Accesos SQL:

- sql_operation
- predicate_text
- host_variables_json

Dependencias:

- variables_json
- control_construct
- dependency_depth

## 4. Constraints

Crear unicidad por `id` para todos los labels.

Índices:

- Program.source_package_hash
- Paragraph.source_package_hash
- DataItem.source_package_hash
- Decision.source_package_hash
- ParameterTable.source_package_hash
- ParameterEntry.source_package_hash
- DataItem.semantic_tag

## 5. Reglas de identidad

No usar `Program.name` como clave.

Program incluye versión y hash de código. Paragraph, DataItem y Decision derivan del Program.id.

## 6. Invariantes principales

- Country.code no vacío.
- Toda Application tiene exactamente un Country entrante.
- Toda Operation tiene exactamente una Application entrante.
- Todo Program tiene al menos una Operation entrante.
- Todo Paragraph tiene exactamente un Program entrante.
- Paragraph.source_text no nulo.
- Toda Decision tiene exactamente un Paragraph entrante.
- Toda Decision V1 candidata tiene LEADS_TO.
- Todo ParameterEntry tiene exactamente un ParameterTable entrante.
- Todo semantic_tag pertenece al catálogo.
- Toda relación técnica entre Paragraphs pertenece a la misma versión de programa o documenta el cruce explícitamente.
- Todo nodo técnico contiene source_package_hash.
