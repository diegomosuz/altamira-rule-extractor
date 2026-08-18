"""DeterministicGuardrail (Prompt 12): valida un `RuleDraft` contra el
`ContextPackage` real que lo origino, sin usar LLM (CLAUDE.md: "No usar
LLM para... validar evidencia").

Cubre los 12 checks literales del Prompt 12: schema (delegado a
`rule_draft_assembly.assemble_rule_draft`, no se repite aqui),
evidence_paths, evidence_ids, numeros, fechas, tablas, codigos, filas
`approved_for_rule_text`, efectos `approved_for_rule_text`, batch vacio,
identificadores desconocidos y prompt injection. Ver la matriz completa
en el informe de cierre de Prompt 12 (funcion/fuente/severidad/test por
categoria).

"numeros"/"fechas" son deliberadamente conservadores: solo normalizan
formatos inequivocos (entero/decimal simple, fecha ISO `YYYY-MM-DD`),
nunca infieren semantica ni intentan interpretar formatos ambiguos (esos
generan WARNING, nunca una validacion falsa). "identificadores
desconocidos" no es un mecanismo aparte: es la consecuencia directa de
`evidence_paths`/`evidence_ids` resolviendo contra el ContextPackage real
(RuleDraft no tiene ningun otro campo estructurado de identificadores)."""

from __future__ import annotations

import re
from typing import Any

from ..contracts.context_package import ContextPackage
from ..contracts.enums import BatchContextStatus, ClaimField, Severity
from ..contracts.guardrail import GuardrailViolation
from ..contracts.rule_draft import Claim, RuleDraft

GUARDRAIL_VERSION = "1.2"

_PATH_TOKEN_RE = re.compile(r"\.(?P<field>[A-Za-z_][A-Za-z0-9_]*)|\[(?P<index>\d+)\]")

_INJECTION_MARKERS = (
    "ignore previous instructions",
    "ignore all previous instructions",
    "ignora las instrucciones anteriores",
    "ignora todas las instrucciones",
    "you are now",
    "ahora eres",
    "system prompt",
    "prompt de sistema",
    "context_package_json_begin",
    "context_package_json_end",
    "rejected_rule_draft_json",
    "guardrails_violations_json",
)

_BATCH_KEYWORDS = ("batch", "lote nocturno", "job batch")


def resolve_json_path(root: Any, path: str) -> tuple[bool, Any]:
    """Resuelve una gramatica JSONPath deliberadamente acotada:
    `$.campo.campo[n].campo` — sin wildcards, filtros ni slices. Devuelve
    `(encontrado, valor)`; `(False, None)` ante cualquier desviacion de
    la gramatica o referencia inexistente."""
    if not path.startswith("$"):
        return False, None
    remainder = path[1:]
    current: Any = root
    pos = 0
    while pos < len(remainder):
        match = _PATH_TOKEN_RE.match(remainder, pos)
        if match is None:
            return False, None
        field = match.group("field")
        if field is not None:
            if not isinstance(current, dict) or field not in current:
                return False, None
            current = current[field]
        else:
            index = int(match.group("index"))
            if not isinstance(current, list) or not (0 <= index < len(current)):
                return False, None
            current = current[index]
        pos = match.end()
    return True, current


def _violation(
    violation_id: str, rule: str, field: str | None, message: str, severity: Severity
) -> GuardrailViolation:
    return GuardrailViolation(
        violation_id=violation_id, rule=rule, field=field, message=message, severity=severity
    )


def _check_evidence_paths_and_ids(
    claims: list[Claim], context_dict: dict[str, Any], evidence_ids: set[str]
) -> list[GuardrailViolation]:
    violations: list[GuardrailViolation] = []
    for claim in claims:
        for path in claim.evidence_paths:
            found, _value = resolve_json_path(context_dict, path)
            if not found:
                violations.append(
                    _violation(
                        f"unknown_evidence_path::{claim.claim_id}::{path}",
                        "unknown_evidence_path",
                        claim.field.value,
                        f"evidence_path {path!r} no resuelve contra el ContextPackage real",
                        Severity.ERROR,
                    )
                )
        for evidence_id in claim.evidence_ids:
            if evidence_id not in evidence_ids:
                violations.append(
                    _violation(
                        f"unknown_evidence_id::{claim.claim_id}::{evidence_id}",
                        "unknown_evidence_id",
                        claim.field.value,
                        f"evidence_id {evidence_id!r} no existe en ContextPackage.evidence",
                        Severity.ERROR,
                    )
                )
    return violations


