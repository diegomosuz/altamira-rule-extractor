MATCH (par:Paragraph {id: $candidate_id})-[:USES]->(di:DataItem)
MATCH (di)-[m:HAS_DOMAIN_TERM]->(term:DomainTerm)
RETURN DISTINCT di.id AS data_item_id,
       di.name AS technical_name,
       di.semantic_tag AS semantic_tag,
       term.id AS domain_term_id,
       term.functional_name AS functional_name,
       term.definition AS definition,
       term.entity_type AS entity_type,
       term.authoritative_source AS authoritative_source,
       term.source_kind AS source_kind,
       term.catalog_version AS catalog_version,
       coalesce(m.confidence, term.confidence) AS confidence;
