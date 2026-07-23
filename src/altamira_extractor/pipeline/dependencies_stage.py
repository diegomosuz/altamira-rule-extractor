"""Orquestacion de la etapa DEPENDENCIES_BUILT: PARSED -> artifacts/03-dependencies.json.

Precondiciones (todas fatales si fallan: `DependencyBuildError`, nunca se
reintenta parcialmente porque el prerequisito mismo esta roto):

- PARSED tiene exactamente una `StageExecution` con status `SUCCEEDED` en
  `RunState.stages` (no se confia solo en `current_stage`).
- Existe un `CanonicalProgram` valido en `02-canonical/` por cada
  `InventoryFile` con `kind == COBOL`, consistente con Inventory/RunState
  (`source_file`, `source_hash`, `source_package_hash`).

Idempotencia: si `artifacts/03-dependencies.json` ya existe, valida
(`DependencyArtifact`), coincide en `run_id`/`source_package_hash`, y
todos sus `from_paragraph_id`/`to_paragraph_id` siguen siendo compatibles
con los `CanonicalProgram` actuales, se reutiliza sin recomputar. En
cualquier otro caso (ausente, invalido, o referencias obsoletas) se
reconstruye por completo: la recomputacion es pura, determinista y
barata (sin subprocess, sin llamadas a Java).
"""

from __future__ import annotations

from pathlib import Path

from ..contracts.canonical import CanonicalProgram
from ..contracts.dependencies import DependencyArtifact
from ..contracts.enums import InventoryFileKind, PipelineStage, StageStatus
from ..contracts.inventory import Inventory
from ..contracts.run_state import StageExecution
from .artifact_store import atomic_write_json
from .dependency_builder import ProgramInput, build_dependencies
from .errors import DependencyBuildError
from .identifiers import ProgramIdentity, paragraph_id


def _verify_parsed_precondition(stages: list[StageExecution]) -> None:
    parsed_executions = [stage for stage in stages if stage.stage == PipelineStage.PARSED]
    if len(parsed_executions) != 1:
        raise DependencyBuildError(
            "PARSED debe tener exactamente una StageExecution en RunState; se encontraron "
            f"{len(parsed_executions)}"
        )
    if parsed_executions[0].status != StageStatus.SUCCEEDED:
        raise DependencyBuildError(
            f"PARSED no esta SUCCEEDED (status={parsed_executions[0].status.value}); no se "
            "puede construir dependencias sobre una etapa PARSED incompleta"
        )


def _load_and_validate_canonical_programs(
    *, canonical_dir: Path, inventory: Inventory, source_package_hash: str
) -> list[CanonicalProgram]:
    cobol_files = sorted(
        (file for file in inventory.files if file.kind == InventoryFileKind.COBOL),
        key=lambda file: file.relative_path,
    )
    programs: list[CanonicalProgram] = []
    for file in cobol_files:
        artifact_path = canonical_dir / f"{file.relative_path}.json"
        if not artifact_path.is_file():
            raise DependencyBuildError(
                f"falta el artefacto canonico esperado para {file.relative_path!r}; PARSED se "
                "declaro SUCCEEDED pero el artefacto no existe"
            )
        try:
            program = CanonicalProgram.model_validate_json(
                artifact_path.read_text(encoding="utf-8")
            )
        except ValueError as exc:
            raise DependencyBuildError(
                f"el artefacto canonico de {file.relative_path!r} no valida contra "
                f"CanonicalProgram: {exc}"
            ) from exc

        if program.source_file != file.relative_path:
            raise DependencyBuildError(
                f"{file.relative_path!r}: CanonicalProgram.source_file no coincide "
                f"({program.source_file!r})"
            )
        if program.source_hash != file.sha256:
            raise DependencyBuildError(
                f"{file.relative_path!r}: CanonicalProgram.source_hash no coincide con "
                "InventoryFile.sha256"
            )
        if program.source_package_hash != source_package_hash:
            raise DependencyBuildError(
                f"{file.relative_path!r}: CanonicalProgram.source_package_hash no coincide con "
                "RunState.source_package_hash"
            )
        programs.append(program)
    return programs


def _valid_paragraph_ids(program_inputs: list[ProgramInput]) -> set[str]:
    valid: set[str] = set()
    for program_input in program_inputs:
        program = program_input.program
        pid = program_input.identity.program_id(
            program_name=program.program_name, source_hash=program.source_hash
        )
        for paragraph in program.paragraphs:
            valid.add(paragraph_id(pid, paragraph.name))
    return valid


def _existing_artifact_is_reusable(
    dependencies_path: Path,
    *,
    run_id: str,
    source_package_hash: str,
    valid_paragraph_ids: set[str],
) -> bool:
    if not dependencies_path.is_file():
        return False
    try:
        artifact = DependencyArtifact.model_validate_json(
            dependencies_path.read_text(encoding="utf-8")
        )
    except ValueError:
        return False
    if artifact.run_id != run_id or artifact.source_package_hash != source_package_hash:
        return False
    for dependency in artifact.dependencies:
        if dependency.from_paragraph_id not in valid_paragraph_ids:
            return False
        if dependency.to_paragraph_id not in valid_paragraph_ids:
            return False
    return True


def run_dependencies_built_stage(
    *,
    run_id: str,
    source_package_hash: str,
    run_stages: list[StageExecution],
    inventory: Inventory,
    canonical_dir: Path,
    dependencies_path: Path,
) -> list[str]:
    """Ejecuta DEPENDENCIES_BUILT y devuelve los warnings (resumen para
    RunState; el detalle completo vive en `dependencies_path`).

    Cualquier inconsistencia con las precondiciones (PARSED incompleto,
    CanonicalProgram ausente/invalido/inconsistente) es fatal: propaga
    `DependencyBuildError`, sin reintento parcial.
    """
    _verify_parsed_precondition(run_stages)

    programs = _load_and_validate_canonical_programs(
        canonical_dir=canonical_dir, inventory=inventory, source_package_hash=source_package_hash
    )

    identity = ProgramIdentity(
        country_code=inventory.manifest.country.code,
        logical_name=inventory.manifest.operation.logical_name,
        version=inventory.manifest.implementation.version,
    )
    program_inputs = [ProgramInput(program=program, identity=identity) for program in programs]

    if _existing_artifact_is_reusable(
        dependencies_path,
        run_id=run_id,
        source_package_hash=source_package_hash,
        valid_paragraph_ids=_valid_paragraph_ids(program_inputs),
    ):
        existing = DependencyArtifact.model_validate_json(
            dependencies_path.read_text(encoding="utf-8")
        )
        return list(existing.warnings)

    dependencies, warnings = build_dependencies(
        program_inputs, source_package_hash=source_package_hash
    )
    artifact = DependencyArtifact(
        run_id=run_id,
        source_package_hash=source_package_hash,
        dependencies=dependencies,
        warnings=warnings,
    )
    atomic_write_json(dependencies_path, artifact)
    return warnings
