"""Tests unitarios de semantic_graph_stage: precondiciones, integridad
referencial entre artefactos, idempotencia. Sin JAR, sin subprocess."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from altamira_extractor.contracts.canonical import CanonicalParagraph, CanonicalProgram
from altamira_extractor.contracts.dependencies import (
    DependencyArtifact,
    DependencyEvidence,
    ParagraphDependency,
)
from altamira_extractor.contracts.enums import (
    DependencyEvidenceRole,
    DependencyType,
    InventoryFileKind,
    LocationKind,
    PipelineStage,
    SourceFormat,
    StageStatus,
    StatementKind,
    TextEncoding,
)
from altamira_extractor.contracts.inventory import Inventory, InventoryFile
from altamira_extractor.contracts.manifest import (
    Manifest,
    ManifestApplication,
    ManifestCountry,
    ManifestImplementation,
    ManifestOperation,
    ManifestSource,
)
from altamira_extractor.contracts.run_state import StageExecution
from altamira_extractor.contracts.semantic_enrichment import SemanticEnrichmentArtifact
from altamira_extractor.contracts.semantic_graph import SemanticGraph
from altamira_extractor.pipeline.artifact_store import atomic_write_json
from altamira_extractor.pipeline.errors import SemanticGraphBuildError
from altamira_extractor.pipeline.semantic_graph_stage import run_semantic_graph_stage

VALID_HASH = "a" * 64
PROGRAM_SOURCE_HASH = "b" * 64
NOW = datetime(2026, 1, 1, tzinfo=UTC)
COBOL_RELATIVE_PATH = "01-codigo/cobol/PROG1.cbl"


def _manifest() -> Manifest:
    return Manifest(
        schema_version="1.0",
        country=ManifestCountry(code="AR", name="Argentina"),
        application=ManifestApplication(name="Transferencias"),
        operation=ManifestOperation(logical_name="OP-TRF-PROPIA", description=None),
        implementation=ManifestImplementation(version="1.0", entry_programs=["PROG1"]),
        source=ManifestSource(format=SourceFormat.FIXED, encoding="UTF-8"),
        parameter_tables=[],
    )


def _inventory() -> Inventory:
    return Inventory(
        run_id="run-1",
        source_package_hash=VALID_HASH,
        manifest=_manifest(),
        files=[
            InventoryFile(
                relative_path=COBOL_RELATIVE_PATH,
                kind=InventoryFileKind.COBOL,
                size_bytes=1,
                sha256=PROGRAM_SOURCE_HASH,
                detected_encoding=TextEncoding.UTF_8,
            )
        ],
    )


def _canonical_program() -> CanonicalProgram:
    return CanonicalProgram(
        program_name="PROG1",
        source_file=COBOL_RELATIVE_PATH,
        source_hash=PROGRAM_SOURCE_HASH,
        source_package_hash=VALID_HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        data_items=[],
        paragraphs=[],
    )


def _semantic_enrichment_built_succeeded_stage() -> StageExecution:
    return StageExecution(
        stage=PipelineStage.SEMANTIC_ENRICHMENT_BUILT,
        status=StageStatus.SUCCEEDED,
        started_at=NOW,
        finished_at=NOW,
        duration_seconds=1.0,
    )


def _semantic_enrichment_built_failed_stage() -> StageExecution:
    return StageExecution(
        stage=PipelineStage.SEMANTIC_ENRICHMENT_BUILT,
        status=StageStatus.FAILED,
        started_at=NOW,
        finished_at=NOW,
        error="boom",
    )


class _Env:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.canonical_dir = tmp_path / "artifacts" / "02-canonical"
        self.dependencies_path = tmp_path / "artifacts" / "03-dependencies.json"
        self.semantic_enrichment_path = tmp_path / "artifacts" / "03b-semantic-enrichment.json"
        self.semantic_graph_path = tmp_path / "artifacts" / "04-semantic-graph.json"
        self.canonical_dir.mkdir(parents=True)

        canonical_path = self.canonical_dir / f"{COBOL_RELATIVE_PATH}.json"
        canonical_path.parent.mkdir(parents=True, exist_ok=True)
        canonical_path.write_text(_canonical_program().to_stable_json(), encoding="utf-8")

        self.inventory = _inventory()

        atomic_write_json(
            self.dependencies_path,
            DependencyArtifact(run_id="run-1", source_package_hash=VALID_HASH, dependencies=[]),
        )
        atomic_write_json(
            self.semantic_enrichment_path,
            SemanticEnrichmentArtifact(
                run_id="run-1",
                source_package_hash=VALID_HASH,
                semantic_tags_config_hash=VALID_HASH,
                domain_glossary_config_hash=VALID_HASH,
            ),
        )

    def run(self, *, run_stages: list[StageExecution] | None = None) -> list[str]:
        if run_stages is None:
            run_stages = [_semantic_enrichment_built_succeeded_stage()]
        return run_semantic_graph_stage(
            run_id="run-1",
            source_package_hash=VALID_HASH,
            run_stages=run_stages,
            inventory=self.inventory,
            canonical_dir=self.canonical_dir,
            dependencies_path=self.dependencies_path,
            semantic_enrichment_path=self.semantic_enrichment_path,
            semantic_graph_path=self.semantic_graph_path,
        )


# --- Precondiciones ---


def test_valid_setup_builds_artifact(tmp_path: Path) -> None:
    env = _Env(tmp_path)

    warnings = env.run()

    assert warnings == []
    assert env.semantic_graph_path.is_file()
    graph = SemanticGraph.model_validate_json(env.semantic_graph_path.read_text(encoding="utf-8"))
    assert graph.source_package_hash == VALID_HASH
    assert any(n.id == "program::AR::OP-TRF-PROPIA::PROG1::1.0::bbbbbbbbbbbb" for n in graph.nodes)


def test_semantic_enrichment_built_not_succeeded_is_fatal(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    with pytest.raises(SemanticGraphBuildError):
        env.run(run_stages=[_semantic_enrichment_built_failed_stage()])


def test_missing_semantic_enrichment_built_stage_is_fatal(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    with pytest.raises(SemanticGraphBuildError):
        env.run(run_stages=[])


def test_duplicate_semantic_enrichment_built_stage_is_fatal(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    with pytest.raises(SemanticGraphBuildError):
        env.run(
            run_stages=[
                _semantic_enrichment_built_succeeded_stage(),
                _semantic_enrichment_built_succeeded_stage(),
            ]
        )


def test_missing_canonical_program_is_fatal(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    (env.canonical_dir / f"{COBOL_RELATIVE_PATH}.json").unlink()
    with pytest.raises(SemanticGraphBuildError):
        env.run()


def test_missing_dependencies_artifact_is_fatal(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.dependencies_path.unlink()
    with pytest.raises(SemanticGraphBuildError):
        env.run()


def test_corrupt_dependencies_artifact_is_fatal(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.dependencies_path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(SemanticGraphBuildError):
        env.run()


def test_dependencies_artifact_run_id_mismatch_is_fatal(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    atomic_write_json(
        env.dependencies_path,
        DependencyArtifact(run_id="other-run", source_package_hash=VALID_HASH, dependencies=[]),
    )
    with pytest.raises(SemanticGraphBuildError):
        env.run()


def test_dependencies_artifact_source_package_hash_mismatch_is_fatal(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    atomic_write_json(
        env.dependencies_path,
        DependencyArtifact(run_id="run-1", source_package_hash="c" * 64, dependencies=[]),
    )
    with pytest.raises(SemanticGraphBuildError):
        env.run()


def test_missing_semantic_enrichment_artifact_is_fatal(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.semantic_enrichment_path.unlink()
    with pytest.raises(SemanticGraphBuildError):
        env.run()


def test_corrupt_semantic_enrichment_artifact_is_fatal(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.semantic_enrichment_path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(SemanticGraphBuildError):
        env.run()


def test_semantic_enrichment_artifact_run_id_mismatch_is_fatal(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    atomic_write_json(
        env.semantic_enrichment_path,
        SemanticEnrichmentArtifact(
            run_id="other-run",
            source_package_hash=VALID_HASH,
            semantic_tags_config_hash=VALID_HASH,
            domain_glossary_config_hash=VALID_HASH,
        ),
    )
    with pytest.raises(SemanticGraphBuildError):
        env.run()


def test_orphan_paragraph_dependency_reference_is_fatal_at_stage_level(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    dependency = ParagraphDependency(
        dependency_type=DependencyType.CONTROL_DEPENDS_ON,
        from_paragraph_id="program::AR::OP-TRF-PROPIA::PROG1::1.0::bbbbbbbbbbbb::paragraph::GHOST",
        to_paragraph_id="program::AR::OP-TRF-PROPIA::PROG1::1.0::bbbbbbbbbbbb::paragraph::GHOST2",
        variables=[],
        control_construct="PERFORM",
        dependency_depth=1,
        confidence=1.0,
        derivation_rule="EXPLICIT_CONTROL_TARGET",
        source_file=COBOL_RELATIVE_PATH,
        line_start=5,
        line_end=5,
        location_kind=LocationKind.EXACT,
        source_package_hash=VALID_HASH,
        evidence=[
            DependencyEvidence(
                role=DependencyEvidenceRole.CONTROL,
                statement_id="S1",
                statement_kind=StatementKind.PERFORM,
                source_text="PERFORM GHOST2.",
                source_file=COBOL_RELATIVE_PATH,
                line_start=5,
                line_end=5,
                location_kind=LocationKind.EXACT,
                original_target="GHOST2",
            )
        ],
    )
    atomic_write_json(
        env.dependencies_path,
        DependencyArtifact(
            run_id="run-1", source_package_hash=VALID_HASH, dependencies=[dependency]
        ),
    )
    with pytest.raises(SemanticGraphBuildError):
        env.run()


# --- Idempotencia ---


def test_valid_existing_artifact_is_reused(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.run()
    first_bytes = env.semantic_graph_path.read_bytes()

    env.run()

    assert env.semantic_graph_path.read_bytes() == first_bytes


def test_corrupt_artifact_is_rebuilt(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.semantic_graph_path.parent.mkdir(parents=True, exist_ok=True)
    env.semantic_graph_path.write_text("{not valid json", encoding="utf-8")

    warnings = env.run()

    assert warnings == []
    graph = SemanticGraph.model_validate_json(env.semantic_graph_path.read_text(encoding="utf-8"))
    assert graph.source_package_hash == VALID_HASH


def _paragraph(name: str, *, line_start: int, line_end: int) -> CanonicalParagraph:
    return CanonicalParagraph(
        name=name,
        source_text=f"{name}.",
        source_file=COBOL_RELATIVE_PATH,
        line_start=line_start,
        line_end=line_end,
        location_kind=LocationKind.EXACT,
        statements=[],
        variables_read=[],
        variables_written=[],
        sql_access=[],
    )


def test_modified_dependencies_forces_rebuild(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.run()
    first_bytes = env.semantic_graph_path.read_bytes()

    # Se agrega una dependencia real entre dos paragraphs existentes en el
    # canonico actual (para que el resultado recomputado sea distinto sin
    # volverse una referencia huerfana).
    canonical_with_paragraphs = CanonicalProgram(
        program_name="PROG1",
        source_file=COBOL_RELATIVE_PATH,
        source_hash=PROGRAM_SOURCE_HASH,
        source_package_hash=VALID_HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        data_items=[],
        paragraphs=[
            _paragraph("PARA-A", line_start=1, line_end=2),
            _paragraph("PARA-B", line_start=3, line_end=4),
        ],
    )
    (env.canonical_dir / f"{COBOL_RELATIVE_PATH}.json").write_text(
        canonical_with_paragraphs.to_stable_json(), encoding="utf-8"
    )
    from_id = "program::AR::OP-TRF-PROPIA::PROG1::1.0::bbbbbbbbbbbb::paragraph::PARA-A"
    to_id = "program::AR::OP-TRF-PROPIA::PROG1::1.0::bbbbbbbbbbbb::paragraph::PARA-B"
    dependency = ParagraphDependency(
        dependency_type=DependencyType.CONTROL_DEPENDS_ON,
        from_paragraph_id=from_id,
        to_paragraph_id=to_id,
        variables=[],
        control_construct="PERFORM",
        dependency_depth=1,
        confidence=1.0,
        derivation_rule="EXPLICIT_CONTROL_TARGET",
        source_file=COBOL_RELATIVE_PATH,
        line_start=1,
        line_end=1,
        location_kind=LocationKind.EXACT,
        source_package_hash=VALID_HASH,
        evidence=[
            DependencyEvidence(
                role=DependencyEvidenceRole.CONTROL,
                statement_id="S1",
                statement_kind=StatementKind.PERFORM,
                source_text="PERFORM PARA-B.",
                source_file=COBOL_RELATIVE_PATH,
                line_start=1,
                line_end=1,
                location_kind=LocationKind.EXACT,
                original_target="PARA-B",
            )
        ],
    )
    atomic_write_json(
        env.dependencies_path,
        DependencyArtifact(
            run_id="run-1", source_package_hash=VALID_HASH, dependencies=[dependency]
        ),
    )

    env.run()

    assert env.semantic_graph_path.read_bytes() != first_bytes
