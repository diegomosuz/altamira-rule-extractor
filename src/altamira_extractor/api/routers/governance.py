"""Endpoints `/api/runs/{run_id}/governance*` (Fase 15A Parte 7,
`feat/operational-governance-ui`).

Capa de gobierno operativo EXCLUSIVAMENTE de lectura: cada handler es
`GET` o `HEAD`, nunca `POST`/`PUT`/`PATCH`/`DELETE`. Ninguno ejecuta
materializacion, fallback ni rollback (Fase 14B) -- ninguno modifica
`active.json`, ninguna generacion, ningun evento. Construye
`OperationalGovernanceOverview` en memoria en cada peticion via
`pipeline/operational_governance_reader.py`, nunca lo persiste.

La descarga de artifacts (`GET`/`HEAD .../artifacts/{logical_name}`)
resuelve EXCLUSIVAMENTE via el router read-only de Fase 14B
(`unified_active_lane_router.resolve_active_artifact`) -- NUNCA
`resolve_with_fallback`: una corrupcion del lane activo se reporta
(409), nunca se repara automaticamente por un GET."""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from pathlib import Path

from fastapi import APIRouter, Depends, Response

from ...config import Settings
from ...contracts.operational_governance import (
    GovernanceEventSummary,
    GovernanceGenerationSummary,
    GovernanceUnifiedGroupSummary,
    OperationalGovernanceOverview,
)
from ...contracts.unified_activation_materialization import ActivationResolutionStatus
from ...pipeline.errors import UnifiedActivationStoreError, UnifiedMaterializationError
from ...pipeline.operational_governance_group_adapter import (
    OperationalGovernanceGroupAdapterError,
    build_unified_group_summaries,
)
from ...pipeline.operational_governance_reader import (
    OperationalGovernanceReadError,
    build_operational_governance_overview,
)
from ...pipeline.unified_activation_store import UnifiedActivationStore
from ...pipeline.unified_active_lane_router import KNOWN_LOGICAL_NAMES, resolve_active_artifact
from ..deps import get_settings
from ..errors import ApiError
from ..validation import validate_run_id

router = APIRouter(prefix="/api/runs", tags=["governance"])

_GENERATION_ID_RE = re.compile(r"^generation-[0-9a-f]{64}$")


class GovernanceLogicalName(StrEnum):
    """Catalogo CERRADO de nombres logicos descargables -- FastAPI
    rechaza cualquier otro valor con 422 ANTES de que el handler se
    ejecute (Fase 15A Parte 7: 'no aceptar paths')."""

    CANDIDATES = "candidates"
    CONTEXT_PACKAGES = "context-packages"
    RULE_DRAFTS = "rule-drafts"
    GUARDRAILS = "guardrails"


assert {member.value for member in GovernanceLogicalName} == KNOWN_LOGICAL_NAMES


class GovernanceRunNotFoundError(ApiError):
    def __init__(self) -> None:
        super().__init__("run no encontrado", status_code=404, code="run_not_found")


class GovernanceNotInitializedError(ApiError):
    def __init__(self) -> None:
        super().__init__(
            "la activacion no esta inicializada para este run",
            status_code=404,
            code="governance_not_initialized",
        )


class GovernanceGenerationNotFoundError(ApiError):
    def __init__(self) -> None:
        super().__init__(
            "generacion no encontrada", status_code=404, code="governance_generation_not_found"
        )


class GovernanceInvalidGenerationIdError(ApiError):
    def __init__(self) -> None:
        super().__init__(
            "generation_id con formato invalido", status_code=422, code="invalid_identifier"
        )


class GovernanceArtifactNotAvailableError(ApiError):
    def __init__(self) -> None:
        super().__init__(
            "el artefacto solicitado no esta disponible en el lane activo (ausencia legitima)",
            status_code=404,
            code="governance_artifact_not_available",
        )


class GovernanceArtifactBlockedError(ApiError):
    def __init__(self) -> None:
        super().__init__(
            "el artefacto del lane activo esta corrupto o no reconcilia con su manifiesto -- "
            "nunca se repara automaticamente desde una lectura",
            status_code=409,
            code="governance_artifact_blocked",
        )


def _run_dir(settings: Settings, run_id: str) -> Path:
    validate_run_id(run_id)
    run_dir = settings.runs_dir / run_id
    if run_dir.parent != settings.runs_dir:
        raise GovernanceInvalidGenerationIdError()
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise GovernanceRunNotFoundError()
    return run_dir


def _validate_generation_id(generation_id: str) -> str:
    if not _GENERATION_ID_RE.match(generation_id):
        raise GovernanceInvalidGenerationIdError()
    return generation_id


def _build_overview(run_dir: Path, run_id: str) -> OperationalGovernanceOverview:
    try:
        return build_operational_governance_overview(run_dir, run_id)
    except OperationalGovernanceReadError as exc:
        raise GovernanceRunNotFoundError() from exc


@router.get(
    "/{run_id}/governance",
    response_model=OperationalGovernanceOverview,
    summary="Overview de gobierno operativo (lane activo, integridad, issues) -- solo lectura",
)
def get_governance_overview(
    run_id: str, settings: Settings = Depends(get_settings)
) -> OperationalGovernanceOverview:
    run_dir = _run_dir(settings, run_id)
    return _build_overview(run_dir, run_id)


