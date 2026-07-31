"""Tests del defecto real de estabilizacion de baseline: persistencia
critica de `run.json` (Fase 4/5/6 del checkpoint correctivo).

Cubre, a nivel de unidad (sin `Settings`, sin ZIP, sin `run_ingestion`
completo -- eso ya lo cubre `test_runner.py`): la funcion centralizada
`pipeline.runner._persist_run_state` (unico punto de escritura critica
de `run.json`), el artefacto durable de emergencia
(`contracts.run_state_recovery`), el fallback de precedencia en
`api.reads.read_run_state`, y la observabilidad diferenciada de
`api.executor.RunExecutor` ante `RunStatePersistenceError`."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from altamira_extractor.api import run_actions
from altamira_extractor.api.executor import RunExecutor
from altamira_extractor.api.reads import read_run_state
from altamira_extractor.config import Settings
from altamira_extractor.contracts.enums import PipelineStage, StageStatus
from altamira_extractor.contracts.run_state import RunState, StageExecution
from altamira_extractor.contracts.run_state_recovery import RunStatePersistenceFailureRecord
from altamira_extractor.pipeline import runner as runner_module
from altamira_extractor.pipeline.artifact_store import atomic_write_json
from altamira_extractor.pipeline.errors import AtomicWriteError, RunStatePersistenceError


def _state(
    *,
    run_id: str = "run-1",
    current_stage: PipelineStage = PipelineStage.VALIDATED,
    updated_at: datetime | None = None,
) -> RunState:
    now = updated_at or datetime.now(UTC)
    return RunState(
        run_id=run_id,
        package_filename="input/package.zip",
        current_stage=current_stage,
        stages=[
            StageExecution(
                stage=PipelineStage.VALIDATED,
                status=StageStatus.SUCCEEDED,
                started_at=now,
                finished_at=now,
                duration_seconds=0.0,
            )
        ],
        created_at=now,
        updated_at=now,
    )


def _always_fails_atomic_write(target_name: str) -> Any:
    """`side_effect` para `runner.atomic_write_json`: falla UNICAMENTE
    para el archivo cuyo nombre coincide con `target_name`, delega en la
    implementacion real para cualquier otro (p. ej. el propio artefacto
    de emergencia, cuando el test necesita que SI se escriba)."""

    def _side_effect(path: Path, model: Any, *, max_wait_seconds: float | None = None) -> None:
        if Path(path).name == target_name:
            raise AtomicWriteError(f"simulado: agotado el deadline para {target_name!r}")
        if max_wait_seconds is None:
            atomic_write_json(path, model)
        else:
            atomic_write_json(path, model, max_wait_seconds=max_wait_seconds)

    return _side_effect


# ---------------------------------------------------------------------------
# _persist_run_state: comportamiento normal
# ---------------------------------------------------------------------------


def test_persist_run_state_normal_failed_transition_succeeds(tmp_path: Path) -> None:
    run_json_path = tmp_path / "run.json"
    state = _state(current_stage=PipelineStage.FAILED)

    result = runner_module._persist_run_state(
        state, run_json_path, stage=PipelineStage.EXTRACTED, transition="FAILED"
    )

    assert result is state
    persisted = RunState.model_validate_json(run_json_path.read_text(encoding="utf-8"))
    assert persisted.current_stage == PipelineStage.FAILED


def test_persist_run_state_normal_succeeded_transition_succeeds(tmp_path: Path) -> None:
    run_json_path = tmp_path / "run.json"
    state = _state(current_stage=PipelineStage.EXTRACTED)

    result = runner_module._persist_run_state(
        state, run_json_path, stage=PipelineStage.EXTRACTED, transition="SUCCEEDED"
    )

    assert result is state
    persisted = RunState.model_validate_json(run_json_path.read_text(encoding="utf-8"))
    assert persisted.current_stage == PipelineStage.EXTRACTED


def test_persist_run_state_normal_completed_transition_succeeds(tmp_path: Path) -> None:
    run_json_path = tmp_path / "run.json"
    state = _state(current_stage=PipelineStage.COMPLETED)

    result = runner_module._persist_run_state(
        state, run_json_path, stage=PipelineStage.COMPLETED, transition="SUCCEEDED"
    )

    assert result is state
    persisted = RunState.model_validate_json(run_json_path.read_text(encoding="utf-8"))
    assert persisted.current_stage == PipelineStage.COMPLETED


def test_persist_run_state_transient_atomic_write_error_recovers_without_emergency_artifact(
    tmp_path: Path,
) -> None:
    """Un `AtomicWriteError` que en realidad NO ocurre (el reemplazo
    normal de `atomic_write_json` ya absorbe la contencion transitoria,
    ver test_artifact_store.py) nunca llega a `_persist_run_state`: este
    test confirma que el camino feliz no crea NINGUN artefacto de
    emergencia."""
    run_json_path = tmp_path / "run.json"
    state = _state(current_stage=PipelineStage.EXTRACTED)

    runner_module._persist_run_state(
        state, run_json_path, stage=PipelineStage.EXTRACTED, transition="SUCCEEDED"
    )

    assert list(tmp_path.glob("run-state-persistence-failure-*.json")) == []


# ---------------------------------------------------------------------------
# Agotamiento del deadline critico: artefacto de emergencia
# ---------------------------------------------------------------------------


def test_persist_run_state_exhaustion_writes_emergency_artifact_and_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_json_path = tmp_path / "run.json"
    atomic_write_json(run_json_path, _state(current_stage=PipelineStage.VALIDATED))
    state = _state(current_stage=PipelineStage.EXTRACTED)

    monkeypatch.setattr(
        runner_module, "atomic_write_json", _always_fails_atomic_write("run.json")
    )

    with pytest.raises(RunStatePersistenceError) as excinfo:
        runner_module._persist_run_state(
            state, run_json_path, stage=PipelineStage.EXTRACTED, transition="SUCCEEDED"
        )

    exc = excinfo.value
    assert exc.run_id == state.run_id
    assert exc.stage == PipelineStage.EXTRACTED
    assert exc.transition == "SUCCEEDED"
    assert exc.emergency_artifact_path is not None
    assert exc.emergency_artifact_path.is_file()

    # run.json en si NUNCA se toco: sigue mostrando el ultimo estado
    # persistido con exito, nunca un contenido parcial o inventado.
    on_disk = RunState.model_validate_json(run_json_path.read_text(encoding="utf-8"))
    assert on_disk.current_stage == PipelineStage.VALIDATED


def test_mark_failed_exhaustion_writes_emergency_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_json_path = tmp_path / "run.json"
    atomic_write_json(run_json_path, _state(current_stage=PipelineStage.VALIDATED))
    state = _state(current_stage=PipelineStage.VALIDATED)

    monkeypatch.setattr(
        runner_module, "atomic_write_json", _always_fails_atomic_write("run.json")
    )

    with pytest.raises(RunStatePersistenceError) as excinfo:
        runner_module._mark_failed(
            state, run_json_path, PipelineStage.EXTRACTED, datetime.now(UTC), "fallo simulado"
        )

    assert excinfo.value.transition == "FAILED"
    assert excinfo.value.emergency_artifact_path is not None


def test_mark_succeeded_exhaustion_writes_emergency_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_json_path = tmp_path / "run.json"
    atomic_write_json(run_json_path, _state(current_stage=PipelineStage.VALIDATED))
    state = _state(current_stage=PipelineStage.VALIDATED)

    monkeypatch.setattr(
        runner_module, "atomic_write_json", _always_fails_atomic_write("run.json")
    )

    with pytest.raises(RunStatePersistenceError) as excinfo:
        runner_module._mark_succeeded(
            state, run_json_path, PipelineStage.EXTRACTED, datetime.now(UTC)
        )

    assert excinfo.value.transition == "SUCCEEDED"
    assert excinfo.value.emergency_artifact_path is not None


def test_persist_run_state_double_failure_raises_with_no_emergency_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Si TAMBIEN falla la escritura del artefacto de emergencia,
    `emergency_artifact_path` debe quedar en `None` -- nunca se afirma
    una evidencia durable que en realidad no existe."""
    run_json_path = tmp_path / "run.json"
    atomic_write_json(run_json_path, _state(current_stage=PipelineStage.VALIDATED))
    state = _state(current_stage=PipelineStage.EXTRACTED)

    def _always_fails(path: Path, model: Any, *, max_wait_seconds: float | None = None) -> None:
        raise AtomicWriteError("simulado: falla TODA escritura, incluida la de emergencia")

    monkeypatch.setattr(runner_module, "atomic_write_json", _always_fails)

    with pytest.raises(RunStatePersistenceError) as excinfo:
        runner_module._persist_run_state(
            state, run_json_path, stage=PipelineStage.EXTRACTED, transition="SUCCEEDED"
        )

    assert excinfo.value.emergency_artifact_path is None
    # Ningun artefacto de emergencia quedo a medias en disco.
    assert list(tmp_path.glob("run-state-persistence-failure-*.json")) == []


