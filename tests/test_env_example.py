"""`.env.example` (Prompt 14a.1): una copia sanitizada temporal debe
cargar `Settings()` sin error, con `llm_temperature == 0` y los campos
criticos esperados; las variables inertes eliminadas no deben
reaparecer; y toda interpolacion `${VAR}` de `docker-compose.yml` debe
tener una entrada en `.env.example` o un default explicito en el propio
Compose. Nunca escribe `.env` en la raiz, nunca depende de variables
reales del host, nunca contacta Neo4j ni un proveedor LLM, nunca invoca
Docker."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from altamira_extractor.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = REPO_ROOT / ".env.example"
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"

# checkpoint correctivo (estabilizacion de baseline): `Settings(_env_file=...)`
# solo elige QUE ARCHIVO dotenv se lee -- pydantic-settings sigue leyendo
# `os.environ` para cualquier campo no cubierto por ese archivo. Si el
# desarrollador tiene alguna de estas variables exportada en su shell (uso
# manual habitual del proyecto), sobreescribiria en silencio los valores
# esperados de la copia sanitizada de `.env.example` que estos tests
# verifican explicitamente. Helper OPT-IN (nunca autouse): solo los tests
# que verifican los valores exactos de la copia sanitizada lo necesitan.
_ENV_EXAMPLE_SENSITIVE_VARS = (
    "NEO4J_URI",
    "NEO4J_USER",
    "NEO4J_PASSWORD",
    "NEO4J_DATABASE",
    "LLM_PROVIDER",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
    "PWC_GENAI_API_KEY",
    "PWC_GENAI_BASE_URL",
    "PWC_GENAI_MODEL",
    "LLM_TEMPERATURE",
    "LLM_REPAIR_ATTEMPTS",
    "LLM_HTTP_RETRIES",
    "API_MAX_WORKERS",
)


def _clear_env_example_sensitive_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV_EXAMPLE_SENSITIVE_VARS:
        monkeypatch.delenv(name, raising=False)

_OBSOLETE_VARIABLE_NAMES = (
    "APP_ENV",
    "APP_HOST",
    "APP_PORT",
    "DATA_DIR",
    "MAX_ZIP_SIZE_BYTES",
    "MAX_UNCOMPRESSED_SIZE_BYTES",
    "MAX_ZIP_FILES",
    "MAX_EXPANSION_RATIO",
    "CODE_SLICE_MAX_DEPTH",
    "PIPELINE_MAX_CONCURRENCY",
)

_INTERPOLATION_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-[^}]*)?}")


def _defined_variable_names(text: str) -> set[str]:
    return {
        line.split("=", 1)[0].strip()
        for line in text.splitlines()
        if "=" in line and not line.strip().startswith("#")
    }


def _sanitized_env_example_copy(tmp_path: Path) -> Path:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    text = text.replace("change-this-password", "test-password-123")
    env_path = tmp_path / ".env.example.sanitized"
    env_path.write_text(text, encoding="utf-8")
    return env_path


def test_env_example_sanitized_copy_loads_settings_without_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_env_example_sensitive_vars(monkeypatch)
    env_path = _sanitized_env_example_copy(tmp_path)
    settings = Settings(_env_file=str(env_path))

    assert settings.llm_temperature == 0
    assert type(settings.llm_temperature) is int
    assert settings.neo4j_uri == "bolt://neo4j:7687"
    assert settings.neo4j_user == "neo4j"
    assert settings.neo4j_password.get_secret_value() == "test-password-123"
    assert settings.neo4j_database == "neo4j"
    assert settings.api_max_workers == 1
    assert settings.llm_repair_attempts == 2
    assert settings.llm_http_retries == 3


def test_env_example_does_not_set_a_real_llm_provider_or_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Las credenciales LLM quedan comentadas: load_settings() no debe
    # terminar con un proveedor "configurado por accidente".
    _clear_env_example_sensitive_vars(monkeypatch)
    env_path = _sanitized_env_example_copy(tmp_path)
    settings = Settings(_env_file=str(env_path))

    assert settings.llm_provider is None
    assert settings.openai_api_key is None
    assert settings.pwc_genai_api_key is None


def test_env_example_llm_credential_lines_are_commented_out() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("OPENAI_API_KEY=", "PWC_GENAI_API_KEY=", "LLM_PROVIDER=")):
            raise AssertionError(f"linea LLM sin comentar en .env.example: {line!r}")


def test_env_example_never_reintroduces_the_removed_inert_variables() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    defined = _defined_variable_names(text)
    for obsolete in _OBSOLETE_VARIABLE_NAMES:
        assert obsolete not in defined, f"variable inerte reintroducida: {obsolete}"


def test_env_example_neo4j_password_placeholder_is_long_enough_and_documented() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    match = re.search(r"^NEO4J_PASSWORD=(.+)$", text, re.MULTILINE)
    assert match is not None
    placeholder = match.group(1).strip()
    assert len(placeholder) >= 8
    assert "produccion" in text.lower() or "production" in text.lower()


def test_every_compose_interpolated_variable_has_env_example_entry_or_default() -> None:
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    env_example_names = _defined_variable_names(ENV_EXAMPLE.read_text(encoding="utf-8"))

    for name, default in _INTERPOLATION_RE.findall(compose_text):
        has_default = default != ""
        assert name in env_example_names or has_default, (
            f"{name} no tiene entrada en .env.example ni default explicito en compose"
        )


def test_healthcheck_auxiliary_variables_never_required_in_env_example() -> None:
    # HEALTHCHECK_NEO4J_USER/HEALTHCHECK_NEO4J_PASSWORD viven UNICAMENTE
    # dentro de docker-compose.yml (derivadas de ${NEO4J_USER}/
    # ${NEO4J_PASSWORD}); el usuario nunca debe tener que definirlas por
    # separado en .env.
    env_example_text = ENV_EXAMPLE.read_text(encoding="utf-8")
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")

    assert "HEALTHCHECK_NEO4J_USER" not in env_example_text
    assert "HEALTHCHECK_NEO4J_PASSWORD" not in env_example_text
    assert "HEALTHCHECK_NEO4J_USER: \"${NEO4J_USER}\"" in compose_text
    assert "HEALTHCHECK_NEO4J_PASSWORD: \"${NEO4J_PASSWORD}\"" in compose_text
