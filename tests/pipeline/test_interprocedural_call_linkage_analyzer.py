"""Tests del analizador PURO de la fundacion interprocedural CALL/LINKAGE
(Fase 6 de la ampliacion semantica, `pipeline/interprocedural_call_linkage_analyzer.py`).

Formaliza como pytest los 25 escenarios prototipados durante el
desarrollo (resolucion de programa, binding actual-formal, potential
flow, recursion/ciclos, determinismo) -- ver
docs/INTERPROCEDURAL_CALL_LINKAGE.md. Construye `CanonicalProgram`
directamente (nunca via ProLeap: eso ya lo cubre
`InterproceduralCallLinkageExtractionTest.java`) y usa el analizador
real de `SemanticEffectsArtifact` como entrada -- nunca un
`SemanticEffect` fabricado a mano, para no divergir del contrato real
entre analizadores."""

from __future__ import annotations

import copy

from altamira_extractor.contracts.canonical import (
    CanonicalCallArgument,
    CanonicalDataItem,
    CanonicalEntryParameter,
    CanonicalLinkageDataItem,
    CanonicalParagraph,
    CanonicalProgram,
    CanonicalStatement,
)
from altamira_extractor.contracts.enums import (
    BranchKind,
    CallPassingMode,
    CallTargetKind,
    LocationKind,
    SourceFormat,
    StatementKind,
)
from altamira_extractor.contracts.interprocedural_call_linkage import (
    ArgumentBindingStatus,
    InterproceduralCallLinkageArtifact,
    PotentialDataFlow,
    ProgramResolutionStatus,
)
from altamira_extractor.contracts.semantic_effects import SemanticEffectsArtifact
from altamira_extractor.pipeline.interprocedural_call_linkage_analyzer import (
    _resolve_program,
    analyze_interprocedural_call_linkage,
)
from altamira_extractor.pipeline.semantic_effects_analyzer import analyze_semantic_effects

HASH = "a" * 64


def make_call_statement(**overrides: object) -> CanonicalStatement:
    fields: dict[str, object] = {
        "statement_id": "CALLER::MAIN::0::CALL",
        "kind": StatementKind.CALL,
        "source_text": "CALL",
        "source_file": "01-codigo/cobol/CALLER.cbl",
        "line_start": 10,
        "line_end": 10,
        "location_kind": LocationKind.EXACT,
        "call_target_kind": CallTargetKind.LITERAL,
        "called_program_name": "CALLEE",
    }
    fields.update(overrides)
    return CanonicalStatement(**fields)  # type: ignore[arg-type]


def make_paragraph(
    name: str = "MAIN", statements: list[CanonicalStatement] | None = None
) -> CanonicalParagraph:
    stmts = statements if statements is not None else [make_call_statement()]
    return CanonicalParagraph(
        name=name,
        source_text=f"{name}.",
        location_kind=LocationKind.UNKNOWN,
        statements=stmts,
    )


def make_program(
    name: str,
    *,
    paragraphs: list[CanonicalParagraph] | None = None,
    data_items: list[CanonicalDataItem] | None = None,
    linkage_data_items: list[CanonicalLinkageDataItem] | None = None,
    entry_parameters: list[CanonicalEntryParameter] | None = None,
    entry_returning_data_item: str | None = None,
) -> CanonicalProgram:
    return CanonicalProgram(
        schema_version="1.2",
        program_name=name,
        source_file=f"01-codigo/cobol/{name}.cbl",
        source_hash=HASH,
        source_package_hash=HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        data_items=data_items or [],
        paragraphs=paragraphs if paragraphs is not None else [make_paragraph()],
        linkage_data_items=linkage_data_items or [],
        entry_parameters=entry_parameters or [],
        entry_returning_data_item=entry_returning_data_item,
    )


def make_call_argument(**overrides: object) -> CanonicalCallArgument:
    fields: dict[str, object] = {
        "ordinal": 1,
        "expression": "WS-INPUT",
        "data_item_name": "WS-INPUT",
        "qualified_data_item_name": "WS-INPUT",
        "passing_mode": CallPassingMode.REFERENCE,
        "location_kind": LocationKind.UNKNOWN,
    }
    fields.update(overrides)
    return CanonicalCallArgument(**fields)  # type: ignore[arg-type]


def make_data_item(name: str) -> CanonicalDataItem:
    return CanonicalDataItem(
        name=name, qualified_name=name, level=1, location_kind=LocationKind.UNKNOWN
    )


