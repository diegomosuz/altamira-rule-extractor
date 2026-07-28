"""Pantallas candidatos, contexto, regla y guardrail (Prompt 13d,
secciones 15-18): campos exactos, minimizacion, y ausencia de
provenance interna."""

from __future__ import annotations

from fastapi.testclient import TestClient

from altamira_extractor.config import Settings
from altamira_extractor.contracts.enums import PipelineStage, StageStatus

from ..api.conftest import (
    CANDIDATE_ID,
    RUN_ID,
    build_run_state,
    build_run_up_to_candidates_detected,
    build_run_up_to_contexts_built,
    build_run_up_to_guardrails_applied,
    stage_execution,
    write_candidates_artifact,
    write_input_package_zip,
    write_run_state,
)


def test_candidates_empty_message(client: TestClient, settings: Settings) -> None:
    run_dir = settings.runs_dir / RUN_ID
    stages = [
        stage_execution(PipelineStage.RECEIVED, StageStatus.SUCCEEDED),
        stage_execution(PipelineStage.CANDIDATES_DETECTED, StageStatus.SUCCEEDED),
    ]
    state = build_run_state(
        RUN_ID, stages=stages, current_stage=PipelineStage.CANDIDATES_DETECTED
    )
    write_run_state(run_dir, state)

    write_input_package_zip(run_dir)
    write_candidates_artifact(run_dir, [])

    response = client.get(f"/ui/runs/{RUN_ID}/candidates")
    assert response.status_code == 200
    assert "No hay candidatos detectados." in response.text
    assert "<table" not in response.text


def test_candidates_stage_not_reached_is_409(client: TestClient, settings: Settings) -> None:
    run_dir = settings.runs_dir / RUN_ID
    stages = [stage_execution(PipelineStage.RECEIVED, StageStatus.SUCCEEDED)]
    state = build_run_state(RUN_ID, stages=stages, current_stage=PipelineStage.RECEIVED)
    write_run_state(run_dir, state)

    write_input_package_zip(run_dir)

    response = client.get(f"/ui/runs/{RUN_ID}/candidates")
    assert response.status_code == 409


