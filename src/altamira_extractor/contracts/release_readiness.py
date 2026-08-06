"""Contrato tipado de release readiness FUNCIONAL (Fase 15B2-A, Parte G).

Alcance deliberadamente ACOTADO: evalua UNICAMENTE dos senales ya
producidas por este mismo bloque -- la reconciliacion estatica del
catalogo semantico (Parte C, `pipeline/semantic_coverage_registry.py`) y
la validacion funcional determinista de un run concreto (Parte F,
`contracts/functional_validation.py`). NUNCA evalua Docker/Helm/K3s,
backup/restore, consolidacion de version ni el manifiesto final de
release -- esas piezas son explicitamente responsabilidad de un bloque
posterior (observabilidad/release engineering, fuera de FASE 15B2-A) y
CLAUDE.md prohibe presentar una salida V1 como aprobacion oficial.

`ReleaseReadinessDisposition` refleja esto en su nomenclatura: nunca usa
"READY"/"APPROVED" a secas (eso implicaria una aprobacion de release
completa que este contrato no puede otorgar) -- `FUNCTIONAL_CRITERIA_MET`
significa unicamente "los criterios funcionales configurados en
`config/release_readiness_policy.yaml` se cumplieron", nunca "el release
esta aprobado" (ver CLAUDE.md, seccion "Candidato, fidelidad y
aprobacion": `FUNCTIONALLY_APPROVED` esta fuera del alcance V1).

Motor de criterios CERRADO, no un DSL generico (mismo principio que el
resto del bloque: nada de reglas dinamicas ni expresiones libres) --
`ReleaseReadinessCriterionKind` enumera EXACTAMENTE los cinco tipos de
criterio soportados; agregar un tipo nuevo exige extender el enum y el
evaluador (`pipeline/release_readiness_evaluator.py`), nunca configurarlo
por texto libre en el YAML.

Aplicabilidad del ground truth (checkpoint correctivo, cierre de Fase
15B2-A): cuando `FunctionalValidationReport.dataset_applicability=
NOT_APPLICABLE` (ningun `GroundTruthCase` tiene su fixture set presente
en el run evaluado -- p. ej. cualquiera de los paquetes reales de
ingenieria, que nunca contienen las fixtures sinteticas), los CUATRO
criterios que dependen de `FunctionalValidationReport`
(`NO_MISSING_POSITIVE_CASES`/`NO_UNEXPECTED_NEGATIVE_CASES`/
`MINIMUM_RECALL`/`MINIMUM_PRECISION`) quedan
`status=NOT_EVALUATED` -- NUNCA `FAILED` (`no usar ceros como
sustituto`, CLAUDE.md/instrucciones de cierre): la ausencia de ground
truth aplicable no es una regresion detectable, es la ausencia de una
senal. `NO_ERROR_SEVERITY_COVERAGE_ISSUES` (estructural, Parte C, nunca
depende de ground truth) se evalua siempre normalmente. `domain_
functional_readiness` NUNCA alcanza un estado de aprobacion en esta
fase: `PENDING_DOMAIN_REVIEW` es el maximo posible, y solo cuando la
ingenieria (aplicable) paso -- jamas `CLIENT_APPROVED`, que no existe
como valor en este contrato (CLAUDE.md: `FUNCTIONALLY_APPROVED` fuera de
alcance V1)."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, Sha256Hex


class ReleaseReadinessCriterionKind(StrEnum):
    """Tipo cerrado de criterio evaluable. `NO_ERROR_SEVERITY_COVERAGE_
    ISSUES` consume `pipeline.semantic_coverage_registry.reconcile_
    manifest` (Parte C, estatico). Los otros cuatro consumen un
    `FunctionalValidationReport` (Parte F, por-run)."""

    NO_ERROR_SEVERITY_COVERAGE_ISSUES = "NO_ERROR_SEVERITY_COVERAGE_ISSUES"
    NO_MISSING_POSITIVE_CASES = "NO_MISSING_POSITIVE_CASES"
    NO_UNEXPECTED_NEGATIVE_CASES = "NO_UNEXPECTED_NEGATIVE_CASES"
    MINIMUM_RECALL = "MINIMUM_RECALL"
    MINIMUM_PRECISION = "MINIMUM_PRECISION"


_THRESHOLD_KINDS = frozenset(
    {ReleaseReadinessCriterionKind.MINIMUM_RECALL, ReleaseReadinessCriterionKind.MINIMUM_PRECISION}
)


class ReleaseReadinessCriterion(AltamiraBaseModel):
    """Un criterio configurado en `config/release_readiness_policy.yaml`.
    `minimum_value` es obligatorio UNICAMENTE para
    `MINIMUM_RECALL`/`MINIMUM_PRECISION` -- los otros tres criterios son
    binarios (sin umbral configurable) y `minimum_value` debe estar
    ausente."""

    criterion_id: str = Field(min_length=1, max_length=100)
    kind: ReleaseReadinessCriterionKind
    description: str = Field(min_length=1, max_length=500)
    minimum_value: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_minimum_value_matches_kind(self) -> ReleaseReadinessCriterion:
        requires_threshold = self.kind in _THRESHOLD_KINDS
        if requires_threshold and self.minimum_value is None:
            raise ValueError(
                f"criterion_id={self.criterion_id!r}: kind={self.kind.value} exige "
                "minimum_value"
            )
        if not requires_threshold and self.minimum_value is not None:
            raise ValueError(
                f"criterion_id={self.criterion_id!r}: kind={self.kind.value} no admite "
                "minimum_value (criterio binario)"
            )
        return self


class ReleaseReadinessPolicy(AltamiraBaseModel):
    """Contenedor persistido en `config/release_readiness_policy.yaml`."""

    schema_version: Literal["1.0"] = "1.0"
    policy_edition: str = Field(min_length=1, max_length=100)
    criteria: list[ReleaseReadinessCriterion] = Field(default_factory=list, min_length=1)

    @model_validator(mode="after")
    def _check_criteria_sorted_and_unique(self) -> ReleaseReadinessPolicy:
        ids = [c.criterion_id for c in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError("criteria contiene criterion_id duplicado")
        if ids != sorted(ids):
            raise ValueError("criteria no esta ordenado deterministicamente por criterion_id")
        return self


class ReleaseReadinessCriterionStatus(StrEnum):
    """NOT_EVALUATED es exclusivo de los cuatro criterios dependientes de
    `FunctionalValidationReport` cuando el ground truth no es aplicable a
    este run -- nunca un sustituto de FAILED (ver docstring del modulo)."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    NOT_EVALUATED = "NOT_EVALUATED"


