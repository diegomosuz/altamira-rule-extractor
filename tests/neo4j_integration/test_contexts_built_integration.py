"""Integracion real de CONTEXTS_BUILT (Q1-Q7 + ContextPackageBuilder)
contra un servidor Neo4j 5, ejecutando los 9 archivos REALES de
`queries/v1/` (no dobles).

Requiere ALTAMIRA_TEST_NEO4J_URI/_USER/_PASSWORD(/_DATABASE) en el
entorno; se salta descriptivamente si faltan (ver
tests/neo4j_integration/conftest.py).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import neo4j
import pytest

from altamira_extractor.config import Settings
from altamira_extractor.contracts.candidate import CandidateArtifact, RuleCandidate
from altamira_extractor.contracts.context_manifest import ContextDirectoryManifest
from altamira_extractor.contracts.context_package import ContextPackage
from altamira_extractor.contracts.enums import (
    ApplicabilityStatus,
    AttributionScope,
    BatchContextStatus,
    CompletenessStatus,
    InclusionReason,
    NodeLabel,
    PipelineStage,
    RelationshipType,
    StageStatus,
    TableEffectOperation,
)
from altamira_extractor.contracts.run_state import StageExecution
from altamira_extractor.contracts.semantic_graph import GraphNode, GraphRelationship, SemanticGraph
from altamira_extractor.pipeline.contexts_built_stage import run_contexts_built_stage
from altamira_extractor.pipeline.errors import ContextBuildError
from altamira_extractor.pipeline.neo4j_repository import Neo4jRepository

pytestmark = [pytest.mark.integration, pytest.mark.neo4j_integration]

_HASH_A = "a" * 64
_RUN_ID = "run-ctx-1"
_PROGRAM_ID = "program::AR::OP1::PROG1::1::abc123456789"
_MAIN_ID = f"{_PROGRAM_ID}::paragraph::MAIN"
_UPSTREAM_ID = f"{_PROGRAM_ID}::paragraph::UPSTREAM"
_CTRL_ID = f"{_PROGRAM_ID}::paragraph::CTRL"
_BOTH_ID = f"{_PROGRAM_ID}::paragraph::BOTHDEP"
_OTHER_ID = f"{_PROGRAM_ID}::paragraph::OTHER"
_DECISION_ID = f"{_MAIN_ID}::decision::10::1"
_RETURN_CODE_DATA_ITEM_ID = f"{_PROGRAM_ID}::data::WS-COD-RESULT"
_PARM_TABLE_ID = "table::AR::default::PARM01"
_TX_TABLE_ID = "table::AR::default::TABLA_TX"
_CUENTAS_ID = "table::AR::default::CUENTAS"
_OTRA_TABLA_ID = "table::AR::default::OTRA_TABLA"
_LOG_TABLE_ID = "table::AR::default::LOG_TABLE"
_DOMAIN_TERM_ID = "term::1.0::result_code"


def _canonical_props(props: dict[str, object]) -> str:
    return json.dumps(props, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _node(node_id: str, labels: list[NodeLabel], properties: dict[str, object]) -> GraphNode:
    return GraphNode(id=node_id, labels=labels, properties=properties)


def _rel(
    rel_type: RelationshipType,
    from_id: str,
    to_id: str,
    properties: dict[str, object] | None = None,
) -> GraphRelationship:
    return GraphRelationship(
        type=rel_type, from_id=from_id, to_id=to_id, properties=properties or {}
    )


def _build_graph(
    *,
    source_package_hash: str = _HASH_A,
    main_predicate_text: str | None = "COD = '01'",
    include_domain_term: bool = True,
) -> SemanticGraph:
    nodes = [
        _node(
            "country::AR",
            [NodeLabel.COUNTRY],
            {"code": "AR", "source_package_hash": source_package_hash},
        ),
        _node(
            "application::AR::TRF",
            [NodeLabel.APPLICATION],
            {"name": "Transferencias", "source_package_hash": source_package_hash},
        ),
        _node(
            "operation::AR::TRF::OP1",
            [NodeLabel.OPERATION],
            {
                "logical_name": "OP1",
                "description": "Operacion de prueba",
                "source_package_hash": source_package_hash,
            },
        ),
        _node(
            _PROGRAM_ID,
            [NodeLabel.PROGRAM],
            {
                "name": "PROG1",
                "version": "1",
                "source_file": "01-codigo/cobol/PROG1.cbl",
                "source_package_hash": source_package_hash,
            },
        ),
        _node(
            _MAIN_ID,
            [NodeLabel.PARAGRAPH],
            {
                "name": "MAIN",
                "source_file": "01-codigo/cobol/PROG1.cbl",
                "source_text": "IF WS-COD-RESULT = 'R001'",
                "line_start": 10,
                "line_end": 12,
                "source_package_hash": source_package_hash,
            },
        ),
        _node(
            _UPSTREAM_ID,
            [NodeLabel.PARAGRAPH],
            {
                "name": "UPSTREAM",
                "source_file": "01-codigo/cobol/PROG1.cbl",
                "source_text": "MOVE 1 TO WS-FLAG",
                "line_start": 20,
                "line_end": 21,
                "source_package_hash": source_package_hash,
            },
        ),
        _node(
            _CTRL_ID,
            [NodeLabel.PARAGRAPH],
            {
                "name": "CTRL",
                "source_file": "01-codigo/cobol/PROG1.cbl",
                "source_text": "PERFORM MAIN",
                "line_start": 30,
                "line_end": 31,
                "source_package_hash": source_package_hash,
            },
        ),
        _node(
            _BOTH_ID,
            [NodeLabel.PARAGRAPH],
            {
                "name": "BOTHDEP",
                "source_file": "01-codigo/cobol/PROG1.cbl",
                "source_text": "PERFORM CTRL",
                "line_start": 40,
                "line_end": 41,
                "source_package_hash": source_package_hash,
            },
        ),
        _node(
            _OTHER_ID,
            [NodeLabel.PARAGRAPH],
            {
                "name": "OTHER",
                "source_file": "01-codigo/cobol/PROG1.cbl",
                "source_text": "INSERT INTO LOG_TABLE",
                "line_start": 50,
                "line_end": 51,
                "source_package_hash": source_package_hash,
            },
        ),
        _node(
            _DECISION_ID,
            [NodeLabel.DECISION],
            {
                "expression": "WS-COD-RESULT = 'R001'",
                "normalized_expression": "WS-COD-RESULT = 'R001'",
                "operands_json": json.dumps(["WS-COD-RESULT"]),
                "rule_type": None,
                "outcome_code": "R001",
                "line_start": 10,
                "line_end": 11,
                "source_package_hash": source_package_hash,
            },
        ),
        _node(
            _RETURN_CODE_DATA_ITEM_ID,
            [NodeLabel.DATA_ITEM],
            {
                "name": "WS-COD-RESULT",
                "semantic_tag": "return_code",
                "source_package_hash": source_package_hash,
            },
        ),
        _node(
            _PARM_TABLE_ID,
            [NodeLabel.TABLE, NodeLabel.PARAMETER_TABLE],
            {
                "name": "PARM01",
                "snapshot_date": "2026-01-01",
                "source_package_hash": source_package_hash,
            },
        ),
        _node(
            f"{_PARM_TABLE_ID}::entry::1::aaaaaaaaaaaa",
            [NodeLabel.PARAMETER_ENTRY],
            {
                "id": f"{_PARM_TABLE_ID}::entry::1::aaaaaaaaaaaa",
                "row_number": 1,
                "row_hash": "aaaaaaaaaaaa",
                "raw_row_json": json.dumps({"COD": "01", "LIMITE": "1000"}),
                "normalized_row_json": json.dumps({"COD": "01", "LIMITE": 1000}),
                "source_package_hash": source_package_hash,
            },
        ),
        _node(
            f"{_PARM_TABLE_ID}::entry::2::bbbbbbbbbbbb",
            [NodeLabel.PARAMETER_ENTRY],
            {
                "id": f"{_PARM_TABLE_ID}::entry::2::bbbbbbbbbbbb",
                "row_number": 2,
                "row_hash": "bbbbbbbbbbbb",
                "raw_row_json": json.dumps({"COD": "02", "LIMITE": "2000"}),
                "normalized_row_json": json.dumps({"COD": "02", "LIMITE": 2000}),
                "source_package_hash": source_package_hash,
            },
        ),
        _node(
            _TX_TABLE_ID,
            [NodeLabel.TABLE],
            {"name": "TABLA_TX", "source_package_hash": source_package_hash},
        ),
        _node(
            _CUENTAS_ID,
            [NodeLabel.TABLE],
            {"name": "CUENTAS", "source_package_hash": source_package_hash},
        ),
        _node(
            _OTRA_TABLA_ID,
            [NodeLabel.TABLE],
            {"name": "OTRA_TABLA", "source_package_hash": source_package_hash},
        ),
        _node(
            _LOG_TABLE_ID,
            [NodeLabel.TABLE],
            {"name": "LOG_TABLE", "source_package_hash": source_package_hash},
        ),
        _node(
            "batch::AR::CTRLM::JOB1",
            [NodeLabel.BATCH_JOB],
            {
                "name": "JOB1BATCH",
                "schedule": "0 2 * * *",
                "source_package_hash": source_package_hash,
            },
        ),
    ]

    if include_domain_term:
        nodes.append(
            _node(
                _DOMAIN_TERM_ID,
                [NodeLabel.DOMAIN_TERM],
                {
                    "functional_name": "codigo de resultado",
                    "definition": "Codigo de resultado de la transferencia",
                    "entity_type": "status_code",
                    "authoritative_source": "V1 controlled glossary",
                    "source_kind": "CURATED_CONFIG",
                    "catalog_version": "1.0",
                    "confidence": 0.95,
                    "source_package_hash": source_package_hash,
                },
            )
        )

    relationships = [
        _rel(RelationshipType.HAS_APPLICATION, "country::AR", "application::AR::TRF"),
        _rel(RelationshipType.HAS_OPERATION, "application::AR::TRF", "operation::AR::TRF::OP1"),
        _rel(RelationshipType.EXECUTES_VIA, "operation::AR::TRF::OP1", _PROGRAM_ID),
        _rel(RelationshipType.CONTAINS, _PROGRAM_ID, _MAIN_ID),
        _rel(RelationshipType.CONTAINS, _PROGRAM_ID, _UPSTREAM_ID),
        _rel(RelationshipType.CONTAINS, _PROGRAM_ID, _CTRL_ID),
        _rel(RelationshipType.CONTAINS, _PROGRAM_ID, _BOTH_ID),
        _rel(RelationshipType.CONTAINS, _PROGRAM_ID, _OTHER_ID),
        _rel(RelationshipType.HAS_DECISION, _MAIN_ID, _DECISION_ID),
        _rel(RelationshipType.LEADS_TO, _DECISION_ID, _RETURN_CODE_DATA_ITEM_ID),
        _rel(RelationshipType.USES, _MAIN_ID, _RETURN_CODE_DATA_ITEM_ID),
        _rel(RelationshipType.DATA_DEPENDS_ON, _UPSTREAM_ID, _MAIN_ID),
        _rel(RelationshipType.CONTROL_DEPENDS_ON, _CTRL_ID, _MAIN_ID),
        _rel(RelationshipType.DATA_DEPENDS_ON, _BOTH_ID, _MAIN_ID),
        _rel(RelationshipType.CONTROL_DEPENDS_ON, _BOTH_ID, _MAIN_ID),
        _rel(
            RelationshipType.READS,
            _MAIN_ID,
            _PARM_TABLE_ID,
            {
                "predicate_text": main_predicate_text,
                "host_variables_json": None,
                "source_file": "01-codigo/cobol/PROG1.cbl",
                "line_start": 11,
                "line_end": 11,
            },
        ),
        _rel(
            RelationshipType.HAS_ENTRY,
            _PARM_TABLE_ID,
            f"{_PARM_TABLE_ID}::entry::1::aaaaaaaaaaaa",
        ),
        _rel(
            RelationshipType.HAS_ENTRY,
            _PARM_TABLE_ID,
            f"{_PARM_TABLE_ID}::entry::2::bbbbbbbbbbbb",
        ),
        _rel(
            RelationshipType.READS,
            _MAIN_ID,
            _TX_TABLE_ID,
            {
                "source_file": "01-codigo/cobol/PROG1.cbl",
                "line_start": 13,
                "line_end": 13,
            },
        ),
        _rel(
            RelationshipType.WRITES,
            _MAIN_ID,
            _CUENTAS_ID,
            {"source_file": "01-codigo/cobol/PROG1.cbl", "line_start": 14, "line_end": 14},
        ),
        _rel(
            RelationshipType.UPDATES,
            _UPSTREAM_ID,
            _OTRA_TABLA_ID,
            {"source_file": "01-codigo/cobol/PROG1.cbl", "line_start": 22, "line_end": 22},
        ),
        _rel(
            RelationshipType.INSERTS,
            _OTHER_ID,
            _LOG_TABLE_ID,
            {"source_file": "01-codigo/cobol/PROG1.cbl", "line_start": 51, "line_end": 51},
        ),
        _rel(RelationshipType.READS, "batch::AR::CTRLM::JOB1", _CUENTAS_ID),
    ]
    if include_domain_term:
        relationships.append(
            _rel(
                RelationshipType.HAS_DOMAIN_TERM,
                _RETURN_CODE_DATA_ITEM_ID,
                _DOMAIN_TERM_ID,
                {"confidence": 0.95},
            )
        )

    nodes.sort(key=lambda n: n.id)
    relationships.sort(
        key=lambda r: (r.type.value, r.from_id, r.to_id, _canonical_props(r.properties))
    )
    return SemanticGraph(
        source_package_hash=source_package_hash, nodes=nodes, relationships=relationships
    )


def _stage(stage: PipelineStage, status: StageStatus) -> StageExecution:
    now = datetime.now(UTC)
    return StageExecution(stage=stage, status=status, started_at=now, finished_at=now)


def _load_graph_into_neo4j(
    tmp_path: Path, graph: SemanticGraph, repository: Neo4jRepository
) -> tuple[Path, str]:
    graph_path = tmp_path / "04-semantic-graph.json"
    graph_path.write_text(graph.model_dump_json(), encoding="utf-8")
    semantic_graph_hash = hashlib.sha256(graph_path.read_bytes()).hexdigest()
    repository.ensure_schema()
    repository.load_graph(graph, semantic_graph_hash=semantic_graph_hash, server_version="5.24.0")
    return graph_path, semantic_graph_hash


def _write_candidates(
    tmp_path: Path,
    *,
    graph: SemanticGraph,
    semantic_graph_hash: str,
    candidates: list[RuleCandidate],
) -> Path:
    candidates_path = tmp_path / "06-candidates.json"
    artifact = CandidateArtifact(
        run_id=_RUN_ID,
        source_package_hash=graph.source_package_hash,
        semantic_graph_hash=semantic_graph_hash,
        invariants_query_hash="c" * 64,
        q0_query_hash="d" * 64,
        candidates=candidates,
        warnings=[],
    )
    candidates_path.write_text(artifact.model_dump_json(), encoding="utf-8")
    return candidates_path


def _main_candidate(source_package_hash: str = _HASH_A) -> RuleCandidate:
    return RuleCandidate(
        candidate_id=f"candidate::q0-return-code-decision::1.0::{source_package_hash}::{_DECISION_ID}",
        paragraph_id=_MAIN_ID,
        paragraph_name="MAIN",
        decision_id=_DECISION_ID,
        detector_id="q0-return-code-decision",
        detector_version="1.0",
        detector_score=1.0,
        condition="WS-COD-RESULT = 'R001'",
        outcome_code="R001",
        rule_type=None,
        line_start=10,
        source_file="01-codigo/cobol/PROG1.cbl",
        source_package_hash=source_package_hash,
    )


def _run_stage(
    *,
    graph: SemanticGraph,
    graph_path: Path,
    candidates_path: Path,
    context_dir: Path,
    settings: Settings,
) -> list[str]:
    return run_contexts_built_stage(
        run_id=_RUN_ID,
        source_package_hash=graph.source_package_hash,
        run_stages=[_stage(PipelineStage.CANDIDATES_DETECTED, StageStatus.SUCCEEDED)],
        semantic_graph_path=graph_path,
        candidates_path=candidates_path,
        context_dir=context_dir,
        settings=settings,
    )


def test_full_context_package_with_real_q1_to_q7(
    neo4j_test_settings: Settings, clean_managed_graph: None, tmp_path: Path
) -> None:
    graph = _build_graph()
    repository = Neo4jRepository.connect(neo4j_test_settings)
    try:
        graph_path, semantic_graph_hash = _load_graph_into_neo4j(tmp_path, graph, repository)
    finally:
        repository.close()

    candidates_path = _write_candidates(
        tmp_path,
        graph=graph,
        semantic_graph_hash=semantic_graph_hash,
        candidates=[_main_candidate()],
    )
    context_dir = tmp_path / "07-context"

    warnings = _run_stage(
        graph=graph,
        graph_path=graph_path,
        candidates_path=candidates_path,
        context_dir=context_dir,
        settings=neo4j_test_settings,
    )

    assert warnings == ["1 contexto(s)"]
    manifest = ContextDirectoryManifest.model_validate_json(
        (context_dir / "context-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest.context_count == 1
    assert len(manifest.query_records) == 9
    assert {record.logical_query for record in manifest.query_records} == {
        "Q1", "Q2", "Q3A", "Q3B", "Q4", "Q5A", "Q5B", "Q6", "Q7",
    }
    record = manifest.context_records[0]
    package = ContextPackage.model_validate_json(
        (context_dir / record.relative_filename).read_text(encoding="utf-8")
    )

    # D1 scope
    assert package.scope.country == "AR"
    assert package.scope.application == "Transferencias"
    assert package.scope.program == "PROG1"
    assert package.scope.paragraph == "MAIN"

    # D2 code slice: InclusionReason topologico real
    by_paragraph = {entry.paragraph: entry.inclusion_reason for entry in package.code_slice}
    assert by_paragraph["MAIN"] == InclusionReason.CANDIDATE
    assert by_paragraph["UPSTREAM"] == InclusionReason.DATA_DEPENDENCY
    assert by_paragraph["CTRL"] == InclusionReason.CONTROL_DEPENDENCY
    assert by_paragraph["BOTHDEP"] == InclusionReason.BOTH
    assert "OTHER" not in by_paragraph

    # D3 data context: EXACT con fila aplicable + fila de contexto
    parm = next(t for t in package.data_context.parameter_tables if t.name == "PARM01")
    assert parm.applicability_status == ApplicabilityStatus.EXACT
    assert len(parm.applicable_rows) == 1
    assert parm.applicable_rows[0].values["COD"] == "01"
    assert len(parm.context_rows) == 1
    assert parm.context_rows[0].values["COD"] == "02"
    tx_tables = {t.name for t in package.data_context.transactional_tables_read}
    assert tx_tables == {"TABLA_TX"}

    # D4 decision
    assert package.decision.outcome_code == "R001"
    assert package.decision.rule_type is None
    assert package.decision.operands == ["WS-COD-RESULT"]

    # D5 effects
    assert [rc.code for rc in package.effects.return_codes] == ["R001"]
    effects_by_scope = {
        (effect.table, effect.attribution_scope): effect for effect in package.effects.table_effects
    }
    direct = effects_by_scope[("CUENTAS", AttributionScope.DIRECT)]
    assert direct.operation == TableEffectOperation.WRITES
    assert direct.approved_for_rule_text is True
    dep_slice = effects_by_scope[("OTRA_TABLA", AttributionScope.DEPENDENCY_SLICE)]
    assert dep_slice.approved_for_rule_text is True
    program_context = effects_by_scope[("LOG_TABLE", AttributionScope.PROGRAM_CONTEXT)]
    assert program_context.approved_for_rule_text is False

    # D6 batch
    assert package.batch_context.status == BatchContextStatus.COMPLETE
    assert len(package.batch_context.downstream_jobs) == 1
    assert package.batch_context.downstream_jobs[0]["job_name"] == "JOB1BATCH"

    # D7 domain glossary
    assert len(package.domain_glossary) == 1
    glossary_entry = package.domain_glossary[0]
    assert glossary_entry.data_item_id == _RETURN_CODE_DATA_ITEM_ID
    assert glossary_entry.domain_term_id == _DOMAIN_TERM_ID

    # completeness
    assert package.completeness.D3 == CompletenessStatus.COMPLETE
    assert package.completeness.D6 == CompletenessStatus.COMPLETE
    assert package.completeness.D7 == CompletenessStatus.COMPLETE


def test_parameter_table_without_predicate_is_unresolved(
    neo4j_test_settings: Settings, clean_managed_graph: None, tmp_path: Path
) -> None:
    graph = _build_graph(main_predicate_text=None)
    repository = Neo4jRepository.connect(neo4j_test_settings)
    try:
        graph_path, semantic_graph_hash = _load_graph_into_neo4j(tmp_path, graph, repository)
    finally:
        repository.close()

    candidates_path = _write_candidates(
        tmp_path,
        graph=graph,
        semantic_graph_hash=semantic_graph_hash,
        candidates=[_main_candidate()],
    )
    context_dir = tmp_path / "07-context"

    _run_stage(
        graph=graph,
        graph_path=graph_path,
        candidates_path=candidates_path,
        context_dir=context_dir,
        settings=neo4j_test_settings,
    )

    manifest = ContextDirectoryManifest.model_validate_json(
        (context_dir / "context-manifest.json").read_text(encoding="utf-8")
    )
    package = ContextPackage.model_validate_json(
        (context_dir / manifest.context_records[0].relative_filename).read_text(encoding="utf-8")
    )
    parm = next(t for t in package.data_context.parameter_tables if t.name == "PARM01")
    assert parm.applicability_status == ApplicabilityStatus.UNRESOLVED
    assert parm.applicable_rows == []
    assert len(parm.context_rows) == 2


def test_no_domain_term_produces_empty_glossary_and_d7_not_available(
    neo4j_test_settings: Settings, clean_managed_graph: None, tmp_path: Path
) -> None:
    graph = _build_graph(include_domain_term=False)
    repository = Neo4jRepository.connect(neo4j_test_settings)
    try:
        graph_path, semantic_graph_hash = _load_graph_into_neo4j(tmp_path, graph, repository)
    finally:
        repository.close()

    candidates_path = _write_candidates(
        tmp_path,
        graph=graph,
        semantic_graph_hash=semantic_graph_hash,
        candidates=[_main_candidate()],
    )
    context_dir = tmp_path / "07-context"

    warnings = _run_stage(
        graph=graph,
        graph_path=graph_path,
        candidates_path=candidates_path,
        context_dir=context_dir,
        settings=neo4j_test_settings,
    )

    manifest = ContextDirectoryManifest.model_validate_json(
        (context_dir / "context-manifest.json").read_text(encoding="utf-8")
    )
    package = ContextPackage.model_validate_json(
        (context_dir / manifest.context_records[0].relative_filename).read_text(encoding="utf-8")
    )
    assert package.domain_glossary == []
    assert package.completeness.D7 == CompletenessStatus.NOT_AVAILABLE
    assert warnings == ["1 contexto(s)"]


def test_drift_after_load_blocks_context_build(
    neo4j_driver: neo4j.Driver,
    neo4j_test_settings: Settings,
    clean_managed_graph: None,
    tmp_path: Path,
) -> None:
    graph = _build_graph()
    repository = Neo4jRepository.connect(neo4j_test_settings)
    try:
        graph_path, semantic_graph_hash = _load_graph_into_neo4j(tmp_path, graph, repository)
    finally:
        repository.close()

    candidates_path = _write_candidates(
        tmp_path,
        graph=graph,
        semantic_graph_hash=semantic_graph_hash,
        candidates=[_main_candidate()],
    )
    context_dir = tmp_path / "07-context"

    with neo4j_driver.session(database=neo4j_test_settings.neo4j_database) as session:
        session.run(
            "MATCH (n:Paragraph {name: 'OTHER'}) DETACH DELETE n"
        ).consume()

    with pytest.raises(ContextBuildError):
        _run_stage(
            graph=graph,
            graph_path=graph_path,
            candidates_path=candidates_path,
            context_dir=context_dir,
            settings=neo4j_test_settings,
        )
    assert not context_dir.exists()


def test_second_run_with_no_changes_does_not_rewrite(
    neo4j_test_settings: Settings, clean_managed_graph: None, tmp_path: Path
) -> None:
    graph = _build_graph()
    repository = Neo4jRepository.connect(neo4j_test_settings)
    try:
        graph_path, semantic_graph_hash = _load_graph_into_neo4j(tmp_path, graph, repository)
    finally:
        repository.close()

    candidates_path = _write_candidates(
        tmp_path,
        graph=graph,
        semantic_graph_hash=semantic_graph_hash,
        candidates=[_main_candidate()],
    )
    context_dir = tmp_path / "07-context"

    _run_stage(
        graph=graph,
        graph_path=graph_path,
        candidates_path=candidates_path,
        context_dir=context_dir,
        settings=neo4j_test_settings,
    )
    manifest_path = context_dir / "context-manifest.json"
    first_bytes = manifest_path.read_bytes()

    second_warnings = _run_stage(
        graph=graph,
        graph_path=graph_path,
        candidates_path=candidates_path,
        context_dir=context_dir,
        settings=neo4j_test_settings,
    )

    assert second_warnings == ["1 contexto(s) (sin cambios)"]
    assert manifest_path.read_bytes() == first_bytes


def test_empty_candidates_produces_empty_manifest_without_running_queries(
    neo4j_test_settings: Settings, clean_managed_graph: None, tmp_path: Path
) -> None:
    graph = _build_graph()
    repository = Neo4jRepository.connect(neo4j_test_settings)
    try:
        graph_path, semantic_graph_hash = _load_graph_into_neo4j(tmp_path, graph, repository)
    finally:
        repository.close()

    candidates_path = _write_candidates(
        tmp_path, graph=graph, semantic_graph_hash=semantic_graph_hash, candidates=[]
    )
    context_dir = tmp_path / "07-context"

    warnings = _run_stage(
        graph=graph,
        graph_path=graph_path,
        candidates_path=candidates_path,
        context_dir=context_dir,
        settings=neo4j_test_settings,
    )

    assert warnings == ["0 contexto(s)"]
    manifest = ContextDirectoryManifest.model_validate_json(
        (context_dir / "context-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest.context_count == 0
    assert manifest.context_records == []
    assert list(context_dir.glob("*.json")) == [context_dir / "context-manifest.json"]
