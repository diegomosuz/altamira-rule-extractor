"""Fixtures compartidas para los tests de contratos tipados."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from altamira_extractor.contracts import (
    ApplicabilityStatus,
    ApplicableParameterRow,
    AttributionScope,
    BatchContext,
    BatchContextStatus,
    Claim,
    ClaimField,
    CodeSliceEntry,
    Completeness,
    CompletenessStatus,
    ContextPackage,
    ContextPackageCandidate,
    ContextPackageDecision,
    ContextPackageOperation,
    ContextPackageScope,
    DataContext,
    DomainGlossaryEntry,
    Effects,
    EvidenceEntry,
    EvidenceValidationStatus,
    GraphNode,
    GraphRelationship,
    InclusionReason,
    Manifest,
    ManifestApplication,
    ManifestCountry,
    ManifestImplementation,
    ManifestOperation,
    ManifestSource,
    NodeLabel,
    ParameterTableContext,
    RelationshipType,
    ReturnCodeEffect,
    RuleDraft,
    SemanticGraph,
    SourceFormat,
    TableEffect,
    TableEffectOperation,
    TransactionalTableRead,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMAS_DIR = REPO_ROOT / "schemas"

VALID_HASH = "a" * 64


def load_schema(name: str) -> dict[str, Any]:
    with (SCHEMAS_DIR / name).open(encoding="utf-8") as handle:
        result: dict[str, Any] = json.load(handle)
        return result


def assert_matches_schema(instance: dict[str, Any], schema: dict[str, Any]) -> None:
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    validator = validator_cls(schema)
    errors = sorted(validator.iter_errors(instance), key=str)
    assert not errors, "\n".join(f"{e.json_path}: {e.message}" for e in errors)


@pytest.fixture
def context_package_schema() -> dict[str, Any]:
    return load_schema("context-package.schema.json")


@pytest.fixture
def rule_draft_schema() -> dict[str, Any]:
    return load_schema("rule-draft.schema.json")


@pytest.fixture
def semantic_graph_schema() -> dict[str, Any]:
    return load_schema("semantic-graph.schema.json")


@pytest.fixture
def valid_manifest() -> Manifest:
    return Manifest(
        schema_version="1.0",
        country=ManifestCountry(code="AR", name="Argentina"),
        application=ManifestApplication(name="Transferencias"),
        operation=ManifestOperation(
            logical_name="OP-TRF-PROPIA", description="Transferencia propia"
        ),
        implementation=ManifestImplementation(version="3.2", entry_programs=["ARTRFPROP01"]),
        source=ManifestSource(format=SourceFormat.FIXED, encoding="CP037"),
        parameter_tables=[],
    )


@pytest.fixture
def valid_semantic_graph() -> SemanticGraph:
    return SemanticGraph(
        schema_version="2.0",
        source_package_hash=VALID_HASH,
        nodes=[
            GraphNode(
                id="program::AR::op::PROG::1::abc123",
                labels=[NodeLabel.PROGRAM],
                properties={},
            ),
            GraphNode(
                id="program::AR::op::PROG::1::abc123::paragraph::PARA-1",
                labels=[NodeLabel.PARAGRAPH],
                properties={"name": "PARA-1"},
            ),
            GraphNode(
                id="table::AR::default::PARM01",
                labels=[NodeLabel.TABLE, NodeLabel.PARAMETER_TABLE],
                properties={"name": "PARM01"},
            ),
        ],
        relationships=[
            GraphRelationship(
                type=RelationshipType.CONTAINS,
                from_id="program::AR::op::PROG::1::abc123",
                to_id="program::AR::op::PROG::1::abc123::paragraph::PARA-1",
                properties={},
            ),
        ],
        warnings=[],
    )


@pytest.fixture
def valid_context_package() -> ContextPackage:
    return ContextPackage(
        schema_version="2.0",
        candidate=ContextPackageCandidate(
            candidate_id="cand-1",
            decision_id="dec-1",
            detector_id="return-code-decision",
            detector_version="1.0",
            detector_score=0.9,
        ),
        scope=ContextPackageScope(
            country="AR",
            application="Transferencias",
            operation=ContextPackageOperation(logical_name="OP-TRF-PROPIA", description=None),
            program="ARTRFPROP01",
            program_version="3.2",
            paragraph="PARA-1",
            source_file="cobol/ARTRFPROP01.cbl",
            line_start=10,
            line_end=20,
            source_package_hash=VALID_HASH,
        ),
        code_slice=[
            CodeSliceEntry(
                paragraph_id="p1",
                paragraph="PARA-1",
                source_file="cobol/ARTRFPROP01.cbl",
                source_text="IF WS-MONTO > WS-LIMITE",
                line_start=10,
                line_end=20,
                inclusion_reason=InclusionReason.CANDIDATE,
                evidence_ids=["ev-1"],
            ),
        ],
        data_context=DataContext(
            parameter_tables=[
                ParameterTableContext(
                    name="PARM01",
                    snapshot_date="2026-05-15",
                    predicates=["WS-PAIS = 'AR'"],
                    resolved_predicates=["WS-PAIS = 'AR'"],
                    unresolved_predicates=[],
                    applicability_status=ApplicabilityStatus.EXACT,
                    applicable_rows=[
                        ApplicableParameterRow(
                            parameter_entry_id="parameter::PARM01::entry::1::abc123456789",
                            values={"limite": 1000},
                        )
                    ],
                    context_rows=[],
                    evidence_ids=["ev-2"],
                ),
            ],
            transactional_tables_read=[
                TransactionalTableRead(name="CUENTAS", evidence_ids=["ev-3"])
            ],
        ),
        decision=ContextPackageDecision(
            expression="WS-MONTO > WS-LIMITE",
            normalized_expression="WS-MONTO > WS-LIMITE",
            operands=["WS-MONTO", "WS-LIMITE"],
            rule_type="threshold",
            outcome_code="R001",
            evidence_ids=["ev-1"],
        ),
        effects=Effects(
            return_codes=[
                ReturnCodeEffect(code="R001", approved_for_rule_text=True, evidence_ids=["ev-1"])
            ],
            table_effects=[
                TableEffect(
                    table="CUENTAS",
                    operation=TableEffectOperation.UPDATES,
                    attribution_scope=AttributionScope.DIRECT,
                    approved_for_rule_text=True,
                    evidence_ids=["ev-3"],
                ),
                TableEffect(
                    table="LOG_AUDITORIA",
                    operation=TableEffectOperation.INSERTS,
                    attribution_scope=AttributionScope.PROGRAM_CONTEXT,
                    approved_for_rule_text=False,
                    evidence_ids=["ev-4"],
                ),
            ],
        ),
        batch_context=BatchContext(status=BatchContextStatus.NOT_AVAILABLE, downstream_jobs=[]),
        domain_glossary=[
            DomainGlossaryEntry(
                data_item_id="program::AR::op::PROG::1::abc123::data::WS-MONTO",
                technical_name="WS-MONTO",
                semantic_tag="amount",
                domain_term_id="term::1.0::requested_amount",
                functional_name="importe solicitado",
                definition="Importe solicitado por el cliente",
                entity_type="monetary_amount",
                source_kind="CURATED_CONFIG",
                authoritative_source="V1 controlled glossary",
                confidence=1.0,
                evidence_ids=["ev-1"],
            ),
        ],
        evidence=[
            EvidenceEntry(
                evidence_id="ev-1",
                kind="decision",
                source_file="cobol/ARTRFPROP01.cbl",
                line_start=10,
                line_end=20,
                source_package_hash=VALID_HASH,
            ),
            EvidenceEntry(
                evidence_id="ev-2",
                kind="parameter_row",
                source_file="parametria/ddl/PARM01.sql",
                line_start=None,
                line_end=None,
                source_package_hash=VALID_HASH,
            ),
            EvidenceEntry(
                evidence_id="ev-3",
                kind="sql_access",
                source_file="cobol/ARTRFPROP01.cbl",
                line_start=25,
                line_end=27,
                source_package_hash=VALID_HASH,
            ),
            EvidenceEntry(
                evidence_id="ev-4",
                kind="sql_access",
                source_file="cobol/ARTRFPROP01.cbl",
                line_start=40,
                line_end=41,
                source_package_hash=VALID_HASH,
            ),
        ],
        completeness=Completeness(
            D1=CompletenessStatus.COMPLETE,
            D2=CompletenessStatus.COMPLETE,
            D3=CompletenessStatus.COMPLETE,
            D4=CompletenessStatus.COMPLETE,
            D5=CompletenessStatus.COMPLETE,
            D6=CompletenessStatus.NOT_AVAILABLE,
            D7=CompletenessStatus.COMPLETE,
        ),
    )


@pytest.fixture
def valid_rule_draft() -> RuleDraft:
    return RuleDraft(
        schema_version="2.0",
        title="Limite de monto para transferencia propia",
        context="Transferencias propias en Argentina",
        statement=(
            "Si el monto solicitado supera el limite parametrico, la transferencia se rechaza"
        ),
        condition="WS-MONTO > WS-LIMITE",
        parameters=["limite=1000"],
        effect="Se actualiza CUENTAS y se retorna el codigo R001",
        parameter_source="PARM01",
        traceability=["ev-1", "ev-3"],
        limitations=["Requiere revision funcional antes de publicarse"],
        claims=[
            Claim(
                claim_id="claim-1",
                field=ClaimField.STATEMENT,
                evidence_paths=["$.decision.expression"],
                evidence_ids=["ev-1"],
            ),
        ],
        evidence_validation_status=EvidenceValidationStatus.EVIDENCE_VALIDATED,
    )
