"""Round-trip generico modelo -> JSON -> modelo y determinismo de la
serializacion, sobre varios contratos representativos."""

from __future__ import annotations

import json

from altamira_extractor.contracts import (
    AltamiraBaseModel,
    ContextPackage,
    Manifest,
    RuleDraft,
    SemanticGraph,
)


def _assert_round_trips(model: AltamiraBaseModel) -> None:
    dumped = model.to_stable_json()
    restored = type(model).model_validate_json(dumped)
    assert restored == model


def test_manifest_round_trip(valid_manifest: Manifest) -> None:
    _assert_round_trips(valid_manifest)


def test_semantic_graph_round_trip(valid_semantic_graph: SemanticGraph) -> None:
    _assert_round_trips(valid_semantic_graph)


def test_context_package_round_trip(valid_context_package: ContextPackage) -> None:
    _assert_round_trips(valid_context_package)


def test_rule_draft_round_trip(valid_rule_draft: RuleDraft) -> None:
    _assert_round_trips(valid_rule_draft)


def test_serialization_is_deterministic_across_calls(valid_context_package: ContextPackage) -> None:
    first = valid_context_package.to_stable_json()
    second = valid_context_package.to_stable_json()
    assert first == second


def test_serialization_is_utf8_and_sorted(valid_context_package: ContextPackage) -> None:
    payload = valid_context_package.to_stable_json()
    payload.encode("utf-8")  # no debe lanzar

    parsed = json.loads(payload)
    # las claves de nivel superior deben venir ordenadas alfabeticamente
    top_level_keys = list(parsed.keys())
    assert top_level_keys == sorted(top_level_keys)


def test_serialization_is_human_readable(valid_rule_draft: RuleDraft) -> None:
    payload = valid_rule_draft.to_stable_json()
    assert "\n" in payload
    assert payload.endswith("\n")
