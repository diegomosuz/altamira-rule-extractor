"""GET /api/runs, GET /api/runs/{run_id}, .../candidates, .../context,
.../rule (Prompt 13b): sin ejecutar ningun pipeline real -- runs
construidos a mano via tests/api/conftest.py."""

from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from altamira_extractor.config import Settings
from altamira_extractor.contracts.candidate import CandidateArtifact
from altamira_extractor.contracts.context_package import ContextPackage
from altamira_extractor.contracts.enums import (
    PipelineStage,
    Severity,
    StageStatus,
)
from altamira_extractor.contracts.guardrail import GuardrailViolation

from .conftest import (
    CANDIDATE_ID,
    RUN_ID,
    build_final_draft,
    build_guardrail_artifact,
    build_run_completed,
    build_run_state,
    build_run_up_to_candidates_detected,
    build_run_up_to_contexts_built,
    build_run_up_to_guardrails_applied,
    stage_execution,
    write_run_state,
)

# --- GET /api/runs ---


def test_list_runs_empty(client: TestClient) -> None:
    response = client.get("/api/runs")
    assert response.status_code == 200
    assert response.json() == {"runs": [], "total": 0, "limit": 20, "offset": 0}


def test_list_runs_returns_created_runs_descending(client: TestClient, settings: Settings) -> None:
    for run_id in ("20260101T000000000000-aaaaaaaa", "20260102T000000000000-bbbbbbbb"):
        build_run_completed(settings.runs_dir / run_id, run_id)

    response = client.get("/api/runs")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert [r["run_id"] for r in body["runs"]] == [
        "20260102T000000000000-bbbbbbbb",
        "20260101T000000000000-aaaaaaaa",
    ]


