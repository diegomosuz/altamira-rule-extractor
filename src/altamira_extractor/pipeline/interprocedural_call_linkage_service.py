"""Servicio de filesystem de la fundacion interprocedural CALL/LINKAGE
(Fase 6 de la ampliacion semantica,
`feat/interprocedural-call-linkage-foundation`). Unico punto que localiza
un run, carga y valida `artifacts/02-canonical/`, calcula
`SemanticEffectsArtifact` EN MEMORIA (mismo patron que
`semantic_propagation_service.py`/`v2_shadow_candidates_service.py`:
nunca lee ni escribe `diagnostics/semantic-effects.json`), invoca
`interprocedural_call_linkage_analyzer.analyze_interprocedural_call_linkage`
(analizador puro) y persiste el resultado en `<run_dir>/diagnostics/
interprocedural-call-linkage.json`.

NO es un `PipelineStage`: se invoca exclusivamente bajo demanda (CLI
`semantic-interprocedural`), nunca desde `runner.py`/`run_ingestion`.
Nunca modifica `run.json` ni ningun artefacto de entrada; nunca escribe
un reporte parcial; nunca usa Neo4j ni un proveedor LLM."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..contracts.canonical import CanonicalProgram
from ..contracts.enums import PipelineStage, StageStatus
from ..contracts.interprocedural_call_linkage import InterproceduralCallLinkageArtifact
from ..contracts.run_state import RunState
from .artifact_store import atomic_write_json
from .errors import InterproceduralCallLinkageError
from .interprocedural_call_linkage_analyzer import analyze_interprocedural_call_linkage
from .semantic_effects_analyzer import analyze_semantic_effects

_CANONICAL_DIR_NAME = "02-canonical"
_DIAGNOSTICS_DIR_NAME = "diagnostics"
_REPORT_FILENAME = "interprocedural-call-linkage.json"

_REQUIRED_STAGES = (PipelineStage.PARSED,)


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
        raise InterproceduralCallLinkageError(
            f"run {run_dir.name!r} no encontrado: run.json ausente"
        )
    try:
        return RunState.model_validate_json(run_json_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise InterproceduralCallLinkageError("run.json invalido") from exc


def _require_stages_succeeded(state: RunState) -> None:
    for required_stage in _REQUIRED_STAGES:
        execution = next((s for s in state.stages if s.stage == required_stage), None)
        if execution is None or execution.status != StageStatus.SUCCEEDED:
            raise InterproceduralCallLinkageError(
                f"el run no alcanzo {required_stage.value} (SUCCEEDED); no se puede ejecutar "
                "el analisis interprocedural todavia"
            )


def _require_directory(path: Path, *, artifact_label: str) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise InterproceduralCallLinkageError(
            f"{artifact_label} ausente o no es un directorio regular"
        )
    return path


def _load_canonical_programs(canonical_dir: Path) -> list[CanonicalProgram]:
    _require_directory(canonical_dir, artifact_label=f"artifacts/{_CANONICAL_DIR_NAME}")
    json_paths = sorted(
        path for path in canonical_dir.rglob("*.json") if path.is_file() and not path.is_symlink()
    )
    if not json_paths:
        raise InterproceduralCallLinkageError(
            f"artifacts/{_CANONICAL_DIR_NAME} no contiene ningun artefacto CanonicalProgram"
        )
    programs: list[CanonicalProgram] = []
    for json_path in json_paths:
        relative_suffix = json_path.relative_to(canonical_dir).as_posix()
        relative_label = f"artifacts/{_CANONICAL_DIR_NAME}/{relative_suffix}"
        try:
            raw_text = json_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise InterproceduralCallLinkageError(f"{relative_label}: fallo de lectura") from exc
        try:
            programs.append(CanonicalProgram.model_validate_json(raw_text))
        except ValueError as exc:
            raise InterproceduralCallLinkageError(
                f"{relative_label}: JSON invalido o incompatible con su contrato"
            ) from exc
    return programs


def compute_interprocedural_call_linkage_artifact(
    run_dir: Path, run_id: str
) -> InterproceduralCallLinkageArtifact:
    """Localiza `run_dir`, carga y valida `artifacts/02-canonical/`,
    calcula `SemanticEffectsArtifact` en memoria, ejecuta el analizador
    interprocedural puro, y devuelve el `InterproceduralCallLinkageArtifact`
    calculado. Nunca escribe nada -- la persistencia es responsabilidad
    de `write_interprocedural_call_linkage_artifact`/el comando CLI."""
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise InterproceduralCallLinkageError(f"run {run_dir.name!r} no encontrado")

    state = _load_run_state(run_dir)
    _require_stages_succeeded(state)

    canonical_dir = run_dir / "artifacts" / _CANONICAL_DIR_NAME
    canonical_programs = _load_canonical_programs(canonical_dir)

    source_package_hash = state.source_package_hash or canonical_programs[0].source_package_hash
    canonical_hash = _hash_directory(canonical_dir)
    source_artifact_hashes = {f"artifacts/{_CANONICAL_DIR_NAME}": canonical_hash}

    semantic_effects = analyze_semantic_effects(
        canonical_programs=canonical_programs,
        run_id=run_id,
        source_package_hash=source_package_hash,
        source_artifact_hashes=source_artifact_hashes,
    )

    try:
        return analyze_interprocedural_call_linkage(
            canonical_programs=canonical_programs,
            semantic_effects=semantic_effects,
            run_id=run_id,
            source_package_hash=source_package_hash,
            source_artifact_hashes=source_artifact_hashes,
        )
    except Exception as exc:  # noqa: BLE001 - se reclasifica como error de dominio explicito
        raise InterproceduralCallLinkageError(
            "no se pudo construir el analisis interprocedural: artefactos inconsistentes entre si"
        ) from exc


def interprocedural_call_linkage_artifact_path(run_dir: Path) -> Path:
    return run_dir / _DIAGNOSTICS_DIR_NAME / _REPORT_FILENAME


def write_interprocedural_call_linkage_artifact(
    run_dir: Path, artifact: InterproceduralCallLinkageArtifact
) -> Path:
    """Persiste `artifact` de forma atomica en `diagnostics/
    interprocedural-call-linkage.json`. Nunca escribe un archivo
    parcial: `atomic_write_json` ya garantiza temporal-hermano + flush +
    fsync + replace."""
    report_path = interprocedural_call_linkage_artifact_path(run_dir)
    atomic_write_json(report_path, artifact)
    return report_path
