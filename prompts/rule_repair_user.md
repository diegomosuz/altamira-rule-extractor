CONTEXT_PACKAGE:
{{CONTEXT_PACKAGE_JSON}}

EVIDENCE_CATALOG:
{{EVIDENCE_CATALOG_JSON}}

REJECTED_RULE_DRAFT:
{{REJECTED_RULE_DRAFT_JSON}}

GUARDRAIL_VIOLATIONS:
{{GUARDRAIL_VIOLATIONS_JSON}}

REJECTED_RULE_DRAFT ya usa evidence_refs con alias del EVIDENCE_CATALOG,
nunca evidence_id/evidence_path reales. Devuelve el RuleDraft corregido
usando el mismo formato: claims con evidence_refs, solo alias del
EVIDENCE_CATALOG.
