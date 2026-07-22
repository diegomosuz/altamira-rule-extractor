MATCH (sink:Paragraph {id: $candidate_id})
OPTIONAL MATCH (origin:Paragraph)
  -[:DATA_DEPENDS_ON|CONTROL_DEPENDS_ON*1..4]->(sink)
WITH collect(DISTINCT origin) + [sink] AS paragraphs
UNWIND paragraphs AS par
MATCH (par)-[r:READS]->(pt:ParameterTable)
OPTIONAL MATCH (pt)-[:HAS_ENTRY]->(pe:ParameterEntry)
RETURN pt.id AS parameter_table_id,
       pt.name AS parameter_table,
       pt.snapshot_date AS snapshot_date,
       collect(DISTINCT properties(pe)) AS entries,
       collect(DISTINCT {
         paragraph_id: par.id,
         predicate_text: r.predicate_text,
         host_variables_json: r.host_variables_json,
         source_file: r.source_file,
         line_start: r.line_start,
         line_end: r.line_end
       }) AS access_evidence;
