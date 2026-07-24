"""Tests de orquestacion de la transicion GUARDRAILS_APPLIED -> COMPLETED
(Prompt 13a): atomicidad, idempotencia y persistencia de
artifacts/10-rules/ a partir de un artifacts/09-guardrails/ realista
construido a mano (sin LLM, sin ContextPackage, sin Neo4j)."""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from altamira_extractor.contracts.enums import (
    EvidenceValidationStatus,
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
from altamira_extractor.contracts.run_state import StageExecution
from altamira_extractor.pipeline.errors import MarkdownRenderError
from altamira_extractor.pipeline.rules_rendered_stage import run_rules_rendered_stage

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
    return [_stage(PipelineStage.GUARDRAILS_APPLIED, StageStatus.SUCCEEDED)]


def _hash(draft: RuleDraft) -> str:
    return hashlib.sha256(draft.to_stable_json().encode("utf-8")).hexdigest()


def _final_draft(*, title: str = "Titulo", evidence_id: str = "ev-1") -> RuleDraft:
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


def _artifact(
    candidate_id: str, *, source_package_hash: str, draft: RuleDraft | None = None
) -> GuardrailCandidateArtifact:
    final_draft = draft if draft is not None else _final_draft()
    initial_draft = final_draft.model_copy(
        update={"evidence_validation_status": EvidenceValidationStatus.PENDING}
    )
    return GuardrailCandidateArtifact(
        candidate_id=candidate_id,
        source_package_hash=source_package_hash,
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
            source_package_hash=source_package_hash,
        ),
        repair_history=[],
        warnings=[],
    )


def _guardrail_filename(candidate_id: str) -> str:
    return hashlib.sha256(candidate_id.encode("utf-8")).hexdigest() + ".json"


def _md_filename(candidate_id: str) -> str:
    return hashlib.sha256(candidate_id.encode("utf-8")).hexdigest() + ".md"