def make_linkage_item(name: str, **overrides: object) -> CanonicalLinkageDataItem:
    fields: dict[str, object] = {
        "name": name,
        "qualified_name": name,
        "level": 1,
        "pic": "X(10)",
        "location_kind": LocationKind.UNKNOWN,
    }
    fields.update(overrides)
    return CanonicalLinkageDataItem(**fields)  # type: ignore[arg-type]


def make_entry_parameter(
    name: str, ordinal: int = 1, **overrides: object
) -> CanonicalEntryParameter:
    fields: dict[str, object] = {
        "ordinal": ordinal,
        "name": name,
        "qualified_name": name,
        "linkage_item_qualified_name": name,
        "passing_mode": CallPassingMode.REFERENCE,
        "location_kind": LocationKind.UNKNOWN,
    }
    fields.update(overrides)
    return CanonicalEntryParameter(**fields)  # type: ignore[arg-type]


def analyze(
    programs: list[CanonicalProgram], *, run_id: str = "run1"
) -> tuple[InterproceduralCallLinkageArtifact, SemanticEffectsArtifact]:
    effects = analyze_semantic_effects(
        canonical_programs=programs,
        run_id=run_id,
        source_package_hash=HASH,
        source_artifact_hashes={"artifacts/02-canonical": HASH},
    )
    artifact = analyze_interprocedural_call_linkage(
        canonical_programs=programs,
        semantic_effects=effects,
        run_id=run_id,
        source_package_hash=HASH,
        source_artifact_hashes={"artifacts/02-canonical": HASH},
    )
    return artifact, effects


# --- 1. Resolucion de programa (Fase 11) --------------------------------------


def test_literal_call_resolves_internally_when_callee_present() -> None:
    caller = make_program("CALLER")
    callee = make_program("CALLEE", paragraphs=[])
    artifact, _ = analyze([caller, callee])
    assert len(artifact.call_sites) == 1
    call_site = artifact.call_sites[0]
    assert call_site.resolution_status == ProgramResolutionStatus.RESOLVED_INTERNAL
    assert call_site.resolved_callee_program == "CALLEE"
    assert artifact.summary.resolved_internal_count == 1


def test_literal_call_to_missing_program_is_unresolved() -> None:
    caller = make_program(
        "CALLER",
        paragraphs=[make_paragraph(statements=[make_call_statement(called_program_name="ABSENT")])],
    )
    artifact, _ = analyze([caller])
    call_site = artifact.call_sites[0]
    assert call_site.resolution_status == ProgramResolutionStatus.UNRESOLVED_MISSING_PROGRAM
    assert call_site.resolved_callee_program is None
    assert artifact.summary.missing_program_count == 1


def test_dynamic_call_target_is_never_resolved() -> None:
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-PROGRAM-NAME")],
        paragraphs=[
            make_paragraph(
                statements=[
                    make_call_statement(
                        call_target_kind=CallTargetKind.DYNAMIC,
                        called_program_name=None,
                        called_program_expression="WS-PROGRAM-NAME",
                    )
                ]
            )
        ],
    )
    artifact, _ = analyze([caller])
    call_site = artifact.call_sites[0]
    assert call_site.target_kind == CallTargetKind.DYNAMIC
    assert call_site.resolution_status == ProgramResolutionStatus.UNRESOLVED_DYNAMIC
    assert call_site.resolved_callee_program is None
    assert artifact.summary.dynamic_count == 1


def test_resolve_program_reports_ambiguous_when_two_programs_share_a_name() -> None:
    """Dos CanonicalProgram con el mismo program_name -- caso patologico
    (dos versiones del mismo PROGRAM-ID en el mismo paquete). Se prueba
    `_resolve_program` (Fase 11) directamente: `SemanticEffectsArtifact`
    ya exige `program_name` unico entre `canonical_programs` (ver
    `_check_programs_unique`), asi que este caso nunca llega end-to-end
    a traves de `analyze_semantic_effects` -> `analyze_interprocedural_
    call_linkage` con los `CanonicalProgram` completos -- la deteccion de
    ambiguedad en si misma se prueba de forma aislada, nunca eligiendo
    arbitrariamente entre los candidatos."""
    base_callee = make_program("CALLEE", paragraphs=[])
    callee_1 = base_callee.model_copy(update={"source_file": "01-codigo/cobol/CALLEE_V1.cbl"})
    callee_2 = base_callee.model_copy(update={"source_file": "01-codigo/cobol/CALLEE_V2.cbl"})
    status, resolved = _resolve_program(
        target_kind=CallTargetKind.LITERAL,
        called_program_name="CALLEE",
        programs_by_name={"CALLEE": [callee_1, callee_2]},
    )
    assert status == ProgramResolutionStatus.AMBIGUOUS_PROGRAM
    assert resolved is None


