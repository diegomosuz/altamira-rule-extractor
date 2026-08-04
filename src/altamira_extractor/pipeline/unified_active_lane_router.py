"""Router de lane activo (Fase 14B Parte 10,
`feat/controlled-unified-materialization`).

Resuelve UNICAMENTE por `logical_name` tipado -- nunca acepta una ruta
libre, nunca aplica fuzzy fallback, nunca oculta corrupcion. Este
modulo es de SOLO LECTURA: nunca ejecuta una transicion (el fallback
EJECUTABLE, que si transiciona a V1 ante una generacion unified
invalida, vive en `pipeline/unified_active_lane_service.py`, Parte
11 -- este router solo RESUELVE, deja `status=BLOCKED` cuando la
generacion resuelta no es valida)."""

from __future__ import annotations

import hashlib

from ..contracts.unified_activation_materialization import (
    ActivationResolutionStatus,
    ActiveArtifactResolution,
)
from .errors import UnifiedActivationStoreError, UnifiedMaterializationError
from .unified_activation_store import UnifiedActivationStore

LOGICAL_NAME_CANDIDATES = "candidates"
LOGICAL_NAME_CONTEXT_PACKAGES = "context-packages"
LOGICAL_NAME_RULE_DRAFTS = "rule-drafts"
LOGICAL_NAME_GUARDRAILS = "guardrails"

KNOWN_LOGICAL_NAMES = frozenset(
    {
        LOGICAL_NAME_CANDIDATES,
        LOGICAL_NAME_CONTEXT_PACKAGES,
        LOGICAL_NAME_RULE_DRAFTS,
        LOGICAL_NAME_GUARDRAILS,
    }
)


def resolve_active_artifact(
    store: UnifiedActivationStore, *, run_id: str, logical_name: str
) -> ActiveArtifactResolution:
    """Punto de entrada puro respecto de politica (solo lee, nunca
    transiciona). Lanza `UnifiedMaterializationError` UNICAMENTE para
    `logical_name` desconocido o ausencia total de lane activo --
    cualquier otro problema (manifest corrupto, hash no reconciliado,
    archivo ausente) se tipa como `status=BLOCKED`, nunca como
    excepcion."""
    if logical_name not in KNOWN_LOGICAL_NAMES:
        raise UnifiedMaterializationError(
            f"logical_name desconocido: {logical_name!r} (validos: "
            f"{', '.join(sorted(KNOWN_LOGICAL_NAMES))})"
        )

    pointer = store.read_active_pointer()
    if pointer is None:
        raise UnifiedMaterializationError(
            "el run no tiene ningun lane activo todavia -- ejecutar "
            "unified-activation-materialize primero"
        )

    try:
        manifest = store.read_generation_manifest(pointer.active_generation_id)
    except UnifiedActivationStoreError:
        return ActiveArtifactResolution(
            run_id=run_id,
            status=ActivationResolutionStatus.BLOCKED,
            requested_logical_name=logical_name,
            resolved_lane=pointer.active_lane,
            generation_id=pointer.active_generation_id,
            fallback_applied=False,
        )

    manifest_hash = hashlib.sha256(manifest.to_stable_json().encode("utf-8")).hexdigest()
    if manifest_hash != pointer.active_generation_manifest_hash:
        return ActiveArtifactResolution(
            run_id=run_id,
            status=ActivationResolutionStatus.BLOCKED,
            requested_logical_name=logical_name,
            resolved_lane=pointer.active_lane,
            generation_id=pointer.active_generation_id,
            fallback_applied=False,
        )

    file_reference = next((f for f in manifest.files if f.logical_name == logical_name), None)
    if file_reference is None:
        return ActiveArtifactResolution(
            run_id=run_id,
            status=ActivationResolutionStatus.NOT_AVAILABLE_IN_LANE,
            requested_logical_name=logical_name,
            resolved_lane=pointer.active_lane,
            generation_id=pointer.active_generation_id,
            fallback_applied=False,
        )

    try:
        store.validate_file_reference(file_reference)
    except UnifiedActivationStoreError:
        return ActiveArtifactResolution(
            run_id=run_id,
            status=ActivationResolutionStatus.BLOCKED,
            requested_logical_name=logical_name,
            resolved_lane=pointer.active_lane,
            generation_id=pointer.active_generation_id,
            relative_path=file_reference.relative_path,
            sha256=file_reference.sha256,
            fallback_applied=False,
        )

    return ActiveArtifactResolution(
        run_id=run_id,
        status=ActivationResolutionStatus.RESOLVED,
        requested_logical_name=logical_name,
        resolved_lane=pointer.active_lane,
        generation_id=pointer.active_generation_id,
        relative_path=file_reference.relative_path,
        sha256=file_reference.sha256,
        fallback_applied=False,
    )


__all__ = [
    "KNOWN_LOGICAL_NAMES",
    "LOGICAL_NAME_CANDIDATES",
    "LOGICAL_NAME_CONTEXT_PACKAGES",
    "LOGICAL_NAME_GUARDRAILS",
    "LOGICAL_NAME_RULE_DRAFTS",
    "resolve_active_artifact",
]
