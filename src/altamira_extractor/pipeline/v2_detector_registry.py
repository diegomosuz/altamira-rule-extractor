"""Registry determinístico de detectores V2 (Fase 5 de la ampliacion
semantica, `feat/v2-detectors-shadow-mode`).

Mapea `detector_id -> V2DetectorDefinition` de forma explicita y estatica:
sin import dinamico, sin discovery automatico, sin plugins. Agregar un
detector nuevo significa escribir la funcion en `v2_detectors.py` y
agregar UNA entrada aqui -- nunca registrarlo por efecto secundario de
importar un modulo."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..contracts.v2_shadow_candidates import V2RuleType, V2ShadowCandidate
from .v2_detector_context import V2DetectorContext
from .v2_detectors import (
    DETECTOR_ID_CALCULATION,
    DETECTOR_ID_LEVEL_88_RETURN_CODE,
    DETECTOR_ID_RETURN_CODE_PROPAGATION,
    DETECTOR_ID_STATE_CHANGE,
    DETECTOR_VERSION_CALCULATION,
    DETECTOR_VERSION_LEVEL_88_RETURN_CODE,
    DETECTOR_VERSION_RETURN_CODE_PROPAGATION,
    DETECTOR_VERSION_STATE_CHANGE,
    detect_calculation,
    detect_level_88_return_code,
    detect_return_code_propagation,
    detect_state_change,
)

DetectorCallable = Callable[[V2DetectorContext], list[V2ShadowCandidate]]


@dataclass(frozen=True)
class V2DetectorDefinition:
    detector_id: str
    detector_version: str
    rule_type: V2RuleType
    callable: DetectorCallable
    description: str


V2_DETECTOR_REGISTRY: dict[str, V2DetectorDefinition] = {
    DETECTOR_ID_RETURN_CODE_PROPAGATION: V2DetectorDefinition(
        detector_id=DETECTOR_ID_RETURN_CODE_PROPAGATION,
        detector_version=DETECTOR_VERSION_RETURN_CODE_PROPAGATION,
        rule_type=V2RuleType.RETURN_CODE_RULE,
        callable=detect_return_code_propagation,
        description=(
            "Decisiones cuyo efecto sobre un DataItem semantic_tag=return_code puede "
            "demostrarse via SemanticPropagation (literal directo o cadena de copias), "
            "aunque Q0 no tenga un LEADS_TO directo suficiente."
        ),
    ),
    DETECTOR_ID_LEVEL_88_RETURN_CODE: V2DetectorDefinition(
        detector_id=DETECTOR_ID_LEVEL_88_RETURN_CODE,
        detector_version=DETECTOR_VERSION_LEVEL_88_RETURN_CODE,
        rule_type=V2RuleType.LEVEL_88_RETURN_CODE_RULE,
        callable=detect_level_88_return_code,
        description=(
            "SET condicion-88 TO TRUE, dentro de una decision, resuelto contra un unico "
            "VALUE, cuyo padre es un DataItem semantic_tag=return_code."
        ),
    ),
    DETECTOR_ID_STATE_CHANGE: V2DetectorDefinition(
        detector_id=DETECTOR_ID_STATE_CHANGE,
        detector_version=DETECTOR_VERSION_STATE_CHANGE,
        rule_type=V2RuleType.STATE_CHANGE_RULE,
        callable=detect_state_change,
        description=(
            "Decisiones que cambian deterministicamente un data item ordinario (no "
            "return_code). Conservador: support=PARTIAL, relevancia funcional no "
            "garantizada."
        ),
    ),
    DETECTOR_ID_CALCULATION: V2DetectorDefinition(
        detector_id=DETECTOR_ID_CALCULATION,
        detector_version=DETECTOR_VERSION_CALCULATION,
        rule_type=V2RuleType.CALCULATION_RULE,
        callable=detect_calculation,
        description=(
            "Calculos (COMPUTE/ADD/SUBTRACT/MULTIPLY/DIVIDE) cuya formula esta "
            "estructuralmente demostrada via SemanticEffect COMPUTE_VALUE. "
            "support=PARTIAL: formula probada, valor numerico runtime no evaluado. "
            "decision_id=None cuando no hay IF/EVALUATE envolvente (calculo "
            "incondicional, productivizado desde 15B3-C2-B2)."
        ),
    ),
}


def ordered_detector_ids() -> list[str]:
    """Orden de ejecucion determinístico: alfabetico por detector_id
    (nunca el orden de insercion del dict, que Python preserva pero que
    no es la garantia que este modulo quiere exponer como contrato)."""
    return sorted(V2_DETECTOR_REGISTRY)