# --- 2. Bindings actual-formal (Fase 12) --------------------------------------


def test_call_with_zero_arguments_produces_no_bindings() -> None:
    caller = make_program(
        "CALLER", paragraphs=[make_paragraph(statements=[make_call_statement(call_arguments=[])])]
    )
    callee = make_program("CALLEE", paragraphs=[])
    artifact, _ = analyze([caller, callee])
    assert artifact.call_sites[0].arguments == []
    assert artifact.summary.binding_count == 0


def test_call_with_one_to_one_binding_resolves_positionally() -> None:
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-INPUT")],
        paragraphs=[
            make_paragraph(statements=[make_call_statement(call_arguments=[make_call_argument()])])
        ],
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-INPUT")],
        entry_parameters=[make_entry_parameter("LK-INPUT")],
        paragraphs=[],
    )
    artifact, _ = analyze([caller, callee])
    binding = artifact.call_sites[0].arguments[0]
    assert binding.status == ArgumentBindingStatus.RESOLVED_POSITIONAL
    assert binding.formal_name == "LK-INPUT"
    assert binding.actual_name == "WS-INPUT"


def test_call_with_multiple_bindings_preserves_ordinal_order() -> None:
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-A"), make_data_item("WS-B"), make_data_item("WS-C")],
        paragraphs=[
            make_paragraph(
                statements=[
                    make_call_statement(
                        call_arguments=[
                            make_call_argument(
                                ordinal=1,
                                expression="WS-A",
                                data_item_name="WS-A",
                                qualified_data_item_name="WS-A",
                            ),
                            make_call_argument(
                                ordinal=2,
                                expression="WS-B",
                                data_item_name="WS-B",
                                qualified_data_item_name="WS-B",
                            ),
                            make_call_argument(
                                ordinal=3,
                                expression="WS-C",
                                data_item_name="WS-C",
                                qualified_data_item_name="WS-C",
                            ),
                        ]
                    )
                ]
            )
        ],
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[
            make_linkage_item("LK-A"),
            make_linkage_item("LK-B"),
            make_linkage_item("LK-C"),
        ],
        entry_parameters=[
            make_entry_parameter("LK-A", ordinal=1),
            make_entry_parameter("LK-B", ordinal=2),
            make_entry_parameter("LK-C", ordinal=3),
        ],
        paragraphs=[],
    )
    artifact, _ = analyze([caller, callee])
    bindings = artifact.call_sites[0].arguments
    assert [b.ordinal for b in bindings] == [1, 2, 3]
    assert [b.formal_name for b in bindings] == ["LK-A", "LK-B", "LK-C"]


def test_call_with_fewer_actuals_than_formals_reports_missing_actual() -> None:
    caller = make_program(
        "CALLER", paragraphs=[make_paragraph(statements=[make_call_statement(call_arguments=[])])]
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-INPUT")],
        entry_parameters=[make_entry_parameter("LK-INPUT")],
        paragraphs=[],
    )
    artifact, _ = analyze([caller, callee])
    bindings = artifact.call_sites[0].arguments
    assert len(bindings) == 1
    assert bindings[0].status == ArgumentBindingStatus.MISSING_ACTUAL
    assert bindings[0].formal_name == "LK-INPUT"


def test_call_with_more_actuals_than_formals_reports_extra_actual() -> None:
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-A"), make_data_item("WS-B")],
        paragraphs=[
            make_paragraph(
                statements=[
                    make_call_statement(
                        call_arguments=[
                            make_call_argument(
                                ordinal=1,
                                expression="WS-A",
                                data_item_name="WS-A",
                                qualified_data_item_name="WS-A",
                            ),
                            make_call_argument(
                                ordinal=2,
                                expression="WS-B",
                                data_item_name="WS-B",
                                qualified_data_item_name="WS-B",
                            ),
                        ]
                    )
                ]
            )
        ],
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-A")],
        entry_parameters=[make_entry_parameter("LK-A", ordinal=1)],
        paragraphs=[],
    )
    artifact, _ = analyze([caller, callee])
    bindings = artifact.call_sites[0].arguments
    assert bindings[0].status == ArgumentBindingStatus.RESOLVED_POSITIONAL
    assert bindings[1].status == ArgumentBindingStatus.EXTRA_ACTUAL
    assert bindings[1].formal_name is None


