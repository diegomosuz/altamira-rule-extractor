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

from enum import StrEnum
from typing import Final, Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, RelativePath, Sha256Hex
from .enums import LocationKind, StatementKind

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
    en "1.0".

    Deliberadamente SIN ningun timestamp: dos ejecuciones sobre los
    mismos artefactos de entrada deben producir bytes identicos."""

    schema_version: Literal["1.0"] = "1.0"
    analyzer_version: Literal["1.0", "1.1"] = "1.1"
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
