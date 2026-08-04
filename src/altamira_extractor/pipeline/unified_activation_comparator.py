"""Comparador diferencial PURO entre referencias V1 y unified (Fase
14A Parte 6, `feat/controlled-unified-activation`).

Compara UNICAMENTE mediante igualdad demostrable de campos
estructurales -- NUNCA fuzzy matching, distancia de edicion,
embeddings, LLM ni comparacion por nombres parciales.

Ancla de agrupacion (Fase 14A Parte 1: los UNICOS dos campos que
`UnifiedActivationV1Reference` y `UnifiedActivationUnifiedReference`
exponen SIEMPRE ambos, ya que V1 nunca expone `target` y esta fase no
expone `paragraph` en la referencia unified): `(program,
output_literal)`. Dentro de un ancla compartida:

- mismo `level` (RULE/CANDIDATE) exigido siempre -- niveles distintos
  son SIEMPRE `NOT_COMPARABLE`, nunca una equivalencia forzada;
- `family`/`target`/`statement` (este ultimo solo en nivel RULE) se
  comparan UNICAMENTE cuando AMBOS lados los exponen (`None` en
  cualquier lado = comparacion vacuamente satisfecha, NUNCA una
  contradiccion) -- V1 nunca expone `target`, por lo que un
  `target_conflict` solo puede surgir entre dos referencias unified,
  nunca V1-vs-unified;
- cualquier contradiccion detectada en family/target/statement -->
  `CONFLICTING`;
- ausencia de contradiccion --> `EXACT_EQUIVALENT`.

Referencias que comparten UNICAMENTE `program` (sin igualdad de
`output_literal`) producen `RELATED` -- relacion demostrable por la
estructura misma (mismo programa), mas datos insuficientes para
equivalencia exacta, sin conflicto detectable.

Una referencia que no participa en ninguna comparacion EXACT_
EQUIVALENT/CONFLICTING/RELATED se resuelve como `V1_ONLY` (V1 sin
representacion unified) o `UNIFIED_ADDITIVE` (unified sin equivalente
V1) -- UNICAMENTE cuando la fuente OPUESTA fue efectivamente evaluada
(`v1_available`/`unified_available`); si la fuente opuesta nunca se
evaluo, la referencia queda `NOT_EVALUATED` -- nunca se afirma "V1 no
tiene esto" si V1 nunca se reviso.

Cada par semantico (conjunto ordenado de v1_reference_ids +
conjunto ordenado de unified_reference_ids) se serializa UNA sola vez
-- ver `UnifiedActivationEvaluationArtifact::
_check_comparison_pairs_serialized_once`."""

from __future__ import annotations

import hashlib

from ..contracts.unified_activation_evaluation import (
    UnifiedActivationComparison,
    UnifiedActivationComparisonKind,
    UnifiedActivationComparisonLevel,
    UnifiedActivationUnifiedReference,
    UnifiedActivationV1Reference,
)

_REASON_EXACT_EQUIVALENT = "same_anchor_no_contradiction"
_REASON_CONFLICTING = "same_anchor_contradiction"
_REASON_NOT_COMPARABLE_LEVEL = "different_comparison_level"
_REASON_RELATED_SAME_PROGRAM = "same_program_only"
_REASON_V1_ONLY = "no_unified_representation"
_REASON_UNIFIED_ADDITIVE = "no_v1_representation"
_REASON_NOT_EVALUATED_V1 = "v1_not_evaluated"
_REASON_NOT_EVALUATED_UNIFIED = "unified_not_evaluated"


def _comparison_id(
    kind: UnifiedActivationComparisonKind, v1_ids: list[str], unified_ids: list[str]
) -> str:
    canonical = "\x1f".join([kind.value, *sorted(v1_ids), "\x1e", *sorted(unified_ids)])
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return f"activation-comparison::{digest}"


def _anchor(program: str | None, output_literal: str | None) -> tuple[str, str] | None:
    if program is None or output_literal is None:
        return None
    return (program, output_literal)


