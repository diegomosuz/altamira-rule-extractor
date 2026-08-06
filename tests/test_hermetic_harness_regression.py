"""Test de regresion del defecto de Fase 15B2-A (incidente: harness no
hermetico).

Reproduce el escenario EXACTO del incidente: variables de entorno de un
proveedor real presentes + un `.env` descubrible en el CWD con
credenciales que PARECEN reales, y prueba que `run_ingestion` ejecutado
a traves del harness hermetico (`tests/hermetic_llm_support.py`) nunca
se ve afectado por esa contaminacion.

El defecto original: un script ad-hoc (`run_six_packages.py`, ya
eliminado) llamo `run_ingestion(settings=load_settings())` sin construir
`Settings` sinteticas ni deshabilitar la carga de `.env`. `load_settings()`
resolvio un `LLM_PROVIDER` real desde un `.env` descubrible y 6 paquetes
100% sinteticos activaron llamadas reales a un proveedor LLM.

Este test debe fallar si en el futuro:
- alguien retira `_env_file=None` de `build_hermetic_settings` (la
  primera asercion, `test_bare_load_settings_is_vulnerable_to_pollution`,
  prueba que el escenario de contaminacion simulado aqui SI es capaz de
  contaminar `Settings`/`load_settings()` sin la correccion -- si algun
  cambio futuro rompe esa simulacion, esta asercion deja de ser una
  prueba valida del defecto y falla explicitamente);
- alguien deja de reemplazar `OpenAICompatibleChatClient` en los dos
  sitios reales de construccion (`hermetic_llm_and_network_guard` deja
  de cubrirlos: el pipeline fallaria en RULE_DRAFTS_GENERATED en vez de
  llegar a COMPLETED, porque `_DeterministicFakeClient`/`_RealClientBlocked`
  dejarian de interceptar);
- alguien permite una conexion de socket externa no autorizada (el guard
  la bloquea con `HermeticNetworkViolation`, que no se atrapa en ningun
  punto de este test)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import altamira_extractor.pipeline.guardrails_applied_stage as guardrails_stage_module
import altamira_extractor.pipeline.rule_drafts_generated_stage as rule_drafts_stage_module
from altamira_extractor.config import load_settings

from .e2e_support import (
    build_settings,
    install_dynamic_rule_draft_fake_client,
    install_fake_client,
    require_jar,
    write_package_zip,
)
from .hermetic_llm_support import (
    FAKE_LLM_API_KEY,
    FAKE_LLM_BASE_URL,
    FAKE_LLM_MODEL,
    build_hermetic_settings,
    hermetic_llm_and_network_guard,
)

pytestmark = pytest.mark.integration

_POLLUTED_ENV_VARS = {
    "LLM_PROVIDER": "openai",
    "OPENAI_API_KEY": "sk-should-never-be-used-real-looking-key",
    "OPENAI_BASE_URL": "https://api.openai.com/v1",
    "OPENAI_MODEL": "gpt-4o-mini",
}


def _write_polluted_dotenv(directory: Path) -> Path:
    """Un `.env` "descubrible" con exactamente el mismo tipo de valores
    reales-parecidos que causaron el incidente original."""
    env_path = directory / ".env"
    env_path.write_text(
        "\n".join(f"{key}={value}" for key, value in _POLLUTED_ENV_VARS.items()) + "\n",
        encoding="utf-8",
    )
    return env_path


@pytest.fixture
def polluted_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Path:
    """Simula el precondicion exacta del incidente: variables de entorno
    de un proveedor real presentes Y un `.env` descubrible en el CWD."""
    for key, value in _POLLUTED_ENV_VARS.items():
        monkeypatch.setenv(key, value)
    cwd_dir = tmp_path / "polluted_cwd"
    cwd_dir.mkdir()
    _write_polluted_dotenv(cwd_dir)
    monkeypatch.chdir(cwd_dir)
    return cwd_dir


def test_bare_load_settings_is_vulnerable_to_pollution(
    polluted_environment: Path,
) -> None:
    """Confirma que el escenario simulado aqui SI reproduce el defecto
    original: `load_settings()` (la llamada exacta del script eliminado,
    sin overrides) recoge el proveedor "real" contaminado. Esto no es
    una asercion sobre el harness corregido -- es la prueba de que la
    precondicion del incidente sigue siendo genuina, para que las
    aserciones de abajo (que SI usan el harness corregido) demuestren
    algo real."""
    unsafe = load_settings()
    assert unsafe.llm_provider == "openai"
    assert unsafe.openai_base_url == "https://api.openai.com/v1"
    assert unsafe.openai_api_key is not None
    assert unsafe.openai_api_key.get_secret_value() == _POLLUTED_ENV_VARS["OPENAI_API_KEY"]


def test_hermetic_settings_ignore_polluted_environment(
    polluted_environment: Path, tmp_path: Path
) -> None:
    """`build_hermetic_settings` construye `Settings` que NUNCA reflejan
    la contaminacion del entorno/`.env`, sin importar que ambos esten
    presentes simultaneamente."""
    safe = build_hermetic_settings(tmp_path / "hermetic_data")
    assert safe.openai_base_url == FAKE_LLM_BASE_URL
    assert safe.openai_api_key is not None
    assert safe.openai_api_key.get_secret_value() == FAKE_LLM_API_KEY
    assert "api.openai.com" not in (safe.openai_base_url or "")


def test_hermetic_pipeline_completes_without_touching_real_provider(
    polluted_environment: Path,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """El test central: `run_ingestion` corrido a traves del harness
    hermetico, DENTRO del mismo entorno/`.env` contaminado, llega a
    COMPLETED usando exclusivamente el fake -- nunca el proveedor real
    "descubierto" por la precondicion contaminada."""
    require_jar()
    zip_path = write_package_zip(tmp_path / "package.zip")
    settings = build_hermetic_settings(
        tmp_path / "hermetic_data",
        NEO4J_URI=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        NEO4J_USER=os.environ.get("NEO4J_USER", "neo4j"),
        NEO4J_PASSWORD=os.environ.get("NEO4J_PASSWORD", "neo4j"),
        NEO4J_DATABASE=os.environ.get("NEO4J_DATABASE", "neo4j"),
    )

    # Scoped al logger propio de la app ("altamira_extractor.*"): a nivel
    # DEBUG, el logger del driver de Neo4j ("neo4j.*") traza el texto
    # completo de cada query/registro -- incluye `Paragraph.source_text`
    # legitimamente persistido en el grafo, que NO es un prompt/respuesta
    # de LLM y ensuciaria esta asercion con un falso positivo.
    with (
        caplog.at_level("DEBUG", logger="altamira_extractor"),
        hermetic_llm_and_network_guard() as fake_client_cls,
    ):
        from altamira_extractor.pipeline.runner import run_ingestion

        state = run_ingestion(source_zip=zip_path, settings=settings)

    assert state.current_stage.value == "COMPLETED", [
        (s.stage.value, s.status.value, s.error) for s in state.stages
    ]
    assert all(s.status.value != "FAILED" for s in state.stages)

    # Proveedor efectivo: FAKE, nunca el real "descubierto" por el .env.
    assert settings.openai_base_url == FAKE_LLM_BASE_URL
    assert fake_client_cls.call_count > 0

    # Cero llamadas al transporte real: el fake nunca abre un socket, y
    # el guard hubiera lanzado HermeticNetworkViolation (nunca atrapada
    # en este test) ante cualquier intento externo -- llegar a COMPLETED
    # sin excepcion ya lo prueba; se refuerza explicitamente aqui.
    log_text = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("altamira_extractor")
    )
    assert "api.openai.com" not in log_text
    assert _POLLUTED_ENV_VARS["OPENAI_API_KEY"] not in log_text
    assert FAKE_LLM_API_KEY not in log_text

    # No debe filtrarse contenido COBOL fuente ni prompts/respuestas
    # crudos del LLM en los logs de la corrida.
    assert "IDENTIFICATION DIVISION" not in log_text
    assert "WS-SALDO" not in log_text


def test_hermetic_fake_client_output_is_deterministic(
    polluted_environment: Path, tmp_path: Path
) -> None:
    """Dos corridas independientes (dos run_id nuevos, mismo paquete)
    producen el mismo RuleDraft fake -- nunca contenido variable que
    pudiera confundirse con una respuesta real no determinista."""
    require_jar()
    zip_path = write_package_zip(tmp_path / "package.zip")

    drafts: list[str] = []
    for index in range(2):
        settings = build_hermetic_settings(
            tmp_path / f"hermetic_data_{index}",
            NEO4J_URI=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
            NEO4J_USER=os.environ.get("NEO4J_USER", "neo4j"),
            NEO4J_PASSWORD=os.environ.get("NEO4J_PASSWORD", "neo4j"),
            NEO4J_DATABASE=os.environ.get("NEO4J_DATABASE", "neo4j"),
        )
        with hermetic_llm_and_network_guard():
            from altamira_extractor.pipeline.runner import run_ingestion

            state = run_ingestion(source_zip=zip_path, settings=settings)
        assert state.current_stage.value == "COMPLETED"

        rules_dir = settings.runs_dir / state.run_id / "artifacts" / "10-rules"
        md_files = sorted(rules_dir.glob("*.md"))
        assert len(md_files) == 1
        drafts.append(md_files[0].read_text(encoding="utf-8"))

    assert drafts[0] == drafts[1]


def test_e2e_support_build_settings_ignores_polluted_environment(
    polluted_environment: Path, tmp_path: Path
) -> None:
    """Endurecimiento de `tests/e2e_support.py::build_settings` (Seccion
    4 del cierre de Fase 15B2-A): construido DENTRO del mismo entorno/
    `.env` contaminado que reproduce el incidente original, `build_
    settings` nunca refleja esa contaminacion -- `_env_file=None` mas los
    sentinels `FAKE_LLM_*` (reutilizados de `hermetic_llm_support.py`)
    se imponen sobre cualquier `LLM_PROVIDER`/`OPENAI_*` descubierto."""
    settings = build_settings(tmp_path)
    assert settings.openai_base_url == FAKE_LLM_BASE_URL
    assert settings.openai_base_url != _POLLUTED_ENV_VARS["OPENAI_BASE_URL"]
    assert settings.openai_model == FAKE_LLM_MODEL
    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == FAKE_LLM_API_KEY
    assert settings.openai_api_key.get_secret_value() != _POLLUTED_ENV_VARS["OPENAI_API_KEY"]
    assert "api.openai.com" not in settings.openai_base_url


def test_e2e_support_build_settings_pipeline_never_touches_real_provider(
    polluted_environment: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prueba de punta a punta con el mismo mecanismo que usan los E2E
    reales (`install_fake_client`/`install_dynamic_rule_draft_fake_
    client`, patch de `OpenAICompatibleChatClient` via `monkeypatch`,
    nunca el guard hermetico): `run_ingestion` con `Settings` de `build_
    settings`, DENTRO del entorno/`.env` contaminado, llega a COMPLETED
    usando exclusivamente el fake -- el cliente real nunca se
    instancia (si lo hiciera, intentaria abrir una conexion HTTP real y
    el test fallaria por timeout/error de red en vez de completar)."""
    require_jar()
    zip_path = write_package_zip(tmp_path / "package.zip")
    settings = build_settings(tmp_path)

    install_dynamic_rule_draft_fake_client(monkeypatch, rule_drafts_stage_module)
    repair_calls = install_fake_client(monkeypatch, guardrails_stage_module, [])

    from altamira_extractor.pipeline.runner import run_ingestion

    state = run_ingestion(source_zip=zip_path, settings=settings)

    assert state.current_stage.value == "COMPLETED", [
        (s.stage.value, s.status.value, s.error) for s in state.stages
    ]
    assert len(repair_calls) == 0
