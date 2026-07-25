"""Tests de Settings: manifest_xsd_path debe ser estable frente al CWD."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from altamira_extractor.config import Settings, _discover_repo_root


def test_manifest_xsd_path_points_to_the_real_schema_file() -> None:
    settings = Settings()
    assert settings.manifest_xsd_path.is_absolute()
    assert settings.manifest_xsd_path.name == "manifest.xsd"
    assert settings.manifest_xsd_path.is_file()


def test_manifest_xsd_path_is_independent_of_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = Settings().manifest_xsd_path

    other_dir = tmp_path / "somewhere" / "else"
    other_dir.mkdir(parents=True)
    monkeypatch.chdir(other_dir)

    from_elsewhere = Settings().manifest_xsd_path

    assert from_elsewhere == baseline
    assert from_elsewhere.is_file()


def test_semantic_tags_path_points_to_the_real_config_file() -> None:
    settings = Settings()
    assert settings.semantic_tags_path.is_absolute()
    assert settings.semantic_tags_path.name == "semantic-tags.yml"
    assert settings.semantic_tags_path.is_file()


def test_semantic_tags_path_is_independent_of_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = Settings().semantic_tags_path

    other_dir = tmp_path / "somewhere" / "else"
    other_dir.mkdir(parents=True)
    monkeypatch.chdir(other_dir)

    from_elsewhere = Settings().semantic_tags_path

    assert from_elsewhere == baseline
    assert from_elsewhere.is_file()


def test_domain_glossary_path_points_to_the_real_config_file() -> None:
    settings = Settings()
    assert settings.domain_glossary_path.is_absolute()
    assert settings.domain_glossary_path.name == "domain-glossary.example.yml"
    assert settings.domain_glossary_path.is_file()


def test_domain_glossary_path_is_independent_of_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = Settings().domain_glossary_path

    other_dir = tmp_path / "somewhere" / "else"
    other_dir.mkdir(parents=True)
    monkeypatch.chdir(other_dir)

    from_elsewhere = Settings().domain_glossary_path

    assert from_elsewhere == baseline
    assert from_elsewhere.is_file()


def test_discover_repo_root_raises_descriptive_error_when_not_found(tmp_path: Path) -> None:
    # tmp_path vive fuera del repo (temp del sistema): ninguno de sus
    # ancestros contiene pyproject.toml, asi que el recorrido hacia arriba
    # debe agotarse y fallar con un mensaje claro, no un traceback opaco.
    isolated = tmp_path / "no-repo-here"
    isolated.mkdir()

    with pytest.raises(RuntimeError, match="pyproject.toml"):
        _discover_repo_root(start=isolated)


# ---------------------------------------------------------------------------
# llm_temperature: Prompt 14a.1. `Settings()` construido con kwargs Python
# (`llm_temperature=0`, un int real) SIEMPRE funciono; el bug real solo se
# manifiesta cuando el valor llega como texto desde un `.env`/variable de
# entorno real (nunca antes ejercitado en ningun test previo). Cada caso de
# abajo usa un archivo `.env` real en tmp_path via `_env_file=`, nunca el
# `.env` del desarrollador.
# ---------------------------------------------------------------------------


def _settings_from_env_file(tmp_path: Path, content: str) -> Settings:
    env_path = tmp_path / ".env"
    env_path.write_text(content, encoding="utf-8")
    return Settings(_env_file=str(env_path))


def test_llm_temperature_zero_string_from_env_file_loads_as_int_zero(tmp_path: Path) -> None:
    settings = _settings_from_env_file(tmp_path, "LLM_TEMPERATURE=0\n")
    assert settings.llm_temperature == 0
    assert type(settings.llm_temperature) is int


def test_llm_temperature_padded_zero_string_from_env_file_loads_as_int_zero(
    tmp_path: Path,
) -> None:
    # Valor entrecomillado: garantiza que el espacio en blanco exterior
    # llegue intacto hasta el validador, sin depender de si el parser
    # dotenv ya lo recorta por su cuenta para valores sin comillas.
    settings = _settings_from_env_file(tmp_path, 'LLM_TEMPERATURE=" 0 "\n')
    assert settings.llm_temperature == 0


def test_llm_temperature_zero_via_python_kwargs_still_works() -> None:
    # Regresion: la construccion que YA funcionaba (kwargs Python con el
    # ALIAS real del campo -- `LLM_TEMPERATURE`, no `llm_temperature`:
    # `Settings` no habilita `populate_by_name`, asi que un kwarg con el
    # nombre Python de un campo con `validation_alias` se ignora en
    # silencio y cae al default, en vez de fallar o de usarse) debe
    # seguir funcionando identica.
    settings = Settings(LLM_TEMPERATURE=0)
    assert settings.llm_temperature == 0


def test_llm_temperature_one_from_env_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="LLM_TEMPERATURE"):
        _settings_from_env_file(tmp_path, "LLM_TEMPERATURE=1\n")


def test_llm_temperature_fractional_from_env_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="LLM_TEMPERATURE"):
        _settings_from_env_file(tmp_path, "LLM_TEMPERATURE=0.1\n")


def test_llm_temperature_arbitrary_text_from_env_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="LLM_TEMPERATURE"):
        _settings_from_env_file(tmp_path, "LLM_TEMPERATURE=hot\n")


def test_llm_temperature_one_as_python_kwarg_is_still_rejected() -> None:
    # Mismo alias real (`LLM_TEMPERATURE`, ver comentario arriba). mypy ya
    # marca `1` como invalido para `Literal[0]` en tiempo estatico -- el
    # ignore documenta que este test ejercita deliberadamente ese mismo
    # caso invalido en tiempo de ejecucion.
    with pytest.raises(ValidationError, match="LLM_TEMPERATURE"):
        Settings(LLM_TEMPERATURE=1)  # type: ignore[arg-type]


def test_env_file_with_llm_temperature_zero_does_not_change_other_settings(
    tmp_path: Path,
) -> None:
    # El resto de Settings no cambia: mismos defaults que sin .env,
    # salvo los campos explicitamente presentes en el archivo.
    baseline = Settings(
        data_dir=tmp_path / "data",
        runs_dir=tmp_path / "data" / "runs",
        incoming_dir=tmp_path / "data" / "incoming",
    )
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"LLM_TEMPERATURE=0\n"
        f"ALTAMIRA_DATA_DIR={tmp_path / 'data'}\n"
        f"ALTAMIRA_RUNS_DIR={tmp_path / 'data' / 'runs'}\n"
        f"ALTAMIRA_INCOMING_DIR={tmp_path / 'data' / 'incoming'}\n",
        encoding="utf-8",
    )
    from_file = Settings(_env_file=str(env_path))

    assert from_file.llm_temperature == 0 == baseline.llm_temperature
    assert from_file.data_dir == baseline.data_dir
    assert from_file.max_package_size_bytes == baseline.max_package_size_bytes
    assert from_file.api_max_workers == baseline.api_max_workers
    assert from_file.llm_repair_attempts == baseline.llm_repair_attempts
