"""Analizador PURO de efectos semanticos normalizados (Fase 2 de la
ampliacion semantica, checkpoint `feat/semantic-effects-foundation`).

Recibe unicamente `CanonicalProgram[]` ya cargados en memoria y devuelve
un `SemanticEffectsArtifact`. Nunca:

- lee filesystem (la carga es responsabilidad exclusiva de
  `semantic_effects_service.py`);
- accede a Neo4j ni consulta `SemanticGraph`;
- lee variables de entorno;
- escribe archivos;
- modifica los objetos de entrada;
- consulta `CandidateArtifact` (no reinterpreta ni alimenta deteccion de
  candidatos);
- propaga valores entre sentencias -- cada `SemanticEffect` describe
  EXCLUSIVAMENTE la sentencia que lo origino (ver
  `test_two_hop_move_chain_produces_no_propagation` en el test suite:
  `MOVE '0005' TO WS-COD-AUX` seguido de `MOVE WS-COD-AUX TO
  WS-COD-RETORNO` produce dos efectos independientes, nunca un tercero
  ni una anotacion que vincule el literal '0005' con WS-COD-RETORNO).

Toda clasificacion pasa por UNA tabla-registro (`_STATEMENT_NORMALIZERS`):
nunca se dispersa la logica en `if`/`elif` sueltos fuera de ese registro."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ..contracts.canonical import (
    CanonicalConditionName,
    CanonicalConditionValue,
    CanonicalDataItem,
    CanonicalProgram,
    CanonicalStatement,
)
from ..contracts.enums import LocationKind, StatementKind, TableAccessOperation
from ..contracts.semantic_coverage import SemanticSupportStatus
from ..contracts.semantic_effects import (
    ProgramSemanticEffects,
    SemanticEffect,
    SemanticEffectKind,
    SemanticEffectsArtifact,
    SemanticEffectSourceReference,
    SemanticEffectsSummary,
)

_UNKNOWN_PARAGRAPH_PLACEHOLDER = "UNKNOWN"
_UNSUPPORTED_CONSTRUCT_SEPARATOR = " en paragraph "


# ---------------------------------------------------------------------------
# Contexto de normalizacion + construccion de effect_id deterministico (Fase 2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _NormalizeContext:
    program_name: str
    paragraph_name: str
    stmt: CanonicalStatement
    level_88_names: frozenset[str]
    conditions_by_name: dict[str, CanonicalConditionName]

    def is_level_88_target(self, names: Sequence[str]) -> bool:
        return bool(set(names) & self.level_88_names)

    def build(
        self,
        kind: SemanticEffectKind,
        support_status: SemanticSupportStatus,
        *,
        ordinal: int,
        explanation: str,
        reads: list[str] | None = None,
        writes: list[str] | None = None,
        source_data_items: list[str] | None = None,
        target_data_items: list[str] | None = None,
        literal: str | None = None,
        expression: str | None = None,
        control_targets: list[str] | None = None,
        sql_operation: TableAccessOperation | None = None,
        sql_tables: list[str] | None = None,
        sql_host_variables: list[str] | None = None,
        condition_name: str | None = None,
        parent_data_item: str | None = None,
        condition_values: list[str] | None = None,
        diagnostic_codes: list[str] | None = None,
    ) -> SemanticEffect:
        return SemanticEffect(
            effect_id=_effect_id(
                program=self.program_name,
                paragraph=self.paragraph_name,
                statement_id=self.stmt.statement_id,
                kind=kind,
                ordinal=ordinal,
            ),
            kind=kind,
            support_status=support_status,
            source_reference=SemanticEffectSourceReference(
                program=self.program_name,
                paragraph=self.paragraph_name,
                statement_id=self.stmt.statement_id,
                statement_kind=self.stmt.kind,
                source_file=self.stmt.source_file,
                line_start=self.stmt.line_start,
                line_end=self.stmt.line_end,
                location_kind=self.stmt.location_kind,
                parent_statement_id=self.stmt.parent_statement_id,
                branch_kind=self.stmt.branch_kind,
            ),
            reads=reads or [],
            writes=writes or [],
            source_data_items=source_data_items or [],
            target_data_items=target_data_items or [],
            literal=literal,
            expression=expression,
            control_targets=control_targets or [],
            sql_operation=sql_operation,
            sql_tables=sql_tables or [],
            sql_host_variables=sql_host_variables or [],
            condition_name=condition_name,
            parent_data_item=parent_data_item,
            condition_values=condition_values or [],
            diagnostic_codes=diagnostic_codes or [],
            explanation=explanation,
        )


def _effect_id(
    *, program: str, paragraph: str, statement_id: str, kind: SemanticEffectKind, ordinal: int
) -> str:
    """Deterministico por construccion (Fase 2): concatenacion legible y
    estable de program/paragraph/statement_id/kind/ordinal -- nunca UUID
    aleatorio, nunca timestamp, nunca `hash()` de Python (no estable
    entre procesos)."""
    return f"effect::{program}::{paragraph}::{statement_id}::{kind.value}::{ordinal}"


# ---------------------------------------------------------------------------
# Normalizadores por StatementKind: fuente unica de verdad (Fase 3/4).
# ---------------------------------------------------------------------------


def _normalize_if_evaluate(ctx: _NormalizeContext) -> list[SemanticEffect]:
    # IF/EVALUATE nunca generan un efecto de negocio artificial (Fase 4):
    # sus sentencias hijas ya conservan parent_statement_id/branch_kind, y
    # el vinculo con la decision se preserva via source_reference de esas
    # hijas. Cero efectos aqui NO es un error.
    return []


def _normalize_move(ctx: _NormalizeContext) -> list[SemanticEffect]:
    stmt = ctx.stmt
    literal = stmt.assigned_literal
    targets = stmt.target_data_items
    sources = stmt.variables_read

    if literal is not None and len(targets) == 1:
        # A: literal directo con un unico destino.
        return [
            ctx.build(
                SemanticEffectKind.ASSIGN_LITERAL,
                SemanticSupportStatus.FULLY_SUPPORTED,
                ordinal=0,
                writes=[targets[0]],
                target_data_items=[targets[0]],
                literal=literal,
                explanation=(
                    "MOVE de un literal a un unico target_data_item resoluble: "
                    "asignacion directa."
                ),
            )
        ]

    if literal is not None and len(targets) > 1:
        # B: literal con multiples destinos -> un efecto por destino,
        # ordinal estable = indice en target_data_items (orden ya
        # preservado por el parser).
        return [
            ctx.build(
                SemanticEffectKind.ASSIGN_LITERAL,
                SemanticSupportStatus.PARTIALLY_SUPPORTED,
                ordinal=index,
                writes=[target],
                target_data_items=[target],
                literal=literal,
                diagnostic_codes=["MULTIPLE_TARGET_ASSIGNMENT"],
                explanation=(
                    "MOVE de un literal a multiples target_data_items: un efecto "
                    "ASSIGN_LITERAL por destino declarado."
                ),
            )
            for index, target in enumerate(targets)
        ]

    if literal is None and sources and targets:
        # C (una fuente, un destino) y D (multiples fuentes/destinos)
        # comparten tratamiento: COPY_VALUE, nunca se inventa
        # correspondencia campo a campo -- reads/writes y
        # source/target_data_items listan TODO lo declarado por el
        # parser, sin pares individuales.
        is_simple = len(sources) == 1 and len(targets) == 1
        return [
            ctx.build(
                SemanticEffectKind.COPY_VALUE,
                SemanticSupportStatus.PARTIALLY_SUPPORTED,
                ordinal=0,
                reads=sources,
                writes=targets,
                source_data_items=sources,
                target_data_items=targets,
                diagnostic_codes=[] if is_simple else ["AMBIGUOUS_MULTI_FIELD_COPY"],
                explanation=(
                    "MOVE de variable a variable: nombres conservados (sin propagacion "
                    "de valores ni evaluacion)."
                    if is_simple
                    else (
                        "MOVE con multiples fuentes y/o destinos: se listan todos los "
                        "nombres declarados, nunca se infiere una correspondencia "
                        "campo a campo entre grupos."
                    )
                ),
            )
        ]

    # E: MOVE CORRESPONDING, MOVE de grupo, o cualquier forma sin literal
    # directo ni fuente+destino simultaneamente resolubles (incluye el
    # caso limite de un target no vacio sin fuente conocida: nunca se
    # afirma un target en PRESERVED_STATEMENT, per contrato -- por eso
    # este caso nunca declara target_data_items).
    return [
        ctx.build(
            SemanticEffectKind.PRESERVED_STATEMENT,
            (
                SemanticSupportStatus.PRESERVED_ONLY
                if not (sources or targets)
                else SemanticSupportStatus.PARTIALLY_SUPPORTED
            ),
            ordinal=0,
            diagnostic_codes=["MOVE_GROUP_OR_CORRESPONDING_NOT_EXPANDED"],
            explanation=(
                "MOVE de grupo, MOVE CORRESPONDING, u otra forma sin destino/fuente "
                "directamente resolubles en un unico paso; nunca se expanden campos "
                "elementales ni se infiere correspondencia."
            ),
        )
    ]


def _normalize_set(ctx: _NormalizeContext) -> list[SemanticEffect]:
    stmt = ctx.stmt
    if stmt.condition_name_target is not None:
        return _normalize_set_condition(ctx)

    diagnostic_codes = ["SET_SEMANTIC_VARIANT_UNRESOLVED"]
    # Fase 6 (previa a la Fase 3 de soporte nivel 88): solo se agrega
    # LEVEL_88_VALUE_NOT_AVAILABLE si el target coincide por NOMBRE con
    # un CanonicalDataItem(level=88) real del programa Y el SET no pudo
    # resolverse estructuralmente contra condition_names (rama anterior).
    # Nunca se infiere VALUE/TRUE/FALSE/THRU/variable padre en este caso
    # -- solo se llega aqui cuando esa resolucion completa fallo (p. ej.
    # homonimo ambiguo entre distintos padres, ver
    # docs/LEVEL_88_SUPPORT.md).
    if ctx.is_level_88_target(stmt.target_data_items):
        diagnostic_codes.append("LEVEL_88_VALUE_NOT_AVAILABLE")
    return [
        ctx.build(
            SemanticEffectKind.SET_VALUE,
            SemanticSupportStatus.PARTIALLY_SUPPORTED,
            ordinal=0,
            reads=stmt.variables_read,
            writes=stmt.target_data_items,
            target_data_items=stmt.target_data_items,
            literal=stmt.assigned_literal,
            diagnostic_codes=diagnostic_codes,
            explanation=(
                "SET captura target_data_items/assigned_literal cuando estan presentes, "
                "pero no distingue estructuralmente SET ordinario de condicion-88 TO "
                "TRUE/FALSE ni de SET ... UP/DOWN BY."
            ),
        )
    ]


def _condition_value_text(value: CanonicalConditionValue) -> str:
    if value.through_value is None:
        return value.value
    return f"{value.value}..{value.through_value}"


def _normalize_set_condition(ctx: _NormalizeContext) -> list[SemanticEffect]:
    """`CanonicalStatement.condition_name_target`/`condition_set_value`
    ya fueron resueltos de forma estructural por el parser Java (target
    unico, valor literal TRUE/FALSE verificado, nombre unico contra
    `CanonicalProgram.condition_names`) -- ver docs/LEVEL_88_SUPPORT.md.
    Si por algun motivo el nombre no aparece en el registro de este
    analizador (artefacto desincronizado / editado a mano), no se
    inventa padre ni valor: se declara UNSUPPORTED_STATEMENT explicito."""
    stmt = ctx.stmt
    assert stmt.condition_name_target is not None
    condition = ctx.conditions_by_name.get(stmt.condition_name_target)
    kind = (
        SemanticEffectKind.SET_CONDITION_TRUE
        if stmt.condition_set_value
        else SemanticEffectKind.SET_CONDITION_FALSE
    )
    if condition is None:
        return [
            ctx.build(
                SemanticEffectKind.UNSUPPORTED_STATEMENT,
                SemanticSupportStatus.UNSUPPORTED,
                ordinal=0,
                diagnostic_codes=["CONDITION_NAME_TARGET_NOT_IN_REGISTRY"],
                explanation=(
                    f"SET declara condition_name_target={stmt.condition_name_target!r} pero "
                    "ese nombre no existe en CanonicalProgram.condition_names; no se infiere "
                    "padre ni valor."
                ),
            )
        ]

    condition_values = [_condition_value_text(value) for value in condition.values]
    diagnostic_codes: list[str] = []
    literal: str | None = None
    if len(condition.values) == 1 and condition.values[0].through_value is None:
        literal = condition.values[0].value
    else:
        diagnostic_codes.append("CONDITION_HAS_MULTIPLE_OR_RANGE_VALUES")

    verb = "TRUE" if stmt.condition_set_value else "FALSE"
    return [
        ctx.build(
            kind,
            SemanticSupportStatus.FULLY_SUPPORTED,
            ordinal=0,
            writes=[condition.parent_qualified_name],
            literal=literal,
            condition_name=condition.qualified_name,
            parent_data_item=condition.parent_qualified_name,
            condition_values=condition_values,
            diagnostic_codes=diagnostic_codes,
            explanation=(
                f"SET {condition.name} TO {verb}: condicion resuelta estructuralmente contra "
                "CanonicalProgram.condition_names (padre y VALUE conservados); el valor "
                "declarado nunca se propaga a sentencias posteriores."
            ),
        )
    ]


def _normalize_compute(ctx: _NormalizeContext) -> list[SemanticEffect]:
    stmt = ctx.stmt
    if stmt.expression is None or not stmt.target_data_items:
        return [
            ctx.build(
                SemanticEffectKind.PRESERVED_STATEMENT,
                SemanticSupportStatus.PRESERVED_ONLY,
                ordinal=0,
                diagnostic_codes=["COMPUTE_TARGET_OR_EXPRESSION_UNAVAILABLE"],
                explanation="COMPUTE sin expression y/o target_data_items capturados.",
            )
        ]
    return [
        ctx.build(
            SemanticEffectKind.COMPUTE_VALUE,
            SemanticSupportStatus.PARTIALLY_SUPPORTED,
            ordinal=0,
            reads=stmt.variables_read,
            writes=stmt.target_data_items,
            target_data_items=stmt.target_data_items,
            expression=stmt.expression,
            diagnostic_codes=["COMPUTE_EXPRESSION_NOT_EVALUATED"],
            explanation=(
                "La expresion se conserva como texto normalizado; no se evalua ni se "
                "propaga su resultado hacia los data items que dependen de ella."
            ),
        )
    ]


def _normalize_go_to(ctx: _NormalizeContext) -> list[SemanticEffect]:
    stmt = ctx.stmt
    if not stmt.target_paragraphs:
        return [
            ctx.build(
                SemanticEffectKind.PRESERVED_STATEMENT,
                SemanticSupportStatus.PRESERVED_ONLY,
                ordinal=0,
                diagnostic_codes=["CONTROL_TRANSFER_TARGET_UNAVAILABLE"],
                explanation="GO TO sin target_paragraphs capturado.",
            )
        ]
    return [
        ctx.build(
            SemanticEffectKind.CONTROL_TRANSFER,
            SemanticSupportStatus.FULLY_SUPPORTED,
            ordinal=0,
            control_targets=stmt.target_paragraphs,
            explanation="GO TO: destino(s) explicito(s) capturado(s) (simple o DEPENDING ON).",
        )
    ]


def _normalize_perform(ctx: _NormalizeContext) -> list[SemanticEffect]:
    stmt = ctx.stmt
    if not stmt.target_paragraphs:
        return [
            ctx.build(
                SemanticEffectKind.PRESERVED_STATEMENT,
                SemanticSupportStatus.PRESERVED_ONLY,
                ordinal=0,
                diagnostic_codes=["CONTROL_TRANSFER_TARGET_UNAVAILABLE"],
                explanation="PERFORM sin target_paragraphs capturado.",
            )
        ]
    return [
        ctx.build(
            SemanticEffectKind.CONTROL_TRANSFER,
            SemanticSupportStatus.PARTIALLY_SUPPORTED,
            ordinal=0,
            control_targets=stmt.target_paragraphs,
            diagnostic_codes=["PERFORM_LOOP_CONTROL_NOT_FULLY_REPRESENTED"],
            explanation=(
                "PERFORM (simple o THRU): target_paragraphs capturado, pero las clausulas "
                "UNTIL/VARYING y el CFG completo de iteracion pueden no estar representados."
            ),
        )
    ]


def _normalize_exec_sql(ctx: _NormalizeContext) -> list[SemanticEffect]:
    """`CanonicalSqlAccess.host_variables` es una unica lista plana que el
    extractor Java (`EmbeddedSqlExtractor.hostVariablesOf`) construye con
    un regex sobre TODO el texto crudo de la sentencia -- INTO, WHERE,
    VALUES y SET indistintamente, sin distinguir entrada de salida. Un
    `SELECT ... INTO :destino ... WHERE campo = :filtro` demuestra el
    caso: ambas variables terminan en la misma lista, aunque `:destino`
    es salida y `:filtro` es entrada (`EmbeddedSqlExtractorTest.
    selectWithHostVariablesAndPredicate`). `operation` (READS/WRITES/
    UPDATES/INSERTS) describe la operacion sobre la TABLA, nunca la
    direccion de cada variable individual -- nunca se usa para inferir
    reads/writes. Mientras no exista evidencia estructural inequivoca,
    `reads`/`writes` permanecen vacios y toda variable host se conserva
    unicamente en el campo neutral `sql_host_variables` (ordenado, sin
    duplicados) junto con `diagnostic_code=
    SQL_HOST_VARIABLE_DIRECTION_UNRESOLVED`."""
    stmt = ctx.stmt
    if not stmt.sql_access:
        return [
            ctx.build(
                SemanticEffectKind.PRESERVED_STATEMENT,
                SemanticSupportStatus.PRESERVED_ONLY,
                ordinal=0,
                diagnostic_codes=["EXEC_SQL_ACCESS_UNAVAILABLE"],
                explanation="EXEC SQL sin sql_access estructurado capturado.",
            )
        ]
    effects: list[SemanticEffect] = []
    for index, access in enumerate(stmt.sql_access):
        sql_host_variables = sorted(set(access.host_variables))
        diagnostic_codes = ["EXEC_SQL_BASIC_ACCESS_ONLY"]
        if sql_host_variables:
            diagnostic_codes.append("SQL_HOST_VARIABLE_DIRECTION_UNRESOLVED")
        effects.append(
            ctx.build(
                SemanticEffectKind.EXECUTE_SQL,
                SemanticSupportStatus.PARTIALLY_SUPPORTED,
                ordinal=index,
                sql_operation=access.operation,
                sql_tables=[access.table],
                sql_host_variables=sql_host_variables,
                diagnostic_codes=diagnostic_codes,
                explanation=(
                    "Operacion y tabla se conservan; sin JOIN, subconsulta, CTE, cursores "
                    "ni SQL dinamico. El predicado WHERE nunca se copia completo a este "
                    "artefacto. El parser actual conserva las variables host declaradas "
                    "pero no distingue contractualmente cuales son de entrada (WHERE/"
                    "VALUES/SET) y cuales de salida (INTO): se listan en "
                    "sql_host_variables sin asignarlas a reads ni writes."
                ),
            )
        )
    return effects


def _normalize_other(ctx: _NormalizeContext) -> list[SemanticEffect]:
    return [
        ctx.build(
            SemanticEffectKind.PRESERVED_STATEMENT,
            SemanticSupportStatus.PRESERVED_ONLY,
            ordinal=0,
            diagnostic_codes=["STATEMENT_TEXT_PRESERVED_WITHOUT_SEMANTIC_EFFECT"],
            explanation=(
                "La sentencia no coincide con ninguna de las 8 categorias interpretadas "
                "estructuralmente; se conserva sin efecto semantico normalizado (nunca "
                "source_text)."
            ),
        )
    ]


_STATEMENT_NORMALIZERS: dict[StatementKind, Callable[[_NormalizeContext], list[SemanticEffect]]] = {
    StatementKind.IF: _normalize_if_evaluate,
    StatementKind.EVALUATE: _normalize_if_evaluate,
    StatementKind.MOVE: _normalize_move,
    StatementKind.SET: _normalize_set,
    StatementKind.COMPUTE: _normalize_compute,
    StatementKind.GO_TO: _normalize_go_to,
    StatementKind.PERFORM: _normalize_perform,
    StatementKind.EXEC_SQL: _normalize_exec_sql,
    StatementKind.OTHER: _normalize_other,
}


def _normalize_statement(ctx: _NormalizeContext) -> list[SemanticEffect]:
    normalizer = _STATEMENT_NORMALIZERS.get(ctx.stmt.kind)
    if normalizer is None:
        # Defensivo, nunca alcanzable en la practica: StatementKind es un
        # enum cerrado y sus 9 valores estan cubiertos arriba.
        return [
            ctx.build(
                SemanticEffectKind.UNSUPPORTED_STATEMENT,
                SemanticSupportStatus.UNSUPPORTED,
                ordinal=0,
                diagnostic_codes=["UNKNOWN_STATEMENT_KIND"],
                explanation=(
                    f"StatementKind {ctx.stmt.kind.value!r} no tiene normalizador "
                    "registrado en semantic_effects_analyzer."
                ),
            )
        ]
    return normalizer(ctx)


# ---------------------------------------------------------------------------
# unsupported_constructs -> UNSUPPORTED_STATEMENT (una entrada agregada por
# construccion declarada, mismo mecanismo de extraccion que
# semantic_coverage_analyzer.py).
# ---------------------------------------------------------------------------


def _parse_unsupported_construct_message(message: str) -> tuple[str, str | None]:
    if _UNSUPPORTED_CONSTRUCT_SEPARATOR not in message:
        return message.strip()[:80], None
    construct_part, remainder = message.split(_UNSUPPORTED_CONSTRUCT_SEPARATOR, 1)
    paragraph_token = remainder.split(" ", 1)[0].strip()
    return construct_part.strip() or message.strip()[:80], paragraph_token or None


def _unsupported_construct_effects(
    program_name: str, unsupported_constructs: list[str]
) -> list[SemanticEffect]:
    effects: list[SemanticEffect] = []
    for index, message in enumerate(unsupported_constructs):
        construct_name, paragraph_name = _parse_unsupported_construct_message(message)
        paragraph = paragraph_name or _UNKNOWN_PARAGRAPH_PLACEHOLDER
        # Identificador sintetico, deterministico y honesto (nunca
        # pretende ser un statement_id real emitido por el parser): el
        # mensaje de unsupported_constructs no trae uno.
        synthetic_statement_id = (
            f"{program_name}::{paragraph}::unsupported::{index}::{construct_name}"
        )
        effects.append(
            SemanticEffect(
                effect_id=_effect_id(
                    program=program_name,
                    paragraph=paragraph,
                    statement_id=synthetic_statement_id,
                    kind=SemanticEffectKind.UNSUPPORTED_STATEMENT,
                    ordinal=index,
                ),
                kind=SemanticEffectKind.UNSUPPORTED_STATEMENT,
                support_status=SemanticSupportStatus.UNSUPPORTED,
                source_reference=SemanticEffectSourceReference(
                    program=program_name,
                    paragraph=paragraph,
                    statement_id=synthetic_statement_id,
                    statement_kind=StatementKind.OTHER,
                    location_kind=LocationKind.UNKNOWN,
                ),
                diagnostic_codes=["DECLARED_UNSUPPORTED_BY_PRODUCER"],
                explanation=(
                    "El parser/adaptador declaro explicitamente esta construccion como no "
                    "decodificada estructuralmente (CanonicalProgram.unsupported_constructs)."
                ),
            )
        )
    return effects


# ---------------------------------------------------------------------------
# Nivel 88 (Fase 6): nombres conocidos, nunca inferidos.
# ---------------------------------------------------------------------------


def _level_88_names(data_items: list[CanonicalDataItem]) -> frozenset[str]:
    names: set[str] = set()
    for item in data_items:
        if item.level == 88:
            names.add(item.name)
            names.add(item.qualified_name)
    return frozenset(names)


def _conditions_by_name(
    condition_names: list[CanonicalConditionName],
) -> dict[str, CanonicalConditionName]:
    """Indexado por nombre simple: `CanonicalStatement.condition_name_target`
    ya fue resuelto por el parser Java contra nombres unicos (homonimos
    entre distintos padres quedan excluidos alli, nunca llegan a poblar
    `condition_name_target`), asi que esta busqueda es segura sin
    necesidad de repetir esa deduplicacion aqui."""
    return {condition.name: condition for condition in condition_names}


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------


def _analyze_program(program: CanonicalProgram) -> ProgramSemanticEffects:
    level_88_names = _level_88_names(program.data_items)
    conditions_by_name = _conditions_by_name(program.condition_names)
    effects: list[SemanticEffect] = []

    for paragraph in program.paragraphs:
        for stmt in paragraph.statements:
            ctx = _NormalizeContext(
                program_name=program.program_name,
                paragraph_name=paragraph.name,
                stmt=stmt,
                level_88_names=level_88_names,
                conditions_by_name=conditions_by_name,
            )
            effects.extend(_normalize_statement(ctx))

    effects.extend(
        _unsupported_construct_effects(program.program_name, program.unsupported_constructs)
    )

    effects.sort(key=lambda effect: effect.effect_id)
    return ProgramSemanticEffects(
        program=program.program_name, effect_count=len(effects), effects=effects
    )


def _build_summary(programs: Sequence[ProgramSemanticEffects]) -> SemanticEffectsSummary:
    counts_by_kind: dict[SemanticEffectKind, int] = {}
    counts_by_status: dict[SemanticSupportStatus, int] = {}
    for program in programs:
        for effect in program.effects:
            counts_by_kind[effect.kind] = counts_by_kind.get(effect.kind, 0) + 1
            counts_by_status[effect.support_status] = (
                counts_by_status.get(effect.support_status, 0) + 1
            )

    return SemanticEffectsSummary(
        program_count=len(programs),
        effect_count=sum(program.effect_count for program in programs),
        counts_by_kind=counts_by_kind,
        counts_by_support_status=counts_by_status,
    )


def analyze_semantic_effects(
    *,
    canonical_programs: Sequence[CanonicalProgram],
    run_id: str,
    source_package_hash: str,
    source_artifact_hashes: dict[str, str],
) -> SemanticEffectsArtifact:
    """Punto de entrada del analizador puro (Fase 3-6). Determinista:
    misma entrada siempre produce el mismo `SemanticEffectsArtifact`
    (orden de `programs`/`effects` siempre normalizado antes de construir
    el modelo, nunca dependiente del orden de `canonical_programs`
    recibido). Nunca propaga valores entre sentencias ni entre efectos."""
    programs = sorted(
        (_analyze_program(program) for program in canonical_programs),
        key=lambda coverage: coverage.program,
    )
    summary = _build_summary(programs)
    return SemanticEffectsArtifact(
        run_id=run_id,
        source_package_hash=source_package_hash,
        source_artifact_hashes=dict(source_artifact_hashes),
        summary=summary,
        programs=programs,
    )
