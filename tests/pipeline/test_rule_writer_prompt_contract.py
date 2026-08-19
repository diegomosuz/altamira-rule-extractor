"""Regresion real (v1.18.1): `prompts/rule_writer_user.md`/`rule_writer_
system.md` causaban una reparacion estructural sistematica (13/13
candidatos reales, gpt-4.1-2025-04-14 Y gpt-4o-mini) porque el prompt
nunca decia explicitamente que `traceability`/`limitations` deben ser
arrays JSON (su propio ejemplo mostraba un string suelto), y porque
inducia al modelo a crear un claim con `evidence_refs` vacio para la
limitacion procedimental de "revision funcional". El mismo ejemplo de
traceability tambien causaba rechazos de guardrail reales
(`unsupported_explicit_number`) al nombrar el parrafo de origen sin
instruir citar la evidencia que lo respalda.

Regresion real adicional (v1.18.2 multi-corpus): dos candidatos reales
de paquetes DISTINTOS a Catherine (`PAQUETE_SINTETICO_CLIENTES_
EMPRESAS_MULTIPROGRAMA_15_REGLAS.zip`, `PAQUETE_SINTETICO_PRESTAMOS_
EMPRESAS_5_REGLAS.zip`) agotaron la reparacion estructural con
`effect (alias_leaked_into_free_text)`: en ambos casos el candidato
tiene una evidencia `return_code_effect` que respalda el claim de
`effect` en `evidence_refs`, y el modelo escribio litealmente ese alias
(p. ej. "E003") como VALOR de `effect` en vez de una oracion de negocio
-- reproducido en vivo contra gpt-4o-mini real: el payload inicial
escribia el outcome_code desnudo ("PE05"), y la reparacion estructural
(disparada por un error NO relacionado, evidence_refs vacio en otro
claim) sustituyo ese codigo por el alias E003, quedandose atascada
(intento 2 identico a intento 1, mismo problema con LLM_TEMPERATURE=0).
Fix exclusivamente de prompt: `rule_writer_user.md` ahora exige que
`effect` sea siempre una oracion de negocio (nunca un codigo/alias
aislado) y aclara explicitamente que un alias en evidence_refs nunca
reemplaza el valor del campo; `rule_structure_repair_system.md` da guia
accionable para el error `alias_leaked_into_free_text`. Ningun
guardrail determinístico, chequeo de ensamblado (`rule_draft_
assembly.py::_check_no_bare_aliases_in_free_text`) ni contrato
persistido fue modificado ni relajado -- la deteccion sigue siendo
exclusivamente por coincidencia EXACTA del valor completo del campo.

Estos tests son deterministicos (solo texto de los propios archivos de
prompt, sin LLM): protegen las propiedades concretas de la correccion
real, no intentan predecir el comportamiento del modelo."""

from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
_WRITER_USER = (_PROMPTS_DIR / "rule_writer_user.md").read_text(encoding="utf-8")
_WRITER_SYSTEM = (_PROMPTS_DIR / "rule_writer_system.md").read_text(encoding="utf-8")
_REPAIR_SYSTEM = (_PROMPTS_DIR / "rule_repair_system.md").read_text(encoding="utf-8")
_STRUCTURE_REPAIR_SYSTEM = (
    _PROMPTS_DIR / "rule_structure_repair_system.md"
).read_text(encoding="utf-8")


def test_writer_user_declares_traceability_as_array() -> None:
    assert "trazabilidad: ARRAY JSON de strings" in _WRITER_USER


def test_writer_user_declares_limitations_as_array() -> None:
    assert "limitaciones: ARRAY JSON de strings" in _WRITER_USER


def test_writer_user_traceability_example_uses_bracket_syntax() -> None:
    # El ejemplo real que disparaba el bug era un string suelto:
    # "Basado en la decisión del párrafo X del programa Y" (sin
    # corchetes). La correccion debe mostrar el ejemplo como elemento de
    # un array.
    assert '["Basado en la decisión implementada en el programa Y."]' in _WRITER_USER


def test_writer_user_instructs_citing_evidence_for_named_numeric_identifiers() -> None:
    assert "un identificador" in _WRITER_USER
    assert "que incluya un número" in _WRITER_USER
    assert "TODOS los alias del catálogo necesarios" in _WRITER_USER


def test_writer_user_does_not_require_a_claim_per_allowed_field() -> None:
    assert "NO es obligatorio crear un claim por cada campo" in _WRITER_USER


def test_writer_system_forbids_claim_for_functional_review_limitation() -> None:
    assert "NUNCA le asignes un claim" in _WRITER_SYSTEM
    assert "revisión funcional" in _WRITER_SYSTEM


def test_repair_system_gives_actionable_guidance_for_unsupported_number_or_date() -> None:
    assert "unsupported_explicit_number" in _REPAIR_SYSTEM
    assert "unsupported_explicit_date" in _REPAIR_SYSTEM
    assert "Nunca reenvies un campo con el mismo texto" in _REPAIR_SYSTEM


def test_repair_system_forbids_self_assigned_fields() -> None:
    # Regresion real (v1.18.2 multi-corpus, candidato VALIDAR-MORA-PARA
    # de PAQUETE_SINTETICO_CLIENTES_EMPRESAS_MULTIPROGRAMA_15_REGLAS.zip):
    # a diferencia de rule_structure_repair_system.md,
    # rule_repair_system.md nunca advertia sobre schema_version/
    # evidence_validation_status/functional_review_status -- y
    # REJECTED_RULE_DRAFT (redact_rule_draft_for_prompt) SI incluye esos
    # campos (es el RuleDraft completo con solo evidence_ids/paths
    # sustituidos por evidence_refs), asi que el modelo los repetia en
    # su propia respuesta, causando rechazo estructural (nunca
    # semantico) en AMBOS intentos de reparacion de forma reproducible
    # contra gpt-4o-mini real -- agotando LLM_REPAIR_ATTEMPTS con
    # `current_draft`/`violations` congelados en el estado INICIAL
    # (ninguno de los dos intentos llegaba a evaluar el guardrail).
    assert "schema_version" in _REPAIR_SYSTEM
    assert "evidence_validation_status" in _REPAIR_SYSTEM
    assert "functional_review_status" in _REPAIR_SYSTEM


