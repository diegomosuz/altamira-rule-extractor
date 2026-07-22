"""Base compartida por todos los contratos tipados del pipeline.

Centraliza lo que CLAUDE.md exige de forma transversal: sin campos
adicionales en contratos persistidos, paths relativos (nunca
absolutos) y serializacion JSON estable, UTF-8 y legible.
"""

from __future__ import annotations

import json
import re
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict

SHA256_HEX_PATTERN = r"^[a-f0-9]{64}$"

_SHA256_RE = re.compile(SHA256_HEX_PATTERN)
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def ensure_relative_path(value: str) -> str:
    """Valida que un path persistido sea relativo y no escape su raiz."""
    if not value:
        raise ValueError("el path no puede estar vacio")
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or _WINDOWS_DRIVE_RE.match(value):
        raise ValueError(f"el path debe ser relativo, no absoluto: {value!r}")
    segments = normalized.split("/")
    if ".." in segments:
        raise ValueError(f"el path no puede contener segmentos '..': {value!r}")
    return value


def ensure_sha256_hex(value: str) -> str:
    """Valida que un hash de paquete sea sha256 hexadecimal en minusculas."""
    if not _SHA256_RE.match(value):
        raise ValueError(
            f"source_package_hash invalido, se espera sha256 hex de 64 caracteres: {value!r}"
        )
    return value


RelativePath = Annotated[str, AfterValidator(ensure_relative_path)]
Sha256Hex = Annotated[str, AfterValidator(ensure_sha256_hex)]


class AltamiraBaseModel(BaseModel):
    """Base para todos los contratos.

    Prohibe campos adicionales (los contratos son cerrados) y ofrece
    serializacion JSON estable: UTF-8, claves ordenadas, formato legible.
    """

    model_config = ConfigDict(extra="forbid")

    def to_stable_json(self) -> str:
        payload = self.model_dump(mode="json")
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
