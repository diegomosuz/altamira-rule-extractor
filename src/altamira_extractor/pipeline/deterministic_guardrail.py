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

GUARDRAIL_VERSION = "1.4"

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


def _parse_claim_scoped_violation_id(violation_id: str) -> tuple[str, str, str] | None:
    """Analiza un `violation_id` con formato `{rule}::{claim_id}::{token}`
    (checkpoint correctivo v1.18.3 Fase 2 -- bug preexistente descubierto
    via prueba de corpus real: candidato PAGAUX01::1300-PROPAGAR-04,
    `claim_id` real generado por el modelo = "claim::6", que contiene `::`
    dentro de si mismo). Un `violation_id.split("::")` ingenuo asumiendo
    exactamente 3 partes rompe (silenciosamente, `len(parts) != 3`) tanto
    `augment_claims_with_authoritative_anchors` como
    `retarget_unapproved_table_effect_citations` cada vez que el modelo
    genera un `claim_id` con este patron -- confirmado NO es un caso raro
    (es la convencion de nombrado mas comun observada en candidatos V2
    reales).

    Estrategia robusta: `rule` (prefijo fijo, nunca contiene `::`) se
    toma hasta el PRIMER separador; `token` (numero/fecha/literal/indice,
    nunca contiene `::` por construccion de sus propios tokenizadores) se
    toma desde el ULTIMO separador; todo lo que queda en medio -- sin
    importar cuantos `::` internos tenga -- es `claim_id`. Devuelve
    `None` unicamente si falta un separador o si algun componente
    resultante queda vacio (violation_id genuinamente malformado)."""
    first_sep = violation_id.find("::")
    if first_sep == -1:
        return None
    rule = violation_id[:first_sep]
    rest = violation_id[first_sep + 2 :]
    last_sep = rest.rfind("::")
    if last_sep == -1:
        return None
    claim_id = rest[:last_sep]
    token = rest[last_sep + 2 :]
    if not rule or not claim_id or not token:
        return None
    return rule, claim_id, token


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

# checkpoint correctivo v1.18.3 Fase 2: literal de codigo citado
# explicitamente entre comillas simples o dobles (MISMA comilla en
# ambos lados via el backreference `(?P=quote)` -- nunca acepta
# comillas desparejadas como `'A"`), 1-8 caracteres, UNICAMENTE
# mayusculas A-Z y digitos 0-9. Deliberadamente NUNCA gobierna
# minusculas, espacios, guiones, guiones bajos ni codigos sin comillas
# (`ACT`/`PYM`/`USD` sueltos): alcance angosto explicito de Fase 2, ver
# docs de cierre de preflight v1.18.3 seccion 9 ("comenzar con el
# alcance mas defendible").
_QUOTED_LITERAL_TOKEN_RE = re.compile(r"(?P<quote>['\"])(?P<value>[A-Z0-9]{1,8})(?P=quote)")

# Campos de negocio donde el descubrimiento de hechos explicitos (numero/
# fecha/literal) es FIELD-FIRST (Fase 2): se evaluan SIEMPRE, incluso sin
# ningun Claim -- cierra el bypass documentado en el cierre de preflight
# v1.18.3 ("un campo sin ningun claim queda invisible"). NUNCA incluye
# traceability (mecanismo dedicado propio, comportamiento v1.18.2 sin
# cambios) ni limitations (texto de advertencia deliberadamente libre,
# ver rule_writer_system.md regla 13: la limitacion de "revision
# funcional" nunca lleva claim a proposito). Documentado tambien en
# tests/pipeline/test_deterministic_guardrail.py.
_EXPLICIT_FACT_FIELD_FIRST_FIELDS: frozenset[ClaimField] = frozenset(
    {
        ClaimField.TITLE,
        ClaimField.CONTEXT,
        ClaimField.STATEMENT,
        ClaimField.CONDITION,
        ClaimField.EFFECT,
        ClaimField.PARAMETER_SOURCE,
        ClaimField.PARAMETERS,
    }
)

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


