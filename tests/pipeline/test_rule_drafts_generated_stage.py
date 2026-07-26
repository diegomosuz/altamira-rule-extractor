"""Tests de orquestacion de RULE_DRAFTS_GENERATED (Prompt 12): atomicidad
por etapa, fail-fast en la primera respuesta invalida, fast-path de
idempotencia y persistencia de artifacts/08-rule-drafts/.

Cliente LLM inyectado por monkeypatch (mismo patron que Prompt 11): sin
`httpx` real, sin red."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from altamira_extractor.config import Settings
from altamira_extractor.contracts.context_manifest import (
    ContextDirectoryManifest,
    ContextRecord,
    QueryRecord,
)
from altamira_extractor.contracts.context_package import (
    BatchContext,
    CodeSliceEntry,
    Completeness,
    ContextPackage,
    ContextPackageCandidate,
    ContextPackageDecision,
    ContextPackageOperation,
    ContextPackageScope,
    DataContext,
    Effects,
    EvidenceEntry,
)
from altamira_extractor.contracts.enums import (
    BatchContextStatus,
    CompletenessStatus,
    InclusionReason,
    PipelineStage,
    StageStatus,
)
from altamira_extractor.contracts.rule_draft_manifest import RuleDraftDirectoryManifest
from altamira_extractor.contracts.run_state import StageExecution
from altamira_extractor.pipeline import rule_drafts_generated_stage as stage_module
from altamira_extractor.pipeline.errors import (
    LlmAuthenticationError,
    LlmRateLimitError,
    LlmTimeoutError,
    RuleDraftGenerationError,
)
from altamira_extractor.pipeline.prompt_loader import load_prompt_template, render_prompt
from altamira_extractor.pipeline.rule_draft_assembly import (
    assemble_rule_draft,
    load_rule_draft_schema,
)
from altamira_extractor.pipeline.rule_drafts_generated_stage import run_rule_drafts_generated_stage

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_STRUCTURE_REPAIR_SYSTEM_PATH = REPO_ROOT / "prompts" / "rule_structure_repair_system.md"
REAL_STRUCTURE_REPAIR_USER_PATH = REPO_ROOT / "prompts" / "rule_structure_repair_user.md"
REAL_RULE_DRAFT_SCHEMA_PATH = REPO_ROOT / "schemas" / "rule-draft.schema.json"
_EXAMPLE_JSON_RE = re.compile(r"EJEMPLO_JSON_BEGIN\s*(.*?)\s*EJEMPLO_JSON_END", re.DOTALL)

_HASH_A = "a" * 64
_RUN_ID = "run-1"


def _stage(stage: PipelineStage, status: StageStatus) -> StageExecution:
    now = datetime.now(UTC)
    kwargs: dict[str, Any] = {"stage": stage, "status": status}
    if status in (StageStatus.SUCCEEDED, StageStatus.FAILED):
        kwargs["started_at"] = now
        kwargs["finished_at"] = now
    if status == StageStatus.FAILED:
        kwargs["error"] = "fallo simulado"
    return StageExecution(**kwargs)


def _succeeded_stages() -> list[StageExecution]:
    return [_stage(PipelineStage.CONTEXTS_BUILT, StageStatus.SUCCEEDED)]


def _package(candidate_id: str, decision_id: str = "dec-1") -> ContextPackage:
    evidence = EvidenceEntry(
        evidence_id="ev-1",
        kind="decision",
        source_file="cobol/PROG1.cbl",
        line_start=10,
        line_end=10,
        source_package_hash=_HASH_A,
    )
    return ContextPackage(
        schema_version="2.0",
        candidate=ContextPackageCandidate(
            candidate_id=candidate_id,
            decision_id=decision_id,
            detector_id="det",
            detector_version="1.0",
            detector_score=1.0,
        ),
        scope=ContextPackageScope(
            country="AR",
            application="Transferencias",
            operation=ContextPackageOperation(logical_name="OP1", description=None),
            program="PROG1",
            program_version="1",
            paragraph="MAIN",
            source_file="cobol/PROG1.cbl",
            line_start=10,
            line_end=10,
            source_package_hash=_HASH_A,
        ),
        code_slice=[
            CodeSliceEntry(
                paragraph_id="p1",
                paragraph="MAIN",
                source_file="cobol/PROG1.cbl",
                source_text="IF WS-COD = 'R001'",
                line_start=10,
                line_end=10,
                inclusion_reason=InclusionReason.CANDIDATE,
                evidence_ids=["ev-1"],
            )
        ],
        data_context=DataContext(parameter_tables=[], transactional_tables_read=[]),
        decision=ContextPackageDecision(
            expression="WS-COD = 'R001'",
            normalized_expression="WS-COD = 'R001'",
            operands=[],
            rule_type=None,
            outcome_code="R001",
            evidence_ids=["ev-1"],
        ),
        effects=Effects(return_codes=[], table_effects=[]),
        batch_context=BatchContext(status=BatchContextStatus.NOT_AVAILABLE, downstream_jobs=[]),
        domain_glossary=[],
        evidence=[evidence],
        completeness=Completeness(
            D1=CompletenessStatus.COMPLETE,
            D2=CompletenessStatus.COMPLETE,
            D3=CompletenessStatus.NOT_AVAILABLE,
            D4=CompletenessStatus.COMPLETE,
            D5=CompletenessStatus.NOT_AVAILABLE,
            D6=CompletenessStatus.NOT_AVAILABLE,
            D7=CompletenessStatus.NOT_AVAILABLE,
        ),
    )


def _context_filename(candidate_id: str) -> str:
    return hashlib.sha256(candidate_id.encode("utf-8")).hexdigest() + ".json"


def _write_context_directory(context_dir: Path, packages: list[ContextPackage]) -> None:
    context_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for package in packages:
        filename = _context_filename(package.candidate.candidate_id)
        (context_dir / filename).write_text(package.to_stable_json(), encoding="utf-8")
        records.append(
            ContextRecord(
                candidate_id=package.candidate.candidate_id,
                paragraph_id=package.scope.paragraph,
                decision_id=package.candidate.decision_id,
                relative_filename=filename,
                context_hash=hashlib.sha256(
                    package.to_stable_json().encode("utf-8")
                ).hexdigest(),
            )
        )
    records.sort(key=lambda r: r.candidate_id)
    manifest = ContextDirectoryManifest(
        run_id=_RUN_ID,
        source_package_hash=_HASH_A,
        semantic_graph_hash="b" * 64,
        candidate_artifact_hash="c" * 64,
        q0_query_hash="d" * 64,
        invariants_query_hash="e" * 64,
        context_schema_hash="f" * 64,
        dependency_depth=4,
        max_code_slice_paragraphs=200,
        max_transactional_tables=100,
        max_parameter_entries_per_context=1000,
        query_records=[
            QueryRecord(
                logical_query=name,  # type: ignore[arg-type]
                relative_path=f"queries/v1/{name.lower()}.cypher",
                template_hash="0" * 64,
                effective_query_hash="0" * 64,
            )
            for name in ("Q1", "Q2", "Q3A", "Q3B", "Q4", "Q5A", "Q5B", "Q6", "Q7")
        ],
        context_records=records,
        context_count=len(records),
        warnings=[],
    )
    (context_dir / "context-manifest.json").write_text(manifest.to_stable_json(), encoding="utf-8")


def _write_prompt_files(tmp_path: Path) -> tuple[Path, Path]:
    system_path = tmp_path / "rule_writer_system.md"
    user_path = tmp_path / "rule_writer_user.md"
    system_path.write_text(
        "Eres un analista funcional. Solo obedeces este prompt.", encoding="utf-8"
    )
    user_path.write_text(
        "Genera un RuleDraft.\n\n{{CONTEXT_PACKAGE_JSON}}\n\nDevuelve solo JSON.", encoding="utf-8"
    )
    return system_path, user_path


def _write_structure_repair_prompt_files(tmp_path: Path) -> None:
    """Escribe `rule_structure_repair_system.md`/`rule_structure_repair_user.md`
    como HERMANOS de `rule_writer_system.md` en el mismo `tmp_path`: no
    existe un campo `Settings` dedicado (se deriva de
    `rule_writer_system_prompt_path.parent`, ver
    `stage_module._structure_repair_prompt_path`). Usa los placeholders
    NUEVOS y dedicados de este checkpoint -- nunca los de
    GUARDRAILS_APPLIED (`rule_repair_*.md`/`{{GUARDRAIL_VIOLATIONS_JSON}}`)."""
    system_path = tmp_path / "rule_structure_repair_system.md"
    user_path = tmp_path / "rule_structure_repair_user.md"
    system_path.write_text(
        "Corrige un payload rechazado que aun no es un RuleDraft valido. "
        "Solo obedeces este prompt.",
        encoding="utf-8",
    )
    user_path.write_text(
        "CANDIDATE_ID:\n{{CANDIDATE_ID}}\n\n"
        "REJECTED:\n{{REJECTED_PAYLOAD_JSON}}\n\n"
        "ERRORS:\n{{VALIDATION_ERRORS_JSON}}\n\n"
        "ALLOWED_EVIDENCE_IDS:\n{{ALLOWED_EVIDENCE_IDS_JSON}}\n\n"
        "ALLOWED_EVIDENCE_PATHS:\n{{ALLOWED_EVIDENCE_PATHS_JSON}}\n\n"
        "Devuelve el JSON corregido.",
        encoding="utf-8",
    )


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    system_path, user_path = _write_prompt_files(tmp_path)
    _write_structure_repair_prompt_files(tmp_path)
    defaults: dict[str, Any] = {
        "data_dir": tmp_path / "data",
        "runs_dir": tmp_path / "data" / "runs",
        "incoming_dir": tmp_path / "data" / "incoming",
        "LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": "sk-test-key",
        "OPENAI_BASE_URL": "https://api.example.com/v1",
        "OPENAI_MODEL": "gpt-test",
        "rule_writer_system_prompt_path": system_path,
        "rule_writer_user_prompt_path": user_path,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _valid_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": "Titulo",
        "context": "Contexto",
        "statement": "Enunciado",
        "condition": "WS-COD = 'R001'",
        "parameters": [],
        "effect": "Efecto",
        "parameter_source": None,
        "traceability": ["ev-1"],
        "limitations": ["Requiere revision funcional"],
        "claims": [
            {
                "claim_id": "c1",
                "field": "condition",
                "evidence_paths": ["$.decision.expression"],
                "evidence_ids": ["ev-1"],
            }
        ],
    }
    payload.update(overrides)
    return payload


def _install_fake_client(
    monkeypatch: pytest.MonkeyPatch, responses: list[dict[str, Any] | BaseException]
) -> list[list[Any]]:
    calls: list[list[Any]] = []
    queue = list(responses)

    class _FakeClient:
        def __init__(self, profile: Any, **kwargs: Any) -> None:
            self.profile = profile

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        async def complete(self, messages: list[Any]) -> dict[str, Any]:
            calls.append(messages)
            if not queue:
                raise AssertionError("el fake client se llamo mas veces de lo esperado")
            item = queue.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item

    monkeypatch.setattr(stage_module, "OpenAICompatibleChatClient", _FakeClient)
    return calls


class _PoisonClient:
    def __init__(self, profile: Any, **kwargs: Any) -> None:
        raise AssertionError("no deberia construirse ningun cliente LLM")


def _base_kwargs(tmp_path: Path, *, packages: list[ContextPackage]) -> dict[str, Any]:
    context_dir = tmp_path / "07-context"
    _write_context_directory(context_dir, packages)
    return {
        "run_id": _RUN_ID,
        "source_package_hash": _HASH_A,
        "run_stages": _succeeded_stages(),
        "context_dir": context_dir,
        "rule_draft_dir": tmp_path / "08-rule-drafts",
        "settings": _settings(tmp_path),
    }


# --- precondicion / drift ---


def test_missing_contexts_built_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(stage_module, "OpenAICompatibleChatClient", _PoisonClient)
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1")])
    kwargs["run_stages"] = []
    with pytest.raises(RuleDraftGenerationError):
        run_rule_drafts_generated_stage(**kwargs)


def test_contexts_built_not_succeeded_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(stage_module, "OpenAICompatibleChatClient", _PoisonClient)
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1")])
    kwargs["run_stages"] = [_stage(PipelineStage.CONTEXTS_BUILT, StageStatus.FAILED)]
    with pytest.raises(RuleDraftGenerationError):
        run_rule_drafts_generated_stage(**kwargs)


def test_context_hash_drift_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(stage_module, "OpenAICompatibleChatClient", _PoisonClient)
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1")])
    filename = _context_filename("cand-1")
    tampered = _package("cand-1").model_copy(
        update={"decision": _package("cand-1").decision.model_copy(update={"outcome_code": "R999"})}
    )
    (kwargs["context_dir"] / filename).write_text(tampered.to_stable_json(), encoding="utf-8")
    with pytest.raises(RuleDraftGenerationError):
        run_rule_drafts_generated_stage(**kwargs)


# --- flujo feliz ---


def test_happy_path_single_candidate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = _install_fake_client(monkeypatch, [_valid_payload()])
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1")])

    warnings = run_rule_drafts_generated_stage(**kwargs)

    assert warnings == ["1 draft(s)"]
    assert len(calls) == 1
    manifest = RuleDraftDirectoryManifest.model_validate_json(
        (kwargs["rule_draft_dir"] / "rule-draft-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest.draft_count == 1
    record = manifest.records[0]
    assert record.candidate_id == "cand-1"
    assert (kwargs["rule_draft_dir"] / record.relative_filename).is_file()


def test_empty_candidates_no_llm_calls(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(stage_module, "OpenAICompatibleChatClient", _PoisonClient)
    kwargs = _base_kwargs(tmp_path, packages=[])

    warnings = run_rule_drafts_generated_stage(**kwargs)

    assert warnings == ["0 draft(s)"]
    manifest = RuleDraftDirectoryManifest.model_validate_json(
        (kwargs["rule_draft_dir"] / "rule-draft-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest.draft_count == 0


# --- atomicidad: todo o nada ---


def test_structural_failure_aborts_whole_stage_no_partial_promotion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # cand-1 exitoso (1 llamada). cand-2 invalido en la respuesta inicial
    # Y en sus 2 intentos de reparacion (llm_repair_attempts=2 por
    # defecto): agota el presupuesto y la corrida completa falla. Nunca
    # se promueve cand-1 de forma parcial (test 15 del checkpoint).
    invalid = {"not": "the expected shape"}
    calls = _install_fake_client(monkeypatch, [_valid_payload(), invalid, invalid, invalid])
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1"), _package("cand-2")])

    with pytest.raises(RuleDraftGenerationError):
        run_rule_drafts_generated_stage(**kwargs)

    assert len(calls) == 4
    assert not kwargs["rule_draft_dir"].exists()
    # ningun temporal huerfano
    assert list(tmp_path.glob("08-rule-drafts.tmp-*")) == []


def test_previous_valid_output_preserved_when_new_run_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1")])
    _install_fake_client(monkeypatch, [_valid_payload()])
    run_rule_drafts_generated_stage(**kwargs)
    first_manifest_bytes = (kwargs["rule_draft_dir"] / "rule-draft-manifest.json").read_bytes()

    # segunda corrida: cambia el contexto (nuevo candidato) para forzar
    # regeneracion, y esta vez el modelo devuelve una respuesta invalida
    # incluso tras agotar los 2 intentos de reparacion por defecto.
    kwargs2 = _base_kwargs(tmp_path, packages=[_package("cand-1"), _package("cand-2")])
    kwargs2["rule_draft_dir"] = kwargs["rule_draft_dir"]
    kwargs2["settings"] = kwargs["settings"]
    bad = {"bad": "payload"}
    _install_fake_client(monkeypatch, [_valid_payload(), bad, bad, bad])

    with pytest.raises(RuleDraftGenerationError):
        run_rule_drafts_generated_stage(**kwargs2)

    manifest_path = kwargs["rule_draft_dir"] / "rule-draft-manifest.json"
    assert manifest_path.read_bytes() == first_manifest_bytes


def test_llm_client_error_aborts_stage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_fake_client(monkeypatch, [LlmTimeoutError("boom")])
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1")])

    with pytest.raises(RuleDraftGenerationError):
        run_rule_drafts_generated_stage(**kwargs)

    assert not kwargs["rule_draft_dir"].exists()


def test_model_cannot_self_assign_forbidden_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # El modelo insiste en autoasignarse evidence_validation_status en
    # TODOS sus intentos (inicial + 2 reparaciones): sigue rechazandose
    # siempre, la reparacion nunca lo "perdona" (test 13 del checkpoint).
    bad_payload = _valid_payload(evidence_validation_status="EVIDENCE_VALIDATED")
    calls = _install_fake_client(monkeypatch, [bad_payload, bad_payload, bad_payload])
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1")])

    with pytest.raises(RuleDraftGenerationError) as excinfo:
        run_rule_drafts_generated_stage(**kwargs)

    assert len(calls) == 3
    assert "evidence_validation_status" in str(excinfo.value)
    assert not kwargs["rule_draft_dir"].exists()


def test_context_package_size_limit_is_enforced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fake_client(monkeypatch, [_valid_payload()])
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1")])
    kwargs["settings"] = _settings(tmp_path, max_context_package_json_chars=1)

    with pytest.raises(RuleDraftGenerationError):
        run_rule_drafts_generated_stage(**kwargs)


# --- fast-path de idempotencia ---


def test_second_run_with_no_changes_skips_llm_entirely(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1")])
    _install_fake_client(monkeypatch, [_valid_payload()])
    first_warnings = run_rule_drafts_generated_stage(**kwargs)
    assert first_warnings == ["1 draft(s)"]

    monkeypatch.setattr(stage_module, "OpenAICompatibleChatClient", _PoisonClient)
    second_warnings = run_rule_drafts_generated_stage(**kwargs)

    assert second_warnings == ["1 draft(s) (sin cambios)"]


def test_fast_path_rejects_extra_unreferenced_file_and_regenerates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1")])
    _install_fake_client(monkeypatch, [_valid_payload()])
    run_rule_drafts_generated_stage(**kwargs)

    # Un archivo .json extra, no referenciado por rule-draft-manifest.json,
    # invalida el fast-path (defensa en profundidad: el directorio
    # canonico debe contener EXACTAMENTE los archivos que el manifest
    # declara, ni mas ni menos).
    (kwargs["rule_draft_dir"] / "extra-not-in-manifest.json").write_text("{}", encoding="utf-8")

    _install_fake_client(monkeypatch, [_valid_payload()])
    warnings = run_rule_drafts_generated_stage(**kwargs)

    assert warnings == ["1 draft(s)"]
    # La regeneracion promueve un directorio nuevo por swap atomico: el
    # archivo huerfano no sobrevive.
    assert not (kwargs["rule_draft_dir"] / "extra-not-in-manifest.json").exists()


def test_fast_path_does_not_require_previous_runstate_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1")])
    _install_fake_client(monkeypatch, [_valid_payload()])
    run_rule_drafts_generated_stage(**kwargs)

    # Aunque run_stages ya no refleje ningun exito previo de
    # RULE_DRAFTS_GENERATED (solo el precondicion CONTEXTS_BUILT), el
    # fast-path debe igual reutilizar la salida valida existente en disco.
    monkeypatch.setattr(stage_module, "OpenAICompatibleChatClient", _PoisonClient)
    warnings = run_rule_drafts_generated_stage(**kwargs)
    assert warnings == ["1 draft(s) (sin cambios)"]


# --- checkpoint correctivo: ciclo de reparacion estructural ---


def _manifest_of(rule_draft_dir: Path) -> RuleDraftDirectoryManifest:
    return RuleDraftDirectoryManifest.model_validate_json(
        (rule_draft_dir / "rule-draft-manifest.json").read_text(encoding="utf-8")
    )


def test_valid_initial_response_has_zero_repairs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _install_fake_client(monkeypatch, [_valid_payload()])
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1")])

    warnings = run_rule_drafts_generated_stage(**kwargs)

    assert len(calls) == 1
    assert warnings == ["1 draft(s)"]
    manifest = _manifest_of(kwargs["rule_draft_dir"])
    assert manifest.warnings == []


def test_missing_field_initial_first_repair_valid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing_field_payload = _valid_payload()
    del missing_field_payload["title"]
    calls = _install_fake_client(monkeypatch, [missing_field_payload, _valid_payload()])
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1")])

    warnings = run_rule_drafts_generated_stage(**kwargs)

    assert len(calls) == 2
    assert warnings == ["1 draft(s)", "cand-1: reparado tras 1 intento(s) estructural(es)"]
    manifest = _manifest_of(kwargs["rule_draft_dir"])
    assert manifest.warnings == ["cand-1: reparado tras 1 intento(s) estructural(es)"]

    # el segundo mensaje enviado al modelo (la reparacion) debe incluir
    # el payload rechazado y los errores estructurados, sin inventar
    # nada mas alla de eso.
    repair_user_message = calls[1][1].content
    assert "title" in repair_user_message


def test_extra_field_initial_first_repair_valid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    extra_field_payload = _valid_payload(unexpected_field="nope")
    calls = _install_fake_client(monkeypatch, [extra_field_payload, _valid_payload()])
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1")])

    warnings = run_rule_drafts_generated_stage(**kwargs)

    assert len(calls) == 2
    assert warnings == ["1 draft(s)", "cand-1: reparado tras 1 intento(s) estructural(es)"]


def test_wrong_type_initial_second_repair_valid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wrong_type_payload = _valid_payload(parameters="not-a-list")
    still_missing_payload = _valid_payload()
    del still_missing_payload["condition"]
    calls = _install_fake_client(
        monkeypatch, [wrong_type_payload, still_missing_payload, _valid_payload()]
    )
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1")])

    warnings = run_rule_drafts_generated_stage(**kwargs)

    assert len(calls) == 3
    assert warnings == ["1 draft(s)", "cand-1: reparado tras 2 intento(s) estructural(es)"]


def test_all_repair_attempts_invalid_fails_after_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    always_invalid = {"not": "the expected shape"}
    # 1 inicial + 2 reparaciones (llm_repair_attempts=2 por defecto) = 3.
    calls = _install_fake_client(
        monkeypatch, [always_invalid, always_invalid, always_invalid]
    )
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1")])

    with pytest.raises(RuleDraftGenerationError) as excinfo:
        run_rule_drafts_generated_stage(**kwargs)

    assert len(calls) == 3
    assert "cand-1" in str(excinfo.value)
    assert "2 intento(s) de reparacion" in str(excinfo.value)
    assert not kwargs["rule_draft_dir"].exists()


def test_final_error_exposes_sanitized_loc_type_msg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing_and_extra = _valid_payload(unexpected_field="nope")
    del missing_and_extra["title"]
    _install_fake_client(
        monkeypatch, [missing_and_extra, missing_and_extra, missing_and_extra]
    )
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1")])

    with pytest.raises(RuleDraftGenerationError) as excinfo:
        run_rule_drafts_generated_stage(**kwargs)

    issues = excinfo.value.validation_errors
    assert len(issues) >= 1
    locs = {issue.loc for issue in issues}
    types = {issue.type for issue in issues}
    assert "title" in locs
    assert "unexpected_field" in locs
    assert "missing" in types
    assert "extra_forbidden" in types
    # cada issue tiene su propio msg no vacio (nunca el payload completo).
    assert all(issue.msg for issue in issues)


def test_full_payload_never_appears_in_final_error_or_logs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("DEBUG")
    # El propio VALOR invalido es el marcador secreto (tipo incorrecto:
    # string en vez de lista) -- Pydantic expone ese valor crudo unicamente
    # via `input` (nunca copiado a ValidationIssue), asi que ni siquiera el
    # campo que realmente fallo deberia filtrar su contenido.
    secret_marker = "SECRET_PAYLOAD_MARKER_9f8e7d6c"
    bad_payload = _valid_payload(parameters=secret_marker)
    _install_fake_client(monkeypatch, [bad_payload, bad_payload, bad_payload])
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1")])

    with pytest.raises(RuleDraftGenerationError) as excinfo:
        run_rule_drafts_generated_stage(**kwargs)

    assert secret_marker not in str(excinfo.value)
    assert secret_marker not in caplog.text
    for issue in excinfo.value.validation_errors:
        assert secret_marker not in issue.msg
        assert secret_marker not in issue.loc


def test_api_key_never_appears_in_final_error_or_logs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("DEBUG")
    api_key = "sk-super-secret-test-key-should-never-leak"
    always_invalid = {"not": "the expected shape"}
    _install_fake_client(monkeypatch, [always_invalid, always_invalid, always_invalid])
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1")])
    kwargs["settings"] = _settings(tmp_path, OPENAI_API_KEY=api_key)

    with pytest.raises(RuleDraftGenerationError) as excinfo:
        run_rule_drafts_generated_stage(**kwargs)

    assert api_key not in str(excinfo.value)
    assert api_key not in caplog.text


def test_authentication_error_never_triggers_repair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _install_fake_client(monkeypatch, [LlmAuthenticationError("401")])
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1")])

    with pytest.raises(RuleDraftGenerationError):
        run_rule_drafts_generated_stage(**kwargs)

    assert len(calls) == 1
    assert not kwargs["rule_draft_dir"].exists()


def test_rate_limit_error_never_triggers_repair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _install_fake_client(monkeypatch, [LlmRateLimitError("429")])
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1")])

    with pytest.raises(RuleDraftGenerationError):
        run_rule_drafts_generated_stage(**kwargs)

    assert len(calls) == 1
    assert not kwargs["rule_draft_dir"].exists()


def test_timeout_error_never_triggers_repair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _install_fake_client(monkeypatch, [LlmTimeoutError("timeout")])
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1")])

    with pytest.raises(RuleDraftGenerationError):
        run_rule_drafts_generated_stage(**kwargs)

    assert len(calls) == 1
    assert not kwargs["rule_draft_dir"].exists()


def test_llm_client_error_during_repair_never_triggers_further_repair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # La respuesta INICIAL es estructuralmente invalida (dispara el
    # primer intento de reparacion); esa llamada de reparacion falla con
    # un 429 real -- debe abortar de inmediato, sin consumir un segundo
    # intento de reparacion ni tratar el 429 como otra respuesta invalida.
    always_invalid = {"not": "the expected shape"}
    calls = _install_fake_client(
        monkeypatch, [always_invalid, LlmRateLimitError("429 durante reparacion")]
    )
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1")])

    with pytest.raises(RuleDraftGenerationError):
        run_rule_drafts_generated_stage(**kwargs)

    assert len(calls) == 2
    assert not kwargs["rule_draft_dir"].exists()


def _payload_with_claim(**claim_overrides: Any) -> dict[str, Any]:
    claim: dict[str, Any] = {
        "claim_id": "c1",
        "field": "condition",
        "evidence_paths": ["$.decision.expression"],
        "evidence_ids": ["ev-1"],
    }
    claim.update(claim_overrides)
    return _valid_payload(claims=[claim])


def test_invented_evidence_id_initiates_repair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """checkpoint correctivo: `check_evidence_references` corre DESPUES de
    `assemble_rule_draft` y ANTES de aceptar el draft -- un evidence_id
    que no existe en `ContextPackage.evidence` es tratado como error
    reparable de contenido estructurado (mismo `ValidationIssue`, mismo
    ciclo), nunca delegado exclusivamente a GUARDRAILS_APPLIED."""
    invented = _payload_with_claim(evidence_ids=["ev-does-not-exist"])
    calls = _install_fake_client(monkeypatch, [invented, _payload_with_claim()])
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1")])

    warnings = run_rule_drafts_generated_stage(**kwargs)

    assert len(calls) == 2
    assert warnings == ["1 draft(s)", "cand-1: reparado tras 1 intento(s) estructural(es)"]


def test_invented_evidence_id_never_promoted_when_repair_exhausted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    invented = _payload_with_claim(evidence_ids=["ev-does-not-exist"])
    calls = _install_fake_client(monkeypatch, [invented, invented, invented])
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1")])

    with pytest.raises(RuleDraftGenerationError) as excinfo:
        run_rule_drafts_generated_stage(**kwargs)

    assert len(calls) == 3
    issues = excinfo.value.validation_errors
    assert any(issue.type == "unknown_evidence_id" for issue in issues)
    assert not kwargs["rule_draft_dir"].exists()


def test_invented_evidence_path_initiates_repair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    invented = _payload_with_claim(evidence_paths=["$.decision.does_not_exist"])
    calls = _install_fake_client(monkeypatch, [invented, _payload_with_claim()])
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1")])

    warnings = run_rule_drafts_generated_stage(**kwargs)

    assert len(calls) == 2
    assert warnings == ["1 draft(s)", "cand-1: reparado tras 1 intento(s) estructural(es)"]


def test_invented_evidence_path_never_promoted_when_repair_exhausted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    invented = _payload_with_claim(evidence_paths=["$.decision.does_not_exist"])
    calls = _install_fake_client(monkeypatch, [invented, invented, invented])
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1")])

    with pytest.raises(RuleDraftGenerationError) as excinfo:
        run_rule_drafts_generated_stage(**kwargs)

    assert len(calls) == 3
    issues = excinfo.value.validation_errors
    assert any(issue.type == "unknown_evidence_path" for issue in issues)
    assert not kwargs["rule_draft_dir"].exists()


def test_evidence_path_prefix_or_partial_match_is_not_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # "$.decision" (sin ".expression") es un prefijo real del path
    # correcto, pero NUNCA se acepta como coincidencia parcial: debe
    # tratarse como invalido igual que un path inventado.
    prefix_only = _payload_with_claim(evidence_paths=["$.decisio"])
    calls = _install_fake_client(monkeypatch, [prefix_only, _payload_with_claim()])
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1")])

    warnings = run_rule_drafts_generated_stage(**kwargs)

    assert len(calls) == 2
    assert warnings == ["1 draft(s)", "cand-1: reparado tras 1 intento(s) estructural(es)"]


def test_repair_prompt_never_receives_full_context_package(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Marcador exclusivo del ContextPackage (nombre de programa real de
    # _package()): el prompt de reparacion estructural nunca debe verlo,
    # ya que no recibe el ContextPackage completo (solo candidate_id,
    # errores, payload rechazado y listas de evidencia permitidas).
    missing_field_payload = _valid_payload()
    del missing_field_payload["title"]
    calls = _install_fake_client(monkeypatch, [missing_field_payload, _valid_payload()])
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1")])

    run_rule_drafts_generated_stage(**kwargs)

    repair_user_message = calls[1][1].content
    assert "Transferencias" not in repair_user_message
    assert "PROG1" not in repair_user_message
    assert "cand-1" in repair_user_message


def test_no_partial_artifacts_remain_after_exhausted_repair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    always_invalid = {"not": "the expected shape"}
    _install_fake_client(monkeypatch, [always_invalid, always_invalid, always_invalid])
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1")])

    with pytest.raises(RuleDraftGenerationError):
        run_rule_drafts_generated_stage(**kwargs)

    assert not kwargs["rule_draft_dir"].exists()
    assert list(tmp_path.glob("08-rule-drafts.tmp-*")) == []
    # ningun archivo huerfano del intento fallido, mas alla de la entrada
    # legitima de 07-context/ (precondicion, no generada por esta etapa).
    assert list(tmp_path.glob("08-rule-drafts*")) == []


def test_three_candidates_second_fails_first_never_partially_promoted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    always_invalid = {"not": "the expected shape"}
    calls = _install_fake_client(
        monkeypatch,
        [
            _valid_payload(),  # cand-1: valido de entrada
            always_invalid,  # cand-2: inicial invalido
            always_invalid,  # cand-2: reparacion 1 invalida
            always_invalid,  # cand-2: reparacion 2 invalida -> agota limite
            # cand-3 nunca deberia llamarse: la corrida ya fallo en cand-2.
        ],
    )
    kwargs = _base_kwargs(
        tmp_path, packages=[_package("cand-1"), _package("cand-2"), _package("cand-3")]
    )

    with pytest.raises(RuleDraftGenerationError):
        run_rule_drafts_generated_stage(**kwargs)

    assert len(calls) == 4
    assert not kwargs["rule_draft_dir"].exists()


def test_manifest_records_real_repair_attempts_for_multiple_candidates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing_field_payload = _valid_payload()
    del missing_field_payload["title"]
    calls = _install_fake_client(
        monkeypatch,
        [
            _valid_payload(),  # cand-1: sin reparacion
            missing_field_payload,  # cand-2: inicial invalido
            _valid_payload(),  # cand-2: reparacion 1 valida
        ],
    )
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1"), _package("cand-2")])

    warnings = run_rule_drafts_generated_stage(**kwargs)

    assert len(calls) == 3
    manifest = _manifest_of(kwargs["rule_draft_dir"])
    assert manifest.warnings == ["cand-2: reparado tras 1 intento(s) estructural(es)"]
    assert warnings == ["2 draft(s)", "cand-2: reparado tras 1 intento(s) estructural(es)"]


def test_response_hash_matches_the_finally_accepted_response(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing_field_payload = _valid_payload()
    del missing_field_payload["title"]
    accepted_payload = _valid_payload(title="Titulo final aceptado")
    _install_fake_client(monkeypatch, [missing_field_payload, accepted_payload])
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1")])

    run_rule_drafts_generated_stage(**kwargs)

    manifest = _manifest_of(kwargs["rule_draft_dir"])
    record = manifest.records[0]
    expected_hash = hashlib.sha256(
        json.dumps(
            accepted_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    assert record.response_hash == expected_hash


# --- prompts estructurales reales (checkpoint correctivo) ---


def test_real_structure_repair_prompts_render_without_error() -> None:
    """Carga y renderiza los archivos REALES del repositorio (nunca un
    fixture ficticio): confirma que ambos placeholders declarados existen
    exactamente una vez y que la sustitucion completa (los 5 placeholders
    del prompt de usuario) no levanta `PromptTemplateError`."""
    system = load_prompt_template(
        REAL_STRUCTURE_REPAIR_SYSTEM_PATH,
        relative_path="prompts/rule_structure_repair_system.md",
        expected_placeholder_counts={},
    )
    user = load_prompt_template(
        REAL_STRUCTURE_REPAIR_USER_PATH,
        relative_path="prompts/rule_structure_repair_user.md",
        expected_placeholder_counts={
            "{{CANDIDATE_ID}}": 1,
            "{{REJECTED_PAYLOAD_JSON}}": 1,
            "{{VALIDATION_ERRORS_JSON}}": 1,
            "{{ALLOWED_EVIDENCE_IDS_JSON}}": 1,
            "{{ALLOWED_EVIDENCE_PATHS_JSON}}": 1,
        },
    )
    assert system.template_text
    rendered = render_prompt(
        user.template_text,
        {
            "{{CANDIDATE_ID}}": "candidate::demo",
            "{{REJECTED_PAYLOAD_JSON}}": "{}",
            "{{VALIDATION_ERRORS_JSON}}": "{}",
            "{{ALLOWED_EVIDENCE_IDS_JSON}}": "[]",
            "{{ALLOWED_EVIDENCE_PATHS_JSON}}": "[]",
        },
    )
    assert "{{" not in rendered.effective_text


def test_real_structure_repair_system_prompt_example_validates_against_contract() -> None:
    """Extrae el ejemplo JSON literal embebido entre EJEMPLO_JSON_BEGIN/
    END en el prompt REAL y lo valida contra `assemble_rule_draft` (el
    mismo camino que usa la etapa real). Si el contrato de RuleDraft
    cambia y el ejemplo del prompt queda desactualizado, este test falla
    -- nunca se asume manualmente que siguen sincronizados."""
    text = REAL_STRUCTURE_REPAIR_SYSTEM_PATH.read_text(encoding="utf-8")
    match = _EXAMPLE_JSON_RE.search(text)
    assert match is not None, "el prompt debe contener EJEMPLO_JSON_BEGIN/END"
    example = json.loads(match.group(1))

    schema, _hash = load_rule_draft_schema(REAL_RULE_DRAFT_SCHEMA_PATH)
    validator_cls = jsonschema.validators.validator_for(schema)
    validator = validator_cls(schema)

    rule_draft = assemble_rule_draft(example, schema_validator=validator)
    assert rule_draft.schema_version == "2.0"


def test_structure_repair_prompt_mentions_every_current_functional_field() -> None:
    """Proteccion minima contra drift de la prosa (no solo del ejemplo):
    cada uno de los 10 campos funcionales actuales de RuleDraft debe
    aparecer mencionado literalmente en el prompt real. No reemplaza el
    test anterior (que valida el EJEMPLO contra el contrato real); este
    cubre la DESCRIPCION en prosa."""
    from altamira_extractor.contracts.rule_draft import RuleDraft

    governed = {"schema_version", "evidence_validation_status", "functional_review_status"}
    functional_fields = set(RuleDraft.model_fields) - governed

    text = REAL_STRUCTURE_REPAIR_SYSTEM_PATH.read_text(encoding="utf-8")
    for field_name in functional_fields:
        assert field_name in text, f"campo funcional {field_name!r} no mencionado en el prompt"
