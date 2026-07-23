"""Tests de encoding_detector: deteccion pura, sin filesystem ni Docker."""

from __future__ import annotations

from altamira_extractor.contracts.enums import TextEncoding
from altamira_extractor.pipeline.encoding_detector import detect_file_encoding


def test_utf8_bom_is_detected_as_utf8() -> None:
    data = b"\xef\xbb\xbf       IDENTIFICATION DIVISION.\n"

    encoding, warning = detect_file_encoding(data, declared_encoding="AUTO", relative_path="a.cbl")

    assert encoding == TextEncoding.UTF_8
    assert warning is None


def test_bom_utf8_contradicting_declared_windows_1252_wins_with_warning() -> None:
    data = b"\xef\xbb\xbf       IDENTIFICATION DIVISION.\n"

    encoding, warning = detect_file_encoding(
        data, declared_encoding="WINDOWS-1252", relative_path="modulo/a.cbl"
    )

    assert encoding == TextEncoding.UTF_8
    assert warning is not None
    assert "modulo/a.cbl" in warning
    assert "WINDOWS-1252" in warning


def test_bom_utf8_contradicting_declared_iso_8859_1_wins_with_warning() -> None:
    data = b"\xef\xbb\xbf       IDENTIFICATION DIVISION.\n"

    encoding, warning = detect_file_encoding(
        data, declared_encoding="ISO-8859-1", relative_path="a.cbl"
    )

    assert encoding == TextEncoding.UTF_8
    assert warning is not None


def test_bom_utf8_with_declared_utf8_has_no_contradiction_warning() -> None:
    data = b"\xef\xbb\xbf       IDENTIFICATION DIVISION.\n"

    encoding, warning = detect_file_encoding(data, declared_encoding="UTF-8", relative_path="a.cbl")

    assert encoding == TextEncoding.UTF_8
    assert warning is None


def test_bom_utf8_with_declared_auto_has_no_contradiction_warning() -> None:
    data = b"\xef\xbb\xbf       IDENTIFICATION DIVISION.\n"

    encoding, warning = detect_file_encoding(data, declared_encoding="AUTO", relative_path="a.cbl")

    assert encoding == TextEncoding.UTF_8
    assert warning is None


def test_bom_utf8_contradicting_declared_cp037_wins_with_warning() -> None:
    # La contradiccion no se limita a WINDOWS-1252/ISO-8859-1: cualquier
    # declaracion distinta de UTF-8 o AUTO (incluidos valores no
    # reconocidos como CP037) debe dejar constancia.
    data = b"\xef\xbb\xbf       IDENTIFICATION DIVISION.\n"

    encoding, warning = detect_file_encoding(
        data, declared_encoding="CP037", relative_path="modulo/a.cbl"
    )

    assert encoding == TextEncoding.UTF_8
    assert warning is not None
    assert "modulo/a.cbl" in warning
    assert "CP037" in warning


def test_ascii_with_declared_utf8_is_utf8() -> None:
    data = b"       IDENTIFICATION DIVISION.\n"

    encoding, warning = detect_file_encoding(data, declared_encoding="UTF-8", relative_path="a.cbl")

    assert encoding == TextEncoding.UTF_8
    assert warning is None


def test_declared_utf8_but_invalid_bytes_leaves_unresolved() -> None:
    data = b"DATO \x93 INVALIDO\n"

    encoding, warning = detect_file_encoding(data, declared_encoding="UTF-8", relative_path="a.cbl")

    assert encoding is None
    assert warning is not None
    assert "UTF-8" in warning


def test_ascii_with_declared_windows_1252_is_windows_1252() -> None:
    # Declaracion explicita gobierna aunque el contenido tambien sea UTF-8
    # valido: no se prefiere UTF-8 "por mas probable".
    data = b"       IDENTIFICATION DIVISION.\n"

    encoding, warning = detect_file_encoding(
        data, declared_encoding="WINDOWS-1252", relative_path="a.cbl"
    )

    assert encoding == TextEncoding.WINDOWS_1252
    assert warning is None


def test_declared_windows_1252_valid_non_ascii_is_persisted() -> None:
    # 0x93/0x94 = comillas curvas cp1252, invalido como UTF-8.
    data = b"MENSAJE \x93HOLA\x94.\n"

    encoding, warning = detect_file_encoding(
        data, declared_encoding="WINDOWS-1252", relative_path="a.cbl"
    )

    assert encoding == TextEncoding.WINDOWS_1252
    assert warning is None


