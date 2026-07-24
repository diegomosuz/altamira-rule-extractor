"""Tests de runner: orquestacion RECEIVED..CONTEXTS_BUILT, hash e
idempotencia.

La mayoria de estos tests ejercitan RECEIVED..INVENTORIED y la plomeria de
RunState alrededor de PARSED/DEPENDENCIES_BUILT/SEMANTIC_ENRICHMENT_BUILT/
SEMANTIC_GRAPH_BUILT/SEMANTIC_GRAPH_LOADED/GRAPH_VALIDATED/
CANDIDATES_DETECTED/CONTEXTS_BUILT (transiciones, StageExecution,
idempotencia a nivel de etapa); por eso las ocho etapas se stubean por
defecto para no depender de un JAR, un binario `java`, un servidor Neo4j
real, ni YAML/artefactos reales aqui. El comportamiento real de PARSED
esta cubierto en test_parser_client.py/test_parsed_stage.py; el de
DEPENDENCIES_BUILT en
test_dependency_builder.py/test_dependencies_stage.py; el de
SEMANTIC_ENRICHMENT_BUILT en test_ddl_parser.py/test_csv_loader.py/
test_semantic_tagger.py/test_domain_term_mapper.py/
test_semantic_enrichment_stage.py; el de SEMANTIC_GRAPH_BUILT en
test_semantic_graph_builder.py/test_semantic_graph_stage.py; el de
SEMANTIC_GRAPH_LOADED/GRAPH_VALIDATED en test_neo4j_repository.py/
test_semantic_graph_load_stage.py/test_graph_invariant_validator.py/
test_graph_validated_stage.py; el de CANDIDATES_DETECTED en
test_candidate_detector.py/test_candidates_detected_stage.py; el de
CONTEXTS_BUILT en test_context_package_builder.py/
test_contexts_built_stage.py; la integracion con JAR/Neo4j reales esta
en tests/parser_integration/ y tests/neo4j_integration/."""

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
    CandidateDetectionError,
    ContextBuildError,
    DependencyBuildError,
    GraphLoadError,
    GraphValidationError,
    ParserUnavailableError,
    RunConflictError,
    SemanticEnrichmentBuildError,
    SemanticGraphBuildError,
)
from altamira_extractor.pipeline.neo4j_repository import GraphLoadResult
from altamira_extractor.pipeline.parsed_stage import ParsedStageOutcome
from altamira_extractor.pipeline.runner import _copy_and_hash, run_ingestion

from .conftest import build_valid_package_zip

_TOTAL_STAGE_COUNT = 12


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


@pytest.fixture(autouse=True)
def _stub_semantic_enrichment_stage_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runner_module,
        "run_semantic_enrichment_stage",
        lambda **kwargs: [],
    )


@pytest.fixture(autouse=True)
def _stub_semantic_graph_stage_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runner_module,
        "run_semantic_graph_stage",
        lambda **kwargs: [],
    )


@pytest.fixture(autouse=True)
def _stub_semantic_graph_load_stage_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runner_module,
        "run_semantic_graph_load_stage",
        lambda **kwargs: GraphLoadResult(
            node_count=0, relationship_count=0, server_version="5.24.0", database="neo4j"
        ),
    )


@pytest.fixture(autouse=True)
def _stub_graph_validated_stage_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runner_module,
        "run_graph_validated_stage",
        lambda **kwargs: [],
    )


@pytest.fixture(autouse=True)
def _stub_candidates_detected_stage_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runner_module,
        "run_candidates_detected_stage",
        lambda **kwargs: [],
    )


@pytest.fixture(autouse=True)
def _stub_contexts_built_stage_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runner_module,
        "run_contexts_built_stage",
        lambda **kwargs: [],
    )


