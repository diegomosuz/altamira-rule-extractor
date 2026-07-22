# Alineación con el metamodelo semántico

## Núcleo común

La aplicación debe producir un paquete contextual con las siete dimensiones:

| Dimensión | Nombre | Elementos principales |
|---|---|---|
| D1 | Alcance | Country, Application, Operation, Program, Paragraph |
| D2 | Implementación | Paragraph, source_text, DATA_DEPENDS_ON, CONTROL_DEPENDS_ON |
| D3 | Datos de entrada | DataItem, Table, ParameterTable, ParameterEntry, READS |
| D4 | Condición decisional | Decision, HAS_DECISION, operands, rule_type |
| D5 | Efectos | LEADS_TO, WRITES, UPDATES, INSERTS |
| D6 | Contexto batch | BatchJob, PRECEDED_BY, TRIGGERS |
| D7 | Vocabulario funcional | DataItem, DomainTerm, HAS_DOMAIN_TERM |

D1-D5 y D7 son obligatorias conceptualmente. D6 es opcional y queda `NOT_AVAILABLE` en el V1 cuando no se ingiere capa 4.

## Capas Altamira

### C1 - Código

- Program
- Paragraph
- DataItem
- Decision
- Table para accesos DB2 transaccionales
- relaciones de acceso y dependencias

### C2 - Parametría

- ParameterTable
- ParameterEntry
- snapshot_date explícita

### C3 - Repositorio Altamira

En V1 no se ingiere XML. Country, Application y Operation se crean desde el manifest del paquete. Esta es una fuente sustituta, no equivalente a la capa 3 completa.

### C4 - Batch

El metamodelo incluye BatchJob, PRECEDED_BY y TRIGGERS, pero V1 no los puebla.

## Ajustes de implementación necesarios

### 1. CPG reducido

El documento usa `DATA_DEPENDS_ON` y `CONTROL_DEPENDS_ON` en Q2 aunque no aparecen en la tabla principal de aristas. Se incorporan como extensión técnica necesaria para construir el slice de código.

No se implementa un CPG exhaustivo nodo por statement. Se construyen dependencias a nivel Paragraph, suficientes para D2.

### 2. SQL sin nodo semántico adicional

Los statements SQL viven en el JSON canónico. En el grafo se materializan como relaciones directas:

```text
Paragraph -[:READS]-> Table
Paragraph -[:WRITES|UPDATES|INSERTS]-> Table
```

Las relaciones llevan propiedades de evidencia:

- source_file
- line_start
- line_end
- sql_operation
- predicate_text
- source_package_hash

### 3. ParameterTable como especialización

Cada paramétrica se carga con dos labels:

```text
(:Table:ParameterTable)
```

Esto permite consultar todas las tablas como `Table` y excluir paramétricas mediante `WHERE NOT t:ParameterTable`.

### 4. DomainTerm explícito

`semantic_tag` describe el rol técnico-funcional de un DataItem.

`DomainTerm` representa el concepto funcional de negocio.

No son equivalentes.

Ejemplo:

```text
(:DataItem {name: "WS-IMPORTE", semantic_tag: "amount"})
  -[:HAS_DOMAIN_TERM]->
(:DomainTerm {
  functional_name: "importe solicitado",
  definition: "...",
  entity_type: "monetary_amount"
})
```

### 5. Identidad versionada

La fórmula conceptual `PROGRAMA::ELEMENTO` se conserva, pero Program incluye país, operativa, versión y hash del código. Esto evita mezclar dos versiones.

### 6. Candidato no equivale a regla aprobada

Q0 devuelve hipótesis estructurales. La aplicación conserva:

- detector;
- score;
- evidencia;
- estado `DETECTED_CANDIDATE`.

La validación funcional humana queda fuera del V1.

### 7. Aplicabilidad paramétrica

Una tabla puede contener filas no aplicables al candidato. El contexto distingue:

- filas exactamente aplicables;
- filas potenciales;
- filas solo contextuales.

El LLM no puede declarar como aplicable una fila cuya resolución sea parcial o no resuelta.

### 8. Efectos sobre tablas

La query original a nivel Program puede sobre-atribuir escrituras. La implementación conserva el concepto D5, pero clasifica la evidencia:

- DIRECT: escritura en el párrafo candidato.
- DEPENDENCY_SLICE: escritura en un párrafo del slice.
- PROGRAM_CONTEXT: escritura en otro párrafo del programa sin dependencia demostrada.

Solo DIRECT y DEPENDENCY_SLICE pueden aprobarse automáticamente para redacción.
