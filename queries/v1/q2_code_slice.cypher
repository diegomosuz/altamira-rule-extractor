MATCH (sink:Paragraph {id: $paragraph_id})
OPTIONAL MATCH (data_origin:Paragraph)-[:DATA_DEPENDS_ON*1..__DEPENDENCY_DEPTH__]->(sink)
WITH sink, collect(DISTINCT data_origin.id) AS data_ids
OPTIONAL MATCH (control_origin:Paragraph)-[:CONTROL_DEPENDS_ON*1..__DEPENDENCY_DEPTH__]->(sink)
WITH sink, data_ids, collect(DISTINCT control_origin.id) AS control_ids
UNWIND (data_ids + control_ids + [sink.id]) AS raw_id
WITH sink, data_ids, control_ids, collect(DISTINCT raw_id) AS all_ids
UNWIND all_ids AS para_id
MATCH (par:Paragraph {id: para_id})
WITH par, sink,
     par.id IN data_ids AS reached_via_data,
     par.id IN control_ids AS reached_via_control
WITH par,
     CASE
       WHEN par.id = sink.id THEN 'CANDIDATE'
       WHEN reached_via_data AND reached_via_control THEN 'BOTH'
       WHEN reached_via_data THEN 'DATA_DEPENDENCY'
       WHEN reached_via_control THEN 'CONTROL_DEPENDENCY'
       ELSE 'DEPENDENCY'
     END AS inclusion_reason
RETURN par.id AS paragraph_id,
       par.name AS paragraph_name,
       par.source_file AS source_file,
       par.source_text AS source_text,
       par.line_start AS line_start,
       par.line_end AS line_end,
       inclusion_reason
ORDER BY line_start;