_TABLE_EFFECT_PATH_RE = re.compile(r"^\$\.effects\.table_effects\[(\d+)\]")
_PARAMETER_CONTEXT_ROW_PATH_RE = re.compile(
    r"^\$\.data_context\.parameter_tables\[(\d+)\]\.context_rows\[(\d+)\]"
)
_RETURN_CODE_PATH_RE = re.compile(r"^\$\.effects\.return_codes\[(\d+)\]")


def _check_approved_for_rule_text(
    claims: list[Claim], package: ContextPackage
) -> list[GuardrailViolation]:
    violations: list[GuardrailViolation] = []
    for claim in claims:
        for path in claim.evidence_paths:
            table_match = _TABLE_EFFECT_PATH_RE.match(path)
            if table_match:
                index = int(table_match.group(1))
                if index < len(package.effects.table_effects):
                    effect = package.effects.table_effects[index]
                    if not effect.approved_for_rule_text:
                        violations.append(
                            _violation(
                                f"unapproved_table_effect::{claim.claim_id}::{index}",
                                "unapproved_table_effect",
                                claim.field.value,
                                f"claim cita effects.table_effects[{index}] "
                                f"(tabla {effect.table!r}), que no tiene "
                                "approved_for_rule_text=true",
                                Severity.ERROR,
                            )
                        )
                continue
            row_match = _PARAMETER_CONTEXT_ROW_PATH_RE.match(path)
            if row_match:
                table_index, row_index = int(row_match.group(1)), int(row_match.group(2))
                violations.append(
                    _violation(
                        f"unapproved_parameter_row::{claim.claim_id}::{table_index}::{row_index}",
                        "unapproved_parameter_row",
                        claim.field.value,
                        f"claim cita data_context.parameter_tables[{table_index}]"
                        f".context_rows[{row_index}], que nunca esta aprobada para "
                        "redaccion (solo applicable_rows)",
                        Severity.ERROR,
                    )
                )
                continue
            return_code_match = _RETURN_CODE_PATH_RE.match(path)
            if return_code_match:
                index = int(return_code_match.group(1))
                if index < len(package.effects.return_codes):
                    code_effect = package.effects.return_codes[index]
                    if not code_effect.approved_for_rule_text:
                        violations.append(
                            _violation(
                                f"unapproved_return_code::{claim.claim_id}::{index}",
                                "unapproved_return_code",
                                claim.field.value,
                                f"claim cita effects.return_codes[{index}], que no "
                                "tiene approved_for_rule_text=true",
                                Severity.ERROR,
                            )
                        )
    return violations


def _check_prompt_injection(rule_draft: RuleDraft) -> list[GuardrailViolation]:
    texts: list[tuple[str, str]] = [
        ("title", rule_draft.title),
        ("context", rule_draft.context),
        ("statement", rule_draft.statement),
        ("condition", rule_draft.condition),
        ("effect", rule_draft.effect),
    ]
    if rule_draft.parameter_source:
        texts.append(("parameter_source", rule_draft.parameter_source))
    for index, value in enumerate(rule_draft.parameters):
        texts.append((f"parameters[{index}]", value))
    for index, value in enumerate(rule_draft.traceability):
        texts.append((f"traceability[{index}]", value))
    for index, value in enumerate(rule_draft.limitations):
        texts.append((f"limitations[{index}]", value))

    violations: list[GuardrailViolation] = []
    for field_label, text in texts:
        lowered = text.lower()
        for marker in _INJECTION_MARKERS:
            if marker in lowered:
                violations.append(
                    _violation(
                        f"possible_prompt_injection::{field_label}::{marker}",
                        "possible_prompt_injection",
                        None,
                        f"el campo {field_label!r} contiene una frase asociada a intentos "
                        "de prompt injection",
                        Severity.ERROR,
                    )
                )
    return violations


