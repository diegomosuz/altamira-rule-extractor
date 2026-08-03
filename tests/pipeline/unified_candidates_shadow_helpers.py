"""Fixtures compartidas del artefacto unificado de candidatos en shadow
mode (Fase 11 de la ampliacion semantica,
`feat/unified-candidate-artifact-shadow`). NO es un modulo de test (sin
prefijo `test_`): expone builders de V1/V2/interprocedural, referencias
unificadas de Fase 9, assessment/review package/plan de un escenario
completo con DOS propuestas aprobadas y equivalentes (una V2, una
INTERPROCEDURAL) -- reutilizado por los tests de resolver/adaptador/
agrupador/comparador/analizador/servicio/CLI."""

from __future__ import annotations

import hashlib

from altamira_extractor.contracts.candidate import CandidateArtifact, RuleCandidate
from altamira_extractor.contracts.candidate_promotion_assessment import (
    CandidatePromotionAssessment,
    CandidatePromotionAssessmentArtifact,
    CandidatePromotionAssessmentSummary,
    CandidateRelation,
    CandidateRelationKind,
    CandidateSource,
    PromotionCriterionKind,
    PromotionCriterionResult,
    PromotionCriterionStatus,
    PromotionDisposition,
    SourceAvailability,
    UnifiedCandidateReference,
    UnifiedRuleFamily,
    recommended_action_for,
)
from altamira_extractor.contracts.candidate_promotion_plan import CandidatePromotionPlanArtifact
from altamira_extractor.contracts.candidate_promotion_review import (
    CandidatePromotionDecision,
    CandidatePromotionDecisionManifest,
    CandidatePromotionReviewPackage,
    DecisionReasonCode,
    ReviewDecision,
)
from altamira_extractor.contracts.enums import CandidateStatus
from altamira_extractor.contracts.interprocedural_rule_candidates import (
    InterproceduralRuleCandidate,
    InterproceduralRuleCandidatesArtifact,
)
from altamira_extractor.contracts.v2_shadow_candidates import (
    V2ShadowCandidate,
    V2ShadowCandidatesArtifact,
)
from altamira_extractor.pipeline.candidate_promotion_plan_builder import (
    build_candidate_promotion_plan,
)
from altamira_extractor.pipeline.candidate_promotion_review_generator import (
    generate_candidate_promotion_review_package,
    review_item_id_for,
)

from .candidate_promotion_assessment_helpers import (
    HASH as CAND_HASH,
)
from .candidate_promotion_assessment_helpers import (
    interprocedural_artifact,
    interprocedural_candidate,
    program_paragraph_id,
    v1_artifact,
    v1_candidate,
    v2_artifact,
    v2_candidate,
)

RUN_ID = "run1"


def stable_hash(obj: object) -> str:
    return hashlib.sha256(obj.to_stable_json().encode("utf-8")).hexdigest()  # type: ignore[attr-defined]


def dump_hash(obj: object) -> str:
    return hashlib.sha256(obj.model_dump_json().encode("utf-8")).hexdigest()  # type: ignore[attr-defined]


def v1_reference(
    *,
    reference_id: str = "unified::v1::a",
    source_candidate_id: str = "candidate::1",
    program: str = "CALLER",
    paragraph: str = "MAIN",
    target: str | None = None,
    output_literal: str | None = "R001",
    decision_id: str | None = None,
) -> UnifiedCandidateReference:
    return UnifiedCandidateReference(
        unified_reference_id=reference_id,
        source=CandidateSource.V1,
        source_candidate_id=source_candidate_id,
        source_artifact_hash=CAND_HASH,
        rule_family=UnifiedRuleFamily.RETURN_CODE,
        original_support="DETERMINISTIC",
        program=program,
        paragraph=paragraph,
        decision_id=decision_id,
        target=target,
        output_literal=output_literal,
        evidence_ids=[],
    )


def v2_reference(
    *,
    reference_id: str = "unified::v2::a",
    source_candidate_id: str,
    program: str = "CALLER",
    paragraph: str = "MAIN",
    target: str | None = "WS-X",
    output_literal: str | None = "R001",
    decision_id: str | None = None,
) -> UnifiedCandidateReference:
    return UnifiedCandidateReference(
        unified_reference_id=reference_id,
        source=CandidateSource.V2,
        source_candidate_id=source_candidate_id,
        source_artifact_hash=CAND_HASH,
        rule_family=UnifiedRuleFamily.RETURN_CODE,
        original_support="DETERMINISTIC",
        program=program,
        paragraph=paragraph,
        decision_id=decision_id,
        target=target,
        output_literal=output_literal,
        evidence_ids=["evidence::1"],
    )


