"""Analizador PURO de propagacion limitada de constantes y copias (Fase 4
de la ampliacion semantica, `feat/constant-copy-propagation`).

Recibe `CanonicalProgram[]` y un `SemanticEffectsArtifact` ya calculados
en memoria (nunca los vuelve a derivar ni los reinterpreta) y devuelve un
`SemanticPropagationArtifact`. Nunca:

- lee filesystem (responsabilidad de `semantic_propagation_service.py`);
- accede a Neo4j ni consulta `SemanticGraph`;
- lee variables de entorno;
- escribe archivos;
- modifica los objetos de entrada (`CanonicalProgram`/`SemanticEffectsArtifact`
  nunca se mutan);
- ejecuta un LLM;
- crea candidatos ni relaciones de grafo.

Alcance (docs/SEMANTIC_PROPAGATION.md): intraprograma, intraparrafo,
sensible al orden real de `CanonicalParagraph.statements[]` (nunca al
orden de `SemanticEffectsArtifact.programs[].effects[]`, que esta
reordenado por `effect_id` para estabilidad de serializacion -- ver
auditoria de Fase 1) y sensible a ramas. Sin cruce entre paragraphs, sin
analisis interprocedural, sin evaluacion aritmetica, sin alias por
REDEFINES/OCCURS, sin propagacion via SQL/archivos/CALL/CICS. La ausencia
de prueba suficiente siempre produce UNRESOLVED_COPY/INVALIDATED_VALUE/
BLOCKED_PROPAGATION, nunca una inferencia optimista."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..contracts.canonical import (
    CanonicalConditionName,
    CanonicalDataItem,
    CanonicalParagraph,
    CanonicalProgram,
    CanonicalStatement,
)
from ..contracts.enums import BranchKind, StatementKind
from ..contracts.semantic_coverage import SemanticSupportStatus
from ..contracts.semantic_effects import (
    ProgramSemanticEffects,
    SemanticEffect,
    SemanticEffectKind,
    SemanticEffectsArtifact,
)
from ..contracts.semantic_propagation import (
    ProgramSemanticPropagation,
    PropagatedValueFact,
    PropagationBarrier,
    PropagationBarrierReason,
    PropagationFactKind,
    PropagationSourceReference,
    PropagationStep,
    SemanticPropagationArtifact,
    SemanticPropagationSummary,
)
from .errors import SemanticPropagationError

# ---------------------------------------------------------------------------
# Resolucion de simbolos (Fase 5): exacto por qualified_name, o por name
# simple UNICAMENTE cuando es unico en todo el programa. Sin normalizacion
# de mayusculas/minusculas (ningun punto ya auditado del pipeline Java o
# Python normaliza casing de identificadores COBOL: se preserva el nombre
# exacto que entrega ProLeap -- ver auditoria de Fase 1).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ResolvedSymbol:
    identity_key: str
    qualified_name: str | None
    ambiguous: bool


@dataclass(frozen=True)
class _SymbolTable:
    by_qualified_name: dict[str, CanonicalDataItem]
    by_simple_name: dict[str, list[CanonicalDataItem]]

    def resolve(self, raw_name: str) -> _ResolvedSymbol:
        if raw_name in self.by_qualified_name:
            return _ResolvedSymbol(identity_key=raw_name, qualified_name=raw_name, ambiguous=False)
        candidates = self.by_simple_name.get(raw_name, [])
        if len(candidates) == 1:
            qualified_name = candidates[0].qualified_name
            return _ResolvedSymbol(
                identity_key=qualified_name, qualified_name=qualified_name, ambiguous=False
            )
        if len(candidates) > 1:
            return _ResolvedSymbol(identity_key=raw_name, qualified_name=None, ambiguous=True)
        # Cero candidatos: no es un data item declarado que este parser
        # conserve (p. ej. un registro especial, o un nombre que el
        # extractor no pudo resolver). Nunca se inventa un qualified_name;
        # se usa el nombre crudo como identidad -- sigue siendo seguro
        # porque no colisiona con ningun otro simbolo real.
        return _ResolvedSymbol(identity_key=raw_name, qualified_name=None, ambiguous=False)


def _build_symbol_table(program: CanonicalProgram) -> _SymbolTable:
    by_qualified: dict[str, CanonicalDataItem] = {}
    by_simple: dict[str, list[CanonicalDataItem]] = defaultdict(list)
    for item in program.data_items:
        by_qualified[item.qualified_name] = item
        by_simple[item.name].append(item)
    return _SymbolTable(by_qualified_name=by_qualified, by_simple_name=dict(by_simple))


# ---------------------------------------------------------------------------
# Entorno de valores conocidos (Fase 6/8): inmutable por valor, clonado por
# referencia superficial (dict) en cada rama -- nunca compartido/mutado
# entre ramas hermanas.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _KnownValue:
    literal: str
    steps: tuple[PropagationStep, ...]
    fact_kind: PropagationFactKind
    qualified_name: str | None


_Environment = dict[str, _KnownValue]


# ---------------------------------------------------------------------------
# IDs deterministicos (Fase 3): ordinal estable por combinacion de claves,
# nunca UUID/timestamp/hash() de Python/orden accidental de dict.
# ---------------------------------------------------------------------------


class _Collector:
    """Acumula facts/barriers de UN programa y genera sus IDs
    deterministicos. `program`/`run_id` nunca cambian durante la vida del
    collector (una instancia por programa)."""

    def __init__(self, program: str) -> None:
        self.program = program
        self.facts: list[PropagatedValueFact] = []
        self.barriers: list[PropagationBarrier] = []
        self._fact_ordinals: dict[tuple[str, str, str, str, str], int] = {}
        self._barrier_ordinals: dict[tuple[str, str, str, str], int] = {}

    def _next_fact_ordinal(
        self, *, paragraph: str, region_id: str, target_variable: str, source_statement_id: str,
        fact_kind: PropagationFactKind,
    ) -> int:
        key = (paragraph, region_id, target_variable, source_statement_id, fact_kind.value)
        ordinal = self._fact_ordinals.get(key, 0)
        self._fact_ordinals[key] = ordinal + 1
        return ordinal

    def _next_barrier_ordinal(
        self, *, paragraph: str, region_id: str, source_statement_id: str,
        reason: PropagationBarrierReason,
    ) -> int:
        key = (paragraph, region_id, source_statement_id, reason.value)
        ordinal = self._barrier_ordinals.get(key, 0)
        self._barrier_ordinals[key] = ordinal + 1
        return ordinal

    def add_fact(
        self,
        *,
        fact_kind: PropagationFactKind,
        paragraph: str,
        region_id: str,
        target_variable: str,
        target_qualified_name: str | None,
        literal: str | None,
        source_variable: str | None,
        source_qualified_name: str | None,
        source_reference: PropagationSourceReference,
        derivation_steps: tuple[PropagationStep, ...],
        support_status: SemanticSupportStatus,
        diagnostic_codes: list[str],
        explanation: str,
    ) -> None:
        ordinal = self._next_fact_ordinal(
            paragraph=paragraph,
            region_id=region_id,
            target_variable=target_variable,
            source_statement_id=source_reference.statement_id,
            fact_kind=fact_kind,
        )
        fact_id = (
            f"fact::{self.program}::{paragraph}::{region_id}::{target_variable}::"
            f"{source_reference.statement_id}::{fact_kind.value}::{ordinal}"
        )
        self.facts.append(
            PropagatedValueFact(
                fact_id=fact_id,
                fact_kind=fact_kind,
                program=self.program,
                paragraph=paragraph,
                region_id=region_id,
                target_variable=target_variable,
                target_qualified_name=target_qualified_name,
                literal=literal,
                source_variable=source_variable,
                source_qualified_name=source_qualified_name,
                source_reference=source_reference,
                derivation_steps=list(derivation_steps),
                derivation_depth=len(derivation_steps),
                support_status=support_status,
                diagnostic_codes=sorted(set(diagnostic_codes)),
                explanation=explanation,
            )
        )

    def add_barrier(
        self,
        *,
        paragraph: str,
        region_id: str,
        source_reference: PropagationSourceReference,
        reason: PropagationBarrierReason,
        affected_variables: list[str],
        clears_entire_environment: bool,
        diagnostic_code: str,
        explanation: str,
    ) -> None:
        ordinal = self._next_barrier_ordinal(
            paragraph=paragraph,
            region_id=region_id,
            source_statement_id=source_reference.statement_id,
            reason=reason,
        )
        barrier_id = (
            f"barrier::{self.program}::{paragraph}::{region_id}::"
            f"{source_reference.statement_id}::{reason.value}::{ordinal}"
        )
        self.barriers.append(
            PropagationBarrier(
                barrier_id=barrier_id,
                program=self.program,
                paragraph=paragraph,
                region_id=region_id,
                source_reference=source_reference,
                reason=reason,
                affected_variables=sorted(set(affected_variables)),
                clears_entire_environment=clears_entire_environment,
                diagnostic_code=diagnostic_code,
                explanation=explanation,
            )
        )


# ---------------------------------------------------------------------------
# Regiones y flujo de control (Fase 6)
# ---------------------------------------------------------------------------


def _paragraph_root_region_id(program: str, paragraph: str) -> str:
    return f"{program}::{paragraph}::root"


def _branch_region_id(
    parent_region_id: str, decision_statement_id: str, branch_kind: BranchKind, run_index: int
) -> str:
    return f"{parent_region_id}::{decision_statement_id}::{branch_kind.value}::{run_index}"


def _split_into_branch_runs(
    children: Sequence[CanonicalStatement],
) -> list[tuple[BranchKind, list[CanonicalStatement]]]:
    """Agrupa los hijos directos de una decision en 'runs' contiguos por
    rama real -- nunca por `branch_kind` como clave de diccionario (dos
    WHEN distintos de un mismo EVALUATE comparten `branch_kind=WHEN` y se
    fusionarian incorrectamente). `branch_condition` se declara solo en el
    primer statement de cada rama WHEN (ver `StatementExtractor.
    walkBranchWithCondition`): junto con un cambio de `branch_kind`, es la
    unica senal fiable de un nuevo comienzo de rama (auditoria de Fase 1)."""
    runs: list[tuple[BranchKind, list[CanonicalStatement]]] = []
    current_kind: BranchKind | None = None
    current_run: list[CanonicalStatement] = []
    for child in children:
        starts_new_run = (
            not current_run
            or child.branch_kind != current_kind
            or child.branch_condition is not None
        )
        if starts_new_run and current_run:
            assert current_kind is not None
            runs.append((current_kind, current_run))
            current_run = []
        current_run.append(child)
        current_kind = child.branch_kind
    if current_run:
        assert current_kind is not None
        runs.append((current_kind, current_run))
    return runs


@dataclass
class _ParagraphContext:
    program_name: str
    paragraph: CanonicalParagraph
    children_by_parent: dict[str, list[CanonicalStatement]]
    effects_by_statement_id: dict[str, list[SemanticEffect]]
    symbols: _SymbolTable
    conditions_by_qualified_name: dict[str, CanonicalConditionName]
    collector: _Collector
    max_derivation_depth: int


def _source_reference(
    ctx: _ParagraphContext, stmt: CanonicalStatement
) -> PropagationSourceReference:
    return PropagationSourceReference(
        program=ctx.program_name,
        paragraph=ctx.paragraph.name,
        statement_id=stmt.statement_id,
        statement_kind=stmt.kind,
        source_file=stmt.source_file,
        line_start=stmt.line_start,
        line_end=stmt.line_end,
        parent_statement_id=stmt.parent_statement_id,
        branch_kind=stmt.branch_kind,
    )


def _clear_environment_barrier(
    ctx: _ParagraphContext,
    stmt: CanonicalStatement,
    env: _Environment,
    region_id: str,
    *,
    reason: PropagationBarrierReason,
    diagnostic_code: str,
    explanation: str,
) -> tuple[_Environment, set[str]]:
    affected = sorted(env.keys())
    ctx.collector.add_barrier(
        paragraph=ctx.paragraph.name,
        region_id=region_id,
        source_reference=_source_reference(ctx, stmt),
        reason=reason,
        affected_variables=affected,
        clears_entire_environment=True,
        diagnostic_code=diagnostic_code,
        explanation=explanation,
    )
    return {}, set(affected)


def _process_decision(
    ctx: _ParagraphContext, stmt: CanonicalStatement, env: _Environment, region_id: str
) -> tuple[_Environment, set[str]]:
    children = ctx.children_by_parent.get(stmt.statement_id, [])
    if not children:
        # Ninguna rama observable (todas vacias/elididas): COBOL garantiza
        # que una rama vacia no escribe nada, asi que el entorno de salida
        # es identico al de entrada -- conservador y correcto, nunca una
        # barrera artificial.
        return env, set()

    if any(child.branch_kind is None for child in children):
        # Defensivo (nunca alcanzable con el productor Java actual, que
        # siempre declara branch_kind en hijos de IF/EVALUATE): sin esa
        # senal no puede reconstruirse a que rama pertenece cada hijo.
        return _clear_environment_barrier(
            ctx, stmt, env, region_id,
            reason=PropagationBarrierReason.CONTROL_FLOW_BOUNDARY,
            diagnostic_code="DECISION_BRANCH_STRUCTURE_UNDETERMINED",
            explanation=(
                f"{stmt.kind.value} en {ctx.paragraph.name!r} tiene hijos sin branch_kind "
                "determinable: no puede reconstruirse la estructura de ramas de forma segura."
            ),
        )

    runs = _split_into_branch_runs(children)
    all_written: set[str] = set()
    for index, (branch_kind, branch_statements) in enumerate(runs):
        branch_region_id = _branch_region_id(region_id, stmt.statement_id, branch_kind, index)
        _branch_env, branch_written = _process_sequence(
            ctx, branch_statements, dict(env), branch_region_id
        )
        all_written |= branch_written

    merged_env = {key: value for key, value in env.items() if key not in all_written}
    return merged_env, all_written


def _process_sequence(
    ctx: _ParagraphContext,
    statements: Sequence[CanonicalStatement],
    env: _Environment,
    region_id: str,
) -> tuple[_Environment, set[str]]:
    written: set[str] = set()
    for stmt in statements:
        if stmt.kind in (StatementKind.IF, StatementKind.EVALUATE):
            env, stmt_written = _process_decision(ctx, stmt, env, region_id)
        else:
            env, stmt_written = _process_leaf(ctx, stmt, env, region_id)
        written |= stmt_written
        for known in env.values():
            if len(known.steps) > ctx.max_derivation_depth:
                raise SemanticPropagationError(
                    "invariante interno violado: derivation_depth excede la cantidad de "
                    f"statements del paragraph {ctx.paragraph.name!r}"
                )
    return env, written


# ---------------------------------------------------------------------------
# Registry de funciones de transferencia por SemanticEffectKind (Fase 7)
# ---------------------------------------------------------------------------


@dataclass
class _EffectOutcome:
    updates: dict[str, _KnownValue | None] = field(default_factory=dict)


def _effect_ordinal(effect: SemanticEffect) -> int:
    return int(effect.effect_id.rsplit("::", 1)[-1])


def _handle_assign_literal(
    ctx: _ParagraphContext, stmt: CanonicalStatement, effect: SemanticEffect, env: _Environment,
    region_id: str,
) -> _EffectOutcome:
    if not effect.target_data_items:
        return _EffectOutcome()
    target_raw = effect.target_data_items[0]
    symbol = ctx.symbols.resolve(target_raw)
    source_ref = _source_reference(ctx, stmt)
    if symbol.ambiguous:
        ctx.collector.add_barrier(
            paragraph=ctx.paragraph.name, region_id=region_id, source_reference=source_ref,
            reason=PropagationBarrierReason.AMBIGUOUS_SYMBOL,
            affected_variables=[target_raw], clears_entire_environment=False,
            diagnostic_code="AMBIGUOUS_DATA_ITEM_REFERENCE",
            explanation=f"{target_raw!r} coincide con mas de un data item por nombre simple.",
        )
        ctx.collector.add_fact(
            fact_kind=PropagationFactKind.BLOCKED_PROPAGATION, paragraph=ctx.paragraph.name,
            region_id=region_id, target_variable=target_raw, target_qualified_name=None,
            literal=None, source_variable=None, source_qualified_name=None,
            source_reference=source_ref, derivation_steps=(),
            support_status=SemanticSupportStatus.UNSUPPORTED,
            diagnostic_codes=["AMBIGUOUS_DATA_ITEM_REFERENCE"],
            explanation=(
                f"No se propaga: {target_raw!r} es ambiguo (multiples data items homonimos)."
            ),
        )
        return _EffectOutcome(updates={target_raw: None})

    step = PropagationStep(
        statement_id=stmt.statement_id, effect_id=effect.effect_id,
        operation=SemanticEffectKind.ASSIGN_LITERAL, source_variable=None,
        target_variable=target_raw, literal=effect.literal,
    )
    assert effect.literal is not None
    known = _KnownValue(
        literal=effect.literal, steps=(step,), fact_kind=PropagationFactKind.DIRECT_LITERAL,
        qualified_name=symbol.qualified_name,
    )
    ctx.collector.add_fact(
        fact_kind=PropagationFactKind.DIRECT_LITERAL, paragraph=ctx.paragraph.name,
        region_id=region_id, target_variable=target_raw,
        target_qualified_name=symbol.qualified_name, literal=effect.literal, source_variable=None,
        source_qualified_name=None, source_reference=source_ref, derivation_steps=(step,),
        support_status=SemanticSupportStatus.FULLY_SUPPORTED, diagnostic_codes=[],
        explanation=f"MOVE de literal directo: {target_raw!r} recibe {effect.literal!r}.",
    )
    return _EffectOutcome(updates={symbol.identity_key: known})


def _handle_copy_value(
    ctx: _ParagraphContext, stmt: CanonicalStatement, effect: SemanticEffect, env: _Environment,
    region_id: str,
) -> _EffectOutcome:
    source_ref = _source_reference(ctx, stmt)
    updates: dict[str, _KnownValue | None] = {}

    if len(effect.source_data_items) != 1 or len(effect.target_data_items) != 1:
        # Copia multi-campo: la correspondencia campo a campo no puede
        # demostrarse (ver AMBIGUOUS_MULTI_FIELD_COPY del efecto origen).
        # Nunca se adivina un emparejamiento: cada target declarado queda
        # invalidado.
        for target_raw in effect.target_data_items:
            symbol = ctx.symbols.resolve(target_raw)
            ctx.collector.add_fact(
                fact_kind=PropagationFactKind.UNRESOLVED_COPY, paragraph=ctx.paragraph.name,
                region_id=region_id, target_variable=target_raw,
                target_qualified_name=symbol.qualified_name, literal=None,
                source_variable=None, source_qualified_name=None, source_reference=source_ref,
                derivation_steps=(), support_status=SemanticSupportStatus.UNSUPPORTED,
                diagnostic_codes=["AMBIGUOUS_MULTI_FIELD_COPY"],
                explanation=(
                    f"MOVE con multiples fuentes/destinos: no se demuestra correspondencia "
                    f"campo a campo para {target_raw!r}."
                ),
            )
            updates[symbol.identity_key] = None
        ctx.collector.add_barrier(
            paragraph=ctx.paragraph.name, region_id=region_id, source_reference=source_ref,
            reason=PropagationBarrierReason.UNKNOWN_SIDE_EFFECT,
            affected_variables=list(effect.target_data_items), clears_entire_environment=False,
            diagnostic_code="AMBIGUOUS_MULTI_FIELD_COPY",
            explanation="Copia con multiples fuentes/destinos: efecto por campo desconocido.",
        )
        return _EffectOutcome(updates=updates)

    source_raw = effect.source_data_items[0]
    target_raw = effect.target_data_items[0]
    source_symbol = ctx.symbols.resolve(source_raw)
    target_symbol = ctx.symbols.resolve(target_raw)

    if target_symbol.ambiguous:
        ctx.collector.add_barrier(
            paragraph=ctx.paragraph.name, region_id=region_id, source_reference=source_ref,
            reason=PropagationBarrierReason.AMBIGUOUS_SYMBOL,
            affected_variables=[target_raw], clears_entire_environment=False,
            diagnostic_code="AMBIGUOUS_DATA_ITEM_REFERENCE",
            explanation=f"Target {target_raw!r} es ambiguo (multiples data items homonimos).",
        )
        ctx.collector.add_fact(
            fact_kind=PropagationFactKind.BLOCKED_PROPAGATION, paragraph=ctx.paragraph.name,
            region_id=region_id, target_variable=target_raw, target_qualified_name=None,
            literal=None, source_variable=source_raw, source_qualified_name=None,
            source_reference=source_ref, derivation_steps=(),
            support_status=SemanticSupportStatus.UNSUPPORTED,
            diagnostic_codes=["AMBIGUOUS_DATA_ITEM_REFERENCE"],
            explanation=f"No se propaga: target {target_raw!r} es ambiguo.",
        )
        return _EffectOutcome(updates={target_raw: None})

    if source_symbol.ambiguous:
        ctx.collector.add_barrier(
            paragraph=ctx.paragraph.name, region_id=region_id, source_reference=source_ref,
            reason=PropagationBarrierReason.AMBIGUOUS_SYMBOL,
            affected_variables=[source_raw], clears_entire_environment=False,
            diagnostic_code="AMBIGUOUS_DATA_ITEM_REFERENCE",
            explanation=f"Source {source_raw!r} es ambiguo (multiples data items homonimos).",
        )
        ctx.collector.add_fact(
            fact_kind=PropagationFactKind.BLOCKED_PROPAGATION, paragraph=ctx.paragraph.name,
            region_id=region_id, target_variable=target_raw,
            target_qualified_name=target_symbol.qualified_name, literal=None,
            source_variable=source_raw, source_qualified_name=None, source_reference=source_ref,
            derivation_steps=(), support_status=SemanticSupportStatus.UNSUPPORTED,
            diagnostic_codes=["AMBIGUOUS_DATA_ITEM_REFERENCE"],
            explanation=f"No se propaga: source {source_raw!r} es ambiguo; target invalidado.",
        )
        return _EffectOutcome(updates={target_symbol.identity_key: None})

    known_source = env.get(source_symbol.identity_key)
    if known_source is None:
        ctx.collector.add_fact(
            fact_kind=PropagationFactKind.UNRESOLVED_COPY, paragraph=ctx.paragraph.name,
            region_id=region_id, target_variable=target_raw,
            target_qualified_name=target_symbol.qualified_name, literal=None,
            source_variable=source_raw, source_qualified_name=source_symbol.qualified_name,
            source_reference=source_ref, derivation_steps=(),
            support_status=SemanticSupportStatus.PARTIALLY_SUPPORTED, diagnostic_codes=[],
            explanation=f"{source_raw!r} no tiene valor conocido en este punto: copia no resuelta.",
        )
        return _EffectOutcome(updates={target_symbol.identity_key: None})

    step = PropagationStep(
        statement_id=stmt.statement_id, effect_id=effect.effect_id,
        operation=SemanticEffectKind.COPY_VALUE, source_variable=source_raw,
        target_variable=target_raw, literal=known_source.literal,
    )
    new_steps = known_source.steps + (step,)
    known = _KnownValue(
        literal=known_source.literal,
        steps=new_steps,
        fact_kind=PropagationFactKind.PROPAGATED_LITERAL,
        qualified_name=target_symbol.qualified_name,
    )
    ctx.collector.add_fact(
        fact_kind=PropagationFactKind.PROPAGATED_LITERAL, paragraph=ctx.paragraph.name,
        region_id=region_id, target_variable=target_raw,
        target_qualified_name=target_symbol.qualified_name, literal=known_source.literal,
        source_variable=source_raw, source_qualified_name=source_symbol.qualified_name,
        source_reference=source_ref, derivation_steps=new_steps,
        support_status=SemanticSupportStatus.PARTIALLY_SUPPORTED, diagnostic_codes=[],
        explanation=(
            f"{target_raw!r} copia el valor conocido de {source_raw!r} "
            f"({known_source.literal!r}), demostrado a traves de {len(new_steps)} paso(s)."
        ),
    )
    return _EffectOutcome(updates={target_symbol.identity_key: known})


def _condition_barrier_reason(condition: CanonicalConditionName | None) -> PropagationBarrierReason:
    if condition is not None and any(value.through_value is not None for value in condition.values):
        return PropagationBarrierReason.CONDITION_VALUE_RANGE
    return PropagationBarrierReason.MULTIPLE_CONDITION_VALUES


def _handle_set_condition_true(
    ctx: _ParagraphContext, stmt: CanonicalStatement, effect: SemanticEffect, env: _Environment,
    region_id: str,
) -> _EffectOutcome:
    source_ref = _source_reference(ctx, stmt)
    parent = effect.parent_data_item
    assert parent is not None
    parent_symbol = ctx.symbols.resolve(parent)

    if effect.literal is None:
        condition = ctx.conditions_by_qualified_name.get(effect.condition_name or "")
        reason = _condition_barrier_reason(condition)
        ctx.collector.add_barrier(
            paragraph=ctx.paragraph.name, region_id=region_id, source_reference=source_ref,
            reason=reason, affected_variables=[parent], clears_entire_environment=False,
            diagnostic_code="CONDITION_HAS_MULTIPLE_OR_RANGE_VALUES",
            explanation=(
                f"SET {effect.condition_name} TO TRUE: la condicion no tiene un unico VALUE "
                "sin THRU demostrable; no se propaga un literal."
            ),
        )
        ctx.collector.add_fact(
            fact_kind=PropagationFactKind.BLOCKED_PROPAGATION, paragraph=ctx.paragraph.name,
            region_id=region_id, target_variable=parent,
            target_qualified_name=parent_symbol.qualified_name, literal=None, source_variable=None,
            source_qualified_name=None, source_reference=source_ref, derivation_steps=(),
            support_status=SemanticSupportStatus.PARTIALLY_SUPPORTED,
            diagnostic_codes=["CONDITION_HAS_MULTIPLE_OR_RANGE_VALUES"],
            explanation=(
                f"SET TO TRUE resuelto pero con multiples VALUE/THRU: {parent!r} invalidado."
            ),
        )
        return _EffectOutcome(updates={parent_symbol.identity_key: None})

    step = PropagationStep(
        statement_id=stmt.statement_id, effect_id=effect.effect_id,
        operation=SemanticEffectKind.SET_CONDITION_TRUE, source_variable=None,
        target_variable=parent, literal=effect.literal,
    )
    known = _KnownValue(
        literal=effect.literal, steps=(step,), fact_kind=PropagationFactKind.CONDITION_LITERAL,
        qualified_name=parent_symbol.qualified_name,
    )
    ctx.collector.add_fact(
        fact_kind=PropagationFactKind.CONDITION_LITERAL, paragraph=ctx.paragraph.name,
        region_id=region_id, target_variable=parent,
        target_qualified_name=parent_symbol.qualified_name, literal=effect.literal,
        source_variable=None, source_qualified_name=None, source_reference=source_ref,
        derivation_steps=(step,), support_status=SemanticSupportStatus.FULLY_SUPPORTED,
        diagnostic_codes=[],
        explanation=(
            f"SET {effect.condition_name} TO TRUE resuelto contra un unico VALUE: "
            f"{parent!r} recibe {effect.literal!r}."
        ),
    )
    return _EffectOutcome(updates={parent_symbol.identity_key: known})


def _handle_set_condition_false(
    ctx: _ParagraphContext, stmt: CanonicalStatement, effect: SemanticEffect, env: _Environment,
    region_id: str,
) -> _EffectOutcome:
    """SET condicion-88 TO FALSE nunca produce DIRECT_LITERAL/
    CONDITION_LITERAL/PROPAGATED_LITERAL: negar una condicion de nivel 88
    solo garantiza que el VALUE (o los VALUE, o el rango THRU) declarado
    no se satisface -- COBOL nunca define asi un unico valor alternativo
    para el data item padre, sin importar si la condicion tiene un unico
    VALUE, varios VALUE, o un THRU. Por eso el motivo de barrera es
    siempre `CONDITION_FALSE_VALUE_UNDETERMINED`, nunca
    `MULTIPLE_CONDITION_VALUES` (reservado a una condicion que
    REALMENTE declara mas de un VALUE) ni `CONDITION_VALUE_RANGE`
    (reservado a VALUE THRU): ninguna de esas dos categorias describe la
    causa real del bloqueo aqui, que es la negacion en si misma."""
    source_ref = _source_reference(ctx, stmt)
    parent = effect.parent_data_item
    assert parent is not None
    parent_symbol = ctx.symbols.resolve(parent)
    ctx.collector.add_barrier(
        paragraph=ctx.paragraph.name, region_id=region_id, source_reference=source_ref,
        reason=PropagationBarrierReason.CONDITION_FALSE_VALUE_UNDETERMINED,
        affected_variables=[parent], clears_entire_environment=False,
        diagnostic_code="SET_CONDITION_FALSE_HAS_NO_UNIQUE_PARENT_VALUE",
        explanation=(
            f"SET {effect.condition_name} TO FALSE: negar una condicion de nivel 88 solo "
            "establece que el padre no satisface el/los VALUE declarado(s); puede haber "
            "multiples valores alternativos compatibles, asi que no se infiere ningun literal."
        ),
    )
    ctx.collector.add_fact(
        fact_kind=PropagationFactKind.BLOCKED_PROPAGATION, paragraph=ctx.paragraph.name,
        region_id=region_id, target_variable=parent,
        target_qualified_name=parent_symbol.qualified_name, literal=None, source_variable=None,
        source_qualified_name=None, source_reference=source_ref, derivation_steps=(),
        support_status=SemanticSupportStatus.PARTIALLY_SUPPORTED,
        diagnostic_codes=["SET_CONDITION_FALSE_HAS_NO_UNIQUE_PARENT_VALUE"],
        explanation=f"SET TO FALSE invalida {parent!r} sin afirmar un nuevo valor.",
    )
    return _EffectOutcome(updates={parent_symbol.identity_key: None})


def _invalidate_targets(
    ctx: _ParagraphContext, stmt: CanonicalStatement, effect: SemanticEffect, region_id: str,
    *, reason: PropagationBarrierReason, diagnostic_code: str, explanation: str,
) -> _EffectOutcome:
    source_ref = _source_reference(ctx, stmt)
    targets = effect.target_data_items
    if not targets:
        return _EffectOutcome()
    updates: dict[str, _KnownValue | None] = {}
    for target_raw in targets:
        symbol = ctx.symbols.resolve(target_raw)
        ctx.collector.add_fact(
            fact_kind=PropagationFactKind.INVALIDATED_VALUE, paragraph=ctx.paragraph.name,
            region_id=region_id, target_variable=target_raw,
            target_qualified_name=symbol.qualified_name, literal=None, source_variable=None,
            source_qualified_name=None, source_reference=source_ref, derivation_steps=(),
            support_status=SemanticSupportStatus.PARTIALLY_SUPPORTED,
            diagnostic_codes=[diagnostic_code], explanation=explanation,
        )
        updates[symbol.identity_key] = None
    ctx.collector.add_barrier(
        paragraph=ctx.paragraph.name, region_id=region_id, source_reference=source_ref,
        reason=reason, affected_variables=list(targets), clears_entire_environment=False,
        diagnostic_code=diagnostic_code, explanation=explanation,
    )
    return _EffectOutcome(updates=updates)


def _handle_set_value(
    ctx: _ParagraphContext, stmt: CanonicalStatement, effect: SemanticEffect, env: _Environment,
    region_id: str,
) -> _EffectOutcome:
    return _invalidate_targets(
        ctx, stmt, effect, region_id, reason=PropagationBarrierReason.UNKNOWN_SIDE_EFFECT,
        diagnostic_code="SET_SEMANTIC_VARIANT_UNRESOLVED",
        explanation=(
            "SET ordinario: puede representar indice, puntero, UP/DOWN u otra semantica "
            "distinta de asignacion COBOL; nunca se infiere un literal."
        ),
    )


def _handle_compute_value(
    ctx: _ParagraphContext, stmt: CanonicalStatement, effect: SemanticEffect, env: _Environment,
    region_id: str,
) -> _EffectOutcome:
    return _invalidate_targets(
        ctx, stmt, effect, region_id, reason=PropagationBarrierReason.COMPUTED_VALUE,
        diagnostic_code="COMPUTE_EXPRESSION_NOT_EVALUATED",
        explanation="COMPUTE: la expresion no se evalua ni se hace folding aritmetico.",
    )


def _handle_execute_sql(
    ctx: _ParagraphContext, stmt: CanonicalStatement, effect: SemanticEffect, env: _Environment,
    region_id: str,
) -> _EffectOutcome:
    source_ref = _source_reference(ctx, stmt)
    host_variables = effect.sql_host_variables
    if not host_variables:
        return _EffectOutcome()
    updates: dict[str, _KnownValue | None] = {}
    for raw in host_variables:
        symbol = ctx.symbols.resolve(raw)
        ctx.collector.add_fact(
            fact_kind=PropagationFactKind.INVALIDATED_VALUE, paragraph=ctx.paragraph.name,
            region_id=region_id, target_variable=raw, target_qualified_name=symbol.qualified_name,
            literal=None, source_variable=None, source_qualified_name=None,
            source_reference=source_ref, derivation_steps=(),
            support_status=SemanticSupportStatus.PARTIALLY_SUPPORTED,
            diagnostic_codes=["SQL_HOST_VARIABLE_DIRECTION_UNRESOLVED"],
            explanation=(
                f"EXEC SQL no distingue direccion de host variables: {raw!r} invalidado "
                "de forma conservadora."
            ),
        )
        updates[symbol.identity_key] = None
    ctx.collector.add_barrier(
        paragraph=ctx.paragraph.name, region_id=region_id, source_reference=source_ref,
        reason=PropagationBarrierReason.SQL_HOST_DIRECTION_UNKNOWN,
        affected_variables=list(host_variables), clears_entire_environment=False,
        diagnostic_code="SQL_HOST_VARIABLE_DIRECTION_UNRESOLVED",
        explanation="CanonicalSqlAccess no distingue host variables de entrada/salida.",
    )
    return _EffectOutcome(updates=updates)


def _handle_control_transfer(
    ctx: _ParagraphContext, stmt: CanonicalStatement, effect: SemanticEffect, env: _Environment,
    region_id: str,
) -> _EffectOutcome:
    # Un unico effect kind (CONTROL_TRANSFER) cubre tanto GO TO como
    # PERFORM (ver semantic_effects_analyzer._normalize_go_to/_perform):
    # se distinguen por StatementKind, no por otro effect kind, porque el
    # modelo de SemanticEffect no los separa (Fase 7).
    reason = (
        PropagationBarrierReason.LOOP_OR_PERFORM_BOUNDARY
        if stmt.kind == StatementKind.PERFORM
        else PropagationBarrierReason.CONTROL_FLOW_BOUNDARY
    )
    source_ref = _source_reference(ctx, stmt)
    affected = sorted(env.keys())
    for target_raw in affected:
        symbol_qualified = env[target_raw].qualified_name
        ctx.collector.add_fact(
            fact_kind=PropagationFactKind.INVALIDATED_VALUE, paragraph=ctx.paragraph.name,
            region_id=region_id, target_variable=target_raw,
            target_qualified_name=symbol_qualified, literal=None, source_variable=None,
            source_qualified_name=None, source_reference=source_ref, derivation_steps=(),
            support_status=SemanticSupportStatus.PARTIALLY_SUPPORTED,
            diagnostic_codes=["CONTROL_TRANSFER_CLEARS_ENVIRONMENT"],
            explanation=(
                f"{stmt.kind.value} limpia el entorno de propagacion; no se propaga a traves "
                "del cuerpo o retorno."
            ),
        )
    ctx.collector.add_barrier(
        paragraph=ctx.paragraph.name, region_id=region_id, source_reference=source_ref,
        reason=reason, affected_variables=affected, clears_entire_environment=True,
        diagnostic_code="CONTROL_TRANSFER_CLEARS_ENVIRONMENT",
        explanation=f"{stmt.kind.value}: limite de flujo de control, entorno reiniciado.",
    )
    return _EffectOutcome(updates={key: None for key in affected})


def _handle_unknown_effect(
    ctx: _ParagraphContext, stmt: CanonicalStatement, effect: SemanticEffect, env: _Environment,
    region_id: str,
) -> _EffectOutcome:
    reason = (
        PropagationBarrierReason.UNSUPPORTED_STATEMENT
        if effect.kind == SemanticEffectKind.UNSUPPORTED_STATEMENT
        else PropagationBarrierReason.UNKNOWN_SIDE_EFFECT
    )
    source_ref = _source_reference(ctx, stmt)
    affected = sorted(env.keys())
    for target_raw in affected:
        ctx.collector.add_fact(
            fact_kind=PropagationFactKind.INVALIDATED_VALUE, paragraph=ctx.paragraph.name,
            region_id=region_id, target_variable=target_raw,
            target_qualified_name=env[target_raw].qualified_name, literal=None,
            source_variable=None, source_qualified_name=None, source_reference=source_ref,
            derivation_steps=(), support_status=SemanticSupportStatus.UNSUPPORTED,
            diagnostic_codes=["UNKNOWN_STATEMENT_SIDE_EFFECT"],
            explanation=(
                f"{effect.kind.value}: efecto real desconocido, nunca se asume ausencia de "
                "side effects."
            ),
        )
    ctx.collector.add_barrier(
        paragraph=ctx.paragraph.name, region_id=region_id, source_reference=source_ref,
        reason=reason, affected_variables=affected, clears_entire_environment=True,
        diagnostic_code="UNKNOWN_STATEMENT_SIDE_EFFECT",
        explanation=f"{effect.kind.value}: sentencia no interpretada estructuralmente.",
    )
    return _EffectOutcome(updates={key: None for key in affected})


# Registry unico SemanticEffectKind -> funcion de transferencia (Fase 7):
# nunca dispersar el comportamiento en if/elif fuera de esta tabla.
_HANDLERS = {
    SemanticEffectKind.ASSIGN_LITERAL: _handle_assign_literal,
    SemanticEffectKind.COPY_VALUE: _handle_copy_value,
    SemanticEffectKind.SET_CONDITION_TRUE: _handle_set_condition_true,
    SemanticEffectKind.SET_CONDITION_FALSE: _handle_set_condition_false,
    SemanticEffectKind.SET_VALUE: _handle_set_value,
    SemanticEffectKind.COMPUTE_VALUE: _handle_compute_value,
    SemanticEffectKind.EXECUTE_SQL: _handle_execute_sql,
    SemanticEffectKind.CONTROL_TRANSFER: _handle_control_transfer,
    SemanticEffectKind.PRESERVED_STATEMENT: _handle_unknown_effect,
    SemanticEffectKind.UNSUPPORTED_STATEMENT: _handle_unknown_effect,
}


def _process_leaf(
    ctx: _ParagraphContext, stmt: CanonicalStatement, env: _Environment, region_id: str
) -> tuple[_Environment, set[str]]:
    effects = sorted(
        ctx.effects_by_statement_id.get(stmt.statement_id, []), key=_effect_ordinal
    )
    if not effects:
        return env, set()

    new_env = dict(env)
    written: set[str] = set()
    for effect in effects:
        handler = _HANDLERS.get(effect.kind)
        if handler is None:
            continue
        outcome = handler(ctx, stmt, effect, new_env, region_id)
        for key, value in outcome.updates.items():
            if value is None:
                new_env.pop(key, None)
            else:
                new_env[key] = value
            written.add(key)
    return new_env, written


# ---------------------------------------------------------------------------
# Punto de entrada por programa/paragraph
# ---------------------------------------------------------------------------


def _children_by_parent(
    statements: Sequence[CanonicalStatement],
) -> dict[str, list[CanonicalStatement]]:
    children: dict[str, list[CanonicalStatement]] = defaultdict(list)
    for stmt in statements:
        if stmt.parent_statement_id is not None:
            children[stmt.parent_statement_id].append(stmt)
    return dict(children)


def _top_level_statements(statements: Sequence[CanonicalStatement]) -> list[CanonicalStatement]:
    return [stmt for stmt in statements if stmt.parent_statement_id is None]


def _conditions_by_qualified_name(
    condition_names: Sequence[CanonicalConditionName],
) -> dict[str, CanonicalConditionName]:
    return {condition.qualified_name: condition for condition in condition_names}


def _analyze_program(
    program: CanonicalProgram, program_effects: ProgramSemanticEffects
) -> ProgramSemanticPropagation:
    symbols = _build_symbol_table(program)
    conditions_by_qualified_name = _conditions_by_qualified_name(program.condition_names)
    effects_by_statement_id: dict[str, list[SemanticEffect]] = defaultdict(list)
    for effect in program_effects.effects:
        effects_by_statement_id[effect.source_reference.statement_id].append(effect)

    collector = _Collector(program.program_name)

    for paragraph in program.paragraphs:
        # Cada paragraph comienza con un entorno vacio (Fase 6.1/6.2):
        # nunca se propaga ningun valor entre paragraphs.
        children_by_parent = _children_by_parent(paragraph.statements)
        ctx = _ParagraphContext(
            program_name=program.program_name,
            paragraph=paragraph,
            children_by_parent=children_by_parent,
            effects_by_statement_id=dict(effects_by_statement_id),
            symbols=symbols,
            conditions_by_qualified_name=conditions_by_qualified_name,
            collector=collector,
            max_derivation_depth=max(len(paragraph.statements), 1),
        )
        top_level = _top_level_statements(paragraph.statements)
        root_region_id = _paragraph_root_region_id(program.program_name, paragraph.name)
        _process_sequence(ctx, top_level, {}, root_region_id)

    facts = sorted(collector.facts, key=lambda fact: fact.fact_id)
    barriers = sorted(collector.barriers, key=lambda barrier: barrier.barrier_id)
    return ProgramSemanticPropagation(
        program=program.program_name,
        paragraph_count=len(program.paragraphs),
        fact_count=len(facts),
        barrier_count=len(barriers),
        facts=facts,
        barriers=barriers,
    )


def _build_summary(programs: Sequence[ProgramSemanticPropagation]) -> SemanticPropagationSummary:
    counts_by_fact_kind: dict[PropagationFactKind, int] = {}
    counts_by_barrier_reason: dict[PropagationBarrierReason, int] = {}
    for program in programs:
        for fact in program.facts:
            counts_by_fact_kind[fact.fact_kind] = counts_by_fact_kind.get(fact.fact_kind, 0) + 1
        for barrier in program.barriers:
            counts_by_barrier_reason[barrier.reason] = (
                counts_by_barrier_reason.get(barrier.reason, 0) + 1
            )

    def _count(kind: PropagationFactKind) -> int:
        return counts_by_fact_kind.get(kind, 0)

    return SemanticPropagationSummary(
        program_count=len(programs),
        paragraph_count=sum(program.paragraph_count for program in programs),
        fact_count=sum(program.fact_count for program in programs),
        direct_literal_count=_count(PropagationFactKind.DIRECT_LITERAL),
        propagated_literal_count=_count(PropagationFactKind.PROPAGATED_LITERAL),
        condition_literal_count=_count(PropagationFactKind.CONDITION_LITERAL),
        unresolved_copy_count=_count(PropagationFactKind.UNRESOLVED_COPY),
        invalidated_count=_count(PropagationFactKind.INVALIDATED_VALUE),
        blocked_count=_count(PropagationFactKind.BLOCKED_PROPAGATION),
        barrier_count=sum(program.barrier_count for program in programs),
        counts_by_fact_kind=counts_by_fact_kind,
        counts_by_barrier_reason=counts_by_barrier_reason,
    )


def analyze_semantic_propagation(
    *,
    canonical_programs: Sequence[CanonicalProgram],
    semantic_effects: SemanticEffectsArtifact,
    run_id: str,
    source_package_hash: str,
    source_artifact_hashes: dict[str, str],
) -> SemanticPropagationArtifact:
    """Punto de entrada del analizador puro (Fase 4). Determinista: misma
    entrada siempre produce el mismo `SemanticPropagationArtifact`. Nunca
    modifica `canonical_programs`/`semantic_effects`."""
    effects_by_program = {program.program: program for program in semantic_effects.programs}
    canonical_by_name = {program.program_name: program for program in canonical_programs}

    if set(canonical_by_name) != set(effects_by_program):
        raise SemanticPropagationError(
            "los programas de SemanticEffectsArtifact no coinciden con CanonicalProgram[]"
        )
    if source_package_hash != semantic_effects.source_package_hash:
        raise SemanticPropagationError(
            "source_package_hash no coincide entre CanonicalProgram y SemanticEffectsArtifact"
        )

    programs = sorted(
        (
            _analyze_program(canonical_by_name[name], effects_by_program[name])
            for name in canonical_by_name
        ),
        key=lambda program: program.program,
    )
    summary = _build_summary(programs)
    return SemanticPropagationArtifact(
        semantic_effects_schema_version=semantic_effects.schema_version,
        semantic_effects_analyzer_version=semantic_effects.analyzer_version,
        run_id=run_id,
        source_package_hash=source_package_hash,
        source_artifact_hashes=dict(source_artifact_hashes),
        summary=summary,
        programs=programs,
    )
