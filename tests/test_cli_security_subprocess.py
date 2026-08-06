"""Tests de seguridad del CLI via subprocess REAL (cierre de
seguridad de Fase 12, `feat/unified-shadow-differential-validation`,
Partes 1 y 5). `CliRunner` (usado en el resto de la suite) captura la
salida DENTRO del mismo proceso pytest -- nunca demuestra si un
traceback llega realmente al stderr de un usuario en una terminal
real, porque el logger raiz de Python (`logging.lastResort`) se
comporta distinto segun si el proceso ya tiene handlers instalados
por otra cosa (p. ej. pytest). Estos tests invocan
`python -m altamira_extractor.cli` como un proceso HIJO real,
exactamente como lo haria un operador, y verifican la salida cruda de
stdout/stderr: cero "Traceback", cero rutas absolutas del contenedor
(`/workspace/`) ni de Windows (`C:\\`), exit code distinto de cero, y
ningun archivo parcial."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import altamira_extractor.cli as cli_module
from altamira_extractor.contracts.enums import PipelineStage
from altamira_extractor.pipeline.unified_candidates_shadow_service import (
    compute_unified_candidates_shadow_artifact,
    write_unified_candidates_shadow_artifact,
)

from .e2e_support import build_settings
from .test_cli_unified_candidates_shadow import (
    _NONEXISTENT_RUN_ID,
    _RUN_ID,
    _generate_review_package_and_plan_via_cli,
    _write_parsed_run,
    _write_run_state,
)

_FORBIDDEN_SUBSTRINGS = ("Traceback", "/workspace/", "C:\\")

# Nombres exactos y patrones de variables sensibles/de proveedor LLM a
# eliminar del entorno heredado (cierre correctivo 15B2-B, Seccion 7):
# nunca se reemplaza `os.environ` por un mapa incompleto (eso rompia la
# resolucion de site-packages en Windows, que depende de `SystemRoot`/
# `USERPROFILE`/`APPDATA`/`PATH`) -- se hereda todo y se elimina
# unicamente lo sensible.
_SENSITIVE_ENV_VAR_PATTERN = re.compile(
    r"(API_KEY|SECRET|TOKEN|PASSWORD)", re.IGNORECASE
)
_PROVIDER_ENV_VAR_PREFIXES = ("LLM_", "OPENAI_", "PWC_GENAI_", "ALTAMIRA_")


def _sanitized_inherited_env() -> dict[str, str]:
    """Entorno heredado del proceso de test (nunca un mapa incompleto:
    conserva `PATH`/`SystemRoot`/`USERPROFILE`/`APPDATA`, necesarios en
    Windows para que el subproceso hijo resuelva `site-packages` e
    importe `altamira_extractor`), con variables sensibles y de
    proveedor/config de la app eliminadas -- nunca provider real,
    nunca secretos heredados del entorno del desarrollador."""
    sanitized: dict[str, str] = {}
    for key, value in os.environ.items():
        if _SENSITIVE_ENV_VAR_PATTERN.search(key):
            continue
        if any(key.startswith(prefix) for prefix in _PROVIDER_ENV_VAR_PREFIXES):
            continue
        sanitized[key] = value
    return sanitized


def _settings_env(tmp_path: Path) -> dict[str, str]:
    env = _sanitized_inherited_env()
    env["ALTAMIRA_DATA_DIR"] = str(tmp_path / "data")
    env["ALTAMIRA_RUNS_DIR"] = str(tmp_path / "data" / "runs")
    env["ALTAMIRA_INCOMING_DIR"] = str(tmp_path / "data" / "incoming")
    return env


def _run_cli(args: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "altamira_extractor.cli", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _assert_sanitized_failure(
    result: subprocess.CompletedProcess[str], *, env: dict[str, str]
) -> None:
    combined = result.stdout + result.stderr
    assert result.returncode != 0, (
        f"se esperaba exit code != 0, fue {result.returncode}: {combined!r}"
    )
    for forbidden in _FORBIDDEN_SUBSTRINGS:
        assert forbidden not in combined, f"{forbidden!r} presente en la salida: {combined!r}"
    # Ninguna ruta absoluta de este run: el directorio de datos del
    # escenario nunca debe filtrarse a la salida.
    assert env["ALTAMIRA_RUNS_DIR"] not in combined


# --- Cierre correctivo 15B2-B, Seccion 7: regresion del entorno heredado ---


def test_sanitized_env_strips_sensitive_and_provider_vars_but_keeps_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regresion Windows: el defecto real corregido en este bloque fue
    reemplazar `os.environ` completo por un mapa de 3 claves, lo que
    rompia la resolucion de `site-packages` en Windows (faltaban
    `SystemRoot`/`USERPROFILE`/`PATH`) y producia
    `ModuleNotFoundError: No module named 'altamira_extractor'` en el
    subproceso -- nunca un fallo de seguridad real, pero rompia estos
    18 tests. La correccion hereda `os.environ` y elimina solo lo
    sensible/de proveedor."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-be-removed")
    monkeypatch.setenv("PWC_GENAI_API_KEY", "should-be-removed-too")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("NEO4J_PASSWORD", "should-be-removed-as-well")
    monkeypatch.setenv("SOME_RANDOM_SECRET_TOKEN", "should-be-removed")
    monkeypatch.setenv("ALTAMIRA_SESSION_SECRET", "should-be-removed")

    sanitized = _sanitized_inherited_env()

    for forbidden_key in (
        "OPENAI_API_KEY",
        "PWC_GENAI_API_KEY",
        "LLM_PROVIDER",
        "NEO4J_PASSWORD",
        "SOME_RANDOM_SECRET_TOKEN",
        "ALTAMIRA_SESSION_SECRET",
    ):
        assert forbidden_key not in sanitized

    # Variables necesarias en Windows para que un subproceso hijo
    # resuelva Python/site-packages -- nunca deben eliminarse. El
    # nombre exacto de la variable de sistema varia segun el shell
    # (`SystemRoot` en cmd/PowerShell, `SYSTEMROOT` en Git Bash), asi
    # que se compara sin distinguir mayusculas.
    assert "PATH" in sanitized
    assert sanitized["PATH"] == os.environ["PATH"]
    sanitized_upper_keys = {k.upper() for k in sanitized}
    if any(k.upper() == "SYSTEMROOT" for k in os.environ):
        assert "SYSTEMROOT" in sanitized_upper_keys


# --- Parte 1: unified-shadow-validate ---


def test_subprocess_unified_shadow_validate_nonexistent_run(tmp_path: Path) -> None:
    env = _settings_env(tmp_path)
    result = _run_cli(["unified-shadow-validate", _NONEXISTENT_RUN_ID], env=env)
    _assert_sanitized_failure(result, env=env)
    runs_dir = tmp_path / "data" / "runs"
    assert not runs_dir.exists() or not any(runs_dir.iterdir())


def test_subprocess_unified_shadow_validate_stage_insufficient(tmp_path: Path) -> None:
    env = _settings_env(tmp_path)
    run_dir = tmp_path / "data" / "runs" / _RUN_ID
    _write_run_state(run_dir, stages=(PipelineStage.RECEIVED,))
    result = _run_cli(["unified-shadow-validate", _RUN_ID], env=env)
    _assert_sanitized_failure(result, env=env)
    diagnostics_dir = run_dir / "diagnostics"
    assert not diagnostics_dir.exists() or not any(diagnostics_dir.iterdir())


def test_subprocess_unified_shadow_validate_write_failure(tmp_path: Path) -> None:
    """Fuerza un fallo de escritura real: el destino del reporte ya
    existe como DIRECTORIO en vez de archivo."""
    settings = build_settings(tmp_path)
    cli_module.load_settings = lambda: settings  # type: ignore[assignment]
    run_dir = settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_review_package_and_plan_via_cli(run_dir, tmp_path)
    artifact = compute_unified_candidates_shadow_artifact(run_dir, _RUN_ID)
    write_unified_candidates_shadow_artifact(run_dir, artifact)
    report_path = run_dir / "diagnostics" / "unified-shadow-validation-report.json"
    report_path.mkdir(parents=True, exist_ok=True)

    env = _settings_env(tmp_path)
    result = _run_cli(["unified-shadow-validate", _RUN_ID], env=env)
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    for forbidden in _FORBIDDEN_SUBSTRINGS:
        assert forbidden not in combined, f"{forbidden!r} presente en la salida: {combined!r}"
    # El directorio forzado sigue siendo un directorio -- nunca se
    # reemplazo por un archivo parcial ni se dejo un `.tmp` huerfano.
    assert report_path.is_dir()
    tmp_leftovers = list((run_dir / "diagnostics").glob("*.tmp"))
    assert tmp_leftovers == [], f"archivos temporales huerfanos: {tmp_leftovers}"


# --- Semantica dura de unified-candidates-shadow.json: el objeto
# --- PRINCIPAL de esta validacion, nunca una fuente opcional. Su
# --- ausencia, invalidez o un fallo de lectura son SIEMPRE errores
# --- tecnicos duros -- verificado via subprocess real, no solo
# --- CliRunner. ---


def test_subprocess_unified_shadow_validate_unified_artifact_missing(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    cli_module.load_settings = lambda: settings  # type: ignore[assignment]
    run_dir = settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_review_package_and_plan_via_cli(run_dir, tmp_path)
    # Deliberadamente NO se genera unified-candidates-shadow.json.

    env = _settings_env(tmp_path)
    result = _run_cli(["unified-shadow-validate", _RUN_ID], env=env)
    _assert_sanitized_failure(result, env=env)
    report_path = run_dir / "diagnostics" / "unified-shadow-validation-report.json"
    assert not report_path.exists()


def test_subprocess_unified_shadow_validate_unified_artifact_invalid_json(
    tmp_path: Path,
) -> None:
    settings = build_settings(tmp_path)
    cli_module.load_settings = lambda: settings  # type: ignore[assignment]
    run_dir = settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_review_package_and_plan_via_cli(run_dir, tmp_path)
    artifact = compute_unified_candidates_shadow_artifact(run_dir, _RUN_ID)
    write_unified_candidates_shadow_artifact(run_dir, artifact)
    unified_shadow_path = run_dir / "diagnostics" / "unified-candidates-shadow.json"
    unified_shadow_path.write_text("{SECRET_TOKEN_MARKER not valid json!!", encoding="utf-8")

    env = _settings_env(tmp_path)
    result = _run_cli(["unified-shadow-validate", _RUN_ID], env=env)
    _assert_sanitized_failure(result, env=env)
    combined = result.stdout + result.stderr
    assert "SECRET_TOKEN_MARKER" not in combined
    report_path = run_dir / "diagnostics" / "unified-shadow-validation-report.json"
    assert not report_path.exists()


def test_subprocess_unified_shadow_validate_unified_artifact_incompatible_version(
    tmp_path: Path,
) -> None:
    settings = build_settings(tmp_path)
    cli_module.load_settings = lambda: settings  # type: ignore[assignment]
    run_dir = settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_review_package_and_plan_via_cli(run_dir, tmp_path)
    artifact = compute_unified_candidates_shadow_artifact(run_dir, _RUN_ID)
    write_unified_candidates_shadow_artifact(run_dir, artifact)
    unified_shadow_path = run_dir / "diagnostics" / "unified-candidates-shadow.json"
    payload = json.loads(unified_shadow_path.read_text(encoding="utf-8"))
    payload["schema_version"] = "99.0"
    unified_shadow_path.write_text(json.dumps(payload), encoding="utf-8")

    env = _settings_env(tmp_path)
    result = _run_cli(["unified-shadow-validate", _RUN_ID], env=env)
    _assert_sanitized_failure(result, env=env)
    report_path = run_dir / "diagnostics" / "unified-shadow-validation-report.json"
    assert not report_path.exists()


def test_subprocess_unified_shadow_validate_unified_artifact_invalid_contract(
    tmp_path: Path,
) -> None:
    settings = build_settings(tmp_path)
    cli_module.load_settings = lambda: settings  # type: ignore[assignment]
    run_dir = settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_review_package_and_plan_via_cli(run_dir, tmp_path)
    artifact = compute_unified_candidates_shadow_artifact(run_dir, _RUN_ID)
    write_unified_candidates_shadow_artifact(run_dir, artifact)
    unified_shadow_path = run_dir / "diagnostics" / "unified-candidates-shadow.json"
    payload = json.loads(unified_shadow_path.read_text(encoding="utf-8"))
    payload["baseline_candidates"] = [{"baseline_reference_id": "baseline::broken::1"}]
    unified_shadow_path.write_text(json.dumps(payload), encoding="utf-8")

    env = _settings_env(tmp_path)
    result = _run_cli(["unified-shadow-validate", _RUN_ID], env=env)
    _assert_sanitized_failure(result, env=env)
    report_path = run_dir / "diagnostics" / "unified-shadow-validation-report.json"
    assert not report_path.exists()


def test_subprocess_unified_shadow_validate_unified_artifact_read_error(
    tmp_path: Path,
) -> None:
    """Fallo de lectura real del filesystem: el destino del artefacto
    unificado es un DIRECTORIO, no un archivo."""
    settings = build_settings(tmp_path)
    cli_module.load_settings = lambda: settings  # type: ignore[assignment]
    run_dir = settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_review_package_and_plan_via_cli(run_dir, tmp_path)
    unified_shadow_path = run_dir / "diagnostics" / "unified-candidates-shadow.json"
    unified_shadow_path.mkdir(parents=True, exist_ok=True)

    env = _settings_env(tmp_path)
    result = _run_cli(["unified-shadow-validate", _RUN_ID], env=env)
    _assert_sanitized_failure(result, env=env)
    report_path = run_dir / "diagnostics" / "unified-shadow-validation-report.json"
    assert not report_path.exists()
    assert unified_shadow_path.is_dir()


# --- Parte 1: comandos de fases anteriores (mismo _guard compartido) ---


def test_subprocess_unified_candidates_shadow_nonexistent_run(tmp_path: Path) -> None:
    env = _settings_env(tmp_path)
    result = _run_cli(["unified-candidates-shadow", _NONEXISTENT_RUN_ID], env=env)
    _assert_sanitized_failure(result, env=env)


def test_subprocess_candidate_promotion_assessment_nonexistent_run(tmp_path: Path) -> None:
    env = _settings_env(tmp_path)
    result = _run_cli(["candidate-promotion-assessment", _NONEXISTENT_RUN_ID], env=env)
    _assert_sanitized_failure(result, env=env)


# --- Parte 5: regresion CLI Fase 9-12 (salidas exitosas intactas) ---


def test_subprocess_unified_shadow_validate_success_path_unchanged(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    cli_module.load_settings = lambda: settings  # type: ignore[assignment]
    run_dir = settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_review_package_and_plan_via_cli(run_dir, tmp_path)
    artifact = compute_unified_candidates_shadow_artifact(run_dir, _RUN_ID)
    write_unified_candidates_shadow_artifact(run_dir, artifact)

    env = _settings_env(tmp_path)
    result = _run_cli(["unified-shadow-validate", _RUN_ID], env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"run_id: {_RUN_ID}" in result.stdout
    # Escenario vacio (0 candidatos) con TODAS las fuentes disponibles:
    # cero grupos -> cero elegibles -> REVIEW_REQUIRED (nunca un error).
    assert "disposition: REVIEW_REQUIRED" in result.stdout

    result_json = _run_cli(["unified-shadow-validate", _RUN_ID, "--json"], env=env)
    assert result_json.returncode == 0
    json_start = result_json.stdout.index("{")
    payload = json.loads(result_json.stdout[json_start:])
    assert payload["run_id"] == _RUN_ID


def test_subprocess_unified_candidates_shadow_success_path_unchanged(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    cli_module.load_settings = lambda: settings  # type: ignore[assignment]
    run_dir = settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_review_package_and_plan_via_cli(run_dir, tmp_path)

    env = _settings_env(tmp_path)
    result = _run_cli(["unified-candidates-shadow", _RUN_ID], env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"run_id: {_RUN_ID}" in result.stdout
    assert "shadow_members: 0" in result.stdout


@pytest.mark.parametrize(
    "command",
    [
        ["unified-shadow-validate", _NONEXISTENT_RUN_ID],
        ["unified-candidates-shadow", _NONEXISTENT_RUN_ID],
        ["candidate-promotion-assessment", _NONEXISTENT_RUN_ID],
        ["candidate-promotion-review-package", _NONEXISTENT_RUN_ID],
        ["status", _NONEXISTENT_RUN_ID],
        ["candidates", _NONEXISTENT_RUN_ID],
    ],
)
def test_subprocess_cli_commands_never_leak_traceback_or_paths(
    tmp_path: Path, command: list[str]
) -> None:
    """Regresion transversal (Parte 5): comandos representativos de
    Fase 9 (`candidate-promotion-assessment`, `candidate-promotion-
    review-package`), del pipeline base (`status`, `candidates`) y de
    Fase 11/12 (`unified-candidates-shadow`, `unified-shadow-validate`)
    contra un run inexistente -- todos comparten `_guard`, todos deben
    fallar identicamente sanitizados."""
    env = _settings_env(tmp_path)
    result = _run_cli(command, env=env)
    _assert_sanitized_failure(result, env=env)
