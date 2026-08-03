"""Resolutor PURO de candidatos fuente reales (Fase 11 de la
ampliacion semantica, `feat/unified-candidate-artifact-shadow`).

Dado un `UnifiedCandidateReference` (Fase 9, ya validado contra el
assessment) y los TRES artefactos fuente reales (`CandidateArtifact`
V1, `V2ShadowCandidatesArtifact`, `InterproceduralRuleCandidatesArtifact`,
cualquiera opcionalmente ausente), localiza el candidato REAL por
`source_candidate_id` dentro de la lista de candidatos de la fuente
declarada, verifica que su identidad (target/output/decision_id/
call_site_id/program/paragraph) coincide con lo que `reference` ya
afirma, y devuelve un hash determinista del candidato real -- NUNCA
relee `source_text`, NUNCA reinterpreta un `rule_type` por semejanza de
nombre, NUNCA fabrica un ID ausente, NUNCA sustituye una fuente
invalida por un resultado vacio: cada fallo se distingue explicitamente
(`SourceResolutionFailureReason`).

Puro: no filesystem, no Neo4j, no LLM, nunca muta ninguno de sus
argumentos."""

from __future__ import annotations

import hashlib
from enum import StrEnum

from ..contracts.candidate import CandidateArtifact, RuleCandidate
from ..contracts.candidate_promotion_assessment import CandidateSource, UnifiedCandidateReference
from ..contracts.interprocedural_rule_candidates import (
    InterproceduralRuleCandidate,
    InterproceduralRuleCandidatesArtifact,
)
from ..contracts.v2_shadow_candidates import V2ShadowCandidate, V2ShadowCandidatesArtifact


class SourceResolutionFailureReason(StrEnum):
    """Motivos ESTABLES de fallo de resolucion -- corresponden 1:1 a
    valores de `UnifiedShadowExclusionReason` (Fase 11, Parte 3), nunca
    texto libre."""

    UNKNOWN_SOURCE = "UNKNOWN_SOURCE"
    SOURCE_CANDIDATE_NOT_FOUND = "SOURCE_CANDIDATE_NOT_FOUND"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"


class SourceResolutionResult:
    """Resultado de resolver UNA referencia contra su fuente real.
    Exactamente uno de `source_candidate_hash`/`failure_reason` esta
    presente -- nunca ambos, nunca ninguno."""

    __slots__ = ("source_candidate_hash", "failure_reason", "failure_detail")

    def __init__(
        self,
        *,
        source_candidate_hash: str | None = None,
        failure_reason: SourceResolutionFailureReason | None = None,
        failure_detail: str | None = None,
    ) -> None:
        if (source_candidate_hash is None) == (failure_reason is None):
            raise ValueError(
                "SourceResolutionResult exige exactamente uno de "
                "source_candidate_hash/failure_reason"
            )
        self.source_candidate_hash = source_candidate_hash
        self.failure_reason = failure_reason
        self.failure_detail = failure_detail

    @property
    def is_success(self) -> bool:
        return self.source_candidate_hash is not None


def _hash_candidate(candidate: object) -> str:
    return hashlib.sha256(candidate.to_stable_json().encode("utf-8")).hexdigest()  # type: ignore[attr-defined]


def _find_v1_candidate(
    artifact: CandidateArtifact, source_candidate_id: str
) -> RuleCandidate | None:
    return next((c for c in artifact.candidates if c.candidate_id == source_candidate_id), None)


def _find_v2_candidate(
    artifact: V2ShadowCandidatesArtifact, source_candidate_id: str
) -> V2ShadowCandidate | None:
    for execution in artifact.executions:
        for candidate in execution.candidates:
            if candidate.candidate_id == source_candidate_id:
                return candidate
    return None


def _find_interprocedural_candidate(
    artifact: InterproceduralRuleCandidatesArtifact, source_candidate_id: str
) -> InterproceduralRuleCandidate | None:
    return next((c for c in artifact.candidates if c.candidate_id == source_candidate_id), None)


def _v1_identity_matches(candidate: RuleCandidate, reference: UnifiedCandidateReference) -> bool:
    return (
        candidate.outcome_code == reference.output_literal
        and candidate.decision_id == reference.decision_id
        and candidate.paragraph_name == reference.paragraph
    )


def _v2_identity_matches(
    candidate: V2ShadowCandidate, reference: UnifiedCandidateReference
) -> bool:
    target = candidate.target_qualified_name or candidate.target_variable
    return (
        candidate.program == reference.program
        and candidate.paragraph == reference.paragraph
        and target == reference.target
        and candidate.resolved_literal == reference.output_literal
        and candidate.decision_id == reference.decision_id
    )


