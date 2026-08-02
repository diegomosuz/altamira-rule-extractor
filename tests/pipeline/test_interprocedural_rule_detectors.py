"""Tests de los detectores PUROS de reglas interprocedurales en shadow
mode (Fase 8 de la ampliacion semantica,
`feat/interprocedural-rule-detectors-shadow`):
`pipeline/interprocedural_rule_detectors.py`. Encadena los analizadores
reales de Fase 2/4/6/7 (ver `interprocedural_rule_helpers.py`) como
entrada de los detectores bajo prueba -- nunca fabrica un
`InterproceduralCallLinkageArtifact`/`InterproceduralPropagationArtifact`
a mano. Cubre los items 1-25 de la lista de 40 tests obligatorios de
Fase 8 (ver docs/INTERPROCEDURAL_RULE_DETECTORS_SHADOW.md)."""

from __future__ import annotations

from altamira_extractor.contracts.enums import (
    CallPassingMode,
    CallTargetKind,
    ProgramTerminationKind,
    StatementKind,
)
from altamira_extractor.contracts.interprocedural_call_linkage import (
    InterproceduralAnalysisSummary,
    InterproceduralCallLinkageArtifact,
    InterproceduralCallSite,
    InterproceduralSourceReference,
    ProgramInterface,
    ProgramResolutionStatus,
)
from altamira_extractor.contracts.interprocedural_propagation import (
    InterproceduralFactKind,
    InterproceduralPropagationBarrier,
)
from altamira_extractor.contracts.interprocedural_rule_candidates import (
    InterproceduralCandidateSupport,
    InterproceduralRuleType,
)
from altamira_extractor.contracts.semantic_coverage import SemanticSupportStatus
from altamira_extractor.pipeline.interprocedural_propagation_analyzer import (
    analyze_interprocedural_propagation,
)
from altamira_extractor.pipeline.interprocedural_rule_detectors import (
    build_detector_context,
    detect_by_reference_rule,
    detect_return_code_rule,
    detect_state_transition_rule,
)
from altamira_extractor.pipeline.semantic_effects_analyzer import analyze_semantic_effects
from altamira_extractor.pipeline.semantic_propagation_analyzer import analyze_semantic_propagation

from .interprocedural_rule_helpers import (
    HASH,
    HASHES,
    analyze_all,
    build_ctx,
    make_call,
    make_call_arg,
    make_data_item,
    make_entry_param,
    make_linkage_item,
    make_move,
    make_paragraph,
    make_program,
    make_semantic_enrichment,
    make_stmt,
    make_terminator,
)

# ---------------------------------------------------------------------------
# 1-3. RETURN_CODE_RULE
# ---------------------------------------------------------------------------


def _returning_pair(callee_paragraphs=None):
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-R")],
        paragraphs=[
            make_paragraph(
                "MAIN", [make_call("CALLER::MAIN::0::CALL", call_returning_data_item="WS-R")]
            )
        ],
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-R")],
        entry_returning_data_item="LK-R",
        paragraphs=callee_paragraphs
        if callee_paragraphs is not None
        else [
            make_paragraph(
                "MAIN", [make_move("CALLEE::MAIN::0::MOVE", target="LK-R", literal="0009")]
            )
        ],
    )
    return caller, callee


def test_01_deterministic_returning_generates_candidate() -> None:
    caller, callee = _returning_pair()
    ctx = build_ctx([caller, callee])
    candidates = detect_return_code_rule(ctx)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.rule_type == InterproceduralRuleType.RETURN_CODE_RULE
    assert candidate.support == InterproceduralCandidateSupport.DETERMINISTIC
    assert candidate.output_literal == "0009"
    assert candidate.caller_program == "CALLER"
    assert candidate.callee_program == "CALLEE"
    assert candidate.target == "WS-R"


