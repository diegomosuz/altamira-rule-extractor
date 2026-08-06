"""Contrato tipado de observaciones POR-RUN del catalogo estatico de
construcciones (Fase 15B2-A, Parte D). Persiste en `<run_dir>/
diagnostics/semantic-coverage-observations.json`, fuera de
`artifacts/01-10` -- mismo principio no-contractual que
`SemanticCoverageReport` (Fase 1, `contracts/semantic_coverage.py`).

Distinto de AMBOS contratos existentes en `semantic_coverage.py`:

- `SemanticCoverageReport` (Fase 1): agrega por `StatementKind`/status
  generico (`SemanticSupportStatus`), con `ConstructCoverage.
  construct_name` como string libre -- NUNCA atado al catalogo
  `construct_id` de Parte B/`SemanticCoverageManifest`.
- `SemanticCoverageManifest` (Parte B/C): declara lo que el PRODUCTO dice
  soportar HOY, independiente de cualquier run.

Este contrato ata cada observacion a un `construct_id` REAL del catalogo
estatico (Parte B), midiendo cuantas veces se observo en ESTE run
concreto -- para detectar drift entre lo declarado y lo realmente
ejercitado. Granularidad REAL disponible: `CanonicalStatement.kind`
(`StatementKind`, 11 valores) mas, quando `kind=OTHER`, el nombre de
clase ASG sanitizado extraido de `CanonicalProgram.unsupported_constructs`
(nunca el mensaje completo -- ver `SemanticCoverageUnsupportedObservation`
y decision arquitectonica #5: solo campos cerrados, nunca `source_text`,
SQL, expresiones completas, paths, nombres de archivo, mensajes libres
del parser ni codigo COBOL).

Limite HONESTO deliberado: varios `construct_id` del catalogo comparten
el mismo `java_statement_kind` (p. ej. IF/ELSE/CONDITIONS_COMPOUND
comparten `kind=IF`; PERFORM/PERFORM_THRU/INLINE_PERFORM comparten
`kind=PERFORM`). Este contrato NUNCA finge una precision de
disambiguacion que `CanonicalStatement.kind` no ofrece: esos
`construct_id` hermanos reciben el MISMO `occurrence_count`, y
`shared_java_statement_kind_construct_ids` lo declara explicitamente en
vez de ocultarlo.

Ningun campo admite un timestamp de reloj: dos ejecuciones sobre los
mismos artefactos de entrada deben producir bytes identicos."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, Sha256Hex
from .enums import StatementKind

MAX_UNSUPPORTED_IDENTITY_LENGTH = 80


class SemanticCoverageConstructObservation(AltamiraBaseModel):
    """Ocurrencias observadas de UN `construct_id` del catalogo estatico
    durante ESTE run, medidas a nivel `CanonicalStatement.kind`
    (`java_statement_kind` del catalogo). `construct_id` sin
    `java_statement_kind` en el catalogo (p. ej. `LINKAGE_SECTION`,
    `LITERALS`, `BATCH_JOB`) nunca aparece aqui -- este contrato solo
    puede observar lo que `CanonicalStatement.kind` distingue."""

    construct_id: str = Field(min_length=1, max_length=100)
    java_statement_kind: StatementKind
    observed: bool
    occurrence_count: int = Field(ge=0)
    program_count: int = Field(ge=0)
    shared_java_statement_kind_construct_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_observed_matches_occurrence_count(
        self,
    ) -> SemanticCoverageConstructObservation:
        if self.observed != (self.occurrence_count > 0):
            raise ValueError(
                f"construct_id={self.construct_id!r}: observed debe ser exactamente "
                "(occurrence_count > 0)"
            )
        if self.occurrence_count == 0 and self.program_count != 0:
            raise ValueError(
                f"construct_id={self.construct_id!r}: occurrence_count=0 exige "
                "program_count=0"
            )
        return self

    @model_validator(mode="after")
    def _check_shared_ids_sorted_and_unique(self) -> SemanticCoverageConstructObservation:
        ids = self.shared_java_statement_kind_construct_ids
        if self.construct_id in ids:
            raise ValueError(
                f"construct_id={self.construct_id!r}: shared_java_statement_kind_construct_ids "
                "no puede contenerse a si mismo"
            )
        if len(ids) != len(set(ids)):
            raise ValueError(
                f"construct_id={self.construct_id!r}: shared_java_statement_kind_construct_ids "
                "contiene duplicados"
            )
        if ids != sorted(ids):
            raise ValueError(
                f"construct_id={self.construct_id!r}: shared_java_statement_kind_construct_ids "
                "no esta ordenado deterministicamente"
            )
        return self


class SemanticCoverageUnsupportedObservation(AltamiraBaseModel):
    """Ocurrencias sanitizadas de UNA identidad extraida de
    `CanonicalProgram.unsupported_constructs` (regla de seguridad: NUNCA
    el mensaje crudo completo -- solo el prefijo identificador, acotado a
    `MAX_UNSUPPORTED_IDENTITY_LENGTH`, sin `paragraph`/programa
    individuales). `construct_id` es `None` cuando la identidad no pudo
    asociarse con certeza a un `construct_id` real del catalogo (decision
    arquitectonica #4: nunca inventar la asociacion; ver
    `pipeline/semantic_coverage_observations_service.py::
    _PARSER_CLASS_TO_CONSTRUCT_ID`, la unica tabla de asociacion, curada
    manualmente contra evidencia real del JAR de ProLeap)."""

    identity: str = Field(min_length=1, max_length=MAX_UNSUPPORTED_IDENTITY_LENGTH)
    construct_id: str | None = Field(default=None, max_length=100)
    occurrence_count: int = Field(ge=1)
    program_count: int = Field(ge=1)


_ObservationList = (
    list[SemanticCoverageConstructObservation] | list[SemanticCoverageUnsupportedObservation]
)


def _check_sorted_unique_by(
    entries: _ObservationList,
    *,
    key_name: Literal["construct_id", "identity"],
    context_label: str,
) -> None:
    keys = [getattr(entry, key_name) for entry in entries]
    if len(keys) != len(set(keys)):
        raise ValueError(f"{context_label}: {key_name} duplicado")
    if keys != sorted(keys):
        raise ValueError(f"{context_label}: no esta ordenado deterministicamente por {key_name}")


class SemanticCoverageObservationsSummary(AltamiraBaseModel):
    """Agregacion verificable de `SemanticCoverageObservationsArtifact`."""

    construct_count: int = Field(ge=0)
    observed_construct_count: int = Field(ge=0)
    unsupported_identity_count: int = Field(ge=0)
    mapped_unsupported_identity_count: int = Field(ge=0)


class SemanticCoverageObservationsArtifact(AltamiraBaseModel):
    """Contenedor persistido en `<run_dir>/diagnostics/semantic-coverage-
    observations.json`. NO contractual (fuera de `artifacts/01-10`): un
    run historico sin este archivo se comporta exactamente igual que hoy."""

    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    source_package_hash: Sha256Hex
    manifest_edition: str = Field(min_length=1, max_length=100)
    constructs: list[SemanticCoverageConstructObservation] = Field(default_factory=list)
    unsupported_identities: list[SemanticCoverageUnsupportedObservation] = Field(
        default_factory=list
    )
    summary: SemanticCoverageObservationsSummary

    @model_validator(mode="after")
    def _check_constructs_sorted_and_unique(self) -> SemanticCoverageObservationsArtifact:
        _check_sorted_unique_by(
            self.constructs, key_name="construct_id", context_label="constructs"
        )
        return self

    @model_validator(mode="after")
    def _check_unsupported_identities_sorted_and_unique(
        self,
    ) -> SemanticCoverageObservationsArtifact:
        _check_sorted_unique_by(
            self.unsupported_identities, key_name="identity", context_label="unsupported_identities"
        )
        return self

    @model_validator(mode="after")
    def _check_summary_matches_constructs(self) -> SemanticCoverageObservationsArtifact:
        if self.summary.construct_count != len(self.constructs):
            raise ValueError(
                f"summary.construct_count ({self.summary.construct_count}) != cantidad de "
                f"constructs ({len(self.constructs)})"
            )
        expected_observed = sum(1 for c in self.constructs if c.observed)
        if self.summary.observed_construct_count != expected_observed:
            raise ValueError(
                f"summary.observed_construct_count ({self.summary.observed_construct_count}) != "
                f"cantidad real de constructs observed=true ({expected_observed})"
            )
        if self.summary.unsupported_identity_count != len(self.unsupported_identities):
            raise ValueError(
                f"summary.unsupported_identity_count ({self.summary.unsupported_identity_count}) "
                f"!= cantidad de unsupported_identities ({len(self.unsupported_identities)})"
            )
        expected_mapped = sum(
            1 for entry in self.unsupported_identities if entry.construct_id is not None
        )
        if self.summary.mapped_unsupported_identity_count != expected_mapped:
            raise ValueError(
                "summary.mapped_unsupported_identity_count "
                f"({self.summary.mapped_unsupported_identity_count}) != cantidad real de "
                f"unsupported_identities con construct_id resuelto ({expected_mapped})"
            )
        return self
