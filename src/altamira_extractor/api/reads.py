"""Helpers de lectura de artefactos para la API (Prompt 13b).

Cada uno valida SOLO lo necesario para leer con seguridad el recurso
pedido -- no reaudita el directorio completo (esa es responsabilidad de
la etapa que escribio el artefacto, ya validada al momento de escribir).
La API expone artefactos ya validados; no reconstruye resultados al
leerlos. Ningun `candidate_id` provisto por el cliente deriva un
filename directamente: siempre se busca primero en el manifest
correspondiente."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..contracts.candidate import CandidateArtifact
from ..contracts.context_manifest import ContextDirectoryManifest
from ..contracts.context_package import ContextPackage
from ..contracts.enums import PipelineStage, StageStatus
from ..contracts.guardrail_candidate import GuardrailCandidateArtifact
from ..contracts.guardrail_manifest import GuardrailDirectoryManifest
from ..contracts.run_state import RunState
from .errors import (
    ArtifactCorruptedError,
    CandidateNotFoundError,
    RunNotFoundError,
    StageNotReachedError,
)

_CANDIDATES_FILENAME = "06-candidates.json"
_CONTEXT_MANIFEST_FILENAME = "context-manifest.json"
_GUARDRAIL_MANIFEST_FILENAME = "guardrail-manifest.json"


def read_run_state(run_dir: Path) -> RunState:
    run_json_path = run_dir / "run.json"
    if run_json_path.is_symlink() or not run_json_path.is_file():
        raise RunNotFoundError()
    try:
        return RunState.model_validate_json(run_json_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ArtifactCorruptedError("run.json invalido") from exc


def require_stage_succeeded(state: RunState, stage: PipelineStage) -> None:
    """No compara `PipelineStage` por orden textual: busca la
    `StageExecution` real de la etapa pedida y exige `SUCCEEDED`."""
    execution = next((s for s in state.stages if s.stage == stage), None)
    if execution is None or execution.status != StageStatus.SUCCEEDED:
        raise StageNotReachedError(f"{stage.value} no fue alcanzado (SUCCEEDED) para este run")


def read_candidate_artifact(run_dir: Path, source_package_hash: str | None) -> CandidateArtifact:
    path = run_dir / "artifacts" / _CANDIDATES_FILENAME
    if path.is_symlink() or not path.is_file():
        raise ArtifactCorruptedError(f"{_CANDIDATES_FILENAME} ausente o no es un archivo regular")
    try:
        artifact = CandidateArtifact.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ArtifactCorruptedError(f"{_CANDIDATES_FILENAME} invalido") from exc

    if source_package_hash is not None and artifact.source_package_hash != source_package_hash:
        raise ArtifactCorruptedError(
            f"source_package_hash de {_CANDIDATES_FILENAME} no coincide con el run"
        )
    return artifact


def read_context_package(run_dir: Path, candidate_id: str) -> ContextPackage:
    context_dir = run_dir / "artifacts" / "07-context"
    manifest_path = context_dir / _CONTEXT_MANIFEST_FILENAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ArtifactCorruptedError(f"{_CONTEXT_MANIFEST_FILENAME} ausente o invalido")
    try:
        manifest = ContextDirectoryManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
    except ValueError as exc:
        raise ArtifactCorruptedError(f"{_CONTEXT_MANIFEST_FILENAME} invalido") from exc

    record = next(
        (r for r in manifest.context_records if r.candidate_id == candidate_id), None
    )
    if record is None:
        raise CandidateNotFoundError()

    if "/" in record.relative_filename or "\\" in record.relative_filename:
        raise ArtifactCorruptedError(f"relative_filename de contexto de {candidate_id!r} invalido")
    package_path = context_dir / record.relative_filename
    if package_path.is_symlink() or not package_path.is_file():
        raise ArtifactCorruptedError(
            f"contexto de {candidate_id!r} ausente o no es un archivo regular"
        )
    try:
        package = ContextPackage.model_validate_json(package_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ArtifactCorruptedError(f"contexto de {candidate_id!r} invalido") from exc

    if package.candidate.candidate_id != candidate_id:
        raise ArtifactCorruptedError(
            f"candidate_id interno del contexto no coincide con {candidate_id!r}"
        )

    actual_hash = hashlib.sha256(package.to_stable_json().encode("utf-8")).hexdigest()
    if actual_hash != record.context_hash:
        raise ArtifactCorruptedError(f"context_hash de {candidate_id!r} no coincide")
    return package


def read_guardrail_candidate_artifact(
    run_dir: Path, candidate_id: str
) -> GuardrailCandidateArtifact:
    guardrail_dir = run_dir / "artifacts" / "09-guardrails"
    manifest_path = guardrail_dir / _GUARDRAIL_MANIFEST_FILENAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ArtifactCorruptedError(f"{_GUARDRAIL_MANIFEST_FILENAME} ausente o invalido")
    try:
        manifest = GuardrailDirectoryManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
    except ValueError as exc:
        raise ArtifactCorruptedError(f"{_GUARDRAIL_MANIFEST_FILENAME} invalido") from exc

    record = next((r for r in manifest.records if r.candidate_id == candidate_id), None)
    if record is None:
        raise CandidateNotFoundError()

    if "/" in record.relative_filename or "\\" in record.relative_filename:
        raise ArtifactCorruptedError(f"relative_filename de guardrail de {candidate_id!r} invalido")
    artifact_path = guardrail_dir / record.relative_filename
    if artifact_path.is_symlink() or not artifact_path.is_file():
        raise ArtifactCorruptedError(
            f"guardrail de {candidate_id!r} ausente o no es un archivo regular"
        )
    try:
        artifact = GuardrailCandidateArtifact.model_validate_json(
            artifact_path.read_text(encoding="utf-8")
        )
    except ValueError as exc:
        raise ArtifactCorruptedError(f"guardrail de {candidate_id!r} invalido") from exc

    if artifact.candidate_id != candidate_id:
        raise ArtifactCorruptedError(
            f"candidate_id interno del artefacto no coincide con {candidate_id!r}"
        )

    actual_hash = hashlib.sha256(artifact.to_stable_json().encode("utf-8")).hexdigest()
    if actual_hash != record.guardrail_artifact_hash:
        raise ArtifactCorruptedError(f"guardrail_artifact_hash de {candidate_id!r} no coincide")
    return artifact
