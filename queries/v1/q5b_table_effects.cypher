MATCH (candidate:Paragraph {id: $paragraph_id})
OPTIONAL MATCH (origin:Paragraph)
  -[:DATA_DEPENDS_ON|CONTROL_DEPENDS_ON*1..__DEPENDENCY_DEPTH__]->(candidate)
WITH candidate, collect(DISTINCT origin) + [candidate] AS slice
WITH candidate, slice, [p IN slice | p.id] AS slice_ids
UNWIND slice AS par
OPTIONAL MATCH (par)-[r:WRITES|UPDATES|INSERTS]->(t:Table)
WITH candidate, slice_ids,
     collect(DISTINCT
       CASE WHEN t IS NULL THEN null ELSE {
         table_id: t.id,
         table_name: t.name,
         operation: type(r),
         paragraph_id: par.id,
         attribution_scope: CASE
           WHEN par.id = candidate.id THEN 'DIRECT'
           ELSE 'DEPENDENCY_SLICE'
         END,
         source_file: r.source_file,
         line_start: r.line_start,
         line_end: r.line_end
       } END
     ) AS attributed
MATCH (candidate)<-[:CONTAINS]-(prog:Program)
OPTIONAL MATCH (prog)-[:CONTAINS]->(other:Paragraph)
  -[pr:WRITES|UPDATES|INSERTS]->(pt:Table)
WHERE other IS NULL OR NOT other.id IN slice_ids
WITH attributed,
     collect(DISTINCT
       CASE WHEN pt IS NULL THEN null ELSE {
         table_id: pt.id,
         table_name: pt.name,
         operation: type(pr),
         paragraph_id: other.id,
         attribution_scope: 'PROGRAM_CONTEXT',
         source_file: pr.source_file,
         line_start: pr.line_start,
         line_end: pr.line_end
       } END
     ) AS program_context
RETURN attributed, program_context;
