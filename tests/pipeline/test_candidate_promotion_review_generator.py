"""Tests del generador puro del paquete de revision humana (Fase 10,
`feat/controlled-candidate-promotion-plan`). Items 1-10 de los 55 tests
obligatorios: mapping de eligibility por disposition, summary
reconciliado, IDs deterministicos, no-mutacion del assessment."""

from __future__ import annotations

import copy

from altamira_extractor.contracts.candidate_promotion_assessment import (
    CandidateSource,
    PromotionDisposition,
)
from altamira_extractor.contracts.candidate_promotion_review import ReviewEligibility
from altamira_extractor.pipeline.candidate_promotion_review_generator import (
    generate_candidate_promotion_review_package,
    review_item_id_for,
)

from .candidate_promotion_review_helpers import single_disposition_artifact


def test_baseline_v1_maps_to_baseline_eligibility() -> None:
    artifact = single_disposition_artifact(PromotionDisposition.BASELINE_V1)
    package = generate_candidate_promotion_review_package(artifact)
    item = next(i for i in package.review_items if i.source == CandidateSource.V1)
    assert item.eligibility == ReviewEligibility.BASELINE


def test_already_covered_maps_to_already_covered_eligibility() -> None:
    artifact = single_disposition_artifact(PromotionDisposition.ALREADY_COVERED)
    package = generate_candidate_promotion_review_package(artifact)
    item = next(
        i for i in package.review_items if i.disposition == PromotionDisposition.ALREADY_COVERED
    )
    assert item.eligibility == ReviewEligibility.ALREADY_COVERED


def test_ready_maps_to_eligible() -> None:
    artifact = single_disposition_artifact(PromotionDisposition.READY_FOR_CONTROLLED_REVIEW)
    package = generate_candidate_promotion_review_package(artifact)
    assert package.review_items[0].eligibility == ReviewEligibility.ELIGIBLE


def test_review_required_maps_to_not_eligible() -> None:
    artifact = single_disposition_artifact(PromotionDisposition.REVIEW_REQUIRED)
    package = generate_candidate_promotion_review_package(artifact)
    assert package.review_items[0].eligibility == ReviewEligibility.NOT_ELIGIBLE


def test_blocked_maps_to_blocked_eligibility() -> None:
    artifact = single_disposition_artifact(PromotionDisposition.BLOCKED)
    package = generate_candidate_promotion_review_package(artifact)
    assert package.review_items[0].eligibility == ReviewEligibility.BLOCKED


def test_conflicting_maps_to_blocked_eligibility() -> None:
    artifact = single_disposition_artifact(PromotionDisposition.CONFLICTING)
    package = generate_candidate_promotion_review_package(artifact)
    assert package.review_items[0].eligibility == ReviewEligibility.BLOCKED


def test_not_evaluated_maps_to_not_eligible() -> None:
    artifact = single_disposition_artifact(PromotionDisposition.NOT_EVALUATED)
    package = generate_candidate_promotion_review_package(artifact)
    assert package.review_items[0].eligibility == ReviewEligibility.NOT_ELIGIBLE


def test_package_summary_is_reconciled() -> None:
    artifact = single_disposition_artifact(PromotionDisposition.ALREADY_COVERED)
    package = generate_candidate_promotion_review_package(artifact)
    assert package.summary.total_items == len(package.review_items)
    assert package.summary.already_covered_count == 1
    assert package.summary.baseline_count == 1


def test_review_item_ids_are_deterministic() -> None:
    artifact = single_disposition_artifact(PromotionDisposition.READY_FOR_CONTROLLED_REVIEW)
    package_1 = generate_candidate_promotion_review_package(artifact)
    package_2 = generate_candidate_promotion_review_package(artifact)
    ids_1 = [item.review_item_id for item in package_1.review_items]
    ids_2 = [item.review_item_id for item in package_2.review_items]
    assert ids_1 == ids_2
    assert package_1.model_dump_json() == package_2.model_dump_json()


def test_review_item_id_for_is_deterministic_and_scoped_to_reference() -> None:
    id_1 = review_item_id_for("unified::v1::a")
    id_2 = review_item_id_for("unified::v1::a")
    id_3 = review_item_id_for("unified::v1::b")
    assert id_1 == id_2
    assert id_1 != id_3


def test_generator_never_mutates_assessment() -> None:
    artifact = single_disposition_artifact(PromotionDisposition.READY_FOR_CONTROLLED_REVIEW)
    before = copy.deepcopy(artifact.model_dump())
    generate_candidate_promotion_review_package(artifact)
    assert artifact.model_dump() == before


def test_review_items_preserve_criteria_from_source_assessment() -> None:
    artifact = single_disposition_artifact(PromotionDisposition.BLOCKED)
    package = generate_candidate_promotion_review_package(artifact)
    item = package.review_items[0]
    source_assessment = artifact.assessments[0]
    assert [c.model_dump() for c in item.criteria] == [
        c.model_dump() for c in source_assessment.criteria
    ]
