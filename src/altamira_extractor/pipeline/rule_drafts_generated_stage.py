"""Orquestacion de la etapa RULE_DRAFTS_GENERATED:
CONTEXTS_BUILT -> artifacts/08-rule-drafts/ (Prompt 12).

Etapa atomica para el CONJUNTO COMPLETO de candidatos de la corrida: solo
puede quedar SUCCEEDED cuando existe exactamente un RuleDraft inicial
estructuralmente valido por cada ContextRecord, todos validan contra
Pydantic y `rule-draft.schema.json`, y todos estan en
`rule-draft-manifest.json` — nunca hay exito parcial. Si CUALQUIER
candidato produce una respuesta inicial estructuralmente invalida, el
procesamiento se detiene de inmediato, el directorio temporal se
descarta completo, no se consume `LLM_REPAIR_ATTEMPTS`, y la salida
canonica anterior (si existia) se conserva intacta — nunca se promueve
un draft de esa corrida ni se alcanza GUARDRAILS_APPLIED.

`RuleDraftGenerationBuilder` es de solo lectura sobre `artifacts/07-context/`:
nunca reejecuta Q1-Q7 ni reconstruye ContextPackage."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import jsonschema

from ..config import Settings
from ..contracts.context_manifest import ContextDirectoryManifest
from ..contracts.context_package import ContextPackage
from ..contracts.enums import PipelineStage, StageStatus
from ..contracts.rule_draft import RuleDraft
from ..contracts.rule_draft_manifest import RuleDraftDirectoryManifest, RuleDraftRecord
from ..contracts.run_state import StageExecution
from .atomic_directory_swap import (
    cleanup_orphan_directories,
    discard_temp_directory,
    new_temp_directory,
    swap_directory,
)
from .errors import LlmClientError, PromptTemplateError, RuleDraftGenerationError
from .llm_client import ChatMessage, LlmProfile, OpenAICompatibleChatClient, resolve_llm_profile
from .prompt_loader import load_prompt_template, render_prompt
from .rule_draft_assembly import (
    RuleDraftAssemblyError,
    assemble_rule_draft,
    load_rule_draft_schema,
    rule_draft_json_hash,
)

_DIR_NAME = "08-rule-drafts"
_MANIFEST_FILENAME = "rule-draft-manifest.json"
_CONTEXT_PACKAGE_PLACEHOLDER = "{{CONTEXT_PACKAGE_JSON}}"


def _verify_rule_drafts_generated_precondition(run_stages: list[StageExecution]) -> None:
    matching = [s for s in run_stages if s.stage == PipelineStage.CONTEXTS_BUILT]
    if not matching:
        raise RuleDraftGenerationError("CONTEXTS_BUILT no se ha ejecutado todavia")
    if len(matching) > 1:
        raise RuleDraftGenerationError("CONTEXTS_BUILT tiene mas de una StageExecution")
    if matching[0].status != StageStatus.SUCCEEDED:
        raise RuleDraftGenerationError("CONTEXTS_BUILT no esta SUCCEEDED")


def _reread_and_verify_context_directory(
    context_dir: Path, *, source_package_hash: str
) -> tuple[ContextDirectoryManifest, str, dict[str, ContextPackage]]:
    manifest_path = context_dir / "context-manifest.json"
    if not manifest_path.is_file():
        raise RuleDraftGenerationError("no se encontro artifacts/07-context/context-manifest.json")
    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest = ContextDirectoryManifest.model_validate_json(manifest_bytes.decode("utf-8"))
    except ValueError as exc:
        raise RuleDraftGenerationError("context-manifest.json invalido") from exc
    if manifest.source_package_hash != source_package_hash:
        raise RuleDraftGenerationError("source_package_hash no coincide con context-manifest.json")
    context_manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()

    packages: dict[str, ContextPackage] = {}
    for record in manifest.context_records:
        package_path = context_dir / record.relative_filename
        if not package_path.is_file():
            raise RuleDraftGenerationError(
                f"falta el ContextPackage de {record.candidate_id!r} en 07-context/"
            )
        try:
            package = ContextPackage.model_validate_json(
                package_path.read_text(encoding="utf-8")
            )
        except ValueError as exc:
            raise RuleDraftGenerationError(
                f"ContextPackage de {record.candidate_id!r} invalido"
            ) from exc
        actual_hash = hashlib.sha256(package.to_stable_json().encode("utf-8")).hexdigest()
        if actual_hash != record.context_hash:
            raise RuleDraftGenerationError(
                f"context_hash de {record.candidate_id!r} no coincide (drift en 07-context/)"
            )
        packages[record.candidate_id] = package

    return manifest, context_manifest_hash, packages


def _load_writer_prompts(
    settings: Settings,
) -> tuple[str, str, str, str]:
    """Devuelve (system_text, system_hash, user_template_text, user_template_hash)."""
    try:
        system = load_prompt_template(
            settings.rule_writer_system_prompt_path,
            relative_path="prompts/rule_writer_system.md",
            expected_placeholder_counts={},
        )
        user = load_prompt_template(
            settings.rule_writer_user_prompt_path,
            relative_path="prompts/rule_writer_user.md",
            expected_placeholder_counts={_CONTEXT_PACKAGE_PLACEHOLDER: 1},
        )
    except PromptTemplateError as exc:
        raise RuleDraftGenerationError(str(exc)) from exc
    return system.template_text, system.template_hash, user.template_text, user.template_hash


def _rule_draft_filename(candidate_id: str) -> str:
    return hashlib.sha256(candidate_id.encode("utf-8")).hexdigest() + ".json"


def _read_existing_manifest(rule_draft_dir: Path) -> RuleDraftDirectoryManifest | None:
    manifest_path = rule_draft_dir / _MANIFEST_FILENAME
    if not manifest_path.is_file():
        return None
    try:
        return RuleDraftDirectoryManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
    except ValueError:
        return None


def _existing_output_is_reusable(
    existing: RuleDraftDirectoryManifest | None,
    *,
    rule_draft_dir: Path,
    source_package_hash: str,
    context_manifest_hash: str,
    rule_draft_schema_hash: str,
    provider: str,
    model: str,
    writer_system_template_hash: str,
    writer_user_template_hash: str,
    context_records_by_id: dict[str, str],
) -> bool:
    """Fast-path de idempotencia: nunca depende del estado anterior de
    RunState (una corrida anterior pudo haber fallado DESPUES sin
    corromper el directorio canonico) — solo del contenido real en disco
    comparado con la configuracion/contexto actuales."""
    if existing is None:
        return False
    if existing.source_package_hash != source_package_hash:
        return False
    if existing.context_manifest_hash != context_manifest_hash:
        return False
    if existing.rule_draft_schema_hash != rule_draft_schema_hash:
        return False
    if existing.provider != provider or existing.model != model:
        return False
    if existing.writer_system_template_hash != writer_system_template_hash:
        return False
    if existing.writer_user_template_hash != writer_user_template_hash:
        return False

    existing_by_id = {record.candidate_id: record for record in existing.records}
    if set(existing_by_id) != set(context_records_by_id):
        return False
    for candidate_id, context_hash in context_records_by_id.items():
        record = existing_by_id[candidate_id]
        if record.context_hash != context_hash:
            return False
        draft_path = rule_draft_dir / record.relative_filename
        if not draft_path.is_file():
            return False
        try:
            draft = RuleDraft.model_validate_json(draft_path.read_text(encoding="utf-8"))
        except ValueError:
            return False
        if rule_draft_json_hash(draft) != record.rule_draft_hash:
            return False

    expected_filenames = {record.relative_filename for record in existing.records}
    actual_filenames = {
        path.name for path in rule_draft_dir.glob("*.json") if path.name != _MANIFEST_FILENAME
    }
    if actual_filenames != expected_filenames:
        return False
    return True


async def _generate_all_drafts(
    *,
    candidates: list[tuple[str, ContextPackage]],
    profile: LlmProfile,
    system_text: str,
    user_template_text: str,
    schema_validator: jsonschema.protocols.Validator,
    max_context_package_json_chars: int,
    context_records_by_id: dict[str, str],
    temp_dir: Path,
) -> list[RuleDraftRecord]:
    """Genera, en una unica corrida de evento asyncio, el draft inicial
    de TODOS los candidatos, secuencialmente (sin concurrencia). Por cada
    candidato exitoso escribe el draft UNICAMENTE en `temp_dir` (nunca en
    el directorio canonico) y registra su metadata en memoria. Se detiene
    ante el primer fallo estructural: la excepcion propaga y el llamador
    descarta `temp_dir` completo (nada de esta corrida se promueve)."""
    records: list[RuleDraftRecord] = []
    async with OpenAICompatibleChatClient(profile) as client:
        for candidate_id, package in candidates:
            context_json = package.to_stable_json()
            if len(context_json) > max_context_package_json_chars:
                raise RuleDraftGenerationError(
                    f"el ContextPackage de {candidate_id!r} serializado excede el limite "
                    f"configurado ({max_context_package_json_chars} caracteres)"
                )
            rendered = render_prompt(
                user_template_text, {_CONTEXT_PACKAGE_PLACEHOLDER: context_json}
            )
            messages = [
                ChatMessage(role="system", content=system_text),
                ChatMessage(role="user", content=rendered.effective_text),
            ]
            try:
                payload, response_hash = await _complete_and_hash(client, messages)
            except LlmClientError as exc:
                raise RuleDraftGenerationError(
                    f"fallo del cliente LLM generando el draft inicial de {candidate_id!r}: "
                    f"{type(exc).__name__}"
                ) from exc

            try:
                rule_draft = assemble_rule_draft(payload, schema_validator=schema_validator)
            except RuleDraftAssemblyError as exc:
                raise RuleDraftGenerationError(
                    f"respuesta inicial estructuralmente invalida para {candidate_id!r}: {exc}"
                ) from exc

            filename = _rule_draft_filename(candidate_id)
            (temp_dir / filename).write_text(rule_draft.to_stable_json(), encoding="utf-8")
            records.append(
                RuleDraftRecord(
                    candidate_id=candidate_id,
                    context_hash=context_records_by_id[candidate_id],
                    relative_filename=filename,
                    rule_draft_hash=rule_draft_json_hash(rule_draft),
                    writer_user_effective_hash=rendered.effective_hash,
                    response_hash=response_hash,
                )
            )
    return records


async def _complete_and_hash(
    client: OpenAICompatibleChatClient, messages: list[ChatMessage]
) -> tuple[dict[str, object], str]:
    # El cliente ya devuelve un dict JSON estrictamente parseado (Prompt
    # 11): el response_hash se deriva de ese mismo dict re-serializado de
    # forma estable, nunca del body HTTP crudo (que nunca se persiste).
    payload = await client.complete(messages)
    stable = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    response_hash = hashlib.sha256(stable.encode("utf-8")).hexdigest()
    return payload, response_hash


def run_rule_drafts_generated_stage(
    *,
    run_id: str,
    source_package_hash: str,
    run_stages: list[StageExecution],
    context_dir: Path,
    rule_draft_dir: Path,
    settings: Settings,
) -> list[str]:
    """Ejecuta RULE_DRAFTS_GENERATED y devuelve los warnings (resumen
    para RunState)."""
    _verify_rule_drafts_generated_precondition(run_stages)

    artifacts_dir = rule_draft_dir.parent
    cleanup_orphan_directories(artifacts_dir, _DIR_NAME)

    context_manifest, context_manifest_hash, packages_by_id = _reread_and_verify_context_directory(
        context_dir, source_package_hash=source_package_hash
    )

    try:
        profile = resolve_llm_profile(settings)
    except LlmClientError as exc:
        raise RuleDraftGenerationError(f"perfil LLM invalido: {exc}") from exc

    schema, rule_draft_schema_hash = load_rule_draft_schema(settings.rule_draft_schema_path)
    validator_cls = jsonschema.validators.validator_for(schema)
    schema_validator = validator_cls(schema)

    system_text, system_hash, user_template_text, user_template_hash = _load_writer_prompts(
        settings
    )

    context_records_by_id = {
        record.candidate_id: record.context_hash for record in context_manifest.context_records
    }

    if not context_records_by_id:
        empty_manifest = RuleDraftDirectoryManifest(
            run_id=run_id,
            source_package_hash=source_package_hash,
            context_manifest_hash=context_manifest_hash,
            rule_draft_schema_hash=rule_draft_schema_hash,
            provider=profile.provider.value,
            model=profile.model,
            writer_system_template_hash=system_hash,
            writer_user_template_hash=user_template_hash,
            records=[],
            draft_count=0,
            warnings=[],
        )
        existing = _read_existing_manifest(rule_draft_dir)
        if existing == empty_manifest:
            return ["0 draft(s) (sin cambios)"]
        _write_manifest_and_promote(artifacts_dir, rule_draft_dir, empty_manifest)
        return ["0 draft(s)"]

    existing_manifest = _read_existing_manifest(rule_draft_dir)
    if _existing_output_is_reusable(
        existing_manifest,
        rule_draft_dir=rule_draft_dir,
        source_package_hash=source_package_hash,
        context_manifest_hash=context_manifest_hash,
        rule_draft_schema_hash=rule_draft_schema_hash,
        provider=profile.provider.value,
        model=profile.model,
        writer_system_template_hash=system_hash,
        writer_user_template_hash=user_template_hash,
        context_records_by_id=context_records_by_id,
    ):
        return [f"{len(context_records_by_id)} draft(s) (sin cambios)"]

    candidates = sorted(packages_by_id.items(), key=lambda item: item[0])
    temp_dir = new_temp_directory(artifacts_dir, _DIR_NAME)
    try:
        records = asyncio.run(
            _generate_all_drafts(
                candidates=candidates,
                profile=profile,
                system_text=system_text,
                user_template_text=user_template_text,
                schema_validator=schema_validator,
                max_context_package_json_chars=settings.max_context_package_json_chars,
                context_records_by_id=context_records_by_id,
                temp_dir=temp_dir,
            )
        )
    except BaseException:
        # Cualquier fallo (estructural o del cliente LLM) descarta TODO
        # el directorio temporal: la etapa es atomica, nunca hay
        # promocion parcial ni consumo de LLM_REPAIR_ATTEMPTS aqui.
        discard_temp_directory(temp_dir)
        raise

    records.sort(key=lambda record: record.candidate_id)
    if {record.candidate_id for record in records} != set(context_records_by_id):
        discard_temp_directory(temp_dir)
        raise RuleDraftGenerationError(
            "correspondencia 1:1 rota entre ContextRecord y RuleDraftRecord"
        )

    manifest = RuleDraftDirectoryManifest(
        run_id=run_id,
        source_package_hash=source_package_hash,
        context_manifest_hash=context_manifest_hash,
        rule_draft_schema_hash=rule_draft_schema_hash,
        provider=profile.provider.value,
        model=profile.model,
        writer_system_template_hash=system_hash,
        writer_user_template_hash=user_template_hash,
        records=records,
        draft_count=len(records),
        warnings=[],
    )

    # El contenido logico puede coincidir con la salida existente (por
    # ejemplo, si _existing_output_is_reusable() forzo la regeneracion
    # solo por un archivo .json huerfano no referenciado). En ese caso
    # SOLO se omite la promocion si el directorio en disco ya coincide
    # exactamente con lo que el manifest fresco declara -- de lo
    # contrario la promocion sigue siendo necesaria para limpiar el
    # directorio (invariante: ningun archivo no referenciado en 08-rule-
    # drafts/).
    if existing_manifest == manifest:
        expected_filenames = {record.relative_filename for record in manifest.records}
        actual_filenames = {
            path.name
            for path in rule_draft_dir.glob("*.json")
            if path.name != _MANIFEST_FILENAME
        }
        if actual_filenames == expected_filenames:
            discard_temp_directory(temp_dir)
            return [f"{manifest.draft_count} draft(s) (sin cambios)"]

    try:
        (temp_dir / _MANIFEST_FILENAME).write_text(manifest.to_stable_json(), encoding="utf-8")
        swap_directory(temp_dir, rule_draft_dir, error_factory=RuleDraftGenerationError)
    finally:
        discard_temp_directory(temp_dir)
    return [f"{manifest.draft_count} draft(s)"]


def _write_manifest_and_promote(
    artifacts_dir: Path, rule_draft_dir: Path, manifest: RuleDraftDirectoryManifest
) -> None:
    temp_dir = new_temp_directory(artifacts_dir, _DIR_NAME)
    try:
        (temp_dir / _MANIFEST_FILENAME).write_text(manifest.to_stable_json(), encoding="utf-8")
        swap_directory(temp_dir, rule_draft_dir, error_factory=RuleDraftGenerationError)
    finally:
        discard_temp_directory(temp_dir)
