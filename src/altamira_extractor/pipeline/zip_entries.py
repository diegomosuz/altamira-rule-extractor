"""Clasificacion de tipo/cifrado de una entrada ZIP ya con path normalizado.

Compartido por PackageValidator (validacion previa, sin descomprimir) y
SafeExtractor (re-verificacion defensiva justo antes de escribir cada
entrada): ambos deben aplicar exactamente la misma regla, nunca una
reimplementacion que pueda divergir.
"""

from __future__ import annotations

import stat as stat_module
import zipfile

from .errors import ZipSecurityError

_UNIX_CREATE_SYSTEM = 3
_WINDOWS_REPARSE_POINT_ATTR = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT


def extension_of(normalized_path: str) -> str:
    name = normalized_path.rsplit("/", 1)[-1]
    if "." not in name:
        return ""
    return "." + name.rsplit(".", 1)[-1].lower()


def is_encrypted(info: zipfile.ZipInfo) -> bool:
    return bool(info.flag_bits & 0x1)


def classify_entry_type(info: zipfile.ZipInfo, normalized: str) -> str:
    """Devuelve "dir" o "file"; lanza ZipSecurityError para cualquier otro tipo."""
    if info.is_dir():
        return "dir"

    if info.create_system == _UNIX_CREATE_SYSTEM:
        mode = (info.external_attr >> 16) & 0xFFFF
        if mode != 0 and not stat_module.S_ISREG(mode):
            if stat_module.S_ISLNK(mode):
                raise ZipSecurityError(f"symlink no permitido: {normalized!r}")
            if stat_module.S_ISSOCK(mode):
                raise ZipSecurityError(f"socket no permitido: {normalized!r}")
            if stat_module.S_ISFIFO(mode):
                raise ZipSecurityError(f"FIFO no permitido: {normalized!r}")
            if stat_module.S_ISBLK(mode) or stat_module.S_ISCHR(mode):
                raise ZipSecurityError(f"dispositivo no permitido: {normalized!r}")
            raise ZipSecurityError(f"tipo de entrada especial no permitido: {normalized!r}")
    elif bool(info.external_attr & _WINDOWS_REPARSE_POINT_ATTR):
        raise ZipSecurityError(
            f"reparse point / symlink Windows no permitido: {normalized!r}"
        )

    return "file"
