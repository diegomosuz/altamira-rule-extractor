"""Tests del generador PURO de RuleDraft shadow mediante el fake
determinista oficial (Fase 13 Parte 7,
`feat/unified-shadow-downstream-pipeline`)."""

from __future__ import annotations

from pathlib import Path

import jsonschema
import pytest

from altamira_extractor.contracts.context_package import ContextPackage
from altamira_extractor.pipeline.evidence_catalog import build_evidence_catalog
from altamira_extractor.pipeline.rule_draft_assembly import load_rule_draft_schema
from altamira_extractor.pipeline.unified_shadow_context_adapter import (
    adapt_group_to_context_view,
)
from altamira_extractor.pipeline.unified_shadow_context_assembler import (
    assemble_shadow_context_package,
)
from altamira_extractor.pipeline.unified_shadow_draft_generator import (
    DeterministicFakeDraftProvider,
    DraftGenerationError,
    generate_shadow_rule_draft,
)

from ._unified_shadow_downstream_fixtures import downstream_golden_path
from ._unified_shadow_validation_fixtures import HASH

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


def test_generates_valid_rule_draft_bound_to_group() -> None:
    package = _package()

    result = generate_shadow_rule_draft(
        package=package, provider=DeterministicFakeDraftProvider(), schema_validator=_validator()
    )

    assert result.rule_draft.condition == package.decision.normalized_expression
    assert package.candidate.candidate_id in result.rule_draft.traceability


def test_evidence_aliases_used_come_from_real_catalog() -> None:
    package = _package()

    result = generate_shadow_rule_draft(
        package=package, provider=DeterministicFakeDraftProvider(), schema_validator=_validator()
    )

    known_aliases = {entry.alias for entry in build_evidence_catalog(package).entries}
    assert set(result.evidence_aliases_used) <= known_aliases
    assert result.evidence_aliases_used != ()
    assert result.evidence_aliases_unresolved == ()


def test_generation_is_deterministic() -> None:
    package = _package()
    provider = DeterministicFakeDraftProvider()
    validator = _validator()

    result_1 = generate_shadow_rule_draft(
        package=package, provider=provider, schema_validator=validator
    )
    result_2 = generate_shadow_rule_draft(
        package=package, provider=provider, schema_validator=validator
    )

    assert result_1.payload_hash == result_2.payload_hash
    assert result_1.rule_draft_hash == result_2.rule_draft_hash
    assert result_1.rule_draft.to_stable_json() == result_2.rule_draft.to_stable_json()


def test_rejects_non_fake_provider_by_exact_type() -> None:
    package = _package()

    class _OtherProvider(DeterministicFakeDraftProvider):
        pass

    with pytest.raises(DraftGenerationError):
        generate_shadow_rule_draft(
            package=package, provider=_OtherProvider(), schema_validator=_validator()
        )


def test_rejects_arbitrary_object_as_provider() -> None:
    package = _package()
    bogus_provider: DeterministicFakeDraftProvider = object()  # type: ignore[assignment]

    with pytest.raises(DraftGenerationError):
        generate_shadow_rule_draft(
            package=package, provider=bogus_provider, schema_validator=_validator()
        )


def test_invented_alias_fails_draft_assembly_never_reaches_a_valid_draft() -> None:
    """Consecuencia HONESTA de reutilizar `assemble_rule_draft_with_
    evidence_catalog` sin modificarla (ver docstring del modulo): un
    alias inventado hace fallar la ASAMBLEA -- nunca produce un
    `DraftGenerationResult` con `evidence_aliases_unresolved` no vacio."""
    package = _package()
    provider = DeterministicFakeDraftProvider(inject_unresolvable_alias=True)

    with pytest.raises(DraftGenerationError):
        generate_shadow_rule_draft(
            package=package, provider=provider, schema_validator=_validator()
        )


def test_draft_generator_does_not_mutate_package() -> None:
    package = _package()
    snapshot = package.model_copy(deep=True)

    generate_shadow_rule_draft(
        package=package, provider=DeterministicFakeDraftProvider(), schema_validator=_validator()
    )

    assert package == snapshot
