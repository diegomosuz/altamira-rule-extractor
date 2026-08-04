"""Ejecutor PURO de la cadena downstream completa (Fase 13 Parte 9,
`feat/unified-shadow-downstream-pipeline`).

Orquesta, EXCLUSIVAMENTE para los grupos con elegibilidad EFECTIVA
(`UnifiedShadowGroupValidation.downstream_shadow_eligible=True` Y
`UnifiedShadowValidationReport.disposition` en
`{QUALIFIED_FOR_DOWNSTREAM_SHADOW, QUALIFIED_WITH_WARNINGS}`):
adaptador (Parte 5) -> ensamblador de contexto (Parte 6) -> generador
de draft (Parte 7) -> guardrails (Parte 8). Un grupo cuya disposicion
de validacion global no es QUALIFIED_* NUNCA se ejecuta aqui, aunque su
propia `UnifiedShadowGroupValidation.downstream_shadow_eligible` sea
`True` (Parte 14, casos A/B): el artefacto completo queda
`NOT_EXECUTED`, satisfaciendo el invariante 20 del contrato sin
necesitar un caso especial.

El fallo AISLADO de un grupo (ensamblaje de contexto o generacion de
draft, ambos tipados: `ContextAssemblyError`/`DraftGenerationError`)
nunca afecta a otros grupos ni interrumpe la ejecucion completa -- se
registra como `CONTEXT_ASSEMBLY_FAILED`/`DRAFT_GENERATION_FAILED` en el
`GroupResult` de ESE grupo. Un fallo de CONSISTENCIA GLOBAL entre las
fuentes recibidas (run_id/hash inconsistentes, referencia rota entre
`UnifiedShadowValidationReport` y `UnifiedCandidatesShadowArtifact`) es
tecnico y GLOBAL -- nunca aislable por grupo -- y se propaga como
`UnifiedShadowDownstreamExecutorError` (el llamador, Parte 10, lo
traduce en salida no-cero sin escribir artefacto parcial).

Puro: sin filesystem, sin red, sin Neo4j, nunca muta sus argumentos. El
unico proveedor admitido es `DeterministicFakeDraftProvider` (Parte 7)
-- nunca un LLM real."""

from __future__ import annotations

import hashlib
from typing import Any

import jsonschema

from ..contracts.enums import GuardrailVerdict
from ..contracts.guardrail import GuardrailViolation
from ..contracts.semantic_graph import SemanticGraph
from ..contracts.unified_candidates_shadow import UnifiedCandidatesShadowArtifact
from ..contracts.unified_shadow_downstream import (
    UnifiedShadowContextPackageRecord,
    UnifiedShadowDownstreamArtifact,
    UnifiedShadowDownstreamDisposition,
    UnifiedShadowDownstreamExecutionStatus,
    UnifiedShadowDownstreamGroupResult,
    UnifiedShadowDownstreamSummary,
    UnifiedShadowDraftProvider,
    UnifiedShadowGuardrailRecord,
    UnifiedShadowGuardrailStatus,
    UnifiedShadowRuleDraftRecord,
)
from ..contracts.unified_shadow_validation import (
    UnifiedShadowValidationDisposition,
    UnifiedShadowValidationReport,
)
from .evidence_catalog import build_evidence_catalog
from .unified_shadow_context_adapter import adapt_group_to_context_view
from .unified_shadow_context_assembler import (
    ContextAssemblyError,
    assemble_shadow_context_package,
)
from .unified_shadow_draft_generator import (
    DeterministicFakeDraftProvider,
    DraftGenerationError,
    generate_shadow_rule_draft,
)
from .unified_shadow_guardrail_runner import run_shadow_guardrails, to_shadow_view

_QUALIFIED_VALIDATION_DISPOSITIONS = frozenset(
    {
        UnifiedShadowValidationDisposition.QUALIFIED_FOR_DOWNSTREAM_SHADOW,
        UnifiedShadowValidationDisposition.QUALIFIED_WITH_WARNINGS,
    }
)

_HARD_FAILURE_STATUSES = frozenset(
    {
        UnifiedShadowDownstreamExecutionStatus.CONTEXT_ASSEMBLY_FAILED,
        UnifiedShadowDownstreamExecutionStatus.DRAFT_GENERATION_FAILED,
        UnifiedShadowDownstreamExecutionStatus.TECHNICAL_FAILURE,
    }
)


