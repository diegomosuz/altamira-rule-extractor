"""Tests del ejecutor puro de detectores V2 (Fase 10/11 de la ampliacion
semantica, `feat/v2-detectors-shadow-mode`): `pipeline/v2_shadow_detector.py`.
Cubre generacion de comparaciones V1/V2 (MATCHED/V1_ONLY/V2_ONLY/
RELATED_NOT_EQUIVALENT), agregacion del summary y determinismo byte a
byte de `run_v2_shadow_detection`."""

from __future__ import annotations

from altamira_extractor.contracts.candidate import CandidateArtifact, RuleCandidate
from altamira_extractor.contracts.canonical import (
    CanonicalConditionName,
    CanonicalConditionValue,
    CanonicalDataItem,
    CanonicalParagraph,
    CanonicalProgram,
)
from altamira_extractor.contracts.enums import (
    CandidateStatus,
    LocationKind,
    SourceFormat,
    StatementKind,
)
from altamira_extractor.contracts.v2_shadow_candidates import V1V2ComparisonStatus
from altamira_extractor.pipeline.v2_shadow_detector import run_v2_shadow_detection

from .v2_shadow_helpers import (
    HASH,
    SRC,
    build_ctx,
    decision_node_id_for,
    make_stmt,
    paragraph_node_id,
)


def _program_with_decision(*, line_start: int = 10, literal: str = "0005") -> CanonicalProgram:
    if_stmt = make_stmt(
        statement_id="P1::A::0::IF",
        kind=StatementKind.IF,
        line_start=line_start,
        expression="CONDICION",
    )
    move = make_stmt(
        statement_id="P1::A::1::MOVE",
        target_data_items=["WS-COD-RETORNO"],
        variables_written=["WS-COD-RETORNO"],
        assigned_literal=literal,
        parent_statement_id="P1::A::0::IF",
        branch_kind="THEN",
    )
    paragraph = CanonicalParagraph(
        name="A",
        source_text="A.",
        location_kind=LocationKind.UNKNOWN,
        statements=[if_stmt, move],
        variables_written=["WS-COD-RETORNO"],
    )
    return CanonicalProgram(
        program_name="PROG1",
        source_file=SRC,
        source_hash=HASH,
        source_package_hash=HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        paragraphs=[paragraph],
    )


def _v1_candidate(*, decision_id: str, outcome_code: str, line_start: int = 10) -> RuleCandidate:
    return RuleCandidate(
        candidate_id=f"candidate::q0-return-code-decision::1.0::{HASH}::{decision_id}",
        paragraph_id=paragraph_node_id("PROG1", "A"),
        paragraph_name="A",
        decision_id=decision_id,
        detector_id="q0-return-code-decision",
        detector_version="1.0",
        detector_score=1.0,
        status=CandidateStatus.DETECTED_CANDIDATE,
        condition="CONDICION",
        outcome_code=outcome_code,
        line_start=line_start,
        source_file=SRC,
        source_package_hash=HASH,
    )


def _run(program: CanonicalProgram, *, decisions, data_item_tags, v1_candidates=None):
    ctx = build_ctx(
        program=program,
        decisions=decisions,
        data_item_tags=data_item_tags,
        v1_candidates=v1_candidates,
    )
    return run_v2_shadow_detection(
        ctx,
        run_id="run1",
        source_package_hash=HASH,
        source_artifact_hashes={
            "artifacts/02-canonical": HASH,
            "artifacts/04-semantic-graph.json": HASH,
            "artifacts/06-candidates.json": HASH,
        },
    )


# ---------------------------------------------------------------------------
# V2_ONLY: V2 encuentra evidencia que Q0 (V1) nunca detecto
# ---------------------------------------------------------------------------


def test_v2_only_when_v1_has_no_candidates() -> None:
    program = _program_with_decision()
    artifact = _run(
        program,
        decisions=[("A", 10, "CONDICION")],
        data_item_tags={"WS-COD-RETORNO": "return_code"},
    )
    assert artifact.summary.v1_candidate_count == 0
    assert artifact.summary.v2_candidate_count == 1
    assert len(artifact.comparisons) == 1
    assert artifact.comparisons[0].status == V1V2ComparisonStatus.V2_ONLY
    assert artifact.comparisons[0].v1_candidate_ids == []