def test_full_happy_path_reaches_contexts_built(tmp_path: Path, settings: Settings) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")

    state = run_ingestion(zip_path, settings)

    assert state.current_stage == PipelineStage.CONTEXTS_BUILT
    stage_names = [s.stage for s in state.stages]
    assert stage_names == [
        PipelineStage.RECEIVED,
        PipelineStage.VALIDATED,
        PipelineStage.EXTRACTED,
        PipelineStage.INVENTORIED,
        PipelineStage.PARSED,
        PipelineStage.DEPENDENCIES_BUILT,
        PipelineStage.SEMANTIC_ENRICHMENT_BUILT,
        PipelineStage.SEMANTIC_GRAPH_BUILT,
        PipelineStage.SEMANTIC_GRAPH_LOADED,
        PipelineStage.GRAPH_VALIDATED,
        PipelineStage.CANDIDATES_DETECTED,
        PipelineStage.CONTEXTS_BUILT,
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
    assert len(second_state.stages) == _TOTAL_STAGE_COUNT


def test_no_duplicate_stage_executions_across_reruns(tmp_path: Path, settings: Settings) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    state = run_ingestion(zip_path, settings)
    run_id = state.run_id

    state_again = run_ingestion(zip_path, settings, run_id=run_id)

    stages_seen = [s.stage for s in state_again.stages]
    assert len(stages_seen) == len(set(stages_seen)) == _TOTAL_STAGE_COUNT


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

    assert rebuilt_state.current_stage == PipelineStage.CONTEXTS_BUILT
    inventory = Inventory.model_validate_json(inventory_path.read_text(encoding="utf-8"))
    assert inventory.run_id == state.run_id
    stage_names = [s.stage for s in rebuilt_state.stages]
    assert len(stage_names) == len(set(stage_names)) == _TOTAL_STAGE_COUNT
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

    # PARSED tuvo exito (con warnings) y las etapas siguientes (stubeadas)
    # tambien: el pipeline avanza mas alla de PARSED.
    assert state.current_stage == PipelineStage.CONTEXTS_BUILT
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
    assert second_state.current_stage == PipelineStage.CONTEXTS_BUILT
    stage_names = [s.stage for s in second_state.stages]
    assert len(stage_names) == len(set(stage_names)) == _TOTAL_STAGE_COUNT
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
    assert not any(s.stage == PipelineStage.SEMANTIC_ENRICHMENT_BUILT for s in state.stages)


def test_dependencies_built_warnings_propagate_to_stage_execution(
    tmp_path: Path, settings: Settings
) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")

    def _succeed_with_warnings(**kwargs: object) -> list[str]:
        return ["referencia no resuelta: PARA-X"]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner_module, "run_dependencies_built_stage", _succeed_with_warnings)
        state = run_ingestion(zip_path, settings)

    assert state.current_stage == PipelineStage.CONTEXTS_BUILT
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
    assert second_state.current_stage == PipelineStage.CONTEXTS_BUILT
    stage_names = [s.stage for s in second_state.stages]
    assert len(stage_names) == len(set(stage_names)) == _TOTAL_STAGE_COUNT
    dependencies_executions = [
        s for s in second_state.stages if s.stage == PipelineStage.DEPENDENCIES_BUILT
    ]
    assert len(dependencies_executions) == 1
    assert dependencies_executions[0].status == StageStatus.SUCCEEDED


def test_semantic_enrichment_built_failure_marks_run_failed_with_error(
    tmp_path: Path, settings: Settings
) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")

    def _fail(**kwargs: object) -> list[str]:
        raise SemanticEnrichmentBuildError("YAML de configuracion invalido")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner_module, "run_semantic_enrichment_stage", _fail)
        state = run_ingestion(zip_path, settings)

    assert state.current_stage == PipelineStage.FAILED
    execution = next(
        s for s in state.stages if s.stage == PipelineStage.SEMANTIC_ENRICHMENT_BUILT
    )
    assert execution.status == StageStatus.FAILED
    assert "YAML" in (execution.error or "")
    assert not any(s.stage == PipelineStage.SEMANTIC_GRAPH_BUILT for s in state.stages)


def test_semantic_enrichment_built_warnings_propagate_to_stage_execution(
    tmp_path: Path, settings: Settings
) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")

    def _succeed_with_warnings(**kwargs: object) -> list[str]:
        return ["PARAM_TRANSFER: DDL no soportado"]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner_module, "run_semantic_enrichment_stage", _succeed_with_warnings)
        state = run_ingestion(zip_path, settings)

    assert state.current_stage == PipelineStage.CONTEXTS_BUILT
    execution = next(
        s for s in state.stages if s.stage == PipelineStage.SEMANTIC_ENRICHMENT_BUILT
    )
    assert execution.warnings == ["PARAM_TRANSFER: DDL no soportado"]