def test_list_runs_pagination(client: TestClient, settings: Settings) -> None:
    run_ids = [f"2026010{i}T000000000000-aaaaaaa{i}" for i in range(1, 6)]
    for run_id in run_ids:
        build_run_completed(settings.runs_dir / run_id, run_id)

    response = client.get("/api/runs", params={"limit": 2, "offset": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5
    assert body["limit"] == 2
    assert body["offset"] == 1
    assert len(body["runs"]) == 2


def test_list_runs_rejects_out_of_range_params(client: TestClient) -> None:
    response = client.get("/api/runs", params={"limit": 0})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_list_runs_skips_corrupt_run_without_failing(
    client: TestClient, settings: Settings
) -> None:
    good_run_id = "20260101T000000000000-aaaaaaaa"
    build_run_completed(settings.runs_dir / good_run_id, good_run_id)

    corrupt_dir = settings.runs_dir / "20260102T000000000000-bbbbbbbb"
    corrupt_dir.mkdir(parents=True)
    (corrupt_dir / "run.json").write_text("{not valid json", encoding="utf-8")

    response = client.get("/api/runs")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["runs"][0]["run_id"] == good_run_id


def test_list_runs_ignores_non_directory_entries(client: TestClient, settings: Settings) -> None:
    settings.runs_dir.mkdir(parents=True, exist_ok=True)
    (settings.runs_dir / "stray-file.txt").write_text("x", encoding="utf-8")

    response = client.get("/api/runs")
    assert response.status_code == 200
    assert response.json()["total"] == 0


# --- GET /api/runs/{run_id} ---


def test_get_run_detail(client: TestClient, settings: Settings) -> None:
    build_run_completed(settings.runs_dir / RUN_ID, RUN_ID)

    response = client.get(f"/api/runs/{RUN_ID}")
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == RUN_ID
    assert body["current_stage"] == "COMPLETED"
    assert body["package_filename"] == "input/package.zip"
    stage_names = [s["stage"] for s in body["stages"]]
    assert stage_names == [
        "RECEIVED",
        "CANDIDATES_DETECTED",
        "CONTEXTS_BUILT",
        "GUARDRAILS_APPLIED",
        "COMPLETED",
    ]
    assert all(s["status"] == "SUCCEEDED" for s in body["stages"])


def test_get_run_not_found(client: TestClient) -> None:
    response = client.get("/api/runs/20260101T000000000000-ffffffff")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "run_not_found"


def test_get_run_invalid_run_id_format(client: TestClient) -> None:
    response = client.get("/api/runs/not-a-valid-run-id")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_identifier"


# --- GET /api/runs/{run_id}/candidates ---


def test_get_candidates(client: TestClient, settings: Settings) -> None:
    build_run_up_to_candidates_detected(settings.runs_dir / RUN_ID, RUN_ID)

    response = client.get(f"/api/runs/{RUN_ID}/candidates")
    assert response.status_code == 200
    body = response.json()
    assert len(body["candidates"]) == 1
    candidate = body["candidates"][0]
    assert candidate["candidate_id"] == CANDIDATE_ID
    assert candidate["condition"] == "WS-COD = 'R001'"
    assert candidate["outcome_code"] == "R001"


def test_get_candidates_stage_not_reached(client: TestClient, settings: Settings) -> None:
    run_dir = settings.runs_dir / RUN_ID
    received_only = build_run_state(
        RUN_ID,
        stages=[stage_execution(PipelineStage.RECEIVED, StageStatus.SUCCEEDED)],
        current_stage=PipelineStage.RECEIVED,
    )
    write_run_state(run_dir, received_only)

    response = client.get(f"/api/runs/{RUN_ID}/candidates")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "stage_not_reached"


def test_get_candidates_run_not_found(client: TestClient) -> None:
    response = client.get("/api/runs/20260101T000000000000-ffffffff/candidates")
    assert response.status_code == 404


def test_get_candidates_manifest_corrupted(client: TestClient, settings: Settings) -> None:
    run_dir = settings.runs_dir / RUN_ID
    build_run_up_to_candidates_detected(run_dir, RUN_ID)
    (run_dir / "artifacts" / "06-candidates.json").write_text("{not json", encoding="utf-8")

    response = client.get(f"/api/runs/{RUN_ID}/candidates")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "artifact_corrupted"


def test_get_candidates_source_package_hash_mismatch(
    client: TestClient, settings: Settings
) -> None:
    run_dir = settings.runs_dir / RUN_ID
    build_run_up_to_candidates_detected(run_dir, RUN_ID)
    path = run_dir / "artifacts" / "06-candidates.json"
    artifact = CandidateArtifact.model_validate_json(path.read_text(encoding="utf-8"))
    other_hash = "b" * 64
    tampered_candidates = [
        c.model_copy(update={"source_package_hash": other_hash}) for c in artifact.candidates
    ]
    tampered = artifact.model_copy(
        update={"source_package_hash": other_hash, "candidates": tampered_candidates}
    )
    path.write_text(tampered.to_stable_json(), encoding="utf-8")

    response = client.get(f"/api/runs/{RUN_ID}/candidates")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "artifact_corrupted"


# --- GET .../context ---


def test_get_context(client: TestClient, settings: Settings) -> None:
    build_run_up_to_contexts_built(settings.runs_dir / RUN_ID, RUN_ID)

    response = client.get(f"/api/runs/{RUN_ID}/candidates/{CANDIDATE_ID}/context")
    assert response.status_code == 200
    body = response.json()
    assert body["candidate"]["candidate_id"] == CANDIDATE_ID
    assert body["decision"]["expression"] == "WS-COD = 'R001'"


def test_get_context_candidate_not_found(client: TestClient, settings: Settings) -> None:
    build_run_up_to_contexts_built(settings.runs_dir / RUN_ID, RUN_ID)

    response = client.get(f"/api/runs/{RUN_ID}/candidates/candidate-inexistente/context")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "candidate_not_found"


def test_get_context_stage_not_reached(client: TestClient, settings: Settings) -> None:
    build_run_up_to_candidates_detected(settings.runs_dir / RUN_ID, RUN_ID)

    response = client.get(f"/api/runs/{RUN_ID}/candidates/{CANDIDATE_ID}/context")
    assert response.status_code == 409


def test_get_context_hash_mismatch(client: TestClient, settings: Settings) -> None:
    run_dir = settings.runs_dir / RUN_ID
    build_run_up_to_contexts_built(run_dir, RUN_ID)
    context_dir = run_dir / "artifacts" / "07-context"
    filename = hashlib.sha256(CANDIDATE_ID.encode("utf-8")).hexdigest() + ".json"
    tampered = (context_dir / filename).read_text(encoding="utf-8").replace("MAIN", "OTHER")
    (context_dir / filename).write_text(tampered, encoding="utf-8")

    response = client.get(f"/api/runs/{RUN_ID}/candidates/{CANDIDATE_ID}/context")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "artifact_corrupted"


def test_get_context_internal_candidate_id_mismatch(client: TestClient, settings: Settings) -> None:
    run_dir = settings.runs_dir / RUN_ID
    build_run_up_to_contexts_built(run_dir, RUN_ID)
    context_dir = run_dir / "artifacts" / "07-context"
    filename = hashlib.sha256(CANDIDATE_ID.encode("utf-8")).hexdigest() + ".json"
    package = ContextPackage.model_validate_json(
        (context_dir / filename).read_text(encoding="utf-8")
    )
    tampered_candidate = package.candidate.model_copy(update={"candidate_id": "otro-candidato"})
    tampered = package.model_copy(update={"candidate": tampered_candidate})
    (context_dir / filename).write_text(tampered.to_stable_json(), encoding="utf-8")

    # El manifest sigue apuntando a este archivo por CANDIDATE_ID (el
    # lookup nunca deriva el filename del contenido), pero el
    # candidate_id INTERNO del ContextPackage ya no coincide.
    response = client.get(f"/api/runs/{RUN_ID}/candidates/{CANDIDATE_ID}/context")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "artifact_corrupted"


# --- GET .../rule ---


def test_get_rule_combined_response(client: TestClient, settings: Settings) -> None:
    build_run_up_to_guardrails_applied(settings.runs_dir / RUN_ID, RUN_ID)

    response = client.get(f"/api/runs/{RUN_ID}/candidates/{CANDIDATE_ID}/rule")
    assert response.status_code == 200
    body = response.json()
    assert body["candidate_id"] == CANDIDATE_ID
    assert "final_rule_draft" in body
    assert "guardrail" in body
    assert body["final_rule_draft"]["evidence_validation_status"] == "EVIDENCE_VALIDATED"
    assert body["final_rule_draft"]["functional_review_status"] == "NEEDS_FUNCTIONAL_REVIEW"
    assert body["guardrail"]["verdict"] == "EVIDENCE_VALIDATED"


def test_get_rule_violations_projected(client: TestClient, settings: Settings) -> None:
    violation = GuardrailViolation(
        violation_id="unsupported_explicit_number::c1::5",
        rule="unsupported_explicit_number",
        field="condition",
        message="el numero '5' no aparece en la evidencia citada",
        severity=Severity.WARNING,
    )
    build_run_up_to_guardrails_applied(settings.runs_dir / RUN_ID, RUN_ID, violations=[violation])

    response = client.get(f"/api/runs/{RUN_ID}/candidates/{CANDIDATE_ID}/rule")
    assert response.status_code == 200
    body = response.json()["guardrail"]["violations"]
    assert len(body) == 1
    assert body[0] == {
        "violation_id": "unsupported_explicit_number::c1::5",
        "rule": "unsupported_explicit_number",
        "field": "condition",
        "message": "el numero '5' no aparece en la evidencia citada",
        "severity": "WARNING",
    }


def test_get_rule_repair_attempts_used(client: TestClient, settings: Settings) -> None:
    build_run_up_to_guardrails_applied(settings.runs_dir / RUN_ID, RUN_ID, repair_attempts=0)

    response = client.get(f"/api/runs/{RUN_ID}/candidates/{CANDIDATE_ID}/rule")
    assert response.json()["guardrail"]["repair_attempts_used"] == 0


def test_get_rule_never_exposes_internal_provenance(client: TestClient, settings: Settings) -> None:
    build_run_up_to_guardrails_applied(settings.runs_dir / RUN_ID, RUN_ID)

    response = client.get(f"/api/runs/{RUN_ID}/candidates/{CANDIDATE_ID}/rule")
    raw = response.text
    for forbidden in (
        "repair_history",
        "response_hash",
        "produced_rule_draft_hash",
        "failure_summary",
        "failure_code",
        "provider",
        "\"model\"",
        "context_hash",
        "source_package_hash",
        "initial_rule_draft_hash",
    ):
        assert forbidden not in raw, f"{forbidden!r} no deberia aparecer en /rule"


def test_get_rule_stage_not_reached(client: TestClient, settings: Settings) -> None:
    build_run_up_to_contexts_built(settings.runs_dir / RUN_ID, RUN_ID)

    response = client.get(f"/api/runs/{RUN_ID}/candidates/{CANDIDATE_ID}/rule")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "stage_not_reached"


def test_get_rule_candidate_not_found(client: TestClient, settings: Settings) -> None:
    build_run_up_to_guardrails_applied(settings.runs_dir / RUN_ID, RUN_ID)

    response = client.get(f"/api/runs/{RUN_ID}/candidates/candidate-inexistente/rule")
    assert response.status_code == 404


def test_get_rule_hash_mismatch(client: TestClient, settings: Settings) -> None:
    run_dir = settings.runs_dir / RUN_ID
    build_run_up_to_guardrails_applied(run_dir, RUN_ID)
    tampered = build_guardrail_artifact(final_draft=build_final_draft(title="Tampered"))
    filename = hashlib.sha256(CANDIDATE_ID.encode("utf-8")).hexdigest() + ".json"
    (run_dir / "artifacts" / "09-guardrails" / filename).write_text(
        tampered.to_stable_json(), encoding="utf-8"
    )

    response = client.get(f"/api/runs/{RUN_ID}/candidates/{CANDIDATE_ID}/rule")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "artifact_corrupted"


def test_get_rule_does_not_read_08_rule_drafts(client: TestClient, settings: Settings) -> None:
    run_dir = settings.runs_dir / RUN_ID
    build_run_up_to_guardrails_applied(run_dir, RUN_ID)
    # Ni siquiera existe artifacts/08-rule-drafts/: si el endpoint
    # intentara leerlo, fallaria con 500 por ausencia -- en cambio debe
    # responder 200 sin tocarlo en absoluto.
    assert not (run_dir / "artifacts" / "08-rule-drafts").exists()

    response = client.get(f"/api/runs/{RUN_ID}/candidates/{CANDIDATE_ID}/rule")
    assert response.status_code == 200
    assert not (run_dir / "artifacts" / "08-rule-drafts").exists()
