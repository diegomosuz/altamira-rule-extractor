"""Tests del servicio de filesystem (Fase 9, `feat/unified-candidate-
promotion-assessment`): `pipeline/candidate_promotion_assessment_
service.py`. Items 4-7 (fuente ausente/invalida a nivel filesystem), 41
(service), 43 (errores filesystem), 44 (JSON invalido), 46-48 (V1/V2/
interprocedural nunca modificados) de los 50 tests obligatorios. Sin
Neo4j, sin LLM, sin Docker -- solo filesystem local (`tmp_path`), mismo
patron que `tests/pipeline/test_v2_shadow_candidates_service.py`."""

from __future__ import annotations

import hashlib
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from altamira_extractor.contracts.candidate import CandidateArtifact
from altamira_extractor.contracts.candidate_promotion_assessment import (
    CandidateSource,
    SourceAvailability,
)
from altamira_extractor.contracts.canonical import (
    CanonicalParagraph,
    CanonicalProgram,
    CanonicalStatement,
)
from altamira_extractor.contracts.enums import (
    LocationKind,
    NodeLabel,
    PipelineStage,
    RelationshipType,
    SourceFormat,
    StageStatus,
    StatementKind,
)
from altamira_extractor.contracts.run_state import RunState, StageExecution
from altamira_extractor.contracts.semantic_graph import GraphNode, GraphRelationship, SemanticGraph
from altamira_extractor.pipeline.artifact_store import atomic_write_json
from altamira_extractor.pipeline.candidate_promotion_assessment_service import (
    candidate_promotion_assessment_artifact_path,
    compute_candidate_promotion_assessment_artifact,
    write_candidate_promotion_assessment_artifact,
)
from altamira_extractor.pipeline.errors import CandidatePromotionAssessmentError

_HASH = "f" * 64
_RUN_ID = "20260101T000000000000-eeeeeeee"
_PROGRAM_NODE_ID = "program::AR::APP::PROG1::1.0::" + _HASH[:12]
_PARAGRAPH_NODE_ID = f"{_PROGRAM_NODE_ID}::paragraph::A"
_DECISION_NODE_ID = f"{_PARAGRAPH_NODE_ID}::decision::10::1"
_DATA_ITEM_NODE_ID = f"{_PROGRAM_NODE_ID}::data::WS-COD-RETORNO"


def _write_run_state(run_dir: Path, *, stages: tuple[PipelineStage, ...]) -> RunState:
    now = datetime.now(UTC)
    executions = [
        StageExecution(
            stage=stage,
            status=StageStatus.SUCCEEDED,
            started_at=now,
            finished_at=now,
            duration_seconds=0.0,
        )
        for stage in stages
    ]
    state = RunState(
        run_id=_RUN_ID,
        package_filename="input/package.zip",
        source_package_hash=_HASH,
        current_stage=stages[-1] if stages else PipelineStage.RECEIVED,
        stages=executions,
        created_at=now,
        updated_at=now,
    )
    atomic_write_json(run_dir / "run.json", state)
    return state


