"""Tests contractuales de los 16 invariantes de Fase 8 (ampliacion
semantica, `feat/interprocedural-rule-detectors-shadow`):
`contracts/interprocedural_rule_candidates.py`. Los invariantes 10
(IDs deterministicos), 11 (source_fact_ids deben existir), 12 (binding
IDs deben existir) y 13 (call site IDs deben existir) son invariantes
CRUZADOS contra otros artefactos (Fase 6/7) que el contrato en si mismo
no puede validar de forma aislada -- se prueban a nivel de detector en
`tests/pipeline/test_interprocedural_rule_detectors.py` (items 19-22).
Este modulo cubre los 12 invariantes restantes, verificables solo con
Pydantic."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts.interprocedural_call_linkage import InterproceduralSourceReference
from altamira_extractor.contracts.interprocedural_propagation import (
    InterproceduralPropagationBarrier,
)
from altamira_extractor.contracts.interprocedural_rule_candidates import (
    InterproceduralCandidateComparison,
    InterproceduralCandidateSupport,
    InterproceduralComparisonStatus,
    InterproceduralRelationStatus,
    InterproceduralRuleCandidate,
    InterproceduralRuleCandidatesArtifact,
    InterproceduralRuleCandidatesSummary,
    InterproceduralRuleEvidence,
    InterproceduralRuleType,
    derive_comparison_status,
)

_HASH = "e" * 64


def _evidence(**overrides: object) -> InterproceduralRuleEvidence:
    defaults: dict[str, object] = {
        "evidence_id": "evidence::1",
        "caller_program": "CALLER",
        "callee_program": "CALLEE",
        "call_site_id": "callsite::1",
        "statement_id": "CALLER::MAIN::0::CALL",
        "output_literal": "0009",
    }
    defaults.update(overrides)
    return InterproceduralRuleEvidence(**defaults)  # type: ignore[arg-type]


def _candidate(**overrides: object) -> InterproceduralRuleCandidate:
    defaults: dict[str, object] = {
        "candidate_id": "ipr::interprocedural-return-code-rule::1",
        "detector": "interprocedural-return-code-rule",
        "rule_type": InterproceduralRuleType.RETURN_CODE_RULE,
        "support": InterproceduralCandidateSupport.DETERMINISTIC,
        "caller_program": "CALLER",
        "callee_program": "CALLEE",
        "caller_paragraph": "MAIN",
        "call_site_id": "callsite::1",
        "target": "WS-R",
        "output_literal": "0009",
        "evidence": [_evidence()],
    }
    defaults.update(overrides)
    return InterproceduralRuleCandidate(**defaults)  # type: ignore[arg-type]


def _comparison(**overrides: object) -> InterproceduralCandidateComparison:
    defaults: dict[str, object] = {
        "comparison_id": "comparison::1",
        "interprocedural_candidate_id": "ipr::interprocedural-return-code-rule::1",
        "v1_relation": InterproceduralRelationStatus.NOT_FOUND,
        "v2_relation": InterproceduralRelationStatus.NOT_FOUND,
        "status": InterproceduralComparisonStatus.INTERPROCEDURAL_ONLY,
        "reason": "Sin candidato V1/V2 comparable.",
    }
    defaults.update(overrides)
    return InterproceduralCandidateComparison(**defaults)  # type: ignore[arg-type]


def _summary(**overrides: object) -> InterproceduralRuleCandidatesSummary:
    defaults: dict[str, object] = {
        "candidate_count": 1,
        "deterministic_count": 1,
        "partial_count": 0,
        "blocked_count": 0,
        "counts_by_detector": {"interprocedural-return-code-rule": 1},
        "counts_by_rule_type": {InterproceduralRuleType.RETURN_CODE_RULE: 1},
        "matched_v1_count": 0,
        "matched_v2_count": 0,
        "related_v1_count": 0,
        "related_v2_count": 0,
        "interprocedural_only_count": 1,
        "not_evaluated_count": 0,
    }
    defaults.update(overrides)
    return InterproceduralRuleCandidatesSummary(**defaults)  # type: ignore[arg-type]


def _artifact(**overrides: object) -> InterproceduralRuleCandidatesArtifact:
    defaults: dict[str, object] = {
        "run_id": "run1",
        "source_package_hash": _HASH,
        "source_artifact_hashes": {"artifacts/02-canonical": _HASH},
        "canonical_schema_versions": ["1.2"],
        "semantic_effects_schema_version": "1.2",
        "semantic_propagation_schema_version": "1.1",
        "interprocedural_call_linkage_schema_version": "1.0",
        "interprocedural_propagation_schema_version": "1.0",
        "summary": _summary(),
        "candidates": [_candidate()],
        "comparisons": [_comparison()],
    }
    defaults.update(overrides)
    return InterproceduralRuleCandidatesArtifact(**defaults)  # type: ignore[arg-type]


def test_valid_artifact_round_trips() -> None:
    artifact = _artifact()
    reloaded = InterproceduralRuleCandidatesArtifact.model_validate_json(artifact.model_dump_json())
    assert reloaded == artifact


# --- 1. candidate_id unico -----------------------------------------------


def test_invariant_01_duplicate_candidate_id_rejected() -> None:
    dup = _candidate()
    with pytest.raises(ValidationError, match="candidate_id duplicado"):
        _artifact(candidates=[dup, dup])


# --- 2. evidence_id unico dentro de cada candidato ------------------------


def test_invariant_02_duplicate_evidence_id_rejected() -> None:
    ev = _evidence()
    with pytest.raises(ValidationError, match="evidence_id duplicado"):
        _candidate(evidence=[ev, ev])


# --- 3. comparison_id unico ------------------------------------------------


def test_invariant_03_duplicate_comparison_id_rejected() -> None:
    comp = _comparison()
    with pytest.raises(ValidationError, match="comparison_id duplicado"):
        _artifact(comparisons=[comp, comp])


# --- 4. cada candidato aparece exactamente una vez en la clasificacion ----


def test_invariant_04_candidate_without_comparison_rejected() -> None:
    with pytest.raises(ValidationError, match="sin comparacion"):
        _artifact(comparisons=[])


def test_invariant_04_candidate_with_two_comparisons_rejected() -> None:
    comp_a = _comparison(comparison_id="comparison::a")
    comp_b = _comparison(comparison_id="comparison::b")
    with pytest.raises(ValidationError, match="mas de una comparacion"):
        _artifact(comparisons=[comp_a, comp_b])


def test_invariant_04_comparison_referencing_unknown_candidate_rejected() -> None:
    orphan = _comparison(
        comparison_id="comparison::orphan", interprocedural_candidate_id="ipr::unknown::x"
    )
    with pytest.raises(ValidationError, match="interprocedural_candidate_id inexistente"):
        _artifact(comparisons=[_comparison(), orphan])


# --- 5. ningun candidato BLOCKED puede afirmar output_literal -------------


def test_invariant_05_blocked_candidate_with_output_literal_rejected() -> None:
    with pytest.raises(ValidationError, match="BLOCKED no puede afirmar output_literal"):
        _candidate(
            support=InterproceduralCandidateSupport.BLOCKED,
            output_literal="0009",
            barriers=[InterproceduralPropagationBarrier.NON_RETURNING_TERMINATION],
        )


def test_invariant_05_blocked_candidate_without_barriers_rejected() -> None:
    with pytest.raises(ValidationError, match="BLOCKED exige al menos un barrier"):
        _candidate(support=InterproceduralCandidateSupport.BLOCKED, output_literal=None)


# --- 6/7. un candidato no puede ser MATCHED_V1/RELATED_V1 (o V2) a la vez -


def test_invariant_06_matched_v1_requires_v1_relation_matched_and_v1_id() -> None:
    with pytest.raises(ValidationError, match="v1_relation=MATCHED exige v1_candidate_id"):
        _comparison(
            status=InterproceduralComparisonStatus.MATCHED_V1,
            v1_relation=InterproceduralRelationStatus.MATCHED,
            v2_relation=InterproceduralRelationStatus.NOT_FOUND,
            v1_candidate_id=None,
        )
    with pytest.raises(ValidationError, match="no coincide con la derivacion determinista"):
        _comparison(
            status=InterproceduralComparisonStatus.MATCHED_V1,
            v1_relation=InterproceduralRelationStatus.NOT_FOUND,
            v2_relation=InterproceduralRelationStatus.NOT_FOUND,
        )


def test_invariant_06_matched_v1_may_carry_a_secondary_matched_v2_relation() -> None:
    """Regla de la auditoria de cierre (Parte 4): un candidato puede
    tener v1_relation=MATCHED (status principal MATCHED_V1, prioridad)
    Y v2_relation=MATCHED simultaneamente, cada uno con su propio
    candidate_id -- nunca se pierde la relacion secundaria con V2."""
    comparison = _comparison(
        status=InterproceduralComparisonStatus.MATCHED_V1,
        v1_relation=InterproceduralRelationStatus.MATCHED,
        v2_relation=InterproceduralRelationStatus.MATCHED,
        v1_candidate_id="cand::v1",
        v2_candidate_id="cand::v2",
    )
    assert comparison.v1_candidate_id == "cand::v1"
    assert comparison.v2_candidate_id == "cand::v2"


def test_invariant_07_matched_v2_requires_v2_relation_matched_and_v2_id() -> None:
    with pytest.raises(ValidationError, match="v2_relation=MATCHED exige v2_candidate_id"):
        _comparison(
            status=InterproceduralComparisonStatus.MATCHED_V2,
            v1_relation=InterproceduralRelationStatus.NOT_FOUND,
            v2_relation=InterproceduralRelationStatus.MATCHED,
            v2_candidate_id=None,
        )
    with pytest.raises(ValidationError, match="no coincide con la derivacion determinista"):
        _comparison(
            status=InterproceduralComparisonStatus.MATCHED_V2,
            v1_relation=InterproceduralRelationStatus.NOT_FOUND,
            v2_relation=InterproceduralRelationStatus.NOT_FOUND,
        )


# --- 8. la suma del summary reconcilia exactamente con las listas --------


def test_invariant_08_summary_deterministic_count_must_match_real_aggregation() -> None:
    with pytest.raises(ValidationError, match="summary.deterministic_count"):
        _artifact(summary=_summary(deterministic_count=0, partial_count=1))


def test_invariant_08_summary_matched_v1_count_must_match_real_aggregation() -> None:
    comp = _comparison(
        status=InterproceduralComparisonStatus.MATCHED_V1,
        v1_relation=InterproceduralRelationStatus.MATCHED,
        v1_candidate_id="cand::v1",
    )
    with pytest.raises(ValidationError, match="summary.matched_v1_count"):
        _artifact(
            comparisons=[comp], summary=_summary(matched_v1_count=0, interprocedural_only_count=1)
        )


# --- 9. orden estable ------------------------------------------------------


def test_invariant_09_candidates_must_be_sorted_by_candidate_id() -> None:
    first = _candidate(candidate_id="ipr::z::1")
    second = _candidate(candidate_id="ipr::a::2")
    comp_first = _comparison(
        comparison_id="comparison::first", interprocedural_candidate_id="ipr::z::1"
    )
    comp_second = _comparison(
        comparison_id="comparison::second", interprocedural_candidate_id="ipr::a::2"
    )
    with pytest.raises(ValidationError, match="no esta ordenado deterministicamente"):
        _artifact(
            candidates=[first, second],
            comparisons=[comp_first, comp_second],
            summary=_summary(
                candidate_count=2,
                deterministic_count=2,
                interprocedural_only_count=2,
                counts_by_detector={"interprocedural-return-code-rule": 2},
                counts_by_rule_type={InterproceduralRuleType.RETURN_CODE_RULE: 2},
            ),
        )


def test_invariant_09_comparisons_must_be_sorted_by_comparison_id() -> None:
    comp_z = _comparison(comparison_id="comparison::z")
    with pytest.raises(ValidationError, match="mas de una comparacion|no esta ordenado"):
        _artifact(comparisons=[comp_z, _comparison(comparison_id="comparison::a")])


# --- 14. ningun candidato puede carecer de evidence -----------------------


def test_invariant_14_candidate_without_evidence_rejected() -> None:
    with pytest.raises(ValidationError, match="ningun candidato puede carecer de evidence"):
        _candidate(evidence=[])


# --- 15. output_literal obligatorio para candidatos deterministicos ------


def test_invariant_15_deterministic_candidate_without_output_literal_rejected() -> None:
    with pytest.raises(ValidationError, match="DETERMINISTIC exige output_literal"):
        _candidate(support=InterproceduralCandidateSupport.DETERMINISTIC, output_literal=None)


# --- 16. ausencia de duplicados semanticos ---------------------------------


def test_invariant_16_semantic_duplicate_candidates_rejected() -> None:
    first = _candidate(candidate_id="ipr::interprocedural-return-code-rule::1")
    duplicate = _candidate(candidate_id="ipr::interprocedural-return-code-rule::2")
    comp_first = _comparison(
        comparison_id="comparison::first",
        interprocedural_candidate_id="ipr::interprocedural-return-code-rule::1",
    )
    comp_second = _comparison(
        comparison_id="comparison::second",
        interprocedural_candidate_id="ipr::interprocedural-return-code-rule::2",
    )
    with pytest.raises(ValidationError, match="duplicados semanticos"):
        _artifact(
            candidates=[first, duplicate],
            comparisons=[comp_first, comp_second],
            summary=_summary(
                candidate_count=2,
                deterministic_count=2,
                interprocedural_only_count=2,
                counts_by_detector={"interprocedural-return-code-rule": 2},
                counts_by_rule_type={InterproceduralRuleType.RETURN_CODE_RULE: 2},
            ),
        )


def test_evidence_source_references_use_interprocedural_source_reference() -> None:
    reference = InterproceduralSourceReference(program="CALLER", paragraph="MAIN")
    evidence = _evidence(source_references=[reference])
    assert evidence.source_references == [reference]


# --- derive_comparison_status: unica fuente de verdad (auditoria de cierre) --


def test_derive_comparison_status_matches_priority_table() -> None:
    R = InterproceduralRelationStatus
    S = InterproceduralComparisonStatus
    cases = {
        (R.MATCHED, R.NOT_FOUND): S.MATCHED_V1,
        (R.MATCHED, R.MATCHED): S.MATCHED_V1,
        (R.NOT_FOUND, R.MATCHED): S.MATCHED_V2,
        (R.RELATED, R.MATCHED): S.MATCHED_V2,
        (R.RELATED, R.NOT_FOUND): S.RELATED_V1,
        (R.NOT_FOUND, R.RELATED): S.RELATED_V2,
        (R.NOT_EVALUATED, R.RELATED): S.RELATED_V2,
        (R.NOT_FOUND, R.NOT_FOUND): S.INTERPROCEDURAL_ONLY,
        (R.NOT_EVALUATED, R.NOT_FOUND): S.NOT_EVALUATED,
        (R.NOT_FOUND, R.NOT_EVALUATED): S.NOT_EVALUATED,
        (R.NOT_EVALUATED, R.NOT_EVALUATED): S.NOT_EVALUATED,
    }
    for (v1_relation, v2_relation), expected in cases.items():
        assert derive_comparison_status(v1_relation, v2_relation) == expected, (
            v1_relation,
            v2_relation,
        )
