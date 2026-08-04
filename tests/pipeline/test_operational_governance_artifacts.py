"""Tests de resolucion y descarga de artifacts de gobierno operativo
(Fase 15A Parte 4/7/12, items 42-54, `feat/operational-governance-ui`)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from altamira_extractor.api.app import create_app
from altamira_extractor.config import Settings
from altamira_extractor.pipeline.operational_governance_reader import (
    build_operational_governance_overview,
)
from altamira_extractor.pipeline.unified_activation_store import UnifiedActivationStore

from ._operational_governance_fixtures import (
    build_materialization_fixture,
    governance_run_dir,
    materialize_keep_v1,
    materialize_unified_canary,
)


def _settings_for(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    settings = Settings(
        data_dir=data_dir, runs_dir=data_dir / "runs", incoming_dir=data_dir / "incoming"
    )
    settings.runs_dir.mkdir(parents=True, exist_ok=True)
    return settings


# 42. candidates V1 disponible.
def test_candidates_available_in_v1(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    materialize_keep_v1(run_dir, fx, tmp_path)
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    candidates = next(a for a in overview.artifacts if a.logical_name == "candidates")
    assert candidates.status.value == "AVAILABLE"
    assert candidates.resolved_lane is not None
    assert candidates.resolved_lane.value == "V1"
    assert candidates.downloadable is True


# 43. context V1 no disponible.
def test_context_packages_not_available_in_v1(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    materialize_keep_v1(run_dir, fx, tmp_path)
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    context = next(a for a in overview.artifacts if a.logical_name == "context-packages")
    assert context.status.value == "NOT_AVAILABLE_IN_LANE"
    assert context.downloadable is False


# 44. candidates unified disponible.
def test_candidates_available_in_unified(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    candidates = next(a for a in overview.artifacts if a.logical_name == "candidates")
    assert candidates.status.value == "AVAILABLE"
    assert candidates.resolved_lane is not None
    assert candidates.resolved_lane.value == "UNIFIED"


# 45. context unified disponible.
def test_context_packages_available_in_unified(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    context = next(a for a in overview.artifacts if a.logical_name == "context-packages")
    assert context.status.value == "AVAILABLE"


# 46. drafts unified disponible.
def test_rule_drafts_available_in_unified(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    drafts = next(a for a in overview.artifacts if a.logical_name == "rule-drafts")
    assert drafts.status.value == "AVAILABLE"


# 47. guardrails unified disponible.
def test_guardrails_available_in_unified(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    guardrails = next(a for a in overview.artifacts if a.logical_name == "guardrails")
    assert guardrails.status.value == "AVAILABLE"


# 48. logical name invalido.
def test_invalid_logical_name_rejected_by_api(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/api/runs/{fx.run_id}/governance/artifacts/bogus-name")
    assert response.status_code == 422


# 49. corrupcion no aplica fallback.
def test_corruption_never_triggers_automatic_fallback(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    run_dir = governance_run_dir(tmp_path, fx)
    result = materialize_unified_canary(run_dir, fx, tmp_path)
    store = UnifiedActivationStore(run_dir)
    (store.generation_dir(result.generation_id) / "candidates.json").write_bytes(b"{bad}")

    before_pointer = store.read_active_pointer()
    build_operational_governance_overview(run_dir, fx.run_id)
    after_pointer = store.read_active_pointer()

    assert before_pointer is not None
    assert after_pointer is not None
    assert before_pointer.to_stable_json() == after_pointer.to_stable_json()
    assert after_pointer.active_lane.value == "UNIFIED"


# 50. download ETag.
def test_download_etag_matches_sha256(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    candidates = next(a for a in overview.artifacts if a.logical_name == "candidates")
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/api/runs/{fx.run_id}/governance/artifacts/candidates")
    assert response.status_code == 200
    assert response.headers["etag"] == f'"{candidates.sha256}"'


# 51. download Content-Disposition.
def test_download_content_disposition_is_safe(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/api/runs/{fx.run_id}/governance/artifacts/candidates")
    disposition = response.headers["content-disposition"]
    assert disposition == f'attachment; filename="{fx.run_id}-candidates.json"'
    assert "\r" not in disposition
    assert "\n" not in disposition


# 52. download no-store.
def test_download_cache_control_no_store(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/api/runs/{fx.run_id}/governance/artifacts/candidates")
    assert response.headers["cache-control"] == "no-store"


# 53. HEAD.
def test_download_head_returns_headers_without_body(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.head(f"/api/runs/{fx.run_id}/governance/artifacts/candidates")
    assert response.status_code == 200
    assert response.content == b""
    assert int(response.headers["content-length"]) > 0


# 54. descarga no modifica archivos.
def test_download_never_modifies_files(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    before = {
        p.relative_to(run_dir).as_posix(): p.read_bytes()
        for p in sorted(run_dir.rglob("*"))
        if p.is_file()
    }
    with TestClient(create_app(settings)) as client:
        client.get(f"/api/runs/{fx.run_id}/governance/artifacts/candidates")
        client.head(f"/api/runs/{fx.run_id}/governance/artifacts/candidates")
    after = {
        p.relative_to(run_dir).as_posix(): p.read_bytes()
        for p in sorted(run_dir.rglob("*"))
        if p.is_file()
    }
    assert before == after
