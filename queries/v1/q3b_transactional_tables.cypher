MATCH (sink:Paragraph {id: $paragraph_id})
OPTIONAL MATCH (origin:Paragraph)
  -[:DATA_DEPENDS_ON|CONTROL_DEPENDS_ON*1..__DEPENDENCY_DEPTH__]->(sink)
WITH sink, collect(DISTINCT origin) AS origins
WITH origins + [sink] AS paragraphs
UNWIND paragraphs AS par
OPTIONAL MATCH (par)-[r:READS]->(t:Table)
WHERE t IS NULL OR NOT t:ParameterTable
WITH collect(DISTINCT
  CASE WHEN t IS NULL THEN null ELSE {
    table_id: t.id,
    table_name: t.name,
    paragraph_id: par.id,
    source_file: r.source_file,
    line_start: r.line_start,
    line_end: r.line_end
  } END
) AS tables_read
RETURN tables_read;
