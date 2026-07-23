"""Tests de runner: orquestacion RECEIVED..DEPENDENCIES_BUILT, hash e idempotencia.

La mayoria de estos tests ejercitan RECEIVED..INVENTORIED y la plomeria de
RunState alrededor de PARSED/DEPENDENCIES_BUILT (transiciones,
StageExecution, idempotencia a nivel de etapa); por eso ambas etapas se
stubean por defecto para no depender de un JAR ni de un binario `java`
reales aqui. El comportamiento real de PARSED esta cubierto en
test_parser_client.py/test_parsed_stage.py; el de DEPENDENCIES_BUILT en
test_dependency_builder.py/test_dependencies_stage.py; la integracion con
el JAR real esta en tests/parser_integration/."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from altamira_extractor.config import Settings
from altamira_extractor.contracts.enums import PipelineStage, StageStatus
from altamira_extractor.contracts.inventory import Inventory
from altamira_extractor.contracts.run_state import RunState
from altamira_extractor.pipeline import runner as runner_module
from altamira_extractor.pipeline.errors import (
    DependencyBuildError,
    ParserUnavailableError,
    RunConflictError,
)
from altamira_extractor.pipeline.parsed_stage import ParsedStageOutcome
from altamira_extractor.pipeline.runner import _copy_and_hash, run_ingestion

from .conftest import build_valid_package_zip


@pytest.fixture(autouse=True)
def _stub_parsed_stage_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runner_module,
        "run_parsed_stage",
        lambda **kwargs: ParsedStageOutcome(succeeded=True, warnings=[], error=None),
    )


@pytest.fixture(autouse=True)
def _stub_dependencies_built_stage_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runner_module,
        "run_dependencies_built_stage",
        lambda **kwargs: [],
    )


def test_full_happy_path_reaches_dependencies_built(tmp_path: Path, settings: Settings) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")

    state = run_ingestion(zip_path, settings)

    assert state.current_stage == PipelineStage.DEPENDENCIES_BUILT
    stage_names = [s.stage for s in state.stages]
    assert stage_names == [
        PipelineStage.RECEIVED,
        PipelineStage.VALIDATED,
        PipelineStage.EXTRACTED,
        PipelineStage.INVENTORIED,
        PipelineStage.PARSED,
        PipelineStage.DEPENDENCIES_BUILT,
    ]
    assert all(s.status == StageStatus.SUCCEEDED for s in state.stages)

    run_json_path = settings.runs_dir / state.run_id / "run.json"
    persisted = RunState.model_validate_json(run_json_path.read_text(encoding="utf-8"))
    assert persisted == state

    inventory_path = settings.runs_dir / state.run_id / "artifacts" / "01-inventory.json"
    inventory = Inventory.model_validate_json(inventory_path.read_text(encoding="utf-8"))
    assert inventory.run_id == state.run_id


def test_copy_and_hash_reflects_bytes_actually_persisted(tmp_path: Path) -> None:
    source = tmp_path / "source.zip"
    source.write_bytes(b"contenido de prueba" * 1000)
    destination = tmp_path / "copied.zip"

    digest = _copy_and_hash(source, destination)

    assert digest == hashlib.sha256(source.read_bytes()).hexdigest()
    assert digest == hashlib.sha256(destination.read_bytes()).hexdigest()
    assert destination.read_bytes() == source.read_bytes()


def test_source_package_hash_matches_copied_input(tmp_path: Path, settings: Settings) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")

    state = run_ingestion(zip_path, settings)

    input_zip_path = settings.runs_dir / state.run_id / "input" / "package.zip"
    assert state.source_package_hash == hashlib.sha256(input_zip_path.read_bytes()).hexdigest()


def test_preexisting_run_id_ignores_new_source_and_does_not_overwrite(
    tmp_path: Path, settings: Settings
) -> None:
    first_zip = build_valid_package_zip(tmp_path / "first.zip")
    state = run_ingestion(first_zip, settings)
    input_zip_path = settings.runs_dir / state.run_id / "input" / "package.zip"
    original_bytes = input_zip_path.read_bytes()

    second_zip = build_valid_package_zip(
        tmp_path / "second.zip", extra={"01-codigo/EXTRA.txt": b"contenido distinto"}
    )
    second_state = run_ingestion(second_zip, settings, run_id=state.run_id)

    assert input_zip_path.read_bytes() == original_bytes
    assert second_state.source_package_hash == state.source_package_hash
    assert len(second_state.stages) == 6


def test_no_duplicate_stage_executions_across_reruns(tmp_path: Path, settings: Settings) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    state = run_ingestion(zip_path, settings)
    run_id = state.run_id

    state_again = run_ingestion(zip_path, settings, run_id=run_id)

    stages_seen = [s.stage for s in state_again.stages]
    assert len(stages_seen) == len(set(stages_seen)) == 6


def test_received_rejects_foreign_directory_without_run_state(
    tmp_path: Path, settings: Settings
) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    run_id = "foreign-run"
    foreign_input = settings.runs_dir / run_id / "input" / "package.zip"
    foreign_input.parent.mkdir(parents=True)
    foreign_input.write_bytes(b"contenido ajeno, sin run.json")

    with pytest.raises(RunConflictError):
        run_ingestion(zip_path, settings, run_id=run_id)


def _sabotage_work_dir_as_a_file(settings: Settings, run_id: str) -> Path:
    """Fuerza que EXTRACTED falle: `work/` no puede crearse porque ya existe
    como archivo regular. VALIDATED no toca `work/`, asi que solo EXTRACTED
    se ve afectado."""
    work_dir = settings.runs_dir / run_id / "work"
    work_dir.parent.mkdir(parents=True)
    work_dir.write_bytes(b"esto no es un directorio")
    return work_dir


def test_extraction_failure_marks_run_failed_without_inventory(
    tmp_path: Path, settings: Settings
) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    run_id = "extraction-will-fail"
    work_dir = _sabotage_work_dir_as_a_file(settings, run_id)

    state = run_ingestion(zip_path, settings, run_id=run_id)

    assert state.current_stage == PipelineStage.FAILED
    stage_names = [s.stage for s in state.stages]
    assert stage_names == [
        PipelineStage.RECEIVED,
        PipelineStage.VALIDATED,
        PipelineStage.EXTRACTED,
    ]
    assert state.stages[-1].status == StageStatus.FAILED
    assert state.stages[-1].error

    inventory_path = settings.runs_dir / run_id / "artifacts" / "01-inventory.json"
    assert not inventory_path.exists()
    assert not (work_dir / "extracted").exists()


def test_retry_after_extraction_failure_does_not_duplicate_stages(
    tmp_path: Path, settings: Settings
) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    run_id = "extraction-will-fail-retry"
    _sabotage_work_dir_as_a_file(settings, run_id)

    first_state = run_ingestion(zip_path, settings, run_id=run_id)
    second_state = run_ingestion(zip_path, settings, run_id=run_id)

    assert first_state.current_stage == PipelineStage.FAILED
    assert second_state.current_stage == PipelineStage.FAILED
    stage_names = [s.stage for s in second_state.stages]
    assert len(stage_names) == len(set(stage_names)) == 3


def test_corrupt_inventory_is_rebuilt_instead_of_reused(
    tmp_path: Path, settings: Settings
) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    state = run_ingestion(zip_path, settings)
    inventory_path = settings.runs_dir / state.run_id / "artifacts" / "01-inventory.json"
    inventory_path.write_text("{not valid json", encoding="utf-8")

    rebuilt_state = run_ingestion(zip_path, settings, run_id=state.run_id)

    assert rebuilt_state.current_stage == PipelineStage.DEPENDENCIES_BUILT
    inventory = Inventory.model_validate_json(inventory_path.read_text(encoding="utf-8"))
    assert inventory.run_id == state.run_id
    stage_names = [s.stage for s in rebuilt_state.stages]
    assert len(stage_names) == len(set(stage_names)) == 6
    inventoried_stage = next(
        s for s in rebuilt_state.stages if s.stage == PipelineStage.INVENTORIED
    )
    assert inventoried_stage.status == StageStatus.SUCCEEDED


def test_parsed_failure_marks_run_failed_with_error(tmp_path: Path, settings: Settings) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")

    def _fail(**kwargs: object) -> ParsedStageOutcome:
        return ParsedStageOutcome(
            succeeded=False, warnings=["A.cbl: exit 3"], error="1 programa(s) fallaron: A.cbl"
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner_module, "run_parsed_stage", _fail)
        state = run_ingestion(zip_path, settings)

    assert state.current_stage == PipelineStage.FAILED
    parsed_stage_execution = next(s for s in state.stages if s.stage == PipelineStage.PARSED)
    assert parsed_stage_execution.status == StageStatus.FAILED
    assert parsed_stage_execution.error == "1 programa(s) fallaron: A.cbl"
    # PARSED fallo: DEPENDENCIES_BUILT nunca deberia intentarse.
    assert not any(s.stage == PipelineStage.DEPENDENCIES_BUILT for s in state.stages)


def test_parsed_fatal_exception_marks_run_failed(tmp_path: Path, settings: Settings) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")

    def _raise(**kwargs: object) -> ParsedStageOutcome:
        raise ParserUnavailableError("no se encontro el JAR del parser")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner_module, "run_parsed_stage", _raise)
        state = run_ingestion(zip_path, settings)

    assert state.current_stage == PipelineStage.FAILED
    parsed_stage_execution = next(s for s in state.stages if s.stage == PipelineStage.PARSED)
    assert parsed_stage_execution.status == StageStatus.FAILED
    assert "JAR" in (parsed_stage_execution.error or "")


def test_parsed_warnings_propagate_to_stage_execution(
    tmp_path: Path, settings: Settings
) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")

    def _succeed_with_warnings(**kwargs: object) -> ParsedStageOutcome:
        return ParsedStageOutcome(succeeded=True, warnings=["aviso de ejemplo"], error=None)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner_module, "run_parsed_stage", _succeed_with_warnings)
        state = run_ingestion(zip_path, settings)

    # PARSED tuvo exito (con warnings) y DEPENDENCIES_BUILT (stubeado) tambien:
    # el pipeline avanza mas alla de PARSED.
    assert state.current_stage == PipelineStage.DEPENDENCIES_BUILT
    parsed_stage_execution = next(s for s in state.stages if s.stage == PipelineStage.PARSED)
    assert parsed_stage_execution.warnings == ["aviso de ejemplo"]


def test_retry_after_parsed_failure_does_not_duplicate_stage_execution(
    tmp_path: Path, settings: Settings
) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    run_id = "parsed-retry"

    def _fail(**kwargs: object) -> ParsedStageOutcome:
        return ParsedStageOutcome(succeeded=False, warnings=[], error="fallo simulado")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner_module, "run_parsed_stage", _fail)
        first_state = run_ingestion(zip_path, settings, run_id=run_id)

    def _succeed(**kwargs: object) -> ParsedStageOutcome:
        return ParsedStageOutcome(succeeded=True, warnings=[], error=None)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner_module, "run_parsed_stage", _succeed)
        second_state = run_ingestion(zip_path, settings, run_id=run_id)

    assert first_state.current_stage == PipelineStage.FAILED
    assert second_state.current_stage == PipelineStage.DEPENDENCIES_BUILT
    stage_names = [s.stage for s in second_state.stages]
    assert len(stage_names) == len(set(stage_names)) == 6
    parsed_executions = [s for s in second_state.stages if s.stage == PipelineStage.PARSED]
    assert len(parsed_executions) == 1
    assert parsed_executions[0].status == StageStatus.SUCCEEDED


def test_dependencies_built_failure_marks_run_failed_with_error(
    tmp_path: Path, settings: Settings
) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")

    def _fail(**kwargs: object) -> list[str]:
        raise DependencyBuildError("falta un CanonicalProgram esperado")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner_module, "run_dependencies_built_stage", _fail)
        state = run_ingestion(zip_path, settings)

    assert state.current_stage == PipelineStage.FAILED
    dependencies_execution = next(
        s for s in state.stages if s.stage == PipelineStage.DEPENDENCIES_BUILT
    )
    assert dependencies_execution.status == StageStatus.FAILED
    assert "CanonicalProgram" in (dependencies_execution.error or "")


def test_dependencies_built_warnings_propagate_to_stage_execution(
    tmp_path: Path, settings: Settings
) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")

    def _succeed_with_warnings(**kwargs: object) -> list[str]:
        return ["referencia no resuelta: PARA-X"]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner_module, "run_dependencies_built_stage", _succeed_with_warnings)
        state = run_ingestion(zip_path, settings)

    assert state.current_stage == PipelineStage.DEPENDENCIES_BUILT
    dependencies_execution = next(
        s for s in state.stages if s.stage == PipelineStage.DEPENDENCIES_BUILT
    )
    assert dependencies_execution.warnings == ["referencia no resuelta: PARA-X"]


def test_retry_after_dependencies_built_failure_does_not_duplicate_stage_execution(
    tmp_path: Path, settings: Settings
) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    run_id = "dependencies-retry"

    def _fail(**kwargs: object) -> list[str]:
        raise DependencyBuildError("fallo simulado")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner_module, "run_dependencies_built_stage", _fail)
        first_state = run_ingestion(zip_path, settings, run_id=run_id)

    second_state = run_ingestion(zip_path, settings, run_id=run_id)

    assert first_state.current_stage == PipelineStage.FAILED
    assert second_state.current_stage == PipelineStage.DEPENDENCIES_BUILT
    stage_names = [s.stage for s in second_state.stages]
    assert len(stage_names) == len(set(stage_names)) == 6
    dependencies_executions = [
        s for s in second_state.stages if s.stage == PipelineStage.DEPENDENCIES_BUILT
    ]
    assert len(dependencies_executions) == 1
    assert dependencies_executions[0].status == StageStatus.SUCCEEDED
