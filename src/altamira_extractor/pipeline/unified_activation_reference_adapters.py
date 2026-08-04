"""Adaptadores PUROS de resultados V1 y unified downstream hacia
referencias comparables (Fase 14A Parte 5,
`feat/controlled-unified-activation`).

Auditoria de la superficie V1 mas estable para comparar (Fase 14A
Parte 1): V1 produce, en orden creciente de "finalidad":

1. `artifacts/06-candidates.json` (`CandidateArtifact`) -- SIEMPRE
   disponible una vez CANDIDATES_DETECTED tuvo exito, estructural
   (`RuleCandidate`: condicion COBOL cruda, `outcome_code`, sin
   `RuleDraft`/veredicto de guardrail).
2. `artifacts/09-guardrails/{sha256(candidate_id)}.json`
   (`GuardrailCandidateArtifact`) -- UNICAMENTE para candidatos que
   alcanzaron `EVIDENCE_VALIDATED`: GUARDRAILS_APPLIED es fail-fast y
   atomico (`pipeline/guardrails_applied_stage.py`) -- un candidato
   `REJECTED` NUNCA llega a persistirse aqui, su unico rastro en disco
   sigue siendo el `RuleCandidate` estructural del punto 1 (mas un
   `RuleDraft` PENDING inmutable en `artifacts/08-rule-drafts/`, que
   esta fase NUNCA lee: no representa un resultado final). Contiene
   `final_rule_draft: RuleDraft` (con `evidence_validation_status`
   actualizado a `EVIDENCE_VALIDATED`) y `guardrail_report`.
3. `artifacts/10-rules/{hash}.md` (+ `rules-manifest.json`) -- el
   MISMO conjunto de candidatos que el punto 2 (1:1, ver
   `contracts/rules_manifest.py`), solo que renderizado a Markdown;
   nunca aporta un dato COMPARABLE adicional mas alla de una
   `rule_id`/nombre de archivo.

`UnifiedActivationV1Reference.level=RULE` exige el punto 2
(`rule_draft_id` no-`None`); en cualquier otro caso `level=CANDIDATE`
(unicamente el punto 1). Esta fase NUNCA compara un candidato
estructural V1 contra un draft/regla unified como si fueran
equivalentes sin declarar ese nivel (ver `pipeline/
unified_activation_comparator.py`, que exige el MISMO nivel para
`EXACT_EQUIVALENT`).

`target` de V1 es SIEMPRE `None` (V1 nunca lo expone -- mismo
principio ya establecido por `pipeline/candidate_source_adapters.py::
adapt_v1_candidates`, Fase 9). `program`/`paragraph` se derivan de
`paragraph_id` por POSICION textual fija del esquema de IDs
(`pipeline/identifiers.py::ProgramIdentity`), nunca por semejanza --
`_program_name_from_paragraph_id` esta deliberadamente DUPLICADO aqui
(mismo patron que Fase 9/Fase 8: nunca compartido via un modulo comun
fuera de alcance de esta fase).

Para unified (Fase 11-13): identidad principal = `group_id`
(`unified_shadow_candidate_id`), NUNCA un `source_candidate_id`
individual -- ningun member se elige "ganador". `level=RULE` exige que
`UnifiedShadowDownstreamArtifact` haya ejecutado ese grupo
(`context_package_record_id`/`rule_draft_record_id` presentes); en
cualquier otro caso `level=CANDIDATE` (unicamente el grupo shadow,
Fase 11, sin haber pasado por el flujo downstream de Fase 13).

Ninguna fuente se muta. Ninguna fuente ausente (`None`) fabrica una
referencia sintetica -- produce lista vacia."""

from __future__ import annotations

from ..contracts.candidate import CandidateArtifact
from ..contracts.guardrail_candidate import GuardrailCandidateArtifact
from ..contracts.unified_activation_evaluation import (
    UnifiedActivationComparisonLevel,
    UnifiedActivationUnifiedReference,
    UnifiedActivationV1Reference,
)
from ..contracts.unified_candidates_shadow import UnifiedCandidatesShadowArtifact
from ..contracts.unified_shadow_downstream import (
    UnifiedShadowDownstreamArtifact,
    UnifiedShadowGuardrailStatus,
)


def _program_name_from_paragraph_id(paragraph_id: str) -> str | None:
    """Duplicado deliberado de `pipeline/candidate_source_adapters.py::
    _program_name_from_paragraph_id` (formato: `program::{country}::
    {logical_name}::{program_name}::{version}::{hash12}::paragraph::
    {NAME}[...]`) -- `None` si el formato no coincide, nunca se
    adivina."""
    parts = paragraph_id.split("::")
    if len(parts) < 4 or parts[0] != "program":
        return None
    return parts[3]


def _normalize_statement(text: str) -> str:
    """Normalizacion UNICAMENTE de espacios en blanco (strip +
    colapso de espacios repetidos) -- nunca casefold, nunca
    reescritura semantica: una diferencia de mayusculas/minusculas
    sigue siendo una diferencia real, nunca ruido."""
    return " ".join(text.split())


def _v1_reference_id(candidate_id: str) -> str:
    return f"activation::v1::{candidate_id}"


