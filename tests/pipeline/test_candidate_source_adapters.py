"""Tests de los adaptadores de fuente (Fase 9, `feat/unified-
candidate-promotion-assessment`). Items 1-9 de los 50 tests
obligatorios: adaptacion V1/V2/interprocedural, fuente ausente
(produce `[]`), mapping de familia conocida/desconocida -- mas
no-mutacion y determinismo."""

from __future__ import annotations

import copy

from altamira_extractor.contracts.candidate_promotion_assessment import (
    CandidateSource,
    UnifiedRuleFamily,
)
from altamira_extractor.contracts.v2_shadow_candidates import V2RuleType
from altamira_extractor.pipeline.candidate_source_adapters import (
    adapt_interprocedural_candidates,
    adapt_v1_candidates,
    adapt_v2_candidates,
    unified_reference_id_for,
)

from .candidate_promotion_assessment_helpers import (
    HASH,
    interprocedural_artifact,
    interprocedural_candidate,
    v1_artifact,
    v1_candidate,
    v2_artifact,
    v2_candidate,
)


def test_adapt_v1_candidates_preserves_identity_and_maps_return_code_family() -> None:
    artifact = v1_artifact([v1_candidate(candidate_id="candidate::1", outcome_code="R001")])
    references = adapt_v1_candidates(artifact, source_artifact_hash=HASH)
    assert len(references) == 1
    reference = references[0]
    assert reference.source == CandidateSource.V1
    assert reference.source_candidate_id == "candidate::1"
    assert reference.rule_family == UnifiedRuleFamily.RETURN_CODE
    assert reference.output_literal == "R001"
    assert reference.call_site_id is None
    assert reference.input_literal is None


def test_adapt_v2_candidates_preserves_identity_and_maps_known_families() -> None:
    artifact = v2_artifact(
        candidates=[
            v2_candidate(
                candidate_id="v2::a",
                rule_type=V2RuleType.RETURN_CODE_RULE,
                target_variable="WS-RC",
                resolved_literal="R001",
            )
        ]
    )
    references = adapt_v2_candidates(artifact, source_artifact_hash=HASH)
    assert len(references) == 1
    reference = references[0]
    assert reference.source == CandidateSource.V2
    assert reference.source_candidate_id == "v2::a"
    assert reference.rule_family == UnifiedRuleFamily.RETURN_CODE
    assert reference.target == "WS-RC"
    assert reference.output_literal == "R001"


def test_adapt_interprocedural_candidates_preserves_identity_and_call_site() -> None:
    artifact = interprocedural_artifact(
        candidates=[
            interprocedural_candidate(
                candidate_id="ipr::a", target="WS-X", output_literal="R001"
            )
        ]
    )
    references = adapt_interprocedural_candidates(artifact, source_artifact_hash=HASH)
    assert len(references) == 1
    reference = references[0]
    assert reference.source == CandidateSource.INTERPROCEDURAL
    assert reference.source_candidate_id == "ipr::a"
    assert reference.call_site_id == "callsite::x"
    assert reference.target == "WS-X"
    assert reference.output_literal == "R001"
    assert any(p.startswith("callee_program::") for p in reference.provenance_references)


def test_v1_source_absent_produces_empty_list() -> None:
    assert adapt_v1_candidates(None, source_artifact_hash=HASH) == []


def test_v2_source_absent_produces_empty_list() -> None:
    assert adapt_v2_candidates(None, source_artifact_hash=HASH) == []


def test_interprocedural_source_absent_produces_empty_list() -> None:
    assert adapt_interprocedural_candidates(None, source_artifact_hash=HASH) == []


def test_v2_blocked_candidate_diagnostic_codes_become_barrier_codes() -> None:
    from altamira_extractor.contracts.v2_shadow_candidates import V2CandidateSupport

    artifact = v2_artifact(
        candidates=[
            v2_candidate(
                candidate_id="v2::blocked",
                support=V2CandidateSupport.BLOCKED,
                diagnostic_codes=["V2_BLOCKED_EXAMPLE"],
            )
        ]
    )
    references = adapt_v2_candidates(artifact, source_artifact_hash=HASH)
    assert references[0].barrier_codes == ["V2_BLOCKED_EXAMPLE"]
    assert references[0].diagnostics == []


