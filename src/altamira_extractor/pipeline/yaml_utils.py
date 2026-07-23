"""Carga segura de archivos YAML de configuracion (no de paquetes
subidos por usuarios: `config/*.yml` es contenido controlado por el
equipo, pero igual se carga con `yaml.safe_load` — nunca `yaml.load`
con un Loader que resuelva tags Python arbitrarios).

Un YAML ausente, no legible como archivo regular, o mal formado es un
error fatal de configuracion (`SemanticConfigError`): nunca se degrada
a advertencia ni a un resultado parcial.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml

from .errors import SemanticConfigError


def read_yaml_config(path: Path) -> tuple[Any, str]:
    """Lee y parsea `path` una unica vez. Devuelve `(documento, sha256_hex)`
    calculado sobre los bytes reales del archivo (antes de decodificar)."""
    if not path.is_file():
        raise SemanticConfigError(
            f"no se encontro el archivo de configuracion: {path.name}"
        )

    data = path.read_bytes()
    config_hash = hashlib.sha256(data).hexdigest()

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SemanticConfigError(f"{path.name}: no es UTF-8 valido: {exc}") from exc

    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SemanticConfigError(f"{path.name}: YAML mal formado: {exc}") from exc

    return document, config_hash
