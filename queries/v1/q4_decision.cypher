MATCH (par:Paragraph {id: $candidate_id})-[:HAS_DECISION]->(dec:Decision)
RETURN dec.id AS decision_id,
       dec.expression AS condition,
       dec.normalized_expression AS normalized_condition,
       dec.operands AS operands,
       dec.rule_type AS rule_type,
       dec.outcome_code AS outcome_code,
       dec.line_start AS line_start,
       dec.line_end AS line_end;