def _check_batch_structured_evidence_when_unavailable(
    claims: list[Claim], package: ContextPackage
) -> list[GuardrailViolation]:
    """Cuando `batch_context.status=NOT_AVAILABLE`, NINGUN claim puede
    citar un evidence_path bajo `$.batch_context` (ni siquiera
    `$.batch_context.status`): es una referencia ESTRUCTURADA a un
    campo que, por definicion, no sustenta ningun hecho afirmable sobre
    un job/schedule real. Ver `_check_batch_mentioned_without_evidence`
    para la heuristica WARNING sobre prosa libre sin cita alguna."""
    if package.batch_context.status != BatchContextStatus.NOT_AVAILABLE:
        return []
    violations: list[GuardrailViolation] = []
    for claim in claims:
        for path in claim.evidence_paths:
            if path == "$.batch_context" or path.startswith("$.batch_context."):
                violations.append(
                    _violation(
                        f"batch_structured_evidence_when_unavailable::{claim.claim_id}::{path}",
                        "batch_structured_evidence_when_unavailable",
                        claim.field.value,
                        f"claim cita {path!r} bajo batch_context, pero "
                        "batch_context.status es NOT_AVAILABLE",
                        Severity.ERROR,
                    )
                )
    return violations


def _check_batch_mentioned_without_evidence(
    rule_draft: RuleDraft, package: ContextPackage
) -> list[GuardrailViolation]:
    if package.batch_context.status != BatchContextStatus.NOT_AVAILABLE:
        return []
    batch_claims = [
        claim
        for claim in rule_draft.claims
        if any(path.startswith("$.batch_context") for path in claim.evidence_paths)
    ]
    if batch_claims:
        return []
    lowered_effect = rule_draft.effect.lower()
    if any(keyword in lowered_effect for keyword in _BATCH_KEYWORDS):
        return [
            _violation(
                "batch_mentioned_without_evidence::effect",
                "batch_mentioned_without_evidence",
                "effect",
                "el efecto menciona batch pero batch_context.status es NOT_AVAILABLE y "
                "ningun claim cita $.batch_context (deteccion best-effort sobre texto libre)",
                Severity.WARNING,
            )
        ]
    return []


# --- numeros y fechas: conservador, solo formatos inequivocos ---

_ISO_DATE_TOKEN_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_AMBIGUOUS_DATE_TOKEN_RE = re.compile(r"\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b")
_NUMBER_TOKEN_RE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])")

_CLAIM_FIELD_TO_DRAFT_ATTR: dict[ClaimField, str] = {
    ClaimField.TITLE: "title",
    ClaimField.CONTEXT: "context",
    ClaimField.STATEMENT: "statement",
    ClaimField.CONDITION: "condition",
    ClaimField.EFFECT: "effect",
    ClaimField.PARAMETER_SOURCE: "parameter_source",
}
_CLAIM_FIELD_TO_DRAFT_LIST_ATTR: dict[ClaimField, str] = {
    ClaimField.PARAMETERS: "parameters",
    ClaimField.TRACEABILITY: "traceability",
    ClaimField.LIMITATIONS: "limitations",
}


def _draft_text_for_claim_field(rule_draft: RuleDraft, field: ClaimField) -> str:
    scalar_attr = _CLAIM_FIELD_TO_DRAFT_ATTR.get(field)
    if scalar_attr is not None:
        value = getattr(rule_draft, scalar_attr)
        return value or ""
    list_attr = _CLAIM_FIELD_TO_DRAFT_LIST_ATTR.get(field)
    if list_attr is not None:
        return " ".join(getattr(rule_draft, list_attr))
    return ""


def _flatten_to_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_flatten_to_text(v) for v in value.values())
    if isinstance(value, list):
        return " ".join(_flatten_to_text(v) for v in value)
    return str(value)


def _evidence_blob_for_claim(claim: Claim, context_dict: dict[str, Any]) -> str:
    parts: list[str] = []
    for path in claim.evidence_paths:
        found, value = resolve_json_path(context_dict, path)
        if found:
            parts.append(_flatten_to_text(value))
    return " ".join(parts)


def _claims_by_field(claims: list[Claim]) -> dict[ClaimField, list[Claim]]:
    grouped: dict[ClaimField, list[Claim]] = {}
    for claim in claims:
        grouped.setdefault(claim.field, []).append(claim)
    return grouped


