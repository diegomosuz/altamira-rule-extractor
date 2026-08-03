"""Fixtures compartidas del catalogo unificado de candidatos y
evaluacion de promocion (Fase 9 de la ampliacion semantica,
`feat/unified-candidate-promotion-assessment`). NO es un modulo de test
(sin prefijo `test_`): expone builders reutilizados por los tests de
contrato/adaptadores/relaciones/conflictos/politica/analizador/
servicio/CLI. Mismo patron que `tests/pipeline/v2_shadow_helpers.py`/
`tests/pipeline/interprocedural_rule_helpers.py`."""

from __future__ import annotations

from altamira_extractor.contracts.candidate import CandidateArtifact, RuleCandidate
from altamira_extractor.contracts.enums import CandidateStatus
from altamira_extractor.contracts.interprocedural_rule_candidates import (
    InterproceduralCandidateComparison,
    InterproceduralCandidateSupport,
    InterproceduralComparisonStatus,
    InterproceduralRelationStatus,
    InterproceduralRuleCandidate,
    InterproceduralRuleCandidatesArtifact,
    InterproceduralRuleCandidatesSummary,
    InterproceduralRuleEvidence,
    InterproceduralRuleType,
)
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

HASH = "e" * 64

_CALLER_PARAGRAPH_ID = f"program::AR::APP::CALLER::1.0::{HASH[:12]}::paragraph::MAIN"


def program_paragraph_id(program: str, paragraph: str = "MAIN") -> str:
    return f"program::AR::APP::{program}::1.0::{HASH[:12]}::paragraph::{paragraph}"


def v1_candidate(
    *,
    candidate_id: str = "candidate::1",
    program: str = "CALLER",
    paragraph: str = "MAIN",
    outcome_code: str | None = "R001",
    line_start: int = 10,
) -> RuleCandidate:
    paragraph_id = program_paragraph_id(program, paragraph)
    return RuleCandidate(
        candidate_id=candidate_id,
        paragraph_id=paragraph_id,
        paragraph_name=paragraph,
        decision_id=f"{paragraph_id}::decision::{line_start}::1",
        detector_id="q0-return-code-decision",
        detector_version="1.0",
        detector_score=1.0,
        status=CandidateStatus.DETECTED_CANDIDATE,
        condition="WS-SALDO < 0",
        outcome_code=outcome_code,
        rule_type=None,
        line_start=line_start,
        source_file=f"01-codigo/cobol/{program}.cbl",
        source_package_hash=HASH,
    )


def v1_artifact(
    candidates: list[RuleCandidate] | None = None, run_id: str = "run1"
) -> CandidateArtifact:
    return CandidateArtifact(
        run_id=run_id,
        source_package_hash=HASH,
        semantic_graph_hash=HASH,
        invariants_query_hash=HASH,
        q0_query_hash=HASH,
        candidates=candidates or [],
    )


def v2_source_reference(*, program: str, paragraph: str = "MAIN") -> V2CandidateSourceReference:
    return V2CandidateSourceReference(
        program=program,
        paragraph=paragraph,
        statement_id=f"{program}::{paragraph}::0::MOVE",
        effect_id=f"effect::{program}::{paragraph}::{program}::{paragraph}::0::MOVE::ASSIGN_LITERAL::0",
        fact_id=(
            f"fact::{program}::{paragraph}::root::WS-X::{program}::{paragraph}::0::MOVE::"
            "DIRECT_LITERAL::0"
        ),
    )


def v2_candidate(
    *,
    candidate_id: str = "v2::V2_RETURN_CODE_PROPAGATION::" + "0" * 24,
    detector_id: str = "V2_RETURN_CODE_PROPAGATION",
    rule_type: V2RuleType = V2RuleType.RETURN_CODE_RULE,
    support: V2CandidateSupport = V2CandidateSupport.DETERMINISTIC,
    program: str = "CALLER",
    paragraph: str = "MAIN",
    target_variable: str = "WS-X",
    resolved_literal: str | None = "R001",
    decision_id: str | None = None,
    diagnostic_codes: list[str] | None = None,
    comparable_v1_candidate_ids: list[str] | None = None,
) -> V2ShadowCandidate:
    fields: dict[str, object] = dict(
        candidate_id=candidate_id,
        detector_id=detector_id,
        detector_version="1.0",
        rule_type=rule_type,
        support=support,
        detector_score=1.0 if support == V2CandidateSupport.DETERMINISTIC else 0.7,
        program=program,
        paragraph=paragraph,
        anchor_statement_id=f"{program}::{paragraph}::0::MOVE",
        decision_id=decision_id,
        target_variable=target_variable,
        target_qualified_name=target_variable,
        resolved_literal=resolved_literal if support != V2CandidateSupport.BLOCKED else None,
        reason="Fixture de test Fase 9.",
        comparable_v1_candidate_ids=comparable_v1_candidate_ids or [],
    )
    if support == V2CandidateSupport.DETERMINISTIC:
        fields["semantic_effect_ids"] = [
            f"effect::{program}::{paragraph}::{program}::{paragraph}::0::MOVE::ASSIGN_LITERAL::0"
        ]
        fields["propagation_fact_ids"] = [
            f"fact::{program}::{paragraph}::root::{target_variable}::{program}::{paragraph}::"
            "0::MOVE::DIRECT_LITERAL::0"
        ]
        fields["source_references"] = [v2_source_reference(program=program, paragraph=paragraph)]
    elif support == V2CandidateSupport.BLOCKED:
        fields["diagnostic_codes"] = diagnostic_codes or ["V2_BLOCKED_EXAMPLE"]
    return V2ShadowCandidate(**fields)  # type: ignore[arg-type]


