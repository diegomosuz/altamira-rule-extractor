"""Test de integracion dedicado de la etapa SEMANTIC_ENRICHMENT_BUILT
(Prompt 7): construye un entorno con artefactos REALES en disco
(Inventory, CanonicalProgram, DDL, snapshot CSV, semantic-tags.yml,
domain-glossary.yml persistidos como archivos, no solo objetos en
memoria) y ejecuta la etapa directamente.

No requiere el JAR: `run_semantic_enrichment_stage` no invoca Java (ver
docstring de ese modulo). La cobertura del pipeline completo con JAR real
vive en `test_dependencies_built_integration.py`/`test_parsed_stage_integration.py`,
que ahora tambien atraviesan esta etapa como parte de `run_ingestion`.
"""

from __future__ import annotations

import hashlib
import inspect
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

import altamira_extractor.pipeline.semantic_enrichment_stage as stage_module
from altamira_extractor.config import Settings
from altamira_extractor.contracts.canonical import CanonicalDataItem, CanonicalProgram
from altamira_extractor.contracts.enums import (
    InventoryFileKind,
    LocationKind,
    PipelineStage,
    SourceFormat,
    StageStatus,
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
from altamira_extractor.contracts.semantic_enrichment import SemanticEnrichmentArtifact
from altamira_extractor.pipeline import runner as runner_module
from altamira_extractor.pipeline.artifact_store import atomic_write_json
from altamira_extractor.pipeline.semantic_enrichment_stage import run_semantic_enrichment_stage

VALID_HASH = "a" * 64
PROGRAM_SOURCE_HASH = "b" * 64
NOW = datetime(2026, 1, 1, tzinfo=UTC)

COBOL_RELATIVE_PATH = "01-codigo/cobol/PROG1.cbl"
DDL_RELATIVE_PATH = "02-parametria/ddl/PARAM_TRANSFER.sql"
SNAPSHOT_RELATIVE_PATH = "02-parametria/snapshots/PARAM_TRANSFER_20260101.csv"

_COBOL_SOURCE = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. PROG1.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-IMPORTE-SOLICITADO PIC 9(7)V99.
       PROCEDURE DIVISION.
       MAIN-PARA.
           GOBACK.
"""

_DDL_TEXT = "CREATE TABLE PARAM_TRANSFER (ID INTEGER NOT NULL PRIMARY KEY, LIMITE DECIMAL(9,2));"

# Fila con espacio inicial en LIMITE (para distinguir raw_row de
# normalized_row) y una columna OBSERVACIONES no declarada en el DDL
# (para provocar un warning de columna no declarada) con un valor de celda
# "sensible" que nunca debe aparecer en ningun warning.
_CSV_TEXT = (
    "ID,LIMITE,OBSERVACIONES\n"
    "1, 1000.00,SECRETO-CLIENTE-001\n"
    "2, 2000.50,SECRETO-CLIENTE-002\n"
)

_SEMANTIC_TAGS_YAML = """version: "1.0"
allowed_tags:
  - amount
rules:
  - id: amount-by-name
    tag: amount
    name_regex: "(?i).*(IMPORTE|MONTO|AMOUNT).*"
    requires_numeric_pic: true
    base_confidence: 0.8
"""

_DOMAIN_GLOSSARY_YAML = """version: "1.0"
terms:
  - key: requested_amount
    functional_name: "importe solicitado"
    definition: "Monto monetario solicitado para la operacion."
    entity_type: "monetary_amount"
    authoritative_source: "V1 controlled glossary"
    source_kind: "CURATED_CONFIG"
    match:
      semantic_tags: [amount]
"""


class _Environment:
    """Construye, con archivos reales en disco, todos los insumos que
    SEMANTIC_ENRICHMENT_BUILT necesita: Inventory y CanonicalProgram
    persistidos como JSON, DDL/CSV como archivos de texto, y ambos YAML de
    configuracion como archivos independientes del repo (hermeticos al
    test)."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.extracted_dir = tmp_path / "work" / "extracted"
        self.canonical_dir = tmp_path / "artifacts" / "02-canonical"
        self.inventory_path = tmp_path / "artifacts" / "01-inventory.json"
        self.semantic_enrichment_path = tmp_path / "artifacts" / "03b-semantic-enrichment.json"
        self.run_json_path = tmp_path / "run.json"
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        self.extracted_dir.mkdir(parents=True)
        self.canonical_dir.mkdir(parents=True)

        # --- COBOL fuente real + CanonicalProgram real persistido ---
        cobol_path = self.extracted_dir / COBOL_RELATIVE_PATH
        cobol_path.parent.mkdir(parents=True, exist_ok=True)
        cobol_path.write_bytes(_COBOL_SOURCE)
        cobol_sha256 = hashlib.sha256(cobol_path.read_bytes()).hexdigest()

        canonical_program = CanonicalProgram(
            program_name="PROG1",
            source_file=COBOL_RELATIVE_PATH,
            source_hash=cobol_sha256,
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
                )
            ],
            paragraphs=[],
        )
        canonical_path = self.canonical_dir / f"{COBOL_RELATIVE_PATH}.json"
        canonical_path.parent.mkdir(parents=True, exist_ok=True)
        canonical_path.write_text(canonical_program.to_stable_json(), encoding="utf-8")

        # --- DDL y snapshot CSV reales ---
        ddl_path = self.extracted_dir / DDL_RELATIVE_PATH
        ddl_path.parent.mkdir(parents=True, exist_ok=True)
        ddl_path.write_text(_DDL_TEXT, encoding="utf-8")
        ddl_sha256 = hashlib.sha256(ddl_path.read_bytes()).hexdigest()

        snapshot_path = self.extracted_dir / SNAPSHOT_RELATIVE_PATH
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(_CSV_TEXT, encoding="utf-8")
        snapshot_sha256 = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()

        # --- Manifest + Inventory reales, persistidos ---
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
                    ddl=DDL_RELATIVE_PATH,
                    snapshot=SNAPSHOT_RELATIVE_PATH,
                    snapshot_date=date(2026, 1, 1),
                )
            ],
        )
        inventory = Inventory(
            run_id="run-semantic-enrichment-dedicated",
            source_package_hash=VALID_HASH,
            manifest=manifest,
            files=[
                InventoryFile(
                    relative_path=COBOL_RELATIVE_PATH,
                    kind=InventoryFileKind.COBOL,
                    size_bytes=len(_COBOL_SOURCE),
                    sha256=cobol_sha256,
                    detected_encoding=TextEncoding.UTF_8,
                ),
                InventoryFile(
                    relative_path=DDL_RELATIVE_PATH,
                    kind=InventoryFileKind.DDL,
                    size_bytes=ddl_path.stat().st_size,
                    sha256=ddl_sha256,
                    detected_encoding=TextEncoding.UTF_8,
                ),
                InventoryFile(
                    relative_path=SNAPSHOT_RELATIVE_PATH,
                    kind=InventoryFileKind.SNAPSHOT,
                    size_bytes=snapshot_path.stat().st_size,
                    sha256=snapshot_sha256,
                    detected_encoding=TextEncoding.UTF_8,
                ),
            ],
        )
        self.inventory_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.inventory_path, inventory)

        # --- YAML de configuracion reales, independientes del repo ---
        self.semantic_tags_path = config_dir / "semantic-tags.yml"
        self.semantic_tags_path.write_text(_SEMANTIC_TAGS_YAML, encoding="utf-8")
        self.domain_glossary_path = config_dir / "domain-glossary.yml"
        self.domain_glossary_path.write_text(_DOMAIN_GLOSSARY_YAML, encoding="utf-8")

        self.settings = Settings(
            data_dir=tmp_path / "data",
            runs_dir=tmp_path / "data" / "runs",
            incoming_dir=tmp_path / "data" / "incoming",
            semantic_tags_path=self.semantic_tags_path,
            domain_glossary_path=self.domain_glossary_path,
        )

    def load_inventory_from_disk(self) -> Inventory:
        """Relee Inventory desde el archivo real (no el objeto en memoria):
        prueba que la etapa trabaja sobre el artefacto persistido."""
        return Inventory.model_validate_json(self.inventory_path.read_text(encoding="utf-8"))

    def dependencies_built_stage(self) -> StageExecution:
        return StageExecution(
            stage=PipelineStage.DEPENDENCIES_BUILT,
            status=StageStatus.SUCCEEDED,
            started_at=NOW,
            finished_at=NOW,
            duration_seconds=1.0,
        )

    def run(self) -> list[str]:
        return run_semantic_enrichment_stage(
            run_id="run-semantic-enrichment-dedicated",
            source_package_hash=VALID_HASH,
            run_stages=[self.dependencies_built_stage()],
            inventory=self.load_inventory_from_disk(),
            extracted_dir=self.extracted_dir,
            canonical_dir=self.canonical_dir,
            settings=self.settings,
            semantic_enrichment_path=self.semantic_enrichment_path,
        )


