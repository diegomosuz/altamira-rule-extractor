"""Transiciones atomicas de Fase 14B (`feat/controlled-unified-materialization`).

Cada funcion publica (`initialize_v1`, `activate_unified_canary`,
`activate_unified_primary`, `fallback_to_v1`, `rollback_to_previous`,
`rollback_to_generation`, `keep_current`) ejecuta la MISMA secuencia
(Parte 9): adquirir lock -> cargar puntero actual -> validar
`expected_active_pointer_hash` -> validar/persistir la generacion
destino -> construir el evento inmutable -> persistir el evento ->
verificarlo -> construir el nuevo `active.json` -> escribirlo
atomicamente (el UNICO commit point) -> releer y validar -> liberar
lock.

Un evento que no sea alcanzable desde `active.json.latest_event_id`
(siguiendo `previous_event_id`) es un intento NO confirmado, nunca una
transicion activa -- este modulo NUNCA reescribe ni borra un evento ya
persistido."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..contracts.unified_activation_config import UnifiedFallbackPolicy
from ..contracts.unified_activation_materialization import (
    ActivationTransitionAction,
    ActivationTransitionEvent,
    ActiveActivationPointer,
    MaterializedGenerationManifest,
    compute_event_id,
)
from ..contracts.unified_materialization_authorization import UnifiedMaterializationReasonCode
from .errors import UnifiedActivationTransitionError

if TYPE_CHECKING:
    from .unified_activation_generation_builder import UnifiedGenerationFiles
    from .unified_activation_store import UnifiedActivationStore


def _hash_model(model: MaterializedGenerationManifest | ActiveActivationPointer) -> str:
    return hashlib.sha256(model.to_stable_json().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TransitionResult:
    pointer: ActiveActivationPointer
    event: ActivationTransitionEvent
    manifest: MaterializedGenerationManifest
    idempotent: bool


def keep_current(store: UnifiedActivationStore) -> ActiveActivationPointer | None:
    """Nunca escribe nada -- retorna el puntero actual tal cual (o
    `None` si el run nunca se inicializo). Corresponde a
    `ActivationTransitionAction.KEEP_CURRENT`: reautorizar el estado
    activo actual siempre reduce, en la practica, al mismo camino
    idempotente que cualquier otra transicion hacia la generacion ya
    activa (ver `_apply_transition`)."""
    return store.read_active_pointer()


def _apply_transition(
    store: UnifiedActivationStore,
    *,
    action: ActivationTransitionAction,
    run_id: str,
    to_manifest: MaterializedGenerationManifest,
    data_files: dict[str, bytes],
    activation_evaluation_hash: str,
    authorization_hash: str,
    reason_code: UnifiedMaterializationReasonCode,
    fallback_policy: UnifiedFallbackPolicy,
    fallback_generation_id: str,
    expected_active_pointer_hash: str | None,
) -> TransitionResult:
    with store.lock():
        current_pointer = store.read_active_pointer()

        from_generation_id = (
            None if current_pointer is None else current_pointer.active_generation_id
        )
        previous_event_id = None if current_pointer is None else current_pointer.latest_event_id
        sequence = 1 if current_pointer is None else current_pointer.pointer_version + 1

        # Idempotencia (Parte 2, principio 14; Parte 12), evaluada
        # ANTES de cualquier chequeo estructural por accion:
        # transicionar hacia la generacion YA activa (con cualquier
        # accion, incluido un INITIALIZE_V1 repetido) nunca produce un
        # evento nuevo ni mueve el puntero -- se retorna el estado
        # actual tal cual.
        if current_pointer is not None and from_generation_id == to_manifest.generation_id:
            existing_event = store.read_event(current_pointer.latest_event_id)
            return TransitionResult(
                pointer=current_pointer,
                event=existing_event,
                manifest=to_manifest,
                idempotent=True,
            )

        if current_pointer is None:
            if expected_active_pointer_hash is not None:
                raise UnifiedActivationTransitionError(
                    "expected_active_pointer_hash fue declarado pero no existe ningun "
                    "puntero activo todavia"
                )
            if action != ActivationTransitionAction.INITIALIZE_V1:
                raise UnifiedActivationTransitionError(
                    f"{action.value} exige un puntero activo existente -- "
                    "ejecutar INITIALIZE_V1 primero"
                )
        else:
            if action == ActivationTransitionAction.INITIALIZE_V1:
                raise UnifiedActivationTransitionError(
                    "INITIALIZE_V1 solo aplica cuando el run no tiene puntero activo todavia"
                )
            if expected_active_pointer_hash is not None:
                actual_hash = _hash_model(current_pointer)
                if actual_hash != expected_active_pointer_hash:
                    raise UnifiedActivationTransitionError(
                        "expected_active_pointer_hash no coincide con el puntero real -- "
                        "posible lost update, transicion abortada sin efecto"
                    )

        candidate_event_id = compute_event_id(
            run_id=run_id,
            sequence=sequence,
            action=action,
            from_generation_id=from_generation_id,
            to_generation_id=to_manifest.generation_id,
            previous_event_id=previous_event_id,
            authorization_hash=authorization_hash,
        )

        persisted_manifest = store.persist_generation(to_manifest, data_files)
        store.validate_generation_files(persisted_manifest)

        event = ActivationTransitionEvent(
            event_id=candidate_event_id,
            run_id=run_id,
            sequence=sequence,
            action=action,
            from_generation_id=from_generation_id,
            to_generation_id=persisted_manifest.generation_id,
            previous_event_id=previous_event_id,
            activation_evaluation_hash=activation_evaluation_hash,
            authorization_hash=authorization_hash,
            reason_code=reason_code,
            expected_previous_pointer_hash=expected_active_pointer_hash,
            resulting_lane=persisted_manifest.lane,
        )
        persisted_event = store.persist_event(event)
        if persisted_event.to_stable_json() != event.to_stable_json():
            raise UnifiedActivationTransitionError(
                "fallo al verificar el evento inmediatamente despues de persistirlo"
            )

        new_pointer = ActiveActivationPointer(
            run_id=run_id,
            pointer_version=sequence,
            active_generation_id=persisted_manifest.generation_id,
            active_lane=persisted_manifest.lane,
            active_generation_manifest_hash=_hash_model(persisted_manifest),
            previous_generation_id=from_generation_id,
            fallback_generation_id=fallback_generation_id,
            latest_event_id=persisted_event.event_id,
            fallback_policy=fallback_policy,
        )
        written_pointer = store.write_active_pointer(new_pointer)
        return TransitionResult(
            pointer=written_pointer,
            event=persisted_event,
            manifest=persisted_manifest,
            idempotent=False,
        )


def initialize_v1(
    store: UnifiedActivationStore,
    *,
    run_id: str,
    v1_manifest: MaterializedGenerationManifest,
    activation_evaluation_hash: str,
    authorization_hash: str,
    reason_code: UnifiedMaterializationReasonCode,
    fallback_policy: UnifiedFallbackPolicy = UnifiedFallbackPolicy.NO_FALLBACK,
) -> TransitionResult:
    """Primera transicion de un run: nunca requiere
    `expected_active_pointer_hash` (no existe puntero previo).
    `fallback_generation_id` de V1 es SIEMPRE la propia generacion V1
    (Parte 5, invariante 9: el fallback siempre termina en V1)."""
    return _apply_transition(
        store,
        action=ActivationTransitionAction.INITIALIZE_V1,
        run_id=run_id,
        to_manifest=v1_manifest,
        data_files={},
        activation_evaluation_hash=activation_evaluation_hash,
        authorization_hash=authorization_hash,
        reason_code=reason_code,
        fallback_policy=fallback_policy,
        fallback_generation_id=v1_manifest.generation_id,
        expected_active_pointer_hash=None,
    )


def _activate_unified(
    store: UnifiedActivationStore,
    *,
    action: ActivationTransitionAction,
    run_id: str,
    unified_manifest: MaterializedGenerationManifest,
    unified_files: UnifiedGenerationFiles,
    activation_evaluation_hash: str,
    authorization_hash: str,
    reason_code: UnifiedMaterializationReasonCode,
    fallback_policy: UnifiedFallbackPolicy,
    expected_active_pointer_hash: str | None,
) -> TransitionResult:
    if unified_manifest.fallback_generation_id is None:
        raise UnifiedActivationTransitionError(
            "una generacion UNIFIED exige fallback_generation_id (siempre debe apuntar a V1)"
        )
    return _apply_transition(
        store,
        action=action,
        run_id=run_id,
        to_manifest=unified_manifest,
        data_files=unified_files.bytes_by_logical_name(),
        activation_evaluation_hash=activation_evaluation_hash,
        authorization_hash=authorization_hash,
        reason_code=reason_code,
        fallback_policy=fallback_policy,
        fallback_generation_id=unified_manifest.fallback_generation_id,
        expected_active_pointer_hash=expected_active_pointer_hash,
    )


def activate_unified_canary(
    store: UnifiedActivationStore,
    *,
    run_id: str,
    unified_manifest: MaterializedGenerationManifest,
    unified_files: UnifiedGenerationFiles,
    activation_evaluation_hash: str,
    authorization_hash: str,
    reason_code: UnifiedMaterializationReasonCode,
    fallback_policy: UnifiedFallbackPolicy = UnifiedFallbackPolicy.FALLBACK_TO_V1,
    expected_active_pointer_hash: str | None = None,
) -> TransitionResult:
    return _activate_unified(
        store,
        action=ActivationTransitionAction.ACTIVATE_UNIFIED_CANARY,
        run_id=run_id,
        unified_manifest=unified_manifest,
        unified_files=unified_files,
        activation_evaluation_hash=activation_evaluation_hash,
        authorization_hash=authorization_hash,
        reason_code=reason_code,
        fallback_policy=fallback_policy,
        expected_active_pointer_hash=expected_active_pointer_hash,
    )


def activate_unified_primary(
    store: UnifiedActivationStore,
    *,
    run_id: str,
    unified_manifest: MaterializedGenerationManifest,
    unified_files: UnifiedGenerationFiles,
    activation_evaluation_hash: str,
    authorization_hash: str,
    reason_code: UnifiedMaterializationReasonCode,
    fallback_policy: UnifiedFallbackPolicy = UnifiedFallbackPolicy.FALLBACK_TO_V1,
    expected_active_pointer_hash: str | None = None,
) -> TransitionResult:
    return _activate_unified(
        store,
        action=ActivationTransitionAction.ACTIVATE_UNIFIED_PRIMARY,
        run_id=run_id,
        unified_manifest=unified_manifest,
        unified_files=unified_files,
        activation_evaluation_hash=activation_evaluation_hash,
        authorization_hash=authorization_hash,
        reason_code=reason_code,
        fallback_policy=fallback_policy,
        expected_active_pointer_hash=expected_active_pointer_hash,
    )


def fallback_to_v1(
    store: UnifiedActivationStore,
    *,
    run_id: str,
    v1_manifest: MaterializedGenerationManifest,
    activation_evaluation_hash: str,
    authorization_hash: str,
    reason_code: UnifiedMaterializationReasonCode = (
        UnifiedMaterializationReasonCode.ACTIVE_GENERATION_INVALID
    ),
    fallback_policy: UnifiedFallbackPolicy = UnifiedFallbackPolicy.FALLBACK_TO_V1,
    expected_active_pointer_hash: str | None = None,
) -> TransitionResult:
    """`v1_manifest` debe ser una generacion V1 YA persistida
    previamente (tipicamente la propia `fallback_generation_id` del
    puntero actual) -- el fallback SIEMPRE termina en V1 (Parte 5,
    invariante 22), nunca en otra generacion unified."""
    return _apply_transition(
        store,
        action=ActivationTransitionAction.FALLBACK_TO_V1,
        run_id=run_id,
        to_manifest=v1_manifest,
        data_files={},
        activation_evaluation_hash=activation_evaluation_hash,
        authorization_hash=authorization_hash,
        reason_code=reason_code,
        fallback_policy=fallback_policy,
        fallback_generation_id=v1_manifest.generation_id,
        expected_active_pointer_hash=expected_active_pointer_hash,
    )


def _resolve_rollback_target(
    store: UnifiedActivationStore, *, target_generation_id: str
) -> MaterializedGenerationManifest:
    """Comun a ambos rollbacks: el destino debe ser una generacion
    COMPLETA y verificable (Parte 5, invariantes 23-24) -- nunca una
    generacion parcial o corrupta."""
    manifest = store.read_generation_manifest(target_generation_id)
    store.validate_generation_files(manifest)
    return manifest


def rollback_to_previous(
    store: UnifiedActivationStore,
    *,
    run_id: str,
    activation_evaluation_hash: str,
    authorization_hash: str,
    reason_code: UnifiedMaterializationReasonCode = (
        UnifiedMaterializationReasonCode.OPERATOR_ROLLBACK
    ),
    fallback_policy: UnifiedFallbackPolicy = UnifiedFallbackPolicy.FALLBACK_TO_V1,
    expected_active_pointer_hash: str | None = None,
) -> TransitionResult:
    current_pointer = store.read_active_pointer()
    if current_pointer is None or current_pointer.previous_generation_id is None:
        raise UnifiedActivationTransitionError(
            "ROLLBACK_TO_PREVIOUS exige un puntero activo con previous_generation_id"
        )
    target_manifest = _resolve_rollback_target(
        store, target_generation_id=current_pointer.previous_generation_id
    )
    return _apply_transition(
        store,
        action=ActivationTransitionAction.ROLLBACK_TO_PREVIOUS,
        run_id=run_id,
        to_manifest=target_manifest,
        data_files={},
        activation_evaluation_hash=activation_evaluation_hash,
        authorization_hash=authorization_hash,
        reason_code=reason_code,
        fallback_policy=fallback_policy,
        fallback_generation_id=(
            target_manifest.fallback_generation_id or target_manifest.generation_id
        ),
        expected_active_pointer_hash=expected_active_pointer_hash,
    )


def rollback_to_generation(
    store: UnifiedActivationStore,
    *,
    run_id: str,
    target_generation_id: str,
    activation_evaluation_hash: str,
    authorization_hash: str,
    reason_code: UnifiedMaterializationReasonCode = (
        UnifiedMaterializationReasonCode.OPERATOR_ROLLBACK
    ),
    fallback_policy: UnifiedFallbackPolicy = UnifiedFallbackPolicy.FALLBACK_TO_V1,
    expected_active_pointer_hash: str | None = None,
) -> TransitionResult:
    target_manifest = _resolve_rollback_target(store, target_generation_id=target_generation_id)
    return _apply_transition(
        store,
        action=ActivationTransitionAction.ROLLBACK_TO_GENERATION,
        run_id=run_id,
        to_manifest=target_manifest,
        data_files={},
        activation_evaluation_hash=activation_evaluation_hash,
        authorization_hash=authorization_hash,
        reason_code=reason_code,
        fallback_policy=fallback_policy,
        fallback_generation_id=(
            target_manifest.fallback_generation_id or target_manifest.generation_id
        ),
        expected_active_pointer_hash=expected_active_pointer_hash,
    )


__all__ = [
    "TransitionResult",
    "activate_unified_canary",
    "activate_unified_primary",
    "fallback_to_v1",
    "initialize_v1",
    "keep_current",
    "rollback_to_generation",
    "rollback_to_previous",
]
