"""Tests de los detectores V2 en shadow mode (Fase 5/6/7/8/9 de la
ampliacion semantica, `feat/v2-detectors-shadow-mode`):
`pipeline/v2_detectors.py`. Incluye los tres casos obligatorios de Fase
16 (A: propagacion MOVE literal->copia hacia return_code; B: nivel 88
SET condicion TO TRUE hacia return_code; C: cambio de estado en un
DataItem que no es return_code)."""

from __future__ import annotations

from altamira_extractor.contracts.canonical import (
    CanonicalConditionName,
    CanonicalConditionValue,
    CanonicalDataItem,
    CanonicalParagraph,
    CanonicalProgram,
)
from altamira_extractor.contracts.enums import (
    LocationKind,
    SourceFormat,
    StatementKind,
)
from altamira_extractor.contracts.v2_shadow_candidates import V2CandidateSupport, V2RuleType
from altamira_extractor.pipeline.v2_detectors import (
    DETECTOR_ID_LEVEL_88_RETURN_CODE,
    DETECTOR_ID_RETURN_CODE_PROPAGATION,
    DETECTOR_ID_STATE_CHANGE,
    STATE_CHANGE_DETECTOR_SCORE,
    _merge_candidate_evidence,
    candidate_id_for,
    detect_level_88_return_code,
    detect_return_code_propagation,
    detect_state_change,
)

from .v2_shadow_helpers import HASH, SRC, build_ctx, decision_node_id_for, make_stmt


def _program(
    *,
    program_name: str,
    data_items: list[CanonicalDataItem],
    paragraphs,
    condition_names=(),
) -> CanonicalProgram:
    return CanonicalProgram(
        program_name=program_name,
        source_file=SRC,
        source_hash=HASH,
        source_package_hash=HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        data_items=data_items,
        condition_names=list(condition_names),
        paragraphs=paragraphs,
    )


def _data_item(qualified_name: str, *, level: int = 1) -> CanonicalDataItem:
    return CanonicalDataItem(
        name=qualified_name, qualified_name=qualified_name, level=level,
        location_kind=LocationKind.UNKNOWN,
    )


# ---------------------------------------------------------------------------
# Caso obligatorio A (Fase 16): IF / MOVE literal / MOVE copia -> return_code
# ---------------------------------------------------------------------------


def _case_a_ctx():
    if_stmt = make_stmt(
        statement_id="P1::A::0::IF", kind=StatementKind.IF, line_start=10, expression="CONDICION"
    )
    s1 = make_stmt(
        statement_id="P1::A::1::MOVE", target_data_items=["WS-AUX"],
        variables_written=["WS-AUX"], assigned_literal="0005",
        parent_statement_id="P1::A::0::IF", branch_kind="THEN",
    )
    s2 = make_stmt(
        statement_id="P1::A::2::MOVE", target_data_items=["WS-COD-RETORNO"],
        variables_written=["WS-COD-RETORNO"], variables_read=["WS-AUX"],
        parent_statement_id="P1::A::0::IF", branch_kind="THEN",
    )
    paragraph = CanonicalParagraph(
        name="A", source_text="A.", location_kind=LocationKind.UNKNOWN,
        statements=[if_stmt, s1, s2],
        variables_read=["WS-AUX"], variables_written=["WS-AUX", "WS-COD-RETORNO"],
    )
    program = _program(
        program_name="PROG1",
        data_items=[_data_item("WS-AUX"), _data_item("WS-COD-RETORNO")],
        paragraphs=[paragraph],
    )
    return build_ctx(
        program=program,
        decisions=[("A", 10, "CONDICION")],
        data_item_tags={"WS-AUX": None, "WS-COD-RETORNO": "return_code"},
    )


def test_case_a_return_code_propagation_detects_one_deterministic_candidate() -> None:
    ctx = _case_a_ctx()
    candidates = detect_return_code_propagation(ctx)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.rule_type == V2RuleType.RETURN_CODE_RULE
    assert candidate.support == V2CandidateSupport.DETERMINISTIC
    assert candidate.resolved_literal == "0005"
    assert candidate.detector_score == 1.0
    assert candidate.decision_id == decision_node_id_for("PROG1", "A", 10, 1)
    assert candidate.target_qualified_name == "WS-COD-RETORNO"


