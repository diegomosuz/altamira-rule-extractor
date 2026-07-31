"""Servicio de filesystem del informe de cobertura semantica (Fase 1 de
la ampliacion semantica). Unico punto que localiza un run, carga y
valida los cuatro artefactos V1 requeridos, calcula sus hashes, invoca
`semantic_coverage_analyzer.analyze_semantic_coverage` (analizador puro)
y persiste el resultado en `<run_dir>/diagnostics/semantic-coverage.json`.

NO es un `PipelineStage`: se invoca exclusivamente bajo demanda (CLI
`semantic-coverage`), nunca desde `runner.py`/`run_ingestion`. Nunca
modifica ningun artefacto de entrada (`run.json`, `artifacts/02-canonical/`,
`artifacts/03-dependencies.json`, `artifacts/04-semantic-graph.json`,
`artifacts/06-candidates.json`); nunca escribe un reporte parcial (si
cualquier paso de carga/validacion falla, no se crea `diagnostics/`).

Deliberadamente sin ninguna dependencia de `api/`: `pipeline/` es la capa
de la que `api/`/`cli.py` dependen, nunca al reves (sin precedente en el
resto de `pipeline/` de importar `api.*`). La lectura/validacion minima
de `run.json` que este modulo necesita (existencia, CANDIDATES_DETECTED
SUCCEEDED) se implementa localmente, replicando exactamente la misma
logica que `api/reads.py::read_run_state`/`require_stage_succeeded` sin
importarlas, para mantener `pipeline/` autocontenido."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import BaseModel

from ..contracts.candidate import CandidateArtifact
from ..contracts.canonical import CanonicalProgram
from ..contracts.dependencies import DependencyArtifact
from ..contracts.enums import PipelineStage, StageStatus
from ..contracts.run_state import RunState
from ..contracts.semantic_coverage import SemanticCoverageReport
from ..contracts.semantic_graph import SemanticGraph
from .artifact_store import atomic_write_json
from .errors import SemanticCoverageError
from .semantic_coverage_analyzer import analyze_semantic_coverage

_CANONICAL_DIR_NAME = "02-canonical"
_DEPENDENCIES_FILENAME = "03-dependencies.json"
_SEMANTIC_GRAPH_FILENAME = "04-semantic-graph.json"
_CANDIDATES_FILENAME = "06-candidates.json"
_DIAGNOSTICS_DIR_NAME = "diagnostics"
_REPORT_FILENAME = "semantic-coverage.json"


def _hash_file_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash_directory(dir_path: Path) -> str:
    """Hash deterministico de un directorio: rutas relativas ordenadas +
    hash de cada archivo `.json`, nunca mtimes, nunca rutas absolutas."""
    entries = sorted(
        (path.relative_to(dir_path).as_posix(), _hash_file_bytes(path))
        for path in dir_path.rglob("*.json")
        if path.is_file()
    )
    digest_source = "\n".join(
        f"{relative_path}:{file_hash}" for relative_path, file_hash in entries
    )
    return hashlib.sha256(digest_source.encode("utf-8")).hexdigest()


def _load_run_state(run_dir: Path) -> RunState:
    """Replica exactamente `api/reads.py::read_run_state` para el unico
    caso que este servicio necesita, sin importar `api/` (ver docstring
    del modulo)."""
    run_json_path = run_dir / "run.json"
    if run_json_path.is_symlink() or not run_json_path.is_file():
        raise SemanticCoverageError(f"run {run_dir.name!r} no encontrado: run.json ausente")
    try:
        return RunState.model_validate_json(run_json_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SemanticCoverageError("run.json invalido") from exc


def _require_candidates_detected(state: RunState) -> None:
    execution = next(
        (s for s in state.stages if s.stage == PipelineStage.CANDIDATES_DETECTED), None
    )
    if execution is None or execution.status != StageStatus.SUCCEEDED:
        raise SemanticCoverageError(
            "el run no alcanzo CANDIDATES_DETECTED (SUCCEEDED); no se puede calcular "
            "cobertura semantica todavia"
        )


def _require_file(path: Path, *, artifact_label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise SemanticCoverageError(f"{artifact_label} ausente o no es un archivo regular")
    return path


def _require_directory(path: Path, *, artifact_label: str) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise SemanticCoverageError(f"{artifact_label} ausente o no es un directorio regular")
    return path


def _load_json_artifact[T: BaseModel](path: Path, model: type[T], *, artifact_label: str) -> T:
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SemanticCoverageError(f"{artifact_label}: fallo de lectura") from exc
    try:
        return model.model_validate_json(raw_text)
    except ValueError as exc:
        raise SemanticCoverageError(
            f"{artifact_label}: JSON invalido o incompatible con su contrato"
        ) from exc


def _load_canonical_programs(canonical_dir: Path) -> list[CanonicalProgram]:
    _require_directory(canonical_dir, artifact_label=f"artifacts/{_CANONICAL_DIR_NAME}")
    json_paths = sorted(
        path for path in canonical_dir.rglob("*.json") if path.is_file() and not path.is_symlink()
    )
    if not json_paths:
        raise SemanticCoverageError(
            f"artifacts/{_CANONICAL_DIR_NAME} no contiene ningun artefacto CanonicalProgram"
        )
    programs: list[CanonicalProgram] = []
    for json_path in json_paths:
        relative_suffix = json_path.relative_to(canonical_dir).as_posix()
        relative_label = f"artifacts/{_CANONICAL_DIR_NAME}/{relative_suffix}"
        programs.append(
            _load_json_artifact(json_path, CanonicalProgram, artifact_label=relative_label)
        )
    return programs


def compute_semantic_coverage_report(run_dir: Path, run_id: str) -> SemanticCoverageReport:
    """Localiza `run_dir`, carga y valida los cuatro artefactos V1
    requeridos, calcula sus hashes, y devuelve el `SemanticCoverageReport`
    calculado (analizador puro: sin Neo4j, sin LLM, sin variables de
    entorno). Nunca escribe nada -- la persistencia es responsabilidad
    de `write_semantic_coverage_report`/el comando CLI."""
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise SemanticCoverageError(f"run {run_dir.name!r} no encontrado")

    state = _load_run_state(run_dir)
    _require_candidates_detected(state)

    artifacts_dir = run_dir / "artifacts"
    canonical_dir = artifacts_dir / _CANONICAL_DIR_NAME
    dependencies_path = artifacts_dir / _DEPENDENCIES_FILENAME
    semantic_graph_path = artifacts_dir / _SEMANTIC_GRAPH_FILENAME
    candidates_path = artifacts_dir / _CANDIDATES_FILENAME

    canonical_programs = _load_canonical_programs(canonical_dir)

    dependency_artifact = _load_json_artifact(
        _require_file(dependencies_path, artifact_label=f"artifacts/{_DEPENDENCIES_FILENAME}"),
        DependencyArtifact,
        artifact_label=f"artifacts/{_DEPENDENCIES_FILENAME}",
    )
    semantic_graph = _load_json_artifact(
        _require_file(semantic_graph_path, artifact_label=f"artifacts/{_SEMANTIC_GRAPH_FILENAME}"),
        SemanticGraph,
        artifact_label=f"artifacts/{_SEMANTIC_GRAPH_FILENAME}",
    )
    candidate_artifact = _load_json_artifact(
        _require_file(candidates_path, artifact_label=f"artifacts/{_CANDIDATES_FILENAME}"),
        CandidateArtifact,
        artifact_label=f"artifacts/{_CANDIDATES_FILENAME}",
    )

    source_artifact_hashes = {
        f"artifacts/{_CANONICAL_DIR_NAME}": _hash_directory(canonical_dir),
        f"artifacts/{_DEPENDENCIES_FILENAME}": _hash_file_bytes(dependencies_path),
        f"artifacts/{_SEMANTIC_GRAPH_FILENAME}": _hash_file_bytes(semantic_graph_path),
        f"artifacts/{_CANDIDATES_FILENAME}": _hash_file_bytes(candidates_path),
    }

    return analyze_semantic_coverage(
        canonical_programs=canonical_programs,
        dependency_artifact=dependency_artifact,
        semantic_graph=semantic_graph,
        candidate_artifact=candidate_artifact,
        run_id=run_id,
        source_package_hash=state.source_package_hash or dependency_artifact.source_package_hash,
        source_artifact_hashes=source_artifact_hashes,
    )


def semantic_coverage_report_path(run_dir: Path) -> Path:
    return run_dir / _DIAGNOSTICS_DIR_NAME / _REPORT_FILENAME


def write_semantic_coverage_report(run_dir: Path, report: SemanticCoverageReport) -> Path:
    """Persiste `report` de forma atomica en `diagnostics/semantic-coverage.
    json`. Nunca escribe un archivo parcial: `atomic_write_json` ya
    garantiza temporal-hermano + flush + fsync + replace; si el proceso
    se interrumpe antes del replace, el temporal se descarta y
    `diagnostics/semantic-coverage.json` no cambia (o no llega a
    existir)."""
    report_path = semantic_coverage_report_path(run_dir)
    atomic_write_json(report_path, report)
    return report_path