def _quoted_literal_values(text: str) -> set[str]:
    return {match.group("value") for match in _QUOTED_LITERAL_TOKEN_RE.finditer(text)}


def _bare_value_present(text: str, value: str) -> bool:
    """Coincidencia EXACTA por limite de palabra de un valor literal
    (`A`, `D203`) dentro de texto SIN comillas -- MISMA disciplina que
    `_table_identifier_present` (Fase 1 v1.18.3): nunca substring. El
    valor entre comillas del propio campo de negocio (`'A'`) se compara
    contra el valor BARE de la ancla autoritativa (`decision.
    normalized_expression`/`return_codes[i].code`, ninguno de los
    cuales lleva comillas propias en este contrato)."""
    return re.search(r"\b" + re.escape(value) + r"\b", text) is not None


def _is_literal_authoritative_path(path: str, package: ContextPackage) -> bool:
    """Alcance EXACTO y ACOTADO de anclas autoritativas para
    `unsupported_explicit_literal` (Fase 2 v1.18.3, seccion 6 del cierre
    de preflight): UNICAMENTE `$.decision` (o cualquier sub-path bajo
    el, p. ej. `$.decision.normalized_expression`) y
    `$.effects.return_codes[i]` con `approved_for_rule_text=true`.
    Deliberadamente NUNCA code_slice, domain_glossary, table_effects,
    parameter_tables ni ningun otro contenedor -- aunque un claim los
    cite, nunca respaldan un literal de negocio (una coincidencia
    textual ahi podria ser casualidad de codigo fuente crudo, nunca un
    hecho verificado). Distinto, deliberadamente mas angosto, que la
    evidencia general que SI acepta `_evidence_blob_for_claim` para
    numeros/fechas (ver CLAUDE.md/docstring de
    `_authoritative_anchor_for_token`, mismo principio)."""
    if path == "$.decision" or path.startswith("$.decision."):
        return True
    match = _RETURN_CODE_PATH_RE.match(path)
    if match:
        index = int(match.group(1))
        if index < len(package.effects.return_codes):
            return package.effects.return_codes[index].approved_for_rule_text
    return False


def _literal_evidence_blob_for_claim(
    claim: Claim, context_dict: dict[str, Any], package: ContextPackage
) -> str:
    parts: list[str] = []
    for path in claim.evidence_paths:
        if not _is_literal_authoritative_path(path, package):
            continue
        found, value = resolve_json_path(context_dict, path)
        if found:
            parts.append(_flatten_to_text(value))
    return " ".join(parts)


