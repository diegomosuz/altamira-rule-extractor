"""Tests de los servicios de filesystem del paquete de revision y del
plan (Fase 10, `feat/controlled-candidate-promotion-plan`). Items 42,
43, 46, 47, 48, 49, 50 de los 55 tests obligatorios. Sin Neo4j, sin
LLM, sin Docker -- solo filesystem local (`tmp_path`), mismo patron que
`tests/pipeline/test_candidate_promotion_assessment_service.py`
(Fase 9)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

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
from altamira_extractor.pipeline.candidate_promotion_plan_service import (
    compute_candidate_promotion_plan_artifact,
    write_candidate_promotion_plan_artifact,
)
from altamira_extractor.pipeline.candidate_promotion_review_service import (
    compute_candidate_promotion_review_package,
    write_candidate_promotion_review_package,
)
from altamira_extractor.pipeline.errors import CandidatePromotionPlanError

_HASH = "1" * 64
_RUN_ID = "20260101T000000000000-1abbccdd"
_PROGRAM_NODE_ID = "program::AR::APP::PROG1::1.0::" + _HASH[:12]
_PARAGRAPH_NODE_ID = f"{_PROGRAM_NODE_ID}::paragraph::A"
_DECISION_NODE_ID = f"{_PARAGRAPH_NODE_ID}::decision::10::1"
_DATA_ITEM_NODE_ID = f"{_PROGRAM_NODE_ID}::data::WS-COD-RETORNO"


def _write_run_state(run_dir: Path, *, stages: tuple[PipelineStage, ...]) -> None:
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


def _write_canonical(run_dir: Path) -> None:
    stmt = CanonicalStatement(
        statement_id="P1::A::0::IF",
        kind=StatementKind.IF,
        source_text="IF CONDICION",
        location_kind=LocationKind.EXACT,
        source_file="a.cbl",
        line_start=10,
        line_end=10,
        expression="CONDICION",
    )
    paragraph = CanonicalParagraph(
        name="A", source_text="A.", location_kind=LocationKind.UNKNOWN, statements=[stmt]
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


def _write_full_valid_run(run_dir: Path) -> None:
    _write_run_state(
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


# ---------------------------------------------------------------------------
# Item 42: servicio de review package
# ---------------------------------------------------------------------------


def test_review_package_service_computes_and_writes(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)

    package = compute_candidate_promotion_review_package(run_dir, _RUN_ID)
    path = write_candidate_promotion_review_package(run_dir, package)

    assert path == run_dir / "diagnostics" / "candidate-promotion-review-package.json"
    assert path.is_file()
    assert package.run_id == _RUN_ID


def test_review_package_service_only_creates_its_own_diagnostic(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    entries_before = {p.relative_to(run_dir).as_posix() for p in run_dir.rglob("*") if p.is_file()}

    package = compute_candidate_promotion_review_package(run_dir, _RUN_ID)
    write_candidate_promotion_review_package(run_dir, package)

    entries_after = {p.relative_to(run_dir).as_posix() for p in run_dir.rglob("*") if p.is_file()}
    assert entries_after - entries_before == {
        "diagnostics/candidate-promotion-review-package.json"
    }


# ---------------------------------------------------------------------------
# Item 43: servicio del plan (camino feliz)
# ---------------------------------------------------------------------------


def _write_manifest_for(tmp_path: Path, run_dir: Path) -> Path:
    package = compute_candidate_promotion_review_package(run_dir, _RUN_ID)
    write_candidate_promotion_review_package(run_dir, package)
    review_package_hash = _sha256_bytes(package.to_stable_json().encode("utf-8"))
    manifest_payload = {
        "schema_version": "1.0",
        "review_package_hash": review_package_hash,
        "assessment_artifact_hash": package.assessment_artifact_hash,
        "run_id": _RUN_ID,
        "decisions": [],
    }
    manifest_path = tmp_path / "decisions.json"
    manifest_path.write_text(json.dumps(manifest_payload), encoding="utf-8")
    return manifest_path


def test_plan_service_computes_and_writes(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    manifest_path = _write_manifest_for(tmp_path, run_dir)

    artifact = compute_candidate_promotion_plan_artifact(
        run_dir, _RUN_ID, decisions_path=str(manifest_path)
    )
    path = write_candidate_promotion_plan_artifact(run_dir, artifact)

    assert path == run_dir / "diagnostics" / "candidate-promotion-plan.json"
    assert path.is_file()
    assert artifact.run_id == _RUN_ID


def test_plan_service_requires_review_package_to_exist_first(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    manifest_path = tmp_path / "decisions.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "review_package_hash": "0" * 64,
                "assessment_artifact_hash": "0" * 64,
                "run_id": _RUN_ID,
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CandidatePromotionPlanError, match="paquete de revision"):
        compute_candidate_promotion_plan_artifact(
            run_dir, _RUN_ID, decisions_path=str(manifest_path)
        )


# ---------------------------------------------------------------------------
# Item 46: ruta insegura de --decisions
# ---------------------------------------------------------------------------


def test_plan_service_rejects_directory_as_decisions_path(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    package = compute_candidate_promotion_review_package(run_dir, _RUN_ID)
    write_candidate_promotion_review_package(run_dir, package)

    directory_path = tmp_path / "not-a-file"
    directory_path.mkdir()
    with pytest.raises(CandidatePromotionPlanError, match="archivo regular"):
        compute_candidate_promotion_plan_artifact(
            run_dir, _RUN_ID, decisions_path=str(directory_path)
        )


def test_plan_service_rejects_symlink_decisions_path(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    package = compute_candidate_promotion_review_package(run_dir, _RUN_ID)
    write_candidate_promotion_review_package(run_dir, package)

    real_file = tmp_path / "real-decisions.json"
    real_file.write_text("{}", encoding="utf-8")
    symlink_path = tmp_path / "decisions-link.json"
    try:
        symlink_path.symlink_to(real_file)
    except OSError:
        pytest.skip("symlinks no soportados en este entorno")
    with pytest.raises(CandidatePromotionPlanError, match="symlink"):
        compute_candidate_promotion_plan_artifact(
            run_dir, _RUN_ID, decisions_path=str(symlink_path)
        )


def test_plan_service_rejects_missing_decisions_path(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    package = compute_candidate_promotion_review_package(run_dir, _RUN_ID)
    write_candidate_promotion_review_package(run_dir, package)

    with pytest.raises(CandidatePromotionPlanError):
        compute_candidate_promotion_plan_artifact(
            run_dir, _RUN_ID, decisions_path=str(tmp_path / "does-not-exist.json")
        )


# ---------------------------------------------------------------------------
# Item 47: manifest JSON invalido
# ---------------------------------------------------------------------------


def test_plan_service_rejects_invalid_json_manifest(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    package = compute_candidate_promotion_review_package(run_dir, _RUN_ID)
    write_candidate_promotion_review_package(run_dir, package)

    manifest_path = tmp_path / "decisions.json"
    manifest_path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(CandidatePromotionPlanError, match="JSON valido"):
        compute_candidate_promotion_plan_artifact(
            run_dir, _RUN_ID, decisions_path=str(manifest_path)
        )


# ---------------------------------------------------------------------------
# Item 48: version incompatible
# ---------------------------------------------------------------------------


def test_plan_service_rejects_incompatible_schema_version(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    package = compute_candidate_promotion_review_package(run_dir, _RUN_ID)
    write_candidate_promotion_review_package(run_dir, package)
    review_package_hash = _sha256_bytes(package.to_stable_json().encode("utf-8"))

    manifest_path = tmp_path / "decisions.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "99.0",
                "review_package_hash": review_package_hash,
                "assessment_artifact_hash": package.assessment_artifact_hash,
                "run_id": _RUN_ID,
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CandidatePromotionPlanError):
        compute_candidate_promotion_plan_artifact(
            run_dir, _RUN_ID, decisions_path=str(manifest_path)
        )


# ---------------------------------------------------------------------------
# Items 49/50: nunca modifica run.json ni artefactos fuente
# ---------------------------------------------------------------------------


def test_plan_service_never_modifies_run_json_or_source_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    manifest_path = _write_manifest_for(tmp_path, run_dir)

    before_hashes = _tree_hashes(run_dir / "artifacts")
    run_json_before = (run_dir / "run.json").read_bytes()

    artifact = compute_candidate_promotion_plan_artifact(
        run_dir, _RUN_ID, decisions_path=str(manifest_path)
    )
    write_candidate_promotion_plan_artifact(run_dir, artifact)

    after_hashes = _tree_hashes(run_dir / "artifacts")
    run_json_after = (run_dir / "run.json").read_bytes()

    assert before_hashes == after_hashes
    assert run_json_before == run_json_after


def test_plan_service_never_copies_the_human_manifest_into_the_repo(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    manifest_path = _write_manifest_for(tmp_path, run_dir)

    artifact = compute_candidate_promotion_plan_artifact(
        run_dir, _RUN_ID, decisions_path=str(manifest_path)
    )
    write_candidate_promotion_plan_artifact(run_dir, artifact)

    run_dir_files = {p.name for p in run_dir.rglob("*") if p.is_file()}
    assert manifest_path.name not in run_dir_files
