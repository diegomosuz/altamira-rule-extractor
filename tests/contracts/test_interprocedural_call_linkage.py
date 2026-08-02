"""Tests contractuales de la fundacion interprocedural CALL/LINKAGE
(Fase 6 de la ampliacion semantica, `contracts/interprocedural_call_linkage.py`).
NO contractual respecto a `artifacts/01-10` -- ver docstring del modulo
bajo prueba: es un artefacto diagnostico adyacente en
`diagnostics/interprocedural-call-linkage.json`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts.enums import CallPassingMode, CallTargetKind, LocationKind
from altamira_extractor.contracts.interprocedural_call_linkage import (
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
from altamira_extractor.contracts.semantic_coverage import SemanticSupportStatus

VALID_HASH = "a" * 64
SOURCE_FILE = "01-codigo/cobol/CALLERP.cbl"


# --- factories ---------------------------------------------------------------


def make_source_reference(**overrides: object) -> InterproceduralSourceReference:
    fields: dict[str, object] = {
        "program": "CALLERP",
        "paragraph": "MAIN-PARA",
        "statement_id": "CALLERP::MAIN-PARA::0::CALL",
        "source_file": SOURCE_FILE,
        "line": 11,
        "location_kind": LocationKind.EXACT,
    }
    fields.update(overrides)
    return InterproceduralSourceReference(**fields)  # type: ignore[arg-type]


def make_interface_parameter(**overrides: object) -> ProgramInterfaceParameter:
    fields: dict[str, object] = {
        "ordinal": 1,
        "formal_name": "LK-INPUT",
        "formal_qualified_name": "LK-INPUT",
        "linkage_item_qualified_name": "LK-INPUT",
        "pic": "X(10)",
        "source_reference": make_source_reference(
            program="CALLEEP", paragraph=None, statement_id=None
        ),
        "diagnostics": [],
    }
    fields.update(overrides)
    return ProgramInterfaceParameter(**fields)  # type: ignore[arg-type]


def make_program_interface(**overrides: object) -> ProgramInterface:
    fields: dict[str, object] = {
        "program": "CALLEEP",
        "parameters": [make_interface_parameter()],
        "returning_parameter": None,
        "linkage_item_count": 1,
        "diagnostics": [],
    }
    fields.update(overrides)
    return ProgramInterface(**fields)  # type: ignore[arg-type]


def make_binding(**overrides: object) -> InterproceduralArgumentBinding:
    fields: dict[str, object] = {
        "binding_id": "binding::callsite-1::1",
        "ordinal": 1,
        "status": ArgumentBindingStatus.RESOLVED_POSITIONAL,
        "passing_mode": CallPassingMode.REFERENCE,
        "potential_flow": PotentialDataFlow.INPUT_OUTPUT,
        "actual_name": "WS-INPUT",
        "actual_qualified_name": "WS-INPUT",
        "formal_name": "LK-INPUT",
        "formal_qualified_name": "LK-INPUT",
        "linkage_item_qualified_name": "LK-INPUT",
        "diagnostics": [],
        "source_references": [],
    }
    fields.update(overrides)
    return InterproceduralArgumentBinding(**fields)  # type: ignore[arg-type]


def make_call_site(**overrides: object) -> InterproceduralCallSite:
    fields: dict[str, object] = {
        "call_site_id": "callsite::CALLERP::MAIN-PARA::0",
        "caller_program": "CALLERP",
        "caller_paragraph": "MAIN-PARA",
        "statement_id": "CALLERP::MAIN-PARA::0::CALL",
        "target_kind": CallTargetKind.LITERAL,
        "declared_target": "CALLEEP",
        "resolution_status": ProgramResolutionStatus.RESOLVED_INTERNAL,
        "resolved_callee_program": "CALLEEP",
        "arguments": [make_binding()],
        "returning_binding": None,
        "recursive": False,
        "part_of_cycle": False,
        "support_status": SemanticSupportStatus.FULLY_SUPPORTED,
        "diagnostics": [],
        "source_reference": make_source_reference(),
    }
    fields.update(overrides)
    return InterproceduralCallSite(**fields)  # type: ignore[arg-type]


def make_call_edge(**overrides: object) -> ProgramCallEdge:
    fields: dict[str, object] = {
        "edge_id": "edge::CALLERP::CALLEEP",
        "caller_program": "CALLERP",
        "callee_program": "CALLEEP",
        "call_site_ids": ["callsite::CALLERP::MAIN-PARA::0"],
        "recursive": False,
        "part_of_cycle": False,
    }
    fields.update(overrides)
    return ProgramCallEdge(**fields)  # type: ignore[arg-type]


def make_summary(**overrides: object) -> InterproceduralAnalysisSummary:
    fields: dict[str, object] = {
        "program_count": 2,
        "interface_count": 1,
        "call_site_count": 1,
        "resolved_internal_count": 1,
        "dynamic_count": 0,
        "missing_program_count": 0,
        "ambiguous_program_count": 0,
        "recursive_call_count": 0,
        "cycle_count": 0,
        "binding_count": 1,
        "resolved_binding_count": 1,
        "unresolved_binding_count": 0,
        "counts_by_resolution_status": {ProgramResolutionStatus.RESOLVED_INTERNAL: 1},
        "counts_by_binding_status": {ArgumentBindingStatus.RESOLVED_POSITIONAL: 1},
    }
    fields.update(overrides)
    return InterproceduralAnalysisSummary(**fields)  # type: ignore[arg-type]


def make_artifact(**overrides: object) -> InterproceduralCallLinkageArtifact:
    fields: dict[str, object] = {
        "canonical_schema_versions": ["1.0", "1.2"],
        "semantic_effects_schema_version": "1.2",
        "semantic_effects_analyzer_version": "1.2",
        "run_id": "run-0001",
        "source_package_hash": VALID_HASH,
        "source_artifact_hashes": {"02-canonical/CALLERP.json": VALID_HASH},
        "summary": make_summary(),
        "interfaces": [make_program_interface()],
        "call_sites": [make_call_site()],
        "call_edges": [make_call_edge()],
        "cycles": [],
    }
    fields.update(overrides)
    return InterproceduralCallLinkageArtifact(**fields)  # type: ignore[arg-type]


# --- potential_flow_for_passing_mode ------------------------------------------


def test_potential_flow_reference_is_input_output() -> None:
    assert (
        potential_flow_for_passing_mode(CallPassingMode.REFERENCE) == PotentialDataFlow.INPUT_OUTPUT
    )


def test_potential_flow_content_is_input_only() -> None:
    assert potential_flow_for_passing_mode(CallPassingMode.CONTENT) == PotentialDataFlow.INPUT_ONLY


def test_potential_flow_value_is_input_only() -> None:
    assert potential_flow_for_passing_mode(CallPassingMode.VALUE) == PotentialDataFlow.INPUT_ONLY


def test_potential_flow_unknown_is_unknown() -> None:
    assert potential_flow_for_passing_mode(CallPassingMode.UNKNOWN) == PotentialDataFlow.UNKNOWN


# --- InterproceduralSourceReference / ProgramInterfaceParameter --------------


def test_source_reference_never_has_source_text_field() -> None:
    assert "source_text" not in InterproceduralSourceReference.model_fields


def test_source_reference_paragraph_and_statement_id_are_optional() -> None:
    reference = make_source_reference(paragraph=None, statement_id=None)
    assert reference.paragraph is None
    assert reference.statement_id is None


def test_source_reference_rejects_absolute_path() -> None:
    with pytest.raises(ValidationError):
        make_source_reference(source_file="/etc/passwd")


def test_interface_parameter_diagnostics_reject_unsorted() -> None:
    with pytest.raises(ValidationError, match="diagnostics"):
        make_interface_parameter(diagnostics=["Z_CODE", "A_CODE"])


def test_interface_parameter_diagnostics_reject_duplicates() -> None:
    with pytest.raises(ValidationError, match="diagnostics"):
        make_interface_parameter(diagnostics=["CODE", "CODE"])


def test_interface_parameter_allows_unresolved_linkage_item() -> None:
    parameter = make_interface_parameter(
        linkage_item_qualified_name=None, diagnostics=["FORMAL_UNRESOLVED_AMBIGUOUS"]
    )
    assert parameter.linkage_item_qualified_name is None


# --- ProgramInterface ----------------------------------------------------------


def test_program_interface_requires_consecutive_ordinals() -> None:
    with pytest.raises(ValidationError, match="ordinal consecutivo"):
        make_program_interface(parameters=[make_interface_parameter(ordinal=2)])


def test_program_interface_with_no_parameters_is_valid() -> None:
    interface = make_program_interface(parameters=[], linkage_item_count=0)
    assert interface.parameters == []


def test_program_interface_with_returning_parameter() -> None:
    interface = make_program_interface(
        returning_parameter=make_interface_parameter(
            ordinal=1, formal_name="LK-RESULT", linkage_item_qualified_name="LK-RESULT"
        )
    )
    assert interface.returning_parameter is not None
    assert interface.returning_parameter.formal_name == "LK-RESULT"


def test_program_interface_rejects_additional_properties() -> None:
    payload = make_program_interface().model_dump(mode="json")
    payload["unexpected_field"] = "not allowed"
    with pytest.raises(ValidationError):
        ProgramInterface.model_validate(payload)


# --- InterproceduralArgumentBinding --------------------------------------------


def test_binding_potential_flow_must_match_passing_mode_rule() -> None:
    with pytest.raises(ValidationError, match="no coincide con la"):
        make_binding(
            passing_mode=CallPassingMode.CONTENT, potential_flow=PotentialDataFlow.INPUT_OUTPUT
        )


def test_binding_returning_allows_output_only_regardless_of_passing_mode() -> None:
    """Fase 12 regla 10: el binding de RETURNING usa potential_flow=OUTPUT_ONLY
    independientemente de passing_mode (RETURNING no es BY REFERENCE/CONTENT/VALUE)."""
    binding = make_binding(
        binding_id="binding::callsite-1::returning",
        passing_mode=CallPassingMode.REFERENCE,
        potential_flow=PotentialDataFlow.OUTPUT_ONLY,
        actual_name="WS-RESULT",
        formal_name="LK-RESULT",
        formal_qualified_name="LK-RESULT",
        linkage_item_qualified_name="LK-RESULT",
    )
    assert binding.potential_flow == PotentialDataFlow.OUTPUT_ONLY


def test_binding_missing_actual_forbids_actual_fields() -> None:
    with pytest.raises(ValidationError, match="MISSING_ACTUAL no puede declarar"):
        make_binding(
            status=ArgumentBindingStatus.MISSING_ACTUAL,
            actual_name="WS-INPUT",
        )


def test_binding_missing_actual_with_no_actual_fields_is_valid() -> None:
    binding = make_binding(
        status=ArgumentBindingStatus.MISSING_ACTUAL,
        actual_name=None,
        actual_qualified_name=None,
        actual_literal=None,
    )
    assert binding.status == ArgumentBindingStatus.MISSING_ACTUAL


def test_binding_extra_actual_forbids_formal_fields() -> None:
    with pytest.raises(ValidationError, match="EXTRA_ACTUAL no puede declarar"):
        make_binding(
            status=ArgumentBindingStatus.EXTRA_ACTUAL,
            formal_name="LK-INPUT",
        )


def test_binding_extra_actual_with_no_formal_fields_is_valid() -> None:
    binding = make_binding(
        status=ArgumentBindingStatus.EXTRA_ACTUAL,
        formal_name=None,
        formal_qualified_name=None,
        linkage_item_qualified_name=None,
    )
    assert binding.formal_name is None


def test_binding_resolved_positional_requires_formal_name() -> None:
    with pytest.raises(ValidationError, match="RESOLVED_POSITIONAL exige formal_name"):
        make_binding(formal_name=None, formal_qualified_name=None)


def test_binding_resolved_positional_requires_actual_name_or_literal() -> None:
    with pytest.raises(
        ValidationError, match="RESOLVED_POSITIONAL exige actual_name o actual_literal"
    ):
        make_binding(actual_name=None, actual_qualified_name=None, actual_literal=None)


def test_binding_resolved_positional_accepts_actual_literal_without_name() -> None:
    binding = make_binding(
        actual_name=None,
        actual_qualified_name=None,
        actual_literal="'X'",
        passing_mode=CallPassingMode.CONTENT,
        potential_flow=PotentialDataFlow.INPUT_ONLY,
    )
    assert binding.actual_literal == "'X'"


def test_binding_diagnostics_reject_unsorted() -> None:
    with pytest.raises(ValidationError, match="diagnostics"):
        make_binding(diagnostics=["Z", "A"])


def test_binding_round_trips() -> None:
    binding = make_binding()
    restored = InterproceduralArgumentBinding.model_validate_json(binding.to_stable_json())
    assert restored == binding


# --- InterproceduralCallSite ---------------------------------------------------


def test_call_site_resolved_internal_requires_resolved_callee_program() -> None:
    with pytest.raises(ValidationError, match="RESOLVED_INTERNAL exige resolved_callee_program"):
        make_call_site(resolved_callee_program=None)


def test_call_site_non_resolved_forbids_resolved_callee_program() -> None:
    with pytest.raises(ValidationError, match="no puede declarar resolved_callee_program"):
        make_call_site(
            resolution_status=ProgramResolutionStatus.UNRESOLVED_MISSING_PROGRAM,
            resolved_callee_program="CALLEEP",
        )


def test_call_site_missing_program_is_valid_without_resolved_callee() -> None:
    call_site = make_call_site(
        resolution_status=ProgramResolutionStatus.UNRESOLVED_MISSING_PROGRAM,
        resolved_callee_program=None,
        arguments=[],
        support_status=SemanticSupportStatus.PARTIALLY_SUPPORTED,
    )
    assert call_site.resolved_callee_program is None


def test_call_site_dynamic_target_requires_unresolved_dynamic_status() -> None:
    with pytest.raises(ValidationError, match="DYNAMIC exige resolution_status=UNRESOLVED_DYNAMIC"):
        make_call_site(
            target_kind=CallTargetKind.DYNAMIC,
            declared_target="WS-PROGRAM-NAME",
            resolution_status=ProgramResolutionStatus.RESOLVED_INTERNAL,
        )


def test_call_site_literal_target_forbids_unresolved_dynamic_status() -> None:
    with pytest.raises(
        ValidationError, match="LITERAL no puede declarar resolution_status=UNRESOLVED_DYNAMIC"
    ):
        make_call_site(
            target_kind=CallTargetKind.LITERAL,
            resolution_status=ProgramResolutionStatus.UNRESOLVED_DYNAMIC,
            resolved_callee_program=None,
        )


def test_call_site_unknown_target_may_use_unresolved_dynamic_status() -> None:
    """UNKNOWN (target no identificable estructuralmente) tambien puede
    resolver a UNRESOLVED_DYNAMIC -- la coherencia con DYNAMIC no es
    biyectiva (ver docstring de _check_target_kind_resolution_coherence)."""
    call_site = make_call_site(
        target_kind=CallTargetKind.UNKNOWN,
        declared_target="UNKNOWN",
        resolution_status=ProgramResolutionStatus.UNRESOLVED_DYNAMIC,
        resolved_callee_program=None,
        arguments=[],
        support_status=SemanticSupportStatus.PARTIALLY_SUPPORTED,
    )
    assert call_site.target_kind == CallTargetKind.UNKNOWN


def test_call_site_recursive_requires_resolved_callee_equals_caller() -> None:
    with pytest.raises(
        ValidationError, match="recursive=True exige resolved_callee_program == caller_program"
    ):
        make_call_site(recursive=True, resolved_callee_program="CALLEEP")


def test_call_site_self_call_recursive_is_valid() -> None:
    call_site = make_call_site(
        caller_program="CALLERP",
        declared_target="CALLERP",
        resolved_callee_program="CALLERP",
        recursive=True,
        arguments=[],
    )
    assert call_site.recursive is True


def test_call_site_part_of_cycle_requires_resolved_internal() -> None:
    with pytest.raises(ValidationError, match="part_of_cycle=True exige"):
        make_call_site(
            part_of_cycle=True,
            resolution_status=ProgramResolutionStatus.UNRESOLVED_MISSING_PROGRAM,
            resolved_callee_program=None,
            arguments=[],
            support_status=SemanticSupportStatus.PARTIALLY_SUPPORTED,
        )


def test_call_site_arguments_require_consecutive_ordinals() -> None:
    with pytest.raises(ValidationError, match="ordinal consecutivo"):
        make_call_site(arguments=[make_binding(ordinal=2)])


def test_call_site_diagnostics_reject_duplicates() -> None:
    with pytest.raises(ValidationError, match="diagnostics"):
        make_call_site(
            diagnostics=["CALL_DYNAMIC_TARGET_UNRESOLVED", "CALL_DYNAMIC_TARGET_UNRESOLVED"]
        )


def test_call_site_with_returning_binding_round_trips() -> None:
    call_site = make_call_site(
        returning_binding=make_binding(
            binding_id="binding::callsite-1::returning",
            potential_flow=PotentialDataFlow.OUTPUT_ONLY,
            actual_name="WS-RESULT",
            formal_name="LK-RESULT",
            formal_qualified_name="LK-RESULT",
            linkage_item_qualified_name="LK-RESULT",
        )
    )
    first = call_site.to_stable_json()
    second = InterproceduralCallSite.model_validate_json(first).to_stable_json()
    assert first == second


def test_call_site_rejects_additional_properties() -> None:
    payload = make_call_site().model_dump(mode="json")
    payload["unexpected_field"] = "not allowed"
    with pytest.raises(ValidationError):
        InterproceduralCallSite.model_validate(payload)


# --- ProgramCallEdge -----------------------------------------------------------


def test_call_edge_self_call_requires_recursive_true() -> None:
    with pytest.raises(
        ValidationError, match="caller_program == callee_program exige recursive=True"
    ):
        make_call_edge(caller_program="CALLERP", callee_program="CALLERP", recursive=False)


def test_call_edge_non_self_call_forbids_recursive_true() -> None:
    with pytest.raises(
        ValidationError, match="recursive=True exige caller_program == callee_program"
    ):
        make_call_edge(recursive=True)


def test_call_edge_self_call_with_recursive_true_is_valid() -> None:
    edge = make_call_edge(
        edge_id="edge::CALLERP::CALLERP",
        caller_program="CALLERP",
        callee_program="CALLERP",
        recursive=True,
    )
    assert edge.recursive is True


def test_call_edge_call_site_ids_reject_unsorted() -> None:
    with pytest.raises(ValidationError, match="call_site_ids"):
        make_call_edge(call_site_ids=["callsite::Z", "callsite::A"])


def test_call_edge_requires_at_least_one_call_site() -> None:
    with pytest.raises(ValidationError):
        make_call_edge(call_site_ids=[])


# --- ProgramCallCycle ------------------------------------------------------------


def test_cycle_requires_at_least_two_programs() -> None:
    with pytest.raises(ValidationError):
        ProgramCallCycle(cycle_id="cycle::x", programs=["A"], edge_ids=["edge::A::B"])


def test_cycle_rejects_repeated_program() -> None:
    with pytest.raises(ValidationError, match="no puede repetir un programa"):
        ProgramCallCycle(
            cycle_id="cycle::x", programs=["A", "B", "A"], edge_ids=["edge::A::B", "edge::B::A"]
        )


def test_cycle_two_program_round_trips() -> None:
    cycle = ProgramCallCycle(
        cycle_id="cycle::abc123", programs=["PROGA", "PROGB"], edge_ids=["edge::A::B", "edge::B::A"]
    )
    first = cycle.to_stable_json()
    second = ProgramCallCycle.model_validate_json(first).to_stable_json()
    assert first == second


def test_cycle_edge_ids_reject_unsorted() -> None:
    with pytest.raises(ValidationError, match="edge_ids"):
        ProgramCallCycle(cycle_id="cycle::x", programs=["A", "B"], edge_ids=["edge::Z", "edge::A"])


# --- InterproceduralAnalysisSummary -----------------------------------------------


def test_summary_resolution_partition_must_equal_call_site_count() -> None:
    with pytest.raises(ValidationError, match="no coincide con call_site_count"):
        make_summary(call_site_count=2)


def test_summary_counts_by_resolution_status_must_match_named_counts() -> None:
    with pytest.raises(ValidationError, match="counts_by_resolution_status"):
        make_summary(
            counts_by_resolution_status={ProgramResolutionStatus.RESOLVED_INTERNAL: 2},
            call_site_count=1,
            resolved_internal_count=1,
        )


def test_summary_binding_partition_must_equal_binding_count() -> None:
    with pytest.raises(ValidationError, match="no coincide con binding_count"):
        make_summary(binding_count=2)


def test_summary_resolved_binding_count_must_match_map() -> None:
    with pytest.raises(ValidationError, match="RESOLVED_POSITIONAL"):
        make_summary(resolved_binding_count=0, unresolved_binding_count=1)


def test_summary_zero_call_sites_round_trips() -> None:
    summary = InterproceduralAnalysisSummary(
        program_count=1,
        interface_count=0,
        call_site_count=0,
        resolved_internal_count=0,
        dynamic_count=0,
        missing_program_count=0,
        ambiguous_program_count=0,
        recursive_call_count=0,
        cycle_count=0,
        binding_count=0,
        resolved_binding_count=0,
        unresolved_binding_count=0,
        counts_by_resolution_status={},
        counts_by_binding_status={},
    )
    first = summary.to_stable_json()
    second = InterproceduralAnalysisSummary.model_validate_json(first).to_stable_json()
    assert first == second


# --- InterproceduralCallLinkageArtifact -------------------------------------------


def test_artifact_round_trips() -> None:
    artifact = make_artifact()
    restored = InterproceduralCallLinkageArtifact.model_validate_json(artifact.to_stable_json())
    assert restored == artifact


def test_artifact_rejects_additional_properties() -> None:
    payload = make_artifact().model_dump(mode="json")
    payload["unexpected_field"] = "not allowed"
    with pytest.raises(ValidationError):
        InterproceduralCallLinkageArtifact.model_validate(payload)


def test_artifact_has_no_timestamp_field() -> None:
    fields = InterproceduralCallLinkageArtifact.model_fields
    assert not any("time" in name or "date" in name for name in fields)


def test_artifact_canonical_schema_versions_reject_unsorted() -> None:
    with pytest.raises(ValidationError, match="canonical_schema_versions"):
        make_artifact(canonical_schema_versions=["1.2", "1.0"])


def test_artifact_canonical_schema_versions_reject_duplicates() -> None:
    with pytest.raises(ValidationError, match="canonical_schema_versions"):
        make_artifact(canonical_schema_versions=["1.0", "1.0"])


def test_artifact_canonical_schema_versions_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        make_artifact(canonical_schema_versions=["1.3"])


def test_artifact_interfaces_must_be_sorted_by_program() -> None:
    with pytest.raises(ValidationError, match="ordenado deterministicamente por program"):
        make_artifact(
            interfaces=[
                make_program_interface(program="PROGB"),
                make_program_interface(program="PROGA"),
            ]
        )


def test_artifact_interfaces_reject_duplicate_program() -> None:
    with pytest.raises(ValidationError, match="program duplicado"):
        make_artifact(
            interfaces=[make_program_interface(program="CALLEEP")] * 2,
            summary=make_summary(interface_count=2),
        )


def test_artifact_call_sites_must_be_sorted_by_id() -> None:
    with pytest.raises(ValidationError, match="ordenado deterministicamente por call_site_id"):
        make_artifact(
            call_sites=[
                make_call_site(call_site_id="callsite::Z"),
                make_call_site(call_site_id="callsite::A"),
            ],
            summary=make_summary(
                call_site_count=2,
                resolved_internal_count=2,
                binding_count=2,
                resolved_binding_count=2,
                counts_by_resolution_status={ProgramResolutionStatus.RESOLVED_INTERNAL: 2},
                counts_by_binding_status={ArgumentBindingStatus.RESOLVED_POSITIONAL: 2},
            ),
        )


def test_artifact_call_edges_must_be_sorted_by_id() -> None:
    with pytest.raises(ValidationError, match="ordenado deterministicamente por edge_id"):
        make_artifact(
            call_edges=[
                make_call_edge(edge_id="edge::Z"),
                make_call_edge(edge_id="edge::A"),
            ]
        )


def test_artifact_cycles_must_be_sorted_by_id() -> None:
    with pytest.raises(ValidationError, match="ordenado deterministicamente por cycle_id"):
        make_artifact(
            cycles=[
                ProgramCallCycle(
                    cycle_id="cycle::z", programs=["A", "B"], edge_ids=["edge::A::B", "edge::B::A"]
                ),
                ProgramCallCycle(
                    cycle_id="cycle::a", programs=["C", "D"], edge_ids=["edge::C::D", "edge::D::C"]
                ),
            ],
            summary=make_summary(cycle_count=2),
        )


def test_artifact_summary_interface_count_must_match() -> None:
    with pytest.raises(ValidationError, match="summary.interface_count"):
        make_artifact(summary=make_summary(interface_count=2))


def test_artifact_summary_call_site_count_must_match() -> None:
    with pytest.raises(ValidationError, match="summary.call_site_count"):
        make_artifact(
            summary=make_summary(
                call_site_count=0,
                resolved_internal_count=0,
                counts_by_resolution_status={},
                binding_count=0,
                resolved_binding_count=0,
                counts_by_binding_status={},
            )
        )


def test_artifact_summary_recursive_call_count_must_match() -> None:
    with pytest.raises(ValidationError, match="recursive_call_count"):
        make_artifact(summary=make_summary(recursive_call_count=1))


def test_artifact_summary_binding_count_must_match_real_bindings() -> None:
    with pytest.raises(ValidationError, match="summary.binding_count"):
        make_artifact(
            summary=make_summary(
                binding_count=0, resolved_binding_count=0, counts_by_binding_status={}
            )
        )


def test_artifact_no_call_sites_no_edges_no_cycles_round_trips() -> None:
    artifact = make_artifact(
        interfaces=[],
        call_sites=[],
        call_edges=[],
        cycles=[],
        summary=InterproceduralAnalysisSummary(
            program_count=1,
            interface_count=0,
            call_site_count=0,
            resolved_internal_count=0,
            dynamic_count=0,
            missing_program_count=0,
            ambiguous_program_count=0,
            recursive_call_count=0,
            cycle_count=0,
            binding_count=0,
            resolved_binding_count=0,
            unresolved_binding_count=0,
            counts_by_resolution_status={},
            counts_by_binding_status={},
        ),
    )
    first = artifact.to_stable_json()
    second = InterproceduralCallLinkageArtifact.model_validate_json(first).to_stable_json()
    assert first == second


def test_artifact_source_package_hash_must_be_sha256_hex() -> None:
    with pytest.raises(ValidationError):
        make_artifact(source_package_hash="not-a-hash")


def test_artifact_source_artifact_hashes_requires_at_least_one_entry() -> None:
    with pytest.raises(ValidationError):
        make_artifact(source_artifact_hashes={})


def test_artifact_schema_version_and_analyzer_version_default_to_1_0() -> None:
    artifact = make_artifact()
    assert artifact.schema_version == "1.0"
    assert artifact.analyzer_version == "1.0"


def test_artifact_accepts_semantic_effects_version_1_0_historical() -> None:
    artifact = make_artifact(
        semantic_effects_schema_version="1.0", semantic_effects_analyzer_version="1.0"
    )
    assert artifact.semantic_effects_schema_version == "1.0"


def test_artifact_rejects_unknown_semantic_effects_version() -> None:
    with pytest.raises(ValidationError):
        make_artifact(semantic_effects_schema_version="1.3")