def test_retry_after_semantic_enrichment_built_failure_does_not_duplicate_stage_execution(
    tmp_path: Path, settings: Settings
) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    run_id = "semantic-enrichment-retry"

    def _fail(**kwargs: object) -> list[str]:
        raise SemanticEnrichmentBuildError("fallo simulado")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner_module, "run_semantic_enrichment_stage", _fail)
        first_state = run_ingestion(zip_path, settings, run_id=run_id)

    second_state = run_ingestion(zip_path, settings, run_id=run_id)

    assert first_state.current_stage == PipelineStage.FAILED
    assert second_state.current_stage == PipelineStage.CONTEXTS_BUILT
    stage_names = [s.stage for s in second_state.stages]
    assert len(stage_names) == len(set(stage_names)) == _TOTAL_STAGE_COUNT
    executions = [
        s for s in second_state.stages if s.stage == PipelineStage.SEMANTIC_ENRICHMENT_BUILT
    ]
    assert len(executions) == 1
    assert executions[0].status == StageStatus.SUCCEEDED


def test_semantic_graph_built_failure_marks_run_failed_with_error(
    tmp_path: Path, settings: Settings
) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")

    def _fail(**kwargs: object) -> list[str]:
        raise SemanticGraphBuildError("referencia huerfana en DependencyArtifact")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner_module, "run_semantic_graph_stage", _fail)
        state = run_ingestion(zip_path, settings)

    assert state.current_stage == PipelineStage.FAILED
    execution = next(s for s in state.stages if s.stage == PipelineStage.SEMANTIC_GRAPH_BUILT)
    assert execution.status == StageStatus.FAILED
    assert "huerfana" in (execution.error or "")
    assert not any(s.stage == PipelineStage.SEMANTIC_GRAPH_LOADED for s in state.stages)


def test_semantic_graph_built_warnings_propagate_to_stage_execution(
    tmp_path: Path, settings: Settings
) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")

    def _succeed_with_warnings(**kwargs: object) -> list[str]:
        return ["referencia SQL no calificada es ambigua"]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner_module, "run_semantic_graph_stage", _succeed_with_warnings)
        state = run_ingestion(zip_path, settings)

    assert state.current_stage == PipelineStage.CONTEXTS_BUILT
    execution = next(s for s in state.stages if s.stage == PipelineStage.SEMANTIC_GRAPH_BUILT)
    assert execution.warnings == ["referencia SQL no calificada es ambigua"]


def test_retry_after_semantic_graph_built_failure_does_not_duplicate_stage_execution(
    tmp_path: Path, settings: Settings
) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    run_id = "semantic-graph-retry"

    def _fail(**kwargs: object) -> list[str]:
        raise SemanticGraphBuildError("fallo simulado")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner_module, "run_semantic_graph_stage", _fail)
        first_state = run_ingestion(zip_path, settings, run_id=run_id)

    second_state = run_ingestion(zip_path, settings, run_id=run_id)

    assert first_state.current_stage == PipelineStage.FAILED
    assert second_state.current_stage == PipelineStage.CONTEXTS_BUILT
    stage_names = [s.stage for s in second_state.stages]
    assert len(stage_names) == len(set(stage_names)) == _TOTAL_STAGE_COUNT
    executions = [
        s for s in second_state.stages if s.stage == PipelineStage.SEMANTIC_GRAPH_BUILT
    ]
    assert len(executions) == 1
    assert executions[0].status == StageStatus.SUCCEEDED


def test_semantic_graph_loaded_failure_marks_run_failed_with_error(
    tmp_path: Path, settings: Settings
) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")

    def _fail(**kwargs: object) -> GraphLoadResult:
        raise GraphLoadError("conteo de nodos administrados no coincide")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner_module, "run_semantic_graph_load_stage", _fail)
        state = run_ingestion(zip_path, settings)

    assert state.current_stage == PipelineStage.FAILED
    execution = next(s for s in state.stages if s.stage == PipelineStage.SEMANTIC_GRAPH_LOADED)
    assert execution.status == StageStatus.FAILED
    assert "coincide" in (execution.error or "")
    assert not any(s.stage == PipelineStage.GRAPH_VALIDATED for s in state.stages)