class UnifiedShadowDownstreamExecutorError(Exception):
    """Fallo tecnico GLOBAL (nunca aislable por grupo): las fuentes
    recibidas son inconsistentes entre si (run_id/hash/referencia
    rota) -- el llamador nunca debe persistir un artefacto parcial."""


def _stable_hash(model: Any) -> str:
    return hashlib.sha256(model.to_stable_json().encode("utf-8")).hexdigest()


def _validate_sources(
    *,
    run_id: str,
    unified_shadow: UnifiedCandidatesShadowArtifact,
    validation_report: UnifiedShadowValidationReport,
) -> None:
    if unified_shadow.run_id != run_id or validation_report.run_id != run_id:
        raise UnifiedShadowDownstreamExecutorError(
            "run_id inconsistente entre unified_shadow, validation_report y el run_id esperado"
        )
    if validation_report.source_package_hash != unified_shadow.source_package_hash:
        raise UnifiedShadowDownstreamExecutorError(
            "source_package_hash inconsistente entre unified_shadow y validation_report"
        )
    for field_name in (
        "candidate_v1_artifact_hash",
        "assessment_artifact_hash",
        "review_package_hash",
        "promotion_plan_hash",
    ):
        if getattr(unified_shadow, field_name) != getattr(validation_report, field_name):
            raise UnifiedShadowDownstreamExecutorError(
                f"{field_name} inconsistente entre unified_shadow y validation_report"
            )
    actual_unified_shadow_hash = _stable_hash(unified_shadow)
    if validation_report.unified_candidates_shadow_hash != actual_unified_shadow_hash:
        raise UnifiedShadowDownstreamExecutorError(
            "unified_candidates_shadow_hash del validation_report no coincide con el hash real "
            "del artefacto unified_shadow recibido (hash obsoleto)"
        )


def _blocking_reasons(violations: list[GuardrailViolation]) -> list[str]:
    return sorted({f"{v.rule}::{v.violation_id}::{v.message}" for v in violations})


