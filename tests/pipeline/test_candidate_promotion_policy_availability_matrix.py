"""Matriz exhaustiva de disponibilidad de fuentes x disposition (Fase 9,
`feat/unified-candidate-promotion-assessment`, auditoria de cierre
Parte 1). Codifica, para las 11 combinaciones de `SourceAvailability`
listadas en la auditoria, el comportamiento EXACTO y ya verificado de
`pipeline/candidate_promotion_policy.py::evaluate_candidate` para
referencias V2 e INTERPROCEDURAL -- nunca "reservado a V1" de forma
ambigua: la regla precisa es

    NOT_EVALUATED se aplica a la referencia (V2 o INTERPROCEDURAL) bajo
    evaluacion exactamente cuando V1 -- la unica fuente con prioridad
    para decidir ALREADY_COVERED -- esta SourceAvailability.NOT_AVAILABLE
    (ausencia legitima). V1 INVALID NUNCA produce NOT_EVALUATED: una
    fuente invalida no es una ausencia legitima sino un artefacto no
    confiable, y bloquea la evaluacion (BLOCKED), nunca la deja
    "pendiente". La ausencia/invalidez de V2/INTERPROCEDURAL (la fuente
    de CORROBORACION, no la de cobertura) nunca produce NOT_EVALUATED
    para la referencia bajo evaluacion: solo degrada el criterio
    `INDEPENDENT_CORROBORATION` (NOT_EVALUATED si NOT_AVAILABLE, FAIL si
    INVALID) -- REVIEW_REQUIRED sigue siendo una conclusion COMPLETA y
    valida (nunca "no se pudo evaluar"), salvo que la propia fuente de
    corroboracion este INVALID, que si bloquea (BLOCKED) porque significa
    que el propio calculo de comparacion fallo, no que falte evidencia."""

from __future__ import annotations

import pytest

from altamira_extractor.contracts.candidate_promotion_assessment import (
    CandidateSource,
    PromotionCriterionStatus,
    PromotionDisposition,
    SourceAvailability,
    UnifiedCandidateReference,
    UnifiedRuleFamily,
)
from altamira_extractor.pipeline.candidate_promotion_policy import evaluate_candidate

HASH = "d" * 64


def _clean_reference(source: CandidateSource, ref_id: str) -> UnifiedCandidateReference:
    """Un candidato "limpio": deterministico, con target/output/evidence,
    sin barreras, familia soportada -- ninguno de los criterios de
    calidad propios falla, asi que cualquier disposition observada se
    debe EXCLUSIVAMENTE a la disponibilidad de fuentes, nunca a un
    defecto del candidato mismo."""
    return UnifiedCandidateReference(
        unified_reference_id=ref_id,
        source=source,
        source_candidate_id=ref_id,
        source_artifact_hash=HASH,
        rule_family=UnifiedRuleFamily.RETURN_CODE,
        original_support="DETERMINISTIC",
        program="CALLER",
        target="WS-X",
        output_literal="R001",
        evidence_ids=["evidence::1"],
    )


AVAILABLE = SourceAvailability.AVAILABLE
NOT_AVAILABLE = SourceAvailability.NOT_AVAILABLE
INVALID = SourceAvailability.INVALID

V2 = CandidateSource.V2
IP = CandidateSource.INTERPROCEDURAL

# (id del runbook, V1, V2, INTERPROCEDURAL, fuentes bajo prueba)
_Combo = tuple[
    str, SourceAvailability, SourceAvailability, SourceAvailability, tuple[CandidateSource, ...]
]

_COMBOS: list[_Combo] = [
    ("1_all_available", AVAILABLE, AVAILABLE, AVAILABLE, (V2, IP)),
    ("2_v1_not_available", NOT_AVAILABLE, AVAILABLE, AVAILABLE, (V2, IP)),
    ("3_v1_invalid", INVALID, AVAILABLE, AVAILABLE, (V2, IP)),
    ("4_v2_not_available", AVAILABLE, NOT_AVAILABLE, AVAILABLE, (IP,)),
    ("5_v2_invalid", AVAILABLE, INVALID, AVAILABLE, (IP,)),
    ("6_interprocedural_not_available", AVAILABLE, AVAILABLE, NOT_AVAILABLE, (V2,)),
    ("7_interprocedural_invalid", AVAILABLE, AVAILABLE, INVALID, (V2,)),
    ("8_only_v1", AVAILABLE, NOT_AVAILABLE, NOT_AVAILABLE, ()),
    ("9_only_v2", NOT_AVAILABLE, AVAILABLE, NOT_AVAILABLE, (V2,)),
    ("10_only_interprocedural", NOT_AVAILABLE, NOT_AVAILABLE, AVAILABLE, (IP,)),
    ("11_none_available", NOT_AVAILABLE, NOT_AVAILABLE, NOT_AVAILABLE, ()),
]


