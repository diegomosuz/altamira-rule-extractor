"""Orquestador del E2E Docker sin internet (Prompt 14b).

Construye la imagen `runtime` (via `docker compose build app`) y el
target `test` del mismo `Dockerfile` (via `docker build --target test`),
levanta `app`+`neo4j` con un proyecto Compose y un override temporales
(nunca versionados, nunca dentro del repo), valida el contenedor `app`
por separado (healthcheck + smoke HTTP interno), y corre el recorrido
completo del pipeline (`tests/docker/test_docker_e2e.py`) dentro de una
imagen de test separada, conectada UNICAMENTE a una red Docker interna
(`--internal`, sin salida a Internet) donde el unico host alcanzable es
el `neo4j` real del proyecto temporal.

Solo biblioteca estandar: `subprocess` (listas de argumentos, nunca
`shell=True`), `pathlib`, `tempfile`, `time`, `uuid`, `secrets`, `json`.
Sin Docker SDK, sin `requests`, sin PyYAML, sin dependencias nuevas.

No agrega un tercer servicio a `docker-compose.yml` (nunca se modifica:
el override temporal vive fuera del repositorio). No modifica produccion
para soportar un proveedor LLM falso: el fake vive unicamente dentro del
proceso pytest que corre en la imagen de test (`tests/e2e_support.py`,
`tests/docker/test_docker_e2e.py`).
"""

from __future__ import annotations

import json
import re
import secrets
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

_HEALTH_TIMEOUT_SECONDS = 180.0
_HEALTH_POLL_INTERVAL_SECONDS = 2.0
_TEST_WAIT_TIMEOUT_SECONDS = 1200.0

_APP_SMOKE_SCRIPT = """
import json
import os
import sys
import urllib.request
from pathlib import Path

assert sys.version_info[:2] == (3, 12), sys.version_info
assert os.getuid() != 0, "app corre como root"
assert Path("parser/target/altamira-cobol-parser.jar").is_file(), "JAR ausente"
assert not Path("tests").exists(), "el target runtime no debe incluir tests/ (solo el target test)"

from altamira_extractor import ui
assert (ui.TEMPLATES_DIR / "base.html").is_file()
assert (ui.STATIC_DIR / "app.css").is_file()
assert (ui.STATIC_DIR / "htmx.min.js").is_file()

from altamira_extractor.api.app import app_factory
app = app_factory()
assert app.title


def _get(path):
    with urllib.request.urlopen(f"http://127.0.0.1:8000{path}", timeout=5) as response:
        return response.status, response.read()


status, body = _get("/health")
assert status == 200
assert json.loads(body) == {"status": "ok"}

status, body = _get("/ui/runs")
assert status == 200
assert "<html" in body.decode("utf-8").lower()

status, body = _get("/openapi.json")
assert status == 200
spec = json.loads(body)
assert not [p for p in spec["paths"] if p.startswith("/ui")]

print("SMOKE_APP_OK")
"""


class E2EError(Exception):
    """Fallo de una fase del orquestador. `phase` identifica la fase para
    el reporte final (nunca incluye secretos: los mensajes ya pasan por
    `_sanitize` antes de construir la excepcion)."""

    def __init__(self, phase: str, message: str) -> None:
        super().__init__(message)
        self.phase = phase
        self.message = message