class ReleaseReadinessCriterionResult(AltamiraBaseModel):
    """Resultado de evaluar UN `ReleaseReadinessCriterion` contra las
    senales reales de un run. `actual_value`/`threshold_value` quedan en
    `None` para criterios binarios (sin umbral numerico) y SIEMPRE para
    `status=NOT_EVALUATED`."""

    criterion_id: str = Field(min_length=1, max_length=100)
    kind: ReleaseReadinessCriterionKind
    status: ReleaseReadinessCriterionStatus
    actual_value: float | None = Field(default=None, ge=0.0)
    threshold_value: float | None = Field(default=None, ge=0.0, le=1.0)
    message: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _check_not_evaluated_has_no_values(self) -> ReleaseReadinessCriterionResult:
        if self.status == ReleaseReadinessCriterionStatus.NOT_EVALUATED:
            if self.actual_value is not None:
                raise ValueError(
                    f"criterion_id={self.criterion_id!r}: status=NOT_EVALUATED no puede "
                    "declarar actual_value"
                )
        return self


class ReleaseReadinessDisposition(StrEnum):
    """Nunca `READY`/`APPROVED`: ver docstring del modulo. Un release
    real exige ademas observabilidad, seguridad de proveedores LLM,
    Docker/K3s, backup/restore y consolidacion de version -- ninguno
    evaluado aqui. NOT_EVALUATED es exclusivo de
    `engineering_functional_readiness=NOT_EVALUATED` (ground truth no
    aplicable a este run) -- nunca se degrada a
    FUNCTIONAL_CRITERIA_NOT_MET solo por falta de senal."""

    FUNCTIONAL_CRITERIA_MET = "FUNCTIONAL_CRITERIA_MET"
    FUNCTIONAL_CRITERIA_NOT_MET = "FUNCTIONAL_CRITERIA_NOT_MET"
    NOT_EVALUATED = "NOT_EVALUATED"


class DomainFunctionalReadinessStatus(StrEnum):
    """Dimension de dominio: SIEMPRE por debajo de una aprobacion real
    (CLAUDE.md: `FUNCTIONALLY_APPROVED` fuera de alcance V1 -- este
    contrato ni siquiera declara ese valor). PENDING_DOMAIN_REVIEW
    unicamente cuando `engineering_functional_readiness=PASSED`: recien
    ahi hay algo concreto que un revisor humano de dominio podria
    revisar. NOT_EVALUATED en cualquier otro caso (incluyendo ground
    truth no aplicable o ingenieria con criterios fallidos)."""

    NOT_EVALUATED = "NOT_EVALUATED"
    PENDING_DOMAIN_REVIEW = "PENDING_DOMAIN_REVIEW"


