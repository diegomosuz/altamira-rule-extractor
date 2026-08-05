"""Fixtures compartidas de tests/ui/ (Prompt 13d).

Reutiliza los builders de `tests/api/conftest.py` (mismo patron: crea
`data/runs/<run_id>/` a mano, sin JAR, sin Neo4j, sin LLM real) -- cero
duplicacion de esos helpers."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from altamira_extractor.api.app import create_app
from altamira_extractor.config import Settings

from ..e2e_support import write_disabled_dev_security_config

# same-origin valido para TestClient (base_url por defecto de httpx/starlette).
SAME_ORIGIN = "http://testserver"
FOREIGN_ORIGIN = "http://evil.example.org"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        runs_dir=tmp_path / "data" / "runs",
        incoming_dir=tmp_path / "data" / "incoming",
        security_config_path=write_disabled_dev_security_config(tmp_path),
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client
