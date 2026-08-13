"""Integracion dedicada de la API (Prompt 13b): upload real -> Java 17 +
Neo4j 5 reales -> polling hasta COMPLETED -> candidates/context/rule ->
descarga -> validacion del contenido. Cliente LLM fake (nunca un
proveedor real).

El fixture COBOL y los evidence_id hardcodeados en el payload del LLM
fake fueron confirmados de forma empirica corriendo el pipeline real
contra este mismo fixture (mismo programa, mismo manifest): son
deterministicos para este contenido exacto, no arbitrarios. Los
helpers compartidos (ZIP, payload, fake client, Settings) viven en
`tests/e2e_support.py` (Prompt 14b): este archivo ya no es la fuente
importada por los otros E2E de proceso unico."""

from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path
from typing import Any

import neo4j
import pytest
from fastapi.testclient import TestClient

import altamira_extractor.pipeline.guardrails_applied_stage as guardrails_stage_module
import altamira_extractor.pipeline.rule_drafts_generated_stage as rule_drafts_stage_module
from altamira_extractor.api.app import create_app
from altamira_extractor.config import Settings

from ..e2e_support import (
    MANIFEST_XML,
    PARAM_DEMO_DDL,
    build_settings,
    install_dynamic_rule_draft_fake_client,
    install_fake_client,
    regular_file_info,
    require_jar,
    write_package_zip,
)

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


def _wait_for_terminal(
    client: TestClient, run_id: str, *, timeout: float = 180.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_body: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        if response.status_code == 200:
            last_body = response.json()
            if last_body["current_stage"] in ("COMPLETED", "FAILED"):
                return last_body
        time.sleep(0.5)
    raise AssertionError(f"timeout esperando estado terminal; ultimo body: {last_body}")


def _assert_normalized_expression_flows_through_the_pipeline(
    settings: Settings, run_id: str, source_package_hash: str
) -> None:
    """Aserciones EXPLICITAS del fix del parser (Prompt 13b) en cada capa
    intermedia -- que la API llegue a COMPLETED no es evidencia suficiente
    por si sola de que normalized_expression realmente fluyo correctamente
    en cada punto."""
    run_dir = settings.runs_dir / run_id

    # 1) CanonicalStatement.expression / normalized_expression (JAR real).
    # El artefacto replica el arbol de origen (p. ej.
    # 01-codigo/cobol/PROGRULE1.cbl.json), nunca queda plano bajo
    # 02-canonical/: por eso la busqueda debe ser recursiva.
    canonical_dir = run_dir / "artifacts" / "02-canonical"
    canonical_path = next(canonical_dir.glob("**/*.json"))
    canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
    if_statement = next(
        stmt
        for paragraph in canonical["paragraphs"]
        for stmt in paragraph["statements"]
        if stmt["kind"] == "IF"
    )
    assert if_statement["expression"] is not None
    assert if_statement["normalized_expression"] is not None
    assert if_statement["normalized_expression"] == if_statement["expression"].strip()

    # 2) Decision.normalized_expression persistida en Neo4j real, y 3) Q4
    # (el archivo real de queries/v1/, no una copia) la devuelve.
    driver = neo4j.GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
    )
    try:
        with driver.session(database=settings.neo4j_database) as session:
            decision_row = session.run(
                "MATCH (par:Paragraph)-[:HAS_DECISION]->(dec:Decision) "
                "WHERE dec.source_package_hash = $hash "
                "RETURN par.id AS paragraph_id, dec.id AS decision_id, "
                "       dec.expression AS expression, "
                "       dec.normalized_expression AS normalized_expression",
                hash=source_package_hash,
            ).single()
            assert decision_row is not None
            assert decision_row["normalized_expression"] is not None
            assert decision_row["normalized_expression"] == decision_row["expression"].strip()

            q4_text = (REPO_ROOT / "queries" / "v1" / "q4_decision.cypher").read_text(
                encoding="utf-8"
            )
            q4_row = session.run(
                q4_text,
                paragraph_id=decision_row["paragraph_id"],
                decision_id=decision_row["decision_id"],
            ).single()
            assert q4_row is not None
            assert q4_row["normalized_condition"] is not None
            assert q4_row["normalized_condition"] == decision_row["normalized_expression"]
    finally:
        driver.close()


