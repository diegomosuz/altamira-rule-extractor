MATCH (par:Paragraph {id: $paragraph_id})-[:HAS_DECISION]->(dec:Decision)
WHERE dec.id = $decision_id
RETURN dec.id AS decision_id,
       dec.expression AS condition,
       dec.normalized_expression AS normalized_condition,
       dec.operands_json AS operands_json,
       dec.rule_type AS rule_type,
       dec.outcome_code AS outcome_code,
       dec.line_start AS line_start,
       dec.line_end AS line_end;
