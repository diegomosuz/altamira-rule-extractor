"""No-regresion de Fase 14A (`feat/controlled-unified-activation`)
sobre SEIS paquetes reales: CONSULTA_SALDOS (equivalente al
"PROGRULE1" del runbook), Catherine original, Catherine corregido,
CLIENTES_EMPRESAS multiprograma, PRESTAMOS_EMPRESAS y el escenario real
CALLER10/CALLEE10 de Fase 9-14A (`ready_blocked_zip`, mismo fixture de
`test_unified_activation_evaluate_integration.py`, Parte 12). Confirma
que `unified-activation-evaluate` (servicio, no CLI) agrega
EXCLUSIVAMENTE `diagnostics/unified-activation-evaluation.json` al
run_dir, sin alterar NINGUN otro archivo preexistente (run.json,
artifacts/01-10, ni ningun otro diagnostics/*.json), byte a byte
(SHA-256); que el modo V1_ONLY siempre es evaluable con solo PARSED
(SUCCEEDED), sin exigir artefactos unified; que un modo unified
(SHADOW_COMPARE) SIN prerequisitos (sin unified-candidates-shadow.json/
unified-shadow-validation-report.json/unified-shadow-downstream.json
en disco) queda `NOT_EVALUATED` de forma controlada -- nunca una
excepcion; y que dos ejecuciones consecutivas con la MISMA
configuracion producen bytes identicos. Mismo patron que
`test_candidate_promotion_assessment_no_regression_integration.py`
(Fase 9).

No corre en la suite por defecto (marcado `integration`, requiere JAR
real)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import altamira_extractor.pipeline.guardrails_applied_stage as guardrails_stage_module
import altamira_extractor.pipeline.rule_drafts_generated_stage as rule_drafts_stage_module
from altamira_extractor.pipeline.runner import run_ingestion
from altamira_extractor.pipeline.unified_activation_service import (
    compute_unified_activation_evaluation,
    write_unified_activation_evaluation,
)

from ..e2e_support import build_settings, install_dynamic_rule_draft_fake_client, require_jar
from .test_candidate_promotion_assessment_integration import ready_blocked_zip
from .test_candidate_promotion_assessment_no_regression_integration import (
    CATHERINE_CORRECTED_ZIP,
    CATHERINE_ORIGINAL_ZIP,
    PROGRULE1_ZIP,
    REPO_ROOT,
)

pytestmark = pytest.mark.integration

# Reexportado para que pytest descubra el fixture importado (mismo
# patron que `test_unified_activation_evaluate_integration.py`).
__all__ = ["ready_blocked_zip"]

CLIENTES_EMPRESAS_ZIP = (
    REPO_ROOT / "examples" / "PAQUETE_SINTETICO_CLIENTES_EMPRESAS_MULTIPROGRAMA_15_REGLAS.zip"
)
PRESTAMOS_EMPRESAS_ZIP = (
    REPO_ROOT / "examples" / "PAQUETE_SINTETICO_PRESTAMOS_EMPRESAS_5_REGLAS.zip"
)


def _snapshot(directory: Path) -> dict[str, str]:
    return {
        p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def _write_config(path: Path, *, mode: str) -> None:
    path.write_text(f"mode: {mode}\n", encoding="utf-8")


def _run_no_regression(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, zip_path: Path) -> str:
    """Retorna `current_stage` real del run. Nunca exige COMPLETED:
    solo PARSED (unico requisito real de `compute_unified_activation_
    evaluation` en modo V1_ONLY) mas la comparacion byte a byte antes/
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
    except Exception as exc:  # noqa: BLE001 -- localizar el run_dir tras un fallo aguas abajo
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

    run_id = run_dir.name

    config_v1_only = tmp_path / "config-v1-only.yaml"
    _write_config(config_v1_only, mode="V1_ONLY")
    config_shadow_compare = tmp_path / "config-shadow-compare.yaml"
    _write_config(config_shadow_compare, mode="SHADOW_COMPARE")

    before = _snapshot(run_dir)

    evaluation_v1_only = compute_unified_activation_evaluation(
        run_dir, run_id, config_path=config_v1_only
    )
    write_unified_activation_evaluation(run_dir, evaluation_v1_only)
    assert evaluation_v1_only.readiness_disposition.value in (
        "V1_ONLY_READY",
        "NOT_EVALUATED",
    )

    after_v1_only = _snapshot(run_dir)
    new_paths = set(after_v1_only) - set(before)
    removed_paths = set(before) - set(after_v1_only)
    changed_paths = {p for p in (set(before) & set(after_v1_only)) if before[p] != after_v1_only[p]}
    assert new_paths == {"diagnostics/unified-activation-evaluation.json"}, new_paths
    assert removed_paths == set(), removed_paths
    assert changed_paths == set(), changed_paths

    # Modo unified SIN prerequisitos (no se construyo ninguna cadena
    # Fase 11-13 en este run): debe quedar NOT_EVALUATED de forma
    # controlada, NUNCA una excepcion.
    evaluation_shadow = compute_unified_activation_evaluation(
        run_dir, run_id, config_path=config_shadow_compare
    )
    write_unified_activation_evaluation(run_dir, evaluation_shadow)
    assert evaluation_shadow.readiness_disposition.value == "NOT_EVALUATED"

    after_shadow = _snapshot(run_dir)
    changed_vs_v1_only = {
        p for p in (set(after_v1_only) & set(after_shadow)) if after_v1_only[p] != after_shadow[p]
    }
    assert changed_vs_v1_only <= {"diagnostics/unified-activation-evaluation.json"}
    assert set(after_shadow) - set(after_v1_only) == set()
    assert set(after_v1_only) - set(after_shadow) == set()

    # Determinismo byte a byte: recalcular V1_ONLY sobre el mismo run
    # produce bytes identicos.
    evaluation_v1_only_again = compute_unified_activation_evaluation(
        run_dir, run_id, config_path=config_v1_only
    )
    assert evaluation_v1_only.to_stable_json() == evaluation_v1_only_again.to_stable_json()

    assert zip_path.read_bytes() == original_bytes, f"{zip_path} fue modificado in situ"

    return current_stage


