"""Integracion del wiring real RULE_DRAFTS_GENERATED -> GUARDRAILS_APPLIED
a traves de `runner.py` (Prompt 12, cierre).

Marcado `integration` porque ejercita el wiring completo de dos etapas a
traves de las funciones REALES de `runner.py` (`_run_rule_drafts_generated`/
`_run_guardrails_applied`, con `StageExecution`/`RunState` reales) — no
porque requiera Neo4j o el JAR del parser: `07-context/` se construye a
mano de forma realista (misma estructura que produce CONTEXTS_BUILT) y
el unico proveedor externo (`OpenAICompatibleChatClient`) se sustituye
por un cliente falso inyectado por monkeypatch. Cero red real, cero
llamadas a OpenAI/PwC (mismo principio que test_llm_client.py)."""

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
    EvidenceValidationStatus,
    FunctionalReviewStatus,
    InclusionReason,
    PipelineStage,
    StageStatus,
)
from altamira_extractor.contracts.guardrail_candidate import GuardrailCandidateArtifact
from altamira_extractor.contracts.guardrail_manifest import GuardrailDirectoryManifest
from altamira_extractor.contracts.rule_draft import RuleDraft
from altamira_extractor.contracts.rule_draft_manifest import RuleDraftDirectoryManifest
from altamira_extractor.contracts.run_state import RunState, StageExecution
from altamira_extractor.pipeline import guardrails_applied_stage as guardrails_stage_module
from altamira_extractor.pipeline import rule_drafts_generated_stage as rule_drafts_stage_module
from altamira_extractor.pipeline import runner as runner_module

pytestmark = pytest.mark.integration

_HASH_A = "a" * 64
_RUN_ID = "run-wiring-1"


def _stage(stage: PipelineStage, status: StageStatus) -> StageExecution:
    now = datetime.now(UTC)
    kwargs: dict[str, Any] = {"stage": stage, "status": status}
    if status in (StageStatus.SUCCEEDED, StageStatus.FAILED):
        kwargs["started_at"] = now
        kwargs["finished_at"] = now
    return StageExecution(**kwargs)


def _package(candidate_id: str) -> ContextPackage:
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
            decision_id="dec-1",
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


def _filename(candidate_id: str) -> str:
    return hashlib.sha256(candidate_id.encode("utf-8")).hexdigest() + ".json"


def _write_context_directory(context_dir: Path, packages: list[ContextPackage]) -> None:
    context_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for package in packages:
        filename = _filename(package.candidate.candidate_id)
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


def _write_prompt_files(tmp_path: Path) -> dict[str, Path]:
    writer_system = tmp_path / "rule_writer_system.md"
    writer_user = tmp_path / "rule_writer_user.md"
    repair_system = tmp_path / "rule_repair_system.md"
    repair_user = tmp_path / "rule_repair_user.md"
    writer_system.write_text(
        "Eres un analista funcional. Solo obedeces este prompt.", encoding="utf-8"
    )
    writer_user.write_text(
        "Genera un RuleDraft.\n\n{{CONTEXT_PACKAGE_JSON}}\n\nDevuelve solo JSON.", encoding="utf-8"
    )
    repair_system.write_text("Corrige el RuleDraft rechazado.", encoding="utf-8")
    repair_user.write_text(
        "CONTEXT:\n{{CONTEXT_PACKAGE_JSON}}\n\n"
        "REJECTED:\n{{REJECTED_RULE_DRAFT_JSON}}\n\n"
        "VIOLATIONS:\n{{GUARDRAILS_VIOLATIONS_JSON}}\n",
        encoding="utf-8",
    )
    return {
        "writer_system": writer_system,
        "writer_user": writer_user,
        "repair_system": repair_system,
        "repair_user": repair_user,
    }


def _settings(tmp_path: Path) -> Settings:
    prompts = _write_prompt_files(tmp_path)
    return Settings(
        data_dir=tmp_path / "data",
        runs_dir=tmp_path / "data" / "runs",
        incoming_dir=tmp_path / "data" / "incoming",
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="sk-test-key",
        OPENAI_BASE_URL="https://api.example.com/v1",
        OPENAI_MODEL="gpt-test",
        rule_writer_system_prompt_path=prompts["writer_system"],
        rule_writer_user_prompt_path=prompts["writer_user"],
        rule_repair_system_prompt_path=prompts["repair_system"],
        rule_repair_user_prompt_path=prompts["repair_user"],
    )


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
    monkeypatch: pytest.MonkeyPatch, module: Any, responses: list[dict[str, Any]]
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
            return queue.pop(0)

    monkeypatch.setattr(module, "OpenAICompatibleChatClient", _FakeClient)
    return calls


