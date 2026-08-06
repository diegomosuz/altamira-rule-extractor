"""Tests de instrumentacion del `RunExecutor` (Fase 15B2-B, Seccion
13): gauge de runs activos, contador de rechazos por capacidad, y
registro post-hoc del `RunState` final -- sin tocar `pipeline/runner.py`."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

import pytest
from prometheus_client import generate_latest

from altamira_extractor.api.executor import RunExecutor
from altamira_extractor.contracts.enums import PipelineStage, StageStatus
from altamira_extractor.contracts.run_state import RunState, StageExecution
from altamira_extractor.observability.metrics import ObservabilityRegistry


def _wait_until(predicate: object, *, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():  # type: ignore[operator]
            return True
        time.sleep(0.02)
    return False


def _render(registry: ObservabilityRegistry) -> str:
    return generate_latest(registry.registry).decode("utf-8")


def _fake_run_state(stage: PipelineStage) -> RunState:
    now = datetime.now(UTC)
    return RunState(
        run_id="20260101T000000000000-cccccccc",
        package_filename="input/package.zip",
        source_package_hash="a" * 64,
        current_stage=stage,
        stages=[
            StageExecution(
                stage=PipelineStage.RECEIVED,
                status=StageStatus.SUCCEEDED,
                started_at=now,
                finished_at=now,
                duration_seconds=0.1,
            )
        ],
        created_at=now,
        updated_at=now,
    )


def test_executor_without_metrics_still_works() -> None:
    """`metrics=None` (default) es el comportamiento pre-existente --
    no debe cambiar."""
    executor = RunExecutor(max_workers=1)
    try:
        assert executor.try_submit("run-a", lambda: _fake_run_state(PipelineStage.COMPLETED)) == (
            "submitted"
        )
        assert _wait_until(lambda: not executor.is_active("run-a"))
    finally:
        executor.shutdown(wait=True)


def test_capacity_rejection_increments_counter() -> None:
    registry = ObservabilityRegistry(enabled=True)
    executor = RunExecutor(max_workers=1, metrics=registry)
    block = threading.Event()
    try:
        assert executor.try_submit("run-a", lambda: block.wait(timeout=5)) == "submitted"
        assert _wait_until(lambda: executor.is_active("run-a"))
        assert executor.try_submit("run-b", lambda: None) == "at_capacity"
        output = _render(registry)
        assert "altamira_executor_capacity_rejections_total 1.0" in output
    finally:
        block.set()
        executor.shutdown(wait=True)


def test_active_runs_gauge_tracks_submit_and_completion() -> None:
    registry = ObservabilityRegistry(enabled=True)
    executor = RunExecutor(max_workers=2, metrics=registry)
    block = threading.Event()
    try:
        executor.try_submit("run-a", lambda: block.wait(timeout=5))
        assert _wait_until(lambda: "altamira_executor_active_runs 1.0" in _render(registry))
        block.set()
        assert _wait_until(lambda: "altamira_executor_active_runs 0.0" in _render(registry))
    finally:
        block.set()
        executor.shutdown(wait=True)


def test_pipeline_run_recorded_post_hoc_from_returned_run_state() -> None:
    registry = ObservabilityRegistry(enabled=True)
    executor = RunExecutor(max_workers=1, metrics=registry)
    try:
        executor.try_submit("run-a", lambda: _fake_run_state(PipelineStage.COMPLETED))
        assert _wait_until(lambda: 'final_stage="COMPLETED"' in _render(registry))
        output = _render(registry)
        assert 'stage="RECEIVED",status="SUCCEEDED"' in output.replace(" ", "")
    finally:
        executor.shutdown(wait=True)


def test_non_run_state_return_value_does_not_break_metrics() -> None:
    """Un `fn` que no devuelve `RunState` (p. ej. el fake de los tests
    de capacidad/concurrencia existentes) nunca debe romper la
    instrumentacion -- simplemente no se registra nada de pipeline."""
    registry = ObservabilityRegistry(enabled=True)
    executor = RunExecutor(max_workers=1, metrics=registry)
    finished = threading.Event()
    try:
        executor.try_submit("run-a", lambda: finished.set())
        assert _wait_until(finished.is_set)
        assert _wait_until(lambda: not executor.is_active("run-a"))
    finally:
        executor.shutdown(wait=True)


@pytest.mark.parametrize("final_stage", [PipelineStage.COMPLETED, PipelineStage.FAILED])
def test_pipeline_runs_total_counts_both_terminal_stages(final_stage: PipelineStage) -> None:
    registry = ObservabilityRegistry(enabled=True)
    executor = RunExecutor(max_workers=1, metrics=registry)
    try:
        executor.try_submit("run-a", lambda: _fake_run_state(final_stage))
        assert _wait_until(lambda: f'final_stage="{final_stage.value}"' in _render(registry))
    finally:
        executor.shutdown(wait=True)
