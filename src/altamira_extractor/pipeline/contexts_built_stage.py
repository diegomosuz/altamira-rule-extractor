"""Orquestacion de la etapa CONTEXTS_BUILT:
CANDIDATES_DETECTED -> artifacts/07-context/ (Prompt 10b).

`ContextPackageBuilder` es de solo lectura sobre un grafo ya cargado y
validado: nunca repara drift, nunca reejecuta Q0/CandidateDetector ni
`invariants.cypher`. Q1-Q7 se ejecutan para TODOS los candidatos dentro
de una unica transaccion de lectura (`Neo4jRepository.run_in_read_transaction`),
nunca una sesion/transaccion por query ni por candidato — garantiza una
vista consistente del mismo grafo activo. La metadata activa se relee
despues de cerrar la transaccion: si `semantic_graph_hash`,
`source_package_hash` o los conteos cambiaron mientras se leia (carga
concurrente), se descartan todos los contextos construidos y la etapa
falla — nunca se mezclan contextos de dos versiones del grafo.

`artifacts/07-context/context-manifest.json` es la unica fuente de
provenance de que texto exacto de Q1-Q7 goberno una ejecucion: ni
`ContextPackage` ni `context-package.schema.json` ganan campos de hash de
query (harian a cada archivo individual depender de detalles de
ejecucion ajenos a su propio contenido); el manifiesto cubre ese hueco a
nivel de directorio.

Fase 15B3-C3-C-B: tambien se carga `artifacts/02-canonical/` una vez
(mismo patron que `candidates_detected_stage._load_canonical_programs_
for_enhanced_detection`) para el enriquecimiento evidencial de Decisions
SQLCODE y (Fase 15B3-C5-B) declared_value -- ver
`_load_canonical_indices_for_context_enrichment`.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path

import jsonschema
import neo4j
from jsonschema.exceptions import SchemaError

from ..config import Settings
from ..contracts.candidate import CandidateArtifact
from ..contracts.canonical import CanonicalDataItem, CanonicalParagraph, CanonicalProgram
from ..contracts.context_manifest import ContextDirectoryManifest, ContextRecord, QueryRecord
from ..contracts.context_package import ContextPackage
from ..contracts.enums import PipelineStage, StageStatus
from ..contracts.run_state import StageExecution
from .candidates_detected_stage import load_and_validate_candidate_artifact
from .context_package_builder import ContextQuerySet, build_context_packages
from .cypher_query_loader import LoadedContextQuery, load_context_query
from .errors import CandidateDetectionError, ContextBuildError, GraphLoadError, Neo4jError
from .neo4j_repository import Neo4jRepository
from .semantic_graph_load_stage import load_and_validate_semantic_graph

_CONTEXT_DIR_NAME = "07-context"
_MANIFEST_FILENAME = "context-manifest.json"

# (settings_attr, filename, logical_query, expected_placeholder_count)
_QUERY_SPECS: tuple[tuple[str, str, str, int], ...] = (
    ("q1_scope_cypher_path", "q1_scope.cypher", "Q1", 0),
    ("q2_code_slice_cypher_path", "q2_code_slice.cypher", "Q2", 2),
    ("q3a_parameter_context_cypher_path", "q3a_parameter_context.cypher", "Q3A", 1),
    ("q3b_transactional_tables_cypher_path", "q3b_transactional_tables.cypher", "Q3B", 1),
    ("q4_decision_cypher_path", "q4_decision.cypher", "Q4", 0),
    ("q5a_return_effect_cypher_path", "q5a_return_effect.cypher", "Q5A", 0),
    ("q5b_table_effects_cypher_path", "q5b_table_effects.cypher", "Q5B", 1),
    ("q6_batch_cypher_path", "q6_batch.cypher", "Q6", 0),
    ("q7_glossary_cypher_path", "q7_glossary.cypher", "Q7", 0),
)


def _verify_candidates_detected_precondition(stages: list[StageExecution]) -> None:
    matches = [stage for stage in stages if stage.stage == PipelineStage.CANDIDATES_DETECTED]
    if len(matches) != 1:
        raise ContextBuildError(
            "CANDIDATES_DETECTED debe tener exactamente una StageExecution en RunState; "
            f"se encontraron {len(matches)}"
        )
    if matches[0].status != StageStatus.SUCCEEDED:
        raise ContextBuildError(
            f"CANDIDATES_DETECTED no esta SUCCEEDED (status={matches[0].status.value}); no se "
            "pueden construir contextos sobre candidatos no detectados"
        )


def _load_all_context_queries(
    settings: Settings,
) -> tuple[ContextQuerySet, list[QueryRecord]]:
    loaded: dict[str, LoadedContextQuery] = {}
    query_records: list[QueryRecord] = []
    for settings_attr, filename, logical_query, expected_placeholders in _QUERY_SPECS:
        path: Path = getattr(settings, settings_attr)
        loaded_query = load_context_query(
            path,
            relative_path=f"queries/v1/{filename}",
            logical_query=logical_query,
            expected_placeholder_count=expected_placeholders,
            dependency_depth=settings.dependency_depth,
        )
        loaded[logical_query] = loaded_query
        query_records.append(
            QueryRecord(
                logical_query=logical_query,  # type: ignore[arg-type]
                relative_path=loaded_query.relative_path,
                template_hash=loaded_query.template_hash,
                effective_query_hash=loaded_query.effective_query_hash,
            )
        )
    query_set = ContextQuerySet(
        q1=loaded["Q1"],
        q2=loaded["Q2"],
        q3a=loaded["Q3A"],
        q3b=loaded["Q3B"],
        q4=loaded["Q4"],
        q5a=loaded["Q5A"],
        q5b=loaded["Q5B"],
        q6=loaded["Q6"],
        q7=loaded["Q7"],
    )
    query_records.sort(key=lambda record: record.logical_query)
    return query_set, query_records


def _load_context_schema(settings: Settings) -> tuple[dict[str, object], str]:
    if not settings.context_package_schema_path.is_file():
        raise ContextBuildError(
            f"no se encontro {settings.context_package_schema_path.name}"
        )
    raw_bytes = settings.context_package_schema_path.read_bytes()
    context_schema_hash = hashlib.sha256(raw_bytes).hexdigest()
    try:
        schema = json.loads(raw_bytes.decode("utf-8"))
    except ValueError as exc:
        raise ContextBuildError(
            f"{settings.context_package_schema_path.name} no es JSON valido: {exc}"
        ) from exc
    validator_cls = jsonschema.validators.validator_for(schema)
    try:
        validator_cls.check_schema(schema)
    except SchemaError as exc:
        raise ContextBuildError(
            f"{settings.context_package_schema_path.name} no es un JSON Schema valido: {exc}"
        ) from exc
    return schema, context_schema_hash


def _validate_context_package(
    package: ContextPackage, validator: jsonschema.protocols.Validator
) -> None:
    errors = sorted(validator.iter_errors(package.model_dump(mode="json")), key=str)
    if errors:
        details = "; ".join(f"{error.json_path}: {error.message}" for error in errors[:3])
        raise ContextBuildError(
            f"ContextPackage del candidato {package.candidate.candidate_id!r} no valida "
            f"contra context-package.schema.json: {details}"
        )


def _context_filename(candidate_id: str) -> str:
    return hashlib.sha256(candidate_id.encode("utf-8")).hexdigest() + ".json"


def _cleanup_orphans(artifacts_dir: Path) -> None:
    if not artifacts_dir.is_dir():
        return
    for stray in artifacts_dir.glob(f"{_CONTEXT_DIR_NAME}.tmp-*"):
        shutil.rmtree(stray, ignore_errors=True)
    for stray in artifacts_dir.glob(f"{_CONTEXT_DIR_NAME}.backup-*"):
        shutil.rmtree(stray, ignore_errors=True)


def _read_existing_manifest(context_dir: Path) -> ContextDirectoryManifest | None:
    manifest_path = context_dir / _MANIFEST_FILENAME
    if not manifest_path.is_file():
        return None
    try:
        return ContextDirectoryManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
    except ValueError:
        return None


def _swap_context_directory(temp_dir: Path, target_dir: Path) -> None:
    """Reemplaza `target_dir` por `temp_dir` usando unicamente renames
    hacia nombres inexistentes (Windows no soporta reemplazar
    atomicamente un directorio no vacio existente). Si falla el segundo
    rename, intenta restaurar el backup de inmediato; si eso tambien
    falla, conserva ambos directorios para recuperacion manual."""
    if not target_dir.exists():
        os.rename(temp_dir, target_dir)
        return

    backup_dir = target_dir.parent / f"{target_dir.name}.backup-{secrets.token_hex(8)}"
    try:
        os.rename(target_dir, backup_dir)
    except OSError as exc:
        raise ContextBuildError(
            f"no se pudo respaldar {target_dir.name} antes del reemplazo: {type(exc).__name__}"
        ) from exc

    try:
        os.rename(temp_dir, target_dir)
    except OSError as exc:
        try:
            os.rename(backup_dir, target_dir)
        except OSError as restore_exc:
            raise ContextBuildError(
                "fallo el reemplazo Y la restauracion del directorio de contextos; revisar "
                f"manualmente {backup_dir.name!r} y {temp_dir.name!r}: {type(restore_exc).__name__}"
            ) from restore_exc
        raise ContextBuildError(
            f"fallo el reemplazo de {target_dir.name}, se restauro el respaldo previo: "
            f"{type(exc).__name__}"
        ) from exc
    else:
        shutil.rmtree(backup_dir, ignore_errors=True)


@dataclass(frozen=True)
class _CanonicalEnrichmentIndex:
    paragraphs: dict[tuple[str, str], CanonicalParagraph]
    # (program_name, data_items) por source_file -- program_name viaja
    # explicito desde CanonicalProgram (nunca parseado de un id) para que
    # context_package_builder.py pueda escopar la identidad de
    # declared_value_context por programa (correccion pre-commit
    # 15B3-C5-B "declaration provenance identity").
    data_items_by_source_file: dict[str, tuple[str, list[CanonicalDataItem]]]


def _load_canonical_indices_for_context_enrichment(
    canonical_dir: Path,
) -> _CanonicalEnrichmentIndex:
    """Indices para el enriquecimiento evidencial de Decisions (SQLCODE,
    Fase 15B3-C3-C-B, y declared_value, Fase 15B3-C5-B), nunca
    bloqueantes: ausente/vacio -> indices vacios; corrupcion real de un
    archivo presente SI propaga error. Colision de clave de paragraph ->
    se descarta (nunca elige una arbitraria); data_items_by_source_file
    no tiene ese riesgo (una clave por CanonicalProgram, ya unica)."""
    if not canonical_dir.is_dir():
        return _CanonicalEnrichmentIndex({}, {})
    json_paths = sorted(
        path for path in canonical_dir.rglob("*.json") if path.is_file() and not path.is_symlink()
    )
    if not json_paths:
        return _CanonicalEnrichmentIndex({}, {})
    paragraphs: dict[tuple[str, str], CanonicalParagraph] = {}
    data_items_by_source_file: dict[str, tuple[str, list[CanonicalDataItem]]] = {}
    collisions: set[tuple[str, str]] = set()
    for json_path in json_paths:
        try:
            program = CanonicalProgram.model_validate_json(json_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ContextBuildError(
                f"artifacts/02-canonical/{json_path.name}: JSON invalido o incompatible con "
                f"CanonicalProgram: {exc}"
            ) from exc
        data_items_by_source_file[program.source_file] = (program.program_name, program.data_items)
        for paragraph in program.paragraphs:
            key = (program.source_file, paragraph.name)
            if key in collisions:
                continue
            if key in paragraphs:
                del paragraphs[key]
                collisions.add(key)
                continue
            paragraphs[key] = paragraph
    return _CanonicalEnrichmentIndex(paragraphs, data_items_by_source_file)


def run_contexts_built_stage(
    *,
    run_id: str,
    source_package_hash: str,
    run_stages: list[StageExecution],
    semantic_graph_path: Path,
    candidates_path: Path,
    context_dir: Path,
    settings: Settings,
) -> list[str]:
    """Ejecuta CONTEXTS_BUILT y devuelve los warnings (resumen para
    RunState; el detalle completo vive en `context_dir`)."""
    _verify_candidates_detected_precondition(run_stages)

    artifacts_dir = context_dir.parent
    _cleanup_orphans(artifacts_dir)

    try:
        graph, semantic_graph_hash = load_and_validate_semantic_graph(semantic_graph_path)
    except GraphLoadError as exc:
        raise ContextBuildError(
            f"04-semantic-graph.json ya no se puede releer/validar: {exc}"
        ) from exc

    candidate_artifact: CandidateArtifact
    try:
        candidate_artifact, candidate_artifact_hash = load_and_validate_candidate_artifact(
            candidates_path
        )
    except CandidateDetectionError as exc:
        raise ContextBuildError(
            f"06-candidates.json ya no se puede releer/validar: {exc}"
        ) from exc
    if candidate_artifact.source_package_hash != source_package_hash:
        raise ContextBuildError("source_package_hash no coincide con 06-candidates.json")
    if candidate_artifact.semantic_graph_hash != semantic_graph_hash:
        raise ContextBuildError(
            "semantic_graph_hash no coincide con 06-candidates.json (el artefacto cambio "
            "despues de detectar candidatos)"
        )

    schema, context_schema_hash = _load_context_schema(settings)
    validator_cls = jsonschema.validators.validator_for(schema)
    schema_validator = validator_cls(schema)

    query_set, query_records = _load_all_context_queries(settings)

    repository: Neo4jRepository | None = None
    try:
        repository = Neo4jRepository.connect(settings)
        repository.verify_connectivity()

        active_before = repository.read_active_graph_load()
        if active_before is None:
            raise ContextBuildError("no existe un nodo AltamiraGraphLoad activo en Neo4j")
        if active_before.semantic_graph_hash != semantic_graph_hash:
            raise ContextBuildError(
                "semantic_graph_hash no coincide con la carga activa de Neo4j"
            )
        if active_before.source_package_hash != source_package_hash:
            raise ContextBuildError("source_package_hash no coincide con la carga activa")
        if active_before.node_count != len(graph.nodes):
            raise ContextBuildError("node_count no coincide con la carga activa")
        if active_before.relationship_count != len(graph.relationships):
            raise ContextBuildError("relationship_count no coincide con la carga activa")

        drift = repository.compute_drift(graph)
        if not drift.is_clean:
            raise ContextBuildError(
                "drift detectado entre 04-semantic-graph.json y el estado real de Neo4j"
            )

        packages: list[ContextPackage] = []
        if candidate_artifact.candidates:
            canonical_index = _load_canonical_indices_for_context_enrichment(
                artifacts_dir / "02-canonical"
            )

            def _work(tx: neo4j.ManagedTransaction) -> list[ContextPackage]:
                return build_context_packages(
                    tx,
                    candidate_artifact.candidates,
                    queries=query_set,
                    settings=settings,
                    canonical_paragraphs=canonical_index.paragraphs,
                    data_items_by_source_file=canonical_index.data_items_by_source_file,
                )

            packages = repository.run_in_read_transaction(_work)

            active_after = repository.read_active_graph_load()
            if active_after is None or active_after != active_before:
                raise ContextBuildError(
                    "la metadata activa de Neo4j cambio durante la construccion de contextos "
                    "(carga concurrente detectada); se descartan todos los contextos generados"
                )
    except Neo4jError as exc:
        raise ContextBuildError(str(exc)) from exc
    finally:
        if repository is not None:
            repository.close()

    for package in packages:
        _validate_context_package(package, schema_validator)

    context_records: list[ContextRecord] = []
    filenames: dict[str, ContextPackage] = {}
    for package in packages:
        filename = _context_filename(package.candidate.candidate_id)
        if filename in filenames:
            raise ContextBuildError(
                f"colision de nombre de archivo entre candidatos: {filename!r}"
            )
        filenames[filename] = package
        context_hash = hashlib.sha256(package.to_stable_json().encode("utf-8")).hexdigest()
        context_records.append(
            ContextRecord(
                candidate_id=package.candidate.candidate_id,
                paragraph_id=package.scope.paragraph,
                decision_id=package.candidate.decision_id,
                relative_filename=filename,
                context_hash=context_hash,
            )
        )
    context_records.sort(key=lambda record: record.candidate_id)

    manifest = ContextDirectoryManifest(
        run_id=run_id,
        source_package_hash=source_package_hash,
        semantic_graph_hash=semantic_graph_hash,
        candidate_artifact_hash=candidate_artifact_hash,
        q0_query_hash=candidate_artifact.q0_query_hash,
        invariants_query_hash=candidate_artifact.invariants_query_hash,
        context_schema_hash=context_schema_hash,
        dependency_depth=settings.dependency_depth,
        max_code_slice_paragraphs=settings.max_code_slice_paragraphs,
        max_transactional_tables=settings.max_transactional_tables,
        max_parameter_entries_per_context=settings.max_parameter_entries_per_context,
        query_records=query_records,
        context_records=context_records,
        context_count=len(context_records),
        warnings=[],
    )

    existing_manifest = _read_existing_manifest(context_dir)
    if existing_manifest == manifest:
        return [f"{manifest.context_count} contexto(s) (sin cambios)"]

    temp_dir = artifacts_dir / f"{_CONTEXT_DIR_NAME}.tmp-{secrets.token_hex(8)}"
    temp_dir.mkdir(parents=True)
    try:
        for filename, package in filenames.items():
            (temp_dir / filename).write_text(package.to_stable_json(), encoding="utf-8")
        (temp_dir / _MANIFEST_FILENAME).write_text(manifest.to_stable_json(), encoding="utf-8")
        _swap_context_directory(temp_dir, context_dir)
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

    return [f"{manifest.context_count} contexto(s)"]