def test_formal_not_resolved_against_linkage_yields_formal_unresolved_binding() -> None:
    """entry_parameters()[i].linkage_item_qualified_name=None (homonimo
    ambiguo o LINKAGE item ausente): el binding nunca inventa una
    definicion, queda FORMAL_UNRESOLVED."""
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-INPUT")],
        paragraphs=[
            make_paragraph(statements=[make_call_statement(call_arguments=[make_call_argument()])])
        ],
    )
    callee = make_program(
        "CALLEE",
        entry_parameters=[
            make_entry_parameter(
                "LK-UNRESOLVED", linkage_item_qualified_name=None, passing_mode=None
            )
        ],
        paragraphs=[],
    )
    artifact, _ = analyze([caller, callee])
    binding = artifact.call_sites[0].arguments[0]
    assert binding.status == ArgumentBindingStatus.FORMAL_UNRESOLVED
    assert "CALL_FORMAL_PARAMETER_NOT_RESOLVED_AGAINST_LINKAGE" in binding.diagnostics


def test_ambiguous_actual_name_in_caller_is_not_bound() -> None:
    """Dos data items homonimos en el caller (WORKING-STORAGE +
    LINKAGE) sin qualified_data_item_name: el analizador nunca adivina
    cual referencia el argumento."""
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-DUP")],
        linkage_data_items=[make_linkage_item("WS-DUP")],
        paragraphs=[
            make_paragraph(
                statements=[
                    make_call_statement(
                        call_arguments=[
                            make_call_argument(
                                expression="WS-DUP",
                                data_item_name="WS-DUP",
                                qualified_data_item_name=None,
                            )
                        ]
                    )
                ]
            )
        ],
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-INPUT")],
        entry_parameters=[make_entry_parameter("LK-INPUT")],
        paragraphs=[],
    )
    artifact, _ = analyze([caller, callee])
    binding = artifact.call_sites[0].arguments[0]
    assert binding.status == ArgumentBindingStatus.AMBIGUOUS_ACTUAL


# --- 3. Potential data flow (Fase 10/12) --------------------------------------


def test_by_reference_argument_has_input_output_potential_flow() -> None:
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-INPUT")],
        paragraphs=[
            make_paragraph(
                statements=[
                    make_call_statement(
                        call_arguments=[make_call_argument(passing_mode=CallPassingMode.REFERENCE)]
                    )
                ]
            )
        ],
    )
    artifact, _ = analyze([caller])
    binding = artifact.call_sites[0].arguments[0]
    assert binding.potential_flow == PotentialDataFlow.INPUT_OUTPUT


def test_by_content_argument_has_input_only_potential_flow() -> None:
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-INPUT")],
        paragraphs=[
            make_paragraph(
                statements=[
                    make_call_statement(
                        call_arguments=[make_call_argument(passing_mode=CallPassingMode.CONTENT)]
                    )
                ]
            )
        ],
    )
    artifact, _ = analyze([caller])
    assert artifact.call_sites[0].arguments[0].potential_flow == PotentialDataFlow.INPUT_ONLY


def test_by_value_argument_has_input_only_potential_flow() -> None:
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-INPUT")],
        paragraphs=[
            make_paragraph(
                statements=[
                    make_call_statement(
                        call_arguments=[make_call_argument(passing_mode=CallPassingMode.VALUE)]
                    )
                ]
            )
        ],
    )
    artifact, _ = analyze([caller])
    assert artifact.call_sites[0].arguments[0].potential_flow == PotentialDataFlow.INPUT_ONLY


def test_returning_binding_has_output_only_potential_flow() -> None:
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-RESULT")],
        paragraphs=[
            make_paragraph(
                statements=[
                    make_call_statement(call_arguments=[], call_returning_data_item="WS-RESULT")
                ]
            )
        ],
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-RESULT")],
        entry_returning_data_item="LK-RESULT",
        paragraphs=[],
    )
    artifact, _ = analyze([caller, callee])
    call_site = artifact.call_sites[0]
    assert call_site.returning_binding is not None
    assert call_site.returning_binding.potential_flow == PotentialDataFlow.OUTPUT_ONLY
    assert call_site.returning_binding.status == ArgumentBindingStatus.RESOLVED_POSITIONAL
    assert call_site.returning_binding.formal_name == "LK-RESULT"


