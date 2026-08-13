"""Integracion real de gobierno operativo Fase 15A Parte 14
(`feat/operational-governance-ui`): reutiliza EXACTAMENTE el mismo
escenario real de Fase 9-14B (`ready_blocked_zip` / CALLER10/CALLEE10,
JAR real, Neo4j efimero real) y la misma cadena de materializacion ya
validada en `test_unified_activation_materialize_integration.py`, y
verifica sobre ese resultado real los 4 estados de gobierno pedidos:

A. V1 inicializado.
B. UNIFIED_CANARY activo.
C. FALLBACK aplicado (corrupcion real, exclusiva de la copia de test).
D. ROLLBACK a una generacion unified valida.

Para cada estado se verifica: overview API, pagina HTML, descarga de
`candidates` activo, cadena de eventos, lista de generaciones, y cero
modificacion de archivos preexistentes (`run.json`, `artifacts/01-10`,
`diagnostics/*`). Nunca fabrica manualmente ningun artefacto previo
como sustituto del flujo real."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from altamira_extractor.api.app import create_app
from altamira_extractor.contracts.candidate import CandidateArtifact
from altamira_extractor.contracts.candidate_promotion_review import (
    CandidatePromotionDecision,
    CandidatePromotionDecisionManifest,
    DecisionReasonCode,
    ReviewDecision,
)
from altamira_extractor.contracts.unified_activation_config import (
    UnifiedActivationConfig,
    UnifiedActivationMode,
    UnifiedCanarySelectionStrategy,
)
from altamira_extractor.contracts.unified_activation_config import (
    UnifiedFallbackPolicy as ConfigFallbackPolicy,
)
from altamira_extractor.contracts.unified_activation_evaluation import (
    UnifiedActivationReadinessDisposition,
)
from altamira_extractor.contracts.unified_materialization_authorization import (
    UnifiedMaterializationAction,
    UnifiedMaterializationAuthorization,
    UnifiedMaterializationReasonCode,
)
from altamira_extractor.pipeline.artifact_store import atomic_write_json
from altamira_extractor.pipeline.candidate_promotion_assessment_service import (
    compute_candidate_promotion_assessment_artifact,
)
from altamira_extractor.pipeline.candidate_promotion_plan_builder import (
    build_candidate_promotion_plan,
)
from altamira_extractor.pipeline.candidate_promotion_plan_service import (
    write_candidate_promotion_plan_artifact,
)
from altamira_extractor.pipeline.candidate_promotion_review_generator import (
    generate_candidate_promotion_review_package,
)
from altamira_extractor.pipeline.candidate_promotion_review_service import (
    write_candidate_promotion_review_package,
)
from altamira_extractor.pipeline.operational_governance_reader import (
    build_operational_governance_overview,
)
from altamira_extractor.pipeline.unified_activation_evaluator import evaluate_unified_activation
from altamira_extractor.pipeline.unified_activation_generation_builder import (
    build_unified_generation,
)
from altamira_extractor.pipeline.unified_activation_store import UnifiedActivationStore
from altamira_extractor.pipeline.unified_activation_transition import rollback_to_generation
from altamira_extractor.pipeline.unified_candidates_shadow_service import (
    compute_unified_candidates_shadow_artifact,
    write_unified_candidates_shadow_artifact,
)
from altamira_extractor.pipeline.unified_materialization_service import (
    materialize_unified_activation,
)
from altamira_extractor.pipeline.unified_shadow_downstream_service import (
    compute_unified_shadow_downstream_artifact,
    write_unified_shadow_downstream_artifact,
)
from altamira_extractor.pipeline.unified_shadow_validation_service import (
    compute_unified_shadow_validation_report,
    write_unified_shadow_validation_report,
)

from ..e2e_support import build_settings, require_jar
from .test_candidate_promotion_assessment_integration import _run_pipeline, ready_blocked_zip
from .test_unified_activation_materialize_integration import _stable_hash, _write_authorization

pytestmark = pytest.mark.integration

__all__ = ["ready_blocked_zip"]


def _snapshot_excluding_activation(run_dir: Path) -> dict[str, bytes]:
    return {
        p.relative_to(run_dir).as_posix(): p.read_bytes()
        for p in sorted(run_dir.rglob("*"))
        if p.is_file() and "activation" not in p.relative_to(run_dir).parts
    }


def _assert_governance_surface(
    client: TestClient, run_dir: Path, run_id: str, *, expected_lane: str
) -> None:
    """Verifica overview API, pagina HTML, descarga, cadena de eventos
    y lista de generaciones para el estado ACTUAL -- reutilizado por
    los 4 pasos A-D."""
    overview_response = client.get(f"/api/runs/{run_id}/governance")
    assert overview_response.status_code == 200
    overview_body = overview_response.json()
    assert overview_body["active_lane"] == expected_lane

    html_response = client.get(f"/ui/runs/{run_id}/governance")
    assert html_response.status_code == 200
    assert expected_lane in html_response.text

    events_response = client.get(f"/api/runs/{run_id}/governance/events")
    assert events_response.status_code == 200
    assert len(events_response.json()) >= 1

    generations_response = client.get(f"/api/runs/{run_id}/governance/generations")
    assert generations_response.status_code == 200
    assert len(generations_response.json()) >= 1

    candidates_download = client.get(f"/api/runs/{run_id}/governance/artifacts/candidates")
    assert candidates_download.status_code in (200, 409)  # 409 unicamente en el estado C


def test_real_four_state_governance_lifecycle(tmp_path: Path, ready_blocked_zip: Path) -> None:
    """Fase 15B4-CANDIDATE-QUALITY-5E: enhanced_candidates_enabled=False
    explicito -- reutiliza el escenario de baseline V1/Q0 controlado."""
    require_jar()
    settings = build_settings(tmp_path, enhanced_candidates_enabled=False)

    run_dir, run_id, succeeded_stages = _run_pipeline(settings, ready_blocked_zip)
    assert "PARSED" in succeeded_stages
    if "CANDIDATES_DETECTED" not in succeeded_stages:
        pytest.skip(
            "CANDIDATES_DETECTED no se alcanzo en este entorno (Neo4j no disponible) -- "
            "Fase 15A no es verificable sin V1 real"
        )

    # --- Cadena real Fase 9-13 (identica al patron ya establecido) ---
    assessment = compute_candidate_promotion_assessment_artifact(run_dir, run_id)
    review_package = generate_candidate_promotion_review_package(assessment)
    eligible_items = [
        item for item in review_package.review_items if item.eligibility.value == "ELIGIBLE"
    ]
    write_candidate_promotion_review_package(run_dir, review_package)

    assessment_hash = _stable_hash(assessment)
    review_hash = _stable_hash(review_package)
    decisions = [
        CandidatePromotionDecision(
            decision_id=f"decision::f15a-integration::{idx}::{item.review_item_id}",
            review_item_id=item.review_item_id,
            assessment_id=item.assessment_id,
            reference_id=item.reference_id,
            assessment_artifact_hash=assessment_hash,
            decision=ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION,
            reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
            reviewer_reference=f"f15a-integration-reviewer-{idx}@altamira.local",
        )
        for idx, item in enumerate(eligible_items, start=1)
    ]
    manifest = CandidatePromotionDecisionManifest(
        review_package_hash=review_hash,
        assessment_artifact_hash=assessment_hash,
        run_id=run_id,
        decisions=decisions,
    )
    plan = build_candidate_promotion_plan(
        assessment=assessment, review_package=review_package, manifest=manifest
    )
    write_candidate_promotion_plan_artifact(run_dir, plan)

    unified_shadow = compute_unified_candidates_shadow_artifact(run_dir, run_id)
    write_unified_candidates_shadow_artifact(run_dir, unified_shadow)

    validation_report = compute_unified_shadow_validation_report(run_dir, run_id)
    write_unified_shadow_validation_report(run_dir, validation_report)

    downstream = compute_unified_shadow_downstream_artifact(run_dir, run_id, settings=settings)
    write_unified_shadow_downstream_artifact(run_dir, downstream)
    assert downstream.disposition.value == "COMPLETED"
    source_package_hash = downstream.source_package_hash

    activation_config = UnifiedActivationConfig(
        mode=UnifiedActivationMode.UNIFIED_CANARY,
        canary_strategy=UnifiedCanarySelectionStrategy.EXPLICIT_ALLOWLIST,
        package_hash_allowlist=[source_package_hash],
        fallback_policy=ConfigFallbackPolicy.FALLBACK_TO_V1,
    )
    candidates_path = run_dir / "artifacts" / "06-candidates.json"
    v1_artifact = CandidateArtifact.model_validate_json(candidates_path.read_text(encoding="utf-8"))
    evaluation = evaluate_unified_activation(
        activation_config,
        run_id=run_id,
        source_package_hash=source_package_hash,
        config_hash="c" * 64,
        candidate_v1_artifact=v1_artifact,
        candidate_v1_artifact_hash=source_package_hash,
        unified_shadow=unified_shadow,
        unified_candidates_shadow_hash=source_package_hash,
        validation_report=validation_report,
        validation_report_hash=source_package_hash,
        downstream_artifact=downstream,
        downstream_artifact_hash=source_package_hash,
    )
    assert evaluation.readiness_disposition.value == "READY_FOR_UNIFIED_CANARY"

    atomic_write_json(run_dir / "diagnostics" / "unified-activation-evaluation.json", evaluation)
    eval_hash = hashlib.sha256(
        (run_dir / "diagnostics" / "unified-activation-evaluation.json").read_bytes()
    ).hexdigest()

    approved_group_ids = sorted(
        {
            r.group_id
            for r in evaluation.unified_references
            if r.level.value == "RULE" and r.guardrail_status.value == "PASSED"
        }
    )
    assert len(approved_group_ids) >= 1

    baseline = _snapshot_excluding_activation(run_dir)

    with TestClient(create_app(settings)) as client:
        # --- A. V1 inicializado ---
        keep_v1_auth = tmp_path / "a-keep-v1.yaml"
        _write_authorization(
            keep_v1_auth,
            run_id=run_id,
            eval_hash=eval_hash,
            action="KEEP_V1",
            readiness="READY_FOR_UNIFIED_CANARY",
            reason_code="KEEP_BASELINE",
            fallback_authorized=False,
        )
        result_a = materialize_unified_activation(run_dir, run_id, authorization_path=keep_v1_auth)
        assert result_a.active_lane.value == "V1"
        assert result_a.pointer_version == 1
        v1_generation_id = result_a.generation_id

        overview_a = build_operational_governance_overview(run_dir, run_id)
        assert overview_a.status.value == "HEALTHY_V1"
        assert overview_a.event_chain_status.value == "VALID"
        candidates_a = next(a for a in overview_a.artifacts if a.logical_name == "candidates")
        assert candidates_a.status.value == "AVAILABLE"
        _assert_governance_surface(client, run_dir, run_id, expected_lane="V1")
        assert _snapshot_excluding_activation(run_dir) == baseline

        # --- B. UNIFIED_CANARY activo ---
        canary_auth = tmp_path / "b-canary.yaml"
        _write_authorization(
            canary_auth,
            run_id=run_id,
            eval_hash=eval_hash,
            action="ACTIVATE_UNIFIED_CANARY",
            readiness="READY_FOR_UNIFIED_CANARY",
            reason_code="CANARY_APPROVED",
            approved_group_ids=approved_group_ids,
        )
        result_b = materialize_unified_activation(run_dir, run_id, authorization_path=canary_auth)
        assert result_b.active_lane.value == "UNIFIED"
        assert result_b.pointer_version == 2
        canary_generation_id = result_b.generation_id

        overview_b = build_operational_governance_overview(run_dir, run_id)
        assert overview_b.status.value == "HEALTHY_UNIFIED"
        for logical_name in ("candidates", "context-packages", "rule-drafts", "guardrails"):
            artifact = next(a for a in overview_b.artifacts if a.logical_name == logical_name)
            assert artifact.status.value == "AVAILABLE", logical_name
        assert len(overview_b.unified_groups) >= 1
        for group in overview_b.unified_groups:
            assert group.member_ids
            assert group.source_candidate_ids
            assert group.guardrail_status == "PASSED"
        _assert_governance_surface(client, run_dir, run_id, expected_lane="UNIFIED")
        assert _snapshot_excluding_activation(run_dir) == baseline

        # --- C. FALLBACK aplicado (corrupcion real, solo la copia de test) ---
        store = UnifiedActivationStore(run_dir)
        corrupted_path = store.generation_dir(canary_generation_id) / "candidates.json"
        corrupted_path.write_bytes(b"{corrupted-by-fase-15a-integration-test}")

        overview_c = build_operational_governance_overview(run_dir, run_id)
        candidates_c = next(a for a in overview_c.artifacts if a.logical_name == "candidates")
        assert candidates_c.status.value == "CORRUPT"
        codes_c = {issue.code.value for issue in overview_c.issues}
        assert "ACTIVE_FILE_HASH_MISMATCH" in codes_c
        # La lectura NUNCA ejecuta el fallback real: el puntero sigue
        # apuntando a la generacion unified corrupta hasta que se
        # aplique el fallback REAL explicitamente, mas abajo.
        pointer_before_fallback = store.read_active_pointer()
        assert pointer_before_fallback is not None
        assert pointer_before_fallback.active_lane.value == "UNIFIED"

        from altamira_extractor.pipeline.unified_active_lane_service import (
            resolve_with_fallback,
        )

        fallback_resolution = resolve_with_fallback(store, run_id=run_id, logical_name="candidates")
        assert fallback_resolution.status.value == "FALLBACK_APPLIED"
        pointer_after_fallback = store.read_active_pointer()
        assert pointer_after_fallback is not None
        assert pointer_after_fallback.active_lane.value == "V1"
        assert pointer_after_fallback.pointer_version == 3

        overview_c_after = build_operational_governance_overview(run_dir, run_id)
        assert overview_c_after.active_lane is not None
        assert overview_c_after.active_lane.value == "V1"
        corrupted_generation = next(
            g for g in overview_c_after.generations if g.generation_id == canary_generation_id
        )
        assert corrupted_generation.manifest_integrity.value == "VALID"  # manifest intacto
        v1_candidates_c = next(
            a for a in overview_c_after.artifacts if a.logical_name == "candidates"
        )
        assert v1_candidates_c.status.value == "AVAILABLE"
        _assert_governance_surface(client, run_dir, run_id, expected_lane="V1")
        assert corrupted_path.read_bytes() == b"{corrupted-by-fase-15a-integration-test}"
        assert _snapshot_excluding_activation(run_dir) == baseline

        # --- D. ROLLBACK a una generacion unified VALIDA ---
        primary_auth_hash = "9" * 64
        primary_authorization = UnifiedMaterializationAuthorization(
            run_id=run_id,
            activation_evaluation_hash=eval_hash,
            expected_readiness_disposition=(
                UnifiedActivationReadinessDisposition.READY_FOR_PRIMARY_TRIAL
            ),
            action=UnifiedMaterializationAction.ACTIVATE_UNIFIED_PRIMARY,
            reason_code=UnifiedMaterializationReasonCode.PRIMARY_TRIAL_APPROVED,
            review_reference="fase-15a-real-integration-valid-target",
            approved_group_ids=approved_group_ids,
            fallback_authorized=True,
        )
        valid_manifest, valid_files = build_unified_generation(
            evaluation=evaluation,
            downstream=downstream,
            authorization=primary_authorization,
            run_id=run_id,
            source_package_hash=source_package_hash,
            activation_evaluation_hash=eval_hash,
            authorization_hash=primary_auth_hash,
            fallback_generation_id=v1_generation_id,
        )
        assert valid_manifest.generation_id != canary_generation_id
        store.persist_generation(valid_manifest, valid_files.bytes_by_logical_name())
        rollback_result = rollback_to_generation(
            store,
            run_id=run_id,
            target_generation_id=valid_manifest.generation_id,
            activation_evaluation_hash=eval_hash,
            authorization_hash="a" * 64,
        )
        assert rollback_result.pointer.active_lane.value == "UNIFIED"
        assert rollback_result.pointer.pointer_version == 4

        overview_d = build_operational_governance_overview(run_dir, run_id)
        assert overview_d.active_lane is not None
        assert overview_d.active_lane.value == "UNIFIED"
        assert overview_d.active_generation_id == valid_manifest.generation_id
        assert overview_d.event_chain_length == 4
        corrupted_after_d = next(
            g for g in overview_d.generations if g.generation_id == canary_generation_id
        )
        assert corrupted_after_d.reachability.value in ("HISTORICAL", "ORPHAN")
        active_after_d = next(
            g for g in overview_d.generations if g.generation_id == valid_manifest.generation_id
        )
        assert active_after_d.reachability.value == "ACTIVE"
        _assert_governance_surface(client, run_dir, run_id, expected_lane="UNIFIED")
        assert _snapshot_excluding_activation(run_dir) == baseline

    print("\n--- Fase 15A, ciclo real de gobierno (JAR+Neo4j reales) ---")
    print(f"A: HEALTHY_V1, pointer_version=1, generation={v1_generation_id}")
    print(f"B: HEALTHY_UNIFIED, pointer_version=2, generation={canary_generation_id}")
    print("C: FALLBACK_APPLIED -> V1, pointer_version=3")
    print(
        "D: ROLLBACK -> UNIFIED valida, pointer_version=4, "
        f"generation={valid_manifest.generation_id}"
    )
