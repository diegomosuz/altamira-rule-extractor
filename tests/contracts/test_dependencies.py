"""Tests del contrato de dependencias: DependencyEvidence, ParagraphDependency
y DependencyArtifact (03-dependencies.json)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts import (
    DependencyArtifact,
    DependencyEvidence,
    DependencyEvidenceRole,
    DependencyType,
    LocationKind,
    ParagraphDependency,
    StatementKind,
)

VALID_HASH = "a" * 64
FROM_ID = "program::AR::OP-TRF-PROPIA::PROG::1.0::abc123456789::paragraph::PARA-A"
TO_ID = "program::AR::OP-TRF-PROPIA::PROG::1.0::abc123456789::paragraph::PARA-B"


def _control_evidence(**overrides: object) -> DependencyEvidence:
    defaults: dict[str, object] = {
        "role": DependencyEvidenceRole.CONTROL,
        "statement_id": "PROG::PARA-A::0::PERFORM",
        "statement_kind": StatementKind.PERFORM,
        "source_text": "PERFORM PARA-B.",
        "source_file": "01-codigo/cobol/PROG.cbl",
        "line_start": 10,
        "line_end": 10,
        "location_kind": LocationKind.EXACT,
        "original_target": "PARA-B",
    }
    defaults.update(overrides)
    return DependencyEvidence(**defaults)  # type: ignore[arg-type]


def _writer_evidence(**overrides: object) -> DependencyEvidence:
    defaults: dict[str, object] = {
        "role": DependencyEvidenceRole.WRITER,
        "statement_id": "PROG::PARA-A::0::MOVE",
        "statement_kind": StatementKind.MOVE,
        "source_text": "MOVE 1 TO WS-FLAG.",
        "source_file": "01-codigo/cobol/PROG.cbl",
        "line_start": 5,
        "line_end": 5,
        "location_kind": LocationKind.EXACT,
        "original_variable": "WS-FLAG",
        "resolved_qualified_name": "WS-FLAG",
    }
    defaults.update(overrides)
    return DependencyEvidence(**defaults)  # type: ignore[arg-type]


def _reader_evidence(**overrides: object) -> DependencyEvidence:
    defaults: dict[str, object] = {
        "role": DependencyEvidenceRole.READER,
        "statement_id": "PROG::PARA-B::0::IF",
        "statement_kind": StatementKind.IF,
        "source_text": "IF WS-FLAG = 1",
        "source_file": "01-codigo/cobol/PROG.cbl",
        "line_start": 20,
        "line_end": 20,
        "location_kind": LocationKind.EXACT,
        "original_variable": "WS-FLAG",
        "resolved_qualified_name": "WS-FLAG",
    }
    defaults.update(overrides)
    return DependencyEvidence(**defaults)  # type: ignore[arg-type]


def _control_dependency(**overrides: object) -> ParagraphDependency:
    defaults: dict[str, object] = {
        "dependency_type": DependencyType.CONTROL_DEPENDS_ON,
        "from_paragraph_id": FROM_ID,
        "to_paragraph_id": TO_ID,
        "variables": [],
        "control_construct": "PERFORM",
        "dependency_depth": 1,
        "confidence": 1.0,
        "derivation_rule": "EXPLICIT_CONTROL_TARGET",
        "source_file": "01-codigo/cobol/PROG.cbl",
        "line_start": 10,
        "line_end": 10,
        "location_kind": LocationKind.EXACT,
        "source_package_hash": VALID_HASH,
        "evidence": [_control_evidence()],
    }
    defaults.update(overrides)
    return ParagraphDependency(**defaults)  # type: ignore[arg-type]


def _data_dependency(**overrides: object) -> ParagraphDependency:
    defaults: dict[str, object] = {
        "dependency_type": DependencyType.DATA_DEPENDS_ON,
        "from_paragraph_id": FROM_ID,
        "to_paragraph_id": TO_ID,
        "variables": ["WS-FLAG"],
        "control_construct": None,
        "dependency_depth": 1,
        "confidence": 0.8,
        "derivation_rule": "PARAGRAPH_WRITE_READ_QUALIFIED_MATCH",
        "source_file": "01-codigo/cobol/PROG.cbl",
        "line_start": 5,
        "line_end": 5,
        "location_kind": LocationKind.EXACT,
        "source_package_hash": VALID_HASH,
        "evidence": [_writer_evidence(), _reader_evidence()],
    }
    defaults.update(overrides)
    return ParagraphDependency(**defaults)  # type: ignore[arg-type]


# --- DependencyEvidence: coherencia de ubicacion ---


def test_evidence_exact_valid() -> None:
    evidence = _control_evidence()
    assert evidence.location_kind == LocationKind.EXACT


def test_evidence_exact_without_source_file_rejected() -> None:
    with pytest.raises(ValidationError):
        _control_evidence(source_file=None)


def test_evidence_preprocessed_stream_with_lines_and_no_file_valid() -> None:
    evidence = _control_evidence(
        source_file=None, location_kind=LocationKind.PREPROCESSED_STREAM
    )
    assert evidence.source_file is None
    assert evidence.line_start == 10


def test_evidence_preprocessed_stream_with_source_file_rejected() -> None:
    with pytest.raises(ValidationError):
        _control_evidence(location_kind=LocationKind.PREPROCESSED_STREAM)


def test_evidence_unknown_without_location_valid() -> None:
    evidence = _control_evidence(
        source_file=None, line_start=None, line_end=None, location_kind=LocationKind.UNKNOWN
    )
    assert evidence.location_kind == LocationKind.UNKNOWN


def test_evidence_unknown_with_line_rejected() -> None:
    with pytest.raises(ValidationError):
        _control_evidence(source_file=None, location_kind=LocationKind.UNKNOWN)


def test_evidence_line_end_before_line_start_rejected() -> None:
    with pytest.raises(ValidationError):
        _control_evidence(line_start=10, line_end=5)


# --- ParagraphDependency: coherencia de ubicacion propia ---


def test_dependency_exact_without_source_file_rejected() -> None:
    with pytest.raises(ValidationError):
        _control_dependency(source_file=None)


def test_dependency_preprocessed_stream_valid() -> None:
    dep = _control_dependency(source_file=None, location_kind=LocationKind.PREPROCESSED_STREAM)
    assert dep.source_file is None
    assert dep.line_start == 10


def test_dependency_unknown_valid() -> None:
    dep = _control_dependency(
        source_file=None, line_start=None, line_end=None, location_kind=LocationKind.UNKNOWN
    )
    assert dep.location_kind == LocationKind.UNKNOWN


# --- ParagraphDependency: roles de evidencia exigidos por tipo ---


def test_data_with_writer_and_reader_valid() -> None:
    dep = _data_dependency()
    roles = {item.role for item in dep.evidence}
    assert DependencyEvidenceRole.WRITER in roles
    assert DependencyEvidenceRole.READER in roles


def test_data_without_writer_rejected() -> None:
    with pytest.raises(ValidationError):
        _data_dependency(evidence=[_reader_evidence()])


def test_data_without_reader_rejected() -> None:
    with pytest.raises(ValidationError):
        _data_dependency(evidence=[_writer_evidence()])


def test_control_with_control_evidence_valid() -> None:
    dep = _control_dependency()
    assert dep.evidence[0].role == DependencyEvidenceRole.CONTROL


def test_control_without_control_evidence_rejected() -> None:
    with pytest.raises(ValidationError):
        _control_dependency(evidence=[])


def test_self_dependency_rejected() -> None:
    with pytest.raises(ValidationError):
        _control_dependency(to_paragraph_id=FROM_ID)


# --- round-trip y compatibilidad ---


def test_dependency_round_trips_stably() -> None:
    dep = _data_dependency()
    dumped = dep.to_stable_json()
    restored = ParagraphDependency.model_validate_json(dumped)
    assert restored == dep
    assert restored.to_stable_json() == dumped


def test_legacy_payload_without_location_kind_is_rejected() -> None:
    # ParagraphDependency nunca se persistio en produccion antes de esta
    # version del contrato (DEPENDENCIES_BUILT es la primera etapa que
    # escribe 03-dependencies.json): no existe un artefacto legado real
    # con el que mantener compatibilidad. location_kind es obligatorio
    # (sin default) segun lo pedido explicitamente; un payload previo sin
    # ese campo se rechaza en vez de asumir un valor.
    payload = _control_dependency().model_dump(mode="json")
    del payload["location_kind"]
    with pytest.raises(ValidationError):
        ParagraphDependency.model_validate(payload)


# --- DependencyArtifact ---


def test_dependency_artifact_valid_round_trips() -> None:
    artifact = DependencyArtifact(
        run_id="run-1",
        source_package_hash=VALID_HASH,
        dependencies=[_control_dependency(), _data_dependency()],
        warnings=["a warning", "b warning"],
    )
    dumped = artifact.to_stable_json()
    restored = DependencyArtifact.model_validate_json(dumped)
    assert restored == artifact


def test_dependency_artifact_rejects_mismatched_source_package_hash() -> None:
    with pytest.raises(ValidationError):
        DependencyArtifact(
            run_id="run-1",
            source_package_hash=VALID_HASH,
            dependencies=[_control_dependency(source_package_hash="b" * 64)],
        )


def test_dependency_artifact_rejects_semantic_duplicates() -> None:
    dep = _control_dependency()
    with pytest.raises(ValidationError):
        DependencyArtifact(
            run_id="run-1", source_package_hash=VALID_HASH, dependencies=[dep, dep]
        )


def test_dependency_artifact_allows_go_to_and_perform_to_same_target() -> None:
    go_to = _control_dependency(control_construct="GO_TO")
    perform = _control_dependency(control_construct="PERFORM")
    artifact = DependencyArtifact(
        run_id="run-1", source_package_hash=VALID_HASH, dependencies=[go_to, perform]
    )
    assert len(artifact.dependencies) == 2


def test_dependency_artifact_rejects_unsorted_dependencies() -> None:
    # from_paragraph_id "...PARA-C" ordena despues de FROM_ID; invertir el
    # orden esperado debe fallar la validacion de orden deterministico.
    first = _control_dependency()
    second = _control_dependency(
        from_paragraph_id=FROM_ID.replace("PARA-A", "PARA-C"),
        to_paragraph_id=TO_ID.replace("PARA-B", "PARA-D"),
    )
    with pytest.raises(ValidationError):
        DependencyArtifact(
            run_id="run-1", source_package_hash=VALID_HASH, dependencies=[second, first]
        )


def test_dependency_artifact_rejects_duplicate_warnings() -> None:
    with pytest.raises(ValidationError):
        DependencyArtifact(
            run_id="run-1",
            source_package_hash=VALID_HASH,
            warnings=["same", "same"],
        )


def test_dependency_artifact_rejects_unsorted_warnings() -> None:
    with pytest.raises(ValidationError):
        DependencyArtifact(
            run_id="run-1",
            source_package_hash=VALID_HASH,
            warnings=["zeta", "alpha"],
        )