def test_02_unknown_returning_generates_no_candidate() -> None:
    """El callee modifica LK-R via COMPUTE (nunca evaluado
    aritmeticamente): SemanticPropagation invalida el hecho -- ningun
    candidato se genera, ni DETERMINISTIC ni BLOCKED."""
    caller, _ = _returning_pair()
    compute_stmt = make_stmt(
        statement_id="CALLEE::MAIN::0::COMPUTE",
        kind=StatementKind.COMPUTE,
        source_text="COMPUTE LK-R = LK-R + 1",
        expression="LK-R + 1",
        target_data_items=["LK-R"],
        variables_written=["LK-R"],
        variables_read=["LK-R"],
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-R")],
        entry_returning_data_item="LK-R",
        paragraphs=[make_paragraph("MAIN", [compute_stmt])],
    )
    ctx = build_ctx([caller, callee])
    assert detect_return_code_rule(ctx) == []


def test_03_returning_blocked_by_stop_run_generates_blocked_candidate() -> None:
    stop_run = make_terminator(
        "CALLEE::MAIN::1::PROGRAM_TERMINATION", ProgramTerminationKind.STOP_RUN
    )
    caller, callee = _returning_pair(
        callee_paragraphs=[
            make_paragraph(
                "MAIN",
                [make_move("CALLEE::MAIN::0::MOVE", target="LK-R", literal="0009"), stop_run],
            )
        ]
    )
    ctx = build_ctx([caller, callee])
    candidates = detect_return_code_rule(ctx)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.support == InterproceduralCandidateSupport.BLOCKED
    assert candidate.output_literal is None
    assert candidate.barriers == [InterproceduralPropagationBarrier.NON_RETURNING_TERMINATION]


# ---------------------------------------------------------------------------
# 4-8. BY_REFERENCE_RULE
# ---------------------------------------------------------------------------


def _by_reference_pair(
    *, callee_paragraphs=None, mode: CallPassingMode = CallPassingMode.REFERENCE
):
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-A")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_move("CALLER::MAIN::0::MOVE", target="WS-A", literal="0005"),
                    make_call(
                        "CALLER::MAIN::1::CALL",
                        call_arguments=[make_call_arg("WS-A", mode)],
                    ),
                ],
            )
        ],
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-A")],
        entry_parameters=[make_entry_param("LK-A")],
        paragraphs=callee_paragraphs,
    )
    return caller, callee


def test_04_by_reference_deterministic_change_generates_candidate() -> None:
    caller, callee = _by_reference_pair(
        callee_paragraphs=[
            make_paragraph(
                "MAIN", [make_move("CALLEE::MAIN::0::MOVE", target="LK-A", literal="9999")]
            )
        ]
    )
    ctx = build_ctx([caller, callee])
    candidates = detect_by_reference_rule(ctx)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.rule_type == InterproceduralRuleType.BY_REFERENCE_RULE
    assert candidate.support == InterproceduralCandidateSupport.DETERMINISTIC
    assert candidate.input_literal == "0005"
    assert candidate.output_literal == "9999"
    assert candidate.target == "WS-A"


def test_05_by_reference_no_demonstrable_change_generates_no_candidate() -> None:
    """El actual nunca recibe un valor de entrada conocido (ningun MOVE
    previo a la llamada) y el callee nunca lo escribe en el scope raiz:
    sin un antes ni un despues demostrable, InterproceduralPropagation
    invalida el hecho de salida -- el detector nunca genera candidato."""
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-A")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_call(
                        "CALLER::MAIN::0::CALL",
                        call_arguments=[make_call_arg("WS-A", CallPassingMode.REFERENCE)],
                    )
                ],
            )
        ],
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-A")],
        entry_parameters=[make_entry_param("LK-A")],
        paragraphs=[make_paragraph("MAIN", [])],
    )
    ctx = build_ctx([caller, callee])
    assert detect_by_reference_rule(ctx) == []


def test_06_by_reference_invalidated_generates_no_candidate() -> None:
    compute_stmt = make_stmt(
        statement_id="CALLEE::MAIN::0::COMPUTE",
        kind=StatementKind.COMPUTE,
        source_text="COMPUTE LK-A = LK-A + 1",
        expression="LK-A + 1",
        target_data_items=["LK-A"],
        variables_written=["LK-A"],
        variables_read=["LK-A"],
    )
    caller, callee = _by_reference_pair(callee_paragraphs=[make_paragraph("MAIN", [compute_stmt])])
    ctx = build_ctx([caller, callee])
    assert detect_by_reference_rule(ctx) == []


