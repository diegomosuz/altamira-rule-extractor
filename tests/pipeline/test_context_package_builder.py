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
from altamira_extractor.contracts.enums import (
    ApplicabilityStatus,
    AttributionScope,
    BatchContextStatus,
    CompletenessStatus,
    InclusionReason,
)
from altamira_extractor.pipeline.context_package_builder import (
    ContextQuerySet,
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
        "condition": "A",
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


def _build(rows: dict[str, list[dict[str, Any]]], *, settings: Settings | None = None) -> Any:
    tx = _FakeTx(rows)
    packages = build_context_packages(
        tx, [_candidate()], queries=_query_set(), settings=settings or _settings()
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


# --- Q5a: scope al return_code null ---


def test_q5a_null_return_code_produces_empty_return_codes() -> None:
    rows = _happy_rows()
    rows["Q5A_MARKER"] = [_q5a_row(return_code=None)]
    packages, _ = _build(rows)
    assert packages[0].effects.return_codes == []


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
