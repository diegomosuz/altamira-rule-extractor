"""Tests de la extension de `/ready` (Fase 15B2-B, Seccion 11): JAR del
parser, data root, executor -- preservando exactamente el
comportamiento previo de `security_configuration` (cubierto ya por
`tests/ui/test_security_misconfigured.py`, no duplicado aqui)."""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from altamira_extractor.api.app import create_app
from altamira_extractor.config import Settings
from tests.e2e_support import write_disabled_dev_security_config


def _settings(tmp_path: Path, *, parser_jar_path: Path, runs_dir: Path | None = None) -> Settings:
    return Settings(
        runs_dir=runs_dir if runs_dir is not None else tmp_path / "data" / "runs",
        incoming_dir=tmp_path / "incoming",
        security_config_path=write_disabled_dev_security_config(tmp_path),
        parser_jar_path=parser_jar_path,
    )


@pytest.fixture
def fake_jar(tmp_path: Path) -> Path:
    jar = tmp_path / "fake-parser.jar"
    jar.write_bytes(b"not a real jar, only needs to exist as a regular file")
    return jar


def test_ready_true_when_all_checks_pass(tmp_path: Path, fake_jar: Path) -> None:
    runs_dir = tmp_path / "data" / "runs"
    runs_dir.mkdir(parents=True)
    settings = _settings(tmp_path, parser_jar_path=fake_jar, runs_dir=runs_dir)
    with TestClient(create_app(settings)) as client:
        response = client.get("/ready")
        assert response.status_code == 200
        body = response.json()
        assert body["ready"] is True
        assert body["reason"] is None
        statuses = {c["component_id"]: c["status"] for c in body["checks"]}
        assert statuses == {
            "security_configuration": "READY",
            "parser_jar": "READY",
            "data_root": "READY",
            "executor": "READY",
        }


def test_ready_true_when_runs_dir_not_yet_created_but_parent_exists(
    tmp_path: Path, fake_jar: Path
) -> None:
    """Instalacion nueva (cero runs todavia): `runs_dir` en si no
    existe, pero su padre si -- nunca se exige crear el directorio de
    antemano."""
    (tmp_path / "data").mkdir()
    settings = _settings(tmp_path, parser_jar_path=fake_jar, runs_dir=tmp_path / "data" / "runs")
    with TestClient(create_app(settings)) as client:
        response = client.get("/ready")
        assert response.status_code == 200
        assert response.json()["ready"] is True


def test_ready_false_when_parser_jar_missing(tmp_path: Path) -> None:
    runs_dir = tmp_path / "data" / "runs"
    runs_dir.mkdir(parents=True)
    settings = _settings(
        tmp_path, parser_jar_path=tmp_path / "does-not-exist.jar", runs_dir=runs_dir
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/ready")
        assert response.status_code == 503
        body = response.json()
        assert body["ready"] is False
        assert body["reason"] == "parser_jar_missing"
        parser_jar_check = next(c for c in body["checks"] if c["component_id"] == "parser_jar")
        assert parser_jar_check["status"] == "NOT_READY"
        assert parser_jar_check["reason_code"] == "parser_jar_missing"


def test_ready_false_when_data_root_and_parent_both_missing(tmp_path: Path, fake_jar: Path) -> None:
    settings = _settings(
        tmp_path,
        parser_jar_path=fake_jar,
        runs_dir=tmp_path / "does-not-exist-either" / "runs",
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/ready")
        assert response.status_code == 503
        body = response.json()
        assert body["ready"] is False
        assert body["reason"] == "data_root_unavailable"


def test_ready_never_exposes_absolute_paths(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        parser_jar_path=tmp_path / "does-not-exist.jar",
        runs_dir=tmp_path / "data" / "runs",
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/ready")
        assert str(tmp_path) not in response.text


def test_ready_checks_list_always_has_all_four_components_even_when_passing(
    tmp_path: Path, fake_jar: Path
) -> None:
    runs_dir = tmp_path / "data" / "runs"
    runs_dir.mkdir(parents=True)
    settings = _settings(tmp_path, parser_jar_path=fake_jar, runs_dir=runs_dir)
    with TestClient(create_app(settings)) as client:
        body = client.get("/ready").json()
        assert len(body["checks"]) == 4


def test_health_endpoint_unaffected_by_readiness_extension(tmp_path: Path) -> None:
    """`/health` sigue siendo liveness pura -- nunca depende del JAR/
    data root/executor que ahora si condicionan `/ready`."""
    settings = _settings(
        tmp_path,
        parser_jar_path=tmp_path / "does-not-exist.jar",
        runs_dir=tmp_path / "does-not-exist" / "runs",
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


# --- Cierre correctivo, Seccion 4: JAR ausente/presente sin efectos ----------
# secundarios (sin path absoluto en logs, cero archivos creados, cero red).


def test_ready_never_exposes_absolute_paths_in_logs(
    tmp_path: Path, fake_jar: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs_dir = tmp_path / "data" / "runs"
    runs_dir.mkdir(parents=True)
    settings = _settings(tmp_path, parser_jar_path=fake_jar, runs_dir=runs_dir)
    with TestClient(create_app(settings)) as client:
        capsys.readouterr()
        client.get("/ready")
        captured = capsys.readouterr()
        combined = captured.err + captured.out
        assert str(tmp_path) not in combined
        for line in combined.strip().splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            assert str(tmp_path) not in json.dumps(record)


def test_ready_check_creates_zero_files_regardless_of_jar_presence(tmp_path: Path) -> None:
    runs_dir = tmp_path / "data" / "runs"
    runs_dir.mkdir(parents=True)
    missing_jar = tmp_path / "does-not-exist.jar"
    settings = _settings(tmp_path, parser_jar_path=missing_jar, runs_dir=runs_dir)
    before = {p for p in tmp_path.rglob("*")}
    with TestClient(create_app(settings)) as client:
        client.get("/ready")
        client.get("/ready")
    after = {p for p in tmp_path.rglob("*")}
    assert before == after
    assert not missing_jar.exists()


def test_ready_check_never_opens_a_network_socket(tmp_path: Path, fake_jar: Path) -> None:
    """El chequeo de `/ready` (config de seguridad, JAR, data root,
    executor) es puramente local -- ninguno de los 4 chequeos debe
    intentar abrir un socket (a diferencia de
    `/api/operations/component-diagnostics`, que si intenta Neo4j). El
    guard se instala DESPUES de entrar al `with TestClient(...)`: en
    Windows, `anyio`/`asyncio.ProactorEventLoop` abren un self-pipe via
    `socket.socketpair()` (que internamente usa `socket.connect()` de
    loopback) al construir el event loop -- eso es IPC interno del
    runtime, nunca una conexion de red real, y no debe confundirse con
    lo que este test verifica."""
    runs_dir = tmp_path / "data" / "runs"
    runs_dir.mkdir(parents=True)
    settings = _settings(tmp_path, parser_jar_path=fake_jar, runs_dir=runs_dir)

    with TestClient(create_app(settings)) as client:
        real_connect = socket.socket.connect

        def _guarded_connect(self: socket.socket, *args: object, **kwargs: object) -> object:
            raise AssertionError("intento de conexion de red prohibido durante /ready")

        socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
        try:
            response = client.get("/ready")
        finally:
            socket.socket.connect = real_connect  # type: ignore[method-assign]
        assert response.status_code == 200
