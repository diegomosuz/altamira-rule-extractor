"""Contrato tipado del artefacto 06-candidates.json (salida de Q0).

Un RuleCandidate es un patron estructural detectado, no una regla
confirmada (CLAUDE.md, seccion 'Candidato, fidelidad y aprobacion').

`rule_type` es `str | None`: `StatementKind.IF`/`EVALUATE` son
construcciones tecnicas de COBOL, no evidencia de un tipo funcional de
regla de negocio. Ninguna etapa anterior (SemanticGraphBuilder,
CandidateDetector) deriva o inventa un valor — se conserva `None` tal
como existe en el grafo hasta que una etapa posterior cuente con
evidencia funcional demostrable. No bloquea CANDIDATES_DETECTED ni
CONTEXTS_BUILT.

`detector_score` (Prompt 10a): el unico detector V1 (`q0-return-code-
decision`, ver `pipeline/candidate_detector.py`) es un predicado
estructural binario — Q0 usa `MATCH` exacto, nunca `OPTIONAL`, asi que
toda fila devuelta ya cumplio el patron completo. Por eso
`detector_score` es siempre la constante `1.0`, cuya UNICA semantica es
"la fila satisface completamente el predicado estructural definido por
Q0". No significa confianza funcional, certeza de regla de negocio,
validacion humana, aplicabilidad parametrica ni efecto de negocio
confirmado — ninguno de esos conceptos se deriva ni se combina aqui
(en particular, nunca se mezcla con `DataItem.semantic_confidence`, que
proviene de una fuente distinta, `SemanticTagger`). Si la semantica de
Q0 cambia en el futuro, `detector_version` debe incrementarse.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, RelativePath, Sha256Hex
from .enums import CandidateStatus


class RuleCandidate(AltamiraBaseModel):
    schema_version: Literal["1.0"] = "1.0"
    candidate_id: str = Field(min_length=1)
    paragraph_id: str = Field(min_length=1)
    paragraph_name: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    detector_id: str = Field(min_length=1)
    detector_version: str = Field(min_length=1)
    detector_score: float = Field(ge=0, le=1)
    status: CandidateStatus = CandidateStatus.DETECTED_CANDIDATE
    condition: str = Field(min_length=1)
    outcome_code: str | None = None
    rule_type: str | None = None
    line_start: int = Field(ge=1)
    source_file: RelativePath
    source_package_hash: Sha256Hex


class CandidateArtifact(AltamiraBaseModel):
    """Contenedor persistido en artifacts/06-candidates.json.

    `candidates=[]` es un resultado valido cuando Q0 no encuentra
    coincidencias — ausencia de candidatos es un hallazgo legitimo, no
    un error de la etapa. Sin timestamp por diseno (determinismo:
    `StageExecution` ya registra inicio/fin/duracion)."""

    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    source_package_hash: Sha256Hex
    semantic_graph_hash: Sha256Hex
    invariants_query_hash: Sha256Hex
    q0_query_hash: Sha256Hex
    candidates: list[RuleCandidate] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_candidates_sorted_and_unique(self) -> CandidateArtifact:
        ids = [candidate.candidate_id for candidate in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("candidates contiene candidate_id duplicado")
        if ids != sorted(ids):
            raise ValueError("candidates no esta ordenado deterministicamente por candidate_id")
        return self

    @model_validator(mode="after")
    def _check_candidates_match_artifact_source_package_hash(self) -> CandidateArtifact:
        for candidate in self.candidates:
            if candidate.source_package_hash != self.source_package_hash:
                raise ValueError(
                    f"RuleCandidate {candidate.candidate_id!r} tiene source_package_hash "
                    "distinto del artefacto"
                )
        return self

    @model_validator(mode="after")
    def _check_candidates_status_is_detected_candidate(self) -> CandidateArtifact:
        for candidate in self.candidates:
            if candidate.status != CandidateStatus.DETECTED_CANDIDATE:
                raise ValueError(
                    f"RuleCandidate {candidate.candidate_id!r} debe tener "
                    "status=DETECTED_CANDIDATE"
                )
        return self

    @model_validator(mode="after")
    def _check_warnings_sorted_and_unique(self) -> CandidateArtifact:
        if len(self.warnings) != len(set(self.warnings)):
            raise ValueError("warnings contiene duplicados")
        if self.warnings != sorted(self.warnings):
            raise ValueError("warnings no esta ordenado deterministicamente")
        return self
