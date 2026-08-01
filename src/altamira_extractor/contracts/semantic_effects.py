"""Contrato tipado del artefacto de efectos semanticos normalizados (Fase 2
de la ampliacion semantica, checkpoint `feat/semantic-effects-foundation`).

Diagnostico, NO contractual: persiste en `<run_dir>/diagnostics/
semantic-effects.json`, fuera de `artifacts/01-10` -- mismo patron que
`contracts/run_state_recovery.py` y `contracts/semantic_coverage.py` (Fase
1): un artefacto adyacente, opcional, irrelevante para que un run llegue a
COMPLETED, ignorado por toda etapa V1, compatible con runs historicos que
nunca lo generaron.

Modelo INDEPENDIENTE del informe de cobertura semantica (Fase 1):
`semantic_coverage.py` mide CUANTO se interpreto; este modulo expresa QUE
se interpreto, como un efecto normalizado con procedencia hasta el
`CanonicalStatement` que lo origino. Ambos comparten unicamente
`SemanticSupportStatus` (import de una sola direccion,
`semantic_effects.py -> semantic_coverage.py`; `semantic_coverage.py`
nunca importa de vuelta -- sin acoplamiento circular) porque la nocion de
"cuan completa es la interpretacion" es identica en ambos casos, no
porque compartan ningun otro aspecto de su modelo.

Deliberadamente SIN propagacion de valores: un `SemanticEffect` describe
UNICAMENTE la sentencia que lo origino. Una cadena `MOVE '0005' TO
WS-COD-AUX` / `MOVE WS-COD-AUX TO WS-COD-RETORNO` produce dos efectos
independientes (ver `pipeline/semantic_effects_analyzer.py`); ningun
campo de este contrato existe para expresar una cadena de asignaciones o
un valor que "atraviesa" mas de un efecto."""

from __future__ import annotations

from enum import StrEnum
from typing import Final, Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, RelativePath, Sha256Hex
from .enums import BranchKind, LocationKind, StatementKind, TableAccessOperation
from .semantic_coverage import SemanticSupportStatus

# Unico artefacto de entrada que este modulo requiere (a diferencia de
# semantic_coverage.py, que ademas necesita dependencias/grafo/candidatos):
# los efectos se derivan exclusivamente de CanonicalProgram.
REQUIRED_SOURCE_ARTIFACT_KEYS: Final[frozenset[str]] = frozenset({"artifacts/02-canonical"})


class SemanticEffectKind(StrEnum):
    """Efecto semantico normalizado. `SET_CONDITION_TRUE`/
    `SET_CONDITION_FALSE` (Fase 3 de la ampliacion semantica --
    soporte nivel 88) se agregaron una vez que `CanonicalProgram`
    conserva `condition_names` y `CanonicalStatement.condition_name_target`/
    `condition_set_value` con evidencia estructural verificada por el
    parser Java (ver docs/LEVEL_88_SUPPORT.md). `CALL_PROGRAM`,
    `CICS_LINK`, `READ_FILE`, `WRITE_FILE` siguen fuera de este conjunto:
    requieren informacion que el parser actual no conserva con certeza
    (LINKAGE SECTION, CALL, EXEC CICS, FD/SELECT -- ver auditoria previa)
    y quedan fuera hasta que esa informacion exista en `CanonicalProgram`."""

    ASSIGN_LITERAL = "ASSIGN_LITERAL"
    COPY_VALUE = "COPY_VALUE"
    COMPUTE_VALUE = "COMPUTE_VALUE"
    SET_VALUE = "SET_VALUE"
    SET_CONDITION_TRUE = "SET_CONDITION_TRUE"
    SET_CONDITION_FALSE = "SET_CONDITION_FALSE"
    CONTROL_TRANSFER = "CONTROL_TRANSFER"
    EXECUTE_SQL = "EXECUTE_SQL"
    PRESERVED_STATEMENT = "PRESERVED_STATEMENT"
    UNSUPPORTED_STATEMENT = "UNSUPPORTED_STATEMENT"


