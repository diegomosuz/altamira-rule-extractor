"""Tests unitarios de semantic_enrichment_stage: precondiciones,
integridad DDL/CSV, idempotencia. Sin JAR, sin subprocess."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from altamira_extractor.config import Settings
from altamira_extractor.contracts.canonical import CanonicalProgram
from altamira_extractor.contracts.enums import (
    InventoryFileKind,
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
from altamira_extractor.contracts.run_state import StageExecution
from altamira_extractor.contracts.semantic_enrichment import SemanticEnrichmentArtifact
from altamira_extractor.pipeline.errors import SemanticEnrichmentBuildError
from altamira_extractor.pipeline.semantic_enrichment_stage import run_semantic_enrichment_stage

VALID_HASH = "a" * 64
PROGRAM_SOURCE_HASH = "b" * 64
NOW = datetime(2026, 1, 1, tzinfo=UTC)

_SEMANTIC_TAGS_YAML = """
version: "1.0"
allowed_tags: [amount]
rules:
  - id: amount-name
    tag: amount
    name_regex: "(?i).*IMPORTE.*"
    base_confidence: 0.8
"""

_DOMAIN_GLOSSARY_YAML = """
version: "1.0"
terms:
  - key: requested_amount
    functional_name: "importe solicitado"
    definition: "Monto solicitado."
    entity_type: "monetary_amount"
    authoritative_source: "V1 controlled glossary"
    source_kind: "CURATED_CONFIG"
    match:
      semantic_tags: [amount]