# --- 4. LINKAGE SECTION / interfaz de programa (Fase 5) -----------------------


def test_linkage_item_never_used_as_formal_is_still_preserved_in_interface() -> None:
    """Fase 5 regla 7: linkage_item_count cuenta TODOS los items de
    LINKAGE, incluidos los que nunca aparecen en PROCEDURE DIVISION
    USING -- nunca se descartan."""
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-USED"), make_linkage_item("LK-UNUSED")],
        entry_parameters=[make_entry_parameter("LK-USED")],
        paragraphs=[],
    )
    artifact, _ = analyze([callee])
    interface = artifact.interfaces[0]
    assert interface.linkage_item_count == 2
    assert len(interface.parameters) == 1


def test_program_without_linkage_has_empty_interface() -> None:
    callee = make_program("CALLEE", paragraphs=[])
    artifact, _ = analyze([callee])
    interface = artifact.interfaces[0]
    assert interface.parameters == []
    assert interface.linkage_item_count == 0
    assert interface.returning_parameter is None


# --- 5. CALL dentro de estructuras de control ---------------------------------


def test_call_inside_if_branch_is_still_captured_as_call_site() -> None:
    if_stmt = CanonicalStatement(
        statement_id="CALLER::MAIN::0::IF",
        kind=StatementKind.IF,
        source_text="IF WS-FLAG",
        location_kind=LocationKind.UNKNOWN,
        expression="WS-FLAG",
    )
    call_in_then = make_call_statement(
        statement_id="CALLER::MAIN::1::CALL",
        parent_statement_id="CALLER::MAIN::0::IF",
        branch_kind=BranchKind.THEN,
    )
    caller = make_program("CALLER", paragraphs=[make_paragraph(statements=[if_stmt, call_in_then])])
    callee = make_program("CALLEE", paragraphs=[])
    artifact, _ = analyze([caller, callee])
    assert len(artifact.call_sites) == 1
    assert artifact.call_sites[0].statement_id == "CALLER::MAIN::1::CALL"


def test_call_inside_evaluate_when_branch_is_still_captured_as_call_site() -> None:
    evaluate_stmt = CanonicalStatement(
        statement_id="CALLER::MAIN::0::EVALUATE",
        kind=StatementKind.EVALUATE,
        source_text="EVALUATE WS-FLAG",
        location_kind=LocationKind.UNKNOWN,
    )
    call_in_when = make_call_statement(
        statement_id="CALLER::MAIN::1::CALL",
        parent_statement_id="CALLER::MAIN::0::EVALUATE",
        branch_kind=BranchKind.WHEN,
    )
    caller = make_program(
        "CALLER", paragraphs=[make_paragraph(statements=[evaluate_stmt, call_in_when])]
    )
    callee = make_program("CALLEE", paragraphs=[])
    artifact, _ = analyze([caller, callee])
    assert len(artifact.call_sites) == 1
    assert artifact.call_sites[0].statement_id == "CALLER::MAIN::1::CALL"


# --- 6. Recursion y ciclos (Fase 13) ------------------------------------------


def test_self_call_is_marked_recursive_and_is_not_a_cycle() -> None:
    caller = make_program(
        "SELFPROG",
        paragraphs=[
            make_paragraph(statements=[make_call_statement(called_program_name="SELFPROG")])
        ],
    )
    artifact, _ = analyze([caller])
    call_site = artifact.call_sites[0]
    assert call_site.recursive is True
    assert call_site.part_of_cycle is False
    assert artifact.summary.recursive_call_count == 1
    assert artifact.cycles == []
    assert artifact.call_edges[0].recursive is True


def test_two_program_cycle_is_detected() -> None:
    prog_a = make_program(
        "PROGA",
        paragraphs=[make_paragraph(statements=[make_call_statement(called_program_name="PROGB")])],
    )
    prog_b = make_program(
        "PROGB",
        paragraphs=[make_paragraph(statements=[make_call_statement(called_program_name="PROGA")])],
    )
    artifact, _ = analyze([prog_a, prog_b])
    assert len(artifact.cycles) == 1
    assert artifact.cycles[0].programs == ["PROGA", "PROGB"]
    assert all(call_site.part_of_cycle for call_site in artifact.call_sites)
    assert all(edge.part_of_cycle for edge in artifact.call_edges)
    assert artifact.summary.cycle_count == 1