def test_07_by_content_never_generates_output() -> None:
    caller, callee = _by_reference_pair(
        callee_paragraphs=[
            make_paragraph(
                "MAIN", [make_move("CALLEE::MAIN::0::MOVE", target="LK-A", literal="9999")]
            )
        ],
        mode=CallPassingMode.CONTENT,
    )
    ctx = build_ctx([caller, callee])
    assert detect_by_reference_rule(ctx) == []


def test_08_by_value_never_generates_output() -> None:
    caller, callee = _by_reference_pair(
        callee_paragraphs=[
            make_paragraph(
                "MAIN", [make_move("CALLEE::MAIN::0::MOVE", target="LK-A", literal="9999")]
            )
        ],
        mode=CallPassingMode.VALUE,
    )
    ctx = build_ctx([caller, callee])
    assert detect_by_reference_rule(ctx) == []


def test_by_reference_blocked_by_stop_run_generates_blocked_candidate() -> None:
    stop_run = make_terminator(
        "CALLEE::MAIN::1::PROGRAM_TERMINATION", ProgramTerminationKind.STOP_RUN
    )
    caller, callee = _by_reference_pair(
        callee_paragraphs=[
            make_paragraph(
                "MAIN",
                [make_move("CALLEE::MAIN::0::MOVE", target="LK-A", literal="9999"), stop_run],
            )
        ]
    )
    ctx = build_ctx([caller, callee])
    candidates = detect_by_reference_rule(ctx)
    assert len(candidates) == 1
    assert candidates[0].support == InterproceduralCandidateSupport.BLOCKED
    assert candidates[0].barriers == [InterproceduralPropagationBarrier.NON_RETURNING_TERMINATION]


# ---------------------------------------------------------------------------
# 9-10. STATE_TRANSITION_RULE
# ---------------------------------------------------------------------------


def _status_pair():
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-STATUS")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_move("CALLER::MAIN::0::MOVE", target="WS-STATUS", literal="PENDING"),
                    make_call(
                        "CALLER::MAIN::1::CALL",
                        call_arguments=[make_call_arg("WS-STATUS", CallPassingMode.REFERENCE)],
                    ),
                ],
            )
        ],
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-STATUS")],
        entry_parameters=[make_entry_param("LK-STATUS")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [make_move("CALLEE::MAIN::0::MOVE", target="LK-STATUS", literal="APPROVED")],
            )
        ],
    )
    return caller, callee


def test_09_deterministic_state_transition_generates_candidate() -> None:
    caller, callee = _status_pair()
    enrichment = make_semantic_enrichment(
        program=caller, qualified_name="WS-STATUS", semantic_tag="status"
    )
    ctx = build_ctx([caller, callee], semantic_enrichment=enrichment)
    candidates = detect_state_transition_rule(ctx)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.rule_type == InterproceduralRuleType.STATE_TRANSITION_RULE
    assert candidate.support == InterproceduralCandidateSupport.DETERMINISTIC
    assert candidate.input_literal == "PENDING"
    assert candidate.output_literal == "APPROVED"
    assert candidate.target == "WS-STATUS"
    assert candidate.diagnostics == ["STATE_SEMANTIC_TAG_STATUS"]


def test_10_unclassified_actual_generates_no_state_transition_candidate() -> None:
    """Mismo par PENDING->APPROVED, pero SIN SemanticEnrichment (o sin un
    tag status/status_flag): nunca se genera por el NOMBRE de la
    variable, solo por el tag semantico ya existente."""
    caller, callee = _status_pair()
    ctx_no_enrichment = build_ctx([caller, callee], semantic_enrichment=None)
    assert detect_state_transition_rule(ctx_no_enrichment) == []

    other_tag_enrichment = make_semantic_enrichment(
        program=caller, qualified_name="WS-STATUS", semantic_tag="return_code"
    )
    ctx_wrong_tag = build_ctx([caller, callee], semantic_enrichment=other_tag_enrichment)
    assert detect_state_transition_rule(ctx_wrong_tag) == []


