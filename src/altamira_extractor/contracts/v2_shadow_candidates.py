"""Contrato tipado de detectores V2 ejecutados en shadow mode (Fase 5 de
la ampliacion semantica, `feat/v2-detectors-shadow-mode`).

Diagnostico, NO contractual: persiste en `<run_dir>/diagnostics/
v2-candidates-shadow.json`, fuera de `artifacts/01-10` -- mismo patron que
`contracts/semantic_coverage.py`, `contracts/semantic_effects.py` y
`contracts/semantic_propagation.py`: un artefacto adyacente, opcional,
irrelevante para que un run llegue a COMPLETED, generado exclusivamente
bajo demanda.

Modelo DEPENDIENTE de `CandidateArtifact` V1 (`contracts/candidate.py`,
usado como baseline YA CALCULADO, nunca reejecutado ni reinterpretado),
`SemanticGraph` (`contracts/semantic_graph.py`, cargado desde
`artifacts/04-semantic-graph.json`), `SemanticEffectsArtifact` y
`SemanticPropagationArtifact` (ambos calculados en memoria, igual que
`semantic_propagation_service.py` hace con `SemanticEffectsArtifact`).
Nunca modifica ninguno de esos artefactos: solo AGREGA una capa de
candidatos experimentales por encima de evidencia que ya existe de forma
independiente.

Un `V2ShadowCandidate` nunca alimenta `ContextPackage`, generacion LLM,
guardrails, Markdown, el ZIP final, `CandidateArtifact` V1 ni Neo4j -- ver
docs/V2_DETECTORS_SHADOW_MODE.md."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, RelativePath, Sha256Hex


class V2RuleType(StrEnum):
    """Tipo de regla funcional que un detector V2 afirma haber
    demostrado. Solo se declaran los tipos con semantica ya completamente
    modelada por las fases anteriores (Fase 2 SemanticEffects + Fase 3
    nivel 88 + Fase 4 SemanticPropagation); THRESHOLD_RULE/
    PERSISTENCE_RULE/ROUTING_RULE/AUTHORIZATION_RULE/FILE_OUTPUT_RULE/
    PARAMETER_DEPENDENT_RULE quedan deliberadamente fuera hasta que exista
    esa semantica (docs/V2_DETECTORS_SHADOW_MODE.md).

    `CALCULATION_RULE` (Fase 15B3-C2-B1) cubre COMPUTE/ADD/SUBTRACT/
    MULTIPLY/DIVIDE con un unico tipo -- nunca un tipo por verbo (el verbo
    original se preserva via `StatementKind`, nunca duplicado aqui)."""

    RETURN_CODE_RULE = "RETURN_CODE_RULE"
    LEVEL_88_RETURN_CODE_RULE = "LEVEL_88_RETURN_CODE_RULE"
    STATE_CHANGE_RULE = "STATE_CHANGE_RULE"
    CALCULATION_RULE = "CALCULATION_RULE"


class V2CandidateSupport(StrEnum):
    """Nivel de soporte de un `V2ShadowCandidate`. Distinto de
    `SemanticSupportStatus` (Fase 1/2): aqui describe si el CANDIDATO en
    si mismo tiene evidencia suficiente para presentarse como
    demostrado, no cuanto de un `CanonicalStatement` fue interpretado."""

    DETERMINISTIC = "DETERMINISTIC"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"


def _ordered_unique_strings(values: Iterable[str]) -> list[str]:
    return sorted(set(values))


class V2CandidateSourceReference(AltamiraBaseModel):
    """Procedencia de un `V2ShadowCandidate` hasta el `CanonicalStatement`
    que lo origino. Nunca incluye `source_text` (mismo principio que
    `SemanticEffectSourceReference`/`PropagationSourceReference`)."""

    program: str = Field(min_length=1)
    paragraph: str = Field(min_length=1)
    statement_id: str | None = None
    effect_id: str | None = None
    fact_id: str | None = None
    decision_id: str | None = None
    source_file: RelativePath | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    branch_kind: str | None = None

    @model_validator(mode="after")
    def _check_line_order(self) -> V2CandidateSourceReference:
        if self.line_start is not None and self.line_end is not None:
            if self.line_end < self.line_start:
                raise ValueError("line_end no puede ser anterior a line_start")
        return self

    def _sort_key(self) -> tuple[str, str, str, str, str, str]:
        return (
            self.program,
            self.paragraph,
            self.statement_id or "",
            self.effect_id or "",
            self.fact_id or "",
            self.decision_id or "",
        )


def _check_source_references_sorted_and_unique(
    references: Sequence[V2CandidateSourceReference], *, label: str
) -> None:
    keys = [reference._sort_key() for reference in references]  # noqa: SLF001
    if len(keys) != len(set(keys)):
        raise ValueError(f"{label}: source_references contiene una referencia duplicada")
    if keys != sorted(keys):
        raise ValueError(f"{label}: source_references no esta ordenado deterministicamente")


class V2ShadowCandidate(AltamiraBaseModel):
    """Un candidato experimental detectado por un detector V2. Nunca se
    presenta como regla aprobada ni se persiste fuera de este artefacto
    diagnostico (CLAUDE.md, seccion 'Candidato, fidelidad y aprobacion')."""

    candidate_id: str = Field(min_length=1)
    detector_id: str = Field(min_length=1)
    detector_version: str = Field(min_length=1)
    rule_type: V2RuleType
    support: V2CandidateSupport
    detector_score: float = Field(ge=0.0, le=1.0)
    program: str = Field(min_length=1)
    paragraph: str = Field(min_length=1)
    anchor_statement_id: str = Field(min_length=1)
    decision_id: str | None = None
    condition_name: str | None = None
    target_variable: str = Field(min_length=1)
    target_qualified_name: str | None = None
    resolved_literal: str | None = None
    semantic_effect_ids: list[str] = Field(default_factory=list)
    propagation_fact_ids: list[str] = Field(default_factory=list)
    source_references: list[V2CandidateSourceReference] = Field(default_factory=list)
    diagnostic_codes: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=2000)
    comparable_v1_candidate_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_score_and_support_coherence(self) -> V2ShadowCandidate:
        label = f"V2ShadowCandidate({self.candidate_id!r})"
        if self.support == V2CandidateSupport.DETERMINISTIC:
            if self.detector_score != 1.0:
                raise ValueError(f"{label}: DETERMINISTIC exige detector_score == 1.0")
            if self.resolved_literal is None:
                raise ValueError(f"{label}: DETERMINISTIC exige resolved_literal")
            if not self.semantic_effect_ids:
                raise ValueError(f"{label}: DETERMINISTIC exige al menos un semantic_effect_id")
            if not self.propagation_fact_ids:
                raise ValueError(f"{label}: DETERMINISTIC exige al menos un propagation_fact_id")
            if not self.source_references:
                raise ValueError(f"{label}: DETERMINISTIC exige al menos un source_reference")
        elif self.support == V2CandidateSupport.BLOCKED:
            if self.resolved_literal is not None:
                raise ValueError(f"{label}: BLOCKED no puede afirmar resolved_literal")
            if not self.diagnostic_codes:
                raise ValueError(f"{label}: BLOCKED exige al menos un diagnostic_code")
        return self

    @model_validator(mode="after")
    def _check_lists_sorted_and_unique(self) -> V2ShadowCandidate:
        label = f"V2ShadowCandidate({self.candidate_id!r})"
        if self.semantic_effect_ids != _ordered_unique_strings(self.semantic_effect_ids):
            raise ValueError(f"{label}: semantic_effect_ids debe estar ordenado y sin duplicados")
        if self.propagation_fact_ids != _ordered_unique_strings(self.propagation_fact_ids):
            raise ValueError(f"{label}: propagation_fact_ids debe estar ordenado y sin duplicados")
        if self.diagnostic_codes != _ordered_unique_strings(self.diagnostic_codes):
            raise ValueError(f"{label}: diagnostic_codes debe estar ordenado y sin duplicados")
        if self.comparable_v1_candidate_ids != _ordered_unique_strings(
            self.comparable_v1_candidate_ids
        ):
            raise ValueError(
                f"{label}: comparable_v1_candidate_ids debe estar ordenado y sin duplicados"
            )
        _check_source_references_sorted_and_unique(self.source_references, label=label)
        return self


def _candidates_sort_key(candidate: V2ShadowCandidate) -> str:
    return candidate.candidate_id


def _check_candidates_sorted_and_unique(
    candidates: Sequence[V2ShadowCandidate], *, label: str
) -> None:
    ids = [candidate.candidate_id for candidate in candidates]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{label}: candidates contiene candidate_id duplicado")
    if ids != sorted(ids):
        raise ValueError(f"{label}: candidates no esta ordenado deterministicamente")


class V2DetectorExecution(AltamiraBaseModel):
    """Resultado de ejecutar UN detector V2 sobre `V2DetectorContext`."""

    detector_id: str = Field(min_length=1)
    detector_version: str = Field(min_length=1)
    rule_type: V2RuleType
    candidate_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    candidates: list[V2ShadowCandidate] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_counts(self) -> V2DetectorExecution:
        label = f"V2DetectorExecution({self.detector_id!r})"
        if self.candidate_count != len(self.candidates):
            raise ValueError(
                f"{label}: candidate_count ({self.candidate_count}) != "
                f"len(candidates) ({len(self.candidates)})"
            )
        expected_blocked = sum(
            1 for candidate in self.candidates if candidate.support == V2CandidateSupport.BLOCKED
        )
        if self.blocked_count != expected_blocked:
            raise ValueError(
                f"{label}: blocked_count ({self.blocked_count}) != "
                f"cantidad real de candidatos BLOCKED ({expected_blocked})"
            )
        return self

    @model_validator(mode="after")
    def _check_candidates_ordered(self) -> V2DetectorExecution:
        _check_candidates_sorted_and_unique(
            self.candidates, label=f"V2DetectorExecution({self.detector_id!r})"
        )
        return self

    @model_validator(mode="after")
    def _check_diagnostics_ordered(self) -> V2DetectorExecution:
        if self.diagnostics != _ordered_unique_strings(self.diagnostics):
            raise ValueError(
                f"V2DetectorExecution({self.detector_id!r}): diagnostics debe estar "
                "ordenado alfabeticamente y sin duplicados"
            )
        return self

    @model_validator(mode="after")
    def _check_candidates_belong_to_detector(self) -> V2DetectorExecution:
        for candidate in self.candidates:
            if candidate.detector_id != self.detector_id:
                raise ValueError(
                    f"V2DetectorExecution({self.detector_id!r}): candidato "
                    f"{candidate.candidate_id!r} pertenece a otro detector "
                    f"({candidate.detector_id!r})"
                )
            if candidate.rule_type != self.rule_type:
                raise ValueError(
                    f"V2DetectorExecution({self.detector_id!r}): candidato "
                    f"{candidate.candidate_id!r} tiene rule_type distinto del detector"
                )
        return self


class V1V2ComparisonStatus(StrEnum):
    MATCHED = "MATCHED"
    V1_ONLY = "V1_ONLY"
    V2_ONLY = "V2_ONLY"
    RELATED_NOT_EQUIVALENT = "RELATED_NOT_EQUIVALENT"


class V1V2CandidateComparison(AltamiraBaseModel):
    """Una comparacion explicable entre uno o mas `RuleCandidate` V1 y uno
    o mas `V2ShadowCandidate`. Nunca modifica ninguno de los dos lados:
    es una anotacion de solo lectura sobre evidencia ya calculada."""

    comparison_id: str = Field(min_length=1)
    status: V1V2ComparisonStatus
    v1_candidate_ids: list[str] = Field(default_factory=list)
    v2_candidate_ids: list[str] = Field(default_factory=list)
    program: str = Field(min_length=1)
    paragraph: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _check_status_coherence(self) -> V1V2CandidateComparison:
        label = f"V1V2CandidateComparison({self.comparison_id!r})"
        has_v1 = bool(self.v1_candidate_ids)
        has_v2 = bool(self.v2_candidate_ids)
        if self.status == V1V2ComparisonStatus.MATCHED:
            if not has_v1 or not has_v2:
                raise ValueError(f"{label}: MATCHED exige v1 y v2 no vacios")
        elif self.status == V1V2ComparisonStatus.V1_ONLY:
            if not has_v1 or has_v2:
                raise ValueError(f"{label}: V1_ONLY exige v1 no vacio y v2 vacio")
        elif self.status == V1V2ComparisonStatus.V2_ONLY:
            if has_v1 or not has_v2:
                raise ValueError(f"{label}: V2_ONLY exige v2 no vacio y v1 vacio")
        elif self.status == V1V2ComparisonStatus.RELATED_NOT_EQUIVALENT:
            # Dos formas validas (Fase 10): (a) misma Decision/target en
            # V1 y V2 pero evidencia/literal no verificablemente iguales
            # (v1 y v2 no vacios); (b) dos detectores V2 DISTINTOS sobre
            # el mismo efecto funcional (v1 puede estar vacio si Q0 nunca
            # lo detecto, pero entonces v2 debe tener mas de un
            # candidato -- si no, no hay nada que relacionar).
            if not has_v2:
                raise ValueError(f"{label}: RELATED_NOT_EQUIVALENT exige v2 no vacio")
            if not has_v1 and len(self.v2_candidate_ids) < 2:
                raise ValueError(
                    f"{label}: RELATED_NOT_EQUIVALENT sin v1 exige al menos dos "
                    "v2_candidate_ids (relacion entre detectores V2 distintos)"
                )
        return self

    @model_validator(mode="after")
    def _check_lists_sorted_and_unique(self) -> V1V2CandidateComparison:
        label = f"V1V2CandidateComparison({self.comparison_id!r})"
        if self.v1_candidate_ids != _ordered_unique_strings(self.v1_candidate_ids):
            raise ValueError(f"{label}: v1_candidate_ids debe estar ordenado y sin duplicados")
        if self.v2_candidate_ids != _ordered_unique_strings(self.v2_candidate_ids):
            raise ValueError(f"{label}: v2_candidate_ids debe estar ordenado y sin duplicados")
        return self


class V2ShadowSummary(AltamiraBaseModel):
    detector_count: int = Field(ge=0)
    v1_candidate_count: int = Field(ge=0)
    v2_candidate_count: int = Field(ge=0)
    deterministic_count: int = Field(ge=0)
    partial_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    matched_count: int = Field(ge=0)
    v1_only_count: int = Field(ge=0)
    v2_only_count: int = Field(ge=0)
    related_not_equivalent_count: int = Field(ge=0)
    counts_by_rule_type: dict[V2RuleType, int] = Field(default_factory=dict)
    counts_by_detector: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_counts_non_negative_and_consistent(self) -> V2ShadowSummary:
        for rule_type, count in self.counts_by_rule_type.items():
            if count < 0:
                raise ValueError(f"counts_by_rule_type[{rule_type.value}] no puede ser negativo")
        for detector_id, count in self.counts_by_detector.items():
            if count < 0:
                raise ValueError(f"counts_by_detector[{detector_id}] no puede ser negativo")

        if sum(self.counts_by_rule_type.values()) != self.v2_candidate_count:
            raise ValueError("suma de counts_by_rule_type no coincide con v2_candidate_count")
        if sum(self.counts_by_detector.values()) != self.v2_candidate_count:
            raise ValueError("suma de counts_by_detector no coincide con v2_candidate_count")

        expected_support_sum = self.deterministic_count + self.partial_count + self.blocked_count
        if expected_support_sum != self.v2_candidate_count:
            raise ValueError(
                "deterministic_count + partial_count + blocked_count no coincide con "
                "v2_candidate_count"
            )

        expected_comparison_sum = (
            self.matched_count
            + self.v1_only_count
            + self.v2_only_count
            + self.related_not_equivalent_count
        )
        if expected_comparison_sum <= 0 and (self.v1_candidate_count or self.v2_candidate_count):
            # Si hay candidatos de cualquier lado, debe existir al menos una
            # comparacion que los explique (aunque sea V1_ONLY/V2_ONLY).
            raise ValueError(
                "existen candidatos V1 y/o V2 pero ninguna comparacion los explica"
            )
        return self


class V2ShadowCandidatesArtifact(AltamiraBaseModel):
    """Contenedor persistido en `<run_dir>/diagnostics/
    v2-candidates-shadow.json`. NO contractual, sin timestamps: dos
    ejecuciones sobre los mismos artefactos de entrada deben producir
    bytes identicos.

    `semantic_effects_schema_version`/`semantic_effects_analyzer_version`/
    `semantic_propagation_schema_version`/
    `semantic_propagation_analyzer_version` registran la version de los
    dos artefactos calculados en memoria (mismo patron que
    `SemanticPropagationArtifact.semantic_effects_schema_version`) que
    sirvieron de entrada a los detectores -- nunca se leen los archivos
    `diagnostics/semantic-effects.json`/`diagnostics/semantic-
    propagation.json` del disco, asi que estos campos son la unica
    procedencia disponible de esas versiones."""

    schema_version: Literal["1.0"] = "1.0"
    analyzer_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    source_package_hash: Sha256Hex
    source_artifact_hashes: dict[str, Sha256Hex] = Field(min_length=1)
    semantic_effects_schema_version: Literal["1.0", "1.1", "1.2"]
    semantic_effects_analyzer_version: Literal["1.0", "1.1", "1.2"]
    semantic_propagation_schema_version: Literal["1.0", "1.1"]
    semantic_propagation_analyzer_version: Literal["1.0", "1.1"]
    summary: V2ShadowSummary
    executions: list[V2DetectorExecution] = Field(default_factory=list)
    comparisons: list[V1V2CandidateComparison] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_executions_sorted_and_unique(self) -> V2ShadowCandidatesArtifact:
        ids = [execution.detector_id for execution in self.executions]
        if len(ids) != len(set(ids)):
            raise ValueError("executions contiene detector_id duplicado")
        if ids != sorted(ids):
            raise ValueError("executions no esta ordenado deterministicamente por detector_id")
        return self

    @model_validator(mode="after")
    def _check_comparisons_sorted_and_unique(self) -> V2ShadowCandidatesArtifact:
        ids = [comparison.comparison_id for comparison in self.comparisons]
        if len(ids) != len(set(ids)):
            raise ValueError("comparisons contiene comparison_id duplicado")
        if ids != sorted(ids):
            raise ValueError("comparisons no esta ordenado deterministicamente por comparison_id")
        return self

    @model_validator(mode="after")
    def _check_no_candidate_appears_in_multiple_comparisons(self) -> V2ShadowCandidatesArtifact:
        """Cada `RuleCandidate` V1 y cada `V2ShadowCandidate` debe quedar
        clasificado en EXACTAMENTE una comparacion (nunca dos, nunca
        ninguna categoria incompatible simultanea) -- garantiza que
        `matched_count`/`v1_only_count`/`v2_only_count`/
        `related_not_equivalent_count` particionan los candidatos sin
        doble conteo, verificable independientemente de como
        `v2_shadow_detector.py` construyo las comparaciones."""
        seen_v1: dict[str, str] = {}
        seen_v2: dict[str, str] = {}
        for comparison in self.comparisons:
            for candidate_id in comparison.v1_candidate_ids:
                previous = seen_v1.get(candidate_id)
                if previous is not None:
                    raise ValueError(
                        f"candidato V1 {candidate_id!r} aparece en mas de una comparacion "
                        f"({previous!r} y {comparison.comparison_id!r})"
                    )
                seen_v1[candidate_id] = comparison.comparison_id
            for candidate_id in comparison.v2_candidate_ids:
                previous = seen_v2.get(candidate_id)
                if previous is not None:
                    raise ValueError(
                        f"candidato V2 {candidate_id!r} aparece en mas de una comparacion "
                        f"({previous!r} y {comparison.comparison_id!r})"
                    )
                seen_v2[candidate_id] = comparison.comparison_id
        return self

    @model_validator(mode="after")
    def _check_summary_matches_executions_and_comparisons(self) -> V2ShadowCandidatesArtifact:
        if self.summary.detector_count != len(self.executions):
            raise ValueError(
                f"summary.detector_count ({self.summary.detector_count}) != "
                f"cantidad de executions ({len(self.executions)})"
            )
        all_candidates = [
            candidate for execution in self.executions for candidate in execution.candidates
        ]
        expected_v2_count = len(all_candidates)
        if self.summary.v2_candidate_count != expected_v2_count:
            raise ValueError(
                f"summary.v2_candidate_count ({self.summary.v2_candidate_count}) no coincide "
                f"con la cantidad real de candidatos V2 ({expected_v2_count})"
            )

        expected_by_rule_type: dict[V2RuleType, int] = {}
        expected_by_detector: dict[str, int] = {}
        expected_support: dict[V2CandidateSupport, int] = {}
        for candidate in all_candidates:
            expected_by_rule_type[candidate.rule_type] = (
                expected_by_rule_type.get(candidate.rule_type, 0) + 1
            )
            expected_by_detector[candidate.detector_id] = (
                expected_by_detector.get(candidate.detector_id, 0) + 1
            )
            expected_support[candidate.support] = expected_support.get(candidate.support, 0) + 1
        if self.summary.counts_by_rule_type != expected_by_rule_type:
            raise ValueError("summary.counts_by_rule_type no coincide con la agregacion real")
        if self.summary.counts_by_detector != expected_by_detector:
            raise ValueError("summary.counts_by_detector no coincide con la agregacion real")
        if self.summary.deterministic_count != expected_support.get(
            V2CandidateSupport.DETERMINISTIC, 0
        ):
            raise ValueError("summary.deterministic_count no coincide con la agregacion real")
        if self.summary.partial_count != expected_support.get(V2CandidateSupport.PARTIAL, 0):
            raise ValueError("summary.partial_count no coincide con la agregacion real")
        if self.summary.blocked_count != expected_support.get(V2CandidateSupport.BLOCKED, 0):
            raise ValueError("summary.blocked_count no coincide con la agregacion real")

        expected_comparison_counts: dict[V1V2ComparisonStatus, int] = {}
        for comparison in self.comparisons:
            expected_comparison_counts[comparison.status] = (
                expected_comparison_counts.get(comparison.status, 0) + 1
            )
        if self.summary.matched_count != expected_comparison_counts.get(
            V1V2ComparisonStatus.MATCHED, 0
        ):
            raise ValueError("summary.matched_count no coincide con la agregacion real")
        if self.summary.v1_only_count != expected_comparison_counts.get(
            V1V2ComparisonStatus.V1_ONLY, 0
        ):
            raise ValueError("summary.v1_only_count no coincide con la agregacion real")
        if self.summary.v2_only_count != expected_comparison_counts.get(
            V1V2ComparisonStatus.V2_ONLY, 0
        ):
            raise ValueError("summary.v2_only_count no coincide con la agregacion real")
        if self.summary.related_not_equivalent_count != expected_comparison_counts.get(
            V1V2ComparisonStatus.RELATED_NOT_EQUIVALENT, 0
        ):
            raise ValueError(
                "summary.related_not_equivalent_count no coincide con la agregacion real"
            )
        return self
