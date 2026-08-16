"""Tests de ContextPackageBuilder (Q1-Q7, Prompt 10b) con una transaccion
Neo4j falsa (dispatch por texto exacto de query, ya que `effective_text`
no necesita ser Cypher real para estos tests unitarios — solo debe
coincidir con lo que el propio LoadedContextQuery de prueba declara). El
comportamiento real de las 9 queries contra Neo4j vive en
tests/neo4j_integration/.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from altamira_extractor.config import Settings
from altamira_extractor.contracts.candidate import RuleCandidate
from altamira_extractor.contracts.candidate_promotion_assessment import (
    CandidateSource,
    UnifiedRuleFamily,
)
from altamira_extractor.contracts.context_package import (
    BatchContext,
    CodeSliceEntry,
    Completeness,
    ContextPackage,
    ContextPackageCandidate,
    ContextPackageDecision,
    ContextPackageOperation,
    ContextPackageScope,
    DataContext,
    Effects,
    EvidenceEntry,
)
from altamira_extractor.contracts.enums import (
    ApplicabilityStatus,
    AttributionScope,
    BatchContextStatus,
    CompletenessStatus,
    InclusionReason,
)
from altamira_extractor.pipeline.context_package_builder import (
    ContextQuerySet,
    _validate_deterministic_integrity,
    build_context_packages,
)
from altamira_extractor.pipeline.cypher_query_loader import LoadedContextQuery
from altamira_extractor.pipeline.errors import ContextBuildError

_HASH_A = "a" * 64


def _loaded(marker: str, logical: str) -> LoadedContextQuery:
    return LoadedContextQuery(
        logical_query=logical,
        relative_path=f"queries/v1/{logical.lower()}.cypher",
        template_hash=_HASH_A,
        effective_text=marker,
        effective_query_hash=_HASH_A,
    )


def _query_set() -> ContextQuerySet:
    return ContextQuerySet(
        q1=_loaded("Q1_MARKER", "Q1"),
        q2=_loaded("Q2_MARKER", "Q2"),
        q3a=_loaded("Q3A_MARKER", "Q3A"),
        q3b=_loaded("Q3B_MARKER", "Q3B"),
        q4=_loaded("Q4_MARKER", "Q4"),
        q5a=_loaded("Q5A_MARKER", "Q5A"),
        q5b=_loaded("Q5B_MARKER", "Q5B"),
        q6=_loaded("Q6_MARKER", "Q6"),
        q7=_loaded("Q7_MARKER", "Q7"),
    )


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def __iter__(self) -> Any:
        return iter(self._rows)


class _FakeTx:
    def __init__(self, rows_by_marker: dict[str, list[dict[str, Any]]]) -> None:
        self._rows_by_marker = rows_by_marker
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def run(self, query_text: str, **params: Any) -> _FakeResult:
        self.calls.append((query_text, params))
        return _FakeResult(self._rows_by_marker.get(query_text, []))


def _candidate(**overrides: object) -> RuleCandidate:
    defaults: dict[str, object] = {
        "candidate_id": f"candidate::det::1.0::{_HASH_A}::dec-1",
        "paragraph_id": "para-1",
        "paragraph_name": "MAIN",
        "decision_id": "dec-1",
        "detector_id": "det",
        "detector_version": "1.0",
        "detector_score": 1.0,
        # Igual que outcome_code: coincide con _q4_row()'s condition por
        # diseno -- ambos derivan de la MISMA propiedad Decision.expression
        # del grafo en el sistema real (Q0 y Q4 leen d.expression/
        # dec.expression para el mismo decision_id), invariante ahora
        # exigida por _validate_deterministic_integrity
        # (CONDITION_PRESERVATION).
        "condition": "WS-COD = 'R001'",
        "outcome_code": "R001",
        "rule_type": None,
        "line_start": 10,
        "source_file": "01-codigo/cobol/PROG.cbl",
        "source_package_hash": _HASH_A,
    }
    defaults.update(overrides)
    return RuleCandidate(**defaults)  # type: ignore[arg-type]


def _q1_row(**overrides: object) -> dict[str, Any]:
    row: dict[str, Any] = {
        "country": "AR",
        "application": "Transferencias",
        "operation_logical": "OP-TRF-PROPIA",
        "operation_description": "desc",
        "program": "PROG1",
        "program_version": "1.0",
        "paragraph": "MAIN",
        "source_file": "01-codigo/cobol/PROG.cbl",
        "line_start": 10,
        "line_end": 20,
        "source_package_hash": _HASH_A,
    }
    row.update(overrides)
    return row


def _q2_rows() -> list[dict[str, Any]]:
    return [
        {
            "paragraph_id": "para-1",
            "paragraph_name": "MAIN",
            "source_file": "01-codigo/cobol/PROG.cbl",
            "source_text": "IF WS-COD = 'R001'",
            "line_start": 10,
            "line_end": 20,
            "inclusion_reason": "CANDIDATE",
        }
    ]


def _q4_row(**overrides: object) -> dict[str, Any]:
    row: dict[str, Any] = {
        "decision_id": "dec-1",
        "condition": "WS-COD = 'R001'",
        "normalized_condition": "WS-COD = 'R001'",
        "operands_json": "[]",
        "rule_type": None,
        "outcome_code": "R001",
        "line_start": 10,
        "line_end": 10,
    }
    row.update(overrides)
    return row


def _q5a_row(**overrides: object) -> dict[str, Any]:
    row: dict[str, Any] = {"return_code": "R001", "triggered_when": "A", "decision_id": "dec-1"}
    row.update(overrides)
    return row


def _happy_rows() -> dict[str, list[dict[str, Any]]]:
    return {
        "Q1_MARKER": [_q1_row()],
        "Q2_MARKER": _q2_rows(),
        "Q3A_MARKER": [],
        "Q3B_MARKER": [{"tables_read": []}],
        "Q4_MARKER": [_q4_row()],
        "Q5A_MARKER": [_q5a_row()],
        "Q5B_MARKER": [{"attributed": [], "program_context": []}],
        "Q6_MARKER": [],
        "Q7_MARKER": [],
    }


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]


def _build(
    rows: dict[str, list[dict[str, Any]]],
    *,
    settings: Settings | None = None,
    candidate: RuleCandidate | None = None,
) -> Any:
    tx = _FakeTx(rows)
    packages = build_context_packages(
        tx, [candidate or _candidate()], queries=_query_set(), settings=settings or _settings()
    )
    return packages, tx


# --- flujo feliz ---


def test_happy_path_builds_valid_context_package() -> None:
    packages, tx = _build(_happy_rows())
    assert len(packages) == 1
    package = packages[0]
    assert package.scope.country == "AR"
    assert package.code_slice[0].inclusion_reason == InclusionReason.CANDIDATE
    assert package.decision.outcome_code == "R001"
    assert package.effects.return_codes[0].code == "R001"
    assert package.completeness.D1 == CompletenessStatus.COMPLETE
    assert package.completeness.D6 == CompletenessStatus.NOT_AVAILABLE
    assert package.completeness.D7 == CompletenessStatus.NOT_AVAILABLE


def test_queries_receive_paragraph_id_not_candidate_id() -> None:
    _, tx = _build(_happy_rows())
    q1_call = next(call for call in tx.calls if call[0] == "Q1_MARKER")
    assert q1_call[1] == {"paragraph_id": "para-1"}


def test_q4_and_q5a_receive_decision_id() -> None:
    _, tx = _build(_happy_rows())
    q4_call = next(call for call in tx.calls if call[0] == "Q4_MARKER")
    assert q4_call[1] == {"paragraph_id": "para-1", "decision_id": "dec-1"}
    q5a_call = next(call for call in tx.calls if call[0] == "Q5A_MARKER")
    assert q5a_call[1] == {"paragraph_id": "para-1", "decision_id": "dec-1"}


# --- Fase 15B3-C2-B2: CALCULATION incondicional (decision_id=None) ---


def test_unconditional_calculation_skips_q4_and_q5a() -> None:
    """Q4/Q5a NUNCA se invocan con un decision_id fabricado -- para un
    CALCULATION incondicional simplemente no se ejecutan. Q1/Q2/Q3a/Q3b/
    Q5b/Q6/Q7 (scopeadas unicamente por paragraph_id) siguen ejecutandose
    sin cambios."""
    candidate = _candidate(
        rule_family=UnifiedRuleFamily.CALCULATION,
        decision_id=None,
        condition=None,
        outcome_code=None,
        evidence_ids=["effect::PROG1::A::stmt::COMPUTE_VALUE::0"],
    )
    rows = _happy_rows()
    del rows["Q4_MARKER"]
    del rows["Q5A_MARKER"]
    tx = _FakeTx(rows)
    packages = build_context_packages(tx, [candidate], queries=_query_set(), settings=_settings())

    assert len(packages) == 1
    package = packages[0]
    assert package.decision is None
    assert package.candidate.decision_id is None
    assert package.effects.return_codes == []
    assert package.completeness.D4 == CompletenessStatus.NOT_AVAILABLE

    called_markers = {call[0] for call in tx.calls}
    assert "Q4_MARKER" not in called_markers
    assert "Q5A_MARKER" not in called_markers
    assert "Q1_MARKER" in called_markers
    assert "Q2_MARKER" in called_markers
    assert "Q5B_MARKER" in called_markers


def test_unconditional_calculation_still_gets_table_effects_via_q5b() -> None:
    """Q5b (table_effects) esta scopeada solo por paragraph_id -- se
    conserva sin cambios para un CALCULATION incondicional, igual que
    para cualquier otro candidato de ese Paragraph."""
    candidate = _candidate(
        rule_family=UnifiedRuleFamily.CALCULATION,
        decision_id=None,
        condition=None,
        outcome_code=None,
        evidence_ids=["effect::PROG1::A::stmt::COMPUTE_VALUE::0"],
    )
    rows = _happy_rows()
    del rows["Q4_MARKER"]
    del rows["Q5A_MARKER"]
    rows["Q5B_MARKER"] = [
        {
            "attributed": [
                {
                    "table_id": "table-1",
                    "table_name": "CUENTAS",
                    "operation": "UPDATES",
                    "attribution_scope": "DIRECT",
                    "source_file": "01-codigo/cobol/PROG.cbl",
                    "line_start": 10,
                    "line_end": 10,
                }
            ],
            "program_context": [],
        }
    ]
    tx = _FakeTx(rows)
    packages = build_context_packages(tx, [candidate], queries=_query_set(), settings=_settings())

    assert len(packages[0].effects.table_effects) == 1
    assert packages[0].effects.table_effects[0].table == "CUENTAS"


# --- Q1: cardinalidad ---


def test_q1_zero_rows_is_fatal() -> None:
    rows = _happy_rows()
    rows["Q1_MARKER"] = []
    with pytest.raises(ContextBuildError):
        _build(rows)


def test_q1_multiple_rows_is_fatal() -> None:
    rows = _happy_rows()
    rows["Q1_MARKER"] = [_q1_row(), _q1_row(program="OTHER")]
    with pytest.raises(ContextBuildError):
        _build(rows)


# --- Q4: cardinalidad, operands_json, rule_type ---


def test_q4_zero_rows_is_fatal() -> None:
    rows = _happy_rows()
    rows["Q4_MARKER"] = []
    with pytest.raises(ContextBuildError):
        _build(rows)


def test_q4_multiple_rows_is_fatal() -> None:
    rows = _happy_rows()
    rows["Q4_MARKER"] = [_q4_row(), _q4_row()]
    with pytest.raises(ContextBuildError):
        _build(rows)


def test_q4_operands_json_is_parsed() -> None:
    rows = _happy_rows()
    rows["Q4_MARKER"] = [_q4_row(operands_json='["WS-COD", "WS-LIMITE"]')]
    packages, _ = _build(rows)
    assert packages[0].decision.operands == ["WS-COD", "WS-LIMITE"]


def test_q4_invalid_operands_json_is_fatal() -> None:
    rows = _happy_rows()
    rows["Q4_MARKER"] = [_q4_row(operands_json="{not valid json")]
    with pytest.raises(ContextBuildError):
        _build(rows)


def test_q4_rule_type_none_is_preserved() -> None:
    rows = _happy_rows()
    rows["Q4_MARKER"] = [_q4_row(rule_type=None)]
    packages, _ = _build(rows)
    assert packages[0].decision.rule_type is None


# --- D4/D5 se derivan de candidate.outcome_code, no de la fila Q4/Q5a
# (regresion: bug de perdida silenciosa 06 -> 07 en candidatos V2 cuyo
# outcome_code se resuelve en memoria y nunca se escribe en
# Decision.outcome_code del grafo, p.ej. V2_LEVEL_88_RETURN_CODE) ---


def test_v2_level_88_return_code_outcome_survives_null_graph_row() -> None:
    candidate = _candidate(
        detector_id="V2_LEVEL_88_RETURN_CODE",
        rule_family=UnifiedRuleFamily.LEVEL_88_RETURN_CODE,
        outcome_code="9999",
    )
    rows = _happy_rows()
    # El grafo nunca resolvio esta Decision (mecanismo V2 desacoplado):
    # Q4/Q5a devuelven outcome_code/return_code en None.
    rows["Q4_MARKER"] = [_q4_row(outcome_code=None)]
    rows["Q5A_MARKER"] = [_q5a_row(return_code=None)]
    packages, _ = _build(rows, candidate=candidate)
    assert packages[0].decision.outcome_code == "9999"
    assert [effect.code for effect in packages[0].effects.return_codes] == ["9999"]


def test_v2_level_88_return_code_outcome_survives_null_graph_row_second_value() -> None:
    candidate = _candidate(
        detector_id="V2_LEVEL_88_RETURN_CODE",
        rule_family=UnifiedRuleFamily.LEVEL_88_RETURN_CODE,
        outcome_code="0003",
    )
    rows = _happy_rows()
    rows["Q4_MARKER"] = [_q4_row(outcome_code=None)]
    rows["Q5A_MARKER"] = [_q5a_row(return_code=None)]
    packages, _ = _build(rows, candidate=candidate)
    assert packages[0].decision.outcome_code == "0003"
    assert [effect.code for effect in packages[0].effects.return_codes] == ["0003"]


def test_candidate_outcome_code_none_stays_none_even_if_graph_row_has_value() -> None:
    # No inferir/recuperar un outcome_code que el candidato (06) no afirma,
    # aunque la fila Q4/Q5a traiga un valor (p.ej. otra Decision resuelta
    # por MOVE-literal en el mismo Paragraph).
    candidate = _candidate(outcome_code=None)
    rows = _happy_rows()
    rows["Q4_MARKER"] = [_q4_row(outcome_code="R999")]
    rows["Q5A_MARKER"] = [_q5a_row(return_code="R999")]
    packages, _ = _build(rows, candidate=candidate)
    assert packages[0].decision.outcome_code is None
    assert packages[0].effects.return_codes == []


def test_v1_candidate_outcome_code_matching_graph_row_is_unchanged() -> None:
    # Comportamiento V1 preexistente: candidate.outcome_code coincide
    # tautologicamente con la fila del grafo (mismo origen, ver
    # queries/v1/q0_candidates.cypher) -- no debe cambiar con el fix.
    packages, _ = _build(_happy_rows())
    assert packages[0].decision.outcome_code == "R001"
    assert [effect.code for effect in packages[0].effects.return_codes] == ["R001"]


def test_rule_family_is_never_coerced_into_rule_type() -> None:
    candidate = _candidate(
        detector_id="V2_LEVEL_88_RETURN_CODE",
        rule_family=UnifiedRuleFamily.LEVEL_88_RETURN_CODE,
        outcome_code="9999",
    )
    rows = _happy_rows()
    rows["Q4_MARKER"] = [_q4_row(outcome_code=None, rule_type=None)]
    rows["Q5A_MARKER"] = [_q5a_row(return_code=None)]
    packages, _ = _build(rows, candidate=candidate)
    assert packages[0].decision.rule_type is None


# --- Q5a: scope al return_code null ---


def test_q5a_null_return_code_produces_empty_return_codes() -> None:
    # El guard vive en candidate.outcome_code (no en la fila Q5a, ver
    # test_context_decision_and_effects_source_outcome_from_candidate_*
    # abajo) -- este caso cubre "sin outcome_code en absoluto", tanto en
    # el candidato como en el grafo.
    rows = _happy_rows()
    rows["Q5A_MARKER"] = [_q5a_row(return_code=None)]
    rows["Q4_MARKER"] = [_q4_row(outcome_code=None)]
    packages, _ = _build(rows, candidate=_candidate(outcome_code=None))
    assert packages[0].effects.return_codes == []
    assert packages[0].decision.outcome_code is None


def test_q5a_wrong_row_count_is_fatal() -> None:
    rows = _happy_rows()
    rows["Q5A_MARKER"] = []
    with pytest.raises(ContextBuildError):
        _build(rows)


# --- Q5b: attribution_scope / approved_for_rule_text ---


def _table_effect_item(**overrides: object) -> dict[str, Any]:
    item: dict[str, Any] = {
        "table_id": "table::AR::default::CUENTAS",
        "table_name": "CUENTAS",
        "operation": "UPDATES",
        "attribution_scope": "DIRECT",
        "paragraph_id": "para-1",
        "source_file": "01-codigo/cobol/PROG.cbl",
        "line_start": 15,
        "line_end": 15,
    }
    item.update(overrides)
    return item


def test_q5b_direct_effect_is_approved() -> None:
    rows = _happy_rows()
    rows["Q5B_MARKER"] = [{"attributed": [_table_effect_item()], "program_context": []}]
    packages, _ = _build(rows)
    effect = packages[0].effects.table_effects[0]
    assert effect.attribution_scope == AttributionScope.DIRECT
    assert effect.approved_for_rule_text is True


def test_q5b_dependency_slice_effect_is_approved() -> None:
    rows = _happy_rows()
    item = _table_effect_item(attribution_scope="DEPENDENCY_SLICE", paragraph_id="para-2")
    rows["Q5B_MARKER"] = [{"attributed": [item], "program_context": []}]
    packages, _ = _build(rows)
    effect = packages[0].effects.table_effects[0]
    assert effect.attribution_scope == AttributionScope.DEPENDENCY_SLICE
    assert effect.approved_for_rule_text is True


def test_q5b_program_context_effect_is_never_approved() -> None:
    rows = _happy_rows()
    item = _table_effect_item(attribution_scope="PROGRAM_CONTEXT", paragraph_id="para-9")
    rows["Q5B_MARKER"] = [{"attributed": [], "program_context": [item]}]
    packages, _ = _build(rows)
    effect = packages[0].effects.table_effects[0]
    assert effect.attribution_scope == AttributionScope.PROGRAM_CONTEXT
    assert effect.approved_for_rule_text is False


# --- Q6: batch completeness ---


def test_q6_empty_is_not_available_without_warning() -> None:
    packages, _ = _build(_happy_rows())
    assert packages[0].batch_context.status == BatchContextStatus.NOT_AVAILABLE
    assert packages[0].batch_context.downstream_jobs == []


def test_q6_complete_job_is_complete() -> None:
    rows = _happy_rows()
    rows["Q6_MARKER"] = [
        {
            "job_id": "batch::AR::CTRLM::JOB1",
            "job_name": "JOB1",
            "schedule": "daily",
            "window_start": "02:00",
            "window_end": "03:00",
            "preceded_by": None,
            "triggers_next": None,
        }
    ]
    packages, _ = _build(rows)
    assert packages[0].batch_context.status == BatchContextStatus.COMPLETE


def test_q6_missing_job_name_is_partial() -> None:
    rows = _happy_rows()
    rows["Q6_MARKER"] = [
        {
            "job_id": "batch::AR::CTRLM::JOB1",
            "job_name": None,
            "schedule": None,
            "window_start": None,
            "window_end": None,
            "preceded_by": None,
            "triggers_next": None,
        }
    ]
    packages, _ = _build(rows)
    assert packages[0].batch_context.status == BatchContextStatus.PARTIAL


# --- Q7: glosario, dedup, D7 completeness ---


def _q7_row(**overrides: object) -> dict[str, Any]:
    row: dict[str, Any] = {
        "data_item_id": "program::AR::op::PROG::1::abc::data::WS-MONTO",
        "technical_name": "WS-MONTO",
        "semantic_tag": "amount",
        "domain_term_id": "term::1.0::requested_amount",
        "functional_name": "importe solicitado",
        "definition": "Importe solicitado",
        "entity_type": "monetary_amount",
        "authoritative_source": "V1 controlled glossary",
        "source_kind": "CURATED_CONFIG",
        "catalog_version": "1.0",
        "confidence": 1.0,
    }
    row.update(overrides)
    return row


def test_q7_empty_produces_not_available_completeness() -> None:
    packages, _ = _build(_happy_rows())
    assert packages[0].domain_glossary == []
    assert packages[0].completeness.D7 == CompletenessStatus.NOT_AVAILABLE


def test_q7_populates_glossary_and_completeness() -> None:
    rows = _happy_rows()
    rows["Q7_MARKER"] = [_q7_row()]
    packages, _ = _build(rows)
    assert len(packages[0].domain_glossary) == 1
    assert packages[0].completeness.D7 == CompletenessStatus.COMPLETE


def test_q7_same_domain_term_for_two_data_items_keeps_two_entries() -> None:
    rows = _happy_rows()
    rows["Q7_MARKER"] = [
        _q7_row(data_item_id="...::data::WS-A", technical_name="WS-A"),
        _q7_row(data_item_id="...::data::WS-B", technical_name="WS-B"),
    ]
    packages, _ = _build(rows)
    assert len(packages[0].domain_glossary) == 2


def test_q7_duplicate_pair_with_identical_content_consolidates() -> None:
    rows = _happy_rows()
    rows["Q7_MARKER"] = [_q7_row(), _q7_row()]
    packages, _ = _build(rows)
    assert len(packages[0].domain_glossary) == 1


def test_q7_duplicate_pair_with_different_content_is_fatal() -> None:
    rows = _happy_rows()
    rows["Q7_MARKER"] = [_q7_row(), _q7_row(functional_name="otro nombre")]
    with pytest.raises(ContextBuildError):
        _build(rows)


# --- Q3a: ParameterEntry, dedup por parameter_entry_id ---


def _q3a_row(
    entries: list[dict[str, Any]], access_evidence: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "parameter_table_id": "table::AR::default::PARM01",
        "parameter_table": "PARM01",
        "snapshot_date": "2026-05-15",
        "entries": entries,
        "access_evidence": access_evidence,
    }


def _entry(
    entry_id: str, *, row_number: int, row_hash: str, values: dict[str, Any]
) -> dict[str, Any]:
    return {
        "parameter_entry_id": entry_id,
        "row_number": row_number,
        "row_hash": row_hash,
        "raw_row_json": "{}",
        "normalized_row_json": json.dumps(values),
    }


def _access(predicate_text: str | None) -> dict[str, Any]:
    return {
        "paragraph_id": "para-1",
        "predicate_text": predicate_text,
        "host_variables_json": None,
        "source_file": "01-codigo/cobol/PROG.cbl",
        "line_start": 30,
        "line_end": 30,
    }


def test_q3a_two_entries_with_same_row_hash_different_ids_are_both_kept() -> None:
    entries = [
        _entry("pe-1", row_number=1, row_hash="hash-x", values={"COD": "R001"}),
        _entry("pe-2", row_number=2, row_hash="hash-x", values={"COD": "R002"}),
    ]
    rows = _happy_rows()
    rows["Q3A_MARKER"] = [_q3a_row(entries, [_access(None)])]
    packages, _ = _build(rows)
    table = packages[0].data_context.parameter_tables[0]
    assert len(table.applicable_rows) + len(table.context_rows) == 2


def test_q3a_same_entry_id_inconsistent_content_is_fatal() -> None:
    entries = [
        _entry("pe-1", row_number=1, row_hash="hash-x", values={"COD": "R001"}),
        _entry("pe-1", row_number=2, row_hash="hash-y", values={"COD": "R002"}),
    ]
    rows = _happy_rows()
    rows["Q3A_MARKER"] = [_q3a_row(entries, [_access(None)])]
    with pytest.raises(ContextBuildError):
        _build(rows)


def test_q3a_exact_predicate_marks_matching_entry_applicable() -> None:
    entries = [
        _entry("pe-1", row_number=1, row_hash="hash-x", values={"COD": "R001"}),
        _entry("pe-2", row_number=2, row_hash="hash-y", values={"COD": "R002"}),
    ]
    rows = _happy_rows()
    rows["Q3A_MARKER"] = [_q3a_row(entries, [_access("COD = 'R001'")])]
    packages, _ = _build(rows)
    table = packages[0].data_context.parameter_tables[0]
    assert table.applicability_status == ApplicabilityStatus.EXACT
    assert [row.parameter_entry_id for row in table.applicable_rows] == ["pe-1"]
    assert [row.parameter_entry_id for row in table.context_rows] == ["pe-2"]


def test_q3a_no_predicate_is_unresolved() -> None:
    entries = [_entry("pe-1", row_number=1, row_hash="hash-x", values={"COD": "R001"})]
    rows = _happy_rows()
    rows["Q3A_MARKER"] = [_q3a_row(entries, [_access(None)])]
    packages, _ = _build(rows)
    table = packages[0].data_context.parameter_tables[0]
    assert table.applicability_status == ApplicabilityStatus.UNRESOLVED
    assert table.applicable_rows == []


def test_q3a_exact_with_zero_matches_stays_exact_not_partial() -> None:
    entries = [_entry("pe-1", row_number=1, row_hash="hash-x", values={"COD": "R999"})]
    rows = _happy_rows()
    rows["Q3A_MARKER"] = [_q3a_row(entries, [_access("COD = 'R001'")])]
    packages, _ = _build(rows)
    table = packages[0].data_context.parameter_tables[0]
    assert table.applicability_status == ApplicabilityStatus.EXACT
    assert table.applicable_rows == []
    assert len(table.context_rows) == 1


# --- limites configurados: error fatal, nunca truncamiento ---


def test_max_code_slice_paragraphs_exceeded_is_fatal() -> None:
    rows = _happy_rows()
    rows["Q2_MARKER"] = _q2_rows() + [
        {
            "paragraph_id": "para-2",
            "paragraph_name": "OTHER",
            "source_file": "01-codigo/cobol/PROG.cbl",
            "source_text": "MOVE 1 TO WS-X",
            "line_start": 30,
            "line_end": 30,
            "inclusion_reason": "DATA_DEPENDENCY",
        }
    ]
    settings = _settings(max_code_slice_paragraphs=1)
    with pytest.raises(ContextBuildError):
        _build(rows, settings=settings)


def test_max_transactional_tables_exceeded_is_fatal() -> None:
    rows = _happy_rows()
    rows["Q3B_MARKER"] = [
        {
            "tables_read": [
                {
                    "table_id": "table::AR::default::CUENTAS",
                    "table_name": "CUENTAS",
                    "paragraph_id": "para-1",
                    "source_file": "x.cbl",
                    "line_start": 1,
                    "line_end": 1,
                },
                {
                    "table_id": "table::AR::default::MOVIMIENTOS",
                    "table_name": "MOVIMIENTOS",
                    "paragraph_id": "para-1",
                    "source_file": "x.cbl",
                    "line_start": 2,
                    "line_end": 2,
                },
            ]
        }
    ]
    settings = _settings(max_transactional_tables=1)
    with pytest.raises(ContextBuildError):
        _build(rows, settings=settings)


def test_max_parameter_entries_per_context_exceeded_is_fatal() -> None:
    entries = [
        _entry("pe-1", row_number=1, row_hash="hash-x", values={"COD": "R001"}),
        _entry("pe-2", row_number=2, row_hash="hash-y", values={"COD": "R002"}),
    ]
    rows = _happy_rows()
    rows["Q3A_MARKER"] = [_q3a_row(entries, [_access(None)])]
    settings = _settings(max_parameter_entries_per_context=1)
    with pytest.raises(ContextBuildError):
        _build(rows, settings=settings)


# =====================================================================
# Deterministic integrity hardening (post-v1.17.0): suite parametrizada
# de preservacion 06 -> 07 sobre TODAS las familias PRODUCTIVE_RULE
# actuales, mas los casos adversariales de autoridad de fuente. Nunca
# compara contra la fila Q4/Q5a (el sistema real puede traer null o un
# valor de una computacion V1-only no relacionada, ver docstring de
# _validate_deterministic_integrity) -- solo confirma que
# candidate.outcome_code/candidate.condition, ya autoritativos, llegan
# intactos al ContextPackage resultante.
# =====================================================================

_FAMILY_MATRIX = [
    pytest.param(
        {
            "candidate_source": CandidateSource.V1,
            "rule_family": UnifiedRuleFamily.RETURN_CODE,
            "detector_id": "q0-return-code-decision",
            "outcome_code": "R001",
        },
        id="V1_RETURN_CODE",
    ),
    pytest.param(
        {
            "candidate_source": CandidateSource.V2,
            # V2_RETURN_CODE_PROPAGATION normaliza a la MISMA rule_family
            # que V1 (ver enhanced_candidate_integration.py
            # ::_LOCAL_RULE_FAMILY_BY_TYPE) -- confirmado contra Catherine
            # real (corregido/app-actual): son detector_id distintos,
            # nunca una rule_family separada.
            "rule_family": UnifiedRuleFamily.RETURN_CODE,
            "detector_id": "V2_RETURN_CODE_PROPAGATION",
            "outcome_code": "0002",
        },
        id="V2_RETURN_CODE_PROPAGATION",
    ),
    pytest.param(
        {
            "candidate_source": CandidateSource.V2,
            "rule_family": UnifiedRuleFamily.LEVEL_88_RETURN_CODE,
            "detector_id": "V2_LEVEL_88_RETURN_CODE",
            "outcome_code": "9999",
        },
        id="V2_LEVEL_88_RETURN_CODE",
    ),
    pytest.param(
        {
            "candidate_source": CandidateSource.V2,
            "rule_family": UnifiedRuleFamily.STATE_TRANSITION,
            "detector_id": "V2_STATE_TRANSITION",
            "outcome_code": "R",
        },
        id="STATE_TRANSITION",
    ),
    pytest.param(
        {
            "candidate_source": CandidateSource.V2,
            "rule_family": UnifiedRuleFamily.CALCULATION,
            "detector_id": "V2_CALCULATION",
            "outcome_code": None,
        },
        id="CALCULATION_conditioned",
    ),
]


@pytest.mark.parametrize("candidate_kwargs", _FAMILY_MATRIX)
def test_family_matrix_authoritative_candidate_survives_null_graph_row(
    candidate_kwargs: dict[str, object],
) -> None:
    """Familia matrix (Fase 3 del hardening): para cada familia
    productiva actual, un candidato con outcome_code/condition ya
    resueltos debe sobrevivir aunque el grafo (Q4/Q5a) no tenga el
    outcome_code (null -- el caso real V2, confirmado empiricamente
    contra Catherine)."""
    candidate = _candidate(**candidate_kwargs)
    rows = _happy_rows()
    rows["Q4_MARKER"] = [_q4_row(outcome_code=None)]
    rows["Q5A_MARKER"] = [_q5a_row(return_code=None)]
    packages, _ = _build(rows, candidate=candidate)
    package = packages[0]

    assert package.candidate.candidate_id == candidate.candidate_id
    assert package.decision is not None
    assert package.decision.expression == candidate.condition
    if candidate.outcome_code is None:
        assert package.decision.outcome_code is None
        assert package.effects.return_codes == []
    else:
        assert package.decision.outcome_code == candidate.outcome_code
        assert [e.code for e in package.effects.return_codes] == [candidate.outcome_code]
    # rule_family nunca se filtra a rule_type, para ninguna familia.
    assert package.decision.rule_type is None


@pytest.mark.parametrize("candidate_kwargs", _FAMILY_MATRIX)
def test_family_matrix_authoritative_candidate_wins_over_contradictory_graph_row(
    candidate_kwargs: dict[str, object],
) -> None:
    """Caso adversarial critico (Fase 7): el grafo trae un outcome_code
    CONTRADICTORIO (no solo null, p.ej. de otra Decision resuelta por
    MOVE-literal en el mismo Paragraph). Decision contractual explicita
    (ver docstring de _validate_deterministic_integrity): el candidato
    es autoritativo de forma INCONDICIONAL -- el grafo nunca gana, y una
    discrepancia aqui nunca dispara DETERMINISTIC_INTEGRITY_VIOLATION
    (esa proteccion existe para PERDIDA, no para que el grafo tenga una
    computacion V1-only distinta, algo esperado y normal para V2)."""
    candidate = _candidate(**candidate_kwargs)
    rows = _happy_rows()
    rows["Q4_MARKER"] = [_q4_row(outcome_code="CONTRADICTORY")]
    rows["Q5A_MARKER"] = [_q5a_row(return_code="CONTRADICTORY")]
    packages, _ = _build(rows, candidate=candidate)
    package = packages[0]

    if candidate.outcome_code is None:
        assert package.decision.outcome_code is None
        assert package.effects.return_codes == []
    else:
        assert package.decision.outcome_code == candidate.outcome_code
        assert package.decision.outcome_code != "CONTRADICTORY"
        assert [e.code for e in package.effects.return_codes] == [candidate.outcome_code]


def test_deterministic_integrity_violation_fails_closed_if_decision_ever_diverged() -> None:
    """Nunca deberia poder ocurrir por construccion (_build_decision ya
    usa candidate.outcome_code incondicionalmente) -- esta prueba llama
    al validador directamente para probar el mecanismo de fail-closed en
    si mismo, independiente de que ninguna ruta real del builder pueda
    disparar esta condicion hoy."""
    candidate = _candidate(outcome_code="9999")
    package = ContextPackage(
        schema_version="2.0",
        candidate=ContextPackageCandidate(
            candidate_id=candidate.candidate_id,
            decision_id=candidate.decision_id,
            detector_id=candidate.detector_id,
            detector_version=candidate.detector_version,
            detector_score=candidate.detector_score,
        ),
        scope=ContextPackageScope(
            country="AR",
            application="Transferencias",
            operation=ContextPackageOperation(logical_name="OP", description="desc"),
            program="PROG1",
            program_version="1.0",
            paragraph="MAIN",
            source_file="01-codigo/cobol/PROG.cbl",
            line_start=10,
            line_end=20,
            source_package_hash=_HASH_A,
        ),
        code_slice=[
            CodeSliceEntry(
                paragraph_id=candidate.paragraph_id,
                paragraph=candidate.paragraph_name,
                source_file="01-codigo/cobol/PROG.cbl",
                source_text="IF WS-COD = 'R001'",
                line_start=10,
                line_end=10,
                inclusion_reason=InclusionReason.CANDIDATE,
                evidence_ids=["evidence::0000000000000000"],
            )
        ],
        data_context=DataContext(parameter_tables=[], transactional_tables_read=[]),
        # BUG SIMULADO: decision.outcome_code deliberadamente distinto de
        # candidate.outcome_code -- nunca alcanzable por el builder real
        # tras el fix, pero prueba que el validador SI lo detecta.
        decision=ContextPackageDecision(
            expression=candidate.condition or "X",
            normalized_expression=candidate.condition or "X",
            operands=[],
            rule_type=None,
            outcome_code="WRONG",
            evidence_ids=[],
        ),
        effects=Effects(return_codes=[], table_effects=[]),
        batch_context=BatchContext(status=BatchContextStatus.NOT_AVAILABLE, downstream_jobs=[]),
        domain_glossary=[],
        evidence=[
            EvidenceEntry(
                evidence_id="evidence::0000000000000000",
                kind="code_slice",
                source_file="01-codigo/cobol/PROG.cbl",
                line_start=10,
                line_end=10,
                source_package_hash=_HASH_A,
            )
        ],
        completeness=Completeness(
            D1=CompletenessStatus.COMPLETE,
            D2=CompletenessStatus.COMPLETE,
            D3=CompletenessStatus.NOT_AVAILABLE,
            D4=CompletenessStatus.COMPLETE,
            D5=CompletenessStatus.NOT_AVAILABLE,
            D6=CompletenessStatus.NOT_AVAILABLE,
            D7=CompletenessStatus.NOT_AVAILABLE,
        ),
    )
    with pytest.raises(ContextBuildError, match="OUTCOME_CODE_PRESERVATION"):
        _validate_deterministic_integrity(candidate, package)