def test_state_transition_status_flag_tag_also_qualifies() -> None:
    caller, callee = _status_pair()
    enrichment = make_semantic_enrichment(
        program=caller, qualified_name="WS-STATUS", semantic_tag="status_flag"
    )
    ctx = build_ctx([caller, callee], semantic_enrichment=enrichment)
    candidates = detect_state_transition_rule(ctx)
    assert len(candidates) == 1
    assert candidates[0].diagnostics == ["STATE_SEMANTIC_TAG_STATUS_FLAG"]


def test_state_transition_identical_values_generates_no_candidate() -> None:
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-STATUS")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_move("CALLER::MAIN::0::MOVE", target="WS-STATUS", literal="PENDING"),
                    make_call(
                        "CALLER::MAIN::1::CALL",
                        call_arguments=[make_call_arg("WS-STATUS", CallPassingMode.REFERENCE)],
                    ),
                ],
            )
        ],
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-STATUS")],
        entry_parameters=[make_entry_param("LK-STATUS")],
        paragraphs=[
            make_paragraph(
                "MAIN", [make_move("CALLEE::MAIN::0::MOVE", target="LK-STATUS", literal="PENDING")]
            )
        ],
    )
    enrichment = make_semantic_enrichment(
        program=caller, qualified_name="WS-STATUS", semantic_tag="status"
    )
    ctx = build_ctx([caller, callee], semantic_enrichment=enrichment)
    assert detect_state_transition_rule(ctx) == []


# ---------------------------------------------------------------------------
# 11-15. Bloqueos estructurales (Fase 6/7 ya bloquean el call site)
# ---------------------------------------------------------------------------


def test_11_dynamic_call_generates_no_candidate() -> None:
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-R"), make_data_item("WS-TARGET")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_call(
                        "CALLER::MAIN::0::CALL",
                        called_program_name=None,
                        call_target_kind=CallTargetKind.DYNAMIC,
                        called_program_expression="WS-TARGET",
                        call_returning_data_item="WS-R",
                    )
                ],
            )
        ],
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-R")],
        entry_returning_data_item="LK-R",
        paragraphs=[
            make_paragraph(
                "MAIN", [make_move("CALLEE::MAIN::0::MOVE", target="LK-R", literal="0009")]
            )
        ],
    )
    ctx = build_ctx([caller, callee])
    assert detect_return_code_rule(ctx) == []
    assert detect_by_reference_rule(ctx) == []


def test_12_missing_program_generates_no_candidate() -> None:
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-R")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_call(
                        "CALLER::MAIN::0::CALL",
                        called_program_name="MISSING",
                        call_returning_data_item="WS-R",
                    )
                ],
            )
        ],
    )
    ctx = build_ctx([caller])
    assert detect_return_code_rule(ctx) == []
    assert detect_by_reference_rule(ctx) == []


