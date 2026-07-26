"""Orquestacion de la etapa GUARDRAILS_APPLIED:
RULE_DRAFTS_GENERATED -> artifacts/09-guardrails/ (Prompt 12).

Etapa atomica y FAIL-FAST: procesa candidatos secuencialmente; en cuanto
uno agota `LLM_REPAIR_ATTEMPTS` sin alcanzar EVIDENCE_VALIDATED, se
detiene de inmediato, descarta el directorio temporal completo, nunca
llama al modelo para los candidatos restantes, y conserva intacta
cualquier salida canonica anterior. `artifacts/09-guardrails/` solo
existe como salida canonica cuando TODOS los candidatos terminaron
EVIDENCE_VALIDATED — un unico REJECTED hace que la etapa completa quede
FAILED, nunca se promueve un directorio parcial ni se habilita
MarkdownRenderer (Prompt 13).

`artifacts/08-rule-drafts/` nunca se sobrescribe desde aqui: es evidencia
inmutable de la primera respuesta aceptada del modelo."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema

from ..config import Settings
from ..contracts.context_manifest import ContextDirectoryManifest
from ..contracts.context_package import ContextPackage
from ..contracts.enums import (
    EvidenceValidationStatus,
    GuardrailVerdict,
    PipelineStage,
    Severity,
    StageStatus,
)
from ..contracts.guardrail import GuardrailReport, GuardrailViolation
from ..contracts.guardrail_candidate import GuardrailCandidateArtifact, RepairAttemptRecord
from ..contracts.guardrail_manifest import GuardrailDirectoryManifest, GuardrailRecord
from ..contracts.rule_draft import RuleDraft
from ..contracts.rule_draft_manifest import RuleDraftDirectoryManifest
from ..contracts.run_state import StageExecution
from .atomic_directory_swap import (
    cleanup_orphan_directories,
    discard_temp_directory,
    new_temp_directory,
    swap_directory,
)
from .deterministic_guardrail import GUARDRAIL_VERSION, evaluate_guardrail
from .errors import GuardrailError, LlmClientError, PromptTemplateError
from .llm_client import ChatMessage, LlmProfile, OpenAICompatibleChatClient, resolve_llm_profile
from .prompt_loader import load_prompt_template, render_prompt
from .rule_draft_assembly import (
    RuleDraftAssemblyError,
    assemble_rule_draft,
    load_rule_draft_schema,
    rule_draft_json_hash,
)

_DIR_NAME = "09-guardrails"
_MANIFEST_FILENAME = "guardrail-manifest.json"
_RULE_DRAFT_MANIFEST_FILENAME = "rule-draft-manifest.json"
_FAILURE_SUMMARY_MAX_LENGTH = 500

_CONTEXT_PACKAGE_PLACEHOLDER = "{{CONTEXT_PACKAGE_JSON}}"
_REJECTED_DRAFT_PLACEHOLDER = "{{REJECTED_RULE_DRAFT_JSON}}"
_VIOLATIONS_PLACEHOLDER = "{{GUARDRAIL_VIOLATIONS_JSON}}"


def _verify_guardrails_applied_precondition(run_stages: list[StageExecution]) -> None:
    matching = [s for s in run_stages if s.stage == PipelineStage.RULE_DRAFTS_GENERATED]
    if not matching:
        raise GuardrailError("RULE_DRAFTS_GENERATED no se ha ejecutado todavia")
    if len(matching) > 1:
        raise GuardrailError("RULE_DRAFTS_GENERATED tiene mas de una StageExecution")
    if matching[0].status != StageStatus.SUCCEEDED:
        raise GuardrailError("RULE_DRAFTS_GENERATED no esta SUCCEEDED")


def _reread_and_verify_rule_draft_directory(
    rule_draft_dir: Path, *, source_package_hash: str, context_records_by_id: dict[str, str]
) -> tuple[RuleDraftDirectoryManifest, str, dict[str, RuleDraft]]:
    manifest_path = rule_draft_dir / _RULE_DRAFT_MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise GuardrailError("no se encontro artifacts/08-rule-drafts/rule-draft-manifest.json")
    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest = RuleDraftDirectoryManifest.model_validate_json(manifest_bytes.decode("utf-8"))
    except ValueError as exc:
        raise GuardrailError("rule-draft-manifest.json invalido") from exc
    if manifest.source_package_hash != source_package_hash:
        raise GuardrailError("source_package_hash no coincide con rule-draft-manifest.json")
    rule_draft_manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()

    manifest_ids = {record.candidate_id for record in manifest.records}
    if manifest_ids != set(context_records_by_id):
        raise GuardrailError(
            "correspondencia 1:1 rota entre context-manifest.json y rule-draft-manifest.json"
        )

    expected_filenames = {record.relative_filename for record in manifest.records}
    actual_filenames = {
        path.name
        for path in rule_draft_dir.glob("*.json")
        if path.name != _RULE_DRAFT_MANIFEST_FILENAME
    }
    if actual_filenames != expected_filenames:
        raise GuardrailError(
            "artifacts/08-rule-drafts/ contiene archivos no referenciados por "
            "rule-draft-manifest.json (o le faltan archivos referenciados)"
        )

    drafts: dict[str, RuleDraft] = {}
    for record in manifest.records:
        if record.context_hash != context_records_by_id[record.candidate_id]:
            raise GuardrailError(
                f"context_hash de {record.candidate_id!r} no coincide entre 07-context/ y "
                "08-rule-drafts/ (drift)"
            )
        draft_path = rule_draft_dir / record.relative_filename
        if not draft_path.is_file():
            raise GuardrailError(
                f"falta el RuleDraft de {record.candidate_id!r} en 08-rule-drafts/"
            )
        try:
            draft = RuleDraft.model_validate_json(draft_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise GuardrailError(f"RuleDraft de {record.candidate_id!r} invalido") from exc
        if rule_draft_json_hash(draft) != record.rule_draft_hash:
            raise GuardrailError(
                f"rule_draft_hash de {record.candidate_id!r} no coincide (drift en "
                "08-rule-drafts/)"
            )
        drafts[record.candidate_id] = draft

    return manifest, rule_draft_manifest_hash, drafts


def _reread_context_packages(
    context_dir: Path, context_manifest: ContextDirectoryManifest
) -> dict[str, ContextPackage]:
    packages: dict[str, ContextPackage] = {}
    for record in context_manifest.context_records:
        package_path = context_dir / record.relative_filename
        if not package_path.is_file():
            raise GuardrailError(
                f"falta el ContextPackage de {record.candidate_id!r} en 07-context/"
            )
        try:
            package = ContextPackage.model_validate_json(
                package_path.read_text(encoding="utf-8")
            )
        except ValueError as exc:
            raise GuardrailError(f"ContextPackage de {record.candidate_id!r} invalido") from exc
        actual_hash = hashlib.sha256(package.to_stable_json().encode("utf-8")).hexdigest()
        if actual_hash != record.context_hash:
            raise GuardrailError(
                f"context_hash de {record.candidate_id!r} no coincide (drift en 07-context/)"
            )
        packages[record.candidate_id] = package
    return packages


def _load_context_manifest(
    context_dir: Path, *, source_package_hash: str
) -> ContextDirectoryManifest:
    manifest_path = context_dir / "context-manifest.json"
    if not manifest_path.is_file():
        raise GuardrailError("no se encontro artifacts/07-context/context-manifest.json")
    try:
        manifest = ContextDirectoryManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
    except ValueError as exc:
        raise GuardrailError("context-manifest.json invalido") from exc
    if manifest.source_package_hash != source_package_hash:
        raise GuardrailError("source_package_hash no coincide con context-manifest.json")
    return manifest


def _load_repair_prompts(settings: Settings) -> tuple[str, str, str, str]:
    try:
        system = load_prompt_template(
            settings.rule_repair_system_prompt_path,
            relative_path="prompts/rule_repair_system.md",
            expected_placeholder_counts={},
        )
        user = load_prompt_template(
            settings.rule_repair_user_prompt_path,
            relative_path="prompts/rule_repair_user.md",
            expected_placeholder_counts={
                _CONTEXT_PACKAGE_PLACEHOLDER: 1,
                _REJECTED_DRAFT_PLACEHOLDER: 1,
                _VIOLATIONS_PLACEHOLDER: 1,
            },
        )
    except PromptTemplateError as exc:
        raise GuardrailError(str(exc)) from exc
    return system.template_text, system.template_hash, user.template_text, user.template_hash


def _guardrail_filename(candidate_id: str) -> str:
    return hashlib.sha256(candidate_id.encode("utf-8")).hexdigest() + ".json"


def _read_existing_manifest(guardrail_dir: Path) -> GuardrailDirectoryManifest | None:
    manifest_path = guardrail_dir / _MANIFEST_FILENAME
    if not manifest_path.is_file():
        return None
    try:
        return GuardrailDirectoryManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
    except ValueError:
        return None


def _existing_output_is_reusable(
    existing: GuardrailDirectoryManifest | None,
    *,
    guardrail_dir: Path,
    source_package_hash: str,
    rule_draft_manifest_hash: str,
    rule_draft_schema_hash: str,
    provider: str,
    model: str,
    llm_repair_attempts: int,
    repair_system_template_hash: str,
    repair_user_template_hash: str,
    rule_draft_records_by_id: dict[str, str],
) -> bool:
    """Fast-path de idempotencia: nunca depende del estado anterior de
    RunState — solo del contenido real en disco. Nunca reutiliza un
    directorio parcial, con algun REJECTED, sin manifest, o con hashes
    inconsistentes (si el manifest existe y valida, ya es EVIDENCE_VALIDATED
    en todos sus records por construccion — ver GuardrailRecord)."""
    if existing is None:
        return False
    if existing.source_package_hash != source_package_hash:
        return False
    if existing.rule_draft_manifest_hash != rule_draft_manifest_hash:
        return False
    if existing.rule_draft_schema_hash != rule_draft_schema_hash:
        return False
    if existing.provider != provider or existing.model != model:
        return False
    if existing.llm_repair_attempts != llm_repair_attempts:
        return False
    if existing.guardrail_version != GUARDRAIL_VERSION:
        return False
    if existing.repair_system_template_hash != repair_system_template_hash:
        return False
    if existing.repair_user_template_hash != repair_user_template_hash:
        return False

    existing_by_id = {record.candidate_id: record for record in existing.records}
    if set(existing_by_id) != set(rule_draft_records_by_id):
        return False
    for candidate_id, initial_hash in rule_draft_records_by_id.items():
        record = existing_by_id[candidate_id]
        if record.initial_rule_draft_hash != initial_hash:
            return False
        artifact_path = guardrail_dir / record.relative_filename
        if not artifact_path.is_file():
            return False
        try:
            artifact = GuardrailCandidateArtifact.model_validate_json(
                artifact_path.read_text(encoding="utf-8")
            )
        except ValueError:
            return False
        artifact_hash = hashlib.sha256(artifact.to_stable_json().encode("utf-8")).hexdigest()
        if artifact_hash != record.guardrail_artifact_hash:
            return False

    expected_filenames = {record.relative_filename for record in existing.records}
    actual_filenames = {
        path.name for path in guardrail_dir.glob("*.json") if path.name != _MANIFEST_FILENAME
    }
    if actual_filenames != expected_filenames:
        return False
    return True


def _sanitize_failure_summary(text: str) -> str:
    return text[:_FAILURE_SUMMARY_MAX_LENGTH]


async def _complete_and_hash(
    client: OpenAICompatibleChatClient, messages: list[ChatMessage]
) -> tuple[dict[str, Any], str]:
    payload = await client.complete(messages)
    stable = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    response_hash = hashlib.sha256(stable.encode("utf-8")).hexdigest()
    return payload, response_hash


def _violations_payload(
    violations: list[GuardrailViolation], *, previous_attempt_failed_structurally: bool
) -> str:
    payload = {
        "violations": [violation.model_dump(mode="json") for violation in violations],
        "previous_repair_attempt_failed_structurally": previous_attempt_failed_structurally,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


async def _resolve_one_candidate(
    *,
    candidate_id: str,
    package: ContextPackage,
    initial_draft: RuleDraft,
    client: OpenAICompatibleChatClient,
    repair_system_text: str,
    repair_user_template_text: str,
    schema_validator: jsonschema.protocols.Validator,
    llm_repair_attempts: int,
    source_package_hash: str,
    context_hash: str,
) -> GuardrailCandidateArtifact:
    """Evalua el guardrail y, si hace falta, repara hasta
    `llm_repair_attempts` veces. Devuelve el artefacto SOLO si el
    candidato alcanza EVIDENCE_VALIDATED; lanza `GuardrailError` en
    cuanto se agota el presupuesto sin exito (fail-fast: el llamador
    detiene el procesamiento de inmediato)."""
    initial_hash = rule_draft_json_hash(initial_draft)
    current_draft = initial_draft
    violations = evaluate_guardrail(current_draft, package)
    repair_history: list[RepairAttemptRecord] = []
    previous_attempt_failed_structurally = False

    while any(v.severity == Severity.ERROR for v in violations):
        attempt_number = len(repair_history) + 1
        if attempt_number > llm_repair_attempts:
            # Representacion interna del resultado REJECTED: nunca se
            # escribe a disco (GUARDRAILS_APPLIED es fail-fast y
            # atomica; el directorio temporal se descarta completo en
            # run_guardrails_applied_stage), pero construirla aqui
            # demuestra en runtime que verdict/final_rule_draft.
            # evidence_validation_status quedan deterministicamente
            # coherentes tambien para el caso rechazado.
            rejected_draft = current_draft.model_copy(
                update={"evidence_validation_status": EvidenceValidationStatus.REJECTED}
            )
            GuardrailCandidateArtifact(
                candidate_id=candidate_id,
                source_package_hash=source_package_hash,
                context_hash=context_hash,
                initial_rule_draft_hash=initial_hash,
                final_rule_draft_hash=rule_draft_json_hash(rejected_draft),
                final_rule_draft=rejected_draft,
                guardrail_report=GuardrailReport(
                    candidate_id=candidate_id,
                    verdict=GuardrailVerdict.REJECTED,
                    violations=violations,
                    repair_attempts=len(repair_history),
                    evaluated_at=datetime.now(UTC),
                    source_package_hash=source_package_hash,
                ),
                repair_history=repair_history,
                warnings=[],
            )
            raise GuardrailError(
                f"candidato {candidate_id!r} agoto LLM_REPAIR_ATTEMPTS "
                f"({llm_repair_attempts}) sin alcanzar EVIDENCE_VALIDATED"
            )

        error_count_before = sum(1 for v in violations if v.severity == Severity.ERROR)
        rendered_user = render_prompt(
            repair_user_template_text,
            {
                _CONTEXT_PACKAGE_PLACEHOLDER: package.to_stable_json(),
                _REJECTED_DRAFT_PLACEHOLDER: current_draft.to_stable_json(),
                _VIOLATIONS_PLACEHOLDER: _violations_payload(
                    violations,
                    previous_attempt_failed_structurally=previous_attempt_failed_structurally,
                ),
            },
        )
        messages = [
            ChatMessage(role="system", content=repair_system_text),
            ChatMessage(role="user", content=rendered_user.effective_text),
        ]
        try:
            payload, response_hash = await _complete_and_hash(client, messages)
        except LlmClientError as exc:
            raise GuardrailError(
                f"fallo del cliente LLM reparando {candidate_id!r} (intento "
                f"{attempt_number}): {type(exc).__name__}"
            ) from exc

        try:
            repaired_draft = assemble_rule_draft(payload, schema_validator=schema_validator)
        except RuleDraftAssemblyError as exc:
            repair_history.append(
                RepairAttemptRecord(
                    attempt_number=attempt_number,
                    structurally_valid=False,
                    response_hash=response_hash,
                    produced_rule_draft_hash=None,
                    failure_code="structural_validation_failed",
                    failure_summary=_sanitize_failure_summary(str(exc)),
                    error_count_before=error_count_before,
                    error_count_after=None,
                    warning_count_after=None,
                )
            )
            previous_attempt_failed_structurally = True
            continue

        new_violations = evaluate_guardrail(repaired_draft, package)
        new_error_count = sum(1 for v in new_violations if v.severity == Severity.ERROR)
        new_warning_count = sum(1 for v in new_violations if v.severity == Severity.WARNING)
        repair_history.append(
            RepairAttemptRecord(
                attempt_number=attempt_number,
                structurally_valid=True,
                response_hash=response_hash,
                produced_rule_draft_hash=rule_draft_json_hash(repaired_draft),
                failure_code=None,
                failure_summary=None,
                error_count_before=error_count_before,
                error_count_after=new_error_count,
                warning_count_after=new_warning_count,
            )
        )
        previous_attempt_failed_structurally = False
        current_draft = repaired_draft
        violations = new_violations

    # final_rule_draft es una COPIA nueva del ultimo RuleDraft
    # estructuralmente valido (initial o reparado), con
    # evidence_validation_status actualizado deterministicamente a
    # EVIDENCE_VALIDATED: nunca sobrescribe artifacts/08-rule-drafts/
    # (ese archivo permanece PENDING e inmutable siempre).
    final_draft = current_draft.model_copy(
        update={"evidence_validation_status": EvidenceValidationStatus.EVIDENCE_VALIDATED}
    )
    report = GuardrailReport(
        candidate_id=candidate_id,
        verdict=GuardrailVerdict.EVIDENCE_VALIDATED,
        violations=violations,
        repair_attempts=len(repair_history),
        evaluated_at=datetime.now(UTC),
        source_package_hash=source_package_hash,
    )
    return GuardrailCandidateArtifact(
        candidate_id=candidate_id,
        source_package_hash=source_package_hash,
        context_hash=context_hash,
        initial_rule_draft_hash=initial_hash,
        final_rule_draft_hash=rule_draft_json_hash(final_draft),
        final_rule_draft=final_draft,
        guardrail_report=report,
        repair_history=repair_history,
        warnings=[],
    )


async def _resolve_all_candidates(
    *,
    candidates: list[tuple[str, ContextPackage, RuleDraft, str]],
    profile: LlmProfile,
    repair_system_text: str,
    repair_user_template_text: str,
    schema_validator: jsonschema.protocols.Validator,
    llm_repair_attempts: int,
    source_package_hash: str,
    temp_dir: Path,
) -> list[GuardrailRecord]:
    records: list[GuardrailRecord] = []
    async with OpenAICompatibleChatClient(profile) as client:
        for candidate_id, package, initial_draft, context_hash in candidates:
            artifact = await _resolve_one_candidate(
                candidate_id=candidate_id,
                package=package,
                initial_draft=initial_draft,
                client=client,
                repair_system_text=repair_system_text,
                repair_user_template_text=repair_user_template_text,
                schema_validator=schema_validator,
                llm_repair_attempts=llm_repair_attempts,
                source_package_hash=source_package_hash,
                context_hash=context_hash,
            )
            filename = _guardrail_filename(candidate_id)
            (temp_dir / filename).write_text(artifact.to_stable_json(), encoding="utf-8")
            records.append(
                GuardrailRecord(
                    candidate_id=candidate_id,
                    relative_filename=filename,
                    initial_rule_draft_hash=artifact.initial_rule_draft_hash,
                    final_rule_draft_hash=artifact.final_rule_draft_hash,
                    guardrail_artifact_hash=hashlib.sha256(
                        artifact.to_stable_json().encode("utf-8")
                    ).hexdigest(),
                    final_evidence_validation_status=EvidenceValidationStatus.EVIDENCE_VALIDATED,
                    repair_attempts_used=len(artifact.repair_history),
                    repair_response_hashes=[
                        attempt.response_hash
                        for attempt in artifact.repair_history
                        if attempt.response_hash is not None
                    ],
                )
            )
    return records


def run_guardrails_applied_stage(
    *,
    run_id: str,
    source_package_hash: str,
    run_stages: list[StageExecution],
    context_dir: Path,
    rule_draft_dir: Path,
    guardrail_dir: Path,
    settings: Settings,
) -> list[str]:
    """Ejecuta GUARDRAILS_APPLIED y devuelve los warnings (resumen para
    RunState)."""
    _verify_guardrails_applied_precondition(run_stages)

    artifacts_dir = guardrail_dir.parent
    cleanup_orphan_directories(artifacts_dir, _DIR_NAME)

    context_manifest = _load_context_manifest(context_dir, source_package_hash=source_package_hash)
    context_records_by_id = {
        record.candidate_id: record.context_hash for record in context_manifest.context_records
    }

    rule_draft_manifest, rule_draft_manifest_hash, drafts_by_id = (
        _reread_and_verify_rule_draft_directory(
            rule_draft_dir,
            source_package_hash=source_package_hash,
            context_records_by_id=context_records_by_id,
        )
    )

    try:
        profile = resolve_llm_profile(settings)
    except LlmClientError as exc:
        raise GuardrailError(f"perfil LLM invalido: {exc}") from exc

    schema, rule_draft_schema_hash = load_rule_draft_schema(settings.rule_draft_schema_path)
    validator_cls = jsonschema.validators.validator_for(schema)
    schema_validator = validator_cls(schema)

    repair_system_text, repair_system_hash, repair_user_template_text, repair_user_hash = (
        _load_repair_prompts(settings)
    )

    rule_draft_records_by_id = {
        record.candidate_id: record.rule_draft_hash for record in rule_draft_manifest.records
    }

    existing_manifest = _read_existing_manifest(guardrail_dir)
    if _existing_output_is_reusable(
        existing_manifest,
        guardrail_dir=guardrail_dir,
        source_package_hash=source_package_hash,
        rule_draft_manifest_hash=rule_draft_manifest_hash,
        rule_draft_schema_hash=rule_draft_schema_hash,
        provider=profile.provider.value,
        model=profile.model,
        llm_repair_attempts=settings.llm_repair_attempts,
        repair_system_template_hash=repair_system_hash,
        repair_user_template_hash=repair_user_hash,
        rule_draft_records_by_id=rule_draft_records_by_id,
    ):
        return [f"{len(rule_draft_records_by_id)} guardrail(s) (sin cambios)"]

    if not rule_draft_records_by_id:
        empty_manifest = GuardrailDirectoryManifest(
            run_id=run_id,
            source_package_hash=source_package_hash,
            rule_draft_manifest_hash=rule_draft_manifest_hash,
            rule_draft_schema_hash=rule_draft_schema_hash,
            provider=profile.provider.value,
            model=profile.model,
            llm_repair_attempts=settings.llm_repair_attempts,
            guardrail_version=GUARDRAIL_VERSION,
            repair_system_template_hash=repair_system_hash,
            repair_user_template_hash=repair_user_hash,
            records=[],
            guardrail_count=0,
            warnings=[],
        )
        _write_manifest_and_promote(artifacts_dir, guardrail_dir, empty_manifest)
        return ["0 guardrail(s)"]

    packages_by_id = _reread_context_packages(context_dir, context_manifest)

    candidates = sorted(
        (
            (candidate_id, packages_by_id[candidate_id], draft, context_records_by_id[candidate_id])
            for candidate_id, draft in drafts_by_id.items()
        ),
        key=lambda item: item[0],
    )

    temp_dir = new_temp_directory(artifacts_dir, _DIR_NAME)
    try:
        records = asyncio.run(
            _resolve_all_candidates(
                candidates=candidates,
                profile=profile,
                repair_system_text=repair_system_text,
                repair_user_template_text=repair_user_template_text,
                schema_validator=schema_validator,
                llm_repair_attempts=settings.llm_repair_attempts,
                source_package_hash=source_package_hash,
                temp_dir=temp_dir,
            )
        )
    except BaseException:
        # Fail-fast: cualquier candidato REJECTED (o fallo de
        # infraestructura) descarta TODO el directorio temporal de
        # inmediato; los candidatos restantes nunca llegan a llamarse.
        discard_temp_directory(temp_dir)
        raise

    records.sort(key=lambda record: record.candidate_id)

    if {record.candidate_id for record in records} != set(rule_draft_records_by_id):
        raise GuardrailError("correspondencia 1:1 rota entre RuleDraftRecord y GuardrailRecord")

    manifest = GuardrailDirectoryManifest(
        run_id=run_id,
        source_package_hash=source_package_hash,
        rule_draft_manifest_hash=rule_draft_manifest_hash,
        rule_draft_schema_hash=rule_draft_schema_hash,
        provider=profile.provider.value,
        model=profile.model,
        llm_repair_attempts=settings.llm_repair_attempts,
        guardrail_version=GUARDRAIL_VERSION,
        repair_system_template_hash=repair_system_hash,
        repair_user_template_hash=repair_user_hash,
        records=records,
        guardrail_count=len(records),
        warnings=[],
    )

    # Igual que en rule_drafts_generated_stage.py: solo se omite la
    # promocion si el directorio en disco ya coincide EXACTAMENTE con lo
    # que el manifest fresco declara -- si la regeneracion se forzo por
    # un archivo .json huerfano no referenciado, la promocion sigue
    # siendo necesaria para limpiarlo (invariante de 09-guardrails/).
    if existing_manifest == manifest:
        expected_filenames = {record.relative_filename for record in manifest.records}
        actual_filenames = {
            path.name for path in guardrail_dir.glob("*.json") if path.name != _MANIFEST_FILENAME
        }
        if actual_filenames == expected_filenames:
            discard_temp_directory(temp_dir)
            return [f"{manifest.guardrail_count} guardrail(s) (sin cambios)"]

    try:
        (temp_dir / _MANIFEST_FILENAME).write_text(manifest.to_stable_json(), encoding="utf-8")
        swap_directory(temp_dir, guardrail_dir, error_factory=GuardrailError)
    finally:
        discard_temp_directory(temp_dir)
    return [f"{manifest.guardrail_count} guardrail(s)"]


def _write_manifest_and_promote(
    artifacts_dir: Path, guardrail_dir: Path, manifest: GuardrailDirectoryManifest
) -> None:
    temp_dir = new_temp_directory(artifacts_dir, _DIR_NAME)
    try:
        (temp_dir / _MANIFEST_FILENAME).write_text(manifest.to_stable_json(), encoding="utf-8")
        swap_directory(temp_dir, guardrail_dir, error_factory=GuardrailError)
    finally:
        discard_temp_directory(temp_dir)
