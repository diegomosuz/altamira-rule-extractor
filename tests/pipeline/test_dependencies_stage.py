"""Tests unitarios de dependencies_stage: precondiciones, idempotencia y
persistencia atomica. Sin JAR, sin subprocess."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from altamira_extractor.contracts import (
    DependencyArtifact,
    InventoryFileKind,
    Manifest,
    ManifestApplication,
    ManifestCountry,
    ManifestImplementation,
    ManifestOperation,
    ManifestSource,
    PipelineStage,
    SourceFormat,
    StageExecution,
    StageStatus,
    TextEncoding,
)
from altamira_extractor.contracts.canonical import CanonicalProgram
from altamira_extractor.contracts.dependencies import DependencyEvidence, ParagraphDependency
from altamira_extractor.contracts.enums import (
    DependencyEvidenceRole,
    DependencyType,
    LocationKind,
    StatementKind,
)
from altamira_extractor.contracts.inventory import Inventory, InventoryFile
from altamira_extractor.pipeline.dependencies_stage import run_dependencies_built_stage
from altamira_extractor.pipeline.errors import DependencyBuildError

VALID_HASH = "a" * 64
SOURCE_HASH = "b" * 64
NOW = datetime(2026, 1, 1, tzinfo=UTC)


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


def _canonical_program(*, source_file: str) -> CanonicalProgram:
    return CanonicalProgram(
        program_name="PROG1",
        source_file=source_file,
        source_hash=SOURCE_HASH,
        source_package_hash=VALID_HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        data_items=[],
        paragraphs=[],
        warnings=[],
        unsupported_constructs=[],
    )


def _inventory_file(relative_path: str) -> InventoryFile:
    return InventoryFile(
        relative_path=relative_path,
        kind=InventoryFileKind.COBOL,
        size_bytes=10,
        sha256=SOURCE_HASH,
        detected_encoding=TextEncoding.UTF_8,
    )


def _inventory(files: list[InventoryFile]) -> Inventory:
    return Inventory(
        run_id="run-1", source_package_hash=VALID_HASH, manifest=_manifest(), files=files
    )


def _parsed_succeeded_stage() -> StageExecution:
    return StageExecution(
        stage=PipelineStage.PARSED,
        status=StageStatus.SUCCEEDED,
        started_at=NOW,
        finished_at=NOW,
        duration_seconds=1.0,
    )


def _parsed_failed_stage() -> StageExecution:
    return StageExecution(
        stage=PipelineStage.PARSED,
        status=StageStatus.FAILED,
        started_at=NOW,
        finished_at=NOW,
        error="boom",
    )


def _write_canonical(canonical_dir: Path, relative_path: str, program: CanonicalProgram) -> Path:
    path = canonical_dir / f"{relative_path}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(program.to_stable_json(), encoding="utf-8")
    return path


RELATIVE_PATH = "01-codigo/cobol/PROG1.cbl"


def test_valid_setup_builds_and_persists_artifact(tmp_path: Path) -> None:
    canonical_dir = tmp_path / "02-canonical"
    _write_canonical(canonical_dir, RELATIVE_PATH, _canonical_program(source_file=RELATIVE_PATH))
    inventory = _inventory([_inventory_file(RELATIVE_PATH)])
    dependencies_path = tmp_path / "03-dependencies.json"

    warnings = run_dependencies_built_stage(
        run_id="run-1",
        source_package_hash=VALID_HASH,
        run_stages=[_parsed_succeeded_stage()],
        inventory=inventory,
        canonical_dir=canonical_dir,
        dependencies_path=dependencies_path,
    )

    assert warnings == []
    assert dependencies_path.is_file()
    artifact = DependencyArtifact.model_validate_json(
        dependencies_path.read_text(encoding="utf-8")
    )
    assert artifact.run_id == "run-1"
    assert artifact.dependencies == []


def test_missing_canonical_program_is_fatal(tmp_path: Path) -> None:
    canonical_dir = tmp_path / "02-canonical"
    canonical_dir.mkdir()
    inventory = _inventory([_inventory_file(RELATIVE_PATH)])

    with pytest.raises(DependencyBuildError):
        run_dependencies_built_stage(
            run_id="run-1",
            source_package_hash=VALID_HASH,
            run_stages=[_parsed_succeeded_stage()],
            inventory=inventory,
            canonical_dir=canonical_dir,
            dependencies_path=tmp_path / "03-dependencies.json",
        )


def test_invalid_canonical_json_is_fatal(tmp_path: Path) -> None:
    canonical_dir = tmp_path / "02-canonical"
    path = canonical_dir / f"{RELATIVE_PATH}.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not valid json", encoding="utf-8")
    inventory = _inventory([_inventory_file(RELATIVE_PATH)])

    with pytest.raises(DependencyBuildError):
        run_dependencies_built_stage(
            run_id="run-1",
            source_package_hash=VALID_HASH,
            run_stages=[_parsed_succeeded_stage()],
            inventory=inventory,
            canonical_dir=canonical_dir,
            dependencies_path=tmp_path / "03-dependencies.json",
        )


def test_canonical_program_source_file_mismatch_is_fatal(tmp_path: Path) -> None:
    canonical_dir = tmp_path / "02-canonical"
    _write_canonical(canonical_dir, RELATIVE_PATH, _canonical_program(source_file="OTHER.cbl"))
    inventory = _inventory([_inventory_file(RELATIVE_PATH)])

    with pytest.raises(DependencyBuildError):
        run_dependencies_built_stage(
            run_id="run-1",
            source_package_hash=VALID_HASH,
            run_stages=[_parsed_succeeded_stage()],
            inventory=inventory,
            canonical_dir=canonical_dir,
            dependencies_path=tmp_path / "03-dependencies.json",
        )


def test_canonical_program_source_hash_mismatch_is_fatal(tmp_path: Path) -> None:
    canonical_dir = tmp_path / "02-canonical"
    program = _canonical_program(source_file=RELATIVE_PATH).model_copy(
        update={"source_hash": "c" * 64}
    )
    _write_canonical(canonical_dir, RELATIVE_PATH, program)
    inventory = _inventory([_inventory_file(RELATIVE_PATH)])

    with pytest.raises(DependencyBuildError):
        run_dependencies_built_stage(
            run_id="run-1",
            source_package_hash=VALID_HASH,
            run_stages=[_parsed_succeeded_stage()],
            inventory=inventory,
            canonical_dir=canonical_dir,
            dependencies_path=tmp_path / "03-dependencies.json",
        )


def test_canonical_program_source_package_hash_mismatch_is_fatal(tmp_path: Path) -> None:
    canonical_dir = tmp_path / "02-canonical"
    program = _canonical_program(source_file=RELATIVE_PATH).model_copy(
        update={"source_package_hash": "c" * 64}
    )
    _write_canonical(canonical_dir, RELATIVE_PATH, program)
    inventory = _inventory([_inventory_file(RELATIVE_PATH)])

    with pytest.raises(DependencyBuildError):
        run_dependencies_built_stage(
            run_id="run-1",
            source_package_hash=VALID_HASH,
            run_stages=[_parsed_succeeded_stage()],
            inventory=inventory,
            canonical_dir=canonical_dir,
            dependencies_path=tmp_path / "03-dependencies.json",
        )


def test_parsed_not_succeeded_is_fatal(tmp_path: Path) -> None:
    canonical_dir = tmp_path / "02-canonical"
    _write_canonical(canonical_dir, RELATIVE_PATH, _canonical_program(source_file=RELATIVE_PATH))
    inventory = _inventory([_inventory_file(RELATIVE_PATH)])

    with pytest.raises(DependencyBuildError):
        run_dependencies_built_stage(
            run_id="run-1",
            source_package_hash=VALID_HASH,
            run_stages=[_parsed_failed_stage()],
            inventory=inventory,
            canonical_dir=canonical_dir,
            dependencies_path=tmp_path / "03-dependencies.json",
        )


def test_missing_parsed_stage_execution_is_fatal(tmp_path: Path) -> None:
    canonical_dir = tmp_path / "02-canonical"
    _write_canonical(canonical_dir, RELATIVE_PATH, _canonical_program(source_file=RELATIVE_PATH))
    inventory = _inventory([_inventory_file(RELATIVE_PATH)])

    with pytest.raises(DependencyBuildError):
        run_dependencies_built_stage(
            run_id="run-1",
            source_package_hash=VALID_HASH,
            run_stages=[],
            inventory=inventory,
            canonical_dir=canonical_dir,
            dependencies_path=tmp_path / "03-dependencies.json",
        )


def test_duplicate_parsed_stage_execution_is_fatal(tmp_path: Path) -> None:
    canonical_dir = tmp_path / "02-canonical"
    _write_canonical(canonical_dir, RELATIVE_PATH, _canonical_program(source_file=RELATIVE_PATH))
    inventory = _inventory([_inventory_file(RELATIVE_PATH)])

    with pytest.raises(DependencyBuildError):
        run_dependencies_built_stage(
            run_id="run-1",
            source_package_hash=VALID_HASH,
            run_stages=[_parsed_succeeded_stage(), _parsed_succeeded_stage()],
            inventory=inventory,
            canonical_dir=canonical_dir,
            dependencies_path=tmp_path / "03-dependencies.json",
        )


def test_valid_existing_artifact_is_reused_without_rebuilding(tmp_path: Path) -> None:
    canonical_dir = tmp_path / "02-canonical"
    _write_canonical(canonical_dir, RELATIVE_PATH, _canonical_program(source_file=RELATIVE_PATH))
    inventory = _inventory([_inventory_file(RELATIVE_PATH)])
    dependencies_path = tmp_path / "03-dependencies.json"
    # Un programa sin paragraphs jamas produce warnings al reconstruir: si
    # el marcador sigue presente, prueba que NO se reconstruyo.
    existing = DependencyArtifact(
        run_id="run-1",
        source_package_hash=VALID_HASH,
        dependencies=[],
        warnings=["marcador de reutilizacion"],
    )
    dependencies_path.write_text(existing.to_stable_json(), encoding="utf-8")

    warnings = run_dependencies_built_stage(
        run_id="run-1",
        source_package_hash=VALID_HASH,
        run_stages=[_parsed_succeeded_stage()],
        inventory=inventory,
        canonical_dir=canonical_dir,
        dependencies_path=dependencies_path,
    )

    assert warnings == ["marcador de reutilizacion"]


def test_corrupt_artifact_is_rebuilt(tmp_path: Path) -> None:
    canonical_dir = tmp_path / "02-canonical"
    _write_canonical(canonical_dir, RELATIVE_PATH, _canonical_program(source_file=RELATIVE_PATH))
    inventory = _inventory([_inventory_file(RELATIVE_PATH)])
    dependencies_path = tmp_path / "03-dependencies.json"
    dependencies_path.write_text("{not valid json", encoding="utf-8")

    warnings = run_dependencies_built_stage(
        run_id="run-1",
        source_package_hash=VALID_HASH,
        run_stages=[_parsed_succeeded_stage()],
        inventory=inventory,
        canonical_dir=canonical_dir,
        dependencies_path=dependencies_path,
    )

    assert warnings == []
    artifact = DependencyArtifact.model_validate_json(
        dependencies_path.read_text(encoding="utf-8")
    )
    assert artifact.dependencies == []


def test_wrong_run_id_triggers_rebuild(tmp_path: Path) -> None:
    canonical_dir = tmp_path / "02-canonical"
    _write_canonical(canonical_dir, RELATIVE_PATH, _canonical_program(source_file=RELATIVE_PATH))
    inventory = _inventory([_inventory_file(RELATIVE_PATH)])
    dependencies_path = tmp_path / "03-dependencies.json"
    existing = DependencyArtifact(
        run_id="other-run", source_package_hash=VALID_HASH, dependencies=[], warnings=["stale"]
    )
    dependencies_path.write_text(existing.to_stable_json(), encoding="utf-8")

    warnings = run_dependencies_built_stage(
        run_id="run-1",
        source_package_hash=VALID_HASH,
        run_stages=[_parsed_succeeded_stage()],
        inventory=inventory,
        canonical_dir=canonical_dir,
        dependencies_path=dependencies_path,
    )

    assert warnings == []
    rebuilt = DependencyArtifact.model_validate_json(dependencies_path.read_text(encoding="utf-8"))
    assert rebuilt.run_id == "run-1"


def test_wrong_source_package_hash_triggers_rebuild(tmp_path: Path) -> None:
    canonical_dir = tmp_path / "02-canonical"
    _write_canonical(canonical_dir, RELATIVE_PATH, _canonical_program(source_file=RELATIVE_PATH))
    inventory = _inventory([_inventory_file(RELATIVE_PATH)])
    dependencies_path = tmp_path / "03-dependencies.json"
    existing = DependencyArtifact(
        run_id="run-1", source_package_hash="c" * 64, dependencies=[], warnings=["stale"]
    )
    dependencies_path.write_text(existing.to_stable_json(), encoding="utf-8")

    warnings = run_dependencies_built_stage(
        run_id="run-1",
        source_package_hash=VALID_HASH,
        run_stages=[_parsed_succeeded_stage()],
        inventory=inventory,
        canonical_dir=canonical_dir,
        dependencies_path=dependencies_path,
    )

    assert warnings == []
    rebuilt = DependencyArtifact.model_validate_json(dependencies_path.read_text(encoding="utf-8"))
    assert rebuilt.source_package_hash == VALID_HASH


def test_stale_paragraph_reference_triggers_rebuild(tmp_path: Path) -> None:
    canonical_dir = tmp_path / "02-canonical"
    _write_canonical(canonical_dir, RELATIVE_PATH, _canonical_program(source_file=RELATIVE_PATH))
    inventory = _inventory([_inventory_file(RELATIVE_PATH)])
    dependencies_path = tmp_path / "03-dependencies.json"

    stale_prefix = f"program::AR::OP-TRF-PROPIA::PROG1::1.0::{SOURCE_HASH[:12]}::paragraph::"
    stale_dependency = ParagraphDependency(
        dependency_type=DependencyType.CONTROL_DEPENDS_ON,
        from_paragraph_id=f"{stale_prefix}DOES-NOT-EXIST",
        to_paragraph_id=f"{stale_prefix}ALSO-MISSING",
        variables=[],
        control_construct="PERFORM",
        dependency_depth=1,
        confidence=1.0,
        derivation_rule="EXPLICIT_CONTROL_TARGET",
        source_file=RELATIVE_PATH,
        line_start=1,
        line_end=1,
        location_kind=LocationKind.EXACT,
        source_package_hash=VALID_HASH,
        evidence=[
            DependencyEvidence(
                role=DependencyEvidenceRole.CONTROL,
                statement_id="stale::0::PERFORM",
                statement_kind=StatementKind.PERFORM,
                source_text="PERFORM DOES-NOT-EXIST.",
                source_file=RELATIVE_PATH,
                line_start=1,
                line_end=1,
                location_kind=LocationKind.EXACT,
                original_target="DOES-NOT-EXIST",
            )
        ],
    )
    existing = DependencyArtifact(
        run_id="run-1",
        source_package_hash=VALID_HASH,
        dependencies=[stale_dependency],
        warnings=[],
    )
    dependencies_path.write_text(existing.to_stable_json(), encoding="utf-8")

    run_dependencies_built_stage(
        run_id="run-1",
        source_package_hash=VALID_HASH,
        run_stages=[_parsed_succeeded_stage()],
        inventory=inventory,
        canonical_dir=canonical_dir,
        dependencies_path=dependencies_path,
    )

    rebuilt = DependencyArtifact.model_validate_json(dependencies_path.read_text(encoding="utf-8"))
    # el programa actual no tiene paragraphs: la reconstruccion debe
    # quedar vacia, sin rastro de la referencia obsoleta.
    assert rebuilt.dependencies == []