def test_13_ambiguous_program_generates_no_candidate() -> None:
    """AMBIGUOUS_PROGRAM (dos versiones del mismo programa en el mismo
    paquete) es inalcanzable end-to-end a traves de la cadena real
    (`SemanticEffectsArtifact` ya exige `program_name` unico -- mismo
    hallazgo documentado por `test_interprocedural_propagation_analyzer.
    py::test_ambiguous_program_is_blocked`): se construye un
    `InterproceduralCallLinkageArtifact` directamente con un call site
    AMBIGUOUS_PROGRAM para probar el detector en aislamiento."""
    caller = make_program("CALLER")
    source_ref = InterproceduralSourceReference(program="CALLER", paragraph="MAIN")
    call_site = InterproceduralCallSite(
        call_site_id="callsite::ambiguous",
        caller_program="CALLER",
        caller_paragraph="MAIN",
        statement_id="CALLER::MAIN::0::CALL",
        target_kind=CallTargetKind.LITERAL,
        declared_target="CALLEE",
        resolution_status=ProgramResolutionStatus.AMBIGUOUS_PROGRAM,
        resolved_callee_program=None,
        arguments=[],
        support_status=SemanticSupportStatus.PARTIALLY_SUPPORTED,
        source_reference=source_ref,
    )
    linkage = InterproceduralCallLinkageArtifact(
        canonical_schema_versions=["1.2"],
        semantic_effects_schema_version="1.2",
        semantic_effects_analyzer_version="1.2",
        run_id="run1",
        source_package_hash=HASH,
        source_artifact_hashes=HASHES,
        summary=InterproceduralAnalysisSummary(
            program_count=1,
            interface_count=1,
            call_site_count=1,
            resolved_internal_count=0,
            dynamic_count=0,
            missing_program_count=0,
            ambiguous_program_count=1,
            recursive_call_count=0,
            cycle_count=0,
            binding_count=0,
            resolved_binding_count=0,
            unresolved_binding_count=0,
            counts_by_resolution_status={ProgramResolutionStatus.AMBIGUOUS_PROGRAM: 1},
            counts_by_binding_status={},
        ),
        interfaces=[ProgramInterface(program="CALLER", parameters=[], linkage_item_count=0)],
        call_sites=[call_site],
        call_edges=[],
        cycles=[],
    )
    effects = analyze_semantic_effects(
        canonical_programs=[caller],
        run_id="run1",
        source_package_hash=HASH,
        source_artifact_hashes=HASHES,
    )
    propagation = analyze_semantic_propagation(
        canonical_programs=[caller],
        semantic_effects=effects,
        run_id="run1",
        source_package_hash=HASH,
        source_artifact_hashes=HASHES,
    )
    interprocedural_propagation = analyze_interprocedural_propagation(
        canonical_programs=[caller],
        semantic_effects=effects,
        semantic_propagation=propagation,
        interprocedural_call_linkage=linkage,
        run_id="run1",
        source_package_hash=HASH,
        source_artifact_hashes=HASHES,
    )
    ctx = build_detector_context(
        canonical_programs=[caller],
        semantic_effects=effects,
        semantic_propagation=propagation,
        interprocedural_call_linkage=linkage,
        interprocedural_propagation=interprocedural_propagation,
        semantic_enrichment=None,
    )
    assert detect_return_code_rule(ctx) == []
    assert detect_by_reference_rule(ctx) == []


def test_14_self_call_generates_no_candidate() -> None:
    caller = make_program(
        "SELFCALL",
        data_items=[make_data_item("WS-R")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_call(
                        "SELFCALL::MAIN::0::CALL",
                        called_program_name="SELFCALL",
                        call_returning_data_item="WS-R",
                    )
                ],
            )
        ],
    )
    ctx = build_ctx([caller])
    assert detect_return_code_rule(ctx) == []


def test_15_scc_cycle_generates_no_candidate() -> None:
    program_a = make_program(
        "PROGA",
        data_items=[make_data_item("WS-R")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_call(
                        "PROGA::MAIN::0::CALL",
                        called_program_name="PROGB",
                        call_returning_data_item="WS-R",
                    )
                ],
            )
        ],
    )
    program_b = make_program(
        "PROGB",
        data_items=[make_data_item("WS-R")],
        linkage_data_items=[make_linkage_item("LK-R")],
        entry_returning_data_item="LK-R",
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_call(
                        "PROGB::MAIN::0::CALL",
                        called_program_name="PROGA",
                        call_returning_data_item="WS-R",
                    )
                ],
            )
        ],
    )
    ctx = build_ctx([program_a, program_b])
    assert detect_return_code_rule(ctx) == []


# ---------------------------------------------------------------------------
# 16-17. Multiples valores (invalidacion)
# ---------------------------------------------------------------------------