def test_declared_windows_1252_with_undefined_byte_leaves_unresolved() -> None:
    # 0x81 es indefinido en cp1252: decodificar con cp1252 estricto falla.
    data = b"DATO \x81 INVALIDO\n"

    encoding, warning = detect_file_encoding(
        data, declared_encoding="WINDOWS-1252", relative_path="modulo/a.cbl"
    )

    assert encoding is None
    assert warning is not None
    assert "modulo/a.cbl" in warning
    assert "WINDOWS-1252" in warning


def test_ascii_with_declared_iso_8859_1_is_iso_8859_1() -> None:
    # Declaracion explicita gobierna: no se reemplaza por UTF-8 aunque el
    # contenido sea ASCII puro.
    data = b"       IDENTIFICATION DIVISION.\n"

    encoding, warning = detect_file_encoding(
        data, declared_encoding="ISO-8859-1", relative_path="a.cbl"
    )

    assert encoding == TextEncoding.ISO_8859_1
    assert warning is None


def test_declared_iso_8859_1_any_byte_value_is_always_persisted() -> None:
    data = bytes(range(0, 256))

    encoding, warning = detect_file_encoding(
        data, declared_encoding="ISO-8859-1", relative_path="a.cbl"
    )

    assert encoding == TextEncoding.ISO_8859_1
    assert warning is None


def test_auto_with_valid_utf8_is_utf8() -> None:
    data = "       01 WS-NOMBRE PIC X(20) VALUE 'JOSÉ'.\n".encode()

    encoding, warning = detect_file_encoding(data, declared_encoding="AUTO", relative_path="a.cbl")

    assert encoding == TextEncoding.UTF_8
    assert warning is None


def test_auto_non_utf8_with_cp1252_undefined_byte_is_unambiguous_iso_8859_1() -> None:
    # 0x81 no existe en cp1252 (decodificacion estricta fallaria): descarta
    # cp1252 de forma concluyente y deja solo ISO-8859-1 como candidato.
    data = b"DATO \x81 CAMPO\n"

    encoding, warning = detect_file_encoding(data, declared_encoding="AUTO", relative_path="a.cbl")

    assert encoding == TextEncoding.ISO_8859_1
    assert warning is None


def test_auto_non_utf8_valid_in_both_windows_1252_and_iso_8859_1_stays_ambiguous() -> None:
    # 0x93/0x94 estan definidos tanto en cp1252 (comillas curvas) como en
    # Latin-1 (controles C1): sin declaracion explicita no hay forma de
    # distinguirlos, por lo que NUNCA se asigna WINDOWS-1252 por defecto.
    data = b"MENSAJE \x93HOLA\x94.\n"

    encoding, warning = detect_file_encoding(
        data, declared_encoding="AUTO", relative_path="modulo/b.cbl"
    )

    assert encoding is None
    assert warning is not None
    assert "modulo/b.cbl" in warning
    assert "AUTO" in warning


def test_declared_unsupported_and_non_utf8_leaves_unresolved_with_warning() -> None:
    data = b"DATO \xe9 CAMPO\n"

    encoding, warning = detect_file_encoding(data, declared_encoding="CP037", relative_path="a.cbl")

    assert encoding is None
    assert warning is not None
    assert "CP037" in warning


def test_declared_unsupported_ascii_content_is_still_unresolved() -> None:
    # Un encoding no soportado nunca se reinterpreta silenciosamente como
    # UTF-8, ni siquiera cuando el contenido es ASCII puro.
    data = b"       IDENTIFICATION DIVISION.\n"

    encoding, warning = detect_file_encoding(data, declared_encoding="CP037", relative_path="a.cbl")

    assert encoding is None
    assert warning is not None
    assert "CP037" in warning


def test_empty_file_with_declared_utf8_is_utf8() -> None:
    encoding, warning = detect_file_encoding(b"", declared_encoding="UTF-8", relative_path="a.cbl")

    assert encoding == TextEncoding.UTF_8
    assert warning is None


def test_declared_encoding_case_and_whitespace_are_normalized() -> None:
    data = b"MENSAJE \x93HOLA\x94.\n"

    encoding, warning = detect_file_encoding(
        data, declared_encoding=" windows-1252 ", relative_path="a.cbl"
    )

    assert encoding == TextEncoding.WINDOWS_1252
    assert warning is None