@pytest.mark.integration
def test_consulta_saldos_no_regression(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    final_stage = _run_no_regression(monkeypatch, tmp_path, PROGRULE1_ZIP)
    print(f"CONSULTA_SALDOS: current_stage={final_stage}")


@pytest.mark.integration
def test_catherine_original_no_regression(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    final_stage = _run_no_regression(monkeypatch, tmp_path, CATHERINE_ORIGINAL_ZIP)
    print(f"Catherine original: current_stage={final_stage}")


@pytest.mark.integration
def test_catherine_corrected_no_regression(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    final_stage = _run_no_regression(monkeypatch, tmp_path, CATHERINE_CORRECTED_ZIP)
    print(f"Catherine corregido: current_stage={final_stage}")


@pytest.mark.integration
def test_clientes_empresas_no_regression(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    final_stage = _run_no_regression(monkeypatch, tmp_path, CLIENTES_EMPRESAS_ZIP)
    print(f"CLIENTES_EMPRESAS: current_stage={final_stage}")


@pytest.mark.integration
def test_prestamos_empresas_no_regression(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    final_stage = _run_no_regression(monkeypatch, tmp_path, PRESTAMOS_EMPRESAS_ZIP)
    print(f"PRESTAMOS_EMPRESAS: current_stage={final_stage}")


@pytest.mark.integration
def test_fase9_14a_caller10_callee10_scenario_no_regression(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, ready_blocked_zip: Path
) -> None:
    final_stage = _run_no_regression(monkeypatch, tmp_path, ready_blocked_zip)
    print(f"Escenario Fase 9-14A (CALLER10/CALLEE10): current_stage={final_stage}")
