CANDIDATE_ID:
{{CANDIDATE_ID}}

PAYLOAD_RECHAZADO:
{{REJECTED_PAYLOAD_JSON}}

ERRORES_DE_VALIDACION:
{{VALIDATION_ERRORS_JSON}}

EVIDENCE_CATALOG:
{{EVIDENCE_CATALOG_JSON}}

Corrige UNICAMENTE los errores listados arriba. Cada claim debe usar
evidence_refs con alias EXACTOS del EVIDENCE_CATALOG (p. ej. "E001"):
nunca inventes, abrevies, modifiques ni corrijas un alias que no este
ahi literalmente. Nunca escribas evidence_id ni evidence_path reales.
Devuelve el JSON corregido, sin explicaciones ni texto adicional.
