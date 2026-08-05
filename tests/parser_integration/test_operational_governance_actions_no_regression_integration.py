"""No-regresión de Fase 15B1 (`feat/final-hardening-release`) sobre los
mismos SEIS paquetes reales de Fase 15A: CONSULTA_SALDOS, Catherine
original, Catherine corregido, CLIENTES_EMPRESAS, PRESTAMOS_EMPRESAS y
CALLER10/CALLEE10. Reutiliza exactamente los mismos fixtures/helpers de
`test_operational_governance_no_regression_integration.py` (sin
modificarlo) y agrega, sobre el mismo run V1 ya inicializado, la capa
de escritura NUEVA de Fase 15B1: confirma que un VIEWER puede leer el
gobierno, que un POST no autorizado a las acciones operativas se
rechaza (403) y NUNCA modifica `activation/` (ni ningún otro archivo
preexistente), y que la denegación queda auditada.

El workflow completo de escritura (prepare/confirm/execute real,
canary/fallback/rollback) para CALLER10/CALLEE10 se cubre por separado
en `test_operational_governance_actions_integration.py` (Parte 18) --
este archivo se enfoca en la garantía de NO-REGRESIÓN: nada de lo
nuevo perturba un run existente cuando no hay autorización.

No corre en la suite por defecto (marcado `integration`, requiere JAR
real)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

import altamira_extractor.pipeline.guardrails_applied_stage as guardrails_stage_module
import altamira_extractor.pipeline.rule_drafts_generated_stage as rule_drafts_stage_module
from altamira_extractor.api.app import create_app
from altamira_extractor.contracts.security_config import (
    ApplicationRole,
    AuthenticationMode,
    SecurityConfig,
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

_MARKER_HEADER = "X-Trusted-Proxy"
_MARKER_VALUE = "f15b1-no-regression-marker"
_USER_HEADER = "X-Verified-User"
_GROUPS_HEADER = "X-Verified-Groups"


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


def _security_config() -> SecurityConfig:
    return SecurityConfig(
        authentication_mode=AuthenticationMode.TRUSTED_PROXY_HEADERS,
        trusted_proxy_header_user=_USER_HEADER,
        trusted_proxy_header_groups=_GROUPS_HEADER,
        trusted_proxy_required_marker_header=_MARKER_HEADER,
        trusted_proxy_required_marker_value=SecretStr(_MARKER_VALUE),
        trusted_proxy_allowed_roles=[ApplicationRole.REVIEWER, ApplicationRole.OPERATOR],
        group_role_mapping={
            "operators": ApplicationRole.OPERATOR,
            "reviewers": ApplicationRole.REVIEWER,
        },
        session_cookie_name="altamira_session",
        session_cookie_secure=True,
    )


def _run_governance_actions_no_regression(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, zip_path: Path
) -> None:
    require_jar()
    original_bytes = zip_path.read_bytes()

    install_dynamic_rule_draft_fake_client(monkeypatch, rule_drafts_stage_module)
    install_dynamic_rule_draft_fake_client(monkeypatch, guardrails_stage_module)

    settings = build_settings(tmp_path)
    run_dir, run_id, succeeded_stages = _run_pipeline(settings, zip_path)
    assert "PARSED" in succeeded_stages
    if "CANDIDATES_DETECTED" not in succeeded_stages:
        pytest.skip(
            "CANDIDATES_DETECTED no se alcanzo en este entorno -- no existe base V1 "
            "para inicializar activation/"
        )

    # --- V1 inicializado (identico al patron de Fase 15A) ---
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

    run_json_before = (run_dir / "run.json").read_bytes()
    artifacts_before = {
        p.name: p.read_bytes() for p in (run_dir / "artifacts").glob("*") if p.is_file()
    }
    diagnostics_before = {
        p.name: p.read_bytes() for p in (run_dir / "diagnostics").glob("*") if p.is_file()
    }
    before_snapshot = _snapshot(run_dir)

    with TestClient(create_app(settings), base_url="https://testserver") as client:
        client.app.state.security_config = _security_config()  # type: ignore[attr-defined]
        # Cierre F15B1 ("DISABLED_DEV explicito") -- ver la misma nota
        # en `test_operational_governance_actions_integration.py`.
        client.app.state.security_misconfigured = False  # type: ignore[attr-defined]
        # Ver la misma nota en
        # `test_operational_governance_actions_integration.py`.
        client.app.state.session_secret = SecretStr(  # type: ignore[attr-defined]
            "test-only-synthetic-session-secret-32chars-min"
        )
        client.headers.update({"Origin": "https://testserver"})

        # --- VIEWER: puede leer, no ve acciones, endpoint legacy funciona ---
        client.headers.update(
            {_MARKER_HEADER: _MARKER_VALUE, _USER_HEADER: "viewer1", _GROUPS_HEADER: "no-group"}
        )
        legacy_response = client.get(f"/ui/runs/{run_id}")
        assert legacy_response.status_code == 200
        governance_response = client.get(f"/api/runs/{run_id}/governance")
        assert governance_response.status_code == 200
        actions_list = client.get(f"/ui/runs/{run_id}/governance/actions")
        assert actions_list.status_code == 200
        assert "ACTIVATE_UNIFIED_CANARY" not in actions_list.text

        # --- POST no autorizado se rechaza (403), sin CSRF valido ni permiso ---
        unauthorized_post = client.post(
            f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_CANARY/prepare",
            data={
                "reason_code": "CANARY_APPROVED",
                "review_reference": "no-regression-attempt",
                "target_generation_id": "",
            },
            follow_redirects=False,
        )
        assert unauthorized_post.status_code == 403

        # --- La denegacion queda auditada -- consultado por un REVIEWER,
        # el UNICO rol de este run con VIEW_AUDIT_LOG (VIEWER no lo
        # tiene, por diseno de la matriz RBAC -- Parte 3). ---
        client.headers.update(
            {_MARKER_HEADER: _MARKER_VALUE, _USER_HEADER: "reviewer1", _GROUPS_HEADER: "reviewers"}
        )
        audit_response = client.get(f"/ui/runs/{run_id}/governance/audit")
        assert audit_response.status_code == 200
        assert re.search(r"ACCESS_DENIED|CSRF_REJECTED", audit_response.text) is not None

    # --- Nada preexistente cambio: run.json, artifacts, diagnostics, activation/ ---
    assert (run_dir / "run.json").read_bytes() == run_json_before
    artifacts_after = {
        p.name: p.read_bytes() for p in (run_dir / "artifacts").glob("*") if p.is_file()
    }
    assert artifacts_after == artifacts_before
    diagnostics_after = {
        p.name: p.read_bytes() for p in (run_dir / "diagnostics").glob("*") if p.is_file()
    }
    assert diagnostics_after == diagnostics_before

    store = UnifiedActivationStore(run_dir)
    pointer_final = store.read_active_pointer()
    assert pointer_final is not None
    assert pointer_final.pointer_version == 1
    assert pointer_final.active_lane.value == "V1"

    # `audit/` es la UNICA adicion permitida (evento ACCESS_DENIED) --
    # todo lo demas, byte a byte, permanece igual.
    after_snapshot = _snapshot(run_dir)
    changed_or_new = {k: v for k, v in after_snapshot.items() if before_snapshot.get(k) != v}
    assert all(k.startswith("audit/") for k in changed_or_new), changed_or_new

    assert zip_path.read_bytes() == original_bytes, f"{zip_path} fue modificado in situ"


@pytest.mark.integration
def test_consulta_saldos_governance_actions_no_regression(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _run_governance_actions_no_regression(monkeypatch, tmp_path, PROGRULE1_ZIP)


@pytest.mark.integration
def test_catherine_original_governance_actions_no_regression(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    catherine_before = _catherine_fixtures_snapshot()
    _run_governance_actions_no_regression(monkeypatch, tmp_path, CATHERINE_ORIGINAL_ZIP)
    assert _catherine_fixtures_snapshot() == catherine_before


@pytest.mark.integration
def test_catherine_corrected_governance_actions_no_regression(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    catherine_before = _catherine_fixtures_snapshot()
    _run_governance_actions_no_regression(monkeypatch, tmp_path, CATHERINE_CORRECTED_ZIP)
    assert _catherine_fixtures_snapshot() == catherine_before


@pytest.mark.integration
def test_clientes_empresas_governance_actions_no_regression(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _run_governance_actions_no_regression(monkeypatch, tmp_path, CLIENTES_EMPRESAS_ZIP)


@pytest.mark.integration
def test_prestamos_empresas_governance_actions_no_regression(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _run_governance_actions_no_regression(monkeypatch, tmp_path, PRESTAMOS_EMPRESAS_ZIP)


@pytest.mark.integration
def test_caller10_callee10_governance_actions_no_regression(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, ready_blocked_zip: Path
) -> None:
    catherine_before = _catherine_fixtures_snapshot()
    _run_governance_actions_no_regression(monkeypatch, tmp_path, ready_blocked_zip)
    assert _catherine_fixtures_snapshot() == catherine_before
