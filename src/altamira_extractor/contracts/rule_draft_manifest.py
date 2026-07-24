"""Contrato tipado de `artifacts/08-rule-drafts/rule-draft-manifest.json`
(Prompt 12).

Mismo principio que `ContextDirectoryManifest` (Prompt 10b): `RuleDraft`/
`rule-draft.schema.json` no tienen ni ganan aqui campos de provenance
(hashes de prompt, proveedor, modelo, hash de la respuesta cruda) — ese
hueco se resuelve con un manifiesto separado del directorio, nunca
ensuciando el contrato de cada `RuleDraft` individual. Sin JSON Schema
propio (igual que `ContextDirectoryManifest`/`InvariantArtifact`):
contrato Python-only."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, RelativePath, Sha256Hex


class RuleDraftRecord(AltamiraBaseModel):
    candidate_id: str = Field(min_length=1)
    context_hash: Sha256Hex
    relative_filename: RelativePath
    rule_draft_hash: Sha256Hex
    writer_user_effective_hash: Sha256Hex
    response_hash: Sha256Hex


class RuleDraftDirectoryManifest(AltamiraBaseModel):
    """Contenedor persistido en
    artifacts/08-rule-drafts/rule-draft-manifest.json."""

    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    source_package_hash: Sha256Hex
    context_manifest_hash: Sha256Hex
    rule_draft_schema_hash: Sha256Hex
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    temperature: Literal[0] = 0
    writer_system_template_hash: Sha256Hex
    writer_user_template_hash: Sha256Hex
    records: list[RuleDraftRecord] = Field(default_factory=list)
    draft_count: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_records_ordered_and_unique(self) -> RuleDraftDirectoryManifest:
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
    def _check_draft_count_consistent(self) -> RuleDraftDirectoryManifest:
        if self.draft_count != len(self.records):
            raise ValueError(
                f"draft_count ({self.draft_count}) no coincide con records "
                f"reales ({len(self.records)})"
            )
        return self

    @model_validator(mode="after")
    def _check_warnings_sorted_and_unique(self) -> RuleDraftDirectoryManifest:
        if len(self.warnings) != len(set(self.warnings)):
            raise ValueError("warnings contiene duplicados")
        if self.warnings != sorted(self.warnings):
            raise ValueError("warnings no esta ordenado deterministicamente")
        return self
