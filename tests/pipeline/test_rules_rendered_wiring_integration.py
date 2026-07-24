"""Integracion del wiring real GUARDRAILS_APPLIED -> COMPLETED a traves de
`runner.py` (Prompt 13a).

Marcado `integration` por el mismo motivo que
`test_rule_drafts_and_guardrails_wiring_integration.py`: ejercita la
funcion REAL de `runner.py` (`_run_rules_rendered`, con `StageExecution`/
`RunState` reales) — no porque requiera Neo4j, Java o un proveedor LLM.
`artifacts/09-guardrails/` se construye a mano de forma realista (mismos
contratos que produce GUARDRAILS_APPLIED). `rules_rendered_stage.py` no
importa ningun cliente LLM ni el driver de Neo4j ni
`deterministic_guardrail.py`: no hace falta ningun monkeypatch/cliente
falso para garantizar cero llamadas externas."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from altamira_extractor.contracts.enums import (
    EvidenceValidationStatus,
    FunctionalReviewStatus,
    GuardrailVerdict,
    PipelineStage,
    StageStatus,
)
from altamira_extractor.contracts.guardrail import GuardrailReport
from altamira_extractor.contracts.guardrail_candidate import GuardrailCandidateArtifact
from altamira_extractor.contracts.guardrail_manifest import (
    GuardrailDirectoryManifest,
    GuardrailRecord,
)
from altamira_extractor.contracts.rule_draft import Claim, ClaimField, RuleDraft
from altamira_extractor.contracts.rules_manifest import RulesDirectoryManifest
from altamira_extractor.contracts.run_state import RunState, StageExecution
from altamira_extractor.pipeline import runner as runner_module

pytestmark = pytest.mark.integration

_HASH_A = "a" * 64
_RUN_ID = "run-wiring-rules-1"


def _stage(stage: PipelineStage, status: StageStatus) -> StageExecution:
    now = datetime.now(UTC)
    kwargs: dict[str, Any] = {"stage": stage, "status": status}
    if status in (StageStatus.SUCCEEDED, StageStatus.FAILED):
        kwargs["started_at"] = now
        kwargs["finished_at"] = now
    return StageExecution(**kwargs)


def _hash(draft: RuleDraft) -> str:
    return hashlib.sha256(draft.to_stable_json().encode("utf-8")).hexdigest()


def _final_draft() -> RuleDraft:
    return RuleDraft(
        schema_version="2.0",
        title="Titulo",
        context="Contexto",
        statement="Enunciado",
        condition="WS-COD = 'R001'",
        parameters=[],
        effect="Efecto",
        parameter_source=None,
        traceability=["ev-1"],
        limitations=["Requiere revision funcional"],
        claims=[
            Claim(
                claim_id="c1",
                field=ClaimField.CONDITION,
                evidence_paths=["$.decision.expression"],
                evidence_ids=["ev-1"],
            )
        ],
        evidence_validation_status=EvidenceValidationStatus.EVIDENCE_VALIDATED,
    )


def _guardrail_filename(candidate_id: str) -> str:
    return hashlib.sha256(candidate_id.encode("utf-8")).hexdigest() + ".json"


def _md_filename(candidate_id: str) -> str:
    return hashlib.sha256(candidate_id.encode("utf-8")).hexdigest() + ".md"


def _write_guardrail_directory(guardrail_dir: Path, *, candidate_ids: list[str]) -> None:
    guardrail_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for candidate_id in candidate_ids:
        final_draft = _final_draft()
        initial_draft = final_draft.model_copy(
            update={"evidence_validation_status": EvidenceValidationStatus.PENDING}
        )
        artifact = GuardrailCandidateArtifact(
            candidate_id=candidate_id,
            source_package_hash=_HASH_A,
            context_hash="c" * 64,
            initial_rule_draft_hash=_hash(initial_draft),
            final_rule_draft_hash=_hash(final_draft),
            final_rule_draft=final_draft,
            guardrail_report=GuardrailReport(
                candidate_id=candidate_id,
                verdict=GuardrailVerdict.EVIDENCE_VALIDATED,
                violations=[],
                repair_attempts=0,
                evaluated_at=datetime.now(UTC),
                source_package_hash=_HASH_A,
            ),
            repair_history=[],
            warnings=[],
        )
        filename = _guardrail_filename(candidate_id)
        (guardrail_dir / filename).write_text(artifact.to_stable_json(), encoding="utf-8")
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
                repair_attempts_used=0,
                repair_response_hashes=[],
            )
        )
    records.sort(key=lambda r: r.candidate_id)
    manifest = GuardrailDirectoryManifest(
        run_id=_RUN_ID,
        source_package_hash=_HASH_A,
        rule_draft_manifest_hash="d" * 64,
        rule_draft_schema_hash="e" * 64,
        provider="openai",
        model="gpt-test",
        llm_repair_attempts=2,
        guardrail_version="1.1",
        repair_system_template_hash="f" * 64,
        repair_user_template_hash="0" * 64,
        records=records,
        guardrail_count=len(records),
        warnings=[],
    )
    (guardrail_dir / "guardrail-manifest.json").write_text(
        manifest.to_stable_json(), encoding="utf-8"
    )


def _initial_state() -> RunState:
    now = datetime.now(UTC)
    return RunState(
        run_id=_RUN_ID,
        package_filename="input/package.zip",
        source_package_hash=_HASH_A,
        current_stage=PipelineStage.GUARDRAILS_APPLIED,
        stages=[_stage(PipelineStage.GUARDRAILS_APPLIED, StageStatus.SUCCEEDED)],
        created_at=now,
        updated_at=now,
    )


def test_wiring_reaches_completed_and_renders_markdown(tmp_path: Path) -> None:
    guardrail_dir = tmp_path / "09-guardrails"
    rules_dir = tmp_path / "10-rules"
    _write_guardrail_directory(guardrail_dir, candidate_ids=["cand-1"])
    run_json_path = tmp_path / "run.json"

    state = runner_module._run_rules_rendered(
        _initial_state(), guardrail_dir, rules_dir, run_json_path
    )

    assert state.current_stage == PipelineStage.COMPLETED
    completed_executions = [s for s in state.stages if s.stage == PipelineStage.COMPLETED]
    assert len(completed_executions) == 1
    assert completed_executions[0].status == StageStatus.SUCCEEDED
    assert completed_executions[0].warnings == []

    stage_names = [s.stage for s in state.stages]
    assert len(stage_names) == len(set(stage_names))
    assert stage_names == [PipelineStage.GUARDRAILS_APPLIED, PipelineStage.COMPLETED]

    persisted = RunState.model_validate_json(run_json_path.read_text(encoding="utf-8"))
    assert persisted == state

    manifest = RulesDirectoryManifest.model_validate_json(
        (rules_dir / "rules-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest.rule_count == 1
    record = manifest.records[0]
    assert record.candidate_id == "cand-1"
    assert record.relative_filename == _md_filename("cand-1")

    markdown_text = (rules_dir / record.relative_filename).read_bytes().decode("utf-8")
    assert markdown_text.startswith("# Titulo\n")
    assert "> Estado de evidencia: EVIDENCE_VALIDATED" in markdown_text
    assert (
        "> Estado de revisión funcional: "
        f"{FunctionalReviewStatus.NEEDS_FUNCTIONAL_REVIEW.value}" in markdown_text
    )

    # No se creo ningun artefacto posterior a 10-rules/ (Prompt 13b/13c/13d
    # fuera de alcance).
    artifacts_entries = {p.name for p in tmp_path.iterdir()}
    assert artifacts_entries == {"09-guardrails", "10-rules", "run.json"}


def test_wiring_fast_path_second_run_does_not_rewrite(tmp_path: Path) -> None:
    guardrail_dir = tmp_path / "09-guardrails"
    rules_dir = tmp_path / "10-rules"
    _write_guardrail_directory(guardrail_dir, candidate_ids=["cand-1"])
    run_json_path = tmp_path / "run.json"

    state = runner_module._run_rules_rendered(
        _initial_state(), guardrail_dir, rules_dir, run_json_path
    )
    manifest_path = rules_dir / "rules-manifest.json"
    first_bytes = manifest_path.read_bytes()

    # Segunda corrida: reanuda desde el mismo RunState ya en COMPLETED
    # (misma StageExecution GUARDRAILS_APPLIED SUCCEEDED como precondicion).
    state = runner_module._run_rules_rendered(state, guardrail_dir, rules_dir, run_json_path)

    assert state.current_stage == PipelineStage.COMPLETED
    completed_executions = [s for s in state.stages if s.stage == PipelineStage.COMPLETED]
    assert len(completed_executions) == 1  # nunca se duplica via _upsert_stage
    assert manifest_path.read_bytes() == first_bytes


def test_wiring_guardrail_count_zero_reaches_completed(tmp_path: Path) -> None:
    guardrail_dir = tmp_path / "09-guardrails"
    rules_dir = tmp_path / "10-rules"
    _write_guardrail_directory(guardrail_dir, candidate_ids=[])
    run_json_path = tmp_path / "run.json"

    state = runner_module._run_rules_rendered(
        _initial_state(), guardrail_dir, rules_dir, run_json_path
    )

    assert state.current_stage == PipelineStage.COMPLETED
    manifest = RulesDirectoryManifest.model_validate_json(
        (rules_dir / "rules-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest.rule_count == 0
    assert manifest.records == []
    assert list(rules_dir.glob("*.md")) == []