def _fill_missing_v2_only_comparisons(
    candidates: list[V2ShadowCandidate], comparisons: list[V1V2CandidateComparison]
) -> list[V1V2CandidateComparison]:
    """`V2ShadowSummary` exige que exista al menos una comparacion que
    explique cada candidato presente (ver validador del contrato real).
    Autogenera una comparacion `V2_ONLY` para cada candidato no cubierto
    explicitamente por el test -- mismo patron que usaria el comparador
    real cuando ningun V1 se relaciona con ese candidato."""
    covered = {vid for comparison in comparisons for vid in comparison.v2_candidate_ids}
    extra = [
        v1_v2_v2_only_comparison(v2_id=c.candidate_id, program=c.program, paragraph=c.paragraph)
        for c in candidates
        if c.candidate_id not in covered
    ]
    return [*comparisons, *extra]


def v2_artifact(
    *,
    candidates: list[V2ShadowCandidate] | None = None,
    comparisons: list[V1V2CandidateComparison] | None = None,
    run_id: str = "run1",
) -> V2ShadowCandidatesArtifact:
    candidates = candidates or []
    comparisons = _fill_missing_v2_only_comparisons(candidates, comparisons or [])
    deterministic = sum(1 for c in candidates if c.support == V2CandidateSupport.DETERMINISTIC)
    partial = sum(1 for c in candidates if c.support == V2CandidateSupport.PARTIAL)
    blocked = sum(1 for c in candidates if c.support == V2CandidateSupport.BLOCKED)
    counts_by_rule_type: dict[V2RuleType, int] = {}
    counts_by_detector: dict[str, int] = {}
    for c in candidates:
        counts_by_rule_type[c.rule_type] = counts_by_rule_type.get(c.rule_type, 0) + 1
        counts_by_detector[c.detector_id] = counts_by_detector.get(c.detector_id, 0) + 1
    matched = sum(1 for c in comparisons if c.status == V1V2ComparisonStatus.MATCHED)
    v1_only = sum(1 for c in comparisons if c.status == V1V2ComparisonStatus.V1_ONLY)
    v2_only = sum(1 for c in comparisons if c.status == V1V2ComparisonStatus.V2_ONLY)
    related = sum(1 for c in comparisons if c.status == V1V2ComparisonStatus.RELATED_NOT_EQUIVALENT)
    grouped: dict[tuple[str, V2RuleType], list[V2ShadowCandidate]] = {}
    for c in candidates:
        grouped.setdefault((c.detector_id, c.rule_type), []).append(c)
    executions = [
        V2DetectorExecution(
            detector_id=detector_id,
            detector_version="1.0",
            rule_type=rule_type,
            candidate_count=len(group),
            blocked_count=sum(1 for c in group if c.support == V2CandidateSupport.BLOCKED),
            candidates=sorted(group, key=lambda c: c.candidate_id),
        )
        for (detector_id, rule_type), group in sorted(grouped.items())
    ]
    return V2ShadowCandidatesArtifact(
        run_id=run_id,
        source_package_hash=HASH,
        source_artifact_hashes={"artifacts/02-canonical": HASH},
        semantic_effects_schema_version="1.2",
        semantic_effects_analyzer_version="1.2",
        semantic_propagation_schema_version="1.1",
        semantic_propagation_analyzer_version="1.1",
        summary=V2ShadowSummary(
            detector_count=len(executions),
            v1_candidate_count=0,
            v2_candidate_count=len(candidates),
            deterministic_count=deterministic,
            partial_count=partial,
            blocked_count=blocked,
            matched_count=matched,
            v1_only_count=v1_only,
            v2_only_count=v2_only,
            related_not_equivalent_count=related,
            counts_by_rule_type=counts_by_rule_type,
            counts_by_detector=counts_by_detector,
        ),
        executions=executions,
        comparisons=sorted(comparisons, key=lambda c: c.comparison_id),
    )


