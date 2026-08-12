"""Tests de los adaptadores de fuente (Fase 9, `feat/unified-
candidate-promotion-assessment`). Items 1-9 de los 50 tests
obligatorios: adaptacion V1/V2/interprocedural, fuente ausente
(produce `[]`), mapping de familia conocida/desconocida -- mas
no-mutacion y determinismo."""

from __future__ import annotations

import copy

import pytest

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


def test_unknown_family_mapping_state_change_rule_without_semantic_tag_lookup() -> None:
    """`V2RuleType.STATE_CHANGE_RULE` (intraprograma, siempre PARTIAL) NUNCA
    se mapea a `STATE_TRANSITION` sin evidencia de relevancia funcional
    (`semantic_tag_by_data_item` ausente/`None`, mismo comportamiento que
    antes de Fase 15B3-C8-FIX-1) -- nunca se adivina por semejanza
    textual del nombre. Con el mapping presente y un tag funcional, ver
    `test_state_change_rule_maps_to_state_transition_when_target_tag_is_functional`."""
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


def test_calculation_rule_maps_to_calculation_family() -> None:
    """Fase 15B3-C8-FIX-1: `V2RuleType.CALCULATION_RULE` estaba ausente
    de `_V2_RULE_FAMILY_BY_TYPE` (omision, nunca una decision deliberada
    -- a diferencia de `STATE_CHANGE_RULE`), cayendo silenciosamente a
    `UNKNOWN` via el fallback de `.get(...)`. Debe comportarse igual
    conceptualmente que `enhanced_candidate_integration._LOCAL_RULE_
    FAMILY_BY_TYPE[CALCULATION_RULE] = CALCULATION` -- sin gate
    adicional (a diferencia de STATE_CHANGE_RULE)."""
    from altamira_extractor.contracts.v2_shadow_candidates import V2CandidateSupport

    artifact = v2_artifact(
        candidates=[
            v2_candidate(
                candidate_id="v2::calc",
                rule_type=V2RuleType.CALCULATION_RULE,
                support=V2CandidateSupport.PARTIAL,
                resolved_literal=None,
            )
        ]
    )
    references = adapt_v2_candidates(artifact, source_artifact_hash=HASH)
    assert references[0].rule_family == UnifiedRuleFamily.CALCULATION


@pytest.mark.parametrize("functional_tag", ["status", "status_flag"])
def test_state_change_rule_maps_to_state_transition_when_target_tag_is_functional(
    functional_tag: str,
) -> None:
    """Replica EXACTAMENTE el gate productivo de
    `enhanced_candidate_integration.py`: `STATE_CHANGE_RULE` se promueve
    a `STATE_TRANSITION` unicamente cuando el `semantic_tag` real del
    target (resuelto por el llamador, nunca inferido aqui) esta en
    `{status, status_flag}`."""
    from altamira_extractor.contracts.v2_shadow_candidates import V2CandidateSupport

    artifact = v2_artifact(
        candidates=[
            v2_candidate(
                candidate_id="v2::state",
                rule_type=V2RuleType.STATE_CHANGE_RULE,
                support=V2CandidateSupport.PARTIAL,
                resolved_literal=None,
                program="PROG1",
                target_variable="WS-ESTADO-OPERACION",
            )
        ]
    )
    references = adapt_v2_candidates(
        artifact,
        source_artifact_hash=HASH,
        semantic_tag_by_data_item={("PROG1", "WS-ESTADO-OPERACION"): functional_tag},
    )
    assert references[0].rule_family == UnifiedRuleFamily.STATE_TRANSITION


def test_state_change_rule_stays_unknown_when_target_tag_nonfunctional() -> None:
    """Un `semantic_tag` real pero fuera de `{status, status_flag}`
    (p. ej. `return_code`) nunca demuestra relevancia de STATE_TRANSITION
    -- permanece `UNKNOWN`, exactamente como el gate productivo."""
    from altamira_extractor.contracts.v2_shadow_candidates import V2CandidateSupport

    artifact = v2_artifact(
        candidates=[
            v2_candidate(
                candidate_id="v2::state",
                rule_type=V2RuleType.STATE_CHANGE_RULE,
                support=V2CandidateSupport.PARTIAL,
                resolved_literal=None,
                program="PROG1",
                target_variable="WS-CONTADOR",
            )
        ]
    )
    references = adapt_v2_candidates(
        artifact,
        source_artifact_hash=HASH,
        semantic_tag_by_data_item={("PROG1", "WS-CONTADOR"): "return_code"},
    )
    assert references[0].rule_family == UnifiedRuleFamily.UNKNOWN


def test_state_change_rule_stays_unknown_when_mapping_has_no_entry_for_target() -> None:
    """`semantic_tag_by_data_item` presente pero SIN entrada para este
    `(program, target_qualified_name)` (DataItem sin semantic_tag
    asignado) se trata igual que ausencia total de informacion --
    `UNKNOWN`, nunca se asume relevancia por falta de contraevidencia."""
    from altamira_extractor.contracts.v2_shadow_candidates import V2CandidateSupport

    artifact = v2_artifact(
        candidates=[
            v2_candidate(
                candidate_id="v2::state",
                rule_type=V2RuleType.STATE_CHANGE_RULE,
                support=V2CandidateSupport.PARTIAL,
                resolved_literal=None,
                program="PROG1",
                target_variable="WS-INDICADOR-PROCESO",
            )
        ]
    )
    references = adapt_v2_candidates(
        artifact,
        source_artifact_hash=HASH,
        semantic_tag_by_data_item={("PROG1", "WS-OTHER-ITEM"): "status"},
    )
    assert references[0].rule_family == UnifiedRuleFamily.UNKNOWN


def test_is_functional_state_transition_tag_direct() -> None:
    from altamira_extractor.pipeline.candidate_source_adapters import (
        is_functional_state_transition_tag,
    )

    assert is_functional_state_transition_tag("status") is True
    assert is_functional_state_transition_tag("status_flag") is True
    assert is_functional_state_transition_tag("return_code") is False
    assert is_functional_state_transition_tag(None) is False


def test_interprocedural_by_reference_rule_maps_to_by_reference_output_family() -> None:
    """Mapping existente (`_INTERPROCEDURAL_RULE_FAMILY_BY_TYPE`) nunca
    tocado por Fase 15B3-C8-FIX-1 -- confirmacion explicita de no
    regresion."""
    from altamira_extractor.contracts.interprocedural_rule_candidates import (
        InterproceduralRuleType,
    )

    artifact = interprocedural_artifact(
        candidates=[
            interprocedural_candidate(
                candidate_id="ipr::by_ref",
                rule_type=InterproceduralRuleType.BY_REFERENCE_RULE,
                target="WS-STATUS",
                output_literal="OK00",
            )
        ]
    )
    references = adapt_interprocedural_candidates(artifact, source_artifact_hash=HASH)
    assert references[0].rule_family == UnifiedRuleFamily.BY_REFERENCE_OUTPUT


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