def test_three_program_cycle_is_detected() -> None:
    prog_a = make_program(
        "PROGA",
        paragraphs=[make_paragraph(statements=[make_call_statement(called_program_name="PROGB")])],
    )
    prog_b = make_program(
        "PROGB",
        paragraphs=[make_paragraph(statements=[make_call_statement(called_program_name="PROGC")])],
    )
    prog_c = make_program(
        "PROGC",
        paragraphs=[make_paragraph(statements=[make_call_statement(called_program_name="PROGA")])],
    )
    artifact, _ = analyze([prog_a, prog_b, prog_c])
    assert len(artifact.cycles) == 1
    assert artifact.cycles[0].programs == ["PROGA", "PROGB", "PROGC"]
    assert artifact.summary.cycle_count == 1


def test_multiple_calls_to_same_program_aggregate_into_one_edge() -> None:
    caller = make_program(
        "CALLER",
        paragraphs=[
            make_paragraph(
                statements=[
                    make_call_statement(statement_id="CALLER::MAIN::0::CALL"),
                    make_call_statement(statement_id="CALLER::MAIN::1::CALL"),
                ]
            )
        ],
    )
    callee = make_program("CALLEE", paragraphs=[])
    artifact, _ = analyze([caller, callee])
    assert len(artifact.call_sites) == 2
    assert len(artifact.call_edges) == 1
    assert len(artifact.call_edges[0].call_site_ids) == 2


def test_multiple_isolated_programs_without_calls_produce_no_call_sites() -> None:
    programs = [make_program(name, paragraphs=[]) for name in ("ALPHA", "BETA", "GAMMA")]
    artifact, _ = analyze(programs)
    assert artifact.call_sites == []
    assert artifact.call_edges == []
    assert artifact.cycles == []
    assert artifact.summary.program_count == 3
    assert artifact.summary.interface_count == 3


# ---------------------------------------------------------------------------
# 6.1 Auditoria de release engineering (post-Fase-6): semantica exacta de
# `cycles`/`ProgramCallCycle`. El modelo implementa componentes fuertemente
# conexas (SCC, Tarjan) del call graph directo, filtradas a tamano >= 2 --
# NO una enumeracion de ciclos elementales/concretos. `edge_ids` de un
# `ProgramCallCycle` es "todas las aristas cuyo caller Y callee pertenecen
# a la SCC", nunca una base minima de ciclo: un self-loop de un programa
# miembro de la SCC queda incluido aunque no participe del ciclo de 2+
# programas en si. Ver docs/INTERPROCEDURAL_CALL_LINKAGE.md, seccion
# "Recursion y ciclos", para la semantica documentada de forma inequivoca.
# Estos tests NO cambian el algoritmo: fijan (regression-lock) el
# comportamiento real, auditado y ahora documentado.
# ---------------------------------------------------------------------------


def test_self_call_combined_with_two_program_cycle_marks_self_edge_part_of_cycle() -> None:
    """PROGA se llama a si mismo Y participa de un ciclo PROGA<->PROGB: el
    self-loop de PROGA queda `part_of_cycle=True` (PROGA es miembro de la
    SCC) aunque el self-loop en si no sea uno de los dos edges cruzados
    que forman el ciclo de 2 programas -- exactamente la ambiguedad
    auditada en el informe de cierre (edge_ids incluye el self-loop)."""
    prog_a = make_program(
        "PROGA",
        paragraphs=[
            make_paragraph(
                statements=[
                    make_call_statement(
                        statement_id="PROGA::MAIN::0::CALL", called_program_name="PROGB"
                    ),
                    make_call_statement(
                        statement_id="PROGA::MAIN::1::CALL", called_program_name="PROGA"
                    ),
                ]
            )
        ],
    )
    prog_b = make_program(
        "PROGB",
        paragraphs=[make_paragraph(statements=[make_call_statement(called_program_name="PROGA")])],
    )
    artifact, _ = analyze([prog_a, prog_b])

    assert len(artifact.cycles) == 1
    cycle = artifact.cycles[0]
    assert cycle.programs == ["PROGA", "PROGB"]

    self_edge = next(e for e in artifact.call_edges if e.caller_program == e.callee_program)
    cross_edges = [e for e in artifact.call_edges if e.caller_program != e.callee_program]
    assert self_edge.recursive is True
    assert len(cross_edges) == 2

    # Semantica real (SCC), no un "ciclo minimo": el self-loop de un
    # miembro de la SCC queda dentro de edge_ids del ciclo de 2 programas,
    # junto a los 2 edges cruzados reales.
    assert set(cycle.edge_ids) == {self_edge.edge_id, *[e.edge_id for e in cross_edges]}
    assert self_edge.edge_id in cycle.edge_ids

    # part_of_cycle tambien se propaga al self-edge/self-call-site, por la
    # misma razon estructural (PROGA in programs_in_cycle) -- no porque el
    # self-loop sea "parte" conceptual del ciclo de 2 programas.
    assert self_edge.part_of_cycle is True
    self_call_site = next(cs for cs in artifact.call_sites if cs.recursive)
    assert self_call_site.part_of_cycle is True

    # La recursion directa sigue siendo reconstruible sin ambiguedad de
    # forma independiente de `cycles`: basta con `recursive=True`.
    assert artifact.summary.recursive_call_count == 1
    assert artifact.summary.cycle_count == 1


