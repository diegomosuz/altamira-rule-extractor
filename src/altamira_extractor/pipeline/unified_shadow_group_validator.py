"""Validador PURO de members y groups del artefacto unificado (Fase 12
Parte 6, `feat/unified-shadow-differential-validation`).

Para CADA `UnifiedShadowSourceMember`: re-resuelve su candidato fuente
REAL contra las fuentes ACTUALES reutilizando el resolutor de Fase 11
(`unified_shadow_source_resolver.py::resolve_source_candidate`, NUNCA
reimplementado ni modificado -- deteccion de deriva: el candidato pudo
existir cuando Fase 11 genero el artefacto pero ya no coincidir),
verifica que su `plan_item_id` siga apuntando a un
`CandidatePromotionPlanItem` real con `action=PROPOSE_SHADOW_PROMOTION`/
`status=VALID`, y que tenga evidence/provenance no vacios.

Para CADA `UnifiedShadowCandidateGroup`: revalida (nunca RECALCULA)
que sus miembros existan, compartan family/target/output/program
unicos, que las uniones de evidence/provenance/`review_decision_ids`
coincidan exactamente con sus miembros, y que ningun `source_candidate_id`
se repita dentro del grupo. Nunca modifica el grupo, nunca elige un
candidato "ganador" -- todos los miembros se validan con igual
jerarquia."""

from __future__ import annotations

from dataclasses import dataclass

from ..contracts.candidate import CandidateArtifact
from ..contracts.candidate_promotion_assessment import (
    CandidatePromotionAssessmentArtifact,
    UnifiedRuleFamily,
)
from ..contracts.candidate_promotion_plan import (
    CandidatePromotionPlanArtifact,
    CandidatePromotionPlanItem,
    PromotionPlanAction,
    PromotionPlanItemStatus,
)
from ..contracts.interprocedural_rule_candidates import InterproceduralRuleCandidatesArtifact
from ..contracts.unified_candidates_shadow import (
    UnifiedShadowCandidateGroup,
    UnifiedShadowSourceMember,
    UnifiedShadowSupport,
)
from ..contracts.unified_shadow_validation import UnifiedShadowValidationGate
from ..contracts.unified_shadow_validation import UnifiedShadowValidationIssueCode as Code
from ..contracts.v2_shadow_candidates import V2ShadowCandidatesArtifact
from .unified_shadow_source_resolver import (
    SourceResolutionFailureReason,
    resolve_source_candidate,
)
from .unified_shadow_validation_policy import RawFinding

_MEMBER_GATE = UnifiedShadowValidationGate.MEMBER_SOURCE_RESOLUTION
_GROUP_GATE = UnifiedShadowValidationGate.GROUP_INTERNAL_CONSISTENCY
_PLAN_GATE = UnifiedShadowValidationGate.PLAN_BINDING_INTEGRITY


@dataclass(frozen=True)
class MemberValidationResult:
    member_id: str
    findings: tuple[RawFinding, ...]
    source_resolution_complete: bool


@dataclass(frozen=True)
class GroupValidationResult:
    group_id: str
    findings: tuple[RawFinding, ...]
    member_results: tuple[MemberValidationResult, ...]
    structurally_valid: bool