def interprocedural_reference(
    *,
    reference_id: str = "unified::interprocedural::a",
    source_candidate_id: str,
    program: str = "CALLER",
    paragraph: str = "MAIN",
    call_site_id: str | None = "callsite::x",
    target: str | None = "WS-X",
    input_literal: str | None = None,
    output_literal: str | None = "R001",
) -> UnifiedCandidateReference:
    return UnifiedCandidateReference(
        unified_reference_id=reference_id,
        source=CandidateSource.INTERPROCEDURAL,
        source_candidate_id=source_candidate_id,
        source_artifact_hash=CAND_HASH,
        rule_family=UnifiedRuleFamily.RETURN_CODE,
        original_support="DETERMINISTIC",
        program=program,
        paragraph=paragraph,
        call_site_id=call_site_id,
        target=target,
        input_literal=input_literal,
        output_literal=output_literal,
        evidence_ids=["evidence::1"],
    )


def _criteria_for_disposition(disposition: PromotionDisposition) -> list[PromotionCriterionResult]:
    """Mismo mapeo que `candidate_promotion_review_helpers.py::
    assessment_for`: cada disposicion exige al menos un criterio en el
    estado que la propia disposicion presupone (invariantes del
    contrato real de Fase 9)."""
    overrides: dict[PromotionCriterionKind, PromotionCriterionStatus] = {}
    if disposition == PromotionDisposition.BASELINE_V1:
        return [
            PromotionCriterionResult(
                criterion=c, status=PromotionCriterionStatus.NOT_APPLICABLE, reason="V1 es baseline"
            )
            for c in PromotionCriterionKind
        ]
    if disposition == PromotionDisposition.REVIEW_REQUIRED:
        overrides = {
            PromotionCriterionKind.INDEPENDENT_CORROBORATION: PromotionCriterionStatus.FAIL
        }
    elif disposition == PromotionDisposition.BLOCKED:
        overrides = {PromotionCriterionKind.NO_BARRIERS: PromotionCriterionStatus.FAIL}
    elif disposition == PromotionDisposition.NOT_EVALUATED:
        overrides = {
            PromotionCriterionKind.V1_COMPARISON_AVAILABLE: PromotionCriterionStatus.NOT_EVALUATED
        }
    return [
        PromotionCriterionResult(
            criterion=c, status=overrides.get(c, PromotionCriterionStatus.PASS), reason="ok"
        )
        for c in PromotionCriterionKind
    ]


