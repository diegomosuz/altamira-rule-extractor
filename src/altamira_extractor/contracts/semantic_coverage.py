"""Contrato tipado del informe de cobertura semantica (Fase 1 de la
ampliacion semantica, checkpoint `feat/semantic-expansion-foundation`).

Diagnostico, NO contractual: persiste en `<run_dir>/diagnostics/
semantic-coverage.json`, fuera de `artifacts/01-10` (mismo principio que
`contracts/run_state_recovery.py`: un directorio adyacente, opcional,
irrelevante para que un run llegue a COMPLETED, ignorado por toda etapa
V1). Nunca reemplaza ni reinterpreta `CandidateArtifact`/`ContextPackage`/
`RuleDraft`/`GuardrailCandidateArtifact` — es una lectura de solo
diagnostico sobre artefactos V1 ya persistidos.

`SemanticSupportStatus` es una dimension DISTINTA de
`ParseSupportStatus` (contracts/enums.py, usado por
SemanticEnrichmentArtifact para DDL/CSV): `ParseSupportStatus` mide si un
archivo DDL/CSV pudo interpretarse; `SemanticSupportStatus` mide si una
CONSTRUCCION COBOL ya presente en `CanonicalProgram` recibio
interpretacion semantica estructurada (grafo/dependencias) o solo se
conservo como texto. Nunca se combinan ni se reutiliza uno por el otro.

Ningun campo de este contrato admite un timestamp de reloj: dos
ejecuciones sobre los mismos artefactos de entrada deben producir bytes
identicos (determinismo, no observabilidad temporal)."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Final, Literal

from pydantic import Field, field_validator, model_validator

from .base import AltamiraBaseModel, RelativePath, Sha256Hex
from .enums import LocationKind, NodeLabel, RelationshipType, Severity, StatementKind

# Limite de source_references persistidas por ConstructCoverage: el
# occurrence_count total siempre se conserva completo, pero las
# referencias detalladas (program/paragraph/statement_id/archivo/linea)
# se acotan para que un constructo con miles de ocurrencias no infle el
# reporte ni acerque el contenido a un volcado de codigo fuente completo
# (.claude/rules/security.md: nunca codigo COBOL completo).
MAX_SOURCE_REFERENCES_PER_CONSTRUCT: Final[int] = 5

# Claves obligatorias de `SemanticCoverageReport.source_artifact_hashes`
# (Fase 2): los cuatro artefactos V1 que el analizador puro consume como
# entrada de solo lectura. Nombres relativos, nunca rutas absolutas.
REQUIRED_SOURCE_ARTIFACT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "artifacts/02-canonical",
        "artifacts/03-dependencies.json",
        "artifacts/04-semantic-graph.json",
        "artifacts/06-candidates.json",
    }
)


class SemanticSupportStatus(StrEnum):
    """Nivel de soporte semantico de una construccion COBOL ya presente
    en CanonicalProgram -- nunca de un archivo DDL/CSV (eso es
    `ParseSupportStatus`, dimension distinta, nunca reutilizada aqui)."""

    FULLY_SUPPORTED = "FULLY_SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    PRESERVED_ONLY = "PRESERVED_ONLY"
    UNSUPPORTED = "UNSUPPORTED"


class ZeroCandidateReason(StrEnum):
    """Motivo, determinado conservadoramente, de la cantidad de
    candidatos Q0 de un programa. Nunca `NO_RULES`: un analisis estatico
    no puede afirmar la inexistencia de una regla funcional -- solo
    puede describir por que Q0 no la encontro (o si la encontro)."""

    CANDIDATES_PRESENT = "CANDIDATES_PRESENT"
    NO_DECISIONS = "NO_DECISIONS"
    DECISIONS_WITHOUT_RESOLVED_EFFECTS = "DECISIONS_WITHOUT_RESOLVED_EFFECTS"
    RESOLVED_EFFECTS_WITHOUT_Q0_MATCH = "RESOLVED_EFFECTS_WITHOUT_Q0_MATCH"
    NO_Q0_MATCH = "NO_Q0_MATCH"
    INSUFFICIENT_DIAGNOSTIC_DATA = "INSUFFICIENT_DIAGNOSTIC_DATA"


class CandidateImpact(StrEnum):
    """Impacto DESCRIPTIVO y categorico de una construccion sobre la
    deteccion de candidatos -- nunca una estimacion numerica de reglas
    perdidas (esa cantidad es indemostrable por analisis estatico)."""

    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


class SemanticCoverageSourceReference(AltamiraBaseModel):
    """Referencia sanitizada a UNA ocurrencia fuente de un constructo.
    Nunca incluye `source_text`: identifica la ubicacion, no reproduce el
    codigo COBOL."""

    program: str = Field(min_length=1)
    paragraph: str | None = None
    statement_id: str | None = None
    source_file: RelativePath | None = None
    line: int | None = Field(default=None, ge=1)
    location_kind: LocationKind | None = None


class ConstructCoverage(AltamiraBaseModel):
    """Cobertura semantica de UN constructo dentro de un programa (una
    fila de la matriz de cobertura). `occurrence_count` siempre refleja
    el total real, incluso cuando `source_references` esta acotado por
    `MAX_SOURCE_REFERENCES_PER_CONSTRUCT`."""

    # `construct_name`, no `construct`: ese nombre colisiona con el
    # classmethod heredado `BaseModel.construct` (alias legado de Pydantic
    # v1 para `model_construct`) y mypy --strict lo rechaza como override
    # incompatible. Unica desviacion del nombre de campo literal pedido.
    construct_name: str = Field(min_length=1)
    support_status: SemanticSupportStatus
    occurrence_count: int = Field(ge=1)
    diagnostic_code: str = Field(min_length=1)
    explanation: str = Field(min_length=1, max_length=2000)
    candidate_impact: CandidateImpact
    source_references: list[SemanticCoverageSourceReference] = Field(
        default_factory=list, max_length=MAX_SOURCE_REFERENCES_PER_CONSTRUCT
    )

    @model_validator(mode="after")
    def _check_references_within_occurrence_count(self) -> ConstructCoverage:
        if len(self.source_references) > self.occurrence_count:
            raise ValueError(
                f"ConstructCoverage({self.construct_name!r}): source_references "
                f"({len(self.source_references)}) no puede superar occurrence_count "
                f"({self.occurrence_count})"
            )
        return self


def _construct_coverage_key(entry: ConstructCoverage) -> tuple[str, str, str]:
    return (entry.construct_name, entry.support_status.value, entry.diagnostic_code)


def _check_construct_coverage_sorted_and_unique(
    entries: list[ConstructCoverage], *, context_label: str
) -> None:
    keys = [_construct_coverage_key(entry) for entry in entries]
    if len(keys) != len(set(keys)):
        raise ValueError(f"{context_label}: construct_coverage contiene entradas duplicadas")
    if keys != sorted(keys):
        raise ValueError(
            f"{context_label}: construct_coverage no esta ordenado deterministicamente"
        )


def _check_diagnostics_sorted_and_unique(diagnostics: list[str], *, context_label: str) -> None:
    if len(diagnostics) != len(set(diagnostics)):
        raise ValueError(f"{context_label}: diagnostics contiene duplicados")
    if diagnostics != sorted(diagnostics):
        raise ValueError(f"{context_label}: diagnostics no esta ordenado deterministicamente")


class ProgramSemanticCoverage(AltamiraBaseModel):
    """Cobertura semantica de UN CanonicalProgram."""

    program: str = Field(min_length=1)
    statement_count: int = Field(ge=0)
    fully_supported_count: int = Field(ge=0)
    partially_supported_count: int = Field(ge=0)
    preserved_only_count: int = Field(ge=0)
    unsupported_count: int = Field(ge=0)
    statement_counts_by_kind: dict[StatementKind, int] = Field(default_factory=dict)
    decision_count: int = Field(ge=0)
    decisions_with_resolved_effect_count: int = Field(ge=0)
    decisions_without_resolved_effect_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    zero_candidate_reason: ZeroCandidateReason
    level_88_data_item_count: int = Field(ge=0)
    unsupported_construct_count: int = Field(ge=0)
    construct_coverage: list[ConstructCoverage] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_statement_counts_by_kind_values(self) -> ProgramSemanticCoverage:
        for kind, count in self.statement_counts_by_kind.items():
            if count < 0:
                raise ValueError(
                    f"ProgramSemanticCoverage({self.program!r}): statement_counts_by_kind"
                    f"[{kind.value}] no puede ser negativo"
                )
        return self

    @model_validator(mode="after")
    def _check_statement_status_partition(self) -> ProgramSemanticCoverage:
        # Particion EXACTA de statement_count en 3 vias: todo CanonicalStatement
        # es, sin ambiguedad, o bien clasificado por su StatementKind (rules
        # 1-8, FULLY/PARTIALLY_SUPPORTED) o bien kind=OTHER (rule 9,
        # PRESERVED_ONLY siempre, nunca UNSUPPORTED automaticamente). UNSUPPORTED
        # es una dimension INDEPENDIENTE (ver _check_unsupported_matches_constructs
        # abajo): mide entradas de `unsupported_constructs`, no estatuses de
        # CanonicalStatement, por lo que nunca se suma aqui -- sumarlo
        # duplicaria el conteo de los statements OTHER que tambien aparecen en
        # unsupported_constructs.
        total = (
            self.fully_supported_count + self.partially_supported_count + self.preserved_only_count
        )
        if total != self.statement_count:
            raise ValueError(
                f"ProgramSemanticCoverage({self.program!r}): fully_supported_count + "
                f"partially_supported_count + preserved_only_count ({total}) != "
                f"statement_count ({self.statement_count})"
            )
        if sum(self.statement_counts_by_kind.values()) != self.statement_count:
            raise ValueError(
                f"ProgramSemanticCoverage({self.program!r}): suma de "
                "statement_counts_by_kind no coincide con statement_count"
            )
        return self

    @model_validator(mode="after")
    def _check_unsupported_matches_constructs(self) -> ProgramSemanticCoverage:
        if self.unsupported_count != self.unsupported_construct_count:
            raise ValueError(
                f"ProgramSemanticCoverage({self.program!r}): unsupported_count debe ser "
                "identico a unsupported_construct_count (misma fuente: "
                "CanonicalProgram.unsupported_constructs)"
            )
        return self

    @model_validator(mode="after")
    def _check_decision_partition(self) -> ProgramSemanticCoverage:
        total = (
            self.decisions_with_resolved_effect_count + self.decisions_without_resolved_effect_count
        )
        if total != self.decision_count:
            raise ValueError(
                f"ProgramSemanticCoverage({self.program!r}): "
                "decisions_with_resolved_effect_count + "
                "decisions_without_resolved_effect_count != decision_count"
            )
        return self

    @model_validator(mode="after")
    def _check_zero_candidate_reason_coherent(self) -> ProgramSemanticCoverage:
        is_present = self.zero_candidate_reason == ZeroCandidateReason.CANDIDATES_PRESENT
        if self.candidate_count > 0 and not is_present:
            raise ValueError(
                f"ProgramSemanticCoverage({self.program!r}): candidate_count > 0 requiere "
                "zero_candidate_reason=CANDIDATES_PRESENT"
            )
        if self.candidate_count == 0 and is_present:
            raise ValueError(
                f"ProgramSemanticCoverage({self.program!r}): candidate_count == 0 no puede "
                "declarar zero_candidate_reason=CANDIDATES_PRESENT"
            )
        return self

    @model_validator(mode="after")
    def _check_construct_coverage(self) -> ProgramSemanticCoverage:
        _check_construct_coverage_sorted_and_unique(
            self.construct_coverage, context_label=f"ProgramSemanticCoverage({self.program!r})"
        )
        return self

    @model_validator(mode="after")
    def _check_diagnostics(self) -> ProgramSemanticCoverage:
        _check_diagnostics_sorted_and_unique(
            self.diagnostics, context_label=f"ProgramSemanticCoverage({self.program!r})"
        )
        return self


class SemanticCoverageSummary(AltamiraBaseModel):
    """Agregacion de todos los ProgramSemanticCoverage de un run -- nunca
    pierde el detalle individual: `SemanticCoverageReport.programs`
    conserva cada programa por separado; este resumen es una suma
    verificable, no un reemplazo."""

    program_count: int = Field(ge=0)
    statement_count: int = Field(ge=0)
    fully_supported_count: int = Field(ge=0)
    partially_supported_count: int = Field(ge=0)
    preserved_only_count: int = Field(ge=0)
    unsupported_count: int = Field(ge=0)
    statement_counts_by_kind: dict[StatementKind, int] = Field(default_factory=dict)
    decision_count: int = Field(ge=0)
    decisions_with_resolved_effect_count: int = Field(ge=0)
    decisions_without_resolved_effect_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    level_88_data_item_count: int = Field(ge=0)
    unsupported_construct_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _check_statement_status_partition(self) -> SemanticCoverageSummary:
        total = (
            self.fully_supported_count + self.partially_supported_count + self.preserved_only_count
        )
        if total != self.statement_count:
            raise ValueError(
                "SemanticCoverageSummary: fully_supported_count + partially_supported_count + "
                "preserved_only_count != statement_count"
            )
        if sum(self.statement_counts_by_kind.values()) != self.statement_count:
            raise ValueError(
                "SemanticCoverageSummary: suma de statement_counts_by_kind no coincide "
                "con statement_count"
            )
        return self

    @model_validator(mode="after")
    def _check_unsupported_matches_constructs(self) -> SemanticCoverageSummary:
        if self.unsupported_count != self.unsupported_construct_count:
            raise ValueError(
                "SemanticCoverageSummary: unsupported_count debe ser identico a "
                "unsupported_construct_count"
            )
        return self

    @model_validator(mode="after")
    def _check_decision_partition(self) -> SemanticCoverageSummary:
        total = (
            self.decisions_with_resolved_effect_count + self.decisions_without_resolved_effect_count
        )
        if total != self.decision_count:
            raise ValueError(
                "SemanticCoverageSummary: decisions_with_resolved_effect_count + "
                "decisions_without_resolved_effect_count != decision_count"
            )
        return self


class SemanticCoverageReport(AltamiraBaseModel):
    """Contenedor persistido en `<run_dir>/diagnostics/semantic-coverage.json`.

    NO contractual (fuera de `artifacts/01-10`): un run historico sin
    este archivo se comporta exactamente igual que hoy. `schema_version`
    y `analyzer_version` son independientes -- el primero versiona la
    FORMA de este contrato, el segundo versiona la LOGICA de
    clasificacion del analizador (`semantic_coverage_analyzer.py`);
    ambos pueden evolucionar en momentos distintos. `analyzer_version`
    subio a "1.1" en la Fase 3 de la ampliacion semantica (soporte nivel
    88): la FORMA del contrato no cambio (mismos campos), solo la logica
    de clasificacion de condiciones 88/SET/referencias IF-EVALUATE
    (ver docs/LEVEL_88_SUPPORT.md) -- por eso `schema_version` permanece
    en "1.0". `analyzer_version` subio a "1.2" en la Fase 6 (fundacion
    interprocedural CALL/LINKAGE, ver docs/INTERPROCEDURAL_CALL_LINKAGE.md)
    por el mismo motivo: `StatementKind.CALL` se clasifica via los
    campos ya existentes (`statement_counts_by_kind`/`construct_coverage`/
    `diagnostics`), sin agregar ningun campo nuevo al contrato.
    `analyzer_version` subio a "1.3" en la Fase 7b (distincion GOBACK/
    STOP RUN/EXIT PROGRAM, ver docs/INTERPROCEDURAL_PROPAGATION.md) por
    el mismo motivo exacto: `StatementKind.PROGRAM_TERMINATION` se
    clasifica via los campos ya existentes, sin agregar ningun campo
    nuevo al contrato.

    Deliberadamente SIN ningun timestamp: dos ejecuciones sobre los
    mismos artefactos de entrada deben producir bytes identicos."""

    schema_version: Literal["1.0"] = "1.0"
    analyzer_version: Literal["1.0", "1.1", "1.2", "1.3"] = "1.3"
    run_id: str = Field(min_length=1)
    source_package_hash: Sha256Hex
    source_artifact_hashes: dict[str, Sha256Hex] = Field(
        min_length=len(REQUIRED_SOURCE_ARTIFACT_KEYS)
    )
    summary: SemanticCoverageSummary
    programs: list[ProgramSemanticCoverage] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_required_source_artifact_hashes(self) -> SemanticCoverageReport:
        missing = REQUIRED_SOURCE_ARTIFACT_KEYS - self.source_artifact_hashes.keys()
        if missing:
            raise ValueError(
                f"source_artifact_hashes no contiene las claves requeridas: {sorted(missing)}"
            )
        return self

    @model_validator(mode="after")
    def _check_programs_sorted_and_unique(self) -> SemanticCoverageReport:
        names = [program.program for program in self.programs]
        if len(names) != len(set(names)):
            raise ValueError("programs contiene program duplicado")
        if names != sorted(names):
            raise ValueError("programs no esta ordenado deterministicamente por program")
        return self

    @model_validator(mode="after")
    def _check_summary_matches_programs(self) -> SemanticCoverageReport:
        expected_program_count = len(self.programs)
        if self.summary.program_count != expected_program_count:
            raise ValueError(
                f"summary.program_count ({self.summary.program_count}) != cantidad de "
                f"programs ({expected_program_count})"
            )

        def _sum(attr: str) -> int:
            return sum(getattr(program, attr) for program in self.programs)

        simple_fields = (
            "statement_count",
            "fully_supported_count",
            "partially_supported_count",
            "preserved_only_count",
            "unsupported_count",
            "decision_count",
            "decisions_with_resolved_effect_count",
            "decisions_without_resolved_effect_count",
            "candidate_count",
            "level_88_data_item_count",
            "unsupported_construct_count",
        )
        for field_name in simple_fields:
            expected = _sum(field_name)
            actual = getattr(self.summary, field_name)
            if actual != expected:
                raise ValueError(
                    f"summary.{field_name} ({actual}) no coincide con la suma de "
                    f"programs[].{field_name} ({expected})"
                )

        expected_by_kind: dict[StatementKind, int] = {}
        for program in self.programs:
            for kind, count in program.statement_counts_by_kind.items():
                expected_by_kind[kind] = expected_by_kind.get(kind, 0) + count
        if self.summary.statement_counts_by_kind != expected_by_kind:
            raise ValueError(
                "summary.statement_counts_by_kind no coincide con la suma de "
                "programs[].statement_counts_by_kind"
            )
        return self


# =============================================================================
# Fase 15B2-A: manifiesto ESTATICO de cobertura semantica del PRODUCTO
# (`config/semantic_coverage.yaml`, cargado como `SemanticCoverageManifest`).
#
# Deliberadamente en el MISMO modulo que el diagnostico por-run de arriba
# (Fase 1 de la ampliacion semantica) porque el usuario asi lo pidio
# explicitamente, pero son dos conceptos DISTINTOS que nunca se combinan:
#
# - `SemanticCoverageReport` (arriba): que aparecio en UN run concreto,
#   contra `CanonicalProgram`/`SemanticGraph`/`CandidateArtifact` YA
#   persistidos de ese run -- se recalcula por run, nunca se versiona.
# - `SemanticCoverageManifest` (abajo): que el PRODUCTO declara soportar
#   HOY, independientemente de cualquier run -- se versiona en
#   `config/semantic_coverage.yaml`, se reconcilia contra los registries
#   reales (`pipeline/semantic_coverage_registry.py`) pero nunca se deriva
#   de la salida de un run.
#
# Unico choque de nombre real: el diagnostico por-run ya declara
# `SemanticCoverageSummary` arriba. El resumen del manifiesto se llama
# `SemanticCoverageManifestSummary` (en vez de `SemanticCoverageSummary`,
# como pedia literalmente la Parte A) para no romper ese contrato
# existente, ya consumido por `cli.py`/`pipeline/semantic_coverage_service.py`
# y sus tests -- una desviacion minima, deliberada y documentada aqui.
# =============================================================================

_CONSTRUCT_ID_PATTERN: Final = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$")


class SemanticCoverageStatus(StrEnum):
    """Nivel de soporte de UNA construccion en UNA capa especifica del
    manifiesto estatico -- nunca combinado con `SemanticSupportStatus`
    (esa es la dimension por-run, de arriba)."""

    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    RECOGNIZED_NOT_INTERPRETED = "RECOGNIZED_NOT_INTERPRETED"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


class SemanticCoverageLayer(StrEnum):
    """Capa arquitectonica evaluada para una construccion. Deliberadamente
    separa `PROLEAP_PARSER` (que la gramatica/ASG de ProLeap reconoce) de
    `JAVA_STATEMENT_EXTRACTION` (que `StatementExtractor.java` convierte a
    un `CanonicalStatement`) -- ProLeap puede reconocer una construccion
    que el extractor todavia no convierte especificamente (cae en
    `convertOther`, `kind=OTHER`), y eso NUNCA debe presentarse como
    soporte semantico real."""

    PROLEAP_PARSER = "PROLEAP_PARSER"
    JAVA_STATEMENT_EXTRACTION = "JAVA_STATEMENT_EXTRACTION"
    CANONICAL_REPRESENTATION = "CANONICAL_REPRESENTATION"
    SEMANTIC_GRAPH = "SEMANTIC_GRAPH"
    DATA_FLOW = "DATA_FLOW"
    CONTROL_FLOW = "CONTROL_FLOW"
    INTERPROCEDURAL = "INTERPROCEDURAL"
    DETECTOR = "DETECTOR"
    EVIDENCE = "EVIDENCE"
    PROVENANCE = "PROVENANCE"
    FUNCTIONAL_VALIDATION = "FUNCTIONAL_VALIDATION"


class ValidationEvidenceKind(StrEnum):
    """Tipo de evidencia que respalda el status declarado de una capa.
    `DOMAIN_REVIEW` es la UNICA que puede respaldar `domain_reviewed=true`
    -- ver `SemanticConstructCoverage._check_domain_reviewed_requires_
    domain_review_evidence`."""

    UNIT_TEST = "UNIT_TEST"
    CONTRACT_TEST = "CONTRACT_TEST"
    JAVA_TEST = "JAVA_TEST"
    INTEGRATION_TEST = "INTEGRATION_TEST"
    SYNTHETIC_FIXTURE = "SYNTHETIC_FIXTURE"
    PACKAGE_FIXTURE = "PACKAGE_FIXTURE"
    DOMAIN_REVIEW = "DOMAIN_REVIEW"


class SemanticCoverageEvidenceReference(AltamiraBaseModel):
    """Referencia sanitizada a UNA evidencia (test/fixture/revision) que
    respalda el status declarado de una capa. `reference` es un
    identificador relativo (path de test, nodeid de pytest, nombre de
    fixture o una referencia de revision no personal) -- nunca contenido,
    nunca codigo."""

    kind: ValidationEvidenceKind
    reference: RelativePath
    description: str | None = Field(default=None, max_length=300)


class SemanticLayerCoverage(AltamiraBaseModel):
    """Status de UNA capa para UNA construccion -- una fila de la matriz
    capa x construccion. `SUPPORTED` exige evidencia verificable
    (`_check_supported_has_evidence`): un status que afirma soporte
    completo sin ningun test/fixture/revision que lo demuestre es
    exactamente la afirmacion sin evidencia que el principio de
    honestidad funcional prohibe."""

    layer: SemanticCoverageLayer
    status: SemanticCoverageStatus
    notes: str | None = Field(default=None, max_length=500)
    evidence: list[SemanticCoverageEvidenceReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_supported_has_evidence(self) -> SemanticLayerCoverage:
        if self.status == SemanticCoverageStatus.SUPPORTED and not self.evidence:
            raise ValueError(
                f"layer={self.layer.value}: status=SUPPORTED exige al menos una "
                "evidencia verificable (SemanticCoverageEvidenceReference)"
            )
        return self


class SemanticDetectorCoverage(AltamiraBaseModel):
    """Detector asociado a una construccion -- `detector_id` debe
    coincidir con un id real de V1/`V2_DETECTOR_REGISTRY`/
    `INTERPROCEDURAL_RULE_DETECTOR_REGISTRY` (reconciliado por
    `pipeline/semantic_coverage_registry.py`, nunca inventado aqui)."""

    detector_id: str = Field(min_length=1, max_length=200)
    rule_families: list[str] = Field(default_factory=list)
    notes: str | None = Field(default=None, max_length=500)


class SemanticRuleFamilyCoverage(AltamiraBaseModel):
    """Familia de regla unificada asociada a una construccion --
    `rule_family` debe coincidir con un valor real de `UnifiedRuleFamily`
    (reconciliado por `pipeline/semantic_coverage_registry.py`)."""

    rule_family: str = Field(min_length=1, max_length=100)
    status: SemanticCoverageStatus
    notes: str | None = Field(default=None, max_length=500)


class SemanticConstructCoverage(AltamiraBaseModel):
    """Cobertura completa de UNA construccion COBOL a traves de todas las
    capas arquitectonicas. `construct_id` es un slug estable en
    MAYUSCULAS_CON_GUION_BAJO (nunca un timestamp, nunca derivado de un
    run) -- p. ej. `IF`, `LEVEL_88_CONDITION`, `CALL_DYNAMIC`."""

    construct_id: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=100)
    parser_representation: str | None = Field(default=None, max_length=300)
    java_statement_kind: StatementKind | None = None
    canonical_representation: str | None = Field(default=None, max_length=300)
    graph_nodes: list[NodeLabel] = Field(default_factory=list)
    graph_relationships: list[RelationshipType] = Field(default_factory=list)
    propagation_support: str | None = Field(default=None, max_length=300)
    detectors: list[SemanticDetectorCoverage] = Field(default_factory=list)
    rule_families: list[SemanticRuleFamilyCoverage] = Field(default_factory=list)
    evidence_support: str | None = Field(default=None, max_length=300)
    provenance_support: str | None = Field(default=None, max_length=300)
    layers: list[SemanticLayerCoverage] = Field(default_factory=list)
    fixtures: list[RelativePath] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    unknown_behavior: str | None = Field(default=None, max_length=500)
    domain_reviewed: bool = False
    diagnostics: list[str] = Field(default_factory=list)

    @field_validator("construct_id")
    @classmethod
    def _check_construct_id_pattern(cls, value: str) -> str:
        if not _CONSTRUCT_ID_PATTERN.match(value):
            raise ValueError(
                f"construct_id invalido, se espera MAYUSCULAS_CON_GUION_BAJO: {value!r}"
            )
        return value

    @model_validator(mode="after")
    def _check_layers_cover_required_set_without_duplicates(self) -> SemanticConstructCoverage:
        declared = [entry.layer for entry in self.layers]
        if len(declared) != len(set(declared)):
            raise ValueError(f"construct_id={self.construct_id!r}: layers contiene duplicados")
        missing = set(SemanticCoverageLayer) - set(declared)
        if missing:
            missing_sorted = sorted(layer.value for layer in missing)
            raise ValueError(
                f"construct_id={self.construct_id!r}: faltan capas obligatorias {missing_sorted}"
            )
        return self

    @model_validator(mode="after")
    def _check_layers_ordering_deterministic(self) -> SemanticConstructCoverage:
        order = list(SemanticCoverageLayer)
        actual = [entry.layer for entry in self.layers]
        expected = sorted(actual, key=order.index)
        if actual != expected:
            raise ValueError(
                f"construct_id={self.construct_id!r}: layers no esta ordenado "
                "deterministicamente (orden declarado de SemanticCoverageLayer)"
            )
        return self

    @model_validator(mode="after")
    def _check_domain_reviewed_requires_domain_review_evidence(self) -> SemanticConstructCoverage:
        if not self.domain_reviewed:
            return self
        has_domain_review = any(
            ref.kind == ValidationEvidenceKind.DOMAIN_REVIEW
            for layer in self.layers
            for ref in layer.evidence
        )
        if not has_domain_review:
            raise ValueError(
                f"construct_id={self.construct_id!r}: domain_reviewed=true exige al menos "
                "una SemanticCoverageEvidenceReference(kind=DOMAIN_REVIEW) en alguna capa"
            )
        return self

    @model_validator(mode="after")
    def _check_detectors_sorted_and_unique(self) -> SemanticConstructCoverage:
        ids = [d.detector_id for d in self.detectors]
        if len(ids) != len(set(ids)):
            raise ValueError(f"construct_id={self.construct_id!r}: detectors duplicados")
        if ids != sorted(ids):
            raise ValueError(
                f"construct_id={self.construct_id!r}: detectors no esta ordenado "
                "deterministicamente"
            )
        return self

    @model_validator(mode="after")
    def _check_rule_families_sorted_and_unique(self) -> SemanticConstructCoverage:
        names = [f.rule_family for f in self.rule_families]
        if len(names) != len(set(names)):
            raise ValueError(f"construct_id={self.construct_id!r}: rule_families duplicadas")
        if names != sorted(names):
            raise ValueError(
                f"construct_id={self.construct_id!r}: rule_families no esta ordenado "
                "deterministicamente"
            )
        return self

    @model_validator(mode="after")
    def _check_fixtures_sorted_and_unique(self) -> SemanticConstructCoverage:
        if len(self.fixtures) != len(set(self.fixtures)):
            raise ValueError(f"construct_id={self.construct_id!r}: fixtures duplicadas")
        if self.fixtures != sorted(self.fixtures):
            raise ValueError(
                f"construct_id={self.construct_id!r}: fixtures no esta ordenado "
                "deterministicamente"
            )
        return self

    @model_validator(mode="after")
    def _check_limitations_and_diagnostics_sorted_and_unique(self) -> SemanticConstructCoverage:
        for field_name in ("limitations", "diagnostics"):
            values: list[str] = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(
                    f"construct_id={self.construct_id!r}: {field_name} contiene duplicados"
                )
            if values != sorted(values):
                raise ValueError(
                    f"construct_id={self.construct_id!r}: {field_name} no esta ordenado "
                    "deterministicamente"
                )
        return self


class SemanticCoverageIssue(AltamiraBaseModel):
    """Problema detectado por la reconciliacion ejecutable
    (`pipeline/semantic_coverage_registry.py`) -- p. ej. un StatementKind
    real sin entrada en el manifiesto, o una referencia de test que no
    existe. Nunca contiene codigo ni mensajes libres sin `reason_code`
    cerrado."""

    issue_id: str = Field(min_length=1, max_length=200)
    construct_id: str | None = Field(default=None, max_length=100)
    severity: Severity
    reason_code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=500)


class SemanticCoverageManifestSummary(AltamiraBaseModel):
    """Agregacion verificable del manifiesto completo -- ver nota de
    modulo sobre por que este modelo no se llama `SemanticCoverageSummary`
    (ese nombre ya esta tomado por el diagnostico por-run, arriba)."""

    construct_count: int = Field(ge=0)
    domain_reviewed_count: int = Field(ge=0)
    issue_count: int = Field(ge=0)
    counts_by_layer_and_status: dict[SemanticCoverageLayer, dict[SemanticCoverageStatus, int]] = (
        Field(default_factory=dict)
    )


class SemanticCoverageManifest(AltamiraBaseModel):
    """Contenedor persistido en `config/semantic_coverage.yaml` --
    declara la capacidad CONOCIDA del producto, independiente de
    cualquier run. Deliberadamente sin ningun timestamp: dos cargas del
    mismo YAML deben producir el mismo modelo validado."""

    schema_version: Literal["1.0"] = "1.0"
    manifest_edition: str = Field(min_length=1, max_length=100)
    constructs: list[SemanticConstructCoverage] = Field(default_factory=list)
    issues: list[SemanticCoverageIssue] = Field(default_factory=list)
    summary: SemanticCoverageManifestSummary

    @model_validator(mode="after")
    def _check_constructs_sorted_and_unique(self) -> SemanticCoverageManifest:
        ids = [c.construct_id for c in self.constructs]
        if len(ids) != len(set(ids)):
            raise ValueError("constructs contiene construct_id duplicado")
        if ids != sorted(ids):
            raise ValueError("constructs no esta ordenado deterministicamente por construct_id")
        return self

    @model_validator(mode="after")
    def _check_issues_sorted_and_unique(self) -> SemanticCoverageManifest:
        ids = [i.issue_id for i in self.issues]
        if len(ids) != len(set(ids)):
            raise ValueError("issues contiene issue_id duplicado")
        if ids != sorted(ids):
            raise ValueError("issues no esta ordenado deterministicamente por issue_id")
        return self

    @model_validator(mode="after")
    def _check_summary_matches_constructs_and_issues(self) -> SemanticCoverageManifest:
        if self.summary.construct_count != len(self.constructs):
            raise ValueError(
                f"summary.construct_count ({self.summary.construct_count}) != cantidad de "
                f"constructs ({len(self.constructs)})"
            )
        expected_domain_reviewed = sum(1 for c in self.constructs if c.domain_reviewed)
        if self.summary.domain_reviewed_count != expected_domain_reviewed:
            raise ValueError(
                f"summary.domain_reviewed_count ({self.summary.domain_reviewed_count}) != "
                f"cantidad real de constructs domain_reviewed=true ({expected_domain_reviewed})"
            )
        if self.summary.issue_count != len(self.issues):
            raise ValueError(
                f"summary.issue_count ({self.summary.issue_count}) != cantidad de issues "
                f"({len(self.issues)})"
            )
        expected_counts: dict[SemanticCoverageLayer, dict[SemanticCoverageStatus, int]] = {}
        for construct in self.constructs:
            for layer_entry in construct.layers:
                by_status = expected_counts.setdefault(layer_entry.layer, {})
                by_status[layer_entry.status] = by_status.get(layer_entry.status, 0) + 1
        if self.summary.counts_by_layer_and_status != expected_counts:
            raise ValueError(
                "summary.counts_by_layer_and_status no coincide con la suma real de "
                "constructs[].layers[].status"
            )
        return self