# ---------------------------------------------------------------------------
# V1_ONLY: Q0 detecto un candidato para una Decision sin evidencia V2
# ---------------------------------------------------------------------------


def test_v1_only_when_v2_finds_no_evidence_for_that_decision() -> None:
    # Decision sin ningun statement debajo: ningun detector V2 produce
    # evidencia (ni RETURN_CODE_PROPAGATION, ni LEVEL_88, ni STATE_CHANGE).
    if_stmt = make_stmt(
        statement_id="P1::A::0::IF", kind=StatementKind.IF, line_start=10, expression="CONDICION"
    )
    paragraph = CanonicalParagraph(
        name="A",
        source_text="A.",
        location_kind=LocationKind.UNKNOWN,
        statements=[if_stmt],
    )
    program = CanonicalProgram(
        program_name="PROG1",
        source_file=SRC,
        source_hash=HASH,
        source_package_hash=HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        paragraphs=[paragraph],
    )
    decision_id = decision_node_id_for("PROG1", "A", 10, 1)
    v1_candidates = CandidateArtifact(
        run_id="run1",
        source_package_hash=HASH,
        semantic_graph_hash=HASH,
        invariants_query_hash=HASH,
        q0_query_hash=HASH,
        candidates=[_v1_candidate(decision_id=decision_id, outcome_code="0005")],
    )
    artifact = _run(
        program,
        decisions=[("A", 10, "CONDICION")],
        data_item_tags={},
        v1_candidates=v1_candidates,
    )
    assert artifact.summary.v1_candidate_count == 1
    assert artifact.summary.v2_candidate_count == 0
    v1_only = [c for c in artifact.comparisons if c.status == V1V2ComparisonStatus.V1_ONLY]
    assert len(v1_only) == 1
    assert v1_only[0].v1_candidate_ids == [
        f"candidate::q0-return-code-decision::1.0::{HASH}::{decision_id}"
    ]


# ---------------------------------------------------------------------------
# MATCHED: mismo literal/outcome_code demostrado por V1 y V2
# ---------------------------------------------------------------------------


def test_matched_when_v1_outcome_code_equals_v2_resolved_literal() -> None:
    program = _program_with_decision(literal="0005")
    decision_id = decision_node_id_for("PROG1", "A", 10, 1)
    v1_candidates = CandidateArtifact(
        run_id="run1",
        source_package_hash=HASH,
        semantic_graph_hash=HASH,
        invariants_query_hash=HASH,
        q0_query_hash=HASH,
        candidates=[_v1_candidate(decision_id=decision_id, outcome_code="0005")],
    )
    artifact = _run(
        program,
        decisions=[("A", 10, "CONDICION")],
        data_item_tags={"WS-COD-RETORNO": "return_code"},
        v1_candidates=v1_candidates,
    )
    matched = [c for c in artifact.comparisons if c.status == V1V2ComparisonStatus.MATCHED]
    assert len(matched) == 1
    assert matched[0].v1_candidate_ids and matched[0].v2_candidate_ids


def test_related_not_equivalent_when_v1_outcome_differs_from_v2_literal() -> None:
    program = _program_with_decision(literal="0005")
    decision_id = decision_node_id_for("PROG1", "A", 10, 1)
    v1_candidates = CandidateArtifact(
        run_id="run1",
        source_package_hash=HASH,
        semantic_graph_hash=HASH,
        invariants_query_hash=HASH,
        q0_query_hash=HASH,
        candidates=[_v1_candidate(decision_id=decision_id, outcome_code="9999")],
    )
    artifact = _run(
        program,
        decisions=[("A", 10, "CONDICION")],
        data_item_tags={"WS-COD-RETORNO": "return_code"},
        v1_candidates=v1_candidates,
    )
    related = [
        c for c in artifact.comparisons if c.status == V1V2ComparisonStatus.RELATED_NOT_EQUIVALENT
    ]
    assert len(related) == 1
    assert related[0].v1_candidate_ids and related[0].v2_candidate_ids


