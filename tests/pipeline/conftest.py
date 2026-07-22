"""Factories para construir ZIPs Altamira validos y maliciosos en memoria.

No se versionan binarios ZIP: cada test construye el paquete que
necesita con estas utilidades, para que el contenido malicioso quede
legible y revisable en el diff.
"""

from __future__ import annotations

import stat
import struct
import zipfile
from collections.abc import Iterable
from pathlib import Path

import pytest

from altamira_extractor.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings con limites por defecto pero data_dir/runs_dir aislados en tmp_path.

    manifest_xsd_path se deja en su default (schemas/manifest.xsd real del
    repo): la validacion XSD debe ser genuina, no un doble de prueba.
    """
    return Settings(
        data_dir=tmp_path / "data",
        runs_dir=tmp_path / "data" / "runs",
        incoming_dir=tmp_path / "data" / "incoming",
    )

DEFAULT_MANIFEST_DDL = "02-parametria/ddl/PARAM_TRANSFER.sql"
DEFAULT_MANIFEST_SNAPSHOT = "02-parametria/snapshots/PARAM_TRANSFER_20260515.csv"


def valid_manifest_xml(
    *,
    ddl_path: str | None = DEFAULT_MANIFEST_DDL,
    snapshot_path: str | None = DEFAULT_MANIFEST_SNAPSHOT,
) -> bytes:
    parameter_tables = ""
    if ddl_path or snapshot_path:
        attrs = ' name="PARAM_TRANSFER"'
        if ddl_path:
            attrs += f' ddl="{ddl_path}"'
        if snapshot_path:
            attrs += f' snapshot="{snapshot_path}" snapshot-date="2026-05-15"'
        parameter_tables = f"""
  <parameter-tables>
    <table{attrs}/>
  </parameter-tables>"""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<altamira-package schema-version="1.0">
  <country code="AR" name="Argentina"/>
  <application name="Transferencias"/>
  <operation logical-name="OP-TRF-PROPIA" description="Transferencia entre cuentas propias"/>
  <implementation version="3.2">
    <entry-program>ARTRFPROP01</entry-program>
  </implementation>
  <source format="FIXED" encoding="CP037"/>{parameter_tables}
</altamira-package>
""".encode()


def default_valid_entries() -> dict[str, bytes]:
    return {
        "manifest.xml": valid_manifest_xml(),
        "01-codigo/cobol/ARTRFPROP01.cbl": b"       IDENTIFICATION DIVISION.\n",
        "01-codigo/copybooks/CPYTRF01.cpy": b"       01 WS-TRF.\n",
        "01-codigo/dclgen/DCLTRF01.dcl": b"EXEC SQL DECLARE TRF TABLE\n",
        DEFAULT_MANIFEST_DDL: b"CREATE TABLE PARAM_TRANSFER (ID INT);\n",
        DEFAULT_MANIFEST_SNAPSHOT: b"ID,VALUE\n1,100\n",
    }


def _regular_file_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.date_time = (2026, 1, 1, 0, 0, 0)
    info.create_system = 3  # Unix: habilita bits de modo POSIX en external_attr
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def write_zip(
    path: Path,
    entries: dict[str, bytes] | None = None,
    *,
    raw_entries: Iterable[tuple[zipfile.ZipInfo, bytes]] = (),
) -> Path:
    """Escribe un ZIP con `entries` (nombre->bytes, con metadata de archivo
    regular por defecto) mas cualquier `raw_entries` con ZipInfo ya construido
    a mano (para simular tipos especiales, cifrado, symlinks, etc.)."""
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in (entries or {}).items():
            zf.writestr(_regular_file_info(name), data)
        for info, data in raw_entries:
            zf.writestr(info, data)
    return path


def build_valid_package_zip(path: Path, *, extra: dict[str, bytes] | None = None) -> Path:
    entries = default_valid_entries()
    if extra:
        entries.update(extra)
    return write_zip(path, entries)


def build_package_zip_with_raw_entry(
    path: Path, raw_entries: Iterable[tuple[zipfile.ZipInfo, bytes]]
) -> Path:
    """Paquete valido salvo por una o mas entradas crudas adicionales/reemplazadas."""
    entries = default_valid_entries()
    return write_zip(path, entries, raw_entries=raw_entries)