def _check_explicit_facts(
    claims: list[Claim],
    rule_draft: RuleDraft,
    context_dict: dict[str, Any],
    package: ContextPackage,
) -> list[GuardrailViolation]:
    """checkpoint correctivo (paquete multiprograma, candidato CE10, sin
    cambios): un `field` de `RuleDraft` puede estar respaldado por MAS
    DE UN claim a la vez (p. ej. uno cita la evidencia de control-flow
    que llega al paragraph, otro cita el statement exacto con el
    literal numerico) -- eso es una estructura de claims perfectamente
    valida, nunca prohibida por el contrato. Se agrega la evidencia de
    TODOS los claims que comparten el mismo field antes de exigir que
    el numero/fecha aparezca LITERALMENTE en ese conjunto -- nunca se
    relaja la exigencia de aparicion literal, solo se corrige el
    alcance de "la evidencia citada" de un unico claim al del field
    completo.

    checkpoint correctivo v1.18.3 Fase 2 (cierre del hueco de
    validacion claim-driven documentado en el cierre de preflight de
    v1.18.3): para los campos en `_EXPLICIT_FACT_FIELD_FIRST_FIELDS`
    (title/context/statement/condition/effect/parameter_source/
    parameters -- NUNCA traceability, que conserva su mecanismo propio
    y su comportamiento v1.18.2 sin cambios; NUNCA limitations,
    deliberadamente texto libre), el descubrimiento de hechos
    explicitos es FIELD-FIRST: el campo SIEMPRE se evalua, exista o no
    al menos un claim que lo referencie. Un campo SIN ningun claim
    produce un `evidence_blob`/`literal_evidence_blob` vacios, asi que
    CUALQUIER numero/fecha/literal encontrado en su texto viola de
    inmediato -- la ausencia de un claim nunca vuelve invisible un
    hecho explicito gobernado (nunca requiere una rama especial: la
    MISMA comparacion "el token no aparece en la evidencia" ya lo
    cubre). traceability/limitations conservan el comportamiento
    claims-first anterior sin cambios: solo se evaluan cuando YA tienen
    al menos un claim (ver `_claims_by_field`).

    El nuevo check `unsupported_explicit_literal` (literales entre
    comillas, `_QUOTED_LITERAL_TOKEN_RE`) NUNCA se aplica a
    traceability/limitations, y usa `_literal_evidence_blob_for_claim`
    (NUNCA `_evidence_blob_for_claim`): su ancla autoritativa esta
    deliberadamente acotada a `$.decision`/`return_codes[i]` aprobado
    (ver `_is_literal_authoritative_path`), nunca a code_slice ni
    cualquier otra evidencia que un claim pudiera citar."""
    violations: list[GuardrailViolation] = []
    claims_by_field = _claims_by_field(claims)
    fields_to_check = set(_EXPLICIT_FACT_FIELD_FIRST_FIELDS)
    for legacy_field in (ClaimField.TRACEABILITY, ClaimField.LIMITATIONS):
        if legacy_field in claims_by_field:
            fields_to_check.add(legacy_field)

    for field in fields_to_check:
        text = _draft_text_for_claim_field(rule_draft, field)
        if not text:
            continue
        field_claims = claims_by_field.get(field, [])
        evidence_blob = " ".join(
            _evidence_blob_for_claim(claim, context_dict) for claim in field_claims
        )
        representative_claim_id = (
            min(claim.claim_id for claim in field_claims) if field_claims else "<sin-claim>"
        )
        no_claim_suffix = (
            "" if field_claims else " (el campo no tiene ningun claim que lo respalde)"
        )

        iso_dates = set(_ISO_DATE_TOKEN_RE.findall(text))
        for date_token in sorted(iso_dates):
            if date_token not in evidence_blob:
                violations.append(
                    _violation(
                        f"unsupported_explicit_date::{representative_claim_id}::{date_token}",
                        "unsupported_explicit_date",
                        field.value,
                        f"la fecha {date_token!r} no aparece en la evidencia citada "
                        f"por los claims de este campo{no_claim_suffix}",
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
                        f"por los claims de este campo{no_claim_suffix}",
                        Severity.ERROR,
                    )
                )

        if field in _EXPLICIT_FACT_FIELD_FIRST_FIELDS:
            literal_values = _quoted_literal_values(text)
            if literal_values:
                literal_evidence_blob = " ".join(
                    _literal_evidence_blob_for_claim(claim, context_dict, package)
                    for claim in field_claims
                )
                for literal_value in sorted(literal_values):
                    if not _bare_value_present(literal_evidence_blob, literal_value):
                        violations.append(
                            _violation(
                                f"unsupported_explicit_literal::"
                                f"{representative_claim_id}::{literal_value}",
                                "unsupported_explicit_literal",
                                field.value,
                                f"el literal {literal_value!r} no aparece en evidencia "
                                "autoritativa ($.decision o un return_code aprobado) "
                                f"citada por los claims de este campo{no_claim_suffix}",
                                Severity.ERROR,
                            )
                        )
    return violations