# ---------------------------------------------------------------------------
# Summary: agregacion coherente end-to-end
# ---------------------------------------------------------------------------


def test_summary_detector_count_is_always_three() -> None:
    program = _program_with_decision()
    artifact = _run(
        program,
        decisions=[("A", 10, "CONDICION")],
        data_item_tags={"WS-COD-RETORNO": "return_code"},
    )
    assert artifact.summary.detector_count == 3
    assert len(artifact.executions) == 3


def test_summary_is_all_zero_for_empty_program() -> None:
    paragraph = CanonicalParagraph(
        name="A",
        source_text="A.",
        location_kind=LocationKind.UNKNOWN,
        statements=[],
    )
    program = CanonicalProgram(
        program_name="PROG1",
        source_file=SRC,
        source_hash=HASH,
        source_package_hash=HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        paragraphs=[paragraph],
    )
    artifact = _run(program, decisions=[], data_item_tags={})
    assert artifact.summary.v1_candidate_count == 0
    assert artifact.summary.v2_candidate_count == 0
    assert artifact.comparisons == []


# ---------------------------------------------------------------------------
# Determinismo byte a byte (Fase 18)
# ---------------------------------------------------------------------------


def test_run_v2_shadow_detection_is_byte_for_byte_deterministic() -> None:
    program = _program_with_decision()
    ctx = build_ctx(
        program=program,
        decisions=[("A", 10, "CONDICION")],
        data_item_tags={"WS-COD-RETORNO": "return_code"},
    )
    kwargs = dict(
        run_id="run1",
        source_package_hash=HASH,
        source_artifact_hashes={
            "artifacts/02-canonical": HASH,
            "artifacts/04-semantic-graph.json": HASH,
            "artifacts/06-candidates.json": HASH,
        },
    )
    first = run_v2_shadow_detection(ctx, **kwargs).to_stable_json()
    second = run_v2_shadow_detection(ctx, **kwargs).to_stable_json()
    assert first == second


# ---------------------------------------------------------------------------
# Invariantes de comparacion (auditoria post-Catherine-corregido): cada
# candidato V1/V2 queda clasificado en EXACTAMENTE una comparacion; las
# relaciones V2<->V2 (dos detectores V2 distintos, sin V1) nunca
# incrementan matched/v1_only/v2_only.
# ---------------------------------------------------------------------------


def _multi_decision_program() -> CanonicalProgram:
    if1 = make_stmt(
        statement_id="P1::A::0::IF", kind=StatementKind.IF, line_start=10, expression="COND1"
    )
    mv1 = make_stmt(
        statement_id="P1::A::1::MOVE",
        target_data_items=["WS-COD-RETORNO"],
        variables_written=["WS-COD-RETORNO"],
        assigned_literal="0005",
        parent_statement_id="P1::A::0::IF",
        branch_kind="THEN",
    )
    if2 = make_stmt(
        statement_id="P1::A::2::IF", kind=StatementKind.IF, line_start=20, expression="COND2"
    )
    if3 = make_stmt(
        statement_id="P1::A::3::IF", kind=StatementKind.IF, line_start=30, expression="COND3"
    )
    mv3 = make_stmt(
        statement_id="P1::A::4::MOVE",
        target_data_items=["WS-COD-RETORNO"],
        variables_written=["WS-COD-RETORNO"],
        assigned_literal="0007",
        parent_statement_id="P1::A::3::IF",
        branch_kind="THEN",
    )
    paragraph = CanonicalParagraph(
        name="A",
        source_text="A.",
        location_kind=LocationKind.UNKNOWN,
        statements=[if1, mv1, if2, if3, mv3],
        variables_written=["WS-COD-RETORNO"],
    )
    return CanonicalProgram(
        program_name="PROG1",
        source_file=SRC,
        source_hash=HASH,
        source_package_hash=HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        paragraphs=[paragraph],
    )


