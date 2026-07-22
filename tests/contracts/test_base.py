"""Tests de los validadores compartidos (RelativePath, Sha256Hex)."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from altamira_extractor.contracts.base import RelativePath, Sha256Hex


class _PathHolder(BaseModel):
    path: RelativePath


class _HashHolder(BaseModel):
    value: Sha256Hex


@pytest.mark.parametrize(
    "value",
    ["cobol/PROG.cbl", "ddl/PARM01.sql", "a/b/c.txt"],
)
def test_relative_path_accepts_valid_relative_paths(value: str) -> None:
    assert _PathHolder(path=value).path == value


@pytest.mark.parametrize(
    "value",
    ["/etc/passwd", "C:\\Windows\\system32", "../secrets.txt", "a/../../b", ""],
)
def test_relative_path_rejects_absolute_or_traversal_paths(value: str) -> None:
    with pytest.raises(ValueError):
        _PathHolder(path=value)


def test_sha256_accepts_valid_hash() -> None:
    value = "a" * 64
    assert _HashHolder(value=value).value == value


@pytest.mark.parametrize(
    "value",
    [
        "a" * 63,
        "a" * 65,
        "A" * 64,
        "g" * 64,
        "not-a-hash",
        "",
    ],
)
def test_sha256_rejects_invalid_hash(value: str) -> None:
    with pytest.raises(ValueError):
        _HashHolder(value=value)