def _check_numbers_and_dates(
    claims: list[Claim], rule_draft: RuleDraft, context_dict: dict[str, Any]
) -> list[GuardrailViolation]:
    """checkpoint correctivo (paquete multiprograma, candidato CE10):
    un `field` de `RuleDraft` puede estar respaldado por MAS DE UN claim
    a la vez (p. ej. uno cita la evidencia de control-flow que llega al
    paragraph, otro cita el statement exacto con el literal numerico) --
    eso es una estructura de claims perfectamente valida, nunca prohibida
    por el contrato. La version anterior de este check evaluaba cada
    claim de forma AISLADA contra el texto COMPLETO del field, lo que
    producia un falso positivo cuando el numero/fecha del field estaba
    respaldado por la evidencia de OTRO claim del mismo field: el
    `RuleDraft` quedaba indefinidamente irreparable (el modelo no puede
    satisfacer una expectativa incorrecta sin borrar un claim
    legitimo). La correccion agrega la evidencia de TODOS los claims que
    comparten el mismo field antes de exigir que el numero/fecha
    aparezca LITERALMENTE en ese conjunto -- nunca se relaja la exigencia
    de aparicion literal, solo se corrige el alcance de "la evidencia
    citada" de un unico claim al del field completo."""
    violations: list[GuardrailViolation] = []
    for field, field_claims in _claims_by_field(claims).items():
        text = _draft_text_for_claim_field(rule_draft, field)
        if not text:
            continue
        evidence_blob = " ".join(
            _evidence_blob_for_claim(claim, context_dict) for claim in field_claims
        )
        representative_claim_id = min(claim.claim_id for claim in field_claims)

        iso_dates = set(_ISO_DATE_TOKEN_RE.findall(text))
        for date_token in sorted(iso_dates):
            if date_token not in evidence_blob:
                violations.append(
                    _violation(
                        f"unsupported_explicit_date::{representative_claim_id}::{date_token}",
                        "unsupported_explicit_date",
                        field.value,
                        f"la fecha {date_token!r} no aparece en la evidencia citada "
                        "por los claims de este campo",
                        Severity.ERROR,
                    )
                )

        ambiguous_dates = set(_AMBIGUOUS_DATE_TOKEN_RE.findall(text))
        for ambiguous_token in sorted(ambiguous_dates):
            violations.append(
                _violation(
                    f"ambiguous_date_format::{representative_claim_id}::{ambiguous_token}",
                    "ambiguous_date_format",
                    field.value,
                    f"formato de fecha ambiguo {ambiguous_token!r}: no se analiza de "
                    "forma determinista y segura",
                    Severity.WARNING,
                )
            )

        text_without_dates = _AMBIGUOUS_DATE_TOKEN_RE.sub(
            " ", _ISO_DATE_TOKEN_RE.sub(" ", text)
        )
        numbers = set(_NUMBER_TOKEN_RE.findall(text_without_dates))
        for number_token in sorted(numbers):
            if number_token not in evidence_blob:
                violations.append(
                    _violation(
                        f"unsupported_explicit_number::{representative_claim_id}::{number_token}",
                        "unsupported_explicit_number",
                        field.value,
                        f"el numero {number_token!r} no aparece en la evidencia citada "
                        "por los claims de este campo",
                        Severity.ERROR,
                    )
                )
    return violations


def _text_contains_token(text: str, token: str) -> bool:
    """Reutiliza EXACTAMENTE la misma tokenizacion que
    `_check_numbers_and_dates` (nunca una comparacion de substring
    ingenua, que podria confundir '01' dentro de '4001' con el token
    real): un elemento "contiene" el token unicamente si la MISMA
    extraccion regex que genero la violacion tambien lo encuentra ahi."""
    if _ISO_DATE_TOKEN_RE.fullmatch(token):
        return token in _ISO_DATE_TOKEN_RE.findall(text)
    text_without_dates = _AMBIGUOUS_DATE_TOKEN_RE.sub(" ", _ISO_DATE_TOKEN_RE.sub(" ", text))
    return token in _NUMBER_TOKEN_RE.findall(text_without_dates)


