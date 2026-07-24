MATCH (sink:Paragraph {id: $paragraph_id})
OPTIONAL MATCH (origin:Paragraph)
  -[:DATA_DEPENDS_ON|CONTROL_DEPENDS_ON*1..__DEPENDENCY_DEPTH__]->(sink)
WITH sink, collect(DISTINCT origin) AS origins
WITH origins + [sink] AS paragraphs
UNWIND paragraphs AS par
MATCH (par)-[r:READS]->(pt:ParameterTable)
OPTIONAL MATCH (pt)-[:HAS_ENTRY]->(pe:ParameterEntry)
RETURN pt.id AS parameter_table_id,
       pt.name AS parameter_table,
       pt.snapshot_date AS snapshot_date,
       collect(DISTINCT CASE WHEN pe IS NULL THEN null ELSE {
         parameter_entry_id: pe.id,
         row_number: pe.row_number,
         row_hash: pe.row_hash,
         raw_row_json: pe.raw_row_json,
         normalized_row_json: pe.normalized_row_json
       } END) AS entries,
       collect(DISTINCT {
         paragraph_id: par.id,
         predicate_text: r.predicate_text,
         host_variables_json: r.host_variables_json,
         source_file: r.source_file,
         line_start: r.line_start,
         line_end: r.line_end
       }) AS access_evidence;
