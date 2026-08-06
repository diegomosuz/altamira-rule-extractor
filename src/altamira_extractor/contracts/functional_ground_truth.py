"""Contrato tipado de ground truth funcional VERSIONADO (Fase 15B2-A,
Parte E). Persiste en `config/ground_truth/synthetic_engineering.yaml`,
cargado como `FunctionalGroundTruthSet`.

Tres decisiones arquitectonicas obligatorias gobiernan este contrato (ver
CLAUDE.md/instrucciones de la Fase 15B2-A):

1. NUNCA se crea ground truth para los paquetes reales de ingenieria
   (Catherine original/corregido, CLIENTES_EMPRESAS, PRESTAMOS_EMPRESAS,
   CONSULTA_SALDOS) salvo que exista una expectativa de regla explicita,
   versionada y ya revisada -- ninguna existe hoy, asi que este catalogo
   contiene EXCLUSIVAMENTE fixtures sinteticas.
2. El ground truth versionado usa UNICAMENTE fixtures sinteticas y
   expectativas independientes de la implementacion evaluada: cada
   expectativa de este catalogo fue derivada leyendo directamente el
   codigo fuente de los detectores reales (`candidate_detector.py`,
   `v2_detectors.py`, `interprocedural_rule_detectors.py`) y las reglas
   de `config/semantic-tags.yml` -- NUNCA ejecutando el pipeline y
   copiando su salida.
3. NUNCA se usa LLM/embeddings/similitud semantica como criterio de
   aprobacion; el matching primario (Parte F,
   `contracts/functional_validation.py`) es deterministico y exacto tras
   normalizacion -- por eso `GroundTruthExpectedRule` expone solo campos
   estructurados y comparables (program/paragraph/rule_family/
   minimum_count), nunca una descripcion en lenguaje natural que exigiria
   comparacion difusa.

`fixture_sha256` fija el ground truth a los BYTES EXACTOS de la fixture
citada: si el archivo `.cbl` cambia, la expectativa queda huerfana de
forma detectable (Parte F debe rechazar un caso cuyo hash no coincide),
en vez de validar silenciosamente contra un archivo que ya no es el que
se reviso.

Cobertura deliberadamente PARCIAL en esta edicion: `RETURN_CODE` y
`LEVEL_88_RETURN_CODE` tienen casos POSITIVE verificados linea por linea
contra el detector real. `BY_REFERENCE_OUTPUT`/`STATE_TRANSITION` NO
tienen casos todavia -- requieren verificar con la misma rigurosidad las
precondiciones de `detect_by_reference_rule`/`detect_state_transition_
rule` (Fase 8) antes de afirmar un resultado esperado; agregarlos sin esa
verificacion violaria la decision #2 de arriba. Ademas, `STATE_TRANSITION`
es estructuralmente inalcanzable con `config/semantic-tags.yml` tal como
esta versionado hoy: ninguna regla activa asigna `status`/`status_flag`
(los tags existen en `allowed_tags` pero sin `rules:` que los produzca) --
un caso POSITIVE para esa familia exigiria primero una regla de tagging
real, fuera del alcance de este bloque."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, RelativePath, Sha256Hex
from .candidate_promotion_assessment import UnifiedRuleFamily


class GroundTruthCaseKind(StrEnum):
    """POSITIVE: la fixture debe producir al menos `minimum_count`
    candidatos de `rule_family` en `program`/`paragraph`. NEGATIVE: la
    fixture NUNCA debe producir ningun candidato de `rule_family` distinto
    de `UNKNOWN` en `program` -- prueba la ausencia de falsos positivos,
    nunca una afirmacion de "no existen reglas" (imposible de demostrar
    por analisis estatico, ver `ZeroCandidateReason` en
    `contracts/semantic_coverage.py`)."""

    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"


class GroundTruthExpectedRule(AltamiraBaseModel):
    """Expectativa estructurada de UN resultado esperado. Solo valida
    para `GroundTruthCaseKind.POSITIVE` -- un caso NEGATIVE nunca declara
    `expected_rules` (la ausencia es la expectativa completa)."""

    expectation_id: str = Field(min_length=1, max_length=100)
    rule_family: UnifiedRuleFamily
    paragraph: str | None = None
    minimum_count: int = Field(default=1, ge=1)
    derivation_notes: str = Field(min_length=1, max_length=1000)


class GroundTruthFixtureReference(AltamiraBaseModel):
    """Referencia sanitizada a UNA fixture sintetica: `relative_path`
    siempre vive bajo `config/ground_truth/fixtures/` (nunca reutiliza
    una fixture del parser Java: mismo principio de independencia de la
    decision #2). `sha256` fija el ground truth a los BYTES EXACTOS del
    archivo."""

    relative_path: RelativePath
    sha256: Sha256Hex

    @model_validator(mode="after")
    def _check_under_ground_truth_fixtures_dir(self) -> GroundTruthFixtureReference:
        if not self.relative_path.startswith("config/ground_truth/fixtures/"):
            raise ValueError(
                f"relative_path debe vivir bajo config/ground_truth/fixtures/ "
                f"(recibido {self.relative_path!r})"
            )
        return self


class GroundTruthCase(AltamiraBaseModel):
    """Un caso de ground truth: una o mas fixtures sinteticas (`fixtures`
    -- una lista, no un campo singular: un caso interprocedural genuino,
    p. ej. `BY_REFERENCE_OUTPUT`, necesita caller Y callee en el MISMO
    paquete, dos archivos distintos) + su(s) expectativa(s).

    `program` (PROGRAM-ID real que declara la expectativa -- para un caso
    interprocedural, el programa CALLER, ver `candidate_source_adapters.
    py::program=candidate.caller_program`) vive a nivel de CASO, no de
    cada `GroundTruthExpectedRule`: un caso `NEGATIVE` (sin
    `expected_rules`) todavia necesita saber contra que programa
    verificar la ausencia de candidatos -- sin este campo, Parte F no
    tendria como determinarlo sin re-parsear la fixture en tiempo de
    validacion."""

    case_id: str = Field(min_length=1, max_length=100)
    kind: GroundTruthCaseKind
    program: str = Field(min_length=1)
    fixtures: list[GroundTruthFixtureReference] = Field(default_factory=list, min_length=1)
    description: str = Field(min_length=1, max_length=500)
    expected_rules: list[GroundTruthExpectedRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_fixtures_sorted_and_unique(self) -> GroundTruthCase:
        paths = [f.relative_path for f in self.fixtures]
        if len(paths) != len(set(paths)):
            raise ValueError(f"case_id={self.case_id!r}: fixtures contiene relative_path duplicado")
        if paths != sorted(paths):
            raise ValueError(
                f"case_id={self.case_id!r}: fixtures no esta ordenado deterministicamente "
                "por relative_path"
            )
        return self

    @model_validator(mode="after")
    def _check_expected_rules_match_kind(self) -> GroundTruthCase:
        if self.kind == GroundTruthCaseKind.NEGATIVE and self.expected_rules:
            raise ValueError(
                f"case_id={self.case_id!r}: kind=NEGATIVE no puede declarar expected_rules "
                "(la ausencia de candidatos ES la expectativa completa)"
            )
        if self.kind == GroundTruthCaseKind.POSITIVE and not self.expected_rules:
            raise ValueError(
                f"case_id={self.case_id!r}: kind=POSITIVE exige al menos un "
                "GroundTruthExpectedRule"
            )
        return self

    @model_validator(mode="after")
    def _check_expected_rules_sorted_and_unique(self) -> GroundTruthCase:
        ids = [rule.expectation_id for rule in self.expected_rules]
        if len(ids) != len(set(ids)):
            raise ValueError(f"case_id={self.case_id!r}: expected_rules.expectation_id duplicado")
        if ids != sorted(ids):
            raise ValueError(
                f"case_id={self.case_id!r}: expected_rules no esta ordenado "
                "deterministicamente por expectation_id"
            )
        return self


class FunctionalGroundTruthSummary(AltamiraBaseModel):
    """Agregacion verificable de `FunctionalGroundTruthSet`."""

    case_count: int = Field(ge=0)
    positive_case_count: int = Field(ge=0)
    negative_case_count: int = Field(ge=0)
    expected_rule_count: int = Field(ge=0)


class FunctionalGroundTruthSet(AltamiraBaseModel):
    """Contenedor persistido en `config/ground_truth/synthetic_
    engineering.yaml`. Deliberadamente sin ningun timestamp: dos cargas
    del mismo YAML deben producir el mismo modelo validado."""

    schema_version: Literal["1.0"] = "1.0"
    catalog_edition: str = Field(min_length=1, max_length=100)
    cases: list[GroundTruthCase] = Field(default_factory=list)
    summary: FunctionalGroundTruthSummary

    @model_validator(mode="after")
    def _check_cases_sorted_and_unique(self) -> FunctionalGroundTruthSet:
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("cases contiene case_id duplicado")
        if ids != sorted(ids):
            raise ValueError("cases no esta ordenado deterministicamente por case_id")
        return self

    @model_validator(mode="after")
    def _check_summary_matches_cases(self) -> FunctionalGroundTruthSet:
        if self.summary.case_count != len(self.cases):
            raise ValueError(
                f"summary.case_count ({self.summary.case_count}) != cantidad de cases "
                f"({len(self.cases)})"
            )
        expected_positive = sum(
            1 for c in self.cases if c.kind == GroundTruthCaseKind.POSITIVE
        )
        expected_negative = sum(
            1 for c in self.cases if c.kind == GroundTruthCaseKind.NEGATIVE
        )
        if self.summary.positive_case_count != expected_positive:
            raise ValueError(
                f"summary.positive_case_count ({self.summary.positive_case_count}) != "
                f"cantidad real de cases POSITIVE ({expected_positive})"
            )
        if self.summary.negative_case_count != expected_negative:
            raise ValueError(
                f"summary.negative_case_count ({self.summary.negative_case_count}) != "
                f"cantidad real de cases NEGATIVE ({expected_negative})"
            )
        expected_rule_count = sum(len(c.expected_rules) for c in self.cases)
        if self.summary.expected_rule_count != expected_rule_count:
            raise ValueError(
                f"summary.expected_rule_count ({self.summary.expected_rule_count}) != suma "
                f"real de cases[].expected_rules ({expected_rule_count})"
            )
        return self
