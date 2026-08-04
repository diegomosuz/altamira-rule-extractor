"""Tests del ejecutor PURO de guardrails sobre un RuleDraft shadow
(Fase 13 Parte 8, `feat/unified-shadow-downstream-pipeline`)."""

from __future__ import annotations

from pathlib import Path

import jsonschema

from altamira_extractor.contracts.context_package import ContextPackage
from altamira_extractor.contracts.enums import GuardrailVerdict
from altamira_extractor.pipeline.rule_draft_assembly import load_rule_draft_schema
from altamira_extractor.pipeline.unified_shadow_context_adapter import (
    adapt_group_to_context_view,
)
from altamira_extractor.pipeline.unified_shadow_context_assembler import (
    assemble_shadow_context_package,
)
from altamira_extractor.pipeline.unified_shadow_draft_generator import (
    DeterministicFakeDraftProvider,
    generate_shadow_rule_draft,
)
from altamira_extractor.pipeline.unified_shadow_guardrail_runner import (
    run_shadow_guardrails,
    to_shadow_view,
)

from ._unified_shadow_downstream_fixtures import downstream_golden_path
from ._unified_shadow_validation_fixtures import GROUP_ID, HASH

_SCHEMA_PATH = Path("schemas/rule-draft.schema.json")


def _validator() -> jsonschema.protocols.Validator:
    schema, _hash = load_rule_draft_schema(_SCHEMA_PATH)
    validator_cls = jsonschema.validators.validator_for(schema)
    return validator_cls(schema)


def _package() -> ContextPackage:
    dgp = downstream_golden_path()
    group = dgp.unified_shadow.shadow_groups[0]
    members_by_id = {m.member_id: m for m in dgp.unified_shadow.shadow_members}
    view = adapt_group_to_context_view(group, members_by_id=members_by_id)
    return assemble_shadow_context_package(
        view, semantic_graph=dgp.semantic_graph, source_package_hash=HASH
    )


def test_valid_draft_passes_guardrails() -> None:
    package = _package()
    result = generate_shadow_rule_draft(
        package=package, provider=DeterministicFakeDraftProvider(), schema_validator=_validator()
    )

    report = run_shadow_guardrails(
        result.rule_draft, package, group_id=GROUP_ID, source_package_hash=HASH
    )

    assert report.verdict == GuardrailVerdict.EVIDENCE_VALIDATED
    assert report.violations == []


def test_report_candidate_id_is_group_id_never_a_member_id() -> None:
    package = _package()
    result = generate_shadow_rule_draft(
        package=package, provider=DeterministicFakeDraftProvider(), schema_validator=_validator()
    )

    report = run_shadow_guardrails(
        result.rule_draft, package, group_id=GROUP_ID, source_package_hash=HASH
    )

    assert report.candidate_id == GROUP_ID


def test_guardrail_evaluation_is_deterministic() -> None:
    package = _package()
    result = generate_shadow_rule_draft(
        package=package, provider=DeterministicFakeDraftProvider(), schema_validator=_validator()
    )

    report_1 = run_shadow_guardrails(
        result.rule_draft, package, group_id=GROUP_ID, source_package_hash=HASH
    )
    report_2 = run_shadow_guardrails(
        result.rule_draft, package, group_id=GROUP_ID, source_package_hash=HASH
    )

    assert to_shadow_view(report_1).to_stable_json() == to_shadow_view(report_2).to_stable_json()


def test_repair_attempts_is_always_zero_no_repair_loop_in_shadow_mode() -> None:
    package = _package()
    result = generate_shadow_rule_draft(
        package=package, provider=DeterministicFakeDraftProvider(), schema_validator=_validator()
    )

    report = run_shadow_guardrails(
        result.rule_draft, package, group_id=GROUP_ID, source_package_hash=HASH
    )

    assert report.repair_attempts == 0


def test_guardrail_runner_does_not_mutate_inputs() -> None:
    package = _package()
    result = generate_shadow_rule_draft(
        package=package, provider=DeterministicFakeDraftProvider(), schema_validator=_validator()
    )
    package_snapshot = package.model_copy(deep=True)
    draft_snapshot = result.rule_draft.model_copy(deep=True)

    run_shadow_guardrails(result.rule_draft, package, group_id=GROUP_ID, source_package_hash=HASH)

    assert package == package_snapshot
    assert result.rule_draft == draft_snapshot


class TestShadowViewExcludesTimestamps:
    """Auditoria de seguridad de cierre de Fase 13, Parte 3: el
    `GuardrailReport` productivo real (calculado en memoria por
    `run_shadow_guardrails`, sin modificar su contrato) SI tiene
    `evaluated_at` -- pero `to_shadow_view` es la UNICA representacion
    que Fase 13 persiste, y la excluye por completo."""

    def test_shadow_view_has_no_evaluated_at_field(self) -> None:
        package = _package()
        result = generate_shadow_rule_draft(
            package=package,
            provider=DeterministicFakeDraftProvider(),
            schema_validator=_validator(),
        )
        report = run_shadow_guardrails(
            result.rule_draft, package, group_id=GROUP_ID, source_package_hash=HASH
        )

        view = to_shadow_view(report)

        assert not hasattr(view, "evaluated_at")
        assert "evaluated_at" not in type(view).model_fields

    def test_shadow_view_serialization_contains_no_temporal_key(self) -> None:
        package = _package()
        result = generate_shadow_rule_draft(
            package=package,
            provider=DeterministicFakeDraftProvider(),
            schema_validator=_validator(),
        )
        report = run_shadow_guardrails(
            result.rule_draft, package, group_id=GROUP_ID, source_package_hash=HASH
        )

        serialized = to_shadow_view(report).to_stable_json()

        for forbidden_key in (
            '"evaluated_at"',
            '"created_at"',
            '"updated_at"',
            '"generated_at"',
            '"timestamp"',
        ):
            assert forbidden_key not in serialized

    def test_shadow_view_preserves_verdict_violations_and_source_hash(self) -> None:
        package = _package()
        result = generate_shadow_rule_draft(
            package=package,
            provider=DeterministicFakeDraftProvider(),
            schema_validator=_validator(),
        )
        report = run_shadow_guardrails(
            result.rule_draft, package, group_id=GROUP_ID, source_package_hash=HASH
        )

        view = to_shadow_view(report)

        assert view.candidate_id == report.candidate_id
        assert view.verdict == report.verdict
        assert view.violations == report.violations
        assert view.repair_attempts == report.repair_attempts
        assert view.source_package_hash == report.source_package_hash