def test_case_a_level_88_and_state_change_detect_nothing() -> None:
    ctx = _case_a_ctx()
    assert detect_level_88_return_code(ctx) == []
    # WS-AUX (no return_code) recibe un literal directo, no una copia:
    # STATE_CHANGE solo dispara sobre facts con literal ya resuelto.
    state_change_candidates = detect_state_change(ctx)
    assert len(state_change_candidates) == 1
    assert state_change_candidates[0].target_qualified_name == "WS-AUX"
    assert state_change_candidates[0].rule_type == V2RuleType.STATE_CHANGE_RULE


# ---------------------------------------------------------------------------
# Caso obligatorio B (Fase 16): SET condicion-88 TO TRUE -> return_code
# ---------------------------------------------------------------------------


def _case_b_ctx(*, values: list[CanonicalConditionValue] | None = None):
    if values is None:
        values = [CanonicalConditionValue(value="0005", location_kind=LocationKind.UNKNOWN)]
    condition = CanonicalConditionName(
        name="COD-CAMPO-INVALIDO", qualified_name="WS-COD-RETORNO.COD-CAMPO-INVALIDO",
        parent_name="WS-COD-RETORNO", parent_qualified_name="WS-COD-RETORNO",
        values=values, location_kind=LocationKind.UNKNOWN,
    )
    if_stmt = make_stmt(
        statement_id="P1::PARA::0::IF", kind=StatementKind.IF, line_start=10,
        expression="ERROR-DE-ENTRADA",
    )
    s1 = make_stmt(
        statement_id="P1::PARA::1::SET", kind=StatementKind.SET,
        target_data_items=["COD-CAMPO-INVALIDO"], variables_written=["COD-CAMPO-INVALIDO"],
        condition_name_target="COD-CAMPO-INVALIDO", condition_set_value=True,
        parent_statement_id="P1::PARA::0::IF", branch_kind="THEN",
    )
    paragraph = CanonicalParagraph(
        name="PARA", source_text="PARA.", location_kind=LocationKind.UNKNOWN,
        statements=[if_stmt, s1], variables_written=["COD-CAMPO-INVALIDO"],
    )
    program = _program(
        program_name="PROG1", data_items=[_data_item("WS-COD-RETORNO")],
        paragraphs=[paragraph], condition_names=[condition],
    )
    return build_ctx(
        program=program, decisions=[("PARA", 10, "ERROR-DE-ENTRADA")],
        data_item_tags={"WS-COD-RETORNO": "return_code"},
    )


def test_case_b_level_88_detects_one_deterministic_candidate() -> None:
    ctx = _case_b_ctx()
    candidates = detect_level_88_return_code(ctx)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.rule_type == V2RuleType.LEVEL_88_RETURN_CODE_RULE
    assert candidate.support == V2CandidateSupport.DETERMINISTIC
    assert candidate.resolved_literal == "0005"
    assert candidate.condition_name == "WS-COD-RETORNO.COD-CAMPO-INVALIDO"
    assert candidate.target_qualified_name == "WS-COD-RETORNO"


def test_case_b_return_code_propagation_also_fires_and_is_related_not_merged() -> None:
    """Fase 5 condicion #3 permite CONDITION_LITERAL como evidencia de
    RETURN_CODE_PROPAGATION; Fase 9 exige que ambos candidatos se
    conserven por separado (nunca se fusionan solo por compartir
    decision/target/literal si vienen de detectores distintos)."""
    ctx = _case_b_ctx()
    rc_candidates = detect_return_code_propagation(ctx)
    l88_candidates = detect_level_88_return_code(ctx)
    assert len(rc_candidates) == 1
    assert len(l88_candidates) == 1
    assert rc_candidates[0].candidate_id != l88_candidates[0].candidate_id
    assert rc_candidates[0].detector_id == DETECTOR_ID_RETURN_CODE_PROPAGATION
    assert l88_candidates[0].detector_id == DETECTOR_ID_LEVEL_88_RETURN_CODE
    assert rc_candidates[0].resolved_literal == l88_candidates[0].resolved_literal == "0005"


