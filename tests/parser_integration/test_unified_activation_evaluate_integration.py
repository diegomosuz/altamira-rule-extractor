"""Integracion real Fase 14A (`feat/controlled-unified-activation`):
reutiliza EXACTAMENTE el mismo escenario real de Fase 9-13
(`test_unified_shadow_downstream_integration.py`, fixture
`ready_blocked_zip` / CALLER10/CALLEE10/STOPPER10) -- JAR real, Neo4j
efimero real -- para construir V1 (candidatos estructurales reales,
Q0), unified shadow (Fase 11), validacion diferencial (Fase 12) y
ejecucion downstream shadow (Fase 13) 100% reales, y evaluar sobre
ellos las tres configuraciones reales del control plane de activacion
unificada (Fase 14A Parte 12): A. V1_ONLY, B. SHADOW_COMPARE,
C. UNIFIED_CANARY (allowlist explicita, sin denylist) y D. UNIFIED_
CANARY con el MISMO `source_package_hash` real declarado
simultaneamente en allowlist y denylist (precedencia real de la
denylist, cierre solicitado). Nunca fabrica manualmente ningun
artefacto previo como sustituto del flujo real -- ni siquiera el YAML
de configuracion se copia al repositorio o al run (se escribe en
`tmp_path`, exactamente como lo haria un operador via `--config`).

Nota de entorno: en un sandbox sin egress de red, la etapa V1 nativa
`GUARDRAILS_APPLIED` (que si intenta invocar un proveedor LLM real,
configurado via `LLM_PROVIDER=openai` en `build_settings`) nunca llega
a completarse -- exactamente igual que en Fase 9-13, donde solo se
exige `CANDIDATES_DETECTED`. Esto es intencional y no bloquea esta
prueba: el control plane de activacion unificada UNICAMENTE necesita
`CandidateArtifact` V1 (nivel CANDIDATE), nunca `GUARDRAILS_APPLIED`."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from altamira_extractor.contracts.candidate_promotion_review import (
    CandidatePromotionDecision,
    CandidatePromotionDecisionManifest,
    DecisionReasonCode,
    ReviewDecision,
)
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
from altamira_extractor.pipeline.unified_activation_service import (
    compute_unified_activation_evaluation,
    write_unified_activation_evaluation,
)
from altamira_extractor.pipeline.unified_candidates_shadow_service import (
    compute_unified_candidates_shadow_artifact,
    write_unified_candidates_shadow_artifact,
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

pytestmark = pytest.mark.integration

# Reexportado para que pytest descubra el fixture importado (mismo
# patron que `test_unified_shadow_downstream_integration.py`).
__all__ = ["ready_blocked_zip"]


def _stable_hash(obj: object) -> str:
    return hashlib.sha256(obj.to_stable_json().encode("utf-8")).hexdigest()  # type: ignore[attr-defined]


def _write_config(path: Path, *, mode: str, extra: str = "") -> None:
    """Escribe un YAML de `UnifiedActivationConfig` EXTERNO en
    `tmp_path` -- nunca en el repositorio ni en el directorio del run,
    exactamente como el operador lo haria via `--config`."""
    path.write_text(f"mode: {mode}\n{extra}", encoding="utf-8")


def test_real_three_modes_over_caller10_callee10_scenario(
    tmp_path: Path, ready_blocked_zip: Path
) -> None:
    """Flujo completo real Fase 9 -> 10 -> 11 -> 12 -> 13 -> 14A: un
    unico `shadow_group` VALID/NOT_IN_BASELINE, guardrail PASSED,
    downstream COMPLETED (identico a Fase 13) -- y sobre ese resultado
    real se evaluan las cuatro configuraciones A/B/C/D del control
    plane de activacion unificada, nunca fabricadas a mano.

    Fase 15B4-CANDIDATE-QUALITY-5E: enhanced_candidates_enabled=False
    explicito -- reutiliza el escenario de baseline V1/Q0 controlado."""
    require_jar()
    settings = build_settings(tmp_path, enhanced_candidates_enabled=False)

    run_dir, run_id, succeeded_stages = _run_pipeline(settings, ready_blocked_zip)
    assert "PARSED" in succeeded_stages
    if "CANDIDATES_DETECTED" not in succeeded_stages:
        pytest.skip(
            "CANDIDATES_DETECTED no se alcanzo en este entorno (Neo4j no disponible) -- "
            "el control plane de activacion unificada Fase 14A no es verificable sin V1 "
            "real"
        )

    # -----------------------------------------------------------------
    # Modo A. V1_ONLY -- evaluado EN AISLAMIENTO, antes de construir
    # ningun artefacto unified (Fase 11-13): demuestra que V1_ONLY
    # nunca exige que existan artefactos unified.
    # -----------------------------------------------------------------
    config_a_path = tmp_path / "config-a-v1-only.yaml"
    _write_config(config_a_path, mode="V1_ONLY")
    evaluation_a = compute_unified_activation_evaluation(run_dir, run_id, config_path=config_a_path)
    write_unified_activation_evaluation(run_dir, evaluation_a)

    assert evaluation_a.mode.value == "V1_ONLY"
    assert evaluation_a.effective_lane.value == "V1"
    assert evaluation_a.readiness_disposition.value == "V1_ONLY_READY"
    assert evaluation_a.activation_decision.value == "KEEP_V1"
    assert evaluation_a.materialization_enabled is False
    assert evaluation_a.summary.unified_reference_count == 0

    # --- Assessment real (Fase 9) ---
    assessment = compute_candidate_promotion_assessment_artifact(run_dir, run_id)
    assert assessment.summary.ready_for_controlled_review_count == 2

    # --- Review package real (Fase 10) ---
    review_package = generate_candidate_promotion_review_package(assessment)
    eligible_items = [
        item for item in review_package.review_items if item.eligibility.value == "ELIGIBLE"
    ]
    assert len(eligible_items) == 2
    write_candidate_promotion_review_package(run_dir, review_package)

    # --- Manifiesto sintetico valido: dos decisiones humanas DISTINTAS ---
    assessment_hash = _stable_hash(assessment)
    review_hash = _stable_hash(review_package)
    decisions = [
        CandidatePromotionDecision(
            decision_id=f"decision::f14a-integration::{idx}::{item.review_item_id}",
            review_item_id=item.review_item_id,
            assessment_id=item.assessment_id,
            reference_id=item.reference_id,
            assessment_artifact_hash=assessment_hash,
            decision=ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION,
            reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
            reviewer_reference=f"f14a-integration-reviewer-{idx}@altamira.local",
        )
        for idx, item in enumerate(eligible_items, start=1)
    ]
    manifest = CandidatePromotionDecisionManifest(
        review_package_hash=review_hash,
        assessment_artifact_hash=assessment_hash,
        run_id=run_id,
        decisions=decisions,
    )

    # --- Promotion plan real (Fase 10) ---
    plan = build_candidate_promotion_plan(
        assessment=assessment, review_package=review_package, manifest=manifest
    )
    write_candidate_promotion_plan_artifact(run_dir, plan)

    # --- Unified candidates shadow real (Fase 11) ---
    unified_shadow = compute_unified_candidates_shadow_artifact(run_dir, run_id)
    write_unified_candidates_shadow_artifact(run_dir, unified_shadow)
    assert len(unified_shadow.shadow_groups) == 1
    group = unified_shadow.shadow_groups[0]
    assert group.status.value == "VALID"
    assert group.comparison_to_v1.value == "NOT_IN_BASELINE"

    # --- Validacion diferencial real (Fase 12) ---
    report = compute_unified_shadow_validation_report(run_dir, run_id)
    write_unified_shadow_validation_report(run_dir, report)
    assert report.disposition.value == "QUALIFIED_FOR_DOWNSTREAM_SHADOW"

    # --- Ejecucion downstream shadow real (Fase 13) ---
    downstream = compute_unified_shadow_downstream_artifact(run_dir, run_id, settings=settings)
    write_unified_shadow_downstream_artifact(run_dir, downstream)
    assert downstream.disposition.value == "COMPLETED"
    assert downstream.guardrail_results[0].status.value == "PASSED"
    source_package_hash = downstream.source_package_hash

    # -----------------------------------------------------------------
    # Modo B. SHADOW_COMPARE -- V1 permanece primario, se compara
    # contra el downstream unified real; debe identificar claramente
    # un resultado incremental (unified detecto un candidato que Q0/V1
    # nunca vio, ver docstring de `ready_blocked_zip`/CALLER10).
    # -----------------------------------------------------------------
    config_b_path = tmp_path / "config-b-shadow-compare.yaml"
    _write_config(config_b_path, mode="SHADOW_COMPARE")
    evaluation_b = compute_unified_activation_evaluation(run_dir, run_id, config_path=config_b_path)
    write_unified_activation_evaluation(run_dir, evaluation_b)

    assert evaluation_b.mode.value == "SHADOW_COMPARE"
    assert evaluation_b.effective_lane.value == "V1"
    assert evaluation_b.readiness_disposition.value == "READY_FOR_SHADOW_COMPARISON"
    assert evaluation_b.activation_decision.value == "RUN_SHADOW_COMPARISON"
    assert evaluation_b.summary.unified_reference_count >= 1
    assert len(evaluation_b.comparisons) >= 1

    # -----------------------------------------------------------------
    # Modo C. UNIFIED_CANARY -- paquete seleccionado via allowlist
    # explicita del `source_package_hash` real: dry-run unicamente,
    # `effective_lane` y `fallback_lane` SIEMPRE V1.
    # -----------------------------------------------------------------
    config_c_path = tmp_path / "config-c-unified-canary.yaml"
    _write_config(
        config_c_path,
        mode="UNIFIED_CANARY",
        extra=(
            "canary_strategy: EXPLICIT_ALLOWLIST\n"
            f"package_hash_allowlist: [{source_package_hash!r}]\n"
            "fallback_policy: FALLBACK_TO_V1\n"
        ),
    )
    evaluation_c = compute_unified_activation_evaluation(run_dir, run_id, config_path=config_c_path)
    write_unified_activation_evaluation(run_dir, evaluation_c)

    assert evaluation_c.canary_selection is not None
    assert evaluation_c.canary_selection.selected is True
    assert evaluation_c.requested_lane.value == "UNIFIED_SHADOW"
    assert evaluation_c.effective_lane.value == "V1"
    assert evaluation_c.fallback_lane.value == "V1"
    assert evaluation_c.readiness_disposition.value == "READY_FOR_UNIFIED_CANARY"
    assert evaluation_c.activation_decision.value == "SELECT_UNIFIED_CANARY_DRY_RUN"
    assert evaluation_c.materialization_enabled is False

    # Determinismo byte a byte del modo C (recalcular el servicio sobre
    # el mismo run/config debe producir bytes identicos).
    evaluation_c_again = compute_unified_activation_evaluation(
        run_dir, run_id, config_path=config_c_path
    )
    assert evaluation_c.to_stable_json() == evaluation_c_again.to_stable_json()

    # -----------------------------------------------------------------
    # Modo D. UNIFIED_CANARY -- el MISMO source_package_hash real,
    # ahora declarado SIMULTANEAMENTE en allowlist y denylist (el
    # contrato lo permite deliberadamente, ver `contracts/unified_
    # activation_config.py::TestDenylistPrevails`): la denylist
    # prevalece sobre la inclusion explicita, incluso con datos 100%
    # reales -- nunca solo en un test sintetico.
    # -----------------------------------------------------------------
    config_d_path = tmp_path / "config-d-unified-canary-denylisted.yaml"
    _write_config(
        config_d_path,
        mode="UNIFIED_CANARY",
        extra=(
            "canary_strategy: EXPLICIT_ALLOWLIST\n"
            f"package_hash_allowlist: [{source_package_hash!r}]\n"
            f"package_hash_denylist: [{source_package_hash!r}]\n"
            "fallback_policy: FALLBACK_TO_V1\n"
        ),
    )
    evaluation_d = compute_unified_activation_evaluation(run_dir, run_id, config_path=config_d_path)
    write_unified_activation_evaluation(run_dir, evaluation_d)

    assert evaluation_d.canary_selection is not None
    assert evaluation_d.canary_selection.selected is False
    assert evaluation_d.canary_selection.matched_allowlist is True
    assert evaluation_d.canary_selection.matched_denylist is True
    assert evaluation_d.effective_lane.value == "V1"
    assert evaluation_d.activation_decision.value == "KEEP_V1"
    assert evaluation_d.summary.technical_failure_count == 0
    assert evaluation_d.materialization_enabled is False
    denylist_codes = {issue.code.value for issue in evaluation_d.issues}
    assert "CANARY_DENYLISTED" in denylist_codes

    # Tabla real (para el informe de cierre, Fase 14A Parte 12/15).
    print("\n--- Fase 14A, control plane de activacion unificada (JAR+Neo4j reales) ---")
    header = (
        "mode | canary_selected | requested_lane | effective_lane | fallback_lane | "
        "exact_equiv | additive | v1_only | conflicts | readiness | decision | "
        "materialization_enabled"
    )
    print(header)
    for evaluation in (evaluation_a, evaluation_b, evaluation_c, evaluation_d):
        canary_selected = (
            evaluation.canary_selection.selected if evaluation.canary_selection else False
        )
        print(
            f"{evaluation.mode.value} | {canary_selected} | "
            f"{evaluation.requested_lane.value} | {evaluation.effective_lane.value} | "
            f"{evaluation.fallback_lane.value} | "
            f"{evaluation.summary.exact_equivalent_count} | "
            f"{evaluation.summary.unified_additive_count} | "
            f"{evaluation.summary.v1_only_count} | "
            f"{evaluation.summary.conflicting_count} | "
            f"{evaluation.readiness_disposition.value} | "
            f"{evaluation.activation_decision.value} | "
            f"{evaluation.materialization_enabled}"
        )
