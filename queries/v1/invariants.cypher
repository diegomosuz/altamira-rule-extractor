// Cada bloque debe devolver cero filas para una ingesta válida.

MATCH (p:Paragraph)
WHERE p.source_package_hash = $package_hash
  AND (p.source_text IS NULL OR trim(p.source_text) = '')
RETURN 'PARAGRAPH_WITHOUT_SOURCE_TEXT' AS code,
       'ERROR' AS severity,
       p.id AS entity_id,
       'Paragraph sin source_text' AS message

UNION ALL

MATCH (d:Decision)
WHERE d.source_package_hash = $package_hash
  AND NOT (d)<-[:HAS_DECISION]-(:Paragraph)
RETURN 'ORPHAN_DECISION' AS code,
       'ERROR' AS severity,
       d.id AS entity_id,
       'Decision sin Paragraph' AS message

UNION ALL

MATCH (pe:ParameterEntry)
WHERE pe.source_package_hash = $package_hash
  AND NOT (:ParameterTable)-[:HAS_ENTRY]->(pe)
RETURN 'ORPHAN_PARAMETER_ENTRY' AS code,
       'ERROR' AS severity,
       pe.id AS entity_id,
       'ParameterEntry sin ParameterTable' AS message

UNION ALL

MATCH (di:DataItem)
WHERE di.source_package_hash = $package_hash
  AND NOT di.semantic_tag IN $allowed_semantic_tags
RETURN 'INVALID_SEMANTIC_TAG' AS code,
       'ERROR' AS severity,
       di.id AS entity_id,
       'semantic_tag fuera del catálogo' AS message

UNION ALL

MATCH (prog:Program)
WHERE prog.source_package_hash = $package_hash
  AND NOT (:Operation)-[:EXECUTES_VIA]->(prog)
RETURN 'PROGRAM_WITHOUT_OPERATION' AS code,
       'ERROR' AS severity,
       prog.id AS entity_id,
       'Program sin Operation' AS message

UNION ALL

MATCH (par:Paragraph)
WHERE par.source_package_hash = $package_hash
  AND NOT (:Program)-[:CONTAINS]->(par)
RETURN 'PARAGRAPH_WITHOUT_PROGRAM' AS code,
       'ERROR' AS severity,
       par.id AS entity_id,
       'Paragraph sin Program' AS message

UNION ALL

// Gap real 1/2 (Prompt 0): toda ParameterTable debe conservar tambien el
// label Table (docs/NEO4J_METAMODEL.md: "Debe tener labels Table y
// ParameterTable"). El contrato SemanticGraph ya lo exige antes de
// persistir 04-semantic-graph.json; este bloque detecta si la CARGA en
// si (no el artefacto) corrompio ese invariante.
MATCH (t:ParameterTable)
WHERE t.source_package_hash = $package_hash
  AND NOT t:Table
RETURN 'PARAMETER_TABLE_WITHOUT_TABLE_LABEL' AS code,
       'ERROR' AS severity,
       t.id AS entity_id,
       'ParameterTable sin label Table' AS message

UNION ALL

// Gap real 2/2 (Prompt 0): toda relacion administrada debe tener
// endpoints (origen/destino) con labels compatibles con su tipo, segun
// la misma tabla de endpoints gobernante de
// contracts/semantic_graph.py (_RELATIONSHIP_ENDPOINTS). Se recibe como
// $allowed_relationship_signatures (lista de {type, from_label,
// to_label}) para no duplicar manualmente un bloque por cada uno de los
// 17 RelationshipType. No usa APOC: ANY(...) es Cypher estandar.
MATCH (a)-[r]->(b)
WHERE r.source_package_hash = $package_hash
  AND NOT ANY(
        signature IN $allowed_relationship_signatures
        WHERE signature.type = type(r)
          AND signature.from_label IN labels(a)
          AND signature.to_label IN labels(b)
      )
RETURN 'INVALID_RELATIONSHIP_ENDPOINT' AS code,
       'ERROR' AS severity,
       type(r) + '::' + a.id + '->' + b.id AS entity_id,
       'relacion sin firma de endpoints permitida para su tipo' AS message;
