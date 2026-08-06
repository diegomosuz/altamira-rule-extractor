"""Servicio de filesystem de observaciones POR-RUN del catalogo estatico
de construcciones (Fase 15B2-A, Parte D). Localiza un run, carga y valida
`artifacts/02-canonical/` (unico artefacto V1 requerido: solo necesita
`CanonicalProgram`, a diferencia de `semantic_coverage_service.py` que
tambien requiere dependencies/semantic-graph/candidates), reconcilia sus
`CanonicalStatement.kind`/`unsupported_constructs` contra
`SemanticCoverageManifest` (`config/semantic_coverage.yaml`, cargado via
`semantic_coverage_registry.load_semantic_coverage_manifest`) y persiste
el resultado en `<run_dir>/diagnostics/semantic-coverage-observations.json`.

NO es un `PipelineStage`: se invoca exclusivamente bajo demanda (CLI),
nunca desde `runner.py`/`run_ingestion`. Nunca modifica ningun artefacto
de entrada (decision arquitectonica #7: nunca `artifacts/01-10`).

Sanitizacion (decision arquitectonica #5): el prefijo identificador
extraido de `CanonicalProgram.unsupported_constructs` (formato real,
verificado contra `StatementExtractor.java`: `"<identidad> en paragraph
<p> ..."` o, si el separador no aparece, el mensaje completo recortado a
`MAX_UNSUPPORTED_IDENTITY_LENGTH`) es la UNICA porcion persistida --
nunca el mensaje completo, nunca `paragraph`/programa individuales por
entrada (solo un conteo agregado). La asociacion identidad -> construct_id
usa EXCLUSIVAMENTE `_PARSER_CLASS_TO_CONSTRUCT_ID`, una tabla curada
manualmente contra evidencia real (inspeccion directa `jar tf` del JAR de
ProLeap resuelto, confirmando el sufijo real `...Impl` de cada clase ASG
concreta que `Statement.getClass().getSimpleName()` produce dentro de
`StatementExtractor.convertOther()`); una identidad ausente de la tabla
se persiste con `construct_id=None`, NUNCA con una asociacion adivinada."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from pydantic import BaseModel

from ..contracts.canonical import CanonicalProgram
from ..contracts.enums import PipelineStage, StageStatus, StatementKind
from ..contracts.run_state import RunState
from ..contracts.semantic_coverage import SemanticCoverageManifest
from ..contracts.semantic_coverage_observations import (
    MAX_UNSUPPORTED_IDENTITY_LENGTH,
    SemanticCoverageConstructObservation,
    SemanticCoverageObservationsArtifact,
    SemanticCoverageObservationsSummary,
    SemanticCoverageUnsupportedObservation,
)
from .artifact_store import atomic_write_json
from .errors import SemanticCoverageObservationsError

_CANONICAL_DIR_NAME = "02-canonical"
_DIAGNOSTICS_DIR_NAME = "diagnostics"
_ARTIFACT_FILENAME = "semantic-coverage-observations.json"
_UNSUPPORTED_SEPARATOR = " en paragraph "

# Curada manualmente contra `jar tf` del JAR de ProLeap resuelto (Fase
# 15B2-A, Parte D) + lectura directa de `StatementExtractor.convertOther()`/
# `convertExit()`. Solo incluye identidades para las que existe evidencia
# JAR real Y un construct_id ya catalogado en config/semantic_coverage.yaml
# con java_statement_kind=OTHER. `RewriteStatementImpl` (REWRITE) se deja
# deliberadamente sin mapear: FILE_WRITE cubre WRITE, no REWRITE (ver
# limitations de FILE_WRITE en config/semantic_coverage.yaml) -- mapearlo
# ahi seria una asociacion no verificada.
_PARSER_CLASS_TO_CONSTRUCT_ID: Final[dict[str, str]] = {
    "AddStatementImpl": "ADD",
    "AddToStatementImpl": "ADD",
    "AddToGivingStatementImpl": "ADD",
    "AddCorrespondingStatementImpl": "ADD",
    "SubtractStatementImpl": "SUBTRACT",
    "SubtractFromStatementImpl": "SUBTRACT",
    "SubtractFromGivingStatementImpl": "SUBTRACT",
    "SubtractCorrespondingStatementImpl": "SUBTRACT",
    "MultiplyStatementImpl": "MULTIPLY",
    "DivideStatementImpl": "DIVIDE",
    "DivideIntoStatementImpl": "DIVIDE",
    "DivideIntoGivingStatementImpl": "DIVIDE",
    "InitializeStatementImpl": "INITIALIZE",
    "StringStatementImpl": "STRING",
    "UnstringStatementImpl": "UNSTRING",
    "InspectStatementImpl": "INSPECT",
    "SearchStatementImpl": "SEARCH",
    "SortStatementImpl": "SORT",
    "MergeStatementImpl": "MERGE",
    "ReadStatementImpl": "FILE_READ",
    "WriteStatementImpl": "FILE_WRITE",
    "OpenStatementImpl": "OPEN",
    "CloseStatementImpl": "CLOSE",
    "ClosePortFileIoStatementImpl": "CLOSE",
    "CloseReelUnitStatementImpl": "CLOSE",
    "CloseRelativeStatementImpl": "CLOSE",
    "ExitStatementImpl": "BARE_EXIT",
    "DisplayStatementImpl": "DISPLAY",
    "ExecCicsStatementImpl": "EXEC_CICS",
}


def _extract_unsupported_identity(message: str) -> str:
    """Replica `semantic_coverage_analyzer._parse_unsupported_construct_
    message` (Fase 1) solo para la porcion identidad -- mismo formato
    real, mismo comportamiento lenient (nunca lanza, nunca asume el
    separador presente). Deliberadamente NO reutiliza esa funcion privada
    de otro modulo (mantiene este servicio autocontenido, mismo principio
    que `semantic_coverage_service.py` replicando localmente lo minimo de
    `api/reads.py` que necesita)."""
    if _UNSUPPORTED_SEPARATOR not in message:
        return message.strip()[:MAX_UNSUPPORTED_IDENTITY_LENGTH]
    identity, _remainder = message.split(_UNSUPPORTED_SEPARATOR, 1)
    identity = identity.strip()
    return identity[:MAX_UNSUPPORTED_IDENTITY_LENGTH] or message.strip()[
        :MAX_UNSUPPORTED_IDENTITY_LENGTH
    ]


def _load_run_state(run_dir: Path) -> RunState:
    """Replica `semantic_coverage_service._load_run_state` (mismo
    principio: `pipeline/` autocontenido, sin importar `api/`)."""
    run_json_path = run_dir / "run.json"
    if run_json_path.is_symlink() or not run_json_path.is_file():
        raise SemanticCoverageObservationsError(
            f"run {run_dir.name!r} no encontrado: run.json ausente"
        )
    try:
        return RunState.model_validate_json(run_json_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SemanticCoverageObservationsError("run.json invalido") from exc


def _require_parsed(state: RunState) -> None:
    execution = next((s for s in state.stages if s.stage == PipelineStage.PARSED), None)
    if execution is None or execution.status != StageStatus.SUCCEEDED:
        raise SemanticCoverageObservationsError(
            "el run no alcanzo PARSED (SUCCEEDED); no se pueden calcular observaciones "
            "de cobertura semantica todavia"
        )


def _load_json_artifact[T: BaseModel](path: Path, model: type[T], *, artifact_label: str) -> T:
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SemanticCoverageObservationsError(f"{artifact_label}: fallo de lectura") from exc
    try:
        return model.model_validate_json(raw_text)
    except ValueError as exc:
        raise SemanticCoverageObservationsError(
            f"{artifact_label}: JSON invalido o incompatible con su contrato"
        ) from exc


def _load_canonical_programs(canonical_dir: Path) -> list[CanonicalProgram]:
    if canonical_dir.is_symlink() or not canonical_dir.is_dir():
        raise SemanticCoverageObservationsError(
            f"artifacts/{_CANONICAL_DIR_NAME} ausente o no es un directorio regular"
        )
    json_paths = sorted(
        path for path in canonical_dir.rglob("*.json") if path.is_file() and not path.is_symlink()
    )
    if not json_paths:
        raise SemanticCoverageObservationsError(
            f"artifacts/{_CANONICAL_DIR_NAME} no contiene ningun artefacto CanonicalProgram"
        )
    programs: list[CanonicalProgram] = []
    for json_path in json_paths:
        relative_suffix = json_path.relative_to(canonical_dir).as_posix()
        relative_label = f"artifacts/{_CANONICAL_DIR_NAME}/{relative_suffix}"
        programs.append(
            _load_json_artifact(json_path, CanonicalProgram, artifact_label=relative_label)
        )
    return programs


def observe_semantic_coverage(
    canonical_programs: list[CanonicalProgram],
    manifest: SemanticCoverageManifest,
    *,
    run_id: str,
    source_package_hash: str,
) -> SemanticCoverageObservationsArtifact:
    """Analizador puro: sin filesystem, sin Neo4j, sin LLM. Cuenta
    ocurrencias de `CanonicalStatement.kind` por `construct_id` del
    catalogo (agrupando construct_id hermanos que comparten
    `java_statement_kind`) y ocurrencias sanitizadas de identidades en
    `unsupported_constructs`."""
    kind_occurrences: dict[StatementKind, int] = {}
    kind_programs: dict[StatementKind, set[str]] = {}
    for program in canonical_programs:
        kinds_in_program: dict[StatementKind, int] = {}
        for paragraph in program.paragraphs:
            for statement in paragraph.statements:
                kinds_in_program[statement.kind] = kinds_in_program.get(statement.kind, 0) + 1
        for kind, count in kinds_in_program.items():
            kind_occurrences[kind] = kind_occurrences.get(kind, 0) + count
            kind_programs.setdefault(kind, set()).add(program.program_name)

    constructs_by_kind: dict[StatementKind, list[str]] = {}
    for construct in manifest.constructs:
        if construct.java_statement_kind is None:
            continue
        constructs_by_kind.setdefault(construct.java_statement_kind, []).append(
            construct.construct_id
        )

    construct_observations: list[SemanticCoverageConstructObservation] = []
    for kind, sibling_ids in constructs_by_kind.items():
        occurrence_count = kind_occurrences.get(kind, 0)
        program_count = len(kind_programs.get(kind, set()))
        for construct_id in sibling_ids:
            siblings = sorted(cid for cid in sibling_ids if cid != construct_id)
            construct_observations.append(
                SemanticCoverageConstructObservation(
                    construct_id=construct_id,
                    java_statement_kind=kind,
                    observed=occurrence_count > 0,
                    occurrence_count=occurrence_count,
                    program_count=program_count,
                    shared_java_statement_kind_construct_ids=siblings,
                )
            )
    construct_observations.sort(key=lambda entry: entry.construct_id)

    identity_occurrences: dict[str, int] = {}
    identity_programs: dict[str, set[str]] = {}
    for program in canonical_programs:
        seen_identities_in_program: set[str] = set()
        for message in program.unsupported_constructs:
            identity = _extract_unsupported_identity(message)
            identity_occurrences[identity] = identity_occurrences.get(identity, 0) + 1
            seen_identities_in_program.add(identity)
        for identity in seen_identities_in_program:
            identity_programs.setdefault(identity, set()).add(program.program_name)

    unsupported_observations = [
        SemanticCoverageUnsupportedObservation(
            identity=identity,
            construct_id=_PARSER_CLASS_TO_CONSTRUCT_ID.get(identity),
            occurrence_count=count,
            program_count=len(identity_programs[identity]),
        )
        for identity, count in identity_occurrences.items()
    ]
    unsupported_observations.sort(key=lambda entry: entry.identity)

    summary = SemanticCoverageObservationsSummary(
        construct_count=len(construct_observations),
        observed_construct_count=sum(1 for c in construct_observations if c.observed),
        unsupported_identity_count=len(unsupported_observations),
        mapped_unsupported_identity_count=sum(
            1 for entry in unsupported_observations if entry.construct_id is not None
        ),
    )

    return SemanticCoverageObservationsArtifact(
        run_id=run_id,
        source_package_hash=source_package_hash,
        manifest_edition=manifest.manifest_edition,
        constructs=construct_observations,
        unsupported_identities=unsupported_observations,
        summary=summary,
    )


def compute_semantic_coverage_observations(
    run_dir: Path, run_id: str, manifest: SemanticCoverageManifest
) -> SemanticCoverageObservationsArtifact:
    """Localiza `run_dir`, carga y valida `artifacts/02-canonical/`, y
    devuelve el `SemanticCoverageObservationsArtifact` calculado. Nunca
    escribe nada -- la persistencia es responsabilidad de
    `write_semantic_coverage_observations`/el comando CLI."""
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise SemanticCoverageObservationsError(f"run {run_dir.name!r} no encontrado")

    state = _load_run_state(run_dir)
    _require_parsed(state)

    canonical_dir = run_dir / "artifacts" / _CANONICAL_DIR_NAME
    canonical_programs = _load_canonical_programs(canonical_dir)

    source_package_hash = state.source_package_hash
    if source_package_hash is None:
        raise SemanticCoverageObservationsError(
            "run_state.source_package_hash ausente; no se puede calcular observaciones"
        )

    return observe_semantic_coverage(
        canonical_programs,
        manifest,
        run_id=run_id,
        source_package_hash=source_package_hash,
    )


def semantic_coverage_observations_path(run_dir: Path) -> Path:
    return run_dir / _DIAGNOSTICS_DIR_NAME / _ARTIFACT_FILENAME


def write_semantic_coverage_observations(
    run_dir: Path, artifact: SemanticCoverageObservationsArtifact
) -> Path:
    """Persiste `artifact` de forma atomica en `diagnostics/semantic-
    coverage-observations.json` (mismo mecanismo que `atomic_write_json`
    ya usa para `diagnostics/semantic-coverage.json`: temporal-hermano +
    flush + fsync + replace)."""
    artifact_path = semantic_coverage_observations_path(run_dir)
    atomic_write_json(artifact_path, artifact)
    return artifact_path
