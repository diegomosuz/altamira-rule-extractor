"""Tests del analizador principal PURO (Fase 9, `feat/unified-
candidate-promotion-assessment`). Items 33-38 de los 50 tests
obligatorios: criterios/summary reconciliados (garantizado por los
validadores del propio contrato al construir el artefacto), IDs
deterministicos, orden independiente de la entrada, no-mutacion de
argumentos, serializacion byte-a-byte."""

from __future__ import annotations

import copy

from altamira_extractor.contracts.candidate_promotion_assessment import (
    CandidateSource,
    PromotionDisposition,
    SourceAvailability,
)
from altamira_extractor.contracts.interprocedural_rule_candidates import (
    InterproceduralComparisonStatus,
    InterproceduralRelationStatus,
)
from altamira_extractor.pipeline.candidate_promotion_assessment_analyzer import (
    analyze_candidate_promotion_assessment,
)

from .candidate_promotion_assessment_helpers import (
    HASH,
    interprocedural_artifact,
    interprocedural_candidate,
    interprocedural_comparison,
    v1_artifact,
    v1_candidate,
    v1_v2_matched_comparison,
    v2_artifact,
    v2_candidate,
)

V1_ID = "candidate::1"
V2_ID = "v2::a"
IP_ID = "ipr::a"

_ALL_AVAILABLE = {
    CandidateSource.V1: SourceAvailability.AVAILABLE,
    CandidateSource.V2: SourceAvailability.AVAILABLE,
    CandidateSource.INTERPROCEDURAL: SourceAvailability.AVAILABLE,
}


def _build_scenario():
    v1a = v1_artifact([v1_candidate(candidate_id=V1_ID)])
    v2a = v2_artifact(
        candidates=[v2_candidate(candidate_id=V2_ID)],
        comparisons=[v1_v2_matched_comparison(v1_id=V1_ID, v2_id=V2_ID)],
    )
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
    return v1a, v2a, ipa


def _run(v1a, v2a, ipa):
    return analyze_candidate_promotion_assessment(
        v1_candidates=v1a,
        v2_candidates=v2a,
        interprocedural_candidates=ipa,
        source_availability=_ALL_AVAILABLE,
        source_artifact_hash_by_source={
            CandidateSource.V1: HASH,
            CandidateSource.V2: HASH,
            CandidateSource.INTERPROCEDURAL: HASH,
        },
        run_id="run1",
        source_package_hash=HASH,
        source_artifact_hashes={},
    )


def test_analyzer_produces_a_valid_self_reconciled_artifact() -> None:
    """La sola construccion exitosa de `CandidatePromotionAssessmentArtifact`
    (sin `ValidationError`) ya demuestra que criteria y summary estan
    reconciliados -- los validadores del contrato lo exigen."""
    v1a, v2a, ipa = _build_scenario()
    artifact = _run(v1a, v2a, ipa)
    assert artifact.summary.unified_reference_count == 3
    assert artifact.summary.counts_by_disposition[PromotionDisposition.BASELINE_V1] == 1
    assert artifact.summary.counts_by_disposition[PromotionDisposition.ALREADY_COVERED] == 2


def test_analyzer_ids_are_deterministic_across_runs() -> None:
    v1a, v2a, ipa = _build_scenario()
    artifact_1 = _run(v1a, v2a, ipa)
    artifact_2 = _run(v1a, v2a, ipa)
    ids_1 = [r.unified_reference_id for r in artifact_1.candidate_references]
    ids_2 = [r.unified_reference_id for r in artifact_2.candidate_references]
    assert ids_1 == ids_2
    assessment_ids_1 = [a.assessment_id for a in artifact_1.assessments]
    assessment_ids_2 = [a.assessment_id for a in artifact_2.assessments]
    assert assessment_ids_1 == assessment_ids_2


def test_analyzer_output_independent_of_source_object_identity() -> None:
    """Dos artefactos de entrada CONSTRUIDOS POR SEPARADO pero con el
    mismo contenido logico deben producir el mismo resultado -- el orden
    interno de construccion no puede influir en el resultado final."""
    v1a_1, v2a_1, ipa_1 = _build_scenario()
    v1a_2, v2a_2, ipa_2 = _build_scenario()
    artifact_1 = _run(v1a_1, v2a_1, ipa_1)
    artifact_2 = _run(v1a_2, v2a_2, ipa_2)
    assert artifact_1.model_dump_json() == artifact_2.model_dump_json()


