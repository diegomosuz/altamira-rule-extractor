"""Servicio de filesystem de efectos semanticos normalizados (Fase 2 de
la ampliacion semantica). Unico punto que localiza un run, carga y
valida `artifacts/02-canonical/`, calcula su hash, invoca
`semantic_effects_analyzer.analyze_semantic_effects` (analizador puro)
y persiste el resultado en `<run_dir>/diagnostics/semantic-effects.json`.

NO es un `PipelineStage`: se invoca exclusivamente bajo demanda (CLI
`semantic-effects`), nunca desde `runner.py`/`run_ingestion`. Nunca
modifica ningun artefacto de entrada (`run.json`,
`artifacts/02-canonical/`, ni ningun otro de `artifacts/01-10`); nunca
escribe un reporte parcial (si cualquier paso de carga/validacion
falla, no se crea `diagnostics/`).

Deliberadamente sin ninguna dependencia de `api/`: `pipeline/` es la
capa de la que `api/`/`cli.py` dependen, nunca al reves. La lectura/
validacion minima de `run.json` que este modulo necesita (existencia,
PARSED SUCCEEDED) se implementa localmente, replicando el mismo patron
que `semantic_coverage_service.py` sin importarla ni importar `api/`."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..contracts.canonical import CanonicalProgram
from ..contracts.enums import PipelineStage, StageStatus
from ..contracts.run_state import RunState
from ..contracts.semantic_effects import SemanticEffectsArtifact
from .artifact_store import atomic_write_json
from .errors import SemanticEffectsError
from .semantic_effects_analyzer import analyze_semantic_effects

_CANONICAL_DIR_NAME = "02-canonical"
_DIAGNOSTICS_DIR_NAME = "diagnostics"
_REPORT_FILENAME = "semantic-effects.json"


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
    """Replica `api/reads.py::read_run_state` para el unico caso que este
    servicio necesita, sin importar `api/` (ver docstring del modulo)."""
    run_json_path = run_dir / "run.json"
    if run_json_path.is_symlink() or not run_json_path.is_file():
        raise SemanticEffectsError(f"run {run_dir.name!r} no encontrado: run.json ausente")
    try:
        return RunState.model_validate_json(run_json_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SemanticEffectsError("run.json invalido") from exc


def _require_parsed(state: RunState) -> None:
    execution = next((s for s in state.stages if s.stage == PipelineStage.PARSED), None)
    if execution is None or execution.status != StageStatus.SUCCEEDED:
        raise SemanticEffectsError(
            "el run no alcanzo PARSED (SUCCEEDED); no se pueden calcular efectos "
            "semanticos todavia"
        )


def _require_directory(path: Path, *, artifact_label: str) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise SemanticEffectsError(f"{artifact_label} ausente o no es un directorio regular")
    return path


def _load_canonical_programs(canonical_dir: Path) -> list[CanonicalProgram]:
    _require_directory(canonical_dir, artifact_label=f"artifacts/{_CANONICAL_DIR_NAME}")
    json_paths = sorted(
        path for path in canonical_dir.rglob("*.json") if path.is_file() and not path.is_symlink()
    )
    if not json_paths:
        raise SemanticEffectsError(
            f"artifacts/{_CANONICAL_DIR_NAME} no contiene ningun artefacto CanonicalProgram"
        )
    programs: list[CanonicalProgram] = []
    for json_path in json_paths:
        relative_suffix = json_path.relative_to(canonical_dir).as_posix()
        relative_label = f"artifacts/{_CANONICAL_DIR_NAME}/{relative_suffix}"
        try:
            raw_text = json_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SemanticEffectsError(f"{relative_label}: fallo de lectura") from exc
        try:
            programs.append(CanonicalProgram.model_validate_json(raw_text))
        except ValueError as exc:
            raise SemanticEffectsError(
                f"{relative_label}: JSON invalido o incompatible con su contrato"
            ) from exc
    return programs


def compute_semantic_effects_artifact(run_dir: Path, run_id: str) -> SemanticEffectsArtifact:
    """Localiza `run_dir`, carga y valida `artifacts/02-canonical/`,
    calcula su hash, y devuelve el `SemanticEffectsArtifact` calculado
    (analizador puro: sin Neo4j, sin LLM, sin variables de entorno).
    Nunca escribe nada -- la persistencia es responsabilidad de
    `write_semantic_effects_artifact`/el comando CLI."""
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise SemanticEffectsError(f"run {run_dir.name!r} no encontrado")

    state = _load_run_state(run_dir)
    _require_parsed(state)

    canonical_dir = run_dir / "artifacts" / _CANONICAL_DIR_NAME
    canonical_programs = _load_canonical_programs(canonical_dir)

    source_package_hash = state.source_package_hash or canonical_programs[0].source_package_hash
    source_artifact_hashes = {
        f"artifacts/{_CANONICAL_DIR_NAME}": _hash_directory(canonical_dir),
    }

    return analyze_semantic_effects(
        canonical_programs=canonical_programs,
        run_id=run_id,
        source_package_hash=source_package_hash,
        source_artifact_hashes=source_artifact_hashes,
    )


def semantic_effects_artifact_path(run_dir: Path) -> Path:
    return run_dir / _DIAGNOSTICS_DIR_NAME / _REPORT_FILENAME


def write_semantic_effects_artifact(run_dir: Path, artifact: SemanticEffectsArtifact) -> Path:
    """Persiste `artifact` de forma atomica en `diagnostics/semantic-
    effects.json`. Nunca escribe un archivo parcial: `atomic_write_json`
    ya garantiza temporal-hermano + flush + fsync + replace; si el
    proceso se interrumpe antes del replace, el temporal se descarta y
    `diagnostics/semantic-effects.json` no cambia (o no llega a
    existir)."""
    report_path = semantic_effects_artifact_path(run_dir)
    atomic_write_json(report_path, artifact)
    return report_path
