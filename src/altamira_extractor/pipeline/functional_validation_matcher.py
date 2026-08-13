"""Analizador puro de validacion funcional (Fase 15B2-A, Parte F): sin
filesystem, sin Neo4j, sin LLM. Compara `FunctionalGroundTruthSet` (Parte
E) contra `UnifiedCandidateReference` reales (Fase 9,
`CandidatePromotionAssessmentArtifact.candidate_references`).

Matching EXCLUSIVAMENTE deterministico (decision arquitectonica #10):
`_matches_expected_rule`/`_matches_negative_case` comparan
`(rule_family, program, paragraph)` como igualdad exacta de string tras
`.strip()` -- la unica normalizacion permitida. Nunca hay un umbral de
similitud ni un score continuo.

Aplicabilidad (checkpoint correctivo): `compute_case_applicability`
decide, ANTES de tocar `candidate_references`, si el fixture set de un
caso esta presente en el run evaluado (`run_fixture_hashes`, sha256 de
cada archivo real bajo `run_dir/work/extracted/`, ver `pipeline/
functional_validation_service.py`). Un caso NOT_APPLICABLE nunca llega a
`_evaluate_positive_case`/`_evaluate_negative_case`: `validate_ground_
truth` cortocircuita a `NOT_EVALUATED` sin iterar `candidate_references`,
para que una misma deteccion real nunca pueda "satisfacer" casos de
runs/paquetes distintos.

Completitud (segundo checkpoint correctivo): `validate_ground_truth`
calcula `coverage_status`/`pending_case_ids`/`required_case_count`/etc.
sobre el catalogo COMPLETO (`ground_truth.cases`, nunca solo los
aplicables) -- un caso NOT_APPLICABLE en este run queda pendiente en
`pending_case_ids`, y `dataset_disposition` nunca alcanza
PASS_ENGINEERING mientras haya pendientes (ver `contracts/
functional_validation.py::FunctionalValidationReport._check_dataset_
disposition_matches_coverage`).

Metricas por caso (Seccion 5, cierre de Fase 15B2-A): para un caso
POSITIVE con `outcome=MATCHED` respaldado por EXACTAMENTE un
`UnifiedCandidateReference` (asociacion inequivoca), `_case_level_
metrics_for_match` adjunta `CaseLevelMetrics` con
`evidence_reference_count`/`provenance_reference_count` (siempre
disponibles en `UnifiedCandidateReference`) y, cuando el candidato
`source=V1` Y existe su `GuardrailCandidateArtifact` real
(`guardrail_by_candidate_id`, cargado por `functional_validation_
service.py` desde `artifacts/09-guardrails/`), `guardrail_verdict`/
`guardrail_repair_attempts` reales. Mas de un candidato satisfaciendo la
misma expectation, o cero, nunca produce metricas de caso -- solo
`CaseMetricStatus.NOT_EVALUATED` con `reason_code=
CASE_ARTIFACT_TRACEABILITY_UNAVAILABLE`. NUNCA copia `FunctionalValidation
Metrics` (nivel de run) dentro de un caso."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from ..contracts.candidate_promotion_assessment import (
    CandidateSource,
    UnifiedCandidateReference,
    UnifiedRuleFamily,
)
from ..contracts.functional_ground_truth import (
    FunctionalGroundTruthSet,
    GroundTruthCase,
    GroundTruthCaseKind,
    GroundTruthExpectedRule,
)
from ..contracts.functional_validation import (
    Applicability,
    ArtifactChainIntegrityReport,
    CaseLevelMetrics,
    CaseMetricReasonCode,
    CaseMetricStatus,
    ExpectedRuleMatchResult,
    FinalRuleLinkageReport,
    FinalRuleLinkageStatus,
    FunctionalDatasetCoverageStatus,
    FunctionalDatasetDisposition,
    FunctionalValidationMetrics,
    FunctionalValidationReport,
    GroundTruthCaseResult,
    MatchOutcome,
    ValidationSource,
)

_DEFAULT_ARTIFACT_CHAIN_INTEGRITY = ArtifactChainIntegrityReport(
    candidates_checked=0, candidates_missing_context=[]
)
_DEFAULT_FINAL_RULE_LINKAGE = FinalRuleLinkageReport(status=FinalRuleLinkageStatus.NOT_APPLICABLE)


@dataclass(frozen=True)
class GuardrailLookupEntry:
    """Info minima de UN `GuardrailCandidateArtifact` real, ya cargada
    por la capa de filesystem (`functional_validation_service.py`) --
    este modulo permanece puro, nunca lee `artifacts/09-guardrails/` por
    su cuenta."""

    verdict: str
    repair_attempts: int


_EMPTY_GUARDRAIL_MAP: Mapping[str, GuardrailLookupEntry] = {}


def compute_case_applicability(
    case: GroundTruthCase, run_fixture_hashes: frozenset[str]
) -> Applicability:
    """APPLICABLE unicamente cuando TODAS las `fixtures[].sha256` del
    caso estan presentes en `run_fixture_hashes` -- un caso
    interprocedural (p. ej. BY_REFERENCE_OUTPUT, caller+callee) exige
    ambos archivos en el MISMO run, nunca uno solo."""
    required = {fixture.sha256 for fixture in case.fixtures}
    if required and required.issubset(run_fixture_hashes):
        return Applicability.APPLICABLE
    return Applicability.NOT_APPLICABLE


def _not_evaluated_result(case: GroundTruthCase) -> GroundTruthCaseResult:
    return GroundTruthCaseResult(
        case_id=case.case_id,
        kind=case.kind,
        program=case.program,
        applicability=Applicability.NOT_APPLICABLE,
        outcome=MatchOutcome.NOT_EVALUATED,
    )


def _matches_expected_rule(
    reference: UnifiedCandidateReference, *, rule: GroundTruthExpectedRule, program: str
) -> bool:
    if reference.rule_family != rule.rule_family:
        return False
    if (reference.program or "").strip() != program.strip():
        return False
    if rule.paragraph is not None and (reference.paragraph or "").strip() != rule.paragraph.strip():
        return False
    return True


def _matches_negative_case(reference: UnifiedCandidateReference, *, program: str) -> bool:
    if (reference.program or "").strip() != program.strip():
        return False
    return reference.rule_family != UnifiedRuleFamily.UNKNOWN


def _case_level_metrics_for_match(
    case: GroundTruthCase,
    expectation_results: Sequence[ExpectedRuleMatchResult],
    *,
    candidate_references_by_id: Mapping[str, UnifiedCandidateReference],
    guardrail_by_candidate_id: Mapping[str, GuardrailLookupEntry],
) -> CaseLevelMetrics | None:
    """Solo para casos POSITIVE con `outcome=MATCHED`. Asociacion
    inequivoca exige EXACTAMENTE una `expected_rules` (siempre cierto
    hoy en el catalogo real) y EXACTAMENTE un `unified_reference_id`
    satisfaciendola -- mas de uno, o cero, deja `NOT_EVALUATED` en vez
    de elegir arbitrariamente cual candidato "representa" el caso."""
    if len(case.expected_rules) != 1:
        return CaseLevelMetrics(
            status=CaseMetricStatus.NOT_EVALUATED,
            reason_code=CaseMetricReasonCode.CASE_ARTIFACT_TRACEABILITY_UNAVAILABLE,
        )
    matched_ids = expectation_results[0].matched_unified_reference_ids
    if len(matched_ids) != 1:
        return CaseLevelMetrics(
            status=CaseMetricStatus.NOT_EVALUATED,
            reason_code=CaseMetricReasonCode.CASE_ARTIFACT_TRACEABILITY_UNAVAILABLE,
        )
    reference = candidate_references_by_id.get(matched_ids[0])
    if reference is None:
        return CaseLevelMetrics(
            status=CaseMetricStatus.NOT_EVALUATED,
            reason_code=CaseMetricReasonCode.CASE_ARTIFACT_TRACEABILITY_UNAVAILABLE,
        )
    guardrail_entry = (
        guardrail_by_candidate_id.get(reference.source_candidate_id)
        if reference.source == CandidateSource.V1
        else None
    )
    return CaseLevelMetrics(
        status=CaseMetricStatus.EVALUATED,
        unified_reference_id=reference.unified_reference_id,
        source_candidate_id=reference.source_candidate_id,
        evidence_reference_count=len(reference.evidence_ids),
        provenance_reference_count=len(reference.provenance_references),
        guardrail_verdict=guardrail_entry.verdict if guardrail_entry else None,
        guardrail_repair_attempts=guardrail_entry.repair_attempts if guardrail_entry else None,
    )


def _evaluate_positive_case(
    case: GroundTruthCase,
    candidate_references: Sequence[UnifiedCandidateReference],
    *,
    guardrail_by_candidate_id: Mapping[str, GuardrailLookupEntry],
) -> GroundTruthCaseResult:
    expectation_results: list[ExpectedRuleMatchResult] = []
    for rule in case.expected_rules:
        matched_ids = sorted(
            reference.unified_reference_id
            for reference in candidate_references
            if _matches_expected_rule(reference, rule=rule, program=case.program)
        )
        outcome: Literal[MatchOutcome.MATCHED, MatchOutcome.MISSING] = (
            MatchOutcome.MATCHED if len(matched_ids) >= rule.minimum_count else MatchOutcome.MISSING
        )
        expectation_results.append(
            ExpectedRuleMatchResult(
                expectation_id=rule.expectation_id,
                rule_family=rule.rule_family,
                minimum_count=rule.minimum_count,
                matched_count=len(matched_ids),
                outcome=outcome,
                matched_unified_reference_ids=matched_ids,
            )
        )
    expectation_results.sort(key=lambda r: r.expectation_id)
    case_outcome = (
        MatchOutcome.MATCHED
        if all(r.outcome == MatchOutcome.MATCHED for r in expectation_results)
        else MatchOutcome.MISSING
    )
    case_metrics = None
    if case_outcome == MatchOutcome.MATCHED:
        candidate_references_by_id = {
            reference.unified_reference_id: reference for reference in candidate_references
        }
        case_metrics = _case_level_metrics_for_match(
            case,
            expectation_results,
            candidate_references_by_id=candidate_references_by_id,
            guardrail_by_candidate_id=guardrail_by_candidate_id,
        )
    return GroundTruthCaseResult(
        case_id=case.case_id,
        kind=GroundTruthCaseKind.POSITIVE,
        program=case.program,
        applicability=Applicability.APPLICABLE,
        outcome=case_outcome,
        expectation_results=expectation_results,
        case_metrics=case_metrics,
    )


def _evaluate_negative_case(
    case: GroundTruthCase, candidate_references: Sequence[UnifiedCandidateReference]
) -> GroundTruthCaseResult:
    unexpected_ids = sorted(
        reference.unified_reference_id
        for reference in candidate_references
        if _matches_negative_case(reference, program=case.program)
    )
    outcome = (
        MatchOutcome.UNEXPECTED_CANDIDATES if unexpected_ids else MatchOutcome.CONFIRMED_ABSENT
    )
    return GroundTruthCaseResult(
        case_id=case.case_id,
        kind=GroundTruthCaseKind.NEGATIVE,
        program=case.program,
        applicability=Applicability.APPLICABLE,
        outcome=outcome,
        unexpected_candidate_reference_ids=unexpected_ids,
    )


def _compute_metrics(case_results: Sequence[GroundTruthCaseResult]) -> FunctionalValidationMetrics:
    """Solo agrega casos `applicability=APPLICABLE` -- un caso
    NOT_APPLICABLE nunca contribuye TP/FP/FN/TN (checkpoint correctivo)."""
    true_positive_count = 0
    false_negative_count = 0
    false_positive_count = 0
    true_negative_count = 0
    for case in case_results:
        if case.applicability == Applicability.NOT_APPLICABLE:
            continue
        if case.kind == GroundTruthCaseKind.POSITIVE:
            true_positive_count += sum(
                1 for r in case.expectation_results if r.outcome == MatchOutcome.MATCHED
            )
            false_negative_count += sum(
                1 for r in case.expectation_results if r.outcome == MatchOutcome.MISSING
            )
        elif case.outcome == MatchOutcome.UNEXPECTED_CANDIDATES:
            false_positive_count += 1
        else:
            true_negative_count += 1

    precision_denominator = true_positive_count + false_positive_count
    precision = (
        true_positive_count / precision_denominator if precision_denominator > 0 else None
    )
    recall_denominator = true_positive_count + false_negative_count
    recall = true_positive_count / recall_denominator if recall_denominator > 0 else None
    if precision is None or recall is None:
        f1_score = None
    elif precision + recall == 0:
        f1_score = 0.0
    else:
        f1_score = 2 * precision * recall / (precision + recall)

    return FunctionalValidationMetrics(
        true_positive_count=true_positive_count,
        false_negative_count=false_negative_count,
        false_positive_count=false_positive_count,
        true_negative_count=true_negative_count,
        precision=precision,
        recall=recall,
        f1_score=f1_score,
    )


def validate_ground_truth(
    ground_truth: FunctionalGroundTruthSet,
    candidate_references: Sequence[UnifiedCandidateReference],
    *,
    run_id: str,
    source_package_hash: str,
    run_fixture_hashes: frozenset[str],
    guardrail_by_candidate_id: Mapping[str, GuardrailLookupEntry] = _EMPTY_GUARDRAIL_MAP,
    validation_source: ValidationSource = ValidationSource.PROMOTION_ASSESSMENT_SHADOW,
    artifact_chain_integrity: ArtifactChainIntegrityReport | None = None,
    final_rule_linkage: FinalRuleLinkageReport | None = None,
) -> FunctionalValidationReport:
    """Analizador puro: para cada `GroundTruthCase`, decide primero su
    `Applicability` (`compute_case_applicability`, contra
    `run_fixture_hashes` -- sha256 de los archivos REALMENTE ingeridos
    por este run, nunca "todos los runs"). Un caso NOT_APPLICABLE nunca
    llega a comparar `candidate_references` (evita que una deteccion de
    UN run "satisfaga" un caso de OTRO run/paquete). No filtra
    `candidate_references` por `program`/paquete de entrada -- el llamador
    (`pipeline/functional_validation_service.py`) es responsable de pasar
    exclusivamente los candidatos del run correcto.

    `coverage_status`/`pending_case_ids` se calculan sobre
    `ground_truth.cases` COMPLETO (el catalogo entero, nunca solo lo
    aplicable en este run) -- un caso NOT_APPLICABLE aqui queda
    pendiente para la evaluacion global del dataset (checkpoint
    correctivo, ver docstring del modulo)."""
    case_results = [
        (
            _evaluate_positive_case(
                case, candidate_references, guardrail_by_candidate_id=guardrail_by_candidate_id
            )
            if case.kind == GroundTruthCaseKind.POSITIVE
            else _evaluate_negative_case(case, candidate_references)
        )
        if compute_case_applicability(case, run_fixture_hashes) == Applicability.APPLICABLE
        else _not_evaluated_result(case)
        for case in ground_truth.cases
    ]
    case_results.sort(key=lambda c: c.case_id)

    any_applicable = any(c.applicability == Applicability.APPLICABLE for c in case_results)
    dataset_applicability = (
        Applicability.APPLICABLE if any_applicable else Applicability.NOT_APPLICABLE
    )

    required_cases = [c for c in case_results if c.kind == GroundTruthCaseKind.POSITIVE]
    forbidden_cases = [c for c in case_results if c.kind == GroundTruthCaseKind.NEGATIVE]
    required_case_count = len(required_cases)
    evaluated_required_case_count = sum(
        1 for c in required_cases if c.applicability == Applicability.APPLICABLE
    )
    forbidden_case_count = len(forbidden_cases)
    evaluated_forbidden_case_count = sum(
        1 for c in forbidden_cases if c.applicability == Applicability.APPLICABLE
    )
    pending_case_ids = sorted(
        c.case_id for c in case_results if c.applicability == Applicability.NOT_APPLICABLE
    )

    if dataset_applicability == Applicability.NOT_APPLICABLE:
        coverage_status = FunctionalDatasetCoverageStatus.NOT_EVALUATED
    elif pending_case_ids:
        coverage_status = FunctionalDatasetCoverageStatus.PARTIALLY_EVALUATED
    else:
        coverage_status = FunctionalDatasetCoverageStatus.COMPLETELY_EVALUATED

    if coverage_status != FunctionalDatasetCoverageStatus.COMPLETELY_EVALUATED:
        # Checkpoint correctivo: un reporte parcial (o sin ningun caso
        # aplicable) nunca puede afirmar PASS_ENGINEERING del dataset,
        # sin importar que los casos evaluados hayan resultado correctos.
        dataset_disposition = FunctionalDatasetDisposition.NOT_EVALUATED
    else:
        applicable_cases = [c for c in case_results if c.applicability == Applicability.APPLICABLE]
        required_satisfied = all(
            c.outcome == MatchOutcome.MATCHED
            for c in applicable_cases
            if c.kind == GroundTruthCaseKind.POSITIVE
        )
        forbidden_satisfied = all(
            c.outcome == MatchOutcome.CONFIRMED_ABSENT
            for c in applicable_cases
            if c.kind == GroundTruthCaseKind.NEGATIVE
        )
        dataset_disposition = (
            FunctionalDatasetDisposition.PASS_ENGINEERING
            if required_satisfied and forbidden_satisfied
            else FunctionalDatasetDisposition.FAIL_ENGINEERING
        )

    return FunctionalValidationReport(
        run_id=run_id,
        source_package_hash=source_package_hash,
        ground_truth_catalog_edition=ground_truth.catalog_edition,
        validation_source=validation_source,
        productive_candidate_count=len(candidate_references),
        dataset_applicability=dataset_applicability,
        coverage_status=coverage_status,
        required_case_count=required_case_count,
        evaluated_required_case_count=evaluated_required_case_count,
        forbidden_case_count=forbidden_case_count,
        evaluated_forbidden_case_count=evaluated_forbidden_case_count,
        pending_case_ids=pending_case_ids,
        dataset_disposition=dataset_disposition,
        case_results=case_results,
        metrics=_compute_metrics(case_results),
        artifact_chain_integrity=artifact_chain_integrity or _DEFAULT_ARTIFACT_CHAIN_INTEGRITY,
        final_rule_linkage=final_rule_linkage or _DEFAULT_FINAL_RULE_LINKAGE,
    )
