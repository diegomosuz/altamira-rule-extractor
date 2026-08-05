"""`load_security_config` (cierre Fase 15B1, "DISABLED_DEV explicito"):
la ausencia o invalidez de `config/security.yaml` YA NO resuelve en
silencio a `DISABLED_DEV` -- ver docstring del modulo bajo prueba."""

from __future__ import annotations

from pathlib import Path

from altamira_extractor.contracts.security_config import AuthenticationMode
from altamira_extractor.security.security_config_loader import (
    SecurityConfigOutcome,
    load_security_config,
)

_VALID_DISABLED_DEV_YAML = "\n".join(
    [
        'schema_version: "1.0"',
        "authentication_mode: DISABLED_DEV",
        'trusted_proxy_header_user: "X-User"',
        'trusted_proxy_required_marker_header: "X-Marker"',
        'trusted_proxy_required_marker_value: "dev-marker-not-enforced"',
        'session_cookie_name: "altamira_session"',
        "session_cookie_secure: false",
    ]
)

_VALID_TRUSTED_PROXY_YAML = "\n".join(
    [
        'schema_version: "1.0"',
        "authentication_mode: TRUSTED_PROXY_HEADERS",
        'trusted_proxy_header_user: "X-User"',
        'trusted_proxy_required_marker_header: "X-Marker"',
        'trusted_proxy_required_marker_value: "synthetic-marker-value"',
        'session_cookie_name: "altamira_session"',
        "session_cookie_secure: true",
    ]
)


def test_missing_file_returns_missing_outcome(tmp_path: Path) -> None:
    result = load_security_config(tmp_path / "does-not-exist.yaml")
    assert result.outcome == SecurityConfigOutcome.MISSING
    assert result.config is None
    assert result.error is None


def test_empty_file_returns_invalid_outcome(tmp_path: Path) -> None:
    path = tmp_path / "security.yaml"
    path.write_text("", encoding="utf-8")
    result = load_security_config(path)
    assert result.outcome == SecurityConfigOutcome.INVALID
    assert result.config is None
    assert result.error is not None


def test_whitespace_only_file_returns_invalid_outcome(tmp_path: Path) -> None:
    path = tmp_path / "security.yaml"
    path.write_text("   \n\n  \n", encoding="utf-8")
    result = load_security_config(path)
    assert result.outcome == SecurityConfigOutcome.INVALID
    assert result.config is None


def test_malformed_yaml_returns_invalid_outcome(tmp_path: Path) -> None:
    path = tmp_path / "security.yaml"
    path.write_text("authentication_mode: [unterminated\n  - broken", encoding="utf-8")
    result = load_security_config(path)
    assert result.outcome == SecurityConfigOutcome.INVALID
    assert result.config is None
    assert result.error is not None


def test_non_utf8_file_returns_invalid_outcome(tmp_path: Path) -> None:
    path = tmp_path / "security.yaml"
    path.write_bytes(b"\xff\xfe\x00\x01authentication_mode: DISABLED_DEV")
    result = load_security_config(path)
    assert result.outcome == SecurityConfigOutcome.INVALID
    assert result.config is None


def test_schema_incompatible_missing_required_field_returns_invalid(tmp_path: Path) -> None:
    path = tmp_path / "security.yaml"
    # Falta `authentication_mode`, campo obligatorio (sin default).
    path.write_text(
        "\n".join(
            [
                'schema_version: "1.0"',
                'trusted_proxy_header_user: "X-User"',
                'trusted_proxy_required_marker_header: "X-Marker"',
                'trusted_proxy_required_marker_value: "m"',
                'session_cookie_name: "altamira_session"',
                "session_cookie_secure: true",
            ]
        ),
        encoding="utf-8",
    )
    result = load_security_config(path)
    assert result.outcome == SecurityConfigOutcome.INVALID
    assert result.config is None
    assert result.error is not None


def test_schema_incompatible_wrong_type_returns_invalid(tmp_path: Path) -> None:
    path = tmp_path / "security.yaml"
    path.write_text("- this\n- is\n- a\n- list\n- not\n- a\n- mapping\n", encoding="utf-8")
    result = load_security_config(path)
    assert result.outcome == SecurityConfigOutcome.INVALID
    assert result.config is None


def test_schema_incompatible_dangerous_header_returns_invalid(tmp_path: Path) -> None:
    path = tmp_path / "security.yaml"
    path.write_text(
        "\n".join(
            [
                'schema_version: "1.0"',
                "authentication_mode: TRUSTED_PROXY_HEADERS",
                'trusted_proxy_header_user: "Authorization"',
                'trusted_proxy_required_marker_header: "X-Marker"',
                'trusted_proxy_required_marker_value: "m"',
                'session_cookie_name: "altamira_session"',
                "session_cookie_secure: true",
            ]
        ),
        encoding="utf-8",
    )
    result = load_security_config(path)
    assert result.outcome == SecurityConfigOutcome.INVALID
    assert result.config is None


def test_explicit_disabled_dev_loads_successfully(tmp_path: Path) -> None:
    path = tmp_path / "security.yaml"
    path.write_text(_VALID_DISABLED_DEV_YAML, encoding="utf-8")
    result = load_security_config(path)
    assert result.outcome == SecurityConfigOutcome.LOADED
    assert result.config is not None
    assert result.config.authentication_mode == AuthenticationMode.DISABLED_DEV
    assert result.error is None


def test_explicit_trusted_proxy_headers_loads_successfully(tmp_path: Path) -> None:
    path = tmp_path / "security.yaml"
    path.write_text(_VALID_TRUSTED_PROXY_YAML, encoding="utf-8")
    result = load_security_config(path)
    assert result.outcome == SecurityConfigOutcome.LOADED
    assert result.config is not None
    assert result.config.authentication_mode == AuthenticationMode.TRUSTED_PROXY_HEADERS
    assert result.error is None


def test_symlink_treated_as_missing_never_followed(tmp_path: Path) -> None:
    real_target = tmp_path / "real-security.yaml"
    real_target.write_text(_VALID_TRUSTED_PROXY_YAML, encoding="utf-8")
    symlink_path = tmp_path / "security.yaml"
    try:
        symlink_path.symlink_to(real_target)
    except OSError:
        # Sin privilegios de symlink en este entorno (comun en CI de
        # Windows sin modo desarrollador) -- el caso relevante
        # (symlink nunca seguido) no es verificable aqui, se omite en
        # vez de fallar por una limitacion del entorno de test.
        return
    result = load_security_config(symlink_path)
    assert result.outcome == SecurityConfigOutcome.MISSING
    assert result.config is None