def run_unified_shadow_downstream(
    *,
    run_id: str,
    unified_shadow: UnifiedCandidatesShadowArtifact,
    validation_report: UnifiedShadowValidationReport,
    semantic_graph: SemanticGraph,
    provider: DeterministicFakeDraftProvider,
    schema_validator: jsonschema.protocols.Validator,
) -> UnifiedShadowDownstreamArtifact:
    """Punto de entrada puro. Lanza `UnifiedShadowDownstreamExecutorError`
    (tecnico, global, nunca aislable) si las fuentes son inconsistentes
    entre si. En cualquier otro caso SIEMPRE retorna un artefacto
    valido -- incluyendo `NOT_EXECUTED` cuando `validation_report.
    disposition` no es QUALIFIED_* o cuando no hay grupos elegibles."""
    _validate_sources(
        run_id=run_id, unified_shadow=unified_shadow, validation_report=validation_report
    )

    members_by_id = {m.member_id: m for m in unified_shadow.shadow_members}
    shadow_groups_by_id = {g.unified_shadow_candidate_id: g for g in unified_shadow.shadow_groups}
    disposition_is_qualified = validation_report.disposition in _QUALIFIED_VALIDATION_DISPOSITIONS

    group_results: list[UnifiedShadowDownstreamGroupResult] = []
    context_packages: list[UnifiedShadowContextPackageRecord] = []
    rule_drafts: list[UnifiedShadowRuleDraftRecord] = []
    guardrail_results: list[UnifiedShadowGuardrailRecord] = []

    for group_validation in sorted(validation_report.group_validations, key=lambda gv: gv.group_id):
        group_id = group_validation.group_id
        try:
            members = [members_by_id[member_id] for member_id in group_validation.member_ids]
        except KeyError as exc:
            raise UnifiedShadowDownstreamExecutorError(
                f"grupo {group_id!r} referencia un member_id ausente en unified_shadow: {exc}"
            ) from exc
        member_ids = sorted(group_validation.member_ids)
        source_candidate_ids = sorted({m.source_candidate_id for m in members})
        review_decision_ids = sorted({m.review_decision_id for m in members})

        effective_eligible = (
            group_validation.downstream_shadow_eligible and disposition_is_qualified
        )

        if not effective_eligible:
            group_results.append(
                UnifiedShadowDownstreamGroupResult(
                    group_id=group_id,
                    execution_status=UnifiedShadowDownstreamExecutionStatus.SKIPPED_NOT_ELIGIBLE,
                    downstream_shadow_eligible=False,
                    comparison_to_v1=group_validation.comparison_to_v1,
                    group_status=group_validation.group_status,
                    member_ids=member_ids,
                    source_candidate_ids=source_candidate_ids,
                    review_decision_ids=review_decision_ids,
                )
            )
            continue

        shadow_group = shadow_groups_by_id.get(group_id)
        if shadow_group is None:
            raise UnifiedShadowDownstreamExecutorError(
                f"grupo {group_id!r} elegible en validation_report pero ausente en unified_shadow"
            )

        view = adapt_group_to_context_view(shadow_group, members_by_id=members_by_id)

        execution_status: UnifiedShadowDownstreamExecutionStatus
        diagnostics: list[str] = []
        context_record: UnifiedShadowContextPackageRecord | None = None
        draft_record: UnifiedShadowRuleDraftRecord | None = None
        guardrail_record: UnifiedShadowGuardrailRecord | None = None
        blocking_reasons: list[str] = []

        try:
            package = assemble_shadow_context_package(
                view,
                semantic_graph=semantic_graph,
                source_package_hash=unified_shadow.source_package_hash,
            )
        except ContextAssemblyError as exc:
            execution_status = UnifiedShadowDownstreamExecutionStatus.CONTEXT_ASSEMBLY_FAILED
            diagnostics = [str(exc)]
        else:
            catalog = build_evidence_catalog(package)
            context_record = UnifiedShadowContextPackageRecord(
                record_id=f"context::{group_id}",
                group_id=group_id,
                member_ids=member_ids,
                source_candidate_ids=source_candidate_ids,
                review_decision_ids=review_decision_ids,
                context_package_hash=_stable_hash(package),
                context_package=package,
                evidence_ids=sorted(view.evidence_ids),
                evidence_aliases=sorted({entry.alias for entry in catalog.entries}),
                provenance_references=sorted(view.provenance_references),
            )
            context_packages.append(context_record)

            try:
                draft_result = generate_shadow_rule_draft(
                    package=package, provider=provider, schema_validator=schema_validator
                )
            except DraftGenerationError as exc:
                execution_status = UnifiedShadowDownstreamExecutionStatus.DRAFT_GENERATION_FAILED
                diagnostics = [str(exc)]
            else:
                draft_record = UnifiedShadowRuleDraftRecord(
                    record_id=f"draft::{group_id}",
                    group_id=group_id,
                    context_package_record_id=context_record.record_id,
                    provider=UnifiedShadowDraftProvider.DETERMINISTIC_FAKE,
                    provider_response_hash=draft_result.payload_hash,
                    rule_draft_hash=draft_result.rule_draft_hash,
                    rule_draft=draft_result.rule_draft,
                    evidence_aliases_used=list(draft_result.evidence_aliases_used),
                    evidence_aliases_unresolved=list(draft_result.evidence_aliases_unresolved),
                )
                rule_drafts.append(draft_record)

                guardrail_report = run_shadow_guardrails(
                    draft_result.rule_draft,
                    package,
                    group_id=group_id,
                    source_package_hash=unified_shadow.source_package_hash,
                )
                guardrail_view = to_shadow_view(guardrail_report)
                passed = guardrail_view.verdict == GuardrailVerdict.EVIDENCE_VALIDATED
                if not passed:
                    blocking_reasons = _blocking_reasons(guardrail_view.violations)
                guardrail_record = UnifiedShadowGuardrailRecord(
                    record_id=f"guardrail::{group_id}",
                    group_id=group_id,
                    rule_draft_record_id=draft_record.record_id,
                    status=(
                        UnifiedShadowGuardrailStatus.PASSED
                        if passed
                        else UnifiedShadowGuardrailStatus.REJECTED
                    ),
                    guardrail_report_hash=_stable_hash(guardrail_view),
                    guardrail_result=guardrail_view,
                    blocking_reasons=blocking_reasons,
                )
                guardrail_results.append(guardrail_record)
                execution_status = (
                    UnifiedShadowDownstreamExecutionStatus.EXECUTED
                    if passed
                    else UnifiedShadowDownstreamExecutionStatus.GUARDRAIL_REJECTED
                )

        group_results.append(
            UnifiedShadowDownstreamGroupResult(
                group_id=group_id,
                execution_status=execution_status,
                downstream_shadow_eligible=True,
                comparison_to_v1=group_validation.comparison_to_v1,
                group_status=group_validation.group_status,
                member_ids=member_ids,
                source_candidate_ids=source_candidate_ids,
                review_decision_ids=review_decision_ids,
                context_package_record_id=context_record.record_id if context_record else None,
                rule_draft_record_id=draft_record.record_id if draft_record else None,
                guardrail_record_id=guardrail_record.record_id if guardrail_record else None,
                blocking_reasons=blocking_reasons,
                diagnostics=diagnostics,
            )
        )

    disposition = _derive_disposition(group_results)
    summary = _build_summary(group_results, context_packages, rule_drafts, guardrail_results)

    return UnifiedShadowDownstreamArtifact(
        run_id=run_id,
        source_package_hash=unified_shadow.source_package_hash,
        unified_candidates_shadow_hash=validation_report.unified_candidates_shadow_hash,
        validation_report_hash=_stable_hash(validation_report),
        candidate_v1_artifact_hash=validation_report.candidate_v1_artifact_hash,
        assessment_artifact_hash=validation_report.assessment_artifact_hash,
        review_package_hash=validation_report.review_package_hash,
        promotion_plan_hash=validation_report.promotion_plan_hash,
        provider=UnifiedShadowDraftProvider.DETERMINISTIC_FAKE,
        disposition=disposition,
        summary=summary,
        context_packages=sorted(context_packages, key=lambda r: r.record_id),
        rule_drafts=sorted(rule_drafts, key=lambda r: r.record_id),
        guardrail_results=sorted(guardrail_results, key=lambda r: r.record_id),
        group_results=sorted(group_results, key=lambda r: r.group_id),
    )


