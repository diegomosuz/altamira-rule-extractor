"""Contrato tipado de la propagacion interprocedural conservadora en
shadow mode (Fase 7 de la ampliacion semantica,
`feat/interprocedural-propagation-shadow`).

Diagnostico, NO contractual: persiste en `<run_dir>/diagnostics/
interprocedural-propagation.json`, fuera de `artifacts/01-10` -- mismo
patron que `contracts/semantic_effects.py`/`semantic_propagation.py`/
`interprocedural_call_linkage.py`/`v2_shadow_candidates.py`: un artefacto
adyacente, opcional, generado exclusivamente bajo demanda, irrelevante
para que un run llegue a COMPLETED, ignorado por toda etapa V1.

Modelo DEPENDIENTE de tres artefactos ya calculados (Fase 2-6), nunca al
reves: `CanonicalProgram[]` (estructura), `SemanticEffectsArtifact`
(`CALL_PROGRAM`), `SemanticPropagationArtifact` (`PropagatedValueFact`
intraprograma ya demostrados) e `InterproceduralCallLinkageArtifact`
(resolucion de programa, bindings actual-formal, call graph, SCC/
recursion). Este modulo AGREGA una capa de propagacion de valores
literales deterministicos ENTRE programas, apoyada exclusivamente en
hechos ya demostrados -- nunca reinterpreta `source_text`, nunca vuelve
a evaluar una asignacion COBOL desde cero.

Explicitamente sin fixed point sobre ciclos en esta fase: solo programas
aciclicos, en orden topologico determinista sobre el call graph directo
de `InterproceduralCallLinkageArtifact`. Un SCC de 2+ programas (o un
self-call) bloquea la propagacion para esos call sites -- nunca se
itera hasta converger."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, Sha256Hex
from .interprocedural_call_linkage import InterproceduralSourceReference


class InterproceduralFactKind(StrEnum):
    """Naturaleza de un `InterproceduralPropagationFact`."""

    ENTRY_FACT = "ENTRY_FACT"
    RETURNING_FACT = "RETURNING_FACT"
    BY_REFERENCE_OUTPUT = "BY_REFERENCE_OUTPUT"
    INVALIDATION = "INVALIDATION"


class InterproceduralPropagationDirection(StrEnum):
    """Sentido del flujo de valor descrito por el hecho: hacia el callee
    (`ENTRY_FACT`) o de vuelta hacia el caller (`RETURNING_FACT`/
    `BY_REFERENCE_OUTPUT`/`INVALIDATION`)."""

    CALLER_TO_CALLEE = "CALLER_TO_CALLEE"
    CALLEE_TO_CALLER = "CALLEE_TO_CALLER"


class InterproceduralPropagationStatus(StrEnum):
    """Resultado de intentar propagar UN hecho puntual (un binding de
    entrada, o el valor final de un formal/RETURNING de salida)."""

    PROPAGATED = "PROPAGATED"
    BLOCKED = "BLOCKED"
    INVALIDATED = "INVALIDATED"
    UNRESOLVED = "UNRESOLVED"


class InterproceduralPropagationBarrier(StrEnum):
    """Motivo por el que un hecho puntual (o un call site completo)
    nunca propaga un valor."""

    DYNAMIC_CALL = "DYNAMIC_CALL"
    MISSING_PROGRAM = "MISSING_PROGRAM"
    AMBIGUOUS_PROGRAM = "AMBIGUOUS_PROGRAM"
    RECURSION = "RECURSION"
    CYCLE = "CYCLE"
    UNRESOLVED_ACTUAL = "UNRESOLVED_ACTUAL"
    UNRESOLVED_FORMAL = "UNRESOLVED_FORMAL"
    UNKNOWN_PASSING_MODE = "UNKNOWN_PASSING_MODE"
    MISSING_ARGUMENT = "MISSING_ARGUMENT"
    EXTRA_ARGUMENT = "EXTRA_ARGUMENT"
    NON_DETERMINISTIC_VALUE = "NON_DETERMINISTIC_VALUE"
    UNSUPPORTED_CONTROL_FLOW = "UNSUPPORTED_CONTROL_FLOW"
    NESTED_CALL_UNRESOLVED = "NESTED_CALL_UNRESOLVED"
    NON_RETURNING_TERMINATION = "NON_RETURNING_TERMINATION"


def _ordered_unique_strings(values: Iterable[str]) -> list[str]:
    return sorted(set(values))


class InterproceduralPropagationFact(AltamiraBaseModel):
    """UN hecho puntual de propagacion interprocedural: o bien un valor
    de entrada trasladado (o bloqueado) hacia un formal del callee
    (`direction=CALLER_TO_CALLEE`), o bien el valor final de un formal/
    RETURNING trasladado (o invalidado) de vuelta hacia el caller
    (`direction=CALLEE_TO_CALLER`). `actual_name` es siempre el nombre en
    el programa CALLER; `formal_name` es siempre el nombre resuelto en
    el programa CALLEE -- nunca al reves, sin importar la direccion.
    `source_fact_ids` referencia los `PropagatedValueFact.fact_id` de
    `SemanticPropagationArtifact` (nunca statement_id/effect_id
    directamente) que demostraron el literal usado, cuando aplica."""

    fact_id: str = Field(min_length=1)
    call_site_id: str = Field(min_length=1)
    caller_program: str = Field(min_length=1)
    callee_program: str | None = None
    caller_statement_id: str = Field(min_length=1)
    binding_id: str | None = None
    direction: InterproceduralPropagationDirection
    kind: InterproceduralFactKind
    status: InterproceduralPropagationStatus
    actual_name: str | None = None
    formal_name: str | None = None
    literal: str | None = None
    source_fact_ids: list[str] = Field(default_factory=list)
    barriers: list[InterproceduralPropagationBarrier] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
    source_references: list[InterproceduralSourceReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_literal_matches_status(self) -> InterproceduralPropagationFact:
        label = f"InterproceduralPropagationFact({self.fact_id!r})"
        if self.status == InterproceduralPropagationStatus.PROPAGATED:
            if self.literal is None:
                raise ValueError(f"{label}: status=PROPAGATED exige literal")
        elif self.literal is not None:
            raise ValueError(
                f"{label}: status={self.status.value} no puede declarar literal "
                "(solo PROPAGATED afirma un valor)"
            )
        return self

    @model_validator(mode="after")
    def _check_kind_matches_status_and_direction(self) -> InterproceduralPropagationFact:
        label = f"InterproceduralPropagationFact({self.fact_id!r})"
        if self.status == InterproceduralPropagationStatus.INVALIDATED:
            if self.kind != InterproceduralFactKind.INVALIDATION:
                raise ValueError(f"{label}: status=INVALIDATED exige kind=INVALIDATION")
        elif self.kind == InterproceduralFactKind.INVALIDATION:
            raise ValueError(f"{label}: kind=INVALIDATION exige status=INVALIDATED")

        if self.kind == InterproceduralFactKind.ENTRY_FACT:
            if self.direction != InterproceduralPropagationDirection.CALLER_TO_CALLEE:
                raise ValueError(f"{label}: kind=ENTRY_FACT exige direction=CALLER_TO_CALLEE")
        elif self.kind in (
            InterproceduralFactKind.RETURNING_FACT,
            InterproceduralFactKind.BY_REFERENCE_OUTPUT,
            InterproceduralFactKind.INVALIDATION,
        ):
            if self.direction != InterproceduralPropagationDirection.CALLEE_TO_CALLER:
                raise ValueError(
                    f"{label}: kind={self.kind.value} exige direction=CALLEE_TO_CALLER"
                )
        return self

    @model_validator(mode="after")
    def _check_barriers_only_when_blocked(self) -> InterproceduralPropagationFact:
        label = f"InterproceduralPropagationFact({self.fact_id!r})"
        if self.status == InterproceduralPropagationStatus.BLOCKED and not self.barriers:
            raise ValueError(f"{label}: status=BLOCKED exige al menos un barrier")
        if self.status != InterproceduralPropagationStatus.BLOCKED and self.barriers:
            raise ValueError(f"{label}: solo status=BLOCKED puede declarar barriers")
        return self

    @model_validator(mode="after")
    def _check_lists_sorted_and_unique(self) -> InterproceduralPropagationFact:
        label = f"InterproceduralPropagationFact({self.fact_id!r})"
        if self.source_fact_ids != _ordered_unique_strings(self.source_fact_ids):
            raise ValueError(f"{label}: source_fact_ids debe estar ordenado y sin duplicados")
        if self.diagnostics != _ordered_unique_strings(self.diagnostics):
            raise ValueError(f"{label}: diagnostics debe estar ordenado y sin duplicados")
        if list(self.barriers) != sorted(set(self.barriers), key=lambda b: b.value):
            raise ValueError(f"{label}: barriers debe estar ordenado y sin duplicados")
        return self


class InterproceduralProgramAnalysis(AltamiraBaseModel):
    """Resultado de propagacion para UN programa considerado como
    CALLEE: `entry_facts` son los hechos `direction=CALLER_TO_CALLEE` de
    todos los call sites (de cualquier caller) que invocan a `program`;
    `exit_facts` son los hechos `direction=CALLEE_TO_CALLER`
    correspondientes. Cada `InterproceduralPropagationFact` aparece en
    exactamente UN `InterproceduralProgramAnalysis` (el de su
    `callee_program`) -- nunca duplicado bajo el caller.
    `blocked_call_sites` son los `call_site_id` de llamadas hechas DESDE
    `program` (program como CALLER) bloqueadas a nivel de call site
    completo (nunca se intento analisis de argumentos): dinamica,
    programa ausente/ambiguo, self-call o miembro de SCC."""

    program: str = Field(min_length=1)
    entry_facts: list[InterproceduralPropagationFact] = Field(default_factory=list)
    exit_facts: list[InterproceduralPropagationFact] = Field(default_factory=list)
    blocked_call_sites: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_fact_directions(self) -> InterproceduralProgramAnalysis:
        if any(
            f.direction != InterproceduralPropagationDirection.CALLER_TO_CALLEE
            for f in self.entry_facts
        ):
            raise ValueError(
                f"InterproceduralProgramAnalysis({self.program!r}): entry_facts solo admite "
                "direction=CALLER_TO_CALLEE"
            )
        if any(
            f.direction != InterproceduralPropagationDirection.CALLEE_TO_CALLER
            for f in self.exit_facts
        ):
            raise ValueError(
                f"InterproceduralProgramAnalysis({self.program!r}): exit_facts solo admite "
                "direction=CALLEE_TO_CALLER"
            )
        return self

    @model_validator(mode="after")
    def _check_lists_sorted_and_unique(self) -> InterproceduralProgramAnalysis:
        label = f"InterproceduralProgramAnalysis({self.program!r})"
        entry_ids = [f.fact_id for f in self.entry_facts]
        if len(entry_ids) != len(set(entry_ids)) or entry_ids != sorted(entry_ids):
            raise ValueError(
                f"{label}: entry_facts debe estar ordenado por fact_id y sin duplicados"
            )
        exit_ids = [f.fact_id for f in self.exit_facts]
        if len(exit_ids) != len(set(exit_ids)) or exit_ids != sorted(exit_ids):
            raise ValueError(
                f"{label}: exit_facts debe estar ordenado por fact_id y sin duplicados"
            )
        if self.blocked_call_sites != _ordered_unique_strings(self.blocked_call_sites):
            raise ValueError(f"{label}: blocked_call_sites debe estar ordenado y sin duplicados")
        if self.diagnostics != _ordered_unique_strings(self.diagnostics):
            raise ValueError(f"{label}: diagnostics debe estar ordenado y sin duplicados")
        return self


class InterproceduralPropagationSummary(AltamiraBaseModel):
    """`eligible_call_count` son los call sites literal+RESOLVED_INTERNAL
    que no son self-call ni miembros de una SCC (los unicos sobre los
    que se intenta analisis de argumentos); `blocked_call_count` son el
    resto (bloqueados por completo a nivel de call site).
    `propagated_call_count` es el subconjunto de `eligible_call_count`
    con al menos un `InterproceduralPropagationFact` en
    `status=PROPAGATED`."""

    program_count: int = Field(ge=0)
    call_site_count: int = Field(ge=0)
    eligible_call_count: int = Field(ge=0)
    propagated_call_count: int = Field(ge=0)
    blocked_call_count: int = Field(ge=0)
    entry_fact_count: int = Field(ge=0)
    returning_fact_count: int = Field(ge=0)
    by_reference_output_count: int = Field(ge=0)
    invalidation_count: int = Field(ge=0)
    counts_by_status: dict[InterproceduralPropagationStatus, int] = Field(default_factory=dict)
    counts_by_barrier: dict[InterproceduralPropagationBarrier, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_call_site_partition(self) -> InterproceduralPropagationSummary:
        if self.eligible_call_count + self.blocked_call_count != self.call_site_count:
            raise ValueError(
                "eligible_call_count + blocked_call_count no coincide con call_site_count"
            )
        if self.propagated_call_count > self.eligible_call_count:
            raise ValueError("propagated_call_count no puede superar eligible_call_count")
        return self

    @model_validator(mode="after")
    def _check_counts_non_negative(self) -> InterproceduralPropagationSummary:
        for status, count in self.counts_by_status.items():
            if count < 0:
                raise ValueError(f"counts_by_status[{status.value}] no puede ser negativo")
        for barrier, count in self.counts_by_barrier.items():
            if count < 0:
                raise ValueError(f"counts_by_barrier[{barrier.value}] no puede ser negativo")
        return self

    @model_validator(mode="after")
    def _check_kind_counts_match_named_fields(self) -> InterproceduralPropagationSummary:
        total_named = (
            self.entry_fact_count
            + self.returning_fact_count
            + self.by_reference_output_count
            + self.invalidation_count
        )
        if total_named != sum(self.counts_by_status.values()):
            # entry/returning/by_reference_output/invalidation particionan por KIND,
            # counts_by_status particiona por STATUS: ambos deben sumar al mismo
            # total de facts (cada fact tiene exactamente un kind y un status).
            raise ValueError(
                "entry_fact_count + returning_fact_count + by_reference_output_count + "
                "invalidation_count no coincide con la suma de counts_by_status"
            )
        return self


class InterproceduralPropagationArtifact(AltamiraBaseModel):
    """Contenedor persistido en `<run_dir>/diagnostics/
    interprocedural-propagation.json`. NO contractual, sin timestamps,
    sin rutas absolutas, sin `source_text` completo: dos ejecuciones
    sobre los mismos artefactos de entrada deben producir bytes
    identicos.

    `interprocedural_analysis_schema_version`/
    `interprocedural_analysis_analyzer_version` registran la version de
    `InterproceduralCallLinkageArtifact` calculado en memoria que sirvio
    de entrada; `semantic_effects_schema_version`/
    `semantic_propagation_schema_version` registran la version de esos
    otros dos artefactos, tambien calculados en memoria -- ninguno de
    los tres se lee de disco, asi que estos campos son la unica
    procedencia disponible."""

    schema_version: Literal["1.0"] = "1.0"
    analyzer_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    source_package_hash: Sha256Hex
    source_artifact_hashes: dict[str, Sha256Hex] = Field(min_length=1)
    interprocedural_analysis_schema_version: Literal["1.0"]
    interprocedural_analysis_analyzer_version: Literal["1.0"]
    semantic_effects_schema_version: Literal["1.0", "1.1", "1.2"]
    semantic_propagation_schema_version: Literal["1.0", "1.1"]
    summary: InterproceduralPropagationSummary
    program_analyses: list[InterproceduralProgramAnalysis] = Field(default_factory=list)
    facts: list[InterproceduralPropagationFact] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_program_analyses_sorted_and_unique(self) -> InterproceduralPropagationArtifact:
        names = [pa.program for pa in self.program_analyses]
        if len(names) != len(set(names)):
            raise ValueError("program_analyses contiene program duplicado")
        if names != sorted(names):
            raise ValueError("program_analyses no esta ordenado deterministicamente por program")
        return self

    @model_validator(mode="after")
    def _check_facts_sorted_and_unique(self) -> InterproceduralPropagationArtifact:
        ids = [f.fact_id for f in self.facts]
        if len(ids) != len(set(ids)):
            raise ValueError("facts contiene fact_id duplicado")
        if ids != sorted(ids):
            raise ValueError("facts no esta ordenado deterministicamente por fact_id")
        return self

    @model_validator(mode="after")
    def _check_diagnostics_sorted_and_unique(self) -> InterproceduralPropagationArtifact:
        if self.diagnostics != _ordered_unique_strings(self.diagnostics):
            raise ValueError("diagnostics debe estar ordenado alfabeticamente y sin duplicados")
        return self

    @model_validator(mode="after")
    def _check_facts_match_program_analyses(self) -> InterproceduralPropagationArtifact:
        expected_facts = sorted(
            (f for pa in self.program_analyses for f in (*pa.entry_facts, *pa.exit_facts)),
            key=lambda f: f.fact_id,
        )
        if expected_facts != self.facts:
            raise ValueError(
                "facts debe ser exactamente la union ordenada de program_analyses[]."
                "entry_facts/exit_facts"
            )
        return self

    @model_validator(mode="after")
    def _check_summary_matches_content(self) -> InterproceduralPropagationArtifact:
        if self.summary.program_count != len(self.program_analyses):
            raise ValueError(
                f"summary.program_count ({self.summary.program_count}) != cantidad de "
                f"program_analyses ({len(self.program_analyses)})"
            )

        blocked_call_sites = {
            call_site_id for pa in self.program_analyses for call_site_id in pa.blocked_call_sites
        }
        eligible_call_sites = {f.call_site_id for f in self.facts}
        overlap = blocked_call_sites & eligible_call_sites
        if overlap:
            raise ValueError(
                "call_site_id presente tanto en blocked_call_sites como en facts: "
                f"{sorted(overlap)}"
            )
        if self.summary.blocked_call_count != len(blocked_call_sites):
            raise ValueError(
                f"summary.blocked_call_count ({self.summary.blocked_call_count}) != cantidad "
                f"real de blocked_call_sites ({len(blocked_call_sites)})"
            )
        if self.summary.eligible_call_count != len(eligible_call_sites):
            raise ValueError(
                f"summary.eligible_call_count ({self.summary.eligible_call_count}) != cantidad "
                f"real de call sites con al menos un fact ({len(eligible_call_sites)})"
            )
        if self.summary.call_site_count != len(blocked_call_sites) + len(eligible_call_sites):
            raise ValueError(
                "summary.call_site_count no coincide con blocked_call_sites + call sites con facts"
            )

        propagated_call_sites = {
            f.call_site_id
            for f in self.facts
            if f.status == InterproceduralPropagationStatus.PROPAGATED
        }
        if self.summary.propagated_call_count != len(propagated_call_sites):
            raise ValueError(
                f"summary.propagated_call_count ({self.summary.propagated_call_count}) != "
                f"cantidad real de call sites con al menos un fact PROPAGATED "
                f"({len(propagated_call_sites)})"
            )

        expected_by_status: dict[InterproceduralPropagationStatus, int] = {}
        expected_by_barrier: dict[InterproceduralPropagationBarrier, int] = {}
        expected_by_kind: dict[InterproceduralFactKind, int] = {}
        for fact in self.facts:
            expected_by_status[fact.status] = expected_by_status.get(fact.status, 0) + 1
            expected_by_kind[fact.kind] = expected_by_kind.get(fact.kind, 0) + 1
            for barrier in fact.barriers:
                expected_by_barrier[barrier] = expected_by_barrier.get(barrier, 0) + 1

        if self.summary.counts_by_status != expected_by_status:
            raise ValueError("summary.counts_by_status no coincide con la agregacion de facts[]")
        if self.summary.counts_by_barrier != expected_by_barrier:
            raise ValueError("summary.counts_by_barrier no coincide con la agregacion de facts[]")

        expected_named_kind_counts = {
            InterproceduralFactKind.ENTRY_FACT: self.summary.entry_fact_count,
            InterproceduralFactKind.RETURNING_FACT: self.summary.returning_fact_count,
            InterproceduralFactKind.BY_REFERENCE_OUTPUT: self.summary.by_reference_output_count,
            InterproceduralFactKind.INVALIDATION: self.summary.invalidation_count,
        }
        for kind, named_count in expected_named_kind_counts.items():
            if expected_by_kind.get(kind, 0) != named_count:
                raise ValueError(
                    f"summary count nombrado para kind={kind.value} ({named_count}) no coincide "
                    f"con la cantidad real de facts ({expected_by_kind.get(kind, 0)})"
                )
        return self
