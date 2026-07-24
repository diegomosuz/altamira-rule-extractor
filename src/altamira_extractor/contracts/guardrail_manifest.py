"""Contrato tipado de `artifacts/09-guardrails/guardrail-manifest.json`
(Prompt 12). Mismo patron Python-only que `ContextDirectoryManifest`/
`RuleDraftDirectoryManifest`: sin JSON Schema propio."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, RelativePath, Sha256Hex
from .enums import EvidenceValidationStatus


class GuardrailRecord(AltamiraBaseModel):
    candidate_id: str = Field(min_length=1)
    relative_filename: RelativePath
    initial_rule_draft_hash: Sha256Hex
    final_rule_draft_hash: Sha256Hex
    guardrail_artifact_hash: Sha256Hex
    final_evidence_validation_status: EvidenceValidationStatus
    repair_attempts_used: int = Field(ge=0)
    repair_response_hashes: list[Sha256Hex] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_status_is_validated(self) -> GuardrailRecord:
        # El directorio 09-guardrails/ solo existe como salida canonica
        # cuando TODOS los candidatos quedaron EVIDENCE_VALIDATED
        # (GUARDRAILS_APPLIED es fail-fast: un REJECTED nunca se
        # promueve, ver guardrails_applied_stage.py).
        if self.final_evidence_validation_status != EvidenceValidationStatus.EVIDENCE_VALIDATED:
            raise ValueError(
                "un GuardrailRecord promovido solo puede tener "
                "final_evidence_validation_status=EVIDENCE_VALIDATED"
            )
        return self

    @model_validator(mode="after")
    def _check_repair_attempts_consistent(self) -> GuardrailRecord:
        if self.repair_attempts_used != len(self.repair_response_hashes):
            raise ValueError(
                "repair_attempts_used no coincide con len(repair_response_hashes)"
            )
        return self


class GuardrailDirectoryManifest(AltamiraBaseModel):
    """Contenedor persistido en
    artifacts/09-guardrails/guardrail-manifest.json."""

    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    source_package_hash: Sha256Hex
    rule_draft_manifest_hash: Sha256Hex
    rule_draft_schema_hash: Sha256Hex
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    temperature: Literal[0] = 0
    llm_repair_attempts: int = Field(ge=0, le=2)
    guardrail_version: str = Field(min_length=1)
    repair_system_template_hash: Sha256Hex
    repair_user_template_hash: Sha256Hex
    records: list[GuardrailRecord] = Field(default_factory=list)
    guardrail_count: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_records_ordered_and_unique(self) -> GuardrailDirectoryManifest:
        candidate_ids = [record.candidate_id for record in self.records]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("records contiene candidate_id duplicado")
        if candidate_ids != sorted(candidate_ids):
            raise ValueError("records no esta ordenado por candidate_id")
        filenames = [record.relative_filename for record in self.records]
        if len(filenames) != len(set(filenames)):
            raise ValueError("records contiene relative_filename duplicado")
        return self

    @model_validator(mode="after")
    def _check_guardrail_count_consistent(self) -> GuardrailDirectoryManifest:
        if self.guardrail_count != len(self.records):
            raise ValueError(
                f"guardrail_count ({self.guardrail_count}) no coincide con records "
                f"reales ({len(self.records)})"
            )
        return self

    @model_validator(mode="after")
    def _check_warnings_sorted_and_unique(self) -> GuardrailDirectoryManifest:
        if len(self.warnings) != len(set(self.warnings)):
            raise ValueError("warnings contiene duplicados")
        if self.warnings != sorted(self.warnings):
            raise ValueError("warnings no esta ordenado deterministicamente")
        return self
