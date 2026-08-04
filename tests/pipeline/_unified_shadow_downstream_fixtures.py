"""Fixtures sinteticas compartidas para los tests de la ejecucion
downstream del artefacto unificado en shadow mode (Fase 13,
`feat/unified-shadow-downstream-pipeline`). NO es un archivo de tests
(pytest lo ignora, no empieza con `test_`).

Reutiliza el `golden_path()`/`analyze_golden_path()` de Fase 12
(`tests/pipeline/_unified_shadow_validation_fixtures.py`) -- el mismo
escenario CALLER10/MAIN/WS-COD-RETORNO/R001 -- y le agrega el UNICO
elemento que Fase 12 no necesitaba: un `SemanticGraph` sintetico minimo
pero INTERNAMENTE COHERENTE (satisface todos los `model_validator` de
`contracts/semantic_graph.py`) que representa EXACTAMENTE la misma
jerarquia Country/Application/Operation/Program/Paragraph/Decision,
para que `unified_shadow_context_assembler.py` pueda resolverla via
`Program.name`/`Paragraph.name`/`Decision.outcome_code` -- el mismo
principio de igualdad exacta que las queries Q1-Q7 productivas, sin
necesitar Neo4j ni el parser Java real."""

from __future__ import annotations

from dataclasses import dataclass

from altamira_extractor.contracts.candidate_promotion_assessment import (
    CandidateSource,
    UnifiedRuleFamily,
)
from altamira_extractor.contracts.enums import NodeLabel, RelationshipType
from altamira_extractor.contracts.semantic_graph import (
    GraphNode,
    GraphRelationship,
    SemanticGraph,
)
from altamira_extractor.contracts.unified_candidates_shadow import (
    UnifiedCandidatesShadowArtifact,
    UnifiedCandidatesShadowSummary,
    UnifiedShadowCandidateGroup,
    UnifiedShadowComparisonKind,
    UnifiedShadowGroupStatus,
    UnifiedShadowSupport,
)
from altamira_extractor.contracts.unified_shadow_validation import (
    UnifiedShadowGateStatus,
    UnifiedShadowGroupValidation,
    UnifiedShadowValidationDisposition,
    UnifiedShadowValidationGate,
    UnifiedShadowValidationGateResult,
    UnifiedShadowValidationReport,
    UnifiedShadowValidationSummary,
)

from ._unified_shadow_validation_fixtures import (
    HASH,
    RUN_ID,
    GoldenPath,
    analyze_golden_path,
    golden_path_with_working_v2_resolution,
    second_member,
    stable_hash,
)

COUNTRY_ID = "country::AR"
APPLICATION_ID = "application::AR::Fase13App"
OPERATION_ID = "operation::AR::Fase13App::OP1"
PROGRAM_ID = "program::AR::CALLER10::CALLER10::1.0::" + "b" * 12
PARAGRAPH_ID = f"{PROGRAM_ID}::paragraph::MAIN"
DECISION_ID = f"{PARAGRAPH_ID}::decision::12::0"

PROGRAM_NAME = "CALLER10"
PARAGRAPH_NAME = "MAIN"
OUTCOME_CODE = "R001"


