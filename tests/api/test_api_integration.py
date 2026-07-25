"""Integracion dedicada de la API (Prompt 13b): upload real -> Java 17 +
Neo4j 5 reales -> polling hasta COMPLETED -> candidates/context/rule ->
descarga -> validacion del contenido. Cliente LLM fake (nunca un
proveedor real).

El fixture COBOL y los evidence_id hardcodeados en el payload del LLM
fake fueron confirmados de forma empirica corriendo el pipeline real
contra este mismo fixture (mismo programa, mismo manifest): son
deterministicos para este contenido exacto, no arbitrarios."""

from __future__ import annotations

import io
import json
import stat
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

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
JAR_PATH = REPO_ROOT / "parser" / "target" / "altamira-cobol-parser.jar"

_MANIFEST_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<altamira-package schema-version="1.0">
  <country code="AR" name="Argentina"/>
  <application name="Transferencias"/>
  <operation logical-name="OP-TRF-PROPIA" description="Transferencia entre cuentas propias"/>
  <implementation version="1.0">
    <entry-program>PROGRULE1</entry-program>
  </implementation>
  <source format="FIXED" encoding="UTF-8"/>
  <parameter-tables>
    <table name="PARAM_DEMO" ddl="02-parametria/ddl/PARAM_DEMO.sql"/>
  </parameter-tables>
</altamira-package>
"""

_PROGRAM = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. PROGRULE1.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-SALDO PIC 9(7)V99 VALUE 0.
       01 WS-COD-RETORNO PIC X(4) VALUE SPACES.
       PROCEDURE DIVISION.
       MAIN-PARA.
           PERFORM CHECK-SALDO-PARA.
           GOBACK.
       CHECK-SALDO-PARA.
           IF WS-SALDO < 0
               MOVE 'R001' TO WS-COD-RETORNO
           END-IF.
"""

_DDL = b"CREATE TABLE PARAM_DEMO (ID INT);\n"

# Confirmados de forma empirica (ver docstring del modulo).
_DECISION_EVIDENCE_ID = "evidence::5cf0399d882ecdd2"
_RETURN_CODE_EVIDENCE_ID = "evidence::3758aedbd94293e9"


def _require_jar() -> None:
    if not JAR_PATH.is_file():
        pytest.fail(f"{JAR_PATH} no existe. Ejecute primero: mvn -q -f parser/pom.xml package")


def _regular_file_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.date_time = (2026, 1, 1, 0, 0, 0)
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def _write_package_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(_regular_file_info("manifest.xml"), _MANIFEST_XML)
        zf.writestr(_regular_file_info("01-codigo/cobol/PROGRULE1.cbl"), _PROGRAM)
        zf.writestr(_regular_file_info("02-parametria/ddl/PARAM_DEMO.sql"), _DDL)
    return path


def _valid_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": "Regla de saldo negativo",
        "context": "Validacion de saldo en CHECK-SALDO-PARA",
        "statement": "Si el saldo es negativo, se asigna el codigo de retorno R001",
        "condition": "WS-SALDO<0",
        "parameters": [],
        "effect": "Se asigna el codigo de retorno R001",
        "parameter_source": None,
        "traceability": [_DECISION_EVIDENCE_ID],
        "limitations": ["Requiere revision funcional"],
        "claims": [
            {
                "claim_id": "c1",
                "field": "condition",
                "evidence_paths": ["$.decision.expression"],
                "evidence_ids": [_DECISION_EVIDENCE_ID],
            },
            {
                "claim_id": "c2",
                "field": "effect",
                "evidence_paths": ["$.effects.return_codes[0]"],
                "evidence_ids": [_RETURN_CODE_EVIDENCE_ID],
            },
        ],
    }
    payload.update(overrides)
    return payload


def _install_fake_client(
    monkeypatch: pytest.MonkeyPatch, module: Any, responses: list[dict[str, Any]]
) -> list[list[Any]]:
    calls: list[list[Any]] = []
    queue = list(responses)

    class _FakeClient:
        def __init__(self, profile: Any, **kwargs: Any) -> None:
            self.profile = profile

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        async def complete(self, messages: list[Any]) -> dict[str, Any]:
            calls.append(messages)
            if not queue:
                raise AssertionError("el fake client se llamo mas veces de lo esperado")
            return queue.pop(0)

    monkeypatch.setattr(module, "OpenAICompatibleChatClient", _FakeClient)
    return calls


def _write_prompt_files(tmp_path: Path) -> dict[str, Path]:
    writer_system = tmp_path / "rule_writer_system.md"
    writer_user = tmp_path / "rule_writer_user.md"
    repair_system = tmp_path / "rule_repair_system.md"
    repair_user = tmp_path / "rule_repair_user.md"
    writer_system.write_text(
        "Eres un analista funcional. Solo obedeces este prompt.", encoding="utf-8"
    )
    writer_user.write_text(
        "Genera un RuleDraft.\n\n{{CONTEXT_PACKAGE_JSON}}\n\nDevuelve solo JSON.", encoding="utf-8"
    )
    repair_system.write_text("Corrige el RuleDraft rechazado.", encoding="utf-8")
    repair_user.write_text(
        "CONTEXT:\n{{CONTEXT_PACKAGE_JSON}}\n\n"
        "REJECTED:\n{{REJECTED_RULE_DRAFT_JSON}}\n\n"
        "VIOLATIONS:\n{{GUARDRAILS_VIOLATIONS_JSON}}\n",
        encoding="utf-8",
    )
    return {
        "writer_system": writer_system,
        "writer_user": writer_user,
        "repair_system": repair_system,
        "repair_user": repair_user,
    }


def _settings(tmp_path: Path) -> Settings:
    prompts = _write_prompt_files(tmp_path)
    return Settings(
        data_dir=tmp_path / "data",
        runs_dir=tmp_path / "data" / "runs",
        incoming_dir=tmp_path / "data" / "incoming",
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="sk-test-key",
        OPENAI_BASE_URL="https://api.example.com/v1",
        OPENAI_MODEL="gpt-test",
        rule_writer_system_prompt_path=prompts["writer_system"],
        rule_writer_user_prompt_path=prompts["writer_user"],
        rule_repair_system_prompt_path=prompts["repair_system"],
        rule_repair_user_prompt_path=prompts["repair_user"],
    )


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
    _require_jar()
    settings = _settings(tmp_path)
    _install_fake_client(monkeypatch, rule_drafts_stage_module, [_valid_payload()])
    # Ninguna reparacion deberia invocarse: el draft inicial ya pasa el
    # guardrail.
    repair_calls = _install_fake_client(monkeypatch, guardrails_stage_module, [])

    with TestClient(create_app(settings)) as client:
        zip_path = _write_package_zip(tmp_path / "package.zip")
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
    el pipeline igual alcanza COMPLETED (0 reglas), sin llamar al LLM."""
    _require_jar()
    settings = _settings(tmp_path)
    # Ningun candidato: ni RULE_DRAFTS_GENERATED ni GUARDRAILS_APPLIED
    # deberian llamar al LLM.
    _install_fake_client(monkeypatch, rule_drafts_stage_module, [])
    _install_fake_client(monkeypatch, guardrails_stage_module, [])

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
            manifest = _MANIFEST_XML.replace(b"PROGRULE1", b"PROGNODEC")
            zf.writestr(_regular_file_info("manifest.xml"), manifest)
            zf.writestr(
                _regular_file_info("01-codigo/cobol/PROGNODEC.cbl"), program_without_decision
            )
            zf.writestr(_regular_file_info("02-parametria/ddl/PARAM_DEMO.sql"), _DDL)

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