def compare_references(
    v1_references: list[UnifiedActivationV1Reference],
    unified_references: list[UnifiedActivationUnifiedReference],
    *,
    v1_available: bool,
    unified_available: bool,
) -> list[UnifiedActivationComparison]:
    """Punto de entrada puro. Nunca muta las listas recibidas.
    Determinista: mismo input siempre produce el mismo conjunto de
    comparaciones, en el mismo orden (ordenadas por `comparison_id`
    por el llamador -- ver `unified_activation_evaluator.py`)."""
    comparisons: list[UnifiedActivationComparison] = []
    matched_v1_ids: set[str] = set()
    matched_unified_ids: set[str] = set()

    v1_by_anchor: dict[tuple[str, str], list[UnifiedActivationV1Reference]] = {}
    for v1_candidate in v1_references:
        anchor = _anchor(v1_candidate.program, v1_candidate.output_literal)
        if anchor is not None:
            v1_by_anchor.setdefault(anchor, []).append(v1_candidate)

    unified_by_anchor: dict[tuple[str, str], list[UnifiedActivationUnifiedReference]] = {}
    for unified_candidate in unified_references:
        anchor = _anchor(unified_candidate.program, unified_candidate.output_literal)
        if anchor is not None:
            unified_by_anchor.setdefault(anchor, []).append(unified_candidate)

    shared_anchors = sorted(set(v1_by_anchor) & set(unified_by_anchor))
    for anchor in shared_anchors:
        for v1_ref in v1_by_anchor[anchor]:
            for unified_ref in unified_by_anchor[anchor]:
                comparisons.append(_compare_pair(v1_ref, unified_ref))
                matched_v1_ids.add(v1_ref.reference_id)
                matched_unified_ids.add(unified_ref.reference_id)

    # RELATED: mismo `program` unicamente, sin ancla completa compartida.
    v1_by_program: dict[str, list[UnifiedActivationV1Reference]] = {}
    for v1_candidate in v1_references:
        if v1_candidate.reference_id in matched_v1_ids or v1_candidate.program is None:
            continue
        v1_by_program.setdefault(v1_candidate.program, []).append(v1_candidate)

    unified_by_program: dict[str, list[UnifiedActivationUnifiedReference]] = {}
    for unified_candidate in unified_references:
        if unified_candidate.reference_id in matched_unified_ids:
            continue
        unified_by_program.setdefault(unified_candidate.program, []).append(unified_candidate)

    shared_programs = sorted(set(v1_by_program) & set(unified_by_program))
    for program in shared_programs:
        for v1_ref in v1_by_program[program]:
            for unified_ref in unified_by_program[program]:
                comparisons.append(
                    UnifiedActivationComparison(
                        comparison_id=_comparison_id(
                            UnifiedActivationComparisonKind.RELATED,
                            [v1_ref.reference_id],
                            [unified_ref.reference_id],
                        ),
                        kind=UnifiedActivationComparisonKind.RELATED,
                        v1_reference_ids=[v1_ref.reference_id],
                        unified_reference_ids=[unified_ref.reference_id],
                        shared_program=program,
                        reason_code=_REASON_RELATED_SAME_PROGRAM,
                        evidence_ids=sorted(
                            set(v1_ref.evidence_ids) | set(unified_ref.evidence_ids)
                        ),
                    )
                )
                matched_v1_ids.add(v1_ref.reference_id)
                matched_unified_ids.add(unified_ref.reference_id)

    for v1_candidate in v1_references:
        if v1_candidate.reference_id in matched_v1_ids:
            continue
        kind = (
            UnifiedActivationComparisonKind.V1_ONLY
            if unified_available
            else UnifiedActivationComparisonKind.NOT_EVALUATED
        )
        reason = _REASON_V1_ONLY if unified_available else _REASON_NOT_EVALUATED_UNIFIED
        comparisons.append(
            UnifiedActivationComparison(
                comparison_id=_comparison_id(kind, [v1_candidate.reference_id], []),
                kind=kind,
                v1_reference_ids=[v1_candidate.reference_id],
                unified_reference_ids=[],
                shared_program=v1_candidate.program,
                shared_output_literal=v1_candidate.output_literal,
                reason_code=reason,
                evidence_ids=sorted(v1_candidate.evidence_ids),
            )
        )

    for unified_candidate in unified_references:
        if unified_candidate.reference_id in matched_unified_ids:
            continue
        kind = (
            UnifiedActivationComparisonKind.UNIFIED_ADDITIVE
            if v1_available
            else UnifiedActivationComparisonKind.NOT_EVALUATED
        )
        reason = _REASON_UNIFIED_ADDITIVE if v1_available else _REASON_NOT_EVALUATED_V1
        comparisons.append(
            UnifiedActivationComparison(
                comparison_id=_comparison_id(kind, [], [unified_candidate.reference_id]),
                kind=kind,
                v1_reference_ids=[],
                unified_reference_ids=[unified_candidate.reference_id],
                shared_program=unified_candidate.program,
                shared_target=unified_candidate.target,
                shared_output_literal=unified_candidate.output_literal,
                reason_code=reason,
                evidence_ids=sorted(unified_candidate.evidence_ids),
            )
        )

    return sorted(comparisons, key=lambda c: c.comparison_id)