@dataclass(frozen=True)
class E2EContext:
    repo_root: Path
    run_id: str
    project_name: str
    network_name: str
    test_container_name: str
    test_image_tag: str
    compose_file: Path
    override_file: Path
    env_file: Path
    neo4j_password: str
    tmp_dir: Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _new_context(repo_root: Path, tmp_dir: Path) -> E2EContext:
    run_id = uuid.uuid4().hex[:8]
    return E2EContext(
        repo_root=repo_root,
        run_id=run_id,
        project_name=f"altamira-e2e-{run_id}",
        network_name=f"altamira-e2e-internal-{run_id}",
        test_container_name=f"altamira-e2e-test-{run_id}",
        test_image_tag=f"altamira-rule-extractor-test:{run_id}",
        compose_file=repo_root / "docker-compose.yml",
        override_file=tmp_dir / "compose.override.e2e.yml",
        env_file=tmp_dir / "e2e.env",
        # hex puro: satisface longitud minima de Neo4j, ningun caracter
        # problematico para dotenv (sin '=', espacios, comillas ni '#'),
        # unico por ejecucion.
        neo4j_password=secrets.token_hex(12),
        tmp_dir=tmp_dir,
    )


def _sanitize(text: str, ctx: E2EContext) -> str:
    """Reemplaza, en este orden (mas especifico primero), la contrasena
    temporal y toda ruta absoluta temporal antes de imprimir cualquier
    salida de un comando."""
    redacted = text
    redacted = redacted.replace(ctx.neo4j_password, "<redacted-password>")
    redacted = redacted.replace(str(ctx.env_file), "<redacted-env-file>")
    redacted = redacted.replace(str(ctx.override_file), "<redacted-override-file>")
    redacted = redacted.replace(str(ctx.tmp_dir), "<redacted-temp-dir>")
    return redacted


