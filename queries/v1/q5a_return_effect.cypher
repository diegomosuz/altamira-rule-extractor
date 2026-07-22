MATCH (par:Paragraph {id: $candidate_id})-[:HAS_DECISION]->(dec:Decision)
RETURN dec.outcome_code AS return_code,
       dec.expression AS triggered_when,
       dec.id AS decision_id;
