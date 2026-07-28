"""Orquestacion de la etapa RULE_DRAFTS_GENERATED:
CONTEXTS_BUILT -> artifacts/08-rule-drafts/ (Prompt 12).

Etapa atomica para el CONJUNTO COMPLETO de candidatos de la corrida: solo
puede quedar SUCCEEDED cuando existe exactamente un RuleDraft final
estructuralmente valido -- y con evidencia real -- por cada ContextRecord,
todos validan contra Pydantic y `rule-draft.schema.json`, y todos estan en
`rule-draft-manifest.json` — nunca hay exito parcial.

Checkpoint correctivo (reparacion estructural dedicada): cuando la
respuesta INICIAL del modelo para un candidato es JSON valido pero no
ensambla como RuleDraft (campo faltante, clave extra, tipo invalido,
alguno de los tres campos gobernados por Python, o un evidence_id/
evidence_path que no existe literalmente en el ContextPackage real -- ver
`rule_draft_assembly.check_evidence_references`), la etapa ejecuta un
ciclo acotado de reparacion (hasta `settings.llm_repair_attempts`) usando
prompts DEDICADOS y versionados
(`prompts/rule_structure_repair_system.md`/`rule_structure_repair_user.md`)
-- nunca los prompts de GUARDRAILS_APPLIED (`rule_repair_*.md`), que
reparan violaciones semanticas de guardrail, no forma.

Checkpoint correctivo (catalogo de alias de evidencia): el modelo NUNCA
escribe un `evidence_id`/`evidence_path` real, ni en la respuesta inicial
ni en ninguna reparacion. Antes de la primera llamada, `build_evidence_
catalog(package)` (ver `evidence_catalog.py`) construye, UNA sola vez por
candidato y reutilizado en todos los intentos, un catalogo deterministico
`E001, E002, ...` donde cada alias representa conjuntamente un
`(evidence_id, evidence_path)` real. El modelo solo ve alias + una
descripcion funcional (`EvidenceCatalog.to_prompt_json()`, nunca el
ContextPackage se envia como fuente de identificadores para citar);
`claims[].evidence_refs` (alias) se resuelve deterministicamente a
`evidence_ids`/`evidence_paths` reales ANTES de ensamblar el RuleDraft
(`rule_draft_assembly.assemble_rule_draft_with_evidence_catalog`). Un
alias inexistente entra al mismo ciclo de reparacion acotado que
cualquier otro fallo estructural -- nunca se corrige por similitud,
prefijo o distancia de edicion.

Solo si TODOS los intentos de reparacion tambien fallan (estructural,
alias inexistente, o evidencia real inexistente) la etapa completa
aborta: el directorio temporal se descarta entero, la salida canonica
anterior (si existia) se conserva intacta, y nunca se promueve un draft
parcial ni se alcanza GUARDRAILS_APPLIED. Un error de infraestructura
(`LlmClientError`: 401/403/429/timeout/red/5xx) o de precondicion (perfil
LLM invalido, ContextPackage/manifest con drift) sigue siendo SIEMPRE un
fallo directo, en cualquier intento (inicial o de reparacion): nunca
dispara ni consume el ciclo de reparacion.

`RuleDraftGenerationBuilder` es de solo lectura sobre `artifacts/07-context/`:
nunca reejecuta Q1-Q7 ni reconstruye ContextPackage."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
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
from .evidence_catalog import EvidenceCatalog, build_evidence_catalog
from .llm_client import ChatMessage, LlmProfile, OpenAICompatibleChatClient, resolve_llm_profile
from .prompt_loader import load_prompt_template, render_prompt
from .rule_draft_assembly import (
    RuleDraftAssemblyError,
    ValidationIssue,
    assemble_rule_draft_with_evidence_catalog,
    load_rule_draft_schema,
    rule_draft_json_hash,
)

logger = logging.getLogger("altamira_extractor.pipeline.rule_drafts_generated_stage")

_DIR_NAME = "08-rule-drafts"
_MANIFEST_FILENAME = "rule-draft-manifest.json"
_CONTEXT_PACKAGE_PLACEHOLDER = "{{CONTEXT_PACKAGE_JSON}}"
# Catalogo de alias de evidencia (checkpoint correctivo): unico
# placeholder compartido por el prompt de escritura inicial y el de
# reparacion estructural -- reemplaza las dos listas independientes
# (ALLOWED_EVIDENCE_IDS_JSON/ALLOWED_EVIDENCE_PATHS_JSON) que existian
# antes, que nunca garantizaban que un id y un path elegidos por el
# modelo realmente correspondieran a la misma evidencia.
_EVIDENCE_CATALOG_PLACEHOLDER = "{{EVIDENCE_CATALOG_JSON}}"

# Placeholders del prompt de reparacion ESTRUCTURAL dedicado (checkpoint
# correctivo): deliberadamente distintos y separados de los que usa
# GUARDRAILS_APPLIED (`rule_repair_system.md`/`rule_repair_user.md`, que
# reparan violaciones de guardrail, no forma). Nunca se envia el
# ContextPackage completo a este prompt.
_CANDIDATE_ID_PLACEHOLDER = "{{CANDIDATE_ID}}"
_REJECTED_PAYLOAD_PLACEHOLDER = "{{REJECTED_PAYLOAD_JSON}}"
_VALIDATION_ERRORS_PLACEHOLDER = "{{VALIDATION_ERRORS_JSON}}"
_STRUCTURE_REPAIR_SYSTEM_FILENAME = "rule_structure_repair_system.md"
_STRUCTURE_REPAIR_USER_FILENAME = "rule_structure_repair_user.md"


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
            expected_placeholder_counts={
                _CONTEXT_PACKAGE_PLACEHOLDER: 1,
                _EVIDENCE_CATALOG_PLACEHOLDER: 1,
            },
        )
    except PromptTemplateError as exc:
        raise RuleDraftGenerationError(str(exc)) from exc
    return system.template_text, system.template_hash, user.template_text, user.template_hash


def _structure_repair_prompt_path(settings: Settings, filename: str) -> Path:
    """Los prompts de reparacion ESTRUCTURAL son siempre hermanos de
    `rule_writer_system.md` (mismo directorio `prompts/`, real o de test):
    no se agrega ningun campo nuevo a `Settings` para esto -- se deriva
    del path ya existente, igual que cualquier otro recurso versionado
    junto a el."""
    return settings.rule_writer_system_prompt_path.parent / filename


def _load_structure_repair_prompts(settings: Settings) -> tuple[str, str]:
    """Devuelve (system_text, user_template_text) de los prompts
    DEDICADOS de reparacion estructural -- nunca los de GUARDRAILS_APPLIED
    (`rule_repair_system.md`/`rule_repair_user.md`, que reparan
    violaciones de guardrail, no forma). El prompt de usuario nunca recibe
    `{{CONTEXT_PACKAGE_JSON}}`: solo candidate_id, payload rechazado,
    errores sanitizados y el catalogo de alias de evidencia (nunca
    evidence_id/evidence_path reales)."""
    try:
        system = load_prompt_template(
            _structure_repair_prompt_path(settings, _STRUCTURE_REPAIR_SYSTEM_FILENAME),
            relative_path=f"prompts/{_STRUCTURE_REPAIR_SYSTEM_FILENAME}",
            expected_placeholder_counts={},
        )
        user = load_prompt_template(
            _structure_repair_prompt_path(settings, _STRUCTURE_REPAIR_USER_FILENAME),
            relative_path=f"prompts/{_STRUCTURE_REPAIR_USER_FILENAME}",
            expected_placeholder_counts={
                _CANDIDATE_ID_PLACEHOLDER: 1,
                _REJECTED_PAYLOAD_PLACEHOLDER: 1,
                _VALIDATION_ERRORS_PLACEHOLDER: 1,
                _EVIDENCE_CATALOG_PLACEHOLDER: 1,
            },
        )
    except PromptTemplateError as exc:
        raise RuleDraftGenerationError(str(exc)) from exc
    return system.template_text, user.template_text


class _LazyStructureRepairPrompts:
    """Carga `rule_structure_repair_system.md`/`rule_structure_repair_user.md`
    UNICAMENTE la primera vez que algun candidato realmente necesita una
    reparacion (memoizado para el resto de la corrida). Si ningun
    candidato falla -- el caso comun -- estos archivos ni siquiera se leen
    ni se validan: una corrida 100% exitosa nunca depende de que la
    infraestructura de reparacion (que no usa) este bien configurada."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cached: tuple[str, str] | None = None

    def get(self) -> tuple[str, str]:
        if self._cached is None:
            self._cached = _load_structure_repair_prompts(self._settings)
        return self._cached


