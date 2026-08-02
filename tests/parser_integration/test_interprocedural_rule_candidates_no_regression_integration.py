"""No-regresion de Fase 8 (`feat/interprocedural-rule-detectors-shadow`,
item 40 de los 40 tests obligatorios) sobre los paquetes reales
Catherine (original y corregido, `examples/PAQUETE_SINTETICO_CATHERINE*.
zip`, nunca modificados): confirma que `interprocedural-candidates-
shadow` (servicio, no CLI -- mismas funciones que invoca la CLI) agrega
EXCLUSIVAMENTE `diagnostics/interprocedural-rule-candidates-shadow.json`
al run_dir, sin alterar NINGUN otro archivo preexistente (run.json,
artifacts/01-10, ni ningun otro diagnostics/*.json), byte a byte
(SHA-256). Mismo patron que
`test_catherine_no_regression_integration.py` (Fase 7).

No corre en la suite por defecto (marcado `integration`, requiere JAR
real)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import altamira_extractor.pipeline.guardrails_applied_stage as guardrails_stage_module
import altamira_extractor.pipeline.rule_drafts_generated_stage as rule_drafts_stage_module
from altamira_extractor.pipeline.interprocedural_rule_candidates_service import (
    compute_interprocedural_rule_candidates_artifact,
    write_interprocedural_rule_candidates_artifact,
)
from altamira_extractor.pipeline.runner import run_ingestion

from ..e2e_support import build_settings, install_dynamic_rule_draft_fake_client, require_jar

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
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


def _run_no_regression(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, zip_path: Path
) -> tuple[str, int]:
    """Retorna (current_stage, candidate_count del diagnostico Fase 8).
    Nunca exige COMPLETED: solo PARSED (unico requisito real del
    servicio Fase 8) mas la comparacion byte a byte antes/despues. Ver
    docstring de `_run_no_regression` en
    `test_catherine_no_regression_integration.py` (Fase 7) para el
    razonamiento completo de la recuperacion tras una excepcion del
    fake oficial."""
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
    except Exception as exc:  # noqa: BLE001 -- limitacion preexistente del fake, ajena a Fase 8
        runs_after = {p.name for p in settings.runs_dir.iterdir() if p.is_dir()}
        new_run_ids = runs_after - runs_before
        assert len(new_run_ids) == 1, (
            f"no se pudo localizar un unico run_dir nuevo tras la excepcion "
            f"({exc!r}): {new_run_ids}"
        )
        run_dir = settings.runs_dir / next(iter(new_run_ids))
        run_json = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        current_stage = run_json["current_stage"]
        succeeded_from_disk = {s["stage"] for s in run_json["stages"] if s["status"] == "SUCCEEDED"}
        assert "PARSED" in succeeded_from_disk, (
            f"PARSED no tuvo exito segun run.json en disco tras la excepcion: {succeeded_from_disk}"
        )

    run_id = run_dir.name

    before = _snapshot(run_dir)
    artifact = compute_interprocedural_rule_candidates_artifact(run_dir, run_id)
    write_interprocedural_rule_candidates_artifact(run_dir, artifact)
    after = _snapshot(run_dir)

    new_paths = set(after) - set(before)
    removed_paths = set(before) - set(after)
    changed_paths = {p for p in (set(before) & set(after)) if before[p] != after[p]}

    assert new_paths == {"diagnostics/interprocedural-rule-candidates-shadow.json"}, new_paths
    assert removed_paths == set(), removed_paths
    assert changed_paths == set(), changed_paths

    assert zip_path.read_bytes() == original_bytes, f"{zip_path} fue modificado in situ"

    return current_stage, artifact.summary.candidate_count


@pytest.mark.integration
def test_catherine_original_no_regression(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    final_stage, candidate_count = _run_no_regression(monkeypatch, tmp_path, CATHERINE_ORIGINAL_ZIP)
    print(f"Catherine original: current_stage={final_stage} candidate_count={candidate_count}")


@pytest.mark.integration
def test_catherine_corrected_no_regression(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    final_stage, candidate_count = _run_no_regression(
        monkeypatch, tmp_path, CATHERINE_CORRECTED_ZIP
    )
    print(f"Catherine corregido: current_stage={final_stage} candidate_count={candidate_count}")
