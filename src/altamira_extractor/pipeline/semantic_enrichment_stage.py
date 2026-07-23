"""Orquestacion de la etapa SEMANTIC_ENRICHMENT_BUILT:
DEPENDENCIES_BUILT -> artifacts/03b-semantic-enrichment.json.

Precondiciones (todas fatales si fallan: `SemanticEnrichmentBuildError`,
sin reintento parcial):

- DEPENDENCIES_BUILT tiene exactamente una `StageExecution` `SUCCEEDED`.
- Existe un `CanonicalProgram` valido por cada `InventoryFile` COBOL,
  consistente con Inventory/RunState (reutiliza la misma verificacion que
  `dependencies_stage.py`).
- Cada DDL/snapshot declarado en `Manifest.parameter_tables` se localiza
  por `relative_path` en Inventory, existe como archivo regular dentro de
  `work/extracted`, y su SHA-256 recalculado coincide con
  `InventoryFile.sha256` (nunca se confia solo en el hash ya persistido).
- Los YAML de configuracion (`semantic_tags_path`/`domain_glossary_path`)
  existen y validan.

No se invoca Java ni se repara ninguna etapa anterior desde aqui.

Idempotencia: se recomputa siempre el artefacto completo (recomputacion
pura, sin subprocess, barata) y se compara estructuralmente contra
`artifacts/03b-semantic-enrichment.json` si ya existe; si son iguales, no
se reescribe (evidencia de que run_id, source_package_hash, hashes de
config YAML, hashes de DDL/CSV y data_item_id siguen siendo consistentes:
cualquier diferencia en esos insumos cambiaria el resultado). Si difieren
o el archivo previo no valida, se sobreescribe atomicamente.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

from ..config import Settings
from ..contracts.canonical import CanonicalProgram
from ..contracts.enums import ParseSupportStatus, PipelineStage, StageStatus
from ..contracts.inventory import Inventory
from ..contracts.run_state import StageExecution
from ..contracts.semantic_enrichment import (
    DataItemSemanticTag,
    ParameterColumnDefinition,
    ParameterEntryRecord,
    ParameterTableRecord,
    SemanticEnrichmentArtifact,
)
from .artifact_store import atomic_write_json
from .csv_loader import load_csv_snapshot, normalize_csv_header
from .ddl_parser import parse_ddl_for_table
from .dependencies_stage import _load_and_validate_canonical_programs
from .domain_term_mapper import load_domain_glossary, map_data_item_tags_to_domain_terms
from .errors import DependencyBuildError, SemanticEnrichmentBuildError
from .identifiers import ProgramIdentity, normalize_identifier
from .identifiers import parameter_table_id as _compute_parameter_table_id
from .identifiers import table_id as _compute_table_id
from .parser_client import _is_contained
from .semantic_tagger import load_semantic_tags_config, tag_data_items


def _verify_dependencies_built_precondition(stages: list[StageExecution]) -> None:
    matches = [stage for stage in stages if stage.stage == PipelineStage.DEPENDENCIES_BUILT]
    if len(matches) != 1:
        raise SemanticEnrichmentBuildError(
            "DEPENDENCIES_BUILT debe tener exactamente una StageExecution en RunState; "
            f"se encontraron {len(matches)}"
        )
    if matches[0].status != StageStatus.SUCCEEDED:
        raise SemanticEnrichmentBuildError(
            f"DEPENDENCIES_BUILT no esta SUCCEEDED (status={matches[0].status.value}); no "
            "se puede construir el enriquecimiento semantico sobre dependencias incompletas"
        )


def _verify_and_read_declared_file(
    *, relative_path: str, inventory: Inventory, extracted_dir: Path, file_label: str
) -> tuple[bytes, str]:
    """Localiza el InventoryFile por relative_path, verifica integridad
    (existe, es archivo regular, permanece dentro de work/extracted, hash
    recalculado coincide) y devuelve `(bytes, sha256)`. Cualquier
    violacion es fatal para la etapa completa."""
    matching = next((f for f in inventory.files if f.relative_path == relative_path), None)
    if matching is None:
        raise SemanticEnrichmentBuildError(
            f"{file_label}: {relative_path!r} declarado en Manifest no aparece en Inventory"
        )

    absolute_path = extracted_dir / relative_path
    if not absolute_path.is_file():
        raise SemanticEnrichmentBuildError(
            f"{file_label}: el archivo declarado no existe o no es un archivo regular"
        )
    if not _is_contained(absolute_path, extracted_dir):
        raise SemanticEnrichmentBuildError(
            f"{file_label}: la ruta resuelta escapa de work/extracted"
        )

    data = absolute_path.read_bytes()
    actual_hash = hashlib.sha256(data).hexdigest()
    if actual_hash != matching.sha256:
        raise SemanticEnrichmentBuildError(
            f"{file_label}: el SHA-256 recalculado no coincide con InventoryFile.sha256 "
            "(cambio desde INVENTORIED)"
        )
    return data, matching.sha256


def _process_parameter_table(
    declared_name: str,
    declared_ddl: str | None,
    declared_snapshot: str | None,
    declared_snapshot_date: date | None,
    *,
    country_code: str,
    inventory: Inventory,
    extracted_dir: Path,
    max_rows: int,
    warnings: list[str],
) -> ParameterTableRecord:
    table_label = declared_name
    table_id_value = _compute_table_id(country_code, declared_name)

    columns: list[ParameterColumnDefinition] = []
    ddl_hash: str | None = None
    # None: DDL no declarado en Manifest, nunca se intenta cargar (no es un
    # error ni un caso "no soportado"). Solo se asigna un ParseSupportStatus
    # real cuando el DDL SI esta declarado.
    ddl_status: ParseSupportStatus | None = None

    if declared_ddl is not None:
        ddl_bytes, ddl_hash = _verify_and_read_declared_file(
            relative_path=declared_ddl,
            inventory=inventory,
            extracted_dir=extracted_dir,
            file_label=f"{table_label} (DDL)",
        )
        ddl_file = next(f for f in inventory.files if f.relative_path == declared_ddl)
        if ddl_file.detected_encoding is None:
            warnings.append(f"{table_label}: encoding del DDL no resuelto; DDL no soportado")
            ddl_status = ParseSupportStatus.UNSUPPORTED
        else:
            try:
                ddl_text = ddl_bytes.decode(ddl_file.detected_encoding.value)
            except UnicodeDecodeError as exc:
                warnings.append(f"{table_label}: no se pudo decodificar el DDL: {exc}")
                ddl_status = ParseSupportStatus.UNSUPPORTED
            else:
                parsed = parse_ddl_for_table(
                    ddl_text,
                    declared_table_name=declared_name,
                    table_label=table_label,
                    warnings=warnings,
                )
                columns = parsed.columns
                ddl_status = parsed.support_status

    entries: list[ParameterEntryRecord] = []
    snapshot_hash: str | None = None
    # Mismo criterio que ddl_status: None solo cuando el snapshot no fue
    # declarado en Manifest.
    snapshot_status: ParseSupportStatus | None = None
    csv_headers: list[str] = []

    if declared_snapshot is not None:
        snapshot_bytes, snapshot_hash = _verify_and_read_declared_file(
            relative_path=declared_snapshot,
            inventory=inventory,
            extracted_dir=extracted_dir,
            file_label=f"{table_label} (snapshot)",
        )
        snapshot_file = next(f for f in inventory.files if f.relative_path == declared_snapshot)
        parameter_table_id_value = _compute_parameter_table_id(
            table_id_value, declared_snapshot_date, snapshot_hash
        )
        if snapshot_file.detected_encoding is None:
            warnings.append(
                f"{table_label}: encoding del snapshot no resuelto; snapshot no soportado"
            )
            snapshot_status = ParseSupportStatus.UNSUPPORTED
        else:
            csv_result = load_csv_snapshot(
                snapshot_bytes,
                encoding=snapshot_file.detected_encoding.value,
                parameter_table_id=parameter_table_id_value,
                max_rows=max_rows,
                table_label=table_label,
                warnings=warnings,
            )
            entries = csv_result.entries
            snapshot_status = csv_result.support_status
            csv_headers = csv_result.original_headers
    else:
        parameter_table_id_value = _compute_parameter_table_id(
            table_id_value, declared_snapshot_date, None
        )

    if columns and csv_headers:
        ddl_names = {column.normalized_name for column in columns}
        csv_names = {normalize_csv_header(header) for header in csv_headers}
        for name in sorted(ddl_names - csv_names):
            warnings.append(f"{table_label}: columna DDL {name!r} ausente en el snapshot CSV")
        for name in sorted(csv_names - ddl_names):
            warnings.append(f"{table_label}: columna CSV {name!r} no declarada en el DDL")

    return ParameterTableRecord(
        table_id=table_id_value,
        parameter_table_id=parameter_table_id_value,
        name=declared_name,
        normalized_name=normalize_identifier(declared_name),
        country_code=country_code,
        ddl_relative_path=declared_ddl,
        snapshot_relative_path=declared_snapshot,
        snapshot_date=declared_snapshot_date,
        ddl_hash=ddl_hash,
        snapshot_hash=snapshot_hash,
        ddl_support_status=ddl_status,
        snapshot_support_status=snapshot_status,
        columns=columns,
        entries=entries,
    )


def _load_parameter_tables(
    *, inventory: Inventory, extracted_dir: Path, settings: Settings, warnings: list[str]
) -> list[ParameterTableRecord]:
    country_code = inventory.manifest.country.code
    records = [
        _process_parameter_table(
            declared.name,
            declared.ddl,
            declared.snapshot,
            declared.snapshot_date,
            country_code=country_code,
            inventory=inventory,
            extracted_dir=extracted_dir,
            max_rows=settings.max_parameter_entries_per_table,
            warnings=warnings,
        )
        for declared in inventory.manifest.parameter_tables
    ]
    records.sort(key=lambda record: record.normalized_name)
    return records


def run_semantic_enrichment_stage(
    *,
    run_id: str,
    source_package_hash: str,
    run_stages: list[StageExecution],
    inventory: Inventory,
    extracted_dir: Path,
    canonical_dir: Path,
    settings: Settings,
    semantic_enrichment_path: Path,
) -> list[str]:
    """Ejecuta SEMANTIC_ENRICHMENT_BUILT y devuelve los warnings (resumen
    para RunState; el detalle completo vive en `semantic_enrichment_path`).
    """
    _verify_dependencies_built_precondition(run_stages)

    try:
        programs: list[CanonicalProgram] = _load_and_validate_canonical_programs(
            canonical_dir=canonical_dir,
            inventory=inventory,
            source_package_hash=source_package_hash,
        )
    except DependencyBuildError as exc:
        # Mismo chequeo de precondicion que DEPENDENCIES_BUILT ya aplico
        # sobre CanonicalProgram; se reexpone con el tipo de excepcion
        # propio de esta etapa para que el llamador (runner.py) tenga un
        # unico tipo que capturar.
        raise SemanticEnrichmentBuildError(str(exc)) from exc

    semantic_tags_config = load_semantic_tags_config(settings.semantic_tags_path)
    domain_glossary = load_domain_glossary(settings.domain_glossary_path)

    identity = ProgramIdentity(
        country_code=inventory.manifest.country.code,
        logical_name=inventory.manifest.operation.logical_name,
        version=inventory.manifest.implementation.version,
    )

    warnings: list[str] = []

    parameter_tables = _load_parameter_tables(
        inventory=inventory, extracted_dir=extracted_dir, settings=settings, warnings=warnings
    )

    data_item_tags: list[DataItemSemanticTag] = []
    for program in programs:
        data_item_tags.extend(tag_data_items(program, identity, semantic_tags_config, warnings))
    data_item_tags.sort(key=lambda tag: tag.data_item_id)

    mappings = map_data_item_tags_to_domain_terms(data_item_tags, domain_glossary, warnings)

    fresh_artifact = SemanticEnrichmentArtifact(
        run_id=run_id,
        source_package_hash=source_package_hash,
        semantic_tags_config_hash=semantic_tags_config.config_hash,
        domain_glossary_config_hash=domain_glossary.config_hash,
        parameter_tables=parameter_tables,
        data_item_tags=data_item_tags,
        domain_terms=domain_glossary.terms,
        data_item_domain_term_mappings=mappings,
        warnings=sorted(set(warnings)),
    )

    if semantic_enrichment_path.is_file():
        try:
            existing = SemanticEnrichmentArtifact.model_validate_json(
                semantic_enrichment_path.read_text(encoding="utf-8")
            )
        except ValueError:
            existing = None
        if existing is not None and existing == fresh_artifact:
            return list(existing.warnings)

    atomic_write_json(semantic_enrichment_path, fresh_artifact)
    return list(fresh_artifact.warnings)