"""

_DDL_TEXT = "CREATE TABLE PARAM_TRANSFER (ID INTEGER NOT NULL, LIMITE DECIMAL(9,2));"
_CSV_TEXT = "ID,LIMITE\n1,1000.00\n"
COBOL_RELATIVE_PATH = "01-codigo/cobol/PROG1.cbl"
DDL_RELATIVE_PATH = "02-parametria/ddl/PARAM_TRANSFER.sql"
SNAPSHOT_RELATIVE_PATH = "02-parametria/snapshots/PARAM_TRANSFER_20260101.csv"


def _manifest(
    *, with_parameter_table: bool = True, with_ddl: bool = True, with_snapshot: bool = True
) -> Manifest:
    return Manifest(
        schema_version="1.0",
        country=ManifestCountry(code="AR", name="Argentina"),
        application=ManifestApplication(name="Transferencias"),
        operation=ManifestOperation(logical_name="OP-TRF-PROPIA", description=None),
        implementation=ManifestImplementation(version="1.0", entry_programs=["PROG1"]),
        source=ManifestSource(format=SourceFormat.FIXED, encoding="UTF-8"),
        parameter_tables=(
            [
                ManifestParameterTable(
                    name="PARAM_TRANSFER",
                    ddl=DDL_RELATIVE_PATH if with_ddl else None,
                    snapshot=SNAPSHOT_RELATIVE_PATH if with_snapshot else None,
                    snapshot_date=date(2026, 1, 1) if with_snapshot else None,
                )
            ]
            if with_parameter_table
            else []
        ),
    )


def _canonical_program() -> CanonicalProgram:
    return CanonicalProgram(
        program_name="PROG1",
        source_file=COBOL_RELATIVE_PATH,
        source_hash=PROGRAM_SOURCE_HASH,
        source_package_hash=VALID_HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        data_items=[],
        paragraphs=[],
    )


def _dependencies_built_succeeded_stage() -> StageExecution:
    return StageExecution(
        stage=PipelineStage.DEPENDENCIES_BUILT,
        status=StageStatus.SUCCEEDED,
        started_at=NOW,
        finished_at=NOW,
        duration_seconds=1.0,
    )


def _dependencies_built_failed_stage() -> StageExecution:
    return StageExecution(
        stage=PipelineStage.DEPENDENCIES_BUILT,
        status=StageStatus.FAILED,
        started_at=NOW,
        finished_at=NOW,
        error="boom",
    )


class _Env:
    def __init__(
        self,
        tmp_path: Path,
        *,
        with_parameter_table: bool = True,
        with_ddl: bool = True,
        with_snapshot: bool = True,
    ) -> None:
        self.tmp_path = tmp_path
        self.extracted_dir = tmp_path / "work" / "extracted"
        self.canonical_dir = tmp_path / "artifacts" / "02-canonical"
        self.semantic_enrichment_path = tmp_path / "artifacts" / "03b-semantic-enrichment.json"
        self.extracted_dir.mkdir(parents=True)
        self.canonical_dir.mkdir(parents=True)

        cobol_path = self.extracted_dir / COBOL_RELATIVE_PATH
        cobol_path.parent.mkdir(parents=True, exist_ok=True)
        cobol_path.write_bytes(b"       IDENTIFICATION DIVISION.\n")

        canonical_path = self.canonical_dir / f"{COBOL_RELATIVE_PATH}.json"
        canonical_path.parent.mkdir(parents=True, exist_ok=True)
        canonical_path.write_text(_canonical_program().to_stable_json(), encoding="utf-8")

        files = [
            InventoryFile(
                relative_path=COBOL_RELATIVE_PATH,
                kind=InventoryFileKind.COBOL,
                size_bytes=len(cobol_path.read_bytes()),
                sha256=PROGRAM_SOURCE_HASH,
                detected_encoding=TextEncoding.UTF_8,
            )
        ]

        if with_parameter_table and with_ddl:
            ddl_path = self.extracted_dir / DDL_RELATIVE_PATH
            ddl_path.parent.mkdir(parents=True, exist_ok=True)
            ddl_path.write_text(_DDL_TEXT, encoding="utf-8")
            files.append(
                InventoryFile(
                    relative_path=DDL_RELATIVE_PATH,
                    kind=InventoryFileKind.DDL,
                    size_bytes=len(ddl_path.read_bytes()),
                    sha256=hashlib.sha256(ddl_path.read_bytes()).hexdigest(),
                    detected_encoding=TextEncoding.UTF_8,
                )
            )

        if with_parameter_table and with_snapshot:
            snapshot_path = self.extracted_dir / SNAPSHOT_RELATIVE_PATH
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            snapshot_path.write_text(_CSV_TEXT, encoding="utf-8")
            files.append(
                InventoryFile(
                    relative_path=SNAPSHOT_RELATIVE_PATH,
                    kind=InventoryFileKind.SNAPSHOT,
                    size_bytes=len(snapshot_path.read_bytes()),
                    sha256=hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
                    detected_encoding=TextEncoding.UTF_8,
                )
            )

        self.inventory = Inventory(
            run_id="run-1",
            source_package_hash=VALID_HASH,
            manifest=_manifest(
                with_parameter_table=with_parameter_table,
                with_ddl=with_ddl,
                with_snapshot=with_snapshot,
            ),
            files=files,
        )

        self.settings = Settings(
            data_dir=tmp_path / "data",
            runs_dir=tmp_path / "data" / "runs",
            incoming_dir=tmp_path / "data" / "incoming",
            semantic_tags_path=_write(tmp_path / "semantic-tags.yml", _SEMANTIC_TAGS_YAML),
            domain_glossary_path=_write(
                tmp_path / "domain-glossary.example.yml", _DOMAIN_GLOSSARY_YAML
            ),
        )

    def run(self, *, run_stages: list[StageExecution] | None = None) -> list[str]:
        if run_stages is None:
            run_stages = [_dependencies_built_succeeded_stage()]
        return run_semantic_enrichment_stage(
            run_id="run-1",
            source_package_hash=VALID_HASH,
            run_stages=run_stages,
            inventory=self.inventory,
            extracted_dir=self.extracted_dir,
            canonical_dir=self.canonical_dir,
            settings=self.settings,
            semantic_enrichment_path=self.semantic_enrichment_path,
        )


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


# --- Precondiciones ---


def test_valid_setup_builds_artifact(tmp_path: Path) -> None:
    env = _Env(tmp_path)

    warnings = env.run()

    assert warnings == []
    assert env.semantic_enrichment_path.is_file()
    artifact = SemanticEnrichmentArtifact.model_validate_json(
        env.semantic_enrichment_path.read_text(encoding="utf-8")
    )
    assert len(artifact.parameter_tables) == 1
    table = artifact.parameter_tables[0]
    assert table.name == "PARAM_TRANSFER"
    assert len(table.columns) == 2
    assert len(table.entries) == 1


def test_dependencies_built_not_succeeded_is_fatal(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    with pytest.raises(SemanticEnrichmentBuildError):
        env.run(run_stages=[_dependencies_built_failed_stage()])


def test_missing_dependencies_built_stage_is_fatal(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    with pytest.raises(SemanticEnrichmentBuildError):
        env.run(run_stages=[])


def test_duplicate_dependencies_built_stage_is_fatal(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    with pytest.raises(SemanticEnrichmentBuildError):
        env.run(
            run_stages=[
                _dependencies_built_succeeded_stage(),
                _dependencies_built_succeeded_stage(),
            ]
        )


def test_missing_canonical_program_is_fatal(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    (env.canonical_dir / f"{COBOL_RELATIVE_PATH}.json").unlink()
    with pytest.raises(SemanticEnrichmentBuildError):
        env.run()


# --- Integridad DDL/CSV ---


def test_ddl_declared_but_missing_from_inventory_is_fatal(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    # Se declara un DDL que no aparece en Inventory.files.
    env.inventory = env.inventory.model_copy(
        update={
            "manifest": env.inventory.manifest.model_copy(
                update={
                    "parameter_tables": [
                        ManifestParameterTable(name="PARAM_TRANSFER", ddl="no/existe.sql")
                    ]
                }
            )
        }
    )
    with pytest.raises(SemanticEnrichmentBuildError):
        env.run()


def test_ddl_hash_mismatch_is_fatal(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    # El archivo cambio desde INVENTORIED: el hash real ya no coincide.
    (env.extracted_dir / DDL_RELATIVE_PATH).write_text(
        _DDL_TEXT + "\n-- modificado", encoding="utf-8"
    )
    with pytest.raises(SemanticEnrichmentBuildError):
        env.run()
    assert not env.semantic_enrichment_path.exists()


def test_csv_declared_but_missing_from_inventory_is_fatal(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    # Se declara un snapshot que no aparece en Inventory.files.
    env.inventory = env.inventory.model_copy(
        update={
            "manifest": env.inventory.manifest.model_copy(
                update={
                    "parameter_tables": [
                        ManifestParameterTable(
                            name="PARAM_TRANSFER",
                            ddl=DDL_RELATIVE_PATH,
                            snapshot="02-parametria/snapshots/no-existe.csv",
                        )
                    ]
                }
            )
        }
    )
    with pytest.raises(SemanticEnrichmentBuildError):
        env.run()


def test_csv_hash_mismatch_is_fatal(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    # El snapshot cambio desde INVENTORIED: el hash real ya no coincide.
    (env.extracted_dir / SNAPSHOT_RELATIVE_PATH).write_text(
        _CSV_TEXT + "3,3000.00\n", encoding="utf-8"
    )
    with pytest.raises(SemanticEnrichmentBuildError):
        env.run()
    assert not env.semantic_enrichment_path.exists()


def test_declared_ddl_file_not_regular_is_fatal(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    ddl_path = env.extracted_dir / DDL_RELATIVE_PATH
    ddl_path.unlink()
    ddl_path.mkdir()  # el path declarado ahora es un directorio, no un archivo regular.

    with pytest.raises(SemanticEnrichmentBuildError):
        env.run()


def test_declared_csv_file_not_regular_is_fatal(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    snapshot_path = env.extracted_dir / SNAPSHOT_RELATIVE_PATH
    snapshot_path.unlink()
    snapshot_path.mkdir()

    with pytest.raises(SemanticEnrichmentBuildError):
        env.run()


def test_declared_ddl_symlink_escaping_extracted_dir_is_fatal(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    outside_target = tmp_path / "outside-extracted" / "evil.sql"
    outside_target.parent.mkdir(parents=True)
    outside_target.write_text(_DDL_TEXT, encoding="utf-8")

    ddl_path = env.extracted_dir / DDL_RELATIVE_PATH
    ddl_path.unlink()
    ddl_path.symlink_to(outside_target)

    # El InventoryFile.sha256 persistido sigue siendo el del contenido
    # original (identico al de outside_target en este caso): el fallo debe
    # provenir de la contencion de path, no de un hash distinto.
    with pytest.raises(SemanticEnrichmentBuildError, match="escapa"):
        env.run()


def test_ddl_encoding_unresolved_is_unsupported_not_fatal(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    ddl_file = next(f for f in env.inventory.files if f.relative_path == DDL_RELATIVE_PATH)
    updated_files = [
        f.model_copy(update={"detected_encoding": None}) if f is ddl_file else f
        for f in env.inventory.files
    ]
    env.inventory = env.inventory.model_copy(update={"files": updated_files})

    warnings = env.run()

    artifact = SemanticEnrichmentArtifact.model_validate_json(
        env.semantic_enrichment_path.read_text(encoding="utf-8")
    )
    table = artifact.parameter_tables[0]
    assert table.ddl_support_status is not None
    assert table.ddl_support_status.value == "UNSUPPORTED"
    assert table.columns == []
    assert any("DDL" in w and "encoding" in w for w in warnings)


def test_snapshot_not_declared_leaves_status_none_without_warning(tmp_path: Path) -> None:
    env = _Env(tmp_path, with_snapshot=False)

    warnings = env.run()

    assert warnings == []
    artifact = SemanticEnrichmentArtifact.model_validate_json(
        env.semantic_enrichment_path.read_text(encoding="utf-8")
    )
    table = artifact.parameter_tables[0]
    assert table.snapshot_support_status is None
    assert table.entries == []
    assert table.snapshot_relative_path is None
    assert table.snapshot_hash is None
    # El DDL si esta declarado en este escenario: su status es real, no None.
    assert table.ddl_support_status is not None
    assert table.ddl_support_status.value == "SUPPORTED"


def test_ddl_not_declared_leaves_status_none_without_warning(tmp_path: Path) -> None:
    env = _Env(tmp_path, with_ddl=False)

    warnings = env.run()

    assert warnings == []
    artifact = SemanticEnrichmentArtifact.model_validate_json(
        env.semantic_enrichment_path.read_text(encoding="utf-8")
    )
    table = artifact.parameter_tables[0]
    assert table.ddl_support_status is None
    assert table.columns == []
    assert table.ddl_relative_path is None
    assert table.ddl_hash is None
    # El snapshot si esta declarado en este escenario: su status es real.
    assert table.snapshot_support_status is not None
    assert table.snapshot_support_status.value == "SUPPORTED"
    assert len(table.entries) == 1


def test_snapshot_encoding_unresolved_is_unsupported_not_fatal(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    snapshot_file = next(
        f for f in env.inventory.files if f.relative_path == SNAPSHOT_RELATIVE_PATH
    )
    updated_files = [
        f.model_copy(update={"detected_encoding": None}) if f is snapshot_file else f
        for f in env.inventory.files
    ]
    env.inventory = env.inventory.model_copy(update={"files": updated_files})

    warnings = env.run()

    artifact = SemanticEnrichmentArtifact.model_validate_json(
        env.semantic_enrichment_path.read_text(encoding="utf-8")
    )
    table = artifact.parameter_tables[0]
    assert table.snapshot_support_status is not None
    assert table.snapshot_support_status.value == "UNSUPPORTED"
    assert table.entries == []
    assert any("snapshot" in w and "encoding" in w for w in warnings)


def test_valid_ddl_and_snapshot_are_supported(tmp_path: Path) -> None:
    env = _Env(tmp_path)

    env.run()

    artifact = SemanticEnrichmentArtifact.model_validate_json(
        env.semantic_enrichment_path.read_text(encoding="utf-8")
    )
    table = artifact.parameter_tables[0]
    assert table.ddl_support_status is not None
    assert table.ddl_support_status.value == "SUPPORTED"
    assert table.snapshot_support_status is not None
    assert table.snapshot_support_status.value == "SUPPORTED"


def test_no_parameter_tables_declared_produces_empty_list(tmp_path: Path) -> None:
    env = _Env(tmp_path, with_parameter_table=False)

    warnings = env.run()

    assert warnings == []
    artifact = SemanticEnrichmentArtifact.model_validate_json(
        env.semantic_enrichment_path.read_text(encoding="utf-8")
    )
    assert artifact.parameter_tables == []


# --- Idempotencia ---


def test_valid_existing_artifact_is_reused(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.run()
    first_bytes = env.semantic_enrichment_path.read_bytes()

    env.run()

    assert env.semantic_enrichment_path.read_bytes() == first_bytes


def test_modified_semantic_tags_config_forces_rebuild(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.run()
    first = SemanticEnrichmentArtifact.model_validate_json(
        env.semantic_enrichment_path.read_text(encoding="utf-8")
    )

    _write(
        env.settings.semantic_tags_path,
        _SEMANTIC_TAGS_YAML.replace("base_confidence: 0.8", "base_confidence: 0.9"),
    )
    env.run()
    second = SemanticEnrichmentArtifact.model_validate_json(
        env.semantic_enrichment_path.read_text(encoding="utf-8")
    )

    assert second.semantic_tags_config_hash != first.semantic_tags_config_hash


def test_modified_ddl_forces_failure_by_hash_mismatch(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.run()
    original_bytes = env.semantic_enrichment_path.read_bytes()

    (env.extracted_dir / DDL_RELATIVE_PATH).write_text(
        _DDL_TEXT + "\n-- cambiado", encoding="utf-8"
    )

    with pytest.raises(SemanticEnrichmentBuildError):
        env.run()

    # El artefacto valido previo no se reutiliza (el DDL ya no coincide con
    # el hash que lo respalda) ni se sobreescribe con uno nuevo: queda
    # exactamente como estaba antes del intento fallido.
    assert env.semantic_enrichment_path.read_bytes() == original_bytes


def test_modified_csv_forces_failure_by_hash_mismatch(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.run()
    original_bytes = env.semantic_enrichment_path.read_bytes()

    (env.extracted_dir / SNAPSHOT_RELATIVE_PATH).write_text(
        _CSV_TEXT + "3,3000.00\n", encoding="utf-8"
    )

    with pytest.raises(SemanticEnrichmentBuildError):
        env.run()

    assert env.semantic_enrichment_path.read_bytes() == original_bytes


def test_corrupt_artifact_is_rebuilt(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.semantic_enrichment_path.write_text("{not valid json", encoding="utf-8")

    warnings = env.run()

    assert warnings == []
    artifact = SemanticEnrichmentArtifact.model_validate_json(
        env.semantic_enrichment_path.read_text(encoding="utf-8")
    )
    assert len(artifact.parameter_tables) == 1
