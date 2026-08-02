"""Fixtures compartidas de detectores de reglas interprocedurales en
shadow mode (Fase 8 de la ampliacion semantica,
`feat/interprocedural-rule-detectors-shadow`). NO es un modulo de test
(sin prefijo `test_`): expone builders reutilizados por
`test_interprocedural_rule_detectors.py`,
`test_interprocedural_rule_comparator.py`,
`test_interprocedural_rule_detector.py` y
`test_interprocedural_rule_candidates_service.py`. Mismo patron que
`tests/pipeline/v2_shadow_helpers.py`: encadena los analizadores REALES
de Fase 2/4/6/7 (`analyze_semantic_effects`, `analyze_semantic_propagation`,
`analyze_interprocedural_call_linkage`, `analyze_interprocedural_propagation`)
para producir fixtures fielmente correlacionables, nunca fabrica un
`InterproceduralCallLinkageArtifact`/`InterproceduralPropagationArtifact`
a mano."""

from __future__ import annotations

from altamira_extractor.contracts.candidate import CandidateArtifact
from altamira_extractor.contracts.canonical import (
    CanonicalCallArgument,
    CanonicalDataItem,
    CanonicalEntryParameter,
    CanonicalLinkageDataItem,
    CanonicalParagraph,
    CanonicalProgram,
    CanonicalStatement,
)
from altamira_extractor.contracts.enums import (
    CallPassingMode,
    CallTargetKind,
    LocationKind,
    ProgramTerminationKind,
    SourceFormat,
    StatementKind,
)
from altamira_extractor.contracts.interprocedural_call_linkage import (
    InterproceduralCallLinkageArtifact,
)
from altamira_extractor.contracts.interprocedural_propagation import (
    InterproceduralPropagationArtifact,
)
from altamira_extractor.contracts.semantic_effects import SemanticEffectsArtifact
from altamira_extractor.contracts.semantic_enrichment import (
    DataItemSemanticTag,
    SemanticEnrichmentArtifact,
    SemanticTagRuleMatch,
)
from altamira_extractor.contracts.semantic_propagation import SemanticPropagationArtifact
from altamira_extractor.pipeline.interprocedural_call_linkage_analyzer import (
    analyze_interprocedural_call_linkage,
)
from altamira_extractor.pipeline.interprocedural_propagation_analyzer import (
    analyze_interprocedural_propagation,
)
from altamira_extractor.pipeline.interprocedural_rule_detectors import (
    InterproceduralRuleDetectorContext,
    build_detector_context,
)
from altamira_extractor.pipeline.semantic_effects_analyzer import analyze_semantic_effects
from altamira_extractor.pipeline.semantic_propagation_analyzer import analyze_semantic_propagation

HASH = "c" * 64
HASHES = {"artifacts/02-canonical": HASH}


def make_stmt(**overrides: object) -> CanonicalStatement:
    fields: dict[str, object] = {
        "statement_id": "P::A::0::MOVE",
        "kind": StatementKind.MOVE,
        "source_text": "MOVE",
        "location_kind": LocationKind.UNKNOWN,
    }
    fields.update(overrides)
    return CanonicalStatement(**fields)  # type: ignore[arg-type]


def make_move(stmt_id: str, *, target: str, literal: str) -> CanonicalStatement:
    return make_stmt(
        statement_id=stmt_id,
        kind=StatementKind.MOVE,
        target_data_items=[target],
        variables_written=[target],
        assigned_literal=literal,
    )


def make_terminator(
    stmt_id: str,
    termination_kind: ProgramTerminationKind,
    *,
    parent_statement_id: str | None = None,
) -> CanonicalStatement:
    return make_stmt(
        statement_id=stmt_id,
        kind=StatementKind.PROGRAM_TERMINATION,
        source_text=termination_kind.value,
        program_termination_kind=termination_kind,
        parent_statement_id=parent_statement_id,
    )


def make_call_arg(name: str, mode: CallPassingMode, ordinal: int = 1) -> CanonicalCallArgument:
    return CanonicalCallArgument(
        ordinal=ordinal,
        expression=name,
        data_item_name=name,
        qualified_data_item_name=name,
        passing_mode=mode,
        location_kind=LocationKind.UNKNOWN,
    )


def make_call(
    stmt_id: str,
    *,
    called_program_name: str | None = "CALLEE",
    call_target_kind: CallTargetKind = CallTargetKind.LITERAL,
    called_program_expression: str | None = None,
    call_arguments: list[CanonicalCallArgument] | None = None,
    call_returning_data_item: str | None = None,
) -> CanonicalStatement:
    return make_stmt(
        statement_id=stmt_id,
        kind=StatementKind.CALL,
        source_text="CALL",
        call_target_kind=call_target_kind,
        called_program_name=called_program_name,
        called_program_expression=called_program_expression,
        call_arguments=call_arguments or [],
        call_returning_data_item=call_returning_data_item,
    )


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def make_paragraph(name: str, statements: list[CanonicalStatement]) -> CanonicalParagraph:
    return CanonicalParagraph(
        name=name,
        source_text=f"{name}.",
        location_kind=LocationKind.UNKNOWN,
        statements=statements,
        variables_read=_ordered_unique([v for stmt in statements for v in stmt.variables_read]),
        variables_written=_ordered_unique(
            [v for stmt in statements for v in stmt.variables_written]
        ),
    )


