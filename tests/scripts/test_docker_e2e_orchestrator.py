"""Unit tests del orquestador `scripts/docker_e2e.py` (Prompt 14b): sin
Docker real. Mockea el unico punto de invocacion de subprocess
(`scripts.docker_e2e._run`) para verificar: nombres unicos por
ejecucion, que el override reemplaza (no anexa) ports/env_file/volumes,
que `docker compose config --format json` se analiza de verdad (tercer
servicio, `./data` remanente, env_file ajeno), que todos los comandos
Compose comparten project_name/override/env-file, que la contrasena
nunca aparece como argumento de linea de comandos ni en la salida
impresa, que el aislamiento de red exige exactamente una red Internal,
que `docker wait` se parsea estrictamente como entero, que `config` usa
`--quiet` y que `create` usa un unico `--network`, que nunca se usa
`shell=True` ni se ejecuta ningun comando fuera de `docker`, y que el
cleanup se ejecuta tanto en exito como en cada fase de falla (sin dejar
archivos temporales ni tocar `./data`).

Prompt 14b.2: ademas, un pequeno grupo de tests ejercita `_run` de
verdad (sin mockear `subprocess.run`, pero sin Docker -- solo
`sys.executable -c ...`) para confirmar la politica de decodificacion
UTF-8/`errors="replace"` en el limite real de subprocess: acentos
validos se conservan, bytes invalidos no producen `UnicodeDecodeError`,
y la salida sigue siendo JSON parseable cuando corresponde."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
import scripts.docker_e2e as orchestrator

_TOP_OUTPUT_SINGLE_WORKER = (
    "SERVICE  #   UID    PID    PPID   C   STIME  TTY  TIME      CMD\n"
    "app      1   10001  100    99     1   00:00  ?    00:00:00  "
    "/sbin/docker-init -- uvicorn altamira_extractor.api.app:app_factory "
    "--factory --host 0.0.0.0 --port 8000 --workers 1\n"
    "app      1   10001  101    100    5   00:00  ?    00:00:01  "
    "/usr/local/bin/python3.12 /usr/local/bin/uvicorn "
    "altamira_extractor.api.app:app_factory --factory --host 0.0.0.0 "
    "--port 8000 --workers 1\n"
)


def _completed(
    cmd: Sequence[str], *, returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(list(cmd), returncode, stdout=stdout, stderr=stderr)


def _read_env_var(env_file_path: str, key: str) -> str | None:
    prefix = f"{key}="
    for line in Path(env_file_path).read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :]
    return None


class _RecordingRun:
    """Reemplaza `_run`: registra cada comando ejecutado y responde segun
    `handlers` (predicado -> respuesta fija, evaluados en orden); lo no
    manejado explicitamente cae en `_default_response`, que simula una
    ejecucion Docker exitosa "de camino feliz" completa (build, up,
    healthchecks, smoke, config JSON real, red interna, contenedor de
    test). El default de `config --format json` lee el archivo de env
    temporal REAL (`_write_temp_env` escribe un archivo de verdad, no
    mockeado) para reflejar la contrasena real de cada ejecucion, en vez
    de asumir un valor fijo."""

    def __init__(
        self,
        handlers: list[tuple[Callable[[list[str]], bool], subprocess.CompletedProcess[str]]]
        | None = None,
    ) -> None:
        self.calls: list[list[str]] = []
        self._handlers = handlers or []
        self.network_name: str | None = None

    def __call__(
        self, cmd: Sequence[str], *, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        cmd_list = list(cmd)
        self.calls.append(cmd_list)
        for predicate, response in self._handlers:
            if predicate(cmd_list):
                return response
        return self._default_response(cmd_list)

    def _default_response(self, cmd_list: list[str]) -> subprocess.CompletedProcess[str]:
        if cmd_list[:3] == ["docker", "network", "create"] and "--internal" in cmd_list:
            self.network_name = cmd_list[-1]
            return _completed(cmd_list)
        if cmd_list[:3] == ["docker", "network", "inspect"]:
            name = cmd_list[3]
            return _completed(cmd_list, stdout=json.dumps([{"Internal": True, "Name": name}]))
        if "config" in cmd_list and "--format" in cmd_list and "json" in cmd_list:
            env_file_path = cmd_list[cmd_list.index("--env-file") + 1]
            password = _read_env_var(env_file_path, "NEO4J_PASSWORD")
            payload = {
                "services": {
                    "app": {
                        "volumes": [
                            {
                                "type": "volume",
                                "source": "e2e_app_data",
                                "target": "/app/data",
                                "volume": {},
                            }
                        ],
                        "environment": {"NEO4J_PASSWORD": password},
                    },
                    "neo4j": {},
                }
            }
            return _completed(cmd_list, stdout=json.dumps(payload))
        if "ps" in cmd_list and "-q" in cmd_list:
            service = cmd_list[-1]
            return _completed(cmd_list, stdout=f"container-id-{service}\n")
        if cmd_list[:2] == ["docker", "inspect"]:
            payload = [
                {
                    "State": {"Health": {"Status": "healthy"}},
                    "NetworkSettings": {
                        "Networks": {self.network_name: {}} if self.network_name else {}
                    },
                }
            ]
            return _completed(cmd_list, stdout=json.dumps(payload))
        if cmd_list[-3:-1] == ["python", "-c"]:
            return _completed(cmd_list, stdout="SMOKE_APP_OK\n")
        if cmd_list[-2:] == ["java", "-version"]:
            return _completed(cmd_list, stderr='openjdk version "17.0.9" 2023-10-17\n')
        if "top" in cmd_list and cmd_list[-1:] == ["app"]:
            return _completed(cmd_list, stdout=_TOP_OUTPUT_SINGLE_WORKER)
        if cmd_list[:2] == ["docker", "wait"]:
            return _completed(cmd_list, stdout="0\n")
        if cmd_list[:2] == ["docker", "logs"]:
            return _completed(cmd_list, stdout="pytest ok\n")
        return _completed(cmd_list, stdout="")


def _capture_context(monkeypatch: pytest.MonkeyPatch) -> list[orchestrator.E2EContext]:
    captured: list[orchestrator.E2EContext] = []
    original = orchestrator._new_context

    def _wrapper(repo_root: Path, tmp_dir: Path) -> orchestrator.E2EContext:
        ctx = original(repo_root, tmp_dir)
        captured.append(ctx)
        return ctx

    monkeypatch.setattr(orchestrator, "_new_context", _wrapper)
    return captured


def _assert_cleanup_ran(recorder: _RecordingRun, ctx: orchestrator.E2EContext) -> None:
    """Las 4 acciones de cleanup que `_cleanup` intenta siempre
    (independientemente de en que fase fallo la ejecucion): eliminar el
    contenedor de test, `compose down -v --remove-orphans` con el
    project_name unico, eliminar la red interna y eliminar el tag de la
    imagen de test. Tambien confirma que ningun comando toco `./data`."""
    assert ["docker", "rm", "-f", ctx.test_container_name] in recorder.calls
    assert ["docker", "network", "rm", ctx.network_name] in recorder.calls
    assert ["docker", "image", "rm", ctx.test_image_tag] in recorder.calls

    down_calls = [
        cmd for cmd in recorder.calls if cmd[:2] == ["docker", "compose"] and "down" in cmd
    ]
    assert len(down_calls) == 1
    down_cmd = down_calls[0]
    assert down_cmd[down_cmd.index("-p") + 1] == ctx.project_name
    assert "-v" in down_cmd
    assert "--remove-orphans" in down_cmd

    assert not any("./data" in arg for cmd in recorder.calls for arg in cmd)


def test_context_names_are_unique_and_prefixed(tmp_path: Path) -> None:
    first = orchestrator._new_context(tmp_path, tmp_path)
    second = orchestrator._new_context(tmp_path, tmp_path)
    assert first.run_id != second.run_id
    for ctx in (first, second):
        assert ctx.project_name == f"altamira-e2e-{ctx.run_id}"
        assert ctx.network_name == f"altamira-e2e-internal-{ctx.run_id}"
        assert ctx.test_container_name == f"altamira-e2e-test-{ctx.run_id}"
        assert ctx.test_image_tag == f"altamira-rule-extractor-test:{ctx.run_id}"


def test_compose_base_args_are_identical_across_all_compose_invocations(tmp_path: Path) -> None:
    ctx = orchestrator._new_context(tmp_path, tmp_path)
    args = orchestrator._compose_base_args(ctx)
    assert args == [
        "docker",
        "compose",
        "-p",
        ctx.project_name,
        "-f",
        str(ctx.compose_file),
        "-f",
        str(ctx.override_file),
        "--env-file",
        str(ctx.env_file),
    ]


def test_write_temp_override_replaces_ports_env_file_and_volumes(tmp_path: Path) -> None:
    ctx = orchestrator._new_context(tmp_path, tmp_path)
    orchestrator._write_temp_override(ctx)
    content = ctx.override_file.read_text(encoding="utf-8")
    assert content.count("ports: !override []") == 2
    assert "env_file: !override" in content
    assert "volumes: !override" in content
    assert "e2e_app_data:/app/data" in content
    assert json.dumps(ctx.env_file.as_posix()) in content
    assert "./data" not in content


def test_compose_config_quiet_uses_quiet_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorder = _RecordingRun()
    monkeypatch.setattr(orchestrator, "_run", recorder)
    ctx = orchestrator._new_context(tmp_path, tmp_path)
    orchestrator._compose_config_quiet(ctx)
    assert recorder.calls[-1][-2:] == ["config", "--quiet"]


def test_create_internal_network_uses_internal_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorder = _RecordingRun()
    monkeypatch.setattr(orchestrator, "_run", recorder)
    ctx = orchestrator._new_context(tmp_path, tmp_path)
    orchestrator._create_internal_network(ctx)
    create_calls = [cmd for cmd in recorder.calls if cmd[:3] == ["docker", "network", "create"]]
    assert len(create_calls) == 1
    assert "--internal" in create_calls[0]
    assert create_calls[0][-1] == ctx.network_name


def test_create_internal_network_fails_when_internal_is_false(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ctx = orchestrator._new_context(tmp_path, tmp_path)

    def network_inspect(cmd: list[str]) -> bool:
        return cmd[:3] == ["docker", "network", "inspect"]

    response = _completed([], stdout=json.dumps([{"Internal": False, "Name": ctx.network_name}]))
    recorder = _RecordingRun(handlers=[(network_inspect, response)])
    monkeypatch.setattr(orchestrator, "_run", recorder)

    with pytest.raises(orchestrator.E2EError) as exc_info:
        orchestrator._create_internal_network(ctx)
    assert exc_info.value.phase == "network-inspect"


def test_create_test_container_uses_single_network_and_never_puts_password_inline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorder = _RecordingRun()
    monkeypatch.setattr(orchestrator, "_run", recorder)
    ctx = orchestrator._new_context(tmp_path, tmp_path)
    orchestrator._write_temp_env(ctx)
    orchestrator._create_test_container(ctx)

    create_calls = [cmd for cmd in recorder.calls if cmd[:2] == ["docker", "create"]]
    assert len(create_calls) == 1
    cmd = create_calls[0]
    assert cmd.count("--network") == 1
    assert cmd[cmd.index("--network") + 1] == ctx.network_name
    assert ctx.neo4j_password not in cmd
    assert cmd[cmd.index("--env-file") + 1] == str(ctx.env_file)


def test_assert_network_isolation_raises_on_zero_networks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ctx = orchestrator._new_context(tmp_path, tmp_path)

    def container_inspect(cmd: list[str]) -> bool:
        return cmd[:2] == ["docker", "inspect"]

    response = _completed(
        [], stdout=json.dumps([{"NetworkSettings": {"Networks": {}}, "State": {}}])
    )
    recorder = _RecordingRun(handlers=[(container_inspect, response)])
    monkeypatch.setattr(orchestrator, "_run", recorder)

    with pytest.raises(orchestrator.E2EError) as exc_info:
        orchestrator._assert_test_container_network_isolation(ctx)
    assert exc_info.value.phase == "test-inspect"


def test_assert_network_isolation_raises_on_multiple_networks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ctx = orchestrator._new_context(tmp_path, tmp_path)

    def multi_network(cmd: list[str]) -> bool:
        return cmd[:2] == ["docker", "inspect"]

    payload = [
        {
            "NetworkSettings": {"Networks": {ctx.network_name: {}, "bridge": {}}},
            "State": {},
        }
    ]
    response = _completed([], stdout=json.dumps(payload))
    recorder = _RecordingRun(handlers=[(multi_network, response)])
    monkeypatch.setattr(orchestrator, "_run", recorder)

    with pytest.raises(orchestrator.E2EError) as exc_info:
        orchestrator._assert_test_container_network_isolation(ctx)
    assert exc_info.value.phase == "test-inspect"


def test_verify_compose_config_structure_fails_on_third_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ctx = orchestrator._new_context(tmp_path, tmp_path)
    orchestrator._write_temp_env(ctx)

    def config_json(cmd: list[str]) -> bool:
        return "config" in cmd and "--format" in cmd

    payload = {
        "services": {
            "app": {
                "volumes": [{"type": "volume", "target": "/app/data"}],
                "environment": {"NEO4J_PASSWORD": ctx.neo4j_password},
            },
            "neo4j": {},
            "redis": {},
        }
    }
    response = _completed([], stdout=json.dumps(payload))
    recorder = _RecordingRun(handlers=[(config_json, response)])
    monkeypatch.setattr(orchestrator, "_run", recorder)

    with pytest.raises(orchestrator.E2EError) as exc_info:
        orchestrator._verify_compose_config_structure(ctx)
    assert exc_info.value.phase == "compose-config-structure"


def test_verify_compose_config_structure_fails_when_data_bind_mount_remains(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ctx = orchestrator._new_context(tmp_path, tmp_path)
    orchestrator._write_temp_env(ctx)

    def config_json(cmd: list[str]) -> bool:
        return "config" in cmd and "--format" in cmd

    payload = {
        "services": {
            "app": {
                "volumes": [{"type": "bind", "source": "./data", "target": "/app/data"}],
                "environment": {"NEO4J_PASSWORD": ctx.neo4j_password},
            },
            "neo4j": {},
        }
    }
    response = _completed([], stdout=json.dumps(payload))
    recorder = _RecordingRun(handlers=[(config_json, response)])
    monkeypatch.setattr(orchestrator, "_run", recorder)

    with pytest.raises(orchestrator.E2EError) as exc_info:
        orchestrator._verify_compose_config_structure(ctx)
    assert exc_info.value.phase == "compose-config-structure"
    assert "./data" in str(exc_info.value)


def test_verify_compose_config_structure_fails_when_env_file_is_not_the_temp_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ctx = orchestrator._new_context(tmp_path, tmp_path)
    orchestrator._write_temp_env(ctx)

    def config_json(cmd: list[str]) -> bool:
        return "config" in cmd and "--format" in cmd

    payload = {
        "services": {
            "app": {
                "volumes": [{"type": "volume", "target": "/app/data"}],
                # Contrasena distinta de ctx.neo4j_password: simula que
                # el env_file resuelto fue el .env del repositorio (o el
                # de una ejecucion anterior), no el temporal de ESTA.
                "environment": {"NEO4J_PASSWORD": "a-different-password-entirely"},
            },
            "neo4j": {},
        }
    }
    response = _completed([], stdout=json.dumps(payload))
    recorder = _RecordingRun(handlers=[(config_json, response)])
    monkeypatch.setattr(orchestrator, "_run", recorder)

    with pytest.raises(orchestrator.E2EError) as exc_info:
        orchestrator._verify_compose_config_structure(ctx)
    assert exc_info.value.phase == "compose-config-structure"


def test_verify_compose_config_structure_fails_when_ports_still_published(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ctx = orchestrator._new_context(tmp_path, tmp_path)
    orchestrator._write_temp_env(ctx)

    def config_json(cmd: list[str]) -> bool:
        return "config" in cmd and "--format" in cmd

    payload = {
        "services": {
            "app": {
                "ports": [{"target": 8000, "published": "8000"}],
                "volumes": [{"type": "volume", "target": "/app/data"}],
                "environment": {"NEO4J_PASSWORD": ctx.neo4j_password},
            },
            "neo4j": {},
        }
    }
    response = _completed([], stdout=json.dumps(payload))
    recorder = _RecordingRun(handlers=[(config_json, response)])
    monkeypatch.setattr(orchestrator, "_run", recorder)

    with pytest.raises(orchestrator.E2EError) as exc_info:
        orchestrator._verify_compose_config_structure(ctx)
    assert exc_info.value.phase == "compose-config-structure"


def test_docker_wait_non_numeric_output_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ctx = orchestrator._new_context(tmp_path, tmp_path)

    def wait_call(cmd: list[str]) -> bool:
        return cmd[:2] == ["docker", "wait"]

    response = _completed([], stdout="not-a-number\n")
    recorder = _RecordingRun(handlers=[(wait_call, response)])
    monkeypatch.setattr(orchestrator, "_run", recorder)

    with pytest.raises(orchestrator.E2EError) as exc_info:
        orchestrator._start_and_wait_test_container(ctx)
    assert exc_info.value.phase == "test-wait"


def test_nonzero_test_exit_code_propagates_exactly(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_context(monkeypatch)

    def wait_call(cmd: list[str]) -> bool:
        return cmd[:2] == ["docker", "wait"]

    # Un valor distinto de 0 y de 1 (el codigo de retorno generico de
    # una E2EError) para distinguir sin ambiguedad que lo que se
    # propaga es EXACTAMENTE el exit code real de `docker wait`.
    response = _completed([], stdout="3\n")
    recorder = _RecordingRun(handlers=[(wait_call, response)])
    monkeypatch.setattr(orchestrator, "_run", recorder)

    exit_code = orchestrator.main()

    assert exit_code == 3
    _assert_cleanup_ran(recorder, captured[0])


def test_sanitize_redacts_password_and_temp_paths(tmp_path: Path) -> None:
    ctx = orchestrator._new_context(tmp_path, tmp_path)
    text = (
        f"password={ctx.neo4j_password} env={ctx.env_file} "
        f"override={ctx.override_file} tmp={ctx.tmp_dir}"
    )
    sanitized = orchestrator._sanitize(text, ctx)
    assert ctx.neo4j_password not in sanitized
    assert str(ctx.env_file) not in sanitized
    assert str(ctx.override_file) not in sanitized
    assert str(ctx.tmp_dir) not in sanitized


def test_printed_output_never_leaks_password_or_temp_dir_on_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured = _capture_context(monkeypatch)

    def compose_up_fails(cmd: list[str]) -> bool:
        return "up" in cmd and "--no-build" in cmd

    response = _completed(
        [], returncode=1, stderr="fallo con password=leaked-in-error-output-XYZ"
    )
    recorder = _RecordingRun(handlers=[(compose_up_fails, response)])
    monkeypatch.setattr(orchestrator, "_run", recorder)

    orchestrator.main()

    ctx = captured[0]
    output = capsys.readouterr()
    combined = output.out + output.err
    assert ctx.neo4j_password not in combined
    assert str(ctx.tmp_dir) not in combined


def test_full_run_succeeds_cleans_up_and_leaves_no_temp_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_context(monkeypatch)
    recorder = _RecordingRun()
    monkeypatch.setattr(orchestrator, "_run", recorder)

    exit_code = orchestrator.main()

    assert exit_code == 0
    ctx = captured[0]
    _assert_cleanup_ran(recorder, ctx)
    assert not ctx.tmp_dir.exists()


def test_password_never_appears_in_any_command_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed_password = "sentinel-password-0123456789ab"
    monkeypatch.setattr(orchestrator.secrets, "token_hex", lambda n: fixed_password)
    recorder = _RecordingRun()
    monkeypatch.setattr(orchestrator, "_run", recorder)

    exit_code = orchestrator.main()

    assert exit_code == 0
    for cmd in recorder.calls:
        for arg in cmd:
            assert fixed_password not in arg


def test_every_command_invoked_is_docker_never_an_external_contact_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ademas de no contactar ningun host externo por diseno, esto
    prueba que el orquestador nunca ejecuta un binario distinto de
    `docker` (nada de `curl`/`wget`/`ping`/`nslookup`/`dig`, que serian
    la unica forma de intentar un contacto de red activo)."""
    recorder = _RecordingRun()
    monkeypatch.setattr(orchestrator, "_run", recorder)

    orchestrator.main()

    assert recorder.calls
    for cmd in recorder.calls:
        assert cmd[0] == "docker"


