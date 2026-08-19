"""SemanticGraphBuilder (Prompt 8): traduce, de forma pura y sin I/O,
`CanonicalProgram` + `DependencyArtifact` + `SemanticEnrichmentArtifact` +
`Manifest` (embebido en Inventory) al metamodelo documentado en
docs/NEO4J_METAMODEL.md.

No vuelve a parsear COBOL, no invoca el JAR, no vuelve a leer DDL/CSV, no
vuelve a ejecutar SemanticTagger/DomainTermMapper y no rederiva
dependencias: unicamente traduce hechos ya persistidos y ya validados por
etapas anteriores. Los statements se conservan como evidencia serializada
(`*_json`) en properties de nodos/relaciones, nunca como nodos propios; no
se crea ningun nodo `SqlStatement`.

Cualquier referencia (data_item_id de un tag, data_item_id/domain_term_id
de un mapping, from/to_paragraph_id de una dependencia) que no resuelva
contra el universo de Program/Paragraph/DataItem reconstruido desde los
`CanonicalProgram` actuales es un error fatal (`SemanticGraphBuildError`):
indica que los artefactos de Prompt 6/7 quedaron desincronizados del
canonico actual, no una ambiguedad legitima.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..contracts.canonical import CanonicalProgram, CanonicalStatement
from ..contracts.dependencies import DependencyArtifact
from ..contracts.enums import (
    DependencyType,
    NodeLabel,
    RelationshipType,
)
from ..contracts.inventory import Inventory
from ..contracts.semantic_enrichment import (
    DataItemSemanticTag,
    ParameterTableRecord,
    SemanticEnrichmentArtifact,
)
from ..contracts.semantic_graph import GraphNode, GraphRelationship, SemanticGraph
from .dependency_builder import (
    VariableResolutionFailure,
    _build_symbol_index,
    resolve_variable,
)
from .dependency_builder import _SymbolIndex as SymbolIndex
from .errors import SemanticGraphBuildError
from .identifiers import (
    DECISION_STATEMENT_KINDS,
    ProgramIdentity,
    data_item_id,
    decision_id_for,
    decision_statements_in_order,
    normalize_identifier,
    paragraph_id,
)
from .identifiers import table_id as compute_table_id


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# --- Identificador de tabla SQL: normalizador acotado, no un parser SQL ---


def _split_sql_table_identifier(token: str) -> tuple[str | None, str]:
    """Separa un identificador de tabla ya extraido (`CanonicalSqlAccess.table`
    o `ManifestParameterTable.name`) en `(schema_o_None, nombre_tabla)`,
    respetando identificadores entre comillas dobles (un `.` dentro de
    comillas no es separador) y `""` como comilla escapada. Reconoce
    exactamente `schema.table`, `"identificador"."otro"` y nombres sin
    calificar — no es un parser SQL general."""
    parts: list[str] = []
    buffer: list[str] = []
    i = 0
    n = len(token)
    while i < n:
        char = token[i]
        if char == '"':
            buffer.append(char)
            i += 1
            while i < n:
                if token[i] == '"':
                    if i + 1 < n and token[i + 1] == '"':
                        buffer.append('""')
                        i += 2
                        continue
                    buffer.append('"')
                    i += 1
                    break
                buffer.append(token[i])
                i += 1
            continue
        if char == ".":
            parts.append("".join(buffer))
            buffer = []
            i += 1
            continue
        buffer.append(char)
        i += 1
    parts.append("".join(buffer))

    if len(parts) >= 2:
        return _strip_quotes("".join(parts[:-1])), _strip_quotes(parts[-1])
    return None, _strip_quotes(parts[0])


def _strip_quotes(identifier: str) -> str:
    ident = identifier.strip()
    if len(ident) >= 2 and ident.startswith('"') and ident.endswith('"'):
        return ident[1:-1].replace('""', '"')
    return ident


@dataclass
class _TableCorrelationIndex:
    by_qualified: dict[tuple[str, str], str] = field(default_factory=dict)
    by_terminal: dict[str, list[str]] = field(default_factory=dict)


def _build_table_correlation_index(
    parameter_tables: list[ParameterTableRecord],
) -> _TableCorrelationIndex:
    index = _TableCorrelationIndex()
    for record in parameter_tables:
        schema_part, table_part = _split_sql_table_identifier(record.name)
        terminal_key = normalize_identifier(table_part)
        index.by_terminal.setdefault(terminal_key, []).append(record.parameter_table_id)
        if schema_part:
            index.by_qualified[(normalize_identifier(schema_part), terminal_key)] = (
                record.parameter_table_id
            )
    return index


def _resolve_table_node_id(
    token: str,
    *,
    country_code: str,
    correlation: _TableCorrelationIndex,
    nodes: dict[str, GraphNode],
    source_package_hash: str,
    warnings: list[str],
) -> str:
    """Resuelve un token SQL a un `node.id` de Table, correlacionando con
    una ParameterTable declarada cuando corresponde (punto 3 del acuerdo):
    coincidencia calificada unica o coincidencia terminal unica ->
    parameter_table_id; ambiguo -> tabla operacional + warning; sin match
    -> tabla operacional, sin warning (caso normal)."""
    schema_part, table_part = _split_sql_table_identifier(token)
    terminal_key = normalize_identifier(table_part)

    if schema_part:
        qualified_key = (normalize_identifier(schema_part), terminal_key)
        qualified_hit = correlation.by_qualified.get(qualified_key)
        if qualified_hit is not None:
            return qualified_hit
        node_id = compute_table_id(country_code, table_part, schema_name=schema_part)
    else:
        matches = correlation.by_terminal.get(terminal_key, [])
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            warnings.append(
                f"referencia SQL no calificada a {table_part!r} es ambigua entre "
                f"{len(matches)} ParameterTable declaradas; se modela como tabla operacional "
                "sin correlacionar"
            )
        node_id = compute_table_id(country_code, table_part)

    if node_id not in nodes:
        nodes[node_id] = GraphNode(
            id=node_id,
            labels=[NodeLabel.TABLE],
            properties={
                "name": table_part,
                "schema_name": schema_part,
                "country_code": country_code,
                "source_package_hash": source_package_hash,
            },
        )
    return node_id


# --- Decision / LEADS_TO: recorrido estructural de descendientes ---


def _group_statements_by_parent(
    statements: list[CanonicalStatement],
) -> dict[str | None, list[CanonicalStatement]]:
    grouped: dict[str | None, list[CanonicalStatement]] = {}
    for statement in statements:
        grouped.setdefault(statement.parent_statement_id, []).append(statement)
    return grouped


def _collect_leads_to_candidates(
    decision_statement_id: str, by_parent: dict[str | None, list[CanonicalStatement]]
) -> list[CanonicalStatement]:
    """Recorre descendientes via `parent_statement_id`, deteniendose al
    encontrar otra decision IF/EVALUATE (esa decision anidada, y solo
    ella, es duena de todo lo que hay debajo). No usa regex sobre
    `source_text`: solo campos ya estructurados."""
    result: list[CanonicalStatement] = []
    frontier: list[CanonicalStatement] = list(by_parent.get(decision_statement_id, []))
    while frontier:
        statement = frontier.pop(0)
        if statement.kind in DECISION_STATEMENT_KINDS:
            continue
        if statement.assigned_literal is not None and statement.target_data_items:
            result.append(statement)
        frontier.extend(by_parent.get(statement.statement_id, []))
    return result


@dataclass
class _BuildState:
    nodes: dict[str, GraphNode] = field(default_factory=dict)
    relationships: dict[tuple[str, str, str, str], GraphRelationship] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def add_node(self, node: GraphNode) -> None:
        """Registra `node`. Si `node.id` ya existe, conserva las
        `properties` ya registradas (deterministas: la primera
        construccion de un id dado siempre produce las mismas properties,
        derivadas de los mismos insumos) y solo une `labels` si el nuevo
        registro aporta un label ausente (nunca ocurre hoy para Table/
        ParameterTable, que usan ids distintos por diseno — ver punto 3 —
        pero se deja como salvaguarda determinista)."""
        existing = self.nodes.get(node.id)
        if existing is None:
            self.nodes[node.id] = node
            return
        extra_labels = set(node.labels) - set(existing.labels)
        if extra_labels:
            merged_labels = sorted(
                set(existing.labels) | extra_labels, key=lambda label: label.value
            )
            self.nodes[node.id] = GraphNode(
                id=node.id, labels=merged_labels, properties=existing.properties
            )

    def add_relationship(self, relationship: GraphRelationship) -> None:
        key = (
            relationship.type.value,
            relationship.from_id,
            relationship.to_id,
            _canonical_json(relationship.properties),
        )
        self.relationships[key] = relationship

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def build_semantic_graph(
    *,
    inventory: Inventory,
    programs: list[CanonicalProgram],
    dependency_artifact: DependencyArtifact,
    enrichment_artifact: SemanticEnrichmentArtifact,
    source_package_hash: str,
) -> SemanticGraph:
    state = _BuildState()
    identity = ProgramIdentity(
        country_code=inventory.manifest.country.code,
        logical_name=inventory.manifest.operation.logical_name,
        version=inventory.manifest.implementation.version,
    )
    country_code = identity.country_code

    _build_hierarchy_nodes(state, inventory, source_package_hash)
    operation_node_id = _operation_node_id(inventory, country_code)

    all_paragraph_ids: set[str] = set()
    data_item_id_universe: set[str] = set()
    program_contexts: list[tuple[CanonicalProgram, str, SymbolIndex]] = []

    for program in programs:
        program_id_value = identity.program_id(
            program_name=program.program_name, source_hash=program.source_hash
        )
        symbol_index = _build_symbol_index(program)
        program_contexts.append((program, program_id_value, symbol_index))

        _build_program_and_paragraphs(
            state, program, program_id_value, identity.version, operation_node_id, all_paragraph_ids
        )
        item_ids = _build_data_items(
            state, program, program_id_value, enrichment_artifact.data_item_tags
        )
        data_item_id_universe |= item_ids
        _build_decisions_and_leads_to(state, program, program_id_value, symbol_index)
        _build_uses(state, program, program_id_value, symbol_index)

    correlation = _build_table_correlation_index(enrichment_artifact.parameter_tables)
    _build_parameter_tables_and_entries(
        state, enrichment_artifact.parameter_tables, source_package_hash
    )
    for program, program_id_value, _symbol_index in program_contexts:
        _build_sql_relationships(
            state, program, program_id_value, country_code, correlation, source_package_hash
        )

    _build_domain_terms_and_mappings(state, enrichment_artifact, data_item_id_universe)
    _build_dependency_relationships(state, dependency_artifact, all_paragraph_ids)
    _check_semantic_tags_matched(state, enrichment_artifact.data_item_tags, data_item_id_universe)

    nodes = sorted(state.nodes.values(), key=lambda node: node.id)
    relationships = sorted(
        state.relationships.values(),
        key=lambda rel: (
            rel.type.value,
            rel.from_id,
            rel.to_id,
            _canonical_json(rel.properties),
        ),
    )
    warnings = sorted(set(state.warnings))

    return SemanticGraph(
        schema_version="2.0",
        source_package_hash=source_package_hash,
        nodes=nodes,
        relationships=relationships,
        warnings=warnings,
    )


# --- D1: Country / Application / Operation / Program / Paragraph ---


def _operation_node_id(inventory: Inventory, country_code: str) -> str:
    manifest = inventory.manifest
    application_norm = normalize_identifier(manifest.application.name)
    logical_norm = normalize_identifier(manifest.operation.logical_name)
    return f"operation::{country_code}::{application_norm}::{logical_norm}"


def _build_hierarchy_nodes(
    state: _BuildState, inventory: Inventory, source_package_hash: str
) -> None:
    manifest = inventory.manifest
    country_code = manifest.country.code

    country_node_id = f"country::{country_code}"
    state.add_node(
        GraphNode(
            id=country_node_id,
            labels=[NodeLabel.COUNTRY],
            properties={
                "code": manifest.country.code,
                "name": manifest.country.name,
                "source_package_hash": source_package_hash,
            },
        )
    )

    application_norm = normalize_identifier(manifest.application.name)
    application_node_id = f"application::{country_code}::{application_norm}"
    state.add_node(
        GraphNode(
            id=application_node_id,
            labels=[NodeLabel.APPLICATION],
            properties={
                "name": manifest.application.name,
                "country_code": country_code,
                "source_package_hash": source_package_hash,
            },
        )
    )

    operation_node_id = _operation_node_id(inventory, country_code)
    state.add_node(
        GraphNode(
            id=operation_node_id,
            labels=[NodeLabel.OPERATION],
            properties={
                "logical_name": manifest.operation.logical_name,
                "description": manifest.operation.description,
                "country_code": country_code,
                "application_name": manifest.application.name,
                "source_package_hash": source_package_hash,
            },
        )
    )

    state.add_relationship(
        GraphRelationship(
            type=RelationshipType.HAS_APPLICATION,
            from_id=country_node_id,
            to_id=application_node_id,
            properties={"source_package_hash": source_package_hash},
        )
    )
    state.add_relationship(
        GraphRelationship(
            type=RelationshipType.HAS_OPERATION,
            from_id=application_node_id,
            to_id=operation_node_id,
            properties={"source_package_hash": source_package_hash},
        )
    )


def _build_program_and_paragraphs(
    state: _BuildState,
    program: CanonicalProgram,
    program_id_value: str,
    version: str,
    operation_node_id: str,
    all_paragraph_ids: set[str],
) -> None:
    state.add_node(
        GraphNode(
            id=program_id_value,
            labels=[NodeLabel.PROGRAM],
            properties={
                "name": program.program_name,
                "version": version,
                "source_file": program.source_file,
                "source_hash": program.source_hash,
                "source_package_hash": program.source_package_hash,
                "source_format": program.source_format.value,
                "encoding": program.encoding,
            },
        )
    )
    state.add_relationship(
        GraphRelationship(
            type=RelationshipType.EXECUTES_VIA,
            from_id=operation_node_id,
            to_id=program_id_value,
            properties={"source_package_hash": program.source_package_hash},
        )
    )

    for paragraph in program.paragraphs:
        para_id = paragraph_id(program_id_value, paragraph.name)
        all_paragraph_ids.add(para_id)
        state.add_node(
            GraphNode(
                id=para_id,
                labels=[NodeLabel.PARAGRAPH],
                properties={
                    "name": paragraph.name,
                    "source_text": paragraph.source_text,
                    "line_start": paragraph.line_start,
                    "line_end": paragraph.line_end,
                    "source_file": paragraph.source_file,
                    "source_package_hash": program.source_package_hash,
                },
            )
        )
        state.add_relationship(
            GraphRelationship(
                type=RelationshipType.CONTAINS,
                from_id=program_id_value,
                to_id=para_id,
                properties={"source_package_hash": program.source_package_hash},
            )
        )


# --- D3/D7: DataItem (enriquecido con semantic_tag ya resuelto) ---


def _build_data_items(
    state: _BuildState,
    program: CanonicalProgram,
    program_id_value: str,
    data_item_tags: list[DataItemSemanticTag],
) -> set[str]:
    tag_by_id = {tag.data_item_id: tag for tag in data_item_tags}
    built_ids: set[str] = set()

    for item in program.data_items:
        item_id = data_item_id(program_id_value, item.qualified_name)
        built_ids.add(item_id)
        tag = tag_by_id.get(item_id)
        properties: dict[str, object] = {
            "name": item.name,
            "qualified_name": item.qualified_name,
            "level": item.level,
            "pic": item.pic,
            "usage": item.usage,
            "source_file": item.source_file,
            "line": item.line,
            "source_package_hash": program.source_package_hash,
            "semantic_tag": tag.semantic_tag if tag else None,
            "semantic_confidence": tag.semantic_confidence if tag else None,
            "semantic_evidence_json": (
                _canonical_json([evidence.model_dump(mode="json") for evidence in tag.evidence])
                if tag
                else None
            ),
        }
        state.add_node(GraphNode(id=item_id, labels=[NodeLabel.DATA_ITEM], properties=properties))

    return built_ids


def _check_semantic_tags_matched(
    state: _BuildState, data_item_tags: list[DataItemSemanticTag], universe: set[str]
) -> None:
    for tag in data_item_tags:
        if tag.data_item_id not in universe:
            raise SemanticGraphBuildError(
                f"DataItemSemanticTag.data_item_id {tag.data_item_id!r} no corresponde a "
                "ningun CanonicalDataItem actual (artefacto de Prompt 7 desincronizado)"
            )


# --- D4: Decision / HAS_DECISION / LEADS_TO ---


def _build_decisions_and_leads_to(
    state: _BuildState, program: CanonicalProgram, program_id_value: str, symbol_index: SymbolIndex
) -> None:
    for paragraph in program.paragraphs:
        para_id = paragraph_id(program_id_value, paragraph.name)
        by_parent = _group_statements_by_parent(paragraph.statements)
        decision_statements = decision_statements_in_order(paragraph)

        for ordinal, statement in enumerate(decision_statements, start=1):
            decision_id = decision_id_for(para_id, ordinal=ordinal, line_start=statement.line_start)

            candidates = _collect_leads_to_candidates(statement.statement_id, by_parent)
            resolved_literals: set[str] = set()

            for branch_statement in candidates:
                for target_token in branch_statement.target_data_items:
                    resolution = resolve_variable(target_token, symbol_index)
                    if isinstance(resolution, VariableResolutionFailure):
                        reason = "ambigua" if resolution.reason == "AMBIGUOUS" else "no resuelta"
                        state.warn(
                            f"{program.program_name}::{paragraph.name}::"
                            f"{branch_statement.statement_id}: target_data_item {target_token!r} "
                            f"{reason} contra CanonicalDataItem; no se genera LEADS_TO"
                        )
                        continue

                    target_item_id = data_item_id(program_id_value, resolution.qualified_name)
                    resolved_literals.add(branch_statement.assigned_literal or "")
                    state.add_relationship(
                        GraphRelationship(
                            type=RelationshipType.LEADS_TO,
                            from_id=decision_id,
                            to_id=target_item_id,
                            properties={
                                "assigned_literal": branch_statement.assigned_literal,
                                "branch_kind": (
                                    branch_statement.branch_kind.value
                                    if branch_statement.branch_kind
                                    else None
                                ),
                                "branch_condition": branch_statement.branch_condition,
                                "statement_id": branch_statement.statement_id,
                                "location_kind": branch_statement.location_kind.value,
                                "source_file": branch_statement.source_file,
                                "line_start": branch_statement.line_start,
                                "line_end": branch_statement.line_end,
                                "derivation_rule": resolution.derivation_rule,
                                "evidence_json": _canonical_json(
                                    {
                                        "original_target_token": target_token,
                                        "resolved_qualified_name": resolution.qualified_name,
                                    }
                                ),
                                "source_package_hash": program.source_package_hash,
                            },
                        )
                    )

            outcome_code = next(iter(resolved_literals)) if len(resolved_literals) == 1 else None

            state.add_node(
                GraphNode(
                    id=decision_id,
                    labels=[NodeLabel.DECISION],
                    properties={
                        "expression": statement.expression,
                        "normalized_expression": statement.normalized_expression,
                        # legacy_expression (Fase 3 v1.18.3): dato de
                        # compatibilidad de identidad UNICAMENTE, consumido
                        # solo por enhanced_candidate_integration.py para
                        # calcular legacy_key -- ver docstring de
                        # CanonicalStatement.legacy_expression. Nunca leido
                        # por context_package_builder.py/RuleCandidate.
                        "legacy_expression": statement.legacy_expression,
                        "operands_json": _canonical_json(statement.operands),
                        "outcome_code": outcome_code,
                        "rule_type": None,
                        "line_start": statement.line_start,
                        "line_end": statement.line_end,
                        "source_package_hash": program.source_package_hash,
                    },
                )
            )
            state.add_relationship(
                GraphRelationship(
                    type=RelationshipType.HAS_DECISION,
                    from_id=para_id,
                    to_id=decision_id,
                    properties={"source_package_hash": program.source_package_hash},
                )
            )


# --- D2: USES (consolidado desde evidencia a nivel statement) ---


@dataclass
class _UsesAccumulator:
    modes: set[str] = field(default_factory=set)
    evidence: list[dict[str, str]] = field(default_factory=list)
    weakest_rule: str = "QUALIFIED"


def _build_uses(
    state: _BuildState, program: CanonicalProgram, program_id_value: str, symbol_index: SymbolIndex
) -> None:
    grouped: dict[tuple[str, str], _UsesAccumulator] = {}

    for paragraph in program.paragraphs:
        para_id = paragraph_id(program_id_value, paragraph.name)
        for statement in paragraph.statements:
            for access_mode, tokens in (
                ("READ", statement.variables_read),
                ("WRITE", statement.variables_written),
            ):
                for token in tokens:
                    resolution = resolve_variable(token, symbol_index)
                    if isinstance(resolution, VariableResolutionFailure):
                        reason = "ambigua" if resolution.reason == "AMBIGUOUS" else "no resuelta"
                        state.warn(
                            f"{program.program_name}::{paragraph.name}::"
                            f"{statement.statement_id}: variable {token!r} ({access_mode}) "
                            f"{reason} contra CanonicalDataItem; no se genera USES"
                        )
                        continue

                    target_item_id = data_item_id(program_id_value, resolution.qualified_name)
                    key = (para_id, target_item_id)
                    accumulator = grouped.setdefault(key, _UsesAccumulator())
                    accumulator.modes.add(access_mode)
                    accumulator.evidence.append(
                        {
                            "statement_id": statement.statement_id,
                            "access_mode": access_mode,
                            "original_token": token,
                            "resolved_qualified_name": resolution.qualified_name,
                            "derivation_rule": resolution.derivation_rule,
                        }
                    )
                    if resolution.derivation_rule == "SIMPLE_NAME":
                        accumulator.weakest_rule = "SIMPLE_NAME"

    for (para_id, target_item_id), accumulator in grouped.items():
        evidence_list = sorted(
            accumulator.evidence,
            key=lambda item: (item["statement_id"], item["access_mode"], item["original_token"]),
        )
        state.add_relationship(
            GraphRelationship(
                type=RelationshipType.USES,
                from_id=para_id,
                to_id=target_item_id,
                properties={
                    "access_modes_json": _canonical_json(sorted(accumulator.modes)),
                    "evidence_json": _canonical_json(evidence_list),
                    "derivation_rule": accumulator.weakest_rule,
                    "source_package_hash": program.source_package_hash,
                },
            )
        )


# --- D3/C2: Table / ParameterTable / ParameterEntry / HAS_ENTRY ---


def _build_parameter_tables_and_entries(
    state: _BuildState,
    parameter_tables: list[ParameterTableRecord],
    source_package_hash: str,
) -> None:
    for record in parameter_tables:
        schema_part, _table_part = _split_sql_table_identifier(record.name)
        state.add_node(
            GraphNode(
                id=record.parameter_table_id,
                labels=[NodeLabel.TABLE, NodeLabel.PARAMETER_TABLE],
                properties={
                    "name": record.name,
                    "schema_name": schema_part,
                    "country_code": record.country_code,
                    "snapshot_date": (
                        record.snapshot_date.isoformat() if record.snapshot_date else None
                    ),
                    "ddl_file": record.ddl_relative_path,
                    "snapshot_file": record.snapshot_relative_path,
                    "snapshot_hash": record.snapshot_hash,
                    "source_package_hash": source_package_hash,
                },
            )
        )
        for entry in record.entries:
            state.add_node(
                GraphNode(
                    id=entry.parameter_entry_id,
                    labels=[NodeLabel.PARAMETER_ENTRY],
                    properties={
                        "row_number": entry.row_number,
                        "row_hash": entry.row_hash,
                        "raw_row_json": _canonical_json(entry.raw_row),
                        "normalized_row_json": _canonical_json(entry.normalized_row),
                        "source_package_hash": source_package_hash,
                    },
                )
            )
            state.add_relationship(
                GraphRelationship(
                    type=RelationshipType.HAS_ENTRY,
                    from_id=record.parameter_table_id,
                    to_id=entry.parameter_entry_id,
                    properties={"source_package_hash": source_package_hash},
                )
            )


# --- D3/D5: SQL -> READS/WRITES/UPDATES/INSERTS (relaciones directas) ---


@dataclass(frozen=True)
class _SqlAccessEvidence:
    statement_id: str
    predicate_text: str | None
    host_variables_json: str
    source_file: str | None
    line_start: int | None
    line_end: int | None
    location_kind: str

    def sort_key(self) -> tuple[int, str, str]:
        return (
            self.line_start if self.line_start is not None else -1,
            self.source_file or "",
            self.statement_id,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "statement_id": self.statement_id,
            "predicate_text": self.predicate_text,
            "host_variables_json": self.host_variables_json,
            "source_file": self.source_file,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "location_kind": self.location_kind,
        }


def _build_sql_relationships(
    state: _BuildState,
    program: CanonicalProgram,
    program_id_value: str,
    country_code: str,
    correlation: _TableCorrelationIndex,
    source_package_hash: str,
) -> None:
    grouped: dict[tuple[str, str, str], list[_SqlAccessEvidence]] = {}

    for paragraph in program.paragraphs:
        para_id = paragraph_id(program_id_value, paragraph.name)
        for statement in paragraph.statements:
            for access in statement.sql_access:
                table_node_id = _resolve_table_node_id(
                    access.table,
                    country_code=country_code,
                    correlation=correlation,
                    nodes=state.nodes,
                    source_package_hash=source_package_hash,
                    warnings=state.warnings,
                )
                key = (para_id, table_node_id, access.operation.value)
                grouped.setdefault(key, []).append(
                    _SqlAccessEvidence(
                        statement_id=statement.statement_id,
                        predicate_text=access.predicate_text,
                        host_variables_json=_canonical_json(access.host_variables),
                        source_file=access.source_file,
                        line_start=access.line_start,
                        line_end=access.line_end,
                        location_kind=access.location_kind.value,
                    )
                )

    for (para_id, table_node_id, operation), evidence_entries in grouped.items():
        ordered = sorted(evidence_entries, key=lambda entry: entry.sort_key())
        primary = ordered[0]
        state.add_relationship(
            GraphRelationship(
                type=RelationshipType[operation],
                from_id=para_id,
                to_id=table_node_id,
                properties={
                    "sql_operation": operation,
                    "predicate_text": primary.predicate_text,
                    "host_variables_json": primary.host_variables_json,
                    "evidence_json": _canonical_json([entry.as_dict() for entry in ordered]),
                    "location_kind": primary.location_kind,
                    "source_file": primary.source_file,
                    "line_start": primary.line_start,
                    "line_end": primary.line_end,
                    "source_package_hash": source_package_hash,
                },
            )
        )


# --- D7: DomainTerm / HAS_DOMAIN_TERM ---


def _build_domain_terms_and_mappings(
    state: _BuildState,
    enrichment_artifact: SemanticEnrichmentArtifact,
    data_item_id_universe: set[str],
) -> None:
    for term in enrichment_artifact.domain_terms:
        state.add_node(
            GraphNode(
                id=term.domain_term_id,
                labels=[NodeLabel.DOMAIN_TERM],
                properties={
                    "functional_name": term.functional_name,
                    "definition": term.definition,
                    "entity_type": term.entity_type,
                    "authoritative_source": term.authoritative_source,
                    "source_kind": term.source_kind,
                    "catalog_version": term.catalog_version,
                    # Nunca se deriva del mapping mas fuerte: si el
                    # catalogo no declara confidence propia, la propiedad
                    # queda None. La confidence del mapping vive en
                    # HAS_DOMAIN_TERM, no se transfiere al nodo.
                    "confidence": None,
                    "source_package_hash": enrichment_artifact.source_package_hash,
                },
            )
        )

    for mapping in enrichment_artifact.data_item_domain_term_mappings:
        if mapping.data_item_id not in data_item_id_universe:
            raise SemanticGraphBuildError(
                f"DataItemDomainTermMapping.data_item_id {mapping.data_item_id!r} no "
                "corresponde a ningun CanonicalDataItem actual (artefacto de Prompt 7 "
                "desincronizado)"
            )
        state.add_relationship(
            GraphRelationship(
                type=RelationshipType.HAS_DOMAIN_TERM,
                from_id=mapping.data_item_id,
                to_id=mapping.domain_term_id,
                properties={
                    "derivation_rule": mapping.derivation_rule,
                    "confidence": mapping.confidence,
                    "mapping_evidence_json": _canonical_json(mapping.evidence),
                    "source_package_hash": enrichment_artifact.source_package_hash,
                },
            )
        )


# --- D2: DATA_DEPENDS_ON / CONTROL_DEPENDS_ON (mapeo literal 1:1) ---


def _build_dependency_relationships(
    state: _BuildState, dependency_artifact: DependencyArtifact, all_paragraph_ids: set[str]
) -> None:
    for dependency in dependency_artifact.dependencies:
        if dependency.from_paragraph_id not in all_paragraph_ids:
            raise SemanticGraphBuildError(
                f"ParagraphDependency.from_paragraph_id {dependency.from_paragraph_id!r} no "
                "corresponde a ningun Paragraph actual (artefacto de Prompt 6 desincronizado)"
            )
        if dependency.to_paragraph_id not in all_paragraph_ids:
            raise SemanticGraphBuildError(
                f"ParagraphDependency.to_paragraph_id {dependency.to_paragraph_id!r} no "
                "corresponde a ningun Paragraph actual (artefacto de Prompt 6 desincronizado)"
            )

        relationship_type = (
            RelationshipType.DATA_DEPENDS_ON
            if dependency.dependency_type == DependencyType.DATA_DEPENDS_ON
            else RelationshipType.CONTROL_DEPENDS_ON
        )
        state.add_relationship(
            GraphRelationship(
                type=relationship_type,
                from_id=dependency.from_paragraph_id,
                to_id=dependency.to_paragraph_id,
                properties={
                    "variables_json": _canonical_json(dependency.variables),
                    "control_construct": dependency.control_construct,
                    "dependency_depth": dependency.dependency_depth,
                    "confidence": dependency.confidence,
                    "derivation_rule": dependency.derivation_rule,
                    "location_kind": dependency.location_kind.value,
                    "source_file": dependency.source_file,
                    "line_start": dependency.line_start,
                    "line_end": dependency.line_end,
                    "source_package_hash": dependency.source_package_hash,
                    "evidence_json": _canonical_json(
                        [evidence.model_dump(mode="json") for evidence in dependency.evidence]
                    ),
                },
            )
        )
