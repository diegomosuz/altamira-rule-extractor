"""Orquestador prepare/confirm/execute (Fase 15B1 Parte 8).

Tres funciones publicas, dos pasos reales:

1. `prepare_operational_action` (STEP 1, `POST .../prepare`): valida
   permiso/estado, construye un `PreparedOperationalIntent`, lo firma
   en un challenge (`operational_challenge.py`) -- NUNCA ejecuta
   ninguna transicion de Fase 14B.
2. `read_challenge_for_confirm` (`GET .../confirm`, SOLO lectura):
   verifica el challenge y lo retorna para render -- NUNCA ejecuta.
3. `execute_operational_action` (STEP 2, `POST .../execute`): revalida
   TODO contra el estado real vigente (principal, pointer, evaluacion),
   calcula la `OperationalAuthorizationRequest` final, la traduce a un
   `UnifiedMaterializationAuthorization` efimera (puente de
   reutilizacion documentado -- ver mas abajo) escrita en un archivo
   temporal FUERA de `run_dir`, invoca `materialize_unified_activation`
   (Fase 14B) SIN MODIFICARLO, y borra el archivo temporal en
   `finally`.

Puente de reutilizacion: en vez de reimplementar la orquestacion de
Fase 14B (carga de evaluacion, construccion de generaciones,
transiciones atomicas), este modulo construye una
`UnifiedMaterializationAuthorization` EFIMERA -- nunca persistida en el
repositorio ni en `run_dir`, escrita en un archivo temporal del sistema
y borrada inmediatamente despues de la llamada -- y la pasa a
`pipeline.unified_materialization_service.materialize_unified_activation`,
exactamente como lo haria un operador humano con `--authorization` en
el CLI de Fase 14B. Esto satisface "reutilizar sin modificar" sin
duplicar logica de materializacion."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..api.errors import ForbiddenError, OperationalPreconditionError, ServiceUnavailableError
from ..contracts.operational_authorization_request import (
    OperationalAction,
    OperationalAuthorizationRequest,
    PreparedOperationalIntent,
)
from ..contracts.security_config import (
    AuthenticationMode,
    OperationalPermission,
    SecurityConfig,
)
from ..contracts.security_identity import AuthenticatedPrincipal
from ..contracts.unified_activation_evaluation import (
    UnifiedActivationComparisonLevel,
    UnifiedActivationEvaluationArtifact,
    UnifiedActivationReadinessDisposition,
)
from ..contracts.unified_activation_materialization import ActiveActivationPointer
from ..contracts.unified_materialization_authorization import (
    UnifiedMaterializationAction,
    UnifiedMaterializationAuthorization,
    UnifiedMaterializationReasonCode,
)
from ..contracts.unified_shadow_downstream import UnifiedShadowGuardrailStatus
from ..pipeline.errors import UnifiedMaterializationError
from ..pipeline.unified_activation_store import UnifiedActivationStore
from ..pipeline.unified_materialization_service import (
    MaterializationResult,
    materialize_unified_activation,
)
from .operational_challenge import sign_challenge, verify_challenge
from .operational_challenge_consumption import (
    ChallengeAlreadyConsumedError,
    ChallengeConsumptionWriteError,
    consume_challenge_atomically,
)
from .session import SessionData

_DIAGNOSTICS_DIR_NAME = "diagnostics"
_EVALUATION_FILENAME = "unified-activation-evaluation.json"

_REQUIRED_PERMISSION: dict[OperationalAction, OperationalPermission] = {
    OperationalAction.ACTIVATE_UNIFIED_CANARY: OperationalPermission.ACTIVATE_CANARY,
    OperationalAction.ACTIVATE_UNIFIED_PRIMARY: OperationalPermission.ACTIVATE_PRIMARY,
    OperationalAction.FALLBACK_TO_V1: OperationalPermission.EXECUTE_FALLBACK,
    OperationalAction.ROLLBACK_TO_PREVIOUS: OperationalPermission.EXECUTE_ROLLBACK,
    OperationalAction.ROLLBACK_TO_GENERATION: OperationalPermission.EXECUTE_ROLLBACK,
}

_ACTION_MAP: dict[OperationalAction, UnifiedMaterializationAction] = {
    OperationalAction.ACTIVATE_UNIFIED_CANARY: (
        UnifiedMaterializationAction.ACTIVATE_UNIFIED_CANARY
    ),
    OperationalAction.ACTIVATE_UNIFIED_PRIMARY: (
        UnifiedMaterializationAction.ACTIVATE_UNIFIED_PRIMARY
    ),
    OperationalAction.FALLBACK_TO_V1: UnifiedMaterializationAction.FALLBACK_TO_V1,
    OperationalAction.ROLLBACK_TO_PREVIOUS: UnifiedMaterializationAction.ROLLBACK_TO_PREVIOUS,
    OperationalAction.ROLLBACK_TO_GENERATION: UnifiedMaterializationAction.ROLLBACK_TO_GENERATION,
}

_DISTINCT_REVIEWER_DISABLED_MESSAGE = (
    "esta accion exige un revisor distinto del operador -- imposible en "
    "authentication_mode=DISABLED_DEV (toda identidad resuelve al mismo principal anonimo)"
)


class OperationalWorkflowError(Exception):
    """Fallo de precondicion del workflow no cubierto por un `ApiError`
    mas especifico -- nunca envuelve un fallo interno de Fase 14B sin
    reclasificarlo explicitamente como `OperationalPreconditionError`."""


def required_permission_for(action: OperationalAction) -> OperationalPermission:
    return _REQUIRED_PERMISSION[action]


def distinct_reviewer_required_for(action: OperationalAction, config: SecurityConfig) -> bool:
    if action == OperationalAction.ACTIVATE_UNIFIED_PRIMARY:
        return config.require_distinct_reviewer_for_primary
    if action in (
        OperationalAction.ROLLBACK_TO_PREVIOUS,
        OperationalAction.ROLLBACK_TO_GENERATION,
    ):
        return config.require_distinct_reviewer_for_rollback
    return False


def _hash_pointer(pointer: ActiveActivationPointer) -> str:
    """Misma formula que `pipeline/unified_activation_transition.py::
    _hash_model` (sha256 de `to_stable_json()`, API PUBLICA de
    `AltamiraBaseModel`) -- duplicada intencionalmente en vez de
    importar un simbolo privado de otro modulo."""
    return hashlib.sha256(pointer.to_stable_json().encode("utf-8")).hexdigest()


def read_active_pointer_hash(run_dir: Path) -> tuple[ActiveActivationPointer | None, str | None]:
    pointer = UnifiedActivationStore(run_dir).read_active_pointer()
    if pointer is None:
        return None, None
    return pointer, _hash_pointer(pointer)


def read_current_evaluation(run_dir: Path) -> tuple[UnifiedActivationEvaluationArtifact, str]:
    """Misma logica que `unified_materialization_service.py::
    _load_evaluation` (funcion privada de ese modulo, deliberadamente
    NO importada -- ver docstring de este modulo): hash SHA-256 sobre
    los BYTES CRUDOS del archivo, nunca sobre una re-serializacion. Un
    hash calculado de otra forma podria no coincidir con el que
    recalcula `materialize_unified_activation` al releer el mismo
    archivo, rompiendo la revalidacion de `activation_evaluation_hash`."""
    path = run_dir / _DIAGNOSTICS_DIR_NAME / _EVALUATION_FILENAME
    if path.is_symlink() or not path.is_file():
        raise OperationalWorkflowError(
            "diagnostics/unified-activation-evaluation.json ausente -- la evaluacion de "
            "Fase 14A no se ha ejecutado para este run"
        )
    try:
        raw_bytes = path.read_bytes()
        evaluation = UnifiedActivationEvaluationArtifact.model_validate_json(
            raw_bytes.decode("utf-8")
        )
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise OperationalWorkflowError(
            "diagnostics/unified-activation-evaluation.json invalido"
        ) from exc
    return evaluation, hashlib.sha256(raw_bytes).hexdigest()


@dataclass(frozen=True)
class CandidateUnifiedGroup:
    """Grupo elegible para `approved_group_ids` -- proyeccion minima de
    `UnifiedActivationUnifiedReference` para poblar los checkboxes del
    formulario (Parte 12C: "aprobados via checkboxes de datos validos,
    nunca texto libre")."""

    group_id: str
    rule_family: str
    program: str


def list_candidate_unified_groups(run_dir: Path) -> list[CandidateUnifiedGroup]:
    """Misma condicion de elegibilidad que `pipeline/unified_activation_
    generation_builder.py` exige al materializar (`level=RULE` y
    `guardrail_status=PASSED`) -- calculada aqui de forma independiente
    y de solo lectura, para que el formulario nunca ofrezca un grupo
    que la materializacion rechazaria despues. Nunca proviene de
    `overview.unified_groups` (Fase 15A): ese reader refleja la
    generacion unified YA activa, que no existe todavia antes de la
    PRIMERA activacion de canary/primary."""
    evaluation, _hash = read_current_evaluation(run_dir)
    candidates = [
        CandidateUnifiedGroup(
            group_id=reference.group_id,
            rule_family=reference.rule_family,
            program=reference.program,
        )
        for reference in evaluation.unified_references
        if reference.level == UnifiedActivationComparisonLevel.RULE
        and reference.guardrail_status == UnifiedShadowGuardrailStatus.PASSED
    ]
    return sorted(candidates, key=lambda c: c.group_id)


def _require_permission(
    principal: AuthenticatedPrincipal, permission: OperationalPermission
) -> None:
    """La identidad ya esta resuelta en este punto (`identity_resolver.py`
    ya rechazo con 401 cualquier identidad ausente/invalida en
    `TRUSTED_PROXY_HEADERS` ANTES de construir un `AuthenticatedPrincipal`)
    -- esta funcion SOLO revalida autorizacion, nunca autenticacion."""
    if not principal.has_permission(permission):
        raise ForbiddenError(f"falta el permiso {permission.value}")


def prepare_operational_action(
    *,
    run_dir: Path,
    run_id: str,
    action: OperationalAction,
    principal: AuthenticatedPrincipal,
    security_config: SecurityConfig,
    session: SessionData,
    reason_code: UnifiedMaterializationReasonCode,
    review_reference: str,
    approved_group_ids: list[str],
    target_generation_id: str | None,
) -> tuple[PreparedOperationalIntent, str]:
    """STEP 1. Retorna `(intent, challenge_token)`. NUNCA ejecuta
    ninguna transicion de Fase 14B; el backend SIEMPRE revalida
    permisos aqui, sin importar que la UI ya haya ocultado el boton."""
    _require_permission(principal, OperationalPermission.PREPARE_AUTHORIZATION)

    distinct_required = distinct_reviewer_required_for(action, security_config)
    if distinct_required and principal.authentication_mode == AuthenticationMode.DISABLED_DEV:
        raise ForbiddenError(_DISTINCT_REVIEWER_DISABLED_MESSAGE)

    _pointer, pointer_hash = read_active_pointer_hash(run_dir)
    if pointer_hash is None:
        raise OperationalPreconditionError(
            "no existe un puntero de activacion vigente para este run -- inicializar la "
            "activacion (Fase 14B) antes de solicitar una accion operativa"
        )

    intent = PreparedOperationalIntent(
        run_id=run_id,
        action=action,
        prepared_by_principal_id=principal.principal_id,
        distinct_reviewer_required=distinct_required,
        expected_active_pointer_hash=pointer_hash,
        target_generation_id=target_generation_id,
        reason_code=reason_code,
        review_reference=review_reference,
        approved_group_ids=sorted(set(approved_group_ids)),
    )
    token = sign_challenge(intent, session)
    return intent, token


def read_challenge_for_confirm(
    challenge_token: str, session: SessionData
) -> PreparedOperationalIntent:
    """`GET .../confirm`: SOLO lectura -- verifica y retorna el intent
    para render, nunca ejecuta ninguna transicion (un `GET` jamas
    ejecuta -- Parte 8)."""
    intent = verify_challenge(challenge_token, session)
    if intent is None:
        raise OperationalPreconditionError(
            "el challenge esta ausente, expirado, corrupto o pertenece a otra sesion -- "
            "debe iniciarse un nuevo prepare"
        )
    return intent


def _build_bridge_authorization(
    request: OperationalAuthorizationRequest,
    *,
    activation_evaluation_hash: str,
    expected_readiness_disposition: UnifiedActivationReadinessDisposition,
) -> UnifiedMaterializationAuthorization:
    """Traduce la intencion operativa YA revalidada a una
    `UnifiedMaterializationAuthorization` EFIMERA (ver docstring del
    modulo)."""
    action = _ACTION_MAP[request.action]
    review_reference = (
        f"{request.review_reference} | operator={request.operator_principal_id}"
        f" | reviewer={request.reviewer_principal_id or 'n/a'}"
    )
    return UnifiedMaterializationAuthorization(
        run_id=request.run_id,
        activation_evaluation_hash=activation_evaluation_hash,
        expected_readiness_disposition=expected_readiness_disposition,
        action=action,
        target_generation_id=request.target_generation_id,
        expected_active_pointer_hash=request.expected_active_pointer_hash,
        reason_code=request.reason_code,
        review_reference=review_reference,
        approved_group_ids=request.approved_group_ids,
        fallback_authorized=action == UnifiedMaterializationAction.FALLBACK_TO_V1,
        rollback_authorized=action
        in (
            UnifiedMaterializationAction.ROLLBACK_TO_PREVIOUS,
            UnifiedMaterializationAction.ROLLBACK_TO_GENERATION,
        ),
    )


def execute_operational_action(
    *,
    run_dir: Path,
    run_id: str,
    challenge_token: str,
    principal: AuthenticatedPrincipal,
    session: SessionData,
    clock: Callable[[], datetime] | None = None,
) -> MaterializationResult:
    """STEP 2, `POST .../execute`. Revalida TODO contra el estado real
    vigente y ejecuta a traves de `materialize_unified_activation`
    (Fase 14B, sin modificar). Nunca ejecuta con: challenge invalido/
    expirado/de otra sesion; `run_id` distinto al del challenge;
    permiso insuficiente; pointer cambiado; revisor distinto exigido
    pero no satisfecho; challenge ya consumido.

    Orden exacto (cierre de Fase 15B1, "single-use real"): la
    identidad/sesion/CSRF ya se validaron en el router ANTES de llegar
    aqui (`Depends(get_principal)`/`Depends(get_session)`/
    `require_csrf` -- pasos 1-4). Dentro de esta funcion: 5) firma y
    expiracion del challenge (`verify_challenge`); 6) principal/accion/
    run coherentes con el challenge (`run_id`, permiso, autoaprobacion);
    7) CONSUMO ATOMICO del challenge (`consume_challenge_atomically`) --
    a partir de aqui, CUALQUIER resultado (exito, fallo, o transicion
    idempotente sin cambio de pointer) exige un `prepare` nuevo para
    reintentar, sin importar lo que pase despues; 8) relectura del
    pointer/evaluacion vigentes; 9) ejecucion (o rechazo) via
    `materialize_unified_activation`. El paso 10 (auditar resultado) lo
    hace el router, no esta funcion."""
    intent = verify_challenge(challenge_token, session)
    if intent is None:
        raise OperationalPreconditionError(
            "el challenge esta ausente, expirado, corrupto o pertenece a otra sesion"
        )
    if intent.run_id != run_id:
        raise OperationalPreconditionError("el challenge no corresponde a este run_id")

    _require_permission(principal, required_permission_for(intent.action))

    if (
        intent.distinct_reviewer_required
        and principal.authentication_mode == AuthenticationMode.DISABLED_DEV
    ):
        raise ForbiddenError(_DISTINCT_REVIEWER_DISABLED_MESSAGE)

    # La reconstruccion revalida, entre otras cosas, que el operador
    # (identidad de ESTA request) sea distinto del revisor que preparo
    # el challenge cuando distinct_reviewer_required=true (Parte 13).
    try:
        final_request = intent.to_authorization_request(principal.principal_id)
    except ValueError as exc:
        raise ForbiddenError(
            "el operador que ejecuta no puede ser el mismo que preparo la accion cuando se "
            "exige un revisor distinto (autoaprobacion rechazada)"
        ) from exc

    # STEP 7 -- consumo atomico e irreversible. Debe ocurrir DESPUES de
    # toda validacion de identidad/permiso/coherencia (arriba) y ANTES
    # de releer el estado vigente (abajo): un doble submit concurrente,
    # un replay tras exito, tras fallo, o tras una transicion
    # idempotente sin cambio de pointer, quedan todos cubiertos por
    # igual -- ninguno de esos casos llega a `materialize_unified_
    # activation` una segunda vez.
    try:
        if clock is None:
            consume_challenge_atomically(
                run_dir,
                challenge_token=challenge_token,
                run_id=run_id,
                principal_id=principal.principal_id,
                operational_action=intent.action,
            )
        else:
            consume_challenge_atomically(
                run_dir,
                challenge_token=challenge_token,
                run_id=run_id,
                principal_id=principal.principal_id,
                operational_action=intent.action,
                clock=clock,
            )
    except ChallengeAlreadyConsumedError as exc:
        raise OperationalPreconditionError(str(exc)) from exc
    except ChallengeConsumptionWriteError as exc:
        raise ServiceUnavailableError(str(exc)) from exc

    _current_pointer, current_pointer_hash = read_active_pointer_hash(run_dir)
    if current_pointer_hash != intent.expected_active_pointer_hash:
        raise OperationalPreconditionError(
            "el puntero de activacion cambio desde que se preparo esta accion -- posible "
            "lost update, ejecucion abortada sin efecto"
        )

    evaluation, activation_evaluation_hash = read_current_evaluation(run_dir)
    if evaluation.run_id != run_id:
        raise OperationalPreconditionError("la evaluacion de Fase 14A no corresponde a este run_id")

    bridge_authorization = _build_bridge_authorization(
        final_request,
        activation_evaluation_hash=activation_evaluation_hash,
        expected_readiness_disposition=evaluation.readiness_disposition,
    )

    fd, tmp_name = tempfile.mkstemp(prefix="altamira-operational-authorization-", suffix=".yaml")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(bridge_authorization.to_stable_json())
        try:
            return materialize_unified_activation(run_dir, run_id, authorization_path=tmp_path)
        except UnifiedMaterializationError as exc:
            raise OperationalPreconditionError(str(exc)) from exc
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


__all__ = [
    "CandidateUnifiedGroup",
    "OperationalWorkflowError",
    "distinct_reviewer_required_for",
    "execute_operational_action",
    "list_candidate_unified_groups",
    "prepare_operational_action",
    "read_active_pointer_hash",
    "read_challenge_for_confirm",
    "read_current_evaluation",
    "required_permission_for",
]
