"""Fase 15B4-B2: confirma que el `security.yaml` empaquetado en
`deploy/k3s/secret.example.yaml` (a) carga como `TRUSTED_PROXY_HEADERS`
valido via el loader REAL (`security/security_config_loader.py`, nunca
reimplementado aqui), y (b) que los placeholders de
`NEO4J_PASSWORD`/`ALTAMIRA_SESSION_SECRET` son deliberadamente
demasiado cortos para pasar sus respectivos validadores reales si se
dejan sin reemplazar -- fallo visible, nunca un valor "tecnicamente
valido" pero predecible quedando activo en silencio.

`/ready` en si mismo NUNCA se modifica ni se reimplementa -- estos
tests ejercitan directamente los mismos componentes que
`api/routers/health.py::_check_security_configuration` y
`api/app.py` ya usan en produccion."""

from __future__ import annotations

from pathlib import Path

import pydantic
import pytest
import yaml

from altamira_extractor.config import Settings
from altamira_extractor.contracts.security_config import AuthenticationMode
from altamira_extractor.security.security_config_loader import (
    SecurityConfigOutcome,
    load_security_config,
)

DEPLOY_DIR = Path(__file__).resolve().parents[2] / "deploy" / "k3s"


def _packaged_secret_data() -> dict[str, str]:
    text = (DEPLOY_DIR / "secret.example.yaml").read_text(encoding="utf-8")
    docs = list(yaml.safe_load_all(text))
    secret = next(d for d in docs if d.get("kind") == "Secret")
    return secret["stringData"]


def test_packaged_security_yaml_loads_as_trusted_proxy_headers(tmp_path: Path) -> None:
    """(a) config segura completa -> LOADED -> /ready puede estar READY
    (mismo camino real que api/app.py::create_app usa)."""
    security_yaml_text = _packaged_secret_data()["security.yaml"]
    path = tmp_path / "security.yaml"
    path.write_text(security_yaml_text, encoding="utf-8")

    result = load_security_config(path)

    assert result.outcome == SecurityConfigOutcome.LOADED
    assert result.config is not None
    assert result.config.authentication_mode == AuthenticationMode.TRUSTED_PROXY_HEADERS
    assert result.config.session_cookie_secure is True


def test_packaged_security_yaml_is_never_disabled_dev() -> None:
    """K3s release: TRUSTED_PROXY_HEADERS. LOCAL DEVELOPMENT (config/
    security.example.yaml, no tocado por esta fase): DISABLED_DEV.
    Nunca invertidos."""
    security_yaml_text = _packaged_secret_data()["security.yaml"]
    assert "authentication_mode: TRUSTED_PROXY_HEADERS" in security_yaml_text
    assert "DISABLED_DEV" not in security_yaml_text


def test_missing_security_yaml_fails_closed(tmp_path: Path) -> None:
    """(b) ausencia total -> MISSING -> /ready falla (security_
    misconfigured) -- mismo comportamiento documentado en
    security_config_loader.py, ejercitado aqui contra un path real
    inexistente."""
    result = load_security_config(tmp_path / "does-not-exist.yaml")
    assert result.outcome == SecurityConfigOutcome.MISSING
    assert result.config is None


def test_invalid_security_yaml_fails_closed(tmp_path: Path) -> None:
    """(b) config incompleta/invalida -> INVALID -> /ready falla. Se
    corrompe deliberadamente un campo obligatorio del YAML empaquetado
    (nunca se inventa un schema nuevo)."""
    security_yaml_text = _packaged_secret_data()["security.yaml"]
    broken = security_yaml_text.replace(
        'trusted_proxy_header_user: "X-Altamira-Verified-User"',
        'trusted_proxy_header_user: ""',
    )
    path = tmp_path / "security.yaml"
    path.write_text(broken, encoding="utf-8")

    result = load_security_config(path)

    assert result.outcome == SecurityConfigOutcome.INVALID
    assert result.config is None
    assert result.error is not None


def test_neo4j_password_placeholder_is_shorter_than_neo4j_minimum() -> None:
    """La imagen oficial de Neo4j exige >=8 caracteres (ver docker-
    compose.yml/deploy/k3s/neo4j-statefulset.yaml) -- el placeholder
    empaquetado debe ser MAS CORTO, nunca "tecnicamente valido"."""
    placeholder = _packaged_secret_data()["NEO4J_PASSWORD"]
    assert len(placeholder) < 8


def test_session_secret_placeholder_is_shorter_than_settings_minimum(tmp_path: Path) -> None:
    """config.py::_check_session_secret_minimum_length exige >=32
    caracteres -- el placeholder empaquetado debe fallar esa
    validacion real (Settings() debe lanzar), nunca aceptarse."""
    placeholder = _packaged_secret_data()["ALTAMIRA_SESSION_SECRET"]
    assert len(placeholder) < 32

    with pytest.raises(pydantic.ValidationError):
        Settings(
            _env_file=None,
            data_dir=tmp_path / "data",
            runs_dir=tmp_path / "data" / "runs",
            incoming_dir=tmp_path / "data" / "incoming",
            ALTAMIRA_SESSION_SECRET=placeholder,
        )