def test_analyzer_never_mutates_input_artifacts() -> None:
    v1a, v2a, ipa = _build_scenario()
    v1_before = copy.deepcopy(v1a.model_dump())
    v2_before = copy.deepcopy(v2a.model_dump())
    ip_before = copy.deepcopy(ipa.model_dump())

    _run(v1a, v2a, ipa)

    assert v1a.model_dump() == v1_before
    assert v2a.model_dump() == v2_before
    assert ipa.model_dump() == ip_before


def test_analyzer_serialization_is_byte_for_byte_deterministic() -> None:
    v1a, v2a, ipa = _build_scenario()
    artifact_1 = _run(v1a, v2a, ipa)
    artifact_2 = _run(v1a, v2a, ipa)
    assert artifact_1.model_dump_json() == artifact_2.model_dump_json()


def test_analyzer_v1_not_available_produces_not_evaluated_end_to_end() -> None:
    """Auditoria de cierre, Parte 1: con V1 `NOT_AVAILABLE` y V2/
    INTERPROCEDURAL `AVAILABLE`, el artefacto COMPLETO (validado por
    todos los invariantes del contrato) debe reflejar `NOT_EVALUATED`
    para toda referencia no-V1 -- nunca `REVIEW_REQUIRED`/`BLOCKED`."""
    v2a = v2_artifact(candidates=[v2_candidate(candidate_id=V2_ID)])
    ipa = interprocedural_artifact(candidates=[interprocedural_candidate(candidate_id=IP_ID)])
    availability = {
        CandidateSource.V1: SourceAvailability.NOT_AVAILABLE,
        CandidateSource.V2: SourceAvailability.AVAILABLE,
        CandidateSource.INTERPROCEDURAL: SourceAvailability.AVAILABLE,
    }
    artifact = analyze_candidate_promotion_assessment(
        v1_candidates=None,
        v2_candidates=v2a,
        interprocedural_candidates=ipa,
        source_availability=availability,
        source_artifact_hash_by_source={
            CandidateSource.V2: HASH,
            CandidateSource.INTERPROCEDURAL: HASH,
        },
        run_id="run1",
        source_package_hash=HASH,
        source_artifact_hashes={},
    )
    assert artifact.summary.not_evaluated_count == 2
    assert all(
        a.disposition == PromotionDisposition.NOT_EVALUATED for a in artifact.assessments
    )


def test_analyzer_v1_invalid_produces_blocked_end_to_end() -> None:
    """Auditoria de cierre, Parte 1: V1 `INVALID` (nunca `NOT_AVAILABLE`)
    bloquea (`BLOCKED`) toda referencia no-V1 -- una fuente invalida
    nunca se degrada silenciosamente a `NOT_EVALUATED`."""
    v2a = v2_artifact(candidates=[v2_candidate(candidate_id=V2_ID)])
    availability = {
        CandidateSource.V1: SourceAvailability.INVALID,
        CandidateSource.V2: SourceAvailability.AVAILABLE,
        CandidateSource.INTERPROCEDURAL: SourceAvailability.NOT_AVAILABLE,
    }
    artifact = analyze_candidate_promotion_assessment(
        v1_candidates=None,
        v2_candidates=v2a,
        interprocedural_candidates=None,
        source_availability=availability,
        source_artifact_hash_by_source={CandidateSource.V2: HASH},
        run_id="run1",
        source_package_hash=HASH,
        source_artifact_hashes={},
    )
    assert artifact.summary.blocked_count == 1
    assert artifact.assessments[0].disposition == PromotionDisposition.BLOCKED


def test_analyzer_with_only_absent_sources_produces_empty_but_valid_artifact() -> None:
    availability = {
        CandidateSource.V1: SourceAvailability.NOT_AVAILABLE,
        CandidateSource.V2: SourceAvailability.NOT_AVAILABLE,
        CandidateSource.INTERPROCEDURAL: SourceAvailability.NOT_AVAILABLE,
    }
    artifact = analyze_candidate_promotion_assessment(
        v1_candidates=None,
        v2_candidates=None,
        interprocedural_candidates=None,
        source_availability=availability,
        source_artifact_hash_by_source={},
        run_id="run1",
        source_package_hash=HASH,
        source_artifact_hashes={},
    )
    assert artifact.candidate_references == []
    assert artifact.summary.unified_reference_count == 0
