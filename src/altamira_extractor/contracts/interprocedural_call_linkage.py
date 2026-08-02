"""Contrato tipado de la fundacion interprocedural CALL/LINKAGE (Fase 6
de la ampliacion semantica, `feat/interprocedural-call-linkage-foundation`).

Diagnostico, NO contractual: persiste en `<run_dir>/diagnostics/
interprocedural-call-linkage.json`, fuera de `artifacts/01-10` -- mismo
patron que `contracts/semantic_coverage.py`/`semantic_effects.py`/
`semantic_propagation.py`/`v2_shadow_candidates.py`: un artefacto
adyacente, opcional, generado exclusivamente bajo demanda, irrelevante
para que un run llegue a COMPLETED, ignorado por toda etapa V1.

Modelo DEPENDIENTE de `CanonicalProgram[]` (LINKAGE SECTION/PROCEDURE
DIVISION USING/CALL, ver `contracts/canonical.py`) y de
`SemanticEffectsArtifact` (`CALL_PROGRAM`, calculado en memoria) --
nunca al reves. Nunca modifica ninguno de esos artefactos, `SemanticGraph`,
Neo4j, `queries/v1/q0_candidates.cypher`, `CandidateArtifact` V1,
candidatos V2, `ContextPackage`, `RuleDraft`, guardrails ni el ZIP final:
solo AGREGA una representacion interprocedural paralela y diagnostica.

Explicitamente sin propagacion de valores entre programas: un
`InterproceduralArgumentBinding` describe un `potential_flow` CONTRACTUAL
(derivado unicamente del `passing_mode` declarado -- BY REFERENCE/BY
CONTENT/BY VALUE/RETURNING), nunca demuestra que el callee realmente lea o
modifique el parametro. `resolution_status`/`ArgumentBindingStatus` nunca
eligen arbitrariamente ante una referencia ambigua u homonima: la
ausencia de prueba suficiente siempre produce un status explicito de "no
resuelto", nunca una inferencia optimista."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, RelativePath, Sha256Hex
from .enums import CallPassingMode, CallTargetKind, LocationKind
from .semantic_coverage import SemanticSupportStatus


class ProgramResolutionStatus(StrEnum):
    """Resultado de intentar resolver el programa invocado por un
    `InterproceduralCallSite` contra los `CanonicalProgram` del mismo
    paquete (Fase 11) -- nunca contra filesystem, repositorios ni red."""

    RESOLVED_INTERNAL = "RESOLVED_INTERNAL"
    UNRESOLVED_DYNAMIC = "UNRESOLVED_DYNAMIC"
    UNRESOLVED_MISSING_PROGRAM = "UNRESOLVED_MISSING_PROGRAM"
    AMBIGUOUS_PROGRAM = "AMBIGUOUS_PROGRAM"


class ArgumentBindingStatus(StrEnum):
    """Resultado de asociar UN argumento real con SU parametro formal por
    posicion (Fase 12) -- nunca por semejanza de nombres."""

    RESOLVED_POSITIONAL = "RESOLVED_POSITIONAL"
    ACTUAL_UNRESOLVED = "ACTUAL_UNRESOLVED"
    FORMAL_UNRESOLVED = "FORMAL_UNRESOLVED"
    MISSING_ACTUAL = "MISSING_ACTUAL"
    EXTRA_ACTUAL = "EXTRA_ACTUAL"
    AMBIGUOUS_ACTUAL = "AMBIGUOUS_ACTUAL"
    UNSUPPORTED_ARGUMENT = "UNSUPPORTED_ARGUMENT"


class PotentialDataFlow(StrEnum):
    """Direccion de flujo de datos POTENCIAL de un binding -- describe lo
    que el modo de paso declarado PERMITE contractualmente, nunca lo que
    el callee realmente hace (su cuerpo nunca se analiza en esta fase).
    Regla fija: BY REFERENCE -> INPUT_OUTPUT; BY CONTENT/BY VALUE ->
    INPUT_ONLY; RETURNING -> OUTPUT_ONLY; UNKNOWN -> UNKNOWN."""

    INPUT_ONLY = "INPUT_ONLY"
    INPUT_OUTPUT = "INPUT_OUTPUT"
    OUTPUT_ONLY = "OUTPUT_ONLY"
    UNKNOWN = "UNKNOWN"


def potential_flow_for_passing_mode(mode: CallPassingMode) -> PotentialDataFlow:
    """Unica fuente de verdad de la regla fija BY REFERENCE/CONTENT/VALUE
    -> `PotentialDataFlow` (Fase 10/12) -- nunca se reimplementa fuera de
    aqui. No cubre RETURNING (`OUTPUT_ONLY`): esa asignacion es
    independiente de `passing_mode` (ver `InterproceduralArgumentBinding`
    y Fase 12 regla 10, "RETURNING se analiza separadamente")."""
    if mode == CallPassingMode.REFERENCE:
        return PotentialDataFlow.INPUT_OUTPUT
    if mode in (CallPassingMode.CONTENT, CallPassingMode.VALUE):
        return PotentialDataFlow.INPUT_ONLY
    return PotentialDataFlow.UNKNOWN


def _ordered_unique_strings(values: Iterable[str]) -> list[str]:
    return sorted(set(values))


def _check_diagnostics_sorted_and_unique(diagnostics: list[str], *, label: str) -> None:
    if diagnostics != _ordered_unique_strings(diagnostics):
        raise ValueError(
            f"{label}: diagnostics debe estar ordenado alfabeticamente y sin duplicados"
        )


class InterproceduralSourceReference(AltamiraBaseModel):
    """Procedencia sanitizada de un elemento interprocedural. Nunca
    incluye `source_text` (mismo principio que el resto de los
    `*SourceReference` diagnosticos). `paragraph`/`statement_id` son
    opcionales: una declaracion de LINKAGE SECTION o un parametro formal
    de PROCEDURE DIVISION USING no pertenecen a ningun `paragraph` ni
    `CanonicalStatement`."""

    program: str = Field(min_length=1)
    paragraph: str | None = None
    statement_id: str | None = None
    source_file: RelativePath | None = None
    line: int | None = Field(default=None, ge=1)
    location_kind: LocationKind | None = None


class ProgramInterfaceParameter(AltamiraBaseModel):
    """UN parametro formal de la interfaz de un programa (Fase 5),
    resuelto contra su LINKAGE SECTION cuando es posible.
    `linkage_item_qualified_name=None` significa que el nombre no resolvio
    de forma inequivoca (ausente u homonimo ambiguo) -- nunca se inventa
    una definicion; el diagnostico correspondiente queda en
    `diagnostics`."""

    ordinal: int = Field(ge=1)
    formal_name: str = Field(min_length=1)
    formal_qualified_name: str | None = None
    linkage_item_qualified_name: str | None = None
    pic: str | None = None
    usage: str | None = None
    source_reference: InterproceduralSourceReference
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_diagnostics(self) -> ProgramInterfaceParameter:
        _check_diagnostics_sorted_and_unique(
            self.diagnostics, label=f"ProgramInterfaceParameter(#{self.ordinal})"
        )
        return self


class ProgramInterface(AltamiraBaseModel):
    """Interfaz formal completa de UN `CanonicalProgram`: su
    `PROCEDURE DIVISION USING` (en orden fuente exacto) mas, si existe,
    `PROCEDURE DIVISION RETURNING`. `linkage_item_count` es el total de
    `CanonicalProgram.linkage_data_items`, incluidos los que NUNCA
    aparecen como parametro formal (Fase 5 regla 7: se conservan, nunca
    se tratan como parametro)."""

    program: str = Field(min_length=1)
    parameters: list[ProgramInterfaceParameter] = Field(default_factory=list)
    returning_parameter: ProgramInterfaceParameter | None = None
    linkage_item_count: int = Field(ge=0)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_parameters_ordinals(self) -> ProgramInterface:
        ordinals = [parameter.ordinal for parameter in self.parameters]
        if ordinals != list(range(1, len(ordinals) + 1)):
            raise ValueError(
                f"ProgramInterface({self.program!r}): parameters debe estar ordenado por "
                "ordinal consecutivo empezando en 1"
            )
        return self

    @model_validator(mode="after")
    def _check_diagnostics(self) -> ProgramInterface:
        _check_diagnostics_sorted_and_unique(
            self.diagnostics, label=f"ProgramInterface({self.program!r})"
        )
        return self


class InterproceduralArgumentBinding(AltamiraBaseModel):
    """Asociacion (posicional, Fase 12) entre UN argumento real de un
    `InterproceduralCallSite` y su parametro formal correspondiente en la
    `ProgramInterface` del callee -- o el registro explicito de por que
    esa asociacion no pudo demostrarse. Tambien se usa, de forma
    independiente, para el binding de RETURNING de un call site (Fase 12
    regla 10): en ese caso `potential_flow=OUTPUT_ONLY`
    independientemente de `passing_mode` (RETURNING no es BY REFERENCE/
    CONTENT/VALUE)."""

    binding_id: str = Field(min_length=1)
    ordinal: int = Field(ge=1)
    status: ArgumentBindingStatus
    passing_mode: CallPassingMode
    potential_flow: PotentialDataFlow
    actual_name: str | None = None
    actual_qualified_name: str | None = None
    actual_literal: str | None = None
    formal_name: str | None = None
    formal_qualified_name: str | None = None
    linkage_item_qualified_name: str | None = None
    diagnostics: list[str] = Field(default_factory=list)
    source_references: list[InterproceduralSourceReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_potential_flow_matches_passing_mode(self) -> InterproceduralArgumentBinding:
        label = f"InterproceduralArgumentBinding({self.binding_id!r})"
        if self.potential_flow == PotentialDataFlow.OUTPUT_ONLY:
            # RETURNING (Fase 12 regla 10): OUTPUT_ONLY es valido
            # independientemente de passing_mode, que no describe una
            # forma BY REFERENCE/CONTENT/VALUE en este caso.
            return self
        expected = potential_flow_for_passing_mode(self.passing_mode)
        if self.potential_flow != expected:
            raise ValueError(
                f"{label}: potential_flow ({self.potential_flow.value}) no coincide con la "
                f"regla fija para passing_mode={self.passing_mode.value} "
                f"(se esperaba {expected.value})"
            )
        return self

    @model_validator(mode="after")
    def _check_status_coherence(self) -> InterproceduralArgumentBinding:
        label = f"InterproceduralArgumentBinding({self.binding_id!r})"
        if self.status == ArgumentBindingStatus.MISSING_ACTUAL:
            if (
                self.actual_name is not None
                or self.actual_qualified_name is not None
                or self.actual_literal is not None
            ):
                raise ValueError(
                    f"{label}: MISSING_ACTUAL no puede declarar informacion del actual"
                )
        elif self.status == ArgumentBindingStatus.EXTRA_ACTUAL:
            if self.formal_name is not None or self.formal_qualified_name is not None:
                raise ValueError(f"{label}: EXTRA_ACTUAL no puede declarar informacion del formal")
        elif self.status == ArgumentBindingStatus.RESOLVED_POSITIONAL:
            if self.formal_name is None:
                raise ValueError(f"{label}: RESOLVED_POSITIONAL exige formal_name")
            if self.actual_name is None and self.actual_literal is None:
                raise ValueError(
                    f"{label}: RESOLVED_POSITIONAL exige actual_name o actual_literal"
                )
        return self

    @model_validator(mode="after")
    def _check_lists(self) -> InterproceduralArgumentBinding:
        _check_diagnostics_sorted_and_unique(
            self.diagnostics, label=f"binding({self.binding_id!r})"
        )
        return self


def _check_call_arguments_ordinals(
    arguments: list[InterproceduralArgumentBinding], *, label: str
) -> None:
    ordinals = [argument.ordinal for argument in arguments]
    if ordinals != list(range(1, len(ordinals) + 1)):
        raise ValueError(f"{label}: arguments debe estar ordenado por ordinal consecutivo desde 1")


class InterproceduralCallSite(AltamiraBaseModel):
    """UN `CALL` dentro de un `CanonicalProgram` (Fase 6/11/12/13):
    target, resolucion del programa invocado, bindings posicionales de
    `USING`, binding de `RETURNING` (si existe), y si participa de
    recursion/un ciclo (Fase 13)."""

    call_site_id: str = Field(min_length=1)
    caller_program: str = Field(min_length=1)
    caller_paragraph: str = Field(min_length=1)
    statement_id: str = Field(min_length=1)
    target_kind: CallTargetKind
    declared_target: str = Field(min_length=1)
    resolution_status: ProgramResolutionStatus
    resolved_callee_program: str | None = None
    arguments: list[InterproceduralArgumentBinding] = Field(default_factory=list)
    returning_binding: InterproceduralArgumentBinding | None = None
    recursive: bool = False
    part_of_cycle: bool = False
    support_status: SemanticSupportStatus
    diagnostics: list[str] = Field(default_factory=list)
    source_reference: InterproceduralSourceReference

    @model_validator(mode="after")
    def _check_resolution_status_coherence(self) -> InterproceduralCallSite:
        label = f"InterproceduralCallSite({self.call_site_id!r})"
        if self.resolution_status == ProgramResolutionStatus.RESOLVED_INTERNAL:
            if self.resolved_callee_program is None:
                raise ValueError(f"{label}: RESOLVED_INTERNAL exige resolved_callee_program")
        elif self.resolved_callee_program is not None:
            raise ValueError(
                f"{label}: {self.resolution_status.value} no puede declarar "
                "resolved_callee_program"
            )
        return self

    @model_validator(mode="after")
    def _check_target_kind_resolution_coherence(self) -> InterproceduralCallSite:
        # DYNAMIC siempre exige UNRESOLVED_DYNAMIC (nunca se resuelve via
        # propagacion, Fase 11). UNKNOWN (target no identificable de
        # forma estructural, ver CallTargetKind) tambien puede quedar
        # UNRESOLVED_DYNAMIC -- por la misma razon de fondo, "no se sabe
        # que programa se invoca" -- asi que la implicacion NO es
        # biyectiva en ese sentido: solo LITERAL excluye
        # UNRESOLVED_DYNAMIC (un target literal siempre es identificable
        # por definicion).
        label = f"InterproceduralCallSite({self.call_site_id!r})"
        if (
            self.target_kind == CallTargetKind.DYNAMIC
            and self.resolution_status != ProgramResolutionStatus.UNRESOLVED_DYNAMIC
        ):
            raise ValueError(
                f"{label}: target_kind=DYNAMIC exige resolution_status=UNRESOLVED_DYNAMIC"
            )
        if (
            self.target_kind == CallTargetKind.LITERAL
            and self.resolution_status == ProgramResolutionStatus.UNRESOLVED_DYNAMIC
        ):
            raise ValueError(
                f"{label}: target_kind=LITERAL no puede declarar "
                "resolution_status=UNRESOLVED_DYNAMIC"
            )
        return self

    @model_validator(mode="after")
    def _check_recursive_requires_resolution(self) -> InterproceduralCallSite:
        label = f"InterproceduralCallSite({self.call_site_id!r})"
        if self.recursive and self.resolved_callee_program != self.caller_program:
            raise ValueError(
                f"{label}: recursive=True exige resolved_callee_program == caller_program"
            )
        if (
            self.part_of_cycle
            and self.resolution_status != ProgramResolutionStatus.RESOLVED_INTERNAL
        ):
            raise ValueError(
                f"{label}: part_of_cycle=True exige resolution_status=RESOLVED_INTERNAL"
            )
        return self

    @model_validator(mode="after")
    def _check_arguments_ordinals(self) -> InterproceduralCallSite:
        _check_call_arguments_ordinals(
            self.arguments, label=f"InterproceduralCallSite({self.call_site_id!r})"
        )
        return self

    @model_validator(mode="after")
    def _check_diagnostics(self) -> InterproceduralCallSite:
        _check_diagnostics_sorted_and_unique(
            self.diagnostics, label=f"InterproceduralCallSite({self.call_site_id!r})"
        )
        return self


class ProgramCallEdge(AltamiraBaseModel):
    """UNA relacion directa caller->callee del call graph (Fase 13):
    agrega TODOS los `InterproceduralCallSite` resueltos
    (`RESOLVED_INTERNAL`) entre el mismo par de programas -- nunca
    expande recursivamente cuerpos."""

    edge_id: str = Field(min_length=1)
    caller_program: str = Field(min_length=1)
    callee_program: str = Field(min_length=1)
    call_site_ids: list[str] = Field(min_length=1)
    recursive: bool = False
    part_of_cycle: bool = False

    @model_validator(mode="after")
    def _check_call_site_ids(self) -> ProgramCallEdge:
        if self.call_site_ids != _ordered_unique_strings(self.call_site_ids):
            raise ValueError(
                f"ProgramCallEdge({self.edge_id!r}): call_site_ids debe estar ordenado "
                "alfabeticamente y sin duplicados"
            )
        return self

    @model_validator(mode="after")
    def _check_recursive_matches_self_call(self) -> ProgramCallEdge:
        label = f"ProgramCallEdge({self.edge_id!r})"
        is_self_call = self.caller_program == self.callee_program
        if is_self_call and not self.recursive:
            raise ValueError(f"{label}: caller_program == callee_program exige recursive=True")
        if not is_self_call and self.recursive:
            raise ValueError(f"{label}: recursive=True exige caller_program == callee_program")
        return self


class ProgramCallCycle(AltamiraBaseModel):
    """Semantica exacta (auditada explicitamente tras la implementacion
    inicial, ver docs/INTERPROCEDURAL_CALL_LINKAGE.md): UNA componente
    fuertemente conexa (SCC, Tarjan) del call graph directo con 2 o mas
    programas -- NUNCA una enumeracion de ciclos elementales/concretos.
    `programs` es el conjunto de programas de la SCC, ordenado
    alfabeticamente (nunca por orden de deteccion, nunca repite un
    programa: un programa nunca pertenece a mas de una SCC de tamano >=2
    a la vez, por definicion de componente fuertemente conexa).
    `edge_ids` es el conjunto de TODAS las aristas (`ProgramCallEdge`)
    cuyo caller Y callee pertenecen ambos a la SCC -- incluye
    deliberadamente cualquier self-loop (`caller_program ==
    callee_program`) de un programa miembro, aunque ese self-loop no
    "cierre" ningun ciclo de 2+ programas por si mismo: pertenece a la
    SCC por la misma razon de membresia que las demas aristas internas,
    nunca porque se considere parte conceptual del ciclo minimo entre
    dos programas distintos. Cuando la SCC tiene 3+ programas con mas
    aristas internas de las estrictamente necesarias para un unico ciclo
    elemental (un "chord"), `edge_ids` sigue conteniendo TODAS esas
    aristas en un unico objeto -- nunca se enumeran los multiples ciclos
    elementales que la SCC podria contener en teoria de grafos. La
    recursion directa (self-call sin ningun otro programa involucrado)
    NUNCA aparece aqui (`Field(min_length=2)`): se representa
    exclusivamente via `recursive=True` en `InterproceduralCallSite`/
    `ProgramCallEdge`, independiente de este modelo."""

    cycle_id: str = Field(min_length=1)
    programs: list[str] = Field(min_length=2)
    edge_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_programs_unique(self) -> ProgramCallCycle:
        if len(self.programs) != len(set(self.programs)):
            raise ValueError(
                f"ProgramCallCycle({self.cycle_id!r}): programs no puede repetir un programa"
            )
        return self

    @model_validator(mode="after")
    def _check_edge_ids(self) -> ProgramCallCycle:
        if self.edge_ids != _ordered_unique_strings(self.edge_ids):
            raise ValueError(
                f"ProgramCallCycle({self.cycle_id!r}): edge_ids debe estar ordenado "
                "alfabeticamente y sin duplicados"
            )
        return self


class InterproceduralAnalysisSummary(AltamiraBaseModel):
    program_count: int = Field(ge=0)
    interface_count: int = Field(ge=0)
    call_site_count: int = Field(ge=0)
    resolved_internal_count: int = Field(ge=0)
    dynamic_count: int = Field(ge=0)
    missing_program_count: int = Field(ge=0)
    ambiguous_program_count: int = Field(ge=0)
    recursive_call_count: int = Field(ge=0)
    cycle_count: int = Field(ge=0)
    binding_count: int = Field(ge=0)
    resolved_binding_count: int = Field(ge=0)
    unresolved_binding_count: int = Field(ge=0)
    counts_by_resolution_status: dict[ProgramResolutionStatus, int] = Field(default_factory=dict)
    counts_by_binding_status: dict[ArgumentBindingStatus, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_counts_non_negative(self) -> InterproceduralAnalysisSummary:
        for status, count in self.counts_by_resolution_status.items():
            if count < 0:
                raise ValueError(
                    f"counts_by_resolution_status[{status.value}] no puede ser negativo"
                )
        for binding_status, count in self.counts_by_binding_status.items():
            if count < 0:
                raise ValueError(
                    f"counts_by_binding_status[{binding_status.value}] no puede ser negativo"
                )
        return self

    @model_validator(mode="after")
    def _check_call_site_partition(self) -> InterproceduralAnalysisSummary:
        total = (
            self.resolved_internal_count
            + self.dynamic_count
            + self.missing_program_count
            + self.ambiguous_program_count
        )
        if total != self.call_site_count:
            raise ValueError(
                "resolved_internal_count + dynamic_count + missing_program_count + "
                "ambiguous_program_count no coincide con call_site_count"
            )
        if sum(self.counts_by_resolution_status.values()) != self.call_site_count:
            raise ValueError("suma de counts_by_resolution_status no coincide con call_site_count")
        expected_named = {
            ProgramResolutionStatus.RESOLVED_INTERNAL: self.resolved_internal_count,
            ProgramResolutionStatus.UNRESOLVED_DYNAMIC: self.dynamic_count,
            ProgramResolutionStatus.UNRESOLVED_MISSING_PROGRAM: self.missing_program_count,
            ProgramResolutionStatus.AMBIGUOUS_PROGRAM: self.ambiguous_program_count,
        }
        for status, named_count in expected_named.items():
            if self.counts_by_resolution_status.get(status, 0) != named_count:
                raise ValueError(
                    f"counts_by_resolution_status[{status.value}] no coincide con el conteo "
                    "nombrado correspondiente"
                )
        return self

    @model_validator(mode="after")
    def _check_binding_partition(self) -> InterproceduralAnalysisSummary:
        if self.resolved_binding_count + self.unresolved_binding_count != self.binding_count:
            raise ValueError(
                "resolved_binding_count + unresolved_binding_count no coincide con binding_count"
            )
        if sum(self.counts_by_binding_status.values()) != self.binding_count:
            raise ValueError("suma de counts_by_binding_status no coincide con binding_count")
        resolved_from_map = self.counts_by_binding_status.get(
            ArgumentBindingStatus.RESOLVED_POSITIONAL, 0
        )
        if resolved_from_map != self.resolved_binding_count:
            raise ValueError(
                "resolved_binding_count no coincide con "
                "counts_by_binding_status[RESOLVED_POSITIONAL]"
            )
        return self


class InterproceduralCallLinkageArtifact(AltamiraBaseModel):
    """Contenedor persistido en `<run_dir>/diagnostics/
    interprocedural-call-linkage.json`. NO contractual, sin timestamps,
    sin `source_text` completo, sin rutas absolutas: dos ejecuciones
    sobre los mismos artefactos de entrada deben producir bytes
    identicos.

    `canonical_schema_versions` registra el conjunto (ordenado, sin
    duplicados) de `CanonicalProgram.schema_version` realmente presentes
    entre los programas analizados -- un paquete puede mezclar programas
    "1.0"/"1.1"/"1.2"/"1.3" segun cada uno use o no nivel 88/CALL/LINKAGE/
    GOBACK-STOP RUN-EXIT PROGRAM (Fase 7b).
    `semantic_effects_schema_version`/`semantic_effects_analyzer_version`
    registran la version del `SemanticEffectsArtifact` calculado en
    memoria que sirvio de entrada (mismo patron que
    `SemanticPropagationArtifact`) -- nunca se lee `diagnostics/
    semantic-effects.json` del disco."""

    schema_version: Literal["1.0"] = "1.0"
    analyzer_version: Literal["1.0"] = "1.0"
    canonical_schema_versions: list[Literal["1.0", "1.1", "1.2", "1.3"]] = Field(
        default_factory=list
    )
    semantic_effects_schema_version: Literal["1.0", "1.1", "1.2"]
    semantic_effects_analyzer_version: Literal["1.0", "1.1", "1.2"]
    run_id: str = Field(min_length=1)
    source_package_hash: Sha256Hex
    source_artifact_hashes: dict[str, Sha256Hex] = Field(min_length=1)
    summary: InterproceduralAnalysisSummary
    interfaces: list[ProgramInterface] = Field(default_factory=list)
    call_sites: list[InterproceduralCallSite] = Field(default_factory=list)
    call_edges: list[ProgramCallEdge] = Field(default_factory=list)
    cycles: list[ProgramCallCycle] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_canonical_schema_versions(self) -> InterproceduralCallLinkageArtifact:
        if list(self.canonical_schema_versions) != sorted(set(self.canonical_schema_versions)):
            raise ValueError(
                "canonical_schema_versions debe estar ordenado alfabeticamente y sin duplicados"
            )
        return self

    @model_validator(mode="after")
    def _check_interfaces_sorted_and_unique(self) -> InterproceduralCallLinkageArtifact:
        names = [interface.program for interface in self.interfaces]
        if len(names) != len(set(names)):
            raise ValueError("interfaces contiene program duplicado")
        if names != sorted(names):
            raise ValueError("interfaces no esta ordenado deterministicamente por program")
        return self

    @model_validator(mode="after")
    def _check_call_sites_sorted_and_unique(self) -> InterproceduralCallLinkageArtifact:
        ids = [call_site.call_site_id for call_site in self.call_sites]
        if len(ids) != len(set(ids)):
            raise ValueError("call_sites contiene call_site_id duplicado")
        if ids != sorted(ids):
            raise ValueError("call_sites no esta ordenado deterministicamente por call_site_id")
        return self

    @model_validator(mode="after")
    def _check_call_edges_sorted_and_unique(self) -> InterproceduralCallLinkageArtifact:
        ids = [edge.edge_id for edge in self.call_edges]
        if len(ids) != len(set(ids)):
            raise ValueError("call_edges contiene edge_id duplicado")
        if ids != sorted(ids):
            raise ValueError("call_edges no esta ordenado deterministicamente por edge_id")
        return self

    @model_validator(mode="after")
    def _check_cycles_sorted_and_unique(self) -> InterproceduralCallLinkageArtifact:
        ids = [cycle.cycle_id for cycle in self.cycles]
        if len(ids) != len(set(ids)):
            raise ValueError("cycles contiene cycle_id duplicado")
        if ids != sorted(ids):
            raise ValueError("cycles no esta ordenado deterministicamente por cycle_id")
        return self

    @model_validator(mode="after")
    def _check_summary_matches_content(self) -> InterproceduralCallLinkageArtifact:
        if self.summary.interface_count != len(self.interfaces):
            raise ValueError(
                f"summary.interface_count ({self.summary.interface_count}) != cantidad de "
                f"interfaces ({len(self.interfaces)})"
            )
        if self.summary.call_site_count != len(self.call_sites):
            raise ValueError(
                f"summary.call_site_count ({self.summary.call_site_count}) != cantidad de "
                f"call_sites ({len(self.call_sites)})"
            )
        if self.summary.cycle_count != len(self.cycles):
            raise ValueError(
                f"summary.cycle_count ({self.summary.cycle_count}) != cantidad de cycles "
                f"({len(self.cycles)})"
            )

        expected_by_resolution: dict[ProgramResolutionStatus, int] = {}
        recursive_count = 0
        for call_site in self.call_sites:
            expected_by_resolution[call_site.resolution_status] = (
                expected_by_resolution.get(call_site.resolution_status, 0) + 1
            )
            if call_site.recursive:
                recursive_count += 1
        if self.summary.counts_by_resolution_status != expected_by_resolution:
            raise ValueError("summary.counts_by_resolution_status no coincide con call_sites[]")
        if self.summary.recursive_call_count != recursive_count:
            raise ValueError("summary.recursive_call_count no coincide con call_sites[].recursive")

        all_bindings = [
            binding
            for call_site in self.call_sites
            for binding in (
                [*call_site.arguments, call_site.returning_binding]
                if call_site.returning_binding is not None
                else call_site.arguments
            )
        ]
        if self.summary.binding_count != len(all_bindings):
            raise ValueError(
                f"summary.binding_count ({self.summary.binding_count}) != cantidad real de "
                f"bindings ({len(all_bindings)})"
            )
        expected_by_binding_status: dict[ArgumentBindingStatus, int] = {}
        for binding in all_bindings:
            expected_by_binding_status[binding.status] = (
                expected_by_binding_status.get(binding.status, 0) + 1
            )
        if self.summary.counts_by_binding_status != expected_by_binding_status:
            raise ValueError("summary.counts_by_binding_status no coincide con la agregacion real")
        return self
