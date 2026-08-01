"""Servicio de filesystem de detectores V2 en shadow mode (Fase 5 de la
ampliacion semantica, `feat/v2-detectors-shadow-mode`). Unico punto que
localiza un run, carga y valida `artifacts/02-canonical/`,
`artifacts/04-semantic-graph.json` y `artifacts/06-candidates.json`,
calcula sus hashes, calcula `SemanticEffectsArtifact` y
`SemanticPropagationArtifact` EN MEMORIA (mismo patron que
`semantic_propagation_service.py`: nunca lee ni escribe
`diagnostics/semantic-effects.json`/`diagnostics/semantic-propagation.json`/
`diagnostics/semantic-coverage.json`), construye `V2DetectorContext`,
invoca `v2_shadow_detector.run_v2_shadow_detection` (ejecutor puro) y
persiste el resultado en `<run_dir>/diagnostics/v2-candidates-shadow.json`.

NO es un `PipelineStage`: se invoca exclusivamente bajo demanda (CLI
`v2-candidates-shadow`), nunca desde `runner.py`/`run_ingestion`. Nunca
modifica `run.json` ni ningun artefacto de entrada; nunca escribe un
reporte parcial."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..contracts.candidate import CandidateArtifact
from ..contracts.canonical import CanonicalProgram
from ..contracts.enums import PipelineStage, StageStatus
from ..contracts.run_state import RunState
from ..contracts.semantic_graph import SemanticGraph
from ..contracts.v2_shadow_candidates import V2ShadowCandidatesArtifact
from .artifact_store import atomic_write_json
from .errors import V2ShadowCandidatesError
from .semantic_effects_analyzer import analyze_semantic_effects
from .semantic_propagation_analyzer import analyze_semantic_propagation
from .v2_detector_context import build_v2_detector_context
from .v2_shadow_detector import run_v2_shadow_detection

_CANONICAL_DIR_NAME = "02-canonical"
_SEMANTIC_GRAPH_FILENAME = "04-semantic-graph.json"
_CANDIDATES_FILENAME = "06-candidates.json"
_DIAGNOSTICS_DIR_NAME = "diagnostics"
_REPORT_FILENAME = "v2-candidates-shadow.json"

_REQUIRED_STAGES = (
    PipelineStage.PARSED,
    PipelineStage.SEMANTIC_GRAPH_BUILT,
    PipelineStage.CANDIDATES_DETECTED,
)


def _hash_file_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash_directory(dir_path: Path) -> str:
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
    run_json_path = run_dir / "run.json"
    if run_json_path.is_symlink() or not run_json_path.is_file():
        raise V2ShadowCandidatesError(f"run {run_dir.name!r} no encontrado: run.json ausente")
    try:
        return RunState.model_validate_json(run_json_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise V2ShadowCandidatesError("run.json invalido") from exc


def _require_stages_succeeded(state: RunState) -> None:
    for required_stage in _REQUIRED_STAGES:
        execution = next((s for s in state.stages if s.stage == required_stage), None)
        if execution is None or execution.status != StageStatus.SUCCEEDED:
            raise V2ShadowCandidatesError(
                f"el run no alcanzo {required_stage.value} (SUCCEEDED); no se pueden "
                "ejecutar detectores V2 todavia"
            )


def _require_file(path: Path, *, artifact_label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise V2ShadowCandidatesError(f"{artifact_label} ausente o no es un archivo regular")
    return path


def _require_directory(path: Path, *, artifact_label: str) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise V2ShadowCandidatesError(f"{artifact_label} ausente o no es un directorio regular")
    return path


def _load_canonical_programs(canonical_dir: Path) -> list[CanonicalProgram]:
    _require_directory(canonical_dir, artifact_label=f"artifacts/{_CANONICAL_DIR_NAME}")
    json_paths = sorted(
        path for path in canonical_dir.rglob("*.json") if path.is_file() and not path.is_symlink()
    )
    if not json_paths:
        raise V2ShadowCandidatesError(
            f"artifacts/{_CANONICAL_DIR_NAME} no contiene ningun artefacto CanonicalProgram"
        )
    programs: list[CanonicalProgram] = []
    for json_path in json_paths:
        relative_suffix = json_path.relative_to(canonical_dir).as_posix()
        relative_label = f"artifacts/{_CANONICAL_DIR_NAME}/{relative_suffix}"
        try:
            raw_text = json_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise V2ShadowCandidatesError(f"{relative_label}: fallo de lectura") from exc
        try:
            programs.append(CanonicalProgram.model_validate_json(raw_text))
        except ValueError as exc:
            raise V2ShadowCandidatesError(
                f"{relative_label}: JSON invalido o incompatible con su contrato"
            ) from exc
    return programs


def _load_semantic_graph(path: Path) -> SemanticGraph:
    _require_file(path, artifact_label=f"artifacts/{_SEMANTIC_GRAPH_FILENAME}")
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise V2ShadowCandidatesError(
            f"artifacts/{_SEMANTIC_GRAPH_FILENAME}: fallo de lectura"
        ) from exc
    try:
        return SemanticGraph.model_validate_json(raw_text)
    except ValueError as exc:
        raise V2ShadowCandidatesError(
            f"artifacts/{_SEMANTIC_GRAPH_FILENAME}: JSON invalido o incompatible con su contrato"
        ) from exc


def _load_v1_candidates(path: Path) -> CandidateArtifact:
    _require_file(path, artifact_label=f"artifacts/{_CANDIDATES_FILENAME}")
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise V2ShadowCandidatesError(
            f"artifacts/{_CANDIDATES_FILENAME}: fallo de lectura"
        ) from exc
    try:
        return CandidateArtifact.model_validate_json(raw_text)
    except ValueError as exc:
        raise V2ShadowCandidatesError(
            f"artifacts/{_CANDIDATES_FILENAME}: JSON invalido o incompatible con su contrato"
        ) from exc


def compute_v2_shadow_candidates_artifact(
    run_dir: Path, run_id: str
) -> V2ShadowCandidatesArtifact:
    """Localiza `run_dir`, carga y valida `artifacts/02-canonical/`,
    `artifacts/04-semantic-graph.json` y `artifacts/06-candidates.json`,
    calcula `SemanticEffectsArtifact`/`SemanticPropagationArtifact` en
    memoria, ejecuta los detectores V2 registrados, y devuelve el
    `V2ShadowCandidatesArtifact` calculado. Nunca escribe nada -- la
    persistencia es responsabilidad de
    `write_v2_shadow_candidates_artifact`/el comando CLI."""
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise V2ShadowCandidatesError(f"run {run_dir.name!r} no encontrado")

    state = _load_run_state(run_dir)
    _require_stages_succeeded(state)

    canonical_dir = run_dir / "artifacts" / _CANONICAL_DIR_NAME
    canonical_programs = _load_canonical_programs(canonical_dir)

    semantic_graph_path = run_dir / "artifacts" / _SEMANTIC_GRAPH_FILENAME
    semantic_graph = _load_semantic_graph(semantic_graph_path)

    v1_candidates_path = run_dir / "artifacts" / _CANDIDATES_FILENAME
    v1_candidates = _load_v1_candidates(v1_candidates_path)

    source_package_hash = state.source_package_hash or canonical_programs[0].source_package_hash
    source_artifact_hashes = {
        f"artifacts/{_CANONICAL_DIR_NAME}": _hash_directory(canonical_dir),
        f"artifacts/{_SEMANTIC_GRAPH_FILENAME}": _hash_file_bytes(semantic_graph_path),
        f"artifacts/{_CANDIDATES_FILENAME}": _hash_file_bytes(v1_candidates_path),
    }

    semantic_effects = analyze_semantic_effects(
        canonical_programs=canonical_programs,
        run_id=run_id,
        source_package_hash=source_package_hash,
        source_artifact_hashes={f"artifacts/{_CANONICAL_DIR_NAME}": source_artifact_hashes[
            f"artifacts/{_CANONICAL_DIR_NAME}"
        ]},
    )
    semantic_propagation = analyze_semantic_propagation(
        canonical_programs=canonical_programs,
        semantic_effects=semantic_effects,
        run_id=run_id,
        source_package_hash=source_package_hash,
        source_artifact_hashes={f"artifacts/{_CANONICAL_DIR_NAME}": source_artifact_hashes[
            f"artifacts/{_CANONICAL_DIR_NAME}"
        ]},
    )

    try:
        ctx = build_v2_detector_context(
            canonical_programs=canonical_programs,
            semantic_graph=semantic_graph,
            v1_candidates=v1_candidates,
            semantic_effects=semantic_effects,
            semantic_propagation=semantic_propagation,
        )
    except Exception as exc:  # noqa: BLE001 - se reclasifica como error de dominio explicito
        raise V2ShadowCandidatesError(
            "no se pudo construir el contexto de deteccion V2: artefactos inconsistentes "
            "entre si"
        ) from exc

    return run_v2_shadow_detection(
        ctx,
        run_id=run_id,
        source_package_hash=source_package_hash,
        source_artifact_hashes=source_artifact_hashes,
    )


def v2_shadow_candidates_artifact_path(run_dir: Path) -> Path:
    return run_dir / _DIAGNOSTICS_DIR_NAME / _REPORT_FILENAME


def write_v2_shadow_candidates_artifact(
    run_dir: Path, artifact: V2ShadowCandidatesArtifact
) -> Path:
    """Persiste `artifact` de forma atomica en `diagnostics/
    v2-candidates-shadow.json`. Nunca escribe un archivo parcial:
    `atomic_write_json` ya garantiza temporal-hermano + flush + fsync +
    replace."""
    report_path = v2_shadow_candidates_artifact_path(run_dir)
    atomic_write_json(report_path, artifact)
    return report_path
