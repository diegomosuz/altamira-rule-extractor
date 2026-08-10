"""Integracion real de CANDIDATES_DETECTED (CandidateDetector + Q0)
contra un servidor Neo4j 5, ejecutando el `queries/v1/q0_candidates.cypher`
REAL del repositorio (no un doble).

Requiere ALTAMIRA_TEST_NEO4J_URI/_USER/_PASSWORD(/_DATABASE) en el
entorno; se salta descriptivamente si faltan.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import neo4j
import pytest

from altamira_extractor.config import Settings
from altamira_extractor.contracts.candidate import CandidateArtifact
from altamira_extractor.contracts.candidate_promotion_assessment import (
    CandidateSource,
    UnifiedRuleFamily,
)
from altamira_extractor.contracts.canonical import (
    CanonicalConditionName,
    CanonicalConditionValue,
    CanonicalDataItem,
    CanonicalParagraph,
    CanonicalProgram,
    CanonicalStatement,
)
from altamira_extractor.contracts.enums import (
    LocationKind,
    NodeLabel,
    PipelineStage,
    RelationshipType,
    SourceFormat,
    StageStatus,
    StatementKind,
)
from altamira_extractor.contracts.invariants import InvariantArtifact
from altamira_extractor.contracts.run_state import StageExecution
from altamira_extractor.contracts.semantic_enrichment import SemanticEnrichmentArtifact
from altamira_extractor.contracts.semantic_graph import GraphNode, GraphRelationship, SemanticGraph
from altamira_extractor.pipeline.candidates_detected_stage import run_candidates_detected_stage
from altamira_extractor.pipeline.errors import CandidateDetectionError
from altamira_extractor.pipeline.neo4j_repository import Neo4jRepository

pytestmark = [pytest.mark.integration, pytest.mark.neo4j_integration]

_HASH_A = "a" * 64
_RUN_ID = "run-1"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SEMANTIC_TAGS_PATH = _REPO_ROOT / "config" / "semantic-tags.yml"


def _semantic_tags_config_hash() -> str:
    return hashlib.sha256(_SEMANTIC_TAGS_PATH.read_bytes()).hexdigest()


def _base_graph_nodes(
    source_package_hash: str,
) -> tuple[GraphNode, GraphNode, GraphNode, GraphNode]:
    country = GraphNode(id="country::AR", labels=[NodeLabel.COUNTRY], properties={})
    application = GraphNode(
        id="application::AR::TRF", labels=[NodeLabel.APPLICATION], properties={}
    )
    operation = GraphNode(id="operation::AR::TRF::OP1", labels=[NodeLabel.OPERATION], properties={})
    program = GraphNode(
        id="program::AR::OP1::PROG1::1::abcdef123456",
        labels=[NodeLabel.PROGRAM],
        properties={"source_package_hash": source_package_hash},
    )
    return country, application, operation, program


def _graph_with_candidates(
    *, decision_count: int, source_package_hash: str = _HASH_A
) -> SemanticGraph:
    country, application, operation, program = _base_graph_nodes(source_package_hash)
    paragraph = GraphNode(
        id=f"{program.id}::paragraph::MAIN",
        labels=[NodeLabel.PARAGRAPH],
        properties={
            "name": "MAIN",
            "source_file": "01-codigo/cobol/PROG1.cbl",
            "line_start": 10,
            "source_package_hash": source_package_hash,
        },
    )
    nodes = [country, application, operation, program, paragraph]
    relationships = [
        GraphRelationship(
            type=RelationshipType.HAS_APPLICATION,
            from_id=country.id,
            to_id=application.id,
            properties={},
        ),
        GraphRelationship(
            type=RelationshipType.HAS_OPERATION,
            from_id=application.id,
            to_id=operation.id,
            properties={},
        ),
        GraphRelationship(
            type=RelationshipType.EXECUTES_VIA,
            from_id=operation.id,
            to_id=program.id,
            properties={},
        ),
        GraphRelationship(
            type=RelationshipType.CONTAINS, from_id=program.id, to_id=paragraph.id, properties={}
        ),
    ]
    for i in range(decision_count):
        decision = GraphNode(
            id=f"{paragraph.id}::decision::10::{i + 1}",
            labels=[NodeLabel.DECISION],
            properties={
                "expression": f"WS-COD-RESULT = 'R00{i + 1}'",
                "outcome_code": f"R00{i + 1}",
                "rule_type": None,
                "source_package_hash": source_package_hash,
            },
        )
        data_item = GraphNode(
            id=f"{program.id}::data::WS-COD-RESULT-{i + 1}",
            labels=[NodeLabel.DATA_ITEM],
            properties={"semantic_tag": "return_code", "source_package_hash": source_package_hash},
        )
        nodes.extend([decision, data_item])
        relationships.extend(
            [
                GraphRelationship(
                    type=RelationshipType.HAS_DECISION,
                    from_id=paragraph.id,
                    to_id=decision.id,
                    properties={},
                ),
                GraphRelationship(
                    type=RelationshipType.LEADS_TO,
                    from_id=decision.id,
                    to_id=data_item.id,
                    properties={},
                ),
            ]
        )

    nodes.sort(key=lambda n: n.id)
    relationships.sort(key=lambda r: (r.type.value, r.from_id, r.to_id, "{}"))
    return SemanticGraph(
        source_package_hash=source_package_hash, nodes=nodes, relationships=relationships
    )


def _graph_without_return_code_tag(source_package_hash: str = _HASH_A) -> SemanticGraph:
    country, application, operation, program = _base_graph_nodes(source_package_hash)
    paragraph = GraphNode(
        id=f"{program.id}::paragraph::MAIN",
        labels=[NodeLabel.PARAGRAPH],
        properties={
            "name": "MAIN",
            "source_file": "01-codigo/cobol/PROG1.cbl",
            "line_start": 10,
            "source_package_hash": source_package_hash,
        },
    )
    decision = GraphNode(
        id=f"{paragraph.id}::decision::10::1",
        labels=[NodeLabel.DECISION],
        properties={
            "expression": "WS-MONTO > 0",
            "outcome_code": None,
            "rule_type": None,
            "source_package_hash": source_package_hash,
        },
    )
    data_item = GraphNode(
        id=f"{program.id}::data::WS-MONTO",
        labels=[NodeLabel.DATA_ITEM],
        properties={"semantic_tag": "amount", "source_package_hash": source_package_hash},
    )
    nodes = sorted(
        [country, application, operation, program, paragraph, decision, data_item],
        key=lambda n: n.id,
    )
    relationships = [
        GraphRelationship(
            type=RelationshipType.HAS_APPLICATION,
            from_id=country.id,
            to_id=application.id,
            properties={},
        ),
        GraphRelationship(
            type=RelationshipType.HAS_OPERATION,
            from_id=application.id,
            to_id=operation.id,
            properties={},
        ),
        GraphRelationship(
            type=RelationshipType.EXECUTES_VIA,
            from_id=operation.id,
            to_id=program.id,
            properties={},
        ),
        GraphRelationship(
            type=RelationshipType.CONTAINS, from_id=program.id, to_id=paragraph.id, properties={}
        ),
        GraphRelationship(
            type=RelationshipType.HAS_DECISION,
            from_id=paragraph.id,
            to_id=decision.id,
            properties={},
        ),
        GraphRelationship(
            type=RelationshipType.LEADS_TO, from_id=decision.id, to_id=data_item.id, properties={}
        ),
    ]
    relationships.sort(key=lambda r: (r.type.value, r.from_id, r.to_id, "{}"))
    return SemanticGraph(
        source_package_hash=source_package_hash, nodes=nodes, relationships=relationships
    )


def _stage(stage: PipelineStage, status: StageStatus) -> StageExecution:
    now = datetime.now(UTC)
    return StageExecution(stage=stage, status=status, started_at=now, finished_at=now)


def _prepare_artifacts(
    tmp_path: Path, graph: SemanticGraph, repository: Neo4jRepository
) -> tuple[Path, Path, Path, Path, Path]:
    graph_path = tmp_path / "04-semantic-graph.json"
    graph_path.write_text(graph.model_dump_json(), encoding="utf-8")
    semantic_graph_hash = hashlib.sha256(graph_path.read_bytes()).hexdigest()

    repository.ensure_schema()
    result = repository.load_graph(
        graph, semantic_graph_hash=semantic_graph_hash, server_version="5.24.0"
    )

    invariants_path = tmp_path / "05-invariants.json"
    invariants_path.write_text(
        InvariantArtifact(
            run_id=_RUN_ID,
            source_package_hash=graph.source_package_hash,
            semantic_graph_hash=semantic_graph_hash,
            invariants_query_hash="c" * 64,
            neo4j_database=result.database,
            neo4j_server_version=result.server_version,
            node_count=result.node_count,
            relationship_count=result.relationship_count,
            violations=[],
            error_count=0,
            warning_count=0,
            graph_validated=True,
            warnings=[],
        ).model_dump_json(),
        encoding="utf-8",
    )

    enrichment_path = tmp_path / "03b-semantic-enrichment.json"
    enrichment_path.write_text(
        SemanticEnrichmentArtifact(
            run_id=_RUN_ID,
            source_package_hash=graph.source_package_hash,
            semantic_tags_config_hash=_semantic_tags_config_hash(),
            domain_glossary_config_hash="d" * 64,
        ).model_dump_json(),
        encoding="utf-8",
    )

    candidates_path = tmp_path / "06-candidates.json"
    return graph_path, invariants_path, enrichment_path, candidates_path, graph_path


def _run_stage(
    *,
    graph: SemanticGraph,
    graph_path: Path,
    enrichment_path: Path,
    invariants_path: Path,
    candidates_path: Path,
    settings: Settings,
    canonical_dir: Path | None = None,
) -> list[str]:
    return run_candidates_detected_stage(
        run_id=_RUN_ID,
        source_package_hash=graph.source_package_hash,
        run_stages=[_stage(PipelineStage.GRAPH_VALIDATED, StageStatus.SUCCEEDED)],
        semantic_graph_path=graph_path,
        semantic_enrichment_path=enrichment_path,
        invariants_path=invariants_path,
        q0_cypher_path=settings.q0_candidates_cypher_path,
        candidates_path=candidates_path,
        canonical_dir=canonical_dir or (candidates_path.parent / "02-canonical"),
        settings=settings,
    )


def test_q0_detects_a_real_candidate(
    neo4j_test_settings: Settings, clean_managed_graph: None, tmp_path: Path
) -> None:
    graph = _graph_with_candidates(decision_count=1)
    repository = Neo4jRepository.connect(neo4j_test_settings)
    try:
        graph_path, invariants_path, enrichment_path, candidates_path, _ = _prepare_artifacts(
            tmp_path, graph, repository
        )
    finally:
        repository.close()

    warnings = _run_stage(
        graph=graph,
        graph_path=graph_path,
        enrichment_path=enrichment_path,
        invariants_path=invariants_path,
        candidates_path=candidates_path,
        settings=neo4j_test_settings,
    )

    assert warnings == ["detectados 1 candidato(s)"]
    artifact = CandidateArtifact.model_validate_json(candidates_path.read_text(encoding="utf-8"))
    assert len(artifact.candidates) == 1
    candidate = artifact.candidates[0]
    assert candidate.paragraph_name == "MAIN"
    assert candidate.outcome_code == "R001"
    assert candidate.rule_type is None
    assert candidate.source_file == "01-codigo/cobol/PROG1.cbl"
    assert candidate.line_start == 10


def test_data_item_without_return_code_tag_produces_no_candidate(
    neo4j_test_settings: Settings, clean_managed_graph: None, tmp_path: Path
) -> None:
    graph = _graph_without_return_code_tag()
    repository = Neo4jRepository.connect(neo4j_test_settings)
    try:
        graph_path, invariants_path, enrichment_path, candidates_path, _ = _prepare_artifacts(
            tmp_path, graph, repository
        )
    finally:
        repository.close()

    warnings = _run_stage(
        graph=graph,
        graph_path=graph_path,
        enrichment_path=enrichment_path,
        invariants_path=invariants_path,
        candidates_path=candidates_path,
        settings=neo4j_test_settings,
    )

    assert warnings == ["detectados 0 candidato(s)"]
    artifact = CandidateArtifact.model_validate_json(candidates_path.read_text(encoding="utf-8"))
    assert artifact.candidates == []


def test_multiple_decisions_produce_deterministic_candidates(
    neo4j_test_settings: Settings, clean_managed_graph: None, tmp_path: Path
) -> None:
    graph = _graph_with_candidates(decision_count=2)
    repository = Neo4jRepository.connect(neo4j_test_settings)
    try:
        graph_path, invariants_path, enrichment_path, candidates_path, _ = _prepare_artifacts(
            tmp_path, graph, repository
        )
    finally:
        repository.close()

    warnings = _run_stage(
        graph=graph,
        graph_path=graph_path,
        enrichment_path=enrichment_path,
        invariants_path=invariants_path,
        candidates_path=candidates_path,
        settings=neo4j_test_settings,
    )

    assert warnings == ["detectados 2 candidato(s)"]
    artifact = CandidateArtifact.model_validate_json(candidates_path.read_text(encoding="utf-8"))
    ids = [c.candidate_id for c in artifact.candidates]
    assert ids == sorted(ids)
    assert len(set(ids)) == 2


def test_drift_after_load_blocks_candidate_detection(
    neo4j_driver: neo4j.Driver,
    neo4j_test_settings: Settings,
    clean_managed_graph: None,
    tmp_path: Path,
) -> None:
    graph = _graph_with_candidates(decision_count=1)
    repository = Neo4jRepository.connect(neo4j_test_settings)
    try:
        graph_path, invariants_path, enrichment_path, candidates_path, _ = _prepare_artifacts(
            tmp_path, graph, repository
        )
    finally:
        repository.close()

    with neo4j_driver.session(database=neo4j_test_settings.neo4j_database) as session:
        # Elimina el Paragraph usado por el candidato para forzar drift real
        # entre 04-semantic-graph.json y el estado real de Neo4j.
        session.run("MATCH (n:Paragraph) DETACH DELETE n").consume()

    with pytest.raises(CandidateDetectionError):
        _run_stage(
            graph=graph,
            graph_path=graph_path,
            enrichment_path=enrichment_path,
            invariants_path=invariants_path,
            candidates_path=candidates_path,
            settings=neo4j_test_settings,
        )


def test_second_run_does_not_rewrite_identical_artifact(
    neo4j_test_settings: Settings, clean_managed_graph: None, tmp_path: Path
) -> None:
    graph = _graph_with_candidates(decision_count=1)
    repository = Neo4jRepository.connect(neo4j_test_settings)
    try:
        graph_path, invariants_path, enrichment_path, candidates_path, _ = _prepare_artifacts(
            tmp_path, graph, repository
        )
    finally:
        repository.close()

    _run_stage(
        graph=graph,
        graph_path=graph_path,
        enrichment_path=enrichment_path,
        invariants_path=invariants_path,
        candidates_path=candidates_path,
        settings=neo4j_test_settings,
    )
    first_bytes = candidates_path.read_bytes()

    second_warnings = _run_stage(
        graph=graph,
        graph_path=graph_path,
        enrichment_path=enrichment_path,
        invariants_path=invariants_path,
        candidates_path=candidates_path,
        settings=neo4j_test_settings,
    )

    assert second_warnings == ["detectados 1 candidato(s) (sin cambios)"]
    assert candidates_path.read_bytes() == first_bytes


def _graph_and_program_for_enhanced_level_88(
    source_package_hash: str = _HASH_A,
) -> tuple[SemanticGraph, CanonicalProgram]:
    """Grafo + CanonicalProgram correlacionados (mismas propiedades que
    `semantic_graph_builder.py` produce realmente: `Paragraph.source_file`/
    `line_start`, `Decision.expression`/`line_start`, `DataItem.
    qualified_name`/`semantic_tag`) para probar la deteccion V2
    LEVEL_88_RETURN_CODE_RULE contra un Neo4j real (Fase 15B3-B).
    `_graph_with_candidates` (usado por los tests V1 de este archivo) no
    sirve aqui: omite deliberadamente `line_start`/`qualified_name` en
    Decision/DataItem, que Q0 nunca necesita pero V2DetectorContext si.
    Tambien agrega `Program.name` (`_base_graph_nodes` no lo incluye:
    Q0 nunca lo necesita, solo `source_package_hash`) -- sin el,
    `V2DetectorContext.program_node_by_name` queda vacio y ningun indice
    derivado (paragraph/decision/data_item) encuentra nada."""
    country, application, operation, program_base = _base_graph_nodes(source_package_hash)
    program = program_base.model_copy(
        update={"properties": {**program_base.properties, "name": "PROG1"}}
    )
    paragraph = GraphNode(
        id=f"{program.id}::paragraph::MAIN",
        labels=[NodeLabel.PARAGRAPH],
        properties={
            "name": "MAIN",
            "source_file": "01-codigo/cobol/PROG1.cbl",
            "line_start": 1,
            "line_end": 999,
            "source_package_hash": source_package_hash,
        },
    )
    decision = GraphNode(
        id=f"{paragraph.id}::decision::10::1",
        labels=[NodeLabel.DECISION],
        properties={
            "expression": "ERROR-DE-ENTRADA",
            "line_start": 10,
            "line_end": 10,
            "outcome_code": None,
            "rule_type": None,
            "source_package_hash": source_package_hash,
        },
    )
    data_item = GraphNode(
        id=f"{program.id}::data::WS-COD-RETORNO",
        labels=[NodeLabel.DATA_ITEM],
        properties={
            "qualified_name": "WS-COD-RETORNO",
            "semantic_tag": "return_code",
            "source_package_hash": source_package_hash,
        },
    )
    nodes = sorted(
        [country, application, operation, program, paragraph, decision, data_item],
        key=lambda n: n.id,
    )
    relationships = sorted(
        [
            GraphRelationship(
                type=RelationshipType.HAS_APPLICATION,
                from_id=country.id,
                to_id=application.id,
                properties={},
            ),
            GraphRelationship(
                type=RelationshipType.HAS_OPERATION,
                from_id=application.id,
                to_id=operation.id,
                properties={},
            ),
            GraphRelationship(
                type=RelationshipType.EXECUTES_VIA,
                from_id=operation.id,
                to_id=program.id,
                properties={},
            ),
            GraphRelationship(
                type=RelationshipType.CONTAINS,
                from_id=program.id,
                to_id=paragraph.id,
                properties={},
            ),
            GraphRelationship(
                type=RelationshipType.HAS_DECISION,
                from_id=paragraph.id,
                to_id=decision.id,
                properties={},
            ),
        ],
        key=lambda r: (r.type.value, r.from_id, r.to_id, "{}"),
    )
    graph = SemanticGraph(
        source_package_hash=source_package_hash, nodes=nodes, relationships=relationships
    )

    condition = CanonicalConditionName(
        name="COD-CAMPO-INVALIDO",
        qualified_name="WS-COD-RETORNO.COD-CAMPO-INVALIDO",
        parent_name="WS-COD-RETORNO",
        parent_qualified_name="WS-COD-RETORNO",
        values=[CanonicalConditionValue(value="0005", location_kind=LocationKind.UNKNOWN)],
        location_kind=LocationKind.UNKNOWN,
    )
    if_stmt = CanonicalStatement(
        statement_id="MAIN::0::IF",
        kind=StatementKind.IF,
        source_text="IF ERROR-DE-ENTRADA",
        location_kind=LocationKind.EXACT,
        source_file="01-codigo/cobol/PROG1.cbl",
        line_start=10,
        line_end=10,
        expression="ERROR-DE-ENTRADA",
    )
    set_stmt = CanonicalStatement(
        statement_id="MAIN::1::SET",
        kind=StatementKind.SET,
        source_text="SET COD-CAMPO-INVALIDO TO TRUE",
        location_kind=LocationKind.EXACT,
        source_file="01-codigo/cobol/PROG1.cbl",
        line_start=11,
        line_end=11,
        target_data_items=["COD-CAMPO-INVALIDO"],
        variables_written=["COD-CAMPO-INVALIDO"],
        condition_name_target="COD-CAMPO-INVALIDO",
        condition_set_value=True,
        parent_statement_id="MAIN::0::IF",
        branch_kind="THEN",
    )
    paragraph_canonical = CanonicalParagraph(
        name="MAIN",
        source_text="MAIN.",
        location_kind=LocationKind.EXACT,
        source_file="01-codigo/cobol/PROG1.cbl",
        line_start=1,
        line_end=999,
        statements=[if_stmt, set_stmt],
        variables_written=["COD-CAMPO-INVALIDO"],
    )
    canonical_program = CanonicalProgram(
        program_name="PROG1",
        source_file="01-codigo/cobol/PROG1.cbl",
        source_hash="b" * 64,
        source_package_hash=source_package_hash,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        data_items=[
            CanonicalDataItem(
                name="WS-COD-RETORNO",
                qualified_name="WS-COD-RETORNO",
                level=1,
                location_kind=LocationKind.UNKNOWN,
            )
        ],
        condition_names=[condition],
        paragraphs=[paragraph_canonical],
    )
    return graph, canonical_program


def test_enhanced_candidates_enabled_adds_level_88_candidate_via_real_neo4j(
    neo4j_test_settings: Settings, clean_managed_graph: None, tmp_path: Path
) -> None:
    """Fase 15B3-B: con `enhanced_candidates_enabled=true`, CANDIDATES_DETECTED
    agrega candidatos V2 reales (detectados en memoria, sin consulta
    Neo4j adicional) a `06-candidates.json`, sin afectar la deteccion Q0
    (que aqui no encuentra nada: el grafo no tiene ningun `LEADS_TO`
    hacia un DataItem `return_code`). Igual que el caso B de
    `test_v2_detectors.py`: `V2_LEVEL_88_RETURN_CODE` y
    `V2_RETURN_CODE_PROPAGATION` (CONDITION_LITERAL, Fase 5 condicion #3)
    disparan ambos sobre la misma Decision -- se conservan por separado."""
    graph, canonical_program = _graph_and_program_for_enhanced_level_88()
    repository = Neo4jRepository.connect(neo4j_test_settings)
    try:
        graph_path, invariants_path, enrichment_path, candidates_path, _ = _prepare_artifacts(
            tmp_path, graph, repository
        )
    finally:
        repository.close()

    canonical_dir = tmp_path / "02-canonical"
    canonical_dir.mkdir(parents=True, exist_ok=True)
    (canonical_dir / "PROG1.cbl.json").write_text(
        canonical_program.model_dump_json(), encoding="utf-8"
    )

    enhanced_settings = neo4j_test_settings.model_copy(update={"enhanced_candidates_enabled": True})

    warnings = _run_stage(
        graph=graph,
        graph_path=graph_path,
        enrichment_path=enrichment_path,
        invariants_path=invariants_path,
        candidates_path=candidates_path,
        settings=enhanced_settings,
        canonical_dir=canonical_dir,
    )

    assert warnings == ["detectados 2 candidato(s)"]
    artifact = CandidateArtifact.model_validate_json(candidates_path.read_text(encoding="utf-8"))
    assert len(artifact.candidates) == 2
    families = {c.rule_family for c in artifact.candidates}
    assert families == {UnifiedRuleFamily.RETURN_CODE, UnifiedRuleFamily.LEVEL_88_RETURN_CODE}
    for candidate in artifact.candidates:
        assert candidate.candidate_source == CandidateSource.V2
        assert candidate.outcome_code == "0005"
        assert candidate.paragraph_name == "MAIN"


def test_no_context_directory_or_q1_to_q7_side_effects(
    neo4j_test_settings: Settings, clean_managed_graph: None, tmp_path: Path
) -> None:
    graph = _graph_with_candidates(decision_count=1)
    repository = Neo4jRepository.connect(neo4j_test_settings)
    try:
        graph_path, invariants_path, enrichment_path, candidates_path, _ = _prepare_artifacts(
            tmp_path, graph, repository
        )
    finally:
        repository.close()

    _run_stage(
        graph=graph,
        graph_path=graph_path,
        enrichment_path=enrichment_path,
        invariants_path=invariants_path,
        candidates_path=candidates_path,
        settings=neo4j_test_settings,
    )

    assert not (tmp_path / "07-context").exists()
