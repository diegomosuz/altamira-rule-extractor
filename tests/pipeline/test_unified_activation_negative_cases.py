"""Fase 14A Parte 13 (`feat/controlled-unified-activation`): ocho
casos negativos dedicados (A-H) exigidos explicitamente por el guion
de la fase. Cada caso ejercita el evaluador PURO (`evaluate_unified_
activation`) o el servicio de filesystem sobre el escenario sintetico
`activation_golden_path()` (Fase 14A Parte 8/11), ajustado UNICAMENTE
en el campo minimo necesario para reproducir la condicion -- nunca un
artefacto fabricado desde cero como sustituto del escenario real."""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts.candidate import CandidateArtifact, RuleCandidate
from altamira_extractor.contracts.enums import CandidateStatus, PipelineStage, StageStatus
from altamira_extractor.contracts.run_state import RunState, StageExecution
from altamira_extractor.contracts.unified_activation_config import (
    UnifiedActivationConfig,
    UnifiedActivationMode,
    UnifiedActivationProviderPolicy,
    UnifiedCanarySelectionStrategy,
    UnifiedFallbackPolicy,
)
from altamira_extractor.contracts.unified_activation_evaluation import (
    UnifiedActivationDecision,
    UnifiedActivationIssueCode,
    UnifiedActivationLane,
    UnifiedActivationReadinessDisposition,
)
from altamira_extractor.contracts.unified_shadow_downstream import (
    UnifiedShadowDownstreamDisposition,
)
from altamira_extractor.contracts.unified_shadow_validation import (
    UnifiedShadowValidationDisposition,
)
from altamira_extractor.pipeline import (
    unified_activation_canary_selector,
    unified_activation_comparator,
    unified_activation_evaluator,
    unified_activation_policy,
    unified_activation_reference_adapters,
    unified_activation_service,
)
from altamira_extractor.pipeline.artifact_store import atomic_write_json
from altamira_extractor.pipeline.errors import UnifiedActivationError
from altamira_extractor.pipeline.unified_activation_evaluator import evaluate_unified_activation
from altamira_extractor.pipeline.unified_activation_service import (
    compute_unified_activation_evaluation,
    unified_activation_evaluation_path,
)

from ._unified_activation_fixtures import (
    V1_CANDIDATE_ID,
    V1_DECISION_ID,
    V1_PARAGRAPH_ID,
    ActivationGoldenPath,
    activation_golden_path,
    v1_candidate_artifact,
    v1_guardrail_artifact,
)
from ._unified_shadow_validation_fixtures import HASH


def _base_kwargs(gp: ActivationGoldenPath) -> dict[str, object]:
    return {
        "run_id": gp.unified_shadow.run_id,
        "source_package_hash": gp.unified_shadow.source_package_hash,
        "config_hash": "c" * 64,
        "candidate_v1_artifact": gp.v1_artifact,
        "candidate_v1_artifact_hash": gp.unified_shadow.source_package_hash,
        "unified_shadow": gp.unified_shadow,
        "unified_candidates_shadow_hash": gp.unified_shadow.source_package_hash,
        "validation_report": gp.validation_report,
        "validation_report_hash": gp.unified_shadow.source_package_hash,
        "downstream_artifact": gp.downstream_artifact,
        "downstream_artifact_hash": gp.unified_shadow.source_package_hash,
    }


def _canary_config(
    *,
    mode: UnifiedActivationMode,
    allowlist: list[str],
    denylist: list[str] | None = None,
) -> UnifiedActivationConfig:
    return UnifiedActivationConfig(
        mode=mode,
        canary_strategy=UnifiedCanarySelectionStrategy.EXPLICIT_ALLOWLIST,
        package_hash_allowlist=allowlist,
        package_hash_denylist=denylist or [],
        fallback_policy=UnifiedFallbackPolicy.FALLBACK_TO_V1,
    )


# ---------------------------------------------------------------------------
# Caso A. Canary en denylist -- nunca seleccionado, lane efectivo V1,
# nunca un fallo tecnico.
# ---------------------------------------------------------------------------


