"""Ensamblado deterministico de RuleDraft en dos pasos (Prompt 12).

Paso A: el modelo produce UNICAMENTE los 10 campos funcionales
solicitados por `prompts/rule_writer_user.md` (title, context, statement,
condition, parameters, effect, parameter_source, traceability,
limitations, claims). Paso B: Python agrega
`schema_version="2.0"`/`evidence_validation_status=PENDING`/
`functional_review_status=NEEDS_FUNCTIONAL_REVIEW` — el modelo NUNCA
puede autoasignarse estos tres campos (si los incluye, la respuesta se
rechaza integramente, nunca se "limpia" en silencio quitandolos).

Reutilizado tanto por RULE_DRAFTS_GENERATED (respuesta inicial) como por
GUARDRAILS_APPLIED (respuestas de reparacion): en ambos casos el
resultado nace con `evidence_validation_status=PENDING` — solo
GUARDRAILS_APPLIED, tras un veredicto EVIDENCE_VALIDATED, construye una
copia con ese campo actualizado para `GuardrailCandidateArtifact.
final_rule_draft` (ver `contracts/guardrail_candidate.py`)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema
from jsonschema.exceptions import SchemaError
from pydantic import ValidationError as PydanticValidationError

from ..contracts.enums import EvidenceValidationStatus, FunctionalReviewStatus
from ..contracts.rule_draft import RuleDraft

_FORBIDDEN_MODEL_KEYS = frozenset(
    {"schema_version", "evidence_validation_status", "functional_review_status"}
)


class RuleDraftAssemblyError(Exception):
    """Fallo estructural al ensamblar/validar un RuleDraft a partir del
    payload funcional devuelto por el modelo. Nunca incluye el payload
    crudo en su mensaje (puede contener contenido derivado de datos no
    confiables); el llamador decide como traducirla
    (`RuleDraftGenerationError`/`GuardrailError`)."""


def load_rule_draft_schema(schema_path: Path) -> tuple[dict[str, object], str]:
    """Carga y valida `rule-draft.schema.json` (mismo patron que
    `contexts_built_stage._load_context_schema`)."""
    path = schema_path
    if not path.is_file():
        raise RuleDraftAssemblyError(f"no se encontro {path.name}")
    raw_bytes = path.read_bytes()
    schema_hash = hashlib.sha256(raw_bytes).hexdigest()
    try:
        schema = json.loads(raw_bytes.decode("utf-8"))
    except ValueError as exc:
        raise RuleDraftAssemblyError(f"{path.name} no es JSON valido") from exc
    validator_cls = jsonschema.validators.validator_for(schema)
    try:
        validator_cls.check_schema(schema)
    except SchemaError as exc:
        raise RuleDraftAssemblyError(f"{path.name} no es un JSON Schema valido") from exc
    return schema, schema_hash


def assemble_rule_draft(
    payload: dict[str, Any], *, schema_validator: jsonschema.protocols.Validator
) -> RuleDraft:
    """Ensambla y valida un RuleDraft PENDING a partir del payload
    funcional crudo devuelto por el modelo (ya parseado como JSON
    estricto por `OpenAICompatibleChatClient.complete()`)."""
    forbidden_present = _FORBIDDEN_MODEL_KEYS & payload.keys()
    if forbidden_present:
        raise RuleDraftAssemblyError(
            f"el modelo no puede autoasignarse: {sorted(forbidden_present)}"
        )

    full_payload: dict[str, Any] = {
        **payload,
        "schema_version": "2.0",
        "evidence_validation_status": EvidenceValidationStatus.PENDING.value,
        "functional_review_status": FunctionalReviewStatus.NEEDS_FUNCTIONAL_REVIEW.value,
    }

    try:
        rule_draft = RuleDraft.model_validate(full_payload)
    except PydanticValidationError as exc:
        raise RuleDraftAssemblyError("el payload funcional no valida contra RuleDraft") from exc

    errors = sorted(schema_validator.iter_errors(rule_draft.model_dump(mode="json")), key=str)
    if errors:
        raise RuleDraftAssemblyError(
            "el RuleDraft ensamblado no valida contra rule-draft.schema.json"
        )

    return rule_draft


def rule_draft_json_hash(rule_draft: RuleDraft) -> str:
    return hashlib.sha256(rule_draft.to_stable_json().encode("utf-8")).hexdigest()