def test_no_shell_true_anywhere_in_the_orchestrator_source() -> None:
    """AST, no un substring: el propio docstring del modulo menciona
    "nunca `shell=True`" en prosa, que un `in source` ingenuo marcaria
    como falso positivo. Se camina el arbol sintactico real y solo se
    objeta un `shell=True` que aparezca como keyword argument de una
    llamada de verdad."""
    source = Path(orchestrator.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "shell":
            assert not (isinstance(node.value, ast.Constant) and node.value.value is True)


def test_cleanup_runs_when_compose_config_quiet_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_context(monkeypatch)

    def quiet_fails(cmd: list[str]) -> bool:
        return "--quiet" in cmd

    response = _completed([], returncode=1, stderr="unsupported merge key '!override'")
    recorder = _RecordingRun(handlers=[(quiet_fails, response)])
    monkeypatch.setattr(orchestrator, "_run", recorder)

    exit_code = orchestrator.main()

    assert exit_code == 1
    _assert_cleanup_ran(recorder, captured[0])


def test_cleanup_runs_when_test_image_build_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_context(monkeypatch)

    def build_fails(cmd: list[str]) -> bool:
        return cmd[:2] == ["docker", "build"]

    response = _completed([], returncode=1, stderr="build boom")
    recorder = _RecordingRun(handlers=[(build_fails, response)])
    monkeypatch.setattr(orchestrator, "_run", recorder)

    exit_code = orchestrator.main()

    assert exit_code == 1
    _assert_cleanup_ran(recorder, captured[0])


def test_cleanup_runs_when_compose_up_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_context(monkeypatch)

    def up_fails(cmd: list[str]) -> bool:
        return "up" in cmd and "--no-build" in cmd

    response = _completed([], returncode=1, stderr="up boom")
    recorder = _RecordingRun(handlers=[(up_fails, response)])
    monkeypatch.setattr(orchestrator, "_run", recorder)

    exit_code = orchestrator.main()

    assert exit_code == 1
    _assert_cleanup_ran(recorder, captured[0])


def test_cleanup_runs_when_neo4j_health_wait_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_context(monkeypatch)
    monkeypatch.setattr(orchestrator, "_HEALTH_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(orchestrator, "_HEALTH_POLL_INTERVAL_SECONDS", 0.01)

    def always_unhealthy(cmd: list[str]) -> bool:
        return cmd[:2] == ["docker", "inspect"]

    payload = [{"State": {"Health": {"Status": "unhealthy"}}, "NetworkSettings": {"Networks": {}}}]
    response = _completed([], stdout=json.dumps(payload))
    recorder = _RecordingRun(handlers=[(always_unhealthy, response)])
    monkeypatch.setattr(orchestrator, "_run", recorder)

    exit_code = orchestrator.main()

    assert exit_code == 1
    _assert_cleanup_ran(recorder, captured[0])


def test_cleanup_runs_when_app_smoke_check_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_context(monkeypatch)

    def python_smoke_fails(cmd: list[str]) -> bool:
        return cmd[-3:-1] == ["python", "-c"]

    response = _completed([], returncode=1, stderr="assertion failed")
    recorder = _RecordingRun(handlers=[(python_smoke_fails, response)])
    monkeypatch.setattr(orchestrator, "_run", recorder)

    exit_code = orchestrator.main()

    assert exit_code == 1
    _assert_cleanup_ran(recorder, captured[0])


def test_cleanup_runs_when_internal_network_create_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_context(monkeypatch)

    def network_create_fails(cmd: list[str]) -> bool:
        return cmd[:3] == ["docker", "network", "create"]

    response = _completed([], returncode=1, stderr="network create boom")
    recorder = _RecordingRun(handlers=[(network_create_fails, response)])
    monkeypatch.setattr(orchestrator, "_run", recorder)

    exit_code = orchestrator.main()

    assert exit_code == 1
    _assert_cleanup_ran(recorder, captured[0])


def test_cleanup_runs_when_test_container_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_context(monkeypatch)

    def wait_fails(cmd: list[str]) -> bool:
        return cmd[:2] == ["docker", "wait"]

    response = _completed([], stdout="1\n")
    recorder = _RecordingRun(handlers=[(wait_fails, response)])
    monkeypatch.setattr(orchestrator, "_run", recorder)

    exit_code = orchestrator.main()

    assert exit_code == 1
    _assert_cleanup_ran(recorder, captured[0])


def test_cleanup_tolerates_partially_created_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si el contenedor de test y la red nunca llegaron a crearse (falla
    temprana, antes de network-create), el cleanup igual intenta los 5
    pasos sin lanzar -- cada uno reporta "no encontrado" como warning no
    fatal en vez de abortar los pasos siguientes."""
    captured = _capture_context(monkeypatch)

    def fails_immediately(cmd: list[str]) -> bool:
        return "--quiet" in cmd

    not_found = _completed([], returncode=1, stderr="no such resource")
    recorder = _RecordingRun(handlers=[(fails_immediately, not_found)])
    monkeypatch.setattr(orchestrator, "_run", recorder)

    exit_code = orchestrator.main()

    assert exit_code == 1
    ctx = captured[0]
    # Los 5 comandos de cleanup se intentaron pese a que nada existia
    # realmente (el mock generico responde returncode=0 salvo que un
    # handler especifico diga lo contrario, asi que aqui se afirma
    # unicamente que las LLAMADAS se hicieron, no que "tuvieron exito").
    assert ["docker", "rm", "-f", ctx.test_container_name] in recorder.calls
    assert ["docker", "network", "rm", ctx.network_name] in recorder.calls
    assert ["docker", "image", "rm", ctx.test_image_tag] in recorder.calls


# --- Prompt 14b.2: politica de decodificacion UTF-8 en el limite de subprocess ---


def test_run_invokes_subprocess_with_utf8_replace_policy_and_no_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifica los kwargs reales pasados a `subprocess.run` (se mockea
    `subprocess.run` en si, no `orchestrator._run`, precisamente para
    poder inspeccionar como `_run` lo invoca)."""
    captured_kwargs: dict[str, object] = {}

    def fake_subprocess_run(
        cmd: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured_kwargs.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(orchestrator.subprocess, "run", fake_subprocess_run)

    orchestrator._run(["docker", "version"])

    assert captured_kwargs["text"] is True
    assert captured_kwargs["encoding"] == "utf-8"
    assert captured_kwargs["errors"] == "replace"
    assert captured_kwargs["capture_output"] is True
    assert captured_kwargs.get("shell") is not True


def test_run_preserves_valid_utf8_accented_output() -> None:
    """`_run` real (sin mockear `subprocess.run`), sin Docker: un
    subproceso Python que escribe bytes UTF-8 directamente a su buffer
    (para no depender de la codificacion por defecto de SU propio
    stdout) debe decodificarse preservando los acentos."""
    script = (
        "import sys; sys.stdout.buffer.write('café ñ éxito'.encode('utf-8')); "
        "sys.stdout.buffer.flush()"
    )
    result = orchestrator._run([sys.executable, "-c", script])

    assert result.returncode == 0
    assert result.stdout == "café ñ éxito"


def test_run_replaces_invalid_utf8_bytes_without_raising() -> None:
    """Un byte que nunca es valido por si solo en UTF-8 (0x81, el mismo
    byte del traceback real de Windows) no debe producir
    `UnicodeDecodeError` en `_run`: se reemplaza por U+FFFD."""
    script = "import sys; sys.stdout.buffer.write(bytes([0x81])); sys.stdout.buffer.flush()"
    result = orchestrator._run([sys.executable, "-c", script])

    assert result.returncode == 0
    assert "�" in result.stdout


def test_run_output_still_parses_as_json_when_valid() -> None:
    script = "import sys, json; sys.stdout.write(json.dumps({'clave': 'café'}))"
    result = orchestrator._run([sys.executable, "-c", script])

    assert result.returncode == 0
    assert json.loads(result.stdout) == {"clave": "café"}


def test_verify_compose_config_structure_fails_on_invalid_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ctx = orchestrator._new_context(tmp_path, tmp_path)
    orchestrator._write_temp_env(ctx)

    def config_json(cmd: list[str]) -> bool:
        return "config" in cmd and "--format" in cmd

    # Simula el resultado de un byte invalido ya reemplazado por
    # U+FFFD: la captura no fallo, pero el contenido no es JSON valido.
    response = _completed([], stdout="esto no es json � garbled")
    recorder = _RecordingRun(handlers=[(config_json, response)])
    monkeypatch.setattr(orchestrator, "_run", recorder)

    with pytest.raises(orchestrator.E2EError) as exc_info:
        orchestrator._verify_compose_config_structure(ctx)
    assert exc_info.value.phase == "compose-config-structure"


def test_sanitize_hides_secrets_even_when_replacement_characters_present(
    tmp_path: Path,
) -> None:
    ctx = orchestrator._new_context(tmp_path, tmp_path)
    text = f"antes � password={ctx.neo4j_password} � despues"

    sanitized = orchestrator._sanitize(text, ctx)

    assert ctx.neo4j_password not in sanitized
    # Los caracteres de reemplazo no son secretos: no se tocan.
    assert "�" in sanitized


def test_cleanup_unaffected_by_replacement_characters_in_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_context(monkeypatch)

    def up_fails(cmd: list[str]) -> bool:
        return "up" in cmd and "--no-build" in cmd

    response = _completed([], returncode=1, stderr="boom � salida con caracteres reemplazados")
    recorder = _RecordingRun(handlers=[(up_fails, response)])
    monkeypatch.setattr(orchestrator, "_run", recorder)

    exit_code = orchestrator.main()

    assert exit_code == 1
    _assert_cleanup_ran(recorder, captured[0])