def test_every_candidate_appears_in_exactly_one_comparison() -> None:
    """Escenario compuesto: decision 1 -> MATCHED, decision 2 -> V1_ONLY
    (Q0 detecto la decision pero ningun detector V2 aporta evidencia,
    cuerpo vacio), decision 3 -> RELATED_NOT_EQUIVALENT (literal V1
    distinto del literal V2)."""
    program = _multi_decision_program()
    decision1 = decision_node_id_for("PROG1", "A", 10, 1)
    decision2 = decision_node_id_for("PROG1", "A", 20, 2)
    decision3 = decision_node_id_for("PROG1", "A", 30, 3)
    v1_candidates = CandidateArtifact(
        run_id="run1",
        source_package_hash=HASH,
        semantic_graph_hash=HASH,
        invariants_query_hash=HASH,
        q0_query_hash=HASH,
        candidates=[
            _v1_candidate(decision_id=decision1, outcome_code="0005", line_start=10),
            _v1_candidate(decision_id=decision2, outcome_code="0006", line_start=20),
            _v1_candidate(decision_id=decision3, outcome_code="9999", line_start=30),
        ],
    )
    artifact = _run(
        program,
        decisions=[("A", 10, "COND1"), ("A", 20, "COND2"), ("A", 30, "COND3")],
        data_item_tags={"WS-COD-RETORNO": "return_code"},
        v1_candidates=v1_candidates,
    )

    assert artifact.summary.v1_candidate_count == 3
    assert artifact.summary.matched_count == 1
    assert artifact.summary.v1_only_count == 1
    assert artifact.summary.related_not_equivalent_count == 1
    assert artifact.summary.v2_only_count == 0

    all_v1_ids: list[str] = []
    all_v2_ids: list[str] = []
    for comparison in artifact.comparisons:
        all_v1_ids.extend(comparison.v1_candidate_ids)
        all_v2_ids.extend(comparison.v2_candidate_ids)

    assert len(all_v1_ids) == len(set(all_v1_ids)) == 3
    assert len(all_v2_ids) == len(set(all_v2_ids)) == artifact.summary.v2_candidate_count
    assert set(all_v1_ids) == {c.candidate_id for c in v1_candidates.candidates}
    all_v2_from_executions = {
        candidate.candidate_id
        for execution in artifact.executions
        for candidate in execution.candidates
    }
    assert set(all_v2_ids) == all_v2_from_executions


def test_v2_v2_relation_never_increments_matched_v1_only_or_v2_only() -> None:
    """Reproduce el patron Caso B (RETURN_CODE_PROPAGATION + LEVEL_88
    sobre el mismo SET condicion-88 TO TRUE, sin V1): debe clasificarse
    exclusivamente como RELATED_NOT_EQUIVALENT, nunca como MATCHED/
    V1_ONLY/V2_ONLY."""
    condition = CanonicalConditionName(
        name="COD-CAMPO-INVALIDO",
        qualified_name="WS-COD-RETORNO.COD-CAMPO-INVALIDO",
        parent_name="WS-COD-RETORNO",
        parent_qualified_name="WS-COD-RETORNO",
        values=[CanonicalConditionValue(value="0005", location_kind=LocationKind.UNKNOWN)],
        location_kind=LocationKind.UNKNOWN,
    )
    if_stmt = make_stmt(
        statement_id="P1::PARA::0::IF",
        kind=StatementKind.IF,
        line_start=10,
        expression="ERROR-DE-ENTRADA",
    )
    set_stmt = make_stmt(
        statement_id="P1::PARA::1::SET",
        kind=StatementKind.SET,
        target_data_items=["COD-CAMPO-INVALIDO"],
        variables_written=["COD-CAMPO-INVALIDO"],
        condition_name_target="COD-CAMPO-INVALIDO",
        condition_set_value=True,
        parent_statement_id="P1::PARA::0::IF",
        branch_kind="THEN",
    )
    paragraph = CanonicalParagraph(
        name="PARA",
        source_text="PARA.",
        location_kind=LocationKind.UNKNOWN,
        statements=[if_stmt, set_stmt],
        variables_written=["COD-CAMPO-INVALIDO"],
    )
    program = CanonicalProgram(
        program_name="PROG1",
        source_file=SRC,
        source_hash=HASH,
        source_package_hash=HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        data_items=[
            CanonicalDataItem(
                name="WS-COD-RETORNO",
                qualified_name="WS-COD-RETORNO",
                level=1,
                location_kind=LocationKind.UNKNOWN,
            )
        ],
        condition_names=[condition],
        paragraphs=[paragraph],
    )
    artifact = _run(
        program,
        decisions=[("PARA", 10, "ERROR-DE-ENTRADA")],
        data_item_tags={"WS-COD-RETORNO": "return_code"},
    )

    assert artifact.summary.v1_candidate_count == 0
    assert artifact.summary.matched_count == 0
    assert artifact.summary.v1_only_count == 0
    assert artifact.summary.v2_only_count == 0
    assert artifact.summary.related_not_equivalent_count == 1

    comparison = artifact.comparisons[0]
    assert comparison.status == V1V2ComparisonStatus.RELATED_NOT_EQUIVALENT
    assert comparison.v1_candidate_ids == []
    assert len(comparison.v2_candidate_ids) == 2
    detector_ids = {
        candidate.detector_id
        for execution in artifact.executions
        for candidate in execution.candidates
    }
    assert detector_ids == {"V2_RETURN_CODE_PROPAGATION", "V2_LEVEL_88_RETURN_CODE"}


