"""Servicio de materializacion controlada (Fase 14B Parte 12,
`feat/controlled-unified-materialization`).

Orquesta: localizar el run -> cargar `diagnostics/unified-activation-
evaluation.json` (Fase 14A, NUNCA regenerado) -> cargar la
autorizacion (`--authorization`, YAML EXTERNO, NUNCA copiado al
repositorio ni al run -- mismo principio que `--config` en Fase 14A) ->
validar `run_id`/`activation_evaluation_hash`/`expected_readiness_
disposition` contra el estado real -> construir/reutilizar la
generacion V1 -> inicializar `active.json` en V1 si esta ausente ->
ejecutar la accion solicitada -> persistir EXCLUSIVAMENTE bajo
`activation/`.

Nunca modifica `run.json`, ningun `artifacts/01-10` ni ningun
`diagnostics/*.json` preexistente. Idempotente: una autorizacion ya
aplicada al mismo estado produce el mismo resultado sin efectos
nuevos (ver `pipeline/unified_activation_transition.py`)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ..contracts.unified_activation_config import UnifiedFallbackPolicy
from ..contracts.unified_activation_evaluation import UnifiedActivationEvaluationArtifact
from ..contracts.unified_activation_materialization import MaterializedActivationLane
from ..contracts.unified_materialization_authorization import (
    UnifiedMaterializationAction,
    UnifiedMaterializationAuthorization,
)
from ..contracts.unified_shadow_downstream import UnifiedShadowDownstreamArtifact
from .errors import UnifiedMaterializationError
from .unified_activation_generation_builder import build_unified_generation
from .unified_activation_store import UnifiedActivationStore
from .unified_activation_transition import (
    TransitionResult,
    activate_unified_canary,
    activate_unified_primary,
    fallback_to_v1,
    initialize_v1,
    keep_current,
    rollback_to_generation,
    rollback_to_previous,
)
from .v1_activation_generation_builder import build_v1_generation_manifest
from .yaml_utils import read_yaml_config

_DIAGNOSTICS_DIR_NAME = "diagnostics"
_EVALUATION_FILENAME = "unified-activation-evaluation.json"
_DOWNSTREAM_FILENAME = "unified-shadow-downstream.json"


@dataclass(frozen=True)
class MaterializationResult:
    """Resultado tipado de `materialize_unified_activation` -- lo que
    el CLI (`unified-activation-materialize`, Parte 13) resume."""

    run_id: str
    action: UnifiedMaterializationAction
    generation_id: str
    active_lane: MaterializedActivationLane
    previous_generation_id: str | None
    fallback_generation_id: str
    pointer_version: int
    event_id: str
    idempotent: bool
    materialized_file_count: int


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_evaluation(run_dir: Path) -> tuple[UnifiedActivationEvaluationArtifact, str]:
    path = run_dir / _DIAGNOSTICS_DIR_NAME / _EVALUATION_FILENAME
    if path.is_symlink() or not path.is_file():
        raise UnifiedMaterializationError(
            "diagnostics/unified-activation-evaluation.json ausente -- ejecutar "
            "unified-activation-evaluate primero"
        )
    try:
        raw_bytes = path.read_bytes()
        evaluation = UnifiedActivationEvaluationArtifact.model_validate_json(
            raw_bytes.decode("utf-8")
        )
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise UnifiedMaterializationError(
            "diagnostics/unified-activation-evaluation.json invalido"
        ) from exc
    return evaluation, _hash_bytes(raw_bytes)


def _load_downstream(run_dir: Path) -> UnifiedShadowDownstreamArtifact | None:
    path = run_dir / _DIAGNOSTICS_DIR_NAME / _DOWNSTREAM_FILENAME
    if path.is_symlink() or not path.is_file():
        return None
    try:
        return UnifiedShadowDownstreamArtifact.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _load_authorization(
    authorization_path: Path,
) -> tuple[UnifiedMaterializationAuthorization, str]:
    """Carga y valida el YAML EXTERNO de autorizacion -- NUNCA se copia
    al repositorio ni al directorio del run. `authorization_hash` se
    calcula sobre `to_stable_json()` de la autorizacion YA VALIDADA
    (representacion normalizada), nunca sobre los bytes crudos del
    YAML."""
    if authorization_path.is_symlink():
        raise UnifiedMaterializationError(
            "la ruta de autorizacion no es un archivo regular (symlink rechazado)"
        )
    if not authorization_path.is_file():
        raise UnifiedMaterializationError("no se encontro el archivo de autorizacion")
    try:
        document, _raw_hash = read_yaml_config(authorization_path)
    except Exception as exc:  # noqa: BLE001 - se reclasifica como error de dominio explicito
        raise UnifiedMaterializationError("el archivo de autorizacion no es YAML valido") from exc
    try:
        authorization = UnifiedMaterializationAuthorization.model_validate(document)
    except ValueError as exc:
        raise UnifiedMaterializationError(
            "el archivo de autorizacion no cumple el esquema de UnifiedMaterializationAuthorization"
        ) from exc
    return authorization, _hash_bytes(authorization.to_stable_json().encode("utf-8"))


def _to_result(
    run_id: str, action: UnifiedMaterializationAction, transition: TransitionResult
) -> MaterializationResult:
    return MaterializationResult(
        run_id=run_id,
        action=action,
        generation_id=transition.pointer.active_generation_id,
        active_lane=transition.pointer.active_lane,
        previous_generation_id=transition.pointer.previous_generation_id,
        fallback_generation_id=transition.pointer.fallback_generation_id,
        pointer_version=transition.pointer.pointer_version,
        event_id=transition.event.event_id,
        idempotent=transition.idempotent,
        materialized_file_count=len(transition.manifest.files),
    )


def materialize_unified_activation(
    run_dir: Path, run_id: str, *, authorization_path: Path
) -> MaterializationResult:
    """Punto de entrada unico del servicio. Fail-closed: cualquier
    inconsistencia entre la autorizacion y el estado real (run_id,
    hash de evaluacion, disposicion de disponibilidad esperada) aborta
    ANTES de tocar `activation/` -- nunca se persiste un resultado
    parcial."""
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise UnifiedMaterializationError(f"run {run_id!r} no encontrado")

    evaluation, real_evaluation_hash = _load_evaluation(run_dir)
    authorization, authorization_hash = _load_authorization(authorization_path)

    if authorization.run_id != run_id:
        raise UnifiedMaterializationError("la autorizacion no corresponde a este run_id")
    if authorization.activation_evaluation_hash != real_evaluation_hash:
        raise UnifiedMaterializationError(
            "activation_evaluation_hash desactualizado -- la evaluacion de Fase 14A cambio "
            "desde que se redacto la autorizacion"
        )
    if authorization.expected_readiness_disposition != evaluation.readiness_disposition:
        raise UnifiedMaterializationError(
            "expected_readiness_disposition no coincide con la disposicion real de la "
            "evaluacion de Fase 14A"
        )

    store = UnifiedActivationStore(run_dir)
    v1_manifest = build_v1_generation_manifest(
        run_dir,
        run_id=run_id,
        source_package_hash=evaluation.source_package_hash,
        activation_evaluation_hash=real_evaluation_hash,
        authorization_hash=authorization_hash,
    )

    current_pointer = store.read_active_pointer()
    if current_pointer is None:
        init_result = initialize_v1(
            store,
            run_id=run_id,
            v1_manifest=v1_manifest,
            activation_evaluation_hash=real_evaluation_hash,
            authorization_hash=authorization_hash,
            reason_code=authorization.reason_code,
        )
    else:
        init_result = None

    action = authorization.action

    if action == UnifiedMaterializationAction.KEEP_V1:
        if init_result is not None:
            return _to_result(run_id, action, init_result)
        pointer = keep_current(store)
        assert pointer is not None
        event = store.read_event(pointer.latest_event_id)
        manifest = store.read_generation_manifest(pointer.active_generation_id)
        return _to_result(
            run_id,
            action,
            TransitionResult(pointer=pointer, event=event, manifest=manifest, idempotent=True),
        )

    fallback_policy = (
        UnifiedFallbackPolicy.FALLBACK_TO_V1
        if authorization.fallback_authorized
        else UnifiedFallbackPolicy.NO_FALLBACK
    )

    if action in (
        UnifiedMaterializationAction.ACTIVATE_UNIFIED_CANARY,
        UnifiedMaterializationAction.ACTIVATE_UNIFIED_PRIMARY,
    ):
        downstream = _load_downstream(run_dir)
        if downstream is None:
            raise UnifiedMaterializationError(
                "diagnostics/unified-shadow-downstream.json ausente -- no se puede "
                "materializar unified sin la ejecucion downstream real de Fase 13"
            )
        # Asegura que la generacion V1 (fallback target) exista persistida.
        store.persist_generation(v1_manifest, {})
        unified_manifest, unified_files = build_unified_generation(
            evaluation=evaluation,
            downstream=downstream,
            authorization=authorization,
            run_id=run_id,
            source_package_hash=evaluation.source_package_hash,
            activation_evaluation_hash=real_evaluation_hash,
            authorization_hash=authorization_hash,
            fallback_generation_id=v1_manifest.generation_id,
        )
        activate_fn = (
            activate_unified_canary
            if action == UnifiedMaterializationAction.ACTIVATE_UNIFIED_CANARY
            else activate_unified_primary
        )
        transition = activate_fn(
            store,
            run_id=run_id,
            unified_manifest=unified_manifest,
            unified_files=unified_files,
            activation_evaluation_hash=real_evaluation_hash,
            authorization_hash=authorization_hash,
            reason_code=authorization.reason_code,
            fallback_policy=fallback_policy,
            expected_active_pointer_hash=authorization.expected_active_pointer_hash,
        )
        return _to_result(run_id, action, transition)

    if action == UnifiedMaterializationAction.FALLBACK_TO_V1:
        store.persist_generation(v1_manifest, {})
        transition = fallback_to_v1(
            store,
            run_id=run_id,
            v1_manifest=v1_manifest,
            activation_evaluation_hash=real_evaluation_hash,
            authorization_hash=authorization_hash,
            reason_code=authorization.reason_code,
            fallback_policy=fallback_policy,
            expected_active_pointer_hash=authorization.expected_active_pointer_hash,
        )
        return _to_result(run_id, action, transition)

    if action == UnifiedMaterializationAction.ROLLBACK_TO_PREVIOUS:
        transition = rollback_to_previous(
            store,
            run_id=run_id,
            activation_evaluation_hash=real_evaluation_hash,
            authorization_hash=authorization_hash,
            reason_code=authorization.reason_code,
            fallback_policy=fallback_policy,
            expected_active_pointer_hash=authorization.expected_active_pointer_hash,
        )
        return _to_result(run_id, action, transition)

    if action == UnifiedMaterializationAction.ROLLBACK_TO_GENERATION:
        target_generation_id = authorization.target_generation_id
        assert target_generation_id is not None  # invariante del contrato (Parte 3)
        transition = rollback_to_generation(
            store,
            run_id=run_id,
            target_generation_id=target_generation_id,
            activation_evaluation_hash=real_evaluation_hash,
            authorization_hash=authorization_hash,
            reason_code=authorization.reason_code,
            fallback_policy=fallback_policy,
            expected_active_pointer_hash=authorization.expected_active_pointer_hash,
        )
        return _to_result(run_id, action, transition)

    raise UnifiedMaterializationError(f"action no soportada: {action.value}")


_ROLLBACK_ACTIONS = frozenset(
    {
        UnifiedMaterializationAction.ROLLBACK_TO_PREVIOUS,
        UnifiedMaterializationAction.ROLLBACK_TO_GENERATION,
    }
)


def peek_authorization_action(authorization_path: Path) -> UnifiedMaterializationAction:
    """Carga y valida el YAML de autorizacion SIN tocar `activation/`
    -- usado por el CLI `unified-activation-rollback` (Parte 13) para
    rechazar, ANTES de ejecutar cualquier transicion, una autorizacion
    cuya `action` no sea de rollback."""
    authorization, _authorization_hash = _load_authorization(authorization_path)
    return authorization.action


def action_is_rollback(action: UnifiedMaterializationAction) -> bool:
    return action in _ROLLBACK_ACTIONS


__all__ = [
    "MaterializationResult",
    "action_is_rollback",
    "materialize_unified_activation",
    "peek_authorization_action",
]
