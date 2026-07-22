"""CLI Typer del pipeline de ingesta.

Uso (CLAUDE.md, seccion Pipeline / Prompt 3):

    python -m altamira_extractor.cli ingest <zip>
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from .config import load_settings
from .contracts.enums import PipelineStage
from .pipeline.runner import run_ingestion

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


@app.command()
def ingest(package: PackageArgument, run_id: RunIdOption = None) -> None:
    """Ejecuta RECEIVED -> VALIDATED -> EXTRACTED -> INVENTORIED sobre PACKAGE."""
    settings = load_settings()
    state = run_ingestion(source_zip=package, settings=settings, run_id=run_id)

    typer.echo(f"run_id: {state.run_id}")
    typer.echo(f"stage: {state.current_stage.value}")
    for stage in state.stages:
        line = f"  {stage.stage.value}: {stage.status.value}"
        if stage.error:
            line += f" ({stage.error})"
        typer.echo(line)

    if state.current_stage == PipelineStage.FAILED:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
