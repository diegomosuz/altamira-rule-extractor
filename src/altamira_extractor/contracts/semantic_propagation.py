"""Contrato tipado de propagacion limitada de constantes y copias (Fase 4
de la ampliacion semantica, `feat/constant-copy-propagation`).

Diagnostico, NO contractual: persiste en `<run_dir>/diagnostics/
semantic-propagation.json`, fuera de `artifacts/01-10` -- mismo patron que
`contracts/semantic_coverage.py` (Fase 1) y `contracts/semantic_effects.py`
(Fase 2/3): un artefacto adyacente, opcional, irrelevante para que un run
llegue a COMPLETED, ignorado por toda etapa V1.

Modelo DEPENDIENTE de `SemanticEffectsArtifact` (Fase 2/3), nunca al reves:
cada `PropagatedValueFact`/`PropagationBarrier` deriva su procedencia de
uno o mas `SemanticEffect` ya calculados (correlacionados via
`statement_id`), pero jamas modifica ni reinterpreta el `SemanticEffect`
original -- `SET_CONDITION_TRUE`/`COPY_VALUE`/etc. conservan exactamente
la semantica que `semantic_effects_analyzer.py` ya les asigno. Este modulo
solo AGREGA una capa de conclusiones (\"WS-COD-RETORNO puede demostrarse
igual a '0005'\") por encima de efectos que ya existen de forma
independiente.

Alcance intraparrafo (Fase 4, docs/SEMANTIC_PROPAGATION.md): nunca cruza
un limite de paragraph, PERFORM, GO TO, CALL/CICS, ni evalua expresiones
aritmeticas o SQL. La ausencia de prueba suficiente siempre produce
UNRESOLVED_COPY/INVALIDATED_VALUE/BLOCKED_PROPAGATION -- nunca una
inferencia optimista."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, RelativePath, Sha256Hex
from .enums import BranchKind, StatementKind
from .semantic_coverage import SemanticSupportStatus
from .semantic_effects import SemanticEffectKind


class PropagationFactKind(StrEnum):
    """Naturaleza de un `PropagatedValueFact`."""

    DIRECT_LITERAL = "DIRECT_LITERAL"
    PROPAGATED_LITERAL = "PROPAGATED_LITERAL"
    CONDITION_LITERAL = "CONDITION_LITERAL"
    UNRESOLVED_COPY = "UNRESOLVED_COPY"
    INVALIDATED_VALUE = "INVALIDATED_VALUE"
    BLOCKED_PROPAGATION = "BLOCKED_PROPAGATION"


class PropagationBarrierReason(StrEnum):
    """Motivo por el que la propagacion se detiene o invalida el entorno
    en un punto dado. Solo se declaran las categorias efectivamente
    producidas por `semantic_propagation_analyzer.py`."""

    CONTROL_FLOW_BOUNDARY = "CONTROL_FLOW_BOUNDARY"
    AMBIGUOUS_SYMBOL = "AMBIGUOUS_SYMBOL"
    UNKNOWN_SIDE_EFFECT = "UNKNOWN_SIDE_EFFECT"
    UNSUPPORTED_STATEMENT = "UNSUPPORTED_STATEMENT"
    SQL_HOST_DIRECTION_UNKNOWN = "SQL_HOST_DIRECTION_UNKNOWN"
    COMPUTED_VALUE = "COMPUTED_VALUE"
    MULTIPLE_CONDITION_VALUES = "MULTIPLE_CONDITION_VALUES"
    CONDITION_VALUE_RANGE = "CONDITION_VALUE_RANGE"
    CONDITION_FALSE_VALUE_UNDETERMINED = "CONDITION_FALSE_VALUE_UNDETERMINED"
    CROSS_PARAGRAPH_BOUNDARY = "CROSS_PARAGRAPH_BOUNDARY"
    LOOP_OR_PERFORM_BOUNDARY = "LOOP_OR_PERFORM_BOUNDARY"


class PropagationSourceReference(AltamiraBaseModel):
    """Procedencia de un `PropagatedValueFact`/`PropagationBarrier` hasta
    el `CanonicalStatement` que lo origino. Nunca incluye `source_text`
    (mismo principio que `SemanticEffectSourceReference`)."""

    program: str = Field(min_length=1)
    paragraph: str = Field(min_length=1)
    statement_id: str = Field(min_length=1)
    statement_kind: StatementKind
    source_file: RelativePath | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    parent_statement_id: str | None = None
    branch_kind: BranchKind | None = None

    @model_validator(mode="after")
    def _check_line_order(self) -> PropagationSourceReference:
        if self.line_start is not None and self.line_end is not None:
            if self.line_end < self.line_start:
                raise ValueError("line_end no puede ser anterior a line_start")
        return self


class PropagationStep(AltamiraBaseModel):
    """Un unico eslabon de la cadena de procedencia de un
    `PropagatedValueFact`. `operation` es el `SemanticEffectKind` del
    `SemanticEffect` que produjo este paso (p. ej. `ASSIGN_LITERAL` para
    el primer eslabon de una cadena, `COPY_VALUE` para cada copia
    intermedia): nunca un texto libre, para que la cadena sea
    verificable contra `SemanticEffectsArtifact` sin reinterpretacion."""

    statement_id: str = Field(min_length=1)
    effect_id: str = Field(min_length=1)
    operation: SemanticEffectKind
    source_variable: str | None = None
    target_variable: str = Field(min_length=1)
    literal: str | None = None


def _ordered_unique_strings(values: Iterable[str]) -> list[str]:
    return sorted(set(values))


class PropagatedValueFact(AltamiraBaseModel):
    """Una conclusion demostrada sobre el valor de una variable en un
    punto del programa. `region_id` ancla el hecho a la region de flujo
    de control (Fase 6) donde se demostro -- nunca implica que el valor
    sea valido fuera de esa region."""

    fact_id: str = Field(min_length=1)
    fact_kind: PropagationFactKind
    program: str = Field(min_length=1)
    paragraph: str = Field(min_length=1)
    region_id: str = Field(min_length=1)
    target_variable: str = Field(min_length=1)
    target_qualified_name: str | None = None
    literal: str | None = None
    source_variable: str | None = None
    source_qualified_name: str | None = None
    source_reference: PropagationSourceReference
    derivation_steps: list[PropagationStep] = Field(default_factory=list)
    derivation_depth: int = Field(ge=0)
    support_status: SemanticSupportStatus
    diagnostic_codes: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _check_diagnostic_codes_sorted_and_unique(self) -> PropagatedValueFact:
        if self.diagnostic_codes != _ordered_unique_strings(self.diagnostic_codes):
            raise ValueError(
                f"PropagatedValueFact({self.fact_id!r}): diagnostic_codes debe estar "
                "ordenado alfabeticamente y sin duplicados"
            )
        return self

    @model_validator(mode="after")
    def _check_derivation_depth_matches_steps(self) -> PropagatedValueFact:
        if self.derivation_depth != len(self.derivation_steps):
            raise ValueError(
                f"PropagatedValueFact({self.fact_id!r}): derivation_depth "
                f"({self.derivation_depth}) != len(derivation_steps) "
                f"({len(self.derivation_steps)})"
            )
        return self

    @model_validator(mode="after")
    def _check_kind_specific_invariants(self) -> PropagatedValueFact:
        label = f"PropagatedValueFact({self.fact_id!r}, kind={self.fact_kind.value})"
        if self.fact_kind == PropagationFactKind.DIRECT_LITERAL:
            if self.literal is None:
                raise ValueError(f"{label}: DIRECT_LITERAL exige literal")
        elif self.fact_kind == PropagationFactKind.PROPAGATED_LITERAL:
            if self.literal is None:
                raise ValueError(f"{label}: PROPAGATED_LITERAL exige literal")
            if len(self.derivation_steps) < 2:
                raise ValueError(
                    f"{label}: PROPAGATED_LITERAL exige al menos dos pasos de procedencia"
                )
        elif self.fact_kind == PropagationFactKind.CONDITION_LITERAL:
            if self.literal is None:
                raise ValueError(f"{label}: CONDITION_LITERAL exige literal")
            has_condition_step = any(
                step.operation == SemanticEffectKind.SET_CONDITION_TRUE
                for step in self.derivation_steps
            )
            if not has_condition_step:
                raise ValueError(
                    f"{label}: CONDITION_LITERAL exige al menos un derivation_step con "
                    "operation=SET_CONDITION_TRUE"
                )
        elif self.fact_kind == PropagationFactKind.UNRESOLVED_COPY:
            if self.source_variable is None:
                raise ValueError(f"{label}: UNRESOLVED_COPY exige source_variable")
            if self.literal is not None:
                raise ValueError(f"{label}: UNRESOLVED_COPY no puede afirmar literal")
        elif self.fact_kind == PropagationFactKind.INVALIDATED_VALUE:
            if self.literal is not None:
                raise ValueError(f"{label}: INVALIDATED_VALUE no puede afirmar literal actual")
        elif self.fact_kind == PropagationFactKind.BLOCKED_PROPAGATION:
            if self.literal is not None:
                raise ValueError(f"{label}: BLOCKED_PROPAGATION no puede afirmar literal")
        return self


class PropagationBarrier(AltamiraBaseModel):
    """Punto donde la propagacion se detiene deliberadamente: nunca es un
    error del analizador, es la representacion explicita de un limite
    conservador (Fase 6/7)."""

    barrier_id: str = Field(min_length=1)
    program: str = Field(min_length=1)
    paragraph: str = Field(min_length=1)
    region_id: str = Field(min_length=1)
    source_reference: PropagationSourceReference
    reason: PropagationBarrierReason
    affected_variables: list[str] = Field(default_factory=list)
    clears_entire_environment: bool
    diagnostic_code: str = Field(min_length=1)
    explanation: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _check_affected_variables_sorted_and_unique(self) -> PropagationBarrier:
        if self.affected_variables != _ordered_unique_strings(self.affected_variables):
            raise ValueError(
                f"PropagationBarrier({self.barrier_id!r}): affected_variables debe estar "
                "ordenado alfabeticamente y sin duplicados"
            )
        return self


def _facts_sort_key(fact: PropagatedValueFact) -> str:
    return fact.fact_id


def _barriers_sort_key(barrier: PropagationBarrier) -> str:
    return barrier.barrier_id


class ProgramSemanticPropagation(AltamiraBaseModel):
    program: str = Field(min_length=1)
    paragraph_count: int = Field(ge=0)
    fact_count: int = Field(ge=0)
    barrier_count: int = Field(ge=0)
    facts: list[PropagatedValueFact] = Field(default_factory=list)
    barriers: list[PropagationBarrier] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_counts(self) -> ProgramSemanticPropagation:
        if self.fact_count != len(self.facts):
            raise ValueError(
                f"ProgramSemanticPropagation({self.program!r}): fact_count "
                f"({self.fact_count}) != len(facts) ({len(self.facts)})"
            )
        if self.barrier_count != len(self.barriers):
            raise ValueError(
                f"ProgramSemanticPropagation({self.program!r}): barrier_count "
                f"({self.barrier_count}) != len(barriers) ({len(self.barriers)})"
            )
        return self

    @model_validator(mode="after")
    def _check_facts_sorted_and_unique(self) -> ProgramSemanticPropagation:
        ids = [fact.fact_id for fact in self.facts]
        if len(ids) != len(set(ids)):
            raise ValueError(
                f"ProgramSemanticPropagation({self.program!r}): facts contiene fact_id duplicado"
            )
        if ids != sorted(ids):
            raise ValueError(
                f"ProgramSemanticPropagation({self.program!r}): facts no esta ordenado "
                "deterministicamente"
            )
        return self

    @model_validator(mode="after")
    def _check_barriers_sorted_and_unique(self) -> ProgramSemanticPropagation:
        ids = [barrier.barrier_id for barrier in self.barriers]
        if len(ids) != len(set(ids)):
            raise ValueError(
                f"ProgramSemanticPropagation({self.program!r}): barriers contiene "
                "barrier_id duplicado"
            )
        if ids != sorted(ids):
            raise ValueError(
                f"ProgramSemanticPropagation({self.program!r}): barriers no esta ordenado "
                "deterministicamente"
            )
        return self


class SemanticPropagationSummary(AltamiraBaseModel):
    program_count: int = Field(ge=0)
    paragraph_count: int = Field(ge=0)
    fact_count: int = Field(ge=0)
    direct_literal_count: int = Field(ge=0)
    propagated_literal_count: int = Field(ge=0)
    condition_literal_count: int = Field(ge=0)
    unresolved_copy_count: int = Field(ge=0)
    invalidated_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    barrier_count: int = Field(ge=0)
    counts_by_fact_kind: dict[PropagationFactKind, int] = Field(default_factory=dict)
    counts_by_barrier_reason: dict[PropagationBarrierReason, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_counts_non_negative_and_consistent(self) -> SemanticPropagationSummary:
        for kind, count in self.counts_by_fact_kind.items():
            if count < 0:
                raise ValueError(f"counts_by_fact_kind[{kind.value}] no puede ser negativo")
        for reason, count in self.counts_by_barrier_reason.items():
            if count < 0:
                raise ValueError(f"counts_by_barrier_reason[{reason.value}] no puede ser negativo")

        if sum(self.counts_by_fact_kind.values()) != self.fact_count:
            raise ValueError("suma de counts_by_fact_kind no coincide con fact_count")
        if sum(self.counts_by_barrier_reason.values()) != self.barrier_count:
            raise ValueError("suma de counts_by_barrier_reason no coincide con barrier_count")

        expected_by_named_count = {
            PropagationFactKind.DIRECT_LITERAL: self.direct_literal_count,
            PropagationFactKind.PROPAGATED_LITERAL: self.propagated_literal_count,
            PropagationFactKind.CONDITION_LITERAL: self.condition_literal_count,
            PropagationFactKind.UNRESOLVED_COPY: self.unresolved_copy_count,
            PropagationFactKind.INVALIDATED_VALUE: self.invalidated_count,
            PropagationFactKind.BLOCKED_PROPAGATION: self.blocked_count,
        }
        for kind, named_count in expected_by_named_count.items():
            if self.counts_by_fact_kind.get(kind, 0) != named_count:
                raise ValueError(
                    f"{kind.value.lower()}_count ({named_count}) no coincide con "
                    f"counts_by_fact_kind[{kind.value}] ({self.counts_by_fact_kind.get(kind, 0)})"
                )
        return self


class SemanticPropagationArtifact(AltamiraBaseModel):
    """Contenedor persistido en `<run_dir>/diagnostics/
    semantic-propagation.json`. NO contractual, sin timestamps: dos
    ejecuciones sobre los mismos artefactos de entrada deben producir
    bytes identicos.

    `semantic_effects_schema_version`/`semantic_effects_analyzer_version`
    registran la version del `SemanticEffectsArtifact` calculado en
    memoria (Fase 4, ver `semantic_propagation_service.py`) que sirvio de
    entrada a este analisis -- nunca se lee `diagnostics/
    semantic-effects.json` del disco, asi que este campo es la unica
    procedencia disponible de esa version."""

    schema_version: Literal["1.0"] = "1.0"
    analyzer_version: Literal["1.0"] = "1.0"
    semantic_effects_schema_version: Literal["1.0", "1.1"]
    semantic_effects_analyzer_version: Literal["1.0", "1.1"]
    run_id: str = Field(min_length=1)
    source_package_hash: Sha256Hex
    source_artifact_hashes: dict[str, Sha256Hex] = Field(min_length=1)
    summary: SemanticPropagationSummary
    programs: list[ProgramSemanticPropagation] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_programs_sorted_and_unique(self) -> SemanticPropagationArtifact:
        names = [program.program for program in self.programs]
        if len(names) != len(set(names)):
            raise ValueError("programs contiene program duplicado")
        if names != sorted(names):
            raise ValueError("programs no esta ordenado deterministicamente por program")
        return self

    @model_validator(mode="after")
    def _check_summary_matches_programs(self) -> SemanticPropagationArtifact:
        if self.summary.program_count != len(self.programs):
            raise ValueError(
                f"summary.program_count ({self.summary.program_count}) != cantidad de "
                f"programs ({len(self.programs)})"
            )
        expected_paragraph_count = sum(program.paragraph_count for program in self.programs)
        if self.summary.paragraph_count != expected_paragraph_count:
            raise ValueError(
                f"summary.paragraph_count ({self.summary.paragraph_count}) no coincide con "
                f"la suma de programs[].paragraph_count ({expected_paragraph_count})"
            )
        expected_fact_count = sum(program.fact_count for program in self.programs)
        if self.summary.fact_count != expected_fact_count:
            raise ValueError(
                f"summary.fact_count ({self.summary.fact_count}) no coincide con la suma de "
                f"programs[].fact_count ({expected_fact_count})"
            )
        expected_barrier_count = sum(program.barrier_count for program in self.programs)
        if self.summary.barrier_count != expected_barrier_count:
            raise ValueError(
                f"summary.barrier_count ({self.summary.barrier_count}) no coincide con la "
                f"suma de programs[].barrier_count ({expected_barrier_count})"
            )

        expected_by_fact_kind: dict[PropagationFactKind, int] = {}
        for program in self.programs:
            for fact in program.facts:
                expected_by_fact_kind[fact.fact_kind] = (
                    expected_by_fact_kind.get(fact.fact_kind, 0) + 1
                )
        if self.summary.counts_by_fact_kind != expected_by_fact_kind:
            raise ValueError(
                "summary.counts_by_fact_kind no coincide con la agregacion de programs[]"
            )

        expected_by_barrier_reason: dict[PropagationBarrierReason, int] = {}
        for program in self.programs:
            for barrier in program.barriers:
                expected_by_barrier_reason[barrier.reason] = (
                    expected_by_barrier_reason.get(barrier.reason, 0) + 1
                )
        if self.summary.counts_by_barrier_reason != expected_by_barrier_reason:
            raise ValueError(
                "summary.counts_by_barrier_reason no coincide con la agregacion de programs[]"
            )
        return self
