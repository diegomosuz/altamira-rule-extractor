"""Caso GUARDRAIL_REJECTED real (auditoria de seguridad de cierre de
Fase 13, `feat/unified-shadow-downstream-pipeline`) -- INDEPENDIENTE
del caso `DRAFT_GENERATION_FAILED` por alias inventado
(`tests/pipeline/test_unified_shadow_downstream_negative_cases.py::
test_case_c_...`). Aqui el `ContextPackage` es valido, el `RuleDraft`
es contractualmente valido y llega genuinamente a Guardrails, donde
`deterministic_guardrail.py::evaluate_guardrail` (productivo, NUNCA
modificado) lo rechaza por un marcador real de
`_INJECTION_MARKERS` ("ignore previous instructions") inyectado
UNICAMENTE en el texto libre del fake -- nunca alterando aliases de
evidencia, nunca modificando el guardrail para fabricar el rechazo."""

from __future__ import annotations

from pathlib import Path

import jsonschema

from altamira_extractor.pipeline.rule_draft_assembly import load_rule_draft_schema
from altamira_extractor.pipeline.unified_shadow_downstream_executor import (
    run_unified_shadow_downstream,
)
from altamira_extractor.pipeline.unified_shadow_draft_generator import (
    DeterministicFakeDraftProvider,
)

from ._unified_shadow_downstream_fixtures import downstream_golden_path
from ._unified_shadow_validation_fixtures import GROUP_ID

_SCHEMA_PATH = Path("schemas/rule-draft.schema.json")


def _validator() -> jsonschema.protocols.Validator:
    schema, _hash = load_rule_draft_schema(_SCHEMA_PATH)
    validator_cls = jsonschema.validators.validator_for(schema)
    return validator_cls(schema)


def test_real_prompt_injection_marker_produces_genuine_guardrail_rejected() -> None:
    """Caso independiente de `GUARDRAIL_REJECTED`: ContextPackage
    valido -> RuleDraft contractualmente valido (evidence_refs
    plenamente resueltos, la asamblea productiva NUNCA falla aqui) ->
    llega a Guardrails -> `evaluate_guardrail` (productivo, sin
    modificar) detecta el marcador real `_INJECTION_MARKERS` en
    `statement` -> `GuardrailReport.verdict=REJECTED` real ->
    `execution_status=GUARDRAIL_REJECTED`, `disposition=
    COMPLETED_WITH_REJECTIONS` -- el draft se PRESERVA (nunca se
    elimina), ninguna regla Markdown se genera, ningun artifact
    productivo se modifica (ejecutor 100% puro, sin filesystem)."""
    dgp = downstream_golden_path()
    provider = DeterministicFakeDraftProvider(inject_guardrail_violation_marker=True)

    artifact = run_unified_shadow_downstream(
        run_id=dgp.unified_shadow.run_id,
        unified_shadow=dgp.unified_shadow,
        validation_report=dgp.validation_report,
        semantic_graph=dgp.semantic_graph,
        provider=provider,
        schema_validator=_validator(),
    )

    # --- 1-3: ContextPackage valido, RuleDraft valido, llega a Guardrails ---
    assert len(artifact.context_packages) == 1
    assert len(artifact.rule_drafts) == 1
    context_record = artifact.context_packages[0]
    draft_record = artifact.rule_drafts[0]
    assert draft_record.context_package_record_id == context_record.record_id
    assert draft_record.evidence_aliases_unresolved == []

    # --- 4-6: viola un guardrail REAL, GuardrailReport real, draft preservado ---
    assert len(artifact.guardrail_results) == 1
    guardrail_record = artifact.guardrail_results[0]
    assert guardrail_record.rule_draft_record_id == draft_record.record_id
    assert guardrail_record.status.value == "REJECTED"
    assert guardrail_record.guardrail_result.verdict.value == "REJECTED"
    assert len(guardrail_record.guardrail_result.violations) >= 1
    violation = guardrail_record.guardrail_result.violations[0]
    assert violation.rule == "possible_prompt_injection"
    assert violation.severity.value == "ERROR"
    # El draft PRESERVADO: sigue presente en la lista, nunca eliminado.
    assert artifact.rule_drafts[0].rule_draft.statement == draft_record.rule_draft.statement
    assert "ignore previous instructions" in draft_record.rule_draft.statement.lower()

    # --- 7: execution_status / guardrail status / disposition exactos ---
    group_result = artifact.group_results[0]
    assert group_result.group_id == GROUP_ID
    assert group_result.execution_status.value == "GUARDRAIL_REJECTED"
    assert group_result.guardrail_record_id == guardrail_record.record_id
    assert group_result.blocking_reasons != []
    assert artifact.disposition.value == "COMPLETED_WITH_REJECTIONS"
    assert artifact.summary.guardrail_rejected_count == 1
    assert artifact.summary.guardrail_passed_count == 0
    assert artifact.summary.technical_failure_count == 0

    # --- 8-9: ninguna regla Markdown, ningun artifact productivo modificado ---
    # El ejecutor es puro (sin filesystem, ver modulo): no existe ninguna ruta
    # de codigo en este test que escriba un archivo .md ni un artifact
    # productivo -- la unica escritura posible en Fase 13 es
    # diagnostics/unified-shadow-downstream.json, y este test nunca invoca
    # write_unified_shadow_downstream_artifact.

    # Determinismo: dos ejecuciones producen el mismo rechazo, bytes identicos.
    artifact_again = run_unified_shadow_downstream(
        run_id=dgp.unified_shadow.run_id,
        unified_shadow=dgp.unified_shadow,
        validation_report=dgp.validation_report,
        semantic_graph=dgp.semantic_graph,
        provider=provider,
        schema_validator=_validator(),
    )
    assert artifact.to_stable_json() == artifact_again.to_stable_json()

    print("\n--- Fase 13, caso GUARDRAIL_REJECTED real ---")
    print(
        f"test_node_id=tests/pipeline/test_unified_shadow_downstream_guardrail_rejected_real.py::"
        f"test_real_prompt_injection_marker_produces_genuine_guardrail_rejected | "
        f"guardrail_rule={violation.rule} | "
        f"draft_record_id={draft_record.record_id} | "
        f"guardrail_record_id={guardrail_record.record_id} | "
        f"blocking_reasons={group_result.blocking_reasons} | "
        f"execution_status={group_result.execution_status.value} | "
        f"disposition={artifact.disposition.value}"
    )
