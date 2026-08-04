"""No-regresion de Fase 14B (`feat/controlled-unified-materialization`)
sobre SEIS paquetes reales: CONSULTA_SALDOS, Catherine original,
Catherine corregido, CLIENTES_EMPRESAS multiprograma, PRESTAMOS_EMPRESAS
y el escenario real CALLER10/CALLEE10 de Fase 9-14A (`ready_blocked_zip`,
mismo fixture usado por Parte 18). Confirma que `materialize_unified_
activation` (servicio, no CLI) agrega EXCLUSIVAMENTE archivos bajo
`activation/` al run_dir, sin alterar NUNCA `run.json`, `artifacts/01-
10` ni ningun `diagnostics/*.json` preexistente, byte a byte (SHA-256);
que para runs sin estado unified-ready de Fase 14A, `KEEP_V1` inicializa
V1 correctamente y NUNCA materializa unified; que V1 siempre es
resoluble via `ActiveArtifactResolver`; y que dos aplicaciones
consecutivas de la MISMA autorizacion son idempotentes (no agregan
archivos nuevos ni modifican el puntero). El ciclo completo unified
canary + fallback + rollback ya se verifica con datos 100% reales de
CALLER10/CALLEE10 en `test_unified_activation_materialize_integration.py`
(Parte 18); este archivo cubre especificamente la propiedad de
no-regresion (aislamiento de escritura) sobre los SEIS paquetes. Mismo
patron que `test_unified_activation_evaluate_no_regression_
integration.py` (Fase 14A).

No corre en la suite por defecto (marcado `integration`, requiere JAR
real)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import altamira_extractor.pipeline.guardrails_applied_stage as guardrails_stage_module
import altamira_extractor.pipeline.rule_drafts_generated_stage as rule_drafts_stage_module
from altamira_extractor.pipeline.active_artifact_resolver import ActiveArtifactResolver
from altamira_extractor.pipeline.unified_activation_service import (
    compute_unified_activation_evaluation,
    write_unified_activation_evaluation,
)
from altamira_extractor.pipeline.unified_materialization_service import (
    materialize_unified_activation,
)

from ..e2e_support import build_settings, install_dynamic_rule_draft_fake_client, require_jar
from .test_candidate_promotion_assessment_integration import _run_pipeline, ready_blocked_zip
from .test_candidate_promotion_assessment_no_regression_integration import (
    CATHERINE_CORRECTED_ZIP,
    CATHERINE_ORIGINAL_ZIP,
    PROGRULE1_ZIP,
    REPO_ROOT,
)

pytestmark = pytest.mark.integration

__all__ = ["ready_blocked_zip"]

CLIENTES_EMPRESAS_ZIP = (
    REPO_ROOT / "examples" / "PAQUETE_SINTETICO_CLIENTES_EMPRESAS_MULTIPROGRAMA_15_REGLAS.zip"
)
PRESTAMOS_EMPRESAS_ZIP = (
    REPO_ROOT / "examples" / "PAQUETE_SINTETICO_PRESTAMOS_EMPRESAS_5_REGLAS.zip"
)

_ACTIVATION_PREFIX = "activation/"


def _snapshot(directory: Path) -> dict[str, str]:
    return {
        p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def _write_config(path: Path, *, mode: str) -> None:
    path.write_text(f"mode: {mode}\n", encoding="utf-8")


def _write_keep_v1_authorization(
    path: Path, *, run_id: str, eval_hash: str, readiness: str
) -> None:
    path.write_text(
        "\n".join(
            [
                'schema_version: "1.0"',
                f"run_id: {run_id!r}",
                f"activation_evaluation_hash: {eval_hash!r}",
                f"expected_readiness_disposition: {readiness}",
                "action: KEEP_V1",
                "reason_code: KEEP_BASELINE",
                "review_reference: 'fase-14b-no-regression'",
                "approved_group_ids: []",
                "fallback_authorized: false",
                "rollback_authorized: false",
                "provider_policy: DETERMINISTIC_FAKE_ONLY",
                "materialization_enabled: true",
                "diagnostics: []",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _assert_only_activation_changed(before: dict[str, str], after: dict[str, str]) -> None:
    new_paths = set(after) - set(before)
    removed_paths = set(before) - set(after)
    changed_paths = {p for p in (set(before) & set(after)) if before[p] != after[p]}
    assert removed_paths == set(), removed_paths
    assert changed_paths == set(), changed_paths
    assert all(p.startswith(_ACTIVATION_PREFIX) for p in new_paths), new_paths


def _run_no_regression(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, zip_path: Path) -> str:
    require_jar()
    original_bytes = zip_path.read_bytes()

    install_dynamic_rule_draft_fake_client(monkeypatch, rule_drafts_stage_module)
    install_dynamic_rule_draft_fake_client(monkeypatch, guardrails_stage_module)

    settings = build_settings(tmp_path)
    run_dir, run_id, succeeded_stages = _run_pipeline(settings, zip_path)
    assert "PARSED" in succeeded_stages
    if "CANDIDATES_DETECTED" not in succeeded_stages:
        pytest.skip(
            "CANDIDATES_DETECTED no se alcanzo en este entorno (Neo4j no disponible) -- "
            "no existe base V1 (artifacts/06-candidates.json) para materializar"
        )

    config_v1_only = tmp_path / "config-v1-only.yaml"
    _write_config(config_v1_only, mode="V1_ONLY")
    evaluation = compute_unified_activation_evaluation(run_dir, run_id, config_path=config_v1_only)
    write_unified_activation_evaluation(run_dir, evaluation)
    assert evaluation.readiness_disposition.value in ("V1_ONLY_READY", "NOT_EVALUATED")

    before = _snapshot(run_dir)

    keep_v1_auth = tmp_path / "keep-v1.yaml"
    eval_path = run_dir / "diagnostics" / "unified-activation-evaluation.json"
    eval_hash = hashlib.sha256(eval_path.read_bytes()).hexdigest()
    _write_keep_v1_authorization(
        keep_v1_auth,
        run_id=run_id,
        eval_hash=eval_hash,
        readiness=evaluation.readiness_disposition.value,
    )

    result_1 = materialize_unified_activation(run_dir, run_id, authorization_path=keep_v1_auth)
    assert result_1.active_lane.value == "V1"
    assert result_1.pointer_version == 1

    after_first = _snapshot(run_dir)
    _assert_only_activation_changed(before, after_first)

    resolver = ActiveArtifactResolver(run_dir, run_id=run_id)
    assert resolver.resolve("candidates").status.value == "RESOLVED"

    # Idempotencia: repetir la MISMA autorizacion no agrega archivos
    # nuevos ni cambia el puntero.
    result_2 = materialize_unified_activation(run_dir, run_id, authorization_path=keep_v1_auth)
    assert result_2.idempotent is True
    assert result_2.pointer_version == 1

    after_second = _snapshot(run_dir)
    assert after_second == after_first

    assert zip_path.read_bytes() == original_bytes, f"{zip_path} fue modificado in situ"

    return evaluation.readiness_disposition.value


@pytest.mark.integration
def test_consulta_saldos_no_regression(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    final_stage = _run_no_regression(monkeypatch, tmp_path, PROGRULE1_ZIP)
    print(f"CONSULTA_SALDOS: readiness={final_stage}")


@pytest.mark.integration
def test_catherine_original_no_regression(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    final_stage = _run_no_regression(monkeypatch, tmp_path, CATHERINE_ORIGINAL_ZIP)
    print(f"Catherine original: readiness={final_stage}")


@pytest.mark.integration
def test_catherine_corrected_no_regression(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    final_stage = _run_no_regression(monkeypatch, tmp_path, CATHERINE_CORRECTED_ZIP)
    print(f"Catherine corregido: readiness={final_stage}")


@pytest.mark.integration
def test_clientes_empresas_no_regression(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    final_stage = _run_no_regression(monkeypatch, tmp_path, CLIENTES_EMPRESAS_ZIP)
    print(f"CLIENTES_EMPRESAS: readiness={final_stage}")


@pytest.mark.integration
def test_prestamos_empresas_no_regression(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    final_stage = _run_no_regression(monkeypatch, tmp_path, PRESTAMOS_EMPRESAS_ZIP)
    print(f"PRESTAMOS_EMPRESAS: readiness={final_stage}")


@pytest.mark.integration
def test_fase9_14a_caller10_callee10_scenario_no_regression(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, ready_blocked_zip: Path
) -> None:
    final_stage = _run_no_regression(monkeypatch, tmp_path, ready_blocked_zip)
    print(f"Escenario Fase 9-14A (CALLER10/CALLEE10): readiness={final_stage}")
