"""Tests contractuales de observaciones POR-RUN del catalogo estatico
(Fase 15B2-A, Parte D): `contracts/semantic_coverage_observations.py`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts.enums import StatementKind
from altamira_extractor.contracts.semantic_coverage_observations import (
    SemanticCoverageConstructObservation,
    SemanticCoverageObservationsArtifact,
    SemanticCoverageObservationsSummary,
    SemanticCoverageUnsupportedObservation,
)

_HASH = "a" * 64


def _construct_observation(**overrides: object) -> SemanticCoverageConstructObservation:
    defaults: dict[str, object] = {
        "construct_id": "IF",
        "java_statement_kind": StatementKind.IF,
        "observed": True,
        "occurrence_count": 3,
        "program_count": 1,
        "shared_java_statement_kind_construct_ids": ["ELSE"],
    }
    defaults.update(overrides)
    return SemanticCoverageConstructObservation(**defaults)  # type: ignore[arg-type]


def test_observed_matches_occurrence_count() -> None:
    with pytest.raises(ValidationError, match="observed"):
        _construct_observation(observed=False, occurrence_count=1)


def test_zero_occurrences_requires_zero_programs() -> None:
    with pytest.raises(ValidationError, match="program_count"):
        _construct_observation(observed=False, occurrence_count=0, program_count=1)


def test_self_reference_in_shared_ids_rejected() -> None:
    with pytest.raises(ValidationError, match="a si mismo"):
        _construct_observation(shared_java_statement_kind_construct_ids=["IF"])


def test_shared_ids_out_of_order_rejected() -> None:
    with pytest.raises(ValidationError, match="ordenado"):
        _construct_observation(
            shared_java_statement_kind_construct_ids=["ELSE", "CONDITIONS_COMPOUND"]
        )


def test_unsupported_observation_allows_unmapped_construct() -> None:
    entry = SemanticCoverageUnsupportedObservation(
        identity="UnknownStatementImpl", construct_id=None, occurrence_count=2, program_count=1
    )
    assert entry.construct_id is None


def test_artifact_rejects_duplicate_construct_ids() -> None:
    obs = _construct_observation()
    with pytest.raises(ValidationError, match="duplicado"):
        SemanticCoverageObservationsArtifact(
            run_id="run-1",
            source_package_hash=_HASH,
            manifest_edition="edition-1",
            constructs=[obs, obs],
            unsupported_identities=[],
            summary=SemanticCoverageObservationsSummary(
                construct_count=2,
                observed_construct_count=2,
                unsupported_identity_count=0,
                mapped_unsupported_identity_count=0,
            ),
        )


def test_artifact_summary_must_match_observed_count() -> None:
    obs = _construct_observation()
    bad_summary = SemanticCoverageObservationsSummary(
        construct_count=1,
        observed_construct_count=0,
        unsupported_identity_count=0,
        mapped_unsupported_identity_count=0,
    )
    with pytest.raises(ValidationError, match="observed_construct_count"):
        SemanticCoverageObservationsArtifact(
            run_id="run-1",
            source_package_hash=_HASH,
            manifest_edition="edition-1",
            constructs=[obs],
            unsupported_identities=[],
            summary=bad_summary,
        )


def test_artifact_summary_must_match_mapped_unsupported_count() -> None:
    entries = [
        SemanticCoverageUnsupportedObservation(
            identity="AddStatementImpl", construct_id="ADD", occurrence_count=1, program_count=1
        ),
        SemanticCoverageUnsupportedObservation(
            identity="UnknownStatementImpl", construct_id=None, occurrence_count=1, program_count=1
        ),
    ]
    bad_summary = SemanticCoverageObservationsSummary(
        construct_count=0,
        observed_construct_count=0,
        unsupported_identity_count=2,
        mapped_unsupported_identity_count=2,
    )
    with pytest.raises(ValidationError, match="mapped_unsupported_identity_count"):
        SemanticCoverageObservationsArtifact(
            run_id="run-1",
            source_package_hash=_HASH,
            manifest_edition="edition-1",
            constructs=[],
            unsupported_identities=entries,
            summary=bad_summary,
        )


def test_artifact_valid_round_trips_json() -> None:
    obs = _construct_observation()
    entry = SemanticCoverageUnsupportedObservation(
        identity="AddStatementImpl", construct_id="ADD", occurrence_count=1, program_count=1
    )
    artifact = SemanticCoverageObservationsArtifact(
        run_id="run-1",
        source_package_hash=_HASH,
        manifest_edition="edition-1",
        constructs=[obs],
        unsupported_identities=[entry],
        summary=SemanticCoverageObservationsSummary(
            construct_count=1,
            observed_construct_count=1,
            unsupported_identity_count=1,
            mapped_unsupported_identity_count=1,
        ),
    )
    reloaded = SemanticCoverageObservationsArtifact.model_validate_json(artifact.to_stable_json())
    assert reloaded == artifact
