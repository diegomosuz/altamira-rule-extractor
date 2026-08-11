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

`candidate_source`/`rule_family`/`evidence_ids` (Fase 15B3-B/15B3-B1,
`pipeline/enhanced_candidate_integration.py`): campos de procedencia,
opcionales por default y retrocompatibles -- un `RuleCandidate` V1 (Q0)
tiene siempre `candidate_source=V1`, `rule_family=RETURN_CODE`
(garantia estructural de Q0, ver `pipeline/
candidate_source_adapters.py::_V1_RULE_FAMILY`) y `evidence_ids=[]`
salvo que la deteccion ampliada haya fusionado evidencia V2 en el (regla
D: `evidence_ids` se enriquece via `model_copy`, nunca se muta el
candidato V1 original en su lugar). Un `RuleCandidate` agregado por la
deteccion ampliada (V2, opt-in via `Settings.enhanced_candidates_enabled`)
declara su propio `candidate_source`/`rule_family`/`evidence_ids`. Estos
campos nunca implican aprobacion funcional ni cambian el significado de
`status` (`DETECTED_CANDIDATE` sigue siendo el unico status valido
aqui).

`support_level` (auditado y eliminado en Fase 15B3-B1): existio
brevemente en la primera version de esta fase como
`Literal["DETERMINISTIC"]`. Se elimino porque tenia un unico valor
posible (todo candidato que llega a este contrato ya es DETERMINISTIC
por construccion -- `PARTIAL`/`BLOCKED` se descartan antes en
`enhanced_candidate_integration.py`), ningun consumidor tomaba
decisiones con el, y solo anticipaba niveles de soporte hipoteticos
futuros -- exactamente el patron que CLAUDE.md pide evitar ("No
implementar un half-finished... No disenar para requisitos futuros
hipoteticos"). No se reemplazo por otro enum ni por una jerarquia
nueva.

`decision_id`/`condition` (Fase 15B3-C2-B2): `str | None`, no
opcionales para todas las familias por igual. Todo `rule_family`
salvo `CALCULATION` sigue exigiendo ambos no-None (RETURN_CODE/
LEVEL_88_RETURN_CODE/STATE_TRANSITION nacen anclados a una `Decision`
real, sin excepcion). `CALCULATION` es la UNICA familia que puede
representar un calculo aritmetico (COMPUTE/ADD/SUBTRACT/MULTIPLY/
DIVIDE) SIN `Decision` envolvente -- nunca con un `decision_id`/
`condition` sintetico (`"<statement-id>"`, `"TRUE"`, `"UNCONDITIONAL"`
estan prohibidos por diseno): "ser incondicional" se deriva
EXCLUSIVAMENTE de `decision_id is None`/`condition is None`, nunca de
un valor textual fabricado. Ver `_check_decision_anchor_by_family`."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, RelativePath, Sha256Hex
from .candidate_promotion_assessment import CandidateSource, UnifiedRuleFamily
from .enums import CandidateStatus


class RuleCandidate(AltamiraBaseModel):
    schema_version: Literal["1.0"] = "1.0"
    candidate_id: str = Field(min_length=1)
    paragraph_id: str = Field(min_length=1)
    paragraph_name: str = Field(min_length=1)
    decision_id: str | None = Field(default=None, min_length=1)
    detector_id: str = Field(min_length=1)
    detector_version: str = Field(min_length=1)
    detector_score: float = Field(ge=0, le=1)
    status: CandidateStatus = CandidateStatus.DETECTED_CANDIDATE
    condition: str | None = Field(default=None, min_length=1)
    outcome_code: str | None = None
    rule_type: str | None = None
    line_start: int = Field(ge=1)
    source_file: RelativePath
    source_package_hash: Sha256Hex
    candidate_source: CandidateSource = CandidateSource.V1
    rule_family: UnifiedRuleFamily = UnifiedRuleFamily.RETURN_CODE
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_evidence_ids_sorted_and_unique(self) -> RuleCandidate:
        if self.evidence_ids != sorted(set(self.evidence_ids)):
            raise ValueError(
                f"RuleCandidate {self.candidate_id!r}: evidence_ids debe estar ordenado y sin "
                "duplicados"
            )
        return self

    @model_validator(mode="after")
    def _check_decision_anchor_by_family(self) -> RuleCandidate:
        """Fase 15B3-C2-B2: unico punto que decide cuando `decision_id`/
        `condition` pueden faltar. Toda familia salvo CALCULATION sigue
        exigiendo ambos (comportamiento V1/STATE_TRANSITION/LEVEL_88
        sin cambios, verificado bit a bit por los tests historicos).
        CALCULATION admite dos formas validas -- condicionada (ambos
        no-None, sin cambios desde 15B3-C2-B1) o incondicional (ambos
        None) -- pero nunca un estado mixto (uno presente y el otro no),
        que indicaria una construccion parcial/inconsistente."""
        if self.rule_family != UnifiedRuleFamily.CALCULATION:
            if self.decision_id is None or self.condition is None:
                raise ValueError(
                    f"RuleCandidate {self.candidate_id!r}: rule_family "
                    f"{self.rule_family.value} exige decision_id y condition no nulos "
                    "(unicamente CALCULATION puede representar un calculo aritmetico "
                    "incondicional, sin Decision envolvente)"
                )
        elif (self.decision_id is None) != (self.condition is None):
            raise ValueError(
                f"RuleCandidate {self.candidate_id!r}: decision_id y condition deben ser "
                "ambos None (calculo incondicional) o ambos no-None (calculo "
                "condicionado) -- nunca uno solo"
            )
        return self


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
                    f"RuleCandidate {candidate.candidate_id!r} debe tener status=DETECTED_CANDIDATE"
                )
        return self

    @model_validator(mode="after")
    def _check_warnings_sorted_and_unique(self) -> CandidateArtifact:
        if len(self.warnings) != len(set(self.warnings)):
            raise ValueError("warnings contiene duplicados")
        if self.warnings != sorted(self.warnings):
            raise ValueError("warnings no esta ordenado deterministicamente")
        return self