def _write_guardrail_directory(
    guardrail_dir: Path,
    *,
    source_package_hash: str,
    artifacts: dict[str, GuardrailCandidateArtifact],
) -> None:
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
                repair_attempts_used=0,
                repair_response_hashes=[],
            )
        )
    records.sort(key=lambda r: r.candidate_id)
    manifest = GuardrailDirectoryManifest(
        run_id=_RUN_ID,
        source_package_hash=source_package_hash,
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


def _base_kwargs(tmp_path: Path, *, candidate_ids: list[str]) -> dict[str, Any]:
    guardrail_dir = tmp_path / "09-guardrails"
    artifacts = {cid: _artifact(cid, source_package_hash=_HASH_A) for cid in candidate_ids}
    _write_guardrail_directory(guardrail_dir, source_package_hash=_HASH_A, artifacts=artifacts)
    return {
        "run_id": _RUN_ID,
        "source_package_hash": _HASH_A,
        "run_stages": _succeeded_stages(),
        "guardrail_dir": guardrail_dir,
        "rules_dir": tmp_path / "10-rules",
    }


# --- precondicion ---


def test_missing_guardrails_applied_raises(tmp_path: Path) -> None:
    kwargs = _base_kwargs(tmp_path, candidate_ids=["cand-1"])
    kwargs["run_stages"] = []
    with pytest.raises(MarkdownRenderError):
        run_rules_rendered_stage(**kwargs)


def test_guardrails_applied_failed_raises(tmp_path: Path) -> None:
    kwargs = _base_kwargs(tmp_path, candidate_ids=["cand-1"])
    kwargs["run_stages"] = [_stage(PipelineStage.GUARDRAILS_APPLIED, StageStatus.FAILED)]
    with pytest.raises(MarkdownRenderError):
        run_rules_rendered_stage(**kwargs)


def test_duplicate_guardrails_applied_stage_execution_raises(tmp_path: Path) -> None:
    kwargs = _base_kwargs(tmp_path, candidate_ids=["cand-1"])
    kwargs["run_stages"] = [
        _stage(PipelineStage.GUARDRAILS_APPLIED, StageStatus.SUCCEEDED),
        _stage(PipelineStage.GUARDRAILS_APPLIED, StageStatus.SUCCEEDED),
    ]
    with pytest.raises(MarkdownRenderError):
        run_rules_rendered_stage(**kwargs)


# --- integridad de artifacts/09-guardrails/ ---


def test_missing_guardrail_manifest_raises(tmp_path: Path) -> None:
    kwargs = _base_kwargs(tmp_path, candidate_ids=["cand-1"])
    (kwargs["guardrail_dir"] / "guardrail-manifest.json").unlink()
    with pytest.raises(MarkdownRenderError):
        run_rules_rendered_stage(**kwargs)


def test_invalid_guardrail_manifest_raises(tmp_path: Path) -> None:
    kwargs = _base_kwargs(tmp_path, candidate_ids=["cand-1"])
    (kwargs["guardrail_dir"] / "guardrail-manifest.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(MarkdownRenderError):
        run_rules_rendered_stage(**kwargs)


def test_source_package_hash_drift_raises(tmp_path: Path) -> None:
    kwargs = _base_kwargs(tmp_path, candidate_ids=["cand-1"])
    kwargs["source_package_hash"] = "b" * 64
    with pytest.raises(MarkdownRenderError):
        run_rules_rendered_stage(**kwargs)


def test_extra_unreferenced_file_in_guardrail_dir_raises(tmp_path: Path) -> None:
    kwargs = _base_kwargs(tmp_path, candidate_ids=["cand-1"])
    (kwargs["guardrail_dir"] / "extra.txt").write_text("intruso", encoding="utf-8")
    with pytest.raises(MarkdownRenderError):
        run_rules_rendered_stage(**kwargs)


def test_missing_declared_file_in_guardrail_dir_raises(tmp_path: Path) -> None:
    kwargs = _base_kwargs(tmp_path, candidate_ids=["cand-1"])
    (kwargs["guardrail_dir"] / _guardrail_filename("cand-1")).unlink()
    with pytest.raises(MarkdownRenderError):
        run_rules_rendered_stage(**kwargs)


def test_subdirectory_in_guardrail_dir_raises(tmp_path: Path) -> None:
    kwargs = _base_kwargs(tmp_path, candidate_ids=["cand-1"])
    (kwargs["guardrail_dir"] / "stray-subdir").mkdir()
    with pytest.raises(MarkdownRenderError):
        run_rules_rendered_stage(**kwargs)


def test_symlink_in_guardrail_dir_raises(tmp_path: Path) -> None:
    kwargs = _base_kwargs(tmp_path, candidate_ids=["cand-1"])
    target = tmp_path / "outside.json"
    target.write_text("{}", encoding="utf-8")
    try:
        os.symlink(target, kwargs["guardrail_dir"] / "sneaky.json")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks no soportados en este entorno")
    with pytest.raises(MarkdownRenderError):
        run_rules_rendered_stage(**kwargs)


def test_tampered_guardrail_artifact_hash_raises(tmp_path: Path) -> None:
    kwargs = _base_kwargs(tmp_path, candidate_ids=["cand-1"])
    tampered = _artifact(
        "cand-1", source_package_hash=_HASH_A, draft=_final_draft(title="Tampered")
    )
    (kwargs["guardrail_dir"] / _guardrail_filename("cand-1")).write_text(
        tampered.to_stable_json(), encoding="utf-8"
    )
    with pytest.raises(MarkdownRenderError):
        run_rules_rendered_stage(**kwargs)


# --- flujo feliz ---


def test_single_candidate_renders_one_markdown_file(tmp_path: Path) -> None:
    kwargs = _base_kwargs(tmp_path, candidate_ids=["cand-1"])

    warnings = run_rules_rendered_stage(**kwargs)

    assert warnings == []
    rules_dir = kwargs["rules_dir"]
    manifest = RulesDirectoryManifest.model_validate_json(
        (rules_dir / "rules-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest.rule_count == 1
    record = manifest.records[0]
    assert record.candidate_id == "cand-1"
    assert record.relative_filename == _md_filename("cand-1")
    md_path = rules_dir / record.relative_filename
    assert md_path.is_file()
    actual_hash = hashlib.sha256(md_path.read_bytes()).hexdigest()
    assert actual_hash == record.markdown_hash
    assert md_path.read_bytes().decode("utf-8").startswith("# Titulo\n")


def test_multiple_candidates_all_rendered(tmp_path: Path) -> None:
    kwargs = _base_kwargs(tmp_path, candidate_ids=["cand-1", "cand-2"])

    warnings = run_rules_rendered_stage(**kwargs)

    assert warnings == []
    manifest = RulesDirectoryManifest.model_validate_json(
        (kwargs["rules_dir"] / "rules-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest.rule_count == 2
    candidate_ids = sorted(record.candidate_id for record in manifest.records)
    assert candidate_ids == ["cand-1", "cand-2"]
    for record in manifest.records:
        assert (kwargs["rules_dir"] / record.relative_filename).is_file()


def test_guardrail_count_zero_reaches_empty_manifest_without_markdown_files(tmp_path: Path) -> None:
    kwargs = _base_kwargs(tmp_path, candidate_ids=[])

    warnings = run_rules_rendered_stage(**kwargs)

    assert warnings == []
    rules_dir = kwargs["rules_dir"]
    manifest = RulesDirectoryManifest.model_validate_json(
        (rules_dir / "rules-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest.rule_count == 0
    assert manifest.records == []
    assert manifest.warnings == []
    md_files = [p for p in rules_dir.glob("*.md")]
    assert md_files == []


# --- idempotencia / fast-path ---


def test_second_run_with_no_changes_does_not_rewrite(tmp_path: Path) -> None:
    kwargs = _base_kwargs(tmp_path, candidate_ids=["cand-1"])
    run_rules_rendered_stage(**kwargs)
    manifest_path = kwargs["rules_dir"] / "rules-manifest.json"
    first_bytes = manifest_path.read_bytes()
    first_mtime = manifest_path.stat().st_mtime_ns

    warnings = run_rules_rendered_stage(**kwargs)

    assert warnings == []
    assert manifest_path.read_bytes() == first_bytes
    assert manifest_path.stat().st_mtime_ns == first_mtime


def test_fast_path_does_not_require_previous_runstate(tmp_path: Path) -> None:
    kwargs = _base_kwargs(tmp_path, candidate_ids=["cand-1"])
    run_rules_rendered_stage(**kwargs)

    # Sin ningun StageExecution COMPLETED previo (solo la precondicion
    # GUARDRAILS_APPLIED), el fast-path igual reutiliza la salida valida
    # existente en disco.
    warnings = run_rules_rendered_stage(**kwargs)
    assert warnings == []


def test_renderer_version_mismatch_forces_reconstruction(tmp_path: Path) -> None:
    kwargs = _base_kwargs(tmp_path, candidate_ids=["cand-1"])
    run_rules_rendered_stage(**kwargs)
    manifest_path = kwargs["rules_dir"] / "rules-manifest.json"
    manifest = RulesDirectoryManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    tampered = manifest.model_copy(update={"renderer_version": "0.9"})
    manifest_path.write_text(tampered.to_stable_json(), encoding="utf-8")

    run_rules_rendered_stage(**kwargs)

    rebuilt = RulesDirectoryManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    assert rebuilt.renderer_version == "1.0"


def test_change_in_guardrail_artifact_forces_reconstruction(tmp_path: Path) -> None:
    kwargs = _base_kwargs(tmp_path, candidate_ids=["cand-1"])
    run_rules_rendered_stage(**kwargs)
    first_hash = (
        RulesDirectoryManifest.model_validate_json(
            (kwargs["rules_dir"] / "rules-manifest.json").read_text(encoding="utf-8")
        )
        .records[0]
        .final_rule_draft_hash
    )

    new_artifact = _artifact(
        "cand-1", source_package_hash=_HASH_A, draft=_final_draft(title="Titulo nuevo")
    )
    _write_guardrail_directory(
        kwargs["guardrail_dir"], source_package_hash=_HASH_A, artifacts={"cand-1": new_artifact}
    )

    run_rules_rendered_stage(**kwargs)

    manifest = RulesDirectoryManifest.model_validate_json(
        (kwargs["rules_dir"] / "rules-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest.records[0].final_rule_draft_hash != first_hash
    md_path = kwargs["rules_dir"] / manifest.records[0].relative_filename
    assert md_path.read_bytes().decode("utf-8").startswith("# Titulo nuevo\n")


def test_extra_file_in_rules_dir_is_rejected_and_cleaned_on_regeneration(tmp_path: Path) -> None:
    kwargs = _base_kwargs(tmp_path, candidate_ids=["cand-1"])
    run_rules_rendered_stage(**kwargs)
    (kwargs["rules_dir"] / "extra.txt").write_text("intruso", encoding="utf-8")

    warnings = run_rules_rendered_stage(**kwargs)

    assert warnings == []
    assert not (kwargs["rules_dir"] / "extra.txt").exists()


def test_subdirectory_in_rules_dir_raises(tmp_path: Path) -> None:
    kwargs = _base_kwargs(tmp_path, candidate_ids=["cand-1"])
    run_rules_rendered_stage(**kwargs)
    (kwargs["rules_dir"] / "stray-subdir").mkdir()

    with pytest.raises(MarkdownRenderError):
        run_rules_rendered_stage(**kwargs)


def test_symlink_in_rules_dir_raises(tmp_path: Path) -> None:
    kwargs = _base_kwargs(tmp_path, candidate_ids=["cand-1"])
    run_rules_rendered_stage(**kwargs)
    target = tmp_path / "outside.md"
    target.write_text("# intruso\n", encoding="utf-8")
    try:
        os.symlink(target, kwargs["rules_dir"] / "sneaky.md")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks no soportados en este entorno")

    with pytest.raises(MarkdownRenderError):
        run_rules_rendered_stage(**kwargs)


def test_incorrect_filename_in_existing_manifest_is_rejected(tmp_path: Path) -> None:
    kwargs = _base_kwargs(tmp_path, candidate_ids=["cand-1"])
    run_rules_rendered_stage(**kwargs)
    rules_dir = kwargs["rules_dir"]
    manifest_path = rules_dir / "rules-manifest.json"
    manifest = RulesDirectoryManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    correct_name = manifest.records[0].relative_filename
    wrong_name = "not-the-sha256.md"
    (rules_dir / correct_name).rename(rules_dir / wrong_name)
    tampered = manifest.model_copy(
        update={
            "records": [manifest.records[0].model_copy(update={"relative_filename": wrong_name})]
        }
    )
    manifest_path.write_text(tampered.to_stable_json(), encoding="utf-8")

    with pytest.raises(MarkdownRenderError):
        run_rules_rendered_stage(**kwargs)
