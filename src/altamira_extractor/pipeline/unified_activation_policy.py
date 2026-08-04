"""Politica PURA de activacion (Fase 14A Parte 7,
`feat/controlled-unified-activation`).

Determina, a partir del modo solicitado y del estado real de las
fuentes, cuatro cosas: `requested_lane`, `effective_lane`,
`fallback_lane`, `readiness_disposition` y `activation_decision`.
`effective_lane` es SIEMPRE `V1` en Fase 14A -- sin excepcion, para
los cuatro modos -- porque esta fase es exclusivamente control plane y
dry-run (ver invariante de contrato `_check_effective_lane_never_
unified`).

Decisiones de diseno (documentadas explicitamente, dada la ambiguedad
inherente de traducir un requisito en prosa a una tabla de decision
exacta):

- `require_validation_disposition`/`require_downstream_disposition`/
  `require_all_guardrails_passed` (config, Fase 14A Parte 2) son
  interruptores que HABILITAN cada gate correspondiente -- cuando
  estan en `False`, ese gate especifico no bloquea (util para
  evaluaciones exploratorias/staging); `allow_completed_with_
  rejections` amplia el gate de downstream para aceptar tambien
  `COMPLETED_WITH_REJECTIONS` (nunca al reves: `allow_completed_with_
  rejections=True` con `require_downstream_disposition=False` no tiene
  efecto, el gate ya esta deshabilitado).
- Cero fallos tecnicos (`technical_failure_count=0`) es SIEMPRE
  obligatorio para canary/primary, sin interruptor de configuracion --
  nunca es aceptable seleccionar canary sobre un downstream con
  fallos tecnicos.
- Un `V1_ONLY` en `level=CANDIDATE` (nunca alcanzo un `RuleDraft`
  aprobado por guardrail) NO bloquea `UNIFIED_PRIMARY_WITH_V1_
  FALLBACK`: solo un `V1_ONLY` en `level=RULE` (un resultado V1 YA
  aprobado, sin representacion unified) es una omision bloqueante --
  Fase 14A Parte 13, caso F.
- `CONFLICTING` bloquea UNICAMENTE `UNIFIED_PRIMARY_WITH_V1_FALLBACK`
  (Parte 13, caso E, menciona expresamente "primary trial bloqueado" y
  nunca canary) -- canary puede coexistir con un conflicto detectado
  (queda registrado como issue, pero no bloquea el dry-run de canary).
- `CANARY_NOT_SELECTED`/`CANARY_DENYLISTED` nunca son errores tecnicos
  (severidad `INFO`): un run que no participa del cohorte de canary es
  un resultado legitimo, no un fallo."""

from __future__ import annotations

import hashlib

from ..contracts.unified_activation_config import UnifiedActivationConfig, UnifiedActivationMode
from ..contracts.unified_activation_evaluation import (
    UnifiedActivationCanarySelection,
    UnifiedActivationComparison,
    UnifiedActivationComparisonKind,
    UnifiedActivationDecision,
    UnifiedActivationIssue,
    UnifiedActivationIssueCode,
    UnifiedActivationIssueSeverity,
    UnifiedActivationLane,
    UnifiedActivationReadinessDisposition,
)
from ..contracts.unified_shadow_downstream import UnifiedShadowDownstreamArtifact
from ..contracts.unified_shadow_downstream import (
    UnifiedShadowDownstreamDisposition as DownstreamDisposition,
)
from ..contracts.unified_shadow_validation import (
    UnifiedShadowValidationDisposition as ValidationDisposition,
)
from ..contracts.unified_shadow_validation import UnifiedShadowValidationReport

_QUALIFIED_VALIDATION_DISPOSITIONS = frozenset(
    {
        ValidationDisposition.QUALIFIED_FOR_DOWNSTREAM_SHADOW,
        ValidationDisposition.QUALIFIED_WITH_WARNINGS,
    }
)


