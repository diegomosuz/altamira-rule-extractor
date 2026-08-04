"""Configuracion tipada del control plane de activacion unificada
(Fase 14A de la ampliacion semantica,
`feat/controlled-unified-activation`).

Esta fase es EXCLUSIVAMENTE control plane y dry-run: lee una
configuracion explicita provista por un operador (YAML externo, NUNCA
copiado al repositorio ni al directorio del run -- ver
`pipeline/unified_activation_service.py`), determina el modo
operativo solicitado, selecciona canary de forma deterministica,
compara el pipeline V1 con el downstream unified shadow (Fase 13), y
produce una evaluacion de activacion -- pero NUNCA selecciona
`unified` como lane efectivo, NUNCA materializa candidatos/drafts/
reglas, NUNCA inicializa un proveedor real. Esa decision productiva
(Fase 14B) permanece completamente fuera de alcance.

`materialization_enabled: Literal[False]` es una invariante de TIPO,
no solo de valor por defecto: un YAML que intente `materialization_
enabled: true` falla la validacion Pydantic en el borde (nunca llega a
construirse un `UnifiedActivationConfig` con ese valor) -- ningun
codigo posterior puede "olvidar" verificarlo porque el tipo mismo lo
excluye."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, Sha256Hex


class UnifiedActivationMode(StrEnum):
    """Modo operativo SOLICITADO -- nunca implica, por si solo, que
    `unified` se convierta en lane efectivo (ver `pipeline/
    unified_activation_policy.py`: en Fase 14A el lane efectivo es
    SIEMPRE V1, sin excepcion, para los cuatro modos)."""

    V1_ONLY = "V1_ONLY"
    SHADOW_COMPARE = "SHADOW_COMPARE"
    UNIFIED_CANARY = "UNIFIED_CANARY"
    UNIFIED_PRIMARY_WITH_V1_FALLBACK = "UNIFIED_PRIMARY_WITH_V1_FALLBACK"


class UnifiedActivationProviderPolicy(StrEnum):
    """Catalogo de politicas de proveedor. En Fase 14A UNICAMENTE
    `DETERMINISTIC_FAKE_ONLY` es aceptado -- `PRODUCT_PROVIDER_
    EXPLICITLY_AUTHORIZED` existe en el enum para que el contrato sea
    estable hacia una fase futura, pero un `UnifiedActivationConfig`
    con ese valor SIEMPRE falla la validacion en esta fase (ver
    `_check_provider_policy_is_fake_only`)."""

    DETERMINISTIC_FAKE_ONLY = "DETERMINISTIC_FAKE_ONLY"
    PRODUCT_PROVIDER_EXPLICITLY_AUTHORIZED = "PRODUCT_PROVIDER_EXPLICITLY_AUTHORIZED"


class UnifiedCanarySelectionStrategy(StrEnum):
    EXPLICIT_ALLOWLIST = "EXPLICIT_ALLOWLIST"
    PACKAGE_HASH_BUCKET = "PACKAGE_HASH_BUCKET"
    ALLOWLIST_OR_HASH_BUCKET = "ALLOWLIST_OR_HASH_BUCKET"


class UnifiedFallbackPolicy(StrEnum):
    NO_FALLBACK = "NO_FALLBACK"
    FALLBACK_TO_V1 = "FALLBACK_TO_V1"
    FAIL_CLOSED = "FAIL_CLOSED"


def _ordered_unique(values: list[str]) -> list[str]:
    return sorted(set(values))


class UnifiedActivationConfig(AltamiraBaseModel):
    """Configuracion explicita de activacion -- provista por un
    operador via YAML externo (`--config`), NUNCA generada por codigo,
    NUNCA copiada al repositorio ni al run. Sin rutas, claves,
    endpoint ni modelo: ese tipo de configuracion pertenece
    exclusivamente a `Settings`/variables de entorno productivas,
    jamas a este contrato."""

    schema_version: Literal["1.0"] = "1.0"
    mode: UnifiedActivationMode
    provider_policy: UnifiedActivationProviderPolicy = (
        UnifiedActivationProviderPolicy.DETERMINISTIC_FAKE_ONLY
    )
    canary_strategy: UnifiedCanarySelectionStrategy = (
        UnifiedCanarySelectionStrategy.EXPLICIT_ALLOWLIST
    )
    canary_percentage: int = Field(default=0, ge=0, le=100)
    # Un hash puede aparecer en AMBAS listas simultaneamente -- no es un
    # error de configuracion, es la forma de demostrar que la denylist
    # prevalece en tiempo de ejecucion incluso sobre una inclusion
    # explicita (ver `pipeline/unified_activation_canary_selector.py`,
    # orden de precedencia: denylist -> allowlist -> bucket -> ninguna).
    package_hash_allowlist: list[Sha256Hex] = Field(default_factory=list)
    package_hash_denylist: list[Sha256Hex] = Field(default_factory=list)
    fallback_policy: UnifiedFallbackPolicy = UnifiedFallbackPolicy.NO_FALLBACK
    require_validation_disposition: bool = True
    require_downstream_disposition: bool = True
    require_all_guardrails_passed: bool = True
    allow_completed_with_rejections: bool = False
    comparison_required: bool = True
    materialization_enabled: Literal[False] = False
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_provider_policy_is_fake_only(self) -> UnifiedActivationConfig:
        if self.provider_policy != UnifiedActivationProviderPolicy.DETERMINISTIC_FAKE_ONLY:
            raise ValueError(
                "provider_policy debe ser DETERMINISTIC_FAKE_ONLY en Fase 14A -- "
                "ningun proveedor real esta autorizado todavia"
            )
        return self

    @model_validator(mode="after")
    def _check_hash_lists_normalized_and_unique(self) -> UnifiedActivationConfig:
        for field_name in ("package_hash_allowlist", "package_hash_denylist"):
            values = getattr(self, field_name)
            if values != _ordered_unique(values):
                raise ValueError(
                    f"{field_name} debe estar ordenado, en minusculas y sin duplicados"
                )
        return self

    @model_validator(mode="after")
    def _check_canary_modes_require_fallback_to_v1(self) -> UnifiedActivationConfig:
        canary_modes = (
            UnifiedActivationMode.UNIFIED_CANARY,
            UnifiedActivationMode.UNIFIED_PRIMARY_WITH_V1_FALLBACK,
        )
        if (
            self.mode in canary_modes
            and self.fallback_policy != UnifiedFallbackPolicy.FALLBACK_TO_V1
        ):
            raise ValueError(
                f"mode={self.mode.value} exige fallback_policy=FALLBACK_TO_V1 -- "
                f"un canary o primary trial sin fallback planificado nunca es "
                f"una configuracion valida"
            )
        return self

    @model_validator(mode="after")
    def _check_diagnostics_sorted_and_unique(self) -> UnifiedActivationConfig:
        if self.diagnostics != _ordered_unique(self.diagnostics):
            raise ValueError("diagnostics debe estar ordenado alfabeticamente y sin duplicados")
        return self


__all__ = [
    "UnifiedActivationConfig",
    "UnifiedActivationMode",
    "UnifiedActivationProviderPolicy",
    "UnifiedCanarySelectionStrategy",
    "UnifiedFallbackPolicy",
]
