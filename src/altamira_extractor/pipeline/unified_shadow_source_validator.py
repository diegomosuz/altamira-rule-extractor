"""Validador PURO de integridad de fuentes (Fase 12 Parte 4,
`feat/unified-shadow-differential-validation`).

Verifica, sobre artefactos YA CARGADOS por el servicio (nunca los
carga ni los regenera el mismo): version compatible, hash estable,
`run_id`, `source_package_hash`, referencias encadenadas de hash
(assessment -> review package -> plan -> unified shadow), y
disponibilidad real (`SourceAvailability`) de cada fuente. `V1`,
`assessment`, `review package`, `plan` y `unified candidates shadow`
son SIEMPRE requeridos; `V2`/`interprocedural` son requeridos
UNICAMENTE cuando `unified_shadow` declara su hash (`v2_artifact_hash`/
`interprocedural_artifact_hash` no-nulos).

Nunca corrige un hash silenciosamente, nunca sustituye una fuente
invalida por una vacia."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..contracts.candidate import CandidateArtifact
from ..contracts.candidate_promotion_assessment import (
    CandidatePromotionAssessmentArtifact,
    SourceAvailability,
)
from ..contracts.candidate_promotion_plan import CandidatePromotionPlanArtifact
from ..contracts.candidate_promotion_review import CandidatePromotionReviewPackage
from ..contracts.interprocedural_rule_candidates import InterproceduralRuleCandidatesArtifact
from ..contracts.unified_candidates_shadow import UnifiedCandidatesShadowArtifact
from ..contracts.unified_shadow_validation import (
    UnifiedShadowValidationGate,
    UnifiedShadowValidationIssueCode,
)
from ..contracts.v2_shadow_candidates import V2ShadowCandidatesArtifact
from .unified_shadow_validation_policy import RawFinding

_GATE = UnifiedShadowValidationGate.SOURCE_INTEGRITY
_V1_KEY = "artifacts/06-candidates.json"
_V2_KEY = "v2-candidates-shadow(in-memory)"
_INTERPROCEDURAL_KEY = "interprocedural-rule-candidates-shadow(in-memory)"

Code = UnifiedShadowValidationIssueCode


@dataclass(frozen=True)
class LoadedSource:
    """Vista de UNA fuente ya cargada (o no) por el servicio -- este
    modulo nunca decide COMO se cargo, solo evalua el resultado."""

    artifact: object | None
    availability: SourceAvailability


@dataclass(frozen=True)
class SourceIntegrityResult:
    findings: tuple[RawFinding, ...]
    required_source_missing: bool
    gate_passed: bool


def validate_source_integrity(
    *,
    run_id: str,
    source_package_hash: str,
    v1: LoadedSource,
    v2: LoadedSource,
    interprocedural: LoadedSource,
    assessment: LoadedSource,
    review_package: LoadedSource,
    plan: LoadedSource,
    unified_shadow: LoadedSource,
    candidate_v1_artifact_hash: str | None,
    v2_artifact_hash: str | None,
    interprocedural_artifact_hash: str | None,
    assessment_artifact_hash: str | None,
    review_package_hash: str | None,
    promotion_plan_hash: str | None,
    unified_candidates_shadow_hash: str | None,
) -> SourceIntegrityResult:
    """Punto de entrada puro. Nunca muta ninguna fuente. `required_
    source_missing=True` cuando V1/assessment/review package/plan/
    unified shadow (siempre requeridos) o V2/interprocedural (cuando su
    hash esta declarado en `unified_shadow`) no estan `AVAILABLE`."""
    findings: list[RawFinding] = []
    required_source_missing = False

    for label, source in (
        ("V1", v1),
        ("assessment", assessment),
        ("review_package", review_package),
        ("plan", plan),
        ("unified_shadow", unified_shadow),
    ):
        if source.availability != SourceAvailability.AVAILABLE:
            required_source_missing = True
            code = (
                Code.SOURCE_ARTIFACT_MISSING
                if source.availability == SourceAvailability.NOT_AVAILABLE
                else Code.SOURCE_ARTIFACT_INVALID
            )
            findings.append(RawFinding(code=code, gate=_GATE, diagnostics=(label,)))

    # V2/interprocedural: requeridos UNICAMENTE si el unified shadow
    # declara su hash -- nunca se exige una fuente que el artefacto
    # real nunca referencio.
    unified_shadow_artifact = unified_shadow.artifact
    declares_v2 = (
        isinstance(unified_shadow_artifact, UnifiedCandidatesShadowArtifact)
        and unified_shadow_artifact.v2_artifact_hash is not None
    )
    declares_interprocedural = (
        isinstance(unified_shadow_artifact, UnifiedCandidatesShadowArtifact)
        and unified_shadow_artifact.interprocedural_artifact_hash is not None
    )
    for label, source, declared in (
        ("V2", v2, declares_v2),
        ("interprocedural", interprocedural, declares_interprocedural),
    ):
        if declared and source.availability != SourceAvailability.AVAILABLE:
            required_source_missing = True
            code = (
                Code.SOURCE_ARTIFACT_MISSING
                if source.availability == SourceAvailability.NOT_AVAILABLE
                else Code.SOURCE_ARTIFACT_INVALID
            )
            findings.append(RawFinding(code=code, gate=_GATE, diagnostics=(label,)))

    if required_source_missing:
        # Sin todas las fuentes requeridas presentes, ninguna
        # comparacion de hash encadenado es demostrable -- nunca se
        # intenta igual (evitaria un falso PASS/FAIL parcial).
        return SourceIntegrityResult(
            findings=tuple(findings), required_source_missing=True, gate_passed=False
        )

    v1_artifact = v1.artifact
    v2_artifact = v2.artifact if declares_v2 else None
    interprocedural_artifact = interprocedural.artifact if declares_interprocedural else None
    assessment_artifact = assessment.artifact
    review_package_artifact = review_package.artifact
    plan_artifact = plan.artifact
    assert isinstance(v1_artifact, CandidateArtifact)
    assert isinstance(assessment_artifact, CandidatePromotionAssessmentArtifact)
    assert isinstance(review_package_artifact, CandidatePromotionReviewPackage)
    assert isinstance(plan_artifact, CandidatePromotionPlanArtifact)
    assert isinstance(unified_shadow_artifact, UnifiedCandidatesShadowArtifact)
    if declares_v2:
        assert isinstance(v2_artifact, V2ShadowCandidatesArtifact)
    if declares_interprocedural:
        assert isinstance(interprocedural_artifact, InterproceduralRuleCandidatesArtifact)

    hash_mismatches: list[str] = []

    # run_id / source_package_hash consistentes en TODA la cadena.
    for label, candidate_run_id in (
        ("assessment", assessment_artifact.run_id),
        ("review_package", review_package_artifact.run_id),
        ("plan", plan_artifact.run_id),
        ("unified_shadow", unified_shadow_artifact.run_id),
        ("V1", v1_artifact.run_id),
    ):
        if candidate_run_id != run_id:
            hash_mismatches.append(f"run_id::{label}")
    for label, candidate_hash in (
        ("assessment", assessment_artifact.source_package_hash),
        ("unified_shadow", unified_shadow_artifact.source_package_hash),
        ("V1", v1_artifact.source_package_hash),
    ):
        if candidate_hash != source_package_hash:
            hash_mismatches.append(f"source_package_hash::{label}")

    # Cadena de hashes encadenados -- exactamente igual al binding
    # global que Fase 11 ya exige al construir el artefacto (ver
    # `unified_candidates_shadow_analyzer.py::_check_global_hash_binding`),
    # revalidada aqui contra las fuentes ACTUALES (deteccion de deriva).
    if assessment_artifact_hash != review_package_artifact.assessment_artifact_hash:
        hash_mismatches.append("assessment_artifact_hash::review_package")
    if review_package_hash != plan_artifact.review_package_hash:
        hash_mismatches.append("review_package_hash::plan")
    if assessment_artifact_hash != plan_artifact.assessment_artifact_hash:
        hash_mismatches.append("assessment_artifact_hash::plan")
    if assessment_artifact_hash != unified_shadow_artifact.assessment_artifact_hash:
        hash_mismatches.append("assessment_artifact_hash::unified_shadow")
    if review_package_hash != unified_shadow_artifact.review_package_hash:
        hash_mismatches.append("review_package_hash::unified_shadow")
    if promotion_plan_hash != unified_shadow_artifact.promotion_plan_hash:
        hash_mismatches.append("promotion_plan_hash::unified_shadow")
    if candidate_v1_artifact_hash != unified_shadow_artifact.candidate_v1_artifact_hash:
        hash_mismatches.append("candidate_v1_artifact_hash::unified_shadow")
    if declares_v2 and v2_artifact_hash != unified_shadow_artifact.v2_artifact_hash:
        hash_mismatches.append("v2_artifact_hash::unified_shadow")
    if (
        declares_interprocedural
        and interprocedural_artifact_hash != unified_shadow_artifact.interprocedural_artifact_hash
    ):
        hash_mismatches.append("interprocedural_artifact_hash::unified_shadow")

    # Los hashes declarados en el assessment para V1/V2/interprocedural
    # deben coincidir con los hashes ACTUALES de esas mismas fuentes --
    # un hash desactualizado (fuente cambio desde que se calculo el
    # assessment) se detecta aqui, nunca se corrige silenciosamente.
    if assessment_artifact.source_artifact_hashes.get(_V1_KEY) != candidate_v1_artifact_hash:
        hash_mismatches.append("candidate_v1_artifact_hash::assessment")
    if declares_v2 and assessment_artifact.source_artifact_hashes.get(_V2_KEY) != v2_artifact_hash:
        hash_mismatches.append("v2_artifact_hash::assessment")
    if (
        declares_interprocedural
        and assessment_artifact.source_artifact_hashes.get(_INTERPROCEDURAL_KEY)
        != interprocedural_artifact_hash
    ):
        hash_mismatches.append("interprocedural_artifact_hash::assessment")

    # UNIFIED_ARTIFACT_HASH_MISMATCH: verificacion DEDICADA de roundtrip
    # (Fase 12 Parte 11) -- recalcular el hash del objeto YA CARGADO en
    # memoria (`to_stable_json()`, el mismo metodo que `atomic_write_json`)
    # y compararlo contra el hash que el servicio calculo sobre los
    # BYTES CRUDOS originales del archivo. Una discrepancia aqui es
    # DISTINTA de un hash desactualizado entre artefactos (SOURCE_HASH_
    # MISMATCH): significa que la serializacion Pydantic del objeto
    # recargado no reproduce los bytes originales -- un problema de
    # determinismo/roundtrip, nunca corregido silenciosamente.
    recomputed_unified_shadow_hash = hashlib.sha256(
        unified_shadow_artifact.to_stable_json().encode("utf-8")
    ).hexdigest()
    if unified_candidates_shadow_hash != recomputed_unified_shadow_hash:
        findings.append(
            RawFinding(code=Code.UNIFIED_ARTIFACT_HASH_MISMATCH, gate=_GATE)
        )
        return SourceIntegrityResult(
            findings=tuple(findings), required_source_missing=False, gate_passed=False
        )

    if hash_mismatches:
        findings.append(
            RawFinding(
                code=Code.SOURCE_HASH_MISMATCH,
                gate=_GATE,
                diagnostics=tuple(sorted(set(hash_mismatches))),
            )
        )
        return SourceIntegrityResult(
            findings=tuple(findings), required_source_missing=False, gate_passed=False
        )

    return SourceIntegrityResult(findings=(), required_source_missing=False, gate_passed=True)