def test_scc_with_extra_chord_edge_produces_one_cycle_object_not_all_combinations() -> None:
    """SCC de 3 programas (A->B->C->A) mas una arista adicional B->A que
    NO forma parte del ciclo elemental minimo A->B->C->A: el modelo
    produce UN unico `ProgramCallCycle` (la SCC completa, con las 4
    aristas), nunca una enumeracion de los multiples ciclos elementales
    que esa SCC contiene en teoria de grafos (A->B->C->A y, via la arista
    extra, tambien A->B->A). Confirma que `cycles` es SCC, no una base de
    ciclos."""
    prog_a = make_program(
        "PROGA",
        paragraphs=[make_paragraph(statements=[make_call_statement(called_program_name="PROGB")])],
    )
    prog_b = make_program(
        "PROGB",
        paragraphs=[
            make_paragraph(
                statements=[
                    make_call_statement(
                        statement_id="PROGB::MAIN::0::CALL", called_program_name="PROGC"
                    ),
                    make_call_statement(
                        statement_id="PROGB::MAIN::1::CALL", called_program_name="PROGA"
                    ),
                ]
            )
        ],
    )
    prog_c = make_program(
        "PROGC",
        paragraphs=[make_paragraph(statements=[make_call_statement(called_program_name="PROGA")])],
    )
    artifact, _ = analyze([prog_a, prog_b, prog_c])

    assert len(artifact.cycles) == 1
    cycle = artifact.cycles[0]
    assert cycle.programs == ["PROGA", "PROGB", "PROGC"]
    assert len(cycle.edge_ids) == 4
    assert len(artifact.call_edges) == 4
    assert artifact.summary.cycle_count == 1


def test_two_disconnected_cycles_produce_two_separate_cycle_objects() -> None:
    """Dos ciclos de 2 programas sin ninguna arista entre ambos pares: dos
    `ProgramCallCycle` independientes, cada uno con su propia SCC/edge_ids
    -- nunca fusionados en uno solo ni cruzados entre si."""
    prog_a = make_program(
        "PROGA",
        paragraphs=[make_paragraph(statements=[make_call_statement(called_program_name="PROGB")])],
    )
    prog_b = make_program(
        "PROGB",
        paragraphs=[make_paragraph(statements=[make_call_statement(called_program_name="PROGA")])],
    )
    prog_c = make_program(
        "PROGC",
        paragraphs=[make_paragraph(statements=[make_call_statement(called_program_name="PROGD")])],
    )
    prog_d = make_program(
        "PROGD",
        paragraphs=[make_paragraph(statements=[make_call_statement(called_program_name="PROGC")])],
    )
    artifact, _ = analyze([prog_a, prog_b, prog_c, prog_d])

    assert len(artifact.cycles) == 2
    program_sets = [set(cycle.programs) for cycle in artifact.cycles]
    assert {"PROGA", "PROGB"} in program_sets
    assert {"PROGC", "PROGD"} in program_sets
    assert artifact.summary.cycle_count == 2
    for cycle in artifact.cycles:
        assert len(cycle.edge_ids) == 2


