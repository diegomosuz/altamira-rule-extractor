"""Analizador PURO de deteccion de reglas interprocedurales en shadow
mode (Fase 8 de la ampliacion semantica,
`feat/interprocedural-rule-detectors-shadow`).

Recibe `CanonicalProgram[]`, `CandidateArtifact` V1 (opcional),
`V2ShadowCandidatesArtifact` (opcional), `SemanticEffectsArtifact`,
`SemanticPropagationArtifact`, `InterproceduralCallLinkageArtifact`,
`InterproceduralPropagationArtifact` y `SemanticEnrichmentArtifact`
(opcional, solo para `STATE_TRANSITION_RULE`) ya calculados (nunca los
vuelve a derivar) y devuelve un `InterproceduralRuleCandidatesArtifact`.
Nunca:

- lee filesystem (responsabilidad de
  `interprocedural_rule_candidates_service.py`);
- accede a Neo4j ni consulta `SemanticGraph`;
- ejecuta un LLM;
- reinterpreta `source_text`;
- modifica los objetos de entrada;
- promueve un candidato a V1/V2, `ContextPackage` ni `RuleDraft`;
- intenta fixed point sobre ciclos ni resuelve `CALL` dinamico (Fase 7 ya
  bloqueo esos call sites).

Orquesta, en orden: construccion de `InterproceduralRuleDetectorContext`
(`interprocedural_rule_detectors.py`), ejecucion de cada detector
registrado (`interprocedural_rule_detector_registry.py`, orden alfabetico
por `detector_id`), comparacion contra V1/V2
(`interprocedural_rule_comparator.py`, componente separado que ningun
detector invoca directamente) y agregacion del summary.

Auditoria de cierre (Fase 8, hardening): `v1_candidates`/`v2_candidates`
en `None` (fuente nunca disponible para este run) se propagan intactos
hasta el comparador -- nunca se sustituyen por una lista vacia antes de
tiempo, lo que perderia la distincion entre "fuente ausente" y "fuente
presente sin candidatos" (`InterproceduralRelationStatus.NOT_EVALUATED`
vs `NOT_FOUND`). El artefacto final tambien registra, en
`diagnostics`, la ausencia de cada fuente opcional y los call sites
bloqueados que nunca llegaron a candidato (ver
`interprocedural_rule_detectors.py::blocked_call_site_diagnostics`)."""

from __future__ import annotations

from collections.abc import Sequence

from ..contracts.candidate import CandidateArtifact
from ..contracts.canonical import CanonicalProgram
from ..contracts.interprocedural_call_linkage import InterproceduralCallLinkageArtifact
from ..contracts.interprocedural_propagation import InterproceduralPropagationArtifact
from ..contracts.interprocedural_rule_candidates import (
    InterproceduralCandidateComparison,
    InterproceduralCandidateSupport,
    InterproceduralComparisonStatus,
    InterproceduralRuleCandidate,
    InterproceduralRuleCandidatesArtifact,
    InterproceduralRuleCandidatesSummary,
    InterproceduralRuleType,
)
from ..contracts.semantic_effects import SemanticEffectsArtifact
from ..contracts.semantic_enrichment import SemanticEnrichmentArtifact
from ..contracts.semantic_propagation import SemanticPropagationArtifact
from ..contracts.v2_shadow_candidates import V2ShadowCandidate, V2ShadowCandidatesArtifact
from .interprocedural_rule_comparator import build_comparisons
from .interprocedural_rule_detector_registry import (
    INTERPROCEDURAL_RULE_DETECTOR_REGISTRY,
    ordered_detector_ids,
)
from .interprocedural_rule_detectors import (
    InterproceduralRuleDetectorContext,
    blocked_call_site_diagnostics,
    build_detector_context,
)

_V1_UNAVAILABLE_DIAGNOSTIC = "V1_CANDIDATES_UNAVAILABLE_ARTIFACTS_06_CANDIDATES_JSON_ABSENT"
# V2 depende de artifacts/04-semantic-graph.json Y de CandidateArtifact V1
# (V2DetectorContext, Fase 5, exige ambos) -- este diagnostico no distingue
# la causa exacta; `_V1_UNAVAILABLE_DIAGNOSTIC` (tambien presente cuando V1
# falta) ya la deja trazable por separado.
_V2_UNAVAILABLE_DIAGNOSTIC = "V2_SHADOW_CANDIDATES_UNAVAILABLE"
_STATE_TRANSITION_SKIPPED_DIAGNOSTIC = (
    "STATE_TRANSITION_RULE_DETECTOR_SKIPPED_NO_SEMANTIC_ENRICHMENT"
)


def _run_detectors(
    ctx: InterproceduralRuleDetectorContext,
) -> list[InterproceduralRuleCandidate]:
    all_candidates: list[InterproceduralRuleCandidate] = []
    for detector_id in ordered_detector_ids():
        definition = INTERPROCEDURAL_RULE_DETECTOR_REGISTRY[detector_id]
        candidates = definition.callable(ctx)
        for candidate in candidates:
            if candidate.detector != definition.detector_id:
                raise ValueError(
                    f"detector {definition.detector_id!r} produjo un candidato "
                    f"{candidate.candidate_id!r} atribuido a otro detector "
                    f"({candidate.detector!r})"
                )
            if candidate.rule_type != definition.rule_type:
                raise ValueError(
                    f"detector {definition.detector_id!r} produjo un candidato "
                    f"{candidate.candidate_id!r} con rule_type distinto del declarado"
                )
        all_candidates.extend(candidates)
    return sorted(all_candidates, key=lambda candidate: candidate.candidate_id)