def _availability_for(combo: _Combo) -> dict[CandidateSource, SourceAvailability]:
    _, v1_av, v2_av, ip_av, _ = combo
    return {
        CandidateSource.V1: v1_av,
        CandidateSource.V2: v2_av,
        CandidateSource.INTERPROCEDURAL: ip_av,
    }


@pytest.mark.parametrize("combo", _COMBOS, ids=[c[0] for c in _COMBOS])
def test_availability_matrix_no_v1_exact_match(combo: _Combo) -> None:
    """Sin coincidencia exacta con V1 declarada -- ver docstring del
    modulo para la regla exacta que determina cada disposition."""
    _, v1_av, v2_av, ip_av, sources_under_test = combo
    availability = _availability_for(combo)
    v1_ref = _clean_reference(CandidateSource.V1, "unified::v1::a")

    for source in sources_under_test:
        under_test = _clean_reference(source, f"unified::{source.value.lower()}::x")
        reference_by_id = {
            under_test.unified_reference_id: under_test,
            v1_ref.unified_reference_id: v1_ref,
        }
        assessment = evaluate_candidate(
            under_test,
            reference_by_id=reference_by_id,
            exact_match_reference_ids=[],
            related_reference_ids=[],
            conflict_ids=[],
            source_availability=availability,
        )
        criteria = {c.criterion.value: c.status for c in assessment.criteria}

        if v1_av == NOT_AVAILABLE:
            assert assessment.disposition == PromotionDisposition.NOT_EVALUATED, combo
            assert criteria["V1_COMPARISON_AVAILABLE"] == PromotionCriterionStatus.NOT_EVALUATED
        elif v1_av == INVALID:
            assert assessment.disposition == PromotionDisposition.BLOCKED, combo
            assert criteria["V1_COMPARISON_AVAILABLE"] == PromotionCriterionStatus.FAIL
        else:
            assert criteria["V1_COMPARISON_AVAILABLE"] == PromotionCriterionStatus.PASS
            corroboration_source = IP if source == V2 else V2
            corroboration_availability = availability[corroboration_source]
            if corroboration_availability == INVALID:
                assert assessment.disposition == PromotionDisposition.BLOCKED, combo
            else:
                # Sin match V1 y sin match de corroboracion (no se declaro
                # ninguno): nunca READY, nunca NOT_EVALUATED -- siempre una
                # conclusion completa REVIEW_REQUIRED.
                assert assessment.disposition == PromotionDisposition.REVIEW_REQUIRED, combo
                corroboration_criterion = (
                    "INTERPROCEDURAL_COMPARISON_AVAILABLE"
                    if source == CandidateSource.V2
                    else "V2_COMPARISON_AVAILABLE"
                )
                expected = (
                    PromotionCriterionStatus.NOT_EVALUATED
                    if corroboration_availability == NOT_AVAILABLE
                    else PromotionCriterionStatus.PASS
                )
                assert criteria[corroboration_criterion] == expected, combo


@pytest.mark.parametrize("combo", _COMBOS, ids=[c[0] for c in _COMBOS])
def test_availability_matrix_with_v1_exact_match(combo: _Combo) -> None:
    """Con una coincidencia exacta con V1 YA declarada (solo tiene
    sentido si V1 esta realmente disponible; para V1 ausente/invalido se
    reafirma que ninguna cantidad de evidencia de corroboracion permite
    concluir ALREADY_COVERED)."""
    _, v1_av, v2_av, ip_av, sources_under_test = combo
    availability = _availability_for(combo)
    v1_ref = _clean_reference(CandidateSource.V1, "unified::v1::a")

    for source in sources_under_test:
        under_test = _clean_reference(source, f"unified::{source.value.lower()}::x")
        reference_by_id = {
            under_test.unified_reference_id: under_test,
            v1_ref.unified_reference_id: v1_ref,
        }
        assessment = evaluate_candidate(
            under_test,
            reference_by_id=reference_by_id,
            exact_match_reference_ids=[v1_ref.unified_reference_id],
            related_reference_ids=[],
            conflict_ids=[],
            source_availability=availability,
        )
        if v1_av == AVAILABLE:
            corroboration_source = IP if source == V2 else V2
            if availability[corroboration_source] == INVALID:
                assert assessment.disposition == PromotionDisposition.BLOCKED, combo
            else:
                assert assessment.disposition == PromotionDisposition.ALREADY_COVERED, combo
                assert assessment.exact_match_reference_ids == [v1_ref.unified_reference_id]
        elif v1_av == NOT_AVAILABLE:
            # Defensivo: un exact_match_reference_ids con un V1 no puede
            # ocurrir en la practica (V1 ausente nunca produce
            # referencias), pero la politica NUNCA debe interpretarlo
            # como ALREADY_COVERED -- V1 ausente sigue produciendo
            # NOT_EVALUATED incluso ante esa entrada defensiva.
            assert assessment.disposition == PromotionDisposition.NOT_EVALUATED, combo
        else:
            assert assessment.disposition == PromotionDisposition.BLOCKED, combo