def _text_contains_token(text: str, token: str) -> bool:
    """Reutiliza EXACTAMENTE la misma tokenizacion que
    `_check_explicit_facts` (nunca una comparacion de substring
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


def _authoritative_anchor_for_token(
    token: str, package: ContextPackage
) -> tuple[str, list[str]] | None:
    """Busca `token` (mismo tokenizador EXACTO que `_check_numbers_and_
    dates`/`_text_contains_token`) UNICAMENTE en las anclas semanticas
    autoritativas que FIERN ya conoce con certeza para este candidato:

    - `$.decision` (expression/normalized_expression/outcome_code):
      define la IDENTIDAD del candidato -- cuando esta presente
      (`ContextPackage.decision` puede ser `None` para familias sin
      decision asociada), `expression`/`normalized_expression` son
      campos obligatorios del contrato (`min_length=1`), nunca vacios.
      Aplica a TODAS las familias de candidatos con decision, no solo
      Q0 RETURN_CODE.
    - `$.effects.return_codes[i]` con `approved_for_rule_text=True`
      (CLAUDE.md: "Solo efectos con approved_for_rule_text=true pueden
      redactarse como efecto de la regla" -- esta funcion nunca ancla
      contra un efecto no aprobado).

    Nunca busca en evidencia mas debil (code_slice, domain_glossary,
    declared_value_context): esas describen o contextualizan un hecho,
    nunca son su fuente semantica autoritativa -- una coincidencia
    numerica ahi podria ser casualidad, no un hecho verificado.

    `$.effects.table_effects[i]` deliberadamente EXCLUIDO: `TableEffect`
    (contracts/context_package.py) solo tiene `table`/`operation`/
    `attribution_scope`, ningun campo numerico/fecha que pudiera anclar
    un token de negocio -- no hay nada que buscar ahi.

    Devuelve `None` si ninguna ancla respalda el token -- en ese caso el
    llamador NUNCA debe inventar una ancla ni relajar la exigencia: el
    candidato sigue su ciclo de reparacion LLM existente sin cambios
    (posible afirmacion de negocio genuinamente no soportada, o una
    familia de candidatos -- p. ej. V2_STATE_CHANGE/V2_CALCULATION --
    cuyo hecho relevante vive en un campo que esta funcion
    deliberadamente no audita todavia; ver docstring de
    `augment_claims_with_authoritative_anchors`)."""
    decision = package.decision
    if decision is not None:
        decision_text = " ".join(
            text
            for text in (decision.expression, decision.normalized_expression, decision.outcome_code)
            if text is not None
        )
        if _text_contains_token(decision_text, token):
            return "$.decision", list(decision.evidence_ids)

    for index, return_code in enumerate(package.effects.return_codes):
        if not return_code.approved_for_rule_text:
            continue
        if _text_contains_token(return_code.code, token):
            return f"$.effects.return_codes[{index}]", list(return_code.evidence_ids)

    return None


def _authoritative_anchor_for_literal(
    value: str, package: ContextPackage
) -> tuple[str, list[str]] | None:
    """checkpoint correctivo v1.18.3 Fase 2 (extension real, no
    planeada originalmente: candidato real PAGRIE01::1200-FIRMA de
    PAQUETE_SINTETICO_ALTAMIRA_PAGOS_EMPRESAS_EXHAUSTIVO_48_REGLAS_
    v1.18.2_v2_E2E.zip -- `decision.expression`
    "WS-FIRMA-VALIDANOT='S'" contiene 'S' autoritativamente, el claim
    de `condition` citaba UNICAMENTE `$.code_slice[1]` -- MISMA busqueda
    que `_authoritative_anchor_for_token` (decision + return_codes
    aprobado, NUNCA code_slice/domain_glossary/table_effects, ver su
    docstring), pero usando `_bare_value_present` (limite de palabra
    exacto) en vez de `_text_contains_token`: un valor literal
    alfanumerico (`S`, `D203`) no necesita ni debe pasar por el
    stripping de fechas de `_text_contains_token` (irrelevante para un
    codigo de negocio, nunca una fecha)."""
    decision = package.decision
    if decision is not None:
        decision_text = " ".join(
            text
            for text in (decision.expression, decision.normalized_expression, decision.outcome_code)
            if text is not None
        )
        if _bare_value_present(decision_text, value):
            return "$.decision", list(decision.evidence_ids)

    for index, return_code in enumerate(package.effects.return_codes):
        if not return_code.approved_for_rule_text:
            continue
        if _bare_value_present(return_code.code, value):
            return f"$.effects.return_codes[{index}]", list(return_code.evidence_ids)

    return None


def augment_claims_with_authoritative_anchors(
    rule_draft: RuleDraft, package: ContextPackage, violations: list[GuardrailViolation]
) -> RuleDraft | None:
    """Correccion deterministica ACOTADA (checkpoint correctivo v1.18.2,
    cierre de fiabilidad de CAMPOS DE NEGOCIO: candidatos reales
    VALIDAR-MORA-PARA de PAQUETE_SINTETICO_CLIENTES_EMPRESAS_
    MULTIPROGRAMA_15_REGLAS.zip -- "30" en `effect` sin soporte -- y
    MAIN-PARA de PAQUETE_SINTETICO_GROUND_TRUTH_FASE_15D.zip -- "1000"
    en `condition` sin soporte). Confirmado comparando la ejecucion real
    fallida contra la ejecucion real exitosa INMEDIATAMENTE posterior
    del MISMO candidato: en ambos casos el numero escrito por el modelo
    es un HECHO AUTORITATIVO REAL (WS-DIAS-MORA>30 / WS-MONTO>1000, la
    propia `decision.expression`) -- el modelo simplemente no cito la
    evidencia que lo respalda (`$.decision`), citando en su lugar
    evidencia mas debil (`$.effects.return_codes[0]` sola,
    `$.domain_glossary[0]`) o ningun claim en absoluto para ese campo.
    Clasificacion (ver auditoria de ownership): "D. LLM esta siendo
    forzado a regenerar un hecho deterministico que FIERN ya posee" --
    nunca "G. afirmacion de negocio genuinamente no soportada".

    A diferencia de `sanitize_traceability_number_date_violations`/
    `reconstruct_traceability_deterministically` (que ELIMINAN o
    REEMPLAZAN texto), esta funcion NUNCA toca el VALOR de ningun campo
    de negocio -- ni siquiera un caracter. Unicamente AMPLIA
    `claims[].evidence_paths`/`evidence_ids` para que citen la ancla
    autoritativa real que YA respalda, verificado por coincidencia
    EXACTA de token (mismo tokenizador que la violacion), el numero/
    fecha que el modelo ya escribio correctamente. Nunca inventa un
    hecho, nunca fabrica un evidence_id, nunca fabrica un evidence_path
    -- ambos provienen directamente de campos ya poblados del
    `ContextPackage` real (`decision.evidence_ids`/`return_codes[i].
    evidence_ids`).

    Nunca se aplica a `traceability` (tiene su propio mecanismo
    dedicado, sin cambios) ni a ningun campo cuyo token ofensivo no
    resuelva contra una ancla autoritativa (`_authoritative_anchor_
    for_token` devuelve `None`): TODO-O-NADA sobre el conjunto de
    violaciones que esta funcion es responsable de resolver -- si
    CUALQUIERA de ellas no tiene ancla, NINGUNA se amplia, y el
    candidato completo sigue su ciclo de reparacion LLM existente sin
    cambios (nunca amplia unos claims mientras deja otros con una
    afirmacion potencialmente no soportada sin resolver en el mismo
    paso). Esto NUNCA relaja `_check_explicit_facts`: el token debe
    seguir apareciendo LITERALMENTE en la evidencia (ahora ampliada,
    nunca inventada) para que la revalidacion posterior del llamador
    pase."""
    _augmentable_rules = (
        "unsupported_explicit_number",
        "unsupported_explicit_date",
        "unsupported_explicit_literal",
    )
    error_violations = [
        v
        for v in violations
        if v.severity == Severity.ERROR
        and v.rule in _augmentable_rules
        and v.field != ClaimField.TRACEABILITY.value
    ]
    if not error_violations:
        return None

    claims_by_id = {claim.claim_id: claim for claim in rule_draft.claims}
    # Por claim_id: TODAS las anclas distintas necesarias para resolver
    # CADA token ofensivo de ese claim (un mismo claim puede necesitar
    # mas de un ancla, p. ej. "$.decision" para un umbral Y
    # "$.effects.return_codes[1]" para el codigo de retorno, ambos en
    # el mismo campo `effect`) -- nunca se sobreescribe una ancla ya
    # encontrada con la siguiente, se acumulan todas.
    anchor_paths_by_claim_id: dict[str, list[str]] = {}
    anchor_ids_by_claim_id: dict[str, list[str]] = {}
    for violation in error_violations:
        parsed = _parse_claim_scoped_violation_id(violation.violation_id)
        if parsed is None:
            return None
        rule, claim_id, token = parsed
        claim = claims_by_id.get(claim_id)
        if claim is None:
            return None
        # checkpoint correctivo v1.18.3 Fase 2: unsupported_explicit_literal
        # usa _authoritative_anchor_for_literal (limite de palabra exacto,
        # sin el stripping de fechas de _text_contains_token -- irrelevante
        # para un valor literal alfanumerico); number/date conservan
        # exactamente su busqueda original, sin cambios.
        anchor = (
            _authoritative_anchor_for_literal(token, package)
            if rule == "unsupported_explicit_literal"
            else _authoritative_anchor_for_token(token, package)
        )
        if anchor is None:
            return None
        anchor_path, anchor_evidence_ids = anchor
        paths = anchor_paths_by_claim_id.setdefault(claim_id, [])
        if anchor_path not in paths:
            paths.append(anchor_path)
        ids = anchor_ids_by_claim_id.setdefault(claim_id, list(claim.evidence_ids))
        for eid in anchor_evidence_ids:
            if eid not in ids:
                ids.append(eid)

    updated_claims: list[Claim] = []
    any_change = False
    for claim in rule_draft.claims:
        anchor_paths = anchor_paths_by_claim_id.get(claim.claim_id)
        if anchor_paths is None:
            updated_claims.append(claim)
            continue
        new_paths = list(claim.evidence_paths)
        for anchor_path in anchor_paths:
            if anchor_path not in new_paths:
                new_paths.append(anchor_path)
        new_ids = anchor_ids_by_claim_id[claim.claim_id]
        if new_paths == claim.evidence_paths and new_ids == claim.evidence_ids:
            updated_claims.append(claim)
            continue
        any_change = True
        updated_claims.append(
            claim.model_copy(update={"evidence_paths": new_paths, "evidence_ids": new_ids})
        )

    if not any_change:
        return None
    return rule_draft.model_copy(update={"claims": updated_claims})


def _table_identifier_present(text: str, table: str) -> bool:
    """Coincidencia EXACTA por limite de palabra de un identificador de
    tabla (`TableEffect.table`) dentro de texto libre -- NUNCA substring:
    `_` es un caracter `\\w` en Python, asi que `\\b` ya rechaza una
    coincidencia en medio de un identificador mas largo sin logica
    adicional (`PAG_OPER` nunca coincide dentro de `PAG_OPERACION`, porque
    no hay transicion \\w/no-\\w entre `R` y `A`). Case-sensitive de forma
    deliberada: `TableEffect.table` ya esta persistido por el parser en
    una unica forma canonica (sin contrato de normalizacion de
    mayusculas/minusculas en este codebase) -- el guardrail nunca debe
    adivinar una normalizacion de case que no existe."""
    return re.search(r"\b" + re.escape(table) + r"\b", text) is not None


def retarget_unapproved_table_effect_citations(
    rule_draft: RuleDraft, package: ContextPackage, violations: list[GuardrailViolation]
) -> RuleDraft | None:
    """Correccion deterministica ACOTADA (checkpoint correctivo v1.18.3,
    cierre de fiabilidad de citacion table_effect -- candidato real
    PAGDB201::3000-UPDATE-OPERACION de PAQUETE_SINTETICO_ALTAMIRA_
    PAGOS_EMPRESAS_EXHAUSTIVO_48_REGLAS_v1.18.2_v2_E2E.zip, familia
    RETURN_CODE/Q0: el modelo cito `table_effects[0]` (PAG_AUDITORIA,
    PROGRAM_CONTEXT, no aprobado) en el claim `title`, cuando el efecto
    DIRECT+aprobado real (PAG_OPERACION, `table_effects[1]`) ya existia
    en el mismo ContextPackage. Dos intentos reales de reparacion LLM no
    lo corrigieron -- ver forensic run
    `20260818T213051395444-c1fa002a`, `guardrails-failure-diagnostics.json`.

    A diferencia de `augment_claims_with_authoritative_anchors` (que
    SOLO AMPLIA evidence_paths/evidence_ids para numeros/fechas ya
    respaldados por `$.decision`/`$.effects.return_codes[i]`), esta
    funcion corrige violaciones `unapproved_table_effect`: REEMPLAZA
    (nunca amplia) la cita `table_effects[k]` no aprobada por
    `table_effects[j]` aprobada, y UNICAMENTE cuando la correspondencia
    semantica esta probada de forma exacta y no ambigua contra el TEXTO
    del propio campo de negocio del claim.

    Diseno v1.18.3 Fase 1 (decision explicita, ver cierre de preflight):
    la version anterior de este diseno proponia una "Signal B" -- corregir
    por mera UNICIDAD de un unico efecto aprobado cuando el texto no
    nombra ninguna tabla -- FUE RECHAZADA: la ausencia de un identificador
    de tabla en la prosa NUNCA prueba que un unico efecto aprobado
    respalda semanticamente esa prosa. Esta funcion implementa
    UNICAMENTE la senal "Signal A": correspondencia textual EXACTA.

    Invariante de seguridad (TODAS deben cumplirse para cada violacion
    `unapproved_table_effect` sobre `table_effects[k]` de un claim):
      A. el texto de negocio del campo del claim (`_draft_text_for_claim_
         field`, la MISMA funcion que usa el resto del modulo) contiene,
         por coincidencia EXACTA de limite de palabra
         (`_table_identifier_present`, nunca substring/fuzzy/embedding/
         LLM), el identificador `table_effects[j].table` de un efecto
         `j != k` con `approved_for_rule_text=True`;
      B. exactamente UN `j` cumple (A) -- cero coincidencias (sin prueba
         textual) o dos o mas (ambiguedad: el texto nombra mas de una
         tabla aprobada) NUNCA se corrigen, sin importar cuantos efectos
         aprobados existan en total en el ContextPackage;
      C. el identificador de la tabla NO aprobada citada erroneamente
         (`table_effects[k].table`) NO aparece tambien en el texto --
         veto duro: si aparece, el modelo probablemente SI queria hablar
         de esa tabla, y NUNCA se sobreescribe una afirmacion de negocio
         explicita con evidencia de una tabla distinta (contraejemplo
         explicito del cierre de preflight: claim que nombra
         'PAG_AUDITORIA' con cita erronea a PAG_AUDITORIA no aprobada y
         un unico PAG_OPERACION aprobado -- DEBE fallar cerrado, nunca
         retargetear a PAG_OPERACION).

    Si CUALQUIER violacion `unapproved_table_effect` del conjunto actual
    no cumple las tres condiciones, esta funcion devuelve `None` sin
    aplicar NINGUN cambio (ni siquiera a las violaciones que si las
    cumplirian) -- mismo TODO-O-NADA que `augment_claims_with_
    authoritative_anchors`, y el candidato completo sigue su ciclo de
    reparacion LLM existente sin cambios.

    Nunca toca title/context/statement/condition/effect/parameters/
    parameter_source/outcome_code/traceability/limitations/claim.field/
    claim.claim_id -- unicamente `claim.evidence_paths`/`claim.
    evidence_ids` de los claims afectados. Nunca elimina un claim ni lo
    deja con `evidence_paths`/`evidence_ids` vacios (violaria
    `min_length=1` del contrato `Claim`, y reabriria el hueco de
    validacion claim-driven documentado en el cierre de preflight de
    v1.18.3: un campo sin ningun claim queda invisible para
    `_check_explicit_facts`/`_check_approved_for_rule_text`)."""
    table_effect_violations = [
        v
        for v in violations
        if v.severity == Severity.ERROR and v.rule == "unapproved_table_effect"
    ]
    if not table_effect_violations:
        return None

    claims_by_id = {claim.claim_id: claim for claim in rule_draft.claims}
    approved_effects = [
        (index, effect)
        for index, effect in enumerate(package.effects.table_effects)
        if effect.approved_for_rule_text
    ]

    path_replacements_by_claim_id: dict[str, dict[str, str]] = {}
    ids_to_remove_by_claim_id: dict[str, set[str]] = {}
    ids_to_add_by_claim_id: dict[str, list[str]] = {}

    for violation in table_effect_violations:
        parsed = _parse_claim_scoped_violation_id(violation.violation_id)
        if parsed is None:
            return None
        _rule, claim_id, index_text = parsed
        claim = claims_by_id.get(claim_id)
        if claim is None:
            return None
        if not index_text.isdigit():
            return None
        wrong_index = int(index_text)
        if not (0 <= wrong_index < len(package.effects.table_effects)):
            return None
        wrong_effect = package.effects.table_effects[wrong_index]
        wrong_path = f"$.effects.table_effects[{wrong_index}]"
        if wrong_path not in claim.evidence_paths:
            return None

        field_text = _draft_text_for_claim_field(rule_draft, claim.field)

        if _table_identifier_present(field_text, wrong_effect.table):
            return None  # veto duro (condicion C)

        matches = [
            (index, effect)
            for index, effect in approved_effects
            if _table_identifier_present(field_text, effect.table)
        ]
        if len(matches) != 1:
            return None  # sin prueba textual, o ambiguo (condiciones A/B)

        match_index, match_effect = matches[0]
        target_path = f"$.effects.table_effects[{match_index}]"

        claim_replacements = path_replacements_by_claim_id.setdefault(claim_id, {})
        claim_replacements[wrong_path] = target_path
        ids_to_remove_by_claim_id.setdefault(claim_id, set()).update(wrong_effect.evidence_ids)
        claim_ids_to_add = ids_to_add_by_claim_id.setdefault(claim_id, [])
        for evidence_id in match_effect.evidence_ids:
            if evidence_id not in claim_ids_to_add:
                claim_ids_to_add.append(evidence_id)

    if not path_replacements_by_claim_id:
        return None

    updated_claims: list[Claim] = []
    any_change = False
    for claim in rule_draft.claims:
        replacements = path_replacements_by_claim_id.get(claim.claim_id)
        if replacements is None:
            updated_claims.append(claim)
            continue
        new_paths = [replacements.get(path, path) for path in claim.evidence_paths]
        ids_to_remove = ids_to_remove_by_claim_id.get(claim.claim_id, set())
        new_ids = [eid for eid in claim.evidence_ids if eid not in ids_to_remove]
        for evidence_id in ids_to_add_by_claim_id.get(claim.claim_id, []):
            if evidence_id not in new_ids:
                new_ids.append(evidence_id)
        if not new_paths or not new_ids:
            # nunca dejar un claim sin evidencia (min_length=1): si esto
            # ocurriera, la correspondencia asumida no era segura -- se
            # descarta la correccion completa para este candidato.
            return None
        if new_paths == claim.evidence_paths and new_ids == claim.evidence_ids:
            updated_claims.append(claim)
            continue
        any_change = True
        updated_claims.append(
            claim.model_copy(update={"evidence_paths": new_paths, "evidence_ids": new_ids})
        )

    if not any_change:
        return None
    return rule_draft.model_copy(update={"claims": updated_claims})


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
    violations.extend(_check_explicit_facts(rule_draft.claims, rule_draft, context_dict, package))
    violations.extend(_check_prompt_injection(rule_draft))
    violations.extend(_check_batch_structured_evidence_when_unavailable(rule_draft.claims, package))
    violations.extend(_check_batch_mentioned_without_evidence(rule_draft, package))
    return violations
