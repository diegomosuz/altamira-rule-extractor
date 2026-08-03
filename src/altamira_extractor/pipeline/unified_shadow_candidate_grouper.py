"""Agrupador PURO de propuestas shadow EXACTAMENTE equivalentes (Fase
11 de la ampliacion semantica, `feat/unified-candidate-artifact-shadow`).

Agrupa `UnifiedShadowSourceMember` (ya resueltos, ya aprobados) en
componentes conexos de relaciones `CandidateRelationKind.EXACT_MATCH`
YA ESTABLECIDAS por Fase 9 -- nunca recalcula semejanza, nunca agrupa
por texto/target-sin-output/output-sin-target/familia parcial. Cada
componente se revalida ANTES de aceptarse como grupo: misma
`UnifiedRuleFamily`, mismo `program`, mismo `target`, mismo
`output_literal` -- si el componente conectado no es internamente
consistente en estos cuatro ejes, se produce un UNICO grupo
`INCONSISTENT_MEMBERS` con TODOS sus miembros (nunca se fabrican varios
grupos validos parciales en su lugar).

Ningun miembro se descarta ni se elige como "ganador": todos los
`member_ids` de un componente terminan en el MISMO grupo, con igual
jerarquia."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from ..contracts.unified_candidates_shadow import UnifiedShadowSourceMember


class _UnionFind:
    def __init__(self, items: Sequence[str]) -> None:
        self._parent = {item: item for item in items}

    def find(self, item: str) -> str:
        while self._parent[item] != item:
            self._parent[item] = self._parent[self._parent[item]]
            item = self._parent[item]
        return item

    def union(self, first: str, second: str) -> None:
        root_first, root_second = self.find(first), self.find(second)
        if root_first != root_second:
            self._parent[root_second] = root_first


def _digest(*parts: str) -> str:
    canonical = "\x1f".join(parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def group_id_for(
    *,
    member_ids: Sequence[str],
    rule_family: str,
    program: str,
    target: str | None,
    output_literal: str | None,
) -> str:
    """Unica funcion que genera `unified_shadow_candidate_id` --
    determinista (SHA-256 sobre member_ids ordenados + family +
    program + target + output_literal), nunca UUID/timestamp/`hash()`
    de Python."""
    canonical = _digest(
        "\x1f".join(sorted(member_ids)), rule_family, program, target or "", output_literal or ""
    )
    return f"group::{canonical}"


class ShadowMemberComponent:
    """Un componente conexo de `EXACT_MATCH` entre propuestas aprobadas
    -- puede ser internamente consistente (`is_consistent=True`) o no
    (`is_consistent=False`, con `inconsistency_reasons` explicando cada
    eje divergente). Nunca se descompone en subgrupos parciales."""

    __slots__ = ("member_ids", "is_consistent", "inconsistency_reasons")

    def __init__(
        self, *, member_ids: list[str], is_consistent: bool, inconsistency_reasons: list[str]
    ) -> None:
        self.member_ids = member_ids
        self.is_consistent = is_consistent
        self.inconsistency_reasons = inconsistency_reasons


def _check_component_consistency(
    members: Sequence[UnifiedShadowSourceMember],
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    families = {member.rule_family for member in members}
    if len(families) > 1:
        reasons.append(
            "INCONSISTENT_RULE_FAMILY::" + "|".join(sorted(f.value for f in families))
        )
    programs = {member.program for member in members}
    if len(programs) > 1:
        reasons.append("INCONSISTENT_PROGRAM::" + "|".join(sorted(programs)))
    targets = {member.target for member in members}
    if len(targets) > 1:
        reasons.append(
            "INCONSISTENT_TARGET::" + "|".join(sorted(t or "None" for t in targets))
        )
    outputs = {member.output_literal for member in members}
    if len(outputs) > 1:
        reasons.append(
            "INCONSISTENT_OUTPUT_LITERAL::" + "|".join(sorted(o or "None" for o in outputs))
        )
    return (len(reasons) == 0, sorted(reasons))


def group_shadow_members(
    members: Sequence[UnifiedShadowSourceMember],
    *,
    exact_match_reference_pairs: frozenset[frozenset[str]],
) -> list[ShadowMemberComponent]:
    """Punto de entrada puro. Nunca muta `members`. `exact_match_
    reference_pairs` son pares de `assessment_reference_id` (`Unified
    CandidateReference.unified_reference_id`) ya declarados `EXACT_MATCH`
    por Fase 9 (`CandidateRelation`) -- SOLO se consideran aristas entre
    dos miembros de `members` (propuestas aprobadas); una relacion
    `EXACT_MATCH` que involucre V1 o una referencia sin propuesta
    aprobada nunca participa aqui (es responsabilidad exclusiva del
    comparador de baseline, Fase 11 Parte 8)."""
    member_by_reference_id = {member.assessment_reference_id: member for member in members}
    reference_ids = list(member_by_reference_id)
    union_find = _UnionFind(reference_ids)

    for pair in exact_match_reference_pairs:
        if len(pair) != 2:
            continue
        left, right = sorted(pair)
        if left in member_by_reference_id and right in member_by_reference_id:
            union_find.union(left, right)

    components: dict[str, list[str]] = {}
    for reference_id in reference_ids:
        root = union_find.find(reference_id)
        components.setdefault(root, []).append(reference_id)

    result: list[ShadowMemberComponent] = []
    for reference_ids_in_component in components.values():
        component_members = [
            member_by_reference_id[reference_id] for reference_id in reference_ids_in_component
        ]
        is_consistent, reasons = _check_component_consistency(component_members)
        result.append(
            ShadowMemberComponent(
                member_ids=sorted(member.member_id for member in component_members),
                is_consistent=is_consistent,
                inconsistency_reasons=reasons,
            )
        )
    return sorted(result, key=lambda component: component.member_ids)