def _classify_validation_issues(
    issues: tuple[ValidationIssue, ...],
) -> tuple[list[str], list[str], list[str]]:
    """Clasifica errores estructurados en 3 categorias legibles para el
    mensaje final (nunca para decidir logica): faltantes, adicionales, o
    de tipo/valor invalido. Pydantic v2 usa `type="missing"`/
    `"extra_forbidden"`; jsonschema usa el nombre del validador
    (`"required"`/`"additionalProperties"`) en su lugar."""
    missing: list[str] = []
    extra: list[str] = []
    invalid: list[str] = []
    for issue in issues:
        if issue.type in ("missing", "required"):
            missing.append(issue.loc)
        elif issue.type in (
            "extra_forbidden",
            "additionalProperties",
            "forbidden_self_assignment",
            "forbidden_direct_evidence_reference",
        ):
            extra.append(issue.loc)
        else:
            # Incluye "unknown_evidence_alias": un alias que no existe en
            # el catalogo se clasifica como valor invalido, nunca se
            # aproxima ni se corrige.
            invalid.append(f"{issue.loc} ({issue.type})")
    return sorted(set(missing)), sorted(set(extra)), sorted(set(invalid))


def _structural_failure_message(
    candidate_id: str, *, repair_attempts_used: int, issues: tuple[ValidationIssue, ...]
) -> str:
    """Mensaje final tras agotar la reparacion: candidate_id, intentos
    realizados y los campos afectados por categoria -- NUNCA el payload
    completo ni el texto crudo de `msg` (que ya se registro por separado
    via logging estructurado, ver `_log_structural_failure`)."""
    missing, extra, invalid = _classify_validation_issues(issues)
    parts = [
        f"candidato {candidate_id!r} agoto la reparacion estructural "
        f"({repair_attempts_used} intento(s) de reparacion) sin producir un RuleDraft valido"
    ]
    if missing:
        parts.append(f"campos faltantes: {missing}")
    if extra:
        parts.append(f"campos adicionales: {extra}")
    if invalid:
        parts.append(f"campos con tipo/valor invalido: {invalid}")
    return "; ".join(parts)


