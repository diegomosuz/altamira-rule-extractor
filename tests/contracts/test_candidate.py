"""Tests de RuleCandidate (artifacts/06-candidates.json).

`rule_type` es `str | None`: StatementKind.IF/EVALUATE son
construcciones tecnicas de COBOL, no evidencia de un tipo funcional de
regla de negocio, asi que ninguna etapa puede derivarlo ni inventarlo
(ver docstring de contracts/candidate.py)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts import CandidateArtifact, RuleCandidate
from altamira_extractor.contracts.candidate_promotion_assessment import (
    CandidateSource,
    UnifiedRuleFamily,
)

_VALID_HASH = "a" * 64
_OTHER_HASH = "b" * 64


def _candidate(**overrides: object) -> RuleCandidate:
    defaults: dict[str, object] = {
        "candidate_id": "candidate::return-code-decision::dec-1",
        "paragraph_id": "program::AR::op::PROG::1::abc123::paragraph::MAIN",
        "paragraph_name": "MAIN",
        "decision_id": "program::AR::op::PROG::1::abc123::paragraph::MAIN::decision::10::1",
        "detector_id": "return-code-decision",
        "detector_version": "1.0",
        "detector_score": 1.0,
        "condition": "WS-COD-RESULT = 'R001'",
        "outcome_code": "R001",
        "rule_type": None,
        "line_start": 10,
        "source_file": "01-codigo/cobol/PROG.cbl",
        "source_package_hash": _VALID_HASH,
    }
    defaults.update(overrides)
    return RuleCandidate(**defaults)  # type: ignore[arg-type]


def test_rule_candidate_valid_with_rule_type_none() -> None:
    candidate = _candidate(rule_type=None)
    assert candidate.rule_type is None


def test_rule_candidate_valid_with_a_real_rule_type() -> None:
    # Un valor de rule_type real y no vacio sigue siendo valido: el
    # contrato no exige `None`, solo deja de EXIGIR un valor no vacio.
    candidate = _candidate(rule_type="threshold-comparison")
    assert candidate.rule_type == "threshold-comparison"


def test_rule_candidate_round_trips_with_rule_type_none() -> None:
    candidate = _candidate(rule_type=None)
    restored = RuleCandidate.model_validate_json(candidate.to_stable_json())
    assert restored == candidate
    assert restored.rule_type is None


# --- CandidateArtifact (artifacts/06-candidates.json) ---


def _artifact(**overrides: object) -> CandidateArtifact:
    defaults: dict[str, object] = {
        "run_id": "run-1",
        "source_package_hash": _VALID_HASH,
        "semantic_graph_hash": _VALID_HASH,
        "invariants_query_hash": _VALID_HASH,
        "q0_query_hash": _VALID_HASH,
        "candidates": [],
        "warnings": [],
    }
    defaults.update(overrides)
    return CandidateArtifact(**defaults)  # type: ignore[arg-type]


def test_candidate_artifact_with_empty_candidates_is_valid() -> None:
    artifact = _artifact(candidates=[])
    assert artifact.candidates == []


def test_candidate_artifact_round_trips() -> None:
    candidate = _candidate(candidate_id="candidate::det::1.0::" + _VALID_HASH + "::dec-1")
    artifact = _artifact(candidates=[candidate])
    restored = CandidateArtifact.model_validate_json(artifact.to_stable_json())
    assert restored == artifact


def test_candidate_artifact_rejects_duplicate_candidate_id() -> None:
    candidate = _candidate(candidate_id="candidate::det::1.0::" + _VALID_HASH + "::dec-1")
    with pytest.raises(ValidationError):
        _artifact(candidates=[candidate, candidate])


def test_candidate_artifact_rejects_unsorted_candidates() -> None:
    first = _candidate(
        candidate_id="candidate::det::1.0::" + _VALID_HASH + "::dec-2", decision_id="dec-2"
    )
    second = _candidate(
        candidate_id="candidate::det::1.0::" + _VALID_HASH + "::dec-1", decision_id="dec-1"
    )
    with pytest.raises(ValidationError):
        _artifact(candidates=[first, second])


def test_candidate_artifact_accepts_sorted_candidates() -> None:
    first = _candidate(
        candidate_id="candidate::det::1.0::" + _VALID_HASH + "::dec-1", decision_id="dec-1"
    )
    second = _candidate(
        candidate_id="candidate::det::1.0::" + _VALID_HASH + "::dec-2", decision_id="dec-2"
    )
    artifact = _artifact(candidates=[first, second])
    assert [c.candidate_id for c in artifact.candidates] == [
        first.candidate_id,
        second.candidate_id,
    ]


def test_candidate_artifact_rejects_candidate_with_mismatched_source_package_hash() -> None:
    candidate = _candidate(
        candidate_id="candidate::det::1.0::" + _OTHER_HASH + "::dec-1",
        source_package_hash=_OTHER_HASH,
    )
    with pytest.raises(ValidationError):
        _artifact(source_package_hash=_VALID_HASH, candidates=[candidate])


def test_candidate_artifact_rejects_duplicate_warnings() -> None:
    with pytest.raises(ValidationError):
        _artifact(warnings=["igual", "igual"])


def test_candidate_artifact_rejects_unsorted_warnings() -> None:
    with pytest.raises(ValidationError):
        _artifact(warnings=["z", "a"])


# --- Fase 15B3-B/15B3-B1: candidate_source/rule_family/evidence_ids ---


def test_rule_candidate_defaults_are_v1_and_backward_compatible() -> None:
    """Un RuleCandidate construido sin los campos nuevos (como todo el
    historial de runs de V1) sigue siendo valido y describe exactamente
    la semantica V1: fuente V1, familia RETURN_CODE (garantia
    estructural de Q0), sin evidencia adicional."""
    candidate = _candidate()
    assert candidate.candidate_source == CandidateSource.V1
    assert candidate.rule_family == UnifiedRuleFamily.RETURN_CODE
    assert candidate.evidence_ids == []


def test_rule_candidate_accepts_explicit_v2_provenance() -> None:
    candidate = _candidate(
        candidate_source=CandidateSource.V2,
        rule_family=UnifiedRuleFamily.LEVEL_88_RETURN_CODE,
        evidence_ids=["effect::1", "fact::1"],
    )
    assert candidate.candidate_source == CandidateSource.V2
    assert candidate.rule_family == UnifiedRuleFamily.LEVEL_88_RETURN_CODE
    assert candidate.evidence_ids == ["effect::1", "fact::1"]


def test_rule_candidate_rejects_unsorted_evidence_ids() -> None:
    with pytest.raises(ValidationError):
        _candidate(evidence_ids=["z", "a"])


def test_rule_candidate_rejects_duplicate_evidence_ids() -> None:
    with pytest.raises(ValidationError):
        _candidate(evidence_ids=["a", "a"])


def test_rule_candidate_rejects_unknown_field_support_level() -> None:
    """Fase 15B3-B1: `support_level` fue auditado y eliminado (unico
    valor posible, ningun consumidor productivo) -- el contrato es
    cerrado (`extra=forbid`), asi que reintroducirlo debe fallar."""
    with pytest.raises(ValidationError):
        _candidate(support_level="DETERMINISTIC")


def test_pre_existing_run_json_without_new_fields_still_parses() -> None:
    """Runs historicos (`06-candidates.json` persistido antes de la Fase
    15B3-B) nunca tuvieron `candidate_source`/`rule_family`/
    `evidence_ids` -- deben seguir siendo legibles."""
    legacy_json = (
        '{"schema_version":"1.0","candidate_id":"candidate::det::1.0::' + _VALID_HASH + '::dec-1",'
        '"paragraph_id":"program::AR::op::PROG::1::abc123::paragraph::MAIN",'
        '"paragraph_name":"MAIN",'
        '"decision_id":"program::AR::op::PROG::1::abc123::paragraph::MAIN::decision::10::1",'
        '"detector_id":"return-code-decision","detector_version":"1.0","detector_score":1.0,'
        '"status":"DETECTED_CANDIDATE","condition":"WS-COD-RESULT = \'R001\'",'
        '"outcome_code":"R001","rule_type":null,"line_start":10,'
        '"source_file":"01-codigo/cobol/PROG.cbl","source_package_hash":"' + _VALID_HASH + '"}'
    )
    restored = RuleCandidate.model_validate_json(legacy_json)
    assert restored.candidate_source == CandidateSource.V1
    assert restored.rule_family == UnifiedRuleFamily.RETURN_CODE
    assert restored.evidence_ids == []


# ---------------------------------------------------------------------------
# Fase 15B3-C2-B2: decision_id/condition opcionales UNICAMENTE para
# CALCULATION incondicional -- RETURN_CODE/LEVEL_88_RETURN_CODE/
# STATE_TRANSITION siguen exigiendo ambos, sin cambios.
# ---------------------------------------------------------------------------


def test_historical_json_with_decision_id_and_condition_present_parses_identically() -> None:
    """JSON historico con decision_id/condition/decision siempre
    presentes (formato anterior a 15B3-C2-B2) debe seguir parseando
    exactamente igual -- el contrato no se relaja para el caso comun."""
    legacy_json = (
        '{"schema_version":"1.0","candidate_id":"candidate::det::1.0::' + _VALID_HASH + '::dec-1",'
        '"paragraph_id":"program::AR::op::PROG::1::abc123::paragraph::MAIN",'
        '"paragraph_name":"MAIN",'
        '"decision_id":"program::AR::op::PROG::1::abc123::paragraph::MAIN::decision::10::1",'
        '"detector_id":"return-code-decision","detector_version":"1.0","detector_score":1.0,'
        '"status":"DETECTED_CANDIDATE","condition":"WS-COD-RESULT = \'R001\'",'
        '"outcome_code":"R001","rule_type":null,"line_start":10,'
        '"source_file":"01-codigo/cobol/PROG.cbl","source_package_hash":"' + _VALID_HASH + '"}'
    )
    restored = RuleCandidate.model_validate_json(legacy_json)
    expected_decision_id = "program::AR::op::PROG::1::abc123::paragraph::MAIN::decision::10::1"
    assert restored.decision_id == expected_decision_id
    assert restored.condition == "WS-COD-RESULT = 'R001'"
    assert restored.outcome_code == "R001"
    assert restored.rule_family == UnifiedRuleFamily.RETURN_CODE


def test_return_code_candidate_without_decision_id_is_invalid() -> None:
    with pytest.raises(ValidationError):
        _candidate(decision_id=None, rule_family=UnifiedRuleFamily.RETURN_CODE)


def test_return_code_candidate_without_condition_is_invalid() -> None:
    with pytest.raises(ValidationError):
        _candidate(condition=None, rule_family=UnifiedRuleFamily.RETURN_CODE)


def test_level_88_return_code_candidate_without_decision_id_is_invalid() -> None:
    with pytest.raises(ValidationError):
        _candidate(decision_id=None, rule_family=UnifiedRuleFamily.LEVEL_88_RETURN_CODE)


def test_state_transition_candidate_without_decision_id_is_invalid() -> None:
    with pytest.raises(ValidationError):
        _candidate(decision_id=None, rule_family=UnifiedRuleFamily.STATE_TRANSITION)


def test_unconditional_calculation_candidate_with_both_none_is_valid() -> None:
    candidate = _candidate(
        decision_id=None,
        condition=None,
        outcome_code=None,
        rule_family=UnifiedRuleFamily.CALCULATION,
    )
    assert candidate.decision_id is None
    assert candidate.condition is None


def test_conditioned_calculation_candidate_with_both_present_is_valid() -> None:
    candidate = _candidate(rule_family=UnifiedRuleFamily.CALCULATION)
    assert candidate.decision_id is not None
    assert candidate.condition is not None


def test_calculation_candidate_with_only_decision_id_present_is_invalid() -> None:
    """Nunca un estado mixto: decision_id presente sin condition (o
    viceversa) indicaria una construccion parcial/inconsistente."""
    with pytest.raises(ValidationError):
        _candidate(condition=None, rule_family=UnifiedRuleFamily.CALCULATION)


def test_calculation_candidate_with_only_condition_present_is_invalid() -> None:
    with pytest.raises(ValidationError):
        _candidate(decision_id=None, rule_family=UnifiedRuleFamily.CALCULATION)


def test_unconditional_calculation_candidate_round_trips() -> None:
    candidate = _candidate(
        decision_id=None,
        condition=None,
        outcome_code=None,
        rule_family=UnifiedRuleFamily.CALCULATION,
    )
    restored = RuleCandidate.model_validate_json(candidate.to_stable_json())
    assert restored == candidate
    assert restored.decision_id is None
    assert restored.condition is None
