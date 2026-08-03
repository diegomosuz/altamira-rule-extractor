"""Tests del analizador de relaciones (Fase 9, `feat/unified-
candidate-promotion-assessment`). Items 10-16, 39-40 de los 50 tests
obligatorios: EXACT_MATCH/RELATED/NO_RELATION/NOT_EVALUATED entre las
tres fuentes, deduplicacion de relaciones, simetria de la relacion
serializada una unica vez."""

from __future__ import annotations

from altamira_extractor.contracts.candidate_promotion_assessment import CandidateRelationKind
from altamira_extractor.contracts.interprocedural_rule_candidates import (
    InterproceduralComparisonStatus,
    InterproceduralRelationStatus,
)
from altamira_extractor.pipeline.candidate_relation_analyzer import (
    build_candidate_relations,
    relation_id_for,
)
from altamira_extractor.pipeline.candidate_source_adapters import (
    adapt_interprocedural_candidates,
    adapt_v1_candidates,
    adapt_v2_candidates,
)

from .candidate_promotion_assessment_helpers import (
    HASH,
    interprocedural_artifact,
    interprocedural_candidate,
    interprocedural_comparison,
    v1_artifact,
    v1_candidate,
    v1_v2_matched_comparison,
    v1_v2_related_comparison,
    v2_artifact,
    v2_candidate,
)

V1_ID = "candidate::1"
V2_ID = "v2::a"
IP_ID = "ipr::a"


def _references(v1a=None, v2a=None, ipa=None):
    v1_refs = adapt_v1_candidates(v1a, source_artifact_hash=HASH)
    v2_refs = adapt_v2_candidates(v2a, source_artifact_hash=HASH)
    ip_refs = adapt_interprocedural_candidates(ipa, source_artifact_hash=HASH)
    return v1_refs, v2_refs, ip_refs


def test_exact_match_v1_v2_via_existing_comparison() -> None:
    v1a = v1_artifact([v1_candidate(candidate_id=V1_ID)])
    v2a = v2_artifact(
        candidates=[v2_candidate(candidate_id=V2_ID)],
        comparisons=[v1_v2_matched_comparison(v1_id=V1_ID, v2_id=V2_ID)],
    )
    v1_refs, v2_refs, ip_refs = _references(v1a, v2a, None)
    relations = build_candidate_relations(
        v1_references=v1_refs,
        v2_references=v2_refs,
        interprocedural_references=ip_refs,
        v2_artifact=v2a,
        interprocedural_artifact=None,
    )
    assert any(r.relation_kind == CandidateRelationKind.EXACT_MATCH for r in relations)


def test_exact_match_v1_interprocedural_via_existing_comparison() -> None:
    v1a = v1_artifact([v1_candidate(candidate_id=V1_ID)])
    ipa = interprocedural_artifact(
        candidates=[interprocedural_candidate(candidate_id=IP_ID)],
        comparisons=[
            interprocedural_comparison(
                candidate_id=IP_ID,
                status=InterproceduralComparisonStatus.MATCHED_V1,
                v1_relation=InterproceduralRelationStatus.MATCHED,
                v2_relation=InterproceduralRelationStatus.NOT_FOUND,
                v1_candidate_id=V1_ID,
            )
        ],
    )
    v1_refs, v2_refs, ip_refs = _references(v1a, None, ipa)
    relations = build_candidate_relations(
        v1_references=v1_refs,
        v2_references=v2_refs,
        interprocedural_references=ip_refs,
        v2_artifact=None,
        interprocedural_artifact=ipa,
    )
    assert any(r.relation_kind == CandidateRelationKind.EXACT_MATCH for r in relations)


def test_exact_match_v2_interprocedural_via_existing_comparison() -> None:
    v2a = v2_artifact(candidates=[v2_candidate(candidate_id=V2_ID)])
    ipa = interprocedural_artifact(
        candidates=[interprocedural_candidate(candidate_id=IP_ID)],
        comparisons=[
            interprocedural_comparison(
                candidate_id=IP_ID,
                status=InterproceduralComparisonStatus.MATCHED_V2,
                v1_relation=InterproceduralRelationStatus.NOT_FOUND,
                v2_relation=InterproceduralRelationStatus.MATCHED,
                v2_candidate_id=V2_ID,
            )
        ],
    )
    v1_refs, v2_refs, ip_refs = _references(None, v2a, ipa)
    relations = build_candidate_relations(
        v1_references=v1_refs,
        v2_references=v2_refs,
        interprocedural_references=ip_refs,
        v2_artifact=v2a,
        interprocedural_artifact=ipa,
    )
    assert any(r.relation_kind == CandidateRelationKind.EXACT_MATCH for r in relations)


def test_related_v1_v2_via_existing_comparison() -> None:
    v1a = v1_artifact([v1_candidate(candidate_id=V1_ID)])
    v2a = v2_artifact(
        candidates=[v2_candidate(candidate_id=V2_ID)],
        comparisons=[v1_v2_related_comparison(v1_id=V1_ID, v2_id=V2_ID)],
    )
    v1_refs, v2_refs, ip_refs = _references(v1a, v2a, None)
    relations = build_candidate_relations(
        v1_references=v1_refs,
        v2_references=v2_refs,
        interprocedural_references=ip_refs,
        v2_artifact=v2a,
        interprocedural_artifact=None,
    )
    assert any(r.relation_kind == CandidateRelationKind.RELATED for r in relations)


