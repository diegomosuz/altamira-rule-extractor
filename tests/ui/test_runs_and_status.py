"""Pantallas runs y estado (Prompt 13d, secciones 10-11), incluyendo
polling del fragmento de estado."""

from __future__ import annotations

from fastapi.testclient import TestClient

from altamira_extractor.config import Settings
from altamira_extractor.contracts.enums import PipelineStage, StageStatus

from ..api.conftest import (
    RUN_ID,
    build_run_completed,
    build_run_state,
    build_run_up_to_contexts_built,
    stage_execution,
    write_run_state,
)


def test_runs_empty_shows_clear_message(client: TestClient) -> None:
    response = client.get("/ui/runs")
    assert response.status_code == 200
    assert "No hay ejecuciones registradas." in response.text


def test_runs_listed_descending_with_links(client: TestClient, settings: Settings) -> None:
    for run_id in ("20260101T000000000000-aaaaaaaa", "20260201T000000000000-bbbbbbbb"):
        state = build_run_state(
            run_id,
            stages=[stage_execution(PipelineStage.RECEIVED, StageStatus.SUCCEEDED)],
            current_stage=PipelineStage.RECEIVED,
        )
        write_run_state(settings.runs_dir / run_id, state)

    response = client.get("/ui/runs")
    assert response.status_code == 200
    first_index = response.text.index("bbbbbbbb")
    second_index = response.text.index("aaaaaaaa")
    assert first_index < second_index
    assert "/ui/runs/20260201T000000000000-bbbbbbbb" in response.text


def test_runs_pagination_limit_and_offset(client: TestClient, settings: Settings) -> None:
    run_id_a = "20260101T000000000000-aaaaaaaa"
    run_id_b = "20260201T000000000000-bbbbbbbb"
    for run_id in (run_id_a, run_id_b):
        state = build_run_state(
            run_id,
            stages=[stage_execution(PipelineStage.RECEIVED, StageStatus.SUCCEEDED)],
            current_stage=PipelineStage.RECEIVED,
        )
        write_run_state(settings.runs_dir / run_id, state)

    # source_package_hash es el mismo HASH_A ("a" * 64) para ambos runs
    # en este builder: se compara el run_id COMPLETO, nunca un
    # fragmento, para no confundirlo con ese hash compartido.
    response = client.get("/ui/runs", params={"limit": 1, "offset": 0})
    assert run_id_b in response.text
    assert run_id_a not in response.text
    assert "Siguiente" in response.text
    assert "Anterior" not in response.text

    response2 = client.get("/ui/runs", params={"limit": 1, "offset": 1})
    assert run_id_a in response2.text
    assert run_id_b not in response2.text
    assert "Anterior" in response2.text


def test_runs_limit_out_of_range_is_usage_error(client: TestClient) -> None:
    assert client.get("/ui/runs", params={"limit": 0}).status_code == 422
    assert client.get("/ui/runs", params={"limit": 101}).status_code == 422


def test_runs_skips_corrupt_run(client: TestClient, settings: Settings) -> None:
    good_id = "20260101T000000000000-aaaaaaaa"
    state = build_run_state(
        good_id,
        stages=[stage_execution(PipelineStage.RECEIVED, StageStatus.SUCCEEDED)],
        current_stage=PipelineStage.RECEIVED,
    )
    write_run_state(settings.runs_dir / good_id, state)

    corrupt_dir = settings.runs_dir / "20260102T000000000000-cccccccc"
    corrupt_dir.mkdir(parents=True)
    (corrupt_dir / "run.json").write_text("not json", encoding="utf-8")

    response = client.get("/ui/runs")
    assert response.status_code == 200
    assert good_id in response.text
    assert "cccccccc" not in response.text


def test_run_status_shows_required_fields(client: TestClient, settings: Settings) -> None:
    build_run_up_to_contexts_built(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.get(f"/ui/runs/{RUN_ID}")
    assert response.status_code == 200
    assert RUN_ID in response.text
    assert "CONTEXTS_BUILT" in response.text
    assert "input/package.zip" in response.text
    assert "RECEIVED" in response.text
    assert "CANDIDATES_DETECTED" in response.text


def test_run_status_nonexistent_run_is_404(client: TestClient) -> None:
    response = client.get("/ui/runs/20260101T000000000000-ffffffff")
    assert response.status_code == 404


def test_status_fragment_includes_polling_attributes_while_not_terminal(
    client: TestClient, settings: Settings
) -> None:
    build_run_up_to_contexts_built(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.get(f"/ui/runs/{RUN_ID}/status-fragment")
    assert response.status_code == 200
    assert "hx-get" in response.text
    assert "hx-trigger" in response.text
    assert "every 3s" in response.text


def test_status_fragment_omits_polling_attributes_when_completed(
    client: TestClient, settings: Settings
) -> None:
    build_run_completed(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.get(f"/ui/runs/{RUN_ID}/status-fragment")
    assert response.status_code == 200
    assert "hx-get" not in response.text
    assert "hx-trigger" not in response.text


def test_status_fragment_omits_polling_attributes_when_failed(
    client: TestClient, settings: Settings
) -> None:
    run_dir = settings.runs_dir / RUN_ID
    stages = [
        stage_execution(PipelineStage.RECEIVED, StageStatus.SUCCEEDED),
        stage_execution(PipelineStage.VALIDATED, StageStatus.FAILED),
    ]
    state = build_run_state(RUN_ID, stages=stages, current_stage=PipelineStage.FAILED)
    write_run_state(run_dir, state)

    response = client.get(f"/ui/runs/{RUN_ID}/status-fragment")
    assert response.status_code == 200
    assert "hx-get" not in response.text
    assert "hx-trigger" not in response.text


def test_status_fragment_endpoint_never_submits_or_resumes(
    client: TestClient, settings: Settings
) -> None:
    build_run_up_to_contexts_built(settings.runs_dir / RUN_ID, RUN_ID)
    before = client.get(f"/ui/runs/{RUN_ID}").text
    # GET puro repetido: no debe cambiar el estado persistido.
    client.get(f"/ui/runs/{RUN_ID}/status-fragment")
    client.get(f"/ui/runs/{RUN_ID}/status-fragment")
    after = client.get(f"/ui/runs/{RUN_ID}").text
    assert before == after


def test_run_status_page_includes_status_fragment_container(
    client: TestClient, settings: Settings
) -> None:
    build_run_up_to_contexts_built(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.get(f"/ui/runs/{RUN_ID}")
    assert 'id="status-fragment"' in response.text
    assert "hx-get" in response.text  # no terminal: sigue incluyendo polling
