"""Contrato tipado de run.json: la maquina de estados del pipeline."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, RelativePath, Sha256Hex
from .enums import PipelineStage, StageStatus


class StageExecution(AltamiraBaseModel):
    """Registro de una etapa: inicio, fin, duracion, warnings y error
    (CLAUDE.md, seccion Pipeline)."""

    stage: PipelineStage
    status: StageStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None

    @model_validator(mode="after")
    def _check_state_coherence(self) -> StageExecution:
        if self.status == StageStatus.PENDING and (
            self.started_at is not None or self.finished_at is not None
        ):
            raise ValueError("una etapa PENDING no puede tener started_at/finished_at")

        if self.status == StageStatus.RUNNING:
            if self.started_at is None:
                raise ValueError("una etapa RUNNING requiere started_at")
            if self.finished_at is not None:
                raise ValueError("una etapa RUNNING no puede tener finished_at")

        if self.status in (StageStatus.SUCCEEDED, StageStatus.FAILED):
            if self.started_at is None or self.finished_at is None:
                raise ValueError(f"una etapa {self.status.value} requiere started_at y finished_at")
            if self.finished_at < self.started_at:
                raise ValueError("finished_at no puede ser anterior a started_at")

        if self.status == StageStatus.SUCCEEDED and self.error is not None:
            raise ValueError("una etapa SUCCEEDED no puede tener error")

        if self.status == StageStatus.FAILED and not self.error:
            raise ValueError("una etapa FAILED requiere un error no vacio")

        return self


class RunState(AltamiraBaseModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    package_filename: RelativePath
    source_package_hash: Sha256Hex | None = None
    current_stage: PipelineStage
    stages: list[StageExecution] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
