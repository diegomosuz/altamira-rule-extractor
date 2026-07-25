"""Router HTML server-rendered (Prompt 13d): las 8 pantallas literales
(upload, runs, estado, candidatos, contexto, regla, guardrail, descarga)
sobre `/ui/*`. Reutiliza exactamente los mismos casos de uso que
`api/routers/runs.py` -- nunca llama a `/api/*` por HTTP interno.
`include_in_schema=False` en todas las rutas: el OpenAPI JSON solo
documenta la API JSON ya autorizada."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..api.errors import ApiError
from ..api.executor import RunExecutor
from ..api.mappers import build_rule_response
from ..api.reads import (
    read_candidate_artifact,
    read_context_package,
    read_guardrail_candidate_artifact,
    read_run_state,
    require_stage_succeeded,
)
from ..api.run_actions import create_and_submit_run, submit_existing_run
from ..api.validation import validate_candidate_id, validate_run_id
from ..config import Settings
from ..contracts.enums import PipelineStage, StageStatus
from ..contracts.run_state import RunState
from .csrf import verify_same_origin
from .deps import get_executor, get_settings, get_templates

router = APIRouter(prefix="/ui", tags=["ui"], include_in_schema=False)

_TERMINAL_STAGES = (PipelineStage.COMPLETED, PipelineStage.FAILED)


def _run_dir(settings: Settings, run_id: str) -> Path:
    validate_run_id(run_id)
    return settings.runs_dir / run_id


def _stage_succeeded(state: RunState, stage: PipelineStage) -> bool:
    execution = next((s for s in state.stages if s.stage == stage), None)
    return execution is not None and execution.status == StageStatus.SUCCEEDED


def _list_run_states(settings: Settings) -> list[RunState]:
    """Duplicado deliberadamente pequeno (no extraido a `api/reads.py`,
    fuera del alcance de archivos autorizados de este prompt): mismo
    bucle que `api/routers/runs.py::list_runs`/`cli.py::list_runs`
    (omite symlinks, no-directorios y runs corruptos)."""
    states: list[RunState] = []
    if settings.runs_dir.is_dir():
        for entry in settings.runs_dir.iterdir():
            if entry.is_symlink() or not entry.is_dir():
                continue
            try:
                states.append(read_run_state(entry))
            except ApiError:
                continue
    states.sort(key=lambda s: s.run_id, reverse=True)
    return states


def _redirect(request: Request, route_name: str, path_params: dict[str, str]) -> Response:
    """303 See Other estandar (Post/Redirect/Get); si la request vino de
    HTMX, responde 200 con `HX-Redirect` en su lugar (HTMX no sigue
    redirects HTTP normales de la misma forma que un navegador en
    submits `hx-post`). Ruta RELATIVA (`app.url_path_for`, no
    `request.url_for`): igual que el redirect de `GET /` en
    `api/app.py`, evita fijar un host externo distinto si la app corre
    detras de un proxy reverso."""
    url = request.app.url_path_for(route_name, **path_params)
    if request.headers.get("hx-request") == "true":
        response = Response(status_code=200)
        response.headers["HX-Redirect"] = str(url)
        return response
    return RedirectResponse(url, status_code=303)


def _render_upload_form(
    request: Request,
    templates: Jinja2Templates,
    settings: Settings,
    *,
    error: str | None,
    status_code: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "upload.html",
        {"max_package_size_bytes": settings.max_package_size_bytes, "error": error},
        status_code=status_code,
    )


@router.get("/upload", response_class=HTMLResponse, name="ui_upload_form")
def upload_form(
    request: Request,
    settings: Settings = Depends(get_settings),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    return _render_upload_form(request, templates, settings, error=None)


@router.post("/runs", name="ui_create_run", dependencies=[Depends(verify_same_origin)])
def create_run_ui(
    request: Request,
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
    executor: RunExecutor = Depends(get_executor),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    # Mismo caso de uso que POST /api/runs (create_and_submit_run): el
    # unico set de excepciones alcanzable aqui es de subida/RECEIVED/
    # executor (EmptyUploadError, UploadTooLargeError,
    # ServiceUnavailableError, ExecutorAtCapacityError,
    # RunAlreadyActiveError) -- se re-muestra el formulario con el
    # mensaje sanitizado en vez de una pagina de error generica.
    try:
        state = create_and_submit_run(file, settings, executor)
    except ApiError as exc:
        return _render_upload_form(
            request, templates, settings, error=exc.message, status_code=exc.status_code
        )
    return _redirect(request, "ui_run_status", {"run_id": state.run_id})


@router.get("/runs", response_class=HTMLResponse, name="ui_runs")
def list_runs_ui(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    settings: Settings = Depends(get_settings),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    states = _list_run_states(settings)
    total = len(states)
    page = states[offset : offset + limit]
    return templates.TemplateResponse(
        request,
        "runs.html",
        {
            "runs": page,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_previous": offset > 0,
            "has_next": offset + limit < total,
            "previous_offset": max(0, offset - limit),
            "next_offset": offset + limit,
        },
    )


@router.get("/runs/{run_id}", response_class=HTMLResponse, name="ui_run_status")
def run_status_ui(
    request: Request,
    run_id: str,
    settings: Settings = Depends(get_settings),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    run_dir = _run_dir(settings, run_id)
    state = read_run_state(run_dir)
    return templates.TemplateResponse(
        request,
        "run_status.html",
        {"run": state, "is_terminal": state.current_stage in _TERMINAL_STAGES},
    )


@router.get(
    "/runs/{run_id}/status-fragment", response_class=HTMLResponse, name="ui_status_fragment"
)
def run_status_fragment_ui(
    request: Request,
    run_id: str,
    settings: Settings = Depends(get_settings),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    # GET puro: nunca programa ni reanuda una ejecucion, solo lee
    # RunState -- idempotente y seguro de re-pedir cada 3 segundos.
    run_dir = _run_dir(settings, run_id)
    state = read_run_state(run_dir)
    return templates.TemplateResponse(
        request,
        "_status_fragment.html",
        {"run": state, "is_terminal": state.current_stage in _TERMINAL_STAGES},
    )


@router.post(
    "/runs/{run_id}/resume", name="ui_resume", dependencies=[Depends(verify_same_origin)]
)
def resume_ui(
    request: Request,
    run_id: str,
    settings: Settings = Depends(get_settings),
    executor: RunExecutor = Depends(get_executor),
) -> Response:
    run_dir = _run_dir(settings, run_id)
    state = read_run_state(run_dir)
    # RunNotResumableError/RunAlreadyActiveError/ExecutorAtCapacityError/
    # ArtifactCorruptedError se dejan propagar al handler HTML generico
    # (error.html): son errores sanitizados de todos modos, y evita una
    # segunda ruta de render especial dentro de este handler.
    submit_existing_run(run_dir, run_id, state, settings, executor)
    return _redirect(request, "ui_run_status", {"run_id": run_id})


@router.get("/runs/{run_id}/candidates", response_class=HTMLResponse, name="ui_candidates")
def candidates_ui(
    request: Request,
    run_id: str,
    settings: Settings = Depends(get_settings),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    run_dir = _run_dir(settings, run_id)
    state = read_run_state(run_dir)
    require_stage_succeeded(state, PipelineStage.CANDIDATES_DETECTED)
    artifact = read_candidate_artifact(run_dir, state.source_package_hash)
    return templates.TemplateResponse(
        request,
        "candidates.html",
        {
            "run_id": run_id,
            "candidates": artifact.candidates,
            "context_ready": _stage_succeeded(state, PipelineStage.CONTEXTS_BUILT),
            "rule_ready": _stage_succeeded(state, PipelineStage.GUARDRAILS_APPLIED),
        },
    )


@router.get(
    "/runs/{run_id}/candidates/{candidate_id}/context",
    response_class=HTMLResponse,
    name="ui_context",
)
def context_ui(
    request: Request,
    run_id: str,
    candidate_id: str,
    settings: Settings = Depends(get_settings),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    validate_candidate_id(candidate_id)
    run_dir = _run_dir(settings, run_id)
    state = read_run_state(run_dir)
    require_stage_succeeded(state, PipelineStage.CONTEXTS_BUILT)
    package = read_context_package(run_dir, candidate_id)
    return templates.TemplateResponse(
        request,
        "context.html",
        {"run_id": run_id, "candidate_id": candidate_id, "ctx": package},
    )


@router.get(
    "/runs/{run_id}/candidates/{candidate_id}/rule", response_class=HTMLResponse, name="ui_rule"
)
def rule_ui(
    request: Request,
    run_id: str,
    candidate_id: str,
    settings: Settings = Depends(get_settings),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    validate_candidate_id(candidate_id)
    run_dir = _run_dir(settings, run_id)
    state = read_run_state(run_dir)
    require_stage_succeeded(state, PipelineStage.GUARDRAILS_APPLIED)
    artifact = read_guardrail_candidate_artifact(run_dir, candidate_id)
    response = build_rule_response(artifact)
    return templates.TemplateResponse(
        request,
        "rule.html",
        {"run_id": run_id, "candidate_id": candidate_id, "rule": response},
    )


@router.get(
    "/runs/{run_id}/candidates/{candidate_id}/guardrail",
    response_class=HTMLResponse,
    name="ui_guardrail",
)
def guardrail_ui(
    request: Request,
    run_id: str,
    candidate_id: str,
    settings: Settings = Depends(get_settings),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    validate_candidate_id(candidate_id)
    run_dir = _run_dir(settings, run_id)
    state = read_run_state(run_dir)
    require_stage_succeeded(state, PipelineStage.GUARDRAILS_APPLIED)
    artifact = read_guardrail_candidate_artifact(run_dir, candidate_id)
    response = build_rule_response(artifact)
    return templates.TemplateResponse(
        request,
        "guardrail.html",
        {"run_id": run_id, "candidate_id": candidate_id, "rule": response},
    )


@router.get("/runs/{run_id}/download", response_class=HTMLResponse, name="ui_download")
def download_ui(
    request: Request,
    run_id: str,
    settings: Settings = Depends(get_settings),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    run_dir = _run_dir(settings, run_id)
    state = read_run_state(run_dir)
    require_stage_succeeded(state, PipelineStage.COMPLETED)
    return templates.TemplateResponse(request, "download.html", {"run_id": run_id, "run": state})
