"""Contrato tipado del artefacto artifacts/05-invariants.json (Prompt 9).

Reutiliza `Severity` (ya declarado para "invariantes, guardrail, etc.")
en vez de duplicar un enum `InvariantSeverity` equivalente.

Determinístico por diseño: no incluye timestamp (`StageExecution` ya
registra inicio/fin/duracion); el mismo `SemanticGraph` cargado y el mismo
resultado de `invariants.cypher` deben producir siempre el mismo JSON.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, Sha256Hex
from .enums import Severity


class InvariantViolation(AltamiraBaseModel):
    """Una fila real devuelta por `queries/v1/invariants.cypher`."""

    code: str = Field(min_length=1)
    severity: Severity
    entity_id: str = Field(min_length=1)
    message: str = Field(min_length=1)


class InvariantArtifact(AltamiraBaseModel):
    """Contenedor persistido en artifacts/05-invariants.json."""

    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    source_package_hash: Sha256Hex
    semantic_graph_hash: Sha256Hex
    invariants_query_hash: Sha256Hex
    neo4j_database: str = Field(min_length=1)
    neo4j_server_version: str = Field(min_length=1)
    node_count: int = Field(ge=0)
    relationship_count: int = Field(ge=0)
    violations: list[InvariantViolation] = Field(default_factory=list)
    error_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    graph_validated: bool
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_violations_sorted_and_unique(self) -> InvariantArtifact:
        keys = [
            (violation.code, violation.entity_id, violation.severity.value, violation.message)
            for violation in self.violations
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("violations contiene duplicados exactos")
        if keys != sorted(keys):
            raise ValueError("violations no esta ordenado deterministicamente")
        return self

    @model_validator(mode="after")
    def _check_counts_consistent(self) -> InvariantArtifact:
        actual_errors = sum(1 for v in self.violations if v.severity == Severity.ERROR)
        actual_warnings = sum(1 for v in self.violations if v.severity == Severity.WARNING)
        if actual_errors != self.error_count:
            raise ValueError(
                f"error_count ({self.error_count}) no coincide con las violations ERROR "
                f"reales ({actual_errors})"
            )
        if actual_warnings != self.warning_count:
            raise ValueError(
                f"warning_count ({self.warning_count}) no coincide con las violations "
                f"WARNING reales ({actual_warnings})"
            )
        return self

    @model_validator(mode="after")
    def _check_graph_validated_matches_error_count(self) -> InvariantArtifact:
        expected = self.error_count == 0
        if self.graph_validated != expected:
            raise ValueError(
                f"graph_validated ({self.graph_validated}) debe ser exactamente "
                f"error_count == 0 ({expected})"
            )
        return self

    @model_validator(mode="after")
    def _check_warnings_sorted_and_unique(self) -> InvariantArtifact:
        if len(self.warnings) != len(set(self.warnings)):
            raise ValueError("warnings contiene duplicados")
        if self.warnings != sorted(self.warnings):
            raise ValueError("warnings no esta ordenado deterministicamente")
        return self