def test_case_b_multiple_values_blocks_level_88_candidate() -> None:
    ctx = _case_b_ctx(
        values=[
            CanonicalConditionValue(value="0005", location_kind=LocationKind.UNKNOWN),
            CanonicalConditionValue(value="0006", location_kind=LocationKind.UNKNOWN),
        ]
    )
    candidates = detect_level_88_return_code(ctx)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.support == V2CandidateSupport.BLOCKED
    assert candidate.resolved_literal is None
    assert "V2_LEVEL_88_VALUE_NOT_UNIQUE" in candidate.diagnostic_codes


def test_case_b_non_return_code_parent_is_ignored_by_level_88_but_caught_by_state_change() -> None:
    condition = CanonicalConditionName(
        name="COD-X", qualified_name="WS-ESTADO.COD-X",
        parent_name="WS-ESTADO", parent_qualified_name="WS-ESTADO",
        values=[CanonicalConditionValue(value="A", location_kind=LocationKind.UNKNOWN)],
        location_kind=LocationKind.UNKNOWN,
    )
    if_stmt = make_stmt(
        statement_id="P1::PARA::0::IF", kind=StatementKind.IF, line_start=10, expression="COND"
    )
    s1 = make_stmt(
        statement_id="P1::PARA::1::SET", kind=StatementKind.SET,
        target_data_items=["COD-X"], variables_written=["COD-X"],
        condition_name_target="COD-X", condition_set_value=True,
        parent_statement_id="P1::PARA::0::IF", branch_kind="THEN",
    )
    paragraph = CanonicalParagraph(
        name="PARA", source_text="PARA.", location_kind=LocationKind.UNKNOWN,
        statements=[if_stmt, s1], variables_written=["COD-X"],
    )
    program = _program(
        program_name="PROG1", data_items=[_data_item("WS-ESTADO")],
        paragraphs=[paragraph], condition_names=[condition],
    )
    ctx = build_ctx(
        program=program, decisions=[("PARA", 10, "COND")],
        data_item_tags={"WS-ESTADO": None},
    )
    assert detect_level_88_return_code(ctx) == []
    state_change = detect_state_change(ctx)
    assert len(state_change) == 1
    assert state_change[0].target_qualified_name == "WS-ESTADO"
    assert state_change[0].resolved_literal == "A"


# ---------------------------------------------------------------------------
# Caso obligatorio C (Fase 16): IF / MOVE 'A' TO WS-ESTADO (no return_code)
# ---------------------------------------------------------------------------


def _case_c_ctx():
    if_stmt = make_stmt(
        statement_id="P1::A::0::IF", kind=StatementKind.IF, line_start=5, expression="CONDICION"
    )
    s1 = make_stmt(
        statement_id="P1::A::1::MOVE", target_data_items=["WS-ESTADO"],
        variables_written=["WS-ESTADO"], assigned_literal="A",
        parent_statement_id="P1::A::0::IF", branch_kind="THEN",
    )
    paragraph = CanonicalParagraph(
        name="A", source_text="A.", location_kind=LocationKind.UNKNOWN,
        statements=[if_stmt, s1], variables_written=["WS-ESTADO"],
    )
    program = _program(
        program_name="PROG1", data_items=[_data_item("WS-ESTADO")], paragraphs=[paragraph]
    )
    return build_ctx(
        program=program, decisions=[("A", 5, "CONDICION")], data_item_tags={"WS-ESTADO": None}
    )


def test_case_c_state_change_detects_one_partial_candidate() -> None:
    ctx = _case_c_ctx()
    candidates = detect_state_change(ctx)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.rule_type == V2RuleType.STATE_CHANGE_RULE
    assert candidate.support == V2CandidateSupport.PARTIAL
    assert candidate.detector_score == STATE_CHANGE_DETECTOR_SCORE
    assert candidate.resolved_literal == "A"
    assert candidate.target_qualified_name == "WS-ESTADO"
    assert "V2_STATE_CHANGE_FUNCTIONAL_RELEVANCE_NOT_GUARANTEED" in candidate.diagnostic_codes


def test_case_c_return_code_and_level_88_detect_nothing() -> None:
    ctx = _case_c_ctx()
    assert detect_return_code_propagation(ctx) == []
    assert detect_level_88_return_code(ctx) == []