def test_16_multiple_caller_values_generates_no_candidate() -> None:
    """WS-A solo se mueve DENTRO de un IF (condicional), nunca en el
    scope raiz: nunca hay un unico literal de entrada deterministico
    demostrable -- InterproceduralPropagation invalida la entrada, y sin
    valor de entrada de respaldo el detector nunca genera candidato."""
    if_stmt = make_stmt(
        statement_id="CALLER::MAIN::0::IF",
        kind=StatementKind.IF,
        source_text="IF X",
        expression="X",
    )
    move_in_branch = make_stmt(
        statement_id="CALLER::MAIN::1::MOVE",
        kind=StatementKind.MOVE,
        source_text="MOVE",
        target_data_items=["WS-A"],
        variables_written=["WS-A"],
        assigned_literal="0005",
        parent_statement_id="CALLER::MAIN::0::IF",
        branch_kind="THEN",
    )
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-A")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    if_stmt,
                    move_in_branch,
                    make_call(
                        "CALLER::MAIN::2::CALL",
                        call_arguments=[make_call_arg("WS-A", CallPassingMode.REFERENCE)],
                    ),
                ],
            )
        ],
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-A")],
        entry_parameters=[make_entry_param("LK-A")],
    )
    ctx = build_ctx([caller, callee])
    assert detect_by_reference_rule(ctx) == []


def test_17_multiple_output_values_generates_no_candidate() -> None:
    """El callee solo escribe LK-A DENTRO de un IF (condicional), nunca
    en el scope raiz, y el actual tampoco tiene un valor de entrada de
    respaldo: nunca hay un unico literal de salida deterministico
    demostrable -- ningun candidato."""
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-A")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_call(
                        "CALLER::MAIN::0::CALL",
                        call_arguments=[make_call_arg("WS-A", CallPassingMode.REFERENCE)],
                    )
                ],
            )
        ],
    )
    if_stmt = make_stmt(
        statement_id="CALLEE::MAIN::0::IF",
        kind=StatementKind.IF,
        source_text="IF Y",
        expression="Y",
    )
    move_in_branch = make_stmt(
        statement_id="CALLEE::MAIN::1::MOVE",
        kind=StatementKind.MOVE,
        source_text="MOVE",
        target_data_items=["LK-A"],
        variables_written=["LK-A"],
        assigned_literal="2222",
        parent_statement_id="CALLEE::MAIN::0::IF",
        branch_kind="THEN",
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-A")],
        entry_parameters=[make_entry_param("LK-A")],
        paragraphs=[make_paragraph("MAIN", [if_stmt, move_in_branch])],
    )
    ctx = build_ctx([caller, callee])
    assert detect_by_reference_rule(ctx) == []


# ---------------------------------------------------------------------------
# 18-21. Provenance / IDs
# ---------------------------------------------------------------------------


def test_18_complete_provenance_chain() -> None:
    """caller -> call site -> binding -> callee -> output -> caller: cada
    elemento de la cadena esta presente y es consistente."""
    caller, callee = _returning_pair()
    ctx = build_ctx([caller, callee])
    candidate = detect_return_code_rule(ctx)[0]
    call_site = ctx.call_site_by_id[candidate.call_site_id]
    assert call_site.caller_program == candidate.caller_program == "CALLER"
    assert call_site.resolved_callee_program == candidate.callee_program == "CALLEE"
    output_evidence = [e for e in candidate.evidence if e.output_literal is not None]
    assert len(output_evidence) == 1
    assert call_site.returning_binding is not None
    assert output_evidence[0].binding_id == call_site.returning_binding.binding_id
    assert output_evidence[0].call_site_id == candidate.call_site_id
    assert output_evidence[0].caller_program == "CALLER"
    assert output_evidence[0].callee_program == "CALLEE"


def test_19_nonexistent_fact_id_never_referenced() -> None:
    """La evidencia de salida (`output_literal` = literal del candidato)
    siempre referencia el `InterproceduralPropagationFact.fact_id` real
    del hecho RETURNING que la sostiene -- nunca uno inventado.
    `source_fact_ids` (Fase 4, `SemanticPropagationArtifact`, namespace
    distinto) tambien puede aparecer en la lista, pero nunca reemplaza al
    fact_id interprocedural real."""
    caller, callee = _returning_pair()
    ctx = build_ctx([caller, callee])
    candidate = detect_return_code_rule(ctx)[0]
    real_fact = next(
        f
        for f in ctx.interprocedural_propagation.facts
        if f.call_site_id == candidate.call_site_id
        and f.kind == InterproceduralFactKind.RETURNING_FACT
    )
    output_evidence = next(
        e for e in candidate.evidence if e.output_literal == candidate.output_literal
    )
    assert real_fact.fact_id in output_evidence.propagation_fact_ids


