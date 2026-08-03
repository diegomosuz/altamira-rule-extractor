"""Analizador PURO de relaciones entre `UnifiedCandidateReference` de
fuentes DISTINTAS (Fase 9 de la ampliacion semantica,
`feat/unified-candidate-promotion-assessment`).

Solo relaciona candidatos CRUZANDO fuentes (V1<->V2, V1<->interprocedural,
V2<->interprocedural) -- nunca relaciona dos candidatos de la MISMA
fuente (esa deduplicacion ya es responsabilidad de cada fuente: V2 vía
`comparable_v1_candidate_ids`/sus propias comparaciones,
interprocedural via `_check_no_semantic_duplicates`).

Utiliza como evidencia PREFERENTE las comparaciones YA CALCULADAS por
cada fuente -- nunca las recalcula desde cero:

- `V2ShadowCandidatesArtifact.comparisons` (`V1V2CandidateComparison`,
  Fase 5) para pares V1<->V2.
- `InterproceduralRuleCandidatesArtifact.comparisons`
  (`InterproceduralCandidateComparison.v1_relation`/`v2_relation`, Fase
  8) para pares interprocedural<->V1 e interprocedural<->V2.

Ambito de comparacion: solo se evaluan pares que comparten `program`
(ambos lados no-`None` e iguales) -- comparar candidatos de programas
distintos no tiene ningun ancla funcional demostrable. Dentro de ese
ambito:

- `EXACT_MATCH`/`RELATED` cuando la comparacion ya calculada por la
  fuente (o el vinculo declarado `comparable_v1_candidate_ids`) senala
  ese par especifico.
- `NO_RELATION` en cualquier otro caso DEL AMBITO evaluado (ambas
  fuentes disponibles, par considerado, sin coincidencia) -- nunca
  `NO_RELATION` para una fuente ausente (ver `NOT_EVALUATED`).
- `CONFLICT` NUNCA se asigna aqui: es responsabilidad exclusiva de
  `candidate_conflict_analyzer.py`, reconciliado por
  `candidate_promotion_assessment_analyzer.py` (Fase 9) sobre el par
  exacto que el conflicto identifica -- este modulo nunca contradice una
  relacion existente sin ese diagnostico explicito."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from ..contracts.candidate_promotion_assessment import (
    CandidateRelation,
    CandidateRelationKind,
    UnifiedCandidateReference,
)
from ..contracts.interprocedural_rule_candidates import (
    InterproceduralCandidateComparison,
    InterproceduralRelationStatus,
    InterproceduralRuleCandidatesArtifact,
)
from ..contracts.v2_shadow_candidates import (
    V1V2CandidateComparison,
    V1V2ComparisonStatus,
    V2ShadowCandidate,
    V2ShadowCandidatesArtifact,
)


def _digest(*parts: str) -> str:
    canonical = "\x1f".join(parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def relation_id_for(*, left_reference_id: str, right_reference_id: str) -> str:
    """Determinista y SIMETRICO: el orden de los argumentos nunca
    afecta el resultado (siempre se ordena alfabeticamente antes de
    generar el digest), asi que A~B y B~A producen el mismo
    `relation_id` -- consistente con
    `CandidateRelation._check_pair_is_ordered`."""
    left, right = sorted((left_reference_id, right_reference_id))
    return f"relation::{_digest(left, right)}"


def _sorted_unique(values: Sequence[str]) -> list[str]:
    return sorted(set(values))


def _ordered_pair(
    left: UnifiedCandidateReference, right: UnifiedCandidateReference
) -> tuple[UnifiedCandidateReference, UnifiedCandidateReference]:
    if left.unified_reference_id <= right.unified_reference_id:
        return left, right
    return right, left


def _build_relation(
    left: UnifiedCandidateReference,
    right: UnifiedCandidateReference,
    *,
    kind: CandidateRelationKind,
    reason: str,
    evidence: Sequence[str] = (),
) -> CandidateRelation:
    ordered_left, ordered_right = _ordered_pair(left, right)
    shared_target = (
        ordered_left.target
        if ordered_left.target is not None and ordered_left.target == ordered_right.target
        else None
    )
    shared_output_literal = (
        ordered_left.output_literal
        if ordered_left.output_literal is not None
        and ordered_left.output_literal == ordered_right.output_literal
        else None
    )
    shared_decision_id = (
        ordered_left.decision_id
        if ordered_left.decision_id is not None
        and ordered_left.decision_id == ordered_right.decision_id
        else None
    )
    shared_call_site_id = (
        ordered_left.call_site_id
        if ordered_left.call_site_id is not None
        and ordered_left.call_site_id == ordered_right.call_site_id
        else None
    )
    shared_paragraph = (
        ordered_left.paragraph
        if ordered_left.paragraph is not None and ordered_left.paragraph == ordered_right.paragraph
        else None
    )
    return CandidateRelation(
        relation_id=relation_id_for(
            left_reference_id=ordered_left.unified_reference_id,
            right_reference_id=ordered_right.unified_reference_id,
        ),
        left_reference_id=ordered_left.unified_reference_id,
        right_reference_id=ordered_right.unified_reference_id,
        relation_kind=kind,
        shared_program=ordered_left.program,
        shared_paragraph=shared_paragraph,
        shared_decision_id=shared_decision_id,
        shared_call_site_id=shared_call_site_id,
        shared_target=shared_target,
        shared_output_literal=shared_output_literal,
        reason=reason,
        evidence=_sorted_unique(list(evidence)),
    )


def _v1_v2_pair_kind(
    *,
    v1_source_candidate_id: str,
    v2_candidate: V2ShadowCandidate,
    comparisons: Sequence[V1V2CandidateComparison],
) -> tuple[CandidateRelationKind, str, list[str]]:
    for comparison in comparisons:
        if (
            v1_source_candidate_id in comparison.v1_candidate_ids
            and v2_candidate.candidate_id in comparison.v2_candidate_ids
        ):
            if comparison.status == V1V2ComparisonStatus.MATCHED:
                return (
                    CandidateRelationKind.EXACT_MATCH,
                    f"V2ShadowCandidatesArtifact ya calculo MATCHED en "
                    f"{comparison.comparison_id!r}: {comparison.reason}",
                    [comparison.comparison_id],
                )
            if comparison.status == V1V2ComparisonStatus.RELATED_NOT_EQUIVALENT:
                return (
                    CandidateRelationKind.RELATED,
                    f"V2ShadowCandidatesArtifact ya calculo RELATED_NOT_EQUIVALENT en "
                    f"{comparison.comparison_id!r}: {comparison.reason}",
                    [comparison.comparison_id],
                )
    if v1_source_candidate_id in v2_candidate.comparable_v1_candidate_ids:
        return (
            CandidateRelationKind.RELATED,
            f"V2ShadowCandidate({v2_candidate.candidate_id!r}) declara "
            f"comparable_v1_candidate_ids incluyendo {v1_source_candidate_id!r}",
            [],
        )
    return (
        CandidateRelationKind.NO_RELATION,
        "mismo programa, ambas fuentes disponibles, sin comparacion V1/V2 ni vinculo "
        "declarado que relacione este par especifico",
        [],
    )


def _interprocedural_relation_kind(
    *,
    comparison: InterproceduralCandidateComparison,
    other_source_candidate_id: str,
    relation_status: InterproceduralRelationStatus,
    matched_id: str | None,
) -> tuple[CandidateRelationKind, str, list[str]]:
    if relation_status == InterproceduralRelationStatus.MATCHED and matched_id == (
        other_source_candidate_id
    ):
        return (
            CandidateRelationKind.EXACT_MATCH,
            f"InterproceduralCandidateComparison({comparison.comparison_id!r}) ya calculo "
            f"MATCHED: {comparison.reason}",
            [comparison.comparison_id],
        )
    if relation_status == InterproceduralRelationStatus.RELATED and matched_id == (
        other_source_candidate_id
    ):
        return (
            CandidateRelationKind.RELATED,
            f"InterproceduralCandidateComparison({comparison.comparison_id!r}) ya calculo "
            f"RELATED: {comparison.reason}",
            [comparison.comparison_id],
        )
    return (
        CandidateRelationKind.NO_RELATION,
        "mismo programa, ambas fuentes disponibles, sin match/related declarado por Fase "
        "8 para este par especifico",
        [],
    )


def build_candidate_relations(
    *,
    v1_references: Sequence[UnifiedCandidateReference],
    v2_references: Sequence[UnifiedCandidateReference],
    interprocedural_references: Sequence[UnifiedCandidateReference],
    v2_artifact: V2ShadowCandidatesArtifact | None,
    interprocedural_artifact: InterproceduralRuleCandidatesArtifact | None,
    conflicting_pairs: frozenset[frozenset[str]] = frozenset(),
) -> list[CandidateRelation]:
    """Puro: nunca muta ninguno de los argumentos. `conflicting_pairs`
    (calculado por `candidate_conflict_analyzer.py` y reconciliado por
    el analizador principal, Fase 9) tiene prioridad absoluta sobre
    `EXACT_MATCH`/`RELATED`/`NO_RELATION`: un par ya identificado como
    conflictivo nunca se reclasifica como coincidencia."""
    relations: dict[str, CandidateRelation] = {}

    def _add(
        left: UnifiedCandidateReference,
        right: UnifiedCandidateReference,
        kind: CandidateRelationKind,
        reason: str,
        evidence: Sequence[str],
    ) -> None:
        pair = frozenset({left.unified_reference_id, right.unified_reference_id})
        if pair in conflicting_pairs:
            kind = CandidateRelationKind.CONFLICT
            reason = (
                "candidate_conflict_analyzer.py demostro una contradiccion estructural "
                "para este par especifico -- ver CandidateConflict correspondiente"
            )
            evidence = []
        relation = _build_relation(left, right, kind=kind, reason=reason, evidence=evidence)
        relations[relation.relation_id] = relation

    if v2_artifact is not None and v1_references and v2_references:
        v2_candidates_by_source_id = {
            candidate.candidate_id: candidate
            for execution in v2_artifact.executions
            for candidate in execution.candidates
        }
        for v1_ref in v1_references:
            for v2_ref in v2_references:
                if v1_ref.program is None or v1_ref.program != v2_ref.program:
                    continue
                v2_candidate = v2_candidates_by_source_id[v2_ref.source_candidate_id]
                kind, reason, evidence = _v1_v2_pair_kind(
                    v1_source_candidate_id=v1_ref.source_candidate_id,
                    v2_candidate=v2_candidate,
                    comparisons=v2_artifact.comparisons,
                )
                _add(v1_ref, v2_ref, kind, reason, evidence)

    if interprocedural_artifact is not None and interprocedural_references:
        comparison_by_source_id = {
            comparison.interprocedural_candidate_id: comparison
            for comparison in interprocedural_artifact.comparisons
        }
        for interprocedural_ref in interprocedural_references:
            comparison = comparison_by_source_id.get(interprocedural_ref.source_candidate_id)
            if comparison is None:
                continue
            if v1_references:
                for v1_ref in v1_references:
                    if (
                        interprocedural_ref.program is None
                        or interprocedural_ref.program != v1_ref.program
                    ):
                        continue
                    kind, reason, evidence = _interprocedural_relation_kind(
                        comparison=comparison,
                        other_source_candidate_id=v1_ref.source_candidate_id,
                        relation_status=comparison.v1_relation,
                        matched_id=comparison.v1_candidate_id,
                    )
                    _add(interprocedural_ref, v1_ref, kind, reason, evidence)
            if v2_references:
                for v2_ref in v2_references:
                    if (
                        interprocedural_ref.program is None
                        or interprocedural_ref.program != v2_ref.program
                    ):
                        continue
                    kind, reason, evidence = _interprocedural_relation_kind(
                        comparison=comparison,
                        other_source_candidate_id=v2_ref.source_candidate_id,
                        relation_status=comparison.v2_relation,
                        matched_id=comparison.v2_candidate_id,
                    )
                    _add(interprocedural_ref, v2_ref, kind, reason, evidence)

    return sorted(relations.values(), key=lambda relation: relation.relation_id)
