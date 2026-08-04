"""Validador PURO de completitud del baseline V1 (Fase 12 Parte 5,
`feat/unified-shadow-differential-validation`).

Regenera, con el adaptador REAL de Fase 11
(`unified_shadow_baseline_adapter.py::adapt_v1_baseline_candidates`,
NUNCA reimplementado ni modificado), la lista ESPERADA de
`UnifiedBaselineCandidateReference` a partir del `CandidateArtifact` V1
ACTUAL, y la compara -- referencia por referencia -- contra
`unified_shadow.baseline_candidates` (la lista PERSISTIDA). Una
diferencia (falta uno, sobra uno, o alguno cambio) es
`BASELINE_CANDIDATE_MISSING`/`BASELINE_COUNT_MISMATCH`. Un
`CandidateArtifact` V1 valida y vacio (cero candidatos) es un
resultado VALIDO -- nunca un error."""

from __future__ import annotations

from dataclasses import dataclass

from ..contracts.candidate import CandidateArtifact
from ..contracts.unified_candidates_shadow import UnifiedCandidatesShadowArtifact
from ..contracts.unified_shadow_validation import UnifiedShadowValidationGate
from ..contracts.unified_shadow_validation import UnifiedShadowValidationIssueCode as Code
from .errors import UnifiedCandidatesShadowError, UnifiedShadowValidationError
from .unified_shadow_baseline_adapter import adapt_v1_baseline_candidates
from .unified_shadow_validation_policy import RawFinding

_GATE = UnifiedShadowValidationGate.BASELINE_COMPLETENESS


@dataclass(frozen=True)
class BaselineValidationResult:
    findings: tuple[RawFinding, ...]
    gate_passed: bool
    checked_baseline_reference_ids: tuple[str, ...]


def validate_baseline_completeness(
    *,
    v1_candidates: CandidateArtifact,
    unified_shadow: UnifiedCandidatesShadowArtifact,
    candidate_v1_artifact_hash: str,
) -> BaselineValidationResult:
    """Punto de entrada puro. Nunca muta `v1_candidates`/
    `unified_shadow`. Si `adapt_v1_baseline_candidates` (Fase 11) no
    puede derivar el baseline (p. ej. un candidato V1 con
    `paragraph_id` de formato inesperado), el defecto no tiene un
    `UnifiedShadowValidationIssueCode` que lo represente honestamente
    en el reporte -- el contrato mismo no puede construirse -- por lo
    que se relanza como `UnifiedShadowValidationError` tipado (Fase 12,
    nunca el `UnifiedCandidatesShadowError` crudo de Fase 11), que el
    servicio/CLI convierte en un error tecnico controlado sin
    traceback visible."""
    try:
        adapted = adapt_v1_baseline_candidates(
            v1_candidates, source_artifact_hash=candidate_v1_artifact_hash
        )
    except UnifiedCandidatesShadowError as exc:
        raise UnifiedShadowValidationError(
            "no se pudo derivar el baseline V1: un candidato tiene una identidad "
            "estructural que el adaptador de Fase 11 no puede interpretar"
        ) from exc
    expected = {ref.baseline_reference_id: ref for ref in adapted}
    actual = {ref.baseline_reference_id: ref for ref in unified_shadow.baseline_candidates}

    findings: list[RawFinding] = []
    checked_ids = tuple(sorted(set(expected) | set(actual)))

    missing_ids = sorted(set(expected) - set(actual))
    if missing_ids:
        findings.append(
            RawFinding(
                code=Code.BASELINE_CANDIDATE_MISSING,
                gate=_GATE,
                baseline_reference_ids=tuple(missing_ids),
                source_candidate_ids=tuple(
                    sorted(expected[i].source_candidate_id for i in missing_ids)
                ),
            )
        )

    extra_ids = sorted(set(actual) - set(expected))
    changed_ids = sorted(
        ref_id for ref_id in (set(expected) & set(actual)) if expected[ref_id] != actual[ref_id]
    )
    if extra_ids or changed_ids or len(expected) != len(actual):
        findings.append(
            RawFinding(
                code=Code.BASELINE_COUNT_MISMATCH,
                gate=_GATE,
                baseline_reference_ids=tuple(sorted(set(extra_ids) | set(changed_ids))),
                diagnostics=(
                    f"expected_count={len(expected)}",
                    f"actual_count={len(actual)}",
                ),
            )
        )

    return BaselineValidationResult(
        findings=tuple(findings),
        gate_passed=not findings,
        checked_baseline_reference_ids=checked_ids,
    )