class ReleaseReadinessWarningCode(StrEnum):
    """Catalogo CERRADO de warnings tipados -- nunca texto libre sin
    codigo (mismo principio que `SemanticCoverageIssue.reason_code`).
    GROUND_TRUTH_NOT_AVAILABLE: ningun caso del catalogo fue aplicable
    (`coverage_status=NOT_EVALUATED`). REQUIRED_GROUND_TRUTH_CASES_
    NOT_EXECUTED: al menos un caso fue aplicable pero el dataset sigue
    `PARTIALLY_EVALUATED` -- distinto del anterior: aqui SI hubo senal
    real, pero incompleta (checkpoint correctivo: un run aislado con un
    caso REQUIRED pendiente en otro paquete/run nunca debe reportarse
    igual que "sin ground truth en absoluto")."""

    GROUND_TRUTH_NOT_AVAILABLE = "GROUND_TRUTH_NOT_AVAILABLE"
    REQUIRED_GROUND_TRUTH_CASES_NOT_EXECUTED = "REQUIRED_GROUND_TRUTH_CASES_NOT_EXECUTED"


class ReleaseReadinessWarning(AltamiraBaseModel):
    code: ReleaseReadinessWarningCode
    message: str = Field(min_length=1, max_length=500)


class ReleaseReadinessSummary(AltamiraBaseModel):
    criterion_count: int = Field(ge=0)
    passed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    not_evaluated_count: int = Field(ge=0, default=0)