def test_semantic_graph_loaded_summary_propagates_to_stage_execution(
    tmp_path: Path, settings: Settings
) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")

    def _succeed(**kwargs: object) -> GraphLoadResult:
        return GraphLoadResult(
            node_count=42, relationship_count=17, server_version="5.24.0", database="neo4j"
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner_module, "run_semantic_graph_load_stage", _succeed)
        state = run_ingestion(zip_path, settings)

    assert state.current_stage == PipelineStage.CONTEXTS_BUILT
    execution = next(s for s in state.stages if s.stage == PipelineStage.SEMANTIC_GRAPH_LOADED)
    assert execution.warnings == [
        "cargados 42 nodos / 17 relaciones (Neo4j 5.24.0, database 'neo4j')"
    ]


def test_retry_after_semantic_graph_loaded_failure_does_not_duplicate_stage_execution(
    tmp_path: Path, settings: Settings
) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    run_id = "semantic-graph-loaded-retry"

    def _fail(**kwargs: object) -> GraphLoadResult:
        raise GraphLoadError("fallo simulado")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner_module, "run_semantic_graph_load_stage", _fail)
        first_state = run_ingestion(zip_path, settings, run_id=run_id)

    second_state = run_ingestion(zip_path, settings, run_id=run_id)

    assert first_state.current_stage == PipelineStage.FAILED
    assert second_state.current_stage == PipelineStage.CONTEXTS_BUILT
    stage_names = [s.stage for s in second_state.stages]
    assert len(stage_names) == len(set(stage_names)) == _TOTAL_STAGE_COUNT
    executions = [
        s for s in second_state.stages if s.stage == PipelineStage.SEMANTIC_GRAPH_LOADED
    ]
    assert len(executions) == 1
    assert executions[0].status == StageStatus.SUCCEEDED


def test_graph_validated_failure_marks_run_failed_with_error(
    tmp_path: Path, settings: Settings
) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")

    def _fail(**kwargs: object) -> list[str]:
        raise GraphValidationError("2 invariante(s) de severidad ERROR incumplido(s)")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner_module, "run_graph_validated_stage", _fail)
        state = run_ingestion(zip_path, settings)

    assert state.current_stage == PipelineStage.FAILED
    execution = next(s for s in state.stages if s.stage == PipelineStage.GRAPH_VALIDATED)
    assert execution.status == StageStatus.FAILED
    assert "ERROR" in (execution.error or "")
    assert not any(s.stage == PipelineStage.CANDIDATES_DETECTED for s in state.stages)


def test_graph_validated_warnings_propagate_to_stage_execution(
    tmp_path: Path, settings: Settings
) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")

    def _succeed_with_warnings(**kwargs: object) -> list[str]:
        return ["SOME_WARNING: aviso no bloqueante (entity::1)"]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner_module, "run_graph_validated_stage", _succeed_with_warnings)
        state = run_ingestion(zip_path, settings)

    assert state.current_stage == PipelineStage.CONTEXTS_BUILT
    execution = next(s for s in state.stages if s.stage == PipelineStage.GRAPH_VALIDATED)
    assert execution.warnings == ["SOME_WARNING: aviso no bloqueante (entity::1)"]


def test_retry_after_graph_validated_failure_does_not_duplicate_stage_execution(
    tmp_path: Path, settings: Settings
) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    run_id = "graph-validated-retry"

    def _fail(**kwargs: object) -> list[str]:
        raise GraphValidationError("fallo simulado")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner_module, "run_graph_validated_stage", _fail)
        first_state = run_ingestion(zip_path, settings, run_id=run_id)

    second_state = run_ingestion(zip_path, settings, run_id=run_id)

    assert first_state.current_stage == PipelineStage.FAILED
    assert second_state.current_stage == PipelineStage.CONTEXTS_BUILT
    stage_names = [s.stage for s in second_state.stages]
    assert len(stage_names) == len(set(stage_names)) == _TOTAL_STAGE_COUNT
    executions = [s for s in second_state.stages if s.stage == PipelineStage.GRAPH_VALIDATED]
    assert len(executions) == 1
    assert executions[0].status == StageStatus.SUCCEEDED