def _validate_member(
    member: UnifiedShadowSourceMember,
    *,
    assessment: CandidatePromotionAssessmentArtifact,
    plan_item_by_id: dict[str, CandidatePromotionPlanItem],
    v1_candidates: CandidateArtifact,
    v2_candidates: V2ShadowCandidatesArtifact | None,
    interprocedural_candidates: InterproceduralRuleCandidatesArtifact | None,
) -> MemberValidationResult:
    findings: list[RawFinding] = []
    reference_by_id = {ref.unified_reference_id: ref for ref in assessment.candidate_references}
    reference = reference_by_id.get(member.assessment_reference_id)

    source_resolution_complete = False
    if reference is None or reference.source != member.source:
        findings.append(
            RawFinding(
                code=Code.SHADOW_MEMBER_SOURCE_NOT_FOUND,
                gate=_MEMBER_GATE,
                shadow_member_ids=(member.member_id,),
                source_candidate_ids=(member.source_candidate_id,),
            )
        )
    else:
        resolution = resolve_source_candidate(
            reference=reference,
            v1_artifact=v1_candidates,
            v2_artifact=v2_candidates,
            interprocedural_artifact=interprocedural_candidates,
        )
        if not resolution.is_success:
            code = (
                Code.SHADOW_MEMBER_IDENTITY_MISMATCH
                if resolution.failure_reason == SourceResolutionFailureReason.IDENTITY_MISMATCH
                else Code.SHADOW_MEMBER_SOURCE_NOT_FOUND
            )
            findings.append(
                RawFinding(
                    code=code,
                    gate=_MEMBER_GATE,
                    shadow_member_ids=(member.member_id,),
                    source_candidate_ids=(member.source_candidate_id,),
                )
            )
        elif resolution.source_candidate_hash != member.source_candidate_hash:
            findings.append(
                RawFinding(
                    code=Code.SHADOW_MEMBER_IDENTITY_MISMATCH,
                    gate=_MEMBER_GATE,
                    shadow_member_ids=(member.member_id,),
                    source_candidate_ids=(member.source_candidate_id,),
                    diagnostics=("source_candidate_hash",),
                )
            )
        else:
            source_resolution_complete = True

    plan_item = plan_item_by_id.get(member.plan_item_id)
    if (
        plan_item is None
        or plan_item.action != PromotionPlanAction.PROPOSE_SHADOW_PROMOTION
        or plan_item.status != PromotionPlanItemStatus.VALID
        or plan_item.decision_id != member.review_decision_id
    ):
        findings.append(
            RawFinding(
                code=Code.SHADOW_MEMBER_WITHOUT_APPROVAL,
                gate=_PLAN_GATE,
                shadow_member_ids=(member.member_id,),
                plan_item_ids=(member.plan_item_id,),
            )
        )

    if not member.evidence_ids:
        findings.append(
            RawFinding(
                code=Code.SHADOW_MEMBER_WITHOUT_EVIDENCE,
                gate=UnifiedShadowValidationGate.EVIDENCE_COMPLETENESS,
                shadow_member_ids=(member.member_id,),
            )
        )
    if not member.provenance_references:
        findings.append(
            RawFinding(
                code=Code.SHADOW_MEMBER_WITHOUT_PROVENANCE,
                gate=UnifiedShadowValidationGate.PROVENANCE_COMPLETENESS,
                shadow_member_ids=(member.member_id,),
            )
        )

    return MemberValidationResult(
        member_id=member.member_id,
        findings=tuple(findings),
        source_resolution_complete=source_resolution_complete,
    )


