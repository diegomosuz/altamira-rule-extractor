"""Tests de PackageValidator: orden de validacion, seguridad ZIP y manifest."""

from __future__ import annotations

from pathlib import Path

import pytest

from altamira_extractor.config import Settings
from altamira_extractor.pipeline.errors import ManifestValidationError, PackageValidationError
from altamira_extractor.pipeline.package_validator import validate_package

from .conftest import (
    DEFAULT_MANIFEST_DDL,
    DEFAULT_MANIFEST_SNAPSHOT,
    build_package_zip_with_raw_entry,
    build_valid_package_zip,
    corrupt_crc,
    default_valid_entries,
    encrypted_entry,
    fifo_entry,
    mark_entry_encrypted,
    raw_named_entry,
    socket_entry,
    symlink_entry,
    valid_manifest_xml,
    write_zip,
)


def test_valid_package_passes(tmp_path: Path, settings: Settings) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    validated = validate_package(zip_path, settings)
    assert validated.manifest.country.code == "AR"
    assert validated.manifest.implementation.entry_programs == ["ARTRFPROP01"]
    assert "manifest.xml" in validated.normalized_file_entries


def test_admits_cob_and_copy_extensions(tmp_path: Path, settings: Settings) -> None:
    entries = default_valid_entries()
    del entries["01-codigo/cobol/ARTRFPROP01.cbl"]
    del entries["01-codigo/copybooks/CPYTRF01.cpy"]
    entries["01-codigo/cobol/ARTRFPROP01.cob"] = b"       IDENTIFICATION DIVISION.\n"
    entries["01-codigo/copybooks/CPYTRF01.copy"] = b"       01 WS-TRF.\n"
    zip_path = write_zip(tmp_path / "package.zip", entries)

    validated = validate_package(zip_path, settings)
    assert "01-codigo/cobol/ARTRFPROP01.cob" in validated.normalized_file_entries


def test_admits_txt_extension(tmp_path: Path, settings: Settings) -> None:
    zip_path = build_valid_package_zip(
        tmp_path / "package.zip", extra={"01-codigo/README.txt": b"notas"}
    )
    validated = validate_package(zip_path, settings)
    assert "01-codigo/README.txt" in validated.normalized_file_entries


def test_rejects_disallowed_extension(tmp_path: Path, settings: Settings) -> None:
    zip_path = build_valid_package_zip(
        tmp_path / "package.zip", extra={"01-codigo/evil.exe": b"MZ"}
    )
    with pytest.raises(PackageValidationError):
        validate_package(zip_path, settings)


def test_rejects_missing_manifest(tmp_path: Path, settings: Settings) -> None:
    entries = default_valid_entries()
    del entries["manifest.xml"]
    zip_path = write_zip(tmp_path / "package.zip", entries)
    with pytest.raises(PackageValidationError, match="manifest.xml"):
        validate_package(zip_path, settings)


def test_rejects_missing_cobol_program(tmp_path: Path, settings: Settings) -> None:
    entries = default_valid_entries()
    del entries["01-codigo/cobol/ARTRFPROP01.cbl"]
    zip_path = write_zip(tmp_path / "package.zip", entries)
    with pytest.raises(PackageValidationError, match="COBOL"):
        validate_package(zip_path, settings)


def test_rejects_missing_parametria(tmp_path: Path, settings: Settings) -> None:
    entries = default_valid_entries()
    del entries[DEFAULT_MANIFEST_DDL]
    del entries[DEFAULT_MANIFEST_SNAPSHOT]
    entries["manifest.xml"] = valid_manifest_xml(ddl_path=None, snapshot_path=None)
    zip_path = write_zip(tmp_path / "package.zip", entries)
    with pytest.raises(PackageValidationError, match="parametria"):
        validate_package(zip_path, settings)


def test_rejects_symlink_entry(tmp_path: Path, settings: Settings) -> None:
    zip_path = build_package_zip_with_raw_entry(
        tmp_path / "package.zip", [symlink_entry("01-codigo/cobol/evil.cbl", "/etc/passwd")]
    )
    with pytest.raises(PackageValidationError, match="symlink"):
        validate_package(zip_path, settings)


def test_rejects_fifo_entry(tmp_path: Path, settings: Settings) -> None:
    zip_path = build_package_zip_with_raw_entry(
        tmp_path / "package.zip", [fifo_entry("01-codigo/cobol/evil.cbl")]
    )
    with pytest.raises(PackageValidationError, match="FIFO"):
        validate_package(zip_path, settings)


