"""Detectores V2 ejecutados exclusivamente en shadow mode (Fase 5 de la
ampliacion semantica, `feat/v2-detectors-shadow-mode`).

Cada detector es una funcion pura `V2DetectorContext -> list[V2ShadowCandidate]`.
Ninguno reimplementa Q0 (`queries/v1/q0_candidates.cypher`): Q0 sigue
siendo la unica fuente de `CandidateArtifact` V1, consultado aqui
unicamente como baseline ya calculado (`ctx.v1_candidates`).

Principio compartido por los tres detectores: un `PropagatedValueFact` de
tipo `DIRECT_LITERAL`/`PROPAGATED_LITERAL`/`CONDITION_LITERAL` ya es, por
construccion de `semantic_propagation_analyzer.py` (Fase 4), una cadena
SIN barrera intermedia y SIN invalidacion posterior -- ningun detector
necesita reverificar eso: si existiera una barrera o una invalidacion en
el camino, Fase 4 ya habria producido `UNRESOLVED_COPY`/
`INVALIDATED_VALUE`/`BLOCKED_PROPAGATION` en su lugar. Por eso "no
detectar valor invalidado"/"no ignorar barreras" se cumple simplemente
filtrando por `fact_kind`, nunca reimplementando el analisis de flujo."""

from __future__ import annotations

import hashlib

from ..contracts.canonical import CanonicalStatement
from ..contracts.enums import StatementKind
from ..contracts.semantic_effects import SemanticEffectKind
from ..contracts.semantic_graph import GraphNode
from ..contracts.semantic_propagation import PropagatedValueFact, PropagationFactKind
from ..contracts.v2_shadow_candidates import (
    V2CandidateSourceReference,
    V2CandidateSupport,
    V2RuleType,
    V2ShadowCandidate,
)
from .v2_detector_context import V2DetectorContext

_RETURN_CODE_TAG = "return_code"
_LITERAL_FACT_KINDS = frozenset(
    {
        PropagationFactKind.DIRECT_LITERAL,
        PropagationFactKind.PROPAGATED_LITERAL,
        PropagationFactKind.CONDITION_LITERAL,
    }
)
_BLOCKED_FACT_KINDS = frozenset(
    {PropagationFactKind.UNRESOLVED_COPY, PropagationFactKind.BLOCKED_PROPAGATION}
)

# STATE_CHANGE_RULE: constante explicita y documentada (Fase 7) -- el
# cambio estructural esta demostrado (mismo criterio DETERMINISTIC que
# RETURN_CODE_RULE), pero su relevancia funcional no esta garantizada
# (no toda escritura de un data item ordinario es una regla de negocio).
# Nunca varia por programa/paragraph/target.
STATE_CHANGE_DETECTOR_SCORE = 0.7


