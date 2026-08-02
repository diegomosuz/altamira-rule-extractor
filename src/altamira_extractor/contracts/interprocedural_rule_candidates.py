"""Contrato tipado de detectores de reglas interprocedurales en shadow
mode (Fase 8 de la ampliacion semantica,
`feat/interprocedural-rule-detectors-shadow`).

Diagnostico, NO contractual: persiste en `<run_dir>/diagnostics/
interprocedural-rule-candidates-shadow.json`, fuera de `artifacts/01-10`
-- mismo patron que `contracts/v2_shadow_candidates.py`/
`contracts/interprocedural_propagation.py`: un artefacto adyacente,
opcional, generado exclusivamente bajo demanda, irrelevante para que un
run llegue a COMPLETED.

Modelo DEPENDIENTE de cinco artefactos ya calculados (Fase 2/4/6/7, mas
opcionalmente V1/V2), nunca al reves: `SemanticEffectsArtifact`,
`SemanticPropagationArtifact`, `InterproceduralCallLinkageArtifact` e
`InterproceduralPropagationArtifact` ya demostraron toda la evidencia
estructural que este modulo consume -- un `InterproceduralRuleCandidate`
NUNCA reinterpreta `source_text`, nunca vuelve a evaluar una asignacion
COBOL desde cero, y nunca inventa una condicion de negocio que no este
demostrada por esos artefactos. `CandidateArtifact` V1 y
`V2ShadowCandidatesArtifact` (Fase 5) se usan EXCLUSIVAMENTE como
baseline de comparacion de solo lectura -- nunca se modifican, nunca se
recalculan con logica distinta a la ya existente.

Un `InterproceduralRuleCandidate` nunca alimenta `ContextPackage`,
generacion LLM, guardrails, Markdown, el ZIP final, `CandidateArtifact`
V1, `V2ShadowCandidatesArtifact` ni Neo4j -- ver
docs/INTERPROCEDURAL_RULE_DETECTORS_SHADOW.md."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, Sha256Hex
from .interprocedural_call_linkage import InterproceduralSourceReference
from .interprocedural_propagation import InterproceduralPropagationBarrier


class InterproceduralRuleType(StrEnum):
    """Tipo de regla funcional que un detector interprocedural afirma
    haber demostrado. Solo se declaran los tres tipos con semantica ya
    completamente modelada por Fase 6 (bindings actual-formal, call
    graph) y Fase 7 (propagacion RETURNING/BY REFERENCE conservadora)."""

    RETURN_CODE_RULE = "RETURN_CODE_RULE"
    BY_REFERENCE_RULE = "BY_REFERENCE_RULE"
    STATE_TRANSITION_RULE = "STATE_TRANSITION_RULE"


class InterproceduralCandidateSupport(StrEnum):
    """Nivel de soporte de un `InterproceduralRuleCandidate`. Distinto de
    `InterproceduralPropagationStatus` (Fase 7): aqui describe si el
    CANDIDATO DE REGLA en si mismo tiene evidencia suficiente para
    presentarse como demostrado, no el resultado puntual de un hecho de
    propagacion."""

    DETERMINISTIC = "DETERMINISTIC"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"


class InterproceduralComparisonStatus(StrEnum):
    """Clasificacion PRINCIPAL (unica, con prioridad) de comparar UN
    `InterproceduralRuleCandidate` contra los candidatos V1
    (`RuleCandidate`)/V2 (`V2ShadowCandidate`) ya calculados. Es una
    funcion determinista de `InterproceduralCandidateComparison.
    v1_relation`/`v2_relation` (ver `derive_comparison_status`), nunca
    un campo independiente: existe para que el summary tenga una
    particion unica y sin doble conteo, pero la relacion real con CADA
    fuente (independientemente) se conserva en `v1_relation`/
    `v2_relation` -- ver docstring de `InterproceduralCandidateComparison`.

    `BLOCKED` es exclusivo de candidatos con `support=BLOCKED`: nunca se
    intenta comparar contra V1/V2 un candidato que ni siquiera demostro
    un valor (`v1_relation`/`v2_relation` quedan `NOT_EVALUATED`).

    `NOT_EVALUATED` (auditoria de cierre, Fase 8): se usa cuando NINGUNA
    fuente demostro MATCHED/RELATED y al menos una de las dos (V1 o V2)
    nunca estuvo disponible para evaluar -- nunca cuando ambas
    estuvieron disponibles y simplemente no hubo coincidencia (ese caso
    es `INTERPROCEDURAL_ONLY`). Evita que la ausencia de una fuente se
    disfrace de "sin equivalente" (regla C/D de la auditoria de cierre)."""

    MATCHED_V1 = "MATCHED_V1"
    MATCHED_V2 = "MATCHED_V2"
    RELATED_V1 = "RELATED_V1"
    RELATED_V2 = "RELATED_V2"
    INTERPROCEDURAL_ONLY = "INTERPROCEDURAL_ONLY"
    NOT_EVALUATED = "NOT_EVALUATED"
    BLOCKED = "BLOCKED"


class InterproceduralRelationStatus(StrEnum):
    """Estado INDEPENDIENTE de la relacion de un candidato con UNA sola
    fuente (V1 o V2), calculado por separado para cada una (auditoria de
    cierre, Fase 8). Nunca se colapsa en un unico campo global: permite
    saber, sin ambiguedad, si un candidato tiene relacion con V1 Y con
    V2 simultaneamente, incluso cuando `InterproceduralComparisonStatus`
    (la clasificacion principal, con prioridad V1 > V2) solo puede
    reportar una de las dos como "la" clasificacion.

    - `MATCHED`: la fuente estuvo disponible y se demostro identidad
      estructural completa (mismo programa, mismo target/outcome_code,
      mismo literal).
    - `RELATED`: la fuente estuvo disponible, comparte programa (y
      target, para V2) con al menos un candidato, pero la identidad
      exacta no pudo demostrarse.
    - `NOT_FOUND`: la fuente estuvo disponible y se evaluo por completo,
      pero ningun candidato de esa fuente guarda relacion alguna.
    - `NOT_EVALUATED`: la fuente NUNCA estuvo disponible para este run
      (p. ej. `artifacts/06-candidates.json`/`artifacts/04-semantic-
      graph.json` ausentes) -- nunca se fabrica una comparacion negativa
      en su lugar (regla D de la auditoria de cierre)."""

    MATCHED = "MATCHED"
    RELATED = "RELATED"
    NOT_FOUND = "NOT_FOUND"
    NOT_EVALUATED = "NOT_EVALUATED"


def derive_comparison_status(
    v1_relation: InterproceduralRelationStatus, v2_relation: InterproceduralRelationStatus
) -> InterproceduralComparisonStatus:
    """Unica fuente de verdad para derivar la clasificacion PRINCIPAL a
    partir de las dos dimensiones independientes -- usada tanto por el
    comparador (`pipeline/interprocedural_rule_comparator.py`) como por
    el validador de coherencia de este contrato, para que ambos nunca
    puedan divergir. Prioridad: MATCHED_V1 > MATCHED_V2 > RELATED_V1 >
    RELATED_V2 > INTERPROCEDURAL_ONLY (solo si AMBAS fuentes se
    evaluaron y ninguna encontro relacion) > NOT_EVALUATED (en cualquier
    otro caso: al menos una fuente no se evaluo y ninguna encontro
    MATCHED/RELATED)."""
    if v1_relation == InterproceduralRelationStatus.MATCHED:
        return InterproceduralComparisonStatus.MATCHED_V1
    if v2_relation == InterproceduralRelationStatus.MATCHED:
        return InterproceduralComparisonStatus.MATCHED_V2
    if v1_relation == InterproceduralRelationStatus.RELATED:
        return InterproceduralComparisonStatus.RELATED_V1
    if v2_relation == InterproceduralRelationStatus.RELATED:
        return InterproceduralComparisonStatus.RELATED_V2
    if (
        v1_relation == InterproceduralRelationStatus.NOT_FOUND
        and v2_relation == InterproceduralRelationStatus.NOT_FOUND
    ):
        return InterproceduralComparisonStatus.INTERPROCEDURAL_ONLY
    return InterproceduralComparisonStatus.NOT_EVALUATED


def _ordered_unique_strings(values: Iterable[str]) -> list[str]:
    return sorted(set(values))


class InterproceduralRuleEvidence(AltamiraBaseModel):
    """UNA pieza de evidencia estructural que sostiene (o explica el
    bloqueo de) un `InterproceduralRuleCandidate`. Un candidato puede
    tener varias -- p. ej. una para el hecho de entrada (`ENTRY_FACT`) y
    otra para el hecho de salida (`RETURNING_FACT`/`BY_REFERENCE_OUTPUT`)
    del mismo call site. Nunca incluye `source_text` completo."""

    evidence_id: str = Field(min_length=1)
    caller_program: str = Field(min_length=1)
    callee_program: str | None = None
    call_site_id: str = Field(min_length=1)
    statement_id: str = Field(min_length=1)
    binding_id: str | None = None
    propagation_fact_ids: list[str] = Field(default_factory=list)
    semantic_effect_ids: list[str] = Field(default_factory=list)
    actual_name: str | None = None
    formal_name: str | None = None
    input_literal: str | None = None
    output_literal: str | None = None
    source_references: list[InterproceduralSourceReference] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_lists_sorted_and_unique(self) -> InterproceduralRuleEvidence:
        label = f"InterproceduralRuleEvidence({self.evidence_id!r})"
        if self.propagation_fact_ids != _ordered_unique_strings(self.propagation_fact_ids):
            raise ValueError(f"{label}: propagation_fact_ids debe estar ordenado y sin duplicados")
        if self.semantic_effect_ids != _ordered_unique_strings(self.semantic_effect_ids):
            raise ValueError(f"{label}: semantic_effect_ids debe estar ordenado y sin duplicados")
        if self.diagnostics != _ordered_unique_strings(self.diagnostics):
            raise ValueError(f"{label}: diagnostics debe estar ordenado y sin duplicados")
        return self


def _check_evidence_ids_unique(
    evidence: Sequence[InterproceduralRuleEvidence], *, label: str
) -> None:
    ids = [item.evidence_id for item in evidence]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{label}: evidence contiene evidence_id duplicado")


class InterproceduralRuleCandidate(AltamiraBaseModel):
    """Un candidato experimental de regla de negocio, detectado
    exclusivamente a partir de hechos interprocedurales ya demostrados
    (Fase 6/7). Nunca se presenta como regla aprobada ni se persiste
    fuera de este artefacto diagnostico (CLAUDE.md, seccion
    'Candidato, fidelidad y aprobacion')."""

    candidate_id: str = Field(min_length=1)
    detector: str = Field(min_length=1)
    rule_type: InterproceduralRuleType
    support: InterproceduralCandidateSupport
    caller_program: str = Field(min_length=1)
    callee_program: str | None = None
    caller_paragraph: str = Field(min_length=1)
    call_site_id: str = Field(min_length=1)
    decision_id: str | None = None
    target: str = Field(min_length=1)
    input_literal: str | None = None
    output_literal: str | None = None
    evidence: list[InterproceduralRuleEvidence] = Field(default_factory=list)
    barriers: list[InterproceduralPropagationBarrier] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_support_coherence(self) -> InterproceduralRuleCandidate:
        label = f"InterproceduralRuleCandidate({self.candidate_id!r})"
        if self.support == InterproceduralCandidateSupport.DETERMINISTIC:
            if self.output_literal is None:
                raise ValueError(f"{label}: DETERMINISTIC exige output_literal")
            if self.barriers:
                raise ValueError(f"{label}: DETERMINISTIC no puede declarar barriers")
        elif self.support == InterproceduralCandidateSupport.BLOCKED:
            if self.output_literal is not None:
                raise ValueError(f"{label}: BLOCKED no puede afirmar output_literal")
            if not self.barriers:
                raise ValueError(f"{label}: BLOCKED exige al menos un barrier")
        else:  # PARTIAL
            if self.barriers:
                raise ValueError(f"{label}: PARTIAL no puede declarar barriers")
        return self

    @model_validator(mode="after")
    def _check_has_evidence(self) -> InterproceduralRuleCandidate:
        if not self.evidence:
            raise ValueError(
                f"InterproceduralRuleCandidate({self.candidate_id!r}): ningun candidato puede "
                "carecer de evidence"
            )
        return self

    @model_validator(mode="after")
    def _check_lists_sorted_and_unique(self) -> InterproceduralRuleCandidate:
        label = f"InterproceduralRuleCandidate({self.candidate_id!r})"
        if self.diagnostics != _ordered_unique_strings(self.diagnostics):
            raise ValueError(f"{label}: diagnostics debe estar ordenado y sin duplicados")
        if list(self.barriers) != sorted(set(self.barriers), key=lambda b: b.value):
            raise ValueError(f"{label}: barriers debe estar ordenado y sin duplicados")
        _check_evidence_ids_unique(self.evidence, label=label)
        return self


class InterproceduralCandidateComparison(AltamiraBaseModel):
    """Comparacion explicable, conservadora y de solo lectura entre UN
    `InterproceduralRuleCandidate` y los candidatos V1/V2 ya calculados.
    Cada `InterproceduralRuleCandidate` tiene EXACTAMENTE una
    comparacion (invariante de artefacto): nunca se compara por
    semejanza textual, solo por identidad estructural ya demostrada
    (mismo programa, mismo decision_id, mismo target, mismo literal).

    `status` es la clasificacion PRINCIPAL, unica y con prioridad
    (`derive_comparison_status`). `v1_relation`/`v2_relation`
    (auditoria de cierre, Fase 8) son las dos dimensiones
    INDEPENDIENTES: permiten saber, sin ambiguedad, si existe relacion
    con V1 y con V2 por separado -- incluso cuando `status=MATCHED_V1`
    porque V1 tiene prioridad, `v2_relation` puede seguir siendo
    `MATCHED`/`RELATED` con su propio `v2_candidate_id`, informacion que
    de otro modo se perderia. `v1_candidate_id`/`v2_candidate_id` ya NO
    son mutuamente excluyentes: cada uno refleja unicamente su propia
    dimension (`v1_relation`/`v2_relation` en MATCHED o RELATED)."""

    comparison_id: str = Field(min_length=1)
    interprocedural_candidate_id: str = Field(min_length=1)
    v1_candidate_id: str | None = None
    v2_candidate_id: str | None = None
    v1_relation: InterproceduralRelationStatus
    v2_relation: InterproceduralRelationStatus
    status: InterproceduralComparisonStatus
    reason: str = Field(min_length=1, max_length=2000)
    shared_program: str | None = None
    shared_decision_id: str | None = None
    shared_target: str | None = None
    shared_literal: str | None = None

    @model_validator(mode="after")
    def _check_status_coherence(self) -> InterproceduralCandidateComparison:
        label = f"InterproceduralCandidateComparison({self.comparison_id!r})"
        if self.status == InterproceduralComparisonStatus.BLOCKED:
            if self.v1_relation != InterproceduralRelationStatus.NOT_EVALUATED:
                raise ValueError(f"{label}: BLOCKED exige v1_relation=NOT_EVALUATED")
            if self.v2_relation != InterproceduralRelationStatus.NOT_EVALUATED:
                raise ValueError(f"{label}: BLOCKED exige v2_relation=NOT_EVALUATED")
            if self.v1_candidate_id is not None or self.v2_candidate_id is not None:
                raise ValueError(
                    f"{label}: BLOCKED no puede declarar v1_candidate_id ni v2_candidate_id"
                )
            return self

        _relation_requires_id = (
            InterproceduralRelationStatus.MATCHED,
            InterproceduralRelationStatus.RELATED,
        )
        if self.v1_relation in _relation_requires_id:
            if self.v1_candidate_id is None:
                raise ValueError(
                    f"{label}: v1_relation={self.v1_relation.value} exige v1_candidate_id"
                )
        elif self.v1_candidate_id is not None:
            raise ValueError(
                f"{label}: v1_relation={self.v1_relation.value} no puede declarar v1_candidate_id"
            )
        if self.v2_relation in _relation_requires_id:
            if self.v2_candidate_id is None:
                raise ValueError(
                    f"{label}: v2_relation={self.v2_relation.value} exige v2_candidate_id"
                )
        elif self.v2_candidate_id is not None:
            raise ValueError(
                f"{label}: v2_relation={self.v2_relation.value} no puede declarar v2_candidate_id"
            )

        expected_status = derive_comparison_status(self.v1_relation, self.v2_relation)
        if self.status != expected_status:
            raise ValueError(
                f"{label}: status={self.status.value} no coincide con la derivacion "
                f"determinista a partir de v1_relation={self.v1_relation.value}/"
                f"v2_relation={self.v2_relation.value} (esperado: {expected_status.value})"
            )
        return self

    @model_validator(mode="after")
    def _check_interprocedural_only_requires_both_sources_evaluated(
        self,
    ) -> InterproceduralCandidateComparison:
        """Regla C de la auditoria de cierre: INTERPROCEDURAL_ONLY solo
        es alcanzable cuando `derive_comparison_status` decide que ambas
        fuentes se evaluaron (NOT_FOUND, nunca NOT_EVALUATED) -- ya
        aplicado por construccion en `_check_status_coherence`, pero se
        deja como comprobacion explicita y redundante (defensiva) porque
        es la regla mas critica de esta auditoria: nunca disfrazar una
        fuente ausente de "sin equivalente"."""
        if self.status == InterproceduralComparisonStatus.INTERPROCEDURAL_ONLY:
            label = f"InterproceduralCandidateComparison({self.comparison_id!r})"
            if self.v1_relation != InterproceduralRelationStatus.NOT_FOUND:
                raise ValueError(f"{label}: INTERPROCEDURAL_ONLY exige v1_relation=NOT_FOUND")
            if self.v2_relation != InterproceduralRelationStatus.NOT_FOUND:
                raise ValueError(f"{label}: INTERPROCEDURAL_ONLY exige v2_relation=NOT_FOUND")
        return self


class InterproceduralRuleCandidatesSummary(AltamiraBaseModel):
    """`deterministic_count`/`partial_count`/`blocked_count` particionan
    `candidate_count` por `support`. `matched_v1_count`/
    `matched_v2_count`/`related_v1_count`/`related_v2_count`/
    `interprocedural_only_count`/`not_evaluated_count`/`blocked_count`
    (reutilizado, nunca duplicado como campo nuevo: un candidato BLOCKED
    siempre tiene comparacion `status=BLOCKED`, la misma poblacion)
    particionan `candidate_count` por resultado de comparacion
    (`InterproceduralComparisonStatus`, la clasificacion PRINCIPAL).

    `not_evaluated_count` (auditoria de cierre, Fase 8) es DISTINTO de
    `interprocedural_only_count`: el primero cuenta candidatos donde al
    menos una fuente (V1 o V2) nunca estuvo disponible para el run; el
    segundo cuenta candidatos donde AMBAS fuentes se evaluaron
    completamente y ninguna encontro relacion -- el summary nunca
    confunde "cero matches" (interprocedural_only) con "comparacion no
    disponible" (not_evaluated), regla F de la auditoria de cierre."""

    candidate_count: int = Field(ge=0)
    deterministic_count: int = Field(ge=0)
    partial_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    counts_by_detector: dict[str, int] = Field(default_factory=dict)
    counts_by_rule_type: dict[InterproceduralRuleType, int] = Field(default_factory=dict)
    matched_v1_count: int = Field(ge=0)
    matched_v2_count: int = Field(ge=0)
    related_v1_count: int = Field(ge=0)
    related_v2_count: int = Field(ge=0)
    interprocedural_only_count: int = Field(ge=0)
    not_evaluated_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _check_counts_non_negative(self) -> InterproceduralRuleCandidatesSummary:
        for detector_id, count in self.counts_by_detector.items():
            if count < 0:
                raise ValueError(f"counts_by_detector[{detector_id}] no puede ser negativo")
        for rule_type, count in self.counts_by_rule_type.items():
            if count < 0:
                raise ValueError(f"counts_by_rule_type[{rule_type.value}] no puede ser negativo")
        return self

    @model_validator(mode="after")
    def _check_support_partition(self) -> InterproceduralRuleCandidatesSummary:
        if self.deterministic_count + self.partial_count + self.blocked_count != (
            self.candidate_count
        ):
            raise ValueError(
                "deterministic_count + partial_count + blocked_count no coincide con "
                "candidate_count"
            )
        return self

    @model_validator(mode="after")
    def _check_comparison_partition(self) -> InterproceduralRuleCandidatesSummary:
        total = (
            self.matched_v1_count
            + self.matched_v2_count
            + self.related_v1_count
            + self.related_v2_count
            + self.interprocedural_only_count
            + self.not_evaluated_count
            + self.blocked_count
        )
        if total != self.candidate_count:
            raise ValueError(
                "matched_v1_count + matched_v2_count + related_v1_count + related_v2_count + "
                "interprocedural_only_count + not_evaluated_count + blocked_count no coincide "
                "con candidate_count"
            )
        return self

    @model_validator(mode="after")
    def _check_sums_match_named_counts(self) -> InterproceduralRuleCandidatesSummary:
        if sum(self.counts_by_detector.values()) != self.candidate_count:
            raise ValueError("suma de counts_by_detector no coincide con candidate_count")
        if sum(self.counts_by_rule_type.values()) != self.candidate_count:
            raise ValueError("suma de counts_by_rule_type no coincide con candidate_count")
        return self