def v1_v2_matched_comparison(
    *, v1_id: str, v2_id: str, program: str = "CALLER", paragraph: str = "MAIN"
) -> V1V2CandidateComparison:
    return V1V2CandidateComparison(
        comparison_id=f"comparison::matched::{v1_id}::{v2_id}",
        status=V1V2ComparisonStatus.MATCHED,
        v1_candidate_ids=[v1_id],
        v2_candidate_ids=[v2_id],
        program=program,
        paragraph=paragraph,
        reason="Fixture MATCHED de test Fase 9.",
    )


def v1_v2_related_comparison(
    *, v1_id: str, v2_id: str, program: str = "CALLER", paragraph: str = "MAIN"
) -> V1V2CandidateComparison:
    return V1V2CandidateComparison(
        comparison_id=f"comparison::related::{v1_id}::{v2_id}",
        status=V1V2ComparisonStatus.RELATED_NOT_EQUIVALENT,
        v1_candidate_ids=[v1_id],
        v2_candidate_ids=[v2_id],
        program=program,
        paragraph=paragraph,
        reason="Fixture RELATED_NOT_EQUIVALENT de test Fase 9.",
    )


def v1_v2_v1_only_comparison(
    *, v1_id: str, program: str = "CALLER", paragraph: str = "MAIN"
) -> V1V2CandidateComparison:
    return V1V2CandidateComparison(
        comparison_id=f"comparison::v1only::{v1_id}",
        status=V1V2ComparisonStatus.V1_ONLY,
        v1_candidate_ids=[v1_id],
        v2_candidate_ids=[],
        program=program,
        paragraph=paragraph,
        reason="Fixture V1_ONLY de test Fase 9.",
    )


def v1_v2_v2_only_comparison(
    *, v2_id: str, program: str = "CALLER", paragraph: str = "MAIN"
) -> V1V2CandidateComparison:
    return V1V2CandidateComparison(
        comparison_id=f"comparison::v2only::{v2_id}",
        status=V1V2ComparisonStatus.V2_ONLY,
        v1_candidate_ids=[],
        v2_candidate_ids=[v2_id],
        program=program,
        paragraph=paragraph,
        reason="Fixture V2_ONLY de test Fase 9.",
    )


def interprocedural_evidence(
    *, evidence_id: str = "evidence::1", caller: str = "CALLER", callee: str | None = "CALLEE"
) -> InterproceduralRuleEvidence:
    return InterproceduralRuleEvidence(
        evidence_id=evidence_id,
        caller_program=caller,
        callee_program=callee,
        call_site_id="callsite::x",
        statement_id=f"{caller}::MAIN::0::CALL",
        output_literal="R001",
    )


def interprocedural_candidate(
    *,
    candidate_id: str = "ipr::interprocedural-return-code-rule::1",
    detector: str = "interprocedural-return-code-rule",
    rule_type: InterproceduralRuleType = InterproceduralRuleType.RETURN_CODE_RULE,
    support: InterproceduralCandidateSupport = InterproceduralCandidateSupport.DETERMINISTIC,
    caller_program: str = "CALLER",
    callee_program: str | None = "CALLEE",
    target: str = "WS-X",
    output_literal: str | None = "R001",
    barriers: list[str] | None = None,
) -> InterproceduralRuleCandidate:
    fields: dict[str, object] = dict(
        candidate_id=candidate_id,
        detector=detector,
        rule_type=rule_type,
        support=support,
        caller_program=caller_program,
        callee_program=callee_program,
        caller_paragraph="MAIN",
        call_site_id="callsite::x",
        target=target,
        evidence=[interprocedural_evidence(caller=caller_program, callee=callee_program)],
    )
    if support == InterproceduralCandidateSupport.DETERMINISTIC:
        fields["output_literal"] = output_literal
    elif support == InterproceduralCandidateSupport.BLOCKED:
        fields["barriers"] = barriers or ["DYNAMIC_CALL"]
    return InterproceduralRuleCandidate(**fields)  # type: ignore[arg-type]


def interprocedural_comparison(
    *,
    candidate_id: str,
    status: InterproceduralComparisonStatus,
    v1_relation: InterproceduralRelationStatus,
    v2_relation: InterproceduralRelationStatus,
    v1_candidate_id: str | None = None,
    v2_candidate_id: str | None = None,
) -> InterproceduralCandidateComparison:
    return InterproceduralCandidateComparison(
        comparison_id=f"comparison::{candidate_id}",
        interprocedural_candidate_id=candidate_id,
        v1_candidate_id=v1_candidate_id,
        v2_candidate_id=v2_candidate_id,
        v1_relation=v1_relation,
        v2_relation=v2_relation,
        status=status,
        reason="Fixture de test Fase 9.",
    )


