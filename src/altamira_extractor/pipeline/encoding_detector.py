"""Deteccion deterministica del encoding real de un archivo de texto.

Nunca inventa un valor: si la evidencia no alcanza para resolver de forma
inequivoca uno de los tres encodings canonicos (UTF-8, WINDOWS-1252,
ISO-8859-1), devuelve `None` junto con un warning explicativo.

Prioridad, en orden (salvo BOM UTF-8, la declaracion explicita del
manifest gobierna la deteccion; los bytes por si solos NUNCA se prefieren
por sobre una declaracion explicita soportada):

1. BOM UTF-8 (EF BB BF): siempre UTF-8, sin excepcion. Si el manifest
   declara explicitamente cualquier valor distinto de UTF-8 o AUTO (los
   tres canonicos WINDOWS-1252/ISO-8859-1, o cualquier otro valor no
   reconocido como CP037), se deja constancia con un warning de
   contradiccion; el resultado sigue siendo UTF-8 (el BOM es evidencia mas
   fuerte que una declaracion). AUTO no contradice nada: no afirma un
   encoding concreto.
2. Sin BOM, `declared_encoding` = UTF-8: se exige que el archivo
   decodifique como UTF-8 estricto. Si falla, `None` + warning (nunca se
   reinterpreta silenciosamente con otro encoding).
3. Sin BOM, `declared_encoding` = WINDOWS-1252: se exige que decodifique
   como cp1252 estricto. Si funciona, se persiste WINDOWS-1252 aunque el
   contenido tambien sea ASCII o UTF-8 valido (la declaracion gobierna,
   no se prefiere UTF-8 por "mas probable"). Si falla (bytes indefinidos
   en cp1252: 0x81/0x8D/0x8F/0x90/0x9D), `None` + warning.
4. Sin BOM, `declared_encoding` = ISO-8859-1: decodifica siempre (Latin-1
   define los 256 valores de byte), se persiste ISO-8859-1 sin excepcion,
   incluso con contenido puramente ASCII.
5. Sin BOM, `declared_encoding` = AUTO:
   - UTF-8 estricto valido -> UTF-8.
   - Si no es UTF-8 valido y aparece algun byte 0x81/0x8D/0x8F/0x90/0x9D
     (indefinido en cp1252, valido en Latin-1): esos bytes descartan
     cp1252 de forma concluyente -> ISO-8859-1.
   - En cualquier otro caso no-UTF-8: los bytes restantes (0x00-0x7F y
     0x80-0x9F/0xA0-0xFF definidos en ambos) no permiten distinguir
     WINDOWS-1252 de ISO-8859-1 sin una declaracion explicita -> `None` +
     warning de ambiguedad. Nunca se asigna WINDOWS-1252 por defecto: solo
     es fiable cuando el manifest lo declara explicitamente (regla 3).
6. Sin BOM, `declared_encoding` no reconocido (ni AUTO ni uno de los tres
   canonicos): no hay base deterministica para resolverlo -> `None` +
   warning. Nunca se reinterpreta silenciosamente como UTF-8.
"""

from __future__ import annotations

from ..contracts.enums import TextEncoding

_UTF8_BOM = b"\xef\xbb\xbf"

# Bytes 0x80-0x9F indefinidos en cp1252 (windows-1252) pero validos en
# ISO-8859-1 (controles C1 de Latin-1). Es la unica evidencia, a partir de
# los bytes solos, que descarta cp1252 de forma concluyente: el resto del
# rango 0x80-0x9F esta definido en ambos encodings y por lo tanto no sirve
# para distinguirlos sin una declaracion explicita del manifest.
_CP1252_UNDEFINED_BYTES = frozenset({0x81, 0x8D, 0x8F, 0x90, 0x9D})

_PYTHON_CODEC_BY_DECLARED = {
    TextEncoding.WINDOWS_1252.value: "cp1252",
    TextEncoding.ISO_8859_1.value: "iso-8859-1",
}


def _is_valid_utf8(data: bytes) -> bool:
    try:
        data.decode("utf-8", errors="strict")
        return True
    except UnicodeDecodeError:
        return False


def detect_file_encoding(
    data: bytes, *, declared_encoding: str, relative_path: str
) -> tuple[TextEncoding | None, str | None]:
    """Detecta el encoding real de `data`.

    Devuelve `(encoding_detectado, warning)`: `warning` es `None` cuando la
    deteccion fue concluyente sin contradicciones. `declared_encoding` es
    el valor crudo de `manifest.source.encoding` (puede ser "AUTO" o
    cualquier otro string).
    """
    declared = declared_encoding.strip().upper()

    if data.startswith(_UTF8_BOM):
        if declared not in (TextEncoding.UTF_8.value, "AUTO"):
            return TextEncoding.UTF_8, (
                f"{relative_path!r}: el archivo tiene BOM UTF-8 pero el manifest declara "
                f"encoding {declared_encoding!r}; se usa UTF-8 (el BOM prevalece) y se deja "
                "constancia de la contradiccion"
            )
        return TextEncoding.UTF_8, None

    if declared == TextEncoding.UTF_8.value:
        if _is_valid_utf8(data):
            return TextEncoding.UTF_8, None
        return None, (
            f"{relative_path!r}: el manifest declara encoding UTF-8 pero el archivo no "
            "decodifica como UTF-8 valido; detected_encoding queda sin resolver"
        )

    python_codec = _PYTHON_CODEC_BY_DECLARED.get(declared)
    if python_codec is not None:
        try:
            data.decode(python_codec, errors="strict")
        except UnicodeDecodeError as exc:
            return None, (
                f"{relative_path!r}: el manifest declara encoding {declared!r} pero el "
                f"archivo no puede decodificarse con ese encoding ({exc}); "
                "detected_encoding queda sin resolver"
            )
        return TextEncoding(declared), None

    if declared == "AUTO":
        if _is_valid_utf8(data):
            return TextEncoding.UTF_8, None
        if any(byte in _CP1252_UNDEFINED_BYTES for byte in data):
            return TextEncoding.ISO_8859_1, None
        return None, (
            f"{relative_path!r}: el manifest declara encoding AUTO, el archivo no es "
            "UTF-8 valido y los bytes presentes estan definidos tanto en WINDOWS-1252 "
            "como en ISO-8859-1, por lo que no pueden distinguirse sin una declaracion "
            "explicita en el manifest; detected_encoding queda sin resolver"
        )

    return None, (
        f"{relative_path!r}: el archivo no tiene BOM UTF-8 y el manifest declara "
        f"encoding {declared_encoding!r}, que no es uno de los valores soportados "
        "(AUTO, UTF-8, WINDOWS-1252, ISO-8859-1); detected_encoding queda sin resolver"
    )
