"""Auditoria operativa append-only (Fase 15B1,
`feat/final-hardening-release`).

`OperationalAuditEvent` registra QUIEN solicito/autorizo/ejecuto una
accion operativa y el resultado -- es una nocion DISTINTA de
`ActivationTransitionEvent` (Fase 14A/14B), que registra el cambio
TECNICO de lane/generacion. Un evento de auditoria puede referenciar un
`activation_event_id` cuando corresponde a una transicion real, pero
vive en un arbol de archivos completamente separado (`audit/`, nunca
`activation/`) con su propio lock y su propia cadena -- ver
`security/operational_audit_service.py`.

`audit_event_id` es determinista (mismo patron que
`compute_event_id` de Fase 14B: `\\x1f`-join + SHA-256, nunca
`random`/`hash()` nativo) sobre TODO campo salvo `occurred_at_utc` y
`diagnostics` -- el timestamp es deliberadamente informativo, nunca
parte de la identidad del evento (ver `docs/SECURITY_AUTHORIZATION_AND_
AUDIT.md`)."""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel
from .operational_authorization_request import OperationalAction
from .security_config import ApplicationRole, AuthenticationMode
from .unified_materialization_authorization import UnifiedMaterializationReasonCode

_MAX_ERROR_CODE_LENGTH = 200
_OCCURRED_AT_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$"


class AuditAction(StrEnum):
    LOGIN_CONTEXT_RESOLVED = "LOGIN_CONTEXT_RESOLVED"
    AUTHORIZATION_PREPARED = "AUTHORIZATION_PREPARED"
    AUTHORIZATION_REJECTED = "AUTHORIZATION_REJECTED"
    ACTIVATION_CANARY_REQUESTED = "ACTIVATION_CANARY_REQUESTED"
    ACTIVATION_CANARY_SUCCEEDED = "ACTIVATION_CANARY_SUCCEEDED"
    ACTIVATION_PRIMARY_REQUESTED = "ACTIVATION_PRIMARY_REQUESTED"
    ACTIVATION_PRIMARY_SUCCEEDED = "ACTIVATION_PRIMARY_SUCCEEDED"
    FALLBACK_REQUESTED = "FALLBACK_REQUESTED"
    FALLBACK_SUCCEEDED = "FALLBACK_SUCCEEDED"
    ROLLBACK_REQUESTED = "ROLLBACK_REQUESTED"
    ROLLBACK_SUCCEEDED = "ROLLBACK_SUCCEEDED"
    OPERATION_FAILED = "OPERATION_FAILED"
    ACCESS_DENIED = "ACCESS_DENIED"
    CSRF_REJECTED = "CSRF_REJECTED"


class AuditOutcome(StrEnum):
    ALLOWED = "ALLOWED"
    DENIED = "DENIED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


_ACTION_OUTCOME: dict[AuditAction, AuditOutcome] = {
    AuditAction.LOGIN_CONTEXT_RESOLVED: AuditOutcome.ALLOWED,
    AuditAction.AUTHORIZATION_PREPARED: AuditOutcome.ALLOWED,
    AuditAction.AUTHORIZATION_REJECTED: AuditOutcome.DENIED,
    AuditAction.ACTIVATION_CANARY_REQUESTED: AuditOutcome.ALLOWED,
    AuditAction.ACTIVATION_CANARY_SUCCEEDED: AuditOutcome.SUCCEEDED,
    AuditAction.ACTIVATION_PRIMARY_REQUESTED: AuditOutcome.ALLOWED,
    AuditAction.ACTIVATION_PRIMARY_SUCCEEDED: AuditOutcome.SUCCEEDED,
    AuditAction.FALLBACK_REQUESTED: AuditOutcome.ALLOWED,
    AuditAction.FALLBACK_SUCCEEDED: AuditOutcome.SUCCEEDED,
    AuditAction.ROLLBACK_REQUESTED: AuditOutcome.ALLOWED,
    AuditAction.ROLLBACK_SUCCEEDED: AuditOutcome.SUCCEEDED,
    AuditAction.OPERATION_FAILED: AuditOutcome.FAILED,
    AuditAction.ACCESS_DENIED: AuditOutcome.DENIED,
    AuditAction.CSRF_REJECTED: AuditOutcome.DENIED,
}

