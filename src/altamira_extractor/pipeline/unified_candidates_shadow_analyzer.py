"""Analizador PURO principal del artefacto unificado de candidatos en
shadow mode (Fase 11 de la ampliacion semantica,
`feat/unified-candidate-artifact-shadow`).

Orquesta, en orden: (1) validar el binding global de hashes entre
assessment/review package/plan/V1/V2/interprocedural (nunca procesa
parcialmente un plan desactualizado); (2) adaptar el baseline V1
(`unified_shadow_baseline_adapter.py`) -- SIEMPRE, independientemente
del plan; (3) clasificar cada `CandidatePromotionPlanItem`: solo
`action=PROPOSE_SHADOW_PROMOTION` + `status=VALID` continua hacia
resolucion, el resto se registra en `excluded_plan_items` (Fase 11,
Parte 9); (4) resolver cada propuesta aprobada contra su candidato
fuente real (`unified_shadow_source_resolver.py`) -- una resolucion
fallida TAMBIEN termina en `excluded_plan_items`, nunca en un
`UnifiedShadowSourceMember` a medias; (5) agrupar los miembros
resueltos por componentes conexos `EXACT_MATCH`
(`unified_shadow_candidate_grouper.py`) -- un componente inconsistente
excluye a TODOS sus miembros, nunca fabrica un grupo invalido; (6)
comparar cada grupo consistente contra el baseline V1
(`unified_shadow_baseline_comparator.py`) -- `EXACT_BASELINE_MATCH`
fuerza `DUPLICATE_BASELINE_COVERAGE` (nunca `VALID` silenciosamente),
`CONFLICTS_WITH_BASELINE` fuerza `BLOCKED`; (7) reconciliar el summary;
(8) producir el artefacto final.

Puro: sin filesystem, sin Neo4j, sin LLM, nunca muta ninguno de sus
argumentos, deterministico (misma terna de artefactos de entrada
siempre produce el mismo `UnifiedCandidatesShadowArtifact`)."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

from ..contracts.candidate import CandidateArtifact
from ..contracts.candidate_promotion_assessment import (
    CandidatePromotionAssessmentArtifact,
    CandidateRelationKind,
    CandidateSource,
    UnifiedRuleFamily,
)
from ..contracts.candidate_promotion_plan import (
    CandidatePromotionPlanArtifact,
    CandidatePromotionPlanItem,
    PromotionPlanAction,
    PromotionPlanItemStatus,
)
from ..contracts.candidate_promotion_review import CandidatePromotionReviewPackage
from ..contracts.interprocedural_rule_candidates import InterproceduralRuleCandidatesArtifact
from ..contracts.unified_candidates_shadow import (
    UnifiedCandidatesShadowArtifact,
    UnifiedCandidatesShadowSummary,
    UnifiedShadowCandidateGroup,
    UnifiedShadowComparisonKind,
    UnifiedShadowExcludedPlanItem,
    UnifiedShadowExclusionReason,
    UnifiedShadowGroupStatus,
    UnifiedShadowSourceMember,
    UnifiedShadowSupport,
)
from ..contracts.v2_shadow_candidates import V2ShadowCandidatesArtifact
from .errors import UnifiedCandidatesShadowError
from .unified_shadow_baseline_adapter import adapt_v1_baseline_candidates, baseline_reference_id_for
from .unified_shadow_baseline_comparator import compare_group_to_baseline
from .unified_shadow_candidate_grouper import group_id_for, group_shadow_members
from .unified_shadow_source_resolver import resolve_source_candidate

GENERATOR_VERSION = "1.0"
POLICY_VERSION = "1.0"

_V1_CANDIDATES_KEY = "artifacts/06-candidates.json"
_V2_KEY = "v2-candidates-shadow(in-memory)"
_INTERPROCEDURAL_KEY = "interprocedural-rule-candidates-shadow(in-memory)"

_EXCLUSION_REASON_BY_ACTION: dict[PromotionPlanAction, UnifiedShadowExclusionReason] = {
    PromotionPlanAction.KEEP_BASELINE: UnifiedShadowExclusionReason.BASELINE_ITEM,
    PromotionPlanAction.SKIP_ALREADY_COVERED: UnifiedShadowExclusionReason.ALREADY_COVERED,
    PromotionPlanAction.BLOCK: UnifiedShadowExclusionReason.BLOCKED_ITEM,
    PromotionPlanAction.PENDING_REVIEW: UnifiedShadowExclusionReason.PENDING_DECISION,
    PromotionPlanAction.REJECT: UnifiedShadowExclusionReason.REJECTED,
    PromotionPlanAction.DEFER: UnifiedShadowExclusionReason.DEFERRED,
}


def _digest(*parts: str) -> str:
    canonical = "\x1f".join(parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def member_id_for(plan_item_id: str) -> str:
    """Unica funcion que genera `member_id` -- determinista (SHA-256,
    nunca UUID/timestamp/`hash()` de Python)."""
    return f"member::{_digest(plan_item_id)}"


def exclusion_id_for(plan_item_id: str) -> str:
    """Unica funcion que genera `exclusion_id` -- determinista."""
    return f"exclusion::{_digest(plan_item_id)}"


def _sorted_unique(values: Sequence[str | None]) -> list[str]:
    return sorted({v for v in values if v is not None})


def _support_for(members: Sequence[UnifiedShadowSourceMember]) -> UnifiedShadowSupport:
    supports = {member.original_support for member in members}
    if supports == {"DETERMINISTIC"}:
        return UnifiedShadowSupport.DETERMINISTIC
    if "BLOCKED" in supports:
        return UnifiedShadowSupport.BLOCKED
    if "PARTIAL" in supports:
        return UnifiedShadowSupport.PARTIAL
    return UnifiedShadowSupport.UNKNOWN


def _check_global_hash_binding(
    *,
    assessment: CandidatePromotionAssessmentArtifact,
    review_package: CandidatePromotionReviewPackage,
    plan: CandidatePromotionPlanArtifact,
    run_id: str,
    candidate_v1_artifact_hash: str,
    v2_artifact_hash: str | None,
    interprocedural_artifact_hash: str | None,
    assessment_artifact_hash: str,
    review_package_hash: str,
) -> None:
    if assessment_artifact_hash != review_package.assessment_artifact_hash:
        raise UnifiedCandidatesShadowError(
            "el review package esta desactualizado respecto al assessment actual "
            "(assessment_artifact_hash no coincide)"
        )
    if review_package_hash != plan.review_package_hash:
        raise UnifiedCandidatesShadowError(
            "el plan de promocion no corresponde al review package actual "
            "(review_package_hash no coincide)"
        )
    if plan.assessment_artifact_hash != assessment_artifact_hash:
        raise UnifiedCandidatesShadowError(
            "el plan de promocion no corresponde al assessment actual "
            "(assessment_artifact_hash no coincide)"
        )
    if not (run_id == assessment.run_id == review_package.run_id == plan.run_id):
        raise UnifiedCandidatesShadowError(
            "assessment/review package/plan pertenecen a un run_id distinto entre si"
        )
    if assessment.source_artifact_hashes.get(_V1_CANDIDATES_KEY) != candidate_v1_artifact_hash:
        raise UnifiedCandidatesShadowError(
            "el CandidateArtifact V1 actual no coincide con el hash registrado en el "
            "assessment (hash desactualizado)"
        )
    if v2_artifact_hash is not None and assessment.source_artifact_hashes.get(_V2_KEY) != (
        v2_artifact_hash
    ):
        raise UnifiedCandidatesShadowError(
            "el V2ShadowCandidatesArtifact actual no coincide con el hash registrado en "
            "el assessment (hash desactualizado)"
        )
    if interprocedural_artifact_hash is not None and assessment.source_artifact_hashes.get(
        _INTERPROCEDURAL_KEY
    ) != (interprocedural_artifact_hash):
        raise UnifiedCandidatesShadowError(
            "el InterproceduralRuleCandidatesArtifact actual no coincide con el hash "
            "registrado en el assessment (hash desactualizado)"
        )


def _exclusion_reason_for(plan_item: CandidatePromotionPlanItem) -> UnifiedShadowExclusionReason:
    if plan_item.status == PromotionPlanItemStatus.INVALID_DECISION:
        return UnifiedShadowExclusionReason.PLAN_ITEM_NOT_VALID
    return _EXCLUSION_REASON_BY_ACTION.get(
        plan_item.action, UnifiedShadowExclusionReason.PLAN_ACTION_NOT_PROPOSE
    )


def _build_excluded_item(
    plan_item: CandidatePromotionPlanItem,
    *,
    reason: UnifiedShadowExclusionReason,
    diagnostics: Sequence[str] = (),
) -> UnifiedShadowExcludedPlanItem:
    return UnifiedShadowExcludedPlanItem(
        exclusion_id=exclusion_id_for(plan_item.plan_item_id),
        plan_item_id=plan_item.plan_item_id,
        review_item_id=plan_item.review_item_id,
        assessment_id=plan_item.assessment_id,
        reference_id=plan_item.reference_id,
        source=plan_item.source,
        source_candidate_id=plan_item.source_candidate_id,
        action=plan_item.action,
        status=plan_item.status,
        reason=reason,
        diagnostics=sorted(set(diagnostics)),
    )


def analyze_unified_candidates_shadow(
    *,
    run_id: str,
    v1_candidates: CandidateArtifact,
    v2_candidates: V2ShadowCandidatesArtifact | None,
    interprocedural_candidates: InterproceduralRuleCandidatesArtifact | None,
    assessment: CandidatePromotionAssessmentArtifact,
    review_package: CandidatePromotionReviewPackage,
    plan: CandidatePromotionPlanArtifact,
    source_package_hash: str,
    candidate_v1_artifact_hash: str,
    v2_artifact_hash: str | None,
    interprocedural_artifact_hash: str | None,
    assessment_artifact_hash: str,
    review_package_hash: str,
    promotion_plan_hash: str,
    source_artifact_hashes: Mapping[str, str],
) -> UnifiedCandidatesShadowArtifact:
    """Punto de entrada puro. Nunca muta ninguno de sus argumentos.
    Determinista: misma terna de artefactos de entrada siempre produce
    el mismo `UnifiedCandidatesShadowArtifact` (mismos bytes JSON)."""
    _check_global_hash_binding(
        assessment=assessment,
        review_package=review_package,
        plan=plan,
        run_id=run_id,
        candidate_v1_artifact_hash=candidate_v1_artifact_hash,
        v2_artifact_hash=v2_artifact_hash,
        interprocedural_artifact_hash=interprocedural_artifact_hash,
        assessment_artifact_hash=assessment_artifact_hash,
        review_package_hash=review_package_hash,
    )

    baseline_candidates = adapt_v1_baseline_candidates(
        v1_candidates, source_artifact_hash=candidate_v1_artifact_hash
    )
    baseline_reference_id_by_assessment_reference_id: dict[str, str] = {}
    for v1_reference in assessment.candidate_references:
        if v1_reference.source == CandidateSource.V1:
            baseline_reference_id_by_assessment_reference_id[
                v1_reference.unified_reference_id
            ] = baseline_reference_id_for(v1_reference.source_candidate_id)
    baseline_candidates_by_reference_id = {
        ref.baseline_reference_id: ref for ref in baseline_candidates
    }

    review_item_by_id = {item.review_item_id: item for item in review_package.review_items}
    reference_by_id = {ref.unified_reference_id: ref for ref in assessment.candidate_references}

    members: list[UnifiedShadowSourceMember] = []
    excluded_items: list[UnifiedShadowExcludedPlanItem] = []

    for plan_item in plan.plan_items:
        if not (
            plan_item.action == PromotionPlanAction.PROPOSE_SHADOW_PROMOTION
            and plan_item.status == PromotionPlanItemStatus.VALID
        ):
            excluded_items.append(
                _build_excluded_item(plan_item, reason=_exclusion_reason_for(plan_item))
            )
            continue

        review_item = review_item_by_id.get(plan_item.review_item_id)
        if (
            review_item is None
            or review_item.assessment_id != plan_item.assessment_id
            or review_item.reference_id != plan_item.reference_id
        ):
            excluded_items.append(
                _build_excluded_item(
                    plan_item,
                    reason=UnifiedShadowExclusionReason.IDENTITY_MISMATCH,
                    diagnostics=["REVIEW_ITEM_ASSESSMENT_REFERENCE_MISMATCH"],
                )
            )
            continue

        reference = reference_by_id.get(plan_item.reference_id)
        if reference is None or reference.source != plan_item.source:
            excluded_items.append(
                _build_excluded_item(
                    plan_item,
                    reason=UnifiedShadowExclusionReason.SOURCE_CANDIDATE_NOT_FOUND,
                    diagnostics=["ASSESSMENT_REFERENCE_NOT_FOUND_OR_SOURCE_MISMATCH"],
                )
            )
            continue

        if reference.source == CandidateSource.V1:
            excluded_items.append(
                _build_excluded_item(
                    plan_item,
                    reason=UnifiedShadowExclusionReason.UNKNOWN_SOURCE,
                    diagnostics=["SOURCE_V1_NEVER_PRODUCES_A_SHADOW_MEMBER"],
                )
            )
            continue

        resolution = resolve_source_candidate(
            reference=reference,
            v1_artifact=v1_candidates,
            v2_artifact=v2_candidates,
            interprocedural_artifact=interprocedural_candidates,
        )
        if not resolution.is_success:
            reason_map = {
                "UNKNOWN_SOURCE": UnifiedShadowExclusionReason.UNKNOWN_SOURCE,
                "SOURCE_CANDIDATE_NOT_FOUND": (
                    UnifiedShadowExclusionReason.SOURCE_CANDIDATE_NOT_FOUND
                ),
                "IDENTITY_MISMATCH": UnifiedShadowExclusionReason.IDENTITY_MISMATCH,
            }
            assert resolution.failure_reason is not None
            excluded_items.append(
                _build_excluded_item(
                    plan_item,
                    reason=reason_map[resolution.failure_reason.value],
                    diagnostics=([resolution.failure_detail] if resolution.failure_detail else []),
                )
            )
            continue

        assert resolution.source_candidate_hash is not None
        members.append(
            UnifiedShadowSourceMember(
                member_id=member_id_for(plan_item.plan_item_id),
                source=reference.source,
                source_candidate_id=reference.source_candidate_id,
                source_artifact_hash=reference.source_artifact_hash,
                source_candidate_hash=resolution.source_candidate_hash,
                assessment_id=plan_item.assessment_id,
                assessment_reference_id=plan_item.reference_id,
                review_item_id=plan_item.review_item_id,
                plan_item_id=plan_item.plan_item_id,
                review_decision_id=plan_item.decision_id,  # type: ignore[arg-type]
                decision=plan_item.decision,  # type: ignore[arg-type]
                reason_code=plan_item.reason_code,  # type: ignore[arg-type]
                reviewer_reference=plan_item.reviewer_reference,  # type: ignore[arg-type]
                rule_family=reference.rule_family,
                original_rule_type=reference.original_rule_type,
                original_support=reference.original_support,
                program=reference.program,  # type: ignore[arg-type]
                paragraph=reference.paragraph,
                source_decision_id=reference.decision_id,
                decision_reference_id=None,
                call_site_id=reference.call_site_id,
                target=reference.target,
                input_literal=reference.input_literal,
                output_literal=reference.output_literal,
                evidence_ids=sorted(set(reference.evidence_ids)),
                provenance_references=sorted(set(reference.provenance_references)),
                diagnostics=sorted(set(reference.diagnostics)),
            )
        )

    exact_match_pairs = frozenset(
        frozenset({relation.left_reference_id, relation.right_reference_id})
        for relation in assessment.relations
        if relation.relation_kind == CandidateRelationKind.EXACT_MATCH
    )
    components = group_shadow_members(members, exact_match_reference_pairs=exact_match_pairs)

    member_by_id = {member.member_id: member for member in members}
    plan_item_by_id = {item.plan_item_id: item for item in plan.plan_items}

    valid_members: list[UnifiedShadowSourceMember] = []
    groups: list[UnifiedShadowCandidateGroup] = []

    for component in components:
        component_members = [member_by_id[mid] for mid in component.member_ids]
        if not component.is_consistent:
            for member in component_members:
                original_plan_item = plan_item_by_id[member.plan_item_id]
                excluded_items.append(
                    _build_excluded_item(
                        original_plan_item,
                        reason=UnifiedShadowExclusionReason.INCONSISTENT_EXACT_MATCH_GROUP,
                        diagnostics=component.inconsistency_reasons,
                    )
                )
            continue

        valid_members.extend(component_members)
        rule_family: UnifiedRuleFamily = component_members[0].rule_family
        program = component_members[0].program
        target = component_members[0].target
        output_literal = component_members[0].output_literal
        paragraphs = {member.paragraph for member in component_members}
        paragraph = next(iter(paragraphs)) if len(paragraphs) == 1 else None
        input_literals = {member.input_literal for member in component_members}
        input_literal = next(iter(input_literals)) if len(input_literals) == 1 else None

        comparison = compare_group_to_baseline(
            member_assessment_reference_ids=[
                member.assessment_reference_id for member in component_members
            ],
            group_program=program,
            group_target=target,
            group_output_literal=output_literal,
            assessment=assessment,
            baseline_reference_id_by_assessment_reference_id=(
                baseline_reference_id_by_assessment_reference_id
            ),
            baseline_candidates_by_reference_id=baseline_candidates_by_reference_id,
        )

        blocking_reasons: list[str] = []
        diagnostics: list[str] = []
        if comparison.comparison_to_v1 == UnifiedShadowComparisonKind.EXACT_BASELINE_MATCH:
            status = UnifiedShadowGroupStatus.DUPLICATE_BASELINE_COVERAGE
            blocking_reasons.append("EXACT_MATCH_WITH_V1_BASELINE")
            diagnostics.append("PLAN_SOURCE_MISALIGNMENT::APPROVED_PROPOSAL_ALREADY_COVERED_BY_V1")
        elif comparison.comparison_to_v1 == UnifiedShadowComparisonKind.CONFLICTS_WITH_BASELINE:
            status = UnifiedShadowGroupStatus.BLOCKED
            blocking_reasons.append("CONFLICTS_WITH_V1_BASELINE")
        else:
            status = UnifiedShadowGroupStatus.VALID

        groups.append(
            UnifiedShadowCandidateGroup(
                unified_shadow_candidate_id=group_id_for(
                    member_ids=component.member_ids,
                    rule_family=rule_family.value,
                    program=program,
                    target=target,
                    output_literal=output_literal,
                ),
                status=status,
                rule_family=rule_family,
                program=program,
                paragraph=paragraph,
                source_decision_ids=_sorted_unique(
                    [member.source_decision_id for member in component_members]
                ),
                call_site_ids=_sorted_unique([member.call_site_id for member in component_members]),
                target=target,
                input_literal=input_literal,
                output_literal=output_literal,
                support=_support_for(component_members),
                member_ids=component.member_ids,
                evidence_ids=_sorted_unique(
                    [eid for member in component_members for eid in member.evidence_ids]
                ),
                provenance_references=_sorted_unique(
                    [p for member in component_members for p in member.provenance_references]
                ),
                assessment_ids=_sorted_unique(
                    [member.assessment_id for member in component_members]
                ),
                review_item_ids=_sorted_unique(
                    [member.review_item_id for member in component_members]
                ),
                plan_item_ids=_sorted_unique([member.plan_item_id for member in component_members]),
                review_decision_ids=_sorted_unique(
                    [member.review_decision_id for member in component_members]
                ),
                comparison_to_v1=comparison.comparison_to_v1,
                exact_baseline_reference_ids=comparison.exact_baseline_reference_ids,
                related_baseline_reference_ids=comparison.related_baseline_reference_ids,
                conflicting_baseline_reference_ids=comparison.conflicting_baseline_reference_ids,
                blocking_reasons=sorted(set(blocking_reasons)),
                diagnostics=sorted(set(diagnostics)),
            )
        )

    groups.sort(key=lambda group: group.unified_shadow_candidate_id)
    valid_members.sort(key=lambda member: member.member_id)
    excluded_items.sort(key=lambda item: item.exclusion_id)

    summary = _build_summary(
        baseline_count=len(baseline_candidates),
        members=valid_members,
        groups=groups,
        excluded_items=excluded_items,
    )

    return UnifiedCandidatesShadowArtifact(
        run_id=run_id,
        source_package_hash=source_package_hash,
        candidate_v1_artifact_hash=candidate_v1_artifact_hash,
        v2_artifact_hash=v2_artifact_hash,
        interprocedural_artifact_hash=interprocedural_artifact_hash,
        assessment_artifact_hash=assessment_artifact_hash,
        review_package_hash=review_package_hash,
        promotion_plan_hash=promotion_plan_hash,
        source_artifact_hashes=dict(source_artifact_hashes),
        summary=summary,
        baseline_candidates=baseline_candidates,
        shadow_members=valid_members,
        shadow_groups=groups,
        excluded_plan_items=excluded_items,
        diagnostics=[],
    )


def _build_summary(
    *,
    baseline_count: int,
    members: Sequence[UnifiedShadowSourceMember],
    groups: Sequence[UnifiedShadowCandidateGroup],
    excluded_items: Sequence[UnifiedShadowExcludedPlanItem],
) -> UnifiedCandidatesShadowSummary:
    counts_by_source: dict[CandidateSource, int] = {}
    counts_by_family: dict[UnifiedRuleFamily, int] = {}
    for member in members:
        counts_by_source[member.source] = counts_by_source.get(member.source, 0) + 1
        counts_by_family[member.rule_family] = counts_by_family.get(member.rule_family, 0) + 1

    counts_by_status: dict[UnifiedShadowGroupStatus, int] = {}
    counts_by_comparison: dict[UnifiedShadowComparisonKind, int] = {}
    for group in groups:
        counts_by_status[group.status] = counts_by_status.get(group.status, 0) + 1
        counts_by_comparison[group.comparison_to_v1] = (
            counts_by_comparison.get(group.comparison_to_v1, 0) + 1
        )

    counts_by_exclusion: dict[UnifiedShadowExclusionReason, int] = {}
    for item in excluded_items:
        counts_by_exclusion[item.reason] = counts_by_exclusion.get(item.reason, 0) + 1

    valid_group_count = counts_by_status.get(UnifiedShadowGroupStatus.VALID, 0)

    return UnifiedCandidatesShadowSummary(
        v1_baseline_count=baseline_count,
        proposed_plan_item_count=len(members) + len(excluded_items),
        shadow_member_count=len(members),
        shadow_group_count=len(groups),
        excluded_plan_item_count=len(excluded_items),
        exact_baseline_match_group_count=counts_by_comparison.get(
            UnifiedShadowComparisonKind.EXACT_BASELINE_MATCH, 0
        ),
        related_to_baseline_group_count=counts_by_comparison.get(
            UnifiedShadowComparisonKind.RELATED_TO_BASELINE, 0
        ),
        not_in_baseline_group_count=counts_by_comparison.get(
            UnifiedShadowComparisonKind.NOT_IN_BASELINE, 0
        ),
        conflicting_with_baseline_group_count=counts_by_comparison.get(
            UnifiedShadowComparisonKind.CONFLICTS_WITH_BASELINE, 0
        ),
        not_evaluated_group_count=counts_by_comparison.get(
            UnifiedShadowComparisonKind.NOT_EVALUATED, 0
        ),
        valid_group_count=valid_group_count,
        invalid_group_count=len(groups) - valid_group_count,
        counts_by_source=counts_by_source,
        counts_by_rule_family=counts_by_family,
        counts_by_group_status=counts_by_status,
        counts_by_baseline_comparison=counts_by_comparison,
        counts_by_exclusion_reason=counts_by_exclusion,
    )