class PolicyResult:
    """Resultado tipado de `apply_activation_policy` -- deliberadamente
    NO un `AltamiraBaseModel` (nunca se persiste tal cual, el
    evaluador lo vuelca en `UnifiedActivationEvaluationArtifact`)."""

    __slots__ = (
        "requested_lane",
        "effective_lane",
        "fallback_lane",
        "readiness_disposition",
        "activation_decision",
        "issues",
    )

    def __init__(
        self,
        *,
        requested_lane: UnifiedActivationLane,
        effective_lane: UnifiedActivationLane,
        fallback_lane: UnifiedActivationLane,
        readiness_disposition: UnifiedActivationReadinessDisposition,
        activation_decision: UnifiedActivationDecision,
        issues: list[UnifiedActivationIssue],
    ) -> None:
        self.requested_lane = requested_lane
        self.effective_lane = effective_lane
        self.fallback_lane = fallback_lane
        self.readiness_disposition = readiness_disposition
        self.activation_decision = activation_decision
        self.issues = issues


def _issue_id(code: UnifiedActivationIssueCode, *parts: str) -> str:
    canonical = "\x1f".join([code.value, *parts])
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return f"activation-issue::{digest}"


def _issue(
    code: UnifiedActivationIssueCode,
    severity: UnifiedActivationIssueSeverity,
    *,
    comparison_ids: list[str] | None = None,
    v1_reference_ids: list[str] | None = None,
    unified_reference_ids: list[str] | None = None,
) -> UnifiedActivationIssue:
    comparison_ids = sorted(comparison_ids or [])
    v1_ids = sorted(v1_reference_ids or [])
    unified_ids = sorted(unified_reference_ids or [])
    return UnifiedActivationIssue(
        issue_id=_issue_id(code, *comparison_ids, *v1_ids, *unified_ids),
        code=code,
        severity=severity,
        related_v1_reference_ids=v1_ids,
        related_unified_reference_ids=unified_ids,
        comparison_ids=comparison_ids,
        message_code=f"MSG_{code.value}",
    )


def apply_activation_policy(
    config: UnifiedActivationConfig,
    *,
    canary_selection: UnifiedActivationCanarySelection | None,
    v1_available: bool,
    unified_shadow_available: bool,
    validation_report: UnifiedShadowValidationReport | None,
    downstream_artifact: UnifiedShadowDownstreamArtifact | None,
    comparisons: list[UnifiedActivationComparison],
    v1_rule_level_reference_ids: frozenset[str],
) -> PolicyResult:
    """Punto de entrada puro. `v1_rule_level_reference_ids`: el
    conjunto de `reference_id` de `UnifiedActivationV1Reference` con
    `level=RULE` -- necesario para distinguir un `V1_ONLY` bloqueante
    (RULE) de uno no bloqueante (CANDIDATE), sin que este modulo
    conozca el tipo `UnifiedActivationV1Reference` directamente (se
    pasa el conjunto ya calculado por el llamador)."""
    if config.mode == UnifiedActivationMode.V1_ONLY:
        return _policy_v1_only(v1_available=v1_available)

    if config.mode == UnifiedActivationMode.SHADOW_COMPARE:
        return _policy_shadow_compare(
            v1_available=v1_available, unified_shadow_available=unified_shadow_available
        )

    # UNIFIED_CANARY / UNIFIED_PRIMARY_WITH_V1_FALLBACK comparten los
    # mismos gates base; PRIMARY exige adicionalmente ausencia de
    # CONFLICTING y de V1_ONLY bloqueante (level=RULE).
    return _policy_canary_or_primary(
        config,
        canary_selection=canary_selection,
        v1_available=v1_available,
        unified_shadow_available=unified_shadow_available,
        validation_report=validation_report,
        downstream_artifact=downstream_artifact,
        comparisons=comparisons,
        v1_rule_level_reference_ids=v1_rule_level_reference_ids,
        is_primary=config.mode == UnifiedActivationMode.UNIFIED_PRIMARY_WITH_V1_FALLBACK,
    )


def _policy_v1_only(*, v1_available: bool) -> PolicyResult:
    if not v1_available:
        return PolicyResult(
            requested_lane=UnifiedActivationLane.V1,
            effective_lane=UnifiedActivationLane.V1,
            fallback_lane=UnifiedActivationLane.NONE,
            readiness_disposition=UnifiedActivationReadinessDisposition.NOT_EVALUATED,
            activation_decision=UnifiedActivationDecision.KEEP_V1,
            issues=[
                _issue(
                    UnifiedActivationIssueCode.V1_ARTIFACT_MISSING,
                    UnifiedActivationIssueSeverity.WARNING,
                )
            ],
        )
    return PolicyResult(
        requested_lane=UnifiedActivationLane.V1,
        effective_lane=UnifiedActivationLane.V1,
        fallback_lane=UnifiedActivationLane.NONE,
        readiness_disposition=UnifiedActivationReadinessDisposition.V1_ONLY_READY,
        activation_decision=UnifiedActivationDecision.KEEP_V1,
        issues=[],
    )