def _candidate_digest(*parts: str) -> str:
    canonical = "\x1f".join(parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def candidate_id_for(
    *,
    detector_id: str,
    detector_version: str,
    program: str,
    paragraph: str,
    decision_id: str | None,
    anchor_statement_id: str,
    target_key: str,
    resolved_literal: str | None,
) -> str:
    """Fase 8: deterministico por construccion, nunca UUID/timestamp/
    `hash()` de Python. `target_key` debe ser `target_qualified_name`
    cuando exista, si no `target_variable` (misma preferencia que
    `V2DetectorContext.facts_by_target`)."""
    digest = _candidate_digest(
        detector_id,
        detector_version,
        program,
        paragraph,
        decision_id or "",
        anchor_statement_id,
        target_key,
        resolved_literal or "",
    )
    return f"v2::{detector_id}::{digest}"


def _nearest_enclosing_decision(
    ctx: V2DetectorContext, statement: CanonicalStatement
) -> CanonicalStatement | None:
    """Camina `parent_statement_id` desde `statement` y devuelve el
    primer ancestro (o el propio `statement`, si el es una decision) cuyo
    `kind` sea IF/EVALUATE -- nunca cruza de paragraph (`parent_statement_id`
    solo enlaza dentro del mismo `CanonicalParagraph.statements`)."""
    current: CanonicalStatement | None = statement
    visited: set[str] = set()
    while current is not None:
        if current.statement_id in visited:
            return None
        visited.add(current.statement_id)
        if current.kind in (StatementKind.IF, StatementKind.EVALUATE):
            return current
        if current.parent_statement_id is None:
            return None
        current = ctx.statement_by_id.get(current.parent_statement_id)
    return None


def _decision_graph_node_for(
    ctx: V2DetectorContext, *, program: str, paragraph: str, decision_statement: CanonicalStatement
) -> GraphNode | None:
    candidates = ctx.decision_nodes_by_paragraph_key.get((program, paragraph), [])
    for node in candidates:
        if node.properties.get("line_start") == decision_statement.line_start:
            return node
    return None


def _data_item_semantic_tag(
    ctx: V2DetectorContext, *, program: str, qualified_name: str | None
) -> str | None:
    if qualified_name is None:
        return None
    node = ctx.data_item_node_by_key.get((program, qualified_name))
    if node is None:
        return None
    tag = node.properties.get("semantic_tag")
    return tag if isinstance(tag, str) else None


def _source_reference_for_step_statement(
    ctx: V2DetectorContext, *, program: str, paragraph: str, statement_id: str
) -> V2CandidateSourceReference:
    statement = ctx.statement_by_id.get(statement_id)
    return V2CandidateSourceReference(
        program=program,
        paragraph=paragraph,
        statement_id=statement_id,
        source_file=statement.source_file if statement else None,
        line_start=statement.line_start if statement else None,
        line_end=statement.line_end if statement else None,
        branch_kind=(
            statement.branch_kind.value if statement and statement.branch_kind else None
        ),
    )


def _fact_source_references(
    ctx: V2DetectorContext, fact: PropagatedValueFact
) -> list[V2CandidateSourceReference]:
    statement_ids = {step.statement_id for step in fact.derivation_steps}
    statement_ids.add(fact.source_reference.statement_id)
    references = [
        _source_reference_for_step_statement(
            ctx, program=fact.program, paragraph=fact.paragraph, statement_id=statement_id
        )
        for statement_id in statement_ids
    ]
    references.sort(key=lambda ref: ref._sort_key())  # noqa: SLF001
    return references


def _comparable_v1_ids_for_decision(ctx: V2DetectorContext, decision_id: str | None) -> list[str]:
    if decision_id is None:
        return []
    return sorted(
        candidate.candidate_id
        for candidate in ctx.v1_candidates.candidates
        if candidate.decision_id == decision_id
    )


# ---------------------------------------------------------------------------
# V2_RETURN_CODE_PROPAGATION (Fase 5)
# ---------------------------------------------------------------------------

DETECTOR_ID_RETURN_CODE_PROPAGATION = "V2_RETURN_CODE_PROPAGATION"
DETECTOR_VERSION_RETURN_CODE_PROPAGATION = "1.0"


def _origin_statement(
    ctx: V2DetectorContext, fact: PropagatedValueFact
) -> CanonicalStatement | None:
    """Statement que ancla el candidato: el primer paso de la cadena de
    derivacion cuando existe (facts DIRECT_LITERAL/PROPAGATED_LITERAL/
    CONDITION_LITERAL, que Fase 4 siempre puebla con al menos un paso), o
    -- cuando la propagacion no resolvio ninguna cadena (UNRESOLVED_COPY/
    BLOCKED_PROPAGATION, `derivation_steps` siempre vacio por
    construccion de `semantic_propagation_analyzer.py`) -- el statement
    que intento la copia (`fact.source_reference.statement_id`)."""
    if fact.derivation_steps:
        return ctx.statement_by_id.get(fact.derivation_steps[0].statement_id)
    return ctx.statement_by_id.get(fact.source_reference.statement_id)


def detect_return_code_propagation(ctx: V2DetectorContext) -> list[V2ShadowCandidate]:
    candidates_by_id: dict[str, V2ShadowCandidate] = {}

    for program in ctx.canonical_programs:
        program_name = program.program_name
        for paragraph in program.paragraphs:
            paragraph_facts = [
                fact
                for facts in ctx.facts_by_target.values()
                for fact in facts
                if fact.program == program_name and fact.paragraph == paragraph.name
            ]
            for fact in paragraph_facts:
                semantic_tag = _data_item_semantic_tag(
                    ctx, program=program_name, qualified_name=fact.target_qualified_name
                )
                if semantic_tag != _RETURN_CODE_TAG:
                    continue

                origin = _origin_statement(ctx, fact)
                if origin is None:
                    continue
                decision_statement = _nearest_enclosing_decision(ctx, origin)

                if fact.fact_kind in _LITERAL_FACT_KINDS and fact.literal is not None:
                    if decision_statement is None:
                        continue  # literal fuera de una decision: no se detecta.
                    decision_node = _decision_graph_node_for(
                        ctx, program=program_name, paragraph=paragraph.name,
                        decision_statement=decision_statement,
                    )
                    decision_id = decision_node.id if decision_node else None
                    target_key = fact.target_qualified_name or fact.target_variable
                    candidate_id = candidate_id_for(
                        detector_id=DETECTOR_ID_RETURN_CODE_PROPAGATION,
                        detector_version=DETECTOR_VERSION_RETURN_CODE_PROPAGATION,
                        program=program_name, paragraph=paragraph.name, decision_id=decision_id,
                        anchor_statement_id=origin.statement_id, target_key=target_key,
                        resolved_literal=fact.literal,
                    )
                    effect_ids = sorted(
                        {
                            effect.effect_id
                            for step in fact.derivation_steps
                            for effect in ctx.effects_by_source_statement.get(
                                step.statement_id, []
                            )
                        }
                    )
                    candidate = V2ShadowCandidate(
                        candidate_id=candidate_id,
                        detector_id=DETECTOR_ID_RETURN_CODE_PROPAGATION,
                        detector_version=DETECTOR_VERSION_RETURN_CODE_PROPAGATION,
                        rule_type=V2RuleType.RETURN_CODE_RULE,
                        support=V2CandidateSupport.DETERMINISTIC,
                        detector_score=1.0,
                        program=program_name, paragraph=paragraph.name,
                        anchor_statement_id=origin.statement_id, decision_id=decision_id,
                        target_variable=fact.target_variable,
                        target_qualified_name=fact.target_qualified_name,
                        resolved_literal=fact.literal,
                        semantic_effect_ids=effect_ids,
                        propagation_fact_ids=[fact.fact_id],
                        source_references=_fact_source_references(ctx, fact),
                        diagnostic_codes=[],
                        reason=(
                            f"Decision en {paragraph.name!r} determina, via propagacion "
                            f"({fact.fact_kind.value}), que {target_key!r} (return_code) "
                            f"recibe {fact.literal!r}."
                        ),
                        comparable_v1_candidate_ids=_comparable_v1_ids_for_decision(
                            ctx, decision_id
                        ),
                    )
                elif fact.fact_kind in _BLOCKED_FACT_KINDS and decision_statement is not None:
                    decision_node = _decision_graph_node_for(
                        ctx, program=program_name, paragraph=paragraph.name,
                        decision_statement=decision_statement,
                    )
                    decision_id = decision_node.id if decision_node else None
                    target_key = fact.target_qualified_name or fact.target_variable
                    diagnostic_codes = sorted(
                        set(fact.diagnostic_codes) | {"V2_RETURN_CODE_TARGET_NOT_RESOLVED"}
                    )
                    candidate_id = candidate_id_for(
                        detector_id=DETECTOR_ID_RETURN_CODE_PROPAGATION,
                        detector_version=DETECTOR_VERSION_RETURN_CODE_PROPAGATION,
                        program=program_name, paragraph=paragraph.name, decision_id=decision_id,
                        anchor_statement_id=origin.statement_id, target_key=target_key,
                        resolved_literal=None,
                    )
                    candidate = V2ShadowCandidate(
                        candidate_id=candidate_id,
                        detector_id=DETECTOR_ID_RETURN_CODE_PROPAGATION,
                        detector_version=DETECTOR_VERSION_RETURN_CODE_PROPAGATION,
                        rule_type=V2RuleType.RETURN_CODE_RULE,
                        support=V2CandidateSupport.BLOCKED,
                        detector_score=0.0,
                        program=program_name, paragraph=paragraph.name,
                        anchor_statement_id=origin.statement_id, decision_id=decision_id,
                        target_variable=fact.target_variable,
                        target_qualified_name=fact.target_qualified_name,
                        resolved_literal=None,
                        semantic_effect_ids=[],
                        propagation_fact_ids=[fact.fact_id],
                        source_references=_fact_source_references(ctx, fact),
                        diagnostic_codes=diagnostic_codes,
                        reason=(
                            f"Decision en {paragraph.name!r} alcanza un target return_code "
                            f"({target_key!r}) pero la propagacion no resolvio un literal "
                            f"({fact.fact_kind.value})."
                        ),
                        comparable_v1_candidate_ids=_comparable_v1_ids_for_decision(
                            ctx, decision_id
                        ),
                    )
                else:
                    continue

                existing = candidates_by_id.get(candidate.candidate_id)
                if existing is None:
                    candidates_by_id[candidate.candidate_id] = candidate
                elif existing != candidate:
                    candidates_by_id[candidate.candidate_id] = _merge_candidate_evidence(
                        existing, candidate
                    )

    return sorted(candidates_by_id.values(), key=lambda candidate: candidate.candidate_id)


def _merge_candidate_evidence(
    first: V2ShadowCandidate, second: V2ShadowCandidate
) -> V2ShadowCandidate:
    """Fase 9 (deduplicacion intradetector): dos observaciones distintas
    de la MISMA identidad (mismo candidate_id, ver Fase 8) se fusionan
    conservando la union ordenada de evidencia -- nunca se descarta una
    silenciosamente. Solo ocurre cuando el mismo target recibe el mismo
    literal por mas de un camino de propagacion dentro de la misma
    decision (p. ej. dos ramas WHEN con el mismo resultado)."""
    merged_references: dict[tuple[str, str, str, str, str, str], V2CandidateSourceReference] = {}
    for reference in (*first.source_references, *second.source_references):
        merged_references[reference._sort_key()] = reference  # noqa: SLF001
    ordered_references = [merged_references[key] for key in sorted(merged_references)]

    return first.model_copy(
        update={
            "semantic_effect_ids": sorted(
                set(first.semantic_effect_ids) | set(second.semantic_effect_ids)
            ),
            "propagation_fact_ids": sorted(
                set(first.propagation_fact_ids) | set(second.propagation_fact_ids)
            ),
            "source_references": ordered_references,
            "diagnostic_codes": sorted(set(first.diagnostic_codes) | set(second.diagnostic_codes)),
        }
    )


# ---------------------------------------------------------------------------
# V2_LEVEL_88_RETURN_CODE (Fase 6)
# ---------------------------------------------------------------------------

DETECTOR_ID_LEVEL_88_RETURN_CODE = "V2_LEVEL_88_RETURN_CODE"
DETECTOR_VERSION_LEVEL_88_RETURN_CODE = "1.0"


def detect_level_88_return_code(ctx: V2DetectorContext) -> list[V2ShadowCandidate]:
    candidates_by_id: dict[str, V2ShadowCandidate] = {}

    for program in ctx.canonical_programs:
        program_name = program.program_name
        for paragraph in program.paragraphs:
            for statement in paragraph.statements:
                if statement.kind != StatementKind.SET:
                    continue
                if (
                    statement.condition_name_target is None
                    or statement.condition_set_value is not True
                ):
                    continue

                effects = ctx.effects_by_source_statement.get(statement.statement_id, [])
                set_effects = [
                    effect
                    for effect in effects
                    if effect.kind == SemanticEffectKind.SET_CONDITION_TRUE
                ]
                if not set_effects:
                    continue  # SET_CONDITION_TRUE no demostrado estructuralmente.
                effect = set_effects[0]

                parent = effect.parent_data_item
                if parent is None:
                    continue

                decision_statement = _nearest_enclosing_decision(ctx, statement)

                facts = ctx.facts_by_source_statement.get(statement.statement_id, [])
                condition_facts = [
                    fact
                    for fact in facts
                    if fact.fact_kind == PropagationFactKind.CONDITION_LITERAL
                    and (fact.target_qualified_name or fact.target_variable) == parent
                ]

                semantic_tag = _data_item_semantic_tag(
                    ctx, program=program_name, qualified_name=parent
                )

                if (
                    effect.literal is not None
                    and condition_facts
                    and decision_statement is not None
                    and semantic_tag == _RETURN_CODE_TAG
                ):
                    fact = condition_facts[0]
                    decision_node = _decision_graph_node_for(
                        ctx, program=program_name, paragraph=paragraph.name,
                        decision_statement=decision_statement,
                    )
                    decision_id = decision_node.id if decision_node else None
                    candidate_id = candidate_id_for(
                        detector_id=DETECTOR_ID_LEVEL_88_RETURN_CODE,
                        detector_version=DETECTOR_VERSION_LEVEL_88_RETURN_CODE,
                        program=program_name, paragraph=paragraph.name, decision_id=decision_id,
                        anchor_statement_id=statement.statement_id, target_key=parent,
                        resolved_literal=effect.literal,
                    )
                    candidate = V2ShadowCandidate(
                        candidate_id=candidate_id,
                        detector_id=DETECTOR_ID_LEVEL_88_RETURN_CODE,
                        detector_version=DETECTOR_VERSION_LEVEL_88_RETURN_CODE,
                        rule_type=V2RuleType.LEVEL_88_RETURN_CODE_RULE,
                        support=V2CandidateSupport.DETERMINISTIC,
                        detector_score=1.0,
                        program=program_name, paragraph=paragraph.name,
                        anchor_statement_id=statement.statement_id, decision_id=decision_id,
                        condition_name=effect.condition_name,
                        target_variable=parent, target_qualified_name=parent,
                        resolved_literal=effect.literal,
                        semantic_effect_ids=[effect.effect_id],
                        propagation_fact_ids=[fact.fact_id],
                        source_references=[
                            _source_reference_for_step_statement(
                                ctx, program=program_name, paragraph=paragraph.name,
                                statement_id=statement.statement_id,
                            )
                        ],
                        diagnostic_codes=[],
                        reason=(
                            f"SET {effect.condition_name} TO TRUE en {paragraph.name!r} "
                            f"resuelto contra un unico VALUE: padre {parent!r} (return_code) "
                            f"recibe {effect.literal!r}."
                        ),
                        comparable_v1_candidate_ids=_comparable_v1_ids_for_decision(
                            ctx, decision_id
                        ),
                    )
                elif semantic_tag == _RETURN_CODE_TAG and decision_statement is not None:
                    # Padre resuelto, es return_code, esta bajo una decision,
                    # pero el condition-name tiene multiples VALUE/THRU (o
                    # SemanticPropagation lo bloqueo por otra razon
                    # explicable) -- nunca se elige un valor arbitrario.
                    decision_node = _decision_graph_node_for(
                        ctx, program=program_name, paragraph=paragraph.name,
                        decision_statement=decision_statement,
                    )
                    decision_id = decision_node.id if decision_node else None
                    diagnostic_codes = sorted(
                        set(effect.diagnostic_codes) | {"V2_LEVEL_88_VALUE_NOT_UNIQUE"}
                    )
                    candidate_id = candidate_id_for(
                        detector_id=DETECTOR_ID_LEVEL_88_RETURN_CODE,
                        detector_version=DETECTOR_VERSION_LEVEL_88_RETURN_CODE,
                        program=program_name, paragraph=paragraph.name, decision_id=decision_id,
                        anchor_statement_id=statement.statement_id, target_key=parent,
                        resolved_literal=None,
                    )
                    candidate = V2ShadowCandidate(
                        candidate_id=candidate_id,
                        detector_id=DETECTOR_ID_LEVEL_88_RETURN_CODE,
                        detector_version=DETECTOR_VERSION_LEVEL_88_RETURN_CODE,
                        rule_type=V2RuleType.LEVEL_88_RETURN_CODE_RULE,
                        support=V2CandidateSupport.BLOCKED,
                        detector_score=0.0,
                        program=program_name, paragraph=paragraph.name,
                        anchor_statement_id=statement.statement_id, decision_id=decision_id,
                        condition_name=effect.condition_name,
                        target_variable=parent, target_qualified_name=parent,
                        resolved_literal=None,
                        semantic_effect_ids=[effect.effect_id],
                        propagation_fact_ids=[],
                        source_references=[
                            _source_reference_for_step_statement(
                                ctx, program=program_name, paragraph=paragraph.name,
                                statement_id=statement.statement_id,
                            )
                        ],
                        diagnostic_codes=diagnostic_codes,
                        reason=(
                            f"SET {effect.condition_name} TO TRUE en {paragraph.name!r}: "
                            f"padre {parent!r} es return_code y esta bajo una decision, pero "
                            "no se demuestra un unico VALUE (multiples VALUE, THRU, u otra "
                            "condicion bloqueada por SemanticPropagation)."
                        ),
                        comparable_v1_candidate_ids=_comparable_v1_ids_for_decision(
                            ctx, decision_id
                        ),
                    )
                else:
                    continue

                existing = candidates_by_id.get(candidate.candidate_id)
                if existing is None:
                    candidates_by_id[candidate.candidate_id] = candidate
                elif existing != candidate:
                    candidates_by_id[candidate.candidate_id] = _merge_candidate_evidence(
                        existing, candidate
                    )

    return sorted(candidates_by_id.values(), key=lambda candidate: candidate.candidate_id)


# ---------------------------------------------------------------------------
# V2_STATE_CHANGE (Fase 7)
# ---------------------------------------------------------------------------

DETECTOR_ID_STATE_CHANGE = "V2_STATE_CHANGE"
DETECTOR_VERSION_STATE_CHANGE = "1.0"


def detect_state_change(ctx: V2DetectorContext) -> list[V2ShadowCandidate]:
    candidates_by_id: dict[str, V2ShadowCandidate] = {}

    for program in ctx.canonical_programs:
        program_name = program.program_name
        for paragraph in program.paragraphs:
            paragraph_facts = [
                fact
                for facts in ctx.facts_by_target.values()
                for fact in facts
                if fact.program == program_name and fact.paragraph == paragraph.name
            ]
            for fact in paragraph_facts:
                if fact.fact_kind not in _LITERAL_FACT_KINDS or fact.literal is None:
                    continue
                semantic_tag = _data_item_semantic_tag(
                    ctx, program=program_name, qualified_name=fact.target_qualified_name
                )
                if semantic_tag == _RETURN_CODE_TAG:
                    continue  # excluido explicitamente: eso es RETURN_CODE_RULE.

                origin = _origin_statement(ctx, fact)
                if origin is None:
                    continue
                decision_statement = _nearest_enclosing_decision(ctx, origin)
                if decision_statement is None:
                    continue

                decision_node = _decision_graph_node_for(
                    ctx, program=program_name, paragraph=paragraph.name,
                    decision_statement=decision_statement,
                )
                decision_id = decision_node.id if decision_node else None
                target_key = fact.target_qualified_name or fact.target_variable
                candidate_id = candidate_id_for(
                    detector_id=DETECTOR_ID_STATE_CHANGE,
                    detector_version=DETECTOR_VERSION_STATE_CHANGE,
                    program=program_name, paragraph=paragraph.name, decision_id=decision_id,
                    anchor_statement_id=origin.statement_id, target_key=target_key,
                    resolved_literal=fact.literal,
                )
                effect_ids = sorted(
                    {
                        effect.effect_id
                        for step in fact.derivation_steps
                        for effect in ctx.effects_by_source_statement.get(step.statement_id, [])
                    }
                )
                candidate = V2ShadowCandidate(
                    candidate_id=candidate_id,
                    detector_id=DETECTOR_ID_STATE_CHANGE,
                    detector_version=DETECTOR_VERSION_STATE_CHANGE,
                    rule_type=V2RuleType.STATE_CHANGE_RULE,
                    support=V2CandidateSupport.PARTIAL,
                    detector_score=STATE_CHANGE_DETECTOR_SCORE,
                    program=program_name, paragraph=paragraph.name,
                    anchor_statement_id=origin.statement_id, decision_id=decision_id,
                    target_variable=fact.target_variable,
                    target_qualified_name=fact.target_qualified_name,
                    resolved_literal=fact.literal,
                    semantic_effect_ids=effect_ids,
                    propagation_fact_ids=[fact.fact_id],
                    source_references=_fact_source_references(ctx, fact),
                    diagnostic_codes=["V2_STATE_CHANGE_FUNCTIONAL_RELEVANCE_NOT_GUARANTEED"],
                    reason=(
                        f"Decision en {paragraph.name!r} cambia deterministicamente "
                        f"{target_key!r} (no return_code) a {fact.literal!r} via "
                        f"{fact.fact_kind.value}; relevancia funcional no garantizada."
                    ),
                    comparable_v1_candidate_ids=_comparable_v1_ids_for_decision(ctx, decision_id),
                )
                existing = candidates_by_id.get(candidate.candidate_id)
                if existing is None:
                    candidates_by_id[candidate.candidate_id] = candidate
                elif existing != candidate:
                    candidates_by_id[candidate.candidate_id] = _merge_candidate_evidence(
                        existing, candidate
                    )

    return sorted(candidates_by_id.values(), key=lambda candidate: candidate.candidate_id)
