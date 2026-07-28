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
GUARDRAILS_APPLIED.

`resolve_evidence_aliases`/`assemble_rule_draft_with_evidence_catalog`
(checkpoint correctivo: catalogo de alias) van un paso mas alla: el LLM
ya NUNCA escribe `evidence_id`/`evidence_path` reales, solo alias
(`E001`, `E002`, ...) resueltos contra un `EvidenceCatalog` (ver
`evidence_catalog.py`) construido por Python desde el `ContextPackage`
real. `assemble_rule_draft`/`check_evidence_references` permanecen
EXACTAMENTE como estaban -- estas funciones nuevas los envuelven, nunca
los modifican."""

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
from .evidence_catalog import EvidenceCatalog

_FORBIDDEN_MODEL_KEYS = frozenset(
    {"schema_version", "evidence_validation_status", "functional_review_status"}
)
_EVIDENCE_IDS_KEY = "evidence_ids"
_EVIDENCE_PATHS_KEY = "evidence_paths"
_EVIDENCE_REFS_KEY = "evidence_refs"

# checkpoint correctivo (paquete multiprograma, 11/15 candidatos reales):
# los UNICOS campos de RuleDraft que son texto libre (nunca resueltos ni
# validados contra el catalogo de evidencia) -- "claims" se excluye
# deliberadamente, se valida por separado via evidence_refs.
_FREE_TEXT_SCALAR_KEYS = (
    "title",
    "context",
    "statement",
    "condition",
    "effect",
    "parameter_source",
)
_FREE_TEXT_LIST_KEYS = ("parameters", "traceability", "limitations")


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


def resolve_evidence_aliases(
    payload: dict[str, Any], catalog: EvidenceCatalog
) -> tuple[dict[str, Any], tuple[ValidationIssue, ...]]:
    """Traduce `claims[].evidence_refs` (alias del catalogo, p. ej.
    `E001`) a `evidence_ids`/`evidence_paths` reales, ANTES de
    `assemble_rule_draft`. Nunca muta `payload` (siempre devuelve una
    copia nueva): el `payload` original tal como el modelo lo escribio
    se sigue usando para mostrarselo de vuelta en la siguiente ronda de
    reparacion si hace falta.

    Un alias que no existe en el catalogo NUNCA se corrige, sustituye ni
    aproxima por similitud/prefijo/distancia de edicion: se reporta como
    `unknown_evidence_alias` y nada mas. Si el modelo escribe
    `evidence_ids`/`evidence_paths` directos en vez de `evidence_refs`
    (desobedeciendo el prompt), se reporta como
    `forbidden_direct_evidence_reference` -- el modelo solo puede
    referenciar evidencia a traves de un alias, nunca de otra forma.

    Claims malformados (no es un dict, o `evidence_refs` no es una lista)
    se dejan intactos, sin `evidence_ids`/`evidence_paths`: es
    `assemble_rule_draft` quien los reporta como campos faltantes de la
    forma habitual, sin logica especial aqui."""
    issues: list[ValidationIssue] = []
    translated = dict(payload)
    claims = translated.get("claims")
    if not isinstance(claims, list):
        return translated, ()

    new_claims: list[Any] = []
    for claim_index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            new_claims.append(claim)
            continue

        new_claim = dict(claim)
        direct_keys = {_EVIDENCE_IDS_KEY, _EVIDENCE_PATHS_KEY} & new_claim.keys()
        if direct_keys:
            for key in sorted(direct_keys):
                issues.append(
                    ValidationIssue(
                        loc=f"claims.{claim_index}.{key}",
                        type="forbidden_direct_evidence_reference",
                        msg="el modelo debe usar evidence_refs, nunca evidence_ids/evidence_paths",
                    )
                )
            new_claims.append(new_claim)
            continue

        refs = new_claim.pop(_EVIDENCE_REFS_KEY, None)
        if not isinstance(refs, list):
            new_claims.append(new_claim)
            continue

        resolved_ids: list[str] = []
        resolved_paths: list[str] = []
        for ref_index, ref in enumerate(refs):
            entry = catalog.resolve(ref) if isinstance(ref, str) else None
            if entry is None:
                issues.append(
                    ValidationIssue(
                        loc=f"claims.{claim_index}.evidence_refs.{ref_index}",
                        type="unknown_evidence_alias",
                        msg="alias no existe en el catalogo de evidencia del candidato",
                    )
                )
                continue
            resolved_ids.append(entry.evidence_id)
            resolved_paths.append(entry.evidence_path)

        new_claim[_EVIDENCE_IDS_KEY] = resolved_ids
        new_claim[_EVIDENCE_PATHS_KEY] = resolved_paths
        new_claims.append(new_claim)

    translated["claims"] = new_claims
    return translated, tuple(issues)


def _check_no_bare_aliases_in_free_text(
    payload: dict[str, Any], catalog: EvidenceCatalog
) -> tuple[ValidationIssue, ...]:
    """checkpoint correctivo (paquete multiprograma real, 11 de 15
    candidatos): el modelo a veces confunde un campo de texto libre --
    tipicamente `traceability` -- con "cita los alias que usaste", y
    escribe literalmente un alias del catalogo (`E001`, `E002`, ...) ahi
    en vez de en `claims[].evidence_refs`. Los campos de
    `_FREE_TEXT_SCALAR_KEYS`/`_FREE_TEXT_LIST_KEYS` son texto libre por
    diseno (nunca se resuelven ni se validan contra el catalogo): un
    alias asi quedaria persistido tal cual en el RuleDraft final,
    violando el principio central del catalogo (el LLM nunca escribe un
    alias fuera de `evidence_refs`). Se rechaza como error estructural
    reparable -- nunca se "limpia" en silencio ni se aproxima: solo
    coincidencia EXACTA del valor COMPLETO de un campo contra un alias
    REAL del catalogo de ESTE candidato (nunca un patron generico tipo
    "parece un alias", que podria coincidir con un codigo de negocio
    legitimo de otro paquete)."""
    known_aliases = frozenset(entry.alias for entry in catalog.entries)
    if not known_aliases:
        return ()

    issues: list[ValidationIssue] = []
    for key in _FREE_TEXT_SCALAR_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value in known_aliases:
            issues.append(
                ValidationIssue(
                    loc=key,
                    type="alias_leaked_into_free_text",
                    msg="el campo es texto libre y nunca debe contener un alias del catalogo",
                )
            )
    for key in _FREE_TEXT_LIST_KEYS:
        values = payload.get(key)
        if not isinstance(values, list):
            continue
        for index, item in enumerate(values):
            if isinstance(item, str) and item in known_aliases:
                issues.append(
                    ValidationIssue(
                        loc=f"{key}.{index}",
                        type="alias_leaked_into_free_text",
                        msg="el campo es texto libre y nunca debe contener un alias del catalogo",
                    )
                )
    return tuple(issues)


def assemble_rule_draft_with_evidence_catalog(
    payload: dict[str, Any],
    *,
    catalog: EvidenceCatalog,
    package: ContextPackage,
    schema_validator: jsonschema.protocols.Validator,
) -> RuleDraft:
    """Punto de entrada UNICO para ensamblar un RuleDraft a partir de una
    respuesta LLM que usa el catalogo de alias de evidencia (checkpoint
    correctivo): resuelve `evidence_refs` -> `evidence_ids`/
    `evidence_paths` reales (`resolve_evidence_aliases`), rechaza un
    alias filtrado en un campo de texto libre
    (`_check_no_bare_aliases_in_free_text`), delega en
    `assemble_rule_draft` SIN modificarlo para las mismas validaciones
    estructurales/Pydantic/schema de siempre, y ejecuta
    `check_evidence_references` SIN modificarlo como red de seguridad
    final -- no deberia dispararse nunca en la practica (todo id/path
    resuelto proviene del catalogo real construido desde el mismo
    `package`), pero se conserva integra: ninguna validacion existente
    se relaja.

    Cuando un intento falla por varios motivos a la vez (alias
    invalidos, alias filtrado en texto libre, errores de Pydantic/
    schema), todos se combinan en una unica excepcion (una sola ronda de
    reparacion los cubre todos, en vez de gastar el presupuesto acotado
    arreglando un tipo de error a la vez)."""
    translated_payload, alias_issues = resolve_evidence_aliases(payload, catalog)
    free_text_issues = _check_no_bare_aliases_in_free_text(payload, catalog)
    pre_assembly_issues = alias_issues + free_text_issues

    try:
        rule_draft = assemble_rule_draft(translated_payload, schema_validator=schema_validator)
    except RuleDraftAssemblyError as exc:
        raise RuleDraftAssemblyError(
            str(exc), validation_errors=pre_assembly_issues + exc.validation_errors
        ) from exc

    if pre_assembly_issues:
        raise RuleDraftAssemblyError(
            "el RuleDraft ensamblado cita uno o mas alias de evidencia de forma invalida",
            validation_errors=pre_assembly_issues,
        )

    evidence_issues = check_evidence_references(rule_draft, package)
    if evidence_issues:
        raise RuleDraftAssemblyError(
            "el RuleDraft ensamblado cita evidence_id/evidence_path que no "
            "existen en el ContextPackage real",
            validation_errors=evidence_issues,
        )

    return rule_draft


def rule_draft_json_hash(rule_draft: RuleDraft) -> str:
    return hashlib.sha256(rule_draft.to_stable_json().encode("utf-8")).hexdigest()
