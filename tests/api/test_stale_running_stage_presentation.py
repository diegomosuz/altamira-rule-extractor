"""CHECK 2 del pre-commit review v1.17.1: una StageExecution RUNNING
persistida (posible tras un reinicio del proceso a mitad de una etapa,
ver `runner.py::_mark_running` -- nunca actualiza `current_stage`) NUNCA
debe presentarse como "en ejecucion ahora mismo" cuando `RunExecutor` (la
unica fuente autoritativa de ownership activo, en memoria del proceso)
ya no posee ese `run_id`. Verificado a nivel HTTP real, no solo
`compute_pipeline_progress` en aislamiento (ver
`tests/ui/test_pipeline_progress.py` para esas pruebas puras)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from altamira_extractor.config import Settings
from altamira_extractor.contracts.enums import PipelineStage, StageStatus

from .conftest import (
    RUN_ID,
    build_run_state,
    stage_execution,
    write_input_package_zip,
    write_run_state,
)


def _write_run_with_stale_running_stage(settings: Settings) -> None:
    """Simula el escenario exacto del CHECK 2: el proceso se reinicio a
    mitad de EXTRACTED (persistido RUNNING) sin que ningun worker en
    memoria lo posea -- `client` (fixture) arranca un `RunExecutor`
    fresco vacio, exactamente como un reinicio real del proceso."""
    run_dir = settings.runs_dir / RUN_ID
    stages = [
        stage_execution(PipelineStage.RECEIVED, StageStatus.SUCCEEDED),
        stage_execution(PipelineStage.VALIDATED, StageStatus.SUCCEEDED),
        stage_execution(
            PipelineStage.EXTRACTED, StageStatus.RUNNING, started_at=datetime.now(UTC)
        ),
    ]
    state = build_run_state(RUN_ID, stages=stages, current_stage=PipelineStage.VALIDATED)
    write_run_state(run_dir, state)
    write_input_package_zip(run_dir)


def test_stale_running_stage_never_shown_as_actively_running(
    client: TestClient, settings: Settings
) -> None:
    _write_run_with_stale_running_stage(settings)

    response = client.get(f"/ui/runs/{RUN_ID}")
    assert response.status_code == 200
    assert "Interrumpida" in response.text
    # nunca implica trabajo activo real: la barra indeterminada nativa
    # (<progress> sin value=) solo debe aparecer para RUNNING realmente
    # poseido por el executor -- esta pagina no debe contenerla para la
    # etapa obsoleta.
    assert "Reanudar" in response.text


def test_stale_running_stage_does_not_block_resume(
    client: TestClient, settings: Settings
) -> None:
    _write_run_with_stale_running_stage(settings)

    response = client.post(
        f"/ui/runs/{RUN_ID}/resume",
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    # 303 (aceptado) o al menos nunca 409 run_active: RunExecutor esta
    # vacio tras "reiniciar" (fixture fresca), la etapa RUNNING obsoleta
    # nunca bloquea Reanudar.
    assert response.status_code in (303, 200)


def test_stale_running_stage_does_not_block_clean_job(
    client: TestClient, settings: Settings
) -> None:
    _write_run_with_stale_running_stage(settings)
    run_dir = settings.runs_dir / RUN_ID

    response = client.post(
        f"/ui/runs/{RUN_ID}/clean",
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert not run_dir.exists()