def test_not_evaluated_never_applies_when_v1_available_but_corroboration_source_absent() -> None:
    """Aclaracion explicita de "NOT_EVALUATED reservado a V1": la
    ausencia de V2/INTERPROCEDURAL (fuente de CORROBORACION) con V1
    disponible produce REVIEW_REQUIRED -- una conclusion completa --
    NUNCA NOT_EVALUATED. Solo la ausencia de V1 (fuente de COBERTURA)
    produce NOT_EVALUATED."""
    availability = {
        CandidateSource.V1: AVAILABLE,
        CandidateSource.V2: AVAILABLE,
        CandidateSource.INTERPROCEDURAL: NOT_AVAILABLE,
    }
    v2_ref = _clean_reference(CandidateSource.V2, "unified::v2::a")
    assessment = evaluate_candidate(
        v2_ref,
        reference_by_id={v2_ref.unified_reference_id: v2_ref},
        exact_match_reference_ids=[],
        related_reference_ids=[],
        conflict_ids=[],
        source_availability=availability,
    )
    assert assessment.disposition == PromotionDisposition.REVIEW_REQUIRED
    assert assessment.disposition != PromotionDisposition.NOT_EVALUATED


def test_invalid_source_never_silently_becomes_not_evaluated() -> None:
    """V1 INVALID debe bloquear (BLOCKED), nunca degradar a
    NOT_EVALUATED -- una fuente invalida es un hecho mas fuerte que una
    fuente simplemente ausente, nunca se trata como equivalente."""
    availability = {
        CandidateSource.V1: INVALID,
        CandidateSource.V2: AVAILABLE,
        CandidateSource.INTERPROCEDURAL: AVAILABLE,
    }
    v2_ref = _clean_reference(CandidateSource.V2, "unified::v2::a")
    assessment = evaluate_candidate(
        v2_ref,
        reference_by_id={v2_ref.unified_reference_id: v2_ref},
        exact_match_reference_ids=[],
        related_reference_ids=[],
        conflict_ids=[],
        source_availability=availability,
    )
    assert assessment.disposition == PromotionDisposition.BLOCKED
    assert assessment.disposition != PromotionDisposition.NOT_EVALUATED


def test_interprocedural_only_never_reaches_ready_even_with_all_sources_available() -> None:
    """INTERPROCEDURAL_ONLY (sin relacion real V1/V2, todas las fuentes
    disponibles) nunca es suficiente por si solo para READY_FOR_
    CONTROLLED_REVIEW -- exige ademas una corroboracion EXACTA."""
    availability = {
        CandidateSource.V1: AVAILABLE,
        CandidateSource.V2: AVAILABLE,
        CandidateSource.INTERPROCEDURAL: AVAILABLE,
    }
    ip_ref = _clean_reference(CandidateSource.INTERPROCEDURAL, "unified::interprocedural::a")
    assessment = evaluate_candidate(
        ip_ref,
        reference_by_id={ip_ref.unified_reference_id: ip_ref},
        exact_match_reference_ids=[],
        related_reference_ids=[],
        conflict_ids=[],
        source_availability=availability,
    )
    assert assessment.disposition == PromotionDisposition.REVIEW_REQUIRED
    assert assessment.disposition != PromotionDisposition.READY_FOR_CONTROLLED_REVIEW


def test_comparison_availability_criteria_always_match_source_availability() -> None:
    """V1_COMPARISON_AVAILABLE/V2_COMPARISON_AVAILABLE/INTERPROCEDURAL_
    COMPARISON_AVAILABLE deben concordar exactamente con
    source_availability: AVAILABLE->PASS, NOT_AVAILABLE->NOT_EVALUATED,
    INVALID->FAIL (nunca un valor inconsistente)."""
    for v1_av in (AVAILABLE, NOT_AVAILABLE, INVALID):
        availability = {
            CandidateSource.V1: v1_av,
            CandidateSource.V2: AVAILABLE,
            CandidateSource.INTERPROCEDURAL: AVAILABLE,
        }
        v2_ref = _clean_reference(CandidateSource.V2, "unified::v2::a")
        assessment = evaluate_candidate(
            v2_ref,
            reference_by_id={v2_ref.unified_reference_id: v2_ref},
            exact_match_reference_ids=[],
            related_reference_ids=[],
            conflict_ids=[],
            source_availability=availability,
        )
        criteria = {c.criterion.value: c.status for c in assessment.criteria}
        expected = {
            AVAILABLE: PromotionCriterionStatus.PASS,
            NOT_AVAILABLE: PromotionCriterionStatus.NOT_EVALUATED,
            INVALID: PromotionCriterionStatus.FAIL,
        }[v1_av]
        assert criteria["V1_COMPARISON_AVAILABLE"] == expected