class SemanticEffectSourceReference(AltamiraBaseModel):
    """Procedencia obligatoria de un `SemanticEffect` hasta el
    `CanonicalStatement` que lo origino. Nunca incluye `source_text`."""

    program: str = Field(min_length=1)
    paragraph: str = Field(min_length=1)
    statement_id: str = Field(min_length=1)
    statement_kind: StatementKind
    source_file: RelativePath | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    location_kind: LocationKind
    parent_statement_id: str | None = None
    branch_kind: BranchKind | None = None

    @model_validator(mode="after")
    def _check_line_order(self) -> SemanticEffectSourceReference:
        if self.line_start is not None and self.line_end is not None:
            if self.line_end < self.line_start:
                raise ValueError("line_end no puede ser anterior a line_start")
        return self


class SemanticEffect(AltamiraBaseModel):
    """Un efecto semantico normalizado. `reads`/`writes` reflejan
    `CanonicalStatement.variables_read`/`variables_written` tal cual;
    `source_data_items`/`target_data_items` son los roles SEMANTICOS
    especificos de este efecto (p. ej. un `ASSIGN_LITERAL` nunca tiene
    `source_data_items`: no hay una variable origen, solo un literal).

    `sql_host_variables` es un campo NEUTRAL (sin direccion) para variables
    host de un EXEC SQL: `CanonicalSqlAccess.host_variables` es una unica
    lista plana que el extractor Java construye con un regex sobre TODO el
    texto de la sentencia (INTO, WHERE, VALUES, SET indistintamente) -- no
    hay evidencia estructural para decidir si una variable es entrada o
    salida (ver auditoria en `docs/SEMANTIC_EFFECTS.md`). `reads`/`writes`
    de un `EXECUTE_SQL` solo pueden poblarse cuando exista informacion
    inequivoca; mientras eso no exista, permanecen vacios y la variable
    aparece unicamente en `sql_host_variables` (ordenado, sin duplicados)
    junto con `diagnostic_code=SQL_HOST_VARIABLE_DIRECTION_UNRESOLVED`."""

    effect_id: str = Field(min_length=1)
    kind: SemanticEffectKind
    support_status: SemanticSupportStatus
    source_reference: SemanticEffectSourceReference
    reads: list[str] = Field(default_factory=list)
    writes: list[str] = Field(default_factory=list)
    source_data_items: list[str] = Field(default_factory=list)
    target_data_items: list[str] = Field(default_factory=list)
    literal: str | None = None
    expression: str | None = None
    control_targets: list[str] = Field(default_factory=list)
    sql_operation: TableAccessOperation | None = None
    sql_tables: list[str] = Field(default_factory=list)
    sql_host_variables: list[str] = Field(default_factory=list)
    condition_name: str | None = None
    parent_data_item: str | None = None
    condition_values: list[str] = Field(default_factory=list)
    diagnostic_codes: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _check_kind_specific_invariants(self) -> SemanticEffect:
        label = f"SemanticEffect({self.effect_id!r}, kind={self.kind.value})"
        if self.kind == SemanticEffectKind.ASSIGN_LITERAL:
            if self.literal is None or not self.target_data_items:
                raise ValueError(f"{label}: ASSIGN_LITERAL exige literal y al menos un target")
        elif self.kind == SemanticEffectKind.COPY_VALUE:
            if not self.source_data_items or not self.target_data_items:
                raise ValueError(
                    f"{label}: COPY_VALUE exige al menos un source y un target"
                )
        elif self.kind == SemanticEffectKind.COMPUTE_VALUE:
            if self.expression is None or not self.target_data_items:
                raise ValueError(f"{label}: COMPUTE_VALUE exige expression y target")
        elif self.kind in (
            SemanticEffectKind.SET_CONDITION_TRUE,
            SemanticEffectKind.SET_CONDITION_FALSE,
        ):
            missing_condition_fields = (
                self.condition_name is None
                or self.parent_data_item is None
                or not self.condition_values
            )
            if missing_condition_fields:
                raise ValueError(
                    f"{label}: SET_CONDITION_TRUE/FALSE exige condition_name, parent_data_item "
                    "y al menos un condition_value"
                )
        elif self.kind == SemanticEffectKind.CONTROL_TRANSFER:
            if not self.control_targets:
                raise ValueError(f"{label}: CONTROL_TRANSFER exige control_targets")
        elif self.kind == SemanticEffectKind.EXECUTE_SQL:
            if self.sql_operation is None:
                raise ValueError(f"{label}: EXECUTE_SQL exige sql_operation")
            if "SQL_HOST_VARIABLE_DIRECTION_UNRESOLVED" in self.diagnostic_codes and (
                self.reads or self.writes
            ):
                raise ValueError(
                    f"{label}: SQL_HOST_VARIABLE_DIRECTION_UNRESOLVED no puede coexistir "
                    "con reads/writes poblados (CanonicalSqlAccess no distingue direccion "
                    "de variables host: ver docs/SEMANTIC_EFFECTS.md)"
                )
        elif self.kind == SemanticEffectKind.PRESERVED_STATEMENT:
            if self.writes or self.target_data_items or self.literal is not None:
                raise ValueError(
                    f"{label}: PRESERVED_STATEMENT no puede afirmar writes/target/literal"
                )
        elif self.kind == SemanticEffectKind.UNSUPPORTED_STATEMENT:
            if not self.diagnostic_codes:
                raise ValueError(f"{label}: UNSUPPORTED_STATEMENT exige diagnostic_codes")
        return self

    @model_validator(mode="after")
    def _check_sql_host_variables_sorted_and_unique(self) -> SemanticEffect:
        if self.sql_host_variables != sorted(set(self.sql_host_variables)):
            raise ValueError(
                f"SemanticEffect({self.effect_id!r}): sql_host_variables debe estar "
                "ordenado alfabeticamente y sin duplicados"
            )
        return self