def _derive_disposition(
    group_results: list[UnifiedShadowDownstreamGroupResult],
) -> UnifiedShadowDownstreamDisposition:
    eligible = [gr for gr in group_results if gr.downstream_shadow_eligible]
    if not eligible:
        return UnifiedShadowDownstreamDisposition.NOT_EXECUTED
    if any(gr.execution_status in _HARD_FAILURE_STATUSES for gr in eligible):
        return UnifiedShadowDownstreamDisposition.BLOCKED
    if any(
        gr.execution_status == UnifiedShadowDownstreamExecutionStatus.GUARDRAIL_REJECTED
        for gr in eligible
    ):
        return UnifiedShadowDownstreamDisposition.COMPLETED_WITH_REJECTIONS
    return UnifiedShadowDownstreamDisposition.COMPLETED


def _build_summary(
    group_results: list[UnifiedShadowDownstreamGroupResult],
    context_packages: list[UnifiedShadowContextPackageRecord],
    rule_drafts: list[UnifiedShadowRuleDraftRecord],
    guardrail_results: list[UnifiedShadowGuardrailRecord],
) -> UnifiedShadowDownstreamSummary:
    executed = sum(
        1
        for gr in group_results
        if gr.execution_status == UnifiedShadowDownstreamExecutionStatus.EXECUTED
    )
    counts_by_execution_status: dict[UnifiedShadowDownstreamExecutionStatus, int] = {}
    for gr in group_results:
        counts_by_execution_status[gr.execution_status] = (
            counts_by_execution_status.get(gr.execution_status, 0) + 1
        )
    counts_by_guardrail_status: dict[UnifiedShadowGuardrailStatus, int] = {}
    for g in guardrail_results:
        counts_by_guardrail_status[g.status] = counts_by_guardrail_status.get(g.status, 0) + 1
    return UnifiedShadowDownstreamSummary(
        validation_group_count=len(group_results),
        downstream_eligible_group_count=sum(
            1 for gr in group_results if gr.downstream_shadow_eligible
        ),
        executed_group_count=executed,
        skipped_group_count=len(group_results) - executed,
        context_package_count=len(context_packages),
        rule_draft_count=len(rule_drafts),
        guardrail_passed_count=sum(
            1 for g in guardrail_results if g.status == UnifiedShadowGuardrailStatus.PASSED
        ),
        guardrail_rejected_count=sum(
            1 for g in guardrail_results if g.status == UnifiedShadowGuardrailStatus.REJECTED
        ),
        technical_failure_count=sum(
            1
            for gr in group_results
            if gr.execution_status == UnifiedShadowDownstreamExecutionStatus.TECHNICAL_FAILURE
        ),
        counts_by_execution_status=counts_by_execution_status,
        counts_by_guardrail_status=counts_by_guardrail_status,
    )
