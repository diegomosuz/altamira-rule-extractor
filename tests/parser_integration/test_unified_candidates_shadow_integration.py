"""Integracion real Fase 11 (auditoria de cierre, Partes 2/3): reutiliza
EXACTAMENTE el paquete real de
`test_candidate_promotion_assessment_integration.py::ready_blocked_zip`
(fixture `CALLER10`/`CALLEE10`/`STOPPER10`) que ya demostro, con JAR real
y Neo4j efimero real:

- un candidato V2 `V2_RETURN_CODE_PROPAGATION` determinista
  (`WS-COD-RETORNO`, `PROPAGATED_LITERAL` via la cadena
  `MOVE 'R001' TO WS-TEMP` + `MOVE WS-TEMP TO WS-COD-RETORNO` dentro de
  la MISMA rama del IF -- invisible para Q0, que solo colecciona
  descendientes con `assigned_literal is not None`);
- un candidato interprocedural `RETURN_CODE_RULE` determinista
  (`CALL 'CALLEE10' RETURNING WS-COD-RETORNO`);
- `EXACT_MATCH` real entre ambos (mismo target/output/family);
- cero candidatos V1 (Q0 nunca ve `WS-COD-RETORNO` en este paquete);
- cero conflictos;
- ambas referencias `READY_FOR_CONTROLLED_REVIEW`.

Nunca reconstruye un paquete COBOL por aproximacion: el paquete, el
assessment, el review package y el plan son 100% reales (JAR real,
Neo4j efimero real, sin fabricar V2/interprocedural/assessment/plan a
mano). Automatiza el flujo completo de Fase 9/10/11: assessment real ->
review package real -> manifiesto de decisiones humano sintetico (dos
`APPROVE_FOR_SHADOW_PROMOTION` validas y distintas) -> promotion plan
real -> unified candidates shadow real -- exactamente como lo haria un
operador via CLI (`candidate-promotion-assessment` ->
`candidate-promotion-review-package` -> `candidate-promotion-plan` ->
`unified-candidates-shadow`)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from altamira_extractor.contracts.candidate_promotion_assessment import CandidateSource
from altamira_extractor.contracts.candidate_promotion_review import (
    CandidatePromotionDecision,
    CandidatePromotionDecisionManifest,
    DecisionReasonCode,
    ReviewDecision,
)
from altamira_extractor.contracts.unified_candidates_shadow import (
    UnifiedShadowComparisonKind,
    UnifiedShadowGroupStatus,
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

from ..e2e_support import build_settings, require_jar
from .test_candidate_promotion_assessment_integration import _run_pipeline, ready_blocked_zip

pytestmark = pytest.mark.integration

# Reexportado para que pytest descubra el fixture importado (ruff/flake
# nunca lo marcan como no usado gracias al uso explicito como parametro
# de test mas abajo).
__all__ = ["ready_blocked_zip"]


def _stable_hash(obj: object) -> str:
    return hashlib.sha256(obj.to_stable_json().encode("utf-8")).hexdigest()  # type: ignore[attr-defined]


def test_real_two_equivalent_proposals_produce_single_valid_shadow_group(
    tmp_path: Path, ready_blocked_zip: Path
) -> None:
    """Parte 2/3 de la auditoria de cierre: dos propuestas APROBADAS
    equivalentes (V2 + interprocedural), 100% reales, deben producir
    exactamente 2 `shadow_members`, 1 `shadow_group` `VALID`/
    `NOT_IN_BASELINE`, con ambos `source_candidate_id`/`review_decision_id`
    preservados y ningun candidato fuente elegido como "ganador".

    Fase 15B4-CANDIDATE-QUALITY-5E: enhanced_candidates_enabled=False
    explicito -- el escenario exige baseline V1/Q0 en cero candidatos."""
    require_jar()
    settings = build_settings(tmp_path, enhanced_candidates_enabled=False)

    run_dir, run_id, succeeded_stages = _run_pipeline(settings, ready_blocked_zip)
    assert "PARSED" in succeeded_stages
    if "CANDIDATES_DETECTED" not in succeeded_stages:
        pytest.skip(
            "CANDIDATES_DETECTED no se alcanzo en este entorno (Neo4j no disponible) -- "
            "el escenario de dos propuestas equivalentes no es verificable sin V1/V2/"
            "interprocedural reales"
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
    assert {item.source for item in eligible_items} == {
        CandidateSource.V2,
        CandidateSource.INTERPROCEDURAL,
    }
    write_candidate_promotion_review_package(run_dir, review_package)

    # --- Manifiesto sintetico valido: dos decisiones humanas DISTINTAS ---
    assessment_hash = _stable_hash(assessment)
    review_hash = _stable_hash(review_package)
    decisions = [
        CandidatePromotionDecision(
            decision_id=f"decision::integration-test::{idx}::{item.review_item_id}",
            review_item_id=item.review_item_id,
            assessment_id=item.assessment_id,
            reference_id=item.reference_id,
            assessment_artifact_hash=assessment_hash,
            decision=ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION,
            reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
            reviewer_reference=f"integration-test-reviewer-{idx}@altamira.local",
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
    for item in plan.plan_items:
        if item.action.value == "PROPOSE_SHADOW_PROMOTION":
            assert item.status.value == "VALID"
    write_candidate_promotion_plan_artifact(run_dir, plan)

    # --- Unified candidates shadow real (Fase 11), via el servicio real ---
    artifact = compute_unified_candidates_shadow_artifact(run_dir, run_id)
    write_unified_candidates_shadow_artifact(run_dir, artifact)

    assert artifact.summary.v1_baseline_count == 0
    assert len(artifact.shadow_members) == 2
    assert len(artifact.shadow_groups) == 1

    member_by_source = {member.source: member for member in artifact.shadow_members}
    assert set(member_by_source) == {CandidateSource.V2, CandidateSource.INTERPROCEDURAL}
    v2_member = member_by_source[CandidateSource.V2]
    ip_member = member_by_source[CandidateSource.INTERPROCEDURAL]

    # Ambos source_candidate_id/review_decision_id preservados y DISTINTOS.
    assert v2_member.source_candidate_id != ip_member.source_candidate_id
    assert v2_member.review_decision_id != ip_member.review_decision_id
    assert v2_member.review_decision_id in {d.decision_id for d in decisions}
    assert ip_member.review_decision_id in {d.decision_id for d in decisions}

    for member in (v2_member, ip_member):
        assert member.rule_family.value == "RETURN_CODE"
        assert member.target == "WS-COD-RETORNO"
        assert member.output_literal == "R001"
        assert member.decision.value == "APPROVE_FOR_SHADOW_PROMOTION"

    group = artifact.shadow_groups[0]
    assert group.status == UnifiedShadowGroupStatus.VALID
    assert group.comparison_to_v1 == UnifiedShadowComparisonKind.NOT_IN_BASELINE
    assert sorted(group.member_ids) == sorted([v2_member.member_id, ip_member.member_id])
    assert group.rule_family.value == "RETURN_CODE"
    assert group.target == "WS-COD-RETORNO"
    assert group.output_literal == "R001"

    # Reconciliacion del summary.
    assert artifact.summary.proposed_plan_item_count == (
        artifact.summary.shadow_member_count + artifact.summary.excluded_plan_item_count
    )

    # Tabla real (para el informe de cierre).
    print("\n--- Fase 11, dos propuestas equivalentes (real, JAR+Neo4j reales) ---")
    print(
        f"unified_shadow_candidate_id={group.unified_shadow_candidate_id} | "
        f"status={group.status.value} | comparison_to_v1={group.comparison_to_v1.value}"
    )
    for member in (v2_member, ip_member):
        print(
            f"  member_id={member.member_id} | source={member.source.value} | "
            f"source_candidate_id={member.source_candidate_id} | "
            f"assessment_reference_id={member.assessment_reference_id} | "
            f"review_item_id={member.review_item_id} | plan_item_id={member.plan_item_id} | "
            f"review_decision_id={member.review_decision_id} | "
            f"family={member.rule_family.value} | target={member.target} | "
            f"output={member.output_literal}"
        )

    # Determinismo byte a byte: recalcular el servicio sobre el mismo run
    # (review package/plan ya persistidos) debe producir bytes identicos.
    artifact_again = compute_unified_candidates_shadow_artifact(run_dir, run_id)
    assert artifact.to_stable_json() == artifact_again.to_stable_json()