def test_api_end_to_end_reaches_completed_and_downloads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    require_jar()
    settings = build_settings(tmp_path)
    install_dynamic_rule_draft_fake_client(monkeypatch, rule_drafts_stage_module)
    # Ninguna reparacion deberia invocarse: el draft inicial ya pasa el
    # guardrail.
    repair_calls = install_fake_client(monkeypatch, guardrails_stage_module, [])

    with TestClient(create_app(settings)) as client:
        zip_path = write_package_zip(tmp_path / "package.zip")
        with zip_path.open("rb") as fh:
            response = client.post(
                "/api/runs", files={"file": ("package.zip", fh, "application/zip")}
            )
        assert response.status_code == 202
        run_id = response.json()["run_id"]

        final = _wait_for_terminal(client, run_id)
        assert final["current_stage"] == "COMPLETED", final

        stage_names = [s["stage"] for s in final["stages"]]
        assert len(stage_names) == len(set(stage_names))
        assert stage_names == [
            "RECEIVED",
            "VALIDATED",
            "EXTRACTED",
            "INVENTORIED",
            "PARSED",
            "DEPENDENCIES_BUILT",
            "SEMANTIC_ENRICHMENT_BUILT",
            "SEMANTIC_GRAPH_BUILT",
            "SEMANTIC_GRAPH_LOADED",
            "GRAPH_VALIDATED",
            "CANDIDATES_DETECTED",
            "CONTEXTS_BUILT",
            "RULE_DRAFTS_GENERATED",
            "GUARDRAILS_APPLIED",
            "COMPLETED",
        ]
        assert all(s["status"] == "SUCCEEDED" for s in final["stages"])
        assert len(repair_calls) == 0

        # Puntos intermedios del fix de normalized_expression (Prompt
        # 13b): CanonicalStatement, Decision en Neo4j real, Q4 real.
        _assert_normalized_expression_flows_through_the_pipeline(
            settings, run_id, final["source_package_hash"]
        )

        candidates_response = client.get(f"/api/runs/{run_id}/candidates")
        assert candidates_response.status_code == 200
        candidates = candidates_response.json()["candidates"]
        assert len(candidates) == 1
        candidate_id = candidates[0]["candidate_id"]
        assert candidates[0]["outcome_code"] == "R001"

        context_response = client.get(f"/api/runs/{run_id}/candidates/{candidate_id}/context")
        assert context_response.status_code == 200
        context_decision = context_response.json()["decision"]
        assert context_decision["outcome_code"] == "R001"
        # 4) ContextPackageDecision.normalized_expression valida (Pydantic
        # ya la exige min_length=1 al parsear la respuesta JSON).
        assert context_decision["normalized_expression"]
        assert context_decision["normalized_expression"] == context_decision["expression"].strip()

        rule_response = client.get(f"/api/runs/{run_id}/candidates/{candidate_id}/rule")
        assert rule_response.status_code == 200
        rule_body = rule_response.json()
        assert rule_body["candidate_id"] == candidate_id
        assert rule_body["final_rule_draft"]["evidence_validation_status"] == "EVIDENCE_VALIDATED"
        assert rule_body["final_rule_draft"]["title"] == "Regla de saldo negativo"
        assert rule_body["guardrail"]["verdict"] == "EVIDENCE_VALIDATED"
        assert rule_body["guardrail"]["repair_attempts_used"] == 0

        download_response = client.get(f"/api/runs/{run_id}/download")
        assert download_response.status_code == 200
        assert download_response.headers["content-type"] == "application/zip"

        with zipfile.ZipFile(io.BytesIO(download_response.content)) as archive:
            names = archive.namelist()
            assert "rules-manifest.json" in names
            md_files = [n for n in names if n.endswith(".md")]
            assert len(md_files) == 1
            markdown_text = archive.read(md_files[0]).decode("utf-8")
            assert markdown_text.startswith("# Regla de saldo negativo\n")
            assert "> Estado de evidencia: EVIDENCE_VALIDATED" in markdown_text


def test_api_guardrail_count_zero_reaches_completed_when_q0_finds_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Un paquete sin patron de return_code: Q0 no encuentra candidatos y
    el pipeline igual alcanza COMPLETED (0 reglas), sin llamar al LLM.

    Fase 15B4-CANDIDATE-QUALITY-5E: enhanced_candidates_enabled=False
    explicito -- el programa fixture SI contiene un ADD (CALCULATION),
    asi que el escenario "cero candidatos" es especificamente sobre
    Q0/V1, no sobre la deteccion ampliada."""
    require_jar()
    settings = build_settings(tmp_path, enhanced_candidates_enabled=False)
    # Ningun candidato: ni RULE_DRAFTS_GENERATED ni GUARDRAILS_APPLIED
    # deberian llamar al LLM.
    install_fake_client(monkeypatch, rule_drafts_stage_module, [])
    install_fake_client(monkeypatch, guardrails_stage_module, [])

    program_without_decision = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. PROGNODEC.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-CONTADOR PIC 9(4) VALUE 0.
       PROCEDURE DIVISION.
       MAIN-PARA.
           ADD 1 TO WS-CONTADOR.
           GOBACK.
"""

    with TestClient(create_app(settings)) as client:
        zip_path = tmp_path / "package.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            manifest = MANIFEST_XML.replace(b"PROGRULE1", b"PROGNODEC")
            zf.writestr(regular_file_info("manifest.xml"), manifest)
            zf.writestr(
                regular_file_info("01-codigo/cobol/PROGNODEC.cbl"), program_without_decision
            )
            zf.writestr(regular_file_info("02-parametria/ddl/PARAM_DEMO.sql"), PARAM_DEMO_DDL)

        with zip_path.open("rb") as fh:
            response = client.post(
                "/api/runs", files={"file": ("package.zip", fh, "application/zip")}
            )
        assert response.status_code == 202
        run_id = response.json()["run_id"]

        final = _wait_for_terminal(client, run_id)
        assert final["current_stage"] == "COMPLETED", final

        candidates_response = client.get(f"/api/runs/{run_id}/candidates")
        assert candidates_response.json()["candidates"] == []

        download_response = client.get(f"/api/runs/{run_id}/download")
        assert download_response.status_code == 200
