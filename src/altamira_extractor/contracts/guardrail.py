"""Contrato tipado del artefacto 09-guardrails/ (veredicto determinístico
sobre un RuleDraft)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, Sha256Hex
from .enums import GuardrailVerdict, Severity


class GuardrailViolation(AltamiraBaseModel):
    violation_id: str = Field(min_length=1)
    rule: str = Field(min_length=1)
    field: str | None = None
    message: str = Field(min_length=1)
    severity: Severity


class GuardrailReport(AltamiraBaseModel):
    schema_version: Literal["1.0"] = "1.0"
    candidate_id: str = Field(min_length=1)
    verdict: GuardrailVerdict
    violations: list[GuardrailViolation] = Field(default_factory=list)
    repair_attempts: int = Field(ge=0, le=2)
    evaluated_at: datetime
    source_package_hash: Sha256Hex

    @model_validator(mode="after")
    def _verdict_matches_violations(self) -> GuardrailReport:
        has_error = any(v.severity == Severity.ERROR for v in self.violations)
        if self.verdict == GuardrailVerdict.EVIDENCE_VALIDATED and has_error:
            raise ValueError(
                "verdict EVIDENCE_VALIDATED no puede coexistir con violaciones de severidad ERROR"
            )
        if self.verdict == GuardrailVerdict.REJECTED and not has_error:
            raise ValueError("verdict REJECTED requiere al menos una violacion de severidad ERROR")
        return self
