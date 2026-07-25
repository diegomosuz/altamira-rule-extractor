"""CLI Typer del pipeline de ingesta (Prompt 3, extendido en Prompt 13c).

Uso:

    python -m altamira_extractor.cli ingest <zip>
    python -m altamira_extractor.cli resume <run_id>
    python -m altamira_extractor.cli runs
    python -m altamira_extractor.cli status <run_id>
    python -m altamira_extractor.cli candidates <run_id>
    python -m altamira_extractor.cli context <run_id> <candidate_id>
    python -m altamira_extractor.cli rule <run_id> <candidate_id>
    python -m altamira_extractor.cli download <run_id> [--output PATH]

Opera directamente sobre el filesystem local y las funciones Python
compartidas del pipeline (`pipeline/runner.py`) y de lectura de
artefactos (`api/reads.py`, `api/downloads.py`, `api/validation.py`,
`api/mappers.py`) -- nunca llama a FastAPI por HTTP, y por lo tanto no
requiere que el servidor este' levantado. Esos modulos de `api/` son
Python puro (sin `fastapi`, sin `Depends`/`Request`/`Response`, sin
estado de aplicacion ASGI): reutilizarlos aqui no importa el framework
HTTP, solo su logica de validacion/lectura ya probada -- evita
duplicar esa validacion sin necesitar un paquete `application/` nuevo
(ver docs/ARCHITECTURE.md 3.1, que documenta casos de uso compartidos,
no una clase concreta). `build_rule_response` (Prompt 13d) es la misma
funcion que usan `api/routers/runs.py::get_rule` y la UI (`ui/`): un
unico mapeo `GuardrailCandidateArtifact -> RuleResponse`, nunca
duplicado una segunda vez.
"""

from __future__ import annotations

import functools
import logging
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer

from .api.downloads import build_rules_zip
from .api.errors import ApiError, ArtifactCorruptedError, RunNotResumableError
from .api.mappers import build_rule_response
from .api.reads import (
    read_candidate_artifact,
    read_context_package,
    read_guardrail_candidate_artifact,
    read_run_state,
    require_stage_succeeded,
)
from .api.validation import validate_candidate_id, validate_run_id
from .config import Settings, load_settings
from .contracts.enums import PipelineStage
from .contracts.run_state import RunState
from .pipeline.runner import run_ingestion

logger = logging.getLogger(__name__)

app = typer.Typer(add_completion=False, help="Altamira Rule Extractor - pipeline de ingesta")

PackageArgument = Annotated[
    Path,
    typer.Argument(
        exists=True,
        dir_okay=False,
        readable=True,
        help="Ruta al paquete .zip Altamira a ingerir",
    ),
]
RunIdOption = Annotated[
    str | None,
    typer.Option("--run-id", help="Reanuda un run_id existente en vez de crear uno nuevo"),
]
RunIdArgument = Annotated[str, typer.Argument(help="Identificador del run (run_id)")]
CandidateIdArgument = Annotated[
    str, typer.Argument(help="Identificador del candidato (candidate_id)")
]
LimitOption = Annotated[
    int, typer.Option(min=1, max=100, help="Cantidad maxima de runs a listar")
]
OffsetOption = Annotated[
    int, typer.Option(min=0, help="Cantidad de runs a omitir desde el mas reciente")
]
OutputOption = Annotated[
    Path | None,
    typer.Option("--output", help="Destino del ZIP (default: ./RUN_ID-rules.zip)"),
]

# Exit codes estables (Prompt 13c). Mapeados por `ApiError.code` (nunca por
# texto de mensaje): "resource not found" -> 3, "etapa/estado no valido
# para la operacion" -> 4, "artefacto corrupto" -> 5. Ningun otro `code`
# de ApiError es alcanzable desde el CLI (RunAlreadyActiveError,
# ExecutorAtCapacityError, EmptyUploadError, UploadTooLargeError,
# ServiceUnavailableError son exclusivos de la API HTTP/upload/executor,
# que el CLI no usa) -- si alguno llegara a ocurrir de todos modos, cae al
# 1 generico por seguridad.
_EXIT_CODE_BY_ERROR_CODE: dict[str, int] = {
    "run_not_found": 3,
    "candidate_not_found": 3,
    "stage_not_reached": 4,
    "run_not_resumable": 4,
    "artifact_corrupted": 5,
    "invalid_identifier": 2,
}