def _interprocedural_identity_matches(
    candidate: InterproceduralRuleCandidate, reference: UnifiedCandidateReference
) -> bool:
    return (
        candidate.caller_program == reference.program
        and candidate.caller_paragraph == reference.paragraph
        and candidate.call_site_id == reference.call_site_id
        and candidate.target == reference.target
        and candidate.input_literal == reference.input_literal
        and candidate.output_literal == reference.output_literal
    )


def resolve_source_candidate(
    *,
    reference: UnifiedCandidateReference,
    v1_artifact: CandidateArtifact | None,
    v2_artifact: V2ShadowCandidatesArtifact | None,
    interprocedural_artifact: InterproceduralRuleCandidatesArtifact | None,
) -> SourceResolutionResult:
    """Punto de entrada puro. Nunca muta `reference`/los tres
    artefactos. `reference.source` decide cual artefacto se consulta --
    nunca se busca en mas de uno, nunca se completa un campo ausente
    con otra fuente."""
    if reference.source == CandidateSource.V1:
        if v1_artifact is None:
            return SourceResolutionResult(
                failure_reason=SourceResolutionFailureReason.UNKNOWN_SOURCE,
                failure_detail="CandidateArtifact V1 no fue provisto al resolutor",
            )
        v1_candidate = _find_v1_candidate(v1_artifact, reference.source_candidate_id)
        if v1_candidate is None:
            return SourceResolutionResult(
                failure_reason=SourceResolutionFailureReason.SOURCE_CANDIDATE_NOT_FOUND,
                failure_detail=(
                    f"source_candidate_id {reference.source_candidate_id!r} no existe en "
                    "CandidateArtifact V1"
                ),
            )
        if not _v1_identity_matches(v1_candidate, reference):
            return SourceResolutionResult(
                failure_reason=SourceResolutionFailureReason.IDENTITY_MISMATCH,
                failure_detail=(
                    f"el candidato V1 {reference.source_candidate_id!r} no coincide con la "
                    "identidad declarada por el UnifiedCandidateReference de Fase 9"
                ),
            )
        return SourceResolutionResult(source_candidate_hash=_hash_candidate(v1_candidate))

    if reference.source == CandidateSource.V2:
        if v2_artifact is None:
            return SourceResolutionResult(
                failure_reason=SourceResolutionFailureReason.UNKNOWN_SOURCE,
                failure_detail="V2ShadowCandidatesArtifact no fue provisto al resolutor",
            )
        v2_candidate = _find_v2_candidate(v2_artifact, reference.source_candidate_id)
        if v2_candidate is None:
            return SourceResolutionResult(
                failure_reason=SourceResolutionFailureReason.SOURCE_CANDIDATE_NOT_FOUND,
                failure_detail=(
                    f"source_candidate_id {reference.source_candidate_id!r} no existe en "
                    "V2ShadowCandidatesArtifact"
                ),
            )
        if not _v2_identity_matches(v2_candidate, reference):
            return SourceResolutionResult(
                failure_reason=SourceResolutionFailureReason.IDENTITY_MISMATCH,
                failure_detail=(
                    f"el candidato V2 {reference.source_candidate_id!r} no coincide con la "
                    "identidad declarada por el UnifiedCandidateReference de Fase 9"
                ),
            )
        return SourceResolutionResult(source_candidate_hash=_hash_candidate(v2_candidate))

    # CandidateSource.INTERPROCEDURAL
    if interprocedural_artifact is None:
        return SourceResolutionResult(
            failure_reason=SourceResolutionFailureReason.UNKNOWN_SOURCE,
            failure_detail="InterproceduralRuleCandidatesArtifact no fue provisto al resolutor",
        )
    ip_candidate = _find_interprocedural_candidate(
        interprocedural_artifact, reference.source_candidate_id
    )
    if ip_candidate is None:
        return SourceResolutionResult(
            failure_reason=SourceResolutionFailureReason.SOURCE_CANDIDATE_NOT_FOUND,
            failure_detail=(
                f"source_candidate_id {reference.source_candidate_id!r} no existe en "
                "InterproceduralRuleCandidatesArtifact"
            ),
        )
    if not _interprocedural_identity_matches(ip_candidate, reference):
        return SourceResolutionResult(
            failure_reason=SourceResolutionFailureReason.IDENTITY_MISMATCH,
            failure_detail=(
                f"el candidato interprocedural {reference.source_candidate_id!r} no "
                "coincide con la identidad declarada por el UnifiedCandidateReference "
                "de Fase 9"
            ),
        )
    return SourceResolutionResult(source_candidate_hash=_hash_candidate(ip_candidate))