class ReleaseReadinessAssessment(AltamiraBaseModel):
    """Contenedor persistido en `<run_dir>/diagnostics/release-readiness-
    assessment.json`. NO contractual, sin timestamps: dos ejecuciones
    sobre los mismos artefactos de entrada deben producir bytes
    identicos.

    `structural_readiness` refleja EXCLUSIVAMENTE
    `NO_ERROR_SEVERITY_COVERAGE_ISSUES` (Parte C, nunca depende de ground
    truth -- siempre PASSED/FAILED, nunca NOT_EVALUATED).
    `engineering_functional_readiness` agrega los cuatro criterios
    dependientes de `FunctionalValidationReport`: NOT_EVALUATED cuando
    `dataset_applicability=NOT_APPLICABLE`, si no PASSED/FAILED segun
    esos cuatro criterios. `domain_functional_readiness` nunca excede
    PENDING_DOMAIN_REVIEW (ver `DomainFunctionalReadinessStatus`)."""

    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    source_package_hash: Sha256Hex
    policy_edition: str = Field(min_length=1, max_length=100)
    disposition: ReleaseReadinessDisposition
    structural_readiness: ReleaseReadinessCriterionStatus
    engineering_functional_readiness: ReleaseReadinessCriterionStatus
    domain_functional_readiness: DomainFunctionalReadinessStatus
    criteria_results: list[ReleaseReadinessCriterionResult] = Field(default_factory=list)
    summary: ReleaseReadinessSummary
    warnings: list[ReleaseReadinessWarning] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_criteria_results_sorted_and_unique(self) -> ReleaseReadinessAssessment:
        ids = [c.criterion_id for c in self.criteria_results]
        if len(ids) != len(set(ids)):
            raise ValueError("criteria_results contiene criterion_id duplicado")
        if ids != sorted(ids):
            raise ValueError(
                "criteria_results no esta ordenado deterministicamente por criterion_id"
            )
        return self

    @model_validator(mode="after")
    def _check_structural_readiness_matches_results(self) -> ReleaseReadinessAssessment:
        structural_results = [
            r
            for r in self.criteria_results
            if r.kind == ReleaseReadinessCriterionKind.NO_ERROR_SEVERITY_COVERAGE_ISSUES
        ]
        if not structural_results:
            return self
        expected = (
            ReleaseReadinessCriterionStatus.PASSED
            if all(
                r.status == ReleaseReadinessCriterionStatus.PASSED for r in structural_results
            )
            else ReleaseReadinessCriterionStatus.FAILED
        )
        if self.structural_readiness != expected:
            raise ValueError(
                f"structural_readiness={self.structural_readiness.value} no coincide con "
                f"NO_ERROR_SEVERITY_COVERAGE_ISSUES (deberia ser {expected.value})"
            )
        return self

    @model_validator(mode="after")
    def _check_engineering_functional_readiness_matches_results(
        self,
    ) -> ReleaseReadinessAssessment:
        functional_results = [
            r
            for r in self.criteria_results
            if r.kind != ReleaseReadinessCriterionKind.NO_ERROR_SEVERITY_COVERAGE_ISSUES
        ]
        if not functional_results:
            return self
        if all(
            r.status == ReleaseReadinessCriterionStatus.NOT_EVALUATED for r in functional_results
        ):
            expected = ReleaseReadinessCriterionStatus.NOT_EVALUATED
        elif all(
            r.status == ReleaseReadinessCriterionStatus.PASSED for r in functional_results
        ):
            expected = ReleaseReadinessCriterionStatus.PASSED
        else:
            expected = ReleaseReadinessCriterionStatus.FAILED
        if self.engineering_functional_readiness != expected:
            raise ValueError(
                "engineering_functional_readiness="
                f"{self.engineering_functional_readiness.value} no coincide con los criterios "
                f"funcionales (deberia ser {expected.value})"
            )
        return self

    @model_validator(mode="after")
    def _check_domain_functional_readiness_bounded(self) -> ReleaseReadinessAssessment:
        expected = (
            DomainFunctionalReadinessStatus.PENDING_DOMAIN_REVIEW
            if self.engineering_functional_readiness == ReleaseReadinessCriterionStatus.PASSED
            else DomainFunctionalReadinessStatus.NOT_EVALUATED
        )
        if self.domain_functional_readiness != expected:
            raise ValueError(
                f"domain_functional_readiness={self.domain_functional_readiness.value} no "
                f"coincide con engineering_functional_readiness (deberia ser {expected.value})"
            )
        return self

    @model_validator(mode="after")
    def _check_disposition_matches_results(self) -> ReleaseReadinessAssessment:
        if self.engineering_functional_readiness == ReleaseReadinessCriterionStatus.NOT_EVALUATED:
            expected = ReleaseReadinessDisposition.NOT_EVALUATED
        else:
            all_passed = all(
                r.status == ReleaseReadinessCriterionStatus.PASSED for r in self.criteria_results
            )
            expected = (
                ReleaseReadinessDisposition.FUNCTIONAL_CRITERIA_MET
                if all_passed and self.criteria_results
                else ReleaseReadinessDisposition.FUNCTIONAL_CRITERIA_NOT_MET
            )
        if self.disposition != expected:
            raise ValueError(
                f"disposition={self.disposition.value} no coincide con el resultado real de "
                f"criteria_results (deberia ser {expected.value})"
            )
        return self

    @model_validator(mode="after")
    def _check_summary_matches_results(self) -> ReleaseReadinessAssessment:
        if self.summary.criterion_count != len(self.criteria_results):
            raise ValueError(
                f"summary.criterion_count ({self.summary.criterion_count}) != cantidad de "
                f"criteria_results ({len(self.criteria_results)})"
            )
        expected_passed = sum(
            1
            for r in self.criteria_results
            if r.status == ReleaseReadinessCriterionStatus.PASSED
        )
        expected_failed = sum(
            1
            for r in self.criteria_results
            if r.status == ReleaseReadinessCriterionStatus.FAILED
        )
        expected_not_evaluated = sum(
            1
            for r in self.criteria_results
            if r.status == ReleaseReadinessCriterionStatus.NOT_EVALUATED
        )
        if self.summary.passed_count != expected_passed:
            raise ValueError(
                f"summary.passed_count ({self.summary.passed_count}) != cantidad real de "
                f"criterios PASSED ({expected_passed})"
            )
        if self.summary.failed_count != expected_failed:
            raise ValueError(
                f"summary.failed_count ({self.summary.failed_count}) != cantidad real de "
                f"criterios FAILED ({expected_failed})"
            )
        if self.summary.not_evaluated_count != expected_not_evaluated:
            raise ValueError(
                f"summary.not_evaluated_count ({self.summary.not_evaluated_count}) != cantidad "
                f"real de criterios NOT_EVALUATED ({expected_not_evaluated})"
            )
        return self