def _run(cmd: Sequence[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    """Unico punto de invocacion de subprocess: listas de argumentos,
    nunca `shell=True`. Mockeado por los tests unitarios del
    orquestador.

    Prompt 14b.2: `text=True` sin `encoding` explicito decodifica
    stdout/stderr con `locale.getpreferredencoding()` -- en Windows, la
    pagina de codigos regional (tipicamente cp1252), no UTF-8. La salida
    real de Docker/Docker Compose (JSON, logs, mensajes con acentos)
    viene en UTF-8, asi que un byte fuera de cp1252 hacia fallar con
    `UnicodeDecodeError` dentro de los hilos internos de captura de
    `subprocess`. `encoding="utf-8"` fija la codificacion real sin
    depender del locale del sistema; `errors="replace"` evita que la
    CAPTURA en si falle ante un byte invalido (lo sustituye por el
    caracter de reemplazo unicode U+FFFD) -- nunca hace que un JSON
    realmente invalido se acepte como valido, ni oculta un `returncode`
    distinto de 0: cada llamador que parsea JSON sigue capturando
    `json.JSONDecodeError` por separado y fallando con un `E2EError`
    sanitizado."""
    return subprocess.run(
        list(cmd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
    )


def _compose_base_args(ctx: E2EContext) -> list[str]:
    """Base identica para build/up/ps/exec/down: mismo proyecto, mismos
    archivos Compose+override, mismo env-file temporal."""
    return [
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


def _write_temp_env(ctx: E2EContext) -> None:
    """Un unico archivo temporal cubre dos consumidores conceptualmente
    distintos -- nunca contiene nada ajeno a ninguno de los dos:

    - Interpolacion de `docker-compose.yml` (`--env-file`): `NEO4J_IMAGE`,
      `NEO4J_USER`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`.
    - `Settings`/el contenedor de test (mismo archivo, pasado tambien a
      `docker create --env-file`): `NEO4J_URI`, `ALTAMIRA_API_MAX_WORKERS`,
      `LLM_TEMPERATURE`.

    Nunca copia `.env.example` ni el `.env` real del usuario, nunca
    imprime su contenido, nunca incluye credenciales OpenAI/PwC reales."""
    lines = [
        "NEO4J_IMAGE=neo4j:5-community",
        "NEO4J_USER=neo4j",
        f"NEO4J_PASSWORD={ctx.neo4j_password}",
        "NEO4J_DATABASE=neo4j",
        "NEO4J_URI=bolt://neo4j:7687",
        "ALTAMIRA_API_MAX_WORKERS=1",
        "LLM_TEMPERATURE=0",
        "",
    ]
    ctx.env_file.write_text("\n".join(lines), encoding="utf-8")


def _write_temp_override(ctx: E2EContext) -> None:
    """Reemplaza (no anexa) ports/env_file/volumes de `app` y ports de
    `neo4j`. La ruta del env temporal se serializa con `json.dumps` (un
    string JSON valido es tambien un escalar YAML valido entre comillas
    dobles): nunca se concatena sin escapar."""
    env_file_literal = json.dumps(ctx.env_file.as_posix())
    content = (
        "services:\n"
        "  app:\n"
        "    env_file: !override\n"
        f"      - {env_file_literal}\n"
        "    ports: !override []\n"
        "    volumes: !override\n"
        "      - e2e_app_data:/app/data\n"
        "  neo4j:\n"
        "    ports: !override []\n"
        "\n"
        "volumes:\n"
        "  e2e_app_data:\n"
    )
    ctx.override_file.write_text(content, encoding="utf-8")


def _compose_config_quiet(ctx: E2EContext) -> None:
    result = _run([*_compose_base_args(ctx), "config", "--quiet"])
    if result.returncode != 0:
        raise E2EError(
            "compose-config",
            "docker compose config --quiet fallo (posible falta de soporte para la "
            "sintaxis de merge '!override' en esta version de Docker Compose instalada "
            "-- no se genera un .env dentro del repositorio como resguardo silencioso): "
            + _sanitize(result.stderr, ctx),
        )


def _verify_compose_config_structure(ctx: E2EContext) -> None:
    """`config --quiet` solo prueba que Compose pudo renderizar el YAML;
    no prueba que el override temporal realmente reemplazo lo que debia
    reemplazar. Se analiza la salida real (`--format json`: JSON
    estructurado, nunca texto/YAML parseado a mano) y se verifica de
    forma explicita cada propiedad critica del override."""
    result = _run([*_compose_base_args(ctx), "config", "--format", "json"])
    if result.returncode != 0:
        raise E2EError("compose-config-structure", _sanitize(result.stderr, ctx))
    try:
        rendered = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise E2EError(
            "compose-config-structure", f"la salida de config no es JSON valido: {exc}"
        ) from None

    services = rendered.get("services", {})
    if set(services.keys()) != {"app", "neo4j"}:
        raise E2EError(
            "compose-config-structure",
            f"se esperaban exactamente los servicios app y neo4j; se encontro "
            f"{sorted(services.keys())}",
        )
    app = services["app"]
    neo4j = services["neo4j"]

    if app.get("ports"):
        raise E2EError("compose-config-structure", "app sigue publicando puertos")
    if neo4j.get("ports"):
        raise E2EError("compose-config-structure", "neo4j sigue publicando puertos")

    app_volumes = app.get("volumes") or []
    if any(
        volume.get("type") == "bind" and volume.get("target") == "/app/data"
        for volume in app_volumes
    ):
        raise E2EError("compose-config-structure", "app todavia monta ./data:/app/data")
    if not any(
        volume.get("type") == "volume" and volume.get("target") == "/app/data"
        for volume in app_volumes
    ):
        raise E2EError(
            "compose-config-structure", "app no usa el volumen temporal para /app/data"
        )

    # `config` no reexpone `env_file` como campo propio: resuelve su
    # contenido dentro de `environment`. Verificar que el valor real
    # coincide con la contrasena generada para ESTA ejecucion prueba que
    # el env_file efectivamente resuelto es el temporal (no el .env del
    # repositorio ni uno de una ejecucion anterior).
    app_environment = app.get("environment") or {}
    if app_environment.get("NEO4J_PASSWORD") != ctx.neo4j_password:
        raise E2EError(
            "compose-config-structure",
            "el env_file resuelto de app no coincide con el env temporal de esta ejecucion",
        )


def _build_runtime_image(ctx: E2EContext) -> None:
    result = _run([*_compose_base_args(ctx), "build", "app"])
    if result.returncode != 0:
        raise E2EError("build-runtime", _sanitize(result.stderr, ctx))


def _build_test_image(ctx: E2EContext) -> None:
    # Tag unico por ejecucion (`ctx.test_image_tag`); nunca `--no-cache`
    # por defecto -- el E2E debe poder aprovechar las caches
    # reproducibles de Maven/pip que ya usan los demas stages.
    result = _run(
        [
            "docker",
            "build",
            "--target",
            "test",
            "--tag",
            ctx.test_image_tag,
            str(ctx.repo_root),
        ]
    )
    if result.returncode != 0:
        raise E2EError("build-test", _sanitize(result.stderr, ctx))


def _compose_up(ctx: E2EContext) -> None:
    result = _run([*_compose_base_args(ctx), "up", "-d", "--no-build"])
    if result.returncode != 0:
        raise E2EError("compose-up", _sanitize(result.stderr, ctx))


def _container_id(ctx: E2EContext, service: str) -> str:
    result = _run([*_compose_base_args(ctx), "ps", "-q", service])
    container_id = result.stdout.strip()
    if result.returncode != 0 or not container_id:
        raise E2EError("container-id", f"no se pudo obtener el container id de {service}")
    return container_id


def _health_status(container_id: str) -> str | None:
    """JSON estructurado (`docker inspect`, sin `--format`) en vez de un
    template Go: un contenedor sin healthcheck simplemente no tiene la
    clave `Health`, lo que aca se distingue limpiamente de un fallo de
    comando en vez de devolver el texto opaco `<no value>`."""
    result = _run(["docker", "inspect", container_id])
    if result.returncode != 0:
        return None
    try:
        inspected = json.loads(result.stdout)
        status = inspected[0]["State"]["Health"]["Status"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        return None
    return str(status)


def _wait_healthy(
    ctx: E2EContext, service: str, *, timeout: float = _HEALTH_TIMEOUT_SECONDS
) -> None:
    container_id = _container_id(ctx, service)
    deadline = time.monotonic() + timeout
    last_status = "unknown"
    while time.monotonic() < deadline:
        status = _health_status(container_id)
        if status is not None:
            last_status = status
            if last_status == "healthy":
                return
        time.sleep(_HEALTH_POLL_INTERVAL_SECONDS)
    raise E2EError(
        f"wait-healthy-{service}",
        f"{service} no alcanzo 'healthy' en {timeout}s (ultimo estado: {last_status})",
    )


_JAVA_MAJOR_VERSION_RE = re.compile(r'version "(\d+)')


def _parse_compose_top_commands(output: str) -> list[str]:
    """`docker compose top` no tiene salida JSON (a diferencia de
    `docker inspect`): se parsea su tabla de forma deterministica usando
    la posicion real de la columna CMD en el encabezado, en vez de
    asumir un numero fijo de columnas."""
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        return []
    header_columns = lines[0].split()
    if "CMD" not in header_columns:
        return []
    cmd_index = header_columns.index("CMD")
    commands: list[str] = []
    for line in lines[1:]:
        parts = line.split(None, cmd_index)
        if len(parts) > cmd_index:
            commands.append(parts[cmd_index])
    return commands


def _validate_single_uvicorn_worker(commands: list[str]) -> None:
    """`init: true` agrega un wrapper `docker-init` (tini) como PID 1,
    cuyo propio argv incluye literalmente el comando real que exec-a
    despues (`docker-init -- uvicorn ...`) -- por eso una busqueda ingenua
    de la palabra "uvicorn" encuentra 2 lineas. El proceso real (el unico
    que CLAUDE.md exige que sea unico, por el registro en memoria de
    RunExecutor) es el que NO es ese wrapper."""
    uvicorn_commands = [cmd for cmd in commands if "uvicorn" in cmd]
    if not uvicorn_commands:
        raise E2EError(
            "smoke-app-workers", "no se encontro ningun proceso uvicorn en `docker compose top app`"
        )
    worker_commands = [cmd for cmd in uvicorn_commands if "docker-init" not in cmd]
    if len(worker_commands) != 1:
        raise E2EError(
            "smoke-app-workers",
            "se esperaba exactamente 1 proceso uvicorn real (sin contar el wrapper "
            f"docker-init de `init: true`); se encontraron {len(worker_commands)}",
        )
    worker_cmd = worker_commands[0]
    if "--factory" not in worker_cmd:
        raise E2EError("smoke-app-workers", "el proceso uvicorn no incluye --factory")
    if "--workers 1" not in worker_cmd:
        raise E2EError("smoke-app-workers", "el proceso uvicorn no incluye --workers 1")


def _smoke_app(ctx: E2EContext) -> None:
    python_check = _run(
        [*_compose_base_args(ctx), "exec", "-T", "app", "python", "-c", _APP_SMOKE_SCRIPT]
    )
    if python_check.returncode != 0 or "SMOKE_APP_OK" not in python_check.stdout:
        combined = python_check.stdout + python_check.stderr
        raise E2EError("smoke-app", _sanitize(combined, ctx))

    java_check = _run([*_compose_base_args(ctx), "exec", "-T", "app", "java", "-version"])
    java_output = java_check.stdout + java_check.stderr
    java_version_match = _JAVA_MAJOR_VERSION_RE.search(java_output)
    java_major = java_version_match.group(1) if java_version_match else None
    if java_check.returncode != 0 or java_major != "17":
        raise E2EError("smoke-app-java", _sanitize(java_output, ctx))

    top_check = _run([*_compose_base_args(ctx), "top", "app"])
    if top_check.returncode != 0:
        raise E2EError("smoke-app-workers", _sanitize(top_check.stderr, ctx))
    _validate_single_uvicorn_worker(_parse_compose_top_commands(top_check.stdout))


def _network_inspect(name: str) -> dict[str, object] | None:
    """JSON estructurado (sin `--format`): un template Go que fallara en
    silencio con `<no value>` es menos robusto para una validacion
    critica que un `KeyError` claro sobre un JSON real."""
    result = _run(["docker", "network", "inspect", name])
    if result.returncode != 0:
        return None
    try:
        inspected = json.loads(result.stdout)
        first: dict[str, object] = inspected[0]
    except (json.JSONDecodeError, IndexError, TypeError):
        return None
    return first


def _create_internal_network(ctx: E2EContext) -> None:
    result = _run(["docker", "network", "create", "--internal", ctx.network_name])
    if result.returncode != 0:
        raise E2EError("network-create", _sanitize(result.stderr, ctx))

    network = _network_inspect(ctx.network_name)
    if network is None:
        raise E2EError("network-inspect", f"no se pudo inspeccionar la red {ctx.network_name}")
    if network.get("Internal") is not True:
        raise E2EError("network-inspect", f"la red {ctx.network_name} no quedo Internal=true")
    if network.get("Name") != ctx.network_name:
        raise E2EError("network-inspect", "el nombre de red inspeccionado no coincide")


def _connect_neo4j_to_internal_network(ctx: E2EContext) -> str:
    neo4j_id = _container_id(ctx, "neo4j")
    result = _run(
        ["docker", "network", "connect", "--alias", "neo4j", ctx.network_name, neo4j_id]
    )
    if result.returncode != 0:
        raise E2EError("network-connect", _sanitize(result.stderr, ctx))
    return neo4j_id


def _create_test_container(ctx: E2EContext) -> None:
    result = _run(
        [
            "docker",
            "create",
            "--name",
            ctx.test_container_name,
            "--network",
            ctx.network_name,
            "--env-file",
            str(ctx.env_file),
            ctx.test_image_tag,
        ]
    )
    if result.returncode != 0:
        raise E2EError("test-create", _sanitize(result.stderr, ctx))


def _container_networks(container_name: str) -> dict[str, object] | None:
    result = _run(["docker", "inspect", container_name])
    if result.returncode != 0:
        return None
    try:
        inspected = json.loads(result.stdout)
        networks: dict[str, object] = inspected[0]["NetworkSettings"]["Networks"]
    except (json.JSONDecodeError, IndexError, KeyError, TypeError):
        return None
    return networks


def _assert_test_container_network_isolation(ctx: E2EContext) -> None:
    networks = _container_networks(ctx.test_container_name)
    if networks is None:
        raise E2EError(
            "test-inspect", f"no se pudo inspeccionar el contenedor {ctx.test_container_name}"
        )
    if set(networks.keys()) != {ctx.network_name}:
        raise E2EError(
            "test-inspect",
            "el contenedor de test esta conectado a redes inesperadas (se espera "
            f"unicamente la red interna): {sorted(networks.keys())}",
        )


def _start_and_wait_test_container(ctx: E2EContext) -> tuple[int, str]:
    start = _run(["docker", "start", ctx.test_container_name])
    if start.returncode != 0:
        raise E2EError("test-start", _sanitize(start.stderr, ctx))

    wait = _run(["docker", "wait", ctx.test_container_name], timeout=_TEST_WAIT_TIMEOUT_SECONDS)
    if wait.returncode != 0:
        raise E2EError("test-wait", _sanitize(wait.stderr, ctx))
    try:
        exit_code = int(wait.stdout.strip())
    except ValueError:
        raise E2EError(
            "test-wait", f"docker wait no devolvio un entero: {wait.stdout.strip()!r}"
        ) from None

    logs = _run(["docker", "logs", ctx.test_container_name])
    combined_output = logs.stdout + logs.stderr
    return exit_code, combined_output


def _try(
    ctx: E2EContext, description: str, action: Callable[[], subprocess.CompletedProcess[str]]
) -> None:
    """Cada paso de cleanup es independiente: una falla nunca aborta los
    pasos siguientes. Se reporta (sanitizado) pero no se relanza."""
    try:
        result = action()
        if result.returncode != 0:
            print(
                f"cleanup: {description} fallo (no fatal): {_sanitize(result.stderr, ctx)}",
                file=sys.stderr,
            )
    except Exception as exc:  # noqa: BLE001 - cleanup nunca debe abortar por esto
        print(f"cleanup: {description} lanzo una excepcion (no fatal): {exc}", file=sys.stderr)


def _cleanup(ctx: E2EContext, neo4j_container_id: str | None) -> None:
    """Orden: 1) contenedor de test; 2) desconectar neo4j de la red
    interna; 3) `compose down -v --remove-orphans` del proyecto unico;
    4) red interna; 5) tag de la imagen de test; 6) archivos temporales
    (implicito, al salir de `TemporaryDirectory` en `main()`). Cada paso
    es independiente (`_try`); el paso 2 nunca actua sobre un
    `neo4j_container_id` vacio/no inicializado -- solo se llama cuando
    `_connect_neo4j_to_internal_network` realmente devolvio un id real
    de esta misma ejecucion."""
    _try(
        ctx,
        "eliminar contenedor de test",
        lambda: _run(["docker", "rm", "-f", ctx.test_container_name]),
    )
    if neo4j_container_id:
        _try(
            ctx,
            "desconectar neo4j de la red interna",
            lambda: _run(
                ["docker", "network", "disconnect", "-f", ctx.network_name, neo4j_container_id]
            ),
        )
    _try(
        ctx,
        "compose down -v --remove-orphans",
        lambda: _run([*_compose_base_args(ctx), "down", "-v", "--remove-orphans"]),
    )
    _try(ctx, "eliminar red interna", lambda: _run(["docker", "network", "rm", ctx.network_name]))
    _try(
        ctx,
        "eliminar tag de imagen de test",
        lambda: _run(["docker", "image", "rm", ctx.test_image_tag]),
    )
    # Los archivos temporales (env/override) los elimina
    # tempfile.TemporaryDirectory al salir del `with` en main().


def _service_logs(ctx: E2EContext, service: str) -> str:
    """Best-effort: nunca lanza, se usa solo para diagnostico al fallar
    una fase. Vacio si el servicio nunca llego a existir (compose-up
    todavia no corrio)."""
    result = _run([*_compose_base_args(ctx), "logs", "--no-color", service])
    if result.returncode != 0:
        return ""
    return _sanitize(result.stdout + result.stderr, ctx)


def _test_container_logs(ctx: E2EContext) -> str:
    result = _run(["docker", "logs", ctx.test_container_name])
    if result.returncode != 0:
        return ""
    return _sanitize(result.stdout + result.stderr, ctx)


def _print_failure_logs(ctx: E2EContext) -> None:
    """Se capturan por separado (test/app/neo4j) y solo se imprimen al
    fallar una fase -- nunca en el camino de exito, donde solo se
    muestra un resumen breve mas los resultados de pytest."""
    test_logs = _test_container_logs(ctx)
    if test_logs:
        print("--- logs del contenedor de test ---", file=sys.stderr)
        print(test_logs, file=sys.stderr)
    for service in ("app", "neo4j"):
        service_logs = _service_logs(ctx, service)
        if service_logs:
            print(f"--- logs {service} ---", file=sys.stderr)
            print(service_logs, file=sys.stderr)


def main() -> int:
    repo_root = _repo_root()
    with tempfile.TemporaryDirectory(prefix="altamira-e2e-") as tmp_dir_str:
        ctx = _new_context(repo_root, Path(tmp_dir_str))
        neo4j_container_id: str | None = None
        phase = "setup"
        try:
            phase = "write-config"
            _write_temp_env(ctx)
            _write_temp_override(ctx)

            phase = "compose-config"
            _compose_config_quiet(ctx)
            _verify_compose_config_structure(ctx)

            phase = "build-runtime"
            _build_runtime_image(ctx)

            phase = "build-test"
            _build_test_image(ctx)

            phase = "compose-up"
            _compose_up(ctx)

            phase = "wait-healthy-neo4j"
            _wait_healthy(ctx, "neo4j")
            phase = "wait-healthy-app"
            _wait_healthy(ctx, "app")

            phase = "smoke-app"
            _smoke_app(ctx)

            phase = "network-create"
            _create_internal_network(ctx)

            phase = "network-connect"
            neo4j_container_id = _connect_neo4j_to_internal_network(ctx)

            phase = "test-container"
            _create_test_container(ctx)
            _assert_test_container_network_isolation(ctx)
            exit_code, output = _start_and_wait_test_container(ctx)

            if exit_code != 0:
                print(f"FASE: {phase} -- exit code {exit_code}", file=sys.stderr)
                print("--- logs del contenedor de test ---", file=sys.stderr)
                print(_sanitize(output, ctx), file=sys.stderr)
                for service in ("app", "neo4j"):
                    service_logs = _service_logs(ctx, service)
                    if service_logs:
                        print(f"--- logs {service} ---", file=sys.stderr)
                        print(service_logs, file=sys.stderr)
                return exit_code

            # Camino de exito: solo un resumen breve mas los resultados
            # de pytest (el propio stdout del contenedor de test) --
            # nunca se vuelcan aqui los logs de app/neo4j.
            print(_sanitize(output, ctx))
            print("E2E OK")
            return 0
        except E2EError as exc:
            print(f"FASE: {exc.phase}", file=sys.stderr)
            print(_sanitize(exc.message, ctx), file=sys.stderr)
            _print_failure_logs(ctx)
            return 1
        finally:
            _cleanup(ctx, neo4j_container_id)


if __name__ == "__main__":
    sys.exit(main())
