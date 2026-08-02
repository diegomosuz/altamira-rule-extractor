"""Detectores individuales de reglas interprocedurales (Fase 8 de la
ampliacion semantica, `feat/interprocedural-rule-detectors-shadow`).

Cada detector es una funcion PURA `(InterproceduralRuleDetectorContext) ->
list[InterproceduralRuleCandidate]`: nunca accede a filesystem, nunca
accede a Neo4j, nunca muta el contexto ni ningun otro detector, nunca
reinterpreta `source_text`. Toda la evidencia proviene EXCLUSIVAMENTE de
`InterproceduralCallLinkageArtifact` (Fase 6) e
`InterproceduralPropagationArtifact` (Fase 7), ya demostrados -- este
modulo nunca vuelve a evaluar una asignacion COBOL desde cero ni cruza
un ciclo/SCC (Fase 7 ya bloqueo esos call sites, ver
`InterproceduralPropagationBarrier.CYCLE`/`RECURSION`)."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from ..contracts.canonical import CanonicalProgram
from ..contracts.enums import CallPassingMode, CallTargetKind
from ..contracts.interprocedural_call_linkage import (
    ArgumentBindingStatus,
    InterproceduralArgumentBinding,
    InterproceduralCallLinkageArtifact,
    InterproceduralCallSite,
    InterproceduralSourceReference,
    ProgramResolutionStatus,
)
from ..contracts.interprocedural_propagation import (
    InterproceduralFactKind,
    InterproceduralPropagationArtifact,
    InterproceduralPropagationBarrier,
    InterproceduralPropagationDirection,
    InterproceduralPropagationFact,
    InterproceduralPropagationStatus,
)
from ..contracts.interprocedural_rule_candidates import (
    InterproceduralCandidateSupport,
    InterproceduralRuleCandidate,
    InterproceduralRuleEvidence,
    InterproceduralRuleType,
)
from ..contracts.semantic_effects import SemanticEffectsArtifact
from ..contracts.semantic_enrichment import SemanticEnrichmentArtifact
from ..contracts.semantic_propagation import SemanticPropagationArtifact

DETECTOR_ID_RETURN_CODE = "interprocedural-return-code-rule"
DETECTOR_VERSION_RETURN_CODE = "1.0"
DETECTOR_ID_BY_REFERENCE = "interprocedural-by-reference-rule"
DETECTOR_VERSION_BY_REFERENCE = "1.0"
DETECTOR_ID_STATE_TRANSITION = "interprocedural-state-transition-rule"
DETECTOR_VERSION_STATE_TRANSITION = "1.0"

# Fase 8, regla 3 (STATE_TRANSITION_RULE): "la naturaleza de estado debe
# surgir de tags semanticos ya existentes... nunca por heuristica
# textual nueva". Estos dos son los unicos valores de
# `config/semantic-tags.yml.allowed_tags` cuyo nombre describe un estado
# generico (a diferencia de `return_code`/`sqlcode`, que son codigos de
# retorno, no estados de negocio) -- nunca se agrega un valor nuevo aqui,
# solo se reutilizan los ya declarados.
_STATE_SEMANTIC_TAGS = frozenset({"status", "status_flag"})


@dataclass(frozen=True)
class InterproceduralRuleDetectorContext:
    """Vista de solo lectura, construida una unica vez, sobre todos los
    artefactos que un detector Fase 8 puede consultar. Nunca se muta."""

    canonical_programs: tuple[CanonicalProgram, ...]
    semantic_effects: SemanticEffectsArtifact
    semantic_propagation: SemanticPropagationArtifact
    interprocedural_call_linkage: InterproceduralCallLinkageArtifact
    interprocedural_propagation: InterproceduralPropagationArtifact
    semantic_enrichment: SemanticEnrichmentArtifact | None

    program_by_name: dict[str, CanonicalProgram]
    call_site_by_id: dict[str, InterproceduralCallSite]
    binding_by_id: dict[str, InterproceduralArgumentBinding]
    entry_facts_by_call_site: dict[str, list[InterproceduralPropagationFact]]
    exit_facts_by_call_site: dict[str, list[InterproceduralPropagationFact]]
    semantic_tag_by_program_and_qualified_name: dict[tuple[str, str], str]


def build_detector_context(
    *,
    canonical_programs: Sequence[CanonicalProgram],
    semantic_effects: SemanticEffectsArtifact,
    semantic_propagation: SemanticPropagationArtifact,
    interprocedural_call_linkage: InterproceduralCallLinkageArtifact,
    interprocedural_propagation: InterproceduralPropagationArtifact,
    semantic_enrichment: SemanticEnrichmentArtifact | None,
) -> InterproceduralRuleDetectorContext:
    """Construye `InterproceduralRuleDetectorContext` una unica vez.
    Nunca lee filesystem, nunca accede a Neo4j, nunca modifica ninguno
    de los argumentos."""
    call_site_by_id: dict[str, InterproceduralCallSite] = {}
    binding_by_id: dict[str, InterproceduralArgumentBinding] = {}
    for call_site in interprocedural_call_linkage.call_sites:
        call_site_by_id[call_site.call_site_id] = call_site
        for binding in call_site.arguments:
            binding_by_id[binding.binding_id] = binding
        if call_site.returning_binding is not None:
            binding_by_id[call_site.returning_binding.binding_id] = call_site.returning_binding

    entry_facts_by_call_site: dict[str, list[InterproceduralPropagationFact]] = {}
    exit_facts_by_call_site: dict[str, list[InterproceduralPropagationFact]] = {}
    for fact in interprocedural_propagation.facts:
        if fact.direction == InterproceduralPropagationDirection.CALLER_TO_CALLEE:
            entry_facts_by_call_site.setdefault(fact.call_site_id, []).append(fact)
        else:
            exit_facts_by_call_site.setdefault(fact.call_site_id, []).append(fact)

    semantic_tag_by_program_and_qualified_name: dict[tuple[str, str], str] = {}
    if semantic_enrichment is not None:
        for tag in semantic_enrichment.data_item_tags:
            # program_id (Neo4j-shaped) es el prefijo hasta "::data::" --
            # nunca se recalcula: solo se usa como CLAVE de correlacion
            # exclusivamente por posicion textual, jamas re-derivado.
            program_id_prefix = tag.program_id
            semantic_tag_by_program_and_qualified_name[(program_id_prefix, tag.qualified_name)] = (
                tag.semantic_tag
            )

    return InterproceduralRuleDetectorContext(
        canonical_programs=tuple(canonical_programs),
        semantic_effects=semantic_effects,
        semantic_propagation=semantic_propagation,
        interprocedural_call_linkage=interprocedural_call_linkage,
        interprocedural_propagation=interprocedural_propagation,
        semantic_enrichment=semantic_enrichment,
        program_by_name={p.program_name: p for p in canonical_programs},
        call_site_by_id=call_site_by_id,
        binding_by_id=binding_by_id,
        entry_facts_by_call_site=entry_facts_by_call_site,
        exit_facts_by_call_site=exit_facts_by_call_site,
        semantic_tag_by_program_and_qualified_name=semantic_tag_by_program_and_qualified_name,
    )


def _digest(*parts: str) -> str:
    canonical = "\x1f".join(parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _candidate_id(*, detector_id: str, detector_version: str, parts: Sequence[str]) -> str:
    return f"ipr::{detector_id}::{_digest(detector_version, *parts)}"


def _evidence_id(*, candidate_seed: str, label: str) -> str:
    return f"evidence::{_digest(candidate_seed, label)}"


def _sorted_unique(values: Sequence[str]) -> list[str]:
    return sorted(set(values))


def _entry_fact_for_binding(
    ctx: InterproceduralRuleDetectorContext, *, call_site_id: str, binding_id: str
) -> InterproceduralPropagationFact | None:
    for fact in ctx.entry_facts_by_call_site.get(call_site_id, []):
        if fact.binding_id == binding_id:
            return fact
    return None


def _exit_fact_for_binding(
    ctx: InterproceduralRuleDetectorContext,
    *,
    call_site_id: str,
    binding_id: str,
    kind: InterproceduralFactKind,
) -> InterproceduralPropagationFact | None:
    for fact in ctx.exit_facts_by_call_site.get(call_site_id, []):
        if fact.binding_id == binding_id and fact.kind == kind:
            return fact
    return None


def _underlying_semantic_effect_ids(
    ctx: InterproceduralRuleDetectorContext, fact_ids: Sequence[str]
) -> list[str]:
    """Fase 7 nunca conserva `semantic_effect_ids` directamente en sus
    hechos -- solo `source_fact_ids` (Fase 4). Este helper cruza esos
    `PropagatedValueFact.fact_id` contra `SemanticPropagationArtifact`
    para recuperar `derivation_steps[].effect_id`, cuando existe, nunca
    inventando una relacion no demostrada."""
    target_ids = set(fact_ids)
    if not target_ids:
        return []
    effect_ids: set[str] = set()
    for program in ctx.semantic_propagation.programs:
        for fact in program.facts:
            if fact.fact_id not in target_ids:
                continue
            for step in fact.derivation_steps:
                effect_ids.add(step.effect_id)
    return sorted(effect_ids)


def _blocked_evidence(
    ctx: InterproceduralRuleDetectorContext,
    *,
    candidate_seed: str,
    call_site: InterproceduralCallSite,
    binding: InterproceduralArgumentBinding | None,
    fact: InterproceduralPropagationFact,
) -> InterproceduralRuleEvidence:
    return InterproceduralRuleEvidence(
        evidence_id=_evidence_id(candidate_seed=candidate_seed, label="blocked"),
        caller_program=call_site.caller_program,
        callee_program=call_site.resolved_callee_program,
        call_site_id=call_site.call_site_id,
        statement_id=call_site.statement_id,
        binding_id=binding.binding_id if binding is not None else None,
        propagation_fact_ids=[fact.fact_id],
        diagnostics=_sorted_unique(fact.diagnostics),
    )


# ---------------------------------------------------------------------------
# 1. INTERPROCEDURAL_RETURN_CODE_RULE
# ---------------------------------------------------------------------------


def detect_return_code_rule(
    ctx: InterproceduralRuleDetectorContext,
) -> list[InterproceduralRuleCandidate]:
    candidates: list[InterproceduralRuleCandidate] = []
    for call_site in ctx.interprocedural_call_linkage.call_sites:
        if call_site.returning_binding is None:
            continue
        binding = call_site.returning_binding
        if binding.status != ArgumentBindingStatus.RESOLVED_POSITIONAL:
            continue
        exit_fact = _exit_fact_for_binding(
            ctx,
            call_site_id=call_site.call_site_id,
            binding_id=binding.binding_id,
            kind=InterproceduralFactKind.RETURNING_FACT,
        )
        if exit_fact is None:
            continue

        target = binding.actual_qualified_name or binding.actual_name
        if target is None:
            continue

        seed_parts = [call_site.call_site_id, binding.binding_id]

        if exit_fact.status == InterproceduralPropagationStatus.PROPAGATED:
            assert exit_fact.literal is not None
            candidate_id = _candidate_id(
                detector_id=DETECTOR_ID_RETURN_CODE,
                detector_version=DETECTOR_VERSION_RETURN_CODE,
                parts=[*seed_parts, exit_fact.literal],
            )
            entry_evidence: list[InterproceduralRuleEvidence] = []
            # Evidencia de entrada relevante, si existe: los ENTRY_FACT
            # PROPAGATED del mismo call site (nunca se infiere cual
            # argumento "causo" el literal de retorno -- se listan todos
            # los que si se demostraron, como contexto, nunca como
            # afirmacion causal).
            for entry_fact in sorted(
                ctx.entry_facts_by_call_site.get(call_site.call_site_id, []),
                key=lambda f: f.fact_id,
            ):
                if entry_fact.status != InterproceduralPropagationStatus.PROPAGATED:
                    continue
                entry_evidence.append(
                    InterproceduralRuleEvidence(
                        evidence_id=_evidence_id(
                            candidate_seed=candidate_id, label=f"entry::{entry_fact.fact_id}"
                        ),
                        caller_program=call_site.caller_program,
                        callee_program=call_site.resolved_callee_program,
                        call_site_id=call_site.call_site_id,
                        statement_id=call_site.statement_id,
                        binding_id=entry_fact.binding_id,
                        propagation_fact_ids=_sorted_unique(
                            [entry_fact.fact_id, *entry_fact.source_fact_ids]
                        ),
                        semantic_effect_ids=_underlying_semantic_effect_ids(
                            ctx, [entry_fact.fact_id, *entry_fact.source_fact_ids]
                        ),
                        actual_name=entry_fact.actual_name,
                        formal_name=entry_fact.formal_name,
                        input_literal=entry_fact.literal,
                        source_references=list(entry_fact.source_references),
                    )
                )

            output_evidence = InterproceduralRuleEvidence(
                evidence_id=_evidence_id(candidate_seed=candidate_id, label="output"),
                caller_program=call_site.caller_program,
                callee_program=call_site.resolved_callee_program,
                call_site_id=call_site.call_site_id,
                statement_id=call_site.statement_id,
                binding_id=binding.binding_id,
                propagation_fact_ids=_sorted_unique(
                    [exit_fact.fact_id, *exit_fact.source_fact_ids]
                ),
                semantic_effect_ids=_underlying_semantic_effect_ids(
                    ctx, [exit_fact.fact_id, *exit_fact.source_fact_ids]
                ),
                actual_name=exit_fact.actual_name,
                formal_name=exit_fact.formal_name,
                output_literal=exit_fact.literal,
                source_references=list(exit_fact.source_references),
            )

            candidates.append(
                InterproceduralRuleCandidate(
                    candidate_id=candidate_id,
                    detector=DETECTOR_ID_RETURN_CODE,
                    rule_type=InterproceduralRuleType.RETURN_CODE_RULE,
                    support=InterproceduralCandidateSupport.DETERMINISTIC,
                    caller_program=call_site.caller_program,
                    callee_program=call_site.resolved_callee_program,
                    caller_paragraph=call_site.caller_paragraph,
                    call_site_id=call_site.call_site_id,
                    target=target,
                    output_literal=exit_fact.literal,
                    evidence=[*entry_evidence, output_evidence],
                )
            )
        elif exit_fact.status == InterproceduralPropagationStatus.BLOCKED:
            candidate_id = _candidate_id(
                detector_id=DETECTOR_ID_RETURN_CODE,
                detector_version=DETECTOR_VERSION_RETURN_CODE,
                parts=[*seed_parts, "BLOCKED", *[b.value for b in exit_fact.barriers]],
            )
            candidates.append(
                InterproceduralRuleCandidate(
                    candidate_id=candidate_id,
                    detector=DETECTOR_ID_RETURN_CODE,
                    rule_type=InterproceduralRuleType.RETURN_CODE_RULE,
                    support=InterproceduralCandidateSupport.BLOCKED,
                    caller_program=call_site.caller_program,
                    callee_program=call_site.resolved_callee_program,
                    caller_paragraph=call_site.caller_paragraph,
                    call_site_id=call_site.call_site_id,
                    target=target,
                    evidence=[
                        _blocked_evidence(
                            ctx,
                            candidate_seed=candidate_id,
                            call_site=call_site,
                            binding=binding,
                            fact=exit_fact,
                        )
                    ],
                    barriers=list(exit_fact.barriers),
                    diagnostics=_sorted_unique(exit_fact.diagnostics),
                )
            )
        # INVALIDATED/UNRESOLVED: sin certeza estructural de bloqueo NI de
        # determinismo -- no se genera ningun candidato (ver docs).
    return candidates


# ---------------------------------------------------------------------------
# 2. INTERPROCEDURAL_BY_REFERENCE_RULE
# ---------------------------------------------------------------------------


def _by_reference_bindings(
    ctx: InterproceduralRuleDetectorContext,
) -> list[tuple[InterproceduralCallSite, InterproceduralArgumentBinding]]:
    pairs: list[tuple[InterproceduralCallSite, InterproceduralArgumentBinding]] = []
    for call_site in ctx.interprocedural_call_linkage.call_sites:
        for binding in call_site.arguments:
            if binding.passing_mode != CallPassingMode.REFERENCE:
                continue
            if binding.status != ArgumentBindingStatus.RESOLVED_POSITIONAL:
                continue
            pairs.append((call_site, binding))
    return pairs


def detect_by_reference_rule(
    ctx: InterproceduralRuleDetectorContext,
) -> list[InterproceduralRuleCandidate]:
    candidates: list[InterproceduralRuleCandidate] = []
    for call_site, binding in _by_reference_bindings(ctx):
        exit_fact = _exit_fact_for_binding(
            ctx,
            call_site_id=call_site.call_site_id,
            binding_id=binding.binding_id,
            kind=InterproceduralFactKind.BY_REFERENCE_OUTPUT,
        )
        if exit_fact is None:
            continue

        target = binding.actual_qualified_name or binding.actual_name
        if target is None:
            continue

        entry_fact = _entry_fact_for_binding(
            ctx, call_site_id=call_site.call_site_id, binding_id=binding.binding_id
        )
        input_literal = (
            entry_fact.literal
            if entry_fact is not None
            and entry_fact.status == InterproceduralPropagationStatus.PROPAGATED
            else None
        )

        seed_parts = [call_site.call_site_id, binding.binding_id]

        if exit_fact.status == InterproceduralPropagationStatus.PROPAGATED:
            assert exit_fact.literal is not None
            candidate_id = _candidate_id(
                detector_id=DETECTOR_ID_BY_REFERENCE,
                detector_version=DETECTOR_VERSION_BY_REFERENCE,
                parts=[*seed_parts, exit_fact.literal],
            )
            fact_ids = [exit_fact.fact_id, *exit_fact.source_fact_ids]
            if entry_fact is not None:
                fact_ids = [entry_fact.fact_id, *entry_fact.source_fact_ids, *fact_ids]
            evidence = InterproceduralRuleEvidence(
                evidence_id=_evidence_id(candidate_seed=candidate_id, label="by_reference"),
                caller_program=call_site.caller_program,
                callee_program=call_site.resolved_callee_program,
                call_site_id=call_site.call_site_id,
                statement_id=call_site.statement_id,
                binding_id=binding.binding_id,
                propagation_fact_ids=_sorted_unique(fact_ids),
                semantic_effect_ids=_underlying_semantic_effect_ids(ctx, fact_ids),
                actual_name=binding.actual_name,
                formal_name=binding.formal_name,
                input_literal=input_literal,
                output_literal=exit_fact.literal,
                source_references=_sorted_source_references(
                    [
                        *(entry_fact.source_references if entry_fact else []),
                        *exit_fact.source_references,
                    ]
                ),
            )
            candidates.append(
                InterproceduralRuleCandidate(
                    candidate_id=candidate_id,
                    detector=DETECTOR_ID_BY_REFERENCE,
                    rule_type=InterproceduralRuleType.BY_REFERENCE_RULE,
                    support=InterproceduralCandidateSupport.DETERMINISTIC,
                    caller_program=call_site.caller_program,
                    callee_program=call_site.resolved_callee_program,
                    caller_paragraph=call_site.caller_paragraph,
                    call_site_id=call_site.call_site_id,
                    target=target,
                    input_literal=input_literal,
                    output_literal=exit_fact.literal,
                    evidence=[evidence],
                )
            )
        elif exit_fact.status == InterproceduralPropagationStatus.BLOCKED:
            candidate_id = _candidate_id(
                detector_id=DETECTOR_ID_BY_REFERENCE,
                detector_version=DETECTOR_VERSION_BY_REFERENCE,
                parts=[*seed_parts, "BLOCKED", *[b.value for b in exit_fact.barriers]],
            )
            candidates.append(
                InterproceduralRuleCandidate(
                    candidate_id=candidate_id,
                    detector=DETECTOR_ID_BY_REFERENCE,
                    rule_type=InterproceduralRuleType.BY_REFERENCE_RULE,
                    support=InterproceduralCandidateSupport.BLOCKED,
                    caller_program=call_site.caller_program,
                    callee_program=call_site.resolved_callee_program,
                    caller_paragraph=call_site.caller_paragraph,
                    call_site_id=call_site.call_site_id,
                    target=target,
                    evidence=[
                        _blocked_evidence(
                            ctx,
                            candidate_seed=candidate_id,
                            call_site=call_site,
                            binding=binding,
                            fact=exit_fact,
                        )
                    ],
                    barriers=list(exit_fact.barriers),
                    diagnostics=_sorted_unique(exit_fact.diagnostics),
                )
            )
        # INVALIDATED/UNRESOLVED ("sin cambio demostrable"): ningun
        # candidato -- nunca se afirma una actualizacion no probada.
    return candidates


def _source_reference_dedupe_key(
    reference: InterproceduralSourceReference,
) -> tuple[str, str, str, int]:
    return (
        reference.program,
        reference.paragraph or "",
        reference.statement_id or "",
        reference.line or 0,
    )


def _sorted_source_references(
    references: Sequence[InterproceduralSourceReference],
) -> list[InterproceduralSourceReference]:
    by_key = {_source_reference_dedupe_key(reference): reference for reference in references}
    return sorted(by_key.values(), key=_source_reference_dedupe_key)


# ---------------------------------------------------------------------------
# 3. INTERPROCEDURAL_STATE_TRANSITION_RULE
# ---------------------------------------------------------------------------


def _semantic_tag_for_actual(
    ctx: InterproceduralRuleDetectorContext, *, caller_program: str, qualified_name: str | None
) -> str | None:
    if qualified_name is None or ctx.semantic_enrichment is None:
        return None
    program = ctx.program_by_name.get(caller_program)
    if program is None:
        return None
    # program_id (Neo4j-shaped) nunca se recalcula aqui: se busca por
    # CUALQUIER program_id ya indexado cuyo sufijo (tras el ultimo
    # "::") coincida con el program_name real -- unica correlacion
    # disponible sin SemanticGraph/Manifest (ver docstring del modulo).
    for (program_id, q_name), tag in ctx.semantic_tag_by_program_and_qualified_name.items():
        if q_name != qualified_name:
            continue
        if program_id.split("::")[3:4] == [program.program_name]:
            return tag
    return None


def detect_state_transition_rule(
    ctx: InterproceduralRuleDetectorContext,
) -> list[InterproceduralRuleCandidate]:
    if ctx.semantic_enrichment is None:
        return []
    candidates: list[InterproceduralRuleCandidate] = []
    for call_site, binding in _by_reference_bindings(ctx):
        exit_fact = _exit_fact_for_binding(
            ctx,
            call_site_id=call_site.call_site_id,
            binding_id=binding.binding_id,
            kind=InterproceduralFactKind.BY_REFERENCE_OUTPUT,
        )
        if exit_fact is None or exit_fact.status != InterproceduralPropagationStatus.PROPAGATED:
            continue
        entry_fact = _entry_fact_for_binding(
            ctx, call_site_id=call_site.call_site_id, binding_id=binding.binding_id
        )
        if entry_fact is None or entry_fact.status != InterproceduralPropagationStatus.PROPAGATED:
            continue
        assert entry_fact.literal is not None
        assert exit_fact.literal is not None
        if entry_fact.literal == exit_fact.literal:
            continue  # sin transicion observable: mismo valor antes y despues.

        target = binding.actual_qualified_name or binding.actual_name
        if target is None:
            continue
        tag = _semantic_tag_for_actual(
            ctx,
            caller_program=call_site.caller_program,
            # Mismo fallback que `target` (arriba): `qualified_data_item_name`
            # del parser real es frecuentemente `None` para variables planas
            # (no calificadas) -- nunca se descarta la correlacion solo por
            # eso, se reutiliza `actual_name` igual que en todo el resto del
            # detector.
            qualified_name=target,
        )
        if tag not in _STATE_SEMANTIC_TAGS:
            continue

        candidate_id = _candidate_id(
            detector_id=DETECTOR_ID_STATE_TRANSITION,
            detector_version=DETECTOR_VERSION_STATE_TRANSITION,
            parts=[
                call_site.call_site_id,
                binding.binding_id,
                entry_fact.literal,
                exit_fact.literal,
            ],
        )
        fact_ids = _sorted_unique(
            [
                entry_fact.fact_id,
                *entry_fact.source_fact_ids,
                exit_fact.fact_id,
                *exit_fact.source_fact_ids,
            ]
        )
        evidence = InterproceduralRuleEvidence(
            evidence_id=_evidence_id(candidate_seed=candidate_id, label="state_transition"),
            caller_program=call_site.caller_program,
            callee_program=call_site.resolved_callee_program,
            call_site_id=call_site.call_site_id,
            statement_id=call_site.statement_id,
            binding_id=binding.binding_id,
            propagation_fact_ids=fact_ids,
            semantic_effect_ids=_underlying_semantic_effect_ids(ctx, fact_ids),
            actual_name=binding.actual_name,
            formal_name=binding.formal_name,
            input_literal=entry_fact.literal,
            output_literal=exit_fact.literal,
            source_references=_sorted_source_references(
                [*entry_fact.source_references, *exit_fact.source_references]
            ),
            diagnostics=[f"STATE_SEMANTIC_TAG_{tag.upper()}"],
        )
        candidates.append(
            InterproceduralRuleCandidate(
                candidate_id=candidate_id,
                detector=DETECTOR_ID_STATE_TRANSITION,
                rule_type=InterproceduralRuleType.STATE_TRANSITION_RULE,
                support=InterproceduralCandidateSupport.DETERMINISTIC,
                caller_program=call_site.caller_program,
                callee_program=call_site.resolved_callee_program,
                caller_paragraph=call_site.caller_paragraph,
                call_site_id=call_site.call_site_id,
                target=target,
                input_literal=entry_fact.literal,
                output_literal=exit_fact.literal,
                evidence=[evidence],
                diagnostics=[f"STATE_SEMANTIC_TAG_{tag.upper()}"],
            )
        )
    return candidates


# ---------------------------------------------------------------------------
# Trazabilidad de call sites bloqueados que nunca llegan a candidato
# (auditoria de cierre, Fase 8): distingue explicitamente "no existe patron
# de regla" (silencio correcto, nunca trazado) de "existe patron potencial
# pero bloqueado" / "existe patron pero no pudo evaluarse" (ambos deben
# quedar trazables en diagnostics, nunca desaparecer sin dejar rastro).
# ---------------------------------------------------------------------------


def _call_site_has_pattern(call_site: InterproceduralCallSite) -> bool:
    """`True` cuando el call site declara sintacticamente un patron de
    regla potencial (RETURNING o al menos un argumento BY REFERENCE) --
    independientemente de si Fase 6/7 pudo resolverlo o no."""
    if call_site.returning_binding is not None:
        return True
    return any(binding.passing_mode == CallPassingMode.REFERENCE for binding in call_site.arguments)


def _blocked_call_site_barrier(
    call_site: InterproceduralCallSite,
) -> InterproceduralPropagationBarrier | None:
    """Mismo orden de prioridad publico que
    `interprocedural_propagation_analyzer.py::_call_site_barrier` (no se
    importa esa funcion privada: se deriva de los mismos campos
    publicos de `InterproceduralCallSite`, con el mismo resultado) --
    `None` cuando el call site esta resuelto y elegible a nivel de Fase
    6/7 (el bloqueo, si existe, ocurre mas abajo, a nivel de hecho
    individual de Fase 7, ya cubierto por un candidato `BLOCKED` o por
    ausencia de candidato con `INVALIDATED`/`UNRESOLVED`)."""
    if call_site.target_kind != CallTargetKind.LITERAL:
        return InterproceduralPropagationBarrier.DYNAMIC_CALL
    if call_site.recursive:
        return InterproceduralPropagationBarrier.RECURSION
    if call_site.part_of_cycle:
        return InterproceduralPropagationBarrier.CYCLE
    if call_site.resolution_status == ProgramResolutionStatus.UNRESOLVED_MISSING_PROGRAM:
        return InterproceduralPropagationBarrier.MISSING_PROGRAM
    if call_site.resolution_status == ProgramResolutionStatus.AMBIGUOUS_PROGRAM:
        return InterproceduralPropagationBarrier.AMBIGUOUS_PROGRAM
    if call_site.resolution_status != ProgramResolutionStatus.RESOLVED_INTERNAL:
        return InterproceduralPropagationBarrier.DYNAMIC_CALL
    return None


def blocked_call_site_diagnostics(
    ctx: InterproceduralRuleDetectorContext,
    candidates: Sequence[InterproceduralRuleCandidate],
) -> list[str]:
    """Para cada call site con un patron de regla potencial (RETURNING/BY
    REFERENCE) que NINGUN detector convirtio en candidato (ni
    DETERMINISTIC ni BLOCKED), agrega un diagnostico trazable:

    - `BLOCKED_CALL_SITE_NO_CANDIDATE::<call_site_id>::<barrier>` cuando
      el propio call site esta bloqueado a nivel de Fase 6/7 (CALL
      dinamico, programa ausente/ambiguo, self-call, SCC) -- "existe
      patron potencial, pero esta bloqueado estructuralmente antes de
      que Fase 7 pueda siquiera intentar demostrar un valor".
    - `NO_CANDIDATE_UNRESOLVED_VALUE::<call_site_id>` cuando el call
      site SI esta resuelto/elegible pero ningun binding produjo un
      hecho `PROPAGATED`/`BLOCKED` (Fase 7 lo dejo `INVALIDATED`/
      `UNRESOLVED`) -- "existe patron, pero no pudo evaluarse a un
      valor unico ni a un bloqueo con certeza estructural".

    Un call site SIN ningun patron de regla (ni RETURNING ni BY
    REFERENCE) nunca se traza -- "no existe patron de regla" es
    silencio correcto, no una omision."""
    covered_call_sites = {candidate.call_site_id for candidate in candidates}
    diagnostics: list[str] = []
    for call_site in ctx.interprocedural_call_linkage.call_sites:
        if call_site.call_site_id in covered_call_sites:
            continue
        if not _call_site_has_pattern(call_site):
            continue
        barrier = _blocked_call_site_barrier(call_site)
        if barrier is not None:
            diagnostics.append(
                f"BLOCKED_CALL_SITE_NO_CANDIDATE::{call_site.call_site_id}::{barrier.value}"
            )
        else:
            diagnostics.append(f"NO_CANDIDATE_UNRESOLVED_VALUE::{call_site.call_site_id}")
    return sorted(set(diagnostics))