def test_repair_system_declares_traceability_and_limitations_as_arrays() -> None:
    # Regresion real (v1.18.2 cierre de fiabilidad de campos de
    # negocio, candidato EVALUATE SQLCODE WHEN +100 de PAQUETE_
    # SINTETICO_GROUND_TRUTH_FASE_15D.zip): al corregir una violacion
    # en `effect` (numero "100" no soportado), el modelo devolvia
    # `traceability` como un string suelto en vez de un array de un
    # solo elemento -- rule_repair_system.md, a diferencia de
    # rule_writer_user.md/rule_structure_repair_system.md, nunca
    # exigia explicitamente el tipo array, y el borrador rechazado
    # mostrado como referencia (REJECTED_RULE_DRAFT) no bastaba por si
    # solo para evitar la regresion -- reproducido de forma
    # identica (mismo response_hash) en AMBOS intentos de reparacion
    # contra gpt-4o-mini real, agotando LLM_REPAIR_ATTEMPTS con un
    # rechazo estructural que nunca llegaba a re-evaluar el guardrail.
    assert "SIEMPRE arrays JSON de strings" in _REPAIR_SYSTEM
    assert "nunca cambies su tipo al reenviarlo" in _REPAIR_SYSTEM


def test_writer_user_never_teaches_bare_alias_placement_outside_evidence_refs() -> None:
    # Guardia de no-regresion (contrato preexistente, sin cambios): el
    # fix no debe reintroducir la posibilidad de escribir un alias fuera
    # de evidence_refs.
    assert "SOLO pueden\naparecer dentro de evidence_refs" in _WRITER_USER


def test_writer_user_requires_effect_to_be_a_business_sentence() -> None:
    # Regresion real (v1.18.2 multi-corpus): sin este ejemplo, el modelo
    # escribia effect como el outcome_code desnudo ("CE07"/"PE05"), lo
    # que luego derivaba en alias_leaked_into_free_text durante la
    # reparacion estructural.
    assert "SIEMPRE una oración de negocio en" in _WRITER_USER
    assert "nunca un código ni un alias del catálogo aislado" in _WRITER_USER


def test_writer_user_clarifies_evidence_refs_never_replaces_field_value() -> None:
    # Regresion real: que un alias RESPALDE effect en evidence_refs no
    # significa que el VALOR de effect deba ser ese alias -- exactamente
    # la confusion reproducida con gpt-4o-mini real en los dos
    # candidatos de PAQUETE_SINTETICO_CLIENTES_EMPRESAS_MULTIPROGRAMA_
    # 15_REGLAS.zip / PAQUETE_SINTETICO_PRESTAMOS_EMPRESAS_5_REGLAS.zip.
    assert "nunca reemplaza la oración misma" in _WRITER_USER


def test_structure_repair_system_gives_actionable_guidance_for_alias_leaked_into_free_text() -> (
    None
):
    assert "alias_leaked_into_free_text" in _STRUCTURE_REPAIR_SYSTEM
    assert "conserva el evidence_refs del" in _STRUCTURE_REPAIR_SYSTEM


def test_structure_repair_system_never_teaches_bare_alias_placement_outside_evidence_refs() -> (
    None
):
    # Guardia de no-regresion (contrato preexistente, sin cambios).
    assert (
        'SOLO puede\n  aparecer dentro de evidence_refs' in _STRUCTURE_REPAIR_SYSTEM
    )


# ---------------------------------------------------------------------------
# Fase 3B v1.18.3 (checkpoint correctivo de fiabilidad de reparacion de
# hechos explicitos sin claim): regresion real candidato PAGMST01::
# 1000-VALIDAR-CANAL (ejecucion v1.18.3 Fase 3
# `20260819T150454102795-9d462eff`) -- `statement` afirmaba literales
# 'API'/'WEB' (ambos respaldados por $.decision) sin ningun claim,
# porque el prompt del writer solo describia los claims como opcionales
# ("crea un claim UNICAMENTE cuando..."), sin ninguna excepcion que
# volviera obligatorio un claim cuando el campo afirma un hecho
# explicito ya gobernado por el guardrail determinista.
# ---------------------------------------------------------------------------


def test_writer_user_makes_claim_mandatory_for_governed_explicit_facts() -> None:
    assert "EXCEPCIÓN OBLIGATORIA" in _WRITER_USER
    assert "NO es opcional" in _WRITER_USER


def test_writer_system_rule_16_points_to_mandatory_claim_exception() -> None:
    assert "16." in _WRITER_SYSTEM
    assert "no es opcional" in _WRITER_SYSTEM.lower()


def test_writer_user_general_optional_claim_rule_still_present_unchanged() -> None:
    # Guardia de no-regresion: la regla general (claims opcionales para
    # contenido NO gobernado) sigue intacta, la excepcion nunca la
    # reemplaza.
    assert (
        "crea un claim UNICAMENTE\n  cuando exista al menos un alias real del "
        "catálogo que respalde ese\n  campo" in _WRITER_USER
    )