def test_known_family_mapping_return_code_and_level_88() -> None:
    from altamira_extractor.contracts.v2_shadow_candidates import V2CandidateSupport

    artifact = v2_artifact(
        candidates=[
            v2_candidate(
                candidate_id="v2::level88",
                rule_type=V2RuleType.LEVEL_88_RETURN_CODE_RULE,
                support=V2CandidateSupport.DETERMINISTIC,
            )
        ]
    )
    references = adapt_v2_candidates(artifact, source_artifact_hash=HASH)
    assert references[0].rule_family == UnifiedRuleFamily.LEVEL_88_RETURN_CODE


def test_unknown_family_mapping_state_change_rule_never_maps_to_state_transition() -> None:
    """`V2RuleType.STATE_CHANGE_RULE` (intraprograma, siempre PARTIAL) NUNCA
    se mapea a `STATE_TRANSITION` (interprocedural, Fase 8) pese a la
    semejanza textual del nombre -- son conceptos estructuralmente
    distintos, ver docstring de `_V2_RULE_FAMILY_BY_TYPE`."""
    from altamira_extractor.contracts.v2_shadow_candidates import V2CandidateSupport

    artifact = v2_artifact(
        candidates=[
            v2_candidate(
                candidate_id="v2::state",
                rule_type=V2RuleType.STATE_CHANGE_RULE,
                support=V2CandidateSupport.PARTIAL,
                resolved_literal=None,
            )
        ]
    )
    references = adapt_v2_candidates(artifact, source_artifact_hash=HASH)
    assert references[0].rule_family == UnifiedRuleFamily.UNKNOWN


def test_adapters_never_mutate_input_artifacts() -> None:
    v1a = v1_artifact([v1_candidate()])
    v2a = v2_artifact(candidates=[v2_candidate()])
    ipa = interprocedural_artifact(candidates=[interprocedural_candidate()])

    v1_before = copy.deepcopy(v1a.model_dump())
    v2_before = copy.deepcopy(v2a.model_dump())
    ip_before = copy.deepcopy(ipa.model_dump())

    adapt_v1_candidates(v1a, source_artifact_hash=HASH)
    adapt_v2_candidates(v2a, source_artifact_hash=HASH)
    adapt_interprocedural_candidates(ipa, source_artifact_hash=HASH)

    assert v1a.model_dump() == v1_before
    assert v2a.model_dump() == v2_before
    assert ipa.model_dump() == ip_before


def test_adapters_are_deterministic() -> None:
    v1a = v1_artifact([v1_candidate()])
    v2a = v2_artifact(candidates=[v2_candidate()])
    ipa = interprocedural_artifact(candidates=[interprocedural_candidate()])

    refs_1 = (
        adapt_v1_candidates(v1a, source_artifact_hash=HASH)
        + adapt_v2_candidates(v2a, source_artifact_hash=HASH)
        + adapt_interprocedural_candidates(ipa, source_artifact_hash=HASH)
    )
    refs_2 = (
        adapt_v1_candidates(v1a, source_artifact_hash=HASH)
        + adapt_v2_candidates(v2a, source_artifact_hash=HASH)
        + adapt_interprocedural_candidates(ipa, source_artifact_hash=HASH)
    )
    assert [r.model_dump_json() for r in refs_1] == [r.model_dump_json() for r in refs_2]


def test_unified_reference_id_for_is_deterministic_and_source_scoped() -> None:
    id_1 = unified_reference_id_for(source=CandidateSource.V1, source_candidate_id="c1")
    id_2 = unified_reference_id_for(source=CandidateSource.V1, source_candidate_id="c1")
    id_3 = unified_reference_id_for(source=CandidateSource.V2, source_candidate_id="c1")
    assert id_1 == id_2
    assert id_1 != id_3
