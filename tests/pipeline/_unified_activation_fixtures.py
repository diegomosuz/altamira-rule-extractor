"""Fixtures sinteticas compartidas para los tests del control plane de
activacion unificada (Fase 14A, `feat/controlled-unified-activation`).
NO es un archivo de tests (pytest lo ignora, no empieza con `test_`).

Reutiliza `downstream_golden_path()` (Fase 13,
`tests/pipeline/_unified_shadow_downstream_fixtures.py`) para el lado
unified -- el mismo escenario CALLER10/MAIN/WS-COD-RETORNO/R001 -- y
agrega un `CandidateArtifact` V1 sintetico minimo para el lado V1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import jsonschema

from altamira_extractor.contracts.candidate import CandidateArtifact, RuleCandidate
from altamira_extractor.contracts.enums import (
    CandidateStatus,
    ClaimField,
    EvidenceValidationStatus,
    GuardrailVerdict,
)
from altamira_extractor.contracts.guardrail import GuardrailReport
from altamira_extractor.contracts.guardrail_candidate import GuardrailCandidateArtifact
from altamira_extractor.contracts.rule_draft import Claim, RuleDraft
from altamira_extractor.contracts.semantic_graph import SemanticGraph
from altamira_extractor.contracts.unified_candidates_shadow import UnifiedCandidatesShadowArtifact
from altamira_extractor.contracts.unified_shadow_downstream import UnifiedShadowDownstreamArtifact
from altamira_extractor.contracts.unified_shadow_validation import UnifiedShadowValidationReport
from altamira_extractor.pipeline.rule_draft_assembly import load_rule_draft_schema
from altamira_extractor.pipeline.unified_shadow_downstream_executor import (
    run_unified_shadow_downstream,
)
from altamira_extractor.pipeline.unified_shadow_draft_generator import (
    DeterministicFakeDraftProvider,
)

from ._unified_shadow_downstream_fixtures import downstream_golden_path
from ._unified_shadow_validation_fixtures import HASH, RUN_ID

_SCHEMA_PATH = Path("schemas/rule-draft.schema.json")


def _schema_validator() -> jsonschema.protocols.Validator:
    schema, _hash = load_rule_draft_schema(_SCHEMA_PATH)
    validator_cls = jsonschema.validators.validator_for(schema)
    return validator_cls(schema)


def _stable_hash(model: object) -> str:
    import hashlib

    return hashlib.sha256(model.to_stable_json().encode("utf-8")).hexdigest()  # type: ignore[attr-defined]


V1_CANDIDATE_ID = "candidate::v1::1"
V1_PROGRAM_ID = f"program::AR::CALLER10::CALLER10::1.0::{'b' * 12}"
V1_PARAGRAPH_ID = f"{V1_PROGRAM_ID}::paragraph::MAIN"
V1_DECISION_ID = f"{V1_PARAGRAPH_ID}::decision::12::0"


def v1_candidate_artifact(*, outcome_code: str = "R001") -> CandidateArtifact:
    """`CandidateArtifact` V1 sintetico con UN candidato estructural,
    en el MISMO programa/paragraph/outcome_code que el escenario
    unified de Fase 13 -- para poder aislar EXACT_EQUIVALENT/
    CONFLICTING de forma determinista en los tests."""
    candidate = RuleCandidate(
        candidate_id=V1_CANDIDATE_ID,
        paragraph_id=V1_PARAGRAPH_ID,
        paragraph_name="MAIN",
        decision_id=V1_DECISION_ID,
        detector_id="q0-return-code-decision",
        detector_version="1.0",
        detector_score=1.0,
        status=CandidateStatus.DETECTED_CANDIDATE,
        condition="WS-SALDO < 0",
        outcome_code=outcome_code,
        line_start=12,
        source_file="CALLER10.cbl",
        source_package_hash=HASH,
    )
    return CandidateArtifact(
        run_id=RUN_ID,
        source_package_hash=HASH,
        semantic_graph_hash=HASH,
        invariants_query_hash=HASH,
        q0_query_hash=HASH,
        candidates=[candidate],
    )


def v1_guardrail_artifact(
    *, statement: str = "Cuando WS-SALDO < 0, se observa R001.", candidate_id: str = V1_CANDIDATE_ID
) -> GuardrailCandidateArtifact:
    """`GuardrailCandidateArtifact` V1 sintetico (EVIDENCE_VALIDATED --
    el unico veredicto que se persiste en `artifacts/09-guardrails/`,
    ver docstring de `pipeline/unified_activation_reference_
    adapters.py`)."""
    draft = RuleDraft(
        title="Shadow rule for CALLER10/MAIN",
        context="CALLER10::MAIN",
        statement=statement,
        condition="WS-SALDO < 0",
        parameters=[],
        effect="outcome_code=R001",
        parameter_source=None,
        traceability=[candidate_id],
        limitations=["V1 sintetico"],
        claims=[
            Claim(
                claim_id="claim::1",
                field=ClaimField.CONDITION,
                evidence_paths=["$.decision"],
                evidence_ids=["E001"],
            )
        ],
        evidence_validation_status=EvidenceValidationStatus.EVIDENCE_VALIDATED,
    )
    report = GuardrailReport(
        candidate_id=candidate_id,
        verdict=GuardrailVerdict.EVIDENCE_VALIDATED,
        violations=[],
        repair_attempts=0,
        evaluated_at=datetime(2026, 1, 1, tzinfo=UTC),
        source_package_hash=HASH,
    )
    pending_draft = draft.model_copy(
        update={"evidence_validation_status": EvidenceValidationStatus.PENDING}
    )
    return GuardrailCandidateArtifact(
        candidate_id=candidate_id,
        source_package_hash=HASH,
        context_hash=HASH,
        initial_rule_draft_hash=_stable_hash(pending_draft),
        final_rule_draft_hash=_stable_hash(draft),
        final_rule_draft=draft,
        guardrail_report=report,
    )


@dataclass(frozen=True)
class ActivationGoldenPath:
    v1_artifact: CandidateArtifact
    unified_shadow: UnifiedCandidatesShadowArtifact
    validation_report: UnifiedShadowValidationReport
    downstream_artifact: UnifiedShadowDownstreamArtifact
    semantic_graph: SemanticGraph


def activation_golden_path(*, v1_outcome_code: str = "R001") -> ActivationGoldenPath:
    """Escenario completo: V1 sintetico (estructural, `level=
    CANDIDATE`) + unified real (Fase 13, `downstream_golden_path()`)
    -- listo para `evaluate_unified_activation`."""
    dgp = downstream_golden_path()
    downstream = run_unified_shadow_downstream(
        run_id=dgp.unified_shadow.run_id,
        unified_shadow=dgp.unified_shadow,
        validation_report=dgp.validation_report,
        semantic_graph=dgp.semantic_graph,
        provider=DeterministicFakeDraftProvider(),
        schema_validator=_schema_validator(),
    )
    return ActivationGoldenPath(
        v1_artifact=v1_candidate_artifact(outcome_code=v1_outcome_code),
        unified_shadow=dgp.unified_shadow,
        validation_report=dgp.validation_report,
        downstream_artifact=downstream,
        semantic_graph=dgp.semantic_graph,
    )
