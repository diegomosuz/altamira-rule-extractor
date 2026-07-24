"""Orquestacion de la transicion GUARDRAILS_APPLIED -> COMPLETED:
`artifacts/09-guardrails/` -> `artifacts/10-rules/` (Prompt 13a).

No agrega un `PipelineStage` nuevo: el trabajo de esta etapa se registra
como `StageExecution(stage=PipelineStage.COMPLETED, ...)` (ver
`pipeline/runner.py::_run_rules_rendered`), simetrico a como toda otra
etapa real tiene su propio `StageExecution` — `FAILED` sigue siendo el
unico valor de `PipelineStage` que nunca aparece como `StageExecution.
stage` (es un centinela puro de `RunState.current_stage`).

Fuente de verdad EXCLUSIVA: `artifacts/09-guardrails/guardrail-manifest.
json` y cada `GuardrailCandidateArtifact.final_rule_draft` referenciado
alli. Nunca se lee `artifacts/08-rule-drafts/`, `ContextPackage`, Neo4j
ni Q0-Q7 — y nunca se invoca al modelo ni se reejecuta
`DeterministicGuardrail` (`final_evidence_validation_status=
EVIDENCE_VALIDATED`, `functional_review_status=NEEDS_FUNCTIONAL_REVIEW`
y la coherencia verdict/final_rule_draft ya estan garantizadas por los
validadores de `GuardrailRecord`/`GuardrailCandidateArtifact` al parsear
— este modulo solo revalida drift ENTRE archivos, nunca reimplementa
esos invariantes de forma).

Etapa atomica: todo `.md` + `rules-manifest.json` se construyen en un
directorio temporal hermano (mismo patron que RULE_DRAFTS_GENERATED/
GUARDRAILS_APPLIED, via `atomic_directory_swap.py`) y se promueven en un
unico swap; nunca hay una salida parcial en `artifacts/10-rules/`."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..contracts.enums import PipelineStage, StageStatus
from ..contracts.guardrail_candidate import GuardrailCandidateArtifact
from ..contracts.guardrail_manifest import GuardrailDirectoryManifest, GuardrailRecord
from ..contracts.rules_manifest import RulesDirectoryManifest, RulesRecord
from ..contracts.run_state import StageExecution
from .atomic_directory_swap import (
    cleanup_orphan_directories,
    discard_temp_directory,
    new_temp_directory,
    swap_directory,
)
from .errors import MarkdownRenderError
from .markdown_renderer import MARKDOWN_RENDERER_VERSION, render_markdown

_DIR_NAME = "10-rules"
_MANIFEST_FILENAME = "rules-manifest.json"
_GUARDRAIL_DIR_NAME = "09-guardrails"
_GUARDRAIL_MANIFEST_FILENAME = "guardrail-manifest.json"


def _verify_rules_rendered_precondition(run_stages: list[StageExecution]) -> None:
    matching = [s for s in run_stages if s.stage == PipelineStage.GUARDRAILS_APPLIED]
    if not matching:
        raise MarkdownRenderError("GUARDRAILS_APPLIED no se ha ejecutado todavia")
    if len(matching) > 1:
        raise MarkdownRenderError("GUARDRAILS_APPLIED tiene mas de una StageExecution")
    if matching[0].status != StageStatus.SUCCEEDED:
        raise MarkdownRenderError("GUARDRAILS_APPLIED no esta SUCCEEDED")


def _filename_for_candidate(candidate_id: str) -> str:
    return hashlib.sha256(candidate_id.encode("utf-8")).hexdigest() + ".md"


def _scan_directory_strict(dir_path: Path, *, context_label: str) -> set[str]:
    """Enumera TODAS las entradas directas de `dir_path` (sin filtrar por
    extension: un archivo `.txt`/`.json` adicional o un subdirectorio se
    detectan igual). Rechaza symlinks y subdirectorios de inmediato —
    nunca los ignora silenciosamente."""
    names: set[str] = set()
    for entry in dir_path.iterdir():
        if entry.is_symlink():
            raise MarkdownRenderError(f"{context_label}: symlink no permitido ({entry.name!r})")
        if entry.is_dir():
            raise MarkdownRenderError(
                f"{context_label}: subdirectorio no permitido ({entry.name!r})"
            )
        if not entry.is_file():
            raise MarkdownRenderError(
                f"{context_label}: entrada no es un archivo regular ({entry.name!r})"
            )
        names.add(entry.name)
    return names


def _reread_and_verify_guardrail_directory(
    guardrail_dir: Path, *, source_package_hash: str
) -> tuple[GuardrailDirectoryManifest, str, dict[str, GuardrailCandidateArtifact]]:
    if guardrail_dir.is_symlink() or not guardrail_dir.is_dir():
        raise MarkdownRenderError("no se encontro artifacts/09-guardrails/ (o es un symlink)")

    manifest_path = guardrail_dir / _GUARDRAIL_MANIFEST_FILENAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise MarkdownRenderError(
            "no se encontro artifacts/09-guardrails/guardrail-manifest.json "
            "(o es un symlink/no es un archivo regular)"
        )
    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest = GuardrailDirectoryManifest.model_validate_json(manifest_bytes.decode("utf-8"))
    except ValueError as exc:
        raise MarkdownRenderError("guardrail-manifest.json invalido") from exc
    if manifest.source_package_hash != source_package_hash:
        raise MarkdownRenderError("source_package_hash no coincide con guardrail-manifest.json")
    guardrail_manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()

    expected_filenames = {record.relative_filename for record in manifest.records}
    actual_filenames = _scan_directory_strict(
        guardrail_dir, context_label="artifacts/09-guardrails/"
    )
    if actual_filenames != expected_filenames | {_GUARDRAIL_MANIFEST_FILENAME}:
        raise MarkdownRenderError(
            "artifacts/09-guardrails/ contiene archivos no referenciados por "
            "guardrail-manifest.json (o le faltan archivos referenciados)"
        )

    artifacts_by_id: dict[str, GuardrailCandidateArtifact] = {}
    for record in manifest.records:
        artifact_path = guardrail_dir / record.relative_filename
        try:
            artifact = GuardrailCandidateArtifact.model_validate_json(
                artifact_path.read_text(encoding="utf-8")
            )
        except ValueError as exc:
            raise MarkdownRenderError(
                f"GuardrailCandidateArtifact de {record.candidate_id!r} invalido"
            ) from exc
        if artifact.candidate_id != record.candidate_id:
            raise MarkdownRenderError(
                f"candidate_id de {record.relative_filename!r} no coincide con el manifest"
            )
        actual_hash = hashlib.sha256(artifact.to_stable_json().encode("utf-8")).hexdigest()
        if actual_hash != record.guardrail_artifact_hash:
            raise MarkdownRenderError(
                f"guardrail_artifact_hash de {record.candidate_id!r} no coincide (drift en "
                "09-guardrails/)"
            )
        artifacts_by_id[record.candidate_id] = artifact

    return manifest, guardrail_manifest_hash, artifacts_by_id


def _verify_rules_manifest_filenames(manifest: RulesDirectoryManifest) -> None:
    for record in manifest.records:
        if "/" in record.relative_filename or "\\" in record.relative_filename:
            raise MarkdownRenderError(
                f"relative_filename de {record.candidate_id!r} no es hijo directo de "
                "artifacts/10-rules/"
            )
        if record.relative_filename == _MANIFEST_FILENAME:
            raise MarkdownRenderError(
                f"relative_filename de {record.candidate_id!r} colisiona con "
                f"{_MANIFEST_FILENAME!r}"
            )
        expected = _filename_for_candidate(record.candidate_id)
        if record.relative_filename != expected:
            raise MarkdownRenderError(
                f"relative_filename de {record.candidate_id!r} no coincide con "
                f"sha256(candidate_id)+'.md' ({expected!r})"
            )


def _read_existing_rules_manifest(rules_dir: Path) -> RulesDirectoryManifest | None:
    manifest_path = rules_dir / _MANIFEST_FILENAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        return None
    try:
        return RulesDirectoryManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _existing_output_is_reusable(
    existing: RulesDirectoryManifest | None,
    *,
    rules_dir: Path,
    source_package_hash: str,
    guardrail_manifest_hash: str,
    guardrail_records_by_id: dict[str, GuardrailRecord],
) -> bool:
    """Fast-path de idempotencia: nunca depende del estado anterior de
    RunState (`COMPLETED` puede volver a ejecutarse desde una
    precondicion valida) — solo del contenido real en disco comparado
    con `artifacts/09-guardrails/` recien releido."""
    if existing is None:
        return False
    if existing.source_package_hash != source_package_hash:
        return False
    if existing.guardrail_manifest_hash != guardrail_manifest_hash:
        return False
    if existing.renderer_version != MARKDOWN_RENDERER_VERSION:
        return False

    existing_by_id = {record.candidate_id: record for record in existing.records}
    if set(existing_by_id) != set(guardrail_records_by_id):
        return False

    _verify_rules_manifest_filenames(existing)

    for candidate_id, guardrail_record in guardrail_records_by_id.items():
        rules_record = existing_by_id[candidate_id]
        if rules_record.source_guardrail_artifact_hash != guardrail_record.guardrail_artifact_hash:
            return False
        if rules_record.final_rule_draft_hash != guardrail_record.final_rule_draft_hash:
            return False
        markdown_path = rules_dir / rules_record.relative_filename
        if markdown_path.is_symlink() or not markdown_path.is_file():
            return False
        actual_markdown_hash = hashlib.sha256(markdown_path.read_bytes()).hexdigest()
        if actual_markdown_hash != rules_record.markdown_hash:
            return False

    expected_filenames = {record.relative_filename for record in existing.records}
    actual_filenames = _scan_directory_strict(rules_dir, context_label="artifacts/10-rules/")
    return actual_filenames == expected_filenames | {_MANIFEST_FILENAME}


def _write_manifest_and_promote(
    artifacts_dir: Path, rules_dir: Path, manifest: RulesDirectoryManifest
) -> None:
    temp_dir = new_temp_directory(artifacts_dir, _DIR_NAME)
    try:
        (temp_dir / _MANIFEST_FILENAME).write_text(manifest.to_stable_json(), encoding="utf-8")
        swap_directory(temp_dir, rules_dir, error_factory=MarkdownRenderError)
    finally:
        discard_temp_directory(temp_dir)


def run_rules_rendered_stage(
    *,
    run_id: str,
    source_package_hash: str,
    run_stages: list[StageExecution],
    guardrail_dir: Path,
    rules_dir: Path,
) -> list[str]:
    """Ejecuta la transicion GUARDRAILS_APPLIED -> COMPLETED y devuelve
    los warnings (resumen para RunState) — en la practica siempre vacio:
    MarkdownRenderer es puramente deterministico y no tiene casos
    ambiguos que ameriten un warning. El conteo de reglas renderizadas no
    se expone aqui (no es un warning); queda disponible releyendo
    `RulesDirectoryManifest.rule_count` de la salida persistida."""
    _verify_rules_rendered_precondition(run_stages)

    artifacts_dir = rules_dir.parent
    cleanup_orphan_directories(artifacts_dir, _DIR_NAME)

    guardrail_manifest, guardrail_manifest_hash, artifacts_by_id = (
        _reread_and_verify_guardrail_directory(
            guardrail_dir, source_package_hash=source_package_hash
        )
    )
    guardrail_records_by_id = {record.candidate_id: record for record in guardrail_manifest.records}

    existing_manifest = _read_existing_rules_manifest(rules_dir)
    if _existing_output_is_reusable(
        existing_manifest,
        rules_dir=rules_dir,
        source_package_hash=source_package_hash,
        guardrail_manifest_hash=guardrail_manifest_hash,
        guardrail_records_by_id=guardrail_records_by_id,
    ):
        return []

    if not guardrail_records_by_id:
        empty_manifest = RulesDirectoryManifest(
            run_id=run_id,
            source_package_hash=source_package_hash,
            guardrail_manifest_hash=guardrail_manifest_hash,
            renderer_version=MARKDOWN_RENDERER_VERSION,
            records=[],
            rule_count=0,
            warnings=[],
        )
        _write_manifest_and_promote(artifacts_dir, rules_dir, empty_manifest)
        return []

    temp_dir = new_temp_directory(artifacts_dir, _DIR_NAME)
    try:
        records: list[RulesRecord] = []
        for candidate_id in sorted(artifacts_by_id):
            artifact = artifacts_by_id[candidate_id]
            guardrail_record = guardrail_records_by_id[candidate_id]
            markdown_bytes = render_markdown(artifact.final_rule_draft)
            filename = _filename_for_candidate(candidate_id)
            (temp_dir / filename).write_bytes(markdown_bytes)
            records.append(
                RulesRecord(
                    candidate_id=candidate_id,
                    source_guardrail_artifact_hash=guardrail_record.guardrail_artifact_hash,
                    final_rule_draft_hash=guardrail_record.final_rule_draft_hash,
                    relative_filename=filename,
                    markdown_hash=hashlib.sha256(markdown_bytes).hexdigest(),
                )
            )

        actual_temp_filenames = {path.name for path in temp_dir.iterdir()}
        expected_temp_filenames = {record.relative_filename for record in records}
        if actual_temp_filenames != expected_temp_filenames:
            raise MarkdownRenderError(
                "el directorio temporal de artifacts/10-rules/ no coincide con los "
                "RulesRecord generados"
            )

        manifest = RulesDirectoryManifest(
            run_id=run_id,
            source_package_hash=source_package_hash,
            guardrail_manifest_hash=guardrail_manifest_hash,
            renderer_version=MARKDOWN_RENDERER_VERSION,
            records=records,
            rule_count=len(records),
            warnings=[],
        )
        _verify_rules_manifest_filenames(manifest)

        if existing_manifest == manifest:
            existing_filenames = _scan_directory_strict(
                rules_dir, context_label="artifacts/10-rules/"
            )
            if existing_filenames == expected_temp_filenames | {_MANIFEST_FILENAME}:
                discard_temp_directory(temp_dir)
                return []

        (temp_dir / _MANIFEST_FILENAME).write_text(manifest.to_stable_json(), encoding="utf-8")
        swap_directory(temp_dir, rules_dir, error_factory=MarkdownRenderError)
    finally:
        discard_temp_directory(temp_dir)
    return []