def test_emergency_artifact_never_shows_succeeded_even_if_that_was_the_attempted_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Objetivo 6: si no puede persistirse un SUCCEEDED, la vista de
    emergencia debe presentarse igual como FAILED -- nunca se afirma un
    exito que no quedo durable."""
    run_json_path = tmp_path / "run.json"
    atomic_write_json(run_json_path, _state(current_stage=PipelineStage.VALIDATED))
    state = _state(current_stage=PipelineStage.EXTRACTED)

    monkeypatch.setattr(
        runner_module, "atomic_write_json", _always_fails_atomic_write("run.json")
    )

    with pytest.raises(RunStatePersistenceError) as excinfo:
        runner_module._persist_run_state(
            state, run_json_path, stage=PipelineStage.EXTRACTED, transition="SUCCEEDED"
        )

    record = RunStatePersistenceFailureRecord.model_validate_json(
        excinfo.value.emergency_artifact_path.read_text(encoding="utf-8")  # type: ignore[union-attr]
    )
    assert record.transition == "SUCCEEDED"  # registra QUE se intentaba, para diagnostico
    assert record.attempted_state.current_stage == PipelineStage.FAILED  # pero se presenta FAILED
    failed_execution = next(
        s for s in record.attempted_state.stages if s.stage == PipelineStage.EXTRACTED
    )
    assert failed_execution.status == StageStatus.FAILED


def test_emergency_artifact_content_never_contains_paths_or_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_json_path = tmp_path / "run.json"
    atomic_write_json(run_json_path, _state(current_stage=PipelineStage.VALIDATED))
    state = _state(current_stage=PipelineStage.EXTRACTED)

    monkeypatch.setattr(
        runner_module, "atomic_write_json", _always_fails_atomic_write("run.json")
    )

    with pytest.raises(RunStatePersistenceError) as excinfo:
        runner_module._persist_run_state(
            state, run_json_path, stage=PipelineStage.EXTRACTED, transition="SUCCEEDED"
        )

    raw_text = excinfo.value.emergency_artifact_path.read_text(encoding="utf-8")  # type: ignore[union-attr]
    assert str(tmp_path) not in raw_text
    assert "run.json" not in raw_text or "no se pudo persistir run.json" in raw_text
    assert "api_key" not in raw_text.lower()
    assert "password" not in raw_text.lower()
    assert "authorization" not in raw_text.lower()


# ---------------------------------------------------------------------------
# Limpieza best-effort de artefactos de emergencia obsoletos
# ---------------------------------------------------------------------------


def test_successful_terminal_persist_cleans_up_stale_emergency_records(
    tmp_path: Path,
) -> None:
    run_json_path = tmp_path / "run.json"
    state = _state(current_stage=PipelineStage.VALIDATED)
    atomic_write_json(run_json_path, state)

    stale_path = runner_module._write_emergency_record(
        tmp_path,
        state,
        stage=PipelineStage.EXTRACTED,
        transition="SUCCEEDED",
        cause=RuntimeError("causa simulada obsoleta"),
    )
    assert stale_path.is_file()

    completed_state = _state(current_stage=PipelineStage.COMPLETED)
    runner_module._persist_run_state(
        completed_state, run_json_path, stage=PipelineStage.COMPLETED, transition="SUCCEEDED"
    )

    assert list(tmp_path.glob("run-state-persistence-failure-*.json")) == []


def test_cleanup_failure_never_invalidates_the_correctly_persisted_state(
    tmp_path: Path,
) -> None:
    """Simula un fallo al borrar UN artefacto de emergencia obsoleto sin
    tocar `Path.glob`/`Path.unlink` globalmente (eso arriesgaria
    interferir con la limpieza propia de `tmp_path` de pytest): un
    directorio con el mismo patron de nombre coincide con el glob pero
    `Path.unlink()` sobre un directorio siempre falla con
    `IsADirectoryError` (un `OSError`), exactamente el tipo de fallo que
    `_cleanup_emergency_records` ya tolera."""
    run_json_path = tmp_path / "run.json"
    state = _state(current_stage=PipelineStage.VALIDATED)
    atomic_write_json(run_json_path, state)

    undeletable = tmp_path / "run-state-persistence-failure-undeletable.json"
    undeletable.mkdir()

    completed_state = _state(current_stage=PipelineStage.COMPLETED)
    result = runner_module._persist_run_state(
        completed_state, run_json_path, stage=PipelineStage.COMPLETED, transition="SUCCEEDED"
    )

    assert result.current_stage == PipelineStage.COMPLETED
    on_disk = RunState.model_validate_json(run_json_path.read_text(encoding="utf-8"))
    assert on_disk.current_stage == PipelineStage.COMPLETED
    assert undeletable.is_dir()  # el fallo de limpieza no se propago ni corrompio nada


# ---------------------------------------------------------------------------
# api.reads.read_run_state: precedencia del artefacto de emergencia
# ---------------------------------------------------------------------------


def test_read_run_state_prefers_newer_emergency_artifact_over_non_terminal_run_json(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path
    run_json_path = run_dir / "run.json"
    # updated_at explicitamente en el pasado (no `datetime.now(UTC)`
    # crudo): en Windows, llamadas consecutivas muy cercanas a
    # `datetime.now(UTC)` pueden resolver al mismo valor (resolucion de
    # reloj de sistema de ~15ms, verificado empiricamente) -- una
    # separacion explicita hace el test determinista sin depender de la
    # resolucion del reloj del host que lo ejecute.
    state = _state(
        current_stage=PipelineStage.VALIDATED, updated_at=datetime(2020, 1, 1, tzinfo=UTC)
    )
    atomic_write_json(run_json_path, state)

    runner_module._write_emergency_record(
        run_dir,
        state,
        stage=PipelineStage.EXTRACTED,
        transition="SUCCEEDED",
        cause=RuntimeError("causa simulada"),
    )

    resolved = read_run_state(run_dir)

    assert resolved.current_stage == PipelineStage.FAILED
    assert any(
        s.stage == PipelineStage.EXTRACTED and s.status == StageStatus.FAILED
        for s in resolved.stages
    )


def test_read_run_state_terminal_run_json_always_wins_over_stale_emergency_artifact(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path
    run_json_path = run_dir / "run.json"
    state = _state(current_stage=PipelineStage.VALIDATED)
    atomic_write_json(run_json_path, state)

    runner_module._write_emergency_record(
        run_dir,
        state,
        stage=PipelineStage.EXTRACTED,
        transition="SUCCEEDED",
        cause=RuntimeError("causa simulada, ahora obsoleta"),
    )

    # El run se resolvio con exito DESPUES: run.json termina COMPLETED,
    # el artefacto de emergencia arriba queda obsoleto pero no se borra
    # en este test (se simula que la limpieza best-effort no corrio).
    completed_state = _state(current_stage=PipelineStage.COMPLETED)
    atomic_write_json(run_json_path, completed_state)

    resolved = read_run_state(run_dir)

    assert resolved.current_stage == PipelineStage.COMPLETED


def test_read_run_state_non_terminal_run_json_newer_than_emergency_artifact_wins(
    tmp_path: Path,
) -> None:
    """Un run.json no-terminal pero MAS RECIENTE que un artefacto de
    emergencia obsoleto (p. ej. un resume que ya avanzo mas alla del
    punto que fallo) debe prevalecer sobre esa evidencia vieja."""
    run_dir = tmp_path
    run_json_path = run_dir / "run.json"
    early_state = _state(
        current_stage=PipelineStage.VALIDATED, updated_at=datetime(2020, 1, 1, tzinfo=UTC)
    )
    atomic_write_json(run_json_path, early_state)

    runner_module._write_emergency_record(
        run_dir,
        early_state,
        stage=PipelineStage.EXTRACTED,
        transition="SUCCEEDED",
        cause=RuntimeError("causa simulada, va a quedar obsoleta"),
    )

    later_state = _state(
        current_stage=PipelineStage.DEPENDENCIES_BUILT,
        updated_at=datetime.now(UTC),
    )
    atomic_write_json(run_json_path, later_state)

    resolved = read_run_state(run_dir)

    assert resolved.current_stage == PipelineStage.DEPENDENCIES_BUILT


def test_read_run_state_run_json_wins_on_exact_timestamp_tie(
    tmp_path: Path,
) -> None:
    """Investigado y corregido durante esta misma tarea: verificado
    empiricamente en Windows nativo que `datetime.now(UTC)` puede
    devolver el MISMO valor en llamadas consecutivas muy cercanas
    (resolucion de reloj de sistema de ~15ms en este host) --
    `state.updated_at` y `emergency.timestamp_utc` pueden terminar
    siendo EXACTAMENTE iguales en pruebas rapidas y sinteticas (nunca en
    produccion real: el deadline critico que antecede a un artefacto de
    emergencia, hasta 3s de reintentos, siempre separa ambos timestamps
    por mucho mas que la resolucion del reloj). Ante un empate exacto se
    elige la opcion CONSERVADORA: el `run.json` ya en disco prevalece --
    nunca se presenta un run como FAILED sin evidencia claramente
    posterior de que la emergencia lo supera (comparacion estricta `>`
    en `read_run_state`, no `>=`)."""
    run_dir = tmp_path
    run_json_path = run_dir / "run.json"
    tied_timestamp = datetime(2026, 1, 1, 12, 0, 0, 123456, tzinfo=UTC)
    state = _state(current_stage=PipelineStage.VALIDATED, updated_at=tied_timestamp)
    atomic_write_json(run_json_path, state)

    record_path = runner_module._write_emergency_record(
        run_dir,
        state,
        stage=PipelineStage.EXTRACTED,
        transition="SUCCEEDED",
        cause=RuntimeError("causa simulada"),
    )
    # Fuerza el empate exacto reescribiendo timestamp_utc al mismo valor
    # que state.updated_at, en vez de depender de la resolucion real del
    # reloj del host (determinista, no flaky).
    record = RunStatePersistenceFailureRecord.model_validate_json(
        record_path.read_text(encoding="utf-8")
    )
    tied_record = record.model_copy(update={"timestamp_utc": tied_timestamp})
    atomic_write_json(record_path, tied_record)

    resolved = read_run_state(run_dir)

    assert resolved.current_stage == PipelineStage.VALIDATED


def test_read_run_state_missing_run_json_with_emergency_artifact_returns_failed_view(
    tmp_path: Path,
) -> None:
    """Caso extremo: la primerisima transicion (RECEIVED) nunca llego a
    persistir run.json en absoluto. Aun asi debe poder observarse FAILED
    en vez de un 404 opaco, si existe evidencia de emergencia."""
    run_dir = tmp_path
    state = _state(current_stage=PipelineStage.RECEIVED)

    runner_module._write_emergency_record(
        run_dir,
        state,
        stage=PipelineStage.RECEIVED,
        transition="SUCCEEDED",
        cause=RuntimeError("causa simulada"),
    )

    resolved = read_run_state(run_dir)
    assert resolved.current_stage == PipelineStage.FAILED


def test_read_run_state_without_any_emergency_artifact_behaves_exactly_as_before(
    tmp_path: Path,
) -> None:
    """Compatibilidad con runs historicos: el caso universal (ningun
    artefacto de emergencia jamas existio) no cambia en absoluto."""
    run_dir = tmp_path
    run_json_path = run_dir / "run.json"
    state = _state(current_stage=PipelineStage.DEPENDENCIES_BUILT)
    atomic_write_json(run_json_path, state)

    resolved = read_run_state(run_dir)

    assert resolved.current_stage == PipelineStage.DEPENDENCIES_BUILT
    assert resolved.model_dump(mode="json") == state.model_dump(mode="json")


# ---------------------------------------------------------------------------
# api.executor.RunExecutor: observabilidad diferenciada
# ---------------------------------------------------------------------------


def test_executor_logs_warning_when_emergency_artifact_exists(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    executor = RunExecutor(max_workers=1)
    exc = RunStatePersistenceError(
        "no se pudo persistir run.json",
        run_id="run-1",
        stage=PipelineStage.EXTRACTED,
        transition="SUCCEEDED",
        emergency_artifact_path=tmp_path / "run-state-persistence-failure-x.json",
        cause=AtomicWriteError("simulado"),
    )

    def _raises() -> None:
        raise exc

    with caplog.at_level(logging.WARNING, logger="altamira_extractor.api.executor"):
        result = executor.try_submit("run-1", _raises)
        executor.shutdown(wait=True)

    assert result == "submitted"
    assert not executor.is_active("run-1")
    assert any(record.levelno == logging.WARNING for record in caplog.records)
    assert not any(record.levelno >= logging.CRITICAL for record in caplog.records)


def test_executor_logs_critical_when_no_emergency_artifact_exists(
    caplog: pytest.LogCaptureFixture,
) -> None:
    executor = RunExecutor(max_workers=1)
    exc = RunStatePersistenceError(
        "no se pudo persistir run.json ni la emergencia",
        run_id="run-2",
        stage=PipelineStage.EXTRACTED,
        transition="FAILED",
        emergency_artifact_path=None,
        cause=AtomicWriteError("simulado"),
    )

    def _raises() -> None:
        raise exc

    with caplog.at_level(logging.WARNING, logger="altamira_extractor.api.executor"):
        result = executor.try_submit("run-2", _raises)
        executor.shutdown(wait=True)

    assert result == "submitted"
    assert not executor.is_active("run-2")
    assert any(record.levelno == logging.CRITICAL for record in caplog.records)


def test_executor_generic_exception_still_handled_as_before(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Regresion: un fallo generico (no relacionado con persistencia de
    run.json) sigue cayendo en el manejador original, sin cambios."""
    executor = RunExecutor(max_workers=1)

    def _raises() -> None:
        raise ValueError("fallo generico no relacionado")

    with caplog.at_level(logging.ERROR, logger="altamira_extractor.api.executor"):
        result = executor.try_submit("run-3", _raises)
        executor.shutdown(wait=True)

    assert result == "submitted"
    assert not executor.is_active("run-3")
    assert any("fallo no controlado" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# Reanudacion tras este tipo de fallo
# ---------------------------------------------------------------------------


def test_run_surfaced_as_failed_via_emergency_artifact_is_not_reported_as_completed(
    tmp_path: Path,
) -> None:
    """El run nunca puede seguir pareciendo activo/no-terminal
    indefinidamente: `resume`/`submit_existing_run` solo bloquea sobre
    `COMPLETED` (ver api/run_actions.py) -- verificamos que la vista de
    emergencia jamas produce `COMPLETED`."""
    run_dir = tmp_path
    state = _state(
        current_stage=PipelineStage.VALIDATED, updated_at=datetime(2020, 1, 1, tzinfo=UTC)
    )
    atomic_write_json(run_dir / "run.json", state)

    runner_module._write_emergency_record(
        run_dir,
        state,
        stage=PipelineStage.EXTRACTED,
        transition="SUCCEEDED",
        cause=RuntimeError("causa simulada"),
    )

    resolved = read_run_state(run_dir)

    assert resolved.current_stage != PipelineStage.COMPLETED
    assert resolved.current_stage == PipelineStage.FAILED


def test_submit_existing_run_resumes_a_run_surfaced_as_failed_by_emergency_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Extremo a extremo con `api.run_actions.submit_existing_run` (el
    mismo caso de uso que `POST /api/runs/{run_id}/resume` y el CLI
    `resume`): un run cuya UNICA evidencia es el artefacto de emergencia
    (FAILED, nunca COMPLETED) debe poder reanudarse sin
    `RunNotResumableError` -- la idempotencia existente de cada etapa
    (ver docstring de runner.py) hace el resto."""
    run_dir = tmp_path / "runs" / "run-resume-1"
    (run_dir / "input").mkdir(parents=True)
    (run_dir / "input" / "package.zip").write_bytes(b"contenido de prueba, no un zip real")

    state = _state(
        run_id="run-resume-1",
        current_stage=PipelineStage.VALIDATED,
        updated_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    atomic_write_json(run_dir / "run.json", state)
    runner_module._write_emergency_record(
        run_dir,
        state,
        stage=PipelineStage.EXTRACTED,
        transition="SUCCEEDED",
        cause=RuntimeError("causa simulada"),
    )

    resolved = read_run_state(run_dir)
    assert resolved.current_stage == PipelineStage.FAILED  # nunca COMPLETED

    submitted_run_ids: list[str] = []
    monkeypatch.setattr(
        run_actions,
        "run_ingestion",
        lambda *args, **kwargs: submitted_run_ids.append(kwargs.get("run_id", args[-1])),
    )

    settings = Settings(
        data_dir=tmp_path / "data",
        runs_dir=tmp_path / "runs",
        incoming_dir=tmp_path / "data" / "incoming",
    )
    executor = RunExecutor(max_workers=1)
    try:
        run_actions.submit_existing_run(
            run_dir, "run-resume-1", resolved, settings=settings, executor=executor
        )
    finally:
        executor.shutdown(wait=True)

    assert submitted_run_ids == ["run-resume-1"]
