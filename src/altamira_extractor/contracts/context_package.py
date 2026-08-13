"""Contrato tipado de ContextPackage — debe ser compatible con
schemas/context-package.schema.json (schema_version 2.0).

Refleja las siete dimensiones D1-D7 (docs/CONTEXT_PACKAGE_CONTRACT.md).
Donde el JSON Schema deja un hueco pero CLAUDE.md exige una regla de
negocio explicita, se agrega un validador puntual (documentado inline).

`ContextPackageCandidate.decision_id`/`ContextPackage.decision` (Fase
15B3-C2-B2): ambos `| None`, exclusivamente para representar un
CALCULATION incondicional (calculo aritmetico sin Decision envolvente
-- ver `contracts/candidate.py`). Nunca independientes entre si (ver
`_check_decision_presence_matches_candidate`); nunca se fabrica una
Decision sintetica para rellenar el campo cuando falta.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import Field, field_validator, model_serializer, model_validator

from .base import AltamiraBaseModel, RelativePath, Sha256Hex
from .enums import (
    ApplicabilityStatus,
    AttributionScope,
    BatchContextStatus,
    CandidateStatus,
    CompletenessStatus,
    InclusionReason,
    TableEffectOperation,
)


class ContextPackageCandidate(AltamiraBaseModel):
    candidate_id: str = Field(min_length=1)
    # str | None (Fase 15B3-C2-B2): None unicamente para un CALCULATION
    # incondicional (sin Decision envolvente) -- ver
    # `ContextPackage._check_decision_presence_matches_candidate`, que
    # exige que este campo y `ContextPackage.decision` esten ambos
    # presentes o ambos ausentes.
    decision_id: str | None = Field(default=None, min_length=1)
    detector_id: str = Field(min_length=1)
    detector_version: str = Field(min_length=1)
    detector_score: float = Field(ge=0, le=1)
    status: CandidateStatus = CandidateStatus.DETECTED_CANDIDATE


class ContextPackageOperation(AltamiraBaseModel):
    logical_name: str = Field(min_length=1)
    description: str | None = None


class ContextPackageScope(AltamiraBaseModel):
    country: str = Field(min_length=1)
    application: str = Field(min_length=1)
    operation: ContextPackageOperation
    program: str = Field(min_length=1)
    program_version: str = Field(min_length=1)
    paragraph: str = Field(min_length=1)
    source_file: RelativePath
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    source_package_hash: Sha256Hex


class CodeSliceEntry(AltamiraBaseModel):
    """`source_file`: `None` es legitimo (Fase 15B4-CANDIDATE-QUALITY-5A,
    Q2 lee `Paragraph.source_file`) -- mismo motivo que
    `EvidenceEntry.source_file`."""

    paragraph_id: str = Field(min_length=1)
    paragraph: str = Field(min_length=1)
    source_file: RelativePath | None = None
    source_text: str
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    inclusion_reason: InclusionReason
    evidence_ids: list[str] = Field(default_factory=list)


class ApplicableParameterRow(AltamiraBaseModel):
    """Fila con aplicabilidad resuelta y aprobada para figurar en el
    texto de la regla (CLAUDE.md, seccion Parametria).

    `parameter_entry_id` es la identidad primaria de la fila (nunca
    `row_hash`/`row_number`/una combinacion de valores): Prompt 7 permite
    dos ParameterEntry con `row_hash` y valores identicos pero
    `parameter_entry_id` distintos (incluye `row_number`), y ambas filas
    deben poder conservarse."""

    parameter_entry_id: str = Field(min_length=1)
    values: dict[str, Any] = Field(default_factory=dict)
    approved_for_rule_text: Literal[True] = True


class ContextParameterRow(AltamiraBaseModel):
    """Fila de contexto, no aprobada para redactarse como valor aplicable.
    Ver `ApplicableParameterRow` sobre `parameter_entry_id`."""

    parameter_entry_id: str = Field(min_length=1)
    values: dict[str, Any] = Field(default_factory=dict)
    approved_for_rule_text: Literal[False] = False


class ParameterTableContext(AltamiraBaseModel):
    name: str = Field(min_length=1)
    snapshot_date: date | None = None
    predicates: list[str] = Field(default_factory=list)
    resolved_predicates: list[str] = Field(default_factory=list)
    unresolved_predicates: list[str] = Field(default_factory=list)
    applicability_status: ApplicabilityStatus
    applicable_rows: list[ApplicableParameterRow] = Field(default_factory=list)
    context_rows: list[ContextParameterRow] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unresolved_cannot_have_applicable_rows(self) -> ParameterTableContext:
        if (
            self.applicability_status
            in (ApplicabilityStatus.UNRESOLVED, ApplicabilityStatus.NOT_APPLICABLE)
            and self.applicable_rows
        ):
            raise ValueError(
                "no se puede llamar applicable_rows a filas cuya aplicabilidad es "
                "UNRESOLVED o NOT_APPLICABLE (CLAUDE.md, seccion Parametria)"
            )
        return self

    @model_validator(mode="after")
    def _no_parameter_entry_id_in_both_lists(self) -> ParameterTableContext:
        applicable_ids = {row.parameter_entry_id for row in self.applicable_rows}
        context_ids = {row.parameter_entry_id for row in self.context_rows}
        overlap = applicable_ids & context_ids
        if overlap:
            raise ValueError(
                "un mismo parameter_entry_id no puede aparecer en applicable_rows y "
                f"context_rows a la vez: {sorted(overlap)}"
            )
        return self


class TransactionalTableRead(AltamiraBaseModel):
    name: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)


class DataContext(AltamiraBaseModel):
    parameter_tables: list[ParameterTableContext] = Field(default_factory=list)
    transactional_tables_read: list[TransactionalTableRead] = Field(default_factory=list)


class ContextPackageDecision(AltamiraBaseModel):
    expression: str = Field(min_length=1)
    normalized_expression: str = Field(min_length=1)
    operands: list[str] = Field(default_factory=list)
    # str | None: StatementKind.IF/EVALUATE son construcciones tecnicas,
    # no evidencia de un tipo funcional de regla — ninguna etapa deriva
    # o inventa este valor (ver docstring de contracts/candidate.py).
    rule_type: str | None = None
    outcome_code: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class ReturnCodeEffect(AltamiraBaseModel):
    code: str = Field(min_length=1)
    approved_for_rule_text: bool
    evidence_ids: list[str] = Field(default_factory=list)


class TableEffect(AltamiraBaseModel):
    table: str = Field(min_length=1)
    operation: TableEffectOperation
    attribution_scope: AttributionScope
    approved_for_rule_text: bool
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _program_context_never_approved(self) -> TableEffect:
        is_program_context = self.attribution_scope == AttributionScope.PROGRAM_CONTEXT
        if is_program_context and self.approved_for_rule_text:
            raise ValueError(
                "un efecto PROGRAM_CONTEXT no puede tener approved_for_rule_text=true: "
                "solo DIRECT y DEPENDENCY_SLICE pueden aprobarse para redaccion "
                "(docs/METAMODEL_ALIGNMENT.md, punto 8)"
            )
        return self


class Effects(AltamiraBaseModel):
    return_codes: list[ReturnCodeEffect] = Field(default_factory=list)
    table_effects: list[TableEffect] = Field(default_factory=list)


class BatchContext(AltamiraBaseModel):
    status: BatchContextStatus
    downstream_jobs: list[dict[str, Any]] = Field(default_factory=list)


class DomainGlossaryEntry(AltamiraBaseModel):
    """`data_item_id` es identidad, no `technical_name`/`semantic_tag`:
    dos DataItems distintos pueden compartir technical_name en paragraphs
    o programas distintos del mismo slice. Clave de deduplicacion:
    `(domain_term_id, data_item_id)`."""

    data_item_id: str = Field(min_length=1)
    technical_name: str = Field(min_length=1)
    semantic_tag: str = Field(min_length=1)
    domain_term_id: str = Field(min_length=1)
    functional_name: str = Field(min_length=1)
    definition: str = Field(min_length=1)
    entity_type: str = Field(min_length=1)
    source_kind: str = Field(min_length=1)
    authoritative_source: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)


class EvidenceEntry(AltamiraBaseModel):
    """`source_file`: `None` es un estado legitimo (Fase
    15B4-CANDIDATE-QUALITY-5A) -- ocurre cuando la evidencia proviene de
    un `RuleCandidate`/Paragraph con `location_kind` distinto de `EXACT`
    (hoy, programas con `COPY`; ver `contracts/candidate.py`,
    docstring de `RuleCandidate.source_file`). Nunca se sustituye por un
    valor inventado."""

    evidence_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    source_file: RelativePath | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    source_package_hash: Sha256Hex
    details: dict[str, Any] | None = None

    @model_serializer(mode="wrap")
    def _omit_null_details(self, handler: Any) -> dict[str, Any]:
        # El schema declara "details" como objeto opcional (sin admitir
        # null): a diferencia de line_start/line_end, que si son
        # nullable, esta clave debe omitirse en vez de serializarse null.
        data: dict[str, Any] = handler(self)
        if data.get("details") is None:
            data.pop("details", None)
        return data


class Completeness(AltamiraBaseModel):
    D1: CompletenessStatus
    D2: CompletenessStatus
    D3: CompletenessStatus
    D4: CompletenessStatus
    D5: CompletenessStatus
    D6: CompletenessStatus
    D7: CompletenessStatus


class ContextPackage(AltamiraBaseModel):
    schema_version: Literal["2.0"] = "2.0"
    candidate: ContextPackageCandidate
    scope: ContextPackageScope
    code_slice: list[CodeSliceEntry] = Field(min_length=1)
    data_context: DataContext
    # ContextPackageDecision | None (Fase 15B3-C2-B2): None unicamente
    # cuando `candidate.decision_id` tambien es None (CALCULATION
    # incondicional) -- ver `_check_decision_presence_matches_candidate`.
    # Nunca se fabrica una Decision sintetica para rellenar este campo.
    decision: ContextPackageDecision | None = None
    effects: Effects
    batch_context: BatchContext
    domain_glossary: list[DomainGlossaryEntry] = Field(default_factory=list)
    evidence: list[EvidenceEntry] = Field(min_length=1)
    completeness: Completeness

    @field_validator("evidence")
    @classmethod
    def _at_least_one_evidence(cls, evidence: list[EvidenceEntry]) -> list[EvidenceEntry]:
        # Redundante con Field(min_length=1); documenta explicitamente la regla
        # del contrato: "Cada afirmacion tecnica debe poder referenciar un
        # evidence_id" (docs/CONTEXT_PACKAGE_CONTRACT.md).
        if not evidence:
            raise ValueError("un ContextPackage debe tener al menos una entrada de evidencia")
        return evidence

    @model_validator(mode="after")
    def _check_decision_presence_matches_candidate(self) -> ContextPackage:
        if (self.candidate.decision_id is None) != (self.decision is None):
            raise ValueError(
                "ContextPackage: 'decision' debe estar presente si y solo si "
                "candidate.decision_id esta presente -- un CALCULATION incondicional "
                "es el unico caso legitimo con ambos ausentes, nunca un estado mixto"
            )
        return self