def _effect_sort_key(effect: SemanticEffect) -> str:
    return effect.effect_id


def _check_effects_sorted_and_unique(effects: list[SemanticEffect], *, context_label: str) -> None:
    ids = [effect.effect_id for effect in effects]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{context_label}: effects contiene effect_id duplicado")
    if ids != sorted(ids):
        raise ValueError(f"{context_label}: effects no esta ordenado deterministicamente")


class ProgramSemanticEffects(AltamiraBaseModel):
    program: str = Field(min_length=1)
    effect_count: int = Field(ge=0)
    effects: list[SemanticEffect] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_effect_count(self) -> ProgramSemanticEffects:
        if self.effect_count != len(self.effects):
            raise ValueError(
                f"ProgramSemanticEffects({self.program!r}): effect_count "
                f"({self.effect_count}) != len(effects) ({len(self.effects)})"
            )
        return self

    @model_validator(mode="after")
    def _check_effects_ordered(self) -> ProgramSemanticEffects:
        _check_effects_sorted_and_unique(
            self.effects, context_label=f"ProgramSemanticEffects({self.program!r})"
        )
        return self


class SemanticEffectsSummary(AltamiraBaseModel):
    program_count: int = Field(ge=0)
    effect_count: int = Field(ge=0)
    counts_by_kind: dict[SemanticEffectKind, int] = Field(default_factory=dict)
    counts_by_support_status: dict[SemanticSupportStatus, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_counts_non_negative_and_consistent(self) -> SemanticEffectsSummary:
        for kind, count in self.counts_by_kind.items():
            if count < 0:
                raise ValueError(f"counts_by_kind[{kind.value}] no puede ser negativo")
        for status, count in self.counts_by_support_status.items():
            if count < 0:
                raise ValueError(f"counts_by_support_status[{status.value}] no puede ser negativo")
        if sum(self.counts_by_kind.values()) != self.effect_count:
            raise ValueError("suma de counts_by_kind no coincide con effect_count")
        if sum(self.counts_by_support_status.values()) != self.effect_count:
            raise ValueError("suma de counts_by_support_status no coincide con effect_count")
        return self


class SemanticEffectsArtifact(AltamiraBaseModel):
    """Contenedor persistido en `<run_dir>/diagnostics/semantic-effects.json`.
    NO contractual, sin timestamps: dos ejecuciones sobre los mismos
    artefactos de entrada deben producir bytes identicos.

    `schema_version` y `analyzer_version` subieron juntos a `"1.1"` en la
    Fase 3 de la ampliacion semantica (soporte nivel 88, ver
    docs/LEVEL_88_SUPPORT.md), a diferencia de `SemanticCoverageReport`
    (donde solo `analyzer_version` subio): aqui la FORMA del artefacto
    tambien cambio -- `SemanticEffect` gano tres campos nuevos
    (`condition_name`/`parent_data_item`/`condition_values`) y
    `SemanticEffectKind` gano dos valores nuevos
    (`SET_CONDITION_TRUE`/`SET_CONDITION_FALSE`) -- y la logica de
    interpretacion de `SET` cambio de forma inequivoca (un `SET
    condicion-88 TO TRUE/FALSE` resuelto ya no cae en el `SET_VALUE`
    generico de la Fase 2). El contrato acepta `"1.0"` en lectura (un
    `semantic-effects.json` generado por el analizador de la Fase 2 sigue
    cargando: los campos nuevos son opcionales/con default vacio); un
    analizador nuevo SIEMPRE emite `"1.1"` para ambos campos,
    independientemente de si el programa analizado usa o no nivel 88 (a
    diferencia de `CanonicalProgram.schema_version`, que es condicional
    por programa: aqui la version describe la CAPACIDAD del analizador,
    no el contenido de un artefacto individual). Regenerar el diagnostico
    sobre un run historico simplemente sobrescribe
    `diagnostics/semantic-effects.json` con un artefacto `"1.1"` nuevo
    (el archivo no es contractual ni se versiona por run; no hay
    migracion in-place)."""

    schema_version: Literal["1.0", "1.1"] = "1.1"
    analyzer_version: Literal["1.0", "1.1"] = "1.1"
    run_id: str = Field(min_length=1)
    source_package_hash: Sha256Hex
    source_artifact_hashes: dict[str, Sha256Hex] = Field(
        min_length=len(REQUIRED_SOURCE_ARTIFACT_KEYS)
    )
    summary: SemanticEffectsSummary
    programs: list[ProgramSemanticEffects] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_required_source_artifact_hashes(self) -> SemanticEffectsArtifact:
        missing = REQUIRED_SOURCE_ARTIFACT_KEYS - self.source_artifact_hashes.keys()
        if missing:
            raise ValueError(
                f"source_artifact_hashes no contiene las claves requeridas: {sorted(missing)}"
            )
        return self

    @model_validator(mode="after")
    def _check_programs_sorted_and_unique(self) -> SemanticEffectsArtifact:
        names = [program.program for program in self.programs]
        if len(names) != len(set(names)):
            raise ValueError("programs contiene program duplicado")
        if names != sorted(names):
            raise ValueError("programs no esta ordenado deterministicamente por program")
        return self

    @model_validator(mode="after")
    def _check_summary_matches_programs(self) -> SemanticEffectsArtifact:
        if self.summary.program_count != len(self.programs):
            raise ValueError(
                f"summary.program_count ({self.summary.program_count}) != cantidad de "
                f"programs ({len(self.programs)})"
            )
        expected_effect_count = sum(program.effect_count for program in self.programs)
        if self.summary.effect_count != expected_effect_count:
            raise ValueError(
                f"summary.effect_count ({self.summary.effect_count}) no coincide con la "
                f"suma de programs[].effect_count ({expected_effect_count})"
            )

        expected_by_kind: dict[SemanticEffectKind, int] = {}
        expected_by_status: dict[SemanticSupportStatus, int] = {}
        for program in self.programs:
            for effect in program.effects:
                expected_by_kind[effect.kind] = expected_by_kind.get(effect.kind, 0) + 1
                expected_by_status[effect.support_status] = (
                    expected_by_status.get(effect.support_status, 0) + 1
                )
        if self.summary.counts_by_kind != expected_by_kind:
            raise ValueError("summary.counts_by_kind no coincide con la agregacion de programs[]")
        if self.summary.counts_by_support_status != expected_by_status:
            raise ValueError(
                "summary.counts_by_support_status no coincide con la agregacion de programs[]"
            )
        return self
