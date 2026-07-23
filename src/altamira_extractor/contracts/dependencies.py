"""Contrato tipado del CPG reducido (03-dependencies.json).

`DATA_DEPENDS_ON` / `CONTROL_DEPENDS_ON` conectan siempre Paragraph con
Paragraph (docs/NEO4J_METAMODEL.md, seccion 'CPG reducido'). La direccion
es "origen que influye -> parrafo dependiente": `from_paragraph_id` es
quien escribe el dato o transfiere el control; `to_paragraph_id` es quien
lee ese dato o recibe el control.

La ubicacion (`source_file`/`line_start`/`line_end`/`location_kind`) del
propio `ParagraphDependency` es nullable siguiendo el mismo patron que
`CanonicalStatement`/`CanonicalParagraph`: no toda evidencia proviene de
una region con archivo fisico atribuible (COPY expandido ->
PREPROCESSED_STREAM, o sin ubicacion alguna -> UNKNOWN). Nunca se inventa
un valor para completar estos campos.

Como una dependencia DATA involucra dos statements (quien escribe, quien
lee) y una dependencia CONTROL puede requerir contexto de anidamiento
(branch_kind/branch_condition/parent_statement_id) que no cabe en un
unico conjunto de campos planos, la evidencia detallada vive en
`evidence: list[DependencyEvidence]`, tipada explicitamente (nunca
`dict[str, Any]`).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, RelativePath, Sha256Hex
from .enums import (
    BranchKind,
    DependencyEvidenceRole,
    DependencyType,
    LocationKind,
    StatementKind,
)


def _check_location_coherence(
    *,
    source_file: str | None,
    line_start: int | None,
    line_end: int | None,
    location_kind: LocationKind,
    context_label: str,
) -> None:
    if line_start is not None and line_end is not None and line_end < line_start:
        raise ValueError(f"{context_label}: line_end no puede ser anterior a line_start")
    if location_kind == LocationKind.EXACT:
        if source_file is None or line_start is None or line_end is None:
            raise ValueError(
                f"{context_label}: location_kind=EXACT requiere source_file, line_start y line_end"
            )
    elif location_kind == LocationKind.PREPROCESSED_STREAM:
        if source_file is not None:
            raise ValueError(
                f"{context_label}: location_kind=PREPROCESSED_STREAM no puede declarar source_file"
            )
    elif location_kind == LocationKind.UNKNOWN:
        if source_file is not None or line_start is not None or line_end is not None:
            raise ValueError(
                f"{context_label}: location_kind=UNKNOWN no puede declarar source_file ni lineas"
            )


class DependencyEvidence(AltamiraBaseModel):
    """Un CanonicalStatement (o su ubicacion) usado como evidencia de una
    ParagraphDependency. `role` distingue si es el statement que escribe
    (WRITER), el que lee (READER), o el que transfiere control (CONTROL)."""

    role: DependencyEvidenceRole
    statement_id: str = Field(min_length=1)
    statement_kind: StatementKind
    source_text: str
    source_file: RelativePath | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    location_kind: LocationKind
    parent_statement_id: str | None = None
    branch_kind: BranchKind | None = None
    branch_condition: str | None = None
    original_variable: str | None = None
    resolved_qualified_name: str | None = None
    original_target: str | None = None

    @model_validator(mode="after")
    def _check_location(self) -> DependencyEvidence:
        _check_location_coherence(
            source_file=self.source_file,
            line_start=self.line_start,
            line_end=self.line_end,
            location_kind=self.location_kind,
            context_label=(
                f"DependencyEvidence(role={self.role.value}, statement_id={self.statement_id!r})"
            ),
        )
        return self


class ParagraphDependency(AltamiraBaseModel):
    dependency_type: DependencyType
    from_paragraph_id: str = Field(min_length=1)
    to_paragraph_id: str = Field(min_length=1)
    variables: list[str] = Field(default_factory=list)
    control_construct: str | None = None
    dependency_depth: int = Field(ge=1)
    confidence: float = Field(ge=0, le=1)
    # Regla determinística que produjo esta arista (ej.
    # "EXPLICIT_CONTROL_TARGET", "PARAGRAPH_WRITE_READ_QUALIFIED_MATCH");
    # ver dependency_builder.py para la politica completa.
    derivation_rule: str = Field(min_length=1)
    source_file: RelativePath | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    location_kind: LocationKind
    source_package_hash: Sha256Hex
    evidence: list[DependencyEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_self_dependency(self) -> ParagraphDependency:
        if self.from_paragraph_id == self.to_paragraph_id:
            raise ValueError("un paragraph no puede depender de si mismo")
        return self

    @model_validator(mode="after")
    def _check_location(self) -> ParagraphDependency:
        _check_location_coherence(
            source_file=self.source_file,
            line_start=self.line_start,
            line_end=self.line_end,
            location_kind=self.location_kind,
            context_label="ParagraphDependency",
        )
        return self

    @model_validator(mode="after")
    def _check_evidence_roles(self) -> ParagraphDependency:
        roles = {item.role for item in self.evidence}
        if self.dependency_type == DependencyType.DATA_DEPENDS_ON:
            if DependencyEvidenceRole.WRITER not in roles:
                raise ValueError("DATA_DEPENDS_ON requiere al menos una evidencia WRITER")
            if DependencyEvidenceRole.READER not in roles:
                raise ValueError("DATA_DEPENDS_ON requiere al menos una evidencia READER")
        elif self.dependency_type == DependencyType.CONTROL_DEPENDS_ON:
            if DependencyEvidenceRole.CONTROL not in roles:
                raise ValueError("CONTROL_DEPENDS_ON requiere al menos una evidencia CONTROL")
        return self


def _dependency_semantic_key(dep: ParagraphDependency) -> tuple[str, str, str, str]:
    """Clave de deduplicacion: DATA se consolida por (tipo, from, to);
    CONTROL ademas distingue por control_construct (un GO_TO y un PERFORM
    hacia el mismo destino son dependencias distintas)."""
    if dep.dependency_type == DependencyType.CONTROL_DEPENDS_ON:
        return (dep.dependency_type.value, dep.from_paragraph_id, dep.to_paragraph_id,
                dep.control_construct or "")
    return (dep.dependency_type.value, dep.from_paragraph_id, dep.to_paragraph_id, "")


def _dependency_sort_key(
    dep: ParagraphDependency,
) -> tuple[str, str, str, str, tuple[str, ...]]:
    return (
        dep.dependency_type.value,
        dep.from_paragraph_id,
        dep.to_paragraph_id,
        dep.control_construct or "",
        tuple(dep.variables),
    )


class DependencyArtifact(AltamiraBaseModel):
    """Contenedor persistido en artifacts/03-dependencies.json."""

    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    source_package_hash: Sha256Hex
    dependencies: list[ParagraphDependency] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_dependency_package_hash(self) -> DependencyArtifact:
        for dep in self.dependencies:
            if dep.source_package_hash != self.source_package_hash:
                raise ValueError(
                    f"ParagraphDependency {dep.from_paragraph_id!r}->{dep.to_paragraph_id!r}: "
                    "source_package_hash distinto al del artefacto"
                )
        return self

    @model_validator(mode="after")
    def _check_dependencies_sorted_and_unique(self) -> DependencyArtifact:
        seen: set[tuple[str, str, str, str]] = set()
        for dep in self.dependencies:
            key = _dependency_semantic_key(dep)
            if key in seen:
                raise ValueError(f"dependencia duplicada segun clave semantica: {key}")
            seen.add(key)
        expected_order = sorted(self.dependencies, key=_dependency_sort_key)
        if expected_order != list(self.dependencies):
            raise ValueError("dependencies no esta en el orden deterministico esperado")
        return self

    @model_validator(mode="after")
    def _check_warnings_sorted_and_unique(self) -> DependencyArtifact:
        if len(self.warnings) != len(set(self.warnings)):
            raise ValueError("warnings contiene duplicados")
        if sorted(self.warnings) != list(self.warnings):
            raise ValueError("warnings no esta ordenado deterministicamente")
        return self