def _write_canonical(run_dir: Path) -> None:
    if_stmt = CanonicalStatement(
        statement_id="P1::A::0::IF",
        kind=StatementKind.IF,
        source_text="IF CONDICION",
        location_kind=LocationKind.EXACT,
        source_file="a.cbl",
        line_start=10,
        line_end=10,
        expression="CONDICION",
    )
    move = CanonicalStatement(
        statement_id="P1::A::1::MOVE",
        kind=StatementKind.MOVE,
        source_text="MOVE '0005' TO X",
        location_kind=LocationKind.EXACT,
        source_file="a.cbl",
        line_start=11,
        line_end=11,
        target_data_items=["WS-COD-RETORNO"],
        variables_written=["WS-COD-RETORNO"],
        assigned_literal="0005",
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
    program = CanonicalProgram(
        program_name="PROG1",
        source_file="a.cbl",
        source_hash=_HASH,
        source_package_hash=_HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        paragraphs=[paragraph],
    )
    canonical_dir = run_dir / "artifacts" / "02-canonical"
    canonical_dir.mkdir(parents=True)
    atomic_write_json(canonical_dir / "a.cbl.json", program)


def _write_semantic_graph(run_dir: Path) -> None:
    graph = SemanticGraph(
        source_package_hash=_HASH,
        nodes=sorted(
            [
                GraphNode(
                    id=_PROGRAM_NODE_ID, labels=[NodeLabel.PROGRAM], properties={"name": "PROG1"}
                ),
                GraphNode(
                    id=_PARAGRAPH_NODE_ID,
                    labels=[NodeLabel.PARAGRAPH],
                    properties={"name": "A", "line_start": 1, "line_end": 20},
                ),
                GraphNode(
                    id=_DECISION_NODE_ID,
                    labels=[NodeLabel.DECISION],
                    properties={
                        "line_start": 10,
                        "line_end": 10,
                        "expression": "CONDICION",
                        "outcome_code": None,
                        "rule_type": None,
                    },
                ),
                GraphNode(
                    id=_DATA_ITEM_NODE_ID,
                    labels=[NodeLabel.DATA_ITEM],
                    properties={
                        "qualified_name": "WS-COD-RETORNO",
                        "semantic_tag": "return_code",
                    },
                ),
            ],
            key=lambda node: node.id,
        ),
        relationships=sorted(
            [
                GraphRelationship(
                    type=RelationshipType.CONTAINS,
                    from_id=_PROGRAM_NODE_ID,
                    to_id=_PARAGRAPH_NODE_ID,
                ),
                GraphRelationship(
                    type=RelationshipType.HAS_DECISION,
                    from_id=_PARAGRAPH_NODE_ID,
                    to_id=_DECISION_NODE_ID,
                ),
            ],
            key=lambda rel: (rel.type.value, rel.from_id, rel.to_id),
        ),
    )
    atomic_write_json(run_dir / "artifacts" / "04-semantic-graph.json", graph)


def _write_v1_candidates(run_dir: Path) -> None:
    artifact = CandidateArtifact(
        run_id=_RUN_ID,
        source_package_hash=_HASH,
        semantic_graph_hash=_HASH,
        invariants_query_hash=_HASH,
        q0_query_hash=_HASH,
        candidates=[],
    )
    atomic_write_json(run_dir / "artifacts" / "06-candidates.json", artifact)


def _write_full_valid_run(run_dir: Path) -> RunState:
    """Fase 9 solo exige `PARSED` (`SUCCEEDED`) por si misma, pero
    delega en `compute_v2_shadow_candidates_artifact` (Fase 5), que
    ademas exige `SEMANTIC_GRAPH_BUILT`/`CANDIDATES_DETECTED` en
    `RunState.stages` -- un run "completo" realista para que las TRES
    fuentes esten `AVAILABLE` debe reflejar esas etapas tambien."""
    state = _write_run_state(
        run_dir,
        stages=(
            PipelineStage.PARSED,
            PipelineStage.SEMANTIC_GRAPH_BUILT,
            PipelineStage.CANDIDATES_DETECTED,
        ),
    )
    _write_canonical(run_dir)
    _write_semantic_graph(run_dir)
    _write_v1_candidates(run_dir)
    return state


def _write_parsed_only_run(run_dir: Path) -> RunState:
    """Run minimo que solo alcanzo `PARSED` -- unica precondicion propia
    de Fase 9. V2 delega en Fase 5, que exige mas etapas: un run asi
    produce `SourceAvailability.INVALID` para V2 (nunca se oculta), no
    `NOT_AVAILABLE`."""
    state = _write_run_state(run_dir, stages=(PipelineStage.PARSED,))
    _write_canonical(run_dir)
    _write_semantic_graph(run_dir)
    _write_v1_candidates(run_dir)
    return state


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _tree_hashes(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root).as_posix()): _sha256_bytes(path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
    }


def test_service_computes_artifact_with_all_sources_available(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)

    artifact = compute_candidate_promotion_assessment_artifact(run_dir, _RUN_ID)

    assert artifact.run_id == _RUN_ID
    assert artifact.source_availability[CandidateSource.V1] == SourceAvailability.AVAILABLE
    assert artifact.source_availability[CandidateSource.V2] == SourceAvailability.AVAILABLE
    assert (
        artifact.source_availability[CandidateSource.INTERPROCEDURAL]
        == SourceAvailability.AVAILABLE
    )