def make_program(
    name: str,
    *,
    paragraphs: list[CanonicalParagraph] | None = None,
    data_items: list[CanonicalDataItem] | None = None,
    linkage_data_items: list[CanonicalLinkageDataItem] | None = None,
    entry_parameters: list[CanonicalEntryParameter] | None = None,
    entry_returning_data_item: str | None = None,
) -> CanonicalProgram:
    return CanonicalProgram(
        schema_version="1.2",
        program_name=name,
        source_file=f"01-codigo/cobol/{name}.cbl",
        source_hash=HASH,
        source_package_hash=HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        data_items=data_items or [],
        paragraphs=paragraphs if paragraphs is not None else [make_paragraph("MAIN", [])],
        linkage_data_items=linkage_data_items or [],
        entry_parameters=entry_parameters or [],
        entry_returning_data_item=entry_returning_data_item,
    )


def make_data_item(name: str) -> CanonicalDataItem:
    return CanonicalDataItem(
        name=name, qualified_name=name, level=1, location_kind=LocationKind.UNKNOWN
    )


def make_linkage_item(name: str) -> CanonicalLinkageDataItem:
    return CanonicalLinkageDataItem(
        name=name, qualified_name=name, level=1, pic="X(10)", location_kind=LocationKind.UNKNOWN
    )


def make_entry_param(
    name: str, ordinal: int = 1, mode: CallPassingMode = CallPassingMode.REFERENCE
) -> CanonicalEntryParameter:
    return CanonicalEntryParameter(
        ordinal=ordinal,
        name=name,
        qualified_name=name,
        linkage_item_qualified_name=name,
        passing_mode=mode,
        location_kind=LocationKind.UNKNOWN,
    )


def analyze_all(
    programs: list[CanonicalProgram], *, run_id: str = "run1"
) -> tuple[
    InterproceduralPropagationArtifact,
    SemanticEffectsArtifact,
    SemanticPropagationArtifact,
    InterproceduralCallLinkageArtifact,
]:
    effects = analyze_semantic_effects(
        canonical_programs=programs,
        run_id=run_id,
        source_package_hash=HASH,
        source_artifact_hashes=HASHES,
    )
    propagation = analyze_semantic_propagation(
        canonical_programs=programs,
        semantic_effects=effects,
        run_id=run_id,
        source_package_hash=HASH,
        source_artifact_hashes=HASHES,
    )
    linkage = analyze_interprocedural_call_linkage(
        canonical_programs=programs,
        semantic_effects=effects,
        run_id=run_id,
        source_package_hash=HASH,
        source_artifact_hashes=HASHES,
    )
    interprocedural_propagation = analyze_interprocedural_propagation(
        canonical_programs=programs,
        semantic_effects=effects,
        semantic_propagation=propagation,
        interprocedural_call_linkage=linkage,
        run_id=run_id,
        source_package_hash=HASH,
        source_artifact_hashes=HASHES,
    )
    return interprocedural_propagation, effects, propagation, linkage


def make_semantic_enrichment(
    *, program: CanonicalProgram, qualified_name: str, semantic_tag: str, run_id: str = "run1"
) -> SemanticEnrichmentArtifact:
    """`program_id` usa el mismo formato Neo4j-shaped que
    `identifiers.py::ProgramIdentity.program_id` -- solo el sufijo
    (`program_name`, indice 3 tras dividir por `"::"`) importa para la
    correlacion que hacen los detectores, nunca el resto del ID."""
    program_id = f"program::AR::APP::{program.program_name}::1.0::{HASH[:12]}"
    tag = DataItemSemanticTag(
        data_item_id=f"{program_id}::data::{qualified_name}",
        program_id=program_id,
        original_name=qualified_name,
        qualified_name=qualified_name,
        semantic_tag=semantic_tag,
        semantic_confidence=0.9,
        evidence=[SemanticTagRuleMatch(rule_id="rule-1", tag=semantic_tag, base_confidence=0.9)],
    )
    return SemanticEnrichmentArtifact(
        run_id=run_id,
        source_package_hash=HASH,
        semantic_tags_config_hash=HASH,
        domain_glossary_config_hash=HASH,
        data_item_tags=[tag],
    )


def make_v1_candidates(run_id: str = "run1") -> CandidateArtifact:
    return CandidateArtifact(
        run_id=run_id,
        source_package_hash=HASH,
        semantic_graph_hash=HASH,
        invariants_query_hash=HASH,
        q0_query_hash=HASH,
        candidates=[],
    )


def build_ctx(
    programs: list[CanonicalProgram],
    *,
    semantic_enrichment: SemanticEnrichmentArtifact | None = None,
    run_id: str = "run1",
) -> InterproceduralRuleDetectorContext:
    interprocedural_propagation, effects, propagation, linkage = analyze_all(
        programs, run_id=run_id
    )
    return build_detector_context(
        canonical_programs=programs,
        semantic_effects=effects,
        semantic_propagation=propagation,
        interprocedural_call_linkage=linkage,
        interprocedural_propagation=interprocedural_propagation,
        semantic_enrichment=semantic_enrichment,
    )
