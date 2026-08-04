"""No-regresion de Fase 15A (`feat/operational-governance-ui`) sobre
SEIS paquetes reales: CONSULTA_SALDOS, Catherine original, Catherine
corregido, CLIENTES_EMPRESAS multiprograma, PRESTAMOS_EMPRESAS y el
escenario real CALLER10/CALLEE10. Confirma que la capa de gobierno
operativo (reader/API/UI, exclusivamente GET/HEAD) nunca modifica
ningun archivo preexistente -- ni siquiera `activation/` una vez
inicializada -- y que los endpoints legacy (`/ui/runs/{run_id}`,
`/api/runs/{run_id}`) permanecen exactamente iguales.

Para runs SIN `activation/` inicializada: `governance` muestra
`NOT_INITIALIZED`, el detalle legacy del run sigue funcionando, y no
hay ninguna modificacion. Para runs CON V1 inicializado (`KEEP_V1`):
`governance` muestra `HEALTHY_V1`, el legacy sigue igual. Para
CALLER10/CALLEE10: peticiones GET/HEAD repetidas sobre gobierno nunca
crean un evento nuevo, nunca mueven `pointer_version`, nunca ejecutan
un fallback -- y las fixtures Catherine permanecen intactas durante
TODA la ejecucion de este archivo.

No corre en la suite por defecto (marcado `integration`, requiere JAR
real)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import altamira_extractor.pipeline.guardrails_applied_stage as guardrails_stage_module
import altamira_extractor.pipeline.rule_drafts_generated_stage as rule_drafts_stage_module
from altamira_extractor.api.app import create_app
from altamira_extractor.pipeline.operational_governance_reader import (
    build_operational_governance_overview,
)
from altamira_extractor.pipeline.unified_activation_service import (
    compute_unified_activation_evaluation,
    write_unified_activation_evaluation,
)
from altamira_extractor.pipeline.unified_activation_store import UnifiedActivationStore
from altamira_extractor.pipeline.unified_materialization_service import (
    materialize_unified_activation,
)

from ..e2e_support import build_settings, install_dynamic_rule_draft_fake_client, require_jar
from .test_candidate_promotion_assessment_integration import _run_pipeline, ready_blocked_zip
from .test_candidate_promotion_assessment_no_regression_integration import (
    CATHERINE_CORRECTED_ZIP,
    CATHERINE_ORIGINAL_ZIP,
    PROGRULE1_ZIP,
)
from .test_unified_activation_materialize_no_regression_integration import (
    CLIENTES_EMPRESAS_ZIP,
    PRESTAMOS_EMPRESAS_ZIP,
    _write_keep_v1_authorization,
)

pytestmark = pytest.mark.integration

__all__ = ["ready_blocked_zip"]


def _snapshot(directory: Path) -> dict[str, str]:
    return {
        p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def _catherine_fixtures_snapshot() -> dict[str, bytes]:
    return {
        str(path): path.read_bytes()
        for path in (CATHERINE_ORIGINAL_ZIP, CATHERINE_CORRECTED_ZIP)
        if path.is_file()
    }


def _run_no_regression(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, zip_path: Path) -> None:
    require_jar()
    original_bytes = zip_path.read_bytes()

    install_dynamic_rule_draft_fake_client(monkeypatch, rule_drafts_stage_module)
    install_dynamic_rule_draft_fake_client(monkeypatch, guardrails_stage_module)

    settings = build_settings(tmp_path)
    run_dir, run_id, succeeded_stages = _run_pipeline(settings, zip_path)
    assert "PARSED" in succeeded_stages

    with TestClient(create_app(settings)) as client:
        # --- Sin activation: NOT_INITIALIZED, legacy intacto ---
        before_no_activation = _snapshot(run_dir)
        overview_before = build_operational_governance_overview(run_dir, run_id)
        assert overview_before.status.value == "NOT_INITIALIZED"

        legacy_response = client.get(f"/ui/runs/{run_id}")
        assert legacy_response.status_code == 200
        governance_response = client.get(f"/api/runs/{run_id}/governance")
        assert governance_response.status_code == 200
        assert governance_response.json()["status"] == "NOT_INITIALIZED"

        after_no_activation = _snapshot(run_dir)
        assert before_no_activation == after_no_activation

        if "CANDIDATES_DETECTED" not in succeeded_stages:
            pytest.skip(
                "CANDIDATES_DETECTED no se alcanzo en este entorno (Neo4j no disponible) -- "
                "no existe base V1 para inicializar activation/"
            )

        # --- V1 inicializado: HEALTHY_V1, legacy intacto ---
        eval_config = tmp_path / "config-v1-only.yaml"
        eval_config.write_text("mode: V1_ONLY\n", encoding="utf-8")

        evaluation = compute_unified_activation_evaluation(run_dir, run_id, config_path=eval_config)
        write_unified_activation_evaluation(run_dir, evaluation)

        eval_path = run_dir / "diagnostics" / "unified-activation-evaluation.json"
        eval_hash = hashlib.sha256(eval_path.read_bytes()).hexdigest()
        keep_v1_auth = tmp_path / "keep-v1.yaml"
        _write_keep_v1_authorization(
            keep_v1_auth,
            run_id=run_id,
            eval_hash=eval_hash,
            readiness=evaluation.readiness_disposition.value,
        )
        materialize_unified_activation(run_dir, run_id, authorization_path=keep_v1_auth)

        before_v1 = _snapshot(run_dir)
        overview_v1 = build_operational_governance_overview(run_dir, run_id)
        assert overview_v1.status.value == "HEALTHY_V1"

        legacy_response_v1 = client.get(f"/ui/runs/{run_id}")
        assert legacy_response_v1.status_code == 200
        governance_response_v1 = client.get(f"/api/runs/{run_id}/governance")
        assert governance_response_v1.status_code == 200
        assert governance_response_v1.json()["status"] == "HEALTHY_V1"

        # Repetir GET/HEAD varias veces: cero escritura, cero evento
        # nuevo, cero cambio de pointer_version.
        for _ in range(3):
            client.get(f"/api/runs/{run_id}/governance")
            client.get(f"/ui/runs/{run_id}/governance")
            client.head(f"/api/runs/{run_id}/governance/artifacts/candidates")
            client.get(f"/api/runs/{run_id}/governance/artifacts/candidates")
            client.get(f"/api/runs/{run_id}/governance/events")
            client.get(f"/api/runs/{run_id}/governance/generations")

        after_v1 = _snapshot(run_dir)
        assert before_v1 == after_v1

        store = UnifiedActivationStore(run_dir)
        pointer_final = store.read_active_pointer()
        assert pointer_final is not None
        assert pointer_final.pointer_version == 1
        assert pointer_final.active_lane.value == "V1"

    assert zip_path.read_bytes() == original_bytes, f"{zip_path} fue modificado in situ"


@pytest.mark.integration
def test_consulta_saldos_no_regression(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _run_no_regression(monkeypatch, tmp_path, PROGRULE1_ZIP)


@pytest.mark.integration
def test_catherine_original_no_regression(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    catherine_before = _catherine_fixtures_snapshot()
    _run_no_regression(monkeypatch, tmp_path, CATHERINE_ORIGINAL_ZIP)
    assert _catherine_fixtures_snapshot() == catherine_before


@pytest.mark.integration
def test_catherine_corrected_no_regression(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    catherine_before = _catherine_fixtures_snapshot()
    _run_no_regression(monkeypatch, tmp_path, CATHERINE_CORRECTED_ZIP)
    assert _catherine_fixtures_snapshot() == catherine_before


@pytest.mark.integration
def test_clientes_empresas_no_regression(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _run_no_regression(monkeypatch, tmp_path, CLIENTES_EMPRESAS_ZIP)


@pytest.mark.integration
def test_prestamos_empresas_no_regression(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _run_no_regression(monkeypatch, tmp_path, PRESTAMOS_EMPRESAS_ZIP)


@pytest.mark.integration
def test_fase9_15a_caller10_callee10_scenario_no_regression(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, ready_blocked_zip: Path
) -> None:
    catherine_before = _catherine_fixtures_snapshot()
    _run_no_regression(monkeypatch, tmp_path, ready_blocked_zip)
    assert _catherine_fixtures_snapshot() == catherine_before
