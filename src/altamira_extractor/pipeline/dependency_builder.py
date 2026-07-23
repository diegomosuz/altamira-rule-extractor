"""DependencyBuilder: deriva el CPG reducido (Paragraph -> Paragraph) a
partir exclusivamente de los `CanonicalProgram` ya producidos por PARSED.

No vuelve a parsear COBOL, no invoca el JAR y no usa regex sobre
`source_text`: toda arista proviene de campos ya estructurados
(`target_paragraphs`, `variables_read`/`variables_written`,
`data_items`). Las dependencias son siempre dentro del mismo programa
(nunca hay evidencia estructural de CALL entre programas en
`CanonicalProgram`, y `StatementKind` no modela llamadas inter-programa).

Direccion del edge (docs/NEO4J_METAMODEL.md): "origen que influye ->
parrafo dependiente". `from_paragraph_id` escribe el dato / transfiere el
control; `to_paragraph_id` lee ese dato / recibe el control.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..contracts.canonical import (
    CanonicalDataItem,
    CanonicalParagraph,
    CanonicalProgram,
    CanonicalStatement,
)
from ..contracts.dependencies import DependencyEvidence, ParagraphDependency, _dependency_sort_key
from ..contracts.enums import DependencyEvidenceRole, DependencyType, StatementKind
from .identifiers import ProgramIdentity, normalize_identifier, paragraph_id

__all__ = [
    "ProgramIdentity",
    "ProgramInput",
    "VariableResolution",
    "VariableResolutionFailure",
    "build_dependencies",
    "normalize_identifier",
    "paragraph_id",
    "resolve_variable",
]

_CONTROL_STATEMENT_KINDS = (StatementKind.GO_TO, StatementKind.PERFORM)

_CONTROL_CONFIDENCE = 1.0
_CONTROL_DERIVATION_RULE = "EXPLICIT_CONTROL_TARGET"
_DATA_QUALIFIED_CONFIDENCE = 0.8
_DATA_QUALIFIED_DERIVATION_RULE = "PARAGRAPH_WRITE_READ_QUALIFIED_MATCH"
_DATA_UNIQUE_NAME_CONFIDENCE = 0.7
_DATA_UNIQUE_NAME_DERIVATION_RULE = "PARAGRAPH_WRITE_READ_UNIQUE_NAME_MATCH"

_DerivationRule = Literal["QUALIFIED", "SIMPLE_NAME"]
_FailureReason = Literal["AMBIGUOUS", "UNRESOLVED"]


@dataclass(frozen=True)
class ProgramInput:
    program: CanonicalProgram
    identity: ProgramIdentity


@dataclass(frozen=True)
class VariableResolution:
    qualified_name: str
    derivation_rule: _DerivationRule


@dataclass(frozen=True)
class VariableResolutionFailure:
    reason: _FailureReason


@dataclass(frozen=True)
class _SymbolIndex:
    by_qualified_name: dict[str, list[CanonicalDataItem]]
    by_simple_name: dict[str, list[CanonicalDataItem]]


def _build_symbol_index(program: CanonicalProgram) -> _SymbolIndex:
    by_qualified: dict[str, list[CanonicalDataItem]] = {}
    by_simple: dict[str, list[CanonicalDataItem]] = {}
    for item in program.data_items:
        by_qualified.setdefault(normalize_identifier(item.qualified_name), []).append(item)
        by_simple.setdefault(normalize_identifier(item.name), []).append(item)
    return _SymbolIndex(by_qualified_name=by_qualified, by_simple_name=by_simple)


def resolve_variable(
    token: str, index: _SymbolIndex
) -> VariableResolution | VariableResolutionFailure:
    """Resolucion de 4 pasos: qualified_name exacto y unico; en su
    defecto name unico; ambiguo (name o qualified_name repetido) o sin
    coincidencia -> fallo (nunca se inventa una correspondencia)."""
    key = normalize_identifier(token)

    qualified_matches = index.by_qualified_name.get(key, [])
    if len(qualified_matches) == 1:
        return VariableResolution(qualified_matches[0].qualified_name, "QUALIFIED")
    if len(qualified_matches) > 1:
        return VariableResolutionFailure("AMBIGUOUS")

    simple_matches = index.by_simple_name.get(key, [])
    if len(simple_matches) == 1:
        return VariableResolution(simple_matches[0].qualified_name, "SIMPLE_NAME")
    if len(simple_matches) > 1:
        return VariableResolutionFailure("AMBIGUOUS")

    return VariableResolutionFailure("UNRESOLVED")


@dataclass(frozen=True)
class _ParagraphIndex:
    by_normalized_name: dict[str, list[CanonicalParagraph]]


def _build_paragraph_index(program: CanonicalProgram) -> _ParagraphIndex:
    index: dict[str, list[CanonicalParagraph]] = {}
    for paragraph in program.paragraphs:
        index.setdefault(normalize_identifier(paragraph.name), []).append(paragraph)
    return _ParagraphIndex(by_normalized_name=index)


@dataclass(frozen=True)
class _BuildContext:
    program: CanonicalProgram
    program_id: str
    paragraph_index: _ParagraphIndex
    symbol_index: _SymbolIndex
    source_package_hash: str


@dataclass(frozen=True)
class _RawControlEdge:
    from_paragraph: CanonicalParagraph
    to_paragraph: CanonicalParagraph
    construct: str
    statement: CanonicalStatement
    raw_target: str


def _collect_control_edges(ctx: _BuildContext, warnings: list[str]) -> list[_RawControlEdge]:
    edges: list[_RawControlEdge] = []
    program_name = ctx.program.program_name
    for paragraph in ctx.program.paragraphs:
        for statement in paragraph.statements:
            if statement.kind not in _CONTROL_STATEMENT_KINDS:
                continue
            if not statement.target_paragraphs:
                continue

            construct = statement.kind.value

            if statement.kind == StatementKind.PERFORM and len(statement.target_paragraphs) == 2:
                # PERFORM ... THRU ...: ProLeap solo expone los dos
                # extremos nombrados via target_paragraphs. No se lee
                # source_text ni se asume el orden fisico de paragraphs
                # del programa para sintetizar los intermedios: eso
                # inventaria evidencia. Se deja constancia explicita del
                # soporte parcial.
                warnings.append(
                    f"{program_name}::{paragraph.name}::{statement.statement_id}: PERFORM con dos "
                    f"extremos explicitos ({statement.target_paragraphs[0]!r}, "
                    f"{statement.target_paragraphs[1]!r}), posible THRU: se modelan unicamente los "
                    "targets explicitos, no se sintetizan los paragraphs intermedios"
                )

            for raw_target in statement.target_paragraphs:
                matches = ctx.paragraph_index.by_normalized_name.get(
                    normalize_identifier(raw_target), []
                )
                if len(matches) > 1:
                    warnings.append(
                        f"{program_name}::{paragraph.name}::{statement.statement_id}: referencia "
                        f"{construct} a {raw_target!r} es ambigua ({len(matches)} paragraphs con "
                        "ese nombre); no se genera dependencia"
                    )
                    continue
                if not matches:
                    warnings.append(
                        f"{program_name}::{paragraph.name}::{statement.statement_id}: {construct} "
                        f"hacia {raw_target!r} no se encontro entre los paragraphs del programa; "
                        "no se genera dependencia"
                    )
                    continue

                target_paragraph = matches[0]
                if normalize_identifier(target_paragraph.name) == normalize_identifier(
                    paragraph.name
                ):
                    warnings.append(
                        f"{program_name}::{paragraph.name}: referencia de control a si mismo via "
                        f"{construct} omitida (auto-dependencia no representable)"
                    )
                    continue

                edges.append(
                    _RawControlEdge(paragraph, target_paragraph, construct, statement, raw_target)
                )
    return edges


def _control_edges_to_dependencies(
    ctx: _BuildContext, raw_edges: list[_RawControlEdge]
) -> list[ParagraphDependency]:
    grouped: dict[tuple[str, str, str], list[_RawControlEdge]] = {}
    for edge in raw_edges:
        key = (
            paragraph_id(ctx.program_id, edge.from_paragraph.name),
            paragraph_id(ctx.program_id, edge.to_paragraph.name),
            edge.construct,
        )
        grouped.setdefault(key, []).append(edge)

    dependencies: list[ParagraphDependency] = []
    for (from_id, to_id, construct), edges in grouped.items():
        ordered_edges = sorted(edges, key=lambda e: e.statement.statement_id)
        primary_statement = ordered_edges[0].statement

        evidence = [
            DependencyEvidence(
                role=DependencyEvidenceRole.CONTROL,
                statement_id=edge.statement.statement_id,
                statement_kind=edge.statement.kind,
                source_text=edge.statement.source_text,
                source_file=edge.statement.source_file,
                line_start=edge.statement.line_start,
                line_end=edge.statement.line_end,
                location_kind=edge.statement.location_kind,
                parent_statement_id=edge.statement.parent_statement_id,
                branch_kind=edge.statement.branch_kind,
                branch_condition=edge.statement.branch_condition,
                original_target=edge.raw_target,
            )
            for edge in ordered_edges
        ]

        dependencies.append(
            ParagraphDependency(
                dependency_type=DependencyType.CONTROL_DEPENDS_ON,
                from_paragraph_id=from_id,
                to_paragraph_id=to_id,
                variables=[],
                control_construct=construct,
                dependency_depth=1,
                confidence=_CONTROL_CONFIDENCE,
                derivation_rule=_CONTROL_DERIVATION_RULE,
                source_file=primary_statement.source_file,
                line_start=primary_statement.line_start,
                line_end=primary_statement.line_end,
                location_kind=primary_statement.location_kind,
                source_package_hash=ctx.source_package_hash,
                evidence=evidence,
            )
        )
    return dependencies


@dataclass(frozen=True)
class _VariableEvent:
    paragraph: CanonicalParagraph
    statement: CanonicalStatement
    original_token: str
    qualified_name: str
    derivation_rule: _DerivationRule


def _collect_variable_events(
    ctx: _BuildContext, warnings: list[str], *, written: bool
) -> list[_VariableEvent]:
    events: list[_VariableEvent] = []
    program_name = ctx.program.program_name
    role_label = "escritura" if written else "lectura"

    for paragraph in ctx.program.paragraphs:
        for statement in paragraph.statements:
            tokens = statement.variables_written if written else statement.variables_read
            for token in tokens:
                resolution = resolve_variable(token, ctx.symbol_index)
                if isinstance(resolution, VariableResolutionFailure):
                    if resolution.reason == "AMBIGUOUS":
                        warnings.append(
                            f"{program_name}::{paragraph.name}::{statement.statement_id}: "
                            f"variable {token!r} ({role_label}) es ambigua entre multiples "
                            "DataItems; no se usa para derivar dependencias DATA"
                        )
                    else:
                        warnings.append(
                            f"{program_name}::{paragraph.name}::{statement.statement_id}: "
                            f"variable {token!r} ({role_label}) no se pudo resolver contra ningun "
                            "DataItem; no se usa para derivar dependencias DATA"
                        )
                    continue
                events.append(
                    _VariableEvent(
                        paragraph=paragraph,
                        statement=statement,
                        original_token=token,
                        qualified_name=resolution.qualified_name,
                        derivation_rule=resolution.derivation_rule,
                    )
                )
    return events


def _variable_evidence(event: _VariableEvent, role: DependencyEvidenceRole) -> DependencyEvidence:
    return DependencyEvidence(
        role=role,
        statement_id=event.statement.statement_id,
        statement_kind=event.statement.kind,
        source_text=event.statement.source_text,
        source_file=event.statement.source_file,
        line_start=event.statement.line_start,
        line_end=event.statement.line_end,
        location_kind=event.statement.location_kind,
        parent_statement_id=event.statement.parent_statement_id,
        branch_kind=event.statement.branch_kind,
        branch_condition=event.statement.branch_condition,
        original_variable=event.original_token,
        resolved_qualified_name=event.qualified_name,
    )


def _build_data_dependencies(ctx: _BuildContext, warnings: list[str]) -> list[ParagraphDependency]:
    write_events = _collect_variable_events(ctx, warnings, written=True)
    read_events = _collect_variable_events(ctx, warnings, written=False)

    writes_by_qname: dict[str, list[_VariableEvent]] = {}
    for event in write_events:
        writes_by_qname.setdefault(event.qualified_name, []).append(event)

    reads_by_qname: dict[str, list[_VariableEvent]] = {}
    for event in read_events:
        reads_by_qname.setdefault(event.qualified_name, []).append(event)

    grouped: dict[tuple[str, str], list[tuple[str, _VariableEvent, _VariableEvent]]] = {}
    for qname, writers in writes_by_qname.items():
        readers = reads_by_qname.get(qname)
        if not readers:
            continue
        for writer in writers:
            for reader in readers:
                if writer.paragraph.name == reader.paragraph.name:
                    continue  # A != B: el contrato no admite auto-dependencia.
                key = (writer.paragraph.name, reader.paragraph.name)
                grouped.setdefault(key, []).append((qname, writer, reader))

    dependencies: list[ParagraphDependency] = []
    for (from_name, to_name), matches in grouped.items():
        dependencies.append(_consolidate_data_edge(ctx, from_name, to_name, matches))
    return dependencies


def _consolidate_data_edge(
    ctx: _BuildContext,
    from_paragraph_name: str,
    to_paragraph_name: str,
    matches: list[tuple[str, _VariableEvent, _VariableEvent]],
) -> ParagraphDependency:
    # Orden deterministico (no hay orden textual/temporal confiable en
    # COBOL): por qualified_name y luego por statement_id de ambos lados.
    ordered = sorted(
        matches, key=lambda m: (m[0], m[1].statement.statement_id, m[2].statement.statement_id)
    )

    qualified_names = sorted({qname for qname, _writer, _reader in ordered})

    weakest_rule: _DerivationRule = "QUALIFIED"
    for _qname, writer, reader in ordered:
        if writer.derivation_rule == "SIMPLE_NAME" or reader.derivation_rule == "SIMPLE_NAME":
            weakest_rule = "SIMPLE_NAME"
            break

    if weakest_rule == "SIMPLE_NAME":
        confidence = _DATA_UNIQUE_NAME_CONFIDENCE
        derivation_rule = _DATA_UNIQUE_NAME_DERIVATION_RULE
    else:
        confidence = _DATA_QUALIFIED_CONFIDENCE
        derivation_rule = _DATA_QUALIFIED_DERIVATION_RULE

    # Se persiste la ubicacion del primer statement escritor (segun el
    # orden deterministico ya calculado) como source_file/line_start/
    # line_end del edge consolidado: el contrato solo admite una unica
    # ubicacion primaria por dependencia. El resto de la evidencia
    # (todas las variables/statements coincidentes entre este mismo par
    # de paragraphs) no se pierde: queda en `evidence`.
    _primary_qname, primary_writer, _primary_reader = ordered[0]

    evidence: list[DependencyEvidence] = []
    seen: set[tuple[str, str]] = set()
    for _qname, writer, reader in ordered:
        writer_key = (DependencyEvidenceRole.WRITER.value, writer.statement.statement_id)
        if writer_key not in seen:
            seen.add(writer_key)
            evidence.append(_variable_evidence(writer, DependencyEvidenceRole.WRITER))
        reader_key = (DependencyEvidenceRole.READER.value, reader.statement.statement_id)
        if reader_key not in seen:
            seen.add(reader_key)
            evidence.append(_variable_evidence(reader, DependencyEvidenceRole.READER))

    from_id = paragraph_id(ctx.program_id, from_paragraph_name)
    to_id = paragraph_id(ctx.program_id, to_paragraph_name)

    return ParagraphDependency(
        dependency_type=DependencyType.DATA_DEPENDS_ON,
        from_paragraph_id=from_id,
        to_paragraph_id=to_id,
        variables=qualified_names,
        control_construct=None,
        dependency_depth=1,
        confidence=confidence,
        derivation_rule=derivation_rule,
        source_file=primary_writer.statement.source_file,
        line_start=primary_writer.statement.line_start,
        line_end=primary_writer.statement.line_end,
        location_kind=primary_writer.statement.location_kind,
        source_package_hash=ctx.source_package_hash,
        evidence=evidence,
    )


def build_dependencies(
    programs: list[ProgramInput], *, source_package_hash: str
) -> tuple[list[ParagraphDependency], list[str]]:
    """Construye todas las ParagraphDependency de todos los programas.

    Las dependencias son siempre dentro del mismo programa: `program_id`
    (que incluye pais/operativa/version/source_hash) esta embebido en
    cada `paragraph_id`, por lo que dos programas distintos nunca pueden
    colisionar en `(from_paragraph_id, to_paragraph_id)`. La consolidacion
    de duplicados ocurre dentro de cada programa (ver
    `_control_edges_to_dependencies`/`_build_data_dependencies`); el
    resultado global ya sale sin duplicados, solo se ordena.
    """
    all_dependencies: list[ParagraphDependency] = []
    all_warnings: list[str] = []

    for program_input in programs:
        program = program_input.program
        program_id = program_input.identity.program_id(
            program_name=program.program_name, source_hash=program.source_hash
        )
        ctx = _BuildContext(
            program=program,
            program_id=program_id,
            paragraph_index=_build_paragraph_index(program),
            symbol_index=_build_symbol_index(program),
            source_package_hash=source_package_hash,
        )
        raw_control_edges = _collect_control_edges(ctx, all_warnings)
        all_dependencies.extend(_control_edges_to_dependencies(ctx, raw_control_edges))
        all_dependencies.extend(_build_data_dependencies(ctx, all_warnings))

    ordered = sorted(all_dependencies, key=_dependency_sort_key)
    deduped_warnings = sorted(set(all_warnings))
    return ordered, deduped_warnings
