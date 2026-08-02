"""Tests contractuales de la propagacion interprocedural conservadora en
shadow mode (Fase 7 de la ampliacion semantica,
`feat/interprocedural-propagation-shadow`):
`contracts/interprocedural_propagation.py`. NO contractual respecto a
`artifacts/01-10` -- ver docstring del modulo bajo prueba."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts.interprocedural_call_linkage import (
    InterproceduralSourceReference,
)
from altamira_extractor.contracts.interprocedural_propagation import (
    InterproceduralFactKind,
    InterproceduralProgramAnalysis,
    InterproceduralPropagationArtifact,
    InterproceduralPropagationBarrier,
    InterproceduralPropagationDirection,
    InterproceduralPropagationFact,
    InterproceduralPropagationStatus,
    InterproceduralPropagationSummary,
)

VALID_HASH = "c" * 64


def make_source_reference(**overrides: object) -> InterproceduralSourceReference:
    fields: dict[str, object] = {"program": "CALLER", "paragraph": "MAIN"}
    fields.update(overrides)
    return InterproceduralSourceReference(**fields)  # type: ignore[arg-type]


def make_entry_fact(**overrides: object) -> InterproceduralPropagationFact:
    fields: dict[str, object] = {
        "fact_id": "fact::callsite::x::entry::1",
        "call_site_id": "callsite::x",
        "caller_program": "CALLER",
        "callee_program": "CALLEE",
        "caller_statement_id": "CALLER::MAIN::0::CALL",
        "binding_id": "binding::callsite::x::1",
        "direction": InterproceduralPropagationDirection.CALLER_TO_CALLEE,
        "kind": InterproceduralFactKind.ENTRY_FACT,
        "status": InterproceduralPropagationStatus.PROPAGATED,
        "actual_name": "WS-A",
        "formal_name": "LK-A",
        "literal": "0005",
        "source_references": [make_source_reference()],
    }
    fields.update(overrides)
    return InterproceduralPropagationFact(**fields)  # type: ignore[arg-type]


def make_blocked_fact(**overrides: object) -> InterproceduralPropagationFact:
    fields: dict[str, object] = {
        "fact_id": "fact::callsite::x::entry::1",
        "call_site_id": "callsite::x",
        "caller_program": "CALLER",
        "callee_program": None,
        "caller_statement_id": "CALLER::MAIN::0::CALL",
        "direction": InterproceduralPropagationDirection.CALLER_TO_CALLEE,
        "kind": InterproceduralFactKind.ENTRY_FACT,
        "status": InterproceduralPropagationStatus.BLOCKED,
        "barriers": [InterproceduralPropagationBarrier.DYNAMIC_CALL],
        "diagnostics": ["BLOCKED_DYNAMIC_CALL"],
    }
    fields.update(overrides)
    return InterproceduralPropagationFact(**fields)  # type: ignore[arg-type]


def make_invalidation_fact(**overrides: object) -> InterproceduralPropagationFact:
    fields: dict[str, object] = {
        "fact_id": "fact::callsite::x::return::1",
        "call_site_id": "callsite::x",
        "caller_program": "CALLER",
        "callee_program": "CALLEE",
        "caller_statement_id": "CALLER::MAIN::0::CALL",
        "direction": InterproceduralPropagationDirection.CALLEE_TO_CALLER,
        "kind": InterproceduralFactKind.INVALIDATION,
        "status": InterproceduralPropagationStatus.INVALIDATED,
        "actual_name": "WS-A",
        "formal_name": "LK-A",
    }
    fields.update(overrides)
    return InterproceduralPropagationFact(**fields)  # type: ignore[arg-type]


# --- InterproceduralPropagationFact: coherencia literal/status ------------------


def test_propagated_requires_literal() -> None:
    with pytest.raises(ValidationError, match="PROPAGATED exige literal"):
        make_entry_fact(literal=None)


def test_non_propagated_forbids_literal() -> None:
    with pytest.raises(ValidationError, match="no puede declarar literal"):
        make_blocked_fact(literal="0005")


def test_propagated_entry_fact_round_trips() -> None:
    fact = make_entry_fact()
    restored = InterproceduralPropagationFact.model_validate_json(fact.to_stable_json())
    assert restored == fact


# --- coherencia kind/status/direction --------------------------------------------


def test_invalidated_status_requires_invalidation_kind() -> None:
    with pytest.raises(ValidationError, match="INVALIDATED exige kind=INVALIDATION"):
        make_invalidation_fact(kind=InterproceduralFactKind.RETURNING_FACT)


def test_invalidation_kind_requires_invalidated_status() -> None:
    with pytest.raises(ValidationError, match="INVALIDATION exige status=INVALIDATED"):
        make_invalidation_fact(status=InterproceduralPropagationStatus.UNRESOLVED)


def test_entry_fact_kind_requires_caller_to_callee_direction() -> None:
    with pytest.raises(ValidationError, match="ENTRY_FACT exige direction=CALLER_TO_CALLEE"):
        make_entry_fact(direction=InterproceduralPropagationDirection.CALLEE_TO_CALLER)


def test_returning_fact_kind_requires_callee_to_caller_direction() -> None:
    with pytest.raises(ValidationError, match="RETURNING_FACT exige direction=CALLEE_TO_CALLER"):
        make_entry_fact(
            kind=InterproceduralFactKind.RETURNING_FACT,
            direction=InterproceduralPropagationDirection.CALLER_TO_CALLEE,
        )


def test_by_reference_output_kind_requires_callee_to_caller_direction() -> None:
    fact = InterproceduralPropagationFact(
        fact_id="fact::callsite::x::return::1",
        call_site_id="callsite::x",
        caller_program="CALLER",
        callee_program="CALLEE",
        caller_statement_id="CALLER::MAIN::0::CALL",
        direction=InterproceduralPropagationDirection.CALLEE_TO_CALLER,
        kind=InterproceduralFactKind.BY_REFERENCE_OUTPUT,
        status=InterproceduralPropagationStatus.PROPAGATED,
        literal="0005",
    )
    assert fact.kind == InterproceduralFactKind.BY_REFERENCE_OUTPUT


# --- barriers solo cuando BLOCKED ------------------------------------------------


def test_blocked_status_requires_at_least_one_barrier() -> None:
    with pytest.raises(ValidationError, match="BLOCKED exige al menos un barrier"):
        make_blocked_fact(barriers=[])


def test_non_blocked_status_forbids_barriers() -> None:
    with pytest.raises(ValidationError, match="solo status=BLOCKED puede declarar barriers"):
        make_entry_fact(barriers=[InterproceduralPropagationBarrier.DYNAMIC_CALL])


def test_blocked_fact_round_trips() -> None:
    fact = make_blocked_fact()
    restored = InterproceduralPropagationFact.model_validate_json(fact.to_stable_json())
    assert restored == fact


# --- listas ordenadas y sin duplicados -------------------------------------------


def test_source_fact_ids_reject_unsorted() -> None:
    with pytest.raises(ValidationError, match="source_fact_ids"):
        make_entry_fact(source_fact_ids=["fact::z", "fact::a"])


def test_diagnostics_reject_duplicates() -> None:
    with pytest.raises(ValidationError, match="diagnostics"):
        make_entry_fact(diagnostics=["CODE", "CODE"])


def test_barriers_reject_unsorted() -> None:
    with pytest.raises(ValidationError, match="barriers"):
        make_blocked_fact(
            barriers=[
                InterproceduralPropagationBarrier.UNRESOLVED_FORMAL,
                InterproceduralPropagationBarrier.DYNAMIC_CALL,
            ]
        )


# --- InterproceduralProgramAnalysis ----------------------------------------------


def test_entry_facts_must_all_be_caller_to_callee() -> None:
    # Un fact internamente valido (kind=RETURNING_FACT exige
    # direction=CALLEE_TO_CALLER, ya satisfecho) colocado en la lista
    # equivocada (`entry_facts`, que solo admite CALLER_TO_CALLEE):
    # prueba el validador de `InterproceduralProgramAnalysis` en
    # aislamiento, nunca el de `InterproceduralPropagationFact` (ese ya
    # se prueba por separado).
    returning_fact = make_entry_fact(
        fact_id="fact::callsite::x::return::returning",
        kind=InterproceduralFactKind.RETURNING_FACT,
        direction=InterproceduralPropagationDirection.CALLEE_TO_CALLER,
    )
    with pytest.raises(ValidationError, match="entry_facts solo admite direction=CALLER_TO_CALLEE"):
        InterproceduralProgramAnalysis(program="CALLEE", entry_facts=[returning_fact])


def test_exit_facts_must_all_be_callee_to_caller() -> None:
    with pytest.raises(ValidationError, match="exit_facts solo admite direction=CALLEE_TO_CALLER"):
        InterproceduralProgramAnalysis(program="CALLEE", exit_facts=[make_entry_fact()])


def test_program_analysis_round_trips() -> None:
    pa = InterproceduralProgramAnalysis(
        program="CALLEE",
        entry_facts=[make_entry_fact()],
        exit_facts=[make_invalidation_fact()],
        blocked_call_sites=["callsite::y"],
        diagnostics=["BLOCKED_CALL_SITES_INCLUDE_DYNAMIC_CALL"],
    )
    restored = InterproceduralProgramAnalysis.model_validate_json(pa.to_stable_json())
    assert restored == pa


def test_program_analysis_with_no_facts_is_valid() -> None:
    pa = InterproceduralProgramAnalysis(program="ISOLATED")
    assert pa.entry_facts == []
    assert pa.exit_facts == []
    assert pa.blocked_call_sites == []


# --- InterproceduralPropagationSummary -------------------------------------------


def _summary(**overrides: object) -> InterproceduralPropagationSummary:
    fields: dict[str, object] = {
        "program_count": 1,
        "call_site_count": 1,
        "eligible_call_count": 1,
        "propagated_call_count": 1,
        "blocked_call_count": 0,
        "entry_fact_count": 1,
        "returning_fact_count": 0,
        "by_reference_output_count": 0,
        "invalidation_count": 0,
        "counts_by_status": {InterproceduralPropagationStatus.PROPAGATED: 1},
        "counts_by_barrier": {},
    }
    fields.update(overrides)
    return InterproceduralPropagationSummary(**fields)  # type: ignore[arg-type]


def test_summary_call_site_partition() -> None:
    with pytest.raises(ValidationError, match="eligible_call_count \\+ blocked_call_count"):
        _summary(call_site_count=5)


def test_summary_propagated_cannot_exceed_eligible() -> None:
    with pytest.raises(ValidationError, match="propagated_call_count no puede superar"):
        _summary(propagated_call_count=2, eligible_call_count=1, call_site_count=1)


def test_summary_kind_counts_must_match_status_sum() -> None:
    with pytest.raises(ValidationError, match="no coincide con la suma de counts_by_status"):
        _summary(entry_fact_count=5)


def test_summary_zero_everything_round_trips() -> None:
    summary = InterproceduralPropagationSummary(
        program_count=1,
        call_site_count=0,
        eligible_call_count=0,
        propagated_call_count=0,
        blocked_call_count=0,
        entry_fact_count=0,
        returning_fact_count=0,
        by_reference_output_count=0,
        invalidation_count=0,
        counts_by_status={},
        counts_by_barrier={},
    )
    first = summary.to_stable_json()
    second = InterproceduralPropagationSummary.model_validate_json(first).to_stable_json()
    assert first == second


# --- InterproceduralPropagationArtifact ------------------------------------------


def _artifact(**overrides: object) -> InterproceduralPropagationArtifact:
    entry = make_entry_fact()
    pa = InterproceduralProgramAnalysis(program="CALLEE", entry_facts=[entry])
    fields: dict[str, object] = {
        "run_id": "run-1",
        "source_package_hash": VALID_HASH,
        "source_artifact_hashes": {"artifacts/02-canonical": VALID_HASH},
        "interprocedural_analysis_schema_version": "1.0",
        "interprocedural_analysis_analyzer_version": "1.0",
        "semantic_effects_schema_version": "1.2",
        "semantic_propagation_schema_version": "1.1",
        "summary": _summary(),
        "program_analyses": [pa],
        "facts": [entry],
    }
    fields.update(overrides)
    return InterproceduralPropagationArtifact(**fields)  # type: ignore[arg-type]


def test_artifact_round_trips() -> None:
    artifact = _artifact()
    restored = InterproceduralPropagationArtifact.model_validate_json(artifact.to_stable_json())
    assert restored == artifact


def test_artifact_rejects_additional_properties() -> None:
    payload = _artifact().model_dump(mode="json")
    payload["unexpected_field"] = "not allowed"
    with pytest.raises(ValidationError):
        InterproceduralPropagationArtifact.model_validate(payload)


def test_artifact_schema_and_analyzer_version_default_to_1_0() -> None:
    artifact = _artifact()
    assert artifact.schema_version == "1.0"
    assert artifact.analyzer_version == "1.0"


def test_artifact_accepts_semantic_effects_1_0_and_1_1_and_1_2() -> None:
    for version in ("1.0", "1.1", "1.2"):
        artifact = _artifact(semantic_effects_schema_version=version)
        assert artifact.semantic_effects_schema_version == version


def test_artifact_rejects_unknown_semantic_effects_version() -> None:
    with pytest.raises(ValidationError):
        _artifact(semantic_effects_schema_version="2.0")


def test_artifact_accepts_semantic_propagation_1_0_and_1_1() -> None:
    for version in ("1.0", "1.1"):
        artifact = _artifact(semantic_propagation_schema_version=version)
        assert artifact.semantic_propagation_schema_version == version


def test_artifact_facts_must_match_program_analyses_union() -> None:
    entry = make_entry_fact()
    other_fact = make_entry_fact(fact_id="fact::callsite::y::entry::1")
    pa = InterproceduralProgramAnalysis(program="CALLEE", entry_facts=[entry])
    with pytest.raises(ValidationError, match="union ordenada"):
        _artifact(program_analyses=[pa], facts=[entry, other_fact])


def test_artifact_program_analyses_reject_duplicate_program() -> None:
    pa = InterproceduralProgramAnalysis(program="CALLEE")
    with pytest.raises(ValidationError, match="program duplicado"):
        _artifact(program_analyses=[pa, pa], facts=[])


def test_artifact_program_analyses_reject_unsorted() -> None:
    pa_b = InterproceduralProgramAnalysis(program="PROGB")
    pa_a = InterproceduralProgramAnalysis(program="PROGA")
    with pytest.raises(ValidationError, match="ordenado deterministicamente por program"):
        _artifact(program_analyses=[pa_b, pa_a], facts=[])


def test_artifact_facts_reject_unsorted() -> None:
    fact_a = make_entry_fact(fact_id="fact::a")
    fact_z = make_entry_fact(fact_id="fact::z")
    pa = InterproceduralProgramAnalysis(program="CALLEE", entry_facts=[fact_a, fact_z])
    with pytest.raises(ValidationError, match="no esta ordenado deterministicamente"):
        _artifact(program_analyses=[pa], facts=[fact_z, fact_a])


def test_artifact_summary_program_count_mismatch_is_rejected() -> None:
    with pytest.raises(ValidationError, match="summary.program_count"):
        _artifact(summary=_summary(program_count=99))


def test_artifact_blocked_and_eligible_call_sites_cannot_overlap() -> None:
    entry = make_entry_fact()
    pa = InterproceduralProgramAnalysis(
        program="CALLEE", entry_facts=[entry], blocked_call_sites=["callsite::x"]
    )
    with pytest.raises(ValidationError, match="presente tanto en blocked_call_sites como en facts"):
        _artifact(program_analyses=[pa], facts=[entry])


def test_artifact_no_facts_no_program_analyses_round_trips() -> None:
    summary = InterproceduralPropagationSummary(
        program_count=0,
        call_site_count=0,
        eligible_call_count=0,
        propagated_call_count=0,
        blocked_call_count=0,
        entry_fact_count=0,
        returning_fact_count=0,
        by_reference_output_count=0,
        invalidation_count=0,
        counts_by_status={},
        counts_by_barrier={},
    )
    artifact = InterproceduralPropagationArtifact(
        run_id="run-1",
        source_package_hash=VALID_HASH,
        source_artifact_hashes={"artifacts/02-canonical": VALID_HASH},
        interprocedural_analysis_schema_version="1.0",
        interprocedural_analysis_analyzer_version="1.0",
        semantic_effects_schema_version="1.2",
        semantic_propagation_schema_version="1.1",
        summary=summary,
        program_analyses=[],
        facts=[],
    )
    first = artifact.to_stable_json()
    second = InterproceduralPropagationArtifact.model_validate_json(first).to_stable_json()
    assert first == second


def test_artifact_has_no_timestamp_field() -> None:
    fields = InterproceduralPropagationArtifact.model_fields
    assert not any("time" in name or "date" in name for name in fields)


def test_artifact_source_package_hash_must_be_sha256_hex() -> None:
    with pytest.raises(ValidationError):
        _artifact(source_package_hash="not-a-hash")
