"""Tests de contrato del artefacto de ejecucion downstream del
artefacto unificado de candidatos en shadow mode (Fase 13 de la
ampliacion semantica, `feat/unified-shadow-downstream-pipeline`).
Construye instancias MINIMAS directamente contra el modelo Pydantic
(sin pasar por el executor) para aislar cada invariante -- ver
`tests/pipeline/test_unified_shadow_downstream_executor.py` para
pruebas de comportamiento end-to-end del executor real."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts.context_package import (
    BatchContext,
    CodeSliceEntry,
    Completeness,
    ContextPackage,
    ContextPackageCandidate,
    ContextPackageDecision,
    ContextPackageOperation,
    ContextPackageScope,
    DataContext,
    Effects,
    EvidenceEntry,
)
from altamira_extractor.contracts.enums import (
    BatchContextStatus,
    CandidateStatus,
    ClaimField,
    CompletenessStatus,
    EvidenceValidationStatus,
    GuardrailVerdict,
    InclusionReason,
    Severity,
)
from altamira_extractor.contracts.guardrail import GuardrailViolation
from altamira_extractor.contracts.rule_draft import Claim, RuleDraft
from altamira_extractor.contracts.unified_candidates_shadow import (
    UnifiedShadowComparisonKind,
    UnifiedShadowGroupStatus,
)
from altamira_extractor.contracts.unified_shadow_downstream import (
    UnifiedShadowContextPackageRecord,
    UnifiedShadowDownstreamArtifact,
    UnifiedShadowDownstreamDisposition,
    UnifiedShadowDownstreamExecutionStatus,
    UnifiedShadowDownstreamGroupResult,
    UnifiedShadowDownstreamSummary,
    UnifiedShadowDraftProvider,
    UnifiedShadowGuardrailRecord,
    UnifiedShadowGuardrailReportView,
    UnifiedShadowGuardrailStatus,
    UnifiedShadowRuleDraftRecord,
)

HASH = "a" * 64
RUN_ID = "20260101T000000000000-aaaaaaaa"
GROUP_ID = "group::candidate-1"


def _context_package() -> ContextPackage:
    return ContextPackage(
        candidate=ContextPackageCandidate(
            candidate_id=GROUP_ID,
            decision_id="decision::1",
            detector_id="unified-shadow-downstream-executor",
            detector_version="1.0",
            detector_score=1.0,
            status=CandidateStatus.DETECTED_CANDIDATE,
        ),
        scope=ContextPackageScope(
            country="AR",
            application="App1",
            operation=ContextPackageOperation(logical_name="OP1", description=None),
            program="CALLER10",
            program_version="1.0",
            paragraph="MAIN",
            source_file="CALLER10.cbl",
            line_start=10,
            line_end=20,
            source_package_hash=HASH,
        ),
        code_slice=[
            CodeSliceEntry(
                paragraph_id="paragraph::1",
                paragraph="MAIN",
                source_file="CALLER10.cbl",
                source_text="MOVE 'R001' TO WS-COD-RETORNO.",
                line_start=10,
                line_end=20,
                inclusion_reason=InclusionReason.CANDIDATE,
                evidence_ids=["E001"],
            )
        ],
        data_context=DataContext(),
        decision=ContextPackageDecision(
            expression="WS-SALDO < 0",
            normalized_expression="WS-SALDO < 0",
            operands=[],
            rule_type=None,
            outcome_code="R001",
            evidence_ids=["E001"],
        ),
        effects=Effects(),
        batch_context=BatchContext(status=BatchContextStatus.NOT_AVAILABLE),
        domain_glossary=[],
        evidence=[
            EvidenceEntry(
                evidence_id="E001",
                kind="unified_shadow_group_evidence",
                source_file="CALLER10.cbl",
                source_package_hash=HASH,
            )
        ],
        completeness=Completeness(
            D1=CompletenessStatus.COMPLETE,
            D2=CompletenessStatus.COMPLETE,
            D3=CompletenessStatus.NOT_AVAILABLE,
            D4=CompletenessStatus.COMPLETE,
            D5=CompletenessStatus.NOT_AVAILABLE,
            D6=CompletenessStatus.NOT_AVAILABLE,
            D7=CompletenessStatus.NOT_AVAILABLE,
        ),
    )


def _rule_draft() -> RuleDraft:
    return RuleDraft(
        title="Shadow rule for CALLER10/MAIN",
        context="CALLER10::MAIN",
        statement="Cuando WS-SALDO < 0, se observa R001.",
        condition="WS-SALDO < 0",
        parameters=[],
        effect="outcome_code=R001",
        parameter_source=None,
        traceability=[GROUP_ID],
        limitations=["Borrador shadow"],
        claims=[
            Claim(
                claim_id="claim::1",
                field=ClaimField.CONDITION,
                evidence_paths=["$.decision"],
                evidence_ids=["E001"],
            )
        ],
        evidence_validation_status=EvidenceValidationStatus.EVIDENCE_VALIDATED,
    )


def _guardrail_report(
    *, verdict: GuardrailVerdict = GuardrailVerdict.EVIDENCE_VALIDATED
) -> UnifiedShadowGuardrailReportView:
    violations = (
        []
        if verdict == GuardrailVerdict.EVIDENCE_VALIDATED
        else [
            GuardrailViolation(
                violation_id="violation::1",
                rule="EVIDENCE_REQUIRED",
                message="evidencia faltante",
                severity=Severity.ERROR,
            )
        ]
    )
    return UnifiedShadowGuardrailReportView(
        candidate_id=GROUP_ID,
        verdict=verdict,
        violations=violations,
        repair_attempts=0,
        source_package_hash=HASH,
    )


def _context_record() -> UnifiedShadowContextPackageRecord:
    package = _context_package()
    return UnifiedShadowContextPackageRecord(
        record_id=f"context::{GROUP_ID}",
        group_id=GROUP_ID,
        member_ids=["member::1"],
        source_candidate_ids=["source::1"],
        review_decision_ids=["decision::1"],
        context_package_hash=HASH,
        context_package=package,
        evidence_ids=["E001"],
        evidence_aliases=["E001"],
        provenance_references=["provenance::1"],
    )


def _draft_record(
    *, context_record_id: str = f"context::{GROUP_ID}"
) -> UnifiedShadowRuleDraftRecord:
    return UnifiedShadowRuleDraftRecord(
        record_id=f"draft::{GROUP_ID}",
        group_id=GROUP_ID,
        context_package_record_id=context_record_id,
        provider=UnifiedShadowDraftProvider.DETERMINISTIC_FAKE,
        provider_response_hash=HASH,
        rule_draft_hash=HASH,
        rule_draft=_rule_draft(),
        evidence_aliases_used=["E001"],
    )


def _guardrail_record(
    *,
    status: UnifiedShadowGuardrailStatus = UnifiedShadowGuardrailStatus.PASSED,
    draft_record_id: str = f"draft::{GROUP_ID}",
) -> UnifiedShadowGuardrailRecord:
    verdict = (
        GuardrailVerdict.EVIDENCE_VALIDATED
        if status == UnifiedShadowGuardrailStatus.PASSED
        else GuardrailVerdict.REJECTED
    )
    return UnifiedShadowGuardrailRecord(
        record_id=f"guardrail::{GROUP_ID}",
        group_id=GROUP_ID,
        rule_draft_record_id=draft_record_id,
        status=status,
        guardrail_report_hash=HASH,
        guardrail_result=_guardrail_report(verdict=verdict),
        blocking_reasons=[] if status == UnifiedShadowGuardrailStatus.PASSED else ["rule::v1"],
    )


_Status = UnifiedShadowDownstreamExecutionStatus


def _group_result(
    *,
    execution_status: _Status = _Status.EXECUTED,
    eligible: bool = True,
    context_package_record_id: str | None = f"context::{GROUP_ID}",
    rule_draft_record_id: str | None = f"draft::{GROUP_ID}",
    guardrail_record_id: str | None = f"guardrail::{GROUP_ID}",
    diagnostics: list[str] | None = None,
) -> UnifiedShadowDownstreamGroupResult:
    return UnifiedShadowDownstreamGroupResult(
        group_id=GROUP_ID,
        execution_status=execution_status,
        downstream_shadow_eligible=eligible,
        comparison_to_v1=UnifiedShadowComparisonKind.NOT_IN_BASELINE,
        group_status=UnifiedShadowGroupStatus.VALID,
        member_ids=["member::1"],
        source_candidate_ids=["source::1"],
        review_decision_ids=["decision::1"],
        context_package_record_id=context_package_record_id,
        rule_draft_record_id=rule_draft_record_id,
        guardrail_record_id=guardrail_record_id,
        diagnostics=diagnostics or [],
    )


def _summary(**overrides: object) -> UnifiedShadowDownstreamSummary:
    base = {
        "validation_group_count": 1,
        "downstream_eligible_group_count": 1,
        "executed_group_count": 1,
        "skipped_group_count": 0,
        "context_package_count": 1,
        "rule_draft_count": 1,
        "guardrail_passed_count": 1,
        "guardrail_rejected_count": 0,
        "technical_failure_count": 0,
        "counts_by_execution_status": {UnifiedShadowDownstreamExecutionStatus.EXECUTED: 1},
        "counts_by_guardrail_status": {UnifiedShadowGuardrailStatus.PASSED: 1},
    }
    base.update(overrides)
    return UnifiedShadowDownstreamSummary(**base)  # type: ignore[arg-type]


def _artifact(**overrides: object) -> UnifiedShadowDownstreamArtifact:
    base: dict[str, object] = {
        "run_id": RUN_ID,
        "source_package_hash": HASH,
        "unified_candidates_shadow_hash": HASH,
        "validation_report_hash": HASH,
        "candidate_v1_artifact_hash": HASH,
        "assessment_artifact_hash": HASH,
        "review_package_hash": HASH,
        "promotion_plan_hash": HASH,
        "provider": UnifiedShadowDraftProvider.DETERMINISTIC_FAKE,
        "disposition": UnifiedShadowDownstreamDisposition.COMPLETED,
        "summary": _summary(),
        "context_packages": [_context_record()],
        "rule_drafts": [_draft_record()],
        "guardrail_results": [_guardrail_record()],
        "group_results": [_group_result()],
    }
    base.update(overrides)
    return UnifiedShadowDownstreamArtifact(**base)  # type: ignore[arg-type]


class TestHappyPath:
    def test_minimal_completed_artifact_is_valid(self) -> None:
        artifact = _artifact()
        assert artifact.disposition == UnifiedShadowDownstreamDisposition.COMPLETED

    def test_not_executed_artifact_with_zero_eligible_groups_is_valid(self) -> None:
        artifact = _artifact(
            disposition=UnifiedShadowDownstreamDisposition.NOT_EXECUTED,
            summary=_summary(
                downstream_eligible_group_count=0,
                executed_group_count=0,
                skipped_group_count=1,
                context_package_count=0,
                rule_draft_count=0,
                guardrail_passed_count=0,
                counts_by_execution_status={
                    UnifiedShadowDownstreamExecutionStatus.SKIPPED_NOT_ELIGIBLE: 1
                },
                counts_by_guardrail_status={},
            ),
            context_packages=[],
            rule_drafts=[],
            guardrail_results=[],
            group_results=[
                _group_result(
                    execution_status=UnifiedShadowDownstreamExecutionStatus.SKIPPED_NOT_ELIGIBLE,
                    eligible=False,
                    context_package_record_id=None,
                    rule_draft_record_id=None,
                    guardrail_record_id=None,
                )
            ],
        )
        assert artifact.disposition == UnifiedShadowDownstreamDisposition.NOT_EXECUTED


class TestProviderInvariant:
    def test_provider_must_always_be_deterministic_fake(self) -> None:
        with pytest.raises(ValidationError):
            _artifact(provider="REAL_LLM")


class TestRecordIdUniquenessAndOrder:
    def test_duplicate_context_package_record_id_rejected(self) -> None:
        record = _context_record()
        with pytest.raises(ValidationError):
            _artifact(context_packages=[record, record])

    def test_group_results_must_be_ordered_by_group_id(self) -> None:
        gr_b = _group_result()
        gr_a = _group_result().model_copy(update={"group_id": "group::a"})
        with pytest.raises(ValidationError):
            UnifiedShadowDownstreamArtifact(
                run_id=RUN_ID,
                source_package_hash=HASH,
                unified_candidates_shadow_hash=HASH,
                validation_report_hash=HASH,
                candidate_v1_artifact_hash=HASH,
                assessment_artifact_hash=HASH,
                review_package_hash=HASH,
                promotion_plan_hash=HASH,
                provider=UnifiedShadowDraftProvider.DETERMINISTIC_FAKE,
                disposition=UnifiedShadowDownstreamDisposition.NOT_EXECUTED,
                summary=_summary(
                    validation_group_count=2,
                    downstream_eligible_group_count=0,
                    executed_group_count=0,
                    skipped_group_count=2,
                    context_package_count=0,
                    rule_draft_count=0,
                    guardrail_passed_count=0,
                    counts_by_execution_status={
                        UnifiedShadowDownstreamExecutionStatus.SKIPPED_NOT_ELIGIBLE: 2
                    },
                    counts_by_guardrail_status={},
                ),
                context_packages=[],
                rule_drafts=[],
                guardrail_results=[],
                group_results=[gr_b, gr_a],
            )

    def test_duplicate_group_id_in_group_results_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _artifact(group_results=[_group_result(), _group_result()])


class TestReferentialIntegrity:
    def test_context_package_referencing_unknown_group_rejected(self) -> None:
        record = _context_record().model_copy(update={"group_id": "group::unknown"})
        with pytest.raises(ValidationError):
            _artifact(context_packages=[record])

    def test_rule_draft_referencing_unknown_context_package_rejected(self) -> None:
        record = _draft_record(context_record_id="context::unknown")
        with pytest.raises(ValidationError):
            _artifact(rule_drafts=[record])

    def test_guardrail_referencing_unknown_rule_draft_rejected(self) -> None:
        record = _guardrail_record(draft_record_id="draft::unknown")
        with pytest.raises(ValidationError):
            _artifact(guardrail_results=[record])

    def test_group_result_referencing_unknown_context_package_rejected(self) -> None:
        gr = _group_result(context_package_record_id="context::unknown")
        with pytest.raises(ValidationError):
            _artifact(group_results=[gr])


class TestGroupResultInvariants:
    def test_executed_requires_eligible(self) -> None:
        with pytest.raises(ValidationError):
            _group_result(eligible=False)

    def test_not_eligible_requires_skipped_status(self) -> None:
        with pytest.raises(ValidationError):
            _group_result(
                eligible=False,
                execution_status=UnifiedShadowDownstreamExecutionStatus.CONTEXT_ASSEMBLY_FAILED,
            )

    def test_executed_requires_context_package_record(self) -> None:
        with pytest.raises(ValidationError):
            _group_result(context_package_record_id=None)

    def test_guardrail_rejected_requires_guardrail_record(self) -> None:
        with pytest.raises(ValidationError):
            _group_result(
                execution_status=UnifiedShadowDownstreamExecutionStatus.GUARDRAIL_REJECTED,
                guardrail_record_id=None,
            )

    def test_technical_failure_requires_diagnostics(self) -> None:
        with pytest.raises(ValidationError):
            _group_result(
                execution_status=UnifiedShadowDownstreamExecutionStatus.TECHNICAL_FAILURE,
                context_package_record_id=None,
                rule_draft_record_id=None,
                guardrail_record_id=None,
                diagnostics=[],
            )


class TestGuardrailRecordInvariant:
    def test_rejected_requires_blocking_reasons(self) -> None:
        with pytest.raises(ValidationError):
            UnifiedShadowGuardrailRecord(
                record_id=f"guardrail::{GROUP_ID}",
                group_id=GROUP_ID,
                rule_draft_record_id=f"draft::{GROUP_ID}",
                status=UnifiedShadowGuardrailStatus.REJECTED,
                guardrail_report_hash=HASH,
                guardrail_result=_guardrail_report(verdict=GuardrailVerdict.REJECTED),
                blocking_reasons=[],
            )


class TestDispositionInvariants:
    def test_completed_requires_at_least_one_eligible_group(self) -> None:
        with pytest.raises(ValidationError):
            _artifact(
                disposition=UnifiedShadowDownstreamDisposition.COMPLETED,
                group_results=[
                    _group_result(
                        eligible=False,
                        execution_status=UnifiedShadowDownstreamExecutionStatus.SKIPPED_NOT_ELIGIBLE,
                        context_package_record_id=None,
                        rule_draft_record_id=None,
                        guardrail_record_id=None,
                    )
                ],
                context_packages=[],
                rule_drafts=[],
                guardrail_results=[],
                summary=_summary(
                    downstream_eligible_group_count=0,
                    executed_group_count=0,
                    skipped_group_count=1,
                    context_package_count=0,
                    rule_draft_count=0,
                    guardrail_passed_count=0,
                    counts_by_execution_status={
                        UnifiedShadowDownstreamExecutionStatus.SKIPPED_NOT_ELIGIBLE: 1
                    },
                    counts_by_guardrail_status={},
                ),
            )

    def test_completed_requires_all_eligible_groups_executed(self) -> None:
        gr = _group_result(
            execution_status=UnifiedShadowDownstreamExecutionStatus.GUARDRAIL_REJECTED
        )
        with pytest.raises(ValidationError):
            _artifact(group_results=[gr], disposition=UnifiedShadowDownstreamDisposition.COMPLETED)

    def test_completed_with_rejections_requires_at_least_one_rejection(self) -> None:
        with pytest.raises(ValidationError):
            _artifact(disposition=UnifiedShadowDownstreamDisposition.COMPLETED_WITH_REJECTIONS)

    def test_completed_with_rejections_forbids_hard_pipeline_failures(self) -> None:
        gr_rejected = _group_result(
            execution_status=UnifiedShadowDownstreamExecutionStatus.GUARDRAIL_REJECTED
        )
        gr_failed = _group_result(
            execution_status=UnifiedShadowDownstreamExecutionStatus.CONTEXT_ASSEMBLY_FAILED,
            context_package_record_id=None,
            rule_draft_record_id=None,
            guardrail_record_id=None,
            diagnostics=["fallo de ensamblaje"],
        ).model_copy(update={"group_id": "group::candidate-2"})
        with pytest.raises(ValidationError):
            UnifiedShadowDownstreamArtifact(
                run_id=RUN_ID,
                source_package_hash=HASH,
                unified_candidates_shadow_hash=HASH,
                validation_report_hash=HASH,
                candidate_v1_artifact_hash=HASH,
                assessment_artifact_hash=HASH,
                review_package_hash=HASH,
                promotion_plan_hash=HASH,
                provider=UnifiedShadowDraftProvider.DETERMINISTIC_FAKE,
                disposition=UnifiedShadowDownstreamDisposition.COMPLETED_WITH_REJECTIONS,
                summary=_summary(
                    validation_group_count=2,
                    executed_group_count=1,
                    skipped_group_count=1,
                    guardrail_passed_count=0,
                    guardrail_rejected_count=1,
                    counts_by_execution_status={
                        UnifiedShadowDownstreamExecutionStatus.GUARDRAIL_REJECTED: 1,
                        UnifiedShadowDownstreamExecutionStatus.CONTEXT_ASSEMBLY_FAILED: 1,
                    },
                    counts_by_guardrail_status={UnifiedShadowGuardrailStatus.REJECTED: 1},
                ),
                context_packages=[_context_record()],
                rule_drafts=[_draft_record()],
                guardrail_results=[_guardrail_record(status=UnifiedShadowGuardrailStatus.REJECTED)],
                group_results=sorted([gr_rejected, gr_failed], key=lambda g: g.group_id),
            )

    def test_blocked_requires_a_hard_pipeline_failure(self) -> None:
        with pytest.raises(ValidationError):
            _artifact(disposition=UnifiedShadowDownstreamDisposition.BLOCKED)

    def test_blocked_accepts_context_assembly_failed_without_technical_failure(self) -> None:
        gr = _group_result(
            execution_status=UnifiedShadowDownstreamExecutionStatus.CONTEXT_ASSEMBLY_FAILED,
            context_package_record_id=None,
            rule_draft_record_id=None,
            guardrail_record_id=None,
            diagnostics=["fallo de ensamblaje"],
        )
        artifact = _artifact(
            disposition=UnifiedShadowDownstreamDisposition.BLOCKED,
            group_results=[gr],
            context_packages=[],
            rule_drafts=[],
            guardrail_results=[],
            summary=_summary(
                executed_group_count=0,
                skipped_group_count=1,
                context_package_count=0,
                rule_draft_count=0,
                guardrail_passed_count=0,
                counts_by_execution_status={
                    UnifiedShadowDownstreamExecutionStatus.CONTEXT_ASSEMBLY_FAILED: 1
                },
                counts_by_guardrail_status={},
            ),
        )
        assert artifact.disposition == UnifiedShadowDownstreamDisposition.BLOCKED

    def test_not_executed_requires_zero_eligible_groups(self) -> None:
        with pytest.raises(ValidationError):
            _artifact(disposition=UnifiedShadowDownstreamDisposition.NOT_EXECUTED)


class TestSummaryReconciliation:
    def test_summary_context_package_count_must_match(self) -> None:
        with pytest.raises(ValidationError):
            _artifact(summary=_summary(context_package_count=2))

    def test_summary_execution_status_counts_must_match(self) -> None:
        with pytest.raises(ValidationError):
            _artifact(
                summary=_summary(
                    counts_by_execution_status={
                        UnifiedShadowDownstreamExecutionStatus.GUARDRAIL_REJECTED: 1
                    }
                )
            )


class TestListOrderingInvariants:
    def test_context_package_record_lists_must_be_sorted_and_unique(self) -> None:
        package = _context_package()
        with pytest.raises(ValidationError):
            UnifiedShadowContextPackageRecord(
                record_id=f"context::{GROUP_ID}",
                group_id=GROUP_ID,
                member_ids=["member::1"],
                source_candidate_ids=["source::1"],
                review_decision_ids=["decision::1"],
                context_package_hash=HASH,
                context_package=package,
                evidence_ids=["E002", "E001"],
            )

    def test_diagnostics_must_be_sorted_and_unique(self) -> None:
        with pytest.raises(ValidationError):
            _artifact(diagnostics=["b", "a"])