# ---------------------------------------------------------------------------
# Auditoria post-Fase-6: CanonicalProgram.schema_version (1.0/1.1/1.2) nunca
# se lee en este modulo (confirmado por auditoria de codigo: la unica lectura
# de ".schema_version" en v2_shadow_detector.py es la de los artefactos
# SemanticEffects/SemanticPropagation, nunca la de CanonicalProgram) -- la
# deteccion V2 debe producir resultados funcionalmente identicos sin importar
# que version este declarada en el CanonicalProgram de entrada.
# ---------------------------------------------------------------------------


def test_detection_is_identical_regardless_of_canonical_schema_version() -> None:
    """Mismo contenido exacto de programa/paragraph/statements, unicamente
    `schema_version` varia (1.0 historico / 1.1 nivel-88 / 1.2 CALL-LINKAGE,
    ver docs/INTERPROCEDURAL_CALL_LINKAGE.md): el artefacto V2 resultante
    debe ser byte a byte identico en los tres casos, porque
    `run_v2_shadow_detection` nunca lee `CanonicalProgram.schema_version`."""
    program_1_0 = _program_with_decision()
    assert program_1_0.schema_version == "1.0"
    program_1_1 = program_1_0.model_copy(update={"schema_version": "1.1"})
    program_1_2 = program_1_0.model_copy(update={"schema_version": "1.2"})

    decisions = [("A", 10, "CONDICION")]
    data_item_tags = {"WS-COD-RETORNO": "return_code"}

    artifact_1_0 = _run(program_1_0, decisions=decisions, data_item_tags=data_item_tags)
    artifact_1_1 = _run(program_1_1, decisions=decisions, data_item_tags=data_item_tags)
    artifact_1_2 = _run(program_1_2, decisions=decisions, data_item_tags=data_item_tags)

    json_1_0 = artifact_1_0.to_stable_json()
    json_1_1 = artifact_1_1.to_stable_json()
    json_1_2 = artifact_1_2.to_stable_json()

    assert json_1_0 == json_1_1 == json_1_2
    # Confirma tambien que semantic_effects_schema_version/
    # semantic_propagation_schema_version registrados son los del
    # ANALIZADOR (constantes, Fase 6: "1.2"/"1.1"), no derivados del
    # CanonicalProgram de entrada -- prueba directa de la independencia.
    assert artifact_1_0.semantic_effects_schema_version == "1.2"
    assert artifact_1_0.semantic_propagation_schema_version == "1.1"
