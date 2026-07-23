"""Test de integracion dedicado de la etapa SEMANTIC_GRAPH_BUILT
(Prompt 8): construye un entorno con artefactos REALES en disco
(CanonicalProgram, DependencyArtifact, SemanticEnrichmentArtifact
persistidos como archivos, no solo objetos en memoria) y ejecuta la etapa
directamente.

No requiere el JAR en si mismo: `run_semantic_graph_stage` no invoca Java
(ver docstring de ese modulo). Se marca `integration` de todas formas
(consistente con el resto de `tests/parser_integration/`) para mantener
la suite `-m "not integration"` rapida y sin artefactos multi-archivo, y
para que el conteo de la suite `integration` refleje realmente todos los
tests de esta carpeta. La cobertura del pipeline completo con JAR real
vive en `test_dependencies_built_integration.py`/`test_parsed_stage_integration.py`,
que ahora tambien atraviesan esta etapa como parte de `run_ingestion`.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from altamira_extractor.contracts.canonical import (
    CanonicalDataItem,
    CanonicalParagraph,
    CanonicalProgram,
    CanonicalSqlAccess,
    CanonicalStatement,
)
from altamira_extractor.contracts.dependencies import (
    DependencyArtifact,
    DependencyEvidence,
    ParagraphDependency,
)
from altamira_extractor.contracts.enums import (
    BranchKind,
    DependencyEvidenceRole,
    DependencyType,
    InventoryFileKind,
    LocationKind,
    NodeLabel,
    ParseSupportStatus,
    PipelineStage,
    RelationshipType,
    SourceFormat,
    StageStatus,
    StatementKind,
    TableAccessOperation,
    TextEncoding,
)
from altamira_extractor.contracts.inventory import Inventory, InventoryFile
from altamira_extractor.contracts.manifest import (
    Manifest,
    ManifestApplication,
    ManifestCountry,
    ManifestImplementation,
    ManifestOperation,
    ManifestParameterTable,
    ManifestSource,
)
from altamira_extractor.contracts.run_state import RunState, StageExecution
from altamira_extractor.contracts.semantic_enrichment import (
    DataItemDomainTermMapping,
    DataItemSemanticTag,
    DomainTermRecord,
    ParameterColumnDefinition,
    ParameterEntryRecord,
    ParameterTableRecord,
    SemanticEnrichmentArtifact,
    SemanticTagRuleMatch,
)
from altamira_extractor.contracts.semantic_graph import SemanticGraph
from altamira_extractor.pipeline import runner as runner_module
from altamira_extractor.pipeline.artifact_store import atomic_write_json
from altamira_extractor.pipeline.semantic_graph_stage import run_semantic_graph_stage

VALID_HASH = "a" * 64
PROGRAM_SOURCE_HASH = "b" * 64
NOW = datetime(2026, 1, 1, tzinfo=UTC)

COBOL_RELATIVE_PATH = "01-codigo/cobol/PROG1.cbl"
PROGRAM_ID = f"program::AR::OP-TRF-PROPIA::PROG1::1.0::{PROGRAM_SOURCE_HASH[:12]}"
PARAMETER_TABLE_ID = f"parameter::table::AR::DEFAULT::PARAM_TRANSFER::2026-01-01::{'c' * 12}"


class _Environment:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.inventory_path = tmp_path / "artifacts" / "01-inventory.json"
        self.canonical_dir = tmp_path / "artifacts" / "02-canonical"
        self.dependencies_path = tmp_path / "artifacts" / "03-dependencies.json"
        self.semantic_enrichment_path = tmp_path / "artifacts" / "03b-semantic-enrichment.json"
        self.semantic_graph_path = tmp_path / "artifacts" / "04-semantic-graph.json"
        self.run_json_path = tmp_path / "run.json"
        self.canonical_dir.mkdir(parents=True)

        # --- CanonicalProgram real, persistido ---
        main_para_if = CanonicalStatement(
            statement_id="PROG1::MAIN-PARA::0::IF",
            kind=StatementKind.IF,
            source_text="IF WS-IMPORTE-SOLICITADO > 0",
            source_file=COBOL_RELATIVE_PATH,
            line_start=10,
            line_end=15,
            location_kind=LocationKind.EXACT,
            expression="WS-IMPORTE-SOLICITADO > 0",
            operands=["WS-IMPORTE-SOLICITADO"],
        )
        main_para_then = CanonicalStatement(
            statement_id="PROG1::MAIN-PARA::1::MOVE",
            kind=StatementKind.MOVE,
            source_text="MOVE 'R001' TO WS-COD-RESULT",
            source_file=COBOL_RELATIVE_PATH,
            line_start=11,
            line_end=11,
            location_kind=LocationKind.EXACT,
            parent_statement_id="PROG1::MAIN-PARA::0::IF",
            branch_kind=BranchKind.THEN,
            branch_condition="WS-IMPORTE-SOLICITADO > 0",
            assigned_literal="R001",
            target_data_items=["WS-COD-RESULT"],
        )
        sql_access = CanonicalSqlAccess(
            table="PARAM_TRANSFER",
            operation=TableAccessOperation.READS,
            predicate_text="WHERE ID = :ID",
            host_variables=["WS-ID"],
            source_file=COBOL_RELATIVE_PATH,
            line_start=20,
            line_end=21,
            location_kind=LocationKind.EXACT,
        )
        main_para_sql = CanonicalStatement(
            statement_id="PROG1::MAIN-PARA::2::EXEC_SQL",
            kind=StatementKind.EXEC_SQL,
            source_text="EXEC SQL SELECT ... FROM PARAM_TRANSFER END-EXEC",
            source_file=COBOL_RELATIVE_PATH,
            line_start=20,
            line_end=21,
            location_kind=LocationKind.EXACT,
            sql_access=[sql_access],
        )
        main_para_statements = [main_para_if, main_para_then, main_para_sql]
        main_para = CanonicalParagraph(
            name="MAIN-PARA",
            source_text="MAIN-PARA.",
            source_file=COBOL_RELATIVE_PATH,
            line_start=10,
            line_end=21,
            location_kind=LocationKind.EXACT,
            statements=main_para_statements,
            variables_read=[],
            variables_written=[],
            sql_access=[sql_access],
        )

        check_para_stmt = CanonicalStatement(
            statement_id="PROG1::CHECK-PARA::0::IF",
            kind=StatementKind.IF,
            source_text="IF WS-COD-RESULT = 'R001'",
            source_file=COBOL_RELATIVE_PATH,
            line_start=30,
            line_end=30,
            location_kind=LocationKind.EXACT,
            expression="WS-COD-RESULT = 'R001'",
            variables_read=["WS-COD-RESULT"],
        )
        check_para = CanonicalParagraph(
            name="CHECK-PARA",
            source_text="CHECK-PARA.",
            source_file=COBOL_RELATIVE_PATH,
            line_start=30,
            line_end=30,
            location_kind=LocationKind.EXACT,
            statements=[check_para_stmt],
            variables_read=["WS-COD-RESULT"],
            variables_written=[],
            sql_access=[],
        )

        canonical_program = CanonicalProgram(
            program_name="PROG1",
            source_file=COBOL_RELATIVE_PATH,
            source_hash=PROGRAM_SOURCE_HASH,
            source_package_hash=VALID_HASH,
            source_format=SourceFormat.FIXED,
            encoding="UTF-8",
            data_items=[
                CanonicalDataItem(
                    name="WS-IMPORTE-SOLICITADO",
                    qualified_name="WS-IMPORTE-SOLICITADO",
                    level=1,
                    pic="9(7)V99",
                    source_file=COBOL_RELATIVE_PATH,
                    line=5,
                    location_kind=LocationKind.EXACT,
                ),
                CanonicalDataItem(
                    name="WS-COD-RESULT",
                    qualified_name="WS-COD-RESULT",
                    level=1,
                    pic="X(4)",
                    source_file=COBOL_RELATIVE_PATH,
                    line=6,
                    location_kind=LocationKind.EXACT,
                ),
            ],
            paragraphs=[main_para, check_para],
        )
        canonical_path = self.canonical_dir / f"{COBOL_RELATIVE_PATH}.json"
        canonical_path.parent.mkdir(parents=True, exist_ok=True)
        canonical_path.write_text(canonical_program.to_stable_json(), encoding="utf-8")

        # --- DependencyArtifact real, persistido ---
        main_para_id = f"{PROGRAM_ID}::paragraph::MAIN-PARA"
        check_para_id = f"{PROGRAM_ID}::paragraph::CHECK-PARA"
        data_dependency = ParagraphDependency(
            dependency_type=DependencyType.DATA_DEPENDS_ON,
            from_paragraph_id=main_para_id,
            to_paragraph_id=check_para_id,
            variables=["WS-COD-RESULT"],
            control_construct=None,
            dependency_depth=1,
            confidence=0.8,
            derivation_rule="PARAGRAPH_WRITE_READ_QUALIFIED_MATCH",
            source_file=COBOL_RELATIVE_PATH,
            line_start=11,
            line_end=11,
            location_kind=LocationKind.EXACT,
            source_package_hash=VALID_HASH,
            evidence=[
                DependencyEvidence(
                    role=DependencyEvidenceRole.WRITER,
                    statement_id="PROG1::MAIN-PARA::1::MOVE",
                    statement_kind=StatementKind.MOVE,
                    source_text="MOVE 'R001' TO WS-COD-RESULT",
                    source_file=COBOL_RELATIVE_PATH,
                    line_start=11,
                    line_end=11,
                    location_kind=LocationKind.EXACT,
                    original_variable="WS-COD-RESULT",
                    resolved_qualified_name="WS-COD-RESULT",
                ),
                DependencyEvidence(
                    role=DependencyEvidenceRole.READER,
                    statement_id="PROG1::CHECK-PARA::0::IF",
                    statement_kind=StatementKind.IF,
                    source_text="IF WS-COD-RESULT = 'R001'",
                    source_file=COBOL_RELATIVE_PATH,
                    line_start=30,
                    line_end=30,
                    location_kind=LocationKind.EXACT,
                    original_variable="WS-COD-RESULT",
                    resolved_qualified_name="WS-COD-RESULT",
                ),
            ],
        )
        control_dependency = ParagraphDependency(
            dependency_type=DependencyType.CONTROL_DEPENDS_ON,
            from_paragraph_id=main_para_id,
            to_paragraph_id=check_para_id,
            variables=[],
            control_construct="PERFORM",
            dependency_depth=1,
            confidence=1.0,
            derivation_rule="EXPLICIT_CONTROL_TARGET",
            source_file=COBOL_RELATIVE_PATH,
            line_start=12,
            line_end=12,
            location_kind=LocationKind.EXACT,
            source_package_hash=VALID_HASH,
            evidence=[
                DependencyEvidence(
                    role=DependencyEvidenceRole.CONTROL,
                    statement_id="PROG1::MAIN-PARA::3::PERFORM",
                    statement_kind=StatementKind.PERFORM,
                    source_text="PERFORM CHECK-PARA.",
                    source_file=COBOL_RELATIVE_PATH,
                    line_start=12,
                    line_end=12,
                    location_kind=LocationKind.EXACT,
                    original_target="CHECK-PARA",
                )
            ],
        )
        atomic_write_json(
            self.dependencies_path,
            DependencyArtifact(
                run_id="run-semantic-graph-dedicated",
                source_package_hash=VALID_HASH,
                dependencies=[control_dependency, data_dependency],
            ),
        )

        # --- SemanticEnrichmentArtifact real, persistido ---
        item_id = f"{PROGRAM_ID}::data::WS-IMPORTE-SOLICITADO"
        tag = DataItemSemanticTag(
            data_item_id=item_id,
            program_id=PROGRAM_ID,
            source_file=COBOL_RELATIVE_PATH,
            original_name="WS-IMPORTE-SOLICITADO",
            qualified_name="WS-IMPORTE-SOLICITADO",
            semantic_tag="amount",
            semantic_confidence=0.8,
            evidence=[
                SemanticTagRuleMatch(rule_id="amount-by-name", tag="amount", base_confidence=0.8)
            ],
        )
        domain_term = DomainTermRecord(
            domain_term_id="term::1.0::REQUESTED_AMOUNT",
            functional_key="requested_amount",
            normalized_functional_key="REQUESTED_AMOUNT",
            functional_name="importe solicitado",
            definition="Monto monetario solicitado para la operacion.",
            entity_type="monetary_amount",
            authoritative_source="V1 controlled glossary",
            source_kind="CURATED_CONFIG",
            catalog_version="1.0",
            semantic_tags=["amount"],
        )
        mapping = DataItemDomainTermMapping(
            data_item_id=item_id,
            semantic_tag="amount",
            domain_term_id="term::1.0::REQUESTED_AMOUNT",
            derivation_rule="SEMANTIC_TAG_GLOSSARY_MATCH",
            confidence=0.8,
            evidence=(
                "semantic_tag 'amount' declarado en match.semantic_tags de "
                "term::1.0::REQUESTED_AMOUNT"
            ),
        )
        parameter_entry = ParameterEntryRecord(
            parameter_entry_id=f"{PARAMETER_TABLE_ID}::entry::1::{'d' * 12}",
            row_number=1,
            row_hash="e" * 64,
            raw_row={"ID": "1", "LIMITE": " 1000.00"},
            normalized_row={"ID": "1", "LIMITE": "1000.00"},
        )
        parameter_table = ParameterTableRecord(
            table_id="table::AR::DEFAULT::PARAM_TRANSFER",
            parameter_table_id=PARAMETER_TABLE_ID,
            name="PARAM_TRANSFER",
            normalized_name="PARAM_TRANSFER",
            country_code="AR",
            ddl_relative_path="02-parametria/ddl/PARAM_TRANSFER.sql",
            snapshot_relative_path="02-parametria/snapshots/PARAM_TRANSFER_20260101.csv",
            snapshot_date=date(2026, 1, 1),
            ddl_hash=VALID_HASH,
            snapshot_hash="c" * 64,
            ddl_support_status=ParseSupportStatus.SUPPORTED,
            snapshot_support_status=ParseSupportStatus.SUPPORTED,
            columns=[
                ParameterColumnDefinition(
                    original_name="ID",
                    normalized_name="ID",
                    declared_type="INTEGER",
                    nullable=False,
                    is_primary_key=True,
                    ordinal=1,
                )
            ],
            entries=[parameter_entry],
        )
        atomic_write_json(
            self.semantic_enrichment_path,
            SemanticEnrichmentArtifact(
                run_id="run-semantic-graph-dedicated",
                source_package_hash=VALID_HASH,
                semantic_tags_config_hash=VALID_HASH,
                domain_glossary_config_hash=VALID_HASH,
                parameter_tables=[parameter_table],
                data_item_tags=[tag],
                domain_terms=[domain_term],
                data_item_domain_term_mappings=[mapping],
            ),
        )

        # --- Inventory (objeto real, no requiere persistirse: la etapa lo
        # recibe directamente igual que las etapas anteriores) ---
        manifest = Manifest(
            schema_version="1.0",
            country=ManifestCountry(code="AR", name="Argentina"),
            application=ManifestApplication(name="Transferencias"),
            operation=ManifestOperation(logical_name="OP-TRF-PROPIA", description=None),
            implementation=ManifestImplementation(version="1.0", entry_programs=["PROG1"]),
            source=ManifestSource(format=SourceFormat.FIXED, encoding="UTF-8"),
            parameter_tables=[
                ManifestParameterTable(
                    name="PARAM_TRANSFER",
                    ddl="02-parametria/ddl/PARAM_TRANSFER.sql",
                    snapshot="02-parametria/snapshots/PARAM_TRANSFER_20260101.csv",
                    snapshot_date=date(2026, 1, 1),
                )
            ],
        )
        self.inventory = Inventory(
            run_id="run-semantic-graph-dedicated",
            source_package_hash=VALID_HASH,
            manifest=manifest,
            files=[
                InventoryFile(
                    relative_path=COBOL_RELATIVE_PATH,
                    kind=InventoryFileKind.COBOL,
                    size_bytes=1,
                    sha256=PROGRAM_SOURCE_HASH,
                    detected_encoding=TextEncoding.UTF_8,
                )
            ],
        )
        atomic_write_json(self.inventory_path, self.inventory)

    def semantic_enrichment_built_stage(self) -> StageExecution:
        return StageExecution(
            stage=PipelineStage.SEMANTIC_ENRICHMENT_BUILT,
            status=StageStatus.SUCCEEDED,
            started_at=NOW,
            finished_at=NOW,
            duration_seconds=1.0,
        )

    def run(self) -> list[str]:
        return run_semantic_graph_stage(
            run_id="run-semantic-graph-dedicated",
            source_package_hash=VALID_HASH,
            run_stages=[self.semantic_enrichment_built_stage()],
            inventory=self.inventory,
            canonical_dir=self.canonical_dir,
            dependencies_path=self.dependencies_path,
            semantic_enrichment_path=self.semantic_enrichment_path,
            semantic_graph_path=self.semantic_graph_path,
        )


@pytest.fixture
def env(tmp_path: Path) -> _Environment:
    return _Environment(tmp_path)


def _relationships(graph: SemanticGraph, rel_type: RelationshipType) -> list[Any]:
    return [r for r in graph.relationships if r.type == rel_type]


@pytest.mark.integration
def test_semantic_graph_built_produces_complete_valid_artifact(env: _Environment) -> None:
    warnings = env.run()

    # 1. Valida con Pydantic (releido desde el archivo real).
    assert env.semantic_graph_path.is_file()
    graph = SemanticGraph.model_validate_json(
        env.semantic_graph_path.read_text(encoding="utf-8")
    )
    assert graph.source_package_hash == VALID_HASH

    # 2. Valida contra semantic-graph.schema.json.
    import jsonschema

    schemas_dir = Path(__file__).resolve().parents[2] / "schemas"
    schema = json.loads((schemas_dir / "semantic-graph.schema.json").read_text(encoding="utf-8"))
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    errors = sorted(validator_cls(schema).iter_errors(graph.model_dump(mode="json")), key=str)
    assert not errors, "\n".join(f"{e.json_path}: {e.message}" for e in errors)

    # 3-4. ParameterEntry + HAS_ENTRY, DomainTerm + HAS_DOMAIN_TERM.
    entry_node = next(n for n in graph.nodes if n.id.startswith(f"{PARAMETER_TABLE_ID}::entry::"))
    assert entry_node.labels == [NodeLabel.PARAMETER_ENTRY]
    has_entry = _relationships(graph, RelationshipType.HAS_ENTRY)
    assert any(r.from_id == PARAMETER_TABLE_ID and r.to_id == entry_node.id for r in has_entry)

    domain_term_node = next(n for n in graph.nodes if n.id == "term::1.0::REQUESTED_AMOUNT")
    assert domain_term_node.labels == [NodeLabel.DOMAIN_TERM]
    has_domain_term = _relationships(graph, RelationshipType.HAS_DOMAIN_TERM)
    assert any(
        r.to_id == "term::1.0::REQUESTED_AMOUNT"
        and r.from_id == f"{PROGRAM_ID}::data::WS-IMPORTE-SOLICITADO"
        for r in has_domain_term
    )

    # 5. DATA_DEPENDS_ON y CONTROL_DEPENDS_ON.
    main_para_id = f"{PROGRAM_ID}::paragraph::MAIN-PARA"
    check_para_id = f"{PROGRAM_ID}::paragraph::CHECK-PARA"
    data_depends = _relationships(graph, RelationshipType.DATA_DEPENDS_ON)
    control_depends = _relationships(graph, RelationshipType.CONTROL_DEPENDS_ON)
    assert any(r.from_id == main_para_id and r.to_id == check_para_id for r in data_depends)
    assert any(r.from_id == main_para_id and r.to_id == check_para_id for r in control_depends)

    # 6. Relaciones SQL directas hacia la ParameterTable correlacionada.
    reads = _relationships(graph, RelationshipType.READS)
    assert any(r.from_id == main_para_id and r.to_id == PARAMETER_TABLE_ID for r in reads)

    # 7. Ausencia total de SqlStatement.
    all_labels = {label.value for node in graph.nodes for label in node.labels}
    assert "SqlStatement" not in all_labels

    # LEADS_TO de la asignacion directa en MAIN-PARA.
    leads_to = _relationships(graph, RelationshipType.LEADS_TO)
    target_item_id = f"{PROGRAM_ID}::data::WS-COD-RESULT"
    assert any(
        r.to_id == target_item_id and r.properties["assigned_literal"] == "R001"
        for r in leads_to
    )

    assert warnings == []


@pytest.mark.integration
def test_second_run_via_runner_keeps_single_stage_execution(env: _Environment) -> None:
    # Ejecutar la etapa dos veces a traves del runner (que es quien
    # administra RunState.stages) nunca deja dos StageExecution para
    # SEMANTIC_GRAPH_BUILT: _upsert_stage siempre reemplaza la entrada
    # existente para esa etapa.
    state = RunState(
        run_id="run-semantic-graph-dedicated",
        package_filename="input/package.zip",
        source_package_hash=VALID_HASH,
        current_stage=PipelineStage.SEMANTIC_ENRICHMENT_BUILT,
        stages=[env.semantic_enrichment_built_stage()],
        created_at=NOW,
        updated_at=NOW,
    )

    for _ in range(2):
        state = runner_module._run_semantic_graph_built(
            state,
            env.inventory_path,
            env.canonical_dir,
            env.dependencies_path,
            env.semantic_enrichment_path,
            env.semantic_graph_path,
            env.run_json_path,
        )

    assert state.current_stage == PipelineStage.SEMANTIC_GRAPH_BUILT
    matching = [s for s in state.stages if s.stage == PipelineStage.SEMANTIC_GRAPH_BUILT]
    assert len(matching) == 1
    assert matching[0].status == StageStatus.SUCCEEDED

    persisted = RunState.model_validate_json(env.run_json_path.read_text(encoding="utf-8"))
    assert persisted == state


@pytest.mark.integration
def test_second_run_does_not_rewrite_artifact_when_identical(
    env: _Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Cuando el resultado recomputado es identico al artefacto existente,
    # la segunda corrida no reescribe el archivo: se verifica con bytes
    # antes/despues, inodo (el filesystem del contenedor Linux lo soporta)
    # y un spy sobre atomic_write_json.
    env.run()
    assert env.semantic_graph_path.is_file()
    first_bytes = env.semantic_graph_path.read_bytes()
    first_inode = env.semantic_graph_path.stat().st_ino

    write_calls: list[Path] = []
    import altamira_extractor.pipeline.semantic_graph_stage as stage_module

    original_atomic_write_json = stage_module.atomic_write_json

    def _spy_atomic_write_json(path: Path, model: object) -> None:
        write_calls.append(path)
        original_atomic_write_json(path, model)  # type: ignore[arg-type]

    monkeypatch.setattr(stage_module, "atomic_write_json", _spy_atomic_write_json)

    second_warnings = env.run()

    assert write_calls == []
    assert env.semantic_graph_path.read_bytes() == first_bytes
    assert env.semantic_graph_path.stat().st_ino == first_inode
    assert second_warnings == []
