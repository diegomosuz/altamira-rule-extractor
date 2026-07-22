MATCH (sink:Paragraph {id: $candidate_id})
OPTIONAL MATCH p=(origin:Paragraph)
  -[:DATA_DEPENDS_ON|CONTROL_DEPENDS_ON*1..4]->(sink)
WITH sink, collect(DISTINCT origin) + [sink] AS paragraphs
UNWIND paragraphs AS par
WITH DISTINCT par, sink,
     CASE WHEN par.id = sink.id THEN 'CANDIDATE' ELSE 'DEPENDENCY' END AS inclusion_reason
RETURN par.id AS paragraph_id,
       par.name AS paragraph_name,
       par.source_file AS source_file,
       par.source_text AS source_text,
       par.line_start AS line_start,
       par.line_end AS line_end,
       inclusion_reason
ORDER BY line_start;
