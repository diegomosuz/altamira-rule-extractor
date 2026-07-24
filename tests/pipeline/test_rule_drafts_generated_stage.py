"""Tests de orquestacion de RULE_DRAFTS_GENERATED (Prompt 12): atomicidad
por etapa, fail-fast en la primera respuesta invalida, fast-path de
idempotencia y persistencia de artifacts/08-rule-drafts/.

Cliente LLM inyectado por monkeypatch (mismo patron que Prompt 11): sin
`httpx` real, sin red."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
from altamira_extractor.pipeline.errors import LlmTimeoutError, RuleDraftGenerationError
from altamira_extractor.pipeline.rule_drafts_generated_stage import run_rule_drafts_generated_stage

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


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    system_path, user_path = _write_prompt_files(tmp_path)
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
    # cand-1 exitoso, cand-2 con JSON invalido: la corrida completa falla.
    calls = _install_fake_client(monkeypatch, [_valid_payload(), {"not": "the expected shape"}])
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1"), _package("cand-2")])
    # "not the expected shape" es estructuralmente valido como dict pero
    # le faltan campos requeridos -> falla en el ensamblado.

    with pytest.raises(RuleDraftGenerationError):
        run_rule_drafts_generated_stage(**kwargs)

    assert len(calls) == 2
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
    # regeneracion, y esta vez el modelo devuelve una respuesta invalida.
    kwargs2 = _base_kwargs(tmp_path, packages=[_package("cand-1"), _package("cand-2")])
    kwargs2["rule_draft_dir"] = kwargs["rule_draft_dir"]
    kwargs2["settings"] = kwargs["settings"]
    _install_fake_client(monkeypatch, [_valid_payload(), {"bad": "payload"}])

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
    bad_payload = _valid_payload(evidence_validation_status="EVIDENCE_VALIDATED")
    _install_fake_client(monkeypatch, [bad_payload])
    kwargs = _base_kwargs(tmp_path, packages=[_package("cand-1")])

    with pytest.raises(RuleDraftGenerationError):
        run_rule_drafts_generated_stage(**kwargs)


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