def test_20_nonexistent_binding_id_never_referenced() -> None:
    caller, callee = _returning_pair()
    ctx = build_ctx([caller, callee])
    candidate = detect_return_code_rule(ctx)[0]
    real_binding_ids = set(ctx.binding_by_id)
    for evidence in candidate.evidence:
        if evidence.binding_id is not None:
            assert evidence.binding_id in real_binding_ids


def test_21_nonexistent_call_site_never_referenced() -> None:
    caller, callee = _returning_pair()
    ctx = build_ctx([caller, callee])
    candidate = detect_return_code_rule(ctx)[0]
    real_call_site_ids = set(ctx.call_site_by_id)
    assert candidate.call_site_id in real_call_site_ids
    for evidence in candidate.evidence:
        assert evidence.call_site_id in real_call_site_ids


# ---------------------------------------------------------------------------
# 22-24. Determinismo
# ---------------------------------------------------------------------------


def test_22_candidate_ids_are_deterministic_across_runs() -> None:
    caller, callee = _returning_pair()
    ctx1 = build_ctx([caller, callee])
    ctx2 = build_ctx([caller, callee])
    ids1 = [c.candidate_id for c in detect_return_code_rule(ctx1)]
    ids2 = [c.candidate_id for c in detect_return_code_rule(ctx2)]
    assert ids1 == ids2
    assert ids1  # no vacio: hay al menos un candidato deterministico


def test_23_order_independent_of_program_input_order() -> None:
    caller, callee = _returning_pair()
    interprocedural_propagation, effects, propagation, linkage = analyze_all([caller, callee])
    ctx_forward = build_detector_context(
        canonical_programs=[caller, callee],
        semantic_effects=effects,
        semantic_propagation=propagation,
        interprocedural_call_linkage=linkage,
        interprocedural_propagation=interprocedural_propagation,
        semantic_enrichment=None,
    )
    ctx_reversed = build_ctx([callee, caller])
    ids_forward = sorted(c.candidate_id for c in detect_return_code_rule(ctx_forward))
    ids_reversed = sorted(c.candidate_id for c in detect_return_code_rule(ctx_reversed))
    assert ids_forward == ids_reversed
    assert ids_forward  # no vacio: garantiza que la comparacion es significativa


def test_24_detectors_never_mutate_context_inputs() -> None:
    caller, callee = _returning_pair()
    ctx = build_ctx([caller, callee])
    facts_before = list(ctx.interprocedural_propagation.facts)
    call_sites_before = list(ctx.interprocedural_call_linkage.call_sites)
    detect_return_code_rule(ctx)
    detect_by_reference_rule(ctx)
    detect_state_transition_rule(ctx)
    assert list(ctx.interprocedural_propagation.facts) == facts_before
    assert list(ctx.interprocedural_call_linkage.call_sites) == call_sites_before


# ---------------------------------------------------------------------------
# 25. Ausencia de duplicados semanticos
# ---------------------------------------------------------------------------


def test_25_no_semantic_duplicates_across_multiple_call_sites() -> None:
    """Dos call sites distintos con el mismo literal de salida producen
    dos candidatos con candidate_id DIFERENTE (la clave semantica incluye
    call_site_id): nunca se colapsan silenciosamente."""
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-R"), make_data_item("WS-R2")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_call("CALLER::MAIN::0::CALL", call_returning_data_item="WS-R"),
                    make_call("CALLER::MAIN::1::CALL", call_returning_data_item="WS-R2"),
                ],
            )
        ],
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-R")],
        entry_returning_data_item="LK-R",
        paragraphs=[
            make_paragraph(
                "MAIN", [make_move("CALLEE::MAIN::0::MOVE", target="LK-R", literal="0009")]
            )
        ],
    )
    ctx = build_ctx([caller, callee])
    candidates = detect_return_code_rule(ctx)
    assert len(candidates) == 2
    ids = [c.candidate_id for c in candidates]
    assert len(ids) == len(set(ids))