def test_cycle_order_is_deterministic_regardless_of_input_program_order() -> None:
    def build() -> list[CanonicalProgram]:
        prog_a = make_program(
            "PROGA",
            paragraphs=[
                make_paragraph(statements=[make_call_statement(called_program_name="PROGB")])
            ],
        )
        prog_b = make_program(
            "PROGB",
            paragraphs=[
                make_paragraph(statements=[make_call_statement(called_program_name="PROGA")])
            ],
        )
        prog_c = make_program(
            "PROGC",
            paragraphs=[
                make_paragraph(statements=[make_call_statement(called_program_name="PROGD")])
            ],
        )
        prog_d = make_program(
            "PROGD",
            paragraphs=[
                make_paragraph(statements=[make_call_statement(called_program_name="PROGC")])
            ],
        )
        return [prog_a, prog_b, prog_c, prog_d]

    forward, _ = analyze(build())
    programs = build()
    backward, _ = analyze([programs[3], programs[1], programs[2], programs[0]])

    assert [c.cycle_id for c in forward.cycles] == [c.cycle_id for c in backward.cycles]
    assert forward.to_stable_json() == backward.to_stable_json()


# --- 7. Determinismo, orden de entrada, no mutacion ---------------------------


def test_call_site_and_edge_and_cycle_ids_are_stable_across_runs() -> None:
    prog_a = make_program(
        "PROGA",
        paragraphs=[make_paragraph(statements=[make_call_statement(called_program_name="PROGB")])],
    )
    prog_b = make_program(
        "PROGB",
        paragraphs=[make_paragraph(statements=[make_call_statement(called_program_name="PROGA")])],
    )
    artifact_1, _ = analyze([prog_a, prog_b])
    artifact_2, _ = analyze([prog_a, prog_b])
    assert [c.call_site_id for c in artifact_1.call_sites] == [
        c.call_site_id for c in artifact_2.call_sites
    ]
    assert [e.edge_id for e in artifact_1.call_edges] == [e.edge_id for e in artifact_2.call_edges]
    assert [c.cycle_id for c in artifact_1.cycles] == [c.cycle_id for c in artifact_2.cycles]


def test_analysis_is_byte_for_byte_deterministic() -> None:
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-INPUT")],
        paragraphs=[
            make_paragraph(statements=[make_call_statement(call_arguments=[make_call_argument()])])
        ],
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-INPUT")],
        entry_parameters=[make_entry_parameter("LK-INPUT")],
        paragraphs=[],
    )
    artifact_1, _ = analyze([caller, callee])
    artifact_2, _ = analyze([caller, callee])
    assert artifact_1.to_stable_json() == artifact_2.to_stable_json()


def test_analysis_is_independent_of_input_program_order() -> None:
    caller = make_program(
        "CALLER",
        paragraphs=[make_paragraph(statements=[make_call_statement(called_program_name="CALLEE")])],
    )
    callee = make_program("CALLEE", paragraphs=[])
    forward, _ = analyze([caller, callee])
    backward, _ = analyze([callee, caller])
    assert forward.to_stable_json() == backward.to_stable_json()


def test_analysis_never_mutates_input_canonical_programs() -> None:
    caller = make_program(
        "CALLER",
        paragraphs=[make_paragraph(statements=[make_call_statement(called_program_name="CALLEE")])],
    )
    callee = make_program("CALLEE", paragraphs=[])
    caller_before = copy.deepcopy(caller)
    callee_before = copy.deepcopy(callee)
    analyze([caller, callee])
    assert caller == caller_before
    assert callee == callee_before


def test_dynamic_call_target_never_resolved_even_when_matching_program_name_exists() -> None:
    """Ausencia de propagacion interprograma (Fase 9/11): aunque exista
    un programa cuyo nombre coincida con el valor textual mas probable
    del identificador dinamico, el analizador NUNCA lo resuelve --
    UNRESOLVED_DYNAMIC es terminal para CallTargetKind.DYNAMIC."""
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-PROGRAM-NAME")],
        paragraphs=[
            make_paragraph(
                statements=[
                    make_call_statement(
                        call_target_kind=CallTargetKind.DYNAMIC,
                        called_program_name=None,
                        called_program_expression="WS-PROGRAM-NAME",
                    )
                ]
            )
        ],
    )
    # Un programa que casualmente se llama igual que el identificador
    # dinamico -- irrelevante, nunca se usa para resolver.
    decoy = make_program("WS-PROGRAM-NAME", paragraphs=[])
    artifact, _ = analyze([caller, decoy])
    call_site = artifact.call_sites[0]
    assert call_site.resolution_status == ProgramResolutionStatus.UNRESOLVED_DYNAMIC
    assert call_site.resolved_callee_program is None