@pytest.fixture
def env(tmp_path: Path) -> _Environment:
    return _Environment(tmp_path)


def test_semantic_enrichment_built_produces_complete_valid_artifact(env: _Environment) -> None:
    warnings = env.run()

    # 1. El artefacto valida como SemanticEnrichmentArtifact, releido desde
    #    el archivo real (no desde un objeto en memoria).
    assert env.semantic_enrichment_path.is_file()
    artifact = SemanticEnrichmentArtifact.model_validate_json(
        env.semantic_enrichment_path.read_text(encoding="utf-8")
    )

    # 2-3. Los hashes de configuracion coinciden con los bytes reales del YAML.
    assert artifact.semantic_tags_config_hash == hashlib.sha256(
        env.semantic_tags_path.read_bytes()
    ).hexdigest()
    assert artifact.domain_glossary_config_hash == hashlib.sha256(
        env.domain_glossary_path.read_bytes()
    ).hexdigest()

    # 4. La tabla parametrica se identifica correctamente.
    assert len(artifact.parameter_tables) == 1
    table = artifact.parameter_tables[0]
    assert table.name == "PARAM_TRANSFER"
    assert table.table_id == "table::AR::DEFAULT::PARAM_TRANSFER"

    # 5-6. Columnas DDL interpretadas, PRIMARY KEY interpretada.
    assert table.ddl_support_status is not None
    assert table.ddl_support_status.value == "SUPPORTED"
    columns_by_name = {column.normalized_name: column for column in table.columns}
    assert set(columns_by_name) == {"ID", "LIMITE"}
    id_column = columns_by_name["ID"]
    assert id_column.is_primary_key is True
    assert id_column.nullable is False
    assert "DECIMAL" in columns_by_name["LIMITE"].declared_type

    # 7-8. ParameterEntry generados desde el CSV, con raw_row y
    #      normalized_row distintos donde corresponde (espacio inicial).
    assert table.snapshot_support_status is not None
    assert table.snapshot_support_status.value == "SUPPORTED"
    assert len(table.entries) == 2
    first_entry = next(e for e in table.entries if e.row_number == 1)
    assert first_entry.raw_row["LIMITE"] == " 1000.00"
    assert first_entry.normalized_row["LIMITE"] == "1000.00"
    assert first_entry.raw_row["OBSERVACIONES"] == "SECRETO-CLIENTE-001"

    # 9. IDs de tabla/parameter table/entries deterministicos y coherentes
    #    entre si (mismo esquema que semantic_enrichment_stage.py usa).
    expected_parameter_table_id = (
        f"parameter::{table.table_id}::2026-01-01::{table.snapshot_hash[:12]}"
    )
    assert table.parameter_table_id == expected_parameter_table_id
    for entry in table.entries:
        assert entry.parameter_entry_id == (
            f"{table.parameter_table_id}::entry::{entry.row_number}::{entry.row_hash[:12]}"
        )
    # Recalculo independiente del table_id/parameter_table_id a partir de
    # los mismos insumos deterministicos (country_code, nombre normalizado,
    # fecha de snapshot, hash real del CSV) para no depender solo de que
    # ambos lados usen la misma formula por casualidad.
    assert table.table_id == "table::AR::DEFAULT::PARAM_TRANSFER"
    assert table.snapshot_hash == hashlib.sha256(
        (env.extracted_dir / SNAPSHOT_RELATIVE_PATH).read_bytes()
    ).hexdigest()

    # 10-11. DataItemSemanticTag generado con evidencia conservada.
    assert len(artifact.data_item_tags) == 1
    tag = artifact.data_item_tags[0]
    assert tag.semantic_tag == "amount"
    assert tag.original_name == "WS-IMPORTE-SOLICITADO"
    assert len(tag.evidence) >= 1
    assert tag.evidence[0].rule_id == "amount-by-name"
    assert tag.evidence[0].tag == "amount"

    # 12. DomainTerm generado desde el glosario real.
    assert len(artifact.domain_terms) == 1
    term = artifact.domain_terms[0]
    assert term.domain_term_id == "term::1.0::REQUESTED_AMOUNT"
    assert term.functional_key == "requested_amount"

    # 13. DataItemDomainTermMapping generado, enlazando el tag con el termino.
    assert len(artifact.data_item_domain_term_mappings) == 1
    mapping = artifact.data_item_domain_term_mappings[0]
    assert mapping.data_item_id == tag.data_item_id
    assert mapping.domain_term_id == term.domain_term_id
    assert mapping.semantic_tag == "amount"

    # 15. Ningun warning contiene valores de celdas (aunque si debe existir
    #     al menos uno, por la columna OBSERVACIONES no declarada en el DDL).
    assert warnings, "se esperaba al menos un warning (columna CSV no declarada en el DDL)"
    for warning in warnings:
        assert "SECRETO-CLIENTE-001" not in warning
        assert "SECRETO-CLIENTE-002" not in warning
        assert "1000.00" not in warning
        assert "2000.50" not in warning


