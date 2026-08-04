"""Contrato tipado del artefacto de ejecucion downstream del artefacto
unificado de candidatos en shadow mode (Fase 13 de la ampliacion
semantica, `feat/unified-shadow-downstream-pipeline`).

Diagnostico, NO contractual para el pipeline V1: persiste en
`<run_dir>/diagnostics/unified-shadow-downstream.json`. Consume un
`UnifiedShadowValidationReport` (Fase 12) YA `QUALIFIED_FOR_DOWNSTREAM_
SHADOW`/`QUALIFIED_WITH_WARNINGS` y ejecuta, EXCLUSIVAMENTE para los
grupos `downstream_shadow_eligible=true`, el mismo flujo productivo
ContextPackage -> RuleDraft -> Guardrails -- pero envolviendolo, nunca
reemplazandolo: cada `ContextPackage`/`RuleDraft`/`GuardrailReport`
embebido aqui es una instancia REAL y valida de su contrato productivo
respectivo, nunca una version deformada. La identidad principal de
cada resultado es `unified_shadow_candidate_id` (el `group_id`), nunca
un `source_candidate_id` V2/interprocedural individual -- ningun
member se elige como "ganador".

Principio rector (Fase 13 Parte 2): la informacion de cada grupo
proviene de la UNION validada de sus members (family, program, scope,
target, input/output, evidence, provenance, source candidate IDs,
review decision IDs) -- nunca de un unico member representante.

Sin ningun timestamp, en ningun nivel de este artefacto -- ni de la
hora real de ejecucion, ni derivado del `run_id`. El `GuardrailReport`
productivo exige `evaluated_at: datetime` (esa exigencia NUNCA se
modifica), y se calcula internamente en memoria invocando
`evaluate_guardrail` sin alterarlo -- pero ese objeto real NUNCA se
persiste directamente aqui: solo se embebe
`UnifiedShadowGuardrailReportView`, una vista shadow explicita que
preserva `candidate_id`/`verdict`/`violations`/`repair_attempts`/
`source_package_hash` y EXCLUYE `evaluated_at` por completo. Ver
`pipeline/unified_shadow_guardrail_runner.py::to_shadow_view`.

Sin rutas absolutas. Sin claves, endpoint, modelo real ni
configuracion de proveedor externo -- `provider` es SIEMPRE
`DETERMINISTIC_FAKE` (Fase 13 nunca invoca un LLM real, ver
`pipeline/unified_shadow_draft_generator.py`)."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, Sha256Hex
from .context_package import ContextPackage
from .enums import GuardrailVerdict, Severity
from .guardrail import GuardrailViolation
from .rule_draft import RuleDraft
from .unified_candidates_shadow import UnifiedShadowComparisonKind, UnifiedShadowGroupStatus


class UnifiedShadowDownstreamExecutionStatus(StrEnum):
    """Resultado de intentar ejecutar el flujo downstream para UN
    `UnifiedShadowCandidateGroup`. Exactamente uno por grupo (ver
    `UnifiedShadowDownstreamGroupResult`, invariante 2)."""

    EXECUTED = "EXECUTED"
    SKIPPED_NOT_ELIGIBLE = "SKIPPED_NOT_ELIGIBLE"
    CONTEXT_ASSEMBLY_FAILED = "CONTEXT_ASSEMBLY_FAILED"
    DRAFT_GENERATION_FAILED = "DRAFT_GENERATION_FAILED"
    GUARDRAIL_REJECTED = "GUARDRAIL_REJECTED"
    TECHNICAL_FAILURE = "TECHNICAL_FAILURE"


class UnifiedShadowDownstreamDisposition(StrEnum):
    """Disposition global del artefacto -- derivada de los
    `execution_status` de todos los grupos, nunca de un puntaje."""

    COMPLETED = "COMPLETED"
    COMPLETED_WITH_REJECTIONS = "COMPLETED_WITH_REJECTIONS"
    NOT_EXECUTED = "NOT_EXECUTED"
    BLOCKED = "BLOCKED"
    NOT_EVALUATED = "NOT_EVALUATED"


class UnifiedShadowDraftProvider(StrEnum):
    """Catalogo CERRADO de un unico valor: esta fase NUNCA invoca un
    proveedor LLM real (ver Parte 7)."""

    DETERMINISTIC_FAKE = "DETERMINISTIC_FAKE"


class UnifiedShadowGuardrailStatus(StrEnum):
    PASSED = "PASSED"
    REJECTED = "REJECTED"
    NOT_EVALUATED = "NOT_EVALUATED"


def _ordered_unique_strings(values: Iterable[str]) -> list[str]:
    return sorted(set(values))


_HARD_FAILURE_STATUSES = frozenset(
    {
        UnifiedShadowDownstreamExecutionStatus.CONTEXT_ASSEMBLY_FAILED,
        UnifiedShadowDownstreamExecutionStatus.DRAFT_GENERATION_FAILED,
        UnifiedShadowDownstreamExecutionStatus.TECHNICAL_FAILURE,
    }
)
"""Estados que representan un fallo del PIPELINE shadow en si mismo
(nunca del contenido evaluado) -- a diferencia de `GUARDRAIL_REJECTED`,
que es una ejecucion exitosa cuyo contenido fue rechazado. Comparten el
mismo tratamiento en las invariantes de disposicion (17-19): cualquiera
de los tres impide `COMPLETED`/`COMPLETED_WITH_REJECTIONS` y, si es el
UNICO tipo de fallo presente (sin exigir especificamente
`TECHNICAL_FAILURE`), exige `BLOCKED`."""


class UnifiedShadowContextPackageRecord(AltamiraBaseModel):
    """UN `ContextPackage` shadow real (contrato productivo, nunca
    deformado) construido por union de los members de UN grupo --
    `record_id` es la identidad determinista de este registro,
    `group_id` (`unified_shadow_candidate_id`) es la identidad del
    grupo que lo origino."""

    record_id: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
    member_ids: list[str] = Field(min_length=1)
    source_candidate_ids: list[str] = Field(min_length=1)
    review_decision_ids: list[str] = Field(min_length=1)
    context_package_hash: Sha256Hex
    context_package: ContextPackage
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_aliases: list[str] = Field(default_factory=list)
    provenance_references: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_lists_sorted_and_unique(self) -> UnifiedShadowContextPackageRecord:
        label = f"UnifiedShadowContextPackageRecord({self.record_id!r})"
        for field_name in (
            "member_ids",
            "source_candidate_ids",
            "review_decision_ids",
            "evidence_ids",
            "evidence_aliases",
            "provenance_references",
            "diagnostics",
        ):
            values = getattr(self, field_name)
            if values != _ordered_unique_strings(values):
                raise ValueError(f"{label}: {field_name} debe estar ordenado y sin duplicados")
        return self


class UnifiedShadowRuleDraftRecord(AltamiraBaseModel):
    """UN `RuleDraft` shadow real generado por el fake determinista,
    ligado a `context_package_record_id` -- nunca a un member
    individual."""

    record_id: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
    context_package_record_id: str = Field(min_length=1)
    provider: UnifiedShadowDraftProvider
    provider_response_hash: Sha256Hex
    rule_draft_hash: Sha256Hex
    rule_draft: RuleDraft
    evidence_aliases_used: list[str] = Field(default_factory=list)
    evidence_aliases_unresolved: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_lists_sorted_and_unique(self) -> UnifiedShadowRuleDraftRecord:
        label = f"UnifiedShadowRuleDraftRecord({self.record_id!r})"
        for field_name in (
            "evidence_aliases_used",
            "evidence_aliases_unresolved",
            "diagnostics",
        ):
            values = getattr(self, field_name)
            if values != _ordered_unique_strings(values):
                raise ValueError(f"{label}: {field_name} debe estar ordenado y sin duplicados")
        return self


class UnifiedShadowGuardrailReportView(AltamiraBaseModel):
    """Representacion shadow EXPLICITA del resultado de
    `deterministic_guardrail.py::evaluate_guardrail` (productivo, NUNCA
    modificado, NUNCA relajado) -- conserva `candidate_id`/`verdict`/
    `violations`/`repair_attempts`/`source_package_hash` exactamente
    como el `GuardrailReport` productivo real que se calcula
    internamente, pero EXCLUYE deliberadamente `evaluated_at`: el
    contrato productivo de `GuardrailReport` exige ese campo (nunca se
    modifica esa exigencia), pero el `GuardrailReport` real calculado
    en memoria NUNCA se persiste directamente en este artefacto --
    unicamente esta vista, sin ningun campo temporal. Ver
    `pipeline/unified_shadow_guardrail_runner.py::to_shadow_view`."""

    schema_version: Literal["1.0"] = "1.0"
    candidate_id: str = Field(min_length=1)
    verdict: GuardrailVerdict
    violations: list[GuardrailViolation] = Field(default_factory=list)
    repair_attempts: int = Field(ge=0, le=2)
    source_package_hash: Sha256Hex

    @model_validator(mode="after")
    def _verdict_matches_violations(self) -> UnifiedShadowGuardrailReportView:
        has_error = any(v.severity == Severity.ERROR for v in self.violations)
        if self.verdict == GuardrailVerdict.EVIDENCE_VALIDATED and has_error:
            raise ValueError(
                "verdict EVIDENCE_VALIDATED no puede coexistir con violaciones de severidad ERROR"
            )
        if self.verdict == GuardrailVerdict.REJECTED and not has_error:
            raise ValueError("verdict REJECTED requiere al menos una violacion de severidad ERROR")
        return self


class UnifiedShadowGuardrailRecord(AltamiraBaseModel):
    """Resultado shadow de guardrails, ligado a
    `rule_draft_record_id`. Un `REJECTED` nunca elimina el draft ni
    publica una regla -- solo se registra."""

    record_id: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
    rule_draft_record_id: str = Field(min_length=1)
    status: UnifiedShadowGuardrailStatus
    guardrail_report_hash: Sha256Hex
    guardrail_result: UnifiedShadowGuardrailReportView
    blocking_reasons: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_lists_sorted_and_unique(self) -> UnifiedShadowGuardrailRecord:
        label = f"UnifiedShadowGuardrailRecord({self.record_id!r})"
        for field_name in ("blocking_reasons", "diagnostics"):
            values = getattr(self, field_name)
            if values != _ordered_unique_strings(values):
                raise ValueError(f"{label}: {field_name} debe estar ordenado y sin duplicados")
        return self

    @model_validator(mode="after")
    def _check_rejected_requires_blocking_reasons(self) -> UnifiedShadowGuardrailRecord:
        if self.status == UnifiedShadowGuardrailStatus.REJECTED and not self.blocking_reasons:
            raise ValueError(
                f"UnifiedShadowGuardrailRecord({self.record_id!r}): REJECTED exige al menos "
                "una razon de bloqueo (invariante 15)"
            )
        return self


class UnifiedShadowDownstreamGroupResult(AltamiraBaseModel):
    """Resultado de UN `UnifiedShadowCandidateGroup` real (Fase 11/12)
    -- nunca lo modifica, nunca elige un member "ganador". Exactamente
    uno por grupo del `UnifiedShadowValidationReport` de origen (ver
    invariante 2)."""

    group_id: str = Field(min_length=1)
    execution_status: UnifiedShadowDownstreamExecutionStatus
    downstream_shadow_eligible: bool
    comparison_to_v1: UnifiedShadowComparisonKind
    group_status: UnifiedShadowGroupStatus
    member_ids: list[str] = Field(min_length=1)
    source_candidate_ids: list[str] = Field(min_length=1)
    review_decision_ids: list[str] = Field(default_factory=list)
    context_package_record_id: str | None = None
    rule_draft_record_id: str | None = None
    guardrail_record_id: str | None = None
    blocking_reasons: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_lists_sorted_and_unique(self) -> UnifiedShadowDownstreamGroupResult:
        label = f"UnifiedShadowDownstreamGroupResult({self.group_id!r})"
        for field_name in (
            "member_ids",
            "source_candidate_ids",
            "review_decision_ids",
            "blocking_reasons",
            "diagnostics",
        ):
            values = getattr(self, field_name)
            if values != _ordered_unique_strings(values):
                raise ValueError(f"{label}: {field_name} debe estar ordenado y sin duplicados")
        return self

    @model_validator(mode="after")
    def _check_only_eligible_can_execute(self) -> UnifiedShadowDownstreamGroupResult:
        """Invariante 3."""
        if (
            self.execution_status == UnifiedShadowDownstreamExecutionStatus.EXECUTED
            and not self.downstream_shadow_eligible
        ):
            raise ValueError(
                f"UnifiedShadowDownstreamGroupResult({self.group_id!r}): solo un grupo "
                "downstream_shadow_eligible=true puede tener execution_status EXECUTED "
                "(invariante 3)"
            )
        return self

    @model_validator(mode="after")
    def _check_not_eligible_is_skipped(self) -> UnifiedShadowDownstreamGroupResult:
        if (
            not self.downstream_shadow_eligible
            and self.execution_status != UnifiedShadowDownstreamExecutionStatus.SKIPPED_NOT_ELIGIBLE
        ):
            raise ValueError(
                f"UnifiedShadowDownstreamGroupResult({self.group_id!r}): un grupo no elegible "
                "siempre es SKIPPED_NOT_ELIGIBLE (invariante 3)"
            )
        return self

    @model_validator(mode="after")
    def _check_executed_has_context_package_record(self) -> UnifiedShadowDownstreamGroupResult:
        """Invariante 4."""
        if (
            self.execution_status == UnifiedShadowDownstreamExecutionStatus.EXECUTED
            and self.context_package_record_id is None
        ):
            raise ValueError(
                f"UnifiedShadowDownstreamGroupResult({self.group_id!r}): EXECUTED exige "
                "exactamente un ContextPackageRecord (invariante 4)"
            )
        return self

    @model_validator(mode="after")
    def _check_guardrail_rejected_requires_report(self) -> UnifiedShadowDownstreamGroupResult:
        """Invariante 15."""
        if (
            self.execution_status == UnifiedShadowDownstreamExecutionStatus.GUARDRAIL_REJECTED
            and self.guardrail_record_id is None
        ):
            raise ValueError(
                f"UnifiedShadowDownstreamGroupResult({self.group_id!r}): GUARDRAIL_REJECTED "
                "exige un GuardrailRecord (invariante 15)"
            )
        return self

    @model_validator(mode="after")
    def _check_technical_failure_requires_diagnostics(self) -> UnifiedShadowDownstreamGroupResult:
        """Invariante 16."""
        if (
            self.execution_status == UnifiedShadowDownstreamExecutionStatus.TECHNICAL_FAILURE
            and not self.diagnostics
        ):
            raise ValueError(
                f"UnifiedShadowDownstreamGroupResult({self.group_id!r}): TECHNICAL_FAILURE "
                "exige al menos un diagnostico tipado (invariante 16)"
            )
        return self


class UnifiedShadowDownstreamSummary(AltamiraBaseModel):
    validation_group_count: int = Field(ge=0)
    downstream_eligible_group_count: int = Field(ge=0)
    executed_group_count: int = Field(ge=0)
    skipped_group_count: int = Field(ge=0)
    context_package_count: int = Field(ge=0)
    rule_draft_count: int = Field(ge=0)
    guardrail_passed_count: int = Field(ge=0)
    guardrail_rejected_count: int = Field(ge=0)
    technical_failure_count: int = Field(ge=0)
    counts_by_execution_status: dict[UnifiedShadowDownstreamExecutionStatus, int] = Field(
        default_factory=dict
    )
    counts_by_rule_family: dict[str, int] = Field(default_factory=dict)
    counts_by_guardrail_status: dict[UnifiedShadowGuardrailStatus, int] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def _check_counts_reconcile(self) -> UnifiedShadowDownstreamSummary:
        if self.executed_group_count + self.skipped_group_count != self.validation_group_count:
            raise ValueError(
                "executed_group_count + skipped_group_count debe igualar validation_group_count"
            )
        if self.downstream_eligible_group_count > self.validation_group_count:
            raise ValueError(
                "downstream_eligible_group_count no puede exceder validation_group_count"
            )
        if self.guardrail_passed_count > self.executed_group_count:
            raise ValueError(
                "guardrail_passed_count no puede exceder executed_group_count (EXECUTED "
                "significa exactamente 'guardrail PASSED')"
            )
        if (
            self.guardrail_passed_count + self.guardrail_rejected_count
            > self.validation_group_count
        ):
            raise ValueError(
                "guardrail_passed_count + guardrail_rejected_count no puede exceder "
                "validation_group_count (un grupo GUARDRAIL_REJECTED tambien fue evaluado "
                "por guardrails, pero nunca cuenta en executed_group_count)"
            )
        if sum(self.counts_by_execution_status.values()) != self.validation_group_count:
            raise ValueError(
                "suma de counts_by_execution_status debe igualar validation_group_count"
            )
        return self


class UnifiedShadowDownstreamArtifact(AltamiraBaseModel):
    """Contenedor persistido en `<run_dir>/diagnostics/unified-shadow-
    downstream.json`. NO contractual para el pipeline V1, sin
    timestamps propios, sin rutas absolutas: dos ejecuciones sobre los
    mismos artefactos de entrada deben producir bytes identicos."""

    schema_version: Literal["1.0"] = "1.0"
    executor_version: Literal["1.0"] = "1.0"
    policy_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    source_package_hash: Sha256Hex
    unified_candidates_shadow_hash: Sha256Hex
    validation_report_hash: Sha256Hex
    candidate_v1_artifact_hash: Sha256Hex
    assessment_artifact_hash: Sha256Hex
    review_package_hash: Sha256Hex
    promotion_plan_hash: Sha256Hex
    provider: UnifiedShadowDraftProvider
    disposition: UnifiedShadowDownstreamDisposition
    summary: UnifiedShadowDownstreamSummary
    context_packages: list[UnifiedShadowContextPackageRecord] = Field(default_factory=list)
    rule_drafts: list[UnifiedShadowRuleDraftRecord] = Field(default_factory=list)
    guardrail_results: list[UnifiedShadowGuardrailRecord] = Field(default_factory=list)
    group_results: list[UnifiedShadowDownstreamGroupResult] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_provider_is_always_fake(self) -> UnifiedShadowDownstreamArtifact:
        if self.provider != UnifiedShadowDraftProvider.DETERMINISTIC_FAKE:
            raise ValueError(
                "provider debe ser siempre DETERMINISTIC_FAKE: esta fase nunca invoca un "
                "proveedor LLM real"
            )
        return self

    @model_validator(mode="after")
    def _check_record_ids_unique_across_lists(self) -> UnifiedShadowDownstreamArtifact:
        """Invariante 1."""
        context_ids = [r.record_id for r in self.context_packages]
        draft_ids = [r.record_id for r in self.rule_drafts]
        guardrail_ids = [r.record_id for r in self.guardrail_results]
        for label, ids in (
            ("context_packages", context_ids),
            ("rule_drafts", draft_ids),
            ("guardrail_results", guardrail_ids),
        ):
            if len(ids) != len(set(ids)):
                raise ValueError(f"{label} contiene record_id duplicado (invariante 1)")
            if ids != sorted(ids):
                raise ValueError(f"{label} no esta ordenado por record_id")
        return self

    @model_validator(mode="after")
    def _check_group_results_unique_and_ordered(self) -> UnifiedShadowDownstreamArtifact:
        """Invariante 2."""
        ids = [gr.group_id for gr in self.group_results]
        if len(ids) != len(set(ids)):
            raise ValueError("group_results contiene group_id duplicado (invariante 2)")
        if ids != sorted(ids):
            raise ValueError("group_results no esta ordenado por group_id")
        return self

    @model_validator(mode="after")
    def _check_context_package_belongs_to_existing_group(self) -> UnifiedShadowDownstreamArtifact:
        """Invariante 5."""
        known_group_ids = {gr.group_id for gr in self.group_results}
        for record in self.context_packages:
            if record.group_id not in known_group_ids:
                raise ValueError(
                    f"UnifiedShadowContextPackageRecord({record.record_id!r}) referencia un "
                    f"group_id inexistente: {record.group_id!r} (invariante 5)"
                )
        return self

    @model_validator(mode="after")
    def _check_rule_draft_references_existing_context_package(
        self,
    ) -> UnifiedShadowDownstreamArtifact:
        """Invariante 6."""
        known_context_ids = {r.record_id for r in self.context_packages}
        for draft in self.rule_drafts:
            if draft.context_package_record_id not in known_context_ids:
                raise ValueError(
                    f"UnifiedShadowRuleDraftRecord({draft.record_id!r}) referencia un "
                    f"context_package_record_id inexistente: "
                    f"{draft.context_package_record_id!r} (invariante 6)"
                )
        return self

    @model_validator(mode="after")
    def _check_guardrail_references_existing_rule_draft(self) -> UnifiedShadowDownstreamArtifact:
        """Invariante 7."""
        known_draft_ids = {r.record_id for r in self.rule_drafts}
        for guardrail in self.guardrail_results:
            if guardrail.rule_draft_record_id not in known_draft_ids:
                raise ValueError(
                    f"UnifiedShadowGuardrailRecord({guardrail.record_id!r}) referencia un "
                    f"rule_draft_record_id inexistente: "
                    f"{guardrail.rule_draft_record_id!r} (invariante 7)"
                )
        return self

    @model_validator(mode="after")
    def _check_group_result_record_references_exist(self) -> UnifiedShadowDownstreamArtifact:
        known_context_ids = {r.record_id for r in self.context_packages}
        known_draft_ids = {r.record_id for r in self.rule_drafts}
        known_guardrail_ids = {r.record_id for r in self.guardrail_results}
        for gr in self.group_results:
            if (
                gr.context_package_record_id is not None
                and gr.context_package_record_id not in known_context_ids
            ):
                raise ValueError(
                    f"UnifiedShadowDownstreamGroupResult({gr.group_id!r}) referencia un "
                    f"context_package_record_id inexistente: {gr.context_package_record_id!r}"
                )
            if (
                gr.rule_draft_record_id is not None
                and gr.rule_draft_record_id not in known_draft_ids
            ):
                raise ValueError(
                    f"UnifiedShadowDownstreamGroupResult({gr.group_id!r}) referencia un "
                    f"rule_draft_record_id inexistente: {gr.rule_draft_record_id!r}"
                )
            if (
                gr.guardrail_record_id is not None
                and gr.guardrail_record_id not in known_guardrail_ids
            ):
                raise ValueError(
                    f"UnifiedShadowDownstreamGroupResult({gr.group_id!r}) referencia un "
                    f"guardrail_record_id inexistente: {gr.guardrail_record_id!r}"
                )
        return self

    @model_validator(mode="after")
    def _check_completed_requires_all_eligible_executed_and_passed(
        self,
    ) -> UnifiedShadowDownstreamArtifact:
        """Invariante 17."""
        if self.disposition != UnifiedShadowDownstreamDisposition.COMPLETED:
            return self
        eligible = [gr for gr in self.group_results if gr.downstream_shadow_eligible]
        if not eligible:
            raise ValueError("COMPLETED exige al menos un grupo elegible (invariante 17)")
        if any(
            gr.execution_status != UnifiedShadowDownstreamExecutionStatus.EXECUTED
            for gr in eligible
        ):
            raise ValueError(
                "COMPLETED exige que todos los grupos elegibles esten EXECUTED (invariante 17)"
            )
        guardrail_by_id = {g.record_id: g for g in self.guardrail_results}
        for gr in eligible:
            guardrail = guardrail_by_id.get(gr.guardrail_record_id or "")
            if guardrail is None or guardrail.status != UnifiedShadowGuardrailStatus.PASSED:
                raise ValueError(
                    "COMPLETED exige que todos los grupos ejecutados tengan guardrail "
                    "PASSED (invariante 17)"
                )
        return self

    @model_validator(mode="after")
    def _check_completed_with_rejections(self) -> UnifiedShadowDownstreamArtifact:
        """Invariante 18."""
        if self.disposition != UnifiedShadowDownstreamDisposition.COMPLETED_WITH_REJECTIONS:
            return self
        has_rejection = any(
            gr.execution_status == UnifiedShadowDownstreamExecutionStatus.GUARDRAIL_REJECTED
            for gr in self.group_results
        )
        has_hard_failure = any(
            gr.execution_status in _HARD_FAILURE_STATUSES for gr in self.group_results
        )
        if not has_rejection:
            raise ValueError("COMPLETED_WITH_REJECTIONS exige al menos un rechazo (invariante 18)")
        if has_hard_failure:
            raise ValueError(
                "COMPLETED_WITH_REJECTIONS exige cero fallos de pipeline "
                "(CONTEXT_ASSEMBLY_FAILED/DRAFT_GENERATION_FAILED/TECHNICAL_FAILURE) "
                "(invariante 18)"
            )
        return self

    @model_validator(mode="after")
    def _check_blocked_requires_technical_failure(self) -> UnifiedShadowDownstreamArtifact:
        """Invariante 19."""
        if self.disposition != UnifiedShadowDownstreamDisposition.BLOCKED:
            return self
        has_hard_failure = any(
            gr.execution_status in _HARD_FAILURE_STATUSES for gr in self.group_results
        )
        if not has_hard_failure:
            raise ValueError(
                "BLOCKED exige al menos un fallo de pipeline "
                "(CONTEXT_ASSEMBLY_FAILED/DRAFT_GENERATION_FAILED/TECHNICAL_FAILURE) "
                "(invariante 19)"
            )
        return self

    @model_validator(mode="after")
    def _check_not_executed_requires_zero_eligible(self) -> UnifiedShadowDownstreamArtifact:
        """Invariante 20."""
        if self.disposition != UnifiedShadowDownstreamDisposition.NOT_EXECUTED:
            return self
        if any(gr.downstream_shadow_eligible for gr in self.group_results):
            raise ValueError("NOT_EXECUTED exige cero grupos elegibles (invariante 20)")
        return self

    @model_validator(mode="after")
    def _check_summary_reconciles(self) -> UnifiedShadowDownstreamArtifact:
        """Invariante 21."""
        if self.summary.validation_group_count != len(self.group_results):
            raise ValueError(
                "summary.validation_group_count no coincide con la cantidad real de group_results"
            )
        if self.summary.context_package_count != len(self.context_packages):
            raise ValueError(
                "summary.context_package_count no coincide con la cantidad real de context_packages"
            )
        if self.summary.rule_draft_count != len(self.rule_drafts):
            raise ValueError(
                "summary.rule_draft_count no coincide con la cantidad real de rule_drafts"
            )
        expected_eligible = sum(1 for gr in self.group_results if gr.downstream_shadow_eligible)
        if self.summary.downstream_eligible_group_count != expected_eligible:
            raise ValueError(
                "summary.downstream_eligible_group_count no coincide con la cantidad real "
                "de grupos elegibles"
            )
        expected_executed = sum(
            1
            for gr in self.group_results
            if gr.execution_status == UnifiedShadowDownstreamExecutionStatus.EXECUTED
        )
        if self.summary.executed_group_count != expected_executed:
            raise ValueError(
                "summary.executed_group_count no coincide con la cantidad real de grupos EXECUTED"
            )
        expected_skipped = self.summary.validation_group_count - expected_executed
        if self.summary.skipped_group_count != expected_skipped:
            raise ValueError(
                "summary.skipped_group_count no coincide con validation_group_count - "
                "executed_group_count"
            )
        expected_passed = sum(
            1 for g in self.guardrail_results if g.status == UnifiedShadowGuardrailStatus.PASSED
        )
        expected_rejected = sum(
            1 for g in self.guardrail_results if g.status == UnifiedShadowGuardrailStatus.REJECTED
        )
        if self.summary.guardrail_passed_count != expected_passed:
            raise ValueError(
                "summary.guardrail_passed_count no coincide con la cantidad real de "
                "guardrails PASSED"
            )
        if self.summary.guardrail_rejected_count != expected_rejected:
            raise ValueError(
                "summary.guardrail_rejected_count no coincide con la cantidad real de "
                "guardrails REJECTED"
            )
        expected_technical_failures = sum(
            1
            for gr in self.group_results
            if gr.execution_status == UnifiedShadowDownstreamExecutionStatus.TECHNICAL_FAILURE
        )
        if self.summary.technical_failure_count != expected_technical_failures:
            raise ValueError(
                "summary.technical_failure_count no coincide con la cantidad real de "
                "grupos TECHNICAL_FAILURE"
            )
        expected_by_status: dict[str, int] = {}
        for gr in self.group_results:
            expected_by_status[gr.execution_status.value] = (
                expected_by_status.get(gr.execution_status.value, 0) + 1
            )
        actual_by_status = {k.value: v for k, v in self.summary.counts_by_execution_status.items()}
        if actual_by_status != expected_by_status:
            raise ValueError(
                "summary.counts_by_execution_status no coincide con la agregacion real de "
                "group_results"
            )
        expected_by_guardrail_status: dict[str, int] = {}
        for g in self.guardrail_results:
            expected_by_guardrail_status[g.status.value] = (
                expected_by_guardrail_status.get(g.status.value, 0) + 1
            )
        actual_by_guardrail_status = {
            k.value: v for k, v in self.summary.counts_by_guardrail_status.items()
        }
        if actual_by_guardrail_status != expected_by_guardrail_status:
            raise ValueError(
                "summary.counts_by_guardrail_status no coincide con la agregacion real de "
                "guardrail_results"
            )
        return self

    @model_validator(mode="after")
    def _check_diagnostics_sorted_and_unique(self) -> UnifiedShadowDownstreamArtifact:
        if self.diagnostics != _ordered_unique_strings(self.diagnostics):
            raise ValueError("diagnostics debe estar ordenado alfabeticamente y sin duplicados")
        return self


__all__ = [
    "UnifiedShadowContextPackageRecord",
    "UnifiedShadowDownstreamArtifact",
    "UnifiedShadowDownstreamDisposition",
    "UnifiedShadowDownstreamExecutionStatus",
    "UnifiedShadowDownstreamGroupResult",
    "UnifiedShadowDownstreamSummary",
    "UnifiedShadowDraftProvider",
    "UnifiedShadowGuardrailRecord",
    "UnifiedShadowGuardrailReportView",
    "UnifiedShadowGuardrailStatus",
    "UnifiedShadowRuleDraftRecord",
]
