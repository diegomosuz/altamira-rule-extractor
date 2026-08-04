"""Tests de la UI de gobierno operativo (Fase 15A Parte 8/9/12, items
75-93, `feat/operational-governance-ui`)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from altamira_extractor.api.app import create_app
from altamira_extractor.config import Settings
from altamira_extractor.contracts.operational_governance import (
    GovernanceEventChainStatus,
    GovernanceIntegrityStatus,
    GovernanceUnifiedGroupSummary,
    OperationalGovernanceOverview,
    OperationalGovernanceStatus,
)
from altamira_extractor.contracts.unified_activation_materialization import (
    MaterializedActivationLane,
)

from ..pipeline._operational_governance_fixtures import (
    build_materialization_fixture,
    governance_run_dir,
    materialize_keep_v1,
    materialize_unified_canary,
)

MALICIOUS_PROGRAM = "<script>alert(1)</script>"


def _settings_for(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    settings = Settings(
        data_dir=data_dir, runs_dir=data_dir / "runs", incoming_dir=data_dir / "incoming"
    )
    settings.runs_dir.mkdir(parents=True, exist_ok=True)
    return settings


def _malicious_overview(run_id: str) -> OperationalGovernanceOverview:
    group = GovernanceUnifiedGroupSummary(
        group_id="group::malicious",
        rule_family=MALICIOUS_PROGRAM,
        program=MALICIOUS_PROGRAM,
        guardrail_status="PASSED",
    )
    return OperationalGovernanceOverview(
        run_id=run_id,
        run_stage="CANDIDATES_DETECTED",
        activation_initialized=True,
        status=OperationalGovernanceStatus.HEALTHY_UNIFIED,
        active_lane=MaterializedActivationLane.UNIFIED,
        active_generation_id=f"generation-{'a' * 64}",
        pointer_version=1,
        fallback_generation_id=f"generation-{'b' * 64}",
        latest_event_id=f"event-{'c' * 64}",
        event_chain_status=GovernanceEventChainStatus.VALID,
        event_chain_length=0,
        generation_count=0,
        confirmed_event_count=0,
        orphan_generation_count=0,
        orphan_event_count=0,
        active_manifest_integrity=GovernanceIntegrityStatus.VALID,
        unified_groups=[group],
    )


# 75. pagina sin activation.
def test_governance_page_without_activation(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    governance_run_dir(settings.runs_dir, fx)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/ui/runs/{fx.run_id}/governance")
    assert response.status_code == 200
    assert "NOT_INITIALIZED" in response.text


# 76. pagina V1.
def test_governance_page_v1(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    materialize_keep_v1(run_dir, fx, tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/ui/runs/{fx.run_id}/governance")
    assert response.status_code == 200
    assert "HEALTHY_V1" in response.text


# 77. pagina unified.
def test_governance_page_unified(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/ui/runs/{fx.run_id}/governance")
    assert response.status_code == 200
    assert "HEALTHY_UNIFIED" in response.text


# 78. banner read-only.
def test_read_only_banner_visible(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/ui/runs/{fx.run_id}/governance")
    assert "exclusivamente de lectura" in response.text.lower()
    assert "las operaciones de activacion requieren autorizacion explicita" in response.text.lower()


# 79. aviso sin autenticacion.
def test_no_authentication_notice_visible(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/ui/runs/{fx.run_id}/governance")
    assert "no implementa autenticacion de usuario" in response.text.lower()


# 80. lane activo.
def test_active_lane_shown(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/ui/runs/{fx.run_id}/governance")
    assert "UNIFIED" in response.text


# 81. readiness.
def test_readiness_shown(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    materialize_keep_v1(run_dir, fx, tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/ui/runs/{fx.run_id}/governance")
    assert "READY_FOR_UNIFIED_CANARY" in response.text


# 82. artifact table.
def test_artifact_table_shown(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/ui/runs/{fx.run_id}/governance")
    assert "governance-artifacts" in response.text
    assert "candidates" in response.text
    assert "Descargar" in response.text


# 83. event timeline.
def test_event_timeline_shown(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/ui/runs/{fx.run_id}/governance")
    assert "governance-events" in response.text
    assert "INITIALIZE_V1" in response.text
    assert "ACTIVATE_UNIFIED_CANARY" in response.text


# 84. generation table.
def test_generation_table_shown(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/ui/runs/{fx.run_id}/governance")
    assert "governance-generations" in response.text
    assert "V1_BASELINE" in response.text
    assert "UNIFIED_CANARY" in response.text


# 85. groups table.
def test_groups_table_shown(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/ui/runs/{fx.run_id}/governance")
    assert "governance-groups" in response.text
    assert fx.evaluation.unified_references[0].group_id in response.text


# 86. issues.
def test_issues_shown(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/ui/runs/{fx.run_id}/governance")
    assert "governance-issues" in response.text
    assert "WRITE_OPERATIONS_DISABLED" in response.text


# 87. fragments.
def test_all_fragments_reachable_directly(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    with TestClient(create_app(settings)) as client:
        for fragment in (
            "summary-fragment",
            "artifacts-fragment",
            "events-fragment",
            "generations-fragment",
            "groups-fragment",
            "issues-fragment",
        ):
            response = client.get(f"/ui/runs/{fx.run_id}/governance/{fragment}")
            assert response.status_code == 200, fragment


# 88. funciona sin HTMX.
def test_page_works_without_htmx_js(tmp_path: Path) -> None:
    """El HTML completo (sin ejecutar ningun script) ya contiene todo
    el contenido -- las peticiones normales del TestClient nunca
    ejecutan JavaScript, exactamente como un navegador con JS
    deshabilitado."""
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/ui/runs/{fx.run_id}/governance")
    assert "governance-summary" in response.text
    assert "governance-artifacts" in response.text
    assert "governance-generations" in response.text
    assert "governance-groups" in response.text
    assert "governance-issues" in response.text


# 89. navegacion desde run detail.
def test_navigation_link_from_run_detail(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/ui/runs/{fx.run_id}")
        assert response.status_code == 200
        assert f"/ui/runs/{fx.run_id}/governance" in response.text
        follow = client.get(f"/ui/runs/{fx.run_id}/governance")
    assert follow.status_code == 200


# 90. escape XSS.
def test_group_fields_are_escaped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import altamira_extractor.ui.router as ui_router_module

    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    materialize_keep_v1(run_dir, fx, tmp_path)

    overview = _malicious_overview(fx.run_id)
    monkeypatch.setattr(
        ui_router_module, "build_operational_governance_overview", lambda _dir, _id: overview
    )
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/ui/runs/{fx.run_id}/governance")
    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text


# 91. accesibilidad basica.
def test_basic_accessibility_structure(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/ui/runs/{fx.run_id}/governance")
    text = response.text
    assert "<h1>" in text
    assert re.search(r"<h2[ >]", text)
    assert "aria-labelledby" in text
    assert 'scope="col"' in text
    assert "skip-link" in text


# 92. keyboard navigation.
def test_no_javascript_only_interactivity(tmp_path: Path) -> None:
    """Ningun `onclick`/handler JS: toda la interaccion (filtros,
    descargas, navegacion) usa elementos nativos (`<a>`, `<button>`,
    `<select>`, formularios `GET`) -- operables por teclado sin
    dependencias adicionales."""
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/ui/runs/{fx.run_id}/governance")
    text = response.text
    assert "onclick=" not in text
    assert "javascript:" not in text
    assert "<form" in text
    assert 'method="get"' in text


# 93. status no depende solo de color.
def test_status_never_relies_on_color_alone(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/ui/runs/{fx.run_id}/governance")
    # `status_badge` siempre imprime el texto (via status_label) Y el
    # valor tecnico crudo (`badge-code`) -- nunca solo un color.
    assert "badge-code" in response.text
    assert "HEALTHY_UNIFIED" in response.text
