"""Selector PURO y deterministico de canary (Fase 14A Parte 3,
`feat/controlled-unified-activation`).

La decision depende UNICAMENTE de `source_package_hash` (nunca de
`run_id`, nunca de la hora, nunca de `random`/`hash()` nativo de
Python) -- el mismo paquete produce SIEMPRE la misma decision, sin
importar cuantas veces se re-ingiera ni bajo que `run_id`. `run_id` se
acepta como parametro de entrada unicamente para trazabilidad/auditoria
(queda disponible para el llamador), pero NUNCA participa en el calculo
de la seleccion.

Conversion a bucket (documentada, estable): `bucket = int(sha256
(source_package_hash).hexdigest()[:8], 16) % 100` -- un entero en
`[0, 99]`. `canary_percentage=0` nunca selecciona nada por bucket
(`bucket < 0` es siempre falso); `canary_percentage=100` selecciona
TODO por bucket (`bucket < 100` es siempre verdadero, `bucket` esta
acotado a `[0, 99]`).

La denylist SIEMPRE excluye, sin importar la estrategia configurada
-- se evalua primero, antes de considerar allowlist o bucket."""

from __future__ import annotations

import hashlib

from ..contracts.unified_activation_config import (
    UnifiedActivationConfig,
    UnifiedCanarySelectionStrategy,
)
from ..contracts.unified_activation_evaluation import UnifiedActivationCanarySelection

REASON_DENYLISTED = "denylisted"
REASON_ALLOWLISTED = "allowlisted"
REASON_BUCKET_SELECTED = "bucket_selected"
REASON_BUCKET_NOT_SELECTED = "bucket_not_selected"
REASON_NOT_ALLOWLISTED = "not_allowlisted"
REASON_STRATEGY_NEVER_SELECTS = "strategy_never_selects_without_match"


def _bucket_for(source_package_hash: str) -> int:
    """`sha256(source_package_hash)` (digest criptografico estable,
    NUNCA `hash()` nativo de Python, cuyo valor varia entre procesos)
    -- los primeros 8 caracteres hex del digest, interpretados como
    entero base-16, modulo 100."""
    digest = hashlib.sha256(source_package_hash.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 100


def select_canary(
    config: UnifiedActivationConfig,
    *,
    source_package_hash: str,
    run_id: str,  # noqa: ARG001 - aceptado por trazabilidad, nunca usado en el calculo
) -> UnifiedActivationCanarySelection:
    """Punto de entrada puro. Nunca muta `config`. La denylist siempre
    excluye primero; luego, segun `config.canary_strategy`, se evalua
    allowlist explicita y/o bucket derivado del hash.

    Un `source_package_hash` puede aparecer simultaneamente en
    `package_hash_allowlist` y `package_hash_denylist` (el contrato lo
    permite deliberadamente, ver `contracts/unified_activation_
    config.py`): en ese caso `matched_allowlist=True` Y
    `matched_denylist=True` se reportan ambos (la pertenencia real a
    cada lista se preserva, para que la evaluacion sea auditable), pero
    `selected` es SIEMPRE `False` -- la denylist prevalece sin
    excepcion, sin importar la estrategia configurada."""
    allowlisted = source_package_hash in config.package_hash_allowlist
    denylisted = source_package_hash in config.package_hash_denylist
    if denylisted:
        return UnifiedActivationCanarySelection(
            selected=False,
            bucket=None,
            reason=REASON_DENYLISTED,
            matched_allowlist=allowlisted,
            matched_denylist=True,
        )

    strategy = config.canary_strategy
    uses_allowlist = strategy in (
        UnifiedCanarySelectionStrategy.EXPLICIT_ALLOWLIST,
        UnifiedCanarySelectionStrategy.ALLOWLIST_OR_HASH_BUCKET,
    )
    uses_bucket = strategy in (
        UnifiedCanarySelectionStrategy.PACKAGE_HASH_BUCKET,
        UnifiedCanarySelectionStrategy.ALLOWLIST_OR_HASH_BUCKET,
    )

    matched_allowlist = uses_allowlist and allowlisted

    bucket: int | None = None
    bucket_selected = False
    if uses_bucket:
        bucket = _bucket_for(source_package_hash)
        bucket_selected = bucket < config.canary_percentage

    if matched_allowlist:
        return UnifiedActivationCanarySelection(
            selected=True,
            bucket=bucket,
            reason=REASON_ALLOWLISTED,
            matched_allowlist=True,
            matched_denylist=False,
        )

    if uses_bucket and bucket_selected:
        return UnifiedActivationCanarySelection(
            selected=True,
            bucket=bucket,
            reason=REASON_BUCKET_SELECTED,
            matched_allowlist=False,
            matched_denylist=False,
        )

    if uses_bucket:
        reason = REASON_BUCKET_NOT_SELECTED
    elif uses_allowlist:
        reason = REASON_NOT_ALLOWLISTED
    else:
        reason = REASON_STRATEGY_NEVER_SELECTS

    return UnifiedActivationCanarySelection(
        selected=False,
        bucket=bucket,
        reason=reason,
        matched_allowlist=False,
        matched_denylist=False,
    )


__all__ = ["select_canary"]
