"""Registro de consumo de challenges operativos (Fase 15B1, cierre
"single-use real").

El challenge stateless firmado (`security/operational_challenge.py`)
sigue conteniendo la intencion completa (accion, revisor, pointer hash
esperado, grupos aprobados) y sigue siendo la fuente de verdad de QUE
se autorizo -- este registro NO lo reemplaza, solo agrega la garantia
STATEFUL que un diseno puramente stateless no puede dar por si solo:
que un challenge ya usado (con exito, con fallo, o en una transicion
idempotente cuyo `pointer_version` no cambia) nunca se vuelva a
aceptar. El hash del pointer activo (Fase 14B/15B1 ya existente) sigue
siendo una proteccion INDEPENDIENTE contra lost updates -- ninguna de
las dos reemplaza a la otra."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import AltamiraBaseModel, Sha256Hex
from .operational_authorization_request import OperationalAction

_OCCURRED_AT_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$"


class ConsumedChallengeRecord(AltamiraBaseModel):
    """UN registro INMUTABLE de consumo -- nunca se reescribe, nunca se
    borra. NUNCA contiene el token del challenge, el token CSRF, ni la
    cookie de sesion: solo `challenge_hash` (SHA-256 del token
    canonico completo tal como viaja en el formulario), suficiente
    para detectar un replay sin poder reconstruir el secreto original."""

    schema_version: Literal["1.0"] = "1.0"
    challenge_hash: Sha256Hex
    run_id: str = Field(min_length=1)
    principal_id: str = Field(min_length=1)
    operational_action: OperationalAction
    consumed_at_utc: str = Field(pattern=_OCCURRED_AT_PATTERN)


__all__ = ["ConsumedChallengeRecord"]
