"""Analizador PURO de conflictos DEMOSTRABLES entre
`UnifiedCandidateReference` de cualquier fuente (Fase 9 de la
ampliacion semantica, `feat/unified-candidate-promotion-assessment`).

Escanea el catalogo completo (cruzando fuentes) agrupando por un ancla
funcional compartida (`(decision_id, target)`, `(call_site_id, target)`
o `(program, target)` -- SIEMPRE junto con `target`, nunca
`decision_id`/`call_site_id` en solitario: un mismo `CALL` puede
demostrar legitimamente un valor `RETURNING` y un valor `BY REFERENCE`
simultaneos sobre DOS targets distintos, canales de salida
independientes, nunca una contradiccion) y declara un conflicto
UNICAMENTE cuando esa evidencia demuestra una contradiccion real --
nunca por:

- ausencia de literal (una referencia sin `output_literal` simplemente
  no participa en la comparacion de literales del grupo);
- ausencia de `decision_id`/`call_site_id`/`target` (esa referencia no
  entra en ese agrupamiento especifico, no genera conflicto por si
  sola);
- soporte parcial (`original_support` nunca se consulta aqui: lo unico
  relevante es si HAY un `output_literal` y si contradice a otro);
- diferencia de provenance, orden de deteccion o fuente no disponible
  (una fuente ausente simplemente no aporta referencias al grupo).

Deterministico: mismo catalogo de entrada siempre produce el mismo
conjunto de `CandidateConflict` -- nunca UUID/timestamp/`hash()` de
Python para los IDs."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence

from ..contracts.candidate_promotion_assessment import (
    CandidateConflict,
    CandidateConflictType,
    CandidateSource,
    UnifiedCandidateReference,
    UnifiedRuleFamily,
)


def _digest(*parts: str) -> str:
    canonical = "\x1f".join(parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _conflict_id_for(*, conflict_type: CandidateConflictType, reference_ids: Sequence[str]) -> str:
    ordered = sorted(reference_ids)
    return f"conflict::{_digest(conflict_type.value, *ordered)}"


def _sorted_unique(values: Sequence[str]) -> list[str]:
    return sorted(set(values))


def _group_by_str_key(
    references: Sequence[UnifiedCandidateReference],
    key: Callable[[UnifiedCandidateReference], str | None],
) -> dict[str, list[UnifiedCandidateReference]]:
    groups: dict[str, list[UnifiedCandidateReference]] = {}
    for reference in references:
        group_key = key(reference)
        if group_key is None:
            continue
        groups.setdefault(group_key, []).append(reference)
    return groups


def _group_by_program_target(
    references: Sequence[UnifiedCandidateReference],
) -> dict[tuple[str, str], list[UnifiedCandidateReference]]:
    groups: dict[tuple[str, str], list[UnifiedCandidateReference]] = {}
    for reference in references:
        if reference.program is None or reference.target is None:
            continue
        groups.setdefault((reference.program, reference.target), []).append(reference)
    return groups


def _group_by_anchor_and_target(
    references: Sequence[UnifiedCandidateReference],
    anchor: Callable[[UnifiedCandidateReference], str | None],
) -> dict[tuple[str, str], list[UnifiedCandidateReference]]:
    """Agrupa por `(anchor, target)`, nunca por `anchor` solo -- un
    mismo `decision_id`/`call_site_id` puede legitimamente demostrar
    valores DISTINTOS para targets DISTINTOS en la misma rama/llamada
    (p. ej. un `CALL` con salida `RETURNING` y salida `BY REFERENCE`
    simultaneas, cada una sobre su propio target: no son valores en
    competencia por el mismo hecho, nunca una contradiccion). Solo
    participan las referencias que exponen AMBOS campos -- ausencia de
    `target` nunca genera una entrada de grupo (nunca un conflicto)."""
    groups: dict[tuple[str, str], list[UnifiedCandidateReference]] = {}
    for reference in references:
        anchor_value = anchor(reference)
        if anchor_value is None or reference.target is None:
            continue
        groups.setdefault((anchor_value, reference.target), []).append(reference)
    return groups


def _contradictory_conflict_from_group(
    group: Sequence[UnifiedCandidateReference],
    *,
    conflict_type: CandidateConflictType,
    anchor_label: str,
    decision_id: str | None,
    call_site_id: str | None,
    target: str | None,
) -> CandidateConflict | None:
    """Comun a `SAME_DECISION_DIFFERENT_OUTPUT`/`SAME_CALL_SITE_
    DIFFERENT_OUTPUT`/`SAME_TARGET_CONTRADICTORY_OUTPUT`: dentro de un
    grupo ya anclado (mismo (decision_id,target)/(call_site_id,target)/
    (program,target)), compara UNICAMENTE las referencias que SI exponen
    `output_literal`
    -- si dos o mas literales distintos coexisten para el mismo ancla,
    es una contradiccion demostrable. `None` cuando no hay
    contradiccion."""
    with_literal = [reference for reference in group if reference.output_literal is not None]
    literals = _sorted_unique(
        [reference.output_literal for reference in with_literal if reference.output_literal]
    )
    if len(literals) < 2:
        return None
    reference_ids = _sorted_unique([reference.unified_reference_id for reference in with_literal])
    program = next((reference.program for reference in with_literal), None)
    return CandidateConflict(
        conflict_id=_conflict_id_for(conflict_type=conflict_type, reference_ids=reference_ids),
        reference_ids=reference_ids,
        conflict_type=conflict_type,
        program=program,
        decision_id=decision_id,
        call_site_id=call_site_id,
        target=target,
        competing_literals=literals,
        reason=(
            f"{len(with_literal)} referencias comparten {anchor_label} pero declaran "
            f"{len(literals)} output_literal distintos: {literals}"
        ),
    )


def _incompatible_rule_family_conflicts(
    references: Sequence[UnifiedCandidateReference],
) -> list[CandidateConflict]:
    """Misma `decision_id` con `UnifiedRuleFamily` distintas y no
    `UNKNOWN` en ambos lados -- una unica Decision no puede demostrar
    simultaneamente dos familias funcionales incompatibles."""
    conflicts: list[CandidateConflict] = []
    groups = _group_by_str_key(references, lambda reference: reference.decision_id)
    for decision_id, group in groups.items():
        families = {
            reference.rule_family
            for reference in group
            if reference.rule_family != UnifiedRuleFamily.UNKNOWN
        }
        if len(families) < 2:
            continue
        reference_ids = _sorted_unique([reference.unified_reference_id for reference in group])
        program = next((reference.program for reference in group), None)
        conflicts.append(
            CandidateConflict(
                conflict_id=_conflict_id_for(
                    conflict_type=CandidateConflictType.INCOMPATIBLE_RULE_FAMILY,
                    reference_ids=reference_ids,
                ),
                reference_ids=reference_ids,
                conflict_type=CandidateConflictType.INCOMPATIBLE_RULE_FAMILY,
                program=program,
                decision_id=decision_id,
                competing_literals=[],
                reason=(
                    f"decision_id {decision_id!r} compartido por referencias con familias "
                    f"funcionales incompatibles: {sorted(f.value for f in families)}"
                ),
            )
        )
    return conflicts


def _invalid_provenance_conflicts(
    references: Sequence[UnifiedCandidateReference],
) -> list[CandidateConflict]:
    """Una referencia DETERMINISTIC de V2/INTERPROCEDURAL sin ningun
    `evidence_id` es estructuralmente invalida: el propio contrato de
    origen (`V2ShadowCandidate`/`InterproceduralRuleCandidate`) exige al
    menos una pieza de evidencia para `support=DETERMINISTIC` -- una
    referencia que la incumple solo puede deberse a un adaptador
    defectuoso, nunca a un caso legitimo. V1 se excluye deliberadamente:
    no expone `evidence_ids` por diseno (Q0 no produce evidencia
    granular), ausencia legitima, no un conflicto."""
    conflicts: list[CandidateConflict] = []
    for reference in references:
        if reference.source == CandidateSource.V1:
            continue
        if reference.original_support != "DETERMINISTIC":
            continue
        if reference.evidence_ids:
            continue
        conflicts.append(
            CandidateConflict(
                conflict_id=_conflict_id_for(
                    conflict_type=CandidateConflictType.INVALID_PROVENANCE,
                    reference_ids=[reference.unified_reference_id],
                ),
                reference_ids=[reference.unified_reference_id],
                conflict_type=CandidateConflictType.INVALID_PROVENANCE,
                program=reference.program,
                decision_id=reference.decision_id,
                call_site_id=reference.call_site_id,
                target=reference.target,
                competing_literals=[],
                reason=(
                    f"referencia {reference.unified_reference_id!r} (source="
                    f"{reference.source.value}) declara original_support=DETERMINISTIC sin "
                    "ningun evidence_id -- provenance invalida"
                ),
            )
        )
    return conflicts


def analyze_candidate_conflicts(
    references: Sequence[UnifiedCandidateReference],
) -> list[CandidateConflict]:
    """Punto de entrada puro. Nunca muta `references`. Determinista:
    mismo catalogo de entrada siempre produce el mismo resultado,
    independientemente del orden de `references`."""
    conflicts: list[CandidateConflict] = []

    for (decision_id, target), group in _group_by_anchor_and_target(
        references, lambda reference: reference.decision_id
    ).items():
        conflict = _contradictory_conflict_from_group(
            group,
            conflict_type=CandidateConflictType.SAME_DECISION_DIFFERENT_OUTPUT,
            anchor_label="(decision_id, target)",
            decision_id=decision_id,
            call_site_id=None,
            target=target,
        )
        if conflict is not None:
            conflicts.append(conflict)

    for (call_site_id, target), group in _group_by_anchor_and_target(
        references, lambda reference: reference.call_site_id
    ).items():
        conflict = _contradictory_conflict_from_group(
            group,
            conflict_type=CandidateConflictType.SAME_CALL_SITE_DIFFERENT_OUTPUT,
            anchor_label="(call_site_id, target)",
            decision_id=None,
            call_site_id=call_site_id,
            target=target,
        )
        if conflict is not None:
            conflicts.append(conflict)

    for (_program, target), group in _group_by_program_target(references).items():
        conflict = _contradictory_conflict_from_group(
            group,
            conflict_type=CandidateConflictType.SAME_TARGET_CONTRADICTORY_OUTPUT,
            anchor_label="(program, target)",
            decision_id=None,
            call_site_id=None,
            target=target,
        )
        if conflict is not None:
            conflicts.append(conflict)

    conflicts.extend(_incompatible_rule_family_conflicts(references))
    conflicts.extend(_invalid_provenance_conflicts(references))
    return sorted(conflicts, key=lambda conflict: conflict.conflict_id)


def conflicting_pairs(conflicts: Sequence[CandidateConflict]) -> frozenset[frozenset[str]]:
    """Todos los pares (no ordenados) de `reference_id` que aparecen
    juntos en al menos un conflicto -- consumido por
    `candidate_relation_analyzer.py` para que ningun par ya identificado
    como conflictivo se reclasifique como `EXACT_MATCH`/`RELATED`."""
    pairs: set[frozenset[str]] = set()
    for conflict in conflicts:
        ids = conflict.reference_ids
        for i, left in enumerate(ids):
            for right in ids[i + 1 :]:
                pairs.add(frozenset({left, right}))
    return frozenset(pairs)