def _compare_pair(
    v1_ref: UnifiedActivationV1Reference, unified_ref: UnifiedActivationUnifiedReference
) -> UnifiedActivationComparison:
    v1_ids = [v1_ref.reference_id]
    unified_ids = [unified_ref.reference_id]
    evidence_ids = sorted(set(v1_ref.evidence_ids) | set(unified_ref.evidence_ids))

    if v1_ref.level != unified_ref.level:
        kind = UnifiedActivationComparisonKind.NOT_COMPARABLE
        return UnifiedActivationComparison(
            comparison_id=_comparison_id(kind, v1_ids, unified_ids),
            kind=kind,
            v1_reference_ids=v1_ids,
            unified_reference_ids=unified_ids,
            shared_program=v1_ref.program,
            shared_output_literal=v1_ref.output_literal,
            reason_code=_REASON_NOT_COMPARABLE_LEVEL,
            evidence_ids=evidence_ids,
        )

    contradictions: list[str] = []
    if (
        v1_ref.rule_family is not None
        and unified_ref.rule_family is not None
        and v1_ref.rule_family != unified_ref.rule_family
    ):
        contradictions.append("family")
    if (
        v1_ref.target is not None
        and unified_ref.target is not None
        and v1_ref.target != unified_ref.target
    ):
        contradictions.append("target")
    if (
        v1_ref.level == UnifiedActivationComparisonLevel.RULE
        and v1_ref.statement is not None
        and unified_ref.statement is not None
        and v1_ref.statement != unified_ref.statement
    ):
        contradictions.append("statement")

    shared_target = v1_ref.target if v1_ref.target is not None else unified_ref.target
    if contradictions:
        kind = UnifiedActivationComparisonKind.CONFLICTING
        return UnifiedActivationComparison(
            comparison_id=_comparison_id(kind, v1_ids, unified_ids),
            kind=kind,
            v1_reference_ids=v1_ids,
            unified_reference_ids=unified_ids,
            shared_program=v1_ref.program,
            shared_target=shared_target,
            shared_output_literal=v1_ref.output_literal,
            reason_code=f"{_REASON_CONFLICTING}::{'+'.join(sorted(contradictions))}",
            evidence_ids=evidence_ids,
        )

    kind = UnifiedActivationComparisonKind.EXACT_EQUIVALENT
    return UnifiedActivationComparison(
        comparison_id=_comparison_id(kind, v1_ids, unified_ids),
        kind=kind,
        v1_reference_ids=v1_ids,
        unified_reference_ids=unified_ids,
        shared_family=v1_ref.rule_family or unified_ref.rule_family,
        shared_program=v1_ref.program,
        shared_target=shared_target,
        shared_output_literal=v1_ref.output_literal,
        reason_code=_REASON_EXACT_EQUIVALENT,
        evidence_ids=evidence_ids,
    )


__all__ = ["compare_references"]
