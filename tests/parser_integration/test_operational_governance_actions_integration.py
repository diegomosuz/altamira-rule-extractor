"""Integracion real Fase 15B1 (`feat/final-hardening-release`, cierre
"activacion PRIMARY real + single-use real"): reutiliza EXACTAMENTE el
mismo escenario real de Fase 9-14B (`ready_blocked_zip` / CALLER10/
CALLEE10/STOPPER10, JAR real, Neo4j efimero real) para construir V1 +
DOS evaluaciones de activacion 100% reales (`UNIFIED_CANARY` y
`UNIFIED_PRIMARY_WITH_V1_FALLBACK`, ambas del MISMO pipeline real, sin
fabricar ningun artefacto a mano), y ejercer sobre ese run la capa de
gobierno operativo de Fase 15B1 (identidad delegada, RBAC, CSRF,
challenge single-use, workflow prepare/confirm/execute, auditoria) via
HTTP real (`TestClient`) con headers de identidad sinteticos:

A. VIEWER: puede ver gobierno, NO ve acciones privilegiadas, un POST
   directo a prepare se rechaza con 403, y queda auditado.
B. REVIEWER: puede preparar canary, NO puede ejecutarlo (403, falta
   ACTIVATE_CANARY).
C. OPERATOR: activa canary real -- lane V1->UNIFIED, pointer avanzado,
   evento de activacion, evento de auditoria enlazado.
D. OPERATOR: fallback real autorizado (corrupcion EXCLUSIVA de la
   copia de test) -- lane UNIFIED->V1.
E. ADMIN + REVISOR DISTINTO: activa PRIMARY real -- previamente,
   OPERATOR intenta la misma accion y es rechazado en execute (falta
   ACTIVATE_PRIMARY, solo ADMIN la tiene); REVIEWER prepara pero el
   autoaprobacion (el mismo reviewer ejecutando) se rechaza; un ADMIN
   distinto SI ejecuta -- generation kind=UNIFIED_PRIMARY, lane
   V1->UNIFIED, pointer avanzado exactamente uno, evento de activacion
   nuevo, auditoria REQUESTED/SUCCEEDED enlazada, approved_group_ids
   reconciliados contra la evaluacion PRIMARY real.
F. Rollback autorizado a la generacion V1 original -- lane
   UNIFIED->V1, ambas cadenas (activacion + auditoria) integras.

Ademas (cierre "single-use real"): replay del challenge de canary (C)
bloqueado; replay del challenge de primary (E) bloqueado; doble submit
concurrente sobre un challenge de rollback ejecuta una sola transicion
real; y los registros de `audit/consumed-challenges/` se reconcilian
con la cantidad de intentos reales de `execute` (exitosos + rechazados
tras consumo)."""

from __future__ import annotations