def symlink_entry(name: str, target: str) -> tuple[zipfile.ZipInfo, bytes]:
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    info.compress_type = zipfile.ZIP_STORED
    return info, target.encode()


def fifo_entry(name: str) -> tuple[zipfile.ZipInfo, bytes]:
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = (stat.S_IFIFO | 0o644) << 16
    info.compress_type = zipfile.ZIP_STORED
    return info, b""


def socket_entry(name: str) -> tuple[zipfile.ZipInfo, bytes]:
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = (stat.S_IFSOCK | 0o644) << 16
    info.compress_type = zipfile.ZIP_STORED
    return info, b""


def encrypted_entry(name: str, data: bytes = b"secret") -> tuple[zipfile.ZipInfo, bytes]:
    # zipfile.ZipFile reinicia flag_bits a 0 al escribir (ZipFile._open_to_write
    # hace `zinfo.flag_bits = 0x00`), asi que no alcanza con setear el bit
    # aqui: el bit de cifrado se parchea despues, en `mark_entry_encrypted`.
    return _regular_file_info(name), data


def mark_entry_encrypted(zip_path: Path, entry_name: str) -> None:
    """Activa el bit 0 (cifrado) del general purpose flag en el directorio central."""
    data = bytearray(zip_path.read_bytes())
    name_bytes = entry_name.encode()

    cdh_sig = b"PK\x01\x02"
    idx = data.find(cdh_sig)
    while idx != -1:
        name_len = struct.unpack_from("<H", data, idx + 28)[0]
        name = bytes(data[idx + 46 : idx + 46 + name_len])
        if name == name_bytes:
            current_flags = struct.unpack_from("<H", data, idx + 8)[0]
            struct.pack_into("<H", data, idx + 8, current_flags | 0x1)
            zip_path.write_bytes(bytes(data))
            return
        idx = data.find(cdh_sig, idx + 4)
    raise AssertionError(f"no se encontro el registro de directorio central para {entry_name!r}")


def raw_named_entry(raw_name: str, data: bytes = b"x") -> tuple[zipfile.ZipInfo, bytes]:
    """Entrada con un nombre crudo exacto (para nombres con backslash, UNC, etc.)."""
    info = zipfile.ZipInfo(raw_name)
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    info.compress_type = zipfile.ZIP_STORED
    return info, data


def corrupt_crc(zip_path: Path, entry_name: str) -> None:
    """Invalida el CRC-32 declarado en el directorio central de una entrada."""
    data = bytearray(zip_path.read_bytes())
    name_bytes = entry_name.encode()

    cdh_sig = b"PK\x01\x02"
    idx = data.find(cdh_sig)
    while idx != -1:
        name_len = struct.unpack_from("<H", data, idx + 28)[0]
        name = bytes(data[idx + 46 : idx + 46 + name_len])
        if name == name_bytes:
            current = struct.unpack_from("<I", data, idx + 16)[0]
            struct.pack_into("<I", data, idx + 16, current ^ 0xFFFFFFFF)
            zip_path.write_bytes(bytes(data))
            return
        idx = data.find(cdh_sig, idx + 4)
    raise AssertionError(f"no se encontro el registro de directorio central para {entry_name!r}")


def corrupt_declared_uncompressed_size(zip_path: Path, entry_name: str, fake_size: int) -> None:
    """Reescribe el tamano sin comprimir declarado en el directorio central,
    dejando el contenido real (y el tamano comprimido) intacto: simula una
    entrada que miente sobre su tamano declarado."""
    data = bytearray(zip_path.read_bytes())
    name_bytes = entry_name.encode()

    cdh_sig = b"PK\x01\x02"
    idx = data.find(cdh_sig)
    while idx != -1:
        name_len = struct.unpack_from("<H", data, idx + 28)[0]
        name = bytes(data[idx + 46 : idx + 46 + name_len])
        if name == name_bytes:
            struct.pack_into("<I", data, idx + 24, fake_size)
            zip_path.write_bytes(bytes(data))
            return
        idx = data.find(cdh_sig, idx + 4)
    raise AssertionError(f"no se encontro el registro de directorio central para {entry_name!r}")