def test_v1_source_absent_marks_not_available_and_continues(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    (run_dir / "artifacts" / "06-candidates.json").unlink()

    artifact = compute_candidate_promotion_assessment_artifact(run_dir, _RUN_ID)

    assert artifact.source_availability[CandidateSource.V1] == SourceAvailability.NOT_AVAILABLE
    assert any("V1" in diagnostic for diagnostic in artifact.diagnostics)


def test_v2_prerequisites_absent_marks_not_available(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    (run_dir / "artifacts" / "04-semantic-graph.json").unlink()

    artifact = compute_candidate_promotion_assessment_artifact(run_dir, _RUN_ID)

    assert artifact.source_availability[CandidateSource.V2] == SourceAvailability.NOT_AVAILABLE


def test_interprocedural_prerequisites_absent_marks_not_available(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    shutil.rmtree(run_dir / "artifacts" / "02-canonical")

    artifact = compute_candidate_promotion_assessment_artifact(run_dir, _RUN_ID)

    assert (
        artifact.source_availability[CandidateSource.INTERPROCEDURAL]
        == SourceAvailability.NOT_AVAILABLE
    )
    # V2 declara sus PROPIOS prerequisitos (04-semantic-graph.json +
    # 06-candidates.json, ambos presentes) como satisfechos por
    # `_load_v2_candidates`, pero `compute_v2_shadow_candidates_artifact`
    # (Fase 5) tambien exige `artifacts/02-canonical/` internamente --
    # su ausencia hace que el computo falle, nunca se oculta el error
    # (`SourceAvailability.INVALID`, no `NOT_AVAILABLE`).
    assert artifact.source_availability[CandidateSource.V2] == SourceAvailability.INVALID


def test_v1_invalid_json_marks_invalid_never_hides_error(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    (run_dir / "artifacts" / "06-candidates.json").write_text(
        "{not valid json", encoding="utf-8"
    )

    artifact = compute_candidate_promotion_assessment_artifact(run_dir, _RUN_ID)

    assert artifact.source_availability[CandidateSource.V1] == SourceAvailability.INVALID
    assert any("V1" in diagnostic for diagnostic in artifact.diagnostics)


def test_v2_incompatible_version_marks_invalid_never_hides_error(tmp_path: Path) -> None:
    """Auditoria de cierre, Parte 5, item 2: `04-semantic-graph.json`
    presente pero con un esquema incompatible (JSON valido, pero no
    conforma `SemanticGraph`) -- `compute_v2_shadow_candidates_artifact`
    (Fase 5) rechaza la validacion, Fase 9 lo reclasifica como
    `INVALID` (nunca `NOT_AVAILABLE`, nunca un artefacto V2 vacio
    fabricado en su lugar)."""
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    (run_dir / "artifacts" / "04-semantic-graph.json").write_text(
        '{"schema_version": "99.0", "source_package_hash": "not-a-valid-hash"}',
        encoding="utf-8",
    )

    artifact = compute_candidate_promotion_assessment_artifact(run_dir, _RUN_ID)

    assert artifact.source_availability[CandidateSource.V2] == SourceAvailability.INVALID
    assert any("V2" in diagnostic for diagnostic in artifact.diagnostics)
    assert artifact.summary.v2_candidate_count == 0
    assert not any(r.source == CandidateSource.V2 for r in artifact.candidate_references)


def test_interprocedural_invalid_references_marks_invalid_never_hides_error(
    tmp_path: Path,
) -> None:
    """Auditoria de cierre, Parte 5, item 3: `artifacts/02-canonical/`
    presente pero con un `CanonicalProgram` invalido (JSON valido,
    campos obligatorios ausentes) -- Fase 9 lo reclasifica como
    `INVALID`, nunca oculta el error ni fabrica un artefacto
    interprocedural vacio en su lugar."""
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    canonical_path = run_dir / "artifacts" / "02-canonical" / "a.cbl.json"
    canonical_path.write_text('{"program_name": "PROG1"}', encoding="utf-8")

    artifact = compute_candidate_promotion_assessment_artifact(run_dir, _RUN_ID)

    assert (
        artifact.source_availability[CandidateSource.INTERPROCEDURAL]
        == SourceAvailability.INVALID
    )
    assert any("INTERPROCEDURAL" in diagnostic for diagnostic in artifact.diagnostics)
    assert artifact.summary.interprocedural_candidate_count == 0
    assert not any(
        r.source == CandidateSource.INTERPROCEDURAL for r in artifact.candidate_references
    )


def test_nonexistent_run_raises_clear_error(tmp_path: Path) -> None:
    run_dir = tmp_path / "does-not-exist"
    with pytest.raises(CandidatePromotionAssessmentError, match="no encontrado"):
        compute_candidate_promotion_assessment_artifact(run_dir, "does-not-exist")


def test_run_that_only_reached_parsed_still_produces_an_artifact(tmp_path: Path) -> None:
    """La UNICA precondicion propia de Fase 9 es `PARSED` (`SUCCEEDED`)
    -- un run que no avanzo mas alla nunca hace fallar el servicio, aun
    cuando V2 (que exige mas etapas internamente) termine `INVALID`."""
    run_dir = tmp_path / _RUN_ID
    _write_parsed_only_run(run_dir)

    artifact = compute_candidate_promotion_assessment_artifact(run_dir, _RUN_ID)

    assert artifact.run_id == _RUN_ID
    assert artifact.source_availability[CandidateSource.V1] == SourceAvailability.AVAILABLE


def test_run_before_parsed_raises_clear_error(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_run_state(run_dir, stages=())
    with pytest.raises(CandidatePromotionAssessmentError):
        compute_candidate_promotion_assessment_artifact(run_dir, _RUN_ID)


def test_invalid_run_json_raises_clear_error(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text("{not valid json", encoding="utf-8")
    with pytest.raises(CandidatePromotionAssessmentError, match="invalido"):
        compute_candidate_promotion_assessment_artifact(run_dir, _RUN_ID)


def test_error_messages_never_contain_absolute_paths(tmp_path: Path) -> None:
    run_dir = tmp_path / "does-not-exist"
    with pytest.raises(CandidatePromotionAssessmentError) as excinfo:
        compute_candidate_promotion_assessment_artifact(run_dir, "does-not-exist")
    assert str(tmp_path) not in str(excinfo.value)


def test_write_creates_only_diagnostics_report(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    entries_before = {
        p.relative_to(run_dir).as_posix() for p in run_dir.rglob("*") if p.is_file()
    }

    artifact = compute_candidate_promotion_assessment_artifact(run_dir, _RUN_ID)
    write_candidate_promotion_assessment_artifact(run_dir, artifact)

    entries_after = {
        p.relative_to(run_dir).as_posix() for p in run_dir.rglob("*") if p.is_file()
    }
    new_entries = entries_after - entries_before
    assert new_entries == {"diagnostics/candidate-promotion-assessment.json"}


def test_second_execution_is_byte_for_byte_identical(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)

    artifact1 = compute_candidate_promotion_assessment_artifact(run_dir, _RUN_ID)
    path1 = write_candidate_promotion_assessment_artifact(run_dir, artifact1)
    bytes1 = path1.read_bytes()

    artifact2 = compute_candidate_promotion_assessment_artifact(run_dir, _RUN_ID)
    path2 = write_candidate_promotion_assessment_artifact(run_dir, artifact2)
    bytes2 = path2.read_bytes()

    assert bytes1 == bytes2


def test_artifact_path_helper_matches_actual_write_location(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    artifact = compute_candidate_promotion_assessment_artifact(run_dir, _RUN_ID)
    written_path = write_candidate_promotion_assessment_artifact(run_dir, artifact)
    assert written_path == candidate_promotion_assessment_artifact_path(run_dir)


def test_input_artifacts_and_run_json_are_never_modified(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    state_before = _write_full_valid_run(run_dir)

    before_hashes = _tree_hashes(run_dir / "artifacts")
    run_json_bytes_before = (run_dir / "run.json").read_bytes()

    artifact = compute_candidate_promotion_assessment_artifact(run_dir, _RUN_ID)
    write_candidate_promotion_assessment_artifact(run_dir, artifact)

    after_hashes = _tree_hashes(run_dir / "artifacts")
    run_json_bytes_after = (run_dir / "run.json").read_bytes()

    assert before_hashes == after_hashes
    assert run_json_bytes_before == run_json_bytes_after

    state_after = RunState.model_validate_json(run_json_bytes_after.decode("utf-8"))
    assert state_after.current_stage == state_before.current_stage
    assert state_after.updated_at == state_before.updated_at


def test_never_writes_v2_or_interprocedural_diagnostics(tmp_path: Path) -> None:
    """El servicio de Fase 9 NUNCA escribe `diagnostics/v2-candidates-
    shadow.json` ni `diagnostics/interprocedural-rule-candidates-
    shadow.json` -- V2/interprocedural se calculan en memoria."""
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    artifact = compute_candidate_promotion_assessment_artifact(run_dir, _RUN_ID)
    write_candidate_promotion_assessment_artifact(run_dir, artifact)
    assert not (run_dir / "diagnostics" / "v2-candidates-shadow.json").exists()
    assert not (
        run_dir / "diagnostics" / "interprocedural-rule-candidates-shadow.json"
    ).exists()


def test_preexisting_diagnostics_are_never_modified(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    preexisting = run_dir / "diagnostics" / "semantic-effects.json"
    preexisting.parent.mkdir(parents=True, exist_ok=True)
    preexisting.write_text('{"marker": "untouched"}', encoding="utf-8")
    before = preexisting.read_bytes()

    artifact = compute_candidate_promotion_assessment_artifact(run_dir, _RUN_ID)
    write_candidate_promotion_assessment_artifact(run_dir, artifact)

    assert preexisting.read_bytes() == before


def test_no_partial_artifact_created_on_failure(tmp_path: Path) -> None:
    run_dir = tmp_path / "does-not-exist"
    with pytest.raises(CandidatePromotionAssessmentError):
        compute_candidate_promotion_assessment_artifact(run_dir, "does-not-exist")
    assert not (run_dir / "diagnostics").exists()