def test_candidates_links_hidden_until_stages_ready(
    client: TestClient, settings: Settings
) -> None:
    build_run_up_to_candidates_detected(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.get(f"/ui/runs/{RUN_ID}/candidates")
    assert response.status_code == 200
    assert ">Contexto<" not in response.text
    assert ">Regla<" not in response.text
    assert ">Guardrail<" not in response.text


def test_candidates_context_link_shown_once_contexts_built(
    client: TestClient, settings: Settings
) -> None:
    build_run_up_to_contexts_built(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.get(f"/ui/runs/{RUN_ID}/candidates")
    assert ">Contexto<" in response.text
    assert ">Regla<" not in response.text


def test_candidates_shows_only_the_six_documented_fields(
    client: TestClient, settings: Settings
) -> None:
    build_run_up_to_candidates_detected(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.get(f"/ui/runs/{RUN_ID}/candidates")
    assert CANDIDATE_ID in response.text
    assert "MAIN" in response.text
    assert "WS-COD = &#39;R001&#39;" in response.text or "WS-COD = 'R001'" in response.text
    assert "R001" in response.text
    assert "DETECTED_CANDIDATE" in response.text
    # nunca provenance tecnica del detector.
    assert "detector_id" not in response.text
    assert "detector_score" not in response.text
    assert "line_start" not in response.text


def test_context_candidate_id_with_double_colon(client: TestClient, settings: Settings) -> None:
    build_run_up_to_contexts_built(settings.runs_dir / RUN_ID, RUN_ID)
    assert "::" in CANDIDATE_ID
    response = client.get(f"/ui/runs/{RUN_ID}/candidates/{CANDIDATE_ID}/context")
    assert response.status_code == 200
    assert CANDIDATE_ID in response.text


def test_context_shows_structured_dimensions(client: TestClient, settings: Settings) -> None:
    build_run_up_to_contexts_built(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.get(f"/ui/runs/{RUN_ID}/candidates/{CANDIDATE_ID}/context")
    assert response.status_code == 200
    for heading in (
        "Alcance (D1)",
        "Code slice (D2)",
        "Contexto parametrico (D3)",
        "Decision (D4)",
        "Efectos (D5)",
        "Batch (D6)",
        "Glosario funcional (D7)",
        "Completitud",
    ):
        assert heading in response.text


def test_context_never_dumps_full_json(client: TestClient, settings: Settings) -> None:
    build_run_up_to_contexts_built(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.get(f"/ui/runs/{RUN_ID}/candidates/{CANDIDATE_ID}/context")
    # schema_version es un campo de nivel raiz de ContextPackage que solo
    # aparece en un model_dump_json completo -- nunca se renderiza por
    # dimension.
    assert '"schema_version"' not in response.text
    assert "model_dump_json" not in response.text


def test_context_stage_not_reached_is_409(client: TestClient, settings: Settings) -> None:
    build_run_up_to_candidates_detected(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.get(f"/ui/runs/{RUN_ID}/candidates/{CANDIDATE_ID}/context")
    assert response.status_code == 409


def test_rule_shows_professional_draft_notice_and_claims_in_order(
    client: TestClient, settings: Settings
) -> None:
    """Modernizacion UI: el aviso de borrador ya no usa el texto alarmista
    de prototipo ("Este documento es un borrador generado
    automaticamente y requiere revision funcional.") -- se reemplaza por
    una redaccion profesional equivalente, y el estado
    NEEDS_FUNCTIONAL_REVIEW se sigue mostrando tal cual (dato
    contractual), nunca oculto."""
    build_run_up_to_guardrails_applied(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.get(f"/ui/runs/{RUN_ID}/candidates/{CANDIDATE_ID}/rule")
    assert response.status_code == 200
    assert (
        "Este documento es un borrador generado automáticamente y requiere revisión "
        "funcional." not in response.text
    )
    assert "revisión funcional" in response.text or "revision funcional" in response.text
    assert "EVIDENCE_VALIDATED" in response.text
    assert "NEEDS_FUNCTIONAL_REVIEW" in response.text
    assert "c1" in response.text


def test_rule_never_reads_08_rule_drafts_directory(
    client: TestClient, settings: Settings
) -> None:
    run_dir = settings.runs_dir / RUN_ID
    build_run_up_to_guardrails_applied(run_dir, RUN_ID)
    assert not (run_dir / "artifacts" / "08-rule-drafts").exists()
    response = client.get(f"/ui/runs/{RUN_ID}/candidates/{CANDIDATE_ID}/rule")
    assert response.status_code == 200


def test_rule_never_shows_markdown_or_provenance(
    client: TestClient, settings: Settings
) -> None:
    build_run_up_to_guardrails_applied(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.get(f"/ui/runs/{RUN_ID}/candidates/{CANDIDATE_ID}/rule")
    for forbidden in (
        "repair_history",
        "response_hash",
        "produced_rule_draft_hash",
        "provider",
        "context_hash",
        "initial_rule_draft_hash",
        "final_rule_draft_hash",
        "source_package_hash",
        "# Titulo",  # marca de Markdown renderizado (## encabezados MD)
    ):
        assert forbidden not in response.text


def test_guardrail_shows_verdict_violations_warnings_and_repair_count(
    client: TestClient, settings: Settings
) -> None:
    build_run_up_to_guardrails_applied(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.get(f"/ui/runs/{RUN_ID}/candidates/{CANDIDATE_ID}/guardrail")
    assert response.status_code == 200
    assert "EVIDENCE_VALIDATED" in response.text
    assert "Reparaciones utilizadas: 0" in response.text
    assert "Sin violaciones." in response.text


def test_guardrail_never_exposes_internal_fields(client: TestClient, settings: Settings) -> None:
    build_run_up_to_guardrails_applied(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.get(f"/ui/runs/{RUN_ID}/candidates/{CANDIDATE_ID}/guardrail")
    for forbidden in ("repair_history", "response_hash", "provider", "context_hash"):
        assert forbidden not in response.text


def test_guardrail_page_has_no_form_or_mutating_action(
    client: TestClient, settings: Settings
) -> None:
    build_run_up_to_guardrails_applied(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.get(f"/ui/runs/{RUN_ID}/candidates/{CANDIDATE_ID}/guardrail")
    assert "<form" not in response.text