def adapt_v1_references(
    candidate_artifact: CandidateArtifact | None,
    *,
    guardrail_artifacts_by_candidate_id: dict[str, GuardrailCandidateArtifact] | None = None,
    rule_markdown_filename_by_candidate_id: dict[str, str] | None = None,
) -> list[UnifiedActivationV1Reference]:
    """Punto de entrada puro. `candidate_artifact is None` (fuente no
    disponible) siempre produce `[]`. Nunca muta ninguna fuente."""
    if candidate_artifact is None:
        return []
    guardrail_by_id = guardrail_artifacts_by_candidate_id or {}
    markdown_by_id = rule_markdown_filename_by_candidate_id or {}

    references: list[UnifiedActivationV1Reference] = []
    for candidate in candidate_artifact.candidates:
        guardrail_artifact = guardrail_by_id.get(candidate.candidate_id)
        program = _program_name_from_paragraph_id(candidate.paragraph_id)

        if guardrail_artifact is not None:
            references.append(
                UnifiedActivationV1Reference(
                    reference_id=_v1_reference_id(candidate.candidate_id),
                    source_candidate_id=candidate.candidate_id,
                    level=UnifiedActivationComparisonLevel.RULE,
                    rule_draft_id=candidate.candidate_id,
                    rule_id=markdown_by_id.get(candidate.candidate_id),
                    rule_family="RETURN_CODE",
                    program=program,
                    paragraph=candidate.paragraph_name,
                    target=None,
                    output_literal=candidate.outcome_code,
                    statement=_normalize_statement(guardrail_artifact.final_rule_draft.statement),
                    evidence_ids=[],
                    provenance_references=sorted(
                        {f"{candidate.source_file}::{candidate.line_start}"}
                    ),
                )
            )
        else:
            references.append(
                UnifiedActivationV1Reference(
                    reference_id=_v1_reference_id(candidate.candidate_id),
                    source_candidate_id=candidate.candidate_id,
                    level=UnifiedActivationComparisonLevel.CANDIDATE,
                    rule_draft_id=None,
                    rule_id=None,
                    rule_family="RETURN_CODE",
                    program=program,
                    paragraph=candidate.paragraph_name,
                    target=None,
                    output_literal=candidate.outcome_code,
                    statement=None,
                    evidence_ids=[],
                    provenance_references=sorted(
                        {f"{candidate.source_file}::{candidate.line_start}"}
                    ),
                )
            )
    return sorted(references, key=lambda r: r.reference_id)


def _unified_reference_id(group_id: str) -> str:
    return f"activation::unified::{group_id}"


def adapt_unified_references(
    unified_shadow: UnifiedCandidatesShadowArtifact | None,
    *,
    downstream: UnifiedShadowDownstreamArtifact | None = None,
) -> list[UnifiedActivationUnifiedReference]:
    """Punto de entrada puro. `unified_shadow is None` siempre produce
    `[]`. `downstream` es opcional: cuando esta ausente (o el grupo no
    fue ejecutado por Fase 13), `level=CANDIDATE` -- unicamente el
    grupo shadow (Fase 11), sin `statement`/`guardrail_status`
    reales."""
    if unified_shadow is None:
        return []

    members_by_id = {m.member_id: m for m in unified_shadow.shadow_members}
    group_result_by_id = {}
    draft_by_record_id = {}
    guardrail_by_record_id = {}
    if downstream is not None:
        group_result_by_id = {gr.group_id: gr for gr in downstream.group_results}
        draft_by_record_id = {d.record_id: d for d in downstream.rule_drafts}
        guardrail_by_record_id = {g.record_id: g for g in downstream.guardrail_results}

    references: list[UnifiedActivationUnifiedReference] = []
    for group in unified_shadow.shadow_groups:
        members = [members_by_id[member_id] for member_id in group.member_ids]
        source_candidate_ids = sorted({m.source_candidate_id for m in members})

        rule_draft_record_id: str | None = None
        statement: str | None = None
        guardrail_status = UnifiedShadowGuardrailStatus.NOT_EVALUATED

        group_result = group_result_by_id.get(group.unified_shadow_candidate_id)
        if group_result is not None and group_result.rule_draft_record_id is not None:
            rule_draft_record_id = group_result.rule_draft_record_id
            draft_record = draft_by_record_id.get(rule_draft_record_id)
            if draft_record is not None:
                statement = _normalize_statement(draft_record.rule_draft.statement)
            if group_result.guardrail_record_id is not None:
                guardrail_record = guardrail_by_record_id.get(group_result.guardrail_record_id)
                if guardrail_record is not None:
                    guardrail_status = guardrail_record.status

        level = (
            UnifiedActivationComparisonLevel.RULE
            if rule_draft_record_id is not None
            else UnifiedActivationComparisonLevel.CANDIDATE
        )

        references.append(
            UnifiedActivationUnifiedReference(
                reference_id=_unified_reference_id(group.unified_shadow_candidate_id),
                group_id=group.unified_shadow_candidate_id,
                level=level,
                member_ids=sorted(group.member_ids),
                source_candidate_ids=source_candidate_ids,
                rule_draft_record_id=rule_draft_record_id,
                rule_family=group.rule_family.value,
                program=group.program,
                target=group.target,
                output_literal=group.output_literal,
                statement=statement,
                evidence_ids=sorted(group.evidence_ids),
                provenance_references=sorted(group.provenance_references),
                guardrail_status=guardrail_status,
            )
        )
    return sorted(references, key=lambda r: r.reference_id)


__all__ = ["adapt_unified_references", "adapt_v1_references"]
