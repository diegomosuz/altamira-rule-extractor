"""Contrato tipado de una arista del CPG reducido (03-dependencies.json).

DATA_DEPENDS_ON / CONTROL_DEPENDS_ON conectan siempre Paragraph con
Paragraph (docs/NEO4J_METAMODEL.md, seccion 'CPG reducido').
"""

from __future__ import annotations

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, RelativePath, Sha256Hex
from .enums import DependencyType


class ParagraphDependency(AltamiraBaseModel):
    dependency_type: DependencyType
    from_paragraph_id: str = Field(min_length=1)
    to_paragraph_id: str = Field(min_length=1)
    variables: list[str] = Field(default_factory=list)
    control_construct: str | None = None
    dependency_depth: int = Field(ge=1)
    confidence: float = Field(ge=0, le=1)
    source_file: RelativePath
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    source_package_hash: Sha256Hex

    @model_validator(mode="after")
    def _no_self_dependency(self) -> ParagraphDependency:
        if self.from_paragraph_id == self.to_paragraph_id:
            raise ValueError("un paragraph no puede depender de si mismo")
        return self