def test_no_forbidden_graph_constructs_are_produced(env: _Environment) -> None:
    # 14. SEMANTIC_ENRICHMENT_BUILT (Prompt 7) no construye SemanticGraph,
    # GraphNode, GraphRelationship, HAS_ENTRY ni HAS_DOMAIN_TERM como
    # relaciones de grafo: eso es alcance de Prompt 8 (SemanticGraphBuilder,
    # todavia no implementado). El contrato GraphNode/GraphRelationship/
    # SemanticGraph ya existe en el repo desde el bootstrap inicial (commit
    # "feat: add domain contracts", ver git log de
    # contracts/semantic_graph.py) como una definicion adelantada para
    # Prompt 8, asi que su sola existencia no es lo que hay que verificar:
    # lo que importa es que esta etapa no lo instancie ni lo use.
    env.run()

    artifact_fields = set(SemanticEnrichmentArtifact.model_fields)
    forbidden_field_names = {
        "graph_nodes",
        "graph_relationships",
        "has_entry",
        "has_domain_term",
        "semantic_graph",
    }
    assert artifact_fields.isdisjoint(forbidden_field_names)

    stage_source = inspect.getsource(stage_module)
    assert "GraphNode" not in stage_source
    assert "GraphRelationship" not in stage_source
    assert "SemanticGraph" not in stage_source
    assert "semantic_graph_builder" not in stage_source

    # No se escribe ningun artefacto de grafo junto al resto de artifacts/.
    artifacts_dir = env.semantic_enrichment_path.parent
    assert [p.name for p in artifacts_dir.glob("*graph*")] == []


