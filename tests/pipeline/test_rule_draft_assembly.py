"""Tests del ensamblado en dos pasos de RuleDraft (Prompt 12)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from altamira_extractor.contracts.context_package import (
    BatchContext,
    CodeSliceEntry,
    Completeness,
    ContextPackage,
    ContextPackageCandidate,
    ContextPackageDecision,
    ContextPackageOperation,
    ContextPackageScope,
    DataContext,
    Effects,
    EvidenceEntry,
)
from altamira_extractor.contracts.enums import (
    BatchContextStatus,
    CompletenessStatus,
    EvidenceValidationStatus,
    InclusionReason,
)
from altamira_extractor.pipeline.evidence_catalog import EvidenceCatalog, build_evidence_catalog
from altamira_extractor.pipeline.rule_draft_assembly import (
    RuleDraftAssemblyError,
    assemble_rule_draft,
    assemble_rule_draft_with_evidence_catalog,
    check_evidence_references,
    load_rule_draft_schema,
    resolve_evidence_aliases,
    rule_draft_json_hash,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_SCHEMA_PATH = REPO_ROOT / "schemas" / "rule-draft.schema.json"
_HASH_A = "a" * 64


def _package() -> ContextPackage:
    evidence = EvidenceEntry(
        evidence_id="ev-1",
        kind="decision",
        source_file="cobol/PROG1.cbl",
        line_start=10,
        line_end=10,
        source_package_hash=_HASH_A,
    )
    return ContextPackage(
        schema_version="2.0",
        candidate=ContextPackageCandidate(
            candidate_id="cand-1",
            decision_id="dec-1",
            detector_id="det",
            detector_version="1.0",
            detector_score=1.0,
        ),
        scope=ContextPackageScope(
            country="AR",
            application="Transferencias",
            operation=ContextPackageOperation(logical_name="OP1", description=None),
            program="PROG1",
            program_version="1",
            paragraph="MAIN",
            source_file="cobol/PROG1.cbl",
            line_start=10,
            line_end=10,
            source_package_hash=_HASH_A,
        ),
        code_slice=[
            CodeSliceEntry(
                paragraph_id="p1",
                paragraph="MAIN",
                source_file="cobol/PROG1.cbl",
                source_text="IF WS-COD = 'R001'",
                line_start=10,
                line_end=10,
                inclusion_reason=InclusionReason.CANDIDATE,
                evidence_ids=["ev-1"],
            )
        ],
        data_context=DataContext(parameter_tables=[], transactional_tables_read=[]),
        decision=ContextPackageDecision(
            expression="WS-COD = 'R001'",
            normalized_expression="WS-COD = 'R001'",
            operands=[],
            rule_type=None,
            outcome_code="R001",
            evidence_ids=["ev-1"],
        ),
        effects=Effects(return_codes=[], table_effects=[]),
        batch_context=BatchContext(status=BatchContextStatus.NOT_AVAILABLE, downstream_jobs=[]),
        domain_glossary=[],
        evidence=[evidence],
        completeness=Completeness(
            D1=CompletenessStatus.COMPLETE,
            D2=CompletenessStatus.COMPLETE,
            D3=CompletenessStatus.NOT_AVAILABLE,
            D4=CompletenessStatus.COMPLETE,
            D5=CompletenessStatus.NOT_AVAILABLE,
            D6=CompletenessStatus.NOT_AVAILABLE,
            D7=CompletenessStatus.NOT_AVAILABLE,
        ),
    )


def _valid_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": "Limite de monto",
        "context": "Transferencias en Argentina",
        "statement": "Si el monto supera el limite, se rechaza",
        "condition": "WS-MONTO > WS-LIMITE",
        "parameters": ["limite=1000"],
        "effect": "Se actualiza CUENTAS",
        "parameter_source": "PARM01",
        "traceability": ["ev-1"],
        "limitations": ["Requiere revision funcional"],
        "claims": [
            {
                "claim_id": "claim-1",
                "field": "statement",
                "evidence_paths": ["$.decision.expression"],
                "evidence_ids": ["ev-1"],
            }
        ],
    }
    payload.update(overrides)
    return payload


def _real_schema_validator() -> jsonschema.protocols.Validator:
    schema, _hash = load_rule_draft_schema(REAL_SCHEMA_PATH)
    validator_cls = jsonschema.validators.validator_for(schema)
    return validator_cls(schema)


def _catalog() -> EvidenceCatalog:
    return build_evidence_catalog(_package())


def _decision_alias(catalog: EvidenceCatalog) -> str:
    alias = catalog.find_alias("ev-1", "$.decision")
    assert alias is not None
    return alias


# --- load_rule_draft_schema ---


def test_load_real_schema_succeeds() -> None:
    schema, schema_hash = load_rule_draft_schema(REAL_SCHEMA_PATH)
    assert schema["title"] == "RuleDraft"
    assert len(schema_hash) == 64


def test_load_missing_schema_raises(tmp_path: Path) -> None:
    with pytest.raises(RuleDraftAssemblyError):
        load_rule_draft_schema(tmp_path / "missing.json")


def test_load_invalid_json_schema_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(RuleDraftAssemblyError):
        load_rule_draft_schema(path)


def test_load_not_a_json_schema_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad-schema.json"
    path.write_text(json.dumps({"type": "not-a-real-type"}), encoding="utf-8")
    with pytest.raises(RuleDraftAssemblyError):
        load_rule_draft_schema(path)


# --- assemble_rule_draft ---


def test_assemble_valid_payload_produces_pending_rule_draft() -> None:
    validator = _real_schema_validator()
    rule_draft = assemble_rule_draft(_valid_payload(), schema_validator=validator)
    assert rule_draft.evidence_validation_status == EvidenceValidationStatus.PENDING
    assert rule_draft.schema_version == "2.0"


@pytest.mark.parametrize(
    "forbidden_key",
    ["schema_version", "evidence_validation_status", "functional_review_status"],
)
def test_assemble_rejects_forbidden_self_assigned_keys(forbidden_key: str) -> None:
    validator = _real_schema_validator()
    payload = _valid_payload(**{forbidden_key: "anything"})
    with pytest.raises(RuleDraftAssemblyError):
        assemble_rule_draft(payload, schema_validator=validator)


def test_assemble_missing_required_field_raises() -> None:
    validator = _real_schema_validator()
    payload = _valid_payload()
    del payload["title"]
    with pytest.raises(RuleDraftAssemblyError):
        assemble_rule_draft(payload, schema_validator=validator)


def test_assemble_extra_field_raises() -> None:
    validator = _real_schema_validator()
    payload = _valid_payload(unexpected_field="nope")
    with pytest.raises(RuleDraftAssemblyError):
        assemble_rule_draft(payload, schema_validator=validator)


def test_assemble_wrong_type_raises() -> None:
    validator = _real_schema_validator()
    payload = _valid_payload(parameters="not-a-list")
    with pytest.raises(RuleDraftAssemblyError):
        assemble_rule_draft(payload, schema_validator=validator)


def test_assemble_claim_with_non_jsonpath_evidence_path_raises() -> None:
    validator = _real_schema_validator()
    payload = _valid_payload(
        claims=[
            {
                "claim_id": "claim-1",
                "field": "statement",
                "evidence_paths": ["decision.expression"],
                "evidence_ids": ["ev-1"],
            }
        ]
    )
    with pytest.raises(RuleDraftAssemblyError):
        assemble_rule_draft(payload, schema_validator=validator)


def test_assemble_never_includes_raw_payload_in_error_message() -> None:
    validator = _real_schema_validator()
    payload = _valid_payload(title="SECRET_MARKER_VALUE_12345")
    del payload["condition"]
    with pytest.raises(RuleDraftAssemblyError) as excinfo:
        assemble_rule_draft(payload, schema_validator=validator)
    assert "SECRET_MARKER_VALUE_12345" not in str(excinfo.value)


# --- validation_errors (checkpoint correctivo: reparacion estructural) ---


def test_missing_field_error_is_exposed_as_structured_issue() -> None:
    validator = _real_schema_validator()
    payload = _valid_payload()
    del payload["title"]
    with pytest.raises(RuleDraftAssemblyError) as excinfo:
        assemble_rule_draft(payload, schema_validator=validator)
    issues = excinfo.value.validation_errors
    assert len(issues) == 1
    assert issues[0].loc == "title"
    assert issues[0].type == "missing"
    assert issues[0].msg


def test_extra_field_error_is_exposed_as_structured_issue() -> None:
    validator = _real_schema_validator()
    payload = _valid_payload(unexpected_field="nope")
    with pytest.raises(RuleDraftAssemblyError) as excinfo:
        assemble_rule_draft(payload, schema_validator=validator)
    issues = excinfo.value.validation_errors
    assert len(issues) == 1
    assert issues[0].loc == "unexpected_field"
    assert issues[0].type == "extra_forbidden"


def test_wrong_type_error_is_exposed_as_structured_issue() -> None:
    validator = _real_schema_validator()
    payload = _valid_payload(parameters="not-a-list")
    with pytest.raises(RuleDraftAssemblyError) as excinfo:
        assemble_rule_draft(payload, schema_validator=validator)
    issues = excinfo.value.validation_errors
    assert len(issues) == 1
    assert issues[0].loc == "parameters"


def test_forbidden_self_assigned_key_is_exposed_as_structured_issue() -> None:
    validator = _real_schema_validator()
    payload = _valid_payload(schema_version="2.0")
    with pytest.raises(RuleDraftAssemblyError) as excinfo:
        assemble_rule_draft(payload, schema_validator=validator)
    issues = excinfo.value.validation_errors
    assert len(issues) == 1
    assert issues[0].loc == "schema_version"
    assert issues[0].type == "forbidden_self_assignment"


def test_validation_errors_never_include_the_invalid_value_itself() -> None:
    validator = _real_schema_validator()
    secret_marker = "SECRET_INVALID_VALUE_MARKER"
    payload = _valid_payload(parameters=secret_marker)
    with pytest.raises(RuleDraftAssemblyError) as excinfo:
        assemble_rule_draft(payload, schema_validator=validator)
    for issue in excinfo.value.validation_errors:
        assert secret_marker not in issue.msg
        assert secret_marker not in issue.loc


def test_str_message_unchanged_for_existing_guardrails_applied_consumer() -> None:
    # GUARDRAILS_APPLIED sigue leyendo unicamente str(exc): agregar
    # validation_errors no puede alterar ese texto.
    validator = _real_schema_validator()
    payload = _valid_payload()
    del payload["title"]
    with pytest.raises(RuleDraftAssemblyError) as excinfo:
        assemble_rule_draft(payload, schema_validator=validator)
    assert str(excinfo.value) == "el payload funcional no valida contra RuleDraft"


# --- check_evidence_references (checkpoint correctivo) ---


def test_evidence_references_valid_against_real_package_has_no_issues() -> None:
    validator = _real_schema_validator()
    package = _package()
    payload = _valid_payload(
        claims=[
            {
                "claim_id": "c1",
                "field": "statement",
                "evidence_paths": ["$.decision.expression"],
                "evidence_ids": ["ev-1"],
            }
        ]
    )
    rule_draft = assemble_rule_draft(payload, schema_validator=validator)
    assert check_evidence_references(rule_draft, package) == ()


def test_evidence_references_unknown_evidence_id_is_flagged() -> None:
    validator = _real_schema_validator()
    package = _package()
    payload = _valid_payload(
        claims=[
            {
                "claim_id": "c1",
                "field": "statement",
                "evidence_paths": ["$.decision.expression"],
                "evidence_ids": ["ev-does-not-exist"],
            }
        ]
    )
    rule_draft = assemble_rule_draft(payload, schema_validator=validator)
    issues = check_evidence_references(rule_draft, package)
    assert len(issues) == 1
    assert issues[0].type == "unknown_evidence_id"
    assert issues[0].loc == "claims.0.evidence_ids.0"


def test_evidence_references_unknown_evidence_path_is_flagged() -> None:
    validator = _real_schema_validator()
    package = _package()
    payload = _valid_payload(
        claims=[
            {
                "claim_id": "c1",
                "field": "statement",
                "evidence_paths": ["$.decision.does_not_exist"],
                "evidence_ids": ["ev-1"],
            }
        ]
    )
    rule_draft = assemble_rule_draft(payload, schema_validator=validator)
    issues = check_evidence_references(rule_draft, package)
    assert len(issues) == 1
    assert issues[0].type == "unknown_evidence_path"
    assert issues[0].loc == "claims.0.evidence_paths.0"


def test_evidence_references_prefix_or_partial_match_is_rejected() -> None:
    validator = _real_schema_validator()
    package = _package()
    # "$.decisio" es un prefijo de "$.decision" (path real), pero nunca
    # se acepta como coincidencia parcial.
    payload = _valid_payload(
        claims=[
            {
                "claim_id": "c1",
                "field": "statement",
                "evidence_paths": ["$.decisio"],
                "evidence_ids": ["ev-1"],
            }
        ]
    )
    rule_draft = assemble_rule_draft(payload, schema_validator=validator)
    issues = check_evidence_references(rule_draft, package)
    assert len(issues) == 1
    assert issues[0].type == "unknown_evidence_path"


def test_evidence_references_never_correct_or_invent_an_id() -> None:
    # check_evidence_references solo reporta: nunca reescribe claims ni
    # sugiere un id "parecido" -- eso queda para el ciclo de reparacion
    # via LLM, no para esta funcion determinista.
    validator = _real_schema_validator()
    package = _package()
    payload = _valid_payload(
        claims=[
            {
                "claim_id": "c1",
                "field": "statement",
                "evidence_paths": ["$.decision.expression"],
                "evidence_ids": ["ev-1-typo"],
            }
        ]
    )
    rule_draft = assemble_rule_draft(payload, schema_validator=validator)
    issues = check_evidence_references(rule_draft, package)
    assert len(issues) == 1
    assert rule_draft.claims[0].evidence_ids == ["ev-1-typo"]


# --- rule_draft_json_hash ---


def test_rule_draft_json_hash_is_deterministic() -> None:
    validator = _real_schema_validator()
    draft_a = assemble_rule_draft(_valid_payload(), schema_validator=validator)
    draft_b = assemble_rule_draft(_valid_payload(), schema_validator=validator)
    assert rule_draft_json_hash(draft_a) == rule_draft_json_hash(draft_b)


def test_rule_draft_json_hash_changes_with_content() -> None:
    validator = _real_schema_validator()
    draft_a = assemble_rule_draft(_valid_payload(), schema_validator=validator)
    draft_b = assemble_rule_draft(_valid_payload(title="Otro titulo"), schema_validator=validator)
    assert rule_draft_json_hash(draft_a) != rule_draft_json_hash(draft_b)


# --- resolve_evidence_aliases (checkpoint correctivo: catalogo de alias) ---


def test_resolve_evidence_aliases_translates_valid_refs_to_real_ids_and_paths() -> None:
    catalog = _catalog()
    alias = _decision_alias(catalog)
    payload = _valid_payload(
        claims=[{"claim_id": "c1", "field": "statement", "evidence_refs": [alias]}]
    )
    translated, issues = resolve_evidence_aliases(payload, catalog)
    assert issues == ()
    translated_claim = translated["claims"][0]
    assert translated_claim["evidence_ids"] == ["ev-1"]
    assert translated_claim["evidence_paths"] == ["$.decision"]
    assert "evidence_refs" not in translated_claim


def test_resolve_evidence_aliases_never_mutates_the_original_payload() -> None:
    catalog = _catalog()
    alias = _decision_alias(catalog)
    payload = _valid_payload(
        claims=[{"claim_id": "c1", "field": "statement", "evidence_refs": [alias]}]
    )
    resolve_evidence_aliases(payload, catalog)
    assert payload["claims"][0] == {
        "claim_id": "c1",
        "field": "statement",
        "evidence_refs": [alias],
    }


def test_resolve_evidence_aliases_unknown_alias_is_flagged_never_corrected() -> None:
    catalog = _catalog()
    payload = _valid_payload(
        claims=[{"claim_id": "c1", "field": "statement", "evidence_refs": ["E999"]}]
    )
    translated, issues = resolve_evidence_aliases(payload, catalog)
    assert len(issues) == 1
    assert issues[0].type == "unknown_evidence_alias"
    assert issues[0].loc == "claims.0.evidence_refs.0"
    translated_claim = translated["claims"][0]
    assert translated_claim["evidence_ids"] == []
    assert translated_claim["evidence_paths"] == []


def test_resolve_evidence_aliases_rejects_direct_evidence_ids_and_paths() -> None:
    catalog = _catalog()
    payload = _valid_payload(
        claims=[
            {
                "claim_id": "c1",
                "field": "statement",
                "evidence_ids": ["ev-1"],
                "evidence_paths": ["$.decision"],
            }
        ]
    )
    _translated, issues = resolve_evidence_aliases(payload, catalog)
    assert len(issues) == 2
    assert {issue.type for issue in issues} == {"forbidden_direct_evidence_reference"}
    assert {issue.loc for issue in issues} == {
        "claims.0.evidence_ids",
        "claims.0.evidence_paths",
    }


def test_resolve_evidence_aliases_leaves_malformed_claims_for_assemble_rule_draft_to_report() -> (
    None
):
    catalog = _catalog()
    payload = _valid_payload(claims=[{"claim_id": "c1", "field": "statement"}])
    translated, issues = resolve_evidence_aliases(payload, catalog)
    assert issues == ()
    assert translated["claims"][0] == {"claim_id": "c1", "field": "statement"}


# --- assemble_rule_draft_with_evidence_catalog (checkpoint correctivo) ---


def test_assemble_with_catalog_valid_alias_produces_pending_draft_with_real_ids() -> None:
    validator = _real_schema_validator()
    package = _package()
    catalog = build_evidence_catalog(package)
    alias = _decision_alias(catalog)
    payload = _valid_payload(
        claims=[{"claim_id": "c1", "field": "statement", "evidence_refs": [alias]}]
    )
    rule_draft = assemble_rule_draft_with_evidence_catalog(
        payload, catalog=catalog, package=package, schema_validator=validator
    )
    assert rule_draft.evidence_validation_status == EvidenceValidationStatus.PENDING
    assert rule_draft.claims[0].evidence_ids == ["ev-1"]
    assert rule_draft.claims[0].evidence_paths == ["$.decision"]


def test_assemble_with_catalog_unknown_alias_raises_unknown_evidence_alias() -> None:
    validator = _real_schema_validator()
    package = _package()
    catalog = build_evidence_catalog(package)
    payload = _valid_payload(
        claims=[{"claim_id": "c1", "field": "statement", "evidence_refs": ["E999"]}]
    )
    with pytest.raises(RuleDraftAssemblyError) as excinfo:
        assemble_rule_draft_with_evidence_catalog(
            payload, catalog=catalog, package=package, schema_validator=validator
        )
    issue_types = {issue.type for issue in excinfo.value.validation_errors}
    assert "unknown_evidence_alias" in issue_types


def test_assemble_with_catalog_direct_evidence_reference_raises_forbidden() -> None:
    # El payload por defecto ya usa evidence_ids/evidence_paths directos
    # (nunca evidence_refs): exactamente lo que un modelo desobediente
    # produciria.
    validator = _real_schema_validator()
    package = _package()
    catalog = build_evidence_catalog(package)
    payload = _valid_payload()
    with pytest.raises(RuleDraftAssemblyError) as excinfo:
        assemble_rule_draft_with_evidence_catalog(
            payload, catalog=catalog, package=package, schema_validator=validator
        )
    issues = excinfo.value.validation_errors
    assert len(issues) == 2
    assert {issue.type for issue in issues} == {"forbidden_direct_evidence_reference"}


def test_assemble_with_catalog_combines_alias_and_schema_issues_in_one_round() -> None:
    # Un alias desconocido deja evidence_ids/evidence_paths vacios, lo que
    # ademas viola min_length=1 de Claim: ambos tipos de error deben viajar
    # juntos en la misma excepcion para no gastar dos rondas de reparacion
    # en el mismo intento fallido.
    validator = _real_schema_validator()
    package = _package()
    catalog = build_evidence_catalog(package)
    payload = _valid_payload(
        claims=[{"claim_id": "c1", "field": "statement", "evidence_refs": ["E999"]}]
    )
    del payload["title"]
    with pytest.raises(RuleDraftAssemblyError) as excinfo:
        assemble_rule_draft_with_evidence_catalog(
            payload, catalog=catalog, package=package, schema_validator=validator
        )
    issue_types = {issue.type for issue in excinfo.value.validation_errors}
    assert "unknown_evidence_alias" in issue_types
    assert "missing" in issue_types


def test_assemble_with_catalog_never_relaxes_check_evidence_references() -> None:
    # El catalogo se construye desde `package`, pero si se le pasa un
    # catalogo/paquete desalineados (ver mas abajo), check_evidence_references
    # sigue siendo la ultima red de seguridad: aqui confirmamos que sigue
    # activa aun cuando la resolucion de alias fue exitosa.
    validator = _real_schema_validator()
    package = _package()
    catalog = build_evidence_catalog(package)
    other_package = _package()
    other_package.evidence[0].evidence_id = "ev-other"
    alias = _decision_alias(catalog)
    payload = _valid_payload(
        claims=[{"claim_id": "c1", "field": "statement", "evidence_refs": [alias]}]
    )
    with pytest.raises(RuleDraftAssemblyError) as excinfo:
        assemble_rule_draft_with_evidence_catalog(
            payload, catalog=catalog, package=other_package, schema_validator=validator
        )
    issue_types = {issue.type for issue in excinfo.value.validation_errors}
    assert "unknown_evidence_id" in issue_types


def test_assemble_with_catalog_final_draft_never_carries_evidence_refs_or_aliases() -> None:
    validator = _real_schema_validator()
    package = _package()
    catalog = build_evidence_catalog(package)
    alias = _decision_alias(catalog)
    payload = _valid_payload(
        claims=[{"claim_id": "c1", "field": "statement", "evidence_refs": [alias]}]
    )
    rule_draft = assemble_rule_draft_with_evidence_catalog(
        payload, catalog=catalog, package=package, schema_validator=validator
    )
    stable_json = rule_draft.to_stable_json()
    assert "evidence_refs" not in stable_json
    assert alias not in stable_json


# --- checkpoint correctivo: alias filtrado en campos de texto libre
# (paquete multiprograma real, 11 de 15 candidatos con
# traceability=["E001", "E002", ...] en vez de texto humano) ---


def test_assemble_with_catalog_rejects_bare_alias_in_traceability() -> None:
    validator = _real_schema_validator()
    package = _package()
    catalog = build_evidence_catalog(package)
    alias = _decision_alias(catalog)
    payload = _valid_payload(
        traceability=[alias],
        claims=[{"claim_id": "c1", "field": "statement", "evidence_refs": [alias]}],
    )
    with pytest.raises(RuleDraftAssemblyError) as excinfo:
        assemble_rule_draft_with_evidence_catalog(
            payload, catalog=catalog, package=package, schema_validator=validator
        )
    issues = excinfo.value.validation_errors
    assert any(
        issue.type == "alias_leaked_into_free_text" and issue.loc == "traceability.0"
        for issue in issues
    )


def test_assemble_with_catalog_rejects_bare_alias_in_scalar_field() -> None:
    validator = _real_schema_validator()
    package = _package()
    catalog = build_evidence_catalog(package)
    alias = _decision_alias(catalog)
    payload = _valid_payload(
        title=alias,
        claims=[{"claim_id": "c1", "field": "statement", "evidence_refs": [alias]}],
    )
    with pytest.raises(RuleDraftAssemblyError) as excinfo:
        assemble_rule_draft_with_evidence_catalog(
            payload, catalog=catalog, package=package, schema_validator=validator
        )
    issues = excinfo.value.validation_errors
    assert any(
        issue.type == "alias_leaked_into_free_text" and issue.loc == "title" for issue in issues
    )


def test_assemble_with_catalog_allows_normal_free_text_traceability() -> None:
    # Nunca un falso positivo: texto humano normal, aunque mencione la
    # palabra "evidencia" o contenga letras/numeros, nunca se confunde
    # con un alias real -- solo una coincidencia EXACTA del valor
    # completo del campo dispara la validacion.
    validator = _real_schema_validator()
    package = _package()
    catalog = build_evidence_catalog(package)
    alias = _decision_alias(catalog)
    payload = _valid_payload(
        traceability=["La validacion se basa en el parrafo MAIN del programa PROG1."],
        claims=[{"claim_id": "c1", "field": "statement", "evidence_refs": [alias]}],
    )
    rule_draft = assemble_rule_draft_with_evidence_catalog(
        payload, catalog=catalog, package=package, schema_validator=validator
    )
    assert rule_draft.traceability == [
        "La validacion se basa en el parrafo MAIN del programa PROG1."
    ]


def test_assemble_with_catalog_combines_free_text_alias_leak_with_unknown_alias() -> None:
    # Ambos tipos de fallo (alias filtrado en texto libre + alias
    # inexistente en evidence_refs) se combinan en una unica excepcion,
    # igual que alias invalido + error de schema.
    validator = _real_schema_validator()
    package = _package()
    catalog = build_evidence_catalog(package)
    alias = _decision_alias(catalog)
    payload = _valid_payload(
        traceability=[alias],
        claims=[{"claim_id": "c1", "field": "statement", "evidence_refs": ["E999"]}],
    )
    with pytest.raises(RuleDraftAssemblyError) as excinfo:
        assemble_rule_draft_with_evidence_catalog(
            payload, catalog=catalog, package=package, schema_validator=validator
        )
    issue_types = {issue.type for issue in excinfo.value.validation_errors}
    assert "alias_leaked_into_free_text" in issue_types
    assert "unknown_evidence_alias" in issue_types


def test_regression_real_run_traceability_leak_rejected_then_repaired_text_accepted() -> None:
    """Regresion exacta del incidente real observado en el paquete
    multiprograma de 15 reglas (11 de 15 candidatos): el modelo escribio
    literalmente los alias del catalogo (p. ej. `["E001", "E002", "E003",
    "E004", "E005"]` en el candidato real de mayor catalogo) dentro de
    `traceability` en vez de texto humano. Aqui se reproduce con los dos
    alias reales del catalogo de `_package()` (`E001`/`E002`): el primer
    intento debe rechazarse; una reparacion con texto humano real debe
    aceptarse."""
    validator = _real_schema_validator()
    package = _package()
    catalog = build_evidence_catalog(package)
    alias = _decision_alias(catalog)
    all_aliases = sorted(entry.alias for entry in catalog.entries)
    assert all_aliases == ["E001", "E002"]

    poisoned_payload = _valid_payload(
        traceability=all_aliases,
        claims=[{"claim_id": "c1", "field": "statement", "evidence_refs": [alias]}],
    )
    with pytest.raises(RuleDraftAssemblyError) as excinfo:
        assemble_rule_draft_with_evidence_catalog(
            poisoned_payload, catalog=catalog, package=package, schema_validator=validator
        )
    issue_types = {issue.type for issue in excinfo.value.validation_errors}
    assert issue_types == {"alias_leaked_into_free_text"}
    assert len(excinfo.value.validation_errors) == 2

    repaired_payload = _valid_payload(
        traceability=["Basado en la decision registrada en PROG1, parrafo MAIN."],
        claims=[{"claim_id": "c1", "field": "statement", "evidence_refs": [alias]}],
    )
    rule_draft = assemble_rule_draft_with_evidence_catalog(
        repaired_payload, catalog=catalog, package=package, schema_validator=validator
    )
    assert rule_draft.traceability == ["Basado en la decision registrada en PROG1, parrafo MAIN."]
    stable_json = rule_draft.to_stable_json()
    for leaked_alias in all_aliases:
        assert leaked_alias not in stable_json


def test_regression_real_run_effect_alias_leak_rejected_then_repaired_text_accepted() -> None:
    """Regresion exacta de los dos incidentes reales v1.18.2
    multi-corpus (`PAQUETE_SINTETICO_CLIENTES_EMPRESAS_MULTIPROGRAMA_
    15_REGLAS.zip` candidato VALIDAR-LINEA-PARA,
    `PAQUETE_SINTETICO_PRESTAMOS_EMPRESAS_5_REGLAS.zip` candidato
    VALIDAR-MORA-VIGENTE): ambos candidatos reales tenian una evidencia
    `return_code_effect` que respaldaba el claim de `effect` en
    evidence_refs, y el modelo (reproducido en vivo contra gpt-4o-mini
    real) escribio litealmente ese mismo alias como VALOR de `effect`
    en vez de una oracion de negocio -- agotando los 2 intentos de
    reparacion estructural disponibles porque, a diferencia de
    traceability, `rule_writer_user.md` nunca daba un ejemplo de que
    `effect` debe ser siempre una oracion de negocio. Aqui se reproduce
    con el alias real del catalogo de `_package()`: el primer intento
    (effect=alias) debe rechazarse; una reparacion con oracion de
    negocio real, preservando el evidence_refs del claim de effect sin
    cambios, debe aceptarse."""
    validator = _real_schema_validator()
    package = _package()
    catalog = build_evidence_catalog(package)
    alias = _decision_alias(catalog)

    poisoned_payload = _valid_payload(
        effect=alias,
        claims=[{"claim_id": "c1", "field": "effect", "evidence_refs": [alias]}],
    )
    with pytest.raises(RuleDraftAssemblyError) as excinfo:
        assemble_rule_draft_with_evidence_catalog(
            poisoned_payload, catalog=catalog, package=package, schema_validator=validator
        )
    issues = excinfo.value.validation_errors
    assert any(
        issue.type == "alias_leaked_into_free_text" and issue.loc == "effect" for issue in issues
    )

    repaired_payload = _valid_payload(
        effect="Se rechaza la operacion con el codigo de retorno R001.",
        claims=[{"claim_id": "c1", "field": "effect", "evidence_refs": [alias]}],
    )
    rule_draft = assemble_rule_draft_with_evidence_catalog(
        repaired_payload, catalog=catalog, package=package, schema_validator=validator
    )
    assert rule_draft.effect == "Se rechaza la operacion con el codigo de retorno R001."
    assert rule_draft.claims[0].evidence_ids == ["ev-1"]
    assert alias not in rule_draft.to_stable_json()


@pytest.mark.parametrize(
    "embedded_text",
    [
        "[E001]",
        "(E001)",
        "según E001",
        "evidencia E001",
        "rechazar operación [E001]",
        "E001 implica rechazo",
        "código E001",
    ],
)
def test_alias_embedded_in_prose_is_never_flagged_or_rewritten(embedded_text: str) -> None:
    """Matriz adversarial (checkpoint v1.18.2 multi-corpus, seccion 12):
    `_check_no_bare_aliases_in_free_text` distingue de forma
    CONSERVADORA un alias tecnico de texto de negocio legitimo -- solo
    coincidencia EXACTA del VALOR COMPLETO de un campo dispara el
    rechazo. Un alias EMBEBIDO dentro de una oracion mas larga (con
    corchetes, parentesis, precedido de "segun"/"evidencia", o un texto
    de negocio ambiguo como "codigo E001") nunca se marca ni se reescribe
    en silencio -- ninguna heuristica de substring/patron generico existe
    ni debe agregarse: ante la ambiguedad, el texto se preserva
    exactamente tal cual lo escribio el modelo (nunca se asume que es un
    alias filtrado)."""
    validator = _real_schema_validator()
    package = _package()
    catalog = build_evidence_catalog(package)
    alias = _decision_alias(catalog)
    text = embedded_text.replace("E001", alias)

    payload = _valid_payload(
        effect=text,
        claims=[{"claim_id": "c1", "field": "effect", "evidence_refs": [alias]}],
    )
    rule_draft = assemble_rule_draft_with_evidence_catalog(
        payload, catalog=catalog, package=package, schema_validator=validator
    )
    assert rule_draft.effect == text
