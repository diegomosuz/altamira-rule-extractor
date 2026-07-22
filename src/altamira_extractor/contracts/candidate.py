"""Contrato tipado del artefacto 06-candidates.json (salida de Q0).

Un RuleCandidate es un patron estructural detectado, no una regla
confirmada (CLAUDE.md, seccion 'Candidato, fidelidad y aprobacion')."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import AltamiraBaseModel, RelativePath, Sha256Hex
from .enums import CandidateStatus


class RuleCandidate(AltamiraBaseModel):
    schema_version: Literal["1.0"] = "1.0"
    candidate_id: str = Field(min_length=1)
    paragraph_id: str = Field(min_length=1)
    paragraph_name: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    detector_id: str = Field(min_length=1)
    detector_version: str = Field(min_length=1)
    detector_score: float = Field(ge=0, le=1)
    status: CandidateStatus = CandidateStatus.DETECTED_CANDIDATE
    condition: str = Field(min_length=1)
    outcome_code: str | None = None
    rule_type: str = Field(min_length=1)
    line_start: int = Field(ge=1)
    source_file: RelativePath
    source_package_hash: Sha256Hex
