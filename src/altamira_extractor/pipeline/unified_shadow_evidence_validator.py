"""Validador PURO de evidence, provenance y trazabilidad de decision
(Fase 12 Parte 8, `feat/unified-shadow-differential-validation`).

Distingue DOS dimensiones, nunca fusionadas:

- EVIDENCE ESTRUCTURAL (`evidence_ids`): senales tecnicas concretas
  (`effect::...`/`fact::...`/`evidence::...`) que sustentan la
  deteccion del candidato -- nunca una afirmacion de correccion
  funcional (ver `UnifiedShadowValidationIssueCode.
  FUNCTIONAL_VALIDATION_REQUIRED`).
- PROVENANCE DE GOBIERNO (`provenance_references`): la cadena
  administrativa completa hasta el candidato fuente y la decision
  humana -- de que statement/programa proviene, nunca si la regla es
  correcta.

Para CADA `UnifiedShadowSourceMember` verifica la cadena de
trazabilidad COMPLETA: `assessment_id` existe en el assessment real,
`review_item_id` existe en el review package real (y sus propios
`assessment_id`/`reference_id` coinciden), `plan_item_id` existe en el
plan real -- produciendo, por grupo, tres banderas agregadas
(`evidence_complete`/`provenance_complete`/`decision_trace_complete`)
que alimentan tanto el resumen como la elegibilidad downstream (Fase
12 Parte 9). Nunca interpreta la presencia de evidence/provenance como
validacion funcional."""

from __future__ import annotations

from dataclasses import dataclass

from ..contracts.candidate_promotion_assessment import CandidatePromotionAssessmentArtifact
from ..contracts.candidate_promotion_plan import CandidatePromotionPlanArtifact
from ..contracts.candidate_promotion_review import CandidatePromotionReviewPackage
from ..contracts.unified_candidates_shadow import (
    UnifiedShadowCandidateGroup,
    UnifiedShadowSourceMember,
)
from ..contracts.unified_shadow_validation import UnifiedShadowValidationGate
from ..contracts.unified_shadow_validation import UnifiedShadowValidationIssueCode as Code
from .unified_shadow_validation_policy import RawFinding

_EVIDENCE_GATE = UnifiedShadowValidationGate.EVIDENCE_COMPLETENESS
_PROVENANCE_GATE = UnifiedShadowValidationGate.PROVENANCE_COMPLETENESS
_DECISION_GATE = UnifiedShadowValidationGate.DECISION_TRACEABILITY


@dataclass(frozen=True)
class MemberTraceabilityResult:
    member_id: str
    evidence_complete: bool
    provenance_complete: bool
    decision_trace_complete: bool
    findings: tuple[RawFinding, ...]


@dataclass(frozen=True)
class GroupTraceabilityResult:
    group_id: str
    evidence_complete: bool
    provenance_complete: bool
    decision_trace_complete: bool
    findings: tuple[RawFinding, ...]


def _validate_member_traceability(
    member: UnifiedShadowSourceMember,
    *,
    assessment: CandidatePromotionAssessmentArtifact,
    review_package: CandidatePromotionReviewPackage,
    plan: CandidatePromotionPlanArtifact,
) -> MemberTraceabilityResult:
    findings: list[RawFinding] = []

    evidence_complete = bool(member.evidence_ids)
    if not evidence_complete:
        findings.append(
            RawFinding(
                code=Code.SHADOW_MEMBER_WITHOUT_EVIDENCE,
                gate=_EVIDENCE_GATE,
                shadow_member_ids=(member.member_id,),
            )
        )

    provenance_complete = bool(member.provenance_references)
    if not provenance_complete:
        findings.append(
            RawFinding(
                code=Code.SHADOW_MEMBER_WITHOUT_PROVENANCE,
                gate=_PROVENANCE_GATE,
                shadow_member_ids=(member.member_id,),
            )
        )

    assessment_ids = {a.assessment_id for a in assessment.assessments}
    review_item_by_id = {item.review_item_id: item for item in review_package.review_items}
    plan_item_ids = {item.plan_item_id for item in plan.plan_items}

    decision_trace_complete = True
    if member.assessment_id not in assessment_ids:
        decision_trace_complete = False
    review_item = review_item_by_id.get(member.review_item_id)
    if (
        review_item is None
        or review_item.assessment_id != member.assessment_id
        or review_item.reference_id != member.assessment_reference_id
    ):
        decision_trace_complete = False
    if member.plan_item_id not in plan_item_ids:
        decision_trace_complete = False
    if not member.review_decision_id:
        decision_trace_complete = False

    if not decision_trace_complete:
        findings.append(
            RawFinding(
                code=Code.PLAN_BINDING_MISMATCH,
                gate=_DECISION_GATE,
                shadow_member_ids=(member.member_id,),
                plan_item_ids=(member.plan_item_id,),
            )
        )

    return MemberTraceabilityResult(
        member_id=member.member_id,
        evidence_complete=evidence_complete,
        provenance_complete=provenance_complete,
        decision_trace_complete=decision_trace_complete,
        findings=tuple(findings),
    )


def validate_group_traceability(
    group: UnifiedShadowCandidateGroup,
    *,
    members: list[UnifiedShadowSourceMember],
    assessment: CandidatePromotionAssessmentArtifact,
    review_package: CandidatePromotionReviewPackage,
    plan: CandidatePromotionPlanArtifact,
) -> GroupTraceabilityResult:
    """Punto de entrada puro. Nunca muta `group` ni sus miembros. La
    union de evidence/provenance del grupo SIN perdida ni duplicados
    ya se revalida en `unified_shadow_group_validator.py` (Parte 6) --
    este modulo agrega, por grupo, las TRES banderas de completitud
    (evidence/provenance/decision trace) consumidas por la politica de
    elegibilidad (Parte 9)."""
    member_results = [
        _validate_member_traceability(
            member, assessment=assessment, review_package=review_package, plan=plan
        )
        for member in members
    ]
    findings: list[RawFinding] = [
        finding for result in member_results for finding in result.findings
    ]

    evidence_complete = bool(member_results) and all(
        result.evidence_complete for result in member_results
    )
    provenance_complete = bool(member_results) and all(
        result.provenance_complete for result in member_results
    )
    decision_trace_complete = bool(member_results) and all(
        result.decision_trace_complete for result in member_results
    )

    return GroupTraceabilityResult(
        group_id=group.unified_shadow_candidate_id,
        evidence_complete=evidence_complete,
        provenance_complete=provenance_complete,
        decision_trace_complete=decision_trace_complete,
        findings=tuple(findings),
    )
