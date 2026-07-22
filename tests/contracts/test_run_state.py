"""Tests de RunState y StageExecution (maquina de estados del pipeline)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts import PipelineStage, RunState, StageExecution, StageStatus

NOW = datetime(2026, 7, 22, 10, 0, 0, tzinfo=UTC)
LATER = datetime(2026, 7, 22, 10, 0, 5, tzinfo=UTC)


def test_pending_stage_has_no_timestamps() -> None:
    stage = StageExecution(stage=PipelineStage.RECEIVED, status=StageStatus.PENDING)
    assert stage.started_at is None


def test_running_stage_requires_started_at() -> None:
    with pytest.raises(ValidationError):
        StageExecution(stage=PipelineStage.RECEIVED, status=StageStatus.RUNNING)


def test_succeeded_stage_requires_finished_at() -> None:
    with pytest.raises(ValidationError):
        StageExecution(stage=PipelineStage.RECEIVED, status=StageStatus.SUCCEEDED, started_at=NOW)


def test_succeeded_stage_cannot_have_error() -> None:
    with pytest.raises(ValidationError):
        StageExecution(
            stage=PipelineStage.RECEIVED,
            status=StageStatus.SUCCEEDED,
            started_at=NOW,
            finished_at=LATER,
            error="boom",
        )


def test_failed_stage_requires_error() -> None:
    with pytest.raises(ValidationError):
        StageExecution(
            stage=PipelineStage.RECEIVED,
            status=StageStatus.FAILED,
            started_at=NOW,
            finished_at=LATER,
        )


def test_failed_stage_with_error_is_valid() -> None:
    stage = StageExecution(
        stage=PipelineStage.RECEIVED,
        status=StageStatus.FAILED,
        started_at=NOW,
        finished_at=LATER,
        error="zip invalido",
    )
    assert stage.error == "zip invalido"


def test_finished_before_started_is_rejected() -> None:
    with pytest.raises(ValidationError):
        StageExecution(
            stage=PipelineStage.RECEIVED,
            status=StageStatus.SUCCEEDED,
            started_at=LATER,
            finished_at=NOW,
        )


def test_run_state_rejects_invalid_stage_enum() -> None:
    with pytest.raises(ValidationError):
        RunState.model_validate(
            {
                "schema_version": "1.0",
                "run_id": "run-1",
                "package_filename": "incoming/pkg.zip",
                "current_stage": "NOT_A_REAL_STAGE",
                "stages": [],
                "created_at": NOW.isoformat(),
                "updated_at": NOW.isoformat(),
            }
        )


def test_run_state_rejects_absolute_package_filename() -> None:
    with pytest.raises(ValidationError):
        RunState(
            run_id="run-1",
            package_filename="/data/incoming/pkg.zip",
            current_stage=PipelineStage.RECEIVED,
            created_at=NOW,
            updated_at=NOW,
        )


def test_run_state_round_trips() -> None:
    state = RunState(
        run_id="run-1",
        package_filename="incoming/pkg.zip",
        source_package_hash="a" * 64,
        current_stage=PipelineStage.INVENTORIED,
        stages=[
            StageExecution(
                stage=PipelineStage.RECEIVED,
                status=StageStatus.SUCCEEDED,
                started_at=NOW,
                finished_at=LATER,
                duration_seconds=5.0,
            ),
        ],
        created_at=NOW,
        updated_at=LATER,
    )
    restored = RunState.model_validate_json(state.to_stable_json())
    assert restored == state
