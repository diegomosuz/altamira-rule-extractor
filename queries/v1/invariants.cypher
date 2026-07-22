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
       'Paragraph sin Program' AS message;
