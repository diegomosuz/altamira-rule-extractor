"""Tests del adaptador de grupos unified (Fase 15A Parte 6/12, items
55-64, `feat/operational-governance-ui`)."""

from __future__ import annotations

from pathlib import Path

from altamira_extractor.contracts.unified_activation_materialization import (
    MaterializedGenerationManifest,
)
from altamira_extractor.pipeline.operational_governance_group_adapter import (
    build_unified_group_summaries,
)
from altamira_extractor.pipeline.unified_activation_store import UnifiedActivationStore

from ._operational_governance_fixtures import (
    MaterializationFixture,
    build_materialization_fixture,
    governance_run_dir,
    materialize_keep_v1,
    materialize_unified_canary,
)


def _active_manifest_and_store(
    tmp_path: Path,
) -> tuple[MaterializationFixture, UnifiedActivationStore, MaterializedGenerationManifest]:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    result = materialize_unified_canary(run_dir, fx, tmp_path)
    store = UnifiedActivationStore(run_dir)
    manifest = store.read_generation_manifest(result.generation_id)
    return fx, store, manifest


# 55. grupo unified.
def test_unified_group_is_projected(tmp_path: Path) -> None:
    fx, store, manifest = _active_manifest_and_store(tmp_path)
    groups = build_unified_group_summaries(store, manifest)
    assert len(groups) == 1
    group = groups[0]
    reference = fx.evaluation.unified_references[0]
    assert group.group_id == reference.group_id
    assert group.rule_family == reference.rule_family
    assert group.program == reference.program
    assert group.target == reference.target
    assert group.output_literal == reference.output_literal


# 56. members preservados.
def test_member_ids_preserved(tmp_path: Path) -> None:
    fx, store, manifest = _active_manifest_and_store(tmp_path)
    groups = build_unified_group_summaries(store, manifest)
    reference = fx.evaluation.unified_references[0]
    assert groups[0].member_ids == reference.member_ids


# 57. source candidates preservados.
def test_source_candidate_ids_preserved(tmp_path: Path) -> None:
    fx, store, manifest = _active_manifest_and_store(tmp_path)
    groups = build_unified_group_summaries(store, manifest)
    reference = fx.evaluation.unified_references[0]
    assert groups[0].source_candidate_ids == reference.source_candidate_ids


# 58. review decisions preservadas.
def test_review_decision_ids_preserved(tmp_path: Path) -> None:
    fx, store, manifest = _active_manifest_and_store(tmp_path)
    groups = build_unified_group_summaries(store, manifest)
    expected_context = next(
        c for c in fx.gp.downstream_artifact.context_packages if c.group_id == groups[0].group_id
    )
    assert groups[0].review_decision_ids == expected_context.review_decision_ids
    assert groups[0].context_package_record_id == expected_context.record_id


# 59. evidence preservada.
def test_evidence_ids_preserved(tmp_path: Path) -> None:
    fx, store, manifest = _active_manifest_and_store(tmp_path)
    groups = build_unified_group_summaries(store, manifest)
    reference = fx.evaluation.unified_references[0]
    assert groups[0].evidence_ids == reference.evidence_ids


# 60. aliases preservados.
def test_evidence_aliases_preserved(tmp_path: Path) -> None:
    fx, store, manifest = _active_manifest_and_store(tmp_path)
    groups = build_unified_group_summaries(store, manifest)
    expected_context = next(
        c for c in fx.gp.downstream_artifact.context_packages if c.group_id == groups[0].group_id
    )
    assert groups[0].evidence_aliases == expected_context.evidence_aliases
    assert groups[0].evidence_aliases == ["E001", "E002"]


# 61. provenance preservada.
def test_provenance_references_preserved(tmp_path: Path) -> None:
    fx, store, manifest = _active_manifest_and_store(tmp_path)
    groups = build_unified_group_summaries(store, manifest)
    reference = fx.evaluation.unified_references[0]
    assert groups[0].provenance_references == reference.provenance_references


# 62. guardrail status.
def test_guardrail_status_reflected(tmp_path: Path) -> None:
    _fx, store, manifest = _active_manifest_and_store(tmp_path)
    groups = build_unified_group_summaries(store, manifest)
    assert groups[0].guardrail_status == "PASSED"
    assert groups[0].rule_draft_record_id is not None


# 63. sin member ganador.
def test_no_winner_member_chosen(tmp_path: Path) -> None:
    """Todos los `member_ids` de la referencia se preservan tal cual --
    el adaptador nunca reduce la lista a un unico elemento "ganador"."""
    fx, store, manifest = _active_manifest_and_store(tmp_path)
    groups = build_unified_group_summaries(store, manifest)
    reference = fx.evaluation.unified_references[0]
    assert set(groups[0].member_ids) == set(reference.member_ids)
    assert len(groups[0].member_ids) == len(reference.member_ids)


# 64. sin source text completo.
def test_no_full_source_text_exposed(tmp_path: Path) -> None:
    """`GovernanceUnifiedGroupSummary` nunca incluye el `ContextPackage`/
    `RuleDraft` completos -- solo IDs de referencia cruzada."""
    _fx, store, manifest = _active_manifest_and_store(tmp_path)
    groups = build_unified_group_summaries(store, manifest)
    field_names = set(type(groups[0]).model_fields.keys())
    assert "context_package" not in field_names
    assert "rule_draft" not in field_names
    assert "source_text" not in field_names
    serialized = groups[0].to_stable_json()
    assert "context_package_record_id" in serialized


# V1 nunca produce grupos (ausencia legitima).
def test_v1_generation_produces_no_groups(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    result = materialize_keep_v1(run_dir, fx, tmp_path)
    store = UnifiedActivationStore(run_dir)
    manifest = store.read_generation_manifest(result.generation_id)
    groups = build_unified_group_summaries(store, manifest)
    assert groups == []
