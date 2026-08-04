"""Adaptador PURO de UN `UnifiedShadowCandidateGroup` y sus members
(Fase 13 Parte 5, `feat/unified-shadow-downstream-pipeline`).

Construye `ShadowGroupContextView`: la vista MINIMA que
`unified_shadow_context_assembler.py` (Parte 6) necesita para ensamblar
un `ContextPackage` shadow -- la UNION validada de todos los members
del grupo (family, program, paragraphs, target, output, evidence,
provenance, source candidate IDs, review decision IDs). NUNCA elige un
member "ganador": todos los `member_ids` conservan igual jerarquia.
NUNCA fabrica un `decision_id`/`paragraph`/`target`/literal ausente.
NUNCA reinterpreta `UnifiedRuleFamily` por semejanza textual (se copia
tal cual del grupo real, Fase 11).

Puro: sin filesystem, sin Neo4j, sin LLM, nunca muta `group`/
`members_by_id`."""

from __future__ import annotations

from dataclasses import dataclass

from ..contracts.candidate_promotion_assessment import UnifiedRuleFamily
from ..contracts.unified_candidates_shadow import (
    UnifiedShadowCandidateGroup,
    UnifiedShadowSourceMember,
)


@dataclass(frozen=True)
class ShadowGroupContextView:
    """Vista minima de UN grupo -- union validada de sus members, sin
    elegir un "ganador". Consumida exclusivamente por
    `unified_shadow_context_assembler.py` (Parte 6)."""

    group_id: str
    rule_family: UnifiedRuleFamily
    program: str
    paragraphs: tuple[str, ...]
    target: str | None
    output_literal: str | None
    member_ids: tuple[str, ...]
    source_candidate_ids: tuple[str, ...]
    review_decision_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    provenance_references: tuple[str, ...]
    members: tuple[UnifiedShadowSourceMember, ...]


def adapt_group_to_context_view(
    group: UnifiedShadowCandidateGroup,
    *,
    members_by_id: dict[str, UnifiedShadowSourceMember],
) -> ShadowGroupContextView:
    """Punto de entrada puro. `members` conserva TODOS los members del
    grupo en el orden real de `group.member_ids` -- ninguno se
    descarta ni se elige como representante. Nunca falla por si misma
    (los members siempre existen, ya verificados por Fase 12 Parte 6);
    la deteccion de ambigueedad -- p. ej. paragraphs de members
    distintos -- es responsabilidad del ensamblador (Parte 6), nunca
    de este adaptador."""
    members = tuple(members_by_id[member_id] for member_id in group.member_ids)
    paragraphs = tuple(sorted({m.paragraph for m in members if m.paragraph}))
    return ShadowGroupContextView(
        group_id=group.unified_shadow_candidate_id,
        rule_family=group.rule_family,
        program=group.program,
        paragraphs=paragraphs,
        target=group.target,
        output_literal=group.output_literal,
        member_ids=tuple(sorted(group.member_ids)),
        source_candidate_ids=tuple(sorted({m.source_candidate_id for m in members})),
        review_decision_ids=tuple(sorted({m.review_decision_id for m in members})),
        evidence_ids=tuple(sorted(set(group.evidence_ids))),
        provenance_references=tuple(sorted(set(group.provenance_references))),
        members=members,
    )