def assessment_of(
    references: list[UnifiedCandidateReference],
    *,
    dispositions: dict[str, PromotionDisposition],
    exact_match_pairs: list[tuple[str, str]] | None = None,
    conflict_pairs: list[tuple[str, str]] | None = None,
    run_id: str = RUN_ID,
    v1_hash: str | None = None,
    v2_hash: str | None = None,
    interprocedural_hash: str | None = None,
) -> CandidatePromotionAssessmentArtifact:
    """Construye un `CandidatePromotionAssessmentArtifact` MINIMO a mano
    (sin invocar el analizador real de Fase 9) para tests de Fase 11 que
    necesitan control total sobre relations/dispositions -- mismo patron
    que `candidate_promotion_review_helpers.py::single_disposition_artifact`."""
    references = sorted(references, key=lambda r: r.unified_reference_id)

    relations = []
    for left_id, right_id in exact_match_pairs or []:
        left, right = sorted([left_id, right_id])
        relations.append(
            CandidateRelation(
                relation_id=f"relation::exact::{left}::{right}",
                left_reference_id=left,
                right_reference_id=right,
                relation_kind=CandidateRelationKind.EXACT_MATCH,
                reason="fixture Fase 11: EXACT_MATCH",
            )
        )
    for left_id, right_id in conflict_pairs or []:
        left, right = sorted([left_id, right_id])
        relations.append(
            CandidateRelation(
                relation_id=f"relation::conflict::{left}::{right}",
                left_reference_id=left,
                right_reference_id=right,
                relation_kind=CandidateRelationKind.CONFLICT,
                reason="fixture Fase 11: CONFLICT",
            )
        )

    exact_by_ref: dict[str, list[str]] = {}
    for relation in relations:
        if relation.relation_kind == CandidateRelationKind.EXACT_MATCH:
            exact_by_ref.setdefault(relation.left_reference_id, []).append(
                relation.right_reference_id
            )
            exact_by_ref.setdefault(relation.right_reference_id, []).append(
                relation.left_reference_id
            )

    assessments = [
        CandidatePromotionAssessment(
            assessment_id=f"assessment::{ref.unified_reference_id}",
            reference_id=ref.unified_reference_id,
            disposition=dispositions[ref.unified_reference_id],
            criteria=_criteria_for_disposition(dispositions[ref.unified_reference_id]),
            exact_match_reference_ids=sorted(exact_by_ref.get(ref.unified_reference_id, [])),
            conflict_ids=[],
            recommended_action=recommended_action_for(dispositions[ref.unified_reference_id]),
        )
        for ref in references
    ]

    counts_by_source: dict[CandidateSource, int] = {}
    counts_by_family: dict[UnifiedRuleFamily, int] = {}
    counts_by_disposition: dict[PromotionDisposition, int] = {}
    for ref, a in zip(references, assessments, strict=True):
        counts_by_source[ref.source] = counts_by_source.get(ref.source, 0) + 1
        counts_by_family[ref.rule_family] = counts_by_family.get(ref.rule_family, 0) + 1
        counts_by_disposition[a.disposition] = counts_by_disposition.get(a.disposition, 0) + 1

    exact_match_count = len(
        [r for r in relations if r.relation_kind == CandidateRelationKind.EXACT_MATCH]
    )

    summary = CandidatePromotionAssessmentSummary(
        v1_candidate_count=counts_by_source.get(CandidateSource.V1, 0),
        v2_candidate_count=counts_by_source.get(CandidateSource.V2, 0),
        interprocedural_candidate_count=counts_by_source.get(CandidateSource.INTERPROCEDURAL, 0),
        unified_reference_count=len(references),
        exact_match_relation_count=exact_match_count,
        related_relation_count=0,
        conflict_count=0,
        baseline_v1_count=counts_by_disposition.get(PromotionDisposition.BASELINE_V1, 0),
        already_covered_count=counts_by_disposition.get(PromotionDisposition.ALREADY_COVERED, 0),
        ready_for_controlled_review_count=counts_by_disposition.get(
            PromotionDisposition.READY_FOR_CONTROLLED_REVIEW, 0
        ),
        review_required_count=counts_by_disposition.get(PromotionDisposition.REVIEW_REQUIRED, 0),
        blocked_count=counts_by_disposition.get(PromotionDisposition.BLOCKED, 0),
        conflicting_count=counts_by_disposition.get(PromotionDisposition.CONFLICTING, 0),
        not_evaluated_count=counts_by_disposition.get(PromotionDisposition.NOT_EVALUATED, 0),
        counts_by_source=counts_by_source,
        counts_by_rule_family=counts_by_family,
        counts_by_disposition=counts_by_disposition,
        source_availability={
            CandidateSource.V1: SourceAvailability.AVAILABLE,
            CandidateSource.V2: SourceAvailability.AVAILABLE,
            CandidateSource.INTERPROCEDURAL: SourceAvailability.AVAILABLE,
        },
    )

    source_artifact_hashes: dict[str, str] = {}
    if v1_hash is not None:
        source_artifact_hashes["artifacts/06-candidates.json"] = v1_hash
    if v2_hash is not None:
        source_artifact_hashes["v2-candidates-shadow(in-memory)"] = v2_hash
    if interprocedural_hash is not None:
        source_artifact_hashes["interprocedural-rule-candidates-shadow(in-memory)"] = (
            interprocedural_hash
        )

    return CandidatePromotionAssessmentArtifact(
        run_id=run_id,
        source_package_hash=CAND_HASH,
        source_artifact_hashes=source_artifact_hashes,
        source_availability={
            CandidateSource.V1: SourceAvailability.AVAILABLE,
            CandidateSource.V2: SourceAvailability.AVAILABLE,
            CandidateSource.INTERPROCEDURAL: SourceAvailability.AVAILABLE,
        },
        summary=summary,
        candidate_references=references,
        relations=relations,
        conflicts=[],
        assessments=sorted(assessments, key=lambda a: a.assessment_id),
    )


def manifest_approving(
    assessment: CandidatePromotionAssessmentArtifact,
    review_package: CandidatePromotionReviewPackage,
    *,
    reference_ids: list[str],
    run_id: str = RUN_ID,
) -> CandidatePromotionDecisionManifest:
    assessment_hash = stable_hash(assessment)
    review_package_hash = stable_hash(review_package)
    decisions = [
        CandidatePromotionDecision(
            decision_id=f"decision::{reference_id}",
            review_item_id=review_item_id_for(reference_id),
            assessment_id=f"assessment::{reference_id}",
            reference_id=reference_id,
            assessment_artifact_hash=assessment_hash,
            decision=ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION,
            reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
            reviewer_reference="analyst@example.com",
        )
        for reference_id in reference_ids
    ]
    return CandidatePromotionDecisionManifest(
        review_package_hash=review_package_hash,
        assessment_artifact_hash=assessment_hash,
        run_id=run_id,
        decisions=decisions,
    )


