"""Integracion real Fase 14B (`feat/controlled-unified-materialization`):
reutiliza EXACTAMENTE el mismo escenario real de Fase 9-14A
(`test_unified_activation_evaluate_integration.py`, fixture
`ready_blocked_zip` / CALLER10/CALLEE10/STOPPER10) -- JAR real, Neo4j
efimero real -- para construir V1, unified shadow (Fase 11), validacion
diferencial (Fase 12), ejecucion downstream (Fase 13) y evaluacion de
activacion (Fase 14A) 100% reales, y ejecutar sobre ese resultado los
seis pasos de materializacion controlada de Fase 14B Parte 18:

A. Inicializacion V1.
B. Activacion canary unified (autorizacion explicita).
C. Idempotencia (repetir la autorizacion).
D. Fallback real (corrupcion exclusiva de la copia de test).
E. Rollback a una generacion unified valida (kind=UNIFIED_PRIMARY,
   mismos grupos aprobados -- una generacion DISTINTA y valida, nunca
   la corrupta).
F. Rollback a V1.

Nunca fabrica manualmente ningun artefacto previo como sustituto del
flujo real."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

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
from altamira_extractor.pipeline.active_artifact_resolver import ActiveArtifactResolver
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
from altamira_extractor.pipeline.unified_activation_evaluator import evaluate_unified_activation
from altamira_extractor.pipeline.unified_activation_generation_builder import (
    build_unified_generation,
)
from altamira_extractor.pipeline.unified_activation_transition import (
    rollback_to_generation,
    rollback_to_previous,
)
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
from altamira_extractor.pipeline.v1_activation_generation_builder import (
    build_v1_generation_manifest,
)

from ..e2e_support import build_settings, require_jar
from .test_candidate_promotion_assessment_integration import _run_pipeline, ready_blocked_zip

pytestmark = pytest.mark.integration

__all__ = ["ready_blocked_zip"]


def _stable_hash(obj: object) -> str:
    return hashlib.sha256(obj.to_stable_json().encode("utf-8")).hexdigest()  # type: ignore[attr-defined]


def _write_authorization(
    path: Path,
    *,
    run_id: str,
    eval_hash: str,
    action: str,
    readiness: str,
    reason_code: str,
    approved_group_ids: list[str] | None = None,
    fallback_authorized: bool = True,
    rollback_authorized: bool = False,
    target_generation_id: str | None = None,
) -> None:
    approved = approved_group_ids or []
    lines = [
        'schema_version: "1.0"',
        f"run_id: {run_id!r}",
        f"activation_evaluation_hash: {eval_hash!r}",
        f"expected_readiness_disposition: {readiness}",
        f"action: {action}",
    ]
    if target_generation_id is not None:
        lines.append(f"target_generation_id: {target_generation_id!r}")
    lines.extend(
        [
            f"reason_code: {reason_code}",
            "review_reference: 'fase-14b-real-integration'",
            f"approved_group_ids: {approved!r}",
            f"fallback_authorized: {str(fallback_authorized).lower()}",
            f"rollback_authorized: {str(rollback_authorized).lower()}",
            "provider_policy: DETERMINISTIC_FAKE_ONLY",
            "materialization_enabled: true",
            "diagnostics: []",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_real_six_step_materialization_cycle(tmp_path: Path, ready_blocked_zip: Path) -> None:
    """Fase 15B4-CANDIDATE-QUALITY-5E: enhanced_candidates_enabled=False
    explicito -- reutiliza el escenario de baseline V1/Q0 controlado."""
    require_jar()
    settings = build_settings(tmp_path, enhanced_candidates_enabled=False)

    run_dir, run_id, succeeded_stages = _run_pipeline(settings, ready_blocked_zip)
    assert "PARSED" in succeeded_stages
    if "CANDIDATES_DETECTED" not in succeeded_stages:
        pytest.skip(
            "CANDIDATES_DETECTED no se alcanzo en este entorno (Neo4j no disponible) -- "
            "Fase 14B no es verificable sin V1 real"
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
            decision_id=f"decision::f14b-integration::{idx}::{item.review_item_id}",
            review_item_id=item.review_item_id,
            assessment_id=item.assessment_id,
            reference_id=item.reference_id,
            assessment_artifact_hash=assessment_hash,
            decision=ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION,
            reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
            reviewer_reference=f"f14b-integration-reviewer-{idx}@altamira.local",
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

    transitions_table: list[dict[str, object]] = []

    def _v1_intacto() -> bool:
        return candidates_path.read_bytes() == v1_artifact.to_stable_json().encode("utf-8")

    # --- A. Inicializacion V1 ---
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
    resolver = ActiveArtifactResolver(run_dir, run_id=run_id)
    store = resolver.store
    assert resolver.resolve("candidates").status.value == "RESOLVED"

    def _manifest_hash(generation_id: str) -> str:
        manifest_bytes = (store.generation_dir(generation_id) / "manifest.json").read_bytes()
        return hashlib.sha256(manifest_bytes).hexdigest()

    def _manifest_kind(generation_id: str) -> str:
        return store.read_generation_manifest(generation_id).kind.value

    transitions_table.append(
        {
            "step": "A",
            "action": "INITIALIZE_V1",
            "from": None,
            "to": v1_generation_id,
            "kind": _manifest_kind(v1_generation_id),
            "manifest_hash": _manifest_hash(v1_generation_id),
            "lane": "V1",
            "pointer_version": 1,
            "event": result_a.event_id,
            "manifest_valid": True,
            "fallback_applied": False,
            "v1_intacto": _v1_intacto(),
        }
    )

    # --- B. Activacion canary unified ---
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
    assert result_b.previous_generation_id == v1_generation_id
    assert result_b.fallback_generation_id == v1_generation_id
    assert result_b.pointer_version == 2
    canary_generation_id = result_b.generation_id
    for logical_name in ("candidates", "context-packages", "rule-drafts", "guardrails"):
        assert resolver.resolve(logical_name).status.value == "RESOLVED"
    assert _v1_intacto()
    canary_manifest_hash_at_b = _manifest_hash(canary_generation_id)
    transitions_table.append(
        {
            "step": "B",
            "action": "ACTIVATE_UNIFIED_CANARY",
            "from": v1_generation_id,
            "to": canary_generation_id,
            "kind": _manifest_kind(canary_generation_id),
            "manifest_hash": canary_manifest_hash_at_b,
            "lane": "UNIFIED",
            "pointer_version": 2,
            "event": result_b.event_id,
            "manifest_valid": True,
            "fallback_applied": False,
            "v1_intacto": _v1_intacto(),
        }
    )

    # --- C. Idempotencia ---
    result_c = materialize_unified_activation(run_dir, run_id, authorization_path=canary_auth)
    assert result_c.idempotent is True
    assert result_c.generation_id == canary_generation_id
    assert result_c.pointer_version == 2
    transitions_table.append(
        {
            "step": "C",
            "action": "ACTIVATE_UNIFIED_CANARY (repeat)",
            "from": canary_generation_id,
            "to": canary_generation_id,
            "kind": _manifest_kind(canary_generation_id),
            "manifest_hash": _manifest_hash(canary_generation_id),
            "lane": "UNIFIED",
            "pointer_version": 2,
            "event": result_c.event_id,
            "manifest_valid": True,
            "fallback_applied": False,
            "v1_intacto": _v1_intacto(),
        }
    )
    # manifest.json de la generacion canary nunca cambia entre B y C
    # (misma generacion, ninguna reescritura).
    assert _manifest_hash(canary_generation_id) == canary_manifest_hash_at_b

    # --- D. Fallback real (corrupcion exclusiva de la copia de test) ---
    corrupted_path = store.generation_dir(canary_generation_id) / "candidates.json"
    original_bytes = corrupted_path.read_bytes()
    canary_manifest_hash_before_corruption = _manifest_hash(canary_generation_id)
    corrupted_path.write_bytes(b"{corrupted-by-fase-14b-integration-test}")

    fallback_resolution = resolver.resolve("candidates")
    assert fallback_resolution.status.value == "FALLBACK_APPLIED"
    pointer_after_fallback = store.read_active_pointer()
    assert pointer_after_fallback is not None
    assert pointer_after_fallback.active_lane.value == "V1"
    assert pointer_after_fallback.pointer_version == 3
    assert fallback_resolution.fallback_event_id is not None
    # La generacion unified corrupta se preserva tal cual (nunca se borra):
    # el archivo de datos corrupto sigue siendo el corrupto, y su
    # manifest.json (que nunca referencia el hash real del archivo
    # corrupto, solo el original) tampoco cambio -- el fallback NUNCA
    # reescribe la generacion, solo mueve el puntero activo.
    assert corrupted_path.read_bytes() == b"{corrupted-by-fase-14b-integration-test}"
    assert _manifest_hash(canary_generation_id) == canary_manifest_hash_before_corruption
    corrupted_generation_id = canary_generation_id
    transitions_table.append(
        {
            "step": "D",
            "action": "FALLBACK_TO_V1 (auto)",
            "from": canary_generation_id,
            "to": v1_generation_id,
            "kind": _manifest_kind(v1_generation_id),
            "manifest_hash": _manifest_hash(v1_generation_id),
            "lane": "V1",
            "pointer_version": 3,
            "event": fallback_resolution.fallback_event_id,
            "manifest_valid": False,
            "fallback_applied": True,
            "v1_intacto": _v1_intacto(),
        }
    )

    # --- E. Rollback a una generacion unified VALIDA (kind=UNIFIED_PRIMARY,
    # mismos grupos, contenido identico pero generation_id distinto ya
    # que `kind` participa del hash de identidad -- nunca reutiliza la
    # generacion corrupta) ---
    primary_auth_hash = "9" * 64
    primary_authorization = UnifiedMaterializationAuthorization(
        run_id=run_id,
        activation_evaluation_hash=eval_hash,
        expected_readiness_disposition=UnifiedActivationReadinessDisposition.READY_FOR_PRIMARY_TRIAL,
        action=UnifiedMaterializationAction.ACTIVATE_UNIFIED_PRIMARY,
        reason_code=UnifiedMaterializationReasonCode.PRIMARY_TRIAL_APPROVED,
        review_reference="fase-14b-real-integration-valid-target",
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
    # E nunca reutiliza el generation_id corrupto: es un ID DISTINTO
    # (kind=UNIFIED_PRIMARY, no UNIFIED_CANARY), nunca una reescritura de
    # `corrupted_generation_id` bajo el mismo ID.
    assert valid_manifest.generation_id != canary_generation_id
    assert valid_manifest.generation_id != corrupted_generation_id
    store.persist_generation(valid_manifest, valid_files.bytes_by_logical_name())
    store.validate_generation_files(valid_manifest)
    valid_manifest_hash_at_persist = _manifest_hash(valid_manifest.generation_id)

    rollback_valid_result = rollback_to_generation(
        store,
        run_id=run_id,
        target_generation_id=valid_manifest.generation_id,
        activation_evaluation_hash=eval_hash,
        authorization_hash="a" * 64,
    )
    assert rollback_valid_result.pointer.active_lane.value == "UNIFIED"
    assert rollback_valid_result.pointer.active_generation_id == valid_manifest.generation_id
    # E activa un generation_id DISTINTO del corrupto -- nunca vuelve a
    # apuntar a `corrupted_generation_id`.
    assert rollback_valid_result.pointer.active_generation_id != corrupted_generation_id
    assert rollback_valid_result.pointer.pointer_version == 4
    assert resolver.resolve("candidates").status.value == "RESOLVED"
    assert resolver.resolve("candidates").generation_id == valid_manifest.generation_id
    # La generacion corrupta sigue en disco, sigue corrupta, sin cambios
    # (ni reparada ni eliminada), incluso despues de que E activo otra
    # generacion unified.
    assert corrupted_path.read_bytes() == b"{corrupted-by-fase-14b-integration-test}"
    assert _manifest_hash(corrupted_generation_id) == canary_manifest_hash_before_corruption
    transitions_table.append(
        {
            "step": "E",
            "action": "ROLLBACK_TO_GENERATION (valid unified)",
            "from": v1_generation_id,
            "to": valid_manifest.generation_id,
            "kind": _manifest_kind(valid_manifest.generation_id),
            "manifest_hash": valid_manifest_hash_at_persist,
            "lane": "UNIFIED",
            "pointer_version": 4,
            "event": rollback_valid_result.event.event_id,
            "manifest_valid": True,
            "fallback_applied": False,
            "v1_intacto": _v1_intacto(),
        }
    )

    # --- F. Rollback a V1 ---
    rollback_v1_result = rollback_to_previous(
        store,
        run_id=run_id,
        activation_evaluation_hash=eval_hash,
        authorization_hash="b" * 64,
    )
    assert rollback_v1_result.pointer.active_lane.value == "V1"
    assert rollback_v1_result.pointer.pointer_version == 5
    assert resolver.resolve("candidates").status.value == "RESOLVED"
    transitions_table.append(
        {
            "step": "F",
            "action": "ROLLBACK_TO_PREVIOUS (to V1)",
            "from": valid_manifest.generation_id,
            "to": v1_generation_id,
            "kind": _manifest_kind(v1_generation_id),
            "manifest_hash": _manifest_hash(v1_generation_id),
            "lane": "V1",
            "pointer_version": 5,
            "event": rollback_v1_result.event.event_id,
            "manifest_valid": True,
            "fallback_applied": False,
            "v1_intacto": _v1_intacto(),
        }
    )

    # --- V1 intacto durante TODO el ciclo ---
    assert _v1_intacto()
    assert original_bytes != b"{corrupted-by-fase-14b-integration-test}"

    # --- Inmutabilidad final de la generacion corrupta: al cierre del
    # ciclo completo A-F, el archivo de datos corrupto y el manifest de
    # `corrupted_generation_id` siguen exactamente como quedaron en el
    # paso D -- nunca reparados, nunca sobrescritos, nunca eliminados. ---
    assert corrupted_path.exists()
    assert corrupted_path.read_bytes() == b"{corrupted-by-fase-14b-integration-test}"
    assert _manifest_hash(corrupted_generation_id) == canary_manifest_hash_before_corruption
    assert store.generation_exists(corrupted_generation_id)

    # --- Historial completo: la cadena de eventos desde el ultimo
    # puntero remonta hasta el primer evento (sequence=1). ---
    final_pointer = store.read_active_pointer()
    assert final_pointer is not None
    event = store.read_event(final_pointer.latest_event_id)
    chain = [event]
    while event.previous_event_id is not None:
        event = store.read_event(event.previous_event_id)
        chain.append(event)
    assert chain[-1].sequence == 1
    assert len(chain) == 5

    # --- Determinismo: reconstruir la MISMA generacion V1/unified (por
    # contenido) desde CERO produce identicos generation_id. ---
    v1_manifest_rebuilt = build_v1_generation_manifest(
        run_dir,
        run_id=run_id,
        source_package_hash=source_package_hash,
        activation_evaluation_hash=eval_hash,
        authorization_hash="f" * 64,
    )
    assert v1_manifest_rebuilt.generation_id == v1_generation_id

    # --- Determinismo (lane unified): una segunda construccion
    # independiente, con la MISMA autorizacion de canary y los MISMOS
    # artefactos reales de Fase 9-14A, produce el MISMO generation_id
    # que el materializado en el paso B -- dos "ejecuciones" del
    # constructor sobre el mismo contenido nunca divergen. ---
    canary_authorization = UnifiedMaterializationAuthorization.model_validate(
        {
            "run_id": run_id,
            "activation_evaluation_hash": eval_hash,
            "expected_readiness_disposition": "READY_FOR_UNIFIED_CANARY",
            "action": "ACTIVATE_UNIFIED_CANARY",
            "reason_code": "CANARY_APPROVED",
            "review_reference": "fase-14b-real-integration",
            "approved_group_ids": approved_group_ids,
            "fallback_authorized": True,
            "rollback_authorized": False,
            "provider_policy": "DETERMINISTIC_FAKE_ONLY",
            "materialization_enabled": True,
        }
    )
    canary_manifest_rebuilt, _ = build_unified_generation(
        evaluation=evaluation,
        downstream=downstream,
        authorization=canary_authorization,
        run_id=run_id,
        source_package_hash=source_package_hash,
        activation_evaluation_hash=eval_hash,
        authorization_hash="e" * 64,
        fallback_generation_id=v1_generation_id,
    )
    assert canary_manifest_rebuilt.generation_id == canary_generation_id

    print("\n--- Fase 14B, ciclo real de materializacion (JAR+Neo4j reales) ---")
    header = (
        "step | action | from_generation_id | to_generation_id | generation_kind | "
        "generation_manifest_hash | active_lane | pointer_version | event_id | fallback_applied"
    )
    print(header)
    for row in transitions_table:
        print(
            f"{row['step']} | {row['action']} | {row['from']} | {row['to']} | "
            f"{row['kind']} | {row['manifest_hash']} | {row['lane']} | "
            f"{row['pointer_version']} | {row['event']} | {row['fallback_applied']}"
        )
    print(f"\ncorrupted_generation_id (step D) = {corrupted_generation_id}")
    print(f"valid_generation_id activated (step E) = {valid_manifest.generation_id}")
    print(f"ubicacion logica corrupta: activation/generations/{corrupted_generation_id}/")
    print(f"ubicacion logica valida:   activation/generations/{valid_manifest.generation_id}/")

    v1_deterministic = v1_generation_id == v1_manifest_rebuilt.generation_id
    canary_deterministic = canary_generation_id == canary_manifest_rebuilt.generation_id
    print(
        f"\ngeneration_id determinism: V1={v1_deterministic} UNIFIED_CANARY={canary_deterministic}"
    )
