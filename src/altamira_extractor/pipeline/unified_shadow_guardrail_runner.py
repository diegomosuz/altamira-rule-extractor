"""Ejecutor PURO de guardrails sobre un `RuleDraft` shadow (Fase 13
Parte 8, `feat/unified-shadow-downstream-pipeline`).

Reutiliza `deterministic_guardrail.evaluate_guardrail` (productivo,
NUNCA modificado) -- exactamente los mismos chequeos que el flujo V1:
evidence aliases/IDs/paths validos, ausencia de evidencia inventada,
consistencia numeros/fechas, redaccion de datos sensibles (prompt
injection), tablas/return codes `approved_for_rule_text`, contexto de
batch. Nunca reimplementa ni relaja ninguno.

Un rechazo (`GuardrailVerdict.REJECTED`) NUNCA publica una regla, NUNCA
elimina el `RuleDraft`: queda registrado como `GUARDRAIL_REJECTED`
(Fase 13 Parte 3), con las violaciones preservadas -- el llamador
(Parte 9, executor) decide como representarlo, este modulo solo
evalua.

Sin timestamps: el `GuardrailReport` productivo exige `evaluated_at:
datetime` (esa exigencia NUNCA se modifica) -- `run_shadow_guardrails`
lo satisface con un valor SENTINELA fijo (epoch, nunca la hora real ni
un valor derivado de `run_id`), unicamente para poder construir el
objeto productivo en memoria; ese `GuardrailReport` real NUNCA se
persiste. `to_shadow_view` proyecta el resultado a
`UnifiedShadowGuardrailReportView` (Fase 13 Parte 3), que EXCLUYE
`evaluated_at` por completo -- es esa vista, nunca el `GuardrailReport`
productivo, la que el executor embebe en el artefacto persistido.

Puro: sin filesystem, sin red, sin Neo4j, sin `datetime.now()`/
`utcnow()`/`time.time()`."""

from __future__ import annotations

from datetime import UTC, datetime

from ..contracts.context_package import ContextPackage
from ..contracts.enums import GuardrailVerdict, Severity
from ..contracts.guardrail import GuardrailReport
from ..contracts.rule_draft import RuleDraft
from ..contracts.unified_shadow_downstream import UnifiedShadowGuardrailReportView
from .deterministic_guardrail import evaluate_guardrail

_UNPERSISTED_EVALUATED_AT_SENTINEL = datetime(1970, 1, 1, tzinfo=UTC)
"""Valor FIJO (epoch), nunca la hora real, nunca derivado de `run_id`
-- satisface unicamente el campo obligatorio `GuardrailReport.
evaluated_at` del contrato productivo (nunca modificado) para poder
construir ese objeto en memoria. Nunca se persiste: `to_shadow_view`
lo descarta antes de que el executor lo embeba en el artefacto."""


def run_shadow_guardrails(
    rule_draft: RuleDraft,
    package: ContextPackage,
    *,
    group_id: str,
    source_package_hash: str,
) -> GuardrailReport:
    """Punto de entrada puro. `candidate_id` del reporte es SIEMPRE
    `group_id` (`unified_shadow_candidate_id`), nunca un
    `source_candidate_id` individual -- ningun member se trata como
    "el" candidato del grupo. El `GuardrailReport` retornado es real y
    completo (incluye `evaluated_at`, exigido por su contrato
    productivo) pero es responsabilidad EXCLUSIVA del llamador nunca
    persistirlo directamente -- ver `to_shadow_view`."""
    violations = evaluate_guardrail(rule_draft, package)
    has_error = any(v.severity == Severity.ERROR for v in violations)
    verdict = GuardrailVerdict.REJECTED if has_error else GuardrailVerdict.EVIDENCE_VALIDATED
    return GuardrailReport(
        candidate_id=group_id,
        verdict=verdict,
        violations=violations,
        repair_attempts=0,
        evaluated_at=_UNPERSISTED_EVALUATED_AT_SENTINEL,
        source_package_hash=source_package_hash,
    )


def to_shadow_view(report: GuardrailReport) -> UnifiedShadowGuardrailReportView:
    """Proyecta un `GuardrailReport` real a
    `UnifiedShadowGuardrailReportView` -- preserva `candidate_id`/
    `verdict`/`violations`/`repair_attempts`/`source_package_hash`
    exactamente, EXCLUYE `evaluated_at` por completo. Esta es la UNICA
    representacion de un resultado de guardrail que Fase 13 persiste."""
    return UnifiedShadowGuardrailReportView(
        candidate_id=report.candidate_id,
        verdict=report.verdict,
        violations=report.violations,
        repair_attempts=report.repair_attempts,
        source_package_hash=report.source_package_hash,
    )
