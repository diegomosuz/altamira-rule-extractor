CANDIDATE_ID:
{{CANDIDATE_ID}}

PAYLOAD_RECHAZADO:
{{REJECTED_PAYLOAD_JSON}}

ERRORES_DE_VALIDACION:
{{VALIDATION_ERRORS_JSON}}

EVIDENCE_CATALOG:
{{EVIDENCE_CATALOG_JSON}}

ALLOWED_CLAIM_FIELDS:
{{ALLOWED_CLAIM_FIELDS_JSON}}

Corrige UNICAMENTE los errores listados arriba. Cada claim debe usar
evidence_refs con alias EXACTOS del EVIDENCE_CATALOG (p. ej. "E001"):
nunca inventes, abrevies, modifiques ni corrijas un alias que no este
ahi literalmente. Nunca escribas evidence_id ni evidence_path reales.
Si algun error de validacion corresponde a claims[].field, el valor
corregido debe ser EXACTAMENTE uno de ALLOWED_CLAIM_FIELDS (nunca otro,
sin alias ni traducciones); si ninguno es semanticamente adecuado,
omite ese claim en vez de inventar un valor. Devuelve el JSON
corregido, sin explicaciones ni texto adicional.
