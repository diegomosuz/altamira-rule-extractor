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

Estos tests son deterministicos (solo texto de los propios archivos de
prompt, sin LLM): protegen las propiedades concretas de la correccion
real, no intentan predecir el comportamiento del modelo."""

from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
_WRITER_USER = (_PROMPTS_DIR / "rule_writer_user.md").read_text(encoding="utf-8")
_WRITER_SYSTEM = (_PROMPTS_DIR / "rule_writer_system.md").read_text(encoding="utf-8")
_REPAIR_SYSTEM = (_PROMPTS_DIR / "rule_repair_system.md").read_text(encoding="utf-8")


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


def test_writer_user_never_teaches_bare_alias_placement_outside_evidence_refs() -> None:
    # Guardia de no-regresion (contrato preexistente, sin cambios): el
    # fix no debe reintroducir la posibilidad de escribir un alias fuera
    # de evidence_refs.
    assert "SOLO pueden\naparecer dentro de evidence_refs" in _WRITER_USER