class TwoEquivalentProposalsScenario:
    """Escenario canonico de Fase 11: V1 vacio, un candidato V2 y un
    candidato INTERPROCEDURAL EXACT_MATCH entre si, ambos
    READY_FOR_CONTROLLED_REVIEW y aprobados -- el escenario base de
    Parte 14 (dos propuestas equivalentes que deben agruparse en un
    unico `UnifiedShadowCandidateGroup`)."""

    def __init__(self) -> None:
        self.v1 = v1_artifact(candidates=[], run_id=RUN_ID)
        self.v2_cand = v2_candidate(
            candidate_id="v2::V2_RETURN_CODE_PROPAGATION::" + "1" * 24,
            program="CALLER",
            paragraph="MAIN",
            target_variable="WS-X",
            resolved_literal="R001",
        )
        self.v2 = v2_artifact(candidates=[self.v2_cand], run_id=RUN_ID)
        self.ip_cand = interprocedural_candidate(
            candidate_id="ipr::interprocedural-return-code-rule::1",
            caller_program="CALLER",
            callee_program="CALLEE",
            target="WS-X",
            output_literal="R001",
        )
        self.ip = interprocedural_artifact(candidates=[self.ip_cand], run_id=RUN_ID)

        self.v2_ref = v2_reference(
            reference_id="unified::v2::a", source_candidate_id=self.v2_cand.candidate_id
        )
        self.ip_ref = interprocedural_reference(
            reference_id="unified::interprocedural::a",
            source_candidate_id=self.ip_cand.candidate_id,
        )

        self.v1_hash = stable_hash(self.v1)
        self.v2_hash = dump_hash(self.v2)
        self.ip_hash = dump_hash(self.ip)

        self.assessment = assessment_of(
            [self.v2_ref, self.ip_ref],
            dispositions={
                self.v2_ref.unified_reference_id: PromotionDisposition.READY_FOR_CONTROLLED_REVIEW,
                self.ip_ref.unified_reference_id: PromotionDisposition.READY_FOR_CONTROLLED_REVIEW,
            },
            exact_match_pairs=[
                (self.v2_ref.unified_reference_id, self.ip_ref.unified_reference_id)
            ],
            v1_hash=self.v1_hash,
            v2_hash=self.v2_hash,
            interprocedural_hash=self.ip_hash,
        )
        self.review_package = generate_candidate_promotion_review_package(self.assessment)
        self.manifest = manifest_approving(
            self.assessment,
            self.review_package,
            reference_ids=[self.v2_ref.unified_reference_id, self.ip_ref.unified_reference_id],
        )
        self.plan = build_candidate_promotion_plan(
            assessment=self.assessment, review_package=self.review_package, manifest=self.manifest
        )

    def analyzer_kwargs(self) -> dict[str, object]:
        return {
            "run_id": RUN_ID,
            "v1_candidates": self.v1,
            "v2_candidates": self.v2,
            "interprocedural_candidates": self.ip,
            "assessment": self.assessment,
            "review_package": self.review_package,
            "plan": self.plan,
            "source_package_hash": CAND_HASH,
            "candidate_v1_artifact_hash": self.v1_hash,
            "v2_artifact_hash": self.v2_hash,
            "interprocedural_artifact_hash": self.ip_hash,
            "assessment_artifact_hash": stable_hash(self.assessment),
            "review_package_hash": stable_hash(self.review_package),
            "promotion_plan_hash": stable_hash(self.plan),
            "source_artifact_hashes": self.assessment.source_artifact_hashes,
        }


__all__ = [
    "CAND_HASH",
    "RUN_ID",
    "CandidateArtifact",
    "CandidateStatus",
    "InterproceduralRuleCandidate",
    "InterproceduralRuleCandidatesArtifact",
    "RuleCandidate",
    "TwoEquivalentProposalsScenario",
    "V2ShadowCandidate",
    "V2ShadowCandidatesArtifact",
    "CandidatePromotionPlanArtifact",
    "assessment_of",
    "dump_hash",
    "interprocedural_artifact",
    "interprocedural_candidate",
    "interprocedural_reference",
    "manifest_approving",
    "program_paragraph_id",
    "stable_hash",
    "v1_artifact",
    "v1_candidate",
    "v1_reference",
    "v2_artifact",
    "v2_candidate",
    "v2_reference",
]