# ---------------------------------------------------------------------------
# Ausencia de decision: ningun detector activa (los tres exigen decision)
# ---------------------------------------------------------------------------


def test_literal_move_to_return_code_without_enclosing_decision_is_never_detected() -> None:
    s1 = make_stmt(
        statement_id="P1::A::1::MOVE", target_data_items=["WS-COD-RETORNO"],
        variables_written=["WS-COD-RETORNO"], assigned_literal="0009",
    )
    paragraph = CanonicalParagraph(
        name="A", source_text="A.", location_kind=LocationKind.UNKNOWN,
        statements=[s1], variables_written=["WS-COD-RETORNO"],
    )
    program = _program(
        program_name="PROG1", data_items=[_data_item("WS-COD-RETORNO")], paragraphs=[paragraph]
    )
    ctx = build_ctx(program=program, decisions=[], data_item_tags={"WS-COD-RETORNO": "return_code"})
    assert detect_return_code_propagation(ctx) == []
    assert detect_level_88_return_code(ctx) == []
    assert detect_state_change(ctx) == []


# ---------------------------------------------------------------------------
# BLOCKED: copia sin origen resoluble hacia return_code, bajo una decision
# ---------------------------------------------------------------------------


def test_unresolved_copy_to_return_code_under_decision_is_blocked() -> None:
    if_stmt = make_stmt(
        statement_id="P1::A::0::IF", kind=StatementKind.IF, line_start=30, expression="COND"
    )
    s1 = make_stmt(
        statement_id="P1::A::1::MOVE", target_data_items=["WS-COD-RETORNO"],
        variables_written=["WS-COD-RETORNO"], variables_read=["WS-SRC-DESCONOCIDA"],
        parent_statement_id="P1::A::0::IF", branch_kind="THEN",
    )
    paragraph = CanonicalParagraph(
        name="A", source_text="A.", location_kind=LocationKind.UNKNOWN,
        statements=[if_stmt, s1],
        variables_read=["WS-SRC-DESCONOCIDA"], variables_written=["WS-COD-RETORNO"],
    )
    program = _program(
        program_name="PROG1",
        data_items=[_data_item("WS-COD-RETORNO"), _data_item("WS-SRC-DESCONOCIDA")],
        paragraphs=[paragraph],
    )
    ctx = build_ctx(
        program=program, decisions=[("A", 30, "COND")],
        data_item_tags={"WS-COD-RETORNO": "return_code", "WS-SRC-DESCONOCIDA": None},
    )
    candidates = detect_return_code_propagation(ctx)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.support == V2CandidateSupport.BLOCKED
    assert candidate.resolved_literal is None
    assert "V2_RETURN_CODE_TARGET_NOT_RESOLVED" in candidate.diagnostic_codes


# ---------------------------------------------------------------------------
# candidate_id_for: determinismo (Fase 8)
# ---------------------------------------------------------------------------


def test_candidate_id_for_is_deterministic_for_identical_inputs() -> None:
    kwargs = dict(
        detector_id=DETECTOR_ID_RETURN_CODE_PROPAGATION, detector_version="1.0",
        program="PROG1", paragraph="A", decision_id="dec-1",
        anchor_statement_id="P1::A::1::MOVE", target_key="WS-COD-RETORNO",
        resolved_literal="0005",
    )
    assert candidate_id_for(**kwargs) == candidate_id_for(**kwargs)


def test_candidate_id_for_differs_by_detector_id() -> None:
    kwargs = dict(
        detector_version="1.0", program="PROG1", paragraph="A", decision_id="dec-1",
        anchor_statement_id="P1::A::1::MOVE", target_key="WS-COD-RETORNO",
        resolved_literal="0005",
    )
    id_rc = candidate_id_for(detector_id=DETECTOR_ID_RETURN_CODE_PROPAGATION, **kwargs)
    id_l88 = candidate_id_for(detector_id=DETECTOR_ID_LEVEL_88_RETURN_CODE, **kwargs)
    assert id_rc != id_l88
    assert id_rc.startswith(f"v2::{DETECTOR_ID_RETURN_CODE_PROPAGATION}::")
    assert id_l88.startswith(f"v2::{DETECTOR_ID_LEVEL_88_RETURN_CODE}::")


