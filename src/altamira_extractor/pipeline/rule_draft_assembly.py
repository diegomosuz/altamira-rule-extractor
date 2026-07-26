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
final_rule_draft` (ver `contracts/guardrail_candidate.py`).

`check_evidence_references` (checkpoint correctivo) es un PASO ADICIONAL,
posterior a `assemble_rule_draft`: la forma puede ser perfectamente valida
y aun asi citar un `evidence_id`/`evidence_path` que no existe en el
`ContextPackage` real del candidato. RULE_DRAFTS_GENERATED trata ese caso
igual que cualquier otro fallo estructural (mismo `ValidationIssue`, mismo
ciclo de reparacion) en vez de delegarlo exclusivamente a
GUARDRAILS_APPLIED."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema
from jsonschema.exceptions import SchemaError
from pydantic import ValidationError as PydanticValidationError

from ..contracts.context_package import ContextPackage
from ..contracts.enums import EvidenceValidationStatus, FunctionalReviewStatus
from ..contracts.rule_draft import RuleDraft
from .deterministic_guardrail import resolve_json_path

_FORBIDDEN_MODEL_KEYS = frozenset(
    {"schema_version", "evidence_validation_status", "functional_review_status"}
)


@dataclass(frozen=True)
class ValidationIssue:
    """Un unico error estructurado (nunca el payload crudo): `loc` es la
    ruta punteada del campo, `type` clasifica el error (p. ej. `missing`/
    `extra_forbidden` de Pydantic, o el nombre del validador jsonschema
    que fallo), `msg` es la descripcion puntual de ESE campo. Nunca
    incluye el valor invalido en si (Pydantic lo expone por separado en
    `input`, que jamas se copia aqui)."""

    loc: str
    type: str
    msg: str


class RuleDraftAssemblyError(Exception):
    """Fallo estructural al ensamblar/validar un RuleDraft a partir del
    payload funcional devuelto por el modelo. Nunca incluye el payload
    crudo en su mensaje (puede contener contenido derivado de datos no
    confiables); el llamador decide como traducirla
    (`RuleDraftGenerationError`/`GuardrailError`).

    `validation_errors` expone los detalles estructurados (loc/type/msg)
    de Pydantic/jsonschema para que un llamador que implemente un ciclo
    de reparacion (RULE_DRAFTS_GENERATED) pueda describirselos al modelo
    sin tener que volver a parsear `str(self)`; el mensaje de texto
    plano permanece identico a como era antes de agregar este atributo
    (GUARDRAILS_APPLIED sigue usando unicamente `str(exc)`)."""

    def __init__(
        self, message: str, *, validation_errors: tuple[ValidationIssue, ...] = ()
    ) -> None:
        super().__init__(message)
        self.validation_errors = validation_errors


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
        forbidden_issues = tuple(
            ValidationIssue(
                loc=key, type="forbidden_self_assignment", msg="campo gobernado por Python"
            )
            for key in sorted(forbidden_present)
        )
        raise RuleDraftAssemblyError(
            f"el modelo no puede autoasignarse: {sorted(forbidden_present)}",
            validation_errors=forbidden_issues,
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
        issues = tuple(
            ValidationIssue(
                loc=".".join(str(part) for part in error["loc"]) or "(root)",
                type=error["type"],
                msg=error["msg"],
            )
            for error in exc.errors(include_url=False)
        )
        raise RuleDraftAssemblyError(
            "el payload funcional no valida contra RuleDraft", validation_errors=issues
        ) from exc

    errors = sorted(schema_validator.iter_errors(rule_draft.model_dump(mode="json")), key=str)
    if errors:
        schema_issues = tuple(
            ValidationIssue(
                loc=".".join(str(part) for part in error.absolute_path) or "(root)",
                type=str(error.validator),
                msg=error.message,
            )
            for error in errors
        )
        raise RuleDraftAssemblyError(
            "el RuleDraft ensamblado no valida contra rule-draft.schema.json",
            validation_errors=schema_issues,
        )

    return rule_draft


def check_evidence_references(
    rule_draft: RuleDraft, package: ContextPackage
) -> tuple[ValidationIssue, ...]:
    """Verifica, DESPUES de que `assemble_rule_draft` ya acepto la forma
    del RuleDraft pero ANTES de que el llamador lo de por bueno, que cada
    `evidence_id`/`evidence_path` citado por sus claims exista
    LITERALMENTE en el `ContextPackage` real del candidato.

    Reutiliza `resolve_json_path` de `deterministic_guardrail.py` (unica
    gramatica JSONPath del proyecto, sin wildcards ni coincidencias
    parciales: exacta o nada) en vez de reimplementar una segunda
    resolucion -- misma semantica que GUARDRAILS_APPLIED, pero evaluada
    aqui para que un RuleDraft con referencias inventadas nunca se
    promueva desde RULE_DRAFTS_GENERATED (GUARDRAILS_APPLIED puede seguir
    repitiendo este mismo chequeo como parte de `evaluate_guardrail`;
    esta funcion no le quita esa responsabilidad, solo deja de
    delegarsela EXCLUSIVAMENTE a el).

    Nunca corrige ni inventa un id/path: solo reporta. Devuelve una tupla
    vacia si todo resuelve."""
    context_dict = package.model_dump(mode="json")
    known_evidence_ids = {entry.evidence_id for entry in package.evidence}

    issues: list[ValidationIssue] = []
    for claim_index, claim in enumerate(rule_draft.claims):
        for id_index, evidence_id in enumerate(claim.evidence_ids):
            if evidence_id not in known_evidence_ids:
                issues.append(
                    ValidationIssue(
                        loc=f"claims.{claim_index}.evidence_ids.{id_index}",
                        type="unknown_evidence_id",
                        msg="evidence_id no existe en ContextPackage.evidence",
                    )
                )
        for path_index, path in enumerate(claim.evidence_paths):
            found, _value = resolve_json_path(context_dict, path)
            if not found:
                issues.append(
                    ValidationIssue(
                        loc=f"claims.{claim_index}.evidence_paths.{path_index}",
                        type="unknown_evidence_path",
                        msg="evidence_path no resuelve contra el ContextPackage real",
                    )
                )
    return tuple(issues)


def rule_draft_json_hash(rule_draft: RuleDraft) -> str:
    return hashlib.sha256(rule_draft.to_stable_json().encode("utf-8")).hexdigest()
