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
from ..contracts.run_state_recovery import (
    EMERGENCY_RECORD_PREFIX,
    RunStatePersistenceFailureRecord,
)
from .errors import (
    ArtifactCorruptedError,
    CandidateNotFoundError,
    RunNotFoundError,
    StageNotReachedError,
)

_CANDIDATES_FILENAME = "06-candidates.json"
_CONTEXT_MANIFEST_FILENAME = "context-manifest.json"
_GUARDRAIL_MANIFEST_FILENAME = "guardrail-manifest.json"

_TERMINAL_STAGES = frozenset({PipelineStage.FAILED, PipelineStage.COMPLETED})


def _latest_emergency_record(run_dir: Path) -> RunStatePersistenceFailureRecord | None:
    """Best effort: un artefacto de emergencia ausente, corrupto, o un
    `run_dir` no listable nunca impide leer `run.json` normalmente --
    solo se usa como fallback cuando `run.json` esta ausente/invalido o
    no-terminal (ver `read_run_state`)."""
    try:
        candidate_paths = list(run_dir.glob(f"{EMERGENCY_RECORD_PREFIX}*.json"))
    except OSError:
        return None

    latest: RunStatePersistenceFailureRecord | None = None
    for path in candidate_paths:
        if path.is_symlink() or not path.is_file():
            continue
        try:
            record = RunStatePersistenceFailureRecord.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            continue
        if latest is None or record.timestamp_utc > latest.timestamp_utc:
            latest = record
    return latest


def read_run_state(run_dir: Path) -> RunState:
    """Lee `run.json`; si esta ausente, invalido, o su `current_stage` no
    es terminal, consulta el artefacto de emergencia mas reciente (ver
    `contracts/run_state_recovery.py`) y lo prefiere UNICAMENTE si es mas
    reciente que `run.json.updated_at` -- un `run.json` terminal siempre
    prevalece sobre cualquier artefacto de emergencia, por antiguo que
    sea. Runs historicos sin ningun artefacto de emergencia (el caso
    universal hasta ahora) se comportan exactamente igual que antes."""
    run_json_path = run_dir / "run.json"
    state: RunState | None = None
    parse_error: ValueError | None = None

    if not run_json_path.is_symlink() and run_json_path.is_file():
        try:
            state = RunState.model_validate_json(run_json_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            parse_error = exc

    if state is not None and state.current_stage in _TERMINAL_STAGES:
        return state

    emergency = _latest_emergency_record(run_dir)
    if emergency is not None and (state is None or emergency.timestamp_utc > state.updated_at):
        # Estrictamente `>`, deliberado: en un empate exacto (verificado
        # empiricamente que `datetime.now(UTC)` puede resolver al mismo
        # valor en llamadas consecutivas muy cercanas en Windows, reloj
        # de sistema de ~15ms) se prefiere el `run.json` YA EN DISCO, la
        # opcion conservadora -- nunca se presenta un run como FAILED sin
        # evidencia CLARAMENTE posterior de que la emergencia lo
        # supera. En produccion real esto no es ambiguo: el deadline
        # critico que antecede a un artefacto de emergencia
        # (`runner._RUN_STATE_CRITICAL_DEADLINE_SECONDS`, hasta 3s de
        # reintentos) siempre separa ambos timestamps por mucho mas que
        # la resolucion del reloj.
        return emergency.attempted_state

    if state is not None:
        return state
    if run_json_path.is_symlink() or not run_json_path.is_file():
        raise RunNotFoundError()
    raise ArtifactCorruptedError("run.json invalido") from parse_error


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