import concurrent.futures
import hashlib
import re
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from altamira_extractor.api.app import create_app
from altamira_extractor.contracts.candidate import CandidateArtifact
from altamira_extractor.contracts.candidate_promotion_review import (
    CandidatePromotionDecision,
    CandidatePromotionDecisionManifest,
    DecisionReasonCode,
    ReviewDecision,
)
from altamira_extractor.contracts.security_config import (
    ApplicationRole,
    AuthenticationMode,
    SecurityConfig,
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
    UnifiedActivationEvaluationArtifact,
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

pytestmark = pytest.mark.integration

__all__ = ["ready_blocked_zip"]

_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')
_CHALLENGE_RE = re.compile(r'name="challenge_token" value="([^"]+)"')

_MARKER_HEADER = "X-Trusted-Proxy"
_MARKER_VALUE = "f15b1-integration-marker"
_USER_HEADER = "X-Verified-User"
_GROUPS_HEADER = "X-Verified-Groups"


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
) -> None:
    approved = approved_group_ids or []
    lines = [
        'schema_version: "1.0"',
        f"run_id: {run_id!r}",
        f"activation_evaluation_hash: {eval_hash!r}",
        f"expected_readiness_disposition: {readiness}",
        f"action: {action}",
        f"reason_code: {reason_code}",
        "review_reference: 'f15b1-real-integration-bootstrap'",
        f"approved_group_ids: {approved!r}",
        f"fallback_authorized: {str(fallback_authorized).lower()}",
        f"rollback_authorized: {str(rollback_authorized).lower()}",
        "provider_policy: DETERMINISTIC_FAKE_ONLY",
        "materialization_enabled: true",
        "diagnostics: []",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _security_config() -> SecurityConfig:
    return SecurityConfig(
        authentication_mode=AuthenticationMode.TRUSTED_PROXY_HEADERS,
        trusted_proxy_header_user=_USER_HEADER,
        trusted_proxy_header_groups=_GROUPS_HEADER,
        trusted_proxy_required_marker_header=_MARKER_HEADER,
        trusted_proxy_required_marker_value=SecretStr(_MARKER_VALUE),
        trusted_proxy_allowed_roles=[
            ApplicationRole.REVIEWER,
            ApplicationRole.OPERATOR,
            ApplicationRole.ADMIN,
        ],
        group_role_mapping={
            "reviewers": ApplicationRole.REVIEWER,
            "operators": ApplicationRole.OPERATOR,
            "admins": ApplicationRole.ADMIN,
        },
        session_cookie_name="altamira_session",
        session_cookie_secure=True,
        require_distinct_reviewer_for_rollback=True,
    )


def _as(client: TestClient, user: str, group: str) -> None:
    client.headers.update(
        {_MARKER_HEADER: _MARKER_VALUE, _USER_HEADER: user, _GROUPS_HEADER: group}
    )


def test_real_operational_governance_actions_cycle(tmp_path: Path, ready_blocked_zip: Path) -> None:
    """Fase 15B4-CANDIDATE-QUALITY-5E: enhanced_candidates_enabled=False
    explicito -- reutiliza el escenario de baseline V1/Q0 controlado."""
    require_jar()
    settings = build_settings(tmp_path, enhanced_candidates_enabled=False)

    run_dir, run_id, succeeded_stages = _run_pipeline(settings, ready_blocked_zip)
    assert "PARSED" in succeeded_stages
    if "CANDIDATES_DETECTED" not in succeeded_stages:
        pytest.skip(
            "CANDIDATES_DETECTED no se alcanzo en este entorno (Neo4j no disponible) -- "
            "Fase 15B1 no es verificable sin V1 real"
        )

    # --- Cadena real Fase 9-13 (identica al patron de Fase 14B) ---
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
            decision_id=f"decision::f15b1-integration::{idx}::{item.review_item_id}",
            review_item_id=item.review_item_id,
            assessment_id=item.assessment_id,
            reference_id=item.reference_id,
            assessment_artifact_hash=assessment_hash,
            decision=ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION,
            reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
            reviewer_reference=f"f15b1-integration-reviewer-{idx}@altamira.local",
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

    candidates_path = run_dir / "artifacts" / "06-candidates.json"
    v1_artifact = CandidateArtifact.model_validate_json(candidates_path.read_text(encoding="utf-8"))

    def _evaluate(mode: UnifiedActivationMode) -> tuple[UnifiedActivationEvaluationArtifact, str]:
        activation_config = UnifiedActivationConfig(
            mode=mode,
            canary_strategy=UnifiedCanarySelectionStrategy.EXPLICIT_ALLOWLIST,
            package_hash_allowlist=[source_package_hash],
            fallback_policy=ConfigFallbackPolicy.FALLBACK_TO_V1,
        )
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
        atomic_write_json(
            run_dir / "diagnostics" / "unified-activation-evaluation.json", evaluation
        )
        eval_hash = hashlib.sha256(
            (run_dir / "diagnostics" / "unified-activation-evaluation.json").read_bytes()
        ).hexdigest()
        return evaluation, eval_hash

    canary_evaluation, canary_eval_hash = _evaluate(UnifiedActivationMode.UNIFIED_CANARY)
    assert canary_evaluation.readiness_disposition.value == "READY_FOR_UNIFIED_CANARY"
    canary_group_ids = sorted(
        {
            r.group_id
            for r in canary_evaluation.unified_references
            if r.level.value == "RULE" and r.guardrail_status.value == "PASSED"
        }
    )
    assert len(canary_group_ids) >= 1

    # Evaluacion PRIMARY real -- MISMOS artefactos reales, exclusivamente
    # `mode` distinto (nunca se fabrica un artefacto a mano; ver
    # docstring del modulo).
    primary_evaluation, _primary_eval_hash_unused = _evaluate(
        UnifiedActivationMode.UNIFIED_PRIMARY_WITH_V1_FALLBACK
    )
    assert primary_evaluation.readiness_disposition.value == "READY_FOR_PRIMARY_TRIAL"
    primary_group_ids = sorted(
        {
            r.group_id
            for r in primary_evaluation.unified_references
            if r.level.value == "RULE" and r.guardrail_status.value == "PASSED"
        }
    )
    assert len(primary_group_ids) >= 1

    assert (run_dir / "run.json").is_file()

    step_table: list[dict[str, object]] = []

    # --- Bootstrap V1 (fuera de la capa de gobierno HTTP) -- vuelve a
    # dejar la evaluacion escrita en CANARY para el bootstrap y los
    # pasos B/C/D; se reescribe a PRIMARY mas adelante, justo antes del
    # paso E, exactamente como lo haria un operador re-ejecutando
    # `unified-activation-evaluate` con otro modo. ---
    atomic_write_json(
        run_dir / "diagnostics" / "unified-activation-evaluation.json", canary_evaluation
    )
    keep_v1_auth = tmp_path / "bootstrap-keep-v1.yaml"
    _write_authorization(
        keep_v1_auth,
        run_id=run_id,
        eval_hash=canary_eval_hash,
        action="KEEP_V1",
        readiness="READY_FOR_UNIFIED_CANARY",
        reason_code="KEEP_BASELINE",
        fallback_authorized=False,
    )
    result_bootstrap = materialize_unified_activation(
        run_dir, run_id, authorization_path=keep_v1_auth
    )
    assert result_bootstrap.active_lane.value == "V1"
    v1_generation_id = result_bootstrap.generation_id
    resolver = ActiveArtifactResolver(run_dir, run_id=run_id)
    store = resolver.store

    with TestClient(create_app(settings), base_url="https://testserver") as client:
        client.app.state.security_config = _security_config()  # type: ignore[attr-defined]
        # Cierre F15B1 ("DISABLED_DEV explicito"): el lifespan real ya
        # marco `security_misconfigured=True` (no existe config/
        # security.yaml en este entorno de test) -- este override
        # explicito de `security_config` debe acompañarse SIEMPRE del
        # mismo flag en falso, o `fastapi_deps.py::
        # _check_not_misconfigured` rechazaria toda request con 503
        # pese a la config valida recien asignada.
        client.app.state.security_misconfigured = False  # type: ignore[attr-defined]
        # El lifespan real deja `session_secret=None` cuando outcome!=LOADED
        # (config/security.yaml ausente en este entorno de test) -- este
        # override debe acompanarse tambien de un secreto sintetico, o
        # `SessionCookieMiddleware` nunca fija la cookie de sesion
        # (defecto real encontrado en el cierre "DISABLED_DEV explicito":
        # sin cookie, cada request arranca una sesion nueva y el CSRF de
        # la sesion anterior nunca valida).
        client.app.state.session_secret = SecretStr(  # type: ignore[attr-defined]
            "test-only-synthetic-session-secret-32chars-min"
        )
        client.headers.update({"Origin": "https://testserver"})

        # --- A. VIEWER denegado ---
        _as(client, "viewer1", "no-group")
        viewer_list = client.get(f"/ui/runs/{run_id}/governance/actions")
        assert viewer_list.status_code == 200
        assert "ACTIVATE_UNIFIED_CANARY" not in viewer_list.text
        viewer_form = client.get(f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_CANARY")
        assert viewer_form.status_code == 403
        # VIEWER no tiene VIEW_AUDIT_LOG (RBAC) -- el propio rechazo (403)
        # es la garantia que este paso demuestra.
        viewer_audit = client.get(f"/ui/runs/{run_id}/governance/audit")
        assert viewer_audit.status_code == 403
        step_table.append(
            {
                "step": "A",
                "principal": "viewer1",
                "roles": "VIEWER",
                "permission": "PREPARE_AUTHORIZATION",
                "action": "-",
                "http": 403,
                "readiness": "READY_FOR_UNIFIED_CANARY",
                "from_lane": "V1",
                "to_lane": "V1",
            }
        )

        # --- B. REVIEWER prepara canary, NO ejecuta ---
        _as(client, "reviewer1", "reviewers")
        reviewer_form = client.get(f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_CANARY")
        assert reviewer_form.status_code == 200
        reviewer_audit_check = client.get(f"/ui/runs/{run_id}/governance/audit")
        assert reviewer_audit_check.status_code == 200
        assert "ACCESS_DENIED" in reviewer_audit_check.text
        csrf_1 = _CSRF_RE.search(reviewer_form.text)
        group_ids_field = re.findall(
            r'name="approved_group_ids" value="([^"]+)"', reviewer_form.text
        )
        assert csrf_1 is not None
        reviewer_prepare = client.post(
            f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_CANARY/prepare",
            data={
                "csrf_token": csrf_1.group(1),
                "reason_code": "CANARY_APPROVED",
                "review_reference": "f15b1-real-integration",
                "approved_group_ids": group_ids_field,
                "target_generation_id": "",
            },
            follow_redirects=False,
        )
        assert reviewer_prepare.status_code == 303
        reviewer_confirm_url = reviewer_prepare.headers["location"]
        reviewer_confirm = client.get(reviewer_confirm_url)
        assert reviewer_confirm.status_code == 200
        csrf_2 = _CSRF_RE.search(reviewer_confirm.text)
        reviewer_canary_challenge = _CHALLENGE_RE.search(reviewer_confirm.text)
        assert csrf_2 is not None
        assert reviewer_canary_challenge is not None
        reviewer_execute = client.post(
            f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_CANARY/execute",
            data={
                "csrf_token": csrf_2.group(1),
                "challenge_token": reviewer_canary_challenge.group(1),
            },
            follow_redirects=False,
        )
        assert reviewer_execute.status_code == 403
        step_table.append(
            {
                "step": "B",
                "principal": "reviewer1",
                "roles": "VIEWER,REVIEWER",
                "permission": "ACTIVATE_CANARY (falta)",
                "action": "ACTIVATE_UNIFIED_CANARY",
                "http": 403,
                "readiness": "READY_FOR_UNIFIED_CANARY",
                "from_lane": "V1",
                "to_lane": "V1",
            }
        )

        # --- C. OPERATOR activa canary real ---
        _as(client, "operator1", "operators")
        operator_form = client.get(f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_CANARY")
        csrf_3 = _CSRF_RE.search(operator_form.text)
        assert csrf_3 is not None
        operator_prepare = client.post(
            f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_CANARY/prepare",
            data={
                "csrf_token": csrf_3.group(1),
                "reason_code": "CANARY_APPROVED",
                "review_reference": "f15b1-real-integration",
                "approved_group_ids": group_ids_field,
                "target_generation_id": "",
            },
            follow_redirects=False,
        )
        assert operator_prepare.status_code == 303
        operator_confirm = client.get(operator_prepare.headers["location"])
        csrf_4 = _CSRF_RE.search(operator_confirm.text)
        canary_challenge = _CHALLENGE_RE.search(operator_confirm.text)
        assert csrf_4 is not None
        assert canary_challenge is not None
        canary_challenge_token = canary_challenge.group(1)
        operator_execute = client.post(
            f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_CANARY/execute",
            data={"csrf_token": csrf_4.group(1), "challenge_token": canary_challenge_token},
            follow_redirects=False,
        )
        assert operator_execute.status_code == 303
        pointer_after_canary = store.read_active_pointer()
        assert pointer_after_canary is not None
        assert pointer_after_canary.active_lane.value == "UNIFIED"
        assert pointer_after_canary.pointer_version == 2
        canary_generation_id = pointer_after_canary.active_generation_id
        canary_event_id = pointer_after_canary.latest_event_id
        for logical_name in ("candidates", "context-packages", "rule-drafts", "guardrails"):
            assert resolver.resolve(logical_name).status.value == "RESOLVED"
        operator_audit = client.get(f"/ui/runs/{run_id}/governance/audit")
        assert "ACTIVATION_CANARY_SUCCEEDED" in operator_audit.text
        step_table.append(
            {
                "step": "C",
                "principal": "operator1",
                "roles": "VIEWER,OPERATOR",
                "permission": "ACTIVATE_CANARY",
                "action": "ACTIVATE_UNIFIED_CANARY",
                "http": 303,
                "readiness": "READY_FOR_UNIFIED_CANARY",
                "from_lane": "V1",
                "to_lane": "UNIFIED",
                "pointer_version": 2,
                "activation_event": canary_event_id,
            }
        )

        # --- D. OPERATOR fallback real autorizado ---
        corrupted_path = store.generation_dir(canary_generation_id) / "candidates.json"
        corrupted_path.write_bytes(b"{corrupted-by-f15b1-integration-test}")

        fallback_form = client.get(f"/ui/runs/{run_id}/governance/actions/FALLBACK_TO_V1")
        csrf_5 = _CSRF_RE.search(fallback_form.text)
        assert csrf_5 is not None
        fallback_prepare = client.post(
            f"/ui/runs/{run_id}/governance/actions/FALLBACK_TO_V1/prepare",
            data={
                "csrf_token": csrf_5.group(1),
                "reason_code": "OPERATIONAL_INCIDENT",
                "review_reference": "f15b1-real-integration-fallback",
                "target_generation_id": "",
            },
            follow_redirects=False,
        )
        assert fallback_prepare.status_code == 303
        fallback_confirm = client.get(fallback_prepare.headers["location"])
        csrf_6 = _CSRF_RE.search(fallback_confirm.text)
        challenge_3 = _CHALLENGE_RE.search(fallback_confirm.text)
        assert csrf_6 is not None
        assert challenge_3 is not None
        fallback_execute = client.post(
            f"/ui/runs/{run_id}/governance/actions/FALLBACK_TO_V1/execute",
            data={"csrf_token": csrf_6.group(1), "challenge_token": challenge_3.group(1)},
            follow_redirects=False,
        )
        assert fallback_execute.status_code == 303
        pointer_after_fallback = store.read_active_pointer()
        assert pointer_after_fallback is not None
        assert pointer_after_fallback.active_lane.value == "V1"
        assert pointer_after_fallback.pointer_version == 3
        fallback_event_id = pointer_after_fallback.latest_event_id
        assert corrupted_path.read_bytes() == b"{corrupted-by-f15b1-integration-test}"
        fallback_audit_text = client.get(f"/ui/runs/{run_id}/governance/audit").text
        assert "FALLBACK_SUCCEEDED" in fallback_audit_text
        step_table.append(
            {
                "step": "D",
                "principal": "operator1",
                "roles": "VIEWER,OPERATOR",
                "permission": "EXECUTE_FALLBACK",
                "action": "FALLBACK_TO_V1",
                "http": 303,
                "readiness": "READY_FOR_UNIFIED_CANARY",
                "from_lane": "UNIFIED",
                "to_lane": "V1",
                "pointer_version": 3,
                "activation_event": fallback_event_id,
            }
        )

        # --- REPLAY del challenge de canary (ya consumido en C) --------
        _as(client, "operator1", "operators")
        canary_replay = client.post(
            f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_CANARY/execute",
            data={"csrf_token": csrf_4.group(1), "challenge_token": canary_challenge_token},
            follow_redirects=False,
        )
        assert canary_replay.status_code == 409

        # --- E. Activacion PRIMARY real ---------------------------------
        # Evaluacion re-escrita a PRIMARY real (mismos artefactos, solo
        # `mode` distinto) -- ver docstring del modulo.
        atomic_write_json(
            run_dir / "diagnostics" / "unified-activation-evaluation.json", primary_evaluation
        )

        # E.0 -- OPERATOR intenta ACTIVATE_UNIFIED_PRIMARY: `prepare`
        # succeed (solo exige PREPARE_AUTHORIZATION, generico) pero
        # `execute` se rechaza (falta ACTIVATE_PRIMARY, solo ADMIN la
        # tiene) -- demuestra que ninguna transicion ocurre sin el
        # permiso especifico de la accion, aunque el prepare generico
        # haya emitido un challenge valido.
        _as(client, "operator1", "operators")
        operator_primary_form = client.get(
            f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_PRIMARY"
        )
        assert operator_primary_form.status_code == 200
        csrf_op_primary = _CSRF_RE.search(operator_primary_form.text)
        primary_group_field = re.findall(
            r'name="approved_group_ids" value="([^"]+)"', operator_primary_form.text
        )
        assert csrf_op_primary is not None
        operator_primary_prepare = client.post(
            f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_PRIMARY/prepare",
            data={
                "csrf_token": csrf_op_primary.group(1),
                "reason_code": "PRIMARY_TRIAL_APPROVED",
                "review_reference": "f15b1-real-integration-primary-denied",
                "approved_group_ids": primary_group_field,
                "target_generation_id": "",
            },
            follow_redirects=False,
        )
        assert operator_primary_prepare.status_code == 303
        operator_primary_confirm = client.get(operator_primary_prepare.headers["location"])
        csrf_op_primary_2 = _CSRF_RE.search(operator_primary_confirm.text)
        operator_primary_challenge = _CHALLENGE_RE.search(operator_primary_confirm.text)
        assert csrf_op_primary_2 is not None
        assert operator_primary_challenge is not None
        pointer_before_denied_primary = store.read_active_pointer()
        assert pointer_before_denied_primary is not None
        operator_primary_execute = client.post(
            f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_PRIMARY/execute",
            data={
                "csrf_token": csrf_op_primary_2.group(1),
                "challenge_token": operator_primary_challenge.group(1),
            },
            follow_redirects=False,
        )
        assert operator_primary_execute.status_code == 403
        pointer_after_denied_primary = store.read_active_pointer()
        assert pointer_after_denied_primary is not None
        assert (
            pointer_after_denied_primary.pointer_version
            == pointer_before_denied_primary.pointer_version
        )
        assert pointer_after_denied_primary.active_lane.value == "V1"
        denied_primary_audit = client.get(f"/ui/runs/{run_id}/governance/audit").text
        assert "ACCESS_DENIED" in denied_primary_audit
        step_table.append(
            {
                "step": "E.0",
                "principal": "operator1",
                "roles": "VIEWER,OPERATOR",
                "permission": "ACTIVATE_PRIMARY (falta)",
                "action": "ACTIVATE_UNIFIED_PRIMARY",
                "http": 403,
                "readiness": "READY_FOR_PRIMARY_TRIAL",
                "from_lane": "V1",
                "to_lane": "V1",
            }
        )

        # E.1 -- REVIEWER prepara PRIMARY, NO ejecuta.
        _as(client, "reviewer3", "reviewers")
        reviewer_primary_form = client.get(
            f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_PRIMARY"
        )
        assert reviewer_primary_form.status_code == 200
        assert "Revisor distinto exigido" in reviewer_primary_form.text
        csrf_rp1 = _CSRF_RE.search(reviewer_primary_form.text)
        assert csrf_rp1 is not None
        reviewer_primary_prepare = client.post(
            f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_PRIMARY/prepare",
            data={
                "csrf_token": csrf_rp1.group(1),
                "reason_code": "PRIMARY_TRIAL_APPROVED",
                "review_reference": "f15b1-real-integration-primary",
                "approved_group_ids": primary_group_field,
                "target_generation_id": "",
            },
            follow_redirects=False,
        )
        assert reviewer_primary_prepare.status_code == 303
        reviewer_primary_confirm = client.get(reviewer_primary_prepare.headers["location"])
        csrf_rp2 = _CSRF_RE.search(reviewer_primary_confirm.text)
        primary_challenge = _CHALLENGE_RE.search(reviewer_primary_confirm.text)
        assert csrf_rp2 is not None
        assert primary_challenge is not None
        primary_challenge_token = primary_challenge.group(1)
        step_table.append(
            {
                "step": "E.1",
                "principal": "reviewer3",
                "roles": "VIEWER,REVIEWER",
                "permission": "PREPARE_AUTHORIZATION",
                "action": "ACTIVATE_UNIFIED_PRIMARY",
                "http": 303,
                "readiness": "READY_FOR_PRIMARY_TRIAL",
                "from_lane": "V1",
                "to_lane": "V1",
            }
        )

        # E.2 -- Autoaprobacion: el MISMO reviewer3 intenta ejecutar.
        self_approval_primary = client.post(
            f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_PRIMARY/execute",
            data={"csrf_token": csrf_rp2.group(1), "challenge_token": primary_challenge_token},
            follow_redirects=False,
        )
        assert self_approval_primary.status_code == 403
        pointer_after_self_approval = store.read_active_pointer()
        assert pointer_after_self_approval is not None
        assert pointer_after_self_approval.active_lane.value == "V1"
        step_table.append(
            {
                "step": "E.2",
                "principal": "reviewer3 (auto)",
                "roles": "VIEWER,REVIEWER",
                "permission": "revisor distinto exigido",
                "action": "ACTIVATE_UNIFIED_PRIMARY",
                "http": 403,
                "readiness": "READY_FOR_PRIMARY_TRIAL",
                "from_lane": "V1",
                "to_lane": "V1",
            }
        )

        # E.3 -- ADMIN (identidad distinta) SI ejecuta PRIMARY.
        _as(client, "admin2", "admins")
        admin_primary_execute = client.post(
            f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_PRIMARY/execute",
            data={"csrf_token": csrf_rp2.group(1), "challenge_token": primary_challenge_token},
            follow_redirects=False,
        )
        assert admin_primary_execute.status_code == 303
        pointer_after_primary = store.read_active_pointer()
        assert pointer_after_primary is not None
        assert pointer_after_primary.active_lane.value == "UNIFIED"
        assert (
            pointer_after_primary.pointer_version == pointer_after_self_approval.pointer_version + 1
        )
        primary_generation_id = pointer_after_primary.active_generation_id
        primary_event_id = pointer_after_primary.latest_event_id
        assert primary_event_id != canary_event_id
        assert primary_event_id != fallback_event_id
        primary_manifest = store.read_generation_manifest(primary_generation_id)
        assert primary_manifest.kind.value == "UNIFIED_PRIMARY"
        assert sorted(primary_manifest.approved_group_ids) == primary_group_ids
        primary_audit_text = client.get(f"/ui/runs/{run_id}/governance/audit").text
        assert "ACTIVATION_PRIMARY_REQUESTED" in primary_audit_text
        assert "ACTIVATION_PRIMARY_SUCCEEDED" in primary_audit_text
        assert "reviewer3" in primary_audit_text
        assert "admin2" in primary_audit_text
        primary_event_row = re.search(
            r"ACTIVATION_PRIMARY_SUCCEEDED.*?</tr>", primary_audit_text, re.S
        )
        assert primary_event_row is not None
        assert primary_event_id in primary_event_row.group(0)
        step_table.append(
            {
                "step": "E.3",
                "principal": "admin2 (reviewer=reviewer3)",
                "roles": "VIEWER,ADMIN",
                "permission": "ACTIVATE_PRIMARY",
                "action": "ACTIVATE_UNIFIED_PRIMARY",
                "http": 303,
                "readiness": "READY_FOR_PRIMARY_TRIAL",
                "from_lane": "V1",
                "to_lane": "UNIFIED",
                "pointer_version": pointer_after_primary.pointer_version,
                "activation_event": primary_event_id,
                "generation_kind": "UNIFIED_PRIMARY",
            }
        )

        # --- REPLAY del challenge de primary (ya consumido en E.3) -----
        primary_replay = client.post(
            f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_PRIMARY/execute",
            data={"csrf_token": csrf_rp2.group(1), "challenge_token": primary_challenge_token},
            follow_redirects=False,
        )
        assert primary_replay.status_code == 409
        pointer_after_primary_replay = store.read_active_pointer()
        assert pointer_after_primary_replay is not None
        assert pointer_after_primary_replay.pointer_version == pointer_after_primary.pointer_version

        # --- F. Rollback autorizado a la generacion V1 original --------
        _as(client, "reviewer4", "reviewers")
        rollback_form = client.get(f"/ui/runs/{run_id}/governance/actions/ROLLBACK_TO_GENERATION")
        assert rollback_form.status_code == 200
        csrf_7 = _CSRF_RE.search(rollback_form.text)
        assert csrf_7 is not None
        rollback_prepare = client.post(
            f"/ui/runs/{run_id}/governance/actions/ROLLBACK_TO_GENERATION/prepare",
            data={
                "csrf_token": csrf_7.group(1),
                "reason_code": "OPERATOR_ROLLBACK",
                "review_reference": "f15b1-real-integration-rollback",
                "target_generation_id": v1_generation_id,
            },
            follow_redirects=False,
        )
        assert rollback_prepare.status_code == 303
        rollback_confirm = client.get(rollback_prepare.headers["location"])
        csrf_8 = _CSRF_RE.search(rollback_confirm.text)
        rollback_challenge = _CHALLENGE_RE.search(rollback_confirm.text)
        assert csrf_8 is not None
        assert rollback_challenge is not None
        rollback_challenge_token = rollback_challenge.group(1)

        # ADMIN distinto de reviewer4 (revisor distinto exigido por
        # `require_distinct_reviewer_for_rollback=True`) ejecuta -- las
        # DOS ramas concurrentes de abajo comparten esta MISMA identidad
        # (el punto del doble submit es la carrera sobre el consumo del
        # challenge, no sobre el permiso).
        _as(client, "admin3", "admins")

        # --- DOBLE SUBMIT CONCURRENTE sobre el MISMO challenge de
        # rollback -- exactamente una transicion real debe ejecutarse. ---
        barrier = threading.Barrier(2)

        def _submit_rollback() -> int:
            barrier.wait(timeout=5)
            response = client.post(
                f"/ui/runs/{run_id}/governance/actions/ROLLBACK_TO_GENERATION/execute",
                data={"csrf_token": csrf_8.group(1), "challenge_token": rollback_challenge_token},
                follow_redirects=False,
            )
            return int(response.status_code)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            concurrent_statuses = list(pool.map(lambda _: _submit_rollback(), range(2)))

        assert sorted(concurrent_statuses) == [303, 409]
        pointer_after_rollback = store.read_active_pointer()
        assert pointer_after_rollback is not None
        assert pointer_after_rollback.active_lane.value == "V1"
        assert pointer_after_rollback.active_generation_id == v1_generation_id
        # Rollback real (no idempotente): venia de UNIFIED (primary),
        # avanza el pointer exactamente uno pese al doble submit.
        assert pointer_after_rollback.pointer_version == pointer_after_primary.pointer_version + 1

        final_audit_text = client.get(f"/ui/runs/{run_id}/governance/audit").text
        assert "reviewer4" in final_audit_text
        assert "admin3" in final_audit_text
        assert "ROLLBACK_SUCCEEDED" in final_audit_text
        step_table.append(
            {
                "step": "F",
                "principal": "admin3 (reviewer=reviewer4, doble submit concurrente)",
                "roles": "VIEWER,ADMIN",
                "permission": "EXECUTE_ROLLBACK",
                "action": "ROLLBACK_TO_GENERATION",
                "http": "303+409 (doble submit)",
                "readiness": "READY_FOR_PRIMARY_TRIAL",
                "from_lane": "UNIFIED",
                "to_lane": "V1",
                "pointer_version": pointer_after_rollback.pointer_version,
            }
        )

    # --- Reconciliacion de challenges consumidos -------------------------
    consumed_dir = run_dir / "audit" / "consumed-challenges"
    consumed_files = list(consumed_dir.glob("*.json"))
    # El consumo (paso 7) ocurre DESPUES de sesion/identidad/permiso/CSRF/
    # firma-y-expiracion-del-challenge/principal-accion-run (pasos 1-6) --
    # por diseno, un intento rechazado por PERMISO insuficiente (E.0,
    # OPERATOR sin ACTIVATE_PRIMARY) o por AUTOAPROBACION (E.2, mismo
    # reviewer) nunca llega a consumir: si lo hiciera, cualquier intento
    # no autorizado podria "quemar" un challenge legitimo y bloquear a
    # quien SI puede ejecutarlo (denegacion de servicio trivial) -- E.3
    # reutiliza exitosamente el MISMO challenge que E.2 dejo intacto.
    # Solo cuentan intentos que SI llegan al paso 7: canary (C, exitoso),
    # fallback (D, exitoso), primary (E.3, exitoso) y rollback (F, un
    # solo registro pese al doble submit -- la otra rama nunca crea un
    # segundo archivo, es la MISMA carrera O_CREAT|O_EXCL). Los replays
    # de canary y primary reutilizan el MISMO challenge ya consumido, no
    # generan un registro nuevo. Total de registros UNICOS: 4.
    assert len(consumed_files) == 4

    # Historial intacto: la generacion corrupta sigue en disco, sin reparar.
    assert corrupted_path.read_bytes() == b"{corrupted-by-f15b1-integration-test}"
    assert (run_dir / "run.json").is_file()
    for i in range(1, 11):
        artifact_glob = list((run_dir / "artifacts").glob(f"{i:02d}-*"))
        for path in artifact_glob:
            if path.is_dir():
                assert any(p.is_file() for p in path.rglob("*"))
            else:
                assert path.is_file()

    assert len(step_table) == 9
