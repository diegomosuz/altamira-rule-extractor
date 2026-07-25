"""Endpoints `/api/runs*` (Prompt 13b): exactamente los documentados en
docs/ARCHITECTURE.md Seccion 5 -- ningun endpoint adicional (no existe
`/guardrail` separado; la pantalla futura de regla y la pantalla futura
de guardrail consumiran ambas `GET .../rule`)."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from ...config import Settings
from ...contracts.context_package import ContextPackage
from ...contracts.enums import PipelineStage
from ...contracts.run_state import RunState
from ..deps import get_executor, get_settings
from ..downloads import build_rules_zip
from ..errors import ApiError
from ..executor import RunExecutor
from ..mappers import build_rule_response
from ..reads import (
    read_candidate_artifact,
    read_context_package,
    read_guardrail_candidate_artifact,
    read_run_state,
    require_stage_succeeded,
)
from ..run_actions import create_and_submit_run, submit_existing_run
from ..schemas import (
    CandidatesResponse,
    CandidateSummary,
    RuleResponse,
    RunAcceptedResponse,
    RunDetail,
    RunListResponse,
    RunSummary,
    StageExecutionView,
)
from ..validation import validate_candidate_id, validate_run_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/runs", tags=["runs"])


def _run_dir(settings: Settings, run_id: str) -> Path:
    validate_run_id(run_id)
    run_dir = settings.runs_dir / run_id
    if run_dir.parent != settings.runs_dir:
        # Inalcanzable en la practica (el regex de validate_run_id ya
        # prohibe separadores de path), pero se deja como defensa
        # explicita en profundidad.
        raise ApiError("run_id con formato invalido", status_code=422, code="invalid_identifier")
    return run_dir


def _run_summary(state: RunState) -> RunSummary:
    return RunSummary(
        run_id=state.run_id,
        current_stage=state.current_stage,
        source_package_hash=state.source_package_hash,
        created_at=state.created_at,
        updated_at=state.updated_at,
    )


def _run_detail(state: RunState) -> RunDetail:
    summary = _run_summary(state)
    return RunDetail(
        **summary.model_dump(),
        package_filename=state.package_filename,
        stages=[
            StageExecutionView(
                stage=s.stage,
                status=s.status,
                started_at=s.started_at,
                finished_at=s.finished_at,
                duration_seconds=s.duration_seconds,
                warnings=s.warnings,
                error=s.error,
            )
            for s in state.stages
        ],
    )


@router.post(
    "",
    response_model=RunAcceptedResponse,
    status_code=202,
    summary="Crea un run a partir de un paquete .zip y programa su ejecucion",
)
def create_run(
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
    executor: RunExecutor = Depends(get_executor),
) -> RunAcceptedResponse:
    state = create_and_submit_run(file, settings, executor)
    return RunAcceptedResponse(
        run_id=state.run_id,
        current_stage=state.current_stage,
        status="accepted",
        status_url=f"/api/runs/{state.run_id}",
    )


@router.get("", response_model=RunListResponse, summary="Lista runs (mas recientes primero)")
def list_runs(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    settings: Settings = Depends(get_settings),
) -> RunListResponse:
    summaries: list[RunSummary] = []
    if settings.runs_dir.is_dir():
        for entry in settings.runs_dir.iterdir():
            if entry.is_symlink() or not entry.is_dir():
                continue
            try:
                state = read_run_state(entry)
            except ApiError:
                logger.warning("run corrupto omitido del listado: run_id=%s", entry.name)
                continue
            summaries.append(_run_summary(state))

    summaries.sort(key=lambda s: s.run_id, reverse=True)
    total = len(summaries)
    page = summaries[offset : offset + limit]
    return RunListResponse(runs=page, total=total, limit=limit, offset=offset)


@router.get("/{run_id}", response_model=RunDetail, summary="Detalle de un run")
def get_run(run_id: str, settings: Settings = Depends(get_settings)) -> RunDetail:
    run_dir = _run_dir(settings, run_id)
    state = read_run_state(run_dir)
    return _run_detail(state)


@router.post(
    "/{run_id}/resume",
    response_model=RunAcceptedResponse,
    status_code=202,
    summary="Reanuda un run existente",
)
def resume_run(
    run_id: str,
    settings: Settings = Depends(get_settings),
    executor: RunExecutor = Depends(get_executor),
) -> RunAcceptedResponse:
    run_dir = _run_dir(settings, run_id)
    state = read_run_state(run_dir)
    submit_existing_run(run_dir, run_id, state, settings, executor)

    return RunAcceptedResponse(
        run_id=run_id,
        current_stage=state.current_stage,
        status="accepted",
        status_url=f"/api/runs/{run_id}",
    )


@router.get(
    "/{run_id}/candidates",
    response_model=CandidatesResponse,
    summary="Lista los candidatos detectados (Q0)",
)
def get_candidates(run_id: str, settings: Settings = Depends(get_settings)) -> CandidatesResponse:
    run_dir = _run_dir(settings, run_id)
    state = read_run_state(run_dir)
    require_stage_succeeded(state, PipelineStage.CANDIDATES_DETECTED)
    artifact = read_candidate_artifact(run_dir, state.source_package_hash)
    return CandidatesResponse(
        candidates=[
            CandidateSummary(
                candidate_id=c.candidate_id,
                paragraph_id=c.paragraph_id,
                paragraph_name=c.paragraph_name,
                decision_id=c.decision_id,
                condition=c.condition,
                outcome_code=c.outcome_code,
                rule_type=c.rule_type,
                status=c.status,
            )
            for c in artifact.candidates
        ]
    )


@router.get(
    "/{run_id}/candidates/{candidate_id}/context",
    response_model=ContextPackage,
    summary="Paquete contextual (D1-D7) de un candidato",
)
def get_context(
    run_id: str, candidate_id: str, settings: Settings = Depends(get_settings)
) -> ContextPackage:
    validate_candidate_id(candidate_id)
    run_dir = _run_dir(settings, run_id)
    state = read_run_state(run_dir)
    require_stage_succeeded(state, PipelineStage.CONTEXTS_BUILT)
    return read_context_package(run_dir, candidate_id)


@router.get(
    "/{run_id}/candidates/{candidate_id}/rule",
    response_model=RuleResponse,
    summary="Regla propuesta y veredicto del guardrail de un candidato",
    description=(
        "Unico endpoint para 'regla' y 'guardrail': no existe un endpoint separado "
        "GET .../guardrail. La pantalla futura de regla consumira final_rule_draft; la "
        "pantalla futura de guardrail consumira la seccion guardrail."
    ),
)
def get_rule(
    run_id: str, candidate_id: str, settings: Settings = Depends(get_settings)
) -> RuleResponse:
    validate_candidate_id(candidate_id)
    run_dir = _run_dir(settings, run_id)
    state = read_run_state(run_dir)
    require_stage_succeeded(state, PipelineStage.GUARDRAILS_APPLIED)
    artifact = read_guardrail_candidate_artifact(run_dir, candidate_id)
    return build_rule_response(artifact)


def _delete_temp_file(path: Path) -> None:
    if path.exists():
        path.unlink()


@router.get(
    "/{run_id}/download", summary="Descarga el ZIP de reglas finales (artifacts/10-rules/)"
)
def download_rules(run_id: str, settings: Settings = Depends(get_settings)) -> FileResponse:
    run_dir = _run_dir(settings, run_id)
    state = read_run_state(run_dir)
    require_stage_succeeded(state, PipelineStage.COMPLETED)
    zip_path = build_rules_zip(run_dir, run_id)
    return FileResponse(
        path=zip_path,
        media_type="application/zip",
        filename=f"{run_id}-rules.zip",
        background=BackgroundTask(_delete_temp_file, zip_path),
    )
