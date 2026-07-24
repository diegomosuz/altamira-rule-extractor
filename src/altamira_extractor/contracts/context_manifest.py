"""Contrato tipado de `artifacts/07-context/context-manifest.json` (Prompt 10b).

`ContextPackage`/`context-package.schema.json` no tienen (ni ganan aqui)
campos de hash de query: ese hueco de provenance/deteccion-de-cambios se
resuelve con un manifiesto separado del directorio, no ensuciando el
contrato de cada `ContextPackage` individual. Sin JSON Schema propio
(igual que `InvariantArtifact`/`CandidateArtifact`): contrato Python-only.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, RelativePath, Sha256Hex

_EXPECTED_LOGICAL_QUERIES = (
    "Q1",
    "Q2",
    "Q3A",
    "Q3B",
    "Q4",
    "Q5A",
    "Q5B",
    "Q6",
    "Q7",
)


class QueryRecord(AltamiraBaseModel):
    """Evidencia de exactamente que texto de query se ejecuto realmente:
    `template_hash` es el archivo `.cypher` tal como esta versionado;
    `effective_query_hash` es el texto despues de sustituir el placeholder
    de profundidad (identicos cuando la query no tiene placeholder)."""

    logical_query: Literal["Q1", "Q2", "Q3A", "Q3B", "Q4", "Q5A", "Q5B", "Q6", "Q7"]
    relative_path: RelativePath
    template_hash: Sha256Hex
    effective_query_hash: Sha256Hex


class ContextRecord(AltamiraBaseModel):
    candidate_id: str = Field(min_length=1)
    paragraph_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    relative_filename: RelativePath
    context_hash: Sha256Hex


class ContextDirectoryManifest(AltamiraBaseModel):
    """Contenedor persistido en artifacts/07-context/context-manifest.json."""

    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    source_package_hash: Sha256Hex
    semantic_graph_hash: Sha256Hex
    candidate_artifact_hash: Sha256Hex
    q0_query_hash: Sha256Hex
    invariants_query_hash: Sha256Hex
    context_schema_hash: Sha256Hex
    dependency_depth: int = Field(ge=1, le=10)
    max_code_slice_paragraphs: int = Field(ge=1, le=2000)
    max_transactional_tables: int = Field(ge=1, le=1000)
    max_parameter_entries_per_context: int = Field(ge=1, le=10000)
    query_records: list[QueryRecord] = Field(default_factory=list)
    context_records: list[ContextRecord] = Field(default_factory=list)
    context_count: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_query_records_complete_ordered_unique(self) -> ContextDirectoryManifest:
        logical_names = [record.logical_query for record in self.query_records]
        if len(logical_names) != len(set(logical_names)):
            raise ValueError("query_records contiene logical_query duplicado")
        if set(logical_names) != set(_EXPECTED_LOGICAL_QUERIES):
            raise ValueError(
                f"query_records debe contener exactamente {_EXPECTED_LOGICAL_QUERIES}; "
                f"se encontro {sorted(logical_names)}"
            )
        if logical_names != sorted(logical_names):
            raise ValueError("query_records no esta ordenado deterministicamente")
        return self

    @model_validator(mode="after")
    def _check_context_records_ordered_and_unique(self) -> ContextDirectoryManifest:
        candidate_ids = [record.candidate_id for record in self.context_records]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("context_records contiene candidate_id duplicado")
        if candidate_ids != sorted(candidate_ids):
            raise ValueError("context_records no esta ordenado por candidate_id")
        filenames = [record.relative_filename for record in self.context_records]
        if len(filenames) != len(set(filenames)):
            raise ValueError("context_records contiene relative_filename duplicado")
        return self

    @model_validator(mode="after")
    def _check_context_count_consistent(self) -> ContextDirectoryManifest:
        if self.context_count != len(self.context_records):
            raise ValueError(
                f"context_count ({self.context_count}) no coincide con context_records "
                f"reales ({len(self.context_records)})"
            )
        return self

    @model_validator(mode="after")
    def _check_warnings_sorted_and_unique(self) -> ContextDirectoryManifest:
        if len(self.warnings) != len(set(self.warnings)):
            raise ValueError("warnings contiene duplicados")
        if self.warnings != sorted(self.warnings):
            raise ValueError("warnings no esta ordenado deterministicamente")
        return self
