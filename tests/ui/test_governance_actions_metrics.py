"""Tests de instrumentacion del gobierno operativo (Fase 15B2-B,
Seccion 13): `altamira_operational_actions_total` en la ejecucion real
de una activacion, `altamira_security_denials_total` via el choke point
centralizado `api/app.py::_handle_api_error` -- reutiliza el mismo
patron de fixtures que `test_governance_actions_flow.py`."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from prometheus_client import generate_latest
from pydantic import SecretStr

from altamira_extractor.api.app import create_app
from altamira_extractor.config import Settings
from altamira_extractor.contracts.enums import PipelineStage
from altamira_extractor.contracts.run_state import RunState
from altamira_extractor.contracts.security_config import (
    ApplicationRole,
    AuthenticationMode,
    SecurityConfig,
)
from altamira_extractor.pipeline.artifact_store import atomic_write_json
from altamira_extractor.pipeline.unified_materialization_service import (
    materialize_unified_activation,
)
from tests.pipeline._unified_materialization_fixtures import (
    build_materialization_fixture,
    write_authorization_yaml,
    write_run_dir,
)

_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')
_CHALLENGE_RE = re.compile(r'name="challenge_token" value="([^"]+)"')

_MARKER_HEADER = "X-Trusted-Proxy"
_MARKER_VALUE = "test-marker"
_USER_HEADER = "X-Verified-User"
_GROUPS_HEADER = "X-Verified-Groups"


@pytest.fixture
def run_setup(tmp_path: Path) -> tuple[Settings, str]:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    (tmp_path / "incoming").mkdir()
    fx = build_materialization_fixture()
    run_dir = write_run_dir(runs_dir, fx)
    eval_path = run_dir / "diagnostics" / "unified-activation-evaluation.json"
    eval_hash = hashlib.sha256(eval_path.read_bytes()).hexdigest()
    auth_path = tmp_path / "bootstrap.yaml"
    write_authorization_yaml(
        auth_path,
        run_id=fx.run_id,
        activation_evaluation_hash=eval_hash,
        action="KEEP_V1",
        expected_readiness_disposition="READY_FOR_UNIFIED_CANARY",
    )
    materialize_unified_activation(run_dir, fx.run_id, authorization_path=auth_path)
    atomic_write_json(
        run_dir / "run.json",
        RunState(
            run_id=fx.run_id,
            package_filename="package.zip",
            current_stage=PipelineStage.COMPLETED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        ),
    )
    settings = Settings(runs_dir=runs_dir, incoming_dir=tmp_path / "incoming")
    return settings, fx.run_id


def _trusted_config(*, groups: dict[str, ApplicationRole]) -> SecurityConfig:
    return SecurityConfig(
        authentication_mode=AuthenticationMode.TRUSTED_PROXY_HEADERS,
        trusted_proxy_header_user=_USER_HEADER,
        trusted_proxy_header_groups=_GROUPS_HEADER,
        trusted_proxy_required_marker_header=_MARKER_HEADER,
        trusted_proxy_required_marker_value=SecretStr(_MARKER_VALUE),
        trusted_proxy_allowed_roles=list(set(groups.values())),
        group_role_mapping=groups,
        session_cookie_name="altamira_session",
        session_cookie_secure=True,
    )


@pytest.fixture
def trusted_client(run_setup: tuple[Settings, str]) -> Iterator[TestClient]:
    settings, _run_id = run_setup
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        client.app.state.security_config = _trusted_config(  # type: ignore[attr-defined]
            groups={"reviewers": ApplicationRole.REVIEWER, "operators": ApplicationRole.OPERATOR}
        )
        client.app.state.security_misconfigured = False  # type: ignore[attr-defined]
        client.app.state.session_secret = SecretStr(  # type: ignore[attr-defined]
            "test-only-synthetic-session-secret-32chars-min"
        )
        client.headers.update({"Origin": "https://testserver"})
        yield client


def _as(client: TestClient, user: str, group: str) -> None:
    client.headers.update(
        {_MARKER_HEADER: _MARKER_VALUE, _USER_HEADER: user, _GROUPS_HEADER: group}
    )


def _metrics_text(client: TestClient) -> str:
    registry = client.app.state.observability  # type: ignore[attr-defined]
    return generate_latest(registry.registry).decode("utf-8")


def test_successful_canary_activation_increments_operational_action_metric(
    trusted_client: TestClient, run_setup: tuple[Settings, str]
) -> None:
    _settings, run_id = run_setup
    _as(trusted_client, "operator1", "operators")

    form = trusted_client.get(f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_CANARY")
    csrf_token = _CSRF_RE.search(form.text)
    assert csrf_token is not None
    group_ids = re.findall(r'name="approved_group_ids" value="([^"]+)"', form.text)

    prepare = trusted_client.post(
        f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_CANARY/prepare",
        data={
            "csrf_token": csrf_token.group(1),
            "reason_code": "CANARY_APPROVED",
            "review_reference": "pytest-ticket",
            "approved_group_ids": group_ids,
            "target_generation_id": "",
        },
        follow_redirects=False,
    )
    assert prepare.status_code == 303
    confirm = trusted_client.get(prepare.headers["location"])
    csrf_token_2 = _CSRF_RE.search(confirm.text)
    challenge_token = _CHALLENGE_RE.search(confirm.text)
    assert csrf_token_2 is not None
    assert challenge_token is not None

    execute = trusted_client.post(
        f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_CANARY/execute",
        data={"csrf_token": csrf_token_2.group(1), "challenge_token": challenge_token.group(1)},
        follow_redirects=False,
    )
    assert execute.status_code == 303

    output = _metrics_text(trusted_client)
    assert 'action_type="ACTIVATE_UNIFIED_CANARY",outcome="succeeded"' in output.replace(" ", "")


def test_forbidden_action_increments_security_denial_metric_via_central_handler(
    trusted_client: TestClient, run_setup: tuple[Settings, str]
) -> None:
    """VIEWER intentando ejecutar una accion privilegiada -- 403,
    contado en `altamira_security_denials_total` sin que
    `ui/governance_actions_router.py` necesite conocer el registro
    directamente para este caso (choke point central en `api/app.py`)."""
    _settings, run_id = run_setup
    _as(trusted_client, "viewer1", "no-such-group")

    response = trusted_client.get(f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_CANARY")
    assert response.status_code == 403

    output = _metrics_text(trusted_client)
    assert 'reason_code="forbidden"' in output
