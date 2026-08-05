"""Intencion operativa (Fase 15B1, `feat/final-hardening-release`).

`OperationalAuthorizationRequest` representa lo que un principal
autenticado y autorizado QUIERE hacer, construido por el workflow de
`security/operational_workflow.py` a partir de: (1) el `AuthenticatedPrincipal`
de la request (nunca de un campo de formulario -- `operator_principal_id`/
`reviewer_principal_id` se derivan del contexto de sesion/autenticacion);
(2) datos ya validados contra el estado real del run (`expected_active_
pointer_hash`, `approved_group_ids` provienen de la evaluacion/seleccion
vigente, nunca de texto libre). NO es todavia una autorizacion ejecutable
de Fase 14B: el workflow la convierte en `UnifiedMaterializationAuthorization`
solo tras superar el paso `confirm` (ver Parte 8).

Deliberadamente sin rutas, sin secretos y sin timestamps -- eso vive en
`OperationalAuditEvent`/el sistema de archivos, nunca en este contrato
de intencion."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, Sha256Hex
from .unified_materialization_authorization import UnifiedMaterializationReasonCode


class OperationalAction(StrEnum):
    """Subconjunto de `UnifiedMaterializationAction` que puede
    solicitarse desde la UI de gobierno -- `KEEP_V1` se excluye
    deliberadamente: no es una accion que un operador "solicite", es la
    ausencia de accion (el lane activo simplemente no cambia)."""

    ACTIVATE_UNIFIED_CANARY = "ACTIVATE_UNIFIED_CANARY"
    ACTIVATE_UNIFIED_PRIMARY = "ACTIVATE_UNIFIED_PRIMARY"
    FALLBACK_TO_V1 = "FALLBACK_TO_V1"
    ROLLBACK_TO_PREVIOUS = "ROLLBACK_TO_PREVIOUS"
    ROLLBACK_TO_GENERATION = "ROLLBACK_TO_GENERATION"


_GROUP_MATERIALIZING_ACTIONS = frozenset(
    {OperationalAction.ACTIVATE_UNIFIED_CANARY, OperationalAction.ACTIVATE_UNIFIED_PRIMARY}
)

_MAX_REVIEW_REFERENCE_LENGTH = 500


def _ordered_unique(values: list[str]) -> list[str]:
    return sorted(set(values))


class OperationalAuthorizationRequest(AltamiraBaseModel):
    """Intencion operativa validada -- todavia no ejecuta ninguna
    transicion. `operator_principal_id`/`reviewer_principal_id` deben
    construirse SIEMPRE desde `AuthenticatedPrincipal.principal_id`
    (nunca desde un campo de formulario: un formulario no puede influir
    quien es el operador ni el revisor)."""

    schema_version: str = Field(default="1.0", pattern=r"^1\.0$")
    run_id: str = Field(min_length=1)
    action: OperationalAction

    operator_principal_id: str = Field(min_length=1)
    distinct_reviewer_required: bool
    reviewer_principal_id: str | None = Field(default=None, min_length=1)

    expected_active_pointer_hash: Sha256Hex
    target_generation_id: str | None = Field(default=None, min_length=1)

    reason_code: UnifiedMaterializationReasonCode
    review_reference: str = Field(min_length=1, max_length=_MAX_REVIEW_REFERENCE_LENGTH)
    approved_group_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_review_reference_has_no_control_characters(
        self,
    ) -> OperationalAuthorizationRequest:
        if any(ch in self.review_reference for ch in ("\r", "\n", "\x00")):
            raise ValueError("review_reference no puede contener caracteres de control")
        if self.review_reference.strip() != self.review_reference:
            raise ValueError("review_reference no puede tener espacios en los bordes")
        return self

    @model_validator(mode="after")
    def _check_rollback_to_generation_requires_target(self) -> OperationalAuthorizationRequest:
        if (
            self.action == OperationalAction.ROLLBACK_TO_GENERATION
            and self.target_generation_id is None
        ):
            raise ValueError("action=ROLLBACK_TO_GENERATION exige target_generation_id")
        if (
            self.action != OperationalAction.ROLLBACK_TO_GENERATION
            and self.target_generation_id is not None
        ):
            raise ValueError(
                "target_generation_id solo es valido con action=ROLLBACK_TO_GENERATION"
            )
        return self

    @model_validator(mode="after")
    def _check_approved_group_ids_scoped_to_materializing_actions(
        self,
    ) -> OperationalAuthorizationRequest:
        if self.action not in _GROUP_MATERIALIZING_ACTIONS and self.approved_group_ids:
            raise ValueError(
                f"action={self.action.value} no materializa unified -- "
                "approved_group_ids debe estar vacio"
            )
        return self

    @model_validator(mode="after")
    def _check_approved_group_ids_sorted_and_unique(self) -> OperationalAuthorizationRequest:
        if self.approved_group_ids != _ordered_unique(self.approved_group_ids):
            raise ValueError("approved_group_ids debe estar ordenado y sin duplicados")
        return self

    @model_validator(mode="after")
    def _check_distinct_reviewer_invariant(self) -> OperationalAuthorizationRequest:
        """Invariante ESTRUCTURAL de defensa en profundidad -- la
        verificacion autoritativa (que el revisor provenga de una
        sesion distinta, autenticada de forma independiente) vive en el
        workflow (Parte 13), no puede resolverse solo con este
        contrato. Aqui unicamente se garantiza que el dato declarado es
        internamente consistente."""
        if self.distinct_reviewer_required:
            if self.reviewer_principal_id is None:
                raise ValueError("distinct_reviewer_required=true exige reviewer_principal_id")
            if (
                self.reviewer_principal_id.strip().lower()
                == self.operator_principal_id.strip().lower()
            ):
                raise ValueError(
                    "reviewer_principal_id no puede coincidir (normalizado) con "
                    "operator_principal_id cuando distinct_reviewer_required=true"
                )
        elif self.reviewer_principal_id is not None:
            raise ValueError(
                "reviewer_principal_id solo es valido cuando distinct_reviewer_required=true"
            )
        return self


class PreparedOperationalIntent(AltamiraBaseModel):
    """Payload embebido en el challenge firmado que produce el paso
    `prepare` (Parte 8) -- deliberadamente SIN `operator_principal_id`:
    quien ejecuta (`confirm`/`execute`) se resuelve recien en ESE paso,
    desde la identidad autenticada de esa request, nunca desde este
    payload preparado antes. `prepared_by_principal_id` es quien llamo
    a `prepare`; cuando `distinct_reviewer_required=true`, se convierte
    en `reviewer_principal_id` al construir el
    `OperationalAuthorizationRequest` final en `execute`
    (`to_authorization_request`), y ese paso revalida que sea distinto
    del operador real (Parte 13)."""

    schema_version: str = Field(default="1.0", pattern=r"^1\.0$")
    run_id: str = Field(min_length=1)
    action: OperationalAction
    prepared_by_principal_id: str = Field(min_length=1)
    distinct_reviewer_required: bool
    expected_active_pointer_hash: Sha256Hex
    target_generation_id: str | None = Field(default=None, min_length=1)
    reason_code: UnifiedMaterializationReasonCode
    review_reference: str = Field(min_length=1, max_length=_MAX_REVIEW_REFERENCE_LENGTH)
    approved_group_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_review_reference_has_no_control_characters(self) -> PreparedOperationalIntent:
        if any(ch in self.review_reference for ch in ("\r", "\n", "\x00")):
            raise ValueError("review_reference no puede contener caracteres de control")
        if self.review_reference.strip() != self.review_reference:
            raise ValueError("review_reference no puede tener espacios en los bordes")
        return self

    @model_validator(mode="after")
    def _check_rollback_to_generation_requires_target(self) -> PreparedOperationalIntent:
        if (
            self.action == OperationalAction.ROLLBACK_TO_GENERATION
            and self.target_generation_id is None
        ):
            raise ValueError("action=ROLLBACK_TO_GENERATION exige target_generation_id")
        if (
            self.action != OperationalAction.ROLLBACK_TO_GENERATION
            and self.target_generation_id is not None
        ):
            raise ValueError(
                "target_generation_id solo es valido con action=ROLLBACK_TO_GENERATION"
            )
        return self

    @model_validator(mode="after")
    def _check_approved_group_ids_scoped_to_materializing_actions(
        self,
    ) -> PreparedOperationalIntent:
        if self.action not in _GROUP_MATERIALIZING_ACTIONS and self.approved_group_ids:
            raise ValueError(
                f"action={self.action.value} no materializa unified -- "
                "approved_group_ids debe estar vacio"
            )
        return self

    @model_validator(mode="after")
    def _check_approved_group_ids_sorted_and_unique(self) -> PreparedOperationalIntent:
        if self.approved_group_ids != _ordered_unique(self.approved_group_ids):
            raise ValueError("approved_group_ids debe estar ordenado y sin duplicados")
        return self

    def to_authorization_request(
        self, operator_principal_id: str
    ) -> OperationalAuthorizationRequest:
        """Construye la intencion FINAL en `execute` -- `operator_
        principal_id` proviene SIEMPRE de la identidad autenticada de
        la request de ejecucion, nunca de un campo de formulario."""
        return OperationalAuthorizationRequest(
            run_id=self.run_id,
            action=self.action,
            operator_principal_id=operator_principal_id,
            distinct_reviewer_required=self.distinct_reviewer_required,
            reviewer_principal_id=(
                self.prepared_by_principal_id if self.distinct_reviewer_required else None
            ),
            expected_active_pointer_hash=self.expected_active_pointer_hash,
            target_generation_id=self.target_generation_id,
            reason_code=self.reason_code,
            review_reference=self.review_reference,
            approved_group_ids=self.approved_group_ids,
        )


__all__ = [
    "OperationalAction",
    "OperationalAuthorizationRequest",
    "PreparedOperationalIntent",
]
