"""Branding del header y pagina "Acerca de..." (Fase v1.17.1, Feature 1/4)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from altamira_extractor.api.app import create_app
from altamira_extractor.config import Settings

from ..e2e_support import write_disabled_dev_security_config


def _settings_for(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    settings = Settings(
        data_dir=data_dir,
        runs_dir=data_dir / "runs",
        incoming_dir=data_dir / "incoming",
        security_config_path=write_disabled_dev_security_config(tmp_path),
    )
    settings.runs_dir.mkdir(parents=True, exist_ok=True)
    return settings


def test_fiern_header_and_full_name_rendered(tmp_path: Path) -> None:
    settings = _settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get("/ui/runs")
        assert response.status_code == 200
        assert ">FIERN<" in response.text
        assert "Framework Inteligente para la Extracci" in response.text
        # El logo PwC (marca de texto real -- nunca reemplazado por una
        # aproximacion casera) sigue presente.
        assert "brand-mark" in response.text


def test_about_route_works(tmp_path: Path) -> None:
    settings = _settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get("/ui/about")
        assert response.status_code == 200
        assert "Acerca de FIERN" in response.text


def test_about_link_appears_before_api_docs_link(tmp_path: Path) -> None:
    settings = _settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get("/ui/runs")
        about_index = response.text.find("Acerca de")
        docs_index = response.text.find("Documentacion de API")
        if docs_index == -1:
            docs_index = response.text.find("/docs")
        assert about_index != -1
        assert docs_index != -1
        assert about_index < docs_index


def test_api_docs_link_still_available(tmp_path: Path) -> None:
    settings = _settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get("/ui/runs")
        assert 'href="/docs"' in response.text


def test_about_page_has_history_back_navigation(tmp_path: Path) -> None:
    settings = _settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get("/ui/about")
        assert "data-history-back" in response.text
        # Fallback seguro explicito hacia /ui/runs (progressive
        # enhancement: si JS esta deshabilitado, el href normal navega
        # ahi de todos modos). url_for() renderiza URLs absolutas
        # (convencion ya establecida en toda la app), de ahi el
        # substring en vez de un match exacto de href.
        assert "/ui/runs" in response.text


def test_about_page_never_exposes_env_vars_or_secrets(tmp_path: Path) -> None:
    settings = _settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get("/ui/about")
        lowered = response.text.lower()
        for forbidden in ("neo4j_password", "api_key", "secret", ".env", "bolt://"):
            assert forbidden not in lowered


def test_about_page_functional_content_present(tmp_path: Path) -> None:
    settings = _settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get("/ui/about")
        for expected in ("candidato", "contexto", "Limpiar job", "SHA-256", "procesado"):
            assert expected in response.text