def test_candidates_detected_failure_marks_run_failed_with_error(
    tmp_path: Path, settings: Settings
) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")

    def _fail(**kwargs: object) -> list[str]:
        raise CandidateDetectionError("drift detectado entre 04-semantic-graph.json y Neo4j")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner_module, "run_candidates_detected_stage", _fail)
        state = run_ingestion(zip_path, settings)

    assert state.current_stage == PipelineStage.FAILED
    execution = next(s for s in state.stages if s.stage == PipelineStage.CANDIDATES_DETECTED)
    assert execution.status == StageStatus.FAILED
    assert "drift" in (execution.error or "")
    assert not any(s.stage == PipelineStage.CONTEXTS_BUILT for s in state.stages)


def test_candidates_detected_warnings_propagate_to_stage_execution(
    tmp_path: Path, settings: Settings
) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")

    def _succeed_with_warnings(**kwargs: object) -> list[str]:
        return ["detectados 3 candidato(s)"]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner_module, "run_candidates_detected_stage", _succeed_with_warnings)
        state = run_ingestion(zip_path, settings)

    assert state.current_stage == PipelineStage.CONTEXTS_BUILT
    execution = next(s for s in state.stages if s.stage == PipelineStage.CANDIDATES_DETECTED)
    assert execution.warnings == ["detectados 3 candidato(s)"]


def test_retry_after_candidates_detected_failure_does_not_duplicate_stage_execution(
    tmp_path: Path, settings: Settings
) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    run_id = "candidates-detected-retry"

    def _fail(**kwargs: object) -> list[str]:
        raise CandidateDetectionError("fallo simulado")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner_module, "run_candidates_detected_stage", _fail)
        first_state = run_ingestion(zip_path, settings, run_id=run_id)

    second_state = run_ingestion(zip_path, settings, run_id=run_id)

    assert first_state.current_stage == PipelineStage.FAILED
    assert second_state.current_stage == PipelineStage.CONTEXTS_BUILT
    stage_names = [s.stage for s in second_state.stages]
    assert len(stage_names) == len(set(stage_names)) == _TOTAL_STAGE_COUNT
    executions = [s for s in second_state.stages if s.stage == PipelineStage.CANDIDATES_DETECTED]
    assert len(executions) == 1
    assert executions[0].status == StageStatus.SUCCEEDED


def test_contexts_built_failure_marks_run_failed_with_error(
    tmp_path: Path, settings: Settings
) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")

    def _fail(**kwargs: object) -> list[str]:
        raise ContextBuildError("drift detectado entre 04-semantic-graph.json y Neo4j")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner_module, "run_contexts_built_stage", _fail)
        state = run_ingestion(zip_path, settings)

    assert state.current_stage == PipelineStage.FAILED
    execution = next(s for s in state.stages if s.stage == PipelineStage.CONTEXTS_BUILT)
    assert execution.status == StageStatus.FAILED
    assert "drift" in (execution.error or "")


def test_contexts_built_warnings_propagate_to_stage_execution(
    tmp_path: Path, settings: Settings
) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")

    def _succeed_with_warnings(**kwargs: object) -> list[str]:
        return ["3 contexto(s)"]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner_module, "run_contexts_built_stage", _succeed_with_warnings)
        state = run_ingestion(zip_path, settings)

    assert state.current_stage == PipelineStage.CONTEXTS_BUILT
    execution = next(s for s in state.stages if s.stage == PipelineStage.CONTEXTS_BUILT)
    assert execution.warnings == ["3 contexto(s)"]


def test_retry_after_contexts_built_failure_does_not_duplicate_stage_execution(
    tmp_path: Path, settings: Settings
) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    run_id = "contexts-built-retry"

    def _fail(**kwargs: object) -> list[str]:
        raise ContextBuildError("fallo simulado")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner_module, "run_contexts_built_stage", _fail)
        first_state = run_ingestion(zip_path, settings, run_id=run_id)

    second_state = run_ingestion(zip_path, settings, run_id=run_id)

    assert first_state.current_stage == PipelineStage.FAILED
    assert second_state.current_stage == PipelineStage.CONTEXTS_BUILT
    stage_names = [s.stage for s in second_state.stages]
    assert len(stage_names) == len(set(stage_names)) == _TOTAL_STAGE_COUNT
    executions = [s for s in second_state.stages if s.stage == PipelineStage.CONTEXTS_BUILT]
    assert len(executions) == 1
    assert executions[0].status == StageStatus.SUCCEEDED
