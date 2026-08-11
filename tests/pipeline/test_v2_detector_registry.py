"""Tests del registry deterministico de detectores V2 (Fase 3 de la
ampliacion semantica, `feat/v2-detectors-shadow-mode`):
`pipeline/v2_detector_registry.py`."""

from __future__ import annotations

from altamira_extractor.contracts.v2_shadow_candidates import V2RuleType
from altamira_extractor.pipeline.v2_detector_registry import (
    V2_DETECTOR_REGISTRY,
    ordered_detector_ids,
)
from altamira_extractor.pipeline.v2_detectors import (
    DETECTOR_ID_CALCULATION,
    DETECTOR_ID_LEVEL_88_RETURN_CODE,
    DETECTOR_ID_RETURN_CODE_PROPAGATION,
    DETECTOR_ID_STATE_CHANGE,
    DETECTOR_VERSION_CALCULATION,
    DETECTOR_VERSION_LEVEL_88_RETURN_CODE,
    DETECTOR_VERSION_RETURN_CODE_PROPAGATION,
    DETECTOR_VERSION_STATE_CHANGE,
    detect_calculation,
    detect_level_88_return_code,
    detect_return_code_propagation,
    detect_state_change,
)


def test_registry_has_exactly_four_detectors() -> None:
    assert set(V2_DETECTOR_REGISTRY) == {
        DETECTOR_ID_RETURN_CODE_PROPAGATION,
        DETECTOR_ID_LEVEL_88_RETURN_CODE,
        DETECTOR_ID_STATE_CHANGE,
        DETECTOR_ID_CALCULATION,
    }


def test_ordered_detector_ids_is_alphabetically_sorted() -> None:
    assert ordered_detector_ids() == sorted(ordered_detector_ids())
    assert ordered_detector_ids() == [
        DETECTOR_ID_CALCULATION,
        DETECTOR_ID_LEVEL_88_RETURN_CODE,
        DETECTOR_ID_RETURN_CODE_PROPAGATION,
        DETECTOR_ID_STATE_CHANGE,
    ]


def test_ordered_detector_ids_is_deterministic_across_calls() -> None:
    assert ordered_detector_ids() == ordered_detector_ids()


def test_return_code_propagation_definition_wires_correct_callable() -> None:
    definition = V2_DETECTOR_REGISTRY[DETECTOR_ID_RETURN_CODE_PROPAGATION]
    assert definition.callable is detect_return_code_propagation
    assert definition.detector_version == DETECTOR_VERSION_RETURN_CODE_PROPAGATION
    assert definition.rule_type == V2RuleType.RETURN_CODE_RULE


def test_level_88_return_code_definition_wires_correct_callable() -> None:
    definition = V2_DETECTOR_REGISTRY[DETECTOR_ID_LEVEL_88_RETURN_CODE]
    assert definition.callable is detect_level_88_return_code
    assert definition.detector_version == DETECTOR_VERSION_LEVEL_88_RETURN_CODE
    assert definition.rule_type == V2RuleType.LEVEL_88_RETURN_CODE_RULE


def test_state_change_definition_wires_correct_callable() -> None:
    definition = V2_DETECTOR_REGISTRY[DETECTOR_ID_STATE_CHANGE]
    assert definition.callable is detect_state_change
    assert definition.detector_version == DETECTOR_VERSION_STATE_CHANGE
    assert definition.rule_type == V2RuleType.STATE_CHANGE_RULE


def test_calculation_definition_wires_correct_callable() -> None:
    definition = V2_DETECTOR_REGISTRY[DETECTOR_ID_CALCULATION]
    assert definition.callable is detect_calculation
    assert definition.detector_version == DETECTOR_VERSION_CALCULATION
    assert definition.rule_type == V2RuleType.CALCULATION_RULE


def test_every_definition_detector_id_matches_its_registry_key() -> None:
    for key, definition in V2_DETECTOR_REGISTRY.items():
        assert key == definition.detector_id


def test_every_definition_has_non_empty_description() -> None:
    for definition in V2_DETECTOR_REGISTRY.values():
        assert definition.description.strip() != ""