def test_rejects_socket_entry(tmp_path: Path, settings: Settings) -> None:
    zip_path = build_package_zip_with_raw_entry(
        tmp_path / "package.zip", [socket_entry("01-codigo/cobol/evil.cbl")]
    )
    with pytest.raises(PackageValidationError, match="socket"):
        validate_package(zip_path, settings)


def test_rejects_encrypted_entry(tmp_path: Path, settings: Settings) -> None:
    zip_path = build_package_zip_with_raw_entry(
        tmp_path / "package.zip", [encrypted_entry("01-codigo/cobol/evil.cbl")]
    )
    mark_entry_encrypted(zip_path, "01-codigo/cobol/evil.cbl")
    with pytest.raises(PackageValidationError, match="cifrada"):
        validate_package(zip_path, settings)


def test_rejects_path_traversal_entry(tmp_path: Path, settings: Settings) -> None:
    zip_path = build_package_zip_with_raw_entry(
        tmp_path / "package.zip", [raw_named_entry("../evil.cbl")]
    )
    with pytest.raises(PackageValidationError):
        validate_package(zip_path, settings)


def test_rejects_case_insensitive_collision(tmp_path: Path, settings: Settings) -> None:
    entries = default_valid_entries()
    zip_path = tmp_path / "package.zip"
    write_zip(
        zip_path,
        entries,
        raw_entries=[raw_named_entry("01-CODIGO/cobol/ARTRFPROP01.cbl", b"duplicado")],
    )
    with pytest.raises(PackageValidationError, match="colision"):
        validate_package(zip_path, settings)


def test_rejects_entry_count_over_limit(tmp_path: Path, settings: Settings) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    tight_settings = settings.model_copy(update={"max_entry_count": 1})
    with pytest.raises(PackageValidationError, match="entradas"):
        validate_package(zip_path, tight_settings)


def test_rejects_single_entry_size_over_limit(tmp_path: Path, settings: Settings) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    tight_settings = settings.model_copy(update={"max_single_entry_uncompressed_bytes": 4})
    with pytest.raises(PackageValidationError, match="limite"):
        validate_package(zip_path, tight_settings)


def test_rejects_total_size_over_limit(tmp_path: Path, settings: Settings) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    tight_settings = settings.model_copy(update={"max_total_uncompressed_bytes": 4})
    with pytest.raises(PackageValidationError):
        validate_package(zip_path, tight_settings)


def test_rejects_compression_ratio_bomb(tmp_path: Path, settings: Settings) -> None:
    entries = default_valid_entries()
    entries["02-parametria/snapshots/BOMB.csv"] = b"A" * 2_000_000
    zip_path = write_zip(tmp_path / "package.zip", entries)
    with pytest.raises(PackageValidationError, match="ratio"):
        validate_package(zip_path, settings)


def test_rejects_corrupted_crc(tmp_path: Path, settings: Settings) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    corrupt_crc(zip_path, "01-codigo/cobol/ARTRFPROP01.cbl")
    with pytest.raises(PackageValidationError, match="CRC"):
        validate_package(zip_path, settings)


def test_rejects_oversized_package(tmp_path: Path, settings: Settings) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    tight_settings = settings.model_copy(update={"max_package_size_bytes": 4})
    with pytest.raises(PackageValidationError, match="limite"):
        validate_package(zip_path, tight_settings)


def test_rejects_non_zip_file(tmp_path: Path, settings: Settings) -> None:
    zip_path = tmp_path / "package.zip"
    zip_path.write_bytes(b"not a zip file at all")
    with pytest.raises(PackageValidationError, match="ZIP"):
        validate_package(zip_path, settings)


def test_manifest_ddl_path_not_in_zip(tmp_path: Path, settings: Settings) -> None:
    entries = default_valid_entries()
    entries["manifest.xml"] = valid_manifest_xml(
        ddl_path="02-parametria/ddl/DOES_NOT_EXIST.sql",
        snapshot_path=DEFAULT_MANIFEST_SNAPSHOT,
    )
    zip_path = write_zip(tmp_path / "package.zip", entries)
    with pytest.raises(ManifestValidationError, match="no existe"):
        validate_package(zip_path, settings)


def test_manifest_ddl_wrong_prefix_is_rejected(tmp_path: Path, settings: Settings) -> None:
    entries = default_valid_entries()
    entries["manifest.xml"] = valid_manifest_xml(
        ddl_path="01-codigo/PARAM_TRANSFER.sql",
        snapshot_path=DEFAULT_MANIFEST_SNAPSHOT,
    )
    zip_path = write_zip(tmp_path / "package.zip", entries)
    with pytest.raises(ManifestValidationError, match="02-parametria/ddl"):
        validate_package(zip_path, settings)
