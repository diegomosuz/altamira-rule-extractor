"""Contrato tipado de `artifacts/09-guardrails/{hash}.json` (Prompt 12).

`GuardrailReport` (Prompt 2) sigue representando exclusivamente el
veredicto determinístico (verdict/violations/repair_attempts) — nunca se
modifica para cargar el `RuleDraft` final ni la provenance de las
reparaciones. `GuardrailCandidateArtifact` es el wrapper nuevo que
combina ambos sin tocar el contrato existente."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, Sha256Hex
from .enums import EvidenceValidationStatus, FunctionalReviewStatus, GuardrailVerdict
from .guardrail import GuardrailReport
from .rule_draft import RuleDraft


def _stable_hash(draft: RuleDraft) -> str:
    return hashlib.sha256(draft.to_stable_json().encode("utf-8")).hexdigest()

_FAILURE_SUMMARY_MAX_LENGTH = 500


class RepairAttemptRecord(AltamiraBaseModel):
    """Provenance y diagnostico sanitizado de UN intento de reparacion.

    Nunca contiene: el body crudo del modelo, el prompt efectivo
    completo, el ContextPackage completo, ni credenciales — solo hashes y
    conteos (CLAUDE.md/.claude/rules/security.md)."""

    attempt_number: int = Field(ge=1)
    structurally_valid: bool
    response_hash: Sha256Hex | None = None
    produced_rule_draft_hash: Sha256Hex | None = None
    failure_code: str | None = None
    failure_summary: str | None = Field(default=None, max_length=_FAILURE_SUMMARY_MAX_LENGTH)
    error_count_before: int = Field(ge=0)
    error_count_after: int | None = Field(default=None, ge=0)
    warning_count_after: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _check_consistency(self) -> RepairAttemptRecord:
        if self.structurally_valid:
            if self.produced_rule_draft_hash is None:
                raise ValueError(
                    "un intento structurally_valid=true requiere produced_rule_draft_hash"
                )
            if self.failure_code is not None or self.failure_summary is not None:
                raise ValueError(
                    "un intento structurally_valid=true no puede tener "
                    "failure_code/failure_summary"
                )
        else:
            if self.produced_rule_draft_hash is not None:
                raise ValueError(
                    "un intento structurally_valid=false no puede tener produced_rule_draft_hash "
                    "(la respuesta invalida nunca reemplaza el ultimo RuleDraft valido)"
                )
            if self.error_count_after is not None or self.warning_count_after is not None:
                raise ValueError(
                    "un intento structurally_valid=false no pudo evaluarse contra el "
                    "guardrail: error_count_after/warning_count_after deben ser null"
                )
        return self


class GuardrailCandidateArtifact(AltamiraBaseModel):
    """Persistido en artifacts/09-guardrails/{sha256(candidate_id)}.json
    UNICAMENTE cuando el candidato alcanzo EVIDENCE_VALIDATED:
    GUARDRAILS_APPLIED es fail-fast y atomica — si un candidato termina
    REJECTED, el directorio temporal se descarta completo y este
    artefacto nunca llega a escribirse en disco (ver
    guardrails_applied_stage.py). El contrato en si mismo, sin embargo,
    puede representar AMBOS veredictos de forma coherente (se construye
    internamente incluso para el caso REJECTED, como prueba de
    consistencia determinista, aunque nunca se persista).

    `final_rule_draft` es SIEMPRE una copia nueva del ultimo RuleDraft
    estructuralmente valido, con `evidence_validation_status` actualizado
    determinísticamente por Python al veredicto real
    (EVIDENCE_VALIDATED o REJECTED) — nunca PENDING. Esto es distinto de
    `artifacts/08-rule-drafts/`, que permanece PENDING e inmutable
    siempre: `final_rule_draft` NUNCA sobrescribe ese archivo, es una
    copia independiente embebida solo en este wrapper."""

    schema_version: Literal["1.0"] = "1.0"
    candidate_id: str = Field(min_length=1)
    source_package_hash: Sha256Hex
    context_hash: Sha256Hex
    initial_rule_draft_hash: Sha256Hex
    final_rule_draft_hash: Sha256Hex
    final_rule_draft: RuleDraft
    guardrail_report: GuardrailReport
    repair_history: list[RepairAttemptRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_final_draft_hash_matches_object(self) -> GuardrailCandidateArtifact:
        if _stable_hash(self.final_rule_draft) != self.final_rule_draft_hash:
            raise ValueError(
                "final_rule_draft_hash no coincide con el hash real de final_rule_draft"
            )
        return self

    @model_validator(mode="after")
    def _check_functional_review_status(self) -> GuardrailCandidateArtifact:
        # FunctionalReviewStatus solo define NEEDS_FUNCTIONAL_REVIEW hoy
        # (inalcanzable via Pydantic tal cual), pero se deja explicito
        # como invariante documentado (CLAUDE.md: "Nunca presentar una
        # salida V1 como regla oficialmente aprobada").
        if self.final_rule_draft.functional_review_status != (
            FunctionalReviewStatus.NEEDS_FUNCTIONAL_REVIEW
        ):
            raise ValueError(
                "final_rule_draft.functional_review_status debe ser NEEDS_FUNCTIONAL_REVIEW"
            )
        return self

    @model_validator(mode="after")
    def _check_verdict_matches_final_draft_status(self) -> GuardrailCandidateArtifact:
        status = self.final_rule_draft.evidence_validation_status
        verdict = self.guardrail_report.verdict
        if verdict == GuardrailVerdict.EVIDENCE_VALIDATED:
            if status != EvidenceValidationStatus.EVIDENCE_VALIDATED:
                raise ValueError(
                    "verdict EVIDENCE_VALIDATED requiere "
                    "final_rule_draft.evidence_validation_status=EVIDENCE_VALIDATED "
                    f"(no {status.value})"
                )
        elif verdict == GuardrailVerdict.REJECTED:
            if status != EvidenceValidationStatus.REJECTED:
                raise ValueError(
                    "verdict REJECTED requiere "
                    f"final_rule_draft.evidence_validation_status=REJECTED (no {status.value})"
                )
        return self

    @model_validator(mode="after")
    def _check_report_matches_candidate(self) -> GuardrailCandidateArtifact:
        if self.guardrail_report.candidate_id != self.candidate_id:
            raise ValueError("guardrail_report.candidate_id no coincide con candidate_id")
        if self.guardrail_report.source_package_hash != self.source_package_hash:
            raise ValueError("guardrail_report.source_package_hash no coincide")
        return self

    @model_validator(mode="after")
    def _check_repair_attempts_consistent(self) -> GuardrailCandidateArtifact:
        if self.guardrail_report.repair_attempts != len(self.repair_history):
            raise ValueError(
                "guardrail_report.repair_attempts no coincide con len(repair_history)"
            )
        attempt_numbers = [attempt.attempt_number for attempt in self.repair_history]
        if len(attempt_numbers) != len(set(attempt_numbers)):
            raise ValueError("repair_history contiene attempt_number duplicado")
        if attempt_numbers != sorted(attempt_numbers):
            raise ValueError("repair_history no esta ordenado por attempt_number")
        return self

    @model_validator(mode="after")
    def _check_final_draft_content_matches_last_valid_draft(self) -> GuardrailCandidateArtifact:
        # final_rule_draft solo difiere del ultimo RuleDraft
        # estructuralmente valido (initial o de repair_history) en
        # evidence_validation_status: se compara "revirtiendo" ese campo
        # a PENDING y comparando el hash resultante, en vez de exigir
        # coincidencia literal (que nunca podria darse, ya que
        # initial_rule_draft_hash/produced_rule_draft_hash son siempre
        # hashes de una version PENDING).
        pending_equivalent = self.final_rule_draft.model_copy(
            update={"evidence_validation_status": EvidenceValidationStatus.PENDING}
        )
        pending_hash = _stable_hash(pending_equivalent)

        if not self.repair_history:
            if pending_hash != self.initial_rule_draft_hash:
                raise ValueError(
                    "sin reparaciones, final_rule_draft (revertido a PENDING) debe coincidir "
                    "con initial_rule_draft_hash"
                )
            return self
        last_valid = next(
            (
                attempt
                for attempt in reversed(self.repair_history)
                if attempt.structurally_valid
            ),
            None,
        )
        if last_valid is None or last_valid.produced_rule_draft_hash != pending_hash:
            raise ValueError(
                "final_rule_draft (revertido a PENDING) debe coincidir con "
                "produced_rule_draft_hash del ultimo intento structurally_valid en "
                "repair_history"
            )
        return self

    @model_validator(mode="after")
    def _check_warnings_sorted_and_unique(self) -> GuardrailCandidateArtifact:
        if len(self.warnings) != len(set(self.warnings)):
            raise ValueError("warnings contiene duplicados")
        if self.warnings != sorted(self.warnings):
            raise ValueError("warnings no esta ordenado deterministicamente")
        return self
