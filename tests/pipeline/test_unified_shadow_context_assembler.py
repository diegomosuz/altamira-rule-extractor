"""Tests del ensamblador PURO de ContextPackage shadow (Fase 13 Parte 6,
`feat/unified-shadow-downstream-pipeline`). Ver
`tests/parser_integration/test_unified_shadow_downstream_integration.py`
para la verificacion end-to-end contra el escenario real (JAR+Neo4j)."""

from __future__ import annotations

import pytest

from altamira_extractor.contracts.enums import CompletenessStatus
from altamira_extractor.contracts.semantic_graph import SemanticGraph
from altamira_extractor.pipeline.unified_shadow_context_adapter import (
    ShadowGroupContextView,
    adapt_group_to_context_view,
)
from altamira_extractor.pipeline.unified_shadow_context_assembler import (
    ContextAssemblyError,
    assemble_shadow_context_package,
)

from ._unified_shadow_downstream_fixtures import (
    OUTCOME_CODE,
    PARAGRAPH_NAME,
    PROGRAM_NAME,
    downstream_golden_path,
    semantic_graph,
)
from ._unified_shadow_validation_fixtures import HASH, MEMBER_ID, second_member


def _view_and_graph() -> tuple[ShadowGroupContextView, SemanticGraph]:
    dgp = downstream_golden_path()
    group = dgp.unified_shadow.shadow_groups[0]
    members_by_id = {m.member_id: m for m in dgp.unified_shadow.shadow_members}
    view = adapt_group_to_context_view(group, members_by_id=members_by_id)
    return view, dgp.semantic_graph


def test_assembles_real_valid_context_package_from_semantic_graph() -> None:
    view, graph = _view_and_graph()

    package = assemble_shadow_context_package(view, semantic_graph=graph, source_package_hash=HASH)

    assert package.scope.program == PROGRAM_NAME
    assert package.scope.paragraph == PARAGRAPH_NAME
    assert package.decision.outcome_code == OUTCOME_CODE
    assert package.candidate.candidate_id == view.group_id


def test_completeness_reflects_only_derivable_dimensions() -> None:
    view, graph = _view_and_graph()

    package = assemble_shadow_context_package(view, semantic_graph=graph, source_package_hash=HASH)

    assert package.completeness.D1 == CompletenessStatus.COMPLETE
    assert package.completeness.D2 == CompletenessStatus.COMPLETE
    assert package.completeness.D3 == CompletenessStatus.NOT_AVAILABLE
    assert package.completeness.D4 == CompletenessStatus.COMPLETE
    assert package.completeness.D5 == CompletenessStatus.NOT_AVAILABLE
    assert package.completeness.D6 == CompletenessStatus.NOT_AVAILABLE
    assert package.completeness.D7 == CompletenessStatus.NOT_AVAILABLE


def test_d3_d5_d6_d7_are_legitimately_empty_never_fabricated() -> None:
    view, graph = _view_and_graph()

    package = assemble_shadow_context_package(view, semantic_graph=graph, source_package_hash=HASH)

    assert package.data_context.parameter_tables == []
    assert package.data_context.transactional_tables_read == []
    assert package.effects.return_codes == []
    assert package.effects.table_effects == []
    assert package.batch_context.downstream_jobs == []
    assert package.domain_glossary == []


def test_missing_program_in_semantic_graph_raises_context_assembly_error() -> None:
    view, graph = _view_and_graph()
    view_unknown_program = view.__class__(**{**view.__dict__, "program": "DOES-NOT-EXIST"})

    with pytest.raises(ContextAssemblyError):
        assemble_shadow_context_package(
            view_unknown_program, semantic_graph=graph, source_package_hash=HASH
        )


def test_missing_paragraph_in_semantic_graph_raises_context_assembly_error() -> None:
    view, graph = _view_and_graph()
    view_unknown_paragraph = view.__class__(**{**view.__dict__, "paragraphs": ("DOES-NOT-EXIST",)})

    with pytest.raises(ContextAssemblyError):
        assemble_shadow_context_package(
            view_unknown_paragraph, semantic_graph=graph, source_package_hash=HASH
        )


def test_missing_decision_outcome_code_in_semantic_graph_raises_context_assembly_error() -> None:
    view, graph = _view_and_graph()
    view_unknown_outcome = view.__class__(
        **{**view.__dict__, "output_literal": "R999-DOES-NOT-EXIST"}
    )

    with pytest.raises(ContextAssemblyError):
        assemble_shadow_context_package(
            view_unknown_outcome, semantic_graph=graph, source_package_hash=HASH
        )


def test_ambiguous_paragraphs_across_members_raises_context_assembly_error() -> None:
    dgp = downstream_golden_path()
    group = dgp.unified_shadow.shadow_groups[0]
    member_2 = second_member().model_copy(update={"paragraph": "OTHER-PARAGRAPH"})
    group_two_paragraphs = group.model_copy(
        update={"member_ids": sorted([MEMBER_ID, member_2.member_id])}
    )
    members_by_id = {m.member_id: m for m in [*dgp.unified_shadow.shadow_members, member_2]}
    view = adapt_group_to_context_view(group_two_paragraphs, members_by_id=members_by_id)

    with pytest.raises(ContextAssemblyError):
        assemble_shadow_context_package(
            view, semantic_graph=dgp.semantic_graph, source_package_hash=HASH
        )


def test_missing_target_or_output_literal_raises_context_assembly_error() -> None:
    view, graph = _view_and_graph()
    view_no_output = view.__class__(**{**view.__dict__, "output_literal": None})

    with pytest.raises(ContextAssemblyError):
        assemble_shadow_context_package(
            view_no_output, semantic_graph=graph, source_package_hash=HASH
        )


def test_zero_evidence_ids_raises_context_assembly_error() -> None:
    view, graph = _view_and_graph()
    view_no_evidence = view.__class__(**{**view.__dict__, "evidence_ids": ()})

    with pytest.raises(ContextAssemblyError):
        assemble_shadow_context_package(
            view_no_evidence, semantic_graph=graph, source_package_hash=HASH
        )


def test_assembler_does_not_mutate_semantic_graph() -> None:
    view, graph = _view_and_graph()
    graph_snapshot = graph.model_copy(deep=True)

    assemble_shadow_context_package(view, semantic_graph=graph, source_package_hash=HASH)

    assert graph == graph_snapshot


def test_assembly_is_deterministic() -> None:
    view, graph = _view_and_graph()

    package_1 = assemble_shadow_context_package(
        view, semantic_graph=graph, source_package_hash=HASH
    )
    package_2 = assemble_shadow_context_package(
        view, semantic_graph=graph, source_package_hash=HASH
    )

    assert package_1.to_stable_json() == package_2.to_stable_json()


def test_semantic_graph_fixture_is_internally_coherent() -> None:
    graph = semantic_graph()
    assert graph.source_package_hash == HASH
    assert len(graph.nodes) == 6
