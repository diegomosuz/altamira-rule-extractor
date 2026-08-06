"""Tests contractuales del ground truth funcional versionado (Fase
15B2-A, Parte E): `contracts/functional_ground_truth.py`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts.functional_ground_truth import (
    FunctionalGroundTruthSet,
    FunctionalGroundTruthSummary,
    GroundTruthCase,
    GroundTruthCaseKind,
    GroundTruthExpectedRule,
    GroundTruthFixtureReference,
)

_HASH = "a" * 64


def _expected_rule(**overrides: object) -> GroundTruthExpectedRule:
    defaults: dict[str, object] = {
        "expectation_id": "case-1::e1",
        "rule_family": "RETURN_CODE",
        "paragraph": "MAIN-PARA",
        "minimum_count": 1,
        "derivation_notes": "derivado del codigo real del detector.",
    }
    defaults.update(overrides)
    return GroundTruthExpectedRule(**defaults)  # type: ignore[arg-type]


def _fixture(**overrides: object) -> GroundTruthFixtureReference:
    defaults: dict[str, object] = {
        "relative_path": "config/ground_truth/fixtures/x.cbl",
        "sha256": _HASH,
    }
    defaults.update(overrides)
    return GroundTruthFixtureReference(**defaults)  # type: ignore[arg-type]


def _positive_case(**overrides: object) -> GroundTruthCase:
    defaults: dict[str, object] = {
        "case_id": "case-1",
        "kind": GroundTruthCaseKind.POSITIVE,
        "program": "PROG1",
        "fixtures": [_fixture()],
        "description": "descripcion de prueba",
        "expected_rules": [_expected_rule()],
    }
    defaults.update(overrides)
    return GroundTruthCase(**defaults)  # type: ignore[arg-type]


def test_positive_case_round_trips() -> None:
    case = _positive_case()
    assert case.kind == GroundTruthCaseKind.POSITIVE
    assert len(case.expected_rules) == 1


def test_negative_case_requires_empty_expected_rules() -> None:
    with pytest.raises(ValidationError, match="NEGATIVE"):
        _positive_case(kind=GroundTruthCaseKind.NEGATIVE)


def test_positive_case_requires_at_least_one_expected_rule() -> None:
    with pytest.raises(ValidationError, match="POSITIVE"):
        _positive_case(expected_rules=[])


def test_negative_case_without_expected_rules_accepted() -> None:
    case = _positive_case(kind=GroundTruthCaseKind.NEGATIVE, expected_rules=[])
    assert case.expected_rules == []


def test_fixture_outside_ground_truth_dir_rejected() -> None:
    with pytest.raises(ValidationError, match="config/ground_truth/fixtures"):
        _positive_case(fixtures=[_fixture(relative_path="parser/src/test/resources/fixtures/x.cbl")])


def test_fixtures_requires_at_least_one() -> None:
    with pytest.raises(ValidationError):
        _positive_case(fixtures=[])


def test_fixtures_out_of_order_rejected() -> None:
    with pytest.raises(ValidationError, match="ordenado"):
        _positive_case(
            fixtures=[
                _fixture(relative_path="config/ground_truth/fixtures/z.cbl"),
                _fixture(relative_path="config/ground_truth/fixtures/a.cbl"),
            ]
        )


def test_fixtures_duplicate_path_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicado"):
        _positive_case(fixtures=[_fixture(), _fixture()])


def test_multiple_fixtures_accepted_for_interprocedural_case() -> None:
    case = _positive_case(
        fixtures=[
            _fixture(relative_path="config/ground_truth/fixtures/a.cbl"),
            _fixture(relative_path="config/ground_truth/fixtures/b.cbl"),
        ]
    )
    assert len(case.fixtures) == 2


def test_expected_rules_out_of_order_rejected() -> None:
    with pytest.raises(ValidationError, match="ordenado"):
        _positive_case(
            expected_rules=[
                _expected_rule(expectation_id="z"),
                _expected_rule(expectation_id="a"),
            ]
        )


def test_set_rejects_duplicate_case_ids() -> None:
    case = _positive_case()
    with pytest.raises(ValidationError, match="duplicado"):
        FunctionalGroundTruthSet(
            catalog_edition="edition-1",
            cases=[case, case],
            summary=FunctionalGroundTruthSummary(
                case_count=2, positive_case_count=2, negative_case_count=0, expected_rule_count=2
            ),
        )


def test_set_summary_must_match_case_counts() -> None:
    case = _positive_case()
    bad_summary = FunctionalGroundTruthSummary(
        case_count=1, positive_case_count=0, negative_case_count=0, expected_rule_count=1
    )
    with pytest.raises(ValidationError, match="positive_case_count"):
        FunctionalGroundTruthSet(catalog_edition="edition-1", cases=[case], summary=bad_summary)


def test_set_valid_round_trips_json() -> None:
    case = _positive_case()
    gt = FunctionalGroundTruthSet(
        catalog_edition="edition-1",
        cases=[case],
        summary=FunctionalGroundTruthSummary(
            case_count=1, positive_case_count=1, negative_case_count=0, expected_rule_count=1
        ),
    )
    reloaded = FunctionalGroundTruthSet.model_validate_json(gt.to_stable_json())
    assert reloaded == gt


def test_set_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        FunctionalGroundTruthSet.model_validate(
            {
                "catalog_edition": "edition-1",
                "cases": [],
                "summary": {
                    "case_count": 0,
                    "positive_case_count": 0,
                    "negative_case_count": 0,
                    "expected_rule_count": 0,
                },
                "unexpected_field": "boom",
            }
        )
