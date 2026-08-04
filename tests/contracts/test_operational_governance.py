"""Tests del read model de gobierno operativo (Fase 15A Parte 3/12,
items 1-8, `feat/operational-governance-ui`)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts.operational_governance import (
    GovernanceArtifactStatus,
    GovernanceArtifactSummary,
    GovernanceEventChainStatus,
    GovernanceEventSummary,
    GovernanceGenerationReachability,
    GovernanceGenerationSummary,
    GovernanceIntegrityStatus,
    GovernanceIssue,
    GovernanceIssueCode,
    GovernanceIssueSeverity,
    OperationalGovernanceOverview,
    OperationalGovernanceStatus,
)
from altamira_extractor.contracts.unified_activation_materialization import (
    MaterializedActivationLane,
    MaterializedGenerationKind,
)

RUN_ID = "20260101T000000000000-aaaaaaaa"
HASH = "a" * 64


def _minimal_overview(**overrides: object) -> OperationalGovernanceOverview:
    fields: dict[str, object] = {
        "run_id": RUN_ID,
        "run_stage": "CANDIDATES_DETECTED",
        "activation_initialized": False,
        "status": OperationalGovernanceStatus.NOT_INITIALIZED,
        "event_chain_status": GovernanceEventChainStatus.EMPTY,
        "event_chain_length": 0,
        "generation_count": 0,
        "confirmed_event_count": 0,
        "orphan_generation_count": 0,
        "orphan_event_count": 0,
        "active_manifest_integrity": GovernanceIntegrityStatus.NOT_APPLICABLE,
    }
    fields.update(overrides)
    return OperationalGovernanceOverview.model_validate(fields)


# 1. overview valido sin activation.
def test_overview_valid_without_activation() -> None:
    overview = _minimal_overview()
    assert overview.status == OperationalGovernanceStatus.NOT_INITIALIZED
    assert overview.active_lane is None
    assert overview.generations == []


# 2. overview valido V1.
def test_overview_valid_v1() -> None:
    overview = _minimal_overview(
        activation_initialized=True,
        status=OperationalGovernanceStatus.HEALTHY_V1,
        active_lane=MaterializedActivationLane.V1,
        active_generation_id=f"generation-{HASH}",
        active_generation_kind=MaterializedGenerationKind.V1_BASELINE,
        pointer_version=1,
        fallback_generation_id=f"generation-{HASH}",
        latest_event_id=f"event-{HASH}",
        event_chain_status=GovernanceEventChainStatus.VALID,
        event_chain_length=1,
        generation_count=1,
        confirmed_event_count=1,
        active_manifest_integrity=GovernanceIntegrityStatus.VALID,
        generations=[
            GovernanceGenerationSummary(
                generation_id=f"generation-{HASH}",
                lane=MaterializedActivationLane.V1,
                kind=MaterializedGenerationKind.V1_BASELINE,
                reachability=GovernanceGenerationReachability.ACTIVE,
                manifest_integrity=GovernanceIntegrityStatus.VALID,
                manifest_hash=HASH,
                file_count=1,
                files=["candidates"],
            )
        ],
        events=[
            GovernanceEventSummary(
                event_id=f"event-{HASH}",
                sequence=1,
                action="INITIALIZE_V1",
                to_generation_id=f"generation-{HASH}",
                resulting_lane=MaterializedActivationLane.V1,
                confirmed=True,
            )
        ],
    )
    assert overview.active_lane == MaterializedActivationLane.V1
    assert overview.status == OperationalGovernanceStatus.HEALTHY_V1


# 3. overview valido unified.
def test_overview_valid_unified() -> None:
    overview = _minimal_overview(
        activation_initialized=True,
        status=OperationalGovernanceStatus.HEALTHY_UNIFIED,
        active_lane=MaterializedActivationLane.UNIFIED,
        active_generation_id=f"generation-{HASH}",
        active_generation_kind=MaterializedGenerationKind.UNIFIED_CANARY,
        pointer_version=2,
        fallback_generation_id=f"generation-{'b' * 64}",
        latest_event_id=f"event-{HASH}",
        event_chain_status=GovernanceEventChainStatus.VALID,
        event_chain_length=2,
        generation_count=1,
        confirmed_event_count=2,
        active_manifest_integrity=GovernanceIntegrityStatus.VALID,
        artifacts=[
            GovernanceArtifactSummary(
                logical_name="candidates",
                status=GovernanceArtifactStatus.AVAILABLE,
                resolved_lane=MaterializedActivationLane.UNIFIED,
                generation_id=f"generation-{HASH}",
                relative_path=f"activation/generations/generation-{HASH}/candidates.json",
                sha256=HASH,
                downloadable=True,
            )
        ],
        generations=[
            GovernanceGenerationSummary(
                generation_id=f"generation-{HASH}",
                lane=MaterializedActivationLane.UNIFIED,
                kind=MaterializedGenerationKind.UNIFIED_CANARY,
                reachability=GovernanceGenerationReachability.ACTIVE,
                manifest_integrity=GovernanceIntegrityStatus.VALID,
                manifest_hash=HASH,
                file_count=1,
                files=["candidates"],
            )
        ],
        events=[
            GovernanceEventSummary(
                event_id=f"event-{'c' * 64}",
                sequence=1,
                action="INITIALIZE_V1",
                to_generation_id=f"generation-{'b' * 64}",
                resulting_lane=MaterializedActivationLane.V1,
                confirmed=True,
            ),
            GovernanceEventSummary(
                event_id=f"event-{HASH}",
                sequence=2,
                action="ACTIVATE_UNIFIED_CANARY",
                from_generation_id=f"generation-{'b' * 64}",
                to_generation_id=f"generation-{HASH}",
                resulting_lane=MaterializedActivationLane.UNIFIED,
                previous_event_id=f"event-{'c' * 64}",
                confirmed=True,
            ),
        ],
    )
    assert overview.active_lane == MaterializedActivationLane.UNIFIED
    assert overview.artifacts[0].downloadable is True


# 4. arrays ordenados.
def test_arrays_must_be_ordered() -> None:
    gen_a = GovernanceGenerationSummary(
        generation_id=f"generation-{'a' * 64}",
        reachability=GovernanceGenerationReachability.ORPHAN,
        manifest_integrity=GovernanceIntegrityStatus.MISSING,
        file_count=0,
    )
    gen_b = GovernanceGenerationSummary(
        generation_id=f"generation-{'b' * 64}",
        reachability=GovernanceGenerationReachability.ORPHAN,
        manifest_integrity=GovernanceIntegrityStatus.MISSING,
        file_count=0,
    )
    with pytest.raises(ValidationError):
        _minimal_overview(generations=[gen_b, gen_a], generation_count=2, orphan_generation_count=2)


# 5. summary reconciliado.
def test_summary_counts_must_reconcile() -> None:
    with pytest.raises(ValidationError):
        _minimal_overview(generation_count=1)  # generations=[] pero generation_count=1


# 6. sin timestamps.
def test_no_timestamp_fields_anywhere() -> None:
    overview = _minimal_overview()
    serialized = overview.to_stable_json()
    for forbidden in ("timestamp", "created_at", "updated_at", "evaluated_at", "started_at"):
        assert forbidden not in serialized


# 7. sin rutas absolutas.
def test_relative_path_rejects_absolute() -> None:
    with pytest.raises(ValidationError):
        GovernanceArtifactSummary(
            logical_name="candidates",
            status=GovernanceArtifactStatus.AVAILABLE,
            relative_path="/etc/passwd",
            sha256=HASH,
            downloadable=True,
        )


# 8. issues unicos.
def test_issues_must_be_unique() -> None:
    issue = GovernanceIssue(
        issue_id="issue::ORPHAN_EVENT::event-1",
        severity=GovernanceIssueSeverity.WARNING,
        code=GovernanceIssueCode.ORPHAN_EVENT,
        message="existe un evento persistido no alcanzable desde active.json.latest_event_id",
    )
    with pytest.raises(ValidationError):
        _minimal_overview(issues=[issue, issue])


def test_downloadable_requires_available_status() -> None:
    with pytest.raises(ValidationError):
        GovernanceArtifactSummary(
            logical_name="candidates",
            status=GovernanceArtifactStatus.MISSING,
            downloadable=True,
        )


def test_generation_summary_lane_kind_require_valid_integrity() -> None:
    with pytest.raises(ValidationError):
        GovernanceGenerationSummary(
            generation_id=f"generation-{HASH}",
            lane=MaterializedActivationLane.V1,
            kind=MaterializedGenerationKind.V1_BASELINE,
            reachability=GovernanceGenerationReachability.ORPHAN,
            manifest_integrity=GovernanceIntegrityStatus.MISSING,
            file_count=0,
        )


def test_activation_initialized_false_requires_not_initialized_status() -> None:
    with pytest.raises(ValidationError):
        _minimal_overview(
            activation_initialized=False, status=OperationalGovernanceStatus.HEALTHY_V1
        )
