"""Auditoria de cierre de Fase 8 (Parte 3): semantica de
`InterproceduralCandidateSupport.BLOCKED` y trazabilidad de call sites
bloqueados que nunca llegan a candidato
(`pipeline/interprocedural_rule_detectors.py::blocked_call_site_diagnostics`).

Verifica la semantica requerida:

- Nunca se crea un candidato BLOCKED solo porque exista una CALL
  bloqueada (CALL dinamico, programa ausente/ambiguo, self-call, SCC).
- Se crea un candidato BLOCKED unicamente cuando existe un patron de
  regla identificable (RETURNING/BY REFERENCE resuelto) pero falta una
  condicion necesaria para demostrarlo (certeza estructural, p. ej.
  STOP RUN).
- Un call site bloqueado sin patron nunca se traza (silencio correcto).
- Un call site bloqueado CON patron, o resuelto pero indeterminado,
  siempre queda trazable en `diagnostics`, nunca desaparece en silencio.
- `summary.blocked_count` reconcilia exactamente candidatos (nunca
  call sites ni hechos): puede ser 0 aunque existan multiples call
  sites bloqueados."""

from __future__ import annotations

from altamira_extractor.contracts.enums import (
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
from altamira_extractor.contracts.interprocedural_rule_candidates import (
    InterproceduralCandidateSupport,
)
from altamira_extractor.contracts.semantic_coverage import SemanticSupportStatus
from altamira_extractor.pipeline.interprocedural_propagation_analyzer import (
    analyze_interprocedural_propagation,
)
from altamira_extractor.pipeline.interprocedural_rule_detector import (
    analyze_interprocedural_rule_candidates,
)
from altamira_extractor.pipeline.interprocedural_rule_detectors import (
    blocked_call_site_diagnostics,
    build_detector_context,
    detect_return_code_rule,
)
from altamira_extractor.pipeline.semantic_effects_analyzer import analyze_semantic_effects
from altamira_extractor.pipeline.semantic_propagation_analyzer import analyze_semantic_propagation

from .interprocedural_rule_helpers import (
    HASH,
    HASHES,
    analyze_all,
    build_ctx,
    make_call,
    make_data_item,
    make_linkage_item,
    make_move,
    make_paragraph,
    make_program,
    make_stmt,
    make_terminator,
)


def _analyze_full(programs, run_id: str = "run1"):
    interprocedural_propagation, effects, propagation, linkage = analyze_all(
        programs, run_id=run_id
    )
    return analyze_interprocedural_rule_candidates(
        canonical_programs=programs,
        v1_candidates=None,
        v2_candidates=None,
        semantic_effects=effects,
        semantic_propagation=propagation,
        interprocedural_call_linkage=linkage,
        interprocedural_propagation=interprocedural_propagation,
        semantic_enrichment=None,
        run_id=run_id,
        source_package_hash=HASH,
        source_artifact_hashes={"artifacts/02-canonical": HASH},
    )


# --- 1. Call site SIN patron nunca se traza (silencio correcto) -----------


def test_call_site_without_pattern_is_never_traced() -> None:
    caller = make_program(
        "CALLER",
        paragraphs=[make_paragraph("MAIN", [make_call("CALLER::MAIN::0::CALL")])],
    )
    callee = make_program("CALLEE")
    ctx = build_ctx([caller, callee])
    candidates = detect_return_code_rule(ctx)
    assert candidates == []
    diagnostics = blocked_call_site_diagnostics(ctx, candidates)
    assert diagnostics == []


# --- 2. Call sites bloqueados a nivel Fase 6/7 CON patron: trazables ------


def test_dynamic_call_with_pattern_is_traced_never_becomes_candidate() -> None:
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
    candidates = detect_return_code_rule(ctx)
    assert candidates == []
    diagnostics = blocked_call_site_diagnostics(ctx, candidates)
    assert len(diagnostics) == 1
    assert diagnostics[0].startswith("BLOCKED_CALL_SITE_NO_CANDIDATE::")
    assert diagnostics[0].endswith("::DYNAMIC_CALL")


def test_missing_program_with_pattern_is_traced_never_becomes_candidate() -> None:
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
    candidates = detect_return_code_rule(ctx)
    assert candidates == []
    diagnostics = blocked_call_site_diagnostics(ctx, candidates)
    assert len(diagnostics) == 1
    assert diagnostics[0].endswith("::MISSING_PROGRAM")


def test_self_call_with_pattern_is_traced_never_becomes_candidate() -> None:
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
    candidates = detect_return_code_rule(ctx)
    assert candidates == []
    diagnostics = blocked_call_site_diagnostics(ctx, candidates)
    assert len(diagnostics) == 1
    assert diagnostics[0].endswith("::RECURSION")


def test_scc_cycle_with_pattern_is_traced_on_both_sides_never_becomes_candidate() -> None:
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
    candidates = detect_return_code_rule(ctx)
    assert candidates == []
    diagnostics = blocked_call_site_diagnostics(ctx, candidates)
    assert len(diagnostics) == 2
    assert all(d.endswith("::CYCLE") for d in diagnostics)


def test_ambiguous_program_with_pattern_is_traced_never_becomes_candidate() -> None:
    """AMBIGUOUS_PROGRAM es inalcanzable end-to-end (mismo hallazgo que
    Fase 6/7): se construye un `InterproceduralCallLinkageArtifact`
    directamente, igual que en `test_interprocedural_rule_detectors.py`."""
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
        returning_binding=None,
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
    candidates = detect_return_code_rule(ctx)
    assert candidates == []
    # call_site.returning_binding es None (AMBIGUOUS_PROGRAM nunca llega a
    # tener un binding resuelto): _call_site_has_pattern es False, silencio
    # correcto -- no hay "patron" sintactico observable en este nodo
    # aislado (el RETURNING real vive en el CanonicalStatement, no en el
    # call site hecho a mano). Se confirma la ausencia de traza.
    diagnostics = blocked_call_site_diagnostics(ctx, candidates)
    assert diagnostics == []


# --- 3. Resuelto pero indeterminado (INVALIDATED/UNRESOLVED): trazable ----


def test_unresolved_value_with_pattern_is_traced_never_becomes_candidate() -> None:
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-R")],
        paragraphs=[
            make_paragraph(
                "MAIN", [make_call("CALLER::MAIN::0::CALL", call_returning_data_item="WS-R")]
            )
        ],
    )
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
    candidates = detect_return_code_rule(ctx)
    assert candidates == []
    diagnostics = blocked_call_site_diagnostics(ctx, candidates)
    assert len(diagnostics) == 1
    assert diagnostics[0].startswith("NO_CANDIDATE_UNRESOLVED_VALUE::")


