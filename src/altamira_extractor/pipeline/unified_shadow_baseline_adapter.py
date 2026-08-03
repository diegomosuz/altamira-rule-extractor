"""Adaptador PURO de `CandidateArtifact` V1 hacia
`UnifiedBaselineCandidateReference` (Fase 11 de la ampliacion
semantica, `feat/unified-candidate-artifact-shadow`).

Convierte CADA `RuleCandidate` de V1 en una referencia INMUTABLE del
lane `BASELINE_V1` -- nunca modifica V1, nunca completa un campo
ausente con V2/interprocedural, nunca fabrica un valor, y NUNCA
constituye, por si misma, una promocion: es la misma adaptacion
conceptual que `pipeline/candidate_source_adapters.py::
adapt_v1_candidates` (Fase 9), duplicada deliberadamente aqui (mismo
precedente que `_program_name_from_paragraph_id` en Fase 8/9: un
helper pequeno, estable, se prefiere duplicado a compartido entre
fases para no crear un acoplamiento nuevo fuera de alcance) para que
Fase 11 nunca dependa de -- ni pueda romper con un cambio propio -- el
modulo de Fase 9."""

from __future__ import annotations

import hashlib

from ..contracts.candidate import CandidateArtifact, RuleCandidate
from ..contracts.candidate_promotion_assessment import UnifiedRuleFamily
from ..contracts.unified_candidates_shadow import UnifiedBaselineCandidateReference
from .errors import UnifiedCandidatesShadowError

# Q0 (`queries/v1/q0_candidates.cypher`) exige estructuralmente
# `sink.semantic_tag = 'return_code'` en su unico MATCH -- toda fila que
# Q0 devuelve, por construccion de la consulta, describe una asignacion
# de codigo de retorno (mismo razonamiento que Fase 9,
# `pipeline/candidate_source_adapters.py::_V1_RULE_FAMILY`).
_V1_RULE_FAMILY = UnifiedRuleFamily.RETURN_CODE


def _digest(*parts: str) -> str:
    canonical = "\x1f".join(parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def baseline_reference_id_for(source_candidate_id: str) -> str:
    """Unica funcion que genera `baseline_reference_id` -- determinista
    (SHA-256, nunca UUID/timestamp/`hash()` de Python)."""
    return f"baseline::{_digest(source_candidate_id)}"


def _program_name_from_paragraph_id(paragraph_id: str) -> str | None:
    """Extrae `program_name` de un `paragraph_id` Neo4j-shaped por
    POSICION textual fija -- mismo formato/razonamiento que
    `pipeline/candidate_source_adapters.py::_program_name_from_paragraph_id`
    (Fase 9), duplicado deliberadamente."""
    parts = paragraph_id.split("::")
    if len(parts) < 4 or parts[0] != "program":
        return None
    return parts[3]


def _hash_candidate(candidate: RuleCandidate) -> str:
    return hashlib.sha256(candidate.to_stable_json().encode("utf-8")).hexdigest()


def adapt_v1_baseline_candidates(
    artifact: CandidateArtifact, *, source_artifact_hash: str
) -> list[UnifiedBaselineCandidateReference]:
    """Punto de entrada puro. Nunca muta `artifact`. TODO candidato V1
    produce una `UnifiedBaselineCandidateReference`, sea o no
    referenciado por algun plan item -- el baseline es independiente
    del plan."""
    references = []
    for candidate in artifact.candidates:
        program = _program_name_from_paragraph_id(candidate.paragraph_id)
        if program is None:
            raise UnifiedCandidatesShadowError(
                f"candidato V1 {candidate.candidate_id!r} tiene un paragraph_id con formato "
                "inesperado: no se puede extraer program sin fabricar un valor"
            )
        references.append(
            UnifiedBaselineCandidateReference(
                baseline_reference_id=baseline_reference_id_for(candidate.candidate_id),
                source_candidate_id=candidate.candidate_id,
                source_artifact_hash=source_artifact_hash,
                original_candidate_hash=_hash_candidate(candidate),
                rule_family=_V1_RULE_FAMILY,
                original_rule_type=candidate.rule_type,
                original_support=candidate.status.value,
                program=program,
                paragraph=candidate.paragraph_name,
                decision_id=candidate.decision_id,
                target=None,
                input_literal=None,
                output_literal=candidate.outcome_code,
                evidence_ids=[],
                provenance_references=sorted({f"{candidate.source_file}::{candidate.line_start}"}),
                diagnostics=[],
            )
        )
    return sorted(references, key=lambda reference: reference.baseline_reference_id)
