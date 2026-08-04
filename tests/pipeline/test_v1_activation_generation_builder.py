"""Tests del constructor de generaciones V1 (Fase 14B Parte 6/15,
`feat/controlled-unified-materialization`)."""

from __future__ import annotations

from pathlib import Path

import pytest

from altamira_extractor.pipeline.errors import UnifiedMaterializationError
from altamira_extractor.pipeline.v1_activation_generation_builder import (
    build_v1_generation_manifest,
)

HASH = "a" * 64
RUN_ID = "20260101T000000000000-aaaaaaaa"


def _write_candidates(run_dir: Path, content: bytes = b'{"x":1}') -> None:
    (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (run_dir / "artifacts" / "06-candidates.json").write_bytes(content)


def test_v1_manifest_built_from_real_candidates_file(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_candidates(run_dir)
    manifest = build_v1_generation_manifest(
        run_dir,
        run_id=RUN_ID,
        source_package_hash=HASH,
        activation_evaluation_hash=HASH,
        authorization_hash=HASH,
    )
    assert manifest.lane.value == "V1"
    assert manifest.kind.value == "V1_BASELINE"
    assert len(manifest.files) == 1
    assert manifest.files[0].logical_name == "candidates"
    assert manifest.files[0].relative_path == "artifacts/06-candidates.json"


def test_missing_candidates_raises(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    (run_dir / "artifacts").mkdir(parents=True)
    with pytest.raises(UnifiedMaterializationError):
        build_v1_generation_manifest(
            run_dir,
            run_id=RUN_ID,
            source_package_hash=HASH,
            activation_evaluation_hash=HASH,
            authorization_hash=HASH,
        )


# 19. V1 no copiado -- el manifest referencia el archivo por ruta
# relativa (artifacts/...), nunca crea una copia bajo activation/.
def test_v1_never_copies_files(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_candidates(run_dir)
    build_v1_generation_manifest(
        run_dir,
        run_id=RUN_ID,
        source_package_hash=HASH,
        activation_evaluation_hash=HASH,
        authorization_hash=HASH,
    )
    assert not (run_dir / "activation").exists()


def test_optional_surfaces_included_only_when_present(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_candidates(run_dir)
    (run_dir / "artifacts" / "09-guardrails").mkdir(parents=True)
    (run_dir / "artifacts" / "09-guardrails" / "guardrail-manifest.json").write_bytes(b"{}")

    manifest = build_v1_generation_manifest(
        run_dir,
        run_id=RUN_ID,
        source_package_hash=HASH,
        activation_evaluation_hash=HASH,
        authorization_hash=HASH,
    )
    names = {f.logical_name for f in manifest.files}
    assert names == {"candidates", "guardrails"}
    assert "context-packages" not in names
    assert "rule-drafts" not in names


def test_generation_id_deterministic_for_same_content(tmp_path: Path) -> None:
    run_dir_a = tmp_path / "a"
    run_dir_b = tmp_path / "b"
    _write_candidates(run_dir_a, b'{"x":1}')
    _write_candidates(run_dir_b, b'{"x":1}')
    manifest_a = build_v1_generation_manifest(
        run_dir_a,
        run_id=RUN_ID,
        source_package_hash=HASH,
        activation_evaluation_hash=HASH,
        authorization_hash=HASH,
    )
    manifest_b = build_v1_generation_manifest(
        run_dir_b,
        run_id=RUN_ID,
        source_package_hash=HASH,
        activation_evaluation_hash=HASH,
        authorization_hash=HASH,
    )
    assert manifest_a.generation_id == manifest_b.generation_id


def test_generation_id_differs_for_different_content(tmp_path: Path) -> None:
    run_dir_a = tmp_path / "a"
    run_dir_b = tmp_path / "b"
    _write_candidates(run_dir_a, b'{"x":1}')
    _write_candidates(run_dir_b, b'{"x":2}')
    manifest_a = build_v1_generation_manifest(
        run_dir_a,
        run_id=RUN_ID,
        source_package_hash=HASH,
        activation_evaluation_hash=HASH,
        authorization_hash=HASH,
    )
    manifest_b = build_v1_generation_manifest(
        run_dir_b,
        run_id=RUN_ID,
        source_package_hash=HASH,
        activation_evaluation_hash=HASH,
        authorization_hash=HASH,
    )
    assert manifest_a.generation_id != manifest_b.generation_id


# Auditoria de cierre Fase 14B, seccion 2, caso C: mismo contenido +
# distinto authorization_hash -> mismo generation_id (metadato de
# procedencia, nunca identidad de contenido).
def test_generation_id_independent_of_authorization_hash(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_candidates(run_dir, b'{"x":1}')
    manifest_1 = build_v1_generation_manifest(
        run_dir,
        run_id=RUN_ID,
        source_package_hash=HASH,
        activation_evaluation_hash=HASH,
        authorization_hash="1" * 64,
    )
    manifest_2 = build_v1_generation_manifest(
        run_dir,
        run_id=RUN_ID,
        source_package_hash=HASH,
        activation_evaluation_hash=HASH,
        authorization_hash="2" * 64,
    )
    assert manifest_1.generation_id == manifest_2.generation_id
    assert manifest_1.authorization_hash != manifest_2.authorization_hash


# Auditoria de cierre Fase 14B, seccion 2, caso D: mismo contenido +
# distinto activation_evaluation_hash -> mismo generation_id.
def test_generation_id_independent_of_activation_evaluation_hash(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_candidates(run_dir, b'{"x":1}')
    manifest_1 = build_v1_generation_manifest(
        run_dir,
        run_id=RUN_ID,
        source_package_hash=HASH,
        activation_evaluation_hash="3" * 64,
        authorization_hash=HASH,
    )
    manifest_2 = build_v1_generation_manifest(
        run_dir,
        run_id=RUN_ID,
        source_package_hash=HASH,
        activation_evaluation_hash="4" * 64,
        authorization_hash=HASH,
    )
    assert manifest_1.generation_id == manifest_2.generation_id
    assert manifest_1.activation_evaluation_hash != manifest_2.activation_evaluation_hash


def test_never_reads_symlinked_candidates(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    (run_dir / "artifacts").mkdir(parents=True)
    real_file = tmp_path / "real-candidates.json"
    real_file.write_bytes(b'{"x":1}')
    symlink_path = run_dir / "artifacts" / "06-candidates.json"
    try:
        symlink_path.symlink_to(real_file)
    except OSError:
        pytest.skip("symlinks no soportados en este entorno")
    with pytest.raises(UnifiedMaterializationError):
        build_v1_generation_manifest(
            run_dir,
            run_id=RUN_ID,
            source_package_hash=HASH,
            activation_evaluation_hash=HASH,
            authorization_hash=HASH,
        )


# 29. sin timestamps.
def test_manifest_has_no_timestamp_fields(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_candidates(run_dir)
    manifest = build_v1_generation_manifest(
        run_dir,
        run_id=RUN_ID,
        source_package_hash=HASH,
        activation_evaluation_hash=HASH,
        authorization_hash=HASH,
    )
    serialized = manifest.to_stable_json()
    for forbidden in ("timestamp", "created_at", "updated_at", "evaluated_at", "generated_at"):
        assert forbidden not in serialized
