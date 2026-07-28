"""Integracion dedicada de la UI (Prompt 13d): mismo fixture COBOL,
mismo cliente LLM fake y mismos evidence_id empiricamente confirmados
que tests/e2e_support.py (reutilizados por import, nunca reescritos)
-- pero conducidos por TestClient contra `/ui/*`: upload multipart real
con Origin valido, polling del fragmento de estado hasta COMPLETED,
navegacion de las 8 pantallas, descarga via el endpoint binario
`/api/runs/{run_id}/download` ya existente, y verificacion de que un
titulo malicioso generado por el LLM fake se muestra como texto seguro.
Java 17 y Neo4j 5 reales, cero llamadas reales a proveedores. No
levanta Uvicorn."""

from __future__ import annotations

import io
import re
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import altamira_extractor.pipeline.guardrails_applied_stage as guardrails_stage_module
import altamira_extractor.pipeline.rule_drafts_generated_stage as rule_drafts_stage_module
from altamira_extractor.api.app import create_app

from ..e2e_support import (
    build_settings,
    install_dynamic_rule_draft_fake_client,
    install_fake_client,
    require_jar,
    write_package_zip,
)

pytestmark = pytest.mark.integration

SAME_ORIGIN = "http://testserver"
MALICIOUS_TITLE = "<script>alert(1)</script> Regla de saldo negativo"
# Modernizacion UI: candidate_id ahora se muestra como texto tecnico
# secundario dentro de la celda "Programa" (nunca su unico contenido) --
# se extrae por PATRON del ID en si, ya no por posicion exacta en el
# HTML, para no acoplar este test a un unico layout de celda.
_CANDIDATE_ROW_RE = re.compile(r"(candidate::[A-Za-z0-9_.:\-]+)")


def _wait_for_terminal_via_status_fragment(
    client: TestClient, run_id: str, *, timeout: float = 180.0
) -> None:
    """Ejercita el mismo mecanismo de polling autoterminante que un
    navegador real usaria con HTMX: repite GET al fragmento hasta que
    ya no incluya `hx-trigger` (etapa terminal), sin programar ni
    reanudar ninguna ejecucion (GET puro)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/ui/runs/{run_id}/status-fragment")
        assert response.status_code == 200
        if "hx-trigger" not in response.text:
            return
        time.sleep(0.5)
    raise AssertionError("timeout esperando estado terminal via el fragmento de polling de la UI")


def test_ui_end_to_end_upload_polling_navigation_and_download(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    require_jar()
    settings = build_settings(tmp_path)
    install_dynamic_rule_draft_fake_client(
        monkeypatch, rule_drafts_stage_module, title=MALICIOUS_TITLE
    )
    repair_calls = install_fake_client(monkeypatch, guardrails_stage_module, [])

    with TestClient(create_app(settings)) as client:
        zip_path = write_package_zip(tmp_path / "package.zip")
        with zip_path.open("rb") as fh:
            upload_response = client.post(
                "/ui/runs",
                files={"file": ("package.zip", fh, "application/zip")},
                headers={"Origin": SAME_ORIGIN},
                follow_redirects=False,
            )
        assert upload_response.status_code == 303
        location = upload_response.headers["location"]
        assert location.startswith("/ui/runs/")
        run_id = location.rsplit("/", 1)[-1]

        _wait_for_terminal_via_status_fragment(client, run_id)
        assert len(repair_calls) == 0

        status_response = client.get(f"/ui/runs/{run_id}")
        assert status_response.status_code == 200
        assert "COMPLETED" in status_response.text
        assert "FAILED" not in status_response.text
        assert ">Reanudar<" not in status_response.text  # COMPLETED oculta el boton

        candidates_response = client.get(f"/ui/runs/{run_id}/candidates")
        assert candidates_response.status_code == 200
        match = _CANDIDATE_ROW_RE.search(candidates_response.text)
        assert match is not None, candidates_response.text
        candidate_id = match.group(1)

        context_response = client.get(f"/ui/runs/{run_id}/candidates/{candidate_id}/context")
        assert context_response.status_code == 200
        assert "Alcance (D1)" in context_response.text
        assert "Decision (D4)" in context_response.text

        rule_response = client.get(f"/ui/runs/{run_id}/candidates/{candidate_id}/rule")
        assert rule_response.status_code == 200
        assert "<script>alert(1)</script>" not in rule_response.text
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rule_response.text
        # Modernizacion UI: el aviso alarmista de prototipo ya no se
        # muestra (reemplazado por una redaccion profesional
        # equivalente en `.notice`).
        assert (
            "Este documento es un borrador generado automáticamente y requiere revisión "
            "funcional." not in rule_response.text
        )
        assert "EVIDENCE_VALIDATED" in rule_response.text

        guardrail_response = client.get(
            f"/ui/runs/{run_id}/candidates/{candidate_id}/guardrail"
        )
        assert guardrail_response.status_code == 200
        assert "EVIDENCE_VALIDATED" in guardrail_response.text
        assert "repair_history" not in guardrail_response.text

        download_page = client.get(f"/ui/runs/{run_id}/download")
        assert download_page.status_code == 200
        assert f"/api/runs/{run_id}/download" in download_page.text

        zip_response = client.get(f"/api/runs/{run_id}/download")
        assert zip_response.status_code == 200
        assert zip_response.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(zip_response.content)) as archive:
            names = archive.namelist()
            assert "rules-manifest.json" in names
            markdown_name = next(n for n in names if n.endswith(".md"))
            markdown = archive.read(markdown_name).decode("utf-8")
            assert "<script>" not in markdown  # el renderer Markdown ya lo evita

        # /openapi.json sigue sin exponer /ui/* incluso con runs reales.
        openapi = client.get("/openapi.json").json()
        assert not [p for p in openapi["paths"] if p.startswith("/ui")]