def sanitize_traceability_number_date_violations(
    rule_draft: RuleDraft, violations: list[GuardrailViolation]
) -> RuleDraft | None:
    """Correccion deterministica ACOTADA (checkpoint correctivo v1.18.2,
    fallas reales unsupported_explicit_number sobre traceability
    reproducidas con gpt-4o-mini y gpt-4.1-2025-04-14, siempre en el
    campo traceability, nunca en un campo de negocio): nunca fuzzy,
    nunca inventa texto, nunca sustituye evidencia. Si TODAS las
    violaciones ERROR actuales son unsupported_explicit_number/
    unsupported_explicit_date sobre field="traceability", elimina
    UNICAMENTE los elementos de `traceability` que efectivamente
    contienen el numero/fecha literal no soportado y devuelve el
    RuleDraft resultante para que el llamador lo reevalue -- nunca
    reintroduce el elemento, nunca reescribe su contenido.

    Justificacion (ver rule_writer_system.md/rule_writer_user.md/
    rule_repair_system.md, contrato documentado y sin cambios):
    traceability es EXCLUSIVAMENTE una explicacion humana breve de en
    que evidencia se basa la regla -- nunca porta un hecho de negocio
    (outcome_code, condition, effect y parameters permanecen
    intocados). Los tres incidentes reales confirmados (v1.18.0 gpt-4.1
    parrafo 2000-VALIDAR-ENTRADA, v1.18.0 gpt-4o-mini parrafo
    4000-VALIDAR-PRODUCTO, v1.18.1 manual gpt-4o-mini copiando
    verbatim la descripcion del catalogo de evidencia) fueron todos
    fugas de identificadores tecnicos (nombre de parrafo, metadata del
    catalogo) hacia texto narrativo, nunca una afirmacion de negocio no
    soportada. Eliminar el elemento ofensivo nunca altera ningun hecho
    determinista ni evidencia real.

    Nunca se aplica a ningun otro campo (condition/effect/parameters/
    context/statement/parameter_source): un numero no soportado ahi
    puede ser un hecho de negocio real (p. ej. un codigo de retorno) y
    DEBE seguir fallando cerrado via el ciclo de reparacion LLM
    existente, sin cambios -- esta funcion nunca se invoca para esos
    campos.

    Devuelve `None` (nunca sanea, el llamador continua con el ciclo de
    reparacion LLM existente sin cambios) si:
    - alguna violacion ERROR no es de este tipo/campo (violaciones
      mixtas nunca se sanean parcialmente);
    - la saneacion dejaria `traceability` vacio (el schema exige al
      menos 1 elemento) o no elimina ningun elemento -- en ambos casos
      el campo se considera irreparable deterministicamente."""
    error_violations = [v for v in violations if v.severity == Severity.ERROR]
    if not error_violations:
        return None
    if not all(
        v.rule in ("unsupported_explicit_number", "unsupported_explicit_date")
        and v.field == ClaimField.TRACEABILITY.value
        for v in error_violations
    ):
        return None

    offending_tokens = {v.violation_id.rsplit("::", 1)[-1] for v in error_violations}
    kept = [
        text
        for text in rule_draft.traceability
        if not any(_text_contains_token(text, token) for token in offending_tokens)
    ]
    if not kept or len(kept) == len(rule_draft.traceability):
        return None
    return rule_draft.model_copy(update={"traceability": kept})


CANONICAL_TRACEABILITY_SENTENCE = (
    "Basado en la evidencia tecnica asociada a la decision y al efecto de este candidato."
)
"""Reconstruccion canonica de `traceability` (checkpoint correctivo
v1.18.2, cierre de fiabilidad multi-corpus): unica oracion fija,
deliberadamente generica, NUNCA contiene un digito ni una fecha
-- ni `_NUMBER_TOKEN_RE` ni `_ISO_DATE_TOKEN_RE`/`_AMBIGUOUS_DATE_TOKEN_RE`
pueden encontrar nada en este texto, por lo que
`unsupported_explicit_number`/`unsupported_explicit_date` sobre
traceability es ESTRUCTURALMENTE imposible contra esta oracion,
independientemente de que claims la respalden. Nunca contiene un alias
del catalogo, un evidence_id, un evidence_path real, ni un identificador
generado por el modelo -- es texto Python fijo, no generado ni
interpolado desde ningun dato variable del candidato (evitar
interpolar el nombre del programa u otro campo variable es deliberado:
cualquier valor variable reintroduciria la necesidad de re-verificar
que ese valor especifico nunca contenga un digito/fecha, lo que esta
funcion existe precisamente para volver innecesario)."""