def _fill_missing_interprocedural_comparisons(
    candidates: list[InterproceduralRuleCandidate],
    comparisons: list[InterproceduralCandidateComparison],
) -> list[InterproceduralCandidateComparison]:
    """`InterproceduralCandidateComparison`: "Cada InterproceduralRuleCandidate
    tiene EXACTAMENTE una comparacion" (invariante de artefacto real).
    Autogenera la comparacion por defecto para cualquier candidato no
    cubierto explicitamente por el test: `BLOCKED` (soporte BLOCKED) o
    `INTERPROCEDURAL_ONLY` (v1/v2 evaluados, sin coincidencia)."""
    covered = {c.interprocedural_candidate_id for c in comparisons}
    extra: list[InterproceduralCandidateComparison] = []
    for candidate in candidates:
        if candidate.candidate_id in covered:
            continue
        if candidate.support == InterproceduralCandidateSupport.BLOCKED:
            extra.append(
                InterproceduralCandidateComparison(
                    comparison_id=f"comparison::{candidate.candidate_id}",
                    interprocedural_candidate_id=candidate.candidate_id,
                    v1_relation=InterproceduralRelationStatus.NOT_EVALUATED,
                    v2_relation=InterproceduralRelationStatus.NOT_EVALUATED,
                    status=InterproceduralComparisonStatus.BLOCKED,
                    reason="Fixture BLOCKED de test Fase 9.",
                )
            )
        else:
            extra.append(
                interprocedural_comparison(
                    candidate_id=candidate.candidate_id,
                    status=InterproceduralComparisonStatus.INTERPROCEDURAL_ONLY,
                    v1_relation=InterproceduralRelationStatus.NOT_FOUND,
                    v2_relation=InterproceduralRelationStatus.NOT_FOUND,
                )
            )
    return [*comparisons, *extra]


def interprocedural_artifact(
    *,
    candidates: list[InterproceduralRuleCandidate] | None = None,
    comparisons: list[InterproceduralCandidateComparison] | None = None,
    run_id: str = "run1",
) -> InterproceduralRuleCandidatesArtifact:
    candidates = sorted(candidates or [], key=lambda c: c.candidate_id)
    comparisons = sorted(
        _fill_missing_interprocedural_comparisons(candidates, comparisons or []),
        key=lambda c: c.comparison_id,
    )
    deterministic = sum(
        1 for c in candidates if c.support == InterproceduralCandidateSupport.DETERMINISTIC
    )
    partial = sum(1 for c in candidates if c.support == InterproceduralCandidateSupport.PARTIAL)
    blocked = sum(1 for c in candidates if c.support == InterproceduralCandidateSupport.BLOCKED)
    counts_by_detector: dict[str, int] = {}
    counts_by_rule_type: dict[InterproceduralRuleType, int] = {}
    for c in candidates:
        counts_by_detector[c.detector] = counts_by_detector.get(c.detector, 0) + 1
        counts_by_rule_type[c.rule_type] = counts_by_rule_type.get(c.rule_type, 0) + 1
    matched_v1 = sum(
        1 for c in comparisons if c.status == InterproceduralComparisonStatus.MATCHED_V1
    )
    matched_v2 = sum(
        1 for c in comparisons if c.status == InterproceduralComparisonStatus.MATCHED_V2
    )
    related_v1 = sum(
        1 for c in comparisons if c.status == InterproceduralComparisonStatus.RELATED_V1
    )
    related_v2 = sum(
        1 for c in comparisons if c.status == InterproceduralComparisonStatus.RELATED_V2
    )
    interprocedural_only = sum(
        1 for c in comparisons if c.status == InterproceduralComparisonStatus.INTERPROCEDURAL_ONLY
    )
    not_evaluated = sum(
        1 for c in comparisons if c.status == InterproceduralComparisonStatus.NOT_EVALUATED
    )
    return InterproceduralRuleCandidatesArtifact(
        run_id=run_id,
        source_package_hash=HASH,
        source_artifact_hashes={"artifacts/02-canonical": HASH},
        canonical_schema_versions=["1.2"],
        semantic_effects_schema_version="1.2",
        semantic_propagation_schema_version="1.1",
        interprocedural_call_linkage_schema_version="1.0",
        interprocedural_propagation_schema_version="1.0",
        summary=InterproceduralRuleCandidatesSummary(
            candidate_count=len(candidates),
            deterministic_count=deterministic,
            partial_count=partial,
            blocked_count=blocked,
            counts_by_detector=counts_by_detector,
            counts_by_rule_type=counts_by_rule_type,
            matched_v1_count=matched_v1,
            matched_v2_count=matched_v2,
            related_v1_count=related_v1,
            related_v2_count=related_v2,
            interprocedural_only_count=interprocedural_only,
            not_evaluated_count=not_evaluated,
        ),
        candidates=candidates,
        comparisons=comparisons,
        diagnostics=[],
    )
