"""Carga de `ObservabilityConfig` desde `Settings.observability_config_path`
(Fase 15B2-B).

Mismo idioma `LOADED/MISSING/INVALID` que
`security/security_config_loader.py`, con una diferencia deliberada:
aqui el resultado NUNCA activa un gate global de fail-closed. Si el
archivo esta ausente o es invalido, el llamador simplemente usa
`default_observability_config()` (logging JSON activo, metricas
deshabilitadas) y reporta el `outcome` unicamente para diagnostico
(`/api/operations/component-diagnostics`)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ..contracts.observability import ObservabilityConfig, default_observability_config
from ..pipeline.errors import SemanticConfigError
from ..pipeline.yaml_utils import read_yaml_config


class ObservabilityConfigOutcome(StrEnum):
    LOADED = "LOADED"
    MISSING = "MISSING"
    INVALID = "INVALID"


@dataclass(frozen=True)
class ObservabilityConfigLoadResult:
    """`config` siempre tiene un valor utilizable (el default seguro
    cuando `outcome != LOADED`) -- a diferencia de
    `SecurityConfigLoadResult`, aqui el llamador nunca necesita decidir
    un fallback el mismo. `error` es un mensaje sanitizado (nunca la
    ruta absoluta ni el contenido crudo del YAML), presente unicamente
    cuando `outcome=INVALID`."""

    outcome: ObservabilityConfigOutcome
    config: ObservabilityConfig
    error: str | None = None


def load_observability_config(path: Path) -> ObservabilityConfigLoadResult:
    """Nunca lee `.env`. Nunca lanza: cualquier fallo (archivo ausente,
    YAML invalido, schema incompatible) resuelve en el default seguro
    con `outcome` distinto de `LOADED`."""
    if path.is_symlink() or not path.is_file():
        return ObservabilityConfigLoadResult(
            outcome=ObservabilityConfigOutcome.MISSING, config=default_observability_config()
        )
    try:
        document, _hash = read_yaml_config(path)
    except SemanticConfigError as exc:
        return ObservabilityConfigLoadResult(
            outcome=ObservabilityConfigOutcome.INVALID,
            config=default_observability_config(),
            error=f"config/observability.yaml no se pudo leer o parsear como YAML: {exc}",
        )
    try:
        config = ObservabilityConfig.model_validate(document)
    except ValueError as exc:
        return ObservabilityConfigLoadResult(
            outcome=ObservabilityConfigOutcome.INVALID,
            config=default_observability_config(),
            error=f"config/observability.yaml no cumple el schema de ObservabilityConfig: {exc}",
        )
    return ObservabilityConfigLoadResult(outcome=ObservabilityConfigOutcome.LOADED, config=config)


__all__ = [
    "ObservabilityConfigLoadResult",
    "ObservabilityConfigOutcome",
    "load_observability_config",
]
