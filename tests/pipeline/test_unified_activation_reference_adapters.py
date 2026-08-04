"""Tests de los adaptadores V1/unified (Fase 14A Parte 5,
`feat/controlled-unified-activation`)."""

from __future__ import annotations

from altamira_extractor.contracts.unified_activation_evaluation import (
    UnifiedActivationComparisonLevel,
)
from altamira_extractor.pipeline.unified_activation_reference_adapters import (
    adapt_unified_references,
    adapt_v1_references,
)

from ._unified_activation_fixtures import (
    V1_CANDIDATE_ID,
    activation_golden_path,
    v1_candidate_artifact,
    v1_guardrail_artifact,
)


class TestV1ReferenceAdapter:
    def test_none_artifact_produces_empty_list(self) -> None:
        assert adapt_v1_references(None) == []

    def test_candidate_only_produces_level_candidate(self) -> None:
        artifact = v1_candidate_artifact()
        references = adapt_v1_references(artifact)
        assert len(references) == 1
        assert references[0].level == UnifiedActivationComparisonLevel.CANDIDATE
        assert references[0].rule_draft_id is None
        assert references[0].statement is None

    def test_guardrail_approved_produces_level_rule(self) -> None:
        artifact = v1_candidate_artifact()
        guardrail = v1_guardrail_artifact()
        references = adapt_v1_references(
            artifact, guardrail_artifacts_by_candidate_id={V1_CANDIDATE_ID: guardrail}
        )
        assert len(references) == 1
        assert references[0].level == UnifiedActivationComparisonLevel.RULE
        assert references[0].rule_draft_id == V1_CANDIDATE_ID
        assert references[0].statement is not None

    def test_target_is_always_none_for_v1(self) -> None:
        artifact = v1_candidate_artifact()
        references = adapt_v1_references(artifact)
        assert references[0].target is None

    def test_program_derived_from_paragraph_id(self) -> None:
        artifact = v1_candidate_artifact()
        references = adapt_v1_references(artifact)
        assert references[0].program == "CALLER10"

    def test_provenance_preserved(self) -> None:
        artifact = v1_candidate_artifact()
        references = adapt_v1_references(artifact)
        assert references[0].provenance_references == ["CALLER10.cbl::12"]

    def test_output_literal_preserved(self) -> None:
        artifact = v1_candidate_artifact(outcome_code="R002")
        references = adapt_v1_references(artifact)
        assert references[0].output_literal == "R002"

    def test_adapter_does_not_mutate_input(self) -> None:
        artifact = v1_candidate_artifact()
        snapshot = artifact.model_copy(deep=True)
        adapt_v1_references(artifact)
        assert artifact == snapshot

    def test_rule_id_populated_from_markdown_map_when_present(self) -> None:
        artifact = v1_candidate_artifact()
        guardrail = v1_guardrail_artifact()
        references = adapt_v1_references(
            artifact,
            guardrail_artifacts_by_candidate_id={V1_CANDIDATE_ID: guardrail},
            rule_markdown_filename_by_candidate_id={V1_CANDIDATE_ID: "abc123.md"},
        )
        assert references[0].rule_id == "abc123.md"

    def test_references_sorted_by_reference_id(self) -> None:
        artifact = v1_candidate_artifact()
        references = adapt_v1_references(artifact)
        assert references == sorted(references, key=lambda r: r.reference_id)


class TestUnifiedReferenceAdapter:
    def test_none_artifact_produces_empty_list(self) -> None:
        assert adapt_unified_references(None) == []

    def test_group_without_downstream_produces_level_candidate(self) -> None:
        gp = activation_golden_path()
        references = adapt_unified_references(gp.unified_shadow)
        assert len(references) == 1
        assert references[0].level == UnifiedActivationComparisonLevel.CANDIDATE
        assert references[0].rule_draft_record_id is None

    def test_group_with_downstream_executed_produces_level_rule(self) -> None:
        gp = activation_golden_path()
        references = adapt_unified_references(gp.unified_shadow, downstream=gp.downstream_artifact)
        assert len(references) == 1
        assert references[0].level == UnifiedActivationComparisonLevel.RULE
        assert references[0].rule_draft_record_id is not None
        assert references[0].statement is not None

    def test_guardrail_status_reflected(self) -> None:
        gp = activation_golden_path()
        references = adapt_unified_references(gp.unified_shadow, downstream=gp.downstream_artifact)
        assert references[0].guardrail_status.value == "PASSED"

    def test_no_winner_member_all_member_ids_preserved(self) -> None:
        gp = activation_golden_path()
        references = adapt_unified_references(gp.unified_shadow, downstream=gp.downstream_artifact)
        group = gp.unified_shadow.shadow_groups[0]
        assert references[0].member_ids == sorted(group.member_ids)

    def test_evidence_and_provenance_preserved(self) -> None:
        gp = activation_golden_path()
        references = adapt_unified_references(gp.unified_shadow, downstream=gp.downstream_artifact)
        group = gp.unified_shadow.shadow_groups[0]
        assert references[0].evidence_ids == sorted(group.evidence_ids)
        assert references[0].provenance_references == sorted(group.provenance_references)

    def test_adapter_does_not_mutate_inputs(self) -> None:
        gp = activation_golden_path()
        shadow_snapshot = gp.unified_shadow.model_copy(deep=True)
        downstream_snapshot = gp.downstream_artifact.model_copy(deep=True)
        adapt_unified_references(gp.unified_shadow, downstream=gp.downstream_artifact)
        assert gp.unified_shadow == shadow_snapshot
        assert gp.downstream_artifact == downstream_snapshot

    def test_references_sorted_by_reference_id(self) -> None:
        gp = activation_golden_path()
        references = adapt_unified_references(gp.unified_shadow, downstream=gp.downstream_artifact)
        assert references == sorted(references, key=lambda r: r.reference_id)