@router.get(
    "/{run_id}/governance/generations",
    response_model=list[GovernanceGenerationSummary],
    summary="Generaciones persistidas (V1/unified) con reachability e integridad -- solo lectura",
)
def get_governance_generations(
    run_id: str, settings: Settings = Depends(get_settings)
) -> list[GovernanceGenerationSummary]:
    run_dir = _run_dir(settings, run_id)
    return _build_overview(run_dir, run_id).generations


@router.get(
    "/{run_id}/governance/generations/{generation_id}",
    response_model=GovernanceGenerationSummary,
    summary="Detalle de UNA generacion persistida -- solo lectura",
)
def get_governance_generation_detail(
    run_id: str, generation_id: str, settings: Settings = Depends(get_settings)
) -> GovernanceGenerationSummary:
    _validate_generation_id(generation_id)
    run_dir = _run_dir(settings, run_id)
    overview = _build_overview(run_dir, run_id)
    summary = next((g for g in overview.generations if g.generation_id == generation_id), None)
    if summary is None:
        raise GovernanceGenerationNotFoundError()
    return summary


@router.get(
    "/{run_id}/governance/events",
    response_model=list[GovernanceEventSummary],
    summary="Cadena de eventos (confirmados + huerfanos) -- solo lectura",
)
def get_governance_events(
    run_id: str, settings: Settings = Depends(get_settings)
) -> list[GovernanceEventSummary]:
    run_dir = _run_dir(settings, run_id)
    return _build_overview(run_dir, run_id).events


@router.get(
    "/{run_id}/governance/groups",
    response_model=list[GovernanceUnifiedGroupSummary],
    summary="Grupos unified de la generacion activa/seleccionada -- solo lectura",
)
def get_governance_groups(
    run_id: str,
    generation_id: str | None = None,
    settings: Settings = Depends(get_settings),
) -> list[GovernanceUnifiedGroupSummary]:
    run_dir = _run_dir(settings, run_id)
    if generation_id is None:
        return _build_overview(run_dir, run_id).unified_groups

    _validate_generation_id(generation_id)
    store = UnifiedActivationStore(run_dir)
    if not store.generation_exists(generation_id):
        raise GovernanceGenerationNotFoundError()
    try:
        manifest = store.read_generation_manifest(generation_id)
    except UnifiedActivationStoreError as exc:
        raise GovernanceGenerationNotFoundError() from exc
    try:
        return build_unified_group_summaries(store, manifest)
    except OperationalGovernanceGroupAdapterError as exc:
        raise GovernanceArtifactBlockedError() from exc


def _resolve_download(run_dir: Path, run_id: str, logical_name: str) -> tuple[bytes, str, str]:
    """Retorna `(bytes, sha256, relative_path)` o lanza un `ApiError`
    tipado (404 ausencia legitima / run sin activacion, 409
    corrupcion). Solo lectura: `resolve_active_artifact` nunca
    transiciona ni ejecuta fallback."""
    store = UnifiedActivationStore(run_dir)
    try:
        resolution = resolve_active_artifact(store, run_id=run_id, logical_name=logical_name)
    except UnifiedMaterializationError as exc:
        raise GovernanceNotInitializedError() from exc

    if resolution.status == ActivationResolutionStatus.NOT_AVAILABLE_IN_LANE:
        raise GovernanceArtifactNotAvailableError()
    if resolution.status != ActivationResolutionStatus.RESOLVED:
        raise GovernanceArtifactBlockedError()

    assert resolution.relative_path is not None
    assert resolution.sha256 is not None
    candidate_path = run_dir / resolution.relative_path
    if candidate_path.is_symlink() or not candidate_path.is_file():
        raise GovernanceArtifactBlockedError()
    data = candidate_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != resolution.sha256:
        raise GovernanceArtifactBlockedError()
    return data, resolution.sha256, resolution.relative_path


def _download_headers(
    run_id: str, logical_name: str, sha256: str, byte_size: int
) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Content-Disposition": f'attachment; filename="{run_id}-{logical_name}.json"',
        "ETag": f'"{sha256}"',
        "Cache-Control": "no-store",
        "Content-Length": str(byte_size),
    }


@router.get(
    "/{run_id}/governance/artifacts/{logical_name}",
    summary="Descarga el artefacto resuelto del lane activo -- solo lectura, sin fallback",
)
def download_governance_artifact(
    run_id: str, logical_name: GovernanceLogicalName, settings: Settings = Depends(get_settings)
) -> Response:
    run_dir = _run_dir(settings, run_id)
    data, sha256, _relative_path = _resolve_download(run_dir, run_id, logical_name.value)
    headers = _download_headers(run_id, logical_name.value, sha256, len(data))
    return Response(content=data, media_type="application/json", headers=headers)


@router.head(
    "/{run_id}/governance/artifacts/{logical_name}",
    summary="Cabeceras del artefacto resuelto del lane activo, sin cuerpo -- solo lectura",
)
def head_governance_artifact(
    run_id: str, logical_name: GovernanceLogicalName, settings: Settings = Depends(get_settings)
) -> Response:
    run_dir = _run_dir(settings, run_id)
    data, sha256, _relative_path = _resolve_download(run_dir, run_id, logical_name.value)
    headers = _download_headers(run_id, logical_name.value, sha256, len(data))
    return Response(content=b"", media_type="application/json", headers=headers)


__all__ = ["GovernanceLogicalName", "router"]