def _log_structural_failure(
    candidate_id: str, *, attempt: int, issues: tuple[ValidationIssue, ...]
) -> None:
    """Registra unicamente loc/type/msg por error (nunca el payload, ni
    `input`, ni headers/credenciales -- ninguno de los cuales esta
    disponible aqui de todas formas)."""
    logger.info(
        "rule_draft_structural_validation_failed",
        extra={
            "candidate_id": candidate_id,
            "attempt": attempt,
            "issue_count": len(issues),
            "issues": [{"loc": i.loc, "type": i.type, "msg": i.msg} for i in issues],
        },
    )


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


@dataclass(frozen=True)
class _AcceptedDraft:
    rule_draft: RuleDraft
    response_hash: str
    writer_user_effective_hash: str
    repair_attempts_used: int


def _stable_payload_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _validation_errors_payload(issues: tuple[ValidationIssue, ...]) -> str:
    payload = {
        "validation_errors": [
            {"loc": issue.loc, "type": issue.type, "msg": issue.msg} for issue in issues
        ]
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


async def _generate_one_draft(
    *,
    candidate_id: str,
    package: ContextPackage,
    catalog: EvidenceCatalog,
    client: OpenAICompatibleChatClient,
    system_text: str,
    user_template_text: str,
    repair_prompts: _LazyStructureRepairPrompts,
    schema_validator: jsonschema.protocols.Validator,
    llm_repair_attempts: int,
    max_context_package_json_chars: int,
) -> _AcceptedDraft:
    """Genera el draft de UN candidato: llamada inicial, y -- solo si la
    respuesta es JSON valido pero no ensambla como RuleDraft, cita un
    alias de evidencia inexistente, o cita un evidence_id/evidence_path
    real inexistente en el ContextPackage -- hasta `llm_repair_attempts`
    llamadas de reparacion adicionales usando los prompts ESTRUCTURALES
    dedicados (nunca los de GUARDRAILS_APPLIED). `catalog` se construye
    UNA vez por candidato en `_generate_all_drafts` y se reutiliza sin
    cambios en todos los intentos: la numeracion de alias debe
    mantenerse estable durante todo el ciclo de reparacion de un mismo
    candidato. Un `LlmClientError` (en la llamada inicial o en cualquier
    repair) SIEMPRE propaga de inmediato como fallo directo: nunca se
    cuenta como intento de reparacion ni se reintenta aqui (eso ya lo
    resuelve, con sus propios reintentos HTTP acotados,
    `OpenAICompatibleChatClient.complete()`)."""
    context_json = package.to_stable_json()
    if len(context_json) > max_context_package_json_chars:
        raise RuleDraftGenerationError(
            f"el ContextPackage de {candidate_id!r} serializado excede el limite "
            f"configurado ({max_context_package_json_chars} caracteres)"
        )

    catalog_json = catalog.to_prompt_json()
    rendered = render_prompt(
        user_template_text,
        {_CONTEXT_PACKAGE_PLACEHOLDER: context_json, _EVIDENCE_CATALOG_PLACEHOLDER: catalog_json},
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

    repair_attempts_used = 0
    while True:
        try:
            rule_draft = assemble_rule_draft_with_evidence_catalog(
                payload, catalog=catalog, package=package, schema_validator=schema_validator
            )
        except RuleDraftAssemblyError as exc:
            _log_structural_failure(
                candidate_id, attempt=repair_attempts_used, issues=exc.validation_errors
            )
            if repair_attempts_used >= llm_repair_attempts:
                raise RuleDraftGenerationError(
                    _structural_failure_message(
                        candidate_id,
                        repair_attempts_used=repair_attempts_used,
                        issues=exc.validation_errors,
                    ),
                    validation_errors=exc.validation_errors,
                ) from exc

            repair_attempts_used += 1
            repair_system_text, repair_user_template_text = repair_prompts.get()
            repair_rendered = render_prompt(
                repair_user_template_text,
                {
                    _CANDIDATE_ID_PLACEHOLDER: candidate_id,
                    _REJECTED_PAYLOAD_PLACEHOLDER: _stable_payload_json(payload),
                    _VALIDATION_ERRORS_PLACEHOLDER: _validation_errors_payload(
                        exc.validation_errors
                    ),
                    _EVIDENCE_CATALOG_PLACEHOLDER: catalog_json,
                },
            )
            repair_messages = [
                ChatMessage(role="system", content=repair_system_text),
                ChatMessage(role="user", content=repair_rendered.effective_text),
            ]
            try:
                payload, response_hash = await _complete_and_hash(client, repair_messages)
            except LlmClientError as exc2:
                raise RuleDraftGenerationError(
                    f"fallo del cliente LLM reparando el draft de {candidate_id!r} "
                    f"(intento {repair_attempts_used}): {type(exc2).__name__}"
                ) from exc2
            continue

        return _AcceptedDraft(
            rule_draft=rule_draft,
            response_hash=response_hash,
            writer_user_effective_hash=rendered.effective_hash,
            repair_attempts_used=repair_attempts_used,
        )


async def _generate_all_drafts(
    *,
    candidates: list[tuple[str, ContextPackage]],
    profile: LlmProfile,
    system_text: str,
    user_template_text: str,
    repair_prompts: _LazyStructureRepairPrompts,
    schema_validator: jsonschema.protocols.Validator,
    llm_repair_attempts: int,
    max_context_package_json_chars: int,
    context_records_by_id: dict[str, str],
    temp_dir: Path,
) -> tuple[list[RuleDraftRecord], list[str]]:
    """Genera, en una unica corrida de evento asyncio, el draft final
    (inicial o reparado) de TODOS los candidatos, secuencialmente (sin
    concurrencia). Por cada candidato exitoso escribe el draft UNICAMENTE
    en `temp_dir` (nunca en el directorio canonico) y registra su
    metadata en memoria. Se detiene ante el primer candidato que agote su
    presupuesto de reparacion (o ante cualquier fallo de infraestructura):
    la excepcion propaga y el llamador descarta `temp_dir` completo (nada
    de esta corrida se promueve). Devuelve tambien un resumen legible por
    candidato con reparaciones consumidas (se agrega a `warnings` del
    manifest -- unico lugar donde `rule-draft-manifest.json`, sin ganar
    ningun campo nuevo, deja evidencia de que hubo reparacion)."""
    records: list[RuleDraftRecord] = []
    repair_summaries: list[str] = []
    async with OpenAICompatibleChatClient(profile) as client:
        for candidate_id, package in candidates:
            # Construido UNA vez por candidato, antes de la llamada
            # inicial, y reutilizado sin cambios en todos los intentos de
            # reparacion de ese candidato: la numeracion de alias debe
            # permanecer estable durante todo su ciclo de vida.
            catalog = build_evidence_catalog(package)
            accepted = await _generate_one_draft(
                candidate_id=candidate_id,
                package=package,
                catalog=catalog,
                client=client,
                system_text=system_text,
                user_template_text=user_template_text,
                repair_prompts=repair_prompts,
                schema_validator=schema_validator,
                llm_repair_attempts=llm_repair_attempts,
                max_context_package_json_chars=max_context_package_json_chars,
            )

            filename = _rule_draft_filename(candidate_id)
            (temp_dir / filename).write_text(
                accepted.rule_draft.to_stable_json(), encoding="utf-8"
            )
            records.append(
                RuleDraftRecord(
                    candidate_id=candidate_id,
                    context_hash=context_records_by_id[candidate_id],
                    relative_filename=filename,
                    rule_draft_hash=rule_draft_json_hash(accepted.rule_draft),
                    writer_user_effective_hash=accepted.writer_user_effective_hash,
                    response_hash=accepted.response_hash,
                )
            )
            if accepted.repair_attempts_used > 0:
                repair_summaries.append(
                    f"{candidate_id}: reparado tras {accepted.repair_attempts_used} "
                    "intento(s) estructural(es)"
                )
    return records, sorted(repair_summaries)


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
    repair_prompts = _LazyStructureRepairPrompts(settings)

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
        records, repair_summaries = asyncio.run(
            _generate_all_drafts(
                candidates=candidates,
                profile=profile,
                system_text=system_text,
                user_template_text=user_template_text,
                repair_prompts=repair_prompts,
                schema_validator=schema_validator,
                llm_repair_attempts=settings.llm_repair_attempts,
                max_context_package_json_chars=settings.max_context_package_json_chars,
                context_records_by_id=context_records_by_id,
                temp_dir=temp_dir,
            )
        )
    except BaseException:
        # Cualquier fallo (candidato que agoto la reparacion estructural,
        # o un fallo de infraestructura en cualquier intento) descarta
        # TODO el directorio temporal: la etapa es atomica, nunca hay
        # promocion parcial.
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
        warnings=repair_summaries,
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
    return [f"{manifest.draft_count} draft(s)", *repair_summaries]


def _write_manifest_and_promote(
    artifacts_dir: Path, rule_draft_dir: Path, manifest: RuleDraftDirectoryManifest
) -> None:
    temp_dir = new_temp_directory(artifacts_dir, _DIR_NAME)
    try:
        (temp_dir / _MANIFEST_FILENAME).write_text(manifest.to_stable_json(), encoding="utf-8")
        swap_directory(temp_dir, rule_draft_dir, error_factory=RuleDraftGenerationError)
    finally:
        discard_temp_directory(temp_dir)