def _policy_shadow_compare(*, v1_available: bool, unified_shadow_available: bool) -> PolicyResult:
    issues: list[UnifiedActivationIssue] = []
    if not v1_available:
        issues.append(
            _issue(
                UnifiedActivationIssueCode.V1_ARTIFACT_MISSING,
                UnifiedActivationIssueSeverity.WARNING,
            )
        )
    if not unified_shadow_available:
        issues.append(
            _issue(
                UnifiedActivationIssueCode.UNIFIED_ARTIFACT_MISSING,
                UnifiedActivationIssueSeverity.WARNING,
            )
        )
    disposition = (
        UnifiedActivationReadinessDisposition.READY_FOR_SHADOW_COMPARISON
        if v1_available and unified_shadow_available
        else UnifiedActivationReadinessDisposition.NOT_EVALUATED
    )
    return PolicyResult(
        requested_lane=UnifiedActivationLane.UNIFIED_SHADOW,
        effective_lane=UnifiedActivationLane.V1,
        fallback_lane=UnifiedActivationLane.NONE,
        readiness_disposition=disposition,
        activation_decision=UnifiedActivationDecision.RUN_SHADOW_COMPARISON,
        issues=issues,
    )


def _policy_canary_or_primary(
    config: UnifiedActivationConfig,
    *,
    canary_selection: UnifiedActivationCanarySelection | None,
    v1_available: bool,
    unified_shadow_available: bool,
    validation_report: UnifiedShadowValidationReport | None,
    downstream_artifact: UnifiedShadowDownstreamArtifact | None,
    comparisons: list[UnifiedActivationComparison],
    v1_rule_level_reference_ids: frozenset[str],
    is_primary: bool,
) -> PolicyResult:
    issues: list[UnifiedActivationIssue] = []
    requested_lane = UnifiedActivationLane.UNIFIED_SHADOW
    fallback_lane = UnifiedActivationLane.V1

    if canary_selection is None or not canary_selection.selected:
        if canary_selection is not None and canary_selection.matched_denylist:
            issues.append(
                _issue(
                    UnifiedActivationIssueCode.CANARY_DENYLISTED,
                    UnifiedActivationIssueSeverity.INFO,
                )
            )
        else:
            issues.append(
                _issue(
                    UnifiedActivationIssueCode.CANARY_NOT_SELECTED,
                    UnifiedActivationIssueSeverity.INFO,
                )
            )
        return PolicyResult(
            requested_lane=requested_lane,
            effective_lane=UnifiedActivationLane.V1,
            fallback_lane=fallback_lane,
            readiness_disposition=UnifiedActivationReadinessDisposition.NOT_EVALUATED,
            activation_decision=UnifiedActivationDecision.KEEP_V1,
            issues=issues,
        )

    missing_sources = (
        not v1_available
        or not unified_shadow_available
        or validation_report is None
        or downstream_artifact is None
    )
    if missing_sources:
        if not v1_available:
            issues.append(
                _issue(
                    UnifiedActivationIssueCode.V1_ARTIFACT_MISSING,
                    UnifiedActivationIssueSeverity.WARNING,
                )
            )
        if not unified_shadow_available:
            issues.append(
                _issue(
                    UnifiedActivationIssueCode.UNIFIED_ARTIFACT_MISSING,
                    UnifiedActivationIssueSeverity.WARNING,
                )
            )
        if validation_report is None:
            issues.append(
                _issue(
                    UnifiedActivationIssueCode.VALIDATION_REPORT_MISSING,
                    UnifiedActivationIssueSeverity.WARNING,
                )
            )
        if downstream_artifact is None:
            issues.append(
                _issue(
                    UnifiedActivationIssueCode.DOWNSTREAM_ARTIFACT_MISSING,
                    UnifiedActivationIssueSeverity.WARNING,
                )
            )
        return PolicyResult(
            requested_lane=requested_lane,
            effective_lane=UnifiedActivationLane.V1,
            fallback_lane=fallback_lane,
            readiness_disposition=UnifiedActivationReadinessDisposition.NOT_EVALUATED,
            activation_decision=UnifiedActivationDecision.KEEP_V1,
            issues=issues,
        )

    assert validation_report is not None
    assert downstream_artifact is not None

    blocking = False
    fallback_required = False

    if config.require_validation_disposition and validation_report.disposition not in (
        _QUALIFIED_VALIDATION_DISPOSITIONS
    ):
        issues.append(
            _issue(
                UnifiedActivationIssueCode.VALIDATION_NOT_QUALIFIED,
                UnifiedActivationIssueSeverity.BLOCKING,
            )
        )
        blocking = True

    downstream_ok_dispositions = {DownstreamDisposition.COMPLETED}
    if config.allow_completed_with_rejections:
        downstream_ok_dispositions.add(DownstreamDisposition.COMPLETED_WITH_REJECTIONS)
    if (
        config.require_downstream_disposition
        and downstream_artifact.disposition not in downstream_ok_dispositions
    ):
        issues.append(
            _issue(
                UnifiedActivationIssueCode.DOWNSTREAM_NOT_COMPLETED,
                UnifiedActivationIssueSeverity.BLOCKING,
            )
        )
        blocking = True

    if downstream_artifact.summary.technical_failure_count > 0:
        issues.append(
            _issue(
                UnifiedActivationIssueCode.TECHNICAL_FAILURES_PRESENT,
                UnifiedActivationIssueSeverity.BLOCKING,
            )
        )
        blocking = True
        fallback_required = True

    if (
        config.require_all_guardrails_passed
        and downstream_artifact.summary.guardrail_rejected_count > 0
        and not config.allow_completed_with_rejections
    ):
        issues.append(
            _issue(
                UnifiedActivationIssueCode.GUARDRAIL_REJECTIONS_PRESENT,
                UnifiedActivationIssueSeverity.BLOCKING,
            )
        )
        blocking = True

    conflicting = [c for c in comparisons if c.kind == UnifiedActivationComparisonKind.CONFLICTING]
    if is_primary and conflicting:
        issues.append(
            _issue(
                UnifiedActivationIssueCode.V1_UNIFIED_CONFLICT,
                UnifiedActivationIssueSeverity.BLOCKING,
                comparison_ids=[c.comparison_id for c in conflicting],
            )
        )
        blocking = True

    blocking_v1_only = [
        c
        for c in comparisons
        if c.kind == UnifiedActivationComparisonKind.V1_ONLY
        and any(ref_id in v1_rule_level_reference_ids for ref_id in c.v1_reference_ids)
    ]
    if is_primary and blocking_v1_only:
        issues.append(
            _issue(
                UnifiedActivationIssueCode.V1_RESULT_NOT_REPRESENTED,
                UnifiedActivationIssueSeverity.BLOCKING,
                comparison_ids=[c.comparison_id for c in blocking_v1_only],
            )
        )
        blocking = True

    if blocking:
        decision = (
            UnifiedActivationDecision.FALLBACK_TO_V1_PLANNED
            if fallback_required
            else UnifiedActivationDecision.DO_NOT_ACTIVATE
        )
        return PolicyResult(
            requested_lane=requested_lane,
            effective_lane=UnifiedActivationLane.V1,
            fallback_lane=fallback_lane,
            readiness_disposition=UnifiedActivationReadinessDisposition.BLOCKED,
            activation_decision=decision,
            issues=issues,
        )

    if is_primary:
        return PolicyResult(
            requested_lane=requested_lane,
            effective_lane=UnifiedActivationLane.V1,
            fallback_lane=fallback_lane,
            readiness_disposition=UnifiedActivationReadinessDisposition.READY_FOR_PRIMARY_TRIAL,
            activation_decision=UnifiedActivationDecision.SELECT_UNIFIED_PRIMARY_DRY_RUN,
            issues=issues,
        )

    return PolicyResult(
        requested_lane=requested_lane,
        effective_lane=UnifiedActivationLane.V1,
        fallback_lane=fallback_lane,
        readiness_disposition=UnifiedActivationReadinessDisposition.READY_FOR_UNIFIED_CANARY,
        activation_decision=UnifiedActivationDecision.SELECT_UNIFIED_CANARY_DRY_RUN,
        issues=issues,
    )


__all__ = ["PolicyResult", "apply_activation_policy"]