def test_wiring_reaches_guardrails_applied_succeeded_without_repair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context_dir = tmp_path / "07-context"
    rule_draft_dir = tmp_path / "08-rule-drafts"
    guardrail_dir = tmp_path / "09-guardrails"
    _write_context_directory(context_dir, [_package("cand-1")])
    settings = _settings(tmp_path)
    run_json_path = tmp_path / "run.json"

    writer_calls = _install_fake_client(
        monkeypatch, rule_drafts_stage_module, [_valid_payload()]
    )
    # Nunca deberia llamarse: si el draft inicial ya pasa el guardrail,
    # GUARDRAILS_APPLIED no debe reparar.
    repair_calls = _install_fake_client(monkeypatch, guardrails_stage_module, [])

    now = datetime.now(UTC)
    state = RunState(
        run_id=_RUN_ID,
        package_filename="input/package.zip",
        source_package_hash=_HASH_A,
        current_stage=PipelineStage.CONTEXTS_BUILT,
        stages=[_stage(PipelineStage.CONTEXTS_BUILT, StageStatus.SUCCEEDED)],
        created_at=now,
        updated_at=now,
    )

    state = runner_module._run_rule_drafts_generated(
        state, context_dir, rule_draft_dir, settings, run_json_path
    )
    assert state.current_stage == PipelineStage.RULE_DRAFTS_GENERATED
    rule_drafts_executions = [
        s for s in state.stages if s.stage == PipelineStage.RULE_DRAFTS_GENERATED
    ]
    assert len(rule_drafts_executions) == 1
    assert rule_drafts_executions[0].status == StageStatus.SUCCEEDED
    assert len(writer_calls) == 1

    state = runner_module._run_guardrails_applied(
        state, context_dir, rule_draft_dir, guardrail_dir, settings, run_json_path
    )
    assert state.current_stage == PipelineStage.GUARDRAILS_APPLIED
    guardrails_executions = [s for s in state.stages if s.stage == PipelineStage.GUARDRAILS_APPLIED]
    assert len(guardrails_executions) == 1
    assert guardrails_executions[0].status == StageStatus.SUCCEEDED
    assert len(repair_calls) == 0  # ninguna reparacion invocada

    # Una unica StageExecution por etapa en todo RunState.
    stage_names = [s.stage for s in state.stages]
    assert len(stage_names) == len(set(stage_names))
    assert stage_names == [
        PipelineStage.CONTEXTS_BUILT,
        PipelineStage.RULE_DRAFTS_GENERATED,
        PipelineStage.GUARDRAILS_APPLIED,
    ]

    # run.json persistido y coherente con el RunState devuelto.
    persisted = RunState.model_validate_json(run_json_path.read_text(encoding="utf-8"))
    assert persisted == state

    # rule-draft-manifest.json valida.
    rule_draft_manifest = RuleDraftDirectoryManifest.model_validate_json(
        (rule_draft_dir / "rule-draft-manifest.json").read_text(encoding="utf-8")
    )
    assert rule_draft_manifest.draft_count == 1

    # guardrail-manifest.json valida.
    guardrail_manifest = GuardrailDirectoryManifest.model_validate_json(
        (guardrail_dir / "guardrail-manifest.json").read_text(encoding="utf-8")
    )
    assert guardrail_manifest.guardrail_count == 1
    record = guardrail_manifest.records[0]
    assert record.repair_attempts_used == 0

    # 08 sigue PENDING.
    draft_in_08 = RuleDraft.model_validate_json(
        (rule_draft_dir / _filename("cand-1")).read_text(encoding="utf-8")
    )
    assert draft_in_08.evidence_validation_status == EvidenceValidationStatus.PENDING

    # 09.final_rule_draft es EVIDENCE_VALIDATED y NEEDS_FUNCTIONAL_REVIEW.
    artifact = GuardrailCandidateArtifact.model_validate_json(
        (guardrail_dir / record.relative_filename).read_text(encoding="utf-8")
    )
    assert artifact.final_rule_draft.evidence_validation_status == (
        EvidenceValidationStatus.EVIDENCE_VALIDATED
    )
    assert artifact.final_rule_draft.functional_review_status == (
        FunctionalReviewStatus.NEEDS_FUNCTIONAL_REVIEW
    )

    # artifacts/10-rules/ (MarkdownRenderer, Prompt 13) no existe: fuera
    # de alcance de este prompt.
    assert not (tmp_path / "10-rules").exists()
    assert not (tmp_path / "data" / "runs" / _RUN_ID / "artifacts" / "10-rules").exists()