def test_second_run_via_runner_keeps_single_stage_execution(env: _Environment) -> None:
    # 16. Ejecutar la etapa dos veces a traves del runner (que es quien
    # administra RunState.stages) nunca deja dos StageExecution para
    # SEMANTIC_ENRICHMENT_BUILT: _upsert_stage siempre reemplaza la entrada
    # existente para esa etapa.
    state = RunState(
        run_id="run-semantic-enrichment-dedicated",
        package_filename="input/package.zip",
        source_package_hash=VALID_HASH,
        current_stage=PipelineStage.DEPENDENCIES_BUILT,
        stages=[env.dependencies_built_stage()],
        created_at=NOW,
        updated_at=NOW,
    )

    state = runner_module._run_semantic_enrichment_built(
        state,
        env.inventory_path,
        env.extracted_dir,
        env.canonical_dir,
        env.semantic_enrichment_path,
        env.settings,
        env.run_json_path,
    )
    state = runner_module._run_semantic_enrichment_built(
        state,
        env.inventory_path,
        env.extracted_dir,
        env.canonical_dir,
        env.semantic_enrichment_path,
        env.settings,
        env.run_json_path,
    )

    assert state.current_stage == PipelineStage.SEMANTIC_ENRICHMENT_BUILT
    matching = [s for s in state.stages if s.stage == PipelineStage.SEMANTIC_ENRICHMENT_BUILT]
    assert len(matching) == 1
    assert matching[0].status == StageStatus.SUCCEEDED

    persisted = RunState.model_validate_json(env.run_json_path.read_text(encoding="utf-8"))
    assert persisted == state


def test_second_run_does_not_rewrite_artifact_when_identical(
    env: _Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 17. Cuando el resultado recomputado es identico al artefacto
    # existente, la segunda corrida no reescribe el archivo: se verifica
    # con bytes antes/despues, inode (el filesystem del contenedor Linux
    # lo soporta) y un spy sobre atomic_write_json.
    env.run()
    assert env.semantic_enrichment_path.is_file()
    first_bytes = env.semantic_enrichment_path.read_bytes()
    first_inode = env.semantic_enrichment_path.stat().st_ino

    write_calls: list[Path] = []
    original_atomic_write_json = stage_module.atomic_write_json

    def _spy_atomic_write_json(path: Path, model: object) -> None:
        write_calls.append(path)
        original_atomic_write_json(path, model)  # type: ignore[arg-type]

    monkeypatch.setattr(stage_module, "atomic_write_json", _spy_atomic_write_json)

    second_warnings = env.run()

    assert write_calls == []  # nunca se llamo a atomic_write_json en la segunda corrida.
    assert env.semantic_enrichment_path.read_bytes() == first_bytes
    assert env.semantic_enrichment_path.stat().st_ino == first_inode
    assert second_warnings  # se devuelven los warnings del artefacto existente, no una lista vacia.
