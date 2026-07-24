"""Contrato tipado de `artifacts/10-rules/rules-manifest.json` (Prompt 13a).

Mismo principio que `GuardrailDirectoryManifest`/`RuleDraftDirectoryManifest`/
`ContextDirectoryManifest`: contrato Python-only, sin JSON Schema propio.
No valida por si solo la correspondencia 1:1 con `GuardrailDirectoryManifest`
ni la convencion de nombre de archivo (`sha256(candidate_id)+".md"`): esas
son verificaciones de orquestacion en tiempo de ejecucion
(`pipeline/rules_rendered_stage.py`), no invariantes de forma de un
`RulesDirectoryManifest` aislado."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, RelativePath, Sha256Hex


class RulesRecord(AltamiraBaseModel):
    candidate_id: str = Field(min_length=1)
    source_guardrail_artifact_hash: Sha256Hex
    final_rule_draft_hash: Sha256Hex
    relative_filename: RelativePath
    markdown_hash: Sha256Hex


class RulesDirectoryManifest(AltamiraBaseModel):
    """Contenedor persistido en artifacts/10-rules/rules-manifest.json."""

    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    source_package_hash: Sha256Hex
    guardrail_manifest_hash: Sha256Hex
    renderer_version: str = Field(min_length=1)
    records: list[RulesRecord] = Field(default_factory=list)
    rule_count: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_records_ordered_and_unique(self) -> RulesDirectoryManifest:
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
    def _check_rule_count_consistent(self) -> RulesDirectoryManifest:
        if self.rule_count != len(self.records):
            raise ValueError(
                f"rule_count ({self.rule_count}) no coincide con records reales "
                f"({len(self.records)})"
            )
        return self

    @model_validator(mode="after")
    def _check_warnings_sorted_and_unique(self) -> RulesDirectoryManifest:
        if len(self.warnings) != len(set(self.warnings)):
            raise ValueError("warnings contiene duplicados")
        if self.warnings != sorted(self.warnings):
            raise ValueError("warnings no esta ordenado deterministicamente")
        return self
