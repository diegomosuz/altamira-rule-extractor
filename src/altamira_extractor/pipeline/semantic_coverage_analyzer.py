"""Analizador PURO de cobertura semantica (Fase 1 de la ampliacion
semantica, checkpoint `feat/semantic-expansion-foundation`).

Recibe unicamente objetos ya cargados en memoria (`CanonicalProgram[]`,
`DependencyArtifact`, `SemanticGraph`, `CandidateArtifact`) y devuelve un
`SemanticCoverageReport`. Nunca:

- accede a Neo4j (lee `SemanticGraph` ya materializado, no ejecuta Cypher);
- lee variables de entorno ni `Settings`;
- escribe archivos (la persistencia es responsabilidad exclusiva de
  `semantic_coverage_service.py`);
- modifica los objetos de entrada;
- depende del orden de lectura del filesystem (el orden final de
  `programs`/`construct_coverage`/`diagnostics` lo imponen los propios
  validadores de `contracts/semantic_coverage.py`, no el orden de
  iteracion aqui);
- invoca un modelo LLM ni usa heuristicas de lenguaje natural.

Toda clasificacion pasa por UNA tabla-registro (`_STATEMENT_CLASSIFIERS`):
nunca se dispersa la logica en `if`/`elif` sueltos fuera de ese registro.
No reejecuta ni reimplementa `CandidateDetector`/Q0 (lee el
`CandidateArtifact` ya persistido); no ejecuta ninguna query Cypher (lee
`SemanticGraph` ya materializado en memoria, indexado localmente)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from ..contracts.candidate import CandidateArtifact
from ..contracts.canonical import CanonicalProgram, CanonicalStatement
from ..contracts.dependencies import DependencyArtifact
from ..contracts.enums import NodeLabel, RelationshipType, StatementKind
from ..contracts.semantic_coverage import (
    MAX_SOURCE_REFERENCES_PER_CONSTRUCT,
    CandidateImpact,
    ConstructCoverage,
    ProgramSemanticCoverage,
    SemanticCoverageReport,
    SemanticCoverageSourceReference,
    SemanticCoverageSummary,
    SemanticSupportStatus,
    ZeroCandidateReason,
)
from ..contracts.semantic_graph import GraphNode, SemanticGraph

# ---------------------------------------------------------------------------
# Clasificacion de StatementKind: fuente unica de verdad (Fase 3).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Classification:
    status: SemanticSupportStatus
    diagnostic_code: str
    explanation: str
    candidate_impact: CandidateImpact


def _classify_if(stmt: CanonicalStatement) -> _Classification:
    return _Classification(
        SemanticSupportStatus.FULLY_SUPPORTED,
        "DECISION_STRUCTURALLY_CAPTURED",
        "Cobertura estructural de la decision (IF): ramas THEN/ELSE, operandos y "
        "expresion se conservan. La resolucion del efecto (si conduce a un "
        "assigned_literal resoluble via LEADS_TO) se mide por separado en "
        "decisions_with_resolved_effect_count / decisions_without_resolved_effect_count.",
        CandidateImpact.NONE,
    )


def _classify_evaluate(stmt: CanonicalStatement) -> _Classification:
    return _Classification(
        SemanticSupportStatus.FULLY_SUPPORTED,
        "DECISION_STRUCTURALLY_CAPTURED",
        "Cobertura estructural de la decision (EVALUATE): ramas WHEN/WHEN OTHER, "
        "operandos y expresion (solo el sujeto del Select) se conservan. La "
        "resolucion del efecto se mide por separado en decisions_with_resolved_"
        "effect_count / decisions_without_resolved_effect_count.",
        CandidateImpact.NONE,
    )


def _classify_go_to(stmt: CanonicalStatement) -> _Classification:
    return _Classification(
        SemanticSupportStatus.FULLY_SUPPORTED,
        "CONTROL_TARGET_CAPTURED",
        "target_paragraphs explicito (simple o DEPENDING ON) capturado como "
        "CONTROL_DEPENDS_ON.",
        CandidateImpact.NONE,
    )


def _classify_move(stmt: CanonicalStatement) -> _Classification:
    has_literal = stmt.assigned_literal is not None
    target_count = len(stmt.target_data_items)

    if has_literal and target_count == 1:
        return _Classification(
            SemanticSupportStatus.FULLY_SUPPORTED,
            "MOVE_LITERAL_DIRECT",
            "MOVE de un literal a un unico target_data_item resoluble: "
            "assigned_literal y el destino quedan disponibles para "
            "SemanticGraphBuilder (LEADS_TO).",
            CandidateImpact.NONE,
        )
    if has_literal and target_count > 1:
        return _Classification(
            SemanticSupportStatus.PARTIALLY_SUPPORTED,
            "MOVE_LITERAL_MULTIPLE_TARGETS",
            "MOVE de un literal a multiples target_data_items: el literal se "
            "conserva, pero SemanticGraphBuilder solo genera LEADS_TO hacia el "
            "hijo directo de la Decision, no necesariamente hacia todos los "
            "destinos declarados.",
            CandidateImpact.MEDIUM,
        )
    if not has_literal and stmt.variables_read and target_count >= 1:
        return _Classification(
            SemanticSupportStatus.PARTIALLY_SUPPORTED,
            "MOVE_VARIABLE_TO_VARIABLE",
            "MOVE de variable a variable: variables_read/target_data_items se "
            "conservan, pero no existe propagacion de constantes ni de copias "
            "-- no puede determinarse el valor final del destino a partir de "
            "una asignacion literal anterior (ver dependency_builder.py, que "
            "nunca lee assigned_literal).",
            CandidateImpact.MEDIUM,
        )
    return _Classification(
        SemanticSupportStatus.PARTIALLY_SUPPORTED,
        "MOVE_WITHOUT_DIRECT_LITERAL_EFFECT",
        "MOVE estructurado (de grupo, MOVE CORRESPONDING u otra forma) sin un "
        "target_data_item y assigned_literal directamente resolubles en un "
        "unico paso.",
        CandidateImpact.LOW,
    )


def _classify_set(stmt: CanonicalStatement) -> _Classification:
    if stmt.condition_name_target is not None:
        verb = "TRUE" if stmt.condition_set_value else "FALSE"
        return _Classification(
            SemanticSupportStatus.FULLY_SUPPORTED,
            "SET_CONDITION_RESOLVED",
            f"SET condicion-88 TO {verb} resuelto estructuralmente contra "
            "CanonicalProgram.condition_names (condition_name_target/condition_set_value); "
            "ver docs/LEVEL_88_SUPPORT.md.",
            CandidateImpact.NONE,
        )
    return _Classification(
        SemanticSupportStatus.PARTIALLY_SUPPORTED,
        "SET_TARGET_KIND_AMBIGUOUS",
        "SET captura target_data_items/assigned_literal, pero no distingue "
        "estructuralmente entre SET ordinario, SET condicion-88 TO TRUE/FALSE no resuelto "
        "(p. ej. nombre ambiguo entre padres distintos) ni SET ... UP/DOWN BY.",
        CandidateImpact.MEDIUM,
    )


def _classify_compute(stmt: CanonicalStatement) -> _Classification:
    return _Classification(
        SemanticSupportStatus.PARTIALLY_SUPPORTED,
        "COMPUTE_EXPRESSION_PRESERVED_NOT_EVALUATED",
        "La expresion (expression/normalized_expression) se conserva como "
        "texto, pero no existe evaluacion ni propagacion de su resultado hacia "
        "los data items que dependen de el.",
        CandidateImpact.LOW,
    )


def _classify_perform(stmt: CanonicalStatement) -> _Classification:
    return _Classification(
        SemanticSupportStatus.PARTIALLY_SUPPORTED,
        "PERFORM_CONTROL_FLOW_PARTIAL",
        "PERFORM (simple o THRU) captura sus target_paragraphs, pero las "
        "clausulas UNTIL/VARYING no conservan su condicion de control (se "
        "descartan sin advertencia estructurada) y no existe un CFG completo "
        "de iteracion.",
        CandidateImpact.LOW,
    )


def _classify_exec_sql(stmt: CanonicalStatement) -> _Classification:
    return _Classification(
        SemanticSupportStatus.PARTIALLY_SUPPORTED,
        "EXEC_SQL_BASIC_ACCESS_ONLY",
        "Operacion, tabla(s), variables host y un predicado WHERE simple "
        "pueden conservarse (sql_access), pero no existe un parser SQL "
        "general: JOIN, subconsultas, CTE, UNION, cursores y SQL dinamico no "
        "estan cubiertos.",
        CandidateImpact.LOW,
    )


_STATEMENT_CLASSIFIERS: dict[StatementKind, Callable[[CanonicalStatement], _Classification]] = {
    StatementKind.IF: _classify_if,
    StatementKind.EVALUATE: _classify_evaluate,
    StatementKind.GO_TO: _classify_go_to,
    StatementKind.MOVE: _classify_move,
    StatementKind.SET: _classify_set,
    StatementKind.COMPUTE: _classify_compute,
    StatementKind.PERFORM: _classify_perform,
    StatementKind.EXEC_SQL: _classify_exec_sql,
}

_OTHER_CLASSIFICATION = _Classification(
    SemanticSupportStatus.PRESERVED_ONLY,
    "STATEMENT_KIND_OTHER_TEXT_PRESERVED",
    "La sentencia no coincide con ninguna de las 8 categorias que el parser "
    "interpreta estructuralmente (IF/EVALUATE/MOVE/SET/COMPUTE/GO_TO/PERFORM/"
    "EXEC_SQL). Se conserva el texto fuente, sin variables_read/written, "
    "target_data_items ni assigned_literal poblados.",
    CandidateImpact.UNKNOWN,
)

_UNSUPPORTED_CLASSIFICATION_EXPLANATION = (
    "El parser/adaptador declaro explicitamente esta construccion como no "
    "decodificada estructuralmente (CanonicalProgram.unsupported_constructs). "
    "A diferencia de PRESERVED_ONLY, esta es una declaracion explicita del "
    "propio productor del artefacto, no una inferencia de este analizador."
)

LEVEL_88_DIAGNOSTIC_CODE = "LEVEL_88_SEMANTICS_NOT_MODELED"
LEVEL_88_MODELED_DIAGNOSTIC_CODE = "LEVEL_88_CONDITION_FULLY_MODELED"

_CONDITION_REFERENCE_CLASSIFICATION = _Classification(
    SemanticSupportStatus.FULLY_SUPPORTED,
    "CONDITION_REFERENCE_VERIFIED",
    "Referencia directa a un condition-name nivel 88 verificada contra "
    "CanonicalProgram.condition_names (CanonicalStatement.referenced_condition_names); "
    "nunca se infiere que toda variable leida en un IF/EVALUATE sea una condicion 88. "
    "Dimension independiente del conteo primario por StatementKind (una misma sentencia "
    "IF/EVALUATE ya se cuenta una vez alli).",
    CandidateImpact.NONE,
)


def _classify_statement(stmt: CanonicalStatement) -> _Classification:
    if stmt.kind == StatementKind.OTHER:
        return _OTHER_CLASSIFICATION
    classifier = _STATEMENT_CLASSIFIERS.get(stmt.kind)
    if classifier is None:
        # Defensivo, nunca alcanzable en la practica: StatementKind es un
        # enum cerrado y sus 9 valores (los 8 clasificados + OTHER) estan
        # cubiertos arriba. Si un valor nuevo se agrega al enum sin
        # registrar su clasificador aqui, se reporta como UNSUPPORTED en
        # vez de fallar silenciosamente o inventar soporte.
        return _Classification(
            SemanticSupportStatus.UNSUPPORTED,
            "UNKNOWN_STATEMENT_KIND",
            f"StatementKind {stmt.kind.value!r} no tiene clasificador registrado "
            "en semantic_coverage_analyzer.",
            CandidateImpact.UNKNOWN,
        )
    return classifier(stmt)


# ---------------------------------------------------------------------------
# Extraccion de identidad de constructo desde unsupported_constructs (regla 10).
# ---------------------------------------------------------------------------

_UNSUPPORTED_CONSTRUCT_SEPARATOR = " en paragraph "


def _parse_unsupported_construct_message(message: str) -> tuple[str, str | None]:
    """Extrae (construct_name, paragraph_name) del mensaje generado por el
    parser Java (`ExtractionContext.unsupported`, formato real: "<Clase>
    en paragraph <p> no decodificado estructuralmente (...)"). Nunca
    asume ese formato de forma estricta ni lanza excepcion: si el
    separador esperado no aparece, usa el mensaje completo (recortado)
    como identidad y `paragraph_name=None` -- un mensaje con formato
    inesperado sigue siendo reportable, solo con menor granularidad."""
    if _UNSUPPORTED_CONSTRUCT_SEPARATOR not in message:
        return message.strip()[:80], None
    construct_part, remainder = message.split(_UNSUPPORTED_CONSTRUCT_SEPARATOR, 1)
    paragraph_token = remainder.split(" ", 1)[0].strip()
    return construct_part.strip() or message.strip()[:80], paragraph_token or None


# ---------------------------------------------------------------------------
# Indice local del SemanticGraph (nunca Neo4j, nunca Cypher).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _GraphIndex:
    node_by_id: dict[str, GraphNode]
    children_by_relationship: dict[tuple[str, RelationshipType], list[str]]


def _build_graph_index(graph: SemanticGraph) -> _GraphIndex:
    node_by_id = {node.id: node for node in graph.nodes}
    children: dict[tuple[str, RelationshipType], list[str]] = {}
    for relationship in graph.relationships:
        key = (relationship.from_id, relationship.type)
        children.setdefault(key, []).append(relationship.to_id)
    return _GraphIndex(node_by_id=node_by_id, children_by_relationship=children)


def _find_program_node_id(index: _GraphIndex, program_name: str) -> str | None:
    for node in index.node_by_id.values():
        if NodeLabel.PROGRAM in node.labels and node.properties.get("name") == program_name:
            return node.id
    return None


def _paragraph_ids_for_program(index: _GraphIndex, program_node_id: str) -> list[str]:
    return index.children_by_relationship.get((program_node_id, RelationshipType.CONTAINS), [])


def _decision_ids_for_paragraphs(index: _GraphIndex, paragraph_ids: Sequence[str]) -> list[str]:
    decision_ids: list[str] = []
    for paragraph_id in paragraph_ids:
        decision_ids.extend(
            index.children_by_relationship.get((paragraph_id, RelationshipType.HAS_DECISION), [])
        )
    return decision_ids


def _has_leads_to(index: _GraphIndex, decision_id: str) -> bool:
    return bool(index.children_by_relationship.get((decision_id, RelationshipType.LEADS_TO)))


def _zero_candidate_reason(
    *, candidate_count: int, decision_count: int, decisions_with_resolved_effect_count: int
) -> ZeroCandidateReason:
    if candidate_count > 0:
        return ZeroCandidateReason.CANDIDATES_PRESENT
    if decision_count == 0:
        return ZeroCandidateReason.NO_DECISIONS
    if decisions_with_resolved_effect_count == 0:
        return ZeroCandidateReason.DECISIONS_WITHOUT_RESOLVED_EFFECTS
    if decisions_with_resolved_effect_count > 0:
        return ZeroCandidateReason.RESOLVED_EFFECTS_WITHOUT_Q0_MATCH
    # Defensivo: con enteros no negativos y decisions_with_resolved_effect_count
    # <= decision_count, las cuatro ramas anteriores son exhaustivas. Esta
    # rama nunca deberia alcanzarse; si ocurre, se reporta conservadoramente
    # en vez de adivinar.
    return ZeroCandidateReason.INSUFFICIENT_DIAGNOSTIC_DATA


# ---------------------------------------------------------------------------
# Acumulacion de ConstructCoverage (agrupa por construct_name/status/diagnostic_code).
# ---------------------------------------------------------------------------


@dataclass
class _CoverageAccumulator:
    classification: _Classification
    occurrence_count: int = 0
    references: list[SemanticCoverageSourceReference] = field(default_factory=list)

    def add(self, reference: SemanticCoverageSourceReference | None) -> None:
        self.occurrence_count += 1
        if reference is not None and len(self.references) < MAX_SOURCE_REFERENCES_PER_CONSTRUCT:
            self.references.append(reference)


def _statement_reference(
    program_name: str, paragraph_name: str, stmt: CanonicalStatement
) -> SemanticCoverageSourceReference:
    return SemanticCoverageSourceReference(
        program=program_name,
        paragraph=paragraph_name,
        statement_id=stmt.statement_id,
        source_file=stmt.source_file,
        line=stmt.line_start,
        location_kind=stmt.location_kind,
    )


def _analyze_program(
    program: CanonicalProgram,
    *,
    dependency_artifact: DependencyArtifact,
    graph_index: _GraphIndex,
    candidate_artifact: CandidateArtifact,
) -> ProgramSemanticCoverage:
    statement_count = 0
    counts_by_status: dict[SemanticSupportStatus, int] = {
        SemanticSupportStatus.FULLY_SUPPORTED: 0,
        SemanticSupportStatus.PARTIALLY_SUPPORTED: 0,
        SemanticSupportStatus.PRESERVED_ONLY: 0,
    }
    counts_by_kind: dict[StatementKind, int] = {}
    accumulators: dict[tuple[str, SemanticSupportStatus, str], _CoverageAccumulator] = {}

    def _accumulate(
        construct_name: str,
        classification: _Classification,
        reference: SemanticCoverageSourceReference | None,
    ) -> None:
        key = (construct_name, classification.status, classification.diagnostic_code)
        accumulator = accumulators.get(key)
        if accumulator is None:
            accumulator = _CoverageAccumulator(classification=classification)
            accumulators[key] = accumulator
        accumulator.add(reference)

    for paragraph in program.paragraphs:
        for stmt in paragraph.statements:
            statement_count += 1
            counts_by_kind[stmt.kind] = counts_by_kind.get(stmt.kind, 0) + 1
            classification = _classify_statement(stmt)
            counts_by_status[classification.status] = (
                counts_by_status.get(classification.status, 0) + 1
            )
            statement_reference = _statement_reference(program.program_name, paragraph.name, stmt)
            _accumulate(stmt.kind.value, classification, statement_reference)
            if stmt.referenced_condition_names:
                _accumulate(
                    "CONDITION_NAME_REFERENCE_RESOLVED",
                    _CONDITION_REFERENCE_CLASSIFICATION,
                    statement_reference,
                )

    for message in program.unsupported_constructs:
        construct_name, paragraph_name = _parse_unsupported_construct_message(message)
        classification = _Classification(
            SemanticSupportStatus.UNSUPPORTED,
            "DECLARED_UNSUPPORTED_BY_PRODUCER",
            _UNSUPPORTED_CLASSIFICATION_EXPLANATION,
            CandidateImpact.UNKNOWN,
        )
        unsupported_reference: SemanticCoverageSourceReference | None = (
            SemanticCoverageSourceReference(program=program.program_name, paragraph=paragraph_name)
            if paragraph_name is not None
            else None
        )
        _accumulate(construct_name, classification, unsupported_reference)

    level_88_items = [item for item in program.data_items if item.level == 88]
    modeled_qualified_names = {condition.qualified_name for condition in program.condition_names}
    if level_88_items:
        modeled_classification = _Classification(
            SemanticSupportStatus.FULLY_SUPPORTED,
            LEVEL_88_MODELED_DIAGNOSTIC_CODE,
            "Condicion nivel 88 totalmente modelada: nombre, padre (parent_name/"
            "parent_qualified_name) y al menos un VALUE (incluidos multiples VALUE y "
            "rangos THRU) se conservan en CanonicalProgram.condition_names. "
            "Ver docs/LEVEL_88_SUPPORT.md.",
            CandidateImpact.NONE,
        )
        not_modeled_classification = _Classification(
            SemanticSupportStatus.PARTIALLY_SUPPORTED,
            LEVEL_88_DIAGNOSTIC_CODE,
            "Condicion nivel 88 preservada como CanonicalDataItem(level=88), pero el "
            "parser no pudo demostrar su padre y/o al menos un VALUE (ver "
            "CanonicalProgram.unsupported_constructs para el motivo puntual); no se "
            "modela en condition_names ni se infiere padre o valor. Ver "
            "docs/LEVEL_88_SUPPORT.md.",
            CandidateImpact.MEDIUM,
        )
        for item in level_88_items:
            level_88_reference = SemanticCoverageSourceReference(
                program=program.program_name,
                source_file=item.source_file,
                line=item.line,
                location_kind=item.location_kind,
            )
            if item.qualified_name in modeled_qualified_names:
                _accumulate(
                    "LEVEL_88_CONDITION_NAME_MODELED", modeled_classification, level_88_reference
                )
            else:
                _accumulate(
                    "LEVEL_88_CONDITION_NAME", not_modeled_classification, level_88_reference
                )

    construct_coverage = [
        ConstructCoverage(
            construct_name=construct_name,
            support_status=accumulator.classification.status,
            occurrence_count=accumulator.occurrence_count,
            diagnostic_code=accumulator.classification.diagnostic_code,
            explanation=accumulator.classification.explanation,
            candidate_impact=accumulator.classification.candidate_impact,
            source_references=accumulator.references,
        )
        for (construct_name, _status, _code), accumulator in accumulators.items()
    ]

    program_node_id = _find_program_node_id(graph_index, program.program_name)
    diagnostics: list[str] = []
    if program_node_id is None:
        decision_count = 0
        decisions_with_resolved_effect_count = 0
        candidate_count = 0
        zero_reason = ZeroCandidateReason.INSUFFICIENT_DIAGNOSTIC_DATA
        diagnostics.append(
            "graph_program_node_not_found: no se encontro un nodo Program con "
            f"name={program.program_name!r} en SemanticGraph"
        )
    else:
        paragraph_ids = _paragraph_ids_for_program(graph_index, program_node_id)
        paragraph_id_set = set(paragraph_ids)
        decision_ids = _decision_ids_for_paragraphs(graph_index, paragraph_ids)
        decision_count = len(decision_ids)
        decisions_with_resolved_effect_count = sum(
            1 for decision_id in decision_ids if _has_leads_to(graph_index, decision_id)
        )
        candidate_count = sum(
            1
            for candidate in candidate_artifact.candidates
            if candidate.paragraph_id in paragraph_id_set
        )
        zero_reason = _zero_candidate_reason(
            candidate_count=candidate_count,
            decision_count=decision_count,
            decisions_with_resolved_effect_count=decisions_with_resolved_effect_count,
        )
        dependency_edge_count = sum(
            1
            for dependency in dependency_artifact.dependencies
            if dependency.from_paragraph_id in paragraph_id_set
            or dependency.to_paragraph_id in paragraph_id_set
        )
        diagnostics.append(f"dependency_edges_touching_program={dependency_edge_count}")

    decisions_without_resolved_effect_count = decision_count - decisions_with_resolved_effect_count
    unsupported_construct_count = len(program.unsupported_constructs)

    return ProgramSemanticCoverage(
        program=program.program_name,
        statement_count=statement_count,
        fully_supported_count=counts_by_status[SemanticSupportStatus.FULLY_SUPPORTED],
        partially_supported_count=counts_by_status[SemanticSupportStatus.PARTIALLY_SUPPORTED],
        preserved_only_count=counts_by_status[SemanticSupportStatus.PRESERVED_ONLY],
        unsupported_count=unsupported_construct_count,
        statement_counts_by_kind=counts_by_kind,
        decision_count=decision_count,
        decisions_with_resolved_effect_count=decisions_with_resolved_effect_count,
        decisions_without_resolved_effect_count=decisions_without_resolved_effect_count,
        candidate_count=candidate_count,
        zero_candidate_reason=zero_reason,
        level_88_data_item_count=len(level_88_items),
        unsupported_construct_count=unsupported_construct_count,
        construct_coverage=sorted(
            construct_coverage,
            key=lambda entry: (
                entry.construct_name,
                entry.support_status.value,
                entry.diagnostic_code,
            ),
        ),
        diagnostics=sorted(set(diagnostics)),
    )


def _build_summary(programs: Sequence[ProgramSemanticCoverage]) -> SemanticCoverageSummary:
    statement_counts_by_kind: dict[StatementKind, int] = {}
    for program in programs:
        for kind, count in program.statement_counts_by_kind.items():
            statement_counts_by_kind[kind] = statement_counts_by_kind.get(kind, 0) + count

    def _sum(attr: str) -> int:
        return sum(getattr(program, attr) for program in programs)

    return SemanticCoverageSummary(
        program_count=len(programs),
        statement_count=_sum("statement_count"),
        fully_supported_count=_sum("fully_supported_count"),
        partially_supported_count=_sum("partially_supported_count"),
        preserved_only_count=_sum("preserved_only_count"),
        unsupported_count=_sum("unsupported_count"),
        statement_counts_by_kind=statement_counts_by_kind,
        decision_count=_sum("decision_count"),
        decisions_with_resolved_effect_count=_sum("decisions_with_resolved_effect_count"),
        decisions_without_resolved_effect_count=_sum("decisions_without_resolved_effect_count"),
        candidate_count=_sum("candidate_count"),
        level_88_data_item_count=_sum("level_88_data_item_count"),
        unsupported_construct_count=_sum("unsupported_construct_count"),
    )


def analyze_semantic_coverage(
    *,
    canonical_programs: Sequence[CanonicalProgram],
    dependency_artifact: DependencyArtifact,
    semantic_graph: SemanticGraph,
    candidate_artifact: CandidateArtifact,
    run_id: str,
    source_package_hash: str,
    source_artifact_hashes: Mapping[str, str],
) -> SemanticCoverageReport:
    """Punto de entrada del analizador puro (Fase 3-5). Determinista:
    misma entrada siempre produce el mismo `SemanticCoverageReport`
    (orden de `programs`/`construct_coverage`/`diagnostics` siempre
    normalizado antes de construir el modelo, nunca dependiente del
    orden de `canonical_programs` recibido)."""
    graph_index = _build_graph_index(semantic_graph)

    programs = sorted(
        (
            _analyze_program(
                program,
                dependency_artifact=dependency_artifact,
                graph_index=graph_index,
                candidate_artifact=candidate_artifact,
            )
            for program in canonical_programs
        ),
        key=lambda coverage: coverage.program,
    )

    summary = _build_summary(programs)

    return SemanticCoverageReport(
        run_id=run_id,
        source_package_hash=source_package_hash,
        source_artifact_hashes=dict(source_artifact_hashes),
        summary=summary,
        programs=programs,
    )
