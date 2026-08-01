"""Tests unitarios del comando CLI `v2-candidates-shadow` (Fase 5 de la
ampliacion semantica, `feat/v2-detectors-shadow-mode`). Mismo patron que
`tests/test_cli_semantic_propagation.py`: sin Docker, sin JAR, sin
Neo4j, sin FastAPI -- solo filesystem local (`tmp_path`) via `CliRunner`."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

import altamira_extractor.cli as cli_module
from altamira_extractor.config import Settings
from altamira_extractor.contracts.candidate import CandidateArtifact
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

runner = CliRunner()

_HASH = "8" * 64
_RUN_ID = "20260101T000000000000-ffffffff"
_NONEXISTENT_RUN_ID = "20260101T000000000000-11111111"
_PROGRAM_NODE_ID = "program::AR::APP::PROG1::1.0::" + _HASH[:12]
_PARAGRAPH_NODE_ID = f"{_PROGRAM_NODE_ID}::paragraph::A"
_DECISION_NODE_ID = f"{_PARAGRAPH_NODE_ID}::decision::10::1"
_DATA_ITEM_NODE_ID = f"{_PROGRAM_NODE_ID}::data::WS-COD-RETORNO"

_REQUIRED_STAGES = (
    PipelineStage.PARSED,
    PipelineStage.SEMANTIC_GRAPH_BUILT,
    PipelineStage.CANDIDATES_DETECTED,
)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        runs_dir=tmp_path / "data" / "runs",
        incoming_dir=tmp_path / "data" / "incoming",
    )


@pytest.fixture
def patched_settings(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> Settings:
    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)
    return settings


def _write_run_state(run_dir: Path, *, stages: tuple[PipelineStage, ...]) -> None:
    now = datetime.now(UTC)
    executions = [
        StageExecution(
            stage=stage, status=StageStatus.SUCCEEDED, started_at=now, finished_at=now,
            duration_seconds=0.0,
        )
        for stage in stages
    ]
    state = RunState(
        run_id=_RUN_ID, package_filename="input/package.zip", source_package_hash=_HASH,
        current_stage=stages[-1] if stages else PipelineStage.RECEIVED,
        stages=executions, created_at=now, updated_at=now,
    )
    atomic_write_json(run_dir / "run.json", state)


def _write_canonical(run_dir: Path) -> None:
    if_stmt = CanonicalStatement(
        statement_id="P1::A::0::IF", kind=StatementKind.IF, source_text="IF CONDICION",
        location_kind=LocationKind.EXACT, source_file="a.cbl", line_start=10, line_end=10,
        expression="CONDICION",
    )
    move = CanonicalStatement(
        statement_id="P1::A::1::MOVE", kind=StatementKind.MOVE, source_text="MOVE '0005' TO X",
        location_kind=LocationKind.EXACT, source_file="a.cbl", line_start=11, line_end=11,
        target_data_items=["WS-COD-RETORNO"], variables_written=["WS-COD-RETORNO"],
        assigned_literal="0005", parent_statement_id="P1::A::0::IF", branch_kind="THEN",
    )
    paragraph = CanonicalParagraph(
        name="A", source_text="A.", location_kind=LocationKind.UNKNOWN,
        statements=[if_stmt, move], variables_written=["WS-COD-RETORNO"],
    )
    program = CanonicalProgram(
        program_name="PROG1", source_file="a.cbl", source_hash=_HASH, source_package_hash=_HASH,
        source_format=SourceFormat.FIXED, encoding="UTF-8", paragraphs=[paragraph],
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
                    id=_PARAGRAPH_NODE_ID, labels=[NodeLabel.PARAGRAPH],
                    properties={"name": "A", "line_start": 1, "line_end": 20},
                ),
                GraphNode(
                    id=_DECISION_NODE_ID, labels=[NodeLabel.DECISION],
                    properties={
                        "line_start": 10, "line_end": 10, "expression": "CONDICION",
                        "outcome_code": None, "rule_type": None,
                    },
                ),
                GraphNode(
                    id=_DATA_ITEM_NODE_ID, labels=[NodeLabel.DATA_ITEM],
                    properties={"qualified_name": "WS-COD-RETORNO", "semantic_tag": "return_code"},
                ),
            ],
            key=lambda node: node.id,
        ),
        relationships=sorted(
            [
                GraphRelationship(
                    type=RelationshipType.CONTAINS, from_id=_PROGRAM_NODE_ID,
                    to_id=_PARAGRAPH_NODE_ID,
                ),
                GraphRelationship(
                    type=RelationshipType.HAS_DECISION, from_id=_PARAGRAPH_NODE_ID,
                    to_id=_DECISION_NODE_ID,
                ),
            ],
            key=lambda rel: (rel.type.value, rel.from_id, rel.to_id),
        ),
    )
    atomic_write_json(run_dir / "artifacts" / "04-semantic-graph.json", graph)


def _write_v1_candidates(run_dir: Path) -> None:
    artifact = CandidateArtifact(
        run_id=_RUN_ID, source_package_hash=_HASH, semantic_graph_hash=_HASH,
        invariants_query_hash=_HASH, q0_query_hash=_HASH, candidates=[],
    )
    atomic_write_json(run_dir / "artifacts" / "06-candidates.json", artifact)


def _write_valid_run(run_dir: Path) -> None:
    _write_run_state(run_dir, stages=_REQUIRED_STAGES)
    _write_canonical(run_dir)
    _write_semantic_graph(run_dir)
    _write_v1_candidates(run_dir)


# ---------------------------------------------------------------------------
# Camino feliz
# ---------------------------------------------------------------------------


def test_success_prints_readable_summary_and_exit_0(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)

    result = runner.invoke(cli_module.app, ["v2-candidates-shadow", _RUN_ID])

    assert result.exit_code == 0, result.output
    assert f"run_id: {_RUN_ID}" in result.output
    assert "detectors_executed: 3" in result.output
    assert "v1_candidates: 0" in result.output
    assert "v2_candidates:" in result.output
    assert "deterministic:" in result.output
    assert "partial:" in result.output
    assert "blocked:" in result.output
    assert "matched:" in result.output
    assert "v1_only:" in result.output
    assert "v2_only:" in result.output
    assert "related_not_equivalent:" in result.output
    assert "report: diagnostics" in result.output


def test_persists_the_artifact_file(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)

    result = runner.invoke(cli_module.app, ["v2-candidates-shadow", _RUN_ID])

    assert result.exit_code == 0
    artifact_path = run_dir / "diagnostics" / "v2-candidates-shadow.json"
    assert artifact_path.is_file()


def test_json_option_prints_full_artifact_after_persisting(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)

    result = runner.invoke(cli_module.app, ["v2-candidates-shadow", _RUN_ID, "--json"])

    assert result.exit_code == 0
    assert '"schema_version": "1.0"' in result.output
    assert '"analyzer_version": "1.0"' in result.output


def test_no_timestamps_no_absolute_paths_in_persisted_artifact(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)
    runner.invoke(cli_module.app, ["v2-candidates-shadow", _RUN_ID])

    payload = (run_dir / "diagnostics" / "v2-candidates-shadow.json").read_text(encoding="utf-8")
    for forbidden in ("generated_at", "created_at", "updated_at"):
        assert forbidden not in payload
    assert str(patched_settings.runs_dir) not in payload


# ---------------------------------------------------------------------------
# Errores sanitizados / exit codes
# ---------------------------------------------------------------------------


def test_nonexistent_run_exits_nonzero_and_sanitized(patched_settings: Settings) -> None:
    result = runner.invoke(cli_module.app, ["v2-candidates-shadow", _NONEXISTENT_RUN_ID])

    assert result.exit_code != 0
    assert str(patched_settings.runs_dir) not in result.stderr
    assert "Traceback" not in result.stderr


def test_before_required_stages_exits_nonzero(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_run_state(run_dir, stages=(PipelineStage.PARSED,))

    result = runner.invoke(cli_module.app, ["v2-candidates-shadow", _RUN_ID])

    assert result.exit_code != 0


def test_invalid_run_id_format_exits_2(patched_settings: Settings) -> None:
    result = runner.invoke(cli_module.app, ["v2-candidates-shadow", "not/a-valid-run-id"])

    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Nunca modifica run.json ni artifacts/01-10 ni diagnostics preexistentes
# ---------------------------------------------------------------------------


def test_never_modifies_run_json(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)
    before = (run_dir / "run.json").read_bytes()

    runner.invoke(cli_module.app, ["v2-candidates-shadow", _RUN_ID])

    assert (run_dir / "run.json").read_bytes() == before


def test_never_modifies_canonical_artifacts(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)
    canonical_path = run_dir / "artifacts" / "02-canonical" / "a.cbl.json"
    before = canonical_path.read_bytes()

    runner.invoke(cli_module.app, ["v2-candidates-shadow", _RUN_ID])

    assert canonical_path.read_bytes() == before


def test_never_creates_semantic_effects_or_propagation_json(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)

    runner.invoke(cli_module.app, ["v2-candidates-shadow", _RUN_ID])

    assert not (run_dir / "diagnostics" / "semantic-effects.json").exists()
    assert not (run_dir / "diagnostics" / "semantic-propagation.json").exists()


def test_never_modifies_preexisting_semantic_coverage_json(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)
    preexisting = run_dir / "diagnostics" / "semantic-coverage.json"
    preexisting.parent.mkdir(parents=True, exist_ok=True)
    preexisting.write_text('{"marker": "untouched"}', encoding="utf-8")
    before = preexisting.read_bytes()

    runner.invoke(cli_module.app, ["v2-candidates-shadow", _RUN_ID])

    assert preexisting.read_bytes() == before
