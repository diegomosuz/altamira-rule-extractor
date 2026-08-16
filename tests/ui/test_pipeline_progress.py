"""Progreso del pipeline (Fase v1.17.1, Feature 2/3): calculo puro,
derivado exclusivamente de `RunState.stages` y del orden real de
`PipelineStage` -- nunca de tiempo transcurrido. Ver
`ui/presentation.py::compute_pipeline_progress`."""

from __future__ import annotations

from datetime import UTC, datetime

from altamira_extractor.contracts.enums import PipelineStage, StageStatus
from altamira_extractor.contracts.run_state import RunState, StageExecution
from altamira_extractor.ui.presentation import _PROGRESSABLE_STAGES, compute_pipeline_progress

_NOW = datetime.now(UTC)


def _state(stages: list[StageExecution], current_stage: PipelineStage) -> RunState:
    return RunState(
        run_id="run-1",
        package_filename="input/package.zip",
        current_stage=current_stage,
        stages=stages,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _succeeded(stage: PipelineStage) -> StageExecution:
    return StageExecution(
        stage=stage, status=StageStatus.SUCCEEDED, started_at=_NOW, finished_at=_NOW,
        duration_seconds=1.0,
    )


def test_progressable_stages_excludes_failed_and_matches_enum_order() -> None:
    assert PipelineStage.FAILED not in _PROGRESSABLE_STAGES
    assert list(_PROGRESSABLE_STAGES) == [s for s in PipelineStage if s != PipelineStage.FAILED]


def test_fresh_run_is_zero_percent() -> None:
    progress = compute_pipeline_progress(_state([], PipelineStage.RECEIVED), is_actively_owned=True)
    assert progress.overall_percent == 0
    assert all(sv.status == "PENDING" and sv.percent == 0 for sv in progress.stages)


def test_running_stage_contributes_half_credit_not_a_fabricated_percent() -> None:
    stages = [
        StageExecution(stage=PipelineStage.RECEIVED, status=StageStatus.RUNNING, started_at=_NOW)
    ]
    progress = compute_pipeline_progress(
        _state(stages, PipelineStage.RECEIVED), is_actively_owned=True
    )
    total = len(_PROGRESSABLE_STAGES)
    assert progress.overall_percent == round(100 * 0.5 / total)
    received_view = progress.stages[0]
    assert received_view.status == "RUNNING"
    # None (no un numero inventado): la UI renderiza esto como un
    # <progress> nativo sin `value`, nunca un porcentaje fabricado.
    assert received_view.percent is None


def test_intermediate_progress_reflects_succeeded_stage_count() -> None:
    stages = [_succeeded(s) for s in _PROGRESSABLE_STAGES[:5]]
    progress = compute_pipeline_progress(
        _state(stages, _PROGRESSABLE_STAGES[4]), is_actively_owned=True
    )
    assert progress.overall_percent == round(100 * 5 / len(_PROGRESSABLE_STAGES))
    for sv in progress.stages[:5]:
        assert sv.status == "SUCCEEDED" and sv.percent == 100
    for sv in progress.stages[5:]:
        assert sv.status == "PENDING" and sv.percent == 0


def test_failed_run_retains_real_percentage_reached_never_100() -> None:
    stages = [_succeeded(s) for s in _PROGRESSABLE_STAGES[:6]]
    stages.append(
        StageExecution(
            stage=_PROGRESSABLE_STAGES[6],
            status=StageStatus.FAILED,
            started_at=_NOW,
            finished_at=_NOW,
            duration_seconds=1.0,
            error="fallo simulado",
        )
    )
    progress = compute_pipeline_progress(
        _state(stages, PipelineStage.FAILED), is_actively_owned=True
    )
    expected = round(100 * 6 / len(_PROGRESSABLE_STAGES))
    assert progress.overall_percent == expected
    assert progress.overall_percent != 100
    failed_view = next(sv for sv in progress.stages if sv.stage == _PROGRESSABLE_STAGES[6].value)
    assert failed_view.status == "FAILED"
    assert failed_view.percent == 0


def test_complete_pipeline_is_exactly_100_percent() -> None:
    stages = [_succeeded(s) for s in _PROGRESSABLE_STAGES]
    progress = compute_pipeline_progress(
        _state(stages, PipelineStage.COMPLETED), is_actively_owned=True
    )
    assert progress.overall_percent == 100
    assert all(sv.percent == 100 for sv in progress.stages)


def test_percent_never_out_of_bounds_for_any_valid_state() -> None:
    for count in range(len(_PROGRESSABLE_STAGES) + 1):
        stages = [_succeeded(s) for s in _PROGRESSABLE_STAGES[:count]]
        progress = compute_pipeline_progress(
            _state(stages, PipelineStage.RECEIVED), is_actively_owned=True
        )
        assert 0 <= progress.overall_percent <= 100
        for sv in progress.stages:
            if sv.percent is not None:
                assert 0 <= sv.percent <= 100


def test_older_run_json_without_any_stage_reads_as_fully_pending() -> None:
    """Backward compatibility: un RunState v1.17.0 (schema_version="1.0",
    sin ningun campo nuevo) sigue siendo perfectamente legible -- sus
    etapas simplemente se presentan como PENDING."""
    legacy = RunState.model_validate(
        {
            "schema_version": "1.0",
            "run_id": "run-legacy",
            "package_filename": "input/package.zip",
            "current_stage": "RECEIVED",
            "stages": [],
            "created_at": _NOW.isoformat(),
            "updated_at": _NOW.isoformat(),
        }
    )
    assert legacy.duplicate_of_run_id is None
    progress = compute_pipeline_progress(legacy, is_actively_owned=True)
    assert progress.overall_percent == 0


# --- Etapa RUNNING persistida sin ejecucion activa real (restart mid-stage) ---
# CHECK 2 del pre-commit review: `_mark_running` (runner.py) nunca actualiza
# `current_stage` -- un reinicio del proceso a mitad de una etapa deja una
# StageExecution RUNNING persistida sin que ningun StageExecution posterior la
# reemplace hasta el proximo intento real. `RunExecutor.is_active()` (unico en
# memoria, nunca persistido) es la UNICA fuente de "en ejecucion ahora mismo".


def test_stale_running_stage_is_never_presented_as_actively_running() -> None:
    stages = [
        _succeeded(PipelineStage.RECEIVED),
        _succeeded(PipelineStage.VALIDATED),
        StageExecution(stage=PipelineStage.EXTRACTED, status=StageStatus.RUNNING, started_at=_NOW),
    ]
    state = _state(stages, PipelineStage.VALIDATED)  # current_stage nunca avanzo
    progress = compute_pipeline_progress(state, is_actively_owned=False)

    extracted_view = next(sv for sv in progress.stages if sv.stage == "EXTRACTED")
    assert extracted_view.status == "RUNNING"
    assert extracted_view.stale is True
    # Nunca un porcentaje inventado tampoco en el caso obsoleto.
    assert extracted_view.percent is None


def test_live_running_stage_owned_by_executor_is_not_stale() -> None:
    stages = [
        _succeeded(PipelineStage.RECEIVED),
        StageExecution(stage=PipelineStage.VALIDATED, status=StageStatus.RUNNING, started_at=_NOW),
    ]
    state = _state(stages, PipelineStage.RECEIVED)
    progress = compute_pipeline_progress(state, is_actively_owned=True)

    validated_view = next(sv for sv in progress.stages if sv.stage == "VALIDATED")
    assert validated_view.status == "RUNNING"
    assert validated_view.stale is False


def test_stale_running_never_marks_run_completed_or_corrupts_progress() -> None:
    """current_stage nunca avanza por una etapa RUNNING obsoleta (solo
    _mark_succeeded/_mark_failed lo hacen, ver runner.py) -- el credito
    de progreso GLOBAL de esa etapa se mantiene igual (0.5), el problema
    obsoleto es puramente de PRESENTACION de esa etapa puntual, nunca de
    los datos ni del estado general."""
    stages = [_succeeded(s) for s in _PROGRESSABLE_STAGES[:3]]
    stages.append(
        StageExecution(stage=_PROGRESSABLE_STAGES[3], status=StageStatus.RUNNING, started_at=_NOW)
    )
    state = _state(stages, _PROGRESSABLE_STAGES[2])
    progress_live = compute_pipeline_progress(state, is_actively_owned=True)
    progress_stale = compute_pipeline_progress(state, is_actively_owned=False)

    # mismo credito global en ambos casos: is_actively_owned solo afecta
    # la presentacion de ESA etapa, nunca la aritmetica.
    assert progress_live.overall_percent == progress_stale.overall_percent
    assert progress_stale.overall_percent != 100
    assert state.current_stage != PipelineStage.COMPLETED
    # solo una StageExecution por etapa (nunca duplicada).
    assert len(state.stages) == 4
    assert len({s.stage for s in state.stages}) == 4
