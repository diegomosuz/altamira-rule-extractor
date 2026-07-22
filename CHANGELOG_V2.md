# Cambios respecto del diseño anterior

1. El metamodelo Neo4j ahora sigue expresamente los nodos y relaciones del documento de modelado:
   - Country, Application, Operation.
   - Program, Paragraph, DataItem, Decision.
   - Table, ParameterTable, ParameterEntry.
   - BatchJob.
   - DomainTerm.
2. Se elimina `SqlStatement` como nodo del grafo semántico. El SQL se conserva en el artefacto canónico y genera relaciones directas desde Paragraph hacia Table.
3. `ParameterTable` se modela como especialización con labels `:Table:ParameterTable`.
4. Se incorporan `DATA_DEPENDS_ON` y `CONTROL_DEPENDS_ON` como relaciones técnicas CPG necesarias para D2.
5. Se agrega una etapa obligatoria de validación de invariantes del grafo.
6. El glosario D7 ya no se deriva directamente de `semantic_tag`: se materializan nodos DomainTerm y relaciones HAS_DOMAIN_TERM.
7. Los efectos sobre tablas se clasifican por fuerza de atribución para evitar asignar a una regla todas las escrituras del programa.
8. La aplicabilidad de filas paramétricas queda explícita: EXACT, PARTIAL, UNRESOLVED o NOT_APPLICABLE.
9. El LLM solo puede citar filas y efectos marcados `approved_for_rule_text`.
10. Se separan tres conceptos:
    - candidato estructural;
    - redacción validada contra evidencia;
    - aprobación funcional humana.
11. Se añaden queries Cypher de referencia Q0-Q7 e invariantes.
12. Se actualiza la secuencia completa de prompts para Claude Code.
