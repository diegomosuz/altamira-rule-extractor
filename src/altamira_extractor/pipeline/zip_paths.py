"""Normalizacion y validacion segura de paths de entradas ZIP.

Las pruebas corren en Linux pero el destino de despliegue es Docker
Desktop sobre Windows (bind mount NTFS), por lo que un nombre de entrada
debe ser simultaneamente seguro para ambos sistemas de archivos. No se
usa unicamente `Path.resolve()` ni solo la validacion generica de
`contracts.base.ensure_relative_path`: aqui se valida el string crudo del
ZIP antes de tocar el filesystem, con reglas especificas de Windows
(UNC, letras de unidad, nombres reservados, componentes terminados en
punto o espacio) que un `Path` POSIX no conoce.

Ningun nombre peligroso se reescribe o sanitiza en silencio: se rechaza
el paquete completo (.claude/rules/security.md).
"""

from __future__ import annotations

import unicodedata

from .errors import ZipSecurityError

_WINDOWS_RESERVED_BASENAMES: frozenset[str] = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def _reject(reason: str, raw_name: str) -> ZipSecurityError:
    return ZipSecurityError(f"entrada ZIP rechazada ({reason}): {raw_name!r}")


def normalize_zip_entry_path(raw_name: str) -> str:
    """Normaliza y valida el nombre de una entrada ZIP.

    Devuelve un path relativo POSIX (separado por "/", NFC-normalizado)
    seguro para extraer tanto en Linux como en Windows. Lanza
    ZipSecurityError ante cualquier construccion peligrosa.
    """
    if not raw_name:
        raise _reject("nombre vacio", raw_name)

    if any(ord(ch) < 0x20 for ch in raw_name):
        raise _reject("caracter de control en el nombre", raw_name)

    # Normaliza separadores: \ -> / para poder aplicar una unica regla.
    unified = raw_name.replace("\\", "/")

    # UNC: \\server\share (ya convertido a //server/share) o //server/share.
    if unified.startswith("//") or unified.startswith("\\\\"):
        raise _reject("ruta UNC", raw_name)

    # Letra de unidad Windows: C:, D:\, etc. (con o sin barra tras los dos puntos).
    if len(unified) >= 2 and unified[1] == ":" and unified[0].isalpha():
        raise _reject("letra de unidad Windows", raw_name)

    # Absoluto Unix o Windows-con-barra-simple (ya normalizado a /).
    if unified.startswith("/"):
        raise _reject("ruta absoluta", raw_name)

    raw_segments = unified.split("/")
    safe_segments: list[str] = []
    for segment in raw_segments:
        if segment == "":
            # Doble barra o barra final: estructura malformada, se rechaza
            # en vez de colapsarla silenciosamente.
            raise _reject("segmento vacio (barras repetidas o finales)", raw_name)

        normalized_segment = unicodedata.normalize("NFC", segment)

        if normalized_segment in (".", ".."):
            raise _reject("segmento '.' o '..'", raw_name)

        if normalized_segment != normalized_segment.rstrip(" ."):
            raise _reject("componente terminado en punto o espacio", raw_name)

        basename = normalized_segment.split(".", 1)[0].upper()
        if basename in _WINDOWS_RESERVED_BASENAMES:
            raise _reject("nombre reservado de Windows", raw_name)

        safe_segments.append(normalized_segment)

    return "/".join(safe_segments)


def collision_key(normalized_path: str) -> str:
    """Clave de colision insensible a mayusculas/minusculas y a forma Unicode."""
    return unicodedata.normalize("NFC", normalized_path).casefold()
