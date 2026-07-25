"""Empaquetado Docker (Prompt 14a): validaciones estructurales estaticas
de `Dockerfile`/`docker-compose.yml`/`.dockerignore`, sin invocar al
CLI de Docker ni requerir contenedores corriendo -- deben poder correr
en cualquier entorno `pytest -m "not integration"` (incluido un
contenedor `python:3.12-slim` efimero sin Docker-in-Docker). La
validacion real contra el motor Docker (`docker compose config/build/up`)
es manual/CI, documentada en README.md, no parte de esta suite."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO_ROOT / "Dockerfile"
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"


def test_docker_compose_blueprint_no_longer_exists() -> None:
    assert not (REPO_ROOT / "docker-compose.blueprint.yml").exists()
    assert COMPOSE_FILE.is_file()


def test_compose_declares_exactly_app_and_neo4j() -> None:
    spec = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    assert set(spec["services"].keys()) == {"app", "neo4j"}


def test_compose_has_no_extra_top_level_services_or_third_service() -> None:
    spec = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    for forbidden in ("redis", "celery", "kafka", "postgres", "postgresql", "proxy", "test"):
        assert forbidden not in spec["services"]


def test_compose_app_builds_from_dockerfile_runtime_target() -> None:
    spec = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    build = spec["services"]["app"]["build"]
    assert build["dockerfile"] == "Dockerfile"
    assert build["target"] == "runtime"


def test_compose_app_depends_on_neo4j_healthy() -> None:
    spec = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    depends_on = spec["services"]["app"]["depends_on"]
    assert depends_on["neo4j"]["condition"] == "service_healthy"


def test_compose_app_publishes_port_8000_and_mounts_data() -> None:
    spec = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    app = spec["services"]["app"]
    assert "8000:8000" in app["ports"]
    assert "./data:/app/data" in app["volumes"]


def test_compose_app_healthcheck_hits_health_endpoint_without_curl() -> None:
    spec = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    test_cmd = spec["services"]["app"]["healthcheck"]["test"]
    joined = " ".join(test_cmd)
    assert "/health" in joined
    assert "curl" not in joined
    assert "urllib" in joined
    for field in ("interval", "timeout", "retries", "start_period"):
        assert field in spec["services"]["app"]["healthcheck"]


def test_compose_neo4j_uses_named_volume_and_env_credentials() -> None:
    spec = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    neo4j = spec["services"]["neo4j"]
    assert "neo4j_data:/data" in neo4j["volumes"]
    assert "neo4j_data" in spec["volumes"]
    auth = neo4j["environment"]["NEO4J_AUTH"]
    assert "${NEO4J_USER}" in auth
    assert "${NEO4J_PASSWORD}" in auth
    # nunca una credencial literal hardcodeada en el propio compose.
    assert "replace-me" not in auth


def test_compose_neo4j_healthcheck_uses_cypher_shell() -> None:
    spec = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    test_cmd = " ".join(spec["services"]["neo4j"]["healthcheck"]["test"])
    assert "cypher-shell" in test_cmd
    assert "RETURN 1" in test_cmd


def test_compose_does_not_use_host_networking() -> None:
    spec = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    for service in spec["services"].values():
        assert service.get("network_mode") != "host"


def test_compose_no_hardcoded_secrets() -> None:
    text = COMPOSE_FILE.read_text(encoding="utf-8")
    assert "replace-me" not in text
    for suspicious in ("api_key", "API_KEY=sk-", "password=", "PASSWORD=neo4j\n"):
        assert suspicious not in text.lower() or "${" in text


def test_dockerfile_has_the_three_required_stages() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "AS parser-build" in text
    assert "AS python-build" in text
    assert "AS runtime" in text


def test_dockerfile_parser_build_uses_maven_jdk17() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "FROM maven:3.9-eclipse-temurin-17 AS parser-build" in text
    stage = text.split("AS parser-build", 1)[1].split("AS python-build", 1)[0]
    assert "mvn" in stage
    assert "package" in stage


def test_dockerfile_python_build_produces_a_wheel() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    stage = text.split("AS python-build", 1)[1].split("AS runtime", 1)[0]
    assert "pip wheel" in stage
    assert "--no-deps" in stage


def test_dockerfile_runtime_pins_slim_bookworm_and_installs_jre() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "FROM python:3.12-slim-bookworm AS runtime" in text
    # base "slim" flotante (sin -bookworm) prohibida: en esta sesion se
    # confirmo empiricamente que variantes mas recientes (trixie) solo
    # ofrecen paquetes openjdk-21+ via apt, no 17.
    assert "FROM python:3.12-slim AS runtime" not in text
    stage = text.split("AS runtime", 1)[1]
    assert "openjdk-17-jre-headless" in stage
    assert "openjdk-17-jdk" not in stage


def test_dockerfile_runtime_creates_dedicated_non_root_user() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "--uid 10001" in text
    assert "--gid 10001" in text or "groupadd --gid 10001" in text
    assert "USER altamira" in text
    assert "USER root" not in text
    assert "sudo" not in text
    assert "chmod 777" not in text
    assert "chmod -R 777" not in text


def test_dockerfile_does_not_run_maven_at_container_start() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    cmd_line = text.rsplit("CMD ", 1)[-1]
    assert "mvn" not in cmd_line


def test_dockerfile_cmd_pins_exactly_one_worker() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert '"--workers", "1"' in text
    assert '"--workers", "2"' not in text


def test_dockerfile_entrypoint_uses_app_factory() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "altamira_extractor.api.app:app_factory" in text
    assert "--factory" in text
    assert "uvicorn.run" not in text


def test_dockerfile_healthcheck_targets_health_endpoint_without_curl() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    healthcheck_block = text.split("HEALTHCHECK", 1)[1].split("\n\n", 1)[0]
    assert "/health" in healthcheck_block
    assert "curl" not in healthcheck_block
    assert "urllib" in healthcheck_block


def test_dockerfile_copies_only_expected_external_resources() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    for expected in ("COPY schemas", "COPY queries", "COPY prompts", "COPY config"):
        assert expected in text
    # nunca un COPY indiscriminado de todo el repo.
    assert "COPY . ." not in text
    assert "COPY . /app" not in text


def test_dockerfile_jar_copied_to_the_relative_path_settings_expects() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "./parser/target/altamira-cobol-parser.jar" in text
    # ninguna convencion nueva de variable de entorno para la ruta del JAR.
    assert "PARSER_JAR_PATH" not in text


def test_dockerfile_never_hardcodes_secrets() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8").lower()
    for suspicious in ("api_key=sk-", "password=replace", "neo4j_password="):
        assert suspicious not in text


def test_dockerignore_excludes_the_minimum_required_patterns() -> None:
    text = DOCKERIGNORE.read_text(encoding="utf-8")
    for pattern in (
        ".git",
        ".env",
        "data/",
        "parser/target/",
        "__pycache__",
        "*.py[cod]",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "egg-info",
    ):
        assert pattern in text


def test_dockerignore_does_not_exclude_tests_yet() -> None:
    text = DOCKERIGNORE.read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines()]
    assert "tests/" not in lines
    assert "tests" not in lines


def test_readme_documents_docker_compose_workflow_but_not_14b_e2e() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    readme_lower = readme.lower()
    for expected in (
        "docker compose config",
        "docker compose build",
        "docker compose up -d",
        "docker compose ps",
        "/health",
        "/docs",
        "/ui/runs",
        "worker",
        "no-root",
    ):
        assert expected.lower() in readme_lower
    assert "Prompt 14b" in readme
