"""Tests de normalizacion segura de paths de entradas ZIP (Windows + Linux)."""

from __future__ import annotations

import unicodedata

import pytest

from altamira_extractor.pipeline.errors import ZipSecurityError
from altamira_extractor.pipeline.zip_paths import collision_key, normalize_zip_entry_path

# "E" + acento combinante por separado (NFD, dos code points) vs la misma
# letra normalizada a un solo code point precompuesto (NFC). Se construyen
# programaticamente (nunca como literal en el archivo) para que el test no
# dependa de como un editor/terminal decida representar el caracter.
_DECOMPOSED_E_ACUTE = "E" + chr(0x0301)  # U+0301 COMBINING ACUTE ACCENT
_PRECOMPOSED_E_ACUTE = unicodedata.normalize("NFC", _DECOMPOSED_E_ACUTE)  # -> U+00C9


def test_fixture_sanity_decomposed_and_precomposed_differ() -> None:
    assert _DECOMPOSED_E_ACUTE != _PRECOMPOSED_E_ACUTE
    assert len(_DECOMPOSED_E_ACUTE) == 2
    assert len(_PRECOMPOSED_E_ACUTE) == 1


def test_normalizes_backslashes_to_forward_slashes() -> None:
    assert normalize_zip_entry_path("01-codigo\\cobol\\PROG1.cbl") == "01-codigo/cobol/PROG1.cbl"


def test_rejects_backslash_traversal() -> None:
    with pytest.raises(ZipSecurityError):
        normalize_zip_entry_path("01-codigo\\..\\..\\evil.txt")


def test_rejects_forward_slash_traversal() -> None:
    with pytest.raises(ZipSecurityError):
        normalize_zip_entry_path("../../evil.txt")


def test_rejects_dot_dot_segment_in_the_middle() -> None:
    with pytest.raises(ZipSecurityError):
        normalize_zip_entry_path("01-codigo/../02-parametria/evil.sql")


def test_rejects_unc_path() -> None:
    with pytest.raises(ZipSecurityError):
        normalize_zip_entry_path("\\\\server\\share\\evil.cbl")


def test_rejects_unc_path_already_forward_slashed() -> None:
    with pytest.raises(ZipSecurityError):
        normalize_zip_entry_path("//server/share/evil.cbl")


@pytest.mark.parametrize("drive", ["C:", "c:", "Z:"])
def test_rejects_windows_drive_letter(drive: str) -> None:
    with pytest.raises(ZipSecurityError):
        normalize_zip_entry_path(f"{drive}\\evil.cbl")


def test_rejects_unix_absolute_path() -> None:
    with pytest.raises(ZipSecurityError):
        normalize_zip_entry_path("/etc/passwd")


@pytest.mark.parametrize(
    "reserved_name",
    ["CON", "con", "PRN", "AUX", "NUL", "COM1", "com9", "LPT1", "lpt9"],
)
def test_rejects_windows_reserved_basename(reserved_name: str) -> None:
    with pytest.raises(ZipSecurityError):
        normalize_zip_entry_path(f"01-codigo/cobol/{reserved_name}.cbl")


def test_reserved_basename_check_is_not_a_prefix_match() -> None:
    # "CONFIG.cbl" no es un nombre reservado: el nombre base completo es "CONFIG".
    assert normalize_zip_entry_path("01-codigo/cobol/CONFIG.cbl") == "01-codigo/cobol/CONFIG.cbl"


def test_rejects_component_with_trailing_dot() -> None:
    with pytest.raises(ZipSecurityError):
        normalize_zip_entry_path("01-codigo/cobol/PROG1.cbl.")


def test_rejects_component_with_trailing_space() -> None:
    with pytest.raises(ZipSecurityError):
        normalize_zip_entry_path("01-codigo/cobol/PROG1.cbl ")


def test_rejects_directory_component_with_trailing_dot() -> None:
    with pytest.raises(ZipSecurityError):
        normalize_zip_entry_path("01-codigo./cobol/PROG1.cbl")


def test_rejects_empty_name() -> None:
    with pytest.raises(ZipSecurityError):
        normalize_zip_entry_path("")


def test_rejects_double_slash() -> None:
    with pytest.raises(ZipSecurityError):
        normalize_zip_entry_path("01-codigo//cobol/PROG1.cbl")


def test_applies_unicode_nfc_normalization() -> None:
    decomposed = f"01-codigo/cobol/PROG{_DECOMPOSED_E_ACUTE}.cbl"
    precomposed = f"01-codigo/cobol/PROG{_PRECOMPOSED_E_ACUTE}.cbl"
    assert normalize_zip_entry_path(decomposed) == precomposed


def test_collision_key_is_case_insensitive() -> None:
    assert collision_key("01-codigo/cobol/PROG1.cbl") == collision_key(
        "01-codigo/COBOL/prog1.CBL"
    )


def test_collision_key_normalizes_unicode_before_casefold() -> None:
    decomposed = normalize_zip_entry_path(f"01-codigo/cobol/PROG{_DECOMPOSED_E_ACUTE}.cbl")
    precomposed = normalize_zip_entry_path(f"01-codigo/cobol/PROG{_PRECOMPOSED_E_ACUTE}.cbl")
    assert collision_key(decomposed) == collision_key(precomposed)
