"""No-regresion de Fase 10 (`feat/controlled-candidate-promotion-
plan`) sobre CINCO paquetes reales: un paquete de una sola regla
(equivalente al "PROGRULE1" del runbook), Catherine original, Catherine
corregido, el paquete sintetico interprocedural de Fase 8 y el paquete
sintetico nuevo de Fase 10. Confirma que `candidate-promotion-review-
package`/`candidate-promotion-plan` (servicios, no CLI) agregan
EXCLUSIVAMENTE `diagnostics/candidate-promotion-review-package.json` y
`diagnostics/candidate-promotion-plan.json` al run_dir, sin alterar
NINGUN otro archivo preexistente (run.json, artifacts/01-10, ni ningun
otro diagnostics/*.json -- incluyendo `diagnostics/candidate-
promotion-assessment.json`, Fase 9), byte a byte (SHA-256); y que dos
ejecuciones consecutivas con el MISMO manifiesto producen bytes
identicos. Mismo patron que `test_candidate_promotion_assessment_no_
regression_integration.py` (Fase 9).

No corre en la suite por defecto (marcado `integration`, requiere JAR
real)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import altamira_extractor.pipeline.guardrails_applied_stage as guardrails_stage_module
import altamira_extractor.pipeline.rule_drafts_generated_stage as rule_drafts_stage_module
from altamira_extractor.contracts.enums import PipelineStage, StageStatus
from altamira_extractor.pipeline.candidate_promotion_assessment_service import (
    compute_candidate_promotion_assessment_artifact,
    write_candidate_promotion_assessment_artifact,
)
from altamira_extractor.pipeline.candidate_promotion_plan_service import (
    compute_candidate_promotion_plan_artifact,
    write_candidate_promotion_plan_artifact,
)
from altamira_extractor.pipeline.candidate_promotion_review_service import (
    compute_candidate_promotion_review_package,
    write_candidate_promotion_review_package,
)
from altamira_extractor.pipeline.runner import run_ingestion

from ..e2e_support import build_settings, install_dynamic_rule_draft_fake_client, require_jar
from .test_candidate_promotion_assessment_integration import (
    _build_synthetic_package as _build_fase9_package,
)
from .test_candidate_promotion_assessment_no_regression_integration import (
    CATHERINE_CORRECTED_ZIP,
    CATHERINE_ORIGINAL_ZIP,
    PROGRULE1_ZIP,
    _populate_prior_diagnostics,
)
from .test_candidate_promotion_review_and_plan_integration import (
    _build_synthetic_package as _build_fase10_package,
)
from .test_interprocedural_rule_candidates_integration import (
    _build_synthetic_package as _build_fase8_package,
)

pytestmark = pytest.mark.integration


def _snapshot(directory: Path) -> dict[str, str]:
    return {
        p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def _write_empty_decisions_manifest(
    tmp_path: Path, run_id: str, review_package_hash: str, assessment_artifact_hash: str
) -> Path:
    manifest_path = tmp_path / "decisions.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "review_package_hash": review_package_hash,
                "assessment_artifact_hash": assessment_artifact_hash,
                "run_id": run_id,
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )
    return manifest_path


def _run_no_regression(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, zip_path: Path
) -> tuple[str, int]:
    """Retorna (current_stage, total_items del paquete de revision de
    Fase 10). Nunca exige COMPLETED: solo PARSED (unico requisito real
    de los servicios de Fase 10) mas la comparacion byte a byte antes/
    despues."""
    require_jar()
    original_bytes = zip_path.read_bytes()

    install_dynamic_rule_draft_fake_client(monkeypatch, rule_drafts_stage_module)
    install_dynamic_rule_draft_fake_client(monkeypatch, guardrails_stage_module)

    settings = build_settings(tmp_path)
    settings.runs_dir.mkdir(parents=True, exist_ok=True)
    runs_before = {p.name for p in settings.runs_dir.iterdir() if p.is_dir()}

    current_stage: str
    try:
        state = run_ingestion(zip_path, settings)
        run_dir = settings.runs_dir / state.run_id
        current_stage = state.current_stage.value
        succeeded = {s.stage for s in state.stages if s.status == StageStatus.SUCCEEDED}
    except Exception as exc:  # noqa: BLE001 -- limitacion preexistente del fake, ajena a Fase 10
        runs_after = {p.name for p in settings.runs_dir.iterdir() if p.is_dir()}
        new_run_ids = runs_after - runs_before
        assert len(new_run_ids) == 1, (
            f"no se pudo localizar un unico run_dir nuevo tras la excepcion "
            f"({exc!r}): {new_run_ids}"
        )
        run_dir = settings.runs_dir / next(iter(new_run_ids))
        run_json = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        current_stage = run_json["current_stage"]
        succeeded_values = {s["stage"] for s in run_json["stages"] if s["status"] == "SUCCEEDED"}
        assert "PARSED" in succeeded_values, (
            f"PARSED no tuvo exito segun run.json en disco tras la excepcion: {succeeded_values}"
        )
        succeeded = {PipelineStage(v) for v in succeeded_values}

    run_id = run_dir.name
    candidates_detected = PipelineStage.CANDIDATES_DETECTED in succeeded

    _populate_prior_diagnostics(run_dir, run_id, candidates_detected=candidates_detected)

    assessment = compute_candidate_promotion_assessment_artifact(run_dir, run_id)
    write_candidate_promotion_assessment_artifact(run_dir, assessment)

    before = _snapshot(run_dir)

    package = compute_candidate_promotion_review_package(run_dir, run_id)
    write_candidate_promotion_review_package(run_dir, package)

    review_package_hash = hashlib.sha256(package.to_stable_json().encode("utf-8")).hexdigest()
    manifest_path = _write_empty_decisions_manifest(
        tmp_path, run_id, review_package_hash, package.assessment_artifact_hash
    )
    plan = compute_candidate_promotion_plan_artifact(
        run_dir, run_id, decisions_path=str(manifest_path)
    )
    write_candidate_promotion_plan_artifact(run_dir, plan)

    after_first = _snapshot(run_dir)

    new_paths = set(after_first) - set(before)
    removed_paths = set(before) - set(after_first)
    changed_paths = {p for p in (set(before) & set(after_first)) if before[p] != after_first[p]}

    assert new_paths == {
        "diagnostics/candidate-promotion-review-package.json",
        "diagnostics/candidate-promotion-plan.json",
    }, new_paths
    assert removed_paths == set(), removed_paths
    assert changed_paths == set(), changed_paths

    # Segunda ejecucion consecutiva con el MISMO manifiesto: bytes identicos.
    package_again = compute_candidate_promotion_review_package(run_dir, run_id)
    write_candidate_promotion_review_package(run_dir, package_again)
    plan_again = compute_candidate_promotion_plan_artifact(
        run_dir, run_id, decisions_path=str(manifest_path)
    )
    write_candidate_promotion_plan_artifact(run_dir, plan_again)
    after_second = _snapshot(run_dir)
    assert after_first == after_second

    assert zip_path.read_bytes() == original_bytes, f"{zip_path} fue modificado in situ"

    return current_stage, package.summary.total_items


@pytest.mark.integration
def test_progrule1_equivalent_no_regression(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    final_stage, total_items = _run_no_regression(monkeypatch, tmp_path, PROGRULE1_ZIP)
    print(f"PROGRULE1-equivalente: current_stage={final_stage} total_items={total_items}")


@pytest.mark.integration
def test_catherine_original_no_regression(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    final_stage, total_items = _run_no_regression(monkeypatch, tmp_path, CATHERINE_ORIGINAL_ZIP)
    print(f"Catherine original: current_stage={final_stage} total_items={total_items}")


@pytest.mark.integration
def test_catherine_corrected_no_regression(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    final_stage, total_items = _run_no_regression(monkeypatch, tmp_path, CATHERINE_CORRECTED_ZIP)
    print(f"Catherine corregido: current_stage={final_stage} total_items={total_items}")


@pytest.mark.integration
def test_fase8_interprocedural_package_no_regression(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    zip_path = tmp_path / "fase8-synthetic-no-regression.zip"
    _build_fase8_package(zip_path)
    final_stage, total_items = _run_no_regression(monkeypatch, tmp_path, zip_path)
    print(f"Paquete interprocedural Fase 8: current_stage={final_stage} total_items={total_items}")


@pytest.mark.integration
def test_fase9_package_no_regression(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Item explicito de la Parte 12 del runbook ("paquete Fase 9")."""
    zip_path = tmp_path / "fase9-synthetic-no-regression.zip"
    _build_fase9_package(zip_path)
    final_stage, total_items = _run_no_regression(monkeypatch, tmp_path, zip_path)
    print(f"Paquete Fase 9: current_stage={final_stage} total_items={total_items}")


@pytest.mark.integration
def test_fase10_new_package_no_regression(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Cobertura adicional (no exigida explicitamente por la Parte 12,
    pero deseable): el propio paquete nuevo de Fase 10."""
    zip_path = tmp_path / "fase10-synthetic-no-regression.zip"
    _build_fase10_package(zip_path)
    final_stage, total_items = _run_no_regression(monkeypatch, tmp_path, zip_path)
    print(f"Paquete nuevo Fase 10: current_stage={final_stage} total_items={total_items}")
