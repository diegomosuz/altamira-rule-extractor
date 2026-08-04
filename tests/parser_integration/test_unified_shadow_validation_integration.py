"""Integracion real Fase 12 (`feat/unified-shadow-differential-validation`):
reutiliza EXACTAMENTE el mismo escenario real de Fase 11
(`test_unified_candidates_shadow_integration.py`, fixture
`ready_blocked_zip` / `CALLER10`/`CALLEE10`/`STOPPER10`) -- JAR real,
Neo4j efimero real, sin fabricar ningun artefacto V2/interprocedural/
assessment/review/plan/unified-shadow a mano. Automatiza el flujo
completo: assessment real -> review package real -> manifiesto de
decisiones humano sintetico (dos `APPROVE_FOR_SHADOW_PROMOTION`
validas y distintas) -> promotion plan real -> unified candidates
shadow real (Fase 11) -> validacion diferencial real (Fase 12, esta
fase) -- exactamente como lo haria un operador via CLI.

Escenario esperado (identico al de Fase 11): un candidato V2
`V2_RETURN_CODE_PROPAGATION` y un candidato interprocedural
`RETURN_CODE_RULE`, `EXACT_MATCH` mutuo, cero candidatos V1
equivalentes, cero conflictos, un unico `shadow_group`
`VALID`/`NOT_IN_BASELINE` con 2 miembros -- que Fase 12 debe calificar
como estructuralmente `downstream_shadow_eligible=True`, con los 12
gates `PASS`, cero issues `BLOCKING`, y disposition
`QUALIFIED_FOR_DOWNSTREAM_SHADOW` (el unico issue admisible es el
`FUNCTIONAL_VALIDATION_REQUIRED` informativo -- nunca una afirmacion de
correccion funcional ni de promocion real)."""

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
from altamira_extractor.pipeline.unified_shadow_validation_service import (
    compute_unified_shadow_validation_report,
    write_unified_shadow_validation_report,
)

from ..e2e_support import build_settings, require_jar
from .test_candidate_promotion_assessment_integration import _run_pipeline, ready_blocked_zip

pytestmark = pytest.mark.integration

# Reexportado para que pytest descubra el fixture importado (ver
# `test_unified_candidates_shadow_integration.py`, mismo patron).
__all__ = ["ready_blocked_zip"]


def _stable_hash(obj: object) -> str:
    return hashlib.sha256(obj.to_stable_json().encode("utf-8")).hexdigest()  # type: ignore[attr-defined]


def test_real_two_equivalent_proposals_qualify_for_downstream_shadow(
    tmp_path: Path, ready_blocked_zip: Path
) -> None:
    """Flujo completo real Fase 9 -> 10 -> 11 -> 12: dos propuestas
    APROBADAS equivalentes (V2 + interprocedural), 100% reales, deben
    producir un unico `shadow_group` estructuralmente elegible para
    shadow downstream, con disposition `QUALIFIED_FOR_DOWNSTREAM_SHADOW`
    y cero issues BLOCKING -- nunca una promocion real, nunca una
    afirmacion de correccion funcional."""
    require_jar()
    settings = build_settings(tmp_path)

    run_dir, run_id, succeeded_stages = _run_pipeline(settings, ready_blocked_zip)
    assert "PARSED" in succeeded_stages
    if "CANDIDATES_DETECTED" not in succeeded_stages:
        pytest.skip(
            "CANDIDATES_DETECTED no se alcanzo en este entorno (Neo4j no disponible) -- "
            "la validacion diferencial Fase 12 no es verificable sin V1/V2/interprocedural "
            "reales"
        )

    # --- Assessment real (Fase 9) ---
    assessment = compute_candidate_promotion_assessment_artifact(run_dir, run_id)
    assert assessment.summary.v1_candidate_count == 0
    assert assessment.summary.conflict_count == 0
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
            decision_id=f"decision::f12-integration::{idx}::{item.review_item_id}",
            review_item_id=item.review_item_id,
            assessment_id=item.assessment_id,
            reference_id=item.reference_id,
            assessment_artifact_hash=assessment_hash,
            decision=ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION,
            reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
            reviewer_reference=f"f12-integration-reviewer-{idx}@altamira.local",
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
    assert plan.summary.propose_shadow_promotion_count == 2
    write_candidate_promotion_plan_artifact(run_dir, plan)

    # --- Unified candidates shadow real (Fase 11) ---
    unified_shadow = compute_unified_candidates_shadow_artifact(run_dir, run_id)
    write_unified_candidates_shadow_artifact(run_dir, unified_shadow)
    assert len(unified_shadow.shadow_members) == 2
    assert len(unified_shadow.shadow_groups) == 1

    # --- Validacion diferencial real (Fase 12, esta fase) ---
    report = compute_unified_shadow_validation_report(run_dir, run_id)
    report_path = write_unified_shadow_validation_report(run_dir, report)
    assert report_path.name == "unified-shadow-validation-report.json"

    assert report.disposition.value == "QUALIFIED_FOR_DOWNSTREAM_SHADOW"
    assert report.summary.shadow_group_count == 1
    assert report.summary.downstream_eligible_group_count == 1
    assert report.summary.blocking_issue_count == 0
    assert report.summary.error_count == 0

    for gate_result in report.gate_results:
        assert gate_result.status.value == "PASS", (
            f"{gate_result.gate.value} deberia ser PASS, es {gate_result.status.value}"
        )

    group_validation = report.group_validations[0]
    assert group_validation.group_status.value == "VALID"
    assert group_validation.comparison_to_v1.value == "NOT_IN_BASELINE"
    assert group_validation.structurally_valid is True
    assert group_validation.downstream_shadow_eligible is True
    assert sorted(group_validation.member_ids) == sorted(
        member.member_id for member in unified_shadow.shadow_members
    )

    issue_codes = {issue.code.value for issue in report.issues}
    assert issue_codes == {"FUNCTIONAL_VALIDATION_REQUIRED", "GROUP_NOT_IN_BASELINE"}
    for issue in report.issues:
        assert issue.severity.value == "INFO"

    # Determinismo byte a byte: recalcular el servicio sobre el mismo
    # run (fuentes ya persistidas) debe producir bytes identicos.
    report_again = compute_unified_shadow_validation_report(run_dir, run_id)
    assert report.to_stable_json() == report_again.to_stable_json()

    # Tabla real (para el informe de cierre, Fase 12 Parte 15).
    print("\n--- Fase 12, validacion diferencial real (JAR+Neo4j reales) ---")
    print(
        f"group_id={group_validation.group_id} | member_sources="
        f"{sorted(m.source.value for m in unified_shadow.shadow_members)} | "
        f"family={unified_shadow.shadow_groups[0].rule_family.value} | "
        f"target={unified_shadow.shadow_groups[0].target} | "
        f"output={unified_shadow.shadow_groups[0].output_literal} | "
        f"comparison_v1={group_validation.comparison_to_v1.value} | "
        f"group_status={group_validation.group_status.value} | "
        f"evidence_complete={report.summary.groups_with_complete_evidence_count == 1} | "
        f"provenance_complete={report.summary.groups_with_complete_provenance_count == 1} | "
        f"decision_trace_complete="
        f"{report.summary.groups_with_complete_decision_trace_count == 1} | "
        f"downstream_eligible={group_validation.downstream_shadow_eligible} | "
        f"issues={sorted(issue_codes)} | "
        f"disposition={report.disposition.value}"
    )