def test_caso_a_canary_denylisted_never_selected_never_technical_failure() -> None:
    gp = activation_golden_path()
    config = UnifiedActivationConfig(
        mode=UnifiedActivationMode.UNIFIED_CANARY,
        canary_strategy=UnifiedCanarySelectionStrategy.EXPLICIT_ALLOWLIST,
        package_hash_denylist=[gp.unified_shadow.source_package_hash],
        fallback_policy=UnifiedFallbackPolicy.FALLBACK_TO_V1,
    )
    artifact = evaluate_unified_activation(config, **_base_kwargs(gp))  # type: ignore[arg-type]

    assert artifact.canary_selection is not None
    assert artifact.canary_selection.selected is False
    assert artifact.canary_selection.matched_denylist is True
    assert artifact.effective_lane == UnifiedActivationLane.V1
    assert artifact.activation_decision == UnifiedActivationDecision.KEEP_V1
    assert artifact.summary.technical_failure_count == 0
    codes = {issue.code for issue in artifact.issues}
    assert UnifiedActivationIssueCode.CANARY_DENYLISTED in codes


# ---------------------------------------------------------------------------
# Caso B. Validacion REVIEW_REQUIRED -- BLOQUEADO por politica, V1
# permanece efectivo.
# ---------------------------------------------------------------------------


def test_caso_b_validation_review_required_blocks_canary_v1_stays_effective() -> None:
    gp = activation_golden_path()
    review_required_report = gp.validation_report.model_copy(
        update={"disposition": UnifiedShadowValidationDisposition.REVIEW_REQUIRED}
    )
    config = _canary_config(
        mode=UnifiedActivationMode.UNIFIED_CANARY,
        allowlist=[gp.unified_shadow.source_package_hash],
    )
    kwargs = _base_kwargs(gp)
    kwargs["validation_report"] = review_required_report
    artifact = evaluate_unified_activation(config, **kwargs)  # type: ignore[arg-type]

    assert artifact.readiness_disposition == UnifiedActivationReadinessDisposition.BLOCKED
    assert artifact.effective_lane == UnifiedActivationLane.V1
    codes = {issue.code for issue in artifact.issues}
    assert UnifiedActivationIssueCode.VALIDATION_NOT_QUALIFIED in codes


# ---------------------------------------------------------------------------
# Caso C. Downstream COMPLETED_WITH_REJECTIONS no permitido -- canary
# bloqueado.
# ---------------------------------------------------------------------------


def test_caso_c_downstream_completed_with_rejections_not_permitted_blocks_canary() -> None:
    gp = activation_golden_path()
    rejected_downstream = gp.downstream_artifact.model_copy(
        update={"disposition": UnifiedShadowDownstreamDisposition.COMPLETED_WITH_REJECTIONS}
    )
    config = _canary_config(
        mode=UnifiedActivationMode.UNIFIED_CANARY,
        allowlist=[gp.unified_shadow.source_package_hash],
    )
    assert config.allow_completed_with_rejections is False
    kwargs = _base_kwargs(gp)
    kwargs["downstream_artifact"] = rejected_downstream
    artifact = evaluate_unified_activation(config, **kwargs)  # type: ignore[arg-type]

    assert artifact.readiness_disposition == UnifiedActivationReadinessDisposition.BLOCKED
    assert artifact.effective_lane == UnifiedActivationLane.V1
    codes = {issue.code for issue in artifact.issues}
    assert UnifiedActivationIssueCode.DOWNSTREAM_NOT_COMPLETED in codes


# ---------------------------------------------------------------------------
# Caso D. Fallo tecnico en unified -- fallback a V1 planificado, lane
# efectivo V1.
# ---------------------------------------------------------------------------


