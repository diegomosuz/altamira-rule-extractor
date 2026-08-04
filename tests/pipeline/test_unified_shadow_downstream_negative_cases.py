"""Casos negativos aislados (Fase 13 Parte 14,
`feat/unified-shadow-downstream-pipeline`) A-G. Cada test parte de
`downstream_golden_path()`/`downstream_golden_path_two_groups()` -- los
mismos escenarios sinteticos internamente coherentes usados por el
resto de la Parte 12 -- y muta UNICAMENTE lo necesario para aislar un
defecto especifico."""

from __future__ import annotations

from pathlib import Path

import jsonschema
import pytest

from altamira_extractor.contracts.unified_shadow_downstream import (
    UnifiedShadowDownstreamDisposition,
    UnifiedShadowDownstreamExecutionStatus,
)
from altamira_extractor.contracts.unified_shadow_validation import (
    UnifiedShadowValidationDisposition,
)
from altamira_extractor.pipeline.rule_draft_assembly import load_rule_draft_schema
from altamira_extractor.pipeline.unified_shadow_downstream_executor import (
    run_unified_shadow_downstream,
)
from altamira_extractor.pipeline.unified_shadow_draft_generator import (
    DeterministicFakeDraftProvider,
    DraftGenerationError,
)

from ._unified_shadow_downstream_fixtures import (
    downstream_golden_path,
    downstream_golden_path_two_groups,
)
from ._unified_shadow_validation_fixtures import GROUP_ID, stable_hash

_SCHEMA_PATH = Path("schemas/rule-draft.schema.json")


def _validator() -> jsonschema.protocols.Validator:
    schema, _hash = load_rule_draft_schema(_SCHEMA_PATH)
    validator_cls = jsonschema.validators.validator_for(schema)
    return validator_cls(schema)


def test_case_a_validation_review_required_produces_not_executed_no_draft() -> None:
    """A. Disposicion de validacion REVIEW_REQUIRED: cero drafts
    generados, artefacto NOT_EXECUTED -- NUNCA un error tecnico
    (equivalente logico de "exit 0" a nivel de excepcion: la funcion
    retorna normalmente, no lanza)."""
    dgp = downstream_golden_path()
    mutated_report = dgp.validation_report.model_copy(
        update={"disposition": UnifiedShadowValidationDisposition.REVIEW_REQUIRED}
    )

    artifact = run_unified_shadow_downstream(
        run_id=dgp.unified_shadow.run_id,
        unified_shadow=dgp.unified_shadow,
        validation_report=mutated_report,
        semantic_graph=dgp.semantic_graph,
        provider=DeterministicFakeDraftProvider(),
        schema_validator=_validator(),
    )

    assert artifact.disposition == UnifiedShadowDownstreamDisposition.NOT_EXECUTED
    assert artifact.rule_drafts == []


def test_case_b_validation_blocked_produces_not_executed_no_draft() -> None:
    """B. Disposicion de validacion BLOCKED: cero drafts generados,
    artefacto NOT_EXECUTED -- NUNCA un error tecnico."""
    dgp = downstream_golden_path()
    mutated_report = dgp.validation_report.model_copy(
        update={"disposition": UnifiedShadowValidationDisposition.BLOCKED}
    )

    artifact = run_unified_shadow_downstream(
        run_id=dgp.unified_shadow.run_id,
        unified_shadow=dgp.unified_shadow,
        validation_report=mutated_report,
        semantic_graph=dgp.semantic_graph,
        provider=DeterministicFakeDraftProvider(),
        schema_validator=_validator(),
    )

    assert artifact.disposition == UnifiedShadowDownstreamDisposition.NOT_EXECUTED
    assert artifact.rule_drafts == []


def test_case_c_invented_alias_fails_draft_assembly_never_reaches_guardrails() -> None:
    """C. Alias inventado por el fake.

    HALLAZGO HONESTO (documentado desde la Parte 7): la redaccion
    literal de este caso ("guardrail REJECTED; draft preservado") NO
    se cumple con la reutilizacion sin modificar de
    `assemble_rule_draft_with_evidence_catalog` -- esa funcion
    productiva rechaza CUALQUIER alias no resuelto durante la ASAMBLEA
    misma, antes de que exista un `RuleDraft` valido. El resultado real
    y verificado es `DRAFT_GENERATION_FAILED`: el grupo nunca alcanza
    Guardrails, nunca se genera un `RuleDraft` (preservado o no). Se
    documenta aqui, en vez de forzar una ruta alternativa mas permisiva
    solo para que un alias invalido llegue a Guardrails, porque esta
    fase nunca relaja una validacion productiva existente."""
    dgp = downstream_golden_path()
    provider = DeterministicFakeDraftProvider(inject_unresolvable_alias=True)

    artifact = run_unified_shadow_downstream(
        run_id=dgp.unified_shadow.run_id,
        unified_shadow=dgp.unified_shadow,
        validation_report=dgp.validation_report,
        semantic_graph=dgp.semantic_graph,
        provider=provider,
        schema_validator=_validator(),
    )

    group_result = artifact.group_results[0]
    assert (
        group_result.execution_status
        == UnifiedShadowDownstreamExecutionStatus.DRAFT_GENERATION_FAILED
    )
    assert artifact.rule_drafts == []
    assert artifact.guardrail_results == []
    assert artifact.disposition == UnifiedShadowDownstreamDisposition.BLOCKED


