"""Servicio de filesystem de la propagacion interprocedural conservadora
en shadow mode (Fase 7 de la ampliacion semantica,
`feat/interprocedural-propagation-shadow`). Unico punto que localiza un
run, carga y valida `artifacts/02-canonical/`, calcula
`SemanticEffectsArtifact`, `SemanticPropagationArtifact` e
`InterproceduralCallLinkageArtifact` EN MEMORIA (mismo patron que
`interprocedural_call_linkage_service.py`: nunca lee ni escribe
`diagnostics/semantic-effects.json`/`diagnostics/semantic-propagation.json`/
`diagnostics/interprocedural-call-linkage.json`, ni siquiera cuando ya
existen en disco -- los recalcula limpio cada vez con los mismos
analizadores puros, nunca modifica esos diagnosticos preexistentes),
invoca `interprocedural_propagation_analyzer.analyze_interprocedural_
propagation` (analizador puro) y persiste el resultado UNICAMENTE en
`<run_dir>/diagnostics/interprocedural-propagation.json`.

NO es un `PipelineStage`: se invoca exclusivamente bajo demanda (CLI
`semantic-interprocedural-propagation`), nunca desde `runner.py`/
`run_ingestion`. Nunca modifica `run.json` ni ningun artefacto de
entrada; nunca escribe un reporte parcial; nunca usa Neo4j ni un
proveedor LLM."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..contracts.canonical import CanonicalProgram
from ..contracts.enums import PipelineStage, StageStatus
from ..contracts.interprocedural_propagation import InterproceduralPropagationArtifact
from ..contracts.run_state import RunState
from .artifact_store import atomic_write_json
from .errors import InterproceduralPropagationError
from .interprocedural_call_linkage_analyzer import analyze_interprocedural_call_linkage
from .interprocedural_propagation_analyzer import analyze_interprocedural_propagation
from .semantic_effects_analyzer import analyze_semantic_effects
from .semantic_propagation_analyzer import analyze_semantic_propagation

_CANONICAL_DIR_NAME = "02-canonical"
_DIAGNOSTICS_DIR_NAME = "diagnostics"
_REPORT_FILENAME = "interprocedural-propagation.json"

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
        raise InterproceduralPropagationError(
            f"run {run_dir.name!r} no encontrado: run.json ausente"
        )
    try:
        return RunState.model_validate_json(run_json_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise InterproceduralPropagationError("run.json invalido") from exc


def _require_stages_succeeded(state: RunState) -> None:
    for required_stage in _REQUIRED_STAGES:
        execution = next((s for s in state.stages if s.stage == required_stage), None)
        if execution is None or execution.status != StageStatus.SUCCEEDED:
            raise InterproceduralPropagationError(
                f"el run no alcanzo {required_stage.value} (SUCCEEDED); no se puede ejecutar "
                "la propagacion interprocedural todavia"
            )


def _require_directory(path: Path, *, artifact_label: str) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise InterproceduralPropagationError(
            f"{artifact_label} ausente o no es un directorio regular"
        )
    return path


def _load_canonical_programs(canonical_dir: Path) -> list[CanonicalProgram]:
    _require_directory(canonical_dir, artifact_label=f"artifacts/{_CANONICAL_DIR_NAME}")
    json_paths = sorted(
        path for path in canonical_dir.rglob("*.json") if path.is_file() and not path.is_symlink()
    )
    if not json_paths:
        raise InterproceduralPropagationError(
            f"artifacts/{_CANONICAL_DIR_NAME} no contiene ningun artefacto CanonicalProgram"
        )
    programs: list[CanonicalProgram] = []
    for json_path in json_paths:
        relative_suffix = json_path.relative_to(canonical_dir).as_posix()
        relative_label = f"artifacts/{_CANONICAL_DIR_NAME}/{relative_suffix}"
        try:
            raw_text = json_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise InterproceduralPropagationError(f"{relative_label}: fallo de lectura") from exc
        try:
            programs.append(CanonicalProgram.model_validate_json(raw_text))
        except ValueError as exc:
            raise InterproceduralPropagationError(
                f"{relative_label}: JSON invalido o incompatible con su contrato"
            ) from exc
    return programs


def compute_interprocedural_propagation_artifact(
    run_dir: Path, run_id: str
) -> InterproceduralPropagationArtifact:
    """Localiza `run_dir`, carga y valida `artifacts/02-canonical/`,
    calcula `SemanticEffectsArtifact`/`SemanticPropagationArtifact`/
    `InterproceduralCallLinkageArtifact` en memoria (en ese orden, cada
    uno alimentando al siguiente), ejecuta el analizador de propagacion
    interprocedural puro, y devuelve el
    `InterproceduralPropagationArtifact` calculado. Nunca escribe nada
    -- la persistencia es responsabilidad de
    `write_interprocedural_propagation_artifact`/el comando CLI."""
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise InterproceduralPropagationError(f"run {run_dir.name!r} no encontrado")

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

    semantic_propagation = analyze_semantic_propagation(
        canonical_programs=canonical_programs,
        semantic_effects=semantic_effects,
        run_id=run_id,
        source_package_hash=source_package_hash,
        source_artifact_hashes=source_artifact_hashes,
    )

    try:
        interprocedural_call_linkage = analyze_interprocedural_call_linkage(
            canonical_programs=canonical_programs,
            semantic_effects=semantic_effects,
            run_id=run_id,
            source_package_hash=source_package_hash,
            source_artifact_hashes=source_artifact_hashes,
        )
        return analyze_interprocedural_propagation(
            canonical_programs=canonical_programs,
            semantic_effects=semantic_effects,
            semantic_propagation=semantic_propagation,
            interprocedural_call_linkage=interprocedural_call_linkage,
            run_id=run_id,
            source_package_hash=source_package_hash,
            source_artifact_hashes=source_artifact_hashes,
        )
    except Exception as exc:  # noqa: BLE001 - se reclasifica como error de dominio explicito
        raise InterproceduralPropagationError(
            "no se pudo construir la propagacion interprocedural: artefactos inconsistentes "
            "entre si"
        ) from exc


def interprocedural_propagation_artifact_path(run_dir: Path) -> Path:
    return run_dir / _DIAGNOSTICS_DIR_NAME / _REPORT_FILENAME


def write_interprocedural_propagation_artifact(
    run_dir: Path, artifact: InterproceduralPropagationArtifact
) -> Path:
    """Persiste `artifact` de forma atomica en `diagnostics/
    interprocedural-propagation.json`. Nunca escribe un archivo parcial:
    `atomic_write_json` ya garantiza temporal-hermano + flush + fsync +
    replace."""
    report_path = interprocedural_propagation_artifact_path(run_dir)
    atomic_write_json(report_path, artifact)
    return report_path
