MATCH (prog:Program)-[:CONTAINS]->(par:Paragraph)
      -[:HAS_DECISION]->(d:Decision)
      -[:LEADS_TO]->(sink:DataItem)
WHERE prog.source_package_hash = $package_hash
  AND sink.semantic_tag = 'return_code'
RETURN par.id AS candidate_id,
       par.name AS paragraph_name,
       d.id AS decision_id,
       d.expression AS condition,
       d.outcome_code AS outcome,
       d.rule_type AS rule_type,
       par.line_start AS line_start
ORDER BY line_start, decision_id;
