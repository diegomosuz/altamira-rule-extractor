"""Tests del registry estatico de detectores de reglas interprocedurales
(Fase 8 de la ampliacion semantica,
`feat/interprocedural-rule-detectors-shadow`):
`pipeline/interprocedural_rule_detector_registry.py`."""

from __future__ import annotations

from altamira_extractor.contracts.interprocedural_rule_candidates import InterproceduralRuleType
from altamira_extractor.pipeline.interprocedural_rule_detector_registry import (
    DETECTOR_ID_BY_REFERENCE,
    DETECTOR_ID_RETURN_CODE,
    DETECTOR_ID_STATE_TRANSITION,
    INTERPROCEDURAL_RULE_DETECTOR_REGISTRY,
    ordered_detector_ids,
)


def test_registry_declares_exactly_the_three_fase8_detectors() -> None:
    assert set(INTERPROCEDURAL_RULE_DETECTOR_REGISTRY) == {
        DETECTOR_ID_RETURN_CODE,
        DETECTOR_ID_BY_REFERENCE,
        DETECTOR_ID_STATE_TRANSITION,
    }


def test_ordered_detector_ids_is_alphabetical_not_insertion_order() -> None:
    assert ordered_detector_ids() == sorted(INTERPROCEDURAL_RULE_DETECTOR_REGISTRY)


def test_each_definition_declares_its_own_rule_type() -> None:
    expected = {
        DETECTOR_ID_RETURN_CODE: InterproceduralRuleType.RETURN_CODE_RULE,
        DETECTOR_ID_BY_REFERENCE: InterproceduralRuleType.BY_REFERENCE_RULE,
        DETECTOR_ID_STATE_TRANSITION: InterproceduralRuleType.STATE_TRANSITION_RULE,
    }
    for detector_id, rule_type in expected.items():
        assert INTERPROCEDURAL_RULE_DETECTOR_REGISTRY[detector_id].rule_type == rule_type
        assert INTERPROCEDURAL_RULE_DETECTOR_REGISTRY[detector_id].detector_id == detector_id
