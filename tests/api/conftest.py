"""Fixtures y builders compartidos por tests/api/ (Prompt 13b).

Construye `data/runs/<run_id>/` a mano (mismo patron que
`tests/pipeline/test_rules_rendered_stage.py`): sin JAR, sin Neo4j, sin
LLM real. Cada builder deja el run exactamente en la etapa que el test
necesita, con artefactos reales y validos contra los contratos."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from altamira_extractor.api.app import create_app
from altamira_extractor.config import Settings
from altamira_extractor.contracts.candidate import CandidateArtifact, RuleCandidate
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
    CandidateStatus,
    CompletenessStatus,
    EvidenceValidationStatus,
    GuardrailVerdict,
    InclusionReason,
    PipelineStage,
    Severity,
    StageStatus,
)
from altamira_extractor.contracts.guardrail import GuardrailReport, GuardrailViolation
from altamira_extractor.contracts.guardrail_candidate import GuardrailCandidateArtifact
from altamira_extractor.contracts.guardrail_manifest import (
    GuardrailDirectoryManifest,
    GuardrailRecord,
)
from altamira_extractor.contracts.rule_draft import Claim, ClaimField, RuleDraft
from altamira_extractor.contracts.rules_manifest import RulesDirectoryManifest, RulesRecord
from altamira_extractor.contracts.run_state import RunState, StageExecution
from altamira_extractor.pipeline.markdown_renderer import MARKDOWN_RENDERER_VERSION, render_markdown

from ..e2e_support import write_disabled_dev_security_config

HASH_A = "a" * 64
RUN_ID = "20260724T120000123456-deadbeef"
CANDIDATE_ID = "candidate::det::1.0::" + HASH_A + "::dec-1"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        runs_dir=tmp_path / "data" / "runs",
        incoming_dir=tmp_path / "data" / "incoming",
        security_config_path=write_disabled_dev_security_config(tmp_path),
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    # `with` es obligatorio: dispara el `lifespan` (startup/shutdown) que
    # crea `app.state.executor` -- sin el context manager, Depends(get_executor)
    # fallaria con AttributeError en la primera request.
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def now() -> datetime:
    return datetime.now(UTC)


def stage_execution(stage: PipelineStage, status: StageStatus, **overrides: Any) -> StageExecution:
    kwargs: dict[str, Any] = {"stage": stage, "status": status}
    if status in (StageStatus.SUCCEEDED, StageStatus.FAILED):
        kwargs["started_at"] = now()
        kwargs["finished_at"] = now()
    if status == StageStatus.FAILED:
        kwargs["error"] = "fallo simulado"
    kwargs.update(overrides)
    return StageExecution(**kwargs)


def write_run_state(run_dir: Path, state: RunState) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run.json").write_text(state.to_stable_json(), encoding="utf-8")


def write_input_package_zip(run_dir: Path) -> None:
    """Escribe un `input/package.zip` de relleno (solo para que
    `resume`/lectores de RunState encuentren un archivo regular donde lo
    esperan) -- NO es un ZIP Altamira valido; los tests que necesiten
    ejecutar `run_ingestion` de verdad en background deben monkeypatchear
    esa funcion o usar un ZIP real (ver tests/pipeline/conftest.py)."""
    input_dir = run_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "package.zip").write_bytes(b"placeholder, no es un ZIP real")


def build_run_state(
    run_id: str = RUN_ID, *, stages: list[StageExecution], current_stage: PipelineStage
) -> RunState:
    created = now()
    return RunState(
        run_id=run_id,
        package_filename="input/package.zip",
        source_package_hash=HASH_A,
        current_stage=current_stage,
        stages=stages,
        created_at=created,
        updated_at=created,
    )


def build_candidate(candidate_id: str = CANDIDATE_ID) -> RuleCandidate:
    return RuleCandidate(
        candidate_id=candidate_id,
        paragraph_id="p1",
        paragraph_name="MAIN",
        decision_id="dec-1",
        detector_id="det",
        detector_version="1.0",
        detector_score=1.0,
        status=CandidateStatus.DETECTED_CANDIDATE,
        condition="WS-COD = 'R001'",
        outcome_code="R001",
        rule_type=None,
        line_start=10,
        source_file="cobol/PROG1.cbl",
        source_package_hash=HASH_A,
    )


def write_candidates_artifact(run_dir: Path, candidates: list[RuleCandidate]) -> None:
    artifact_dir = run_dir / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact = CandidateArtifact(
        run_id=RUN_ID,
        source_package_hash=HASH_A,
        semantic_graph_hash="b" * 64,
        invariants_query_hash="c" * 64,
        q0_query_hash="d" * 64,
        candidates=sorted(candidates, key=lambda c: c.candidate_id),
        warnings=[],
    )
    (artifact_dir / "06-candidates.json").write_text(artifact.to_stable_json(), encoding="utf-8")


def build_context_package(candidate_id: str = CANDIDATE_ID) -> ContextPackage:
    evidence = EvidenceEntry(
        evidence_id="ev-1",
        kind="decision",
        source_file="cobol/PROG1.cbl",
        line_start=10,
        line_end=10,
        source_package_hash=HASH_A,
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
            source_package_hash=HASH_A,
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


def write_context_directory(run_dir: Path, packages: list[ContextPackage]) -> None:
    context_dir = run_dir / "artifacts" / "07-context"
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
        run_id=RUN_ID,
        source_package_hash=HASH_A,
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


def build_final_draft(*, title: str = "Titulo", evidence_id: str = "ev-1") -> RuleDraft:
    return RuleDraft(
        schema_version="2.0",
        title=title,
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
                evidence_ids=[evidence_id],
            )
        ],
        evidence_validation_status=EvidenceValidationStatus.EVIDENCE_VALIDATED,
    )


def _draft_hash(draft: RuleDraft) -> str:
    return hashlib.sha256(draft.to_stable_json().encode("utf-8")).hexdigest()


def build_guardrail_artifact(
    candidate_id: str = CANDIDATE_ID,
    *,
    final_draft: RuleDraft | None = None,
    violations: list[GuardrailViolation] | None = None,
    repair_attempts: int = 0,
) -> GuardrailCandidateArtifact:
    draft = final_draft if final_draft is not None else build_final_draft()
    initial_draft = draft.model_copy(
        update={"evidence_validation_status": EvidenceValidationStatus.PENDING}
    )
    return GuardrailCandidateArtifact(
        candidate_id=candidate_id,
        source_package_hash=HASH_A,
        context_hash="c" * 64,
        initial_rule_draft_hash=_draft_hash(initial_draft),
        final_rule_draft_hash=_draft_hash(draft),
        final_rule_draft=draft,
        guardrail_report=GuardrailReport(
            candidate_id=candidate_id,
            verdict=GuardrailVerdict.EVIDENCE_VALIDATED,
            violations=violations or [],
            repair_attempts=repair_attempts,
            evaluated_at=now(),
            source_package_hash=HASH_A,
        ),
        repair_history=[],
        warnings=[],
    )


def _guardrail_filename(candidate_id: str) -> str:
    return hashlib.sha256(candidate_id.encode("utf-8")).hexdigest() + ".json"


def write_guardrail_directory(
    run_dir: Path, artifacts: dict[str, GuardrailCandidateArtifact]
) -> None:
    guardrail_dir = run_dir / "artifacts" / "09-guardrails"
    guardrail_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for candidate_id, artifact in artifacts.items():
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
                repair_attempts_used=len(artifact.repair_history),
                repair_response_hashes=[],
            )
        )
    records.sort(key=lambda r: r.candidate_id)
    manifest = GuardrailDirectoryManifest(
        run_id=RUN_ID,
        source_package_hash=HASH_A,
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


def _md_filename(candidate_id: str) -> str:
    return hashlib.sha256(candidate_id.encode("utf-8")).hexdigest() + ".md"


def write_rules_directory(run_dir: Path, artifacts: dict[str, GuardrailCandidateArtifact]) -> None:
    rules_dir = run_dir / "artifacts" / "10-rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for candidate_id, artifact in artifacts.items():
        markdown_bytes = render_markdown(artifact.final_rule_draft)
        filename = _md_filename(candidate_id)
        (rules_dir / filename).write_bytes(markdown_bytes)
        records.append(
            RulesRecord(
                candidate_id=candidate_id,
                source_guardrail_artifact_hash=hashlib.sha256(
                    artifact.to_stable_json().encode("utf-8")
                ).hexdigest(),
                final_rule_draft_hash=artifact.final_rule_draft_hash,
                relative_filename=filename,
                markdown_hash=hashlib.sha256(markdown_bytes).hexdigest(),
            )
        )
    records.sort(key=lambda r: r.candidate_id)
    guardrail_manifest_bytes = (
        run_dir / "artifacts" / "09-guardrails" / "guardrail-manifest.json"
    ).read_bytes()
    manifest = RulesDirectoryManifest(
        run_id=RUN_ID,
        source_package_hash=HASH_A,
        guardrail_manifest_hash=hashlib.sha256(guardrail_manifest_bytes).hexdigest(),
        renderer_version=MARKDOWN_RENDERER_VERSION,
        records=records,
        rule_count=len(records),
        warnings=[],
    )
    (rules_dir / "rules-manifest.json").write_text(manifest.to_stable_json(), encoding="utf-8")


def build_run_up_to_candidates_detected(run_dir: Path, run_id: str = RUN_ID) -> RunState:
    stages = [
        stage_execution(PipelineStage.RECEIVED, StageStatus.SUCCEEDED),
        stage_execution(PipelineStage.CANDIDATES_DETECTED, StageStatus.SUCCEEDED),
    ]
    state = build_run_state(run_id, stages=stages, current_stage=PipelineStage.CANDIDATES_DETECTED)
    write_run_state(run_dir, state)
    write_input_package_zip(run_dir)
    write_candidates_artifact(run_dir, [build_candidate()])
    return state


def build_run_up_to_contexts_built(run_dir: Path, run_id: str = RUN_ID) -> RunState:
    stages = [
        stage_execution(PipelineStage.RECEIVED, StageStatus.SUCCEEDED),
        stage_execution(PipelineStage.CANDIDATES_DETECTED, StageStatus.SUCCEEDED),
        stage_execution(PipelineStage.CONTEXTS_BUILT, StageStatus.SUCCEEDED),
    ]
    state = build_run_state(run_id, stages=stages, current_stage=PipelineStage.CONTEXTS_BUILT)
    write_run_state(run_dir, state)
    write_input_package_zip(run_dir)
    write_candidates_artifact(run_dir, [build_candidate()])
    write_context_directory(run_dir, [build_context_package()])
    return state


def build_run_up_to_guardrails_applied(
    run_dir: Path,
    run_id: str = RUN_ID,
    *,
    final_draft: RuleDraft | None = None,
    violations: list[GuardrailViolation] | None = None,
    repair_attempts: int = 0,
) -> RunState:
    stages = [
        stage_execution(PipelineStage.RECEIVED, StageStatus.SUCCEEDED),
        stage_execution(PipelineStage.CANDIDATES_DETECTED, StageStatus.SUCCEEDED),
        stage_execution(PipelineStage.CONTEXTS_BUILT, StageStatus.SUCCEEDED),
        stage_execution(PipelineStage.GUARDRAILS_APPLIED, StageStatus.SUCCEEDED),
    ]
    state = build_run_state(run_id, stages=stages, current_stage=PipelineStage.GUARDRAILS_APPLIED)
    write_run_state(run_dir, state)
    write_input_package_zip(run_dir)
    write_candidates_artifact(run_dir, [build_candidate()])
    write_context_directory(run_dir, [build_context_package()])
    artifact = build_guardrail_artifact(
        final_draft=final_draft, violations=violations, repair_attempts=repair_attempts
    )
    write_guardrail_directory(run_dir, {CANDIDATE_ID: artifact})
    return state


def build_run_completed(run_dir: Path, run_id: str = RUN_ID) -> RunState:
    stages = [
        stage_execution(PipelineStage.RECEIVED, StageStatus.SUCCEEDED),
        stage_execution(PipelineStage.CANDIDATES_DETECTED, StageStatus.SUCCEEDED),
        stage_execution(PipelineStage.CONTEXTS_BUILT, StageStatus.SUCCEEDED),
        stage_execution(PipelineStage.GUARDRAILS_APPLIED, StageStatus.SUCCEEDED),
        stage_execution(PipelineStage.COMPLETED, StageStatus.SUCCEEDED),
    ]
    state = build_run_state(run_id, stages=stages, current_stage=PipelineStage.COMPLETED)
    write_run_state(run_dir, state)
    write_input_package_zip(run_dir)
    write_candidates_artifact(run_dir, [build_candidate()])
    write_context_directory(run_dir, [build_context_package()])
    artifact = build_guardrail_artifact()
    write_guardrail_directory(run_dir, {CANDIDATE_ID: artifact})
    write_rules_directory(run_dir, {CANDIDATE_ID: artifact})
    return state


__all__ = [
    "CANDIDATE_ID",
    "HASH_A",
    "RUN_ID",
    "Severity",
    "build_candidate",
    "build_context_package",
    "build_final_draft",
    "build_guardrail_artifact",
    "build_run_completed",
    "build_run_state",
    "build_run_up_to_candidates_detected",
    "build_run_up_to_contexts_built",
    "build_run_up_to_guardrails_applied",
    "now",
    "stage_execution",
    "write_candidates_artifact",
    "write_context_directory",
    "write_guardrail_directory",
    "write_input_package_zip",
    "write_rules_directory",
    "write_run_state",
]
