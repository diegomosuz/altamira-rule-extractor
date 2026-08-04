"""Autorizacion tipada de materializacion (Fase 14B de la ampliacion
semantica, `feat/controlled-unified-materialization`).

Un `UnifiedMaterializationAuthorization` es la UNICA entrada humana que
`pipeline/unified_materialization_service.py` acepta -- provista por un
operador via YAML EXTERNO (`--authorization`), NUNCA generada por
codigo, NUNCA copiada al repositorio ni al directorio del run (mismo
principio que `UnifiedActivationConfig`, Fase 14A). `materialization_
enabled: Literal[True]` es una invariante de TIPO: solo este contrato
(nunca `UnifiedActivationConfig`, cuyo `materialization_enabled` es
`Literal[False]`) puede autorizar una materializacion real.

Limitacion de autenticidad (documentada explicitamente, ver Fase 15):
`review_reference` es una referencia auditiva DECLARATIVA -- un texto
libre que el operador escribe para dejar rastro de quien/que aprobo la
accion -- NUNCA una prueba de identidad autenticada, NUNCA una firma
criptografica. Esta fase no verifica que quien redacto el YAML sea
realmente esa persona/proceso; solo registra lo declarado, de forma
auditable."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, Sha256Hex
from .unified_activation_evaluation import UnifiedActivationReadinessDisposition


class UnifiedMaterializationAction(StrEnum):
    """Accion solicitada -- validada contra el estado real por
    `pipeline/unified_materialization_service.py`, nunca ejecutada a
    ciegas."""

    KEEP_V1 = "KEEP_V1"
    ACTIVATE_UNIFIED_CANARY = "ACTIVATE_UNIFIED_CANARY"
    ACTIVATE_UNIFIED_PRIMARY = "ACTIVATE_UNIFIED_PRIMARY"
    FALLBACK_TO_V1 = "FALLBACK_TO_V1"
    ROLLBACK_TO_PREVIOUS = "ROLLBACK_TO_PREVIOUS"
    ROLLBACK_TO_GENERATION = "ROLLBACK_TO_GENERATION"


_UNIFIED_MATERIALIZING_ACTIONS = frozenset(
    {
        UnifiedMaterializationAction.ACTIVATE_UNIFIED_CANARY,
        UnifiedMaterializationAction.ACTIVATE_UNIFIED_PRIMARY,
    }
)
_ROLLBACK_ACTIONS = frozenset(
    {
        UnifiedMaterializationAction.ROLLBACK_TO_PREVIOUS,
        UnifiedMaterializationAction.ROLLBACK_TO_GENERATION,
    }
)


class UnifiedMaterializationReasonCode(StrEnum):
    CANARY_APPROVED = "CANARY_APPROVED"
    PRIMARY_TRIAL_APPROVED = "PRIMARY_TRIAL_APPROVED"
    ACTIVE_GENERATION_INVALID = "ACTIVE_GENERATION_INVALID"
    GUARDRAIL_FAILURE = "GUARDRAIL_FAILURE"
    INTEGRITY_FAILURE = "INTEGRITY_FAILURE"
    OPERATOR_ROLLBACK = "OPERATOR_ROLLBACK"
    OPERATIONAL_INCIDENT = "OPERATIONAL_INCIDENT"
    VALIDATION_REVOKED = "VALIDATION_REVOKED"
    KEEP_BASELINE = "KEEP_BASELINE"
    OTHER_REVIEWED_REASON = "OTHER_REVIEWED_REASON"


def _ordered_unique(values: list[str]) -> list[str]:
    return sorted(set(values))


class UnifiedMaterializationAuthorization(AltamiraBaseModel):
    """Autorizacion explicita de una transicion de materializacion --
    provista por un operador via YAML externo (`--authorization`),
    NUNCA generada por codigo, NUNCA copiada al repositorio ni al run.
    Sin rutas, claves, endpoint ni modelo: esa configuracion pertenece
    exclusivamente a `Settings`/variables de entorno productivas, jamas
    a este contrato."""

    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    activation_evaluation_hash: Sha256Hex
    expected_readiness_disposition: UnifiedActivationReadinessDisposition
    action: UnifiedMaterializationAction
    target_generation_id: str | None = Field(default=None, min_length=1)
    expected_active_pointer_hash: Sha256Hex | None = None
    reason_code: UnifiedMaterializationReasonCode
    # Referencia auditiva DECLARATIVA (ver docstring del modulo) --
    # NUNCA una prueba de identidad autenticada.
    review_reference: str = Field(min_length=1)
    approved_group_ids: list[str] = Field(default_factory=list)
    fallback_authorized: bool = False
    rollback_authorized: bool = False
    provider_policy: Literal["DETERMINISTIC_FAKE_ONLY"] = "DETERMINISTIC_FAKE_ONLY"
    materialization_enabled: Literal[True] = True
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_canary_requires_matching_readiness(self) -> UnifiedMaterializationAuthorization:
        if (
            self.action == UnifiedMaterializationAction.ACTIVATE_UNIFIED_CANARY
            and self.expected_readiness_disposition
            != UnifiedActivationReadinessDisposition.READY_FOR_UNIFIED_CANARY
        ):
            raise ValueError(
                "action=ACTIVATE_UNIFIED_CANARY exige expected_readiness_disposition="
                "READY_FOR_UNIFIED_CANARY"
            )
        return self

    @model_validator(mode="after")
    def _check_primary_requires_matching_readiness(self) -> UnifiedMaterializationAuthorization:
        if (
            self.action == UnifiedMaterializationAction.ACTIVATE_UNIFIED_PRIMARY
            and self.expected_readiness_disposition
            != UnifiedActivationReadinessDisposition.READY_FOR_PRIMARY_TRIAL
        ):
            raise ValueError(
                "action=ACTIVATE_UNIFIED_PRIMARY exige expected_readiness_disposition="
                "READY_FOR_PRIMARY_TRIAL"
            )
        return self

    @model_validator(mode="after")
    def _check_fallback_requires_authorization_flag(self) -> UnifiedMaterializationAuthorization:
        is_fallback = self.action == UnifiedMaterializationAction.FALLBACK_TO_V1
        if is_fallback and not self.fallback_authorized:
            raise ValueError("action=FALLBACK_TO_V1 exige fallback_authorized=true")
        return self

    @model_validator(mode="after")
    def _check_rollback_requires_authorization_flag(self) -> UnifiedMaterializationAuthorization:
        if self.action in _ROLLBACK_ACTIONS and not self.rollback_authorized:
            raise ValueError(f"action={self.action.value} exige rollback_authorized=true")
        return self

    @model_validator(mode="after")
    def _check_rollback_to_generation_requires_target(self) -> UnifiedMaterializationAuthorization:
        if (
            self.action == UnifiedMaterializationAction.ROLLBACK_TO_GENERATION
            and self.target_generation_id is None
        ):
            raise ValueError("action=ROLLBACK_TO_GENERATION exige target_generation_id")
        if (
            self.action != UnifiedMaterializationAction.ROLLBACK_TO_GENERATION
            and self.target_generation_id is not None
        ):
            raise ValueError(
                "target_generation_id solo es valido con action=ROLLBACK_TO_GENERATION"
            )
        return self

    @model_validator(mode="after")
    def _check_keep_v1_never_activates_unified(self) -> UnifiedMaterializationAuthorization:
        """KEEP_V1 nunca puede activar (ni, transitivamente, aprobar
        grupos para) unified -- esta autorizacion mantiene el lane
        activo actual sin materializar nada nuevo."""
        if self.action == UnifiedMaterializationAction.KEEP_V1 and self.approved_group_ids:
            raise ValueError("action=KEEP_V1 no puede declarar approved_group_ids")
        return self

    @model_validator(mode="after")
    def _check_approved_group_ids_scoped_to_unified_materialization(
        self,
    ) -> UnifiedMaterializationAuthorization:
        """`approved_group_ids` UNICAMENTE tiene sentido cuando esta
        autorizacion materializa unified (canary/primary) -- fallback
        y rollback nunca re-materializan un conjunto de grupos nuevo,
        reutilizan una generacion ya construida."""
        if self.action not in _UNIFIED_MATERIALIZING_ACTIONS and self.approved_group_ids:
            raise ValueError(
                f"action={self.action.value} no materializa unified -- "
                "approved_group_ids debe estar vacio"
            )
        return self

    @model_validator(mode="after")
    def _check_lists_sorted_and_unique(self) -> UnifiedMaterializationAuthorization:
        for field_name in ("approved_group_ids", "diagnostics"):
            values = getattr(self, field_name)
            if values != _ordered_unique(values):
                raise ValueError(f"{field_name} debe estar ordenado y sin duplicados")
        return self


__all__ = [
    "UnifiedMaterializationAction",
    "UnifiedMaterializationAuthorization",
    "UnifiedMaterializationReasonCode",
]
