"""Ejecutor PURO de detectores V2 en shadow mode (Fase 5 de la ampliacion
semantica, `feat/v2-detectors-shadow-mode`).

Recibe un `V2DetectorContext` ya construido (`v2_detector_context.py`),
ejecuta todos los detectores del registry (`v2_detector_registry.py`) en
orden determinístico, deduplica dentro de cada detector (ya resuelto por
`candidate_id` en cada detector -- ver `v2_detectors.py`), genera
comparaciones V1/V2 (Fase 10) y agrega el `V2ShadowSummary`. Nunca:

- lee filesystem (responsabilidad de `v2_shadow_candidates_service.py`);
- escribe archivos;
- accede a Neo4j ni ejecuta Cypher;
- modifica `V2DetectorContext` ni ninguno de los artefactos que contiene;
- ejecuta un LLM.

Un fallo interno de un detector (excepcion no capturada) se propaga tal
cual -- nunca se atrapa para producir un artefacto parcial con algunos
detectores faltantes."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from ..contracts.candidate import RuleCandidate
from ..contracts.v2_shadow_candidates import (
    V1V2CandidateComparison,
    V1V2ComparisonStatus,
    V2DetectorExecution,
    V2RuleType,
    V2ShadowCandidate,
    V2ShadowCandidatesArtifact,
    V2ShadowSummary,
)
from .v2_detector_context import V2DetectorContext
from .v2_detector_registry import V2_DETECTOR_REGISTRY, ordered_detector_ids


def _comparison_id_for(*, program: str, decision_id: str, status: V1V2ComparisonStatus) -> str:
    digest = hashlib.sha256(
        f"{program}\x1f{decision_id}\x1f{status.value}".encode()
    ).hexdigest()[:24]
    return f"comparison::{digest}"


def _run_detectors(ctx: V2DetectorContext) -> list[V2DetectorExecution]:
    executions: list[V2DetectorExecution] = []
    for detector_id in ordered_detector_ids():
        definition = V2_DETECTOR_REGISTRY[detector_id]
        candidates = definition.callable(ctx)
        blocked_count = sum(1 for candidate in candidates if candidate.support.value == "BLOCKED")
        executions.append(
            V2DetectorExecution(
                detector_id=definition.detector_id,
                detector_version=definition.detector_version,
                rule_type=definition.rule_type,
                candidate_count=len(candidates),
                blocked_count=blocked_count,
                candidates=sorted(candidates, key=lambda candidate: candidate.candidate_id),
                diagnostics=[],
            )
        )
    return executions


def _build_comparisons(
    ctx: V2DetectorContext, executions: Sequence[V2DetectorExecution]
) -> list[V1V2CandidateComparison]:
    all_v2_candidates = [
        candidate for execution in executions for candidate in execution.candidates
    ]
    v2_by_decision: dict[str, list[V2ShadowCandidate]] = {}
    for candidate in all_v2_candidates:
        if candidate.decision_id is None:
            continue
        v2_by_decision.setdefault(candidate.decision_id, []).append(candidate)

    all_decision_ids = set(ctx.v1_candidates_by_decision_id) | set(v2_by_decision)
    comparisons: list[V1V2CandidateComparison] = []

    for decision_id in sorted(all_decision_ids):
        v1_group: list[RuleCandidate] = ctx.v1_candidates_by_decision_id.get(decision_id, [])
        v2_group: list[V2ShadowCandidate] = v2_by_decision.get(decision_id, [])
        if not v1_group and not v2_group:
            continue

        if v2_group:
            program = v2_group[0].program
            paragraph = v2_group[0].paragraph
        else:
            program = ctx.program_name_for_v1_candidate(v1_group[0]) or "UNKNOWN"
            paragraph = v1_group[0].paragraph_name

        v1_ids = sorted(candidate.candidate_id for candidate in v1_group)
        v2_ids = sorted(candidate.candidate_id for candidate in v2_group)

        if v1_group and not v2_group:
            status = V1V2ComparisonStatus.V1_ONLY
            reason = (
                f"Q0 detecto {len(v1_group)} candidato(s) para la Decision {decision_id!r} "
                "sin equivalente entre los detectores V2 registrados."
            )
        elif v2_group and not v1_group:
            deterministic_detector_ids = {
                candidate.detector_id
                for candidate in v2_group
                if candidate.support.value == "DETERMINISTIC"
            }
            if len(deterministic_detector_ids) >= 2:
                status = V1V2ComparisonStatus.RELATED_NOT_EQUIVALENT
                reason = (
                    f"Detectores V2 distintos ({sorted(deterministic_detector_ids)}) "
                    f"encontraron evidencia sobre la misma Decision {decision_id!r} sin "
                    "equivalente en Q0 (V1): uno describe el efecto normalizado, otro "
                    "conserva la construccion COBOL nivel 88; nunca se fusionan."
                )
            else:
                status = V1V2ComparisonStatus.V2_ONLY
                reason = (
                    f"Detector(es) V2 encontraron {len(v2_group)} candidato(s) para la "
                    f"Decision {decision_id!r} que Q0 (V1) no detecta."
                )
        else:
            v1_outcomes = {
                candidate.outcome_code
                for candidate in v1_group
                if candidate.outcome_code is not None
            }
            v2_literals = {
                candidate.resolved_literal
                for candidate in v2_group
                if candidate.resolved_literal is not None
            }
            if len(v1_outcomes) == 1 and v2_literals and v1_outcomes == v2_literals:
                status = V1V2ComparisonStatus.MATCHED
                reason = (
                    f"Mismo program/paragraph/Decision ({decision_id!r}) y mismo literal "
                    f"({next(iter(v1_outcomes))!r}) demostrado tanto por Q0 (V1) como por "
                    "detector(es) V2."
                )
            else:
                status = V1V2ComparisonStatus.RELATED_NOT_EQUIVALENT
                reason = (
                    f"Q0 (V1) y detector(es) V2 coinciden en la Decision {decision_id!r}, "
                    "pero el literal/outcome_code no puede verificarse como identico "
                    f"(V1={sorted(v1_outcomes)!r}, V2={sorted(v2_literals)!r})."
                )

        comparisons.append(
            V1V2CandidateComparison(
                comparison_id=_comparison_id_for(
                    program=program, decision_id=decision_id, status=status
                ),
                status=status,
                v1_candidate_ids=v1_ids,
                v2_candidate_ids=v2_ids,
                program=program,
                paragraph=paragraph,
                reason=reason,
            )
        )

    return sorted(comparisons, key=lambda comparison: comparison.comparison_id)


def _build_summary(
    ctx: V2DetectorContext,
    executions: Sequence[V2DetectorExecution],
    comparisons: Sequence[V1V2CandidateComparison],
) -> V2ShadowSummary:
    all_v2_candidates = [
        candidate for execution in executions for candidate in execution.candidates
    ]
    counts_by_rule_type: dict[V2RuleType, int] = {}
    counts_by_detector: dict[str, int] = {}
    deterministic_count = 0
    partial_count = 0
    blocked_count = 0
    for candidate in all_v2_candidates:
        counts_by_rule_type[candidate.rule_type] = (
            counts_by_rule_type.get(candidate.rule_type, 0) + 1
        )
        counts_by_detector[candidate.detector_id] = (
            counts_by_detector.get(candidate.detector_id, 0) + 1
        )
        if candidate.support.value == "DETERMINISTIC":
            deterministic_count += 1
        elif candidate.support.value == "PARTIAL":
            partial_count += 1
        elif candidate.support.value == "BLOCKED":
            blocked_count += 1

    matched_count = sum(
        1 for comparison in comparisons if comparison.status == V1V2ComparisonStatus.MATCHED
    )
    v1_only_count = sum(
        1 for comparison in comparisons if comparison.status == V1V2ComparisonStatus.V1_ONLY
    )
    v2_only_count = sum(
        1 for comparison in comparisons if comparison.status == V1V2ComparisonStatus.V2_ONLY
    )
    related_not_equivalent_count = sum(
        1
        for comparison in comparisons
        if comparison.status == V1V2ComparisonStatus.RELATED_NOT_EQUIVALENT
    )

    return V2ShadowSummary(
        detector_count=len(executions),
        v1_candidate_count=len(ctx.v1_candidates.candidates),
        v2_candidate_count=len(all_v2_candidates),
        deterministic_count=deterministic_count,
        partial_count=partial_count,
        blocked_count=blocked_count,
        matched_count=matched_count,
        v1_only_count=v1_only_count,
        v2_only_count=v2_only_count,
        related_not_equivalent_count=related_not_equivalent_count,
        counts_by_rule_type=counts_by_rule_type,
        counts_by_detector=counts_by_detector,
    )


def run_v2_shadow_detection(
    ctx: V2DetectorContext,
    *,
    run_id: str,
    source_package_hash: str,
    source_artifact_hashes: dict[str, str],
) -> V2ShadowCandidatesArtifact:
    """Punto de entrada del ejecutor puro (Fase 11). Determinista: mismo
    `V2DetectorContext` siempre produce el mismo `V2ShadowCandidatesArtifact`."""
    executions = _run_detectors(ctx)
    comparisons = _build_comparisons(ctx, executions)
    summary = _build_summary(ctx, executions, comparisons)

    return V2ShadowCandidatesArtifact(
        run_id=run_id,
        source_package_hash=source_package_hash,
        source_artifact_hashes=dict(source_artifact_hashes),
        semantic_effects_schema_version=ctx.semantic_effects.schema_version,
        semantic_effects_analyzer_version=ctx.semantic_effects.analyzer_version,
        semantic_propagation_schema_version=ctx.semantic_propagation.schema_version,
        semantic_propagation_analyzer_version=ctx.semantic_propagation.analyzer_version,
        summary=summary,
        executions=executions,
        comparisons=comparisons,
    )