def semantic_graph() -> SemanticGraph:
    """Grafo minimo coherente con `golden_path()` (program=CALLER10,
    paragraph=MAIN, outcome_code=R001)."""
    nodes = [
        GraphNode(
            id=COUNTRY_ID,
            labels=[NodeLabel.COUNTRY],
            properties={"code": "AR", "name": "Argentina", "source_package_hash": HASH},
        ),
        GraphNode(
            id=APPLICATION_ID,
            labels=[NodeLabel.APPLICATION],
            properties={
                "name": "Fase13App",
                "country_code": "AR",
                "source_package_hash": HASH,
            },
        ),
        GraphNode(
            id=OPERATION_ID,
            labels=[NodeLabel.OPERATION],
            properties={
                "logical_name": "OP1",
                "description": "Operacion sintetica Fase 13",
                "country_code": "AR",
                "application_name": "Fase13App",
                "source_package_hash": HASH,
            },
        ),
        GraphNode(
            id=PROGRAM_ID,
            labels=[NodeLabel.PROGRAM],
            properties={
                "name": PROGRAM_NAME,
                "version": "1.0",
                "source_file": "CALLER10.cbl",
                "source_hash": HASH,
                "source_package_hash": HASH,
                "source_format": "COBOL",
                "encoding": "UTF-8",
            },
        ),
        GraphNode(
            id=PARAGRAPH_ID,
            labels=[NodeLabel.PARAGRAPH],
            properties={
                "name": PARAGRAPH_NAME,
                "source_text": (
                    "MAIN.\n    IF WS-SALDO < 0\n        MOVE 'R001' TO WS-COD-RETORNO\n    END-IF."
                ),
                "line_start": 10,
                "line_end": 20,
                "source_file": "CALLER10.cbl",
                "source_package_hash": HASH,
            },
        ),
        GraphNode(
            id=DECISION_ID,
            labels=[NodeLabel.DECISION],
            properties={
                "expression": "WS-SALDO < 0",
                "normalized_expression": "WS-SALDO < 0",
                "operands_json": "[]",
                "outcome_code": OUTCOME_CODE,
                "rule_type": "RETURN_CODE_RULE",
                "line_start": 12,
                "line_end": 14,
                "source_package_hash": HASH,
            },
        ),
    ]
    relationships = [
        GraphRelationship(
            type=RelationshipType.HAS_APPLICATION, from_id=COUNTRY_ID, to_id=APPLICATION_ID
        ),
        GraphRelationship(
            type=RelationshipType.HAS_OPERATION, from_id=APPLICATION_ID, to_id=OPERATION_ID
        ),
        GraphRelationship(
            type=RelationshipType.EXECUTES_VIA, from_id=OPERATION_ID, to_id=PROGRAM_ID
        ),
        GraphRelationship(type=RelationshipType.CONTAINS, from_id=PROGRAM_ID, to_id=PARAGRAPH_ID),
        GraphRelationship(
            type=RelationshipType.HAS_DECISION, from_id=PARAGRAPH_ID, to_id=DECISION_ID
        ),
    ]
    return SemanticGraph(
        source_package_hash=HASH,
        nodes=sorted(nodes, key=lambda n: n.id),
        relationships=sorted(relationships, key=lambda r: (r.type.value, r.from_id, r.to_id)),
    )


@dataclass(frozen=True)
class DownstreamGoldenPath:
    gp: GoldenPath
    unified_shadow: UnifiedCandidatesShadowArtifact
    validation_report: UnifiedShadowValidationReport
    semantic_graph: SemanticGraph


def downstream_golden_path() -> DownstreamGoldenPath:
    """Escenario FELIZ completo: `UnifiedCandidatesShadowArtifact` +
    `UnifiedShadowValidationReport` (Fase 12, calculado por el
    analizador real) + `SemanticGraph` sintetico compatible -- listo
    para `run_unified_shadow_downstream`."""
    gp, v2_artifact = golden_path_with_working_v2_resolution()
    report = analyze_golden_path(gp, v2=v2_artifact)
    return DownstreamGoldenPath(
        gp=gp,
        unified_shadow=gp.unified_shadow,
        validation_report=report,
        semantic_graph=semantic_graph(),
    )


SECOND_GROUP_ID = "group::candidate-2"
MISSING_PROGRAM_NAME = "MISSING-PROGRAM-NOT-IN-GRAPH"


def _all_gate_results() -> list[UnifiedShadowValidationGateResult]:
    return [
        UnifiedShadowValidationGateResult(
            gate=gate,
            status=UnifiedShadowGateStatus.PASS,
            required=True,
            blocking=gate != UnifiedShadowValidationGate.DOWNSTREAM_SHADOW_ELIGIBILITY,
        )
        for gate in UnifiedShadowValidationGate
    ]


