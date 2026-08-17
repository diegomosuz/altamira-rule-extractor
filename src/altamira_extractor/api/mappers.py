"""Mappers puros de contratos internos a modelos publicos (Prompt 13d).

Extraido de `api/routers/runs.py::get_rule` (duplicado ya una vez en
`cli.py::_build_rule_response`, Prompt 13c): tercera vez que la UI
necesitaba el mismo mapeo confirmo que ameritaba esta extraccion minima
(no un refactor general hacia una clase `ApplicationService`). No
importa `fastapi`, no toca el filesystem, no expone provenance interna
-- conserva exactamente el mismo contrato ya probado por
`tests/api/test_reads.py`/`tests/test_cli.py`."""

from __future__ import annotations

from ..contracts.guardrail_candidate import GuardrailCandidateArtifact
from .schemas import GuardrailView, GuardrailViolationView, RuleResponse


def build_rule_response(artifact: GuardrailCandidateArtifact) -> RuleResponse:
    report = artifact.guardrail_report
    return RuleResponse(
        candidate_id=artifact.candidate_id,
        final_rule_draft=artifact.final_rule_draft,
        guardrail=GuardrailView(
            verdict=report.verdict,
            violations=[
                GuardrailViolationView(
                    violation_id=v.violation_id,
                    rule=v.rule,
                    field=v.field,
                    message=v.message,
                    severity=v.severity,
                )
                for v in report.violations
            ],
            warnings=artifact.warnings,
            # Cuenta solo intentos LLM reales (response_hash no nulo),
            # nunca la sanitizacion deterministica sintetica de
            # traceability (checkpoint correctivo v1.18.2): esa
            # correccion no consume presupuesto LLM_REPAIR_ATTEMPTS ni
            # es una "reparacion" desde la perspectiva del usuario.
            repair_attempts_used=sum(
                1 for attempt in artifact.repair_history if attempt.response_hash is not None
            ),
        ),
    )