def test_case_d_insufficient_evidence_rejected_at_context_assembly() -> None:
    """D. Evidencia insuficiente (cero `evidence_ids` en el grupo): el
    ensamblador de contexto (Parte 6) exige al menos una entrada de
    evidencia para construir D-evidence -- el grupo se rechaza en
    CONTEXT_ASSEMBLY_FAILED, nunca se completa."""
    dgp = downstream_golden_path()
    group = dgp.unified_shadow.shadow_groups[0].model_copy(update={"evidence_ids": []})
    unified_shadow = dgp.unified_shadow.model_copy(update={"shadow_groups": [group]})
    validation_report = dgp.validation_report.model_copy(
        update={"unified_candidates_shadow_hash": stable_hash(unified_shadow)}
    )

    artifact = run_unified_shadow_downstream(
        run_id=dgp.unified_shadow.run_id,
        unified_shadow=unified_shadow,
        validation_report=validation_report,
        semantic_graph=dgp.semantic_graph,
        provider=DeterministicFakeDraftProvider(),
        schema_validator=_validator(),
    )

    group_result = artifact.group_results[0]
    assert (
        group_result.execution_status
        == UnifiedShadowDownstreamExecutionStatus.CONTEXT_ASSEMBLY_FAILED
    )
    assert group_result.diagnostics != []
    assert artifact.context_packages == []


def test_case_e_stale_hash_raises_technical_error_no_partial_artifact() -> None:
    """E. Hash obsoleto (`unified_candidates_shadow_hash` del reporte
    de validacion no coincide con el `unified_shadow` real recibido):
    error tecnico -- `run_unified_shadow_downstream` lanza sin retornar
    ningun artefacto parcial (ver
    `tests/test_cli_unified_shadow_downstream.py` para la verificacion
    equivalente a nivel de servicio/CLI, sin archivo persistido)."""
    from altamira_extractor.pipeline.unified_shadow_downstream_executor import (
        UnifiedShadowDownstreamExecutorError,
    )

    dgp = downstream_golden_path()
    mutated_report = dgp.validation_report.model_copy(
        update={"unified_candidates_shadow_hash": "f" * 64}
    )

    with pytest.raises(UnifiedShadowDownstreamExecutorError):
        run_unified_shadow_downstream(
            run_id=dgp.unified_shadow.run_id,
            unified_shadow=dgp.unified_shadow,
            validation_report=mutated_report,
            semantic_graph=dgp.semantic_graph,
            provider=DeterministicFakeDraftProvider(),
            schema_validator=_validator(),
        )


def test_case_f_non_fake_provider_rejected_before_draft_generation() -> None:
    """F. Proveedor distinto al fake determinista: rechazado ANTES de
    generar cualquier draft (identidad EXACTA de tipo, ver Parte 7) --
    cero `RuleDraft` en el resultado, grupo `DRAFT_GENERATION_FAILED`."""
    from altamira_extractor.pipeline.unified_shadow_context_adapter import (
        adapt_group_to_context_view,
    )
    from altamira_extractor.pipeline.unified_shadow_context_assembler import (
        assemble_shadow_context_package,
    )
    from altamira_extractor.pipeline.unified_shadow_draft_generator import (
        generate_shadow_rule_draft,
    )

    dgp = downstream_golden_path()
    group = dgp.unified_shadow.shadow_groups[0]
    members_by_id = {m.member_id: m for m in dgp.unified_shadow.shadow_members}
    view = adapt_group_to_context_view(group, members_by_id=members_by_id)
    package = assemble_shadow_context_package(
        view,
        semantic_graph=dgp.semantic_graph,
        source_package_hash=dgp.unified_shadow.source_package_hash,
    )

    class _OtherProvider(DeterministicFakeDraftProvider):
        pass

    with pytest.raises(DraftGenerationError):
        generate_shadow_rule_draft(
            package=package, provider=_OtherProvider(), schema_validator=_validator()
        )


def test_case_g_one_group_failure_does_not_block_the_other_eligible_group() -> None:
    """G. El fallo AISLADO de un grupo (CONTEXT_ASSEMBLY_FAILED, un
    "fallo de pipeline" segun el contrato) nunca impide que otro grupo
    elegible se ejecute -- disposition global BLOCKED por la causa
    TIPADA especifica de ese grupo (ver
    `tests/contracts/test_unified_shadow_downstream.py::
    TestDispositionInvariants::test_completed_with_rejections_forbids_hard_pipeline_failures`
    para la variante equivalente con `GUARDRAIL_REJECTED`, que en
    cambio produce `COMPLETED_WITH_REJECTIONS`)."""
    dgp = downstream_golden_path_two_groups()

    artifact = run_unified_shadow_downstream(
        run_id=dgp.unified_shadow.run_id,
        unified_shadow=dgp.unified_shadow,
        validation_report=dgp.validation_report,
        semantic_graph=dgp.semantic_graph,
        provider=DeterministicFakeDraftProvider(),
        schema_validator=_validator(),
    )

    by_id = {gr.group_id: gr for gr in artifact.group_results}
    assert by_id[GROUP_ID].execution_status == UnifiedShadowDownstreamExecutionStatus.EXECUTED
    assert (
        by_id["group::candidate-2"].execution_status
        == UnifiedShadowDownstreamExecutionStatus.CONTEXT_ASSEMBLY_FAILED
    )
    assert artifact.disposition == UnifiedShadowDownstreamDisposition.BLOCKED