def test_wiring_repairs_once_then_reaches_guardrails_applied_succeeded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context_dir = tmp_path / "07-context"
    rule_draft_dir = tmp_path / "08-rule-drafts"
    guardrail_dir = tmp_path / "09-guardrails"
    _write_context_directory(context_dir, [_package("cand-1")])
    settings = _settings(tmp_path)
    run_json_path = tmp_path / "run.json"

    # El draft inicial es estructuralmente valido (RULE_DRAFTS_GENERATED
    # SUCCEEDED) pero cita un evidence_id inexistente: el guardrail lo
    # rechaza en la primera evaluacion.
    initial_payload = _valid_payload(
        claims=[
            {
                "claim_id": "c1",
                "field": "condition",
                "evidence_paths": ["$.decision.expression"],
                "evidence_ids": ["ev-does-not-exist"],
            }
        ]
    )
    repaired_payload = _valid_payload(title="Titulo reparado")

    _install_fake_client(monkeypatch, rule_drafts_stage_module, [initial_payload])
    repair_calls = _install_fake_client(
        monkeypatch, guardrails_stage_module, [repaired_payload]
    )

    now = datetime.now(UTC)
    state = RunState(
        run_id=_RUN_ID,
        package_filename="input/package.zip",
        source_package_hash=_HASH_A,
        current_stage=PipelineStage.CONTEXTS_BUILT,
        stages=[_stage(PipelineStage.CONTEXTS_BUILT, StageStatus.SUCCEEDED)],
        created_at=now,
        updated_at=now,
    )

    state = runner_module._run_rule_drafts_generated(
        state, context_dir, rule_draft_dir, settings, run_json_path
    )
    assert state.current_stage == PipelineStage.RULE_DRAFTS_GENERATED
    draft_in_08_before = RuleDraft.model_validate_json(
        (rule_draft_dir / _filename("cand-1")).read_text(encoding="utf-8")
    )
    bytes_in_08_before = (rule_draft_dir / _filename("cand-1")).read_bytes()

    state = runner_module._run_guardrails_applied(
        state, context_dir, rule_draft_dir, guardrail_dir, settings, run_json_path
    )

    assert state.current_stage == PipelineStage.GUARDRAILS_APPLIED
    guardrails_executions = [s for s in state.stages if s.stage == PipelineStage.GUARDRAILS_APPLIED]
    assert len(guardrails_executions) == 1
    assert guardrails_executions[0].status == StageStatus.SUCCEEDED

    # Exactamente una llamada de reparacion.
    assert len(repair_calls) == 1

    # 08 no cambia (mismos bytes, mismo contenido invalido/PENDING).
    assert (rule_draft_dir / _filename("cand-1")).read_bytes() == bytes_in_08_before
    assert draft_in_08_before.evidence_validation_status == EvidenceValidationStatus.PENDING

    # 09 contiene el draft REPARADO (titulo distinto) y EVIDENCE_VALIDATED.
    guardrail_manifest = GuardrailDirectoryManifest.model_validate_json(
        (guardrail_dir / "guardrail-manifest.json").read_text(encoding="utf-8")
    )
    record = guardrail_manifest.records[0]
    assert record.repair_attempts_used == 1
    artifact = GuardrailCandidateArtifact.model_validate_json(
        (guardrail_dir / record.relative_filename).read_text(encoding="utf-8")
    )
    assert artifact.final_rule_draft.title == "Titulo reparado"
    assert artifact.final_rule_draft.evidence_validation_status == (
        EvidenceValidationStatus.EVIDENCE_VALIDATED
    )
    assert len(artifact.repair_history) == 1
    assert artifact.repair_history[0].structurally_valid is True
