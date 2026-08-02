"""Analizador PURO de propagacion interprocedural conservadora en shadow
mode (Fase 7 de la ampliacion semantica,
`feat/interprocedural-propagation-shadow`).

Recibe `CanonicalProgram[]`, `SemanticEffectsArtifact`,
`SemanticPropagationArtifact` e `InterproceduralCallLinkageArtifact` ya
calculados (nunca los vuelve a derivar) y devuelve un
`InterproceduralPropagationArtifact`. Nunca:

- lee filesystem (responsabilidad de `interprocedural_propagation_service.py`);
- accede a Neo4j ni consulta `SemanticGraph`;
- ejecuta un LLM;
- reinterpreta `source_text` (usa exclusivamente `PropagatedValueFact` ya
  demostrados por `semantic_propagation_analyzer.py`, mas la ESTRUCTURA
  ya tipada de `CanonicalStatement`/`InterproceduralCallSite`/
  `InterproceduralArgumentBinding` -- nunca vuelve a evaluar una
  asignacion COBOL desde cero);
- modifica los objetos de entrada;
- intenta fixed point sobre ciclos (un SCC de 2+ programas, o un
  self-call, bloquea la propagacion para esos call sites -- nunca se
  itera hasta converger).

Alcance: solo CALL con target literal, `resolution_status=
RESOLVED_INTERNAL`, y bindings actual-formal suficientemente
estructurados (`ArgumentBindingStatus.RESOLVED_POSITIONAL`). El resto
queda bloqueado explicitamente (ver `InterproceduralPropagationBarrier`).

## Resolucion de "valor conocido en un punto" (deliberadamente conservadora)

`_known_literal_at` combina dos niveles, nunca reinterpreta COBOL:

1. **Intraprograma** (`PropagatedValueFact` ya demostrados, Fase 4):
   busca el hecho MAS RECIENTE (por posicion real dentro de
   `CanonicalParagraph.statements`, nunca por orden lexical de
   `fact_id`) para la misma variable, EN EL MISMO `parent_statement_id`
   inmediato que el punto de consulta (misma rama directa, o ambos
   top-level) -- una simplificacion deliberada: un valor establecido en
   una rama ANCESTRA (antes de entrar a un IF/EVALUATE anidado) no se
   considera visible dentro de la rama. Es conservadora (nunca produce
   un falso positivo, en el peor caso dejar de propagar un caso valido)
   y evita reimplementar el algoritmo de regiones/branches completo de
   `semantic_propagation_analyzer.py`.
2. **Interprocedural** (Fase 7, solo si el nivel 1 no encontro NINGUN
   hecho para esa variable -- el programa nunca la toco por su cuenta):
   si la variable es un formal/LINKAGE que recibio un `ENTRY_FACT`
   `PROPAGATED` de TODOS sus call sites entrantes elegibles con el MISMO
   literal (o de uno solo), ese literal se considera conocido. Si dos
   callers entrantes proponen literales distintos, el valor NUNCA se
   usa (ausencia de union/fixed point: solo se propaga cuando hay
   acuerdo total) -- pero NUNCA en silencio: se emite el diagnostico
   explicito `MULTIPLE_CALLER_VALUES_FOR_<key_variable>` en el
   `InterproceduralProgramAnalysis` del programa cuyo formal recibio
   valores en conflicto (ver `_EntryEnvironment.conflicted_keys`). La
   discrepancia sigue siendo rastreable sin campos nuevos: cada
   `ENTRY_FACT` `PROPAGATED` individual conserva su propio
   `literal`/`source_fact_ids`/`caller_program`, asi que filtrar
   `entry_facts` por `formal_name` reconstruye ambos valores en
   conflicto y su procedencia exacta."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from ..contracts.canonical import CanonicalProgram, CanonicalStatement
from ..contracts.enums import (
    CallPassingMode,
    CallTargetKind,
    ProgramTerminationKind,
    StatementKind,
)
from ..contracts.interprocedural_call_linkage import (
    ArgumentBindingStatus,
    InterproceduralArgumentBinding,
    InterproceduralCallLinkageArtifact,
    InterproceduralCallSite,
    ProgramResolutionStatus,
)
from ..contracts.interprocedural_propagation import (
    InterproceduralFactKind,
    InterproceduralProgramAnalysis,
    InterproceduralPropagationArtifact,
    InterproceduralPropagationBarrier,
    InterproceduralPropagationDirection,
    InterproceduralPropagationFact,
    InterproceduralPropagationStatus,
    InterproceduralPropagationSummary,
)
from ..contracts.semantic_effects import SemanticEffectsArtifact
from ..contracts.semantic_propagation import (
    PropagatedValueFact,
    PropagationFactKind,
    SemanticPropagationArtifact,
)

_LITERAL_FACT_KINDS = frozenset(
    {
        PropagationFactKind.DIRECT_LITERAL,
        PropagationFactKind.PROPAGATED_LITERAL,
        PropagationFactKind.CONDITION_LITERAL,
    }
)

_BINDING_STATUS_TO_BARRIER: dict[ArgumentBindingStatus, InterproceduralPropagationBarrier] = {
    ArgumentBindingStatus.ACTUAL_UNRESOLVED: InterproceduralPropagationBarrier.UNRESOLVED_ACTUAL,
    ArgumentBindingStatus.AMBIGUOUS_ACTUAL: InterproceduralPropagationBarrier.UNRESOLVED_ACTUAL,
    ArgumentBindingStatus.FORMAL_UNRESOLVED: InterproceduralPropagationBarrier.UNRESOLVED_FORMAL,
    ArgumentBindingStatus.MISSING_ACTUAL: InterproceduralPropagationBarrier.MISSING_ARGUMENT,
    ArgumentBindingStatus.EXTRA_ACTUAL: InterproceduralPropagationBarrier.EXTRA_ARGUMENT,
    ArgumentBindingStatus.UNSUPPORTED_ARGUMENT: (
        InterproceduralPropagationBarrier.UNSUPPORTED_CONTROL_FLOW
    ),
}

# ---------------------------------------------------------------------------
# Indices estructurales: orden de statements y hechos de SemanticPropagation.
# ---------------------------------------------------------------------------


class _StatementLocation:
    __slots__ = ("paragraph", "ordinal", "parent_statement_id")

    def __init__(self, paragraph: str, ordinal: int, parent_statement_id: str | None) -> None:
        self.paragraph = paragraph
        self.ordinal = ordinal
        self.parent_statement_id = parent_statement_id


def _flatten_statement_locations(program: CanonicalProgram) -> dict[str, _StatementLocation]:
    locations: dict[str, _StatementLocation] = {}
    for paragraph in program.paragraphs:
        for ordinal, stmt in enumerate(paragraph.statements):
            locations[stmt.statement_id] = _StatementLocation(
                paragraph.name, ordinal, stmt.parent_statement_id
            )
    return locations


def _effective_exit_cutoff(
    statements: Sequence[CanonicalStatement],
) -> tuple[int, InterproceduralPropagationBarrier | None]:
    """Posicion de corte para la busqueda de "valor final al retornar"
    (Fase 7, salida RETURNING/BY REFERENCE), mas una barrera opcional
    cuando existe CERTEZA estructural de que el callee nunca retorna
    control normalmente al caller (Fase 7b, ver
    `docs/INTERPROCEDURAL_PROPAGATION.md`).

    Un terminador (`kind=PROGRAM_TERMINATION`) solo califica como punto
    de retorno final cuando es el UNICO terminador de la lista Y es top-
    level (`parent_statement_id is None`) Y es el ultimo statement:

    - Si NO hay exactamente un terminador final incondicional que
      califique (ausente, condicional -- dentro de IF/EVALUATE/PERFORM
      inline --, no-final, o hay 2+ terminadores en cualquier posicion):
      NUNCA se recorta nada, y NUNCA se elige uno arbitrariamente entre
      varios candidatos. `_known_literal_at` seguira su busqueda normal
      hasta el final real de la lista -- si `_handle_unknown_effect`
      (Fase 4) ya invalido el entorno en ese punto (el caso tipico),
      `_exit_fact_for_binding` producira su fallback existente
      `INVALIDATED`/`RETURN_VALUE_NOT_PROVABLY_DETERMINISTIC`: "bloqueo
      como no deterministico" por ausencia de evidencia, nunca un
      `BLOCKED` estructural (esa barrera se reserva para certeza, no
      para falta de prueba).
    - Si el UNICO terminador final califica y es `GOBACK`/`EXIT_PROGRAM`:
      retorna control normalmente al caller -- se recorta EXACTAMENTE
      ese unico statement (nunca mas: una sentencia `OTHER` invalidante
      genuina justo antes, p. ej. un `MOVE CORRESPONDING`, sigue
      invalidando con normalidad).
    - Si el UNICO terminador final califica y es `STOP_RUN`/`UNKNOWN`:
      CERTEZA estructural de que el callee NUNCA retorna control al
      caller (`STOP RUN` termina el run unit COMPLETO segun la semantica
      estandar de COBOL, incluso invocado desde un `CALL`; `UNKNOWN`
      nunca se asume equivalente a `GOBACK`/`EXIT PROGRAM` sin evidencia)
      -- devuelve `NON_RETURNING_TERMINATION`, un `BLOCKED` estructural
      explicito, nunca un simple `INVALIDATED` por falta de evidencia.

    Nunca inspecciona `source_text`: la unica senal es
    `CanonicalStatement.kind`/`program_termination_kind`, ya
    estructuradas por el parser Java via la API tipada de ProLeap."""
    cutoff = len(statements)
    if cutoff == 0:
        return cutoff, None

    terminator_count = sum(1 for s in statements if s.kind == StatementKind.PROGRAM_TERMINATION)
    last = statements[cutoff - 1]
    qualifies = (
        terminator_count == 1
        and last.kind == StatementKind.PROGRAM_TERMINATION
        and last.parent_statement_id is None
    )
    if not qualifies:
        return cutoff, None

    if last.program_termination_kind in (
        ProgramTerminationKind.GOBACK,
        ProgramTerminationKind.EXIT_PROGRAM,
    ):
        return cutoff - 1, None

    # STOP_RUN o UNKNOWN: nunca es un retorno valido al caller.
    return cutoff, InterproceduralPropagationBarrier.NON_RETURNING_TERMINATION


_FactCandidate = tuple[int, str | None, PropagatedValueFact]


def _build_facts_index(
    semantic_propagation: SemanticPropagationArtifact,
    statement_locations: dict[str, dict[str, _StatementLocation]],
) -> dict[tuple[str, str, str], list[_FactCandidate]]:
    """(program, paragraph, variable_key) -> [(ordinal, parent_statement_id, fact)].
    `variable_key` es `target_qualified_name` cuando existe, sino
    `target_variable` -- mismo criterio de resolucion que
    `semantic_propagation_analyzer.py` usa para su propia tabla de
    simbolos."""
    index: dict[tuple[str, str, str], list[_FactCandidate]] = defaultdict(list)
    for program_propagation in semantic_propagation.programs:
        locations = statement_locations.get(program_propagation.program, {})
        for fact in program_propagation.facts:
            location = locations.get(fact.source_reference.statement_id)
            if location is None:
                continue
            key_variable = fact.target_qualified_name or fact.target_variable
            index[(program_propagation.program, location.paragraph, key_variable)].append(
                (location.ordinal, location.parent_statement_id, fact)
            )
    return index


class _EntryEnvironment:
    """Valores de entrada interprocedural acordados por TODOS los call
    sites elegibles que alimentan un formal/LINKAGE dado de un programa
    (Fase 7, nivel 2 de `_known_literal_at`). Se construye
    incrementalmente en orden topologico: antes de analizar las llamadas
    salientes de un programa, ya estan completos los hechos de entrada
    de TODAS sus llamadas entrantes elegibles (ver
    `analyze_interprocedural_propagation`).

    Cuando dos callers distintos aportan literales DIFERENTES para el
    mismo formal, el valor se suprime de forma PERMANENTE para esa clave
    (nunca union, nunca fixed point) -- pero, a diferencia de versiones
    anteriores de esta fase, NUNCA en silencio: `conflicted_keys()`
    expone exactamente que claves quedaron en conflicto por programa,
    para que `analyze_interprocedural_propagation` emita un diagnostico
    explicito (`MULTIPLE_CALLER_VALUES_FOR_<key_variable>`) en el
    `InterproceduralProgramAnalysis` del programa afectado. La
    trazabilidad de CUALES literales exactos entraron en conflicto (y de
    que caller/`PropagatedValueFact` provino cada uno) sigue disponible
    sin campos nuevos: cada `ENTRY_FACT` individual PROPAGATED conserva
    su propio `literal`/`source_fact_ids`/`caller_program` -- basta
    filtrar `entry_facts` del programa afectado por el mismo
    `formal_name` para reconstruir la discrepancia completa."""

    def __init__(self) -> None:
        self._values: dict[str, dict[str, str]] = {}
        self._conflicted: dict[str, set[str]] = defaultdict(set)

    def observe(self, *, program: str, key_variable: str, literal: str) -> None:
        if key_variable in self._conflicted[program]:
            return
        existing = self._values.setdefault(program, {})
        if key_variable in existing:
            if existing[key_variable] != literal:
                del existing[key_variable]
                self._conflicted[program].add(key_variable)
            return
        existing[key_variable] = literal

    def get(self, *, program: str, key_variable: str) -> str | None:
        return self._values.get(program, {}).get(key_variable)

    def conflicted_keys(self, program: str) -> frozenset[str]:
        return frozenset(self._conflicted.get(program, set()))


def _known_literal_at(
    *,
    program: str,
    paragraph: str,
    parent_scope: str | None,
    before_ordinal: int,
    variable_name: str,
    qualified_name: str | None,
    facts_index: dict[tuple[str, str, str], list[_FactCandidate]],
    entry_environment: _EntryEnvironment,
) -> tuple[str | None, list[str]]:
    key_variable = qualified_name or variable_name
    candidates = facts_index.get((program, paragraph, key_variable), [])
    best: tuple[int, PropagatedValueFact] | None = None
    for ordinal, parent_statement_id, fact in candidates:
        if parent_statement_id != parent_scope:
            continue
        if ordinal >= before_ordinal:
            continue
        if best is None or ordinal > best[0]:
            best = (ordinal, fact)
    if best is not None:
        _, fact = best
        if fact.fact_kind in _LITERAL_FACT_KINDS and fact.literal is not None:
            return fact.literal, [fact.fact_id]
        return None, []
    literal = entry_environment.get(program=program, key_variable=key_variable)
    if literal is not None:
        return literal, []
    return None, []


# ---------------------------------------------------------------------------
# Elegibilidad de call sites (Fase 7): barreras a nivel de call site
# completo, nunca a nivel de argumento individual.
# ---------------------------------------------------------------------------


def _call_site_barrier(
    call_site: InterproceduralCallSite,
) -> InterproceduralPropagationBarrier | None:
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
        # Defensivo, nunca alcanzable en la practica: target_kind=LITERAL
        # ya excluye UNRESOLVED_DYNAMIC (ver Fase 6, _check_target_kind_
        # resolution_coherence), y los otros dos casos ya se cubrieron
        # arriba.
        return InterproceduralPropagationBarrier.DYNAMIC_CALL
    return None


# ---------------------------------------------------------------------------
# Hechos por argumento (direccion caller->callee) y por retorno
# (direccion callee->caller).
# ---------------------------------------------------------------------------


def _entry_fact_id(call_site_id: str, ordinal: int) -> str:
    return f"fact::{call_site_id}::entry::{ordinal}"


def _return_fact_id(call_site_id: str, label: str) -> str:
    return f"fact::{call_site_id}::return::{label}"


def _blocked_fact(
    *,
    fact_id: str,
    call_site: InterproceduralCallSite,
    binding: InterproceduralArgumentBinding,
    barrier: InterproceduralPropagationBarrier,
) -> InterproceduralPropagationFact:
    return InterproceduralPropagationFact(
        fact_id=fact_id,
        call_site_id=call_site.call_site_id,
        caller_program=call_site.caller_program,
        callee_program=call_site.resolved_callee_program,
        caller_statement_id=call_site.statement_id,
        binding_id=binding.binding_id,
        direction=InterproceduralPropagationDirection.CALLER_TO_CALLEE,
        kind=InterproceduralFactKind.ENTRY_FACT,
        status=InterproceduralPropagationStatus.BLOCKED,
        actual_name=binding.actual_name,
        formal_name=binding.formal_name,
        barriers=[barrier],
        diagnostics=[f"BLOCKED_{barrier.value}"],
        source_references=list(binding.source_references),
    )


def _entry_fact_for_binding_at(
    *,
    call_site: InterproceduralCallSite,
    binding: InterproceduralArgumentBinding,
    call_paragraph: str,
    call_parent_scope: str | None,
    call_ordinal: int,
    facts_index: dict[tuple[str, str, str], list[_FactCandidate]],
    entry_environment: _EntryEnvironment,
) -> InterproceduralPropagationFact:
    fact_id = _entry_fact_id(call_site.call_site_id, binding.ordinal)

    if binding.status != ArgumentBindingStatus.RESOLVED_POSITIONAL:
        barrier = _BINDING_STATUS_TO_BARRIER.get(
            binding.status, InterproceduralPropagationBarrier.UNSUPPORTED_CONTROL_FLOW
        )
        return _blocked_fact(fact_id=fact_id, call_site=call_site, binding=binding, barrier=barrier)

    if binding.passing_mode == CallPassingMode.UNKNOWN:
        return _blocked_fact(
            fact_id=fact_id,
            call_site=call_site,
            binding=binding,
            barrier=InterproceduralPropagationBarrier.UNKNOWN_PASSING_MODE,
        )

    literal, source_fact_ids = _known_literal_at(
        program=call_site.caller_program,
        paragraph=call_paragraph,
        parent_scope=call_parent_scope,
        before_ordinal=call_ordinal,
        variable_name=binding.actual_name or "",
        qualified_name=binding.actual_qualified_name,
        facts_index=facts_index,
        entry_environment=entry_environment,
    )

    diagnostics: list[str] = []
    if binding.passing_mode == CallPassingMode.REFERENCE:
        diagnostics.append("ENTRY_BY_REFERENCE_POTENTIALLY_MUTABLE")

    if literal is None:
        return InterproceduralPropagationFact(
            fact_id=fact_id,
            call_site_id=call_site.call_site_id,
            caller_program=call_site.caller_program,
            callee_program=call_site.resolved_callee_program,
            caller_statement_id=call_site.statement_id,
            binding_id=binding.binding_id,
            direction=InterproceduralPropagationDirection.CALLER_TO_CALLEE,
            kind=InterproceduralFactKind.ENTRY_FACT,
            status=InterproceduralPropagationStatus.UNRESOLVED,
            actual_name=binding.actual_name,
            formal_name=binding.formal_name,
            diagnostics=sorted({*diagnostics, "ENTRY_ACTUAL_VALUE_NOT_DETERMINED"}),
            source_references=list(binding.source_references),
        )

    return InterproceduralPropagationFact(
        fact_id=fact_id,
        call_site_id=call_site.call_site_id,
        caller_program=call_site.caller_program,
        callee_program=call_site.resolved_callee_program,
        caller_statement_id=call_site.statement_id,
        binding_id=binding.binding_id,
        direction=InterproceduralPropagationDirection.CALLER_TO_CALLEE,
        kind=InterproceduralFactKind.ENTRY_FACT,
        status=InterproceduralPropagationStatus.PROPAGATED,
        actual_name=binding.actual_name,
        formal_name=binding.formal_name,
        literal=literal,
        source_fact_ids=sorted(set(source_fact_ids)),
        diagnostics=sorted(set(diagnostics)),
        source_references=list(binding.source_references),
    )


def _exit_fact_for_binding(
    *,
    call_site: InterproceduralCallSite,
    binding: InterproceduralArgumentBinding,
    kind: InterproceduralFactKind,
    label: str,
    callee_first_paragraph: str | None,
    callee_statement_count: int,
    callee_exit_barrier: InterproceduralPropagationBarrier | None,
    facts_index: dict[tuple[str, str, str], list[_FactCandidate]],
    entry_environment: _EntryEnvironment,
) -> InterproceduralPropagationFact | None:
    fact_id = _return_fact_id(call_site.call_site_id, label)

    if binding.status != ArgumentBindingStatus.RESOLVED_POSITIONAL:
        return None
    if callee_first_paragraph is None or call_site.resolved_callee_program is None:
        return InterproceduralPropagationFact(
            fact_id=fact_id,
            call_site_id=call_site.call_site_id,
            caller_program=call_site.caller_program,
            callee_program=call_site.resolved_callee_program,
            caller_statement_id=call_site.statement_id,
            binding_id=binding.binding_id,
            direction=InterproceduralPropagationDirection.CALLEE_TO_CALLER,
            kind=InterproceduralFactKind.INVALIDATION,
            status=InterproceduralPropagationStatus.INVALIDATED,
            actual_name=binding.actual_name,
            formal_name=binding.formal_name,
            diagnostics=["RETURN_CALLEE_HAS_NO_PROCEDURE_BODY"],
            source_references=list(binding.source_references),
        )

    if callee_exit_barrier is not None:
        # Certeza estructural (Fase 7b): el callee nunca retorna control
        # normalmente al caller (STOP RUN/UNKNOWN como unico terminador
        # final) -- BLOCKED explicito, nunca INVALIDATED por falta de
        # evidencia (ver _effective_exit_cutoff).
        return InterproceduralPropagationFact(
            fact_id=fact_id,
            call_site_id=call_site.call_site_id,
            caller_program=call_site.caller_program,
            callee_program=call_site.resolved_callee_program,
            caller_statement_id=call_site.statement_id,
            binding_id=binding.binding_id,
            direction=InterproceduralPropagationDirection.CALLEE_TO_CALLER,
            kind=kind,
            status=InterproceduralPropagationStatus.BLOCKED,
            actual_name=binding.actual_name,
            formal_name=binding.formal_name,
            barriers=[callee_exit_barrier],
            diagnostics=[f"BLOCKED_{callee_exit_barrier.value}"],
            source_references=list(binding.source_references),
        )

    literal, source_fact_ids = _known_literal_at(
        program=call_site.resolved_callee_program,
        paragraph=callee_first_paragraph,
        parent_scope=None,
        before_ordinal=callee_statement_count,
        variable_name=binding.formal_name or "",
        qualified_name=binding.linkage_item_qualified_name or binding.formal_qualified_name,
        facts_index=facts_index,
        entry_environment=entry_environment,
    )

    if literal is None:
        return InterproceduralPropagationFact(
            fact_id=fact_id,
            call_site_id=call_site.call_site_id,
            caller_program=call_site.caller_program,
            callee_program=call_site.resolved_callee_program,
            caller_statement_id=call_site.statement_id,
            binding_id=binding.binding_id,
            direction=InterproceduralPropagationDirection.CALLEE_TO_CALLER,
            kind=InterproceduralFactKind.INVALIDATION,
            status=InterproceduralPropagationStatus.INVALIDATED,
            actual_name=binding.actual_name,
            formal_name=binding.formal_name,
            diagnostics=["RETURN_VALUE_NOT_PROVABLY_DETERMINISTIC"],
            source_references=list(binding.source_references),
        )

    return InterproceduralPropagationFact(
        fact_id=fact_id,
        call_site_id=call_site.call_site_id,
        caller_program=call_site.caller_program,
        callee_program=call_site.resolved_callee_program,
        caller_statement_id=call_site.statement_id,
        binding_id=binding.binding_id,
        direction=InterproceduralPropagationDirection.CALLEE_TO_CALLER,
        kind=kind,
        status=InterproceduralPropagationStatus.PROPAGATED,
        actual_name=binding.actual_name,
        formal_name=binding.formal_name,
        literal=literal,
        source_fact_ids=sorted(set(source_fact_ids)),
        source_references=list(binding.source_references),
    )


# ---------------------------------------------------------------------------
# Orden topologico deterministico sobre el call graph directo, excluyendo
# self-loops y aristas dentro de un SCC (Fase 7: sin fixed point sobre
# ciclos).
# ---------------------------------------------------------------------------


def _topological_order(
    programs: Sequence[str], linkage: InterproceduralCallLinkageArtifact
) -> list[str]:
    acyclic_edges = [
        edge for edge in linkage.call_edges if not edge.recursive and not edge.part_of_cycle
    ]
    successors: dict[str, set[str]] = defaultdict(set)
    in_degree: dict[str, int] = {program: 0 for program in programs}
    for edge in acyclic_edges:
        if edge.callee_program not in successors[edge.caller_program]:
            successors[edge.caller_program].add(edge.callee_program)
            in_degree[edge.callee_program] = in_degree.get(edge.callee_program, 0) + 1

    ready = sorted(p for p in programs if in_degree.get(p, 0) == 0)
    order: list[str] = []
    remaining_in_degree = dict(in_degree)
    while ready:
        node = ready.pop(0)
        order.append(node)
        for successor in sorted(successors.get(node, ())):
            remaining_in_degree[successor] -= 1
            if remaining_in_degree[successor] == 0:
                ready.append(successor)
        ready.sort()

    # Defensivo: cualquier programa no alcanzado (no deberia ocurrir para
    # un grafo aciclico bien formado) se agrega al final en orden alfabetico.
    missing = sorted(set(programs) - set(order))
    return order + missing


# ---------------------------------------------------------------------------
# Punto de entrada.
# ---------------------------------------------------------------------------


def _build_summary(
    program_analyses: list[InterproceduralProgramAnalysis],
    facts: list[InterproceduralPropagationFact],
) -> InterproceduralPropagationSummary:
    blocked_call_sites = {
        call_site_id for pa in program_analyses for call_site_id in pa.blocked_call_sites
    }
    eligible_call_sites = {fact.call_site_id for fact in facts}
    propagated_call_sites = {
        fact.call_site_id
        for fact in facts
        if fact.status == InterproceduralPropagationStatus.PROPAGATED
    }

    counts_by_status: dict[InterproceduralPropagationStatus, int] = {}
    counts_by_barrier: dict[InterproceduralPropagationBarrier, int] = {}
    counts_by_kind: dict[InterproceduralFactKind, int] = {}
    for fact in facts:
        counts_by_status[fact.status] = counts_by_status.get(fact.status, 0) + 1
        counts_by_kind[fact.kind] = counts_by_kind.get(fact.kind, 0) + 1
        for barrier in fact.barriers:
            counts_by_barrier[barrier] = counts_by_barrier.get(barrier, 0) + 1

    return InterproceduralPropagationSummary(
        program_count=len(program_analyses),
        call_site_count=len(blocked_call_sites) + len(eligible_call_sites),
        eligible_call_count=len(eligible_call_sites),
        propagated_call_count=len(propagated_call_sites),
        blocked_call_count=len(blocked_call_sites),
        entry_fact_count=counts_by_kind.get(InterproceduralFactKind.ENTRY_FACT, 0),
        returning_fact_count=counts_by_kind.get(InterproceduralFactKind.RETURNING_FACT, 0),
        by_reference_output_count=counts_by_kind.get(
            InterproceduralFactKind.BY_REFERENCE_OUTPUT, 0
        ),
        invalidation_count=counts_by_kind.get(InterproceduralFactKind.INVALIDATION, 0),
        counts_by_status=counts_by_status,
        counts_by_barrier=counts_by_barrier,
    )


def analyze_interprocedural_propagation(
    *,
    canonical_programs: Sequence[CanonicalProgram],
    semantic_effects: SemanticEffectsArtifact,
    semantic_propagation: SemanticPropagationArtifact,
    interprocedural_call_linkage: InterproceduralCallLinkageArtifact,
    run_id: str,
    source_package_hash: str,
    source_artifact_hashes: dict[str, str],
) -> InterproceduralPropagationArtifact:
    """Punto de entrada del analizador puro (Fase 7). Determinista: misma
    entrada siempre produce el mismo `InterproceduralPropagationArtifact`.
    `semantic_effects` se recibe por simetria con el resto de la
    fundacion interprocedural y para registrar su version de procedencia,
    pero esta fase concreta no necesita leer sus efectos directamente
    (`SemanticPropagationArtifact`/`InterproceduralCallLinkageArtifact`
    ya incorporan toda la informacion de `CALL_PROGRAM` que hace falta)."""
    del semantic_effects  # ver docstring: solo se usa su version, no su contenido

    canonical_by_name = {program.program_name: program for program in canonical_programs}
    statement_locations = {
        name: _flatten_statement_locations(program) for name, program in canonical_by_name.items()
    }
    facts_index = _build_facts_index(semantic_propagation, statement_locations)

    call_sites_by_caller: dict[str, list[InterproceduralCallSite]] = defaultdict(list)
    for call_site in interprocedural_call_linkage.call_sites:
        call_sites_by_caller[call_site.caller_program].append(call_site)

    program_names = sorted(canonical_by_name)
    order = _topological_order(program_names, interprocedural_call_linkage)

    entry_environment = _EntryEnvironment()
    entry_facts_by_callee: dict[str, list[InterproceduralPropagationFact]] = defaultdict(list)
    exit_facts_by_callee: dict[str, list[InterproceduralPropagationFact]] = defaultdict(list)
    blocked_by_caller: dict[str, list[str]] = defaultdict(list)
    blocked_barriers_by_caller: dict[str, set[str]] = defaultdict(set)
    all_facts: list[InterproceduralPropagationFact] = []

    for program_name in order:
        for call_site in call_sites_by_caller.get(program_name, []):
            barrier = _call_site_barrier(call_site)
            if barrier is not None:
                blocked_by_caller[program_name].append(call_site.call_site_id)
                blocked_barriers_by_caller[program_name].add(barrier.value)
                continue

            assert call_site.resolved_callee_program is not None
            callee_program = call_site.resolved_callee_program
            call_location = statement_locations.get(program_name, {}).get(call_site.statement_id)
            call_paragraph = (
                call_location.paragraph if call_location else call_site.caller_paragraph
            )
            call_parent_scope = call_location.parent_statement_id if call_location else None
            call_ordinal = call_location.ordinal if call_location else 0

            callee_program_obj = canonical_by_name.get(callee_program)
            callee_first_paragraph = (
                callee_program_obj.paragraphs[0].name
                if callee_program_obj is not None and callee_program_obj.paragraphs
                else None
            )
            callee_statement_count, callee_exit_barrier = (
                _effective_exit_cutoff(callee_program_obj.paragraphs[0].statements)
                if callee_program_obj is not None and callee_program_obj.paragraphs
                else (0, None)
            )

            for binding in call_site.arguments:
                entry_fact = _entry_fact_for_binding_at(
                    call_site=call_site,
                    binding=binding,
                    call_paragraph=call_paragraph,
                    call_parent_scope=call_parent_scope,
                    call_ordinal=call_ordinal,
                    facts_index=facts_index,
                    entry_environment=entry_environment,
                )
                entry_facts_by_callee[callee_program].append(entry_fact)
                all_facts.append(entry_fact)

                if (
                    entry_fact.status == InterproceduralPropagationStatus.PROPAGATED
                    and entry_fact.literal is not None
                ):
                    formal_key = binding.linkage_item_qualified_name or binding.formal_name
                    if formal_key is not None:
                        entry_environment.observe(
                            program=callee_program,
                            key_variable=formal_key,
                            literal=entry_fact.literal,
                        )

                if binding.passing_mode == CallPassingMode.REFERENCE:
                    exit_fact = _exit_fact_for_binding(
                        call_site=call_site,
                        binding=binding,
                        kind=InterproceduralFactKind.BY_REFERENCE_OUTPUT,
                        label=str(binding.ordinal),
                        callee_first_paragraph=callee_first_paragraph,
                        callee_statement_count=callee_statement_count,
                        callee_exit_barrier=callee_exit_barrier,
                        facts_index=facts_index,
                        entry_environment=entry_environment,
                    )
                    if exit_fact is not None:
                        exit_facts_by_callee[callee_program].append(exit_fact)
                        all_facts.append(exit_fact)

            if call_site.returning_binding is not None:
                returning_exit_fact = _exit_fact_for_binding(
                    call_site=call_site,
                    binding=call_site.returning_binding,
                    kind=InterproceduralFactKind.RETURNING_FACT,
                    label="returning",
                    callee_first_paragraph=callee_first_paragraph,
                    callee_statement_count=callee_statement_count,
                    callee_exit_barrier=callee_exit_barrier,
                    facts_index=facts_index,
                    entry_environment=entry_environment,
                )
                if returning_exit_fact is not None:
                    exit_facts_by_callee[callee_program].append(returning_exit_fact)
                    all_facts.append(returning_exit_fact)

    program_analyses = []
    for program_name in program_names:
        diagnostics = sorted(
            {
                f"BLOCKED_CALL_SITES_INCLUDE_{barrier}"
                for barrier in blocked_barriers_by_caller.get(program_name, set())
            }
            | {
                f"MULTIPLE_CALLER_VALUES_FOR_{key_variable}"
                for key_variable in entry_environment.conflicted_keys(program_name)
            }
        )
        program_analyses.append(
            InterproceduralProgramAnalysis(
                program=program_name,
                entry_facts=sorted(
                    entry_facts_by_callee.get(program_name, []), key=lambda f: f.fact_id
                ),
                exit_facts=sorted(
                    exit_facts_by_callee.get(program_name, []), key=lambda f: f.fact_id
                ),
                blocked_call_sites=sorted(set(blocked_by_caller.get(program_name, []))),
                diagnostics=diagnostics,
            )
        )

    facts = sorted(all_facts, key=lambda f: f.fact_id)
    summary = _build_summary(program_analyses, facts)

    return InterproceduralPropagationArtifact(
        run_id=run_id,
        source_package_hash=source_package_hash,
        source_artifact_hashes=dict(source_artifact_hashes),
        interprocedural_analysis_schema_version=interprocedural_call_linkage.schema_version,
        interprocedural_analysis_analyzer_version=interprocedural_call_linkage.analyzer_version,
        semantic_effects_schema_version=interprocedural_call_linkage.semantic_effects_schema_version,
        semantic_propagation_schema_version=semantic_propagation.schema_version,
        summary=summary,
        program_analyses=program_analyses,
        facts=facts,
        diagnostics=[],
    )
