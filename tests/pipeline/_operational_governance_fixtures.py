"""Fixtures sinteticas compartidas para los tests de Fase 15A
(`feat/operational-governance-ui`). NO es un archivo de tests (pytest
lo ignora, no empieza con `test_`).

Reutiliza EXACTAMENTE las fixtures de Fase 14B
(`_unified_materialization_fixtures.py`) y agrega UNICAMENTE lo que
Fase 14B nunca necesito: `run.json` (el reader de gobierno lo exige
para `run_stage`) y dos atajos de materializacion (`KEEP_V1`/
`ACTIVATE_UNIFIED_CANARY`) para no repetir el mismo YAML de
autorizacion en cada test."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from altamira_extractor.contracts.enums import PipelineStage, StageStatus
from altamira_extractor.contracts.run_state import RunState, StageExecution
from altamira_extractor.pipeline.unified_materialization_service import (
    MaterializationResult,
    materialize_unified_activation,
)

from ._unified_materialization_fixtures import (
    MaterializationFixture,
    build_materialization_fixture,
    evaluation_hash_of,
    write_authorization_yaml,
    write_run_dir,
)

__all__ = [
    "MaterializationFixture",
    "build_materialization_fixture",
    "evaluation_hash_of",
    "governance_run_dir",
    "materialize_keep_v1",
    "materialize_unified_canary",
    "write_authorization_yaml",
    "write_run_dir",
    "write_run_json",
]

_FIXED_TIMESTAMP = datetime(2026, 1, 1, tzinfo=UTC)


def write_run_json(
    run_dir: Path, run_id: str, *, stage: PipelineStage = PipelineStage.CANDIDATES_DETECTED
) -> None:
    state = RunState(
        run_id=run_id,
        package_filename="package.zip",
        current_stage=stage,
        stages=[
            StageExecution(
                stage=stage,
                status=StageStatus.SUCCEEDED,
                started_at=_FIXED_TIMESTAMP,
                finished_at=_FIXED_TIMESTAMP,
                duration_seconds=1.0,
            )
        ],
        created_at=_FIXED_TIMESTAMP,
        updated_at=_FIXED_TIMESTAMP,
    )
    (run_dir / "run.json").write_text(state.to_stable_json(), encoding="utf-8")


def governance_run_dir(tmp_path: Path, fx: MaterializationFixture) -> Path:
    """`write_run_dir` (Fase 14B) + `run.json` (exigido por el reader
    de gobierno de Fase 15A, que Fase 14B nunca necesito)."""
    run_dir = write_run_dir(tmp_path, fx)
    write_run_json(run_dir, fx.run_id)
    return run_dir


def materialize_keep_v1(
    run_dir: Path, fx: MaterializationFixture, tmp_path: Path
) -> MaterializationResult:
    auth_path = tmp_path / "keep-v1.yaml"
    write_authorization_yaml(
        auth_path,
        run_id=fx.run_id,
        activation_evaluation_hash=evaluation_hash_of(run_dir),
        action="KEEP_V1",
        expected_readiness_disposition="READY_FOR_UNIFIED_CANARY",
        reason_code="KEEP_BASELINE",
        fallback_authorized=False,
    )
    return materialize_unified_activation(run_dir, fx.run_id, authorization_path=auth_path)


def materialize_unified_canary(
    run_dir: Path, fx: MaterializationFixture, tmp_path: Path
) -> MaterializationResult:
    auth_path = tmp_path / "canary.yaml"
    write_authorization_yaml(
        auth_path,
        run_id=fx.run_id,
        activation_evaluation_hash=evaluation_hash_of(run_dir),
        action="ACTIVATE_UNIFIED_CANARY",
        expected_readiness_disposition="READY_FOR_UNIFIED_CANARY",
        approved_group_ids=fx.approved_group_ids,
    )
    return materialize_unified_activation(run_dir, fx.run_id, authorization_path=auth_path)
