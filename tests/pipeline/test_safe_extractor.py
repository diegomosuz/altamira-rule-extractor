"""Tests de SafeExtractor: extraccion transaccional y conteo real de bytes."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from altamira_extractor.config import Settings
from altamira_extractor.pipeline.errors import ExtractionError
from altamira_extractor.pipeline.safe_extractor import extract_package

from .conftest import (
    build_package_zip_with_raw_entry,
    build_valid_package_zip,
    corrupt_declared_uncompressed_size,
    default_valid_entries,
    symlink_entry,
    write_zip,
)


def test_extracts_all_entries_with_correct_content(tmp_path: Path, settings: Settings) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    work_dir = tmp_path / "run" / "work"

    extracted_dir = extract_package(zip_path, work_dir, settings)

    assert extracted_dir == work_dir / "extracted"
    for relative_path, expected_bytes in default_valid_entries().items():
        written = (extracted_dir / relative_path).read_bytes()
        assert written == expected_bytes

    assert list(work_dir.glob("extracted.tmp-*")) == []


def test_failure_cleans_up_temp_dir_and_leaves_no_final_dir(
    tmp_path: Path, settings: Settings
) -> None:
    zip_path = build_package_zip_with_raw_entry(
        tmp_path / "package.zip", [symlink_entry("01-codigo/cobol/evil.cbl", "/etc/passwd")]
    )
    work_dir = tmp_path / "run" / "work"

    with pytest.raises(ExtractionError, match="symlink"):
        extract_package(zip_path, work_dir, settings)

    assert not (work_dir / "extracted").exists()
    assert list(work_dir.glob("extracted.tmp-*")) == []


def test_refuses_to_extract_over_existing_final_dir(tmp_path: Path, settings: Settings) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    work_dir = tmp_path / "run" / "work"
    (work_dir / "extracted").mkdir(parents=True)

    with pytest.raises(ExtractionError, match="ya existe"):
        extract_package(zip_path, work_dir, settings)

    assert list(work_dir.glob("extracted.tmp-*")) == []


def test_aborts_when_streamed_bytes_do_not_match_declared_size(
    tmp_path: Path, settings: Settings
) -> None:
    # El tamano declarado se infla (no se achica): zipfile ya trunca la
    # lectura al tamano declarado y detectaria una mentira "mas chica" como
    # un CRC invalido antes de que nuestro propio contador intervenga (esa
    # ruta se prueba en test_package_validator.test_rejects_corrupted_crc).
    # Mintiendo "mas grande" se ejercita especificamente nuestro chequeo de
    # bytes reales vs. declarados en SafeExtractor.
    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    corrupt_declared_uncompressed_size(
        zip_path, "01-codigo/dclgen/DCLTRF01.dcl", fake_size=10_000
    )
    work_dir = tmp_path / "run" / "work"

    with pytest.raises(ExtractionError, match="inconsistente"):
        extract_package(zip_path, work_dir, settings)

    assert not (work_dir / "extracted").exists()
    assert list(work_dir.glob("extracted.tmp-*")) == []


def test_aborts_when_real_bytes_exceed_single_entry_limit(
    tmp_path: Path, settings: Settings
) -> None:
    entries = default_valid_entries()
    entries["02-parametria/snapshots/BIG.csv"] = b"1,2,3\n" * 100_000
    zip_path = tmp_path / "package.zip"
    write_zip(zip_path, entries)
    tight_settings = settings.model_copy(update={"max_single_entry_uncompressed_bytes": 1_000})
    work_dir = tmp_path / "run" / "work"

    with pytest.raises(ExtractionError, match="limite individual"):
        extract_package(zip_path, work_dir, tight_settings)

    assert not (work_dir / "extracted").exists()
    assert list(work_dir.glob("extracted.tmp-*")) == []


def test_extracted_file_hash_matches_source(tmp_path: Path, settings: Settings) -> None:
    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    work_dir = tmp_path / "run" / "work"

    extracted_dir = extract_package(zip_path, work_dir, settings)

    manifest_bytes = default_valid_entries()["manifest.xml"]
    extracted_manifest = extracted_dir / "manifest.xml"
    assert hashlib.sha256(extracted_manifest.read_bytes()).hexdigest() == hashlib.sha256(
        manifest_bytes
    ).hexdigest()