class DatasetReleaseReadinessAssessment(AltamiraBaseModel):
    """Variante de `ReleaseReadinessAssessment` (mismo motor de
    criterios, mismos enums) para el reporte AGREGADO multi-run
    (`FunctionalDatasetValidationReport`, cierre de Fase 15B2-A, Seccion
    4) -- identificado por `report_id`/`dataset_id`/`dataset_version` en
    vez de `run_id`/`source_package_hash` (no existe un unico paquete
    fuente cuando el dataset se evaluo contra varios runs). Comparte
    integramente `ReleaseReadinessCriterionResult`/`ReleaseReadinessSummary`/
    `ReleaseReadinessWarning` -- solo cambia la identidad del sujeto
    evaluado. Mismas garantias: `domain_functional_readiness` nunca
    excede `PENDING_DOMAIN_REVIEW`."""

    schema_version: Literal["1.0"] = "1.0"
    report_id: str = Field(min_length=1, max_length=64)
    dataset_id: str = Field(min_length=1, max_length=100)
    dataset_version: str = Field(min_length=1, max_length=100)
    policy_edition: str = Field(min_length=1, max_length=100)
    disposition: ReleaseReadinessDisposition
    structural_readiness: ReleaseReadinessCriterionStatus
    engineering_functional_readiness: ReleaseReadinessCriterionStatus
    domain_functional_readiness: DomainFunctionalReadinessStatus
    criteria_results: list[ReleaseReadinessCriterionResult] = Field(default_factory=list)
    summary: ReleaseReadinessSummary
    warnings: list[ReleaseReadinessWarning] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_criteria_results_sorted_and_unique(self) -> DatasetReleaseReadinessAssessment:
        ids = [c.criterion_id for c in self.criteria_results]
        if len(ids) != len(set(ids)):
            raise ValueError("criteria_results contiene criterion_id duplicado")
        if ids != sorted(ids):
            raise ValueError(
                "criteria_results no esta ordenado deterministicamente por criterion_id"
            )
        return self

    @model_validator(mode="after")
    def _check_domain_functional_readiness_bounded(self) -> DatasetReleaseReadinessAssessment:
        expected = (
            DomainFunctionalReadinessStatus.PENDING_DOMAIN_REVIEW
            if self.engineering_functional_readiness == ReleaseReadinessCriterionStatus.PASSED
            else DomainFunctionalReadinessStatus.NOT_EVALUATED
        )
        if self.domain_functional_readiness != expected:
            raise ValueError(
                f"domain_functional_readiness={self.domain_functional_readiness.value} no "
                f"coincide con engineering_functional_readiness (deberia ser {expected.value})"
            )
        return self

    @model_validator(mode="after")
    def _check_disposition_matches_results(self) -> DatasetReleaseReadinessAssessment:
        if self.engineering_functional_readiness == ReleaseReadinessCriterionStatus.NOT_EVALUATED:
            expected = ReleaseReadinessDisposition.NOT_EVALUATED
        else:
            all_passed = all(
                r.status == ReleaseReadinessCriterionStatus.PASSED for r in self.criteria_results
            )
            expected = (
                ReleaseReadinessDisposition.FUNCTIONAL_CRITERIA_MET
                if all_passed and self.criteria_results
                else ReleaseReadinessDisposition.FUNCTIONAL_CRITERIA_NOT_MET
            )
        if self.disposition != expected:
            raise ValueError(
                f"disposition={self.disposition.value} no coincide con el resultado real de "
                f"criteria_results (deberia ser {expected.value})"
            )
        return self

    @model_validator(mode="after")
    def _check_summary_matches_results(self) -> DatasetReleaseReadinessAssessment:
        if self.summary.criterion_count != len(self.criteria_results):
            raise ValueError(
                f"summary.criterion_count ({self.summary.criterion_count}) != cantidad de "
                f"criteria_results ({len(self.criteria_results)})"
            )
        expected_passed = sum(
            1 for r in self.criteria_results if r.status == ReleaseReadinessCriterionStatus.PASSED
        )
        expected_failed = sum(
            1 for r in self.criteria_results if r.status == ReleaseReadinessCriterionStatus.FAILED
        )
        expected_not_evaluated = sum(
            1
            for r in self.criteria_results
            if r.status == ReleaseReadinessCriterionStatus.NOT_EVALUATED
        )
        if self.summary.passed_count != expected_passed:
            raise ValueError(
                f"summary.passed_count ({self.summary.passed_count}) != {expected_passed}"
            )
        if self.summary.failed_count != expected_failed:
            raise ValueError(
                f"summary.failed_count ({self.summary.failed_count}) != {expected_failed}"
            )
        if self.summary.not_evaluated_count != expected_not_evaluated:
            raise ValueError(
                f"summary.not_evaluated_count ({self.summary.not_evaluated_count}) != "
                f"{expected_not_evaluated}"
            )
        return self