def _build_summary(
    candidates: Sequence[InterproceduralRuleCandidate],
    comparisons: Sequence[InterproceduralCandidateComparison],
) -> InterproceduralRuleCandidatesSummary:
    counts_by_detector: dict[str, int] = {}
    counts_by_rule_type: dict[InterproceduralRuleType, int] = {}
    deterministic_count = 0
    partial_count = 0
    blocked_count = 0
    for candidate in candidates:
        counts_by_detector[candidate.detector] = counts_by_detector.get(candidate.detector, 0) + 1
        counts_by_rule_type[candidate.rule_type] = (
            counts_by_rule_type.get(candidate.rule_type, 0) + 1
        )
        if candidate.support == InterproceduralCandidateSupport.DETERMINISTIC:
            deterministic_count += 1
        elif candidate.support == InterproceduralCandidateSupport.PARTIAL:
            partial_count += 1
        else:
            blocked_count += 1

    matched_v1_count = sum(
        1 for c in comparisons if c.status == InterproceduralComparisonStatus.MATCHED_V1
    )
    matched_v2_count = sum(
        1 for c in comparisons if c.status == InterproceduralComparisonStatus.MATCHED_V2
    )
    related_v1_count = sum(
        1 for c in comparisons if c.status == InterproceduralComparisonStatus.RELATED_V1
    )
    related_v2_count = sum(
        1 for c in comparisons if c.status == InterproceduralComparisonStatus.RELATED_V2
    )
    interprocedural_only_count = sum(
        1 for c in comparisons if c.status == InterproceduralComparisonStatus.INTERPROCEDURAL_ONLY
    )
    not_evaluated_count = sum(
        1 for c in comparisons if c.status == InterproceduralComparisonStatus.NOT_EVALUATED
    )

    return InterproceduralRuleCandidatesSummary(
        candidate_count=len(candidates),
        deterministic_count=deterministic_count,
        partial_count=partial_count,
        blocked_count=blocked_count,
        counts_by_detector=counts_by_detector,
        counts_by_rule_type=counts_by_rule_type,
        matched_v1_count=matched_v1_count,
        matched_v2_count=matched_v2_count,
        related_v1_count=related_v1_count,
        related_v2_count=related_v2_count,
        interprocedural_only_count=interprocedural_only_count,
        not_evaluated_count=not_evaluated_count,
    )


def analyze_interprocedural_rule_candidates(
    *,
    canonical_programs: Sequence[CanonicalProgram],
    v1_candidates: CandidateArtifact | None,
    v2_candidates: V2ShadowCandidatesArtifact | None,
    semantic_effects: SemanticEffectsArtifact,
    semantic_propagation: SemanticPropagationArtifact,
    interprocedural_call_linkage: InterproceduralCallLinkageArtifact,
    interprocedural_propagation: InterproceduralPropagationArtifact,
    semantic_enrichment: SemanticEnrichmentArtifact | None,
    run_id: str,
    source_package_hash: str,
    source_artifact_hashes: dict[str, str],
) -> InterproceduralRuleCandidatesArtifact:
    """Punto de entrada del analizador puro (Fase 8). Determinista: misma
    entrada siempre produce el mismo `InterproceduralRuleCandidatesArtifact`."""
    ctx = build_detector_context(
        canonical_programs=canonical_programs,
        semantic_effects=semantic_effects,
        semantic_propagation=semantic_propagation,
        interprocedural_call_linkage=interprocedural_call_linkage,
        interprocedural_propagation=interprocedural_propagation,
        semantic_enrichment=semantic_enrichment,
    )
    candidates = _run_detectors(ctx)

    v2_shadow_candidates: list[V2ShadowCandidate] | None = (
        [c for execution in v2_candidates.executions for c in execution.candidates]
        if v2_candidates is not None
        else None
    )
    comparisons = build_comparisons(
        candidates, v1_candidates_artifact=v1_candidates, v2_candidates=v2_shadow_candidates
    )
    summary = _build_summary(candidates, comparisons)

    diagnostics: list[str] = list(blocked_call_site_diagnostics(ctx, candidates))
    if v1_candidates is None:
        diagnostics.append(_V1_UNAVAILABLE_DIAGNOSTIC)
    if v2_candidates is None:
        diagnostics.append(_V2_UNAVAILABLE_DIAGNOSTIC)
    if semantic_enrichment is None:
        diagnostics.append(_STATE_TRANSITION_SKIPPED_DIAGNOSTIC)
    diagnostics = sorted(set(diagnostics))

    canonical_schema_versions = sorted({program.schema_version for program in canonical_programs})

    return InterproceduralRuleCandidatesArtifact(
        run_id=run_id,
        source_package_hash=source_package_hash,
        source_artifact_hashes=dict(source_artifact_hashes),
        canonical_schema_versions=canonical_schema_versions,
        semantic_effects_schema_version=semantic_effects.schema_version,
        semantic_propagation_schema_version=semantic_propagation.schema_version,
        interprocedural_call_linkage_schema_version=interprocedural_call_linkage.schema_version,
        interprocedural_propagation_schema_version=interprocedural_propagation.schema_version,
        summary=summary,
        candidates=candidates,
        comparisons=comparisons,
        diagnostics=diagnostics,
    )