def validate_group(
    group: UnifiedShadowCandidateGroup,
    *,
    members_by_id: dict[str, UnifiedShadowSourceMember],
    assessment: CandidatePromotionAssessmentArtifact,
    plan: CandidatePromotionPlanArtifact,
    v1_candidates: CandidateArtifact,
    v2_candidates: V2ShadowCandidatesArtifact | None,
    interprocedural_candidates: InterproceduralRuleCandidatesArtifact | None,
) -> GroupValidationResult:
    """Punto de entrada puro. Nunca muta `group` ni sus miembros, nunca
    recalcula la agrupacion, nunca elige un candidato "ganador"."""
    findings: list[RawFinding] = []
    plan_item_by_id = {item.plan_item_id: item for item in plan.plan_items}

    if not group.member_ids:
        findings.append(
            RawFinding(
                code=Code.GROUP_WITHOUT_MEMBERS,
                gate=_GROUP_GATE,
                shadow_group_ids=(group.unified_shadow_candidate_id,),
            )
        )
        return GroupValidationResult(
            group_id=group.unified_shadow_candidate_id,
            findings=tuple(findings),
            member_results=(),
            structurally_valid=False,
        )

    members: list[UnifiedShadowSourceMember] = []
    for member_id in group.member_ids:
        member = members_by_id.get(member_id)
        if member is None:
            findings.append(
                RawFinding(
                    code=Code.GROUP_MEMBER_NOT_FOUND,
                    gate=_GROUP_GATE,
                    shadow_group_ids=(group.unified_shadow_candidate_id,),
                    shadow_member_ids=(member_id,),
                )
            )
        else:
            members.append(member)

    member_results = tuple(
        _validate_member(
            member,
            assessment=assessment,
            plan_item_by_id=plan_item_by_id,
            v1_candidates=v1_candidates,
            v2_candidates=v2_candidates,
            interprocedural_candidates=interprocedural_candidates,
        )
        for member in members
    )
    for result in member_results:
        findings.extend(result.findings)

    if members:
        families = {member.rule_family for member in members}
        if len(families) > 1:
            findings.append(
                RawFinding(
                    code=Code.GROUP_MULTIPLE_FAMILIES,
                    gate=_GROUP_GATE,
                    shadow_group_ids=(group.unified_shadow_candidate_id,),
                )
            )
        targets = {member.target for member in members}
        if len(targets) > 1:
            findings.append(
                RawFinding(
                    code=Code.GROUP_MULTIPLE_TARGETS,
                    gate=_GROUP_GATE,
                    shadow_group_ids=(group.unified_shadow_candidate_id,),
                )
            )
        outputs = {member.output_literal for member in members}
        if len(outputs) > 1:
            findings.append(
                RawFinding(
                    code=Code.GROUP_MULTIPLE_OUTPUTS,
                    gate=_GROUP_GATE,
                    shadow_group_ids=(group.unified_shadow_candidate_id,),
                )
            )
        programs = {member.program for member in members}
        if len(programs) > 1:
            findings.append(
                RawFinding(
                    code=Code.GROUP_INCONSISTENT_SCOPE,
                    gate=_GROUP_GATE,
                    shadow_group_ids=(group.unified_shadow_candidate_id,),
                )
            )

        source_candidate_keys = [(member.source, member.source_candidate_id) for member in members]
        if len(source_candidate_keys) != len(set(source_candidate_keys)):
            findings.append(
                RawFinding(
                    code=Code.GROUP_INCONSISTENT_SCOPE,
                    gate=_GROUP_GATE,
                    shadow_group_ids=(group.unified_shadow_candidate_id,),
                    diagnostics=("duplicate_source_candidate_id",),
                )
            )

        expected_evidence = sorted({e for member in members for e in member.evidence_ids})
        if group.evidence_ids != expected_evidence:
            findings.append(
                RawFinding(
                    code=Code.GROUP_INCONSISTENT_SCOPE,
                    gate=UnifiedShadowValidationGate.EVIDENCE_COMPLETENESS,
                    shadow_group_ids=(group.unified_shadow_candidate_id,),
                    diagnostics=("evidence_union_mismatch",),
                )
            )
        expected_provenance = sorted(
            {p for member in members for p in member.provenance_references}
        )
        if group.provenance_references != expected_provenance:
            findings.append(
                RawFinding(
                    code=Code.GROUP_INCONSISTENT_SCOPE,
                    gate=UnifiedShadowValidationGate.PROVENANCE_COMPLETENESS,
                    shadow_group_ids=(group.unified_shadow_candidate_id,),
                    diagnostics=("provenance_union_mismatch",),
                )
            )
        expected_review_decisions = sorted({member.review_decision_id for member in members})
        if group.review_decision_ids != expected_review_decisions:
            findings.append(
                RawFinding(
                    code=Code.GROUP_INCONSISTENT_SCOPE,
                    gate=UnifiedShadowValidationGate.DECISION_TRACEABILITY,
                    shadow_group_ids=(group.unified_shadow_candidate_id,),
                    diagnostics=("review_decision_ids_mismatch",),
                )
            )

    if group.support == UnifiedShadowSupport.BLOCKED:
        findings.append(
            RawFinding(
                code=Code.GROUP_BLOCKED,
                gate=_GROUP_GATE,
                shadow_group_ids=(group.unified_shadow_candidate_id,),
            )
        )

    if group.rule_family == UnifiedRuleFamily.UNKNOWN:
        findings.append(
            RawFinding(
                code=Code.GROUP_UNKNOWN_FAMILY,
                gate=_GROUP_GATE,
                shadow_group_ids=(group.unified_shadow_candidate_id,),
            )
        )

    structurally_valid = not findings
    return GroupValidationResult(
        group_id=group.unified_shadow_candidate_id,
        findings=tuple(findings),
        member_results=member_results,
        structurally_valid=structurally_valid,
    )
