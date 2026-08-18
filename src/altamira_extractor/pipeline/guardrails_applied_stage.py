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
inmutable de la primera respuesta aceptada del modelo.

Checkpoint correctivo (catalogo de alias de evidencia): el modelo tampoco
escribe `evidence_id`/`evidence_path` reales durante una reparacion de
guardrail. El catalogo se construye UNA vez por candidato
(`build_evidence_catalog(package)`, ver `evidence_catalog.py`) y se
reutiliza en todos los intentos de ese candidato. El `RuleDraft` YA
RESUELTO que se le muestra al modelo como "borrador rechazado"
(`current_draft`) se redacta primero con `redact_rule_draft_for_prompt`
-- sin esto, el modelo veria IDs/paths reales en el propio ejemplo de
borrador rechazado y podria copiarlos, reintroduciendo el problema
original en esta capa. La respuesta de reparacion se resuelve con
`assemble_rule_draft_with_evidence_catalog` (nunca `assemble_rule_draft`
directo), que aplica las mismas validaciones de siempre sin relajar
ninguna."""

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
from .deterministic_guardrail import (
    GUARDRAIL_VERSION,
    augment_claims_with_authoritative_anchors,
    evaluate_guardrail,
    reconstruct_traceability_deterministically,
    sanitize_traceability_number_date_violations,
)
from .errors import GuardrailError, LlmClientError, PromptTemplateError
from .evidence_catalog import EvidenceCatalog, build_evidence_catalog, redact_rule_draft_for_prompt
from .llm_client import ChatMessage, LlmProfile, OpenAICompatibleChatClient, resolve_llm_profile
from .prompt_loader import load_prompt_template, render_prompt
from .rule_draft_assembly import (
    RuleDraftAssemblyError,
    assemble_rule_draft_with_evidence_catalog,
    load_rule_draft_schema,
    rule_draft_json_hash,
)

_DIR_NAME = "09-guardrails"
_MANIFEST_FILENAME = "guardrail-manifest.json"
_RULE_DRAFT_MANIFEST_FILENAME = "rule-draft-manifest.json"
_FAILURE_SUMMARY_MAX_LENGTH = 500
# Checkpoint correctivo (observabilidad de fallos de reparacion): artefacto
# de diagnostico NO contractual, hermano de `09-guardrails/` pero fuera de
# el -- se escribe directo (nunca via new_temp_directory/swap_directory),
# asi que nunca compromete la atomicidad del directorio contractual. Se
# limpia al comienzo de cada corrida y se reescribe unicamente si esa
# corrida vuelve a fallar.
_FAILURE_DIAGNOSTICS_FILENAME = "guardrails-failure-diagnostics.json"

_CONTEXT_PACKAGE_PLACEHOLDER = "{{CONTEXT_PACKAGE_JSON}}"
_REJECTED_DRAFT_PLACEHOLDER = "{{REJECTED_RULE_DRAFT_JSON}}"
_VIOLATIONS_PLACEHOLDER = "{{GUARDRAIL_VIOLATIONS_JSON}}"
_EVIDENCE_CATALOG_PLACEHOLDER = "{{EVIDENCE_CATALOG_JSON}}"


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
                _EVIDENCE_CATALOG_PLACEHOLDER: 1,
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


def _build_failure_diagnostics(
    *,
    candidate_id: str,
    package: ContextPackage,
    current_draft: RuleDraft,
    violations: list[GuardrailViolation],
    repair_history: list[RepairAttemptRecord],
) -> dict[str, Any]:
    """Resumen sanitizado (checkpoint correctivo: observabilidad de
    fallos de reparacion) del ULTIMO estado de un candidato que agoto
    `LLM_REPAIR_ATTEMPTS`: nunca incluye el prompt efectivo, el body
    crudo de ninguna respuesta ni credenciales -- solo identificadores,
    violations ya sanitizadas (`GuardrailViolation.message` nunca cita
    el payload del modelo), los claims del ultimo `RuleDraft` intentado
    (evidence_ids/evidence_paths reales -- el mismo contenido que ya se
    habria persistido en `artifacts/09-guardrails/` de haber tenido
    exito, nunca un secreto) y los `response_hash` ya calculados por
    cada intento. El llamador decide donde persistir esto (nunca dentro
    de `artifacts/09-guardrails/`, que debe permanecer atomico)."""
    return {
        "candidate_id": candidate_id,
        "program": package.scope.program,
        "paragraph": package.scope.paragraph,
        "attempt_count": len(repair_history),
        "last_verdict": GuardrailVerdict.REJECTED.value,
        "violations": [
            {
                "violation_id": violation.violation_id,
                "rule": violation.rule,
                "field": violation.field,
                "message": violation.message,
                "severity": violation.severity.value,
            }
            for violation in violations
        ],
        "claims": [
            {
                "claim_id": claim.claim_id,
                "field": claim.field.value,
                "evidence_ids": list(claim.evidence_ids),
                "evidence_paths": list(claim.evidence_paths),
            }
            for claim in current_draft.claims
        ],
        "response_hashes": [
            attempt.response_hash
            for attempt in repair_history
            if attempt.response_hash is not None
        ],
    }


def _apply_deterministic_guardrail_corrections(
    rule_draft: RuleDraft, package: ContextPackage, violations: list[GuardrailViolation]
) -> tuple[RuleDraft, list[GuardrailViolation]]:
    """Checkpoint correctivo v1.18.2 (extendido en el cierre de
    fiabilidad de campos de negocio): intenta, en cascada, TRES
    correcciones deterministicas ANTES de que el llamador decida si
    consumir un intento de reparacion LLM. Nunca llama al modelo.
    (Anteriormente `_apply_deterministic_traceability_sanitization`,
    renombrada porque ya no se limita a traceability.)

    Pasos 1-2 (SOLO traceability, ver docstrings de ambas funciones) --
    sin cambios respecto al checkpoint anterior:
    1. `sanitize_traceability_number_date_violations`: elimina
       UNICAMENTE el/los elemento(s) ofensivos, preservando el resto.
    2. `reconstruct_traceability_deterministically`: fallback final si
       el paso 1 no puede aplicarse (dejaria el campo vacio) -- caso
       real: candidato 4000-VALIDAR-PRODUCTO de PAQUETE_SINTETICO_
       CATHERINE_CORREGIDO_APP_ACTUAL.zip.

    Paso 3 (NUEVO, campos de NEGOCIO -- condition/effect/etc., NUNCA
    traceability) -- `augment_claims_with_authoritative_anchors`: casos
    reales VALIDAR-MORA-PARA (effect) de PAQUETE_SINTETICO_CLIENTES_
    EMPRESAS_MULTIPROGRAMA_15_REGLAS.zip y MAIN-PARA (condition) de
    PAQUETE_SINTETICO_GROUND_TRUTH_FASE_15D.zip: el numero que el
    modelo escribio es un hecho autoritativo REAL (`decision.
    expression`), solo le faltaba citar `$.decision` en evidence_refs.
    Esta funcion NUNCA toca el VALOR de ningun campo -- unicamente
    amplia claims[].evidence_paths/evidence_ids con la ancla
    autoritativa real ya verificada por coincidencia exacta de token
    (ver su propio docstring para el TODO-O-NADA y las anclas exactas
    permitidas). Si el token no resuelve contra ninguna ancla
    autoritativa, esta funcion devuelve `None` y el candidato sigue su
    ciclo de reparacion LLM existente sin cambios -- posible afirmacion
    de negocio genuinamente no soportada, que DEBE seguir fallando
    cerrado.

    Cada paso solo se intenta si el anterior no dejo el candidato sin
    violaciones ERROR. Si ninguno aplica, devuelve `rule_draft`/
    `violations` sin cambios (misma identidad de objeto -- el llamador
    la usa para detectar si hubo cambio) y el llamador continua
    exactamente como antes (ciclo de reparacion LLM existente, sin
    modificar).

    Provenance: cuando esto cambia el borrador ANTES de cualquier
    intento LLM (repair_history aun vacio), el llamador registra un
    `RepairAttemptRecord` sintetico con `response_hash=None` -- para
    que `GuardrailCandidateArtifact` (contracts/guardrail_candidate.py)
    conserve su cadena de provenance existente (todo cambio de
    contenido debe quedar trazado en repair_history) sin exigir
    ninguna modificacion de schema: `response_hash` ya era opcional, y
    `None` distingue de forma legible "correccion deterministica, cero
    llamadas al modelo" de un intento LLM real -- sin distinguir entre
    los tres mecanismos (todos son "deterministica, cero llamadas al
    modelo", la unica distincion que el contrato de auditoria existente
    necesita). Cuando esto cambia el borrador DESPUES de un intento LLM
    real (dentro del bucle), el resultado se pliega en el MISMO
    `RepairAttemptRecord` que ese intento ya iba a registrar -- no se
    crea una entrada separada."""
    current_draft = rule_draft
    current_violations = violations

    sanitized = sanitize_traceability_number_date_violations(current_draft, current_violations)
    if sanitized is not None:
        current_draft = sanitized
        current_violations = evaluate_guardrail(current_draft, package)

    if any(v.severity == Severity.ERROR for v in current_violations):
        reconstructed = reconstruct_traceability_deterministically(
            current_draft, current_violations
        )
        if reconstructed is not None:
            current_draft = reconstructed
            current_violations = evaluate_guardrail(current_draft, package)

    if any(v.severity == Severity.ERROR for v in current_violations):
        augmented = augment_claims_with_authoritative_anchors(
            current_draft, package, current_violations
        )
        if augmented is not None:
            current_draft = augmented
            current_violations = evaluate_guardrail(current_draft, package)

    return current_draft, current_violations


async def _resolve_one_candidate(
    *,
    candidate_id: str,
    package: ContextPackage,
    catalog: EvidenceCatalog,
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
    detiene el procesamiento de inmediato). `catalog` -- construido UNA
    vez por candidato por el llamador -- se reutiliza sin cambios en
    todos los intentos de reparacion de este candidato."""
    initial_hash = rule_draft_json_hash(initial_draft)
    current_draft = initial_draft
    violations = evaluate_guardrail(current_draft, package)
    repair_history: list[RepairAttemptRecord] = []

    sanitized_draft, sanitized_violations = _apply_deterministic_guardrail_corrections(
        current_draft, package, violations
    )
    if sanitized_draft is not current_draft:
        # Provenance sintetica: el borrador cambio antes de cualquier
        # intento LLM. response_hash=None marca explicitamente que
        # NINGUNA llamada al modelo produjo este cambio (ver docstring
        # de _apply_deterministic_guardrail_corrections) y evita que
        # GuardrailCandidateArtifact rechace un final_rule_draft que ya
        # no coincide con initial_rule_draft_hash sin ningun intento
        # structurally_valid registrado.
        repair_history.append(
            RepairAttemptRecord(
                attempt_number=1,
                structurally_valid=True,
                response_hash=None,
                produced_rule_draft_hash=rule_draft_json_hash(sanitized_draft),
                error_count_before=sum(1 for v in violations if v.severity == Severity.ERROR),
                error_count_after=sum(
                    1 for v in sanitized_violations if v.severity == Severity.ERROR
                ),
                warning_count_after=sum(
                    1 for v in sanitized_violations if v.severity == Severity.WARNING
                ),
            )
        )
    current_draft, violations = sanitized_draft, sanitized_violations
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
            diagnostics = _build_failure_diagnostics(
                candidate_id=candidate_id,
                package=package,
                current_draft=current_draft,
                violations=violations,
                repair_history=repair_history,
            )
            raise GuardrailError(
                f"candidato {candidate_id!r} agoto LLM_REPAIR_ATTEMPTS "
                f"({llm_repair_attempts}) sin alcanzar EVIDENCE_VALIDATED",
                diagnostics=diagnostics,
            )

        error_count_before = sum(1 for v in violations if v.severity == Severity.ERROR)
        # checkpoint correctivo: el borrador rechazado se muestra
        # REDACTADO (evidence_refs, nunca IDs/paths reales) -- sin esto
        # el modelo veria identificadores reales en su propio ejemplo de
        # "borrador rechazado" y podria copiarlos, reintroduciendo el
        # problema original en esta etapa.
        redacted_current_draft = redact_rule_draft_for_prompt(current_draft, catalog)
        redacted_current_draft_json = (
            json.dumps(redacted_current_draft, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n"
        )
        rendered_user = render_prompt(
            repair_user_template_text,
            {
                _CONTEXT_PACKAGE_PLACEHOLDER: package.to_stable_json(),
                _REJECTED_DRAFT_PLACEHOLDER: redacted_current_draft_json,
                _VIOLATIONS_PLACEHOLDER: _violations_payload(
                    violations,
                    previous_attempt_failed_structurally=previous_attempt_failed_structurally,
                ),
                _EVIDENCE_CATALOG_PLACEHOLDER: catalog.to_prompt_json(),
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
            repaired_draft = assemble_rule_draft_with_evidence_catalog(
                payload, catalog=catalog, package=package, schema_validator=schema_validator
            )
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
        repaired_draft, new_violations = _apply_deterministic_guardrail_corrections(
            repaired_draft, package, new_violations
        )
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
            # Construido UNA vez por candidato, antes de cualquier intento
            # de reparacion: la numeracion de alias debe permanecer
            # estable durante todo el ciclo de reparacion de este
            # candidato.
            catalog = build_evidence_catalog(package)
            artifact = await _resolve_one_candidate(
                candidate_id=candidate_id,
                package=package,
                catalog=catalog,
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
                    # repair_attempts_used cuenta intentos LLM reales
                    # (con response_hash), nunca la sanitizacion
                    # deterministica sintetica de traceability (checkpoint
                    # correctivo v1.18.2, response_hash=None) -- esa
                    # correccion no consume presupuesto LLM_REPAIR_ATTEMPTS
                    # ni debe reportarse como "reparacion" en la UI/API.
                    repair_attempts_used=sum(
                        1
                        for attempt in artifact.repair_history
                        if attempt.response_hash is not None
                    ),
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
    # Un diagnostico de una corrida ANTERIOR fallida queda obsoleto en
    # cuanto se intenta de nuevo (exitosa o no): se limpia aqui y solo se
    # reescribe si ESTA corrida tambien falla (ver el `except GuardrailError`
    # mas abajo).
    (artifacts_dir / _FAILURE_DIAGNOSTICS_FILENAME).unlink(missing_ok=True)

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
    except GuardrailError as exc:
        # Fail-fast: cualquier candidato REJECTED (o fallo de
        # infraestructura) descarta TODO el directorio temporal de
        # inmediato; los candidatos restantes nunca llegan a llamarse.
        # Si el candidato agoto la reparacion (ver `diagnostics`), el
        # resumen sanitizado sobrevive en un artefacto NO contractual
        # (nunca dentro de `09-guardrails/`) para que el fallo sea
        # diagnosticable sin depender unicamente del mensaje de texto.
        discard_temp_directory(temp_dir)
        if exc.diagnostics is not None:
            (artifacts_dir / _FAILURE_DIAGNOSTICS_FILENAME).write_text(
                json.dumps(exc.diagnostics, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
        raise
    except BaseException:
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