def downstream_golden_path_two_groups() -> DownstreamGoldenPath:
    """Variante de DOS grupos elegibles, UNICAMENTE para verificar
    aislamiento de fallos por grupo (Fase 13 Parte 9): el grupo 1
    (`GROUP_ID`) apunta a `CALLER10`/`MAIN`, presente en
    `semantic_graph()`, y se ejecuta con exito; el grupo 2
    (`SECOND_GROUP_ID`) apunta a `MISSING_PROGRAM_NAME`, AUSENTE del
    grafo, y falla `CONTEXT_ASSEMBLY_FAILED` -- el fallo del grupo 2
    nunca debe impedir que el grupo 1 se ejecute.

    `unified_shadow`/`validation_report` se construyen DIRECTAMENTE
    contra sus contratos Pydantic (nunca via el analizador de Fase 12,
    que exige encadenar plan/review/assessment reales por cada member
    -- irrelevante para lo unico que este fixture necesita probar: que
    el EJECUTOR de Fase 13 aisla el fallo de un grupo del otro)."""
    gp, _v2_artifact = golden_path_with_working_v2_resolution()

    member_2 = second_member().model_copy(update={"program": MISSING_PROGRAM_NAME})
    group_1 = gp.unified_shadow.shadow_groups[0]
    group_2 = UnifiedShadowCandidateGroup(
        unified_shadow_candidate_id=SECOND_GROUP_ID,
        status=UnifiedShadowGroupStatus.VALID,
        rule_family=UnifiedRuleFamily.RETURN_CODE,
        program=MISSING_PROGRAM_NAME,
        target="WS-COD-RETORNO",
        output_literal="R001",
        support=UnifiedShadowSupport.DETERMINISTIC,
        member_ids=[member_2.member_id],
        comparison_to_v1=UnifiedShadowComparisonKind.NOT_IN_BASELINE,
        evidence_ids=member_2.evidence_ids,
        provenance_references=member_2.provenance_references,
        review_decision_ids=[member_2.review_decision_id],
    )

    unified_shadow = gp.unified_shadow.model_copy(
        update={
            "shadow_members": [*gp.unified_shadow.shadow_members, member_2],
            "shadow_groups": [group_1, group_2],
            "summary": UnifiedCandidatesShadowSummary(
                v1_baseline_count=0,
                proposed_plan_item_count=2,
                shadow_member_count=2,
                shadow_group_count=2,
                excluded_plan_item_count=0,
                exact_baseline_match_group_count=0,
                related_to_baseline_group_count=0,
                not_in_baseline_group_count=2,
                conflicting_with_baseline_group_count=0,
                not_evaluated_group_count=0,
                valid_group_count=2,
                invalid_group_count=0,
                counts_by_source={CandidateSource.V2: 2},
                counts_by_rule_family={UnifiedRuleFamily.RETURN_CODE: 2},
                counts_by_group_status={UnifiedShadowGroupStatus.VALID: 2},
                counts_by_baseline_comparison={UnifiedShadowComparisonKind.NOT_IN_BASELINE: 2},
                counts_by_exclusion_reason={},
            ),
        }
    )

    group_validations = sorted(
        [
            UnifiedShadowGroupValidation(
                group_id=group_1.unified_shadow_candidate_id,
                group_status=UnifiedShadowGroupStatus.VALID,
                comparison_to_v1=UnifiedShadowComparisonKind.NOT_IN_BASELINE,
                structurally_valid=True,
                downstream_shadow_eligible=True,
                member_ids=group_1.member_ids,
            ),
            UnifiedShadowGroupValidation(
                group_id=group_2.unified_shadow_candidate_id,
                group_status=UnifiedShadowGroupStatus.VALID,
                comparison_to_v1=UnifiedShadowComparisonKind.NOT_IN_BASELINE,
                structurally_valid=True,
                downstream_shadow_eligible=True,
                member_ids=group_2.member_ids,
            ),
        ],
        key=lambda gv: gv.group_id,
    )

    validation_report = UnifiedShadowValidationReport(
        run_id=RUN_ID,
        source_package_hash=HASH,
        unified_candidates_shadow_hash=stable_hash(unified_shadow),
        candidate_v1_artifact_hash=HASH,
        assessment_artifact_hash=HASH,
        review_package_hash=HASH,
        promotion_plan_hash=HASH,
        disposition=UnifiedShadowValidationDisposition.QUALIFIED_FOR_DOWNSTREAM_SHADOW,
        gate_results=_all_gate_results(),
        group_validations=group_validations,
        issues=[],
        summary=UnifiedShadowValidationSummary(
            baseline_candidate_count=0,
            shadow_member_count=2,
            shadow_group_count=2,
            valid_shadow_group_count=2,
            invalid_shadow_group_count=0,
            downstream_eligible_group_count=2,
            exact_baseline_match_group_count=0,
            related_to_baseline_group_count=0,
            not_in_baseline_group_count=2,
            conflicting_with_baseline_group_count=0,
            not_evaluated_group_count=0,
            groups_with_complete_evidence_count=2,
            groups_with_complete_provenance_count=2,
            groups_with_complete_decision_trace_count=2,
            error_count=0,
            warning_count=0,
            blocking_issue_count=0,
            counts_by_gate_status={UnifiedShadowGateStatus.PASS: 12},
            counts_by_group_status={UnifiedShadowGroupStatus.VALID: 2},
            counts_by_baseline_comparison={UnifiedShadowComparisonKind.NOT_IN_BASELINE: 2},
        ),
    )

    return DownstreamGoldenPath(
        gp=gp,
        unified_shadow=unified_shadow,
        validation_report=validation_report,
        semantic_graph=semantic_graph(),
    )
