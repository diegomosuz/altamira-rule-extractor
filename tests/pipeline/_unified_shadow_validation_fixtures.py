"""Fixtures sinteticas compartidas para los tests de la validacion
diferencial del artefacto unificado en shadow mode (Fase 12,
`feat/unified-shadow-differential-validation`). NO es un archivo de
tests (pytest lo ignora, no empieza con `test_`) -- construye un
escenario minimo pero INTERNAMENTE COHERENTE (satisface todos los
model_validator de Fase 9/10/11) de: `CandidateArtifact` V1 (vacio),
`CandidatePromotionAssessmentArtifact` con una referencia V2
`READY_FOR_CONTROLLED_REVIEW`, `CandidatePromotionReviewPackage`
ELIGIBLE, `CandidatePromotionPlanArtifact` con
`PROPOSE_SHADOW_PROMOTION`/`VALID`, y `UnifiedCandidatesShadowArtifact`
con un `shadow_member`/`shadow_group` VALID/NOT_IN_BASELINE -- el mismo
patron `family=RETURN_CODE`/`target=WS-COD-RETORNO`/`output=R001`
observado en la integracion real (Fase 11 closing). Cada test de
validador importa `golden_path()` y muta UNICAMENTE lo necesario para
aislar un hallazgo especifico."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from altamira_extractor.contracts.candidate import CandidateArtifact
from altamira_extractor.contracts.candidate_promotion_assessment import (
    CandidatePromotionAssessment,
    CandidatePromotionAssessmentArtifact,
    CandidatePromotionAssessmentSummary,
    CandidateSource,
    PromotionDisposition,
    RecommendedAction,
    SourceAvailability,
    UnifiedCandidateReference,
    UnifiedRuleFamily,
)
from altamira_extractor.contracts.candidate_promotion_plan import (
    CandidatePromotionPlanArtifact,
    CandidatePromotionPlanItem,
    CandidatePromotionPlanSummary,
    PromotionPlanAction,
    PromotionPlanItemStatus,
)
from altamira_extractor.contracts.candidate_promotion_review import (
    CandidatePromotionReviewPackage,
    CandidatePromotionReviewPackageSummary,
    CandidateReviewItem,
    DecisionReasonCode,
    ReviewDecision,
    ReviewEligibility,
)
from altamira_extractor.contracts.interprocedural_rule_candidates import (
    InterproceduralRuleCandidatesArtifact,
)
from altamira_extractor.contracts.unified_candidates_shadow import (
    UnifiedCandidatesShadowArtifact,
    UnifiedCandidatesShadowSummary,
    UnifiedShadowCandidateGroup,
    UnifiedShadowComparisonKind,
    UnifiedShadowGroupStatus,
    UnifiedShadowSourceMember,
    UnifiedShadowSupport,
)
from altamira_extractor.contracts.unified_shadow_validation import UnifiedShadowValidationReport
from altamira_extractor.contracts.v2_shadow_candidates import (
    V1V2CandidateComparison,
    V1V2ComparisonStatus,
    V2CandidateSourceReference,
    V2CandidateSupport,
    V2DetectorExecution,
    V2RuleType,
    V2ShadowCandidate,
    V2ShadowCandidatesArtifact,
    V2ShadowSummary,
)
from altamira_extractor.pipeline.unified_shadow_source_validator import LoadedSource
from altamira_extractor.pipeline.unified_shadow_validation_analyzer import (
    analyze_unified_shadow_validation,
)

HASH = "a" * 64
RUN_ID = "20260101T000000000000-aaaaaaaa"

REFERENCE_ID = "unified::v2::candidate-1"
ASSESSMENT_ID = "assessment::candidate-1"
REVIEW_ITEM_ID = "review::candidate-1"
PLAN_ITEM_ID = "plan::candidate-1"
DECISION_ID = "decision::candidate-1"
MEMBER_ID = "member::candidate-1"
GROUP_ID = "group::candidate-1"
SOURCE_CANDIDATE_ID = "v2::V2_RETURN_CODE_PROPAGATION::candidate-1"


@dataclass(frozen=True)
class GoldenPath:
    v1: CandidateArtifact
    assessment: CandidatePromotionAssessmentArtifact
    review_package: CandidatePromotionReviewPackage
    plan: CandidatePromotionPlanArtifact
    unified_shadow: UnifiedCandidatesShadowArtifact


def golden_path() -> GoldenPath:
    v1 = CandidateArtifact(
        run_id=RUN_ID,
        source_package_hash=HASH,
        semantic_graph_hash=HASH,
        invariants_query_hash=HASH,
        q0_query_hash=HASH,
    )

    reference = UnifiedCandidateReference(
        unified_reference_id=REFERENCE_ID,
        source=CandidateSource.V2,
        source_candidate_id=SOURCE_CANDIDATE_ID,
        source_artifact_hash=HASH,
        rule_family=UnifiedRuleFamily.RETURN_CODE,
        original_support="DETERMINISTIC",
        program="CALLER10",
        paragraph="MAIN",
        target="WS-COD-RETORNO",
        output_literal="R001",
        evidence_ids=["evidence::candidate-1"],
        provenance_references=["provenance::candidate-1"],
    )
    assessment_item = CandidatePromotionAssessment(
        assessment_id=ASSESSMENT_ID,
        reference_id=REFERENCE_ID,
        disposition=PromotionDisposition.READY_FOR_CONTROLLED_REVIEW,
        recommended_action=RecommendedAction.SUBMIT_FOR_CONTROLLED_FUNCTIONAL_REVIEW,
    )
    assessment = CandidatePromotionAssessmentArtifact(
        run_id=RUN_ID,
        source_package_hash=HASH,
        source_artifact_hashes={
            "artifacts/06-candidates.json": HASH,
            "v2-candidates-shadow(in-memory)": HASH,
        },
        candidate_references=[reference],
        assessments=[assessment_item],
        summary=CandidatePromotionAssessmentSummary(
            v1_candidate_count=0,
            v2_candidate_count=1,
            interprocedural_candidate_count=0,
            unified_reference_count=1,
            exact_match_relation_count=0,
            related_relation_count=0,
            conflict_count=0,
            baseline_v1_count=0,
            already_covered_count=0,
            ready_for_controlled_review_count=1,
            review_required_count=0,
            blocked_count=0,
            conflicting_count=0,
            not_evaluated_count=0,
            counts_by_source={CandidateSource.V2: 1},
            counts_by_rule_family={UnifiedRuleFamily.RETURN_CODE: 1},
            counts_by_disposition={PromotionDisposition.READY_FOR_CONTROLLED_REVIEW: 1},
        ),
    )

    review_item = CandidateReviewItem(
        review_item_id=REVIEW_ITEM_ID,
        assessment_id=ASSESSMENT_ID,
        reference_id=REFERENCE_ID,
        source=CandidateSource.V2,
        source_candidate_id=SOURCE_CANDIDATE_ID,
        rule_family=UnifiedRuleFamily.RETURN_CODE,
        disposition=PromotionDisposition.READY_FOR_CONTROLLED_REVIEW,
        eligibility=ReviewEligibility.ELIGIBLE,
        program="CALLER10",
        recommended_action=RecommendedAction.SUBMIT_FOR_CONTROLLED_FUNCTIONAL_REVIEW,
        evidence_ids=["evidence::candidate-1"],
        provenance_references=["provenance::candidate-1"],
    )
    review_package = CandidatePromotionReviewPackage(
        run_id=RUN_ID,
        source_package_hash=HASH,
        assessment_artifact_hash=HASH,
        assessment_policy_version="1.0",
        review_items=[review_item],
        summary=CandidatePromotionReviewPackageSummary(
            total_items=1,
            eligible_count=1,
            not_eligible_count=0,
            already_covered_count=0,
            baseline_count=0,
            blocked_count=0,
            counts_by_source={CandidateSource.V2: 1},
            counts_by_family={UnifiedRuleFamily.RETURN_CODE: 1},
            counts_by_disposition={PromotionDisposition.READY_FOR_CONTROLLED_REVIEW: 1},
            counts_by_eligibility={ReviewEligibility.ELIGIBLE: 1},
        ),
    )

    plan_item = CandidatePromotionPlanItem(
        plan_item_id=PLAN_ITEM_ID,
        review_item_id=REVIEW_ITEM_ID,
        assessment_id=ASSESSMENT_ID,
        reference_id=REFERENCE_ID,
        source=CandidateSource.V2,
        source_candidate_id=SOURCE_CANDIDATE_ID,
        rule_family=UnifiedRuleFamily.RETURN_CODE,
        assessment_disposition=PromotionDisposition.READY_FOR_CONTROLLED_REVIEW,
        eligibility=ReviewEligibility.ELIGIBLE,
        action=PromotionPlanAction.PROPOSE_SHADOW_PROMOTION,
        status=PromotionPlanItemStatus.VALID,
        decision_id=DECISION_ID,
        decision=ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION,
    )
    plan = CandidatePromotionPlanArtifact(
        run_id=RUN_ID,
        source_package_hash=HASH,
        assessment_artifact_hash=HASH,
        review_package_hash=HASH,
        decision_manifest_hash=HASH,
        assessment_policy_version="1.0",
        plan_items=[plan_item],
        summary=CandidatePromotionPlanSummary(
            total_items=1,
            keep_baseline_count=0,
            skip_already_covered_count=0,
            propose_shadow_promotion_count=1,
            reject_count=0,
            defer_count=0,
            block_count=0,
            pending_review_count=0,
            invalid_decision_count=0,
            counts_by_source={CandidateSource.V2: 1},
            counts_by_family={UnifiedRuleFamily.RETURN_CODE: 1},
            counts_by_action={PromotionPlanAction.PROPOSE_SHADOW_PROMOTION: 1},
        ),
    )

    member = UnifiedShadowSourceMember(
        member_id=MEMBER_ID,
        source=CandidateSource.V2,
        source_candidate_id=SOURCE_CANDIDATE_ID,
        source_artifact_hash=HASH,
        source_candidate_hash=HASH,
        assessment_id=ASSESSMENT_ID,
        assessment_reference_id=REFERENCE_ID,
        review_item_id=REVIEW_ITEM_ID,
        plan_item_id=PLAN_ITEM_ID,
        review_decision_id=DECISION_ID,
        decision=ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION,
        reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
        reviewer_reference="analyst@example.com",
        rule_family=UnifiedRuleFamily.RETURN_CODE,
        original_support="DETERMINISTIC",
        program="CALLER10",
        paragraph="MAIN",
        target="WS-COD-RETORNO",
        output_literal="R001",
        evidence_ids=["evidence::candidate-1"],
        provenance_references=["provenance::candidate-1"],
    )
    group = UnifiedShadowCandidateGroup(
        unified_shadow_candidate_id=GROUP_ID,
        status=UnifiedShadowGroupStatus.VALID,
        rule_family=UnifiedRuleFamily.RETURN_CODE,
        program="CALLER10",
        target="WS-COD-RETORNO",
        output_literal="R001",
        support=UnifiedShadowSupport.DETERMINISTIC,
        member_ids=[MEMBER_ID],
        comparison_to_v1=UnifiedShadowComparisonKind.NOT_IN_BASELINE,
        evidence_ids=["evidence::candidate-1"],
        provenance_references=["provenance::candidate-1"],
        review_decision_ids=[DECISION_ID],
    )
    unified_shadow = UnifiedCandidatesShadowArtifact(
        run_id=RUN_ID,
        source_package_hash=HASH,
        candidate_v1_artifact_hash=HASH,
        assessment_artifact_hash=HASH,
        review_package_hash=HASH,
        promotion_plan_hash=HASH,
        shadow_members=[member],
        shadow_groups=[group],
        summary=UnifiedCandidatesShadowSummary(
            v1_baseline_count=0,
            proposed_plan_item_count=1,
            shadow_member_count=1,
            shadow_group_count=1,
            excluded_plan_item_count=0,
            exact_baseline_match_group_count=0,
            related_to_baseline_group_count=0,
            not_in_baseline_group_count=1,
            conflicting_with_baseline_group_count=0,
            not_evaluated_group_count=0,
            valid_group_count=1,
            invalid_group_count=0,
            counts_by_source={CandidateSource.V2: 1},
            counts_by_rule_family={UnifiedRuleFamily.RETURN_CODE: 1},
            counts_by_group_status={UnifiedShadowGroupStatus.VALID: 1},
            counts_by_baseline_comparison={UnifiedShadowComparisonKind.NOT_IN_BASELINE: 1},
            counts_by_exclusion_reason={},
        ),
    )

    return GoldenPath(
        v1=v1,
        assessment=assessment,
        review_package=review_package,
        plan=plan,
        unified_shadow=unified_shadow,
    )


def second_member(
    *,
    member_id: str = "member::candidate-2",
    source_candidate_id: str = "v2::V2_RETURN_CODE_PROPAGATION::candidate-2",
    rule_family: UnifiedRuleFamily = UnifiedRuleFamily.RETURN_CODE,
    target: str | None = "WS-COD-RETORNO",
    output_literal: str | None = "R001",
) -> UnifiedShadowSourceMember:
    """Un SEGUNDO `UnifiedShadowSourceMember` valido de forma AISLADA
    (sin encadenar a un assessment/plan real, ya que `validate_group`
    (Fase 12 Parte 6) opera sobre miembros individuales, nunca exige
    que provengan de un `UnifiedCandidatesShadowArtifact` completo) --
    unicamente para aislar los chequeos de GRUPO (familias/targets/
    outputs multiples, `source_candidate_id` duplicado) sin construir
    una segunda cadena Fase 9/10 completa."""
    return UnifiedShadowSourceMember(
        member_id=member_id,
        source=CandidateSource.V2,
        source_candidate_id=source_candidate_id,
        source_artifact_hash=HASH,
        source_candidate_hash=HASH,
        assessment_id="assessment::candidate-2",
        assessment_reference_id="unified::v2::candidate-2",
        review_item_id="review::candidate-2",
        plan_item_id="plan::candidate-2",
        review_decision_id="decision::candidate-2",
        decision=ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION,
        reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
        reviewer_reference="analyst2@example.com",
        rule_family=rule_family,
        original_support="DETERMINISTIC",
        program="CALLER10",
        paragraph="MAIN",
        target=target,
        output_literal=output_literal,
        evidence_ids=["evidence::candidate-2"],
        provenance_references=["provenance::candidate-2"],
    )


def stable_hash(obj: object) -> str:
    return hashlib.sha256(obj.to_stable_json().encode("utf-8")).hexdigest()  # type: ignore[attr-defined]


_UNSET = object()


def analyze_golden_path(
    gp: GoldenPath,
    *,
    v2: V2ShadowCandidatesArtifact | None = None,
    interprocedural: InterproceduralRuleCandidatesArtifact | None = None,
    v1_override: object = _UNSET,
    assessment_override: object = _UNSET,
    review_package_override: object = _UNSET,
    plan_override: object = _UNSET,
    unified_shadow_override: object = _UNSET,
) -> UnifiedShadowValidationReport:
    """Ejecuta el analizador real sobre `gp` (o sustituciones puntuales
    de cada fuente, para aislar un hallazgo especifico sin reconstruir
    todo el escenario). `..._override=None` significa EXPLICITAMENTE
    "esta fuente esta ausente" (distinto de omitir el parametro, que
    reutiliza `gp`) -- usa `HASH` ("a"*64) consistentemente como
    assessment_artifact_hash/review_package_hash/promotion_plan_hash --
    el mismo valor ya horneado dentro de review_package/plan/
    unified_shadow por `golden_path()` -- y el hash REAL (`to_stable_
    json()`) unicamente para `unified_candidates_shadow_hash` (la
    verificacion dedicada de roundtrip, Parte 11)."""
    v1 = gp.v1 if v1_override is _UNSET else v1_override
    assessment = gp.assessment if assessment_override is _UNSET else assessment_override
    review_package = (
        gp.review_package if review_package_override is _UNSET else review_package_override
    )
    plan = gp.plan if plan_override is _UNSET else plan_override
    unified_shadow = (
        gp.unified_shadow if unified_shadow_override is _UNSET else unified_shadow_override
    )

    def _available(artifact: object | None) -> LoadedSource:
        if artifact is None:
            return LoadedSource(artifact=None, availability=SourceAvailability.NOT_AVAILABLE)
        return LoadedSource(artifact=artifact, availability=SourceAvailability.AVAILABLE)

    return analyze_unified_shadow_validation(
        run_id=RUN_ID,
        source_package_hash=HASH,
        v1=_available(v1),
        v2=_available(v2),
        interprocedural=_available(interprocedural),
        assessment=_available(assessment),
        review_package=_available(review_package),
        plan=_available(plan),
        unified_shadow=_available(unified_shadow),
        candidate_v1_artifact_hash=HASH,
        v2_artifact_hash=HASH if v2 is not None else None,
        interprocedural_artifact_hash=HASH if interprocedural is not None else None,
        assessment_artifact_hash=HASH,
        review_package_hash=HASH,
        promotion_plan_hash=HASH,
        unified_candidates_shadow_hash=(
            stable_hash(unified_shadow) if unified_shadow is not None else None
        ),
        source_artifact_hashes=dict(getattr(assessment, "source_artifact_hashes", {})),
    )


def golden_path_with_working_v2_resolution() -> tuple[GoldenPath, V2ShadowCandidatesArtifact]:
    """Variante de `golden_path()` en la que `MEMBER_SOURCE_RESOLUTION`
    PASA genuinamente: construye un `V2ShadowCandidate` cuya identidad
    (program/paragraph/target/resolved_literal/decision_id) coincide
    EXACTAMENTE con la `UnifiedCandidateReference` de `golden_path()`, y
    hornea el hash REAL de ese candidato (`resolve_source_candidate`,
    Fase 11) dentro del `member.source_candidate_hash` -- necesario para
    aislar limpiamente UN solo hallazgo negativo (Fase 12 Parte 16) sin
    el ruido de un `SHADOW_MEMBER_SOURCE_NOT_FOUND` no relacionado."""
    gp = golden_path()
    v2_candidate = V2ShadowCandidate(
        candidate_id=SOURCE_CANDIDATE_ID,
        detector_id="v2-return-code-propagation",
        detector_version="1.0",
        rule_type=V2RuleType.RETURN_CODE_RULE,
        support=V2CandidateSupport.DETERMINISTIC,
        detector_score=1.0,
        program="CALLER10",
        paragraph="MAIN",
        anchor_statement_id="CALLER10::MAIN::0::MOVE",
        target_variable="WS-COD-RETORNO",
        resolved_literal="R001",
        reason="literal propagado dentro de la misma rama del IF",
        semantic_effect_ids=["effect::candidate-1"],
        propagation_fact_ids=["fact::candidate-1"],
        source_references=[V2CandidateSourceReference(program="CALLER10", paragraph="MAIN")],
    )
    real_hash = hashlib.sha256(v2_candidate.to_stable_json().encode("utf-8")).hexdigest()
    member_with_real_hash = gp.unified_shadow.shadow_members[0].model_copy(
        update={"source_candidate_hash": real_hash}
    )
    unified_shadow = gp.unified_shadow.model_copy(
        update={"shadow_members": [member_with_real_hash]}
    )
    gp = GoldenPath(
        v1=gp.v1,
        assessment=gp.assessment,
        review_package=gp.review_package,
        plan=gp.plan,
        unified_shadow=unified_shadow,
    )
    v2_artifact = V2ShadowCandidatesArtifact(
        run_id=RUN_ID,
        source_package_hash=HASH,
        source_artifact_hashes={"artifacts/04-semantic-graph.json": HASH},
        semantic_effects_schema_version="1.2",
        semantic_effects_analyzer_version="1.2",
        semantic_propagation_schema_version="1.1",
        semantic_propagation_analyzer_version="1.1",
        executions=[
            V2DetectorExecution(
                detector_id="v2-return-code-propagation",
                detector_version="1.0",
                rule_type=V2RuleType.RETURN_CODE_RULE,
                candidate_count=1,
                blocked_count=0,
                candidates=[v2_candidate],
            )
        ],
        comparisons=[
            V1V2CandidateComparison(
                comparison_id="comparison::candidate-1",
                status=V1V2ComparisonStatus.V2_ONLY,
                v2_candidate_ids=[SOURCE_CANDIDATE_ID],
                program="CALLER10",
                paragraph="MAIN",
                reason="V1 nunca ve WS-COD-RETORNO en este escenario sintetico",
            )
        ],
        summary=V2ShadowSummary(
            detector_count=1,
            v1_candidate_count=0,
            v2_candidate_count=1,
            deterministic_count=1,
            partial_count=0,
            blocked_count=0,
            matched_count=0,
            v1_only_count=0,
            v2_only_count=1,
            related_not_equivalent_count=0,
            counts_by_rule_type={V2RuleType.RETURN_CODE_RULE: 1},
            counts_by_detector={"v2-return-code-propagation": 1},
        ),
    )
    return gp, v2_artifact