class _UsageError(Exception):
    """Error de uso local del CLI (p. ej. `--output` invalido) que no
    corresponde a ningun `ApiError` compartido con la API. Mapea a exit
    code 2, igual que un argumento invalido detectado por Typer/Click."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _guard[**P, R](func: Callable[P, R]) -> Callable[P, R]:
    """Limite exterior comun de cada comando: nunca deja escapar un
    stacktrace, un path absoluto interno o un mensaje sin sanitizar, y
    traduce cada situacion anticipada a un exit code estable (ver tabla
    de `_EXIT_CODE_BY_ERROR_CODE`). `typer.Exit` (usado por `ingest`/
    `resume` para el exit code 1 de un pipeline FAILED) se re-lanza sin
    tocar: este wrapper solo interviene en rutas de error no manejadas
    explicitamente por el comando."""

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return func(*args, **kwargs)
        except typer.Exit:
            raise
        except _UsageError as exc:
            typer.echo(exc.message, err=True)
            raise typer.Exit(code=2) from exc
        except KeyboardInterrupt:
            typer.echo("Operación interrumpida.", err=True)
            raise typer.Exit(code=130) from None
        except ApiError as exc:
            typer.echo(exc.message, err=True)
            raise typer.Exit(code=_EXIT_CODE_BY_ERROR_CODE.get(exc.code, 1)) from exc
        except Exception:
            logger.exception("error interno inesperado en comando CLI")
            typer.echo("error interno", err=True)
            raise typer.Exit(code=1) from None

    return wrapper


def _run_dir(settings: Settings, run_id: str) -> Path:
    validate_run_id(run_id)
    return settings.runs_dir / run_id


def _print_ingestion_summary(state: RunState) -> None:
    typer.echo(f"run_id: {state.run_id}")
    typer.echo(f"stage: {state.current_stage.value}")
    for stage in state.stages:
        line = f"  {stage.stage.value}: {stage.status.value}"
        if stage.error:
            line += f" ({stage.error})"
        typer.echo(line)


def _resolve_download_destination(run_id: str, output: Path | None) -> Path:
    destination = output if output is not None else Path(f"{run_id}-rules.zip")
    if destination.is_dir():
        raise _UsageError(f"el destino no puede ser un directorio: {destination}")
    if destination.exists():
        raise _UsageError(f"el destino ya existe, no se sobrescribe: {destination}")
    parent = destination.parent
    if not parent.is_dir():
        raise _UsageError(f"el directorio padre del destino no existe: {parent}")
    return destination


@app.command()
@_guard
def ingest(package: PackageArgument, run_id: RunIdOption = None) -> None:
    """Ejecuta el pipeline completo (RECEIVED -> ... -> COMPLETED o FAILED) sobre PACKAGE."""
    settings = load_settings()
    state = run_ingestion(source_zip=package, settings=settings, run_id=run_id)
    _print_ingestion_summary(state)

    if state.current_stage == PipelineStage.FAILED:
        raise typer.Exit(code=1)


@app.command()
@_guard
def resume(run_id: RunIdArgument) -> None:
    """Reanuda RUN_ID de forma bloqueante, desde la ultima etapa valida.

    Limitacion V1: el CLI no coordina locks con un proceso FastAPI que
    pudiera estar ejecutando el mismo run (no hay `RunExecutor`, no hay
    `api_max_workers` aqui: el CLI es un unico proceso y comando). El
    operador no debe ejecutar `resume` localmente mientras el mismo run
    este activo via la API.
    """
    settings = load_settings()
    run_dir = _run_dir(settings, run_id)
    state = read_run_state(run_dir)

    if state.current_stage == PipelineStage.COMPLETED:
        raise RunNotResumableError()

    input_zip_path = run_dir / "input" / "package.zip"
    if input_zip_path.is_symlink() or not input_zip_path.is_file():
        raise ArtifactCorruptedError("input/package.zip ausente, no regular o symlink")

    state = run_ingestion(input_zip_path, settings, run_id=run_id)
    _print_ingestion_summary(state)

    if state.current_stage == PipelineStage.FAILED:
        raise typer.Exit(code=1)


@app.command(name="runs")
@_guard
def list_runs(limit: LimitOption = 20, offset: OffsetOption = 0) -> None:
    """Lista runs (mas recientes primero), igual orden que `GET /api/runs`."""
    settings = load_settings()
    states: list[RunState] = []
    if settings.runs_dir.is_dir():
        for entry in settings.runs_dir.iterdir():
            if entry.is_symlink() or not entry.is_dir():
                continue
            try:
                states.append(read_run_state(entry))
            except ApiError:
                logger.warning("run corrupto omitido del listado: run_id=%s", entry.name)
                continue

    if not states:
        typer.echo("No hay ejecuciones registradas.")
        return

    states.sort(key=lambda s: s.run_id, reverse=True)
    for state in states[offset : offset + limit]:
        hash_display = state.source_package_hash or "No informado"
        typer.echo(f"{state.run_id}\t{state.current_stage.value}\t{hash_display}")


@app.command()
@_guard
def status(run_id: RunIdArgument) -> None:
    """Muestra el `RunState` persistido de RUN_ID tal cual (sin reinterpretar etapas)."""
    settings = load_settings()
    run_dir = _run_dir(settings, run_id)
    state = read_run_state(run_dir)

    typer.echo(f"run_id: {state.run_id}")
    typer.echo(f"current_stage: {state.current_stage.value}")
    typer.echo(f"package_filename: {state.package_filename}")
    typer.echo(f"source_package_hash: {state.source_package_hash or 'No informado'}")
    for stage_execution in state.stages:
        typer.echo(f"  {stage_execution.stage.value}: {stage_execution.status.value}")
        started_at = stage_execution.started_at
        finished_at = stage_execution.finished_at
        started = started_at.isoformat() if started_at else "No informado"
        finished = finished_at.isoformat() if finished_at else "No informado"
        typer.echo(f"    started_at: {started}")
        typer.echo(f"    finished_at: {finished}")
        for warning in stage_execution.warnings:
            typer.echo(f"    warning: {warning}")
        if stage_execution.error:
            typer.echo(f"    error: {stage_execution.error}")


@app.command()
@_guard
def candidates(run_id: RunIdArgument) -> None:
    """Lista los candidatos detectados (Q0) de RUN_ID, sin hashes ni provenance."""
    settings = load_settings()
    run_dir = _run_dir(settings, run_id)
    state = read_run_state(run_dir)
    require_stage_succeeded(state, PipelineStage.CANDIDATES_DETECTED)
    artifact = read_candidate_artifact(run_dir, state.source_package_hash)

    if not artifact.candidates:
        typer.echo("No hay candidatos detectados.")
        return

    for candidate in artifact.candidates:
        outcome_code = candidate.outcome_code or "No informado"
        rule_type = candidate.rule_type or "No informado"
        typer.echo(
            f"{candidate.candidate_id}\t{candidate.paragraph_name}\t{candidate.condition}\t"
            f"{outcome_code}\t{rule_type}\t{candidate.status.value}"
        )


@app.command()
@_guard
def context(run_id: RunIdArgument, candidate_id: CandidateIdArgument) -> None:
    """Imprime el ContextPackage validado (D1-D7) de CANDIDATE_ID como JSON indentado."""
    validate_candidate_id(candidate_id)
    settings = load_settings()
    run_dir = _run_dir(settings, run_id)
    state = read_run_state(run_dir)
    require_stage_succeeded(state, PipelineStage.CONTEXTS_BUILT)
    package = read_context_package(run_dir, candidate_id)
    typer.echo(package.model_dump_json(indent=2, exclude_none=False))


@app.command()
@_guard
def rule(run_id: RunIdArgument, candidate_id: CandidateIdArgument) -> None:
    """Imprime la regla propuesta y el veredicto del guardrail de CANDIDATE_ID como JSON.

    Mismo contenido funcional que
    `GET /api/runs/{run_id}/candidates/{candidate_id}/rule`: nunca
    `repair_history`, hashes de provenance, `provider`/`model` ni el
    draft PENDING de `artifacts/08-rule-drafts/` (nunca se lee ese
    directorio).
    """
    validate_candidate_id(candidate_id)
    settings = load_settings()
    run_dir = _run_dir(settings, run_id)
    state = read_run_state(run_dir)
    require_stage_succeeded(state, PipelineStage.GUARDRAILS_APPLIED)
    artifact = read_guardrail_candidate_artifact(run_dir, candidate_id)
    response = build_rule_response(artifact)
    typer.echo(response.model_dump_json(indent=2))


@app.command()
@_guard
def download(run_id: RunIdArgument, output: OutputOption = None) -> None:
    """Descarga el ZIP de reglas finales (artifacts/10-rules/) de RUN_ID a OUTPUT."""
    settings = load_settings()
    run_dir = _run_dir(settings, run_id)
    state = read_run_state(run_dir)
    require_stage_succeeded(state, PipelineStage.COMPLETED)
    destination = _resolve_download_destination(run_id, output)

    zip_path = build_rules_zip(run_dir, run_id)
    try:
        shutil.move(str(zip_path), str(destination))
    finally:
        if zip_path.exists():
            zip_path.unlink()

    typer.echo(f"Reglas descargadas en: {destination}")


if __name__ == "__main__":
    app()
