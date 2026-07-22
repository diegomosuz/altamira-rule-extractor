"""Contrato tipado de RuleDraft — debe ser compatible con
schemas/rule-draft.schema.json (schema_version 2.0).

functional_review_status queda fijo en NEEDS_FUNCTIONAL_REVIEW: ninguna
salida V1 puede presentarse como regla aprobada (CLAUDE.md)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from .base import AltamiraBaseModel
from .enums import ClaimField, EvidenceValidationStatus, FunctionalReviewStatus


class Claim(AltamiraBaseModel):
    claim_id: str = Field(min_length=1)
    field: ClaimField
    evidence_paths: list[str] = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)

    @field_validator("evidence_paths")
    @classmethod
    def _paths_must_be_jsonpath(cls, paths: list[str]) -> list[str]:
        for path in paths:
            if not path.startswith("$."):
                raise ValueError(f"evidence_path debe empezar con '$.': {path!r}")
        return paths


class RuleDraft(AltamiraBaseModel):
    schema_version: Literal["2.0"] = "2.0"
    title: str = Field(min_length=1)
    context: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    condition: str = Field(min_length=1)
    parameters: list[str] = Field(default_factory=list)
    effect: str = Field(min_length=1)
    parameter_source: str | None = None
    traceability: list[str] = Field(min_length=1)
    limitations: list[str] = Field(min_length=1)
    claims: list[Claim] = Field(min_length=1)
    evidence_validation_status: EvidenceValidationStatus
    functional_review_status: FunctionalReviewStatus = (
        FunctionalReviewStatus.NEEDS_FUNCTIONAL_REVIEW
    )
