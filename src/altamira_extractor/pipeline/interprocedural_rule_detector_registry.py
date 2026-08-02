"""Registry determinístico de detectores de reglas interprocedurales
(Fase 8 de la ampliacion semantica,
`feat/interprocedural-rule-detectors-shadow`).

Mapea `detector_id -> InterproceduralRuleDetectorDefinition` de forma
explicita y estatica: sin import dinamico, sin discovery automatico, sin
plugins -- mismo patron que `v2_detector_registry.py` (Fase 5). Agregar
un detector nuevo significa escribir la funcion en
`interprocedural_rule_detectors.py` y agregar UNA entrada aqui -- nunca
registrarlo por efecto secundario de importar un modulo. Cada detector
declarado aqui tiene nombre estable, declara su `rule_type`, recibe
exclusivamente `InterproceduralRuleDetectorContext` (inmutable) y nunca
conoce a otros detectores ni al comparador (componente separado, ver
`interprocedural_rule_comparator.py`)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..contracts.interprocedural_rule_candidates import (
    InterproceduralRuleCandidate,
    InterproceduralRuleType,
)
from .interprocedural_rule_detectors import (
    DETECTOR_ID_BY_REFERENCE,
    DETECTOR_ID_RETURN_CODE,
    DETECTOR_ID_STATE_TRANSITION,
    DETECTOR_VERSION_BY_REFERENCE,
    DETECTOR_VERSION_RETURN_CODE,
    DETECTOR_VERSION_STATE_TRANSITION,
    InterproceduralRuleDetectorContext,
    detect_by_reference_rule,
    detect_return_code_rule,
    detect_state_transition_rule,
)

DetectorCallable = Callable[
    [InterproceduralRuleDetectorContext], list[InterproceduralRuleCandidate]
]


@dataclass(frozen=True)
class InterproceduralRuleDetectorDefinition:
    detector_id: str
    detector_version: str
    rule_type: InterproceduralRuleType
    callable: DetectorCallable
    description: str


INTERPROCEDURAL_RULE_DETECTOR_REGISTRY: dict[str, InterproceduralRuleDetectorDefinition] = {
    DETECTOR_ID_RETURN_CODE: InterproceduralRuleDetectorDefinition(
        detector_id=DETECTOR_ID_RETURN_CODE,
        detector_version=DETECTOR_VERSION_RETURN_CODE,
        rule_type=InterproceduralRuleType.RETURN_CODE_RULE,
        callable=detect_return_code_rule,
        description=(
            "Call sites con RETURNING cuyo literal de salida demuestra "
            "InterproceduralPropagation (Fase 7) de forma deterministica, o cuya salida "
            "esta bloqueada con certeza estructural (p. ej. STOP RUN)."
        ),
    ),
    DETECTOR_ID_BY_REFERENCE: InterproceduralRuleDetectorDefinition(
        detector_id=DETECTOR_ID_BY_REFERENCE,
        detector_version=DETECTOR_VERSION_BY_REFERENCE,
        rule_type=InterproceduralRuleType.BY_REFERENCE_RULE,
        callable=detect_by_reference_rule,
        description=(
            "Argumentos BY REFERENCE cuyo valor final demuestra InterproceduralPropagation "
            "(Fase 7) de forma deterministica, o cuya salida esta bloqueada con certeza "
            "estructural."
        ),
    ),
    DETECTOR_ID_STATE_TRANSITION: InterproceduralRuleDetectorDefinition(
        detector_id=DETECTOR_ID_STATE_TRANSITION,
        detector_version=DETECTOR_VERSION_STATE_TRANSITION,
        rule_type=InterproceduralRuleType.STATE_TRANSITION_RULE,
        callable=detect_state_transition_rule,
        description=(
            "Argumentos BY REFERENCE con valor de entrada y salida deterministicos y "
            "distintos, cuyo actual esta clasificado semantic_tag=status/status_flag "
            "(SemanticEnrichment, Fase 3b) -- nunca por heuristica textual de nombres."
        ),
    ),
}


def ordered_detector_ids() -> list[str]:
    """Orden de ejecucion determinístico: alfabetico por detector_id
    (nunca el orden de insercion del dict)."""
    return sorted(INTERPROCEDURAL_RULE_DETECTOR_REGISTRY)
