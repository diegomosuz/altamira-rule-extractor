"""Fallback EJECUTABLE de Fase 14B (Parte 11,
`feat/controlled-unified-materialization`).

Envuelve `pipeline/unified_active_lane_router.py` (solo lectura) con
la UNICA politica que puede transicionar el puntero activo desde una
resolucion: cuando la generacion UNIFIED activa esta corrupta o
incompleta (`status=BLOCKED`, `resolved_lane=UNIFIED`) Y el puntero
autoriza fallback (`fallback_policy=FALLBACK_TO_V1`), ejecuta
`FALLBACK_TO_V1` (Parte 9) y vuelve a resolver desde V1.

Nunca aplica fallback por: `logical_name` desconocido (la excepcion de
`resolve_active_artifact` ya propaga antes de llegar aqui), un
`relative_path`/config invalidos, o cualquier error de uso -- UNICAMENTE
por integridad/disponibilidad real del lane unified activo. Un V1
corrupto NUNCA dispara un segundo fallback (no hay a donde caer mas
alla de V1): se bloquea fail-closed (Parte 5, invariante `V1 permanece
resoluble siempre` se interpreta como "si V1 esta corrupto, eso ya es
un fallo de integridad grave que debe bloquear, nunca ocultarse")."""

from __future__ import annotations

from ..contracts.unified_activation_config import UnifiedFallbackPolicy
from ..contracts.unified_activation_materialization import (
    ActivationResolutionStatus,
    ActiveArtifactResolution,
    MaterializedActivationLane,
)
from ..contracts.unified_materialization_authorization import UnifiedMaterializationReasonCode
from .errors import UnifiedMaterializationError
from .unified_activation_store import UnifiedActivationStore
from .unified_activation_transition import fallback_to_v1
from .unified_active_lane_router import resolve_active_artifact


def resolve_with_fallback(
    store: UnifiedActivationStore, *, run_id: str, logical_name: str
) -> ActiveArtifactResolution:
    """Punto de entrada publico del CLI `unified-activation-resolve`
    (Parte 13, que NUNCA acepta una autorizacion nueva) y de cualquier
    consumidor opt-in (Parte 14). Idempotente: una vez que el fallback
    ya se aplico (lane activo ya es V1), una nueva llamada simplemente
    resuelve desde V1 directamente -- nunca reintenta la transicion.

    Un fallback disparado aqui es una accion de SISTEMA, no una nueva
    decision humana: `activation_evaluation_hash`/`authorization_hash`
    del evento resultante se heredan del manifiesto de la generacion
    unified que se abandona (la MISMA autorizacion que la activo es la
    que queda registrada como causa del fallback), nunca se inventan."""
    resolution = resolve_active_artifact(store, run_id=run_id, logical_name=logical_name)

    if resolution.status != ActivationResolutionStatus.BLOCKED:
        return resolution
    if resolution.resolved_lane != MaterializedActivationLane.UNIFIED:
        # V1 corrupto: fail-closed, sin segundo nivel de fallback.
        return resolution

    pointer = store.read_active_pointer()
    if pointer is None:
        raise UnifiedMaterializationError(
            "el run no tiene ningun lane activo -- estado inesperado tras una resolucion BLOCKED"
        )
    if pointer.fallback_policy != UnifiedFallbackPolicy.FALLBACK_TO_V1:
        # Fallback no autorizado por el puntero actual: BLOCKED se
        # reporta tal cual, active.json NUNCA se modifica.
        return resolution

    corrupt_manifest = store.read_generation_manifest(pointer.active_generation_id)
    v1_manifest = store.read_generation_manifest(pointer.fallback_generation_id)
    store.validate_generation_files(v1_manifest)

    transition_result = fallback_to_v1(
        store,
        run_id=run_id,
        v1_manifest=v1_manifest,
        activation_evaluation_hash=corrupt_manifest.activation_evaluation_hash,
        authorization_hash=corrupt_manifest.authorization_hash,
        reason_code=UnifiedMaterializationReasonCode.ACTIVE_GENERATION_INVALID,
    )

    fallback_resolution = resolve_active_artifact(store, run_id=run_id, logical_name=logical_name)
    resulting_status = (
        ActivationResolutionStatus.FALLBACK_APPLIED
        if fallback_resolution.status == ActivationResolutionStatus.RESOLVED
        else fallback_resolution.status
    )
    return ActiveArtifactResolution(
        run_id=run_id,
        status=resulting_status,
        requested_logical_name=logical_name,
        resolved_lane=fallback_resolution.resolved_lane,
        generation_id=fallback_resolution.generation_id,
        relative_path=fallback_resolution.relative_path,
        sha256=fallback_resolution.sha256,
        fallback_applied=True,
        fallback_event_id=transition_result.event.event_id,
    )


__all__ = ["resolve_with_fallback"]
