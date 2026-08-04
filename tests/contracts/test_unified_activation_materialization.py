"""Tests de los contratos de generacion/puntero/evento/resolucion
(Fase 14B Parte 4/15 items 13-18, `feat/controlled-unified-materialization`)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts.unified_activation_config import UnifiedFallbackPolicy
from altamira_extractor.contracts.unified_activation_materialization import (
    ActivationResolutionStatus,
    ActivationTransitionAction,
    ActivationTransitionEvent,
    ActiveActivationPointer,
    ActiveArtifactResolution,
    MaterializedActivationLane,
    MaterializedFileReference,
    MaterializedGenerationKind,
    MaterializedGenerationManifest,
    compute_event_id,
    compute_generation_id,
)
from altamira_extractor.contracts.unified_materialization_authorization import (
    UnifiedMaterializationReasonCode,
)

HASH = "a" * 64
RUN_ID = "20260101T000000000000-aaaaaaaa"


def _v1_file() -> MaterializedFileReference:
    return MaterializedFileReference(
        logical_name="candidates",
        relative_path="artifacts/06-candidates.json",
        sha256=HASH,
        byte_size=10,
    )


def _v1_manifest(**overrides: object) -> MaterializedGenerationManifest:
    base: dict[str, object] = {
        "generation_id": "generation-v1abc",
        "run_id": RUN_ID,
        "lane": MaterializedActivationLane.V1,
        "kind": MaterializedGenerationKind.V1_BASELINE,
        "source_package_hash": HASH,
        "activation_evaluation_hash": HASH,
        "authorization_hash": HASH,
        "candidate_v1_artifact_hash": HASH,
        "files": [_v1_file()],
    }
    base.update(overrides)
    return MaterializedGenerationManifest(**base)  # type: ignore[arg-type]


def _unified_file(
    generation_id: str, logical_name: str = "candidates"
) -> MaterializedFileReference:
    return MaterializedFileReference(
        logical_name=logical_name,
        relative_path=f"activation/generations/{generation_id}/{logical_name}.json",
        sha256=HASH,
        byte_size=10,
    )


def _unified_manifest(**overrides: object) -> MaterializedGenerationManifest:
    generation_id = "generation-unifiedabc"
    base: dict[str, object] = {
        "generation_id": generation_id,
        "run_id": RUN_ID,
        "lane": MaterializedActivationLane.UNIFIED,
        "kind": MaterializedGenerationKind.UNIFIED_CANARY,
        "source_package_hash": HASH,
        "activation_evaluation_hash": HASH,
        "authorization_hash": HASH,
        "candidate_v1_artifact_hash": HASH,
        "approved_group_ids": ["group::a"],
        "files": [_unified_file(generation_id)],
        "fallback_generation_id": "generation-v1abc",
    }
    base.update(overrides)
    return MaterializedGenerationManifest(**base)  # type: ignore[arg-type]


# 13. manifest V1 valido.
def test_v1_manifest_valid() -> None:
    manifest = _v1_manifest()
    assert manifest.lane == MaterializedActivationLane.V1
    assert manifest.status.value == "COMPLETE"


# 14. manifest unified valido.
def test_unified_manifest_valid() -> None:
    manifest = _unified_manifest()
    assert manifest.lane == MaterializedActivationLane.UNIFIED
    assert manifest.kind == MaterializedGenerationKind.UNIFIED_CANARY


class TestLaneKindConsistency:
    def test_v1_lane_requires_v1_baseline_kind(self) -> None:
        with pytest.raises(ValidationError):
            _v1_manifest(kind=MaterializedGenerationKind.UNIFIED_CANARY)

    def test_unified_lane_rejects_v1_baseline_kind(self) -> None:
        with pytest.raises(ValidationError):
            _unified_manifest(kind=MaterializedGenerationKind.V1_BASELINE)

    def test_v1_baseline_never_declares_approved_groups(self) -> None:
        with pytest.raises(ValidationError):
            _v1_manifest(approved_group_ids=["group::a"])

    def test_v1_files_must_reference_artifacts_prefix(self) -> None:
        with pytest.raises(ValidationError):
            _v1_manifest(
                files=[
                    MaterializedFileReference(
                        logical_name="candidates",
                        relative_path="activation/generations/x/candidates.json",
                        sha256=HASH,
                        byte_size=1,
                    )
                ]
            )

    def test_unified_files_must_reference_own_generation_folder(self) -> None:
        with pytest.raises(ValidationError):
            _unified_manifest(
                files=[
                    MaterializedFileReference(
                        logical_name="candidates",
                        relative_path="artifacts/06-candidates.json",
                        sha256=HASH,
                        byte_size=1,
                    )
                ]
            )

    def test_fallback_generation_id_cannot_be_self(self) -> None:
        with pytest.raises(ValidationError):
            _unified_manifest(fallback_generation_id="generation-unifiedabc")


# 15. generation_id determinístico.
class TestGenerationIdDeterminism:
    def test_same_inputs_same_id(self) -> None:
        id1 = compute_generation_id(
            lane=MaterializedActivationLane.V1,
            kind=MaterializedGenerationKind.V1_BASELINE,
            run_id=RUN_ID,
            file_hashes={"candidates": HASH},
        )
        id2 = compute_generation_id(
            lane=MaterializedActivationLane.V1,
            kind=MaterializedGenerationKind.V1_BASELINE,
            run_id=RUN_ID,
            file_hashes={"candidates": HASH},
        )
        assert id1 == id2

    def test_different_content_different_id(self) -> None:
        id1 = compute_generation_id(
            lane=MaterializedActivationLane.V1,
            kind=MaterializedGenerationKind.V1_BASELINE,
            run_id=RUN_ID,
            file_hashes={"candidates": HASH},
        )
        id2 = compute_generation_id(
            lane=MaterializedActivationLane.V1,
            kind=MaterializedGenerationKind.V1_BASELINE,
            run_id=RUN_ID,
            file_hashes={"candidates": "b" * 64},
        )
        assert id1 != id2

    def test_dict_order_never_affects_id(self) -> None:
        id1 = compute_generation_id(
            lane=MaterializedActivationLane.UNIFIED,
            kind=MaterializedGenerationKind.UNIFIED_CANARY,
            run_id=RUN_ID,
            file_hashes={"candidates": HASH, "guardrails": "b" * 64},
        )
        id2 = compute_generation_id(
            lane=MaterializedActivationLane.UNIFIED,
            kind=MaterializedGenerationKind.UNIFIED_CANARY,
            run_id=RUN_ID,
            file_hashes={"guardrails": "b" * 64, "candidates": HASH},
        )
        assert id1 == id2

    # Auditoria de cierre Fase 14B, seccion 2, caso E: mismo contenido +
    # distinto run_id -> generation_id diferente. `generation_id` es
    # "run-scoped content-addressed": identifica contenido DENTRO de un
    # run, nunca un identificador global comparable entre runs distintos
    # -- run_id participa deliberadamente de la identidad.
    def test_different_run_id_different_id(self) -> None:
        id1 = compute_generation_id(
            lane=MaterializedActivationLane.V1,
            kind=MaterializedGenerationKind.V1_BASELINE,
            run_id=RUN_ID,
            file_hashes={"candidates": HASH},
        )
        id2 = compute_generation_id(
            lane=MaterializedActivationLane.V1,
            kind=MaterializedGenerationKind.V1_BASELINE,
            run_id="20260101T000000000000-bbbbbbbb",
            file_hashes={"candidates": HASH},
        )
        assert id1 != id2

    def test_id_is_filesystem_safe(self) -> None:
        generation_id = compute_generation_id(
            lane=MaterializedActivationLane.V1,
            kind=MaterializedGenerationKind.V1_BASELINE,
            run_id=RUN_ID,
            file_hashes={"candidates": HASH},
        )
        assert ":" not in generation_id
        assert "/" not in generation_id
        assert "\\" not in generation_id

    def test_event_id_deterministic_and_state_dependent(self) -> None:
        kwargs: dict[str, object] = {
            "run_id": RUN_ID,
            "sequence": 1,
            "action": ActivationTransitionAction.INITIALIZE_V1,
            "from_generation_id": None,
            "to_generation_id": "generation-v1abc",
            "previous_event_id": None,
            "authorization_hash": HASH,
        }
        id1 = compute_event_id(**kwargs)  # type: ignore[arg-type]
        id2 = compute_event_id(**kwargs)  # type: ignore[arg-type]
        assert id1 == id2
        id3 = compute_event_id(**{**kwargs, "sequence": 2})  # type: ignore[arg-type]
        assert id1 != id3


# 16. archivos ordenados.
class TestFilesOrdering:
    def test_duplicate_logical_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _v1_manifest(files=[_v1_file(), _v1_file()])

    def test_unordered_files_rejected(self) -> None:
        generation_id = "generation-unifiedabc"
        with pytest.raises(ValidationError):
            _unified_manifest(
                files=[
                    _unified_file(generation_id, "guardrails"),
                    _unified_file(generation_id, "candidates"),
                ]
            )


# 17/18. hashes/bytes reconciliados -- a nivel de contrato, el
# `MaterializedFileReference` en si mismo no reconcilia contra el
# filesystem (eso es responsabilidad del store, Parte 8/15 items
# 17-18 alli); aqui se confirma que el campo exige formato valido.
class TestFileReferenceFormat:
    def test_sha256_must_be_valid_hex(self) -> None:
        with pytest.raises(ValidationError):
            MaterializedFileReference(
                logical_name="candidates",
                relative_path="artifacts/06-candidates.json",
                sha256="not-a-hash",
                byte_size=1,
            )

    def test_byte_size_cannot_be_negative(self) -> None:
        with pytest.raises(ValidationError):
            MaterializedFileReference(
                logical_name="candidates",
                relative_path="artifacts/06-candidates.json",
                sha256=HASH,
                byte_size=-1,
            )

    def test_path_traversal_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MaterializedFileReference(
                logical_name="candidates",
                relative_path="../../etc/passwd",
                sha256=HASH,
                byte_size=1,
            )

    def test_absolute_path_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MaterializedFileReference(
                logical_name="candidates",
                relative_path="/etc/passwd",
                sha256=HASH,
                byte_size=1,
            )

    def test_windows_drive_path_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MaterializedFileReference(
                logical_name="candidates",
                relative_path="C:/Windows/system32",
                sha256=HASH,
                byte_size=1,
            )

    def test_unc_path_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MaterializedFileReference(
                logical_name="candidates",
                relative_path="//server/share/file.json",
                sha256=HASH,
                byte_size=1,
            )


class TestActiveActivationPointerInvariants:
    def _pointer(self, **overrides: object) -> ActiveActivationPointer:
        base: dict[str, object] = {
            "run_id": RUN_ID,
            "pointer_version": 1,
            "active_generation_id": "generation-v1abc",
            "active_lane": MaterializedActivationLane.V1,
            "active_generation_manifest_hash": HASH,
            "fallback_generation_id": "generation-v1abc",
            "latest_event_id": "event-abc",
            "fallback_policy": UnifiedFallbackPolicy.NO_FALLBACK,
        }
        base.update(overrides)
        return ActiveActivationPointer(**base)  # type: ignore[arg-type]

    def test_previous_cannot_equal_active(self) -> None:
        with pytest.raises(ValidationError):
            self._pointer(previous_generation_id="generation-v1abc")

    def test_pointer_version_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            self._pointer(pointer_version=0)


class TestActivationTransitionEventInvariants:
    def _event(self, **overrides: object) -> ActivationTransitionEvent:
        base: dict[str, object] = {
            "event_id": "event-abc",
            "run_id": RUN_ID,
            "sequence": 1,
            "action": ActivationTransitionAction.INITIALIZE_V1,
            "to_generation_id": "generation-v1abc",
            "activation_evaluation_hash": HASH,
            "authorization_hash": HASH,
            "reason_code": UnifiedMaterializationReasonCode.KEEP_BASELINE,
            "resulting_lane": MaterializedActivationLane.V1,
        }
        base.update(overrides)
        return ActivationTransitionEvent(**base)  # type: ignore[arg-type]

    def test_sequence_one_forbids_previous_event(self) -> None:
        with pytest.raises(ValidationError):
            self._event(previous_event_id="event-zzz")

    def test_sequence_above_one_requires_previous_event(self) -> None:
        with pytest.raises(ValidationError):
            self._event(sequence=2)

    def test_initialize_v1_forbids_from_generation(self) -> None:
        with pytest.raises(ValidationError):
            self._event(from_generation_id="generation-other")

    def test_non_initialize_requires_from_generation(self) -> None:
        with pytest.raises(ValidationError):
            self._event(
                action=ActivationTransitionAction.FALLBACK_TO_V1,
                sequence=2,
                previous_event_id="event-zzz",
            )


class TestActiveArtifactResolutionInvariants:
    def _resolution(self, **overrides: object) -> ActiveArtifactResolution:
        base: dict[str, object] = {
            "run_id": RUN_ID,
            "status": ActivationResolutionStatus.RESOLVED,
            "requested_logical_name": "candidates",
            "resolved_lane": MaterializedActivationLane.V1,
            "generation_id": "generation-v1abc",
            "relative_path": "artifacts/06-candidates.json",
            "sha256": HASH,
            "fallback_applied": False,
        }
        base.update(overrides)
        return ActiveArtifactResolution(**base)  # type: ignore[arg-type]

    def test_not_available_forbids_path_and_hash(self) -> None:
        with pytest.raises(ValidationError):
            self._resolution(status=ActivationResolutionStatus.NOT_AVAILABLE_IN_LANE)

    def test_not_available_with_none_path_and_hash_accepted(self) -> None:
        resolution = self._resolution(
            status=ActivationResolutionStatus.NOT_AVAILABLE_IN_LANE,
            relative_path=None,
            sha256=None,
        )
        assert resolution.relative_path is None

    def test_resolved_requires_path_and_hash(self) -> None:
        with pytest.raises(ValidationError):
            self._resolution(relative_path=None, sha256=None)

    def test_fallback_applied_status_requires_v1_lane(self) -> None:
        with pytest.raises(ValidationError):
            self._resolution(
                status=ActivationResolutionStatus.FALLBACK_APPLIED,
                fallback_applied=True,
                resolved_lane=MaterializedActivationLane.UNIFIED,
            )

    def test_fallback_event_id_requires_fallback_applied(self) -> None:
        with pytest.raises(ValidationError):
            self._resolution(fallback_event_id="event-abc", fallback_applied=False)
