"""Carga de `SecurityConfig` desde `Settings.security_config_path`
(Fase 15B1, cierre "DISABLED_DEV explicito").

La AUSENCIA del archivo, o un archivo presente pero invalido, YA NO
resuelve en silencio a `DISABLED_DEV` -- ese fallback implicito era el
defecto real que este cierre corrige (ver `SecurityConfigOutcome`):
`DISABLED_DEV` SOLO se activa cuando el archivo lo declara
EXPLICITAMENTE (`authentication_mode: DISABLED_DEV`). Sin config, o con
config invalida, la aplicacion arranca en modo `SECURITY_MISCONFIGURED`
(fail-closed): `/health` sigue informando el proceso vivo, `/ready`
falla, y ningun principal (ni siquiera el anonimo de `DISABLED_DEV`) se
construye (`security/fastapi_deps.py`)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ..contracts.security_config import SecurityConfig
from ..pipeline.errors import SemanticConfigError
from ..pipeline.yaml_utils import read_yaml_config


class SecurityConfigOutcome(StrEnum):
    LOADED = "LOADED"
    MISSING = "MISSING"
    INVALID = "INVALID"


@dataclass(frozen=True)
class SecurityConfigLoadResult:
    """`config` es `None` a menos que `outcome=LOADED`. `error` es un
    mensaje sanitizado (nunca incluye la ruta absoluta ni el contenido
    crudo del YAML) presente unicamente cuando `outcome=INVALID`."""

    outcome: SecurityConfigOutcome
    config: SecurityConfig | None
    error: str | None = None


def load_security_config(path: Path) -> SecurityConfigLoadResult:
    """Nunca lee `.env`. Nunca lanza: cualquier fallo (archivo ausente,
    YAML invalido, schema incompatible) se reporta en el `outcome`
    devuelto -- el llamador (`api/app.py`) decide como reaccionar
    (fail-closed), nunca esta funcion."""
    if path.is_symlink() or not path.is_file():
        return SecurityConfigLoadResult(outcome=SecurityConfigOutcome.MISSING, config=None)
    try:
        document, _hash = read_yaml_config(path)
    except SemanticConfigError as exc:
        return SecurityConfigLoadResult(
            outcome=SecurityConfigOutcome.INVALID,
            config=None,
            error=f"config/security.yaml no se pudo leer o parsear como YAML: {exc}",
        )
    try:
        config = SecurityConfig.model_validate(document)
    except ValueError as exc:
        return SecurityConfigLoadResult(
            outcome=SecurityConfigOutcome.INVALID,
            config=None,
            error=f"config/security.yaml no cumple el schema de SecurityConfig: {exc}",
        )
    return SecurityConfigLoadResult(outcome=SecurityConfigOutcome.LOADED, config=config)


__all__ = ["SecurityConfigLoadResult", "SecurityConfigOutcome", "load_security_config"]
