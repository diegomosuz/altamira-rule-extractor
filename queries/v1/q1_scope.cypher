MATCH (par:Paragraph {id: $paragraph_id})
      <-[:CONTAINS]-(prog:Program)
      <-[:EXECUTES_VIA]-(op:Operation)
      <-[:HAS_OPERATION]-(app:Application)
      <-[:HAS_APPLICATION]-(country:Country)
RETURN country.code AS country,
       app.name AS application,
       op.logical_name AS operation_logical,
       op.description AS operation_description,
       prog.name AS program,
       prog.version AS program_version,
       par.name AS paragraph,
       par.line_start AS line_start,
       par.line_end AS line_end,
       prog.source_file AS source_file,
       prog.source_package_hash AS source_package_hash;
