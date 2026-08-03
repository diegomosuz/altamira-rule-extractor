"""No-regresion de Fase 9 (`feat/unified-candidate-promotion-
assessment`) sobre CINCO paquetes reales: un paquete de una sola regla
(`PAQUETE_SINTETICO_CONSULTA_SALDOS_3_REGLAS.zip`, equivalente al
"PROGRULE1" del runbook), Catherine original, Catherine corregido, el
paquete sintetico interprocedural de Fase 8 y el paquete sintetico
nuevo de Fase 9. Confirma que `candidate-promotion-assessment`
(servicio, no CLI) agrega EXCLUSIVAMENTE `diagnostics/candidate-
promotion-assessment.json` al run_dir, sin alterar NINGUN otro archivo
preexistente (run.json, artifacts/01-10, ni ningun otro
diagnostics/*.json -- incluyendo semantic-coverage, semantic-effects,
semantic-propagation, v2-candidates-shadow, interprocedural-call-
linkage, interprocedural-propagation, interprocedural-rule-candidates-
shadow, previamente calculados), byte a byte (SHA-256); y que dos
ejecuciones consecutivas de Fase 9 producen bytes identicos. Mismo
patron que `test_interprocedural_rule_candidates_no_regression_
integration.py` (Fase 8).

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
from altamira_extractor.pipeline.interprocedural_call_linkage_service import (
    compute_interprocedural_call_linkage_artifact,
    write_interprocedural_call_linkage_artifact,
)
from altamira_extractor.pipeline.interprocedural_propagation_service import (
    compute_interprocedural_propagation_artifact,
    write_interprocedural_propagation_artifact,
)
from altamira_extractor.pipeline.interprocedural_rule_candidates_service import (
    compute_interprocedural_rule_candidates_artifact,
    write_interprocedural_rule_candidates_artifact,
)
from altamira_extractor.pipeline.runner import run_ingestion
from altamira_extractor.pipeline.semantic_coverage_service import (
    compute_semantic_coverage_report,
    write_semantic_coverage_report,
)
from altamira_extractor.pipeline.semantic_effects_service import (
    compute_semantic_effects_artifact,
    write_semantic_effects_artifact,
)
from altamira_extractor.pipeline.semantic_propagation_service import (
    compute_semantic_propagation_artifact,
    write_semantic_propagation_artifact,
)
from altamira_extractor.pipeline.v2_shadow_candidates_service import (
    compute_v2_shadow_candidates_artifact,
    write_v2_shadow_candidates_artifact,
)

from ..e2e_support import build_settings, install_dynamic_rule_draft_fake_client, require_jar
from .test_candidate_promotion_assessment_integration import (
    _build_synthetic_package as _build_fase9_package,
)
from .test_interprocedural_rule_candidates_integration import (
    _build_synthetic_package as _build_fase8_package,
)

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
PROGRULE1_ZIP = REPO_ROOT / "examples" / "PAQUETE_SINTETICO_CONSULTA_SALDOS_3_REGLAS.zip"
CATHERINE_ORIGINAL_ZIP = REPO_ROOT / "examples" / "PAQUETE_SINTETICO_CATHERINE.zip"
CATHERINE_CORRECTED_ZIP = (
    REPO_ROOT / "examples" / "PAQUETE_SINTETICO_CATHERINE_CORREGIDO_APP_ACTUAL.zip"
)


def _snapshot(directory: Path) -> dict[str, str]:
    return {
        p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def _populate_prior_diagnostics(run_dir: Path, run_id: str, *, candidates_detected: bool) -> None:
    """Precalcula TODOS los diagnosticos previos que Fase 9 nunca debe
    modificar -- solo los que exigen `CANDIDATES_DETECTED` (semantic-
    coverage, v2-candidates-shadow) se omiten cuando el entorno no
    alcanzo Neo4j real."""
    effects = compute_semantic_effects_artifact(run_dir, run_id)
    write_semantic_effects_artifact(run_dir, effects)

    propagation = compute_semantic_propagation_artifact(run_dir, run_id)
    write_semantic_propagation_artifact(run_dir, propagation)

    ip_propagation = compute_interprocedural_propagation_artifact(run_dir, run_id)
    write_interprocedural_propagation_artifact(run_dir, ip_propagation)

    ip_linkage = compute_interprocedural_call_linkage_artifact(run_dir, run_id)
    write_interprocedural_call_linkage_artifact(run_dir, ip_linkage)

    ip_candidates = compute_interprocedural_rule_candidates_artifact(run_dir, run_id)
    write_interprocedural_rule_candidates_artifact(run_dir, ip_candidates)

    if candidates_detected:
        coverage = compute_semantic_coverage_report(run_dir, run_id)
        write_semantic_coverage_report(run_dir, coverage)

        v2_candidates = compute_v2_shadow_candidates_artifact(run_dir, run_id)
        write_v2_shadow_candidates_artifact(run_dir, v2_candidates)


def _run_no_regression(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, zip_path: Path
) -> tuple[str, int]:
    """Retorna (current_stage, unified_reference_count del diagnostico
    Fase 9). Nunca exige COMPLETED: solo PARSED (unico requisito real
    del servicio Fase 9) mas la comparacion byte a byte antes/despues."""
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
    except Exception as exc:  # noqa: BLE001 -- limitacion preexistente del fake, ajena a Fase 9
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

    before = _snapshot(run_dir)
    artifact = compute_candidate_promotion_assessment_artifact(run_dir, run_id)
    write_candidate_promotion_assessment_artifact(run_dir, artifact)
    after_first = _snapshot(run_dir)

    new_paths = set(after_first) - set(before)
    removed_paths = set(before) - set(after_first)
    changed_paths = {p for p in (set(before) & set(after_first)) if before[p] != after_first[p]}

    assert new_paths == {"diagnostics/candidate-promotion-assessment.json"}, new_paths
    assert removed_paths == set(), removed_paths
    assert changed_paths == set(), changed_paths

    # Segunda ejecucion consecutiva: bytes identicos.
    artifact_again = compute_candidate_promotion_assessment_artifact(run_dir, run_id)
    write_candidate_promotion_assessment_artifact(run_dir, artifact_again)
    after_second = _snapshot(run_dir)
    assert after_first == after_second

    assert zip_path.read_bytes() == original_bytes, f"{zip_path} fue modificado in situ"

    return current_stage, artifact.summary.unified_reference_count


@pytest.mark.integration
def test_progrule1_equivalent_no_regression(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    final_stage, reference_count = _run_no_regression(monkeypatch, tmp_path, PROGRULE1_ZIP)
    print(f"PROGRULE1-equivalente: current_stage={final_stage} reference_count={reference_count}")


@pytest.mark.integration
def test_catherine_original_no_regression(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    final_stage, reference_count = _run_no_regression(
        monkeypatch, tmp_path, CATHERINE_ORIGINAL_ZIP
    )
    print(f"Catherine original: current_stage={final_stage} reference_count={reference_count}")


@pytest.mark.integration
def test_catherine_corrected_no_regression(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    final_stage, reference_count = _run_no_regression(
        monkeypatch, tmp_path, CATHERINE_CORRECTED_ZIP
    )
    print(f"Catherine corregido: current_stage={final_stage} reference_count={reference_count}")


@pytest.mark.integration
def test_fase8_interprocedural_package_no_regression(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    zip_path = tmp_path / "fase8-synthetic-no-regression.zip"
    _build_fase8_package(zip_path)
    final_stage, reference_count = _run_no_regression(monkeypatch, tmp_path, zip_path)
    print(
        f"Paquete interprocedural Fase 8: current_stage={final_stage} "
        f"reference_count={reference_count}"
    )


@pytest.mark.integration
def test_fase9_new_package_no_regression(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    zip_path = tmp_path / "fase9-synthetic-no-regression.zip"
    _build_fase9_package(zip_path)
    final_stage, reference_count = _run_no_regression(monkeypatch, tmp_path, zip_path)
    print(f"Paquete nuevo Fase 9: current_stage={final_stage} reference_count={reference_count}")