class InterproceduralRuleCandidatesArtifact(AltamiraBaseModel):
    """Contenedor persistido en `<run_dir>/diagnostics/
    interprocedural-rule-candidates-shadow.json`. NO contractual, sin
    timestamps, sin `source_text` completo, sin rutas absolutas: dos
    ejecuciones sobre los mismos artefactos de entrada deben producir
    bytes identicos.

    `canonical_schema_versions`/`semantic_effects_schema_version`/
    `semantic_propagation_schema_version`/
    `interprocedural_call_linkage_schema_version`/
    `interprocedural_propagation_schema_version` registran la version de
    los artefactos de entrada calculados en memoria (mismo patron que
    `V2ShadowCandidatesArtifact`/`InterproceduralPropagationArtifact`) --
    ninguno se lee de `diagnostics/*.json` del disco, asi que estos
    campos son la unica procedencia disponible."""

    schema_version: Literal["1.0"] = "1.0"
    analyzer_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    source_package_hash: Sha256Hex
    source_artifact_hashes: dict[str, Sha256Hex] = Field(min_length=1)
    canonical_schema_versions: list[Literal["1.0", "1.1", "1.2", "1.3"]] = Field(
        default_factory=list
    )
    semantic_effects_schema_version: Literal["1.0", "1.1", "1.2"]
    semantic_propagation_schema_version: Literal["1.0", "1.1"]
    interprocedural_call_linkage_schema_version: Literal["1.0"]
    interprocedural_propagation_schema_version: Literal["1.0"]
    summary: InterproceduralRuleCandidatesSummary
    candidates: list[InterproceduralRuleCandidate] = Field(default_factory=list)
    comparisons: list[InterproceduralCandidateComparison] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_canonical_schema_versions_sorted_and_unique(
        self,
    ) -> InterproceduralRuleCandidatesArtifact:
        if list(self.canonical_schema_versions) != sorted(set(self.canonical_schema_versions)):
            raise ValueError(
                "canonical_schema_versions debe estar ordenado alfabeticamente y sin duplicados"
            )
        return self

    @model_validator(mode="after")
    def _check_candidates_sorted_and_unique(self) -> InterproceduralRuleCandidatesArtifact:
        ids = [candidate.candidate_id for candidate in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("candidates contiene candidate_id duplicado")
        if ids != sorted(ids):
            raise ValueError("candidates no esta ordenado deterministicamente por candidate_id")
        return self

    @model_validator(mode="after")
    def _check_comparisons_sorted_and_unique(self) -> InterproceduralRuleCandidatesArtifact:
        ids = [comparison.comparison_id for comparison in self.comparisons]
        if len(ids) != len(set(ids)):
            raise ValueError("comparisons contiene comparison_id duplicado")
        if ids != sorted(ids):
            raise ValueError("comparisons no esta ordenado deterministicamente por comparison_id")
        return self

    @model_validator(mode="after")
    def _check_diagnostics_sorted_and_unique(self) -> InterproceduralRuleCandidatesArtifact:
        if self.diagnostics != _ordered_unique_strings(self.diagnostics):
            raise ValueError("diagnostics debe estar ordenado alfabeticamente y sin duplicados")
        return self

    @model_validator(mode="after")
    def _check_every_candidate_has_exactly_one_comparison(
        self,
    ) -> InterproceduralRuleCandidatesArtifact:
        candidate_ids = {candidate.candidate_id for candidate in self.candidates}
        seen: dict[str, str] = {}
        for comparison in self.comparisons:
            target = comparison.interprocedural_candidate_id
            if target not in candidate_ids:
                raise ValueError(
                    f"comparison {comparison.comparison_id!r} referencia un "
                    f"interprocedural_candidate_id inexistente: {target!r}"
                )
            previous = seen.get(target)
            if previous is not None:
                raise ValueError(
                    f"candidato {target!r} aparece en mas de una comparacion "
                    f"({previous!r} y {comparison.comparison_id!r})"
                )
            seen[target] = comparison.comparison_id
        missing = candidate_ids - set(seen)
        if missing:
            raise ValueError(f"candidatos sin comparacion: {sorted(missing)}")
        return self

    @model_validator(mode="after")
    def _check_no_semantic_duplicates(self) -> InterproceduralRuleCandidatesArtifact:
        seen: dict[tuple[object, ...], str] = {}
        for candidate in self.candidates:
            key = (
                candidate.rule_type,
                candidate.caller_program,
                candidate.callee_program,
                candidate.call_site_id,
                candidate.target,
                candidate.output_literal,
                candidate.support,
            )
            previous = seen.get(key)
            if previous is not None:
                raise ValueError(
                    f"candidatos {previous!r} y {candidate.candidate_id!r} son duplicados "
                    "semanticos (mismo rule_type/caller/callee/call_site/target/output_literal)"
                )
            seen[key] = candidate.candidate_id
        return self

    @model_validator(mode="after")
    def _check_summary_matches_content(self) -> InterproceduralRuleCandidatesArtifact:
        if self.summary.candidate_count != len(self.candidates):
            raise ValueError(
                f"summary.candidate_count ({self.summary.candidate_count}) != cantidad real de "
                f"candidates ({len(self.candidates)})"
            )

        expected_support: dict[InterproceduralCandidateSupport, int] = {}
        expected_by_detector: dict[str, int] = {}
        expected_by_rule_type: dict[InterproceduralRuleType, int] = {}
        for candidate in self.candidates:
            expected_support[candidate.support] = expected_support.get(candidate.support, 0) + 1
            expected_by_detector[candidate.detector] = (
                expected_by_detector.get(candidate.detector, 0) + 1
            )
            expected_by_rule_type[candidate.rule_type] = (
                expected_by_rule_type.get(candidate.rule_type, 0) + 1
            )

        if self.summary.deterministic_count != expected_support.get(
            InterproceduralCandidateSupport.DETERMINISTIC, 0
        ):
            raise ValueError("summary.deterministic_count no coincide con la agregacion real")
        if self.summary.partial_count != expected_support.get(
            InterproceduralCandidateSupport.PARTIAL, 0
        ):
            raise ValueError("summary.partial_count no coincide con la agregacion real")
        if self.summary.blocked_count != expected_support.get(
            InterproceduralCandidateSupport.BLOCKED, 0
        ):
            raise ValueError("summary.blocked_count no coincide con la agregacion real")
        if self.summary.counts_by_detector != expected_by_detector:
            raise ValueError("summary.counts_by_detector no coincide con la agregacion real")
        if self.summary.counts_by_rule_type != expected_by_rule_type:
            raise ValueError("summary.counts_by_rule_type no coincide con la agregacion real")

        expected_comparison: dict[InterproceduralComparisonStatus, int] = {}
        for comparison in self.comparisons:
            expected_comparison[comparison.status] = (
                expected_comparison.get(comparison.status, 0) + 1
            )
        if self.summary.matched_v1_count != expected_comparison.get(
            InterproceduralComparisonStatus.MATCHED_V1, 0
        ):
            raise ValueError("summary.matched_v1_count no coincide con la agregacion real")
        if self.summary.matched_v2_count != expected_comparison.get(
            InterproceduralComparisonStatus.MATCHED_V2, 0
        ):
            raise ValueError("summary.matched_v2_count no coincide con la agregacion real")
        if self.summary.related_v1_count != expected_comparison.get(
            InterproceduralComparisonStatus.RELATED_V1, 0
        ):
            raise ValueError("summary.related_v1_count no coincide con la agregacion real")
        if self.summary.related_v2_count != expected_comparison.get(
            InterproceduralComparisonStatus.RELATED_V2, 0
        ):
            raise ValueError("summary.related_v2_count no coincide con la agregacion real")
        if self.summary.interprocedural_only_count != expected_comparison.get(
            InterproceduralComparisonStatus.INTERPROCEDURAL_ONLY, 0
        ):
            raise ValueError(
                "summary.interprocedural_only_count no coincide con la agregacion real"
            )
        if self.summary.not_evaluated_count != expected_comparison.get(
            InterproceduralComparisonStatus.NOT_EVALUATED, 0
        ):
            raise ValueError("summary.not_evaluated_count no coincide con la agregacion real")
        return self
