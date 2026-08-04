"""Integracion real Fase 13 (`feat/unified-shadow-downstream-pipeline`):
reutiliza EXACTAMENTE el mismo escenario real de Fase 9-12
(`test_unified_shadow_validation_integration.py`, fixture
`ready_blocked_zip` / `CALLER10`/`CALLEE10`/`STOPPER10`) -- JAR real,
Neo4j efimero real, sin fabricar ningun artefacto V2/interprocedural/
assessment/review/plan/unified-shadow/validation-report a mano.
Automatiza el flujo completo: assessment real -> review package real ->
manifiesto de decisiones humano sintetico (dos `APPROVE_FOR_SHADOW_
PROMOTION` validas y distintas) -> promotion plan real -> unified
candidates shadow real (Fase 11) -> validacion diferencial real
(Fase 12) -> ejecucion downstream shadow real (Fase 13, esta fase) --
exactamente como lo haria un operador via CLI, con el UNICO proveedor
admitido: el fake determinista oficial.

Escenario esperado (identico al de Fase 9-12): un unico `shadow_group`
`VALID`/`NOT_IN_BASELINE` con 2 miembros (V2 + interprocedural,
EXACT_MATCH), `downstream_shadow_eligible=True`, disposition de
validacion `QUALIFIED_FOR_DOWNSTREAM_SHADOW`. Fase 13 debe producir: 1
grupo elegible, 1 `ContextPackage` shadow real, 1 `RuleDraft` shadow
real, 1 `GuardrailReport` PASSED, disposition `COMPLETED` -- ambos
member IDs, ambos source candidate IDs y ambas decisiones humanas
preservados, sin publicar ninguna regla."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from altamira_extractor.contracts.candidate_promotion_review import (
    CandidatePromotionDecision,
    CandidatePromotionDecisionManifest,
    DecisionReasonCode,
    ReviewDecision,
)
from altamira_extractor.pipeline.candidate_promotion_assessment_service import (
    compute_candidate_promotion_assessment_artifact,
)
from altamira_extractor.pipeline.candidate_promotion_plan_builder import (
    build_candidate_promotion_plan,
)
from altamira_extractor.pipeline.candidate_promotion_plan_service import (
    write_candidate_promotion_plan_artifact,
)
from altamira_extractor.pipeline.candidate_promotion_review_generator import (
    generate_candidate_promotion_review_package,
)
from altamira_extractor.pipeline.candidate_promotion_review_service import (
    write_candidate_promotion_review_package,
)
from altamira_extractor.pipeline.unified_candidates_shadow_service import (
    compute_unified_candidates_shadow_artifact,
    write_unified_candidates_shadow_artifact,
)
from altamira_extractor.pipeline.unified_shadow_downstream_service import (
    compute_unified_shadow_downstream_artifact,
    write_unified_shadow_downstream_artifact,
)
from altamira_extractor.pipeline.unified_shadow_validation_service import (
    compute_unified_shadow_validation_report,
    write_unified_shadow_validation_report,
)

from ..e2e_support import build_settings, require_jar
from .test_candidate_promotion_assessment_integration import _run_pipeline, ready_blocked_zip

pytestmark = pytest.mark.integration

# Reexportado para que pytest descubra el fixture importado (mismo
# patron que `test_unified_shadow_validation_integration.py`).
__all__ = ["ready_blocked_zip"]


def _stable_hash(obj: object) -> str:
    return hashlib.sha256(obj.to_stable_json().encode("utf-8")).hexdigest()  # type: ignore[attr-defined]


def test_real_two_equivalent_proposals_complete_downstream_shadow(
    tmp_path: Path, ready_blocked_zip: Path
) -> None:
    """Flujo completo real Fase 9 -> 10 -> 11 -> 12 -> 13: dos
    propuestas APROBADAS equivalentes (V2 + interprocedural), 100%
    reales, califican para downstream shadow y producen un unico
    ContextPackage/RuleDraft/GuardrailReport shadow real, PASSED,
    disposition COMPLETED -- nunca una regla publicada, nunca un
    proveedor LLM real."""
    require_jar()
    settings = build_settings(tmp_path)

    run_dir, run_id, succeeded_stages = _run_pipeline(settings, ready_blocked_zip)
    assert "PARSED" in succeeded_stages
    if "CANDIDATES_DETECTED" not in succeeded_stages:
        pytest.skip(
            "CANDIDATES_DETECTED no se alcanzo en este entorno (Neo4j no disponible) -- "
            "la ejecucion downstream Fase 13 no es verificable sin V1/V2/interprocedural "
            "reales"
        )

    # --- Assessment real (Fase 9) ---
    assessment = compute_candidate_promotion_assessment_artifact(run_dir, run_id)
    assert assessment.summary.ready_for_controlled_review_count == 2

    # --- Review package real (Fase 10) ---
    review_package = generate_candidate_promotion_review_package(assessment)
    eligible_items = [
        item for item in review_package.review_items if item.eligibility.value == "ELIGIBLE"
    ]
    assert len(eligible_items) == 2
    write_candidate_promotion_review_package(run_dir, review_package)

    # --- Manifiesto sintetico valido: dos decisiones humanas DISTINTAS ---
    assessment_hash = _stable_hash(assessment)
    review_hash = _stable_hash(review_package)
    decisions = [
        CandidatePromotionDecision(
            decision_id=f"decision::f13-integration::{idx}::{item.review_item_id}",
            review_item_id=item.review_item_id,
            assessment_id=item.assessment_id,
            reference_id=item.reference_id,
            assessment_artifact_hash=assessment_hash,
            decision=ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION,
            reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
            reviewer_reference=f"f13-integration-reviewer-{idx}@altamira.local",
        )
        for idx, item in enumerate(eligible_items, start=1)
    ]
    manifest = CandidatePromotionDecisionManifest(
        review_package_hash=review_hash,
        assessment_artifact_hash=assessment_hash,
        run_id=run_id,
        decisions=decisions,
    )

    # --- Promotion plan real (Fase 10) ---
    plan = build_candidate_promotion_plan(
        assessment=assessment, review_package=review_package, manifest=manifest
    )
    write_candidate_promotion_plan_artifact(run_dir, plan)

    # --- Unified candidates shadow real (Fase 11) ---
    unified_shadow = compute_unified_candidates_shadow_artifact(run_dir, run_id)
    write_unified_candidates_shadow_artifact(run_dir, unified_shadow)
    assert len(unified_shadow.shadow_members) == 2
    assert len(unified_shadow.shadow_groups) == 1
    group = unified_shadow.shadow_groups[0]

    # --- Validacion diferencial real (Fase 12) ---
    report = compute_unified_shadow_validation_report(run_dir, run_id)
    write_unified_shadow_validation_report(run_dir, report)
    assert report.disposition.value == "QUALIFIED_FOR_DOWNSTREAM_SHADOW"
    assert report.summary.downstream_eligible_group_count == 1

    # --- Ejecucion downstream shadow real (Fase 13, esta fase) ---
    downstream = compute_unified_shadow_downstream_artifact(run_dir, run_id, settings=settings)
    downstream_path = write_unified_shadow_downstream_artifact(run_dir, downstream)
    assert downstream_path.name == "unified-shadow-downstream.json"

    assert downstream.provider.value == "DETERMINISTIC_FAKE"
    assert downstream.disposition.value == "COMPLETED"
    assert downstream.summary.validation_group_count == 1
    assert downstream.summary.downstream_eligible_group_count == 1
    assert downstream.summary.executed_group_count == 1
    assert downstream.summary.context_package_count == 1
    assert downstream.summary.rule_draft_count == 1
    assert downstream.summary.guardrail_passed_count == 1
    assert downstream.summary.guardrail_rejected_count == 0
    assert downstream.summary.technical_failure_count == 0

    group_result = downstream.group_results[0]
    assert group_result.group_id == group.unified_shadow_candidate_id
    assert group_result.execution_status.value == "EXECUTED"
    assert group_result.downstream_shadow_eligible is True
    assert sorted(group_result.member_ids) == sorted(group.member_ids)
    assert sorted(group_result.source_candidate_ids) == sorted(
        {m.source_candidate_id for m in unified_shadow.shadow_members}
    )
    assert sorted(group_result.review_decision_ids) == sorted(
        {m.review_decision_id for m in unified_shadow.shadow_members}
    )
    assert len(group_result.source_candidate_ids) == 2
    assert len(group_result.review_decision_ids) == 2

    context_record = downstream.context_packages[0]
    assert context_record.group_id == group.unified_shadow_candidate_id
    assert sorted(context_record.member_ids) == sorted(group.member_ids)
    assert context_record.evidence_aliases != []

    draft_record = downstream.rule_drafts[0]
    assert draft_record.context_package_record_id == context_record.record_id
    assert draft_record.provider.value == "DETERMINISTIC_FAKE"
    assert draft_record.evidence_aliases_unresolved == []
    assert draft_record.evidence_aliases_used != []

    guardrail_record = downstream.guardrail_results[0]
    assert guardrail_record.rule_draft_record_id == draft_record.record_id
    assert guardrail_record.status.value == "PASSED"
    assert guardrail_record.blocking_reasons == []
    assert guardrail_record.guardrail_result.verdict.value == "EVIDENCE_VALIDATED"
    assert not hasattr(guardrail_record.guardrail_result, "evaluated_at")
    assert '"evaluated_at"' not in downstream.to_stable_json()

    # Determinismo byte a byte: recalcular el servicio sobre el mismo
    # run (fuentes ya persistidas) debe producir bytes identicos.
    downstream_again = compute_unified_shadow_downstream_artifact(
        run_dir, run_id, settings=settings
    )
    assert downstream.to_stable_json() == downstream_again.to_stable_json()

    # Tabla real (para el informe de cierre, Fase 13 Parte 13/15).
    print("\n--- Fase 13, ejecucion downstream shadow real (JAR+Neo4j reales) ---")
    print(
        f"group_id={group_result.group_id} | "
        f"member_sources={sorted(m.source.value for m in unified_shadow.shadow_members)} | "
        f"source_candidate_ids={sorted(group_result.source_candidate_ids)} | "
        f"context_package_hash={context_record.context_package_hash} | "
        f"evidence_aliases={sorted(context_record.evidence_aliases)} | "
        f"rule_draft_hash={draft_record.rule_draft_hash} | "
        f"aliases_used={sorted(draft_record.evidence_aliases_used)} | "
        f"guardrail_status={guardrail_record.status.value} | "
        f"blocking_reasons={guardrail_record.blocking_reasons} | "
        f"execution_status={group_result.execution_status.value} | "
        f"disposition={downstream.disposition.value}"
    )
