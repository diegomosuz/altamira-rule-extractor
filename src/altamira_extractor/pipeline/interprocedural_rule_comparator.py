"""Comparador PURO, conservador, de solo lectura, entre candidatos
interprocedurales (Fase 8) y los candidatos V1 (`RuleCandidate`)/V2
(`V2ShadowCandidate`) ya calculados (Fase 5, shadow mode). Componente
SEPARADO de los detectores (`interprocedural_rule_detectors.py`) y del
registry (`interprocedural_rule_detector_registry.py`): ningun detector
compara candidatos directamente.

Nunca modifica `CandidateArtifact` V1 ni `V2ShadowCandidatesArtifact`,
nunca inventa equivalencia por semejanza textual -- solo por identidad
estructural ya demostrada (programa, target, literal de salida). Cada
`InterproceduralRuleCandidate` recibe EXACTAMENTE una comparacion
(invariante de artefacto, `contracts/interprocedural_rule_candidates.py`).

Auditoria de cierre (Fase 8, hardening): la disponibilidad de cada
fuente (`v1_candidates_artifact`/`v2_candidates`, `None` = fuente nunca
estuvo disponible para este run) se distingue EXPLICITAMENTE de "fuente
disponible mas cero candidatos relacionados" -- nunca se fabrica una
comparacion negativa (`InterproceduralRelationStatus.NOT_FOUND`) cuando
en realidad la fuente no pudo evaluarse
(`InterproceduralRelationStatus.NOT_EVALUATED`). `v1_relation`/
`v2_relation` se calculan de forma INDEPENDIENTE para cada candidato --
`status` (la clasificacion principal, con prioridad V1 > V2) se deriva
de ambas via `derive_comparison_status`, unica fuente de verdad
compartida con el validador de coherencia del contrato.

`RuleCandidate` (V1) no expone una variable objetivo directamente --
solo `outcome_code` (el literal), `paragraph_name` y `paragraph_id`
(Neo4j-shaped, del que se extrae el `program_name` real por POSICION
textual fija, nunca recalculando el ID: indice 3 del split por `"::"`,
ver `docs/INTERPROCEDURAL_RULE_DETECTORS_SHADOW.md`). Por eso la
comparacion V1 usa (programa, paragraph, outcome_code) como clave; la
comparacion V2 usa (programa, target, resolved_literal), ya que
`V2ShadowCandidate` SI expone `target_variable`/`target_qualified_name`."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from ..contracts.candidate import CandidateArtifact, RuleCandidate
from ..contracts.interprocedural_rule_candidates import (
    InterproceduralCandidateComparison,
    InterproceduralCandidateSupport,
    InterproceduralComparisonStatus,
    InterproceduralRelationStatus,
    InterproceduralRuleCandidate,
    derive_comparison_status,
)
from ..contracts.v2_shadow_candidates import V2ShadowCandidate


def _comparison_id_for(*, candidate_id: str, status: InterproceduralComparisonStatus) -> str:
    digest = hashlib.sha256(f"{candidate_id}\x1f{status.value}".encode()).hexdigest()[:24]
    return f"comparison::{digest}"


def _program_name_from_paragraph_id(paragraph_id: str) -> str | None:
    """Extrae el `program_name` real de un `paragraph_id`/`decision_id`
    Neo4j-shaped por POSICION textual fija -- nunca recalculando el ID
    (formato: `program::{country}::{logical_name}::{program_name}::
    {version}::{hash12}::paragraph::{NAME}[...]`, ver
    `pipeline/identifiers.py::ProgramIdentity.program_id`). `None` si el
    formato no coincide (nunca se adivina)."""
    parts = paragraph_id.split("::")
    if len(parts) < 4 or parts[0] != "program":
        return None
    return parts[3]


def _v1_candidates_for_programs(
    v1_candidates: Sequence[RuleCandidate], *, programs: set[str]
) -> list[RuleCandidate]:
    matches = []
    for candidate in v1_candidates:
        program_name = _program_name_from_paragraph_id(candidate.paragraph_id)
        if program_name in programs:
            matches.append(candidate)
    return matches


def _v2_candidates_for_programs(
    v2_candidates: Sequence[V2ShadowCandidate], *, programs: set[str]
) -> list[V2ShadowCandidate]:
    return [candidate for candidate in v2_candidates if candidate.program in programs]


def _resolve_v1_relation(
    candidate: InterproceduralRuleCandidate,
    *,
    v1_available: bool,
    v1_candidates: Sequence[RuleCandidate],
    programs: set[str],
) -> tuple[InterproceduralRelationStatus, RuleCandidate | None]:
    """Dimension V1, resuelta de forma independiente de V2. `v1_available
    =False` (fuente nunca cargada para este run) siempre produce
    `NOT_EVALUATED` -- nunca se evalua `v1_candidates` en ese caso
    (deberia estar vacia de todos modos, pero la disponibilidad manda,
    nunca el contenido)."""
    if not v1_available:
        return InterproceduralRelationStatus.NOT_EVALUATED, None

    v1_scope = _v1_candidates_for_programs(v1_candidates, programs=programs)
    v1_matched = sorted(
        (
            c
            for c in v1_scope
            if candidate.output_literal is not None and c.outcome_code == candidate.output_literal
        ),
        key=lambda c: c.candidate_id,
    )
    if v1_matched:
        return InterproceduralRelationStatus.MATCHED, v1_matched[0]

    v1_related = sorted(v1_scope, key=lambda c: c.candidate_id)
    if v1_related:
        return InterproceduralRelationStatus.RELATED, v1_related[0]

    return InterproceduralRelationStatus.NOT_FOUND, None


def _resolve_v2_relation(
    candidate: InterproceduralRuleCandidate,
    *,
    v2_available: bool,
    v2_candidates: Sequence[V2ShadowCandidate],
    programs: set[str],
) -> tuple[InterproceduralRelationStatus, V2ShadowCandidate | None]:
    """Dimension V2, resuelta de forma independiente de V1. Misma regla
    de disponibilidad que `_resolve_v1_relation`."""
    if not v2_available:
        return InterproceduralRelationStatus.NOT_EVALUATED, None

    v2_scope = _v2_candidates_for_programs(v2_candidates, programs=programs)
    v2_matched = sorted(
        (
            c
            for c in v2_scope
            if candidate.output_literal is not None
            and c.resolved_literal == candidate.output_literal
            and candidate.target in (c.target_variable, c.target_qualified_name)
        ),
        key=lambda c: c.candidate_id,
    )
    if v2_matched:
        return InterproceduralRelationStatus.MATCHED, v2_matched[0]

    v2_related = sorted(
        (c for c in v2_scope if candidate.target in (c.target_variable, c.target_qualified_name)),
        key=lambda c: c.candidate_id,
    )
    if v2_related:
        return InterproceduralRelationStatus.RELATED, v2_related[0]

    return InterproceduralRelationStatus.NOT_FOUND, None


def _reason_for(
    *,
    candidate: InterproceduralRuleCandidate,
    status: InterproceduralComparisonStatus,
    v1_relation: InterproceduralRelationStatus,
    v2_relation: InterproceduralRelationStatus,
    v1_match: RuleCandidate | None,
    v2_match: V2ShadowCandidate | None,
) -> str:
    if status == InterproceduralComparisonStatus.MATCHED_V1:
        assert v1_match is not None
        program_name = _program_name_from_paragraph_id(v1_match.paragraph_id)
        extra = ""
        if v2_relation == InterproceduralRelationStatus.MATCHED:
            extra = " (tambien MATCHED contra V2, ver v2_relation/v2_candidate_id)"
        elif v2_relation == InterproceduralRelationStatus.RELATED:
            extra = " (tambien RELATED contra V2, ver v2_relation/v2_candidate_id)"
        return (
            f"Mismo programa ({program_name!r}) y mismo literal de salida "
            f"({candidate.output_literal!r}) demostrado tanto por el candidato "
            f"interprocedural como por Q0 (V1, outcome_code={v1_match.outcome_code!r})." + extra
        )
    if status == InterproceduralComparisonStatus.MATCHED_V2:
        assert v2_match is not None
        return (
            f"Mismo programa ({v2_match.program!r}), mismo target ({candidate.target!r}) y "
            f"mismo literal de salida ({candidate.output_literal!r}) demostrado tanto por "
            "el candidato interprocedural como por un detector V2 (shadow)."
        )
    if status == InterproceduralComparisonStatus.RELATED_V1:
        assert v1_match is not None
        program_name = _program_name_from_paragraph_id(v1_match.paragraph_id)
        return (
            f"Comparte programa ({program_name!r}) con un candidato Q0 (V1, "
            f"{v1_match.candidate_id!r}), pero el literal/target no puede verificarse como "
            "identico -- nunca se afirma equivalencia por semejanza textual."
        )
    if status == InterproceduralComparisonStatus.RELATED_V2:
        assert v2_match is not None
        return (
            f"Comparte programa ({v2_match.program!r}) y target ({candidate.target!r}) con un "
            f"detector V2 ({v2_match.candidate_id!r}), pero el literal no puede verificarse "
            "como identico."
        )
    if status == InterproceduralComparisonStatus.INTERPROCEDURAL_ONLY:
        return (
            f"Ningun candidato V1/V2 comparable existe para {candidate.candidate_id!r} -- AMBAS "
            "fuentes se evaluaron por completo (v1_relation=NOT_FOUND, v2_relation=NOT_FOUND): la "
            "evidencia depende esencialmente de cruzar una frontera CALL, invisible para Q0 "
            "(V1, intraparrafo) y para los detectores V2 registrados (tambien intraprograma)."
        )
    # NOT_EVALUATED
    unavailable = []
    if v1_relation == InterproceduralRelationStatus.NOT_EVALUATED:
        unavailable.append("V1 (artifacts/06-candidates.json ausente)")
    if v2_relation == InterproceduralRelationStatus.NOT_EVALUATED:
        unavailable.append("V2 (artifacts/04-semantic-graph.json ausente o V1 tambien ausente)")
    sources = " y ".join(unavailable) if unavailable else "una fuente"
    return (
        f"No se pudo evaluar la comparacion completa de {candidate.candidate_id!r}: {sources} "
        "nunca estuvo disponible para este run -- nunca se fabrica una comparacion negativa "
        "(INTERPROCEDURAL_ONLY) en ausencia de una fuente real."
    )


def build_comparison(
    candidate: InterproceduralRuleCandidate,
    *,
    v1_available: bool,
    v1_candidates: Sequence[RuleCandidate],
    v2_available: bool,
    v2_candidates: Sequence[V2ShadowCandidate],
) -> InterproceduralCandidateComparison:
    programs = {candidate.caller_program}
    if candidate.callee_program is not None:
        programs.add(candidate.callee_program)

    if candidate.support == InterproceduralCandidateSupport.BLOCKED:
        status = InterproceduralComparisonStatus.BLOCKED
        return InterproceduralCandidateComparison(
            comparison_id=_comparison_id_for(candidate_id=candidate.candidate_id, status=status),
            interprocedural_candidate_id=candidate.candidate_id,
            v1_relation=InterproceduralRelationStatus.NOT_EVALUATED,
            v2_relation=InterproceduralRelationStatus.NOT_EVALUATED,
            status=status,
            reason=(
                f"Candidato {candidate.candidate_id!r} tiene support=BLOCKED (barriers="
                f"{[b.value for b in candidate.barriers]}): nunca se compara contra V1/V2, "
                "que exigen un valor demostrado."
            ),
        )

    v1_relation, v1_match = _resolve_v1_relation(
        candidate, v1_available=v1_available, v1_candidates=v1_candidates, programs=programs
    )
    v2_relation, v2_match = _resolve_v2_relation(
        candidate, v2_available=v2_available, v2_candidates=v2_candidates, programs=programs
    )
    status = derive_comparison_status(v1_relation, v2_relation)

    shared_program: str | None = None
    shared_target: str | None = None
    shared_literal: str | None = None
    if status in (
        InterproceduralComparisonStatus.MATCHED_V1,
        InterproceduralComparisonStatus.RELATED_V1,
    ):
        assert v1_match is not None
        shared_program = _program_name_from_paragraph_id(v1_match.paragraph_id)
        if status == InterproceduralComparisonStatus.MATCHED_V1:
            shared_literal = candidate.output_literal
    elif status in (
        InterproceduralComparisonStatus.MATCHED_V2,
        InterproceduralComparisonStatus.RELATED_V2,
    ):
        assert v2_match is not None
        shared_program = v2_match.program
        shared_target = candidate.target
        if status == InterproceduralComparisonStatus.MATCHED_V2:
            shared_literal = candidate.output_literal

    reason = _reason_for(
        candidate=candidate,
        status=status,
        v1_relation=v1_relation,
        v2_relation=v2_relation,
        v1_match=v1_match,
        v2_match=v2_match,
    )

    return InterproceduralCandidateComparison(
        comparison_id=_comparison_id_for(candidate_id=candidate.candidate_id, status=status),
        interprocedural_candidate_id=candidate.candidate_id,
        v1_candidate_id=v1_match.candidate_id if v1_match is not None else None,
        v2_candidate_id=v2_match.candidate_id if v2_match is not None else None,
        v1_relation=v1_relation,
        v2_relation=v2_relation,
        status=status,
        reason=reason,
        shared_program=shared_program,
        shared_target=shared_target,
        shared_literal=shared_literal,
    )


def build_comparisons(
    candidates: Sequence[InterproceduralRuleCandidate],
    *,
    v1_candidates_artifact: CandidateArtifact | None,
    v2_candidates: Sequence[V2ShadowCandidate] | None,
) -> list[InterproceduralCandidateComparison]:
    """`v1_candidates_artifact is None`/`v2_candidates is None` significa
    "fuente nunca disponible para este run" -- DISTINTO de una lista
    vacia (fuente disponible, cero candidatos). Esa distincion se
    propaga intacta a cada `InterproceduralCandidateComparison` via
    `v1_relation`/`v2_relation=NOT_EVALUATED` (auditoria de cierre)."""
    v1_available = v1_candidates_artifact is not None
    v1_list = v1_candidates_artifact.candidates if v1_candidates_artifact is not None else []
    v2_available = v2_candidates is not None
    v2_list = list(v2_candidates) if v2_candidates is not None else []
    comparisons = [
        build_comparison(
            candidate,
            v1_available=v1_available,
            v1_candidates=v1_list,
            v2_available=v2_available,
            v2_candidates=v2_list,
        )
        for candidate in candidates
    ]
    return sorted(comparisons, key=lambda comparison: comparison.comparison_id)
