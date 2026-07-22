---
paths:
  - "queries/**/*.cypher"
  - "src/**/*neo4j*.py"
  - "src/**/*graph*.py"
---

# Neo4j y metamodelo

- Respetar exclusivamente el metamodelo documentado.
- Usar propiedades parametrizadas, nunca concatenar datos en Cypher.
- `ParameterTable` debe ser `:Table:ParameterTable`.
- Las relaciones READS/WRITES/UPDATES/INSERTS nacen de Paragraph o BatchJob.
- DATA_DEPENDS_ON y CONTROL_DEPENDS_ON conectan Paragraph con Paragraph.
- Ejecutar invariantes después de cada carga.
- IDs versionados y determinísticos.
- MERGE idempotente.
- No usar el nombre del programa como única clave.
- No usar APOC como requisito obligatorio.
