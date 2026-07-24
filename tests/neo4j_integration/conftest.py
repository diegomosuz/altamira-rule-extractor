"""Fixtures de integracion contra un servidor Neo4j 5 real.

Estos tests NUNCA levantan ni gestionan el contenedor Neo4j (no invocan
`docker run` ni administran Docker de ninguna forma): leen
`ALTAMIRA_TEST_NEO4J_URI` / `_USER` / `_PASSWORD` / `_DATABASE` del
entorno y se saltan con un mensaje descriptivo si faltan. Levantar un
Neo4j 5 real y exportar esas variables es responsabilidad del operador
(o del pipeline de CI) antes de correr `pytest -m neo4j_integration`.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import neo4j
import pytest

from altamira_extractor.config import Settings

_ENV_URI = "ALTAMIRA_TEST_NEO4J_URI"
_ENV_USER = "ALTAMIRA_TEST_NEO4J_USER"
_ENV_PASSWORD = "ALTAMIRA_TEST_NEO4J_PASSWORD"
_ENV_DATABASE = "ALTAMIRA_TEST_NEO4J_DATABASE"


def _require_env() -> tuple[str, str, str, str]:
    uri = os.environ.get(_ENV_URI)
    user = os.environ.get(_ENV_USER)
    password = os.environ.get(_ENV_PASSWORD)
    database = os.environ.get(_ENV_DATABASE, "neo4j")
    if not uri or not user or not password:
        pytest.skip(
            f"requiere {_ENV_URI}/{_ENV_USER}/{_ENV_PASSWORD} apuntando a un servidor Neo4j "
            "5 real ya en ejecucion; estos tests nunca levantan Docker por si mismos"
        )
    return uri, user, password, database


@pytest.fixture
def neo4j_test_settings(tmp_path: Path) -> Settings:
    uri, user, password, database = _require_env()
    return Settings(
        data_dir=tmp_path / "data",
        runs_dir=tmp_path / "data" / "runs",
        incoming_dir=tmp_path / "data" / "incoming",
        NEO4J_URI=uri,
        NEO4J_USER=user,
        NEO4J_PASSWORD=password,
        NEO4J_DATABASE=database,
    )


@pytest.fixture
def neo4j_driver(neo4j_test_settings: Settings) -> Iterator[neo4j.Driver]:
    driver = neo4j.GraphDatabase.driver(
        neo4j_test_settings.neo4j_uri,
        auth=(
            neo4j_test_settings.neo4j_user,
            neo4j_test_settings.neo4j_password.get_secret_value(),
        ),
    )
    try:
        yield driver
    finally:
        driver.close()


def _delete_managed(driver: neo4j.Driver, database: str) -> None:
    with driver.session(database=database) as session:
        session.run("MATCH (n) WHERE n._altamira_managed = true DETACH DELETE n").consume()


@pytest.fixture
def clean_managed_graph(
    neo4j_driver: neo4j.Driver, neo4j_test_settings: Settings
) -> Iterator[None]:
    """Limpia unicamente nodos/relaciones `_altamira_managed=true` antes y
    despues de cada test; nunca toca datos ajenos (foreign) que pudieran
    coexistir en la misma base."""
    _delete_managed(neo4j_driver, neo4j_test_settings.neo4j_database)
    yield
    _delete_managed(neo4j_driver, neo4j_test_settings.neo4j_database)