def test_candidate_id_for_differs_by_resolved_literal() -> None:
    kwargs = dict(
        detector_id=DETECTOR_ID_STATE_CHANGE, detector_version="1.0", program="PROG1",
        paragraph="A", decision_id="dec-1", anchor_statement_id="P1::A::1::MOVE",
        target_key="WS-ESTADO",
    )
    assert candidate_id_for(resolved_literal="A", **kwargs) != candidate_id_for(
        resolved_literal="B", **kwargs
    )


# ---------------------------------------------------------------------------
# _merge_candidate_evidence (Fase 9): union ordenada, nunca se descarta evidencia
# ---------------------------------------------------------------------------


def test_merge_candidate_evidence_unions_effect_and_fact_ids() -> None:
    ctx = _case_a_ctx()
    base = detect_return_code_propagation(ctx)[0]
    second = base.model_copy(
        update={
            "semantic_effect_ids": ["effect::extra::0"],
            "propagation_fact_ids": ["fact::extra::0"],
            "source_references": base.source_references,
        }
    )
    merged = _merge_candidate_evidence(base, second)
    assert set(base.semantic_effect_ids) | {"effect::extra::0"} == set(merged.semantic_effect_ids)
    assert set(base.propagation_fact_ids) | {"fact::extra::0"} == set(merged.propagation_fact_ids)
    assert merged.semantic_effect_ids == sorted(merged.semantic_effect_ids)
    assert merged.propagation_fact_ids == sorted(merged.propagation_fact_ids)


def test_merge_candidate_evidence_deduplicates_source_references() -> None:
    ctx = _case_a_ctx()
    base = detect_return_code_propagation(ctx)[0]
    merged = _merge_candidate_evidence(base, base)
    assert len(merged.source_references) == len(base.source_references)


def test_merge_candidate_evidence_unions_diagnostic_codes() -> None:
    ctx = _case_a_ctx()
    base = detect_return_code_propagation(ctx)[0].model_copy(
        update={
            "support": "BLOCKED",
            "resolved_literal": None,
            "diagnostic_codes": ["CODE_A"],
            "detector_score": 0.0,
        }
    )
    second = base.model_copy(update={"diagnostic_codes": ["CODE_B"]})
    merged = _merge_candidate_evidence(base, second)
    assert merged.diagnostic_codes == ["CODE_A", "CODE_B"]


# ---------------------------------------------------------------------------
# STATE_CHANGE: multiples targets bajo la misma decision -> candidatos distintos
# ---------------------------------------------------------------------------


def test_state_change_detects_independent_candidates_for_different_targets() -> None:
    if_stmt = make_stmt(
        statement_id="P1::A::0::IF", kind=StatementKind.IF, line_start=7, expression="COND"
    )
    s1 = make_stmt(
        statement_id="P1::A::1::MOVE", target_data_items=["WS-ESTADO"],
        variables_written=["WS-ESTADO"], assigned_literal="A",
        parent_statement_id="P1::A::0::IF", branch_kind="THEN",
    )
    s2 = make_stmt(
        statement_id="P1::A::2::MOVE", target_data_items=["WS-TIPO"],
        variables_written=["WS-TIPO"], assigned_literal="X",
        parent_statement_id="P1::A::0::IF", branch_kind="THEN",
    )
    paragraph = CanonicalParagraph(
        name="A", source_text="A.", location_kind=LocationKind.UNKNOWN,
        statements=[if_stmt, s1, s2], variables_written=["WS-ESTADO", "WS-TIPO"],
    )
    program = _program(
        program_name="PROG1",
        data_items=[_data_item("WS-ESTADO"), _data_item("WS-TIPO")],
        paragraphs=[paragraph],
    )
    ctx = build_ctx(
        program=program, decisions=[("A", 7, "COND")],
        data_item_tags={"WS-ESTADO": None, "WS-TIPO": None},
    )
    candidates = detect_state_change(ctx)
    assert len(candidates) == 2
    targets = {candidate.target_qualified_name for candidate in candidates}
    assert targets == {"WS-ESTADO", "WS-TIPO"}
    assert candidates == sorted(candidates, key=lambda candidate: candidate.candidate_id)