def test_caso_d_technical_failure_plans_fallback_to_v1() -> None:
    gp = activation_golden_path()
    failing_summary = gp.downstream_artifact.summary.model_copy(
        update={"technical_failure_count": 1}
    )
    failing_downstream = gp.downstream_artifact.model_copy(update={"summary": failing_summary})
    config = _canary_config(
        mode=UnifiedActivationMode.UNIFIED_CANARY,
        allowlist=[gp.unified_shadow.source_package_hash],
    )
    kwargs = _base_kwargs(gp)
    kwargs["downstream_artifact"] = failing_downstream
    artifact = evaluate_unified_activation(config, **kwargs)  # type: ignore[arg-type]

    assert artifact.readiness_disposition == UnifiedActivationReadinessDisposition.BLOCKED
    assert artifact.activation_decision == UnifiedActivationDecision.FALLBACK_TO_V1_PLANNED
    assert artifact.effective_lane == UnifiedActivationLane.V1
    codes = {issue.code for issue in artifact.issues}
    assert UnifiedActivationIssueCode.TECHNICAL_FAILURES_PRESENT in codes


# ---------------------------------------------------------------------------
# Caso E. Conflicto V1/unified -- primary trial bloqueado.
# ---------------------------------------------------------------------------


def test_caso_e_v1_unified_conflict_blocks_primary_trial() -> None:
    gp = activation_golden_path()
    conflicting_guardrail = v1_guardrail_artifact(
        statement="Texto V1 deliberadamente distinto al texto unified."
    )
    config = _canary_config(
        mode=UnifiedActivationMode.UNIFIED_PRIMARY_WITH_V1_FALLBACK,
        allowlist=[gp.unified_shadow.source_package_hash],
    )
    kwargs = _base_kwargs(gp)
    kwargs["guardrail_artifacts_by_candidate_id"] = {V1_CANDIDATE_ID: conflicting_guardrail}
    artifact = evaluate_unified_activation(config, **kwargs)  # type: ignore[arg-type]

    assert any(c.kind.value == "CONFLICTING" for c in artifact.comparisons)
    assert artifact.readiness_disposition == UnifiedActivationReadinessDisposition.BLOCKED
    assert artifact.activation_decision == UnifiedActivationDecision.DO_NOT_ACTIVATE
    assert artifact.effective_lane == UnifiedActivationLane.V1
    codes = {issue.code for issue in artifact.issues}
    assert UnifiedActivationIssueCode.V1_UNIFIED_CONFLICT in codes


# ---------------------------------------------------------------------------
# Caso F. Resultado V1_ONLY (nivel RULE) no representado en unified --
# primary trial bloqueado.
# ---------------------------------------------------------------------------


def test_caso_f_v1_only_rule_level_result_blocks_primary_trial() -> None:
    gp = activation_golden_path()
    second_candidate_id = "candidate::v1::unrepresented"
    second_candidate = RuleCandidate(
        candidate_id=second_candidate_id,
        paragraph_id=V1_PARAGRAPH_ID,
        paragraph_name="MAIN",
        decision_id=V1_DECISION_ID,
        detector_id="q0-return-code-decision",
        detector_version="1.0",
        detector_score=1.0,
        status=CandidateStatus.DETECTED_CANDIDATE,
        condition="WS-SALDO > 999999",
        outcome_code="R999",
        line_start=30,
        source_file="CALLER10.cbl",
        source_package_hash=gp.unified_shadow.source_package_hash,
    )
    original_candidate = v1_candidate_artifact()
    combined_v1_artifact = CandidateArtifact(
        run_id=gp.unified_shadow.run_id,
        source_package_hash=gp.unified_shadow.source_package_hash,
        semantic_graph_hash=gp.unified_shadow.source_package_hash,
        invariants_query_hash=gp.unified_shadow.source_package_hash,
        q0_query_hash=gp.unified_shadow.source_package_hash,
        candidates=[*original_candidate.candidates, second_candidate],
    )
    guardrails = {
        V1_CANDIDATE_ID: v1_guardrail_artifact(),
        second_candidate_id: v1_guardrail_artifact(
            statement="Regla V1 sin representacion unified alguna.",
            candidate_id=second_candidate_id,
        ),
    }
    config = _canary_config(
        mode=UnifiedActivationMode.UNIFIED_PRIMARY_WITH_V1_FALLBACK,
        allowlist=[gp.unified_shadow.source_package_hash],
    )
    kwargs = _base_kwargs(gp)
    kwargs["candidate_v1_artifact"] = combined_v1_artifact
    kwargs["guardrail_artifacts_by_candidate_id"] = guardrails
    artifact = evaluate_unified_activation(config, **kwargs)  # type: ignore[arg-type]

    assert any(
        c.kind.value == "V1_ONLY" and any("unrepresented" in rid for rid in c.v1_reference_ids)
        for c in artifact.comparisons
    )
    assert artifact.readiness_disposition == UnifiedActivationReadinessDisposition.BLOCKED
    assert artifact.effective_lane == UnifiedActivationLane.V1
    codes = {issue.code for issue in artifact.issues}
    assert UnifiedActivationIssueCode.V1_RESULT_NOT_REPRESENTED in codes


