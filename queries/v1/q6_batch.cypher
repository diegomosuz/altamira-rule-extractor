MATCH (par:Paragraph {id: $paragraph_id})<-[:CONTAINS]-(prog:Program)
MATCH (prog)-[:CONTAINS]->(:Paragraph)
      -[:WRITES|UPDATES|INSERTS]->(t:Table)
      <-[:READS]-(j:BatchJob)
OPTIONAL MATCH (j)-[:PRECEDED_BY]->(jp:BatchJob)
OPTIONAL MATCH (j)-[:TRIGGERS]->(jn:BatchJob)
RETURN DISTINCT j.id AS job_id,
       j.name AS job_name,
       j.schedule AS schedule,
       j.window_start AS window_start,
       j.window_end AS window_end,
       jp.name AS preceded_by,
       jn.name AS triggers_next;
