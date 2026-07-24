MATCH (par:Paragraph {id: $paragraph_id})-[:HAS_DECISION]->(dec:Decision)
WHERE dec.id = $decision_id
RETURN dec.outcome_code AS return_code,
       dec.expression AS triggered_when,
       dec.id AS decision_id;