_ACTIONS_REQUIRING_OPERATIONAL_CONTEXT = frozenset(
    {
        AuditAction.AUTHORIZATION_PREPARED,
        AuditAction.AUTHORIZATION_REJECTED,
        AuditAction.ACTIVATION_CANARY_REQUESTED,
        AuditAction.ACTIVATION_CANARY_SUCCEEDED,
        AuditAction.ACTIVATION_PRIMARY_REQUESTED,
        AuditAction.ACTIVATION_PRIMARY_SUCCEEDED,
        AuditAction.FALLBACK_REQUESTED,
        AuditAction.FALLBACK_SUCCEEDED,
        AuditAction.ROLLBACK_REQUESTED,
        AuditAction.ROLLBACK_SUCCEEDED,
        AuditAction.OPERATION_FAILED,
    }
)

# OPERATION_FAILED exige operational_action pero reason_code es
# OPCIONAL: puede dispararse antes de que un reason_code llegue a
# establecerse (challenge invalido/expirado en `execute`) o despues
# (p. ej. pointer desactualizado con un challenge por lo demas valido,
# Fase 15B1 Parte 11) -- a diferencia de AUTHORIZATION_PREPARED/
# *_REQUESTED/*_SUCCEEDED, que solo existen cuando ya hubo un
# reason_code declarado y por lo tanto lo exigen siempre.
_ACTIONS_REQUIRING_REASON_CODE = _ACTIONS_REQUIRING_OPERATIONAL_CONTEXT - {
    AuditAction.OPERATION_FAILED
}
_ACTIONS_WITH_OPTIONAL_REASON_CODE = frozenset({AuditAction.OPERATION_FAILED})

_ACTIONS_REQUIRING_ACTIVATION_EVENT_ID = frozenset(
    {
        AuditAction.ACTIVATION_CANARY_SUCCEEDED,
        AuditAction.ACTIVATION_PRIMARY_SUCCEEDED,
        AuditAction.FALLBACK_SUCCEEDED,
        AuditAction.ROLLBACK_SUCCEEDED,
    }
)


def outcome_for_action(action: AuditAction) -> AuditOutcome:
    """Unico punto de verdad de la correspondencia 1:1 accion->outcome
    -- `security/operational_audit_service.py` la usa para construir
    eventos sin duplicar `_ACTION_OUTCOME`."""
    return _ACTION_OUTCOME[action]


def compute_audit_event_id(
    *,
    run_id: str,
    sequence: int,
    action: AuditAction,
    outcome: AuditOutcome,
    principal_id: str,
    correlation_id: str,
    previous_audit_event_id: str | None,
    operational_action: OperationalAction | None,
    activation_event_id: str | None,
    reason_code: UnifiedMaterializationReasonCode | None,
    error_code: str | None,
) -> str:
    """`audit_event_id` determinista -- EXCLUYE deliberadamente
    `occurred_at_utc` y `diagnostics` (ver docstring del modulo).
    NUNCA un timestamp, NUNCA `random`, NUNCA `hash()` nativo."""
    canonical = "\x1f".join(
        [
            run_id,
            str(sequence),
            action.value,
            outcome.value,
            principal_id,
            correlation_id,
            previous_audit_event_id or "",
            operational_action.value if operational_action is not None else "",
            activation_event_id or "",
            reason_code.value if reason_code is not None else "",
            error_code or "",
        ]
    )
    return f"audit-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