# ---------------------------------------------------------------------------
# Caso G. YAML con materialization_enabled: true -- error TECNICO de
# configuracion, jamas un reporte parcial.
# ---------------------------------------------------------------------------


def test_caso_g_materialization_true_is_a_technical_config_error(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    now = datetime.now(UTC)
    run_id = "20260101T000000000000-negcaseg"
    state = RunState(
        run_id=run_id,
        package_filename="input/package.zip",
        source_package_hash=HASH,
        current_stage=PipelineStage.PARSED,
        stages=[
            StageExecution(
                stage=PipelineStage.PARSED,
                status=StageStatus.SUCCEEDED,
                started_at=now,
                finished_at=now,
                duration_seconds=0.0,
            )
        ],
        created_at=now,
        updated_at=now,
    )
    atomic_write_json(run_dir / "run.json", state)

    config_path = tmp_path / "config-g.yaml"
    config_path.write_text("mode: V1_ONLY\nmaterialization_enabled: true\n", encoding="utf-8")

    with pytest.raises(UnifiedActivationError):
        compute_unified_activation_evaluation(run_dir, run_id, config_path=config_path)

    assert not unified_activation_evaluation_path(run_dir).exists()
    assert not any((run_dir / "diagnostics").glob("unified-activation-evaluation*"))


# ---------------------------------------------------------------------------
# Caso H. Politica de proveedor real -- rechazada en el borde del
# contrato, ningun proveedor real se inicializa jamas (verificado
# estaticamente via AST, mismo patron que Fase 13).
# ---------------------------------------------------------------------------


def test_caso_h_real_provider_policy_rejected_at_config_boundary() -> None:
    with pytest.raises(ValidationError):
        UnifiedActivationConfig(
            mode=UnifiedActivationMode.V1_ONLY,
            provider_policy=UnifiedActivationProviderPolicy.PRODUCT_PROVIDER_EXPLICITLY_AUTHORIZED,
        )


_FASE_14A_MODULES = (
    unified_activation_service,
    unified_activation_evaluator,
    unified_activation_policy,
    unified_activation_comparator,
    unified_activation_reference_adapters,
    unified_activation_canary_selector,
)

_FORBIDDEN_PROVIDER_MODULE_NAMES = frozenset({"llm_client", "httpx", "openai"})


def _imported_module_names(module: ModuleType) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module.split(".")[0])
    return names


def test_caso_h_no_fase_14a_module_ever_imports_a_real_provider_client() -> None:
    """Verificacion ESTATICA (AST, nunca substring): ningun modulo del
    control plane de activacion unificada importa `llm_client`/
    `httpx`/`openai` -- estructuralmente IMPOSIBLE que se inicialice un
    proveedor real desde este subsistema."""
    for module in _FASE_14A_MODULES:
        imported = _imported_module_names(module)
        overlap = imported & _FORBIDDEN_PROVIDER_MODULE_NAMES
        assert not overlap, f"{module.__name__} importa un cliente de proveedor real: {overlap}"
