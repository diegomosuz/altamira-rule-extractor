"""Analizador PURO de la fundacion interprocedural CALL/LINKAGE (Fase 6
de la ampliacion semantica, `feat/interprocedural-call-linkage-foundation`).

Recibe unicamente `CanonicalProgram[]` y un `SemanticEffectsArtifact` ya
calculados en memoria y devuelve un `InterproceduralCallLinkageArtifact`.
Nunca:

- lee filesystem (la carga es responsabilidad exclusiva de
  `interprocedural_call_linkage_service.py`);
- accede a Neo4j ni consulta `SemanticGraph`;
- resuelve un programa fuera del paquete (filesystem, classpath, red);
- resuelve un `CALL` dinamico mediante propagacion de valores;
- analiza el cuerpo del programa invocado (nunca cruza de programa);
- expande recursivamente un ciclo (deteccion sobre el grafo ya
  construido, nunca recursion infinita);
- escribe archivos ni modifica los objetos de entrada.

Toda clasificacion de resolucion/binding pasa por funciones puras
dedicadas (Fase 11/12/13), nunca dispersa en el CLI ni en el servicio."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Sequence

from ..contracts.canonical import CanonicalCallArgument, CanonicalEntryParameter, CanonicalProgram
from ..contracts.enums import CallPassingMode, CallTargetKind
from ..contracts.interprocedural_call_linkage import (
    ArgumentBindingStatus,
    InterproceduralAnalysisSummary,
    InterproceduralArgumentBinding,
    InterproceduralCallLinkageArtifact,
    InterproceduralCallSite,
    InterproceduralSourceReference,
    PotentialDataFlow,
    ProgramCallCycle,
    ProgramCallEdge,
    ProgramInterface,
    ProgramInterfaceParameter,
    ProgramResolutionStatus,
    potential_flow_for_passing_mode,
)
from ..contracts.semantic_effects import SemanticEffect, SemanticEffectKind, SemanticEffectsArtifact

# ---------------------------------------------------------------------------
# Fase 5 (releida aqui): interfaz de programa a partir de CanonicalProgram.
# ---------------------------------------------------------------------------


def _interface_parameter_from_entry(
    program_name: str, parameter: CanonicalEntryParameter, ordinal: int
) -> ProgramInterfaceParameter:
    diagnostics: list[str] = []
    if parameter.linkage_item_qualified_name is None:
        diagnostics.append("FORMAL_PARAMETER_NOT_RESOLVED_AGAINST_LINKAGE")
    return ProgramInterfaceParameter(
        ordinal=ordinal,
        formal_name=parameter.name,
        formal_qualified_name=parameter.qualified_name,
        linkage_item_qualified_name=parameter.linkage_item_qualified_name,
        pic=None,
        usage=None,
        source_reference=InterproceduralSourceReference(
            program=program_name,
            paragraph=None,
            statement_id=None,
            source_file=parameter.source_file,
            line=parameter.line,
            location_kind=parameter.location_kind,
        ),
        diagnostics=diagnostics,
    )


def _build_program_interface(program: CanonicalProgram) -> ProgramInterface:
    parameters = [
        _interface_parameter_from_entry(program.program_name, parameter, parameter.ordinal)
        for parameter in program.entry_parameters
    ]
    returning_parameter: ProgramInterfaceParameter | None = None
    if program.entry_returning_data_item is not None:
        linkage_by_qualified = {
            item.qualified_name: item for item in program.linkage_data_items
        }
        linkage_item = linkage_by_qualified.get(program.entry_returning_data_item)
        returning_diagnostics = (
            [] if linkage_item is not None else ["RETURNING_ITEM_NOT_RESOLVED_AGAINST_LINKAGE"]
        )
        returning_parameter = ProgramInterfaceParameter(
            ordinal=1,
            formal_name=program.entry_returning_data_item,
            formal_qualified_name=program.entry_returning_data_item,
            linkage_item_qualified_name=(
                linkage_item.qualified_name if linkage_item is not None else None
            ),
            pic=linkage_item.pic if linkage_item is not None else None,
            usage=linkage_item.usage if linkage_item is not None else None,
            source_reference=InterproceduralSourceReference(program=program.program_name),
            diagnostics=returning_diagnostics,
        )

    interface_diagnostics: list[str] = []
    if not program.entry_parameters and program.linkage_data_items:
        interface_diagnostics.append("LINKAGE_ITEMS_WITHOUT_ANY_FORMAL_PARAMETER")
    return ProgramInterface(
        program=program.program_name,
        parameters=parameters,
        returning_parameter=returning_parameter,
        linkage_item_count=len(program.linkage_data_items),
        diagnostics=sorted(set(interface_diagnostics)),
    )


# ---------------------------------------------------------------------------
# Fase 11: resolucion de programas.
# ---------------------------------------------------------------------------


def _resolve_program(
    *, target_kind: CallTargetKind, called_program_name: str | None,
    programs_by_name: dict[str, list[CanonicalProgram]],
) -> tuple[ProgramResolutionStatus, str | None]:
    if target_kind != CallTargetKind.LITERAL or called_program_name is None:
        return ProgramResolutionStatus.UNRESOLVED_DYNAMIC, None
    matches = programs_by_name.get(called_program_name, [])
    if len(matches) == 1:
        return ProgramResolutionStatus.RESOLVED_INTERNAL, matches[0].program_name
    if len(matches) == 0:
        return ProgramResolutionStatus.UNRESOLVED_MISSING_PROGRAM, None
    return ProgramResolutionStatus.AMBIGUOUS_PROGRAM, None


# ---------------------------------------------------------------------------
# Fase 12: binding actual-formal (posicional, nunca por semejanza de nombres).
# ---------------------------------------------------------------------------


def _actual_shape_unresolved(argument: CanonicalCallArgument) -> bool:
    return (
        not argument.omitted
        and argument.passing_mode == CallPassingMode.UNKNOWN
        and argument.data_item_name is None
        and argument.literal is None
    )


def _actual_is_ambiguous(argument: CanonicalCallArgument, caller_program: CanonicalProgram) -> bool:
    if argument.qualified_data_item_name is not None or argument.data_item_name is None:
        return False
    matches = sum(1 for item in caller_program.data_items if item.name == argument.data_item_name)
    matches += sum(
        1 for item in caller_program.linkage_data_items if item.name == argument.data_item_name
    )
    return matches > 1


def _binding_id(call_site_id: str, *, ordinal: int | None) -> str:
    suffix = "returning" if ordinal is None else str(ordinal)
    return f"binding::{call_site_id}::{suffix}"


def _bind_argument(
    *, call_site_id: str, ordinal: int, argument: CanonicalCallArgument,
    caller_program: CanonicalProgram, formal: ProgramInterfaceParameter | None,
    callee_resolved: bool, caller_paragraph: str, statement_id: str,
) -> InterproceduralArgumentBinding:
    potential_flow = potential_flow_for_passing_mode(argument.passing_mode)
    actual_source = InterproceduralSourceReference(
        program=caller_program.program_name, paragraph=caller_paragraph,
        statement_id=statement_id, source_file=argument.source_file, line=argument.line,
        location_kind=argument.location_kind,
    )
    diagnostics: list[str] = []

    if _actual_shape_unresolved(argument):
        status = ArgumentBindingStatus.ACTUAL_UNRESOLVED
        diagnostics.append("CALL_ARGUMENT_SHAPE_UNRESOLVED")
        return InterproceduralArgumentBinding(
            binding_id=_binding_id(call_site_id, ordinal=ordinal), ordinal=ordinal, status=status,
            passing_mode=argument.passing_mode, potential_flow=potential_flow,
            diagnostics=sorted(set(diagnostics)), source_references=[actual_source],
        )

    if _actual_is_ambiguous(argument, caller_program):
        diagnostics.append("CALL_ACTUAL_ARGUMENT_NAME_AMBIGUOUS")
        return InterproceduralArgumentBinding(
            binding_id=_binding_id(call_site_id, ordinal=ordinal), ordinal=ordinal,
            status=ArgumentBindingStatus.AMBIGUOUS_ACTUAL, passing_mode=argument.passing_mode,
            potential_flow=potential_flow, actual_name=argument.data_item_name,
            diagnostics=sorted(set(diagnostics)), source_references=[actual_source],
        )

    actual_kwargs = {
        "actual_name": argument.data_item_name,
        "actual_qualified_name": argument.qualified_data_item_name,
        "actual_literal": argument.literal,
    }

    if not callee_resolved or formal is None:
        diagnostics.append(
            "CALL_CALLEE_PROGRAM_NOT_RESOLVED"
            if not callee_resolved
            else "CALL_NO_MATCHING_FORMAL_PARAMETER"
        )
        return InterproceduralArgumentBinding(
            binding_id=_binding_id(call_site_id, ordinal=ordinal), ordinal=ordinal,
            status=ArgumentBindingStatus.FORMAL_UNRESOLVED, passing_mode=argument.passing_mode,
            potential_flow=potential_flow, diagnostics=sorted(set(diagnostics)),
            source_references=[actual_source], **actual_kwargs,
        )

    formal_source = formal.source_reference
    source_refs = [actual_source, formal_source]
    if formal.linkage_item_qualified_name is None:
        diagnostics.append("CALL_FORMAL_PARAMETER_NOT_RESOLVED_AGAINST_LINKAGE")
        return InterproceduralArgumentBinding(
            binding_id=_binding_id(call_site_id, ordinal=ordinal), ordinal=ordinal,
            status=ArgumentBindingStatus.FORMAL_UNRESOLVED, passing_mode=argument.passing_mode,
            potential_flow=potential_flow, formal_name=formal.formal_name,
            formal_qualified_name=formal.formal_qualified_name,
            diagnostics=sorted(set(diagnostics)),
            source_references=sorted(source_refs, key=_source_reference_sort_key),
            **actual_kwargs,
        )

    return InterproceduralArgumentBinding(
        binding_id=_binding_id(call_site_id, ordinal=ordinal), ordinal=ordinal,
        status=ArgumentBindingStatus.RESOLVED_POSITIONAL, passing_mode=argument.passing_mode,
        potential_flow=potential_flow, formal_name=formal.formal_name,
        formal_qualified_name=formal.formal_qualified_name,
        linkage_item_qualified_name=formal.linkage_item_qualified_name,
        diagnostics=sorted(set(diagnostics)),
        source_references=sorted(source_refs, key=_source_reference_sort_key),
        **actual_kwargs,
    )


def _source_reference_sort_key(reference: InterproceduralSourceReference) -> tuple[str, str, str]:
    return (reference.program, reference.paragraph or "", reference.statement_id or "")


def _extra_actual_binding(
    call_site_id: str, ordinal: int, argument: CanonicalCallArgument,
    caller_program: CanonicalProgram, caller_paragraph: str, statement_id: str,
) -> InterproceduralArgumentBinding:
    return InterproceduralArgumentBinding(
        binding_id=_binding_id(call_site_id, ordinal=ordinal), ordinal=ordinal,
        status=ArgumentBindingStatus.EXTRA_ACTUAL, passing_mode=argument.passing_mode,
        potential_flow=potential_flow_for_passing_mode(argument.passing_mode),
        actual_name=argument.data_item_name,
        actual_qualified_name=argument.qualified_data_item_name,
        actual_literal=argument.literal, diagnostics=["CALL_MORE_ACTUALS_THAN_FORMALS"],
        source_references=[
            InterproceduralSourceReference(
                program=caller_program.program_name, paragraph=caller_paragraph,
                statement_id=statement_id, source_file=argument.source_file, line=argument.line,
                location_kind=argument.location_kind,
            )
        ],
    )


def _missing_actual_binding(
    call_site_id: str, ordinal: int, formal: ProgramInterfaceParameter,
) -> InterproceduralArgumentBinding:
    return InterproceduralArgumentBinding(
        binding_id=_binding_id(call_site_id, ordinal=ordinal), ordinal=ordinal,
        status=ArgumentBindingStatus.MISSING_ACTUAL, passing_mode=CallPassingMode.UNKNOWN,
        potential_flow=potential_flow_for_passing_mode(CallPassingMode.UNKNOWN),
        formal_name=formal.formal_name, formal_qualified_name=formal.formal_qualified_name,
        linkage_item_qualified_name=formal.linkage_item_qualified_name,
        diagnostics=["CALL_FEWER_ACTUALS_THAN_FORMALS"],
        source_references=[formal.source_reference],
    )


def _build_argument_bindings(
    *, call_site_id: str, arguments: Sequence[CanonicalCallArgument],
    caller_program: CanonicalProgram, caller_paragraph: str, statement_id: str,
    callee_interface: ProgramInterface | None, callee_resolved: bool,
) -> list[InterproceduralArgumentBinding]:
    formals = callee_interface.parameters if callee_interface is not None else []
    bindings: list[InterproceduralArgumentBinding] = []
    for index, argument in enumerate(arguments):
        ordinal = index + 1
        if index < len(formals):
            formal: ProgramInterfaceParameter | None = formals[index]
            bindings.append(
                _bind_argument(
                    call_site_id=call_site_id, ordinal=ordinal, argument=argument,
                    caller_program=caller_program, formal=formal, callee_resolved=callee_resolved,
                    caller_paragraph=caller_paragraph, statement_id=statement_id,
                )
            )
        elif callee_resolved:
            bindings.append(
                _extra_actual_binding(
                    call_site_id, ordinal, argument, caller_program, caller_paragraph, statement_id
                )
            )
        else:
            bindings.append(
                _bind_argument(
                    call_site_id=call_site_id, ordinal=ordinal, argument=argument,
                    caller_program=caller_program, formal=None, callee_resolved=callee_resolved,
                    caller_paragraph=caller_paragraph, statement_id=statement_id,
                )
            )
    if callee_resolved:
        for index in range(len(arguments), len(formals)):
            bindings.append(_missing_actual_binding(call_site_id, index + 1, formals[index]))
    return bindings


def _build_returning_binding(
    *, call_site_id: str, call_returning_data_item: str | None,
    callee_interface: ProgramInterface | None, caller_program: CanonicalProgram,
    caller_paragraph: str, statement_id: str,
) -> InterproceduralArgumentBinding | None:
    if call_returning_data_item is None:
        return None
    formal = callee_interface.returning_parameter if callee_interface is not None else None
    actual_source = InterproceduralSourceReference(
        program=caller_program.program_name, paragraph=caller_paragraph, statement_id=statement_id,
    )
    if formal is None:
        return InterproceduralArgumentBinding(
            binding_id=_binding_id(call_site_id, ordinal=None), ordinal=1,
            status=ArgumentBindingStatus.FORMAL_UNRESOLVED, passing_mode=CallPassingMode.UNKNOWN,
            potential_flow=PotentialDataFlow.OUTPUT_ONLY,
            actual_name=call_returning_data_item,
            diagnostics=["CALL_RETURNING_FORMAL_NOT_RESOLVED"],
            source_references=[actual_source],
        )
    return InterproceduralArgumentBinding(
        binding_id=_binding_id(call_site_id, ordinal=None), ordinal=1,
        status=ArgumentBindingStatus.RESOLVED_POSITIONAL, passing_mode=CallPassingMode.UNKNOWN,
        potential_flow=PotentialDataFlow.OUTPUT_ONLY,
        actual_name=call_returning_data_item, formal_name=formal.formal_name,
        formal_qualified_name=formal.formal_qualified_name,
        linkage_item_qualified_name=formal.linkage_item_qualified_name,
        source_references=sorted(
            [actual_source, formal.source_reference], key=_source_reference_sort_key
        ),
    )


# ---------------------------------------------------------------------------
# Fase 6/11/12: UN InterproceduralCallSite por SemanticEffect(kind=CALL_PROGRAM).
# ---------------------------------------------------------------------------


def _call_site_id(program: str, paragraph: str, statement_id: str) -> str:
    return f"callsite::{program}::{paragraph}::{statement_id}"


def _build_call_site(
    effect: SemanticEffect, *, canonical_programs_by_name: dict[str, CanonicalProgram],
    programs_by_name_for_resolution: dict[str, list[CanonicalProgram]],
    interfaces_by_name: dict[str, ProgramInterface],
) -> InterproceduralCallSite:
    source_reference = effect.source_reference
    program_name = source_reference.program
    paragraph_name = source_reference.paragraph
    statement_id = source_reference.statement_id
    call_site_id = _call_site_id(program_name, paragraph_name, statement_id)

    target_kind = effect.call_target_kind
    if target_kind is None:
        # Defensivo, nunca alcanzable: SemanticEffect.kind=CALL_PROGRAM
        # exige call_target_kind (contracts/semantic_effects.py).
        target_kind = CallTargetKind.UNKNOWN

    if target_kind == CallTargetKind.LITERAL:
        declared_target = effect.called_program_name or "<UNRESOLVED_TARGET>"
    elif target_kind == CallTargetKind.DYNAMIC:
        declared_target = effect.called_program_expression or "<UNRESOLVED_TARGET>"
    else:
        declared_target = "<UNRESOLVED_TARGET>"

    resolution_status, resolved_callee = _resolve_program(
        target_kind=target_kind, called_program_name=effect.called_program_name,
        programs_by_name=programs_by_name_for_resolution,
    )
    callee_resolved = resolution_status == ProgramResolutionStatus.RESOLVED_INTERNAL
    callee_interface = interfaces_by_name.get(resolved_callee or "") if callee_resolved else None
    caller_program = canonical_programs_by_name[program_name]

    arguments = _build_argument_bindings(
        call_site_id=call_site_id, arguments=effect.call_arguments,
        caller_program=caller_program, caller_paragraph=paragraph_name,
        statement_id=statement_id, callee_interface=callee_interface,
        callee_resolved=callee_resolved,
    )
    returning_binding = _build_returning_binding(
        call_site_id=call_site_id, call_returning_data_item=effect.call_returning_data_item,
        callee_interface=callee_interface, caller_program=caller_program,
        caller_paragraph=paragraph_name, statement_id=statement_id,
    )

    recursive = callee_resolved and resolved_callee == program_name

    diagnostics: list[str] = []
    if resolution_status == ProgramResolutionStatus.UNRESOLVED_DYNAMIC:
        diagnostics.append("CALL_DYNAMIC_TARGET_UNRESOLVED")
    elif resolution_status == ProgramResolutionStatus.UNRESOLVED_MISSING_PROGRAM:
        diagnostics.append("CALL_TARGET_PROGRAM_NOT_IN_PACKAGE")
    elif resolution_status == ProgramResolutionStatus.AMBIGUOUS_PROGRAM:
        diagnostics.append("CALL_TARGET_PROGRAM_NAME_AMBIGUOUS_IN_PACKAGE")

    return InterproceduralCallSite(
        call_site_id=call_site_id, caller_program=program_name, caller_paragraph=paragraph_name,
        statement_id=statement_id, target_kind=target_kind, declared_target=declared_target,
        resolution_status=resolution_status, resolved_callee_program=resolved_callee,
        arguments=arguments, returning_binding=returning_binding, recursive=recursive,
        part_of_cycle=False, support_status=effect.support_status,
        diagnostics=sorted(set(diagnostics)),
        source_reference=InterproceduralSourceReference(
            program=program_name, paragraph=paragraph_name, statement_id=statement_id,
            source_file=source_reference.source_file, line=source_reference.line_start,
            location_kind=source_reference.location_kind,
        ),
    )


# ---------------------------------------------------------------------------
# Fase 13: call graph directo (solo RESOLVED_INTERNAL) + ciclos.
# ---------------------------------------------------------------------------


def _edge_id(caller: str, callee: str) -> str:
    return f"edge::{caller}::{callee}"


def _build_call_edges(call_sites: Sequence[InterproceduralCallSite]) -> list[ProgramCallEdge]:
    site_ids_by_pair: dict[tuple[str, str], list[str]] = defaultdict(list)
    for call_site in call_sites:
        if call_site.resolution_status != ProgramResolutionStatus.RESOLVED_INTERNAL:
            continue
        callee = call_site.resolved_callee_program
        if callee is None:
            continue
        site_ids_by_pair[(call_site.caller_program, callee)].append(call_site.call_site_id)

    edges: list[ProgramCallEdge] = []
    for (caller, callee), site_ids in site_ids_by_pair.items():
        edges.append(
            ProgramCallEdge(
                edge_id=_edge_id(caller, callee), caller_program=caller, callee_program=callee,
                call_site_ids=sorted(set(site_ids)), recursive=(caller == callee),
                part_of_cycle=False,
            )
        )
    return sorted(edges, key=lambda edge: edge.edge_id)


def _strongly_connected_components(
    programs: Sequence[str], edges: Sequence[ProgramCallEdge]
) -> list[list[str]]:
    """Tarjan, iterativo (nunca recursion sin cota -- un ciclo real en el
    call graph produciria RecursionError con una implementacion
    recursiva sobre paquetes grandes). Determinista: los programas y sus
    adyacencias se recorren en orden alfabetico estable."""
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        adjacency[edge.caller_program].append(edge.callee_program)
    for callees in adjacency.values():
        callees.sort()

    index_counter = [0]
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    result: list[list[str]] = []

    def strongconnect(start: str) -> None:
        work: list[tuple[str, int]] = [(start, 0)]
        call_stack: list[str] = []
        while work:
            node, child_index = work[-1]
            if child_index == 0:
                indices[node] = index_counter[0]
                lowlink[node] = index_counter[0]
                index_counter[0] += 1
                stack.append(node)
                on_stack.add(node)
                call_stack.append(node)

            recursed = False
            neighbors = adjacency.get(node, [])
            index = child_index
            while index < len(neighbors):
                neighbor = neighbors[index]
                if neighbor not in indices:
                    work[-1] = (node, index + 1)
                    work.append((neighbor, 0))
                    recursed = True
                    break
                if neighbor in on_stack:
                    lowlink[node] = min(lowlink[node], indices[neighbor])
                index += 1
            if recursed:
                continue

            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])

            if lowlink[node] == indices[node]:
                component: list[str] = []
                while True:
                    top = stack.pop()
                    on_stack.discard(top)
                    component.append(top)
                    if top == node:
                        break
                result.append(sorted(component))

    for program in sorted(programs):
        if program not in indices:
            strongconnect(program)
    return result


def _cycle_id(programs: Sequence[str]) -> str:
    digest = hashlib.sha256("\x1f".join(sorted(programs)).encode("utf-8")).hexdigest()[:16]
    return f"cycle::{digest}"


def _detect_cycles(
    programs: Sequence[str], edges: Sequence[ProgramCallEdge],
) -> tuple[list[ProgramCallCycle], set[str]]:
    """UN `ProgramCallCycle` por componente fuertemente conexa (SCC) de
    tamano >= 2 -- nunca una enumeracion de ciclos elementales (ver
    docstring de `ProgramCallCycle`). `edge_ids` incluye deliberadamente
    cualquier self-loop de un programa miembro de la SCC (la condicion
    es "ambos extremos pertenecen a la SCC", no "arista distinta de
    ambos programas del ciclo minimo"): `programs_in_cycle` (y por lo
    tanto `part_of_cycle` en call sites/edges, ver `_mark_part_of_cycle`)
    se marca por la misma regla de membresia."""
    components = _strongly_connected_components(programs, edges)
    cycles: list[ProgramCallCycle] = []
    programs_in_cycle: set[str] = set()
    edge_by_pair = {(edge.caller_program, edge.callee_program): edge for edge in edges}

    for component in components:
        if len(component) < 2:
            continue
        component_set = set(component)
        edge_ids = sorted(
            {
                edge.edge_id
                for (caller, callee), edge in edge_by_pair.items()
                if caller in component_set and callee in component_set
            }
        )
        cycles.append(
            ProgramCallCycle(
                cycle_id=_cycle_id(component), programs=sorted(component), edge_ids=edge_ids
            )
        )
        programs_in_cycle.update(component)
    return sorted(cycles, key=lambda cycle: cycle.cycle_id), programs_in_cycle


def _mark_part_of_cycle(
    call_sites: Sequence[InterproceduralCallSite], edges: Sequence[ProgramCallEdge],
    programs_in_cycle: set[str],
) -> tuple[list[InterproceduralCallSite], list[ProgramCallEdge]]:
    edge_in_cycle = {
        (edge.caller_program, edge.callee_program): (
            edge.caller_program in programs_in_cycle and edge.callee_program in programs_in_cycle
        )
        for edge in edges
    }
    updated_edges = [
        edge.model_copy(
            update={
                "part_of_cycle": edge_in_cycle.get(
                    (edge.caller_program, edge.callee_program), False
                )
            }
        )
        for edge in edges
    ]
    updated_sites = [
        call_site.model_copy(
            update={
                "part_of_cycle": (
                    call_site.resolved_callee_program is not None
                    and edge_in_cycle.get(
                        (call_site.caller_program, call_site.resolved_callee_program), False
                    )
                )
            }
        )
        for call_site in call_sites
    ]
    return updated_sites, updated_edges


# ---------------------------------------------------------------------------
# Resumen + punto de entrada.
# ---------------------------------------------------------------------------


def _build_summary(
    canonical_programs: Sequence[CanonicalProgram], interfaces: Sequence[ProgramInterface],
    call_sites: Sequence[InterproceduralCallSite], cycles: Sequence[ProgramCallCycle],
) -> InterproceduralAnalysisSummary:
    counts_by_resolution: dict[ProgramResolutionStatus, int] = {}
    recursive_count = 0
    for call_site in call_sites:
        counts_by_resolution[call_site.resolution_status] = (
            counts_by_resolution.get(call_site.resolution_status, 0) + 1
        )
        if call_site.recursive:
            recursive_count += 1

    all_bindings = [
        binding
        for call_site in call_sites
        for binding in (
            [*call_site.arguments, call_site.returning_binding]
            if call_site.returning_binding is not None
            else call_site.arguments
        )
    ]
    counts_by_binding: dict[ArgumentBindingStatus, int] = {}
    for binding in all_bindings:
        counts_by_binding[binding.status] = counts_by_binding.get(binding.status, 0) + 1

    return InterproceduralAnalysisSummary(
        program_count=len(canonical_programs),
        interface_count=len(interfaces),
        call_site_count=len(call_sites),
        resolved_internal_count=counts_by_resolution.get(
            ProgramResolutionStatus.RESOLVED_INTERNAL, 0
        ),
        dynamic_count=counts_by_resolution.get(ProgramResolutionStatus.UNRESOLVED_DYNAMIC, 0),
        missing_program_count=counts_by_resolution.get(
            ProgramResolutionStatus.UNRESOLVED_MISSING_PROGRAM, 0
        ),
        ambiguous_program_count=counts_by_resolution.get(
            ProgramResolutionStatus.AMBIGUOUS_PROGRAM, 0
        ),
        recursive_call_count=recursive_count,
        cycle_count=len(cycles),
        binding_count=len(all_bindings),
        resolved_binding_count=counts_by_binding.get(ArgumentBindingStatus.RESOLVED_POSITIONAL, 0),
        unresolved_binding_count=len(all_bindings)
        - counts_by_binding.get(ArgumentBindingStatus.RESOLVED_POSITIONAL, 0),
        counts_by_resolution_status=counts_by_resolution,
        counts_by_binding_status=counts_by_binding,
    )


def analyze_interprocedural_call_linkage(
    *, canonical_programs: Sequence[CanonicalProgram], semantic_effects: SemanticEffectsArtifact,
    run_id: str, source_package_hash: str, source_artifact_hashes: dict[str, str],
) -> InterproceduralCallLinkageArtifact:
    """Punto de entrada del analizador puro (Fase 6, fundacion
    interprocedural). Determinista: misma entrada siempre produce el
    mismo `InterproceduralCallLinkageArtifact` (orden de
    `interfaces`/`call_sites`/`call_edges`/`cycles` siempre normalizado
    antes de construir el modelo)."""
    canonical_programs_by_name = {
        program.program_name: program for program in canonical_programs
    }
    programs_by_name_for_resolution: dict[str, list[CanonicalProgram]] = defaultdict(list)
    for program in canonical_programs:
        programs_by_name_for_resolution[program.program_name].append(program)

    interfaces = sorted(
        (_build_program_interface(program) for program in canonical_programs),
        key=lambda interface: interface.program,
    )
    interfaces_by_name = {interface.program: interface for interface in interfaces}

    call_effects = [
        effect
        for program_effects in semantic_effects.programs
        for effect in program_effects.effects
        if effect.kind == SemanticEffectKind.CALL_PROGRAM
    ]
    call_sites = sorted(
        (
            _build_call_site(
                effect, canonical_programs_by_name=canonical_programs_by_name,
                programs_by_name_for_resolution=dict(programs_by_name_for_resolution),
                interfaces_by_name=interfaces_by_name,
            )
            for effect in call_effects
        ),
        key=lambda call_site: call_site.call_site_id,
    )

    call_edges = _build_call_edges(call_sites)
    cycles, programs_in_cycle = _detect_cycles(list(canonical_programs_by_name), call_edges)
    call_sites, call_edges = _mark_part_of_cycle(call_sites, call_edges, programs_in_cycle)

    summary = _build_summary(canonical_programs, interfaces, call_sites, cycles)

    return InterproceduralCallLinkageArtifact(
        canonical_schema_versions=sorted(
            {program.schema_version for program in canonical_programs}
        ),
        semantic_effects_schema_version=semantic_effects.schema_version,
        semantic_effects_analyzer_version=semantic_effects.analyzer_version,
        run_id=run_id, source_package_hash=source_package_hash,
        source_artifact_hashes=dict(source_artifact_hashes), summary=summary, interfaces=interfaces,
        call_sites=call_sites, call_edges=call_edges, cycles=cycles,
    )