# --- 4. STOP RUN: candidato BLOCKED genuino (patron + certeza estructural) -


def test_stop_run_produces_a_genuine_blocked_candidate_covered_not_traced_separately() -> None:
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-R")],
        paragraphs=[
            make_paragraph(
                "MAIN", [make_call("CALLER::MAIN::0::CALL", call_returning_data_item="WS-R")]
            )
        ],
    )
    stop_run = make_terminator(
        "CALLEE::MAIN::1::PROGRAM_TERMINATION", ProgramTerminationKind.STOP_RUN
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-R")],
        entry_returning_data_item="LK-R",
        paragraphs=[
            make_paragraph(
                "MAIN",
                [make_move("CALLEE::MAIN::0::MOVE", target="LK-R", literal="0009"), stop_run],
            )
        ],
    )
    ctx = build_ctx([caller, callee])
    candidates = detect_return_code_rule(ctx)
    assert len(candidates) == 1
    assert candidates[0].support == InterproceduralCandidateSupport.BLOCKED
    # el call site YA esta cubierto por un candidato BLOCKED -- no debe
    # aparecer TAMBIEN en blocked_call_site_diagnostics (nunca doble
    # trazabilidad del mismo call site).
    diagnostics = blocked_call_site_diagnostics(ctx, candidates)
    assert diagnostics == []


# --- 5. summary.blocked_count reconcilia candidatos, nunca call sites -----


def test_summary_blocked_count_is_zero_despite_multiple_blocked_call_sites() -> None:
    """Cuatro call sites bloqueados a nivel Fase 6 (dinamico, ausente,
    self-call, y el mismo self-call reutilizado con otro target) --
    CERO candidatos BLOCKED (ninguno tiene un binding resuelto), pero
    los cuatro quedan trazables en diagnostics."""
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-R1"), make_data_item("WS-R2"), make_data_item("WS-TARGET")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_call(
                        "CALLER::MAIN::0::CALL",
                        called_program_name=None,
                        call_target_kind=CallTargetKind.DYNAMIC,
                        called_program_expression="WS-TARGET",
                        call_returning_data_item="WS-R1",
                    ),
                    make_call(
                        "CALLER::MAIN::1::CALL",
                        called_program_name="MISSING",
                        call_returning_data_item="WS-R2",
                    ),
                ],
            )
        ],
    )
    artifact = _analyze_full([caller])
    assert artifact.summary.blocked_count == 0
    assert artifact.summary.candidate_count == 0
    assert artifact.candidates == []
    blocked_diagnostics = [
        d for d in artifact.diagnostics if d.startswith("BLOCKED_CALL_SITE_NO_CANDIDATE::")
    ]
    assert len(blocked_diagnostics) == 2
    assert any(d.endswith("::DYNAMIC_CALL") for d in blocked_diagnostics)
    assert any(d.endswith("::MISSING_PROGRAM") for d in blocked_diagnostics)


def test_summary_blocked_count_reconciles_with_real_blocked_candidates() -> None:
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-R")],
        paragraphs=[
            make_paragraph(
                "MAIN", [make_call("CALLER::MAIN::0::CALL", call_returning_data_item="WS-R")]
            )
        ],
    )
    stop_run = make_terminator(
        "CALLEE::MAIN::1::PROGRAM_TERMINATION", ProgramTerminationKind.STOP_RUN
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-R")],
        entry_returning_data_item="LK-R",
        paragraphs=[
            make_paragraph(
                "MAIN",
                [make_move("CALLEE::MAIN::0::MOVE", target="LK-R", literal="0009"), stop_run],
            )
        ],
    )
    artifact = _analyze_full([caller, callee])
    assert artifact.summary.blocked_count == 1
    assert artifact.summary.candidate_count == 1
    assert len(artifact.candidates) == 1
    assert artifact.candidates[0].support == InterproceduralCandidateSupport.BLOCKED
    blocked_diagnostics = [
        d for d in artifact.diagnostics if d.startswith("BLOCKED_CALL_SITE_NO_CANDIDATE::")
    ]
    assert blocked_diagnostics == []