def test_related_v2_interprocedural_via_existing_comparison() -> None:
    v2a = v2_artifact(candidates=[v2_candidate(candidate_id=V2_ID)])
    ipa = interprocedural_artifact(
        candidates=[interprocedural_candidate(candidate_id=IP_ID)],
        comparisons=[
            interprocedural_comparison(
                candidate_id=IP_ID,
                status=InterproceduralComparisonStatus.RELATED_V2,
                v1_relation=InterproceduralRelationStatus.NOT_FOUND,
                v2_relation=InterproceduralRelationStatus.RELATED,
                v2_candidate_id=V2_ID,
            )
        ],
    )
    v1_refs, v2_refs, ip_refs = _references(None, v2a, ipa)
    relations = build_candidate_relations(
        v1_references=v1_refs,
        v2_references=v2_refs,
        interprocedural_references=ip_refs,
        v2_artifact=v2a,
        interprocedural_artifact=ipa,
    )
    assert any(r.relation_kind == CandidateRelationKind.RELATED for r in relations)


def test_no_relation_when_both_sources_available_but_unlinked() -> None:
    v1a = v1_artifact([v1_candidate(candidate_id=V1_ID)])
    v2a = v2_artifact(candidates=[v2_candidate(candidate_id=V2_ID)])
    v1_refs, v2_refs, ip_refs = _references(v1a, v2a, None)
    relations = build_candidate_relations(
        v1_references=v1_refs,
        v2_references=v2_refs,
        interprocedural_references=ip_refs,
        v2_artifact=v2a,
        interprocedural_artifact=None,
    )
    assert len(relations) == 1
    assert relations[0].relation_kind == CandidateRelationKind.NO_RELATION


def test_not_evaluated_when_a_source_is_absent_produces_no_relation_objects() -> None:
    """Cuando una fuente esta ausente, `build_candidate_relations` nunca
    fabrica una relacion `NO_RELATION` para ese par -- simplemente no la
    construye (la ausencia de evidencia nunca se presenta como evidencia
    de ausencia, ver `CandidateRelationKind.NOT_EVALUATED` docstring)."""
    v1a = v1_artifact([v1_candidate(candidate_id=V1_ID)])
    v1_refs, v2_refs, ip_refs = _references(v1a, None, None)
    relations = build_candidate_relations(
        v1_references=v1_refs,
        v2_references=v2_refs,
        interprocedural_references=ip_refs,
        v2_artifact=None,
        interprocedural_artifact=None,
    )
    assert relations == []


def test_relation_id_for_is_symmetric() -> None:
    a = relation_id_for(left_reference_id="unified::v1::a", right_reference_id="unified::v2::b")
    b = relation_id_for(left_reference_id="unified::v2::b", right_reference_id="unified::v1::a")
    assert a == b


def test_relations_deduplicated_pair_appears_once() -> None:
    v1a = v1_artifact([v1_candidate(candidate_id=V1_ID)])
    v2a = v2_artifact(
        candidates=[v2_candidate(candidate_id=V2_ID)],
        comparisons=[v1_v2_matched_comparison(v1_id=V1_ID, v2_id=V2_ID)],
    )
    v1_refs, v2_refs, ip_refs = _references(v1a, v2a, None)
    relations = build_candidate_relations(
        v1_references=v1_refs,
        v2_references=v2_refs,
        interprocedural_references=ip_refs,
        v2_artifact=v2a,
        interprocedural_artifact=None,
    )
    pairs = [(r.left_reference_id, r.right_reference_id) for r in relations]
    assert len(pairs) == len(set(pairs))


def test_symmetric_relation_serialized_once_left_before_right() -> None:
    v1a = v1_artifact([v1_candidate(candidate_id=V1_ID)])
    v2a = v2_artifact(
        candidates=[v2_candidate(candidate_id=V2_ID)],
        comparisons=[v1_v2_matched_comparison(v1_id=V1_ID, v2_id=V2_ID)],
    )
    v1_refs, v2_refs, ip_refs = _references(v1a, v2a, None)
    relations = build_candidate_relations(
        v1_references=v1_refs,
        v2_references=v2_refs,
        interprocedural_references=ip_refs,
        v2_artifact=v2a,
        interprocedural_artifact=None,
    )
    assert len(relations) == 1
    relation = relations[0]
    assert relation.left_reference_id < relation.right_reference_id


def test_conflicting_pairs_override_relation_kind_to_conflict() -> None:
    v1a = v1_artifact([v1_candidate(candidate_id=V1_ID)])
    v2a = v2_artifact(
        candidates=[v2_candidate(candidate_id=V2_ID)],
        comparisons=[v1_v2_matched_comparison(v1_id=V1_ID, v2_id=V2_ID)],
    )
    v1_refs, v2_refs, ip_refs = _references(v1a, v2a, None)
    override_pair = frozenset(
        {v1_refs[0].unified_reference_id, v2_refs[0].unified_reference_id}
    )
    relations = build_candidate_relations(
        v1_references=v1_refs,
        v2_references=v2_refs,
        interprocedural_references=ip_refs,
        v2_artifact=v2a,
        interprocedural_artifact=None,
        conflicting_pairs=frozenset({override_pair}),
    )
    assert relations[0].relation_kind == CandidateRelationKind.CONFLICT