class OperationalAuditEvent(AltamiraBaseModel):
    """UN evento INMUTABLE de la cadena de auditoria operativa -- nunca
    se reescribe, nunca se borra. NUNCA contiene cookies, token CSRF,
    headers crudos, token de autorizacion, secreto de sesion, API key,
    password, codigo fuente ni manifest completo -- solo identificadores
    ya normalizados y codigos cerrados."""

    schema_version: Literal["1.0"] = "1.0"
    audit_event_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    previous_audit_event_id: str | None = Field(default=None, min_length=1)

    action: AuditAction
    outcome: AuditOutcome

    principal_id: str = Field(min_length=1)
    roles: list[ApplicationRole] = Field(default_factory=list)
    authentication_mode: AuthenticationMode
    correlation_id: str = Field(min_length=1)

    operational_action: OperationalAction | None = None
    activation_event_id: str | None = Field(default=None, min_length=1)
    reason_code: UnifiedMaterializationReasonCode | None = None
    error_code: str | None = Field(default=None, min_length=1, max_length=_MAX_ERROR_CODE_LENGTH)

    diagnostics: list[str] = Field(default_factory=list)

    # Informativo unicamente -- ver `compute_audit_event_id`: nunca
    # participa en `audit_event_id`. Debe provenir de un reloj
    # inyectable (Parte 9); los tests controlan el valor explicitamente.
    occurred_at_utc: str | None = Field(default=None, pattern=_OCCURRED_AT_PATTERN)

    @model_validator(mode="after")
    def _check_sequence_matches_previous_event_presence(self) -> OperationalAuditEvent:
        if self.sequence == 1 and self.previous_audit_event_id is not None:
            raise ValueError("sequence=1 (primer evento) no puede declarar previous_audit_event_id")
        if self.sequence > 1 and self.previous_audit_event_id is None:
            raise ValueError(f"sequence={self.sequence} exige previous_audit_event_id")
        return self

    @model_validator(mode="after")
    def _check_outcome_matches_action(self) -> OperationalAuditEvent:
        expected = _ACTION_OUTCOME[self.action]
        if self.outcome != expected:
            raise ValueError(f"action={self.action.value} exige outcome={expected.value}")
        return self

    @model_validator(mode="after")
    def _check_error_code_matches_outcome(self) -> OperationalAuditEvent:
        if self.outcome in (AuditOutcome.DENIED, AuditOutcome.FAILED):
            if self.error_code is None:
                raise ValueError(f"outcome={self.outcome.value} exige error_code")
        elif self.error_code is not None:
            raise ValueError(f"outcome={self.outcome.value} no puede declarar error_code")
        return self

    @model_validator(mode="after")
    def _check_error_code_has_no_control_characters(self) -> OperationalAuditEvent:
        if self.error_code is not None and any(
            ch in self.error_code for ch in ("\r", "\n", "\x00")
        ):
            raise ValueError("error_code no puede contener caracteres de control")
        return self

    @model_validator(mode="after")
    def _check_operational_context_scoped_to_action(self) -> OperationalAuditEvent:
        requires_operational_action = self.action in _ACTIONS_REQUIRING_OPERATIONAL_CONTEXT
        if requires_operational_action:
            if self.operational_action is None:
                raise ValueError(f"action={self.action.value} exige operational_action")
        elif self.operational_action is not None:
            raise ValueError(f"action={self.action.value} no puede declarar operational_action")

        if self.action in _ACTIONS_REQUIRING_REASON_CODE and self.reason_code is None:
            raise ValueError(f"action={self.action.value} exige reason_code")
        if (
            self.action not in _ACTIONS_REQUIRING_REASON_CODE
            and self.action not in _ACTIONS_WITH_OPTIONAL_REASON_CODE
            and self.reason_code is not None
        ):
            raise ValueError(f"action={self.action.value} no puede declarar reason_code")
        return self

    @model_validator(mode="after")
    def _check_activation_event_id_scoped_to_action(self) -> OperationalAuditEvent:
        requires_activation_event = self.action in _ACTIONS_REQUIRING_ACTIVATION_EVENT_ID
        if requires_activation_event and self.activation_event_id is None:
            raise ValueError(f"action={self.action.value} exige activation_event_id")
        if not requires_activation_event and self.activation_event_id is not None:
            raise ValueError(f"action={self.action.value} no puede declarar activation_event_id")
        return self

    @model_validator(mode="after")
    def _check_roles_unique(self) -> OperationalAuditEvent:
        if len(self.roles) != len(set(self.roles)):
            raise ValueError("roles no puede contener duplicados")
        return self

    @model_validator(mode="after")
    def _check_diagnostics_sorted_and_unique(self) -> OperationalAuditEvent:
        if self.diagnostics != sorted(set(self.diagnostics)):
            raise ValueError("diagnostics debe estar ordenado y sin duplicados")
        return self


class OperationalAuditActivePointer(AltamiraBaseModel):
    """`audit/active.json` -- commit point de la cadena de auditoria.
    Escrito atomicamente EN ULTIMO LUGAR, despues de persistir el
    evento en `audit/events/<audit_event_id>.json` -- un evento
    persistido sin que el puntero avance es un huerfano SEGURO (Parte
    10), nunca corrompe la cadena ni se repara automaticamente."""

    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    pointer_version: int = Field(ge=1)
    latest_audit_event_id: str = Field(min_length=1)
    event_count: int = Field(ge=1)

    @model_validator(mode="after")
    def _check_pointer_version_matches_event_count(self) -> OperationalAuditActivePointer:
        if self.pointer_version != self.event_count:
            raise ValueError("pointer_version debe coincidir con event_count")
        return self


__all__ = [
    "AuditAction",
    "AuditOutcome",
    "OperationalAuditActivePointer",
    "OperationalAuditEvent",
    "compute_audit_event_id",
    "outcome_for_action",
]