def reconstruct_traceability_deterministically(
    rule_draft: RuleDraft, violations: list[GuardrailViolation]
) -> RuleDraft | None:
    """Reconstruccion canonica ACOTADA (checkpoint correctivo v1.18.2,
    cierre de fiabilidad: candidato real 4000-VALIDAR-PRODUCTO de
    PAQUETE_SINTETICO_CATHERINE_CORREGIDO_APP_ACTUAL.zip, reproducido
    con gpt-4o-mini real -- traceability de UN SOLO elemento, ese unico
    elemento cita el nombre del parrafo de origen sin que la evidencia
    citada lo respalde; `sanitize_traceability_number_date_violations`
    rehusa correctamente, porque eliminar el unico elemento dejaria el
    campo vacio -- y el ciclo de reparacion LLM entra en un loop de
    respuesta identica el ~1/3 de las veces observado en ejecuciones
    reales consecutivas).

    Decision arquitectonica (Prompt 12/CLAUDE.md: "No usar LLM para...
    validar evidencia"; traceability es EXCLUSIVAMENTE una explicacion
    humana de que evidencia respalda la regla, nunca un hecho de
    negocio -- ver docstring de `sanitize_traceability_number_date_
    violations`): cuando el saneamiento parcial no puede resolver
    deterministicamente una violacion `unsupported_explicit_number`/
    `unsupported_explicit_date` puramente sobre `traceability` (deja el
    campo vacio, o no elimina nada), FIERN reemplaza el campo COMPLETO
    por `CANONICAL_TRACEABILITY_SENTENCE` en vez de depender de un
    reintento LLM no determinista para una violacion que, por
    definicion, nunca puede portar informacion de negocio.

    Nunca toca `claims`: los claims de traceability (si existen) quedan
    EXACTAMENTE igual -- evidence_ids/evidence_paths reales, ya
    validados, nunca se fabrican ni se alteran. La oracion canonica en
    si misma nunca necesita evidencia adicional porque nunca afirma un
    hecho verificable especifico (ver docstring de la constante).

    Mismas garantias que `sanitize_traceability_number_date_violations`
    (violaciones mixtas nunca se tocan; nunca se invoca para ningun
    campo salvo traceability) mas una garantia ADICIONAL: nunca se
    invoca si el saneamiento parcial YA resolvio el campo -- este es
    unicamente el fallback final cuando la remocion selectiva no es
    viable."""
    error_violations = [v for v in violations if v.severity == Severity.ERROR]
    if not error_violations:
        return None
    if not all(
        v.rule in ("unsupported_explicit_number", "unsupported_explicit_date")
        and v.field == ClaimField.TRACEABILITY.value
        for v in error_violations
    ):
        return None
    return rule_draft.model_copy(update={"traceability": [CANONICAL_TRACEABILITY_SENTENCE]})


def evaluate_guardrail(rule_draft: RuleDraft, package: ContextPackage) -> list[GuardrailViolation]:
    """Ejecuta todos los checks deterministicos y devuelve la lista de
    violaciones (posiblemente vacia). No construye `GuardrailReport`: el
    llamador (`guardrails_applied_stage.py`) conoce `repair_attempts` y
    decide el veredicto final."""
    context_dict = package.model_dump(mode="json")
    evidence_ids = {entry.evidence_id for entry in package.evidence}

    violations: list[GuardrailViolation] = []
    violations.extend(_check_evidence_paths_and_ids(rule_draft.claims, context_dict, evidence_ids))
    violations.extend(_check_approved_for_rule_text(rule_draft.claims, package))
    violations.extend(_check_numbers_and_dates(rule_draft.claims, rule_draft, context_dict))
    violations.extend(_check_prompt_injection(rule_draft))
    violations.extend(_check_batch_structured_evidence_when_unavailable(rule_draft.claims, package))
    violations.extend(_check_batch_mentioned_without_evidence(rule_draft, package))
    return violations
