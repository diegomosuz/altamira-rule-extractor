"""Tests de `observability/metrics.py::ObservabilityRegistry` (Fase
15B2-B): cada metodo normaliza labels fuera de catalogo a `UNKNOWN`,
nunca lanza, y una instancia nueva nunca colisiona con otra (colector
propio, no el registro global de `prometheus_client`)."""

from __future__ import annotations

from datetime import UTC, datetime

from prometheus_client import CollectorRegistry
from prometheus_client.parser import text_string_to_metric_families

from altamira_extractor.contracts.enums import PipelineStage, StageStatus
from altamira_extractor.contracts.run_state import RunState, StageExecution
from altamira_extractor.observability.metrics import ObservabilityRegistry


def _render(registry: ObservabilityRegistry) -> str:
    from prometheus_client import generate_latest

    return generate_latest(registry.registry).decode("utf-8")


def test_two_instances_do_not_collide() -> None:
    """El motivo de ser de una instancia por-app: dos `ObservabilityRegistry`
    en el mismo proceso nunca deben lanzar `Duplicated timeseries`."""
    first = ObservabilityRegistry(enabled=True)
    second = ObservabilityRegistry(enabled=True)
    assert isinstance(first.registry, CollectorRegistry)
    assert first.registry is not second.registry


def test_observe_http_request_normalizes_unknown_method() -> None:
    registry = ObservabilityRegistry(enabled=True)
    registry.observe_http_request(
        method="TRACE", route="/api/runs", status_code=200, duration_seconds=0.01
    )
    output = _render(registry)
    assert 'http_method="UNKNOWN"' in output
    assert 'http_method="TRACE"' not in output


def test_observe_http_request_known_method_recorded_as_is() -> None:
    registry = ObservabilityRegistry(enabled=True)
    registry.observe_http_request(
        method="GET", route="/api/runs/{run_id}", status_code=200, duration_seconds=0.02
    )
    output = _render(registry)
    assert 'http_method="GET"' in output
    assert 'http_route="/api/runs/{run_id}"' in output
    assert 'http_status_code="200"' in output


def test_observe_pipeline_run_records_final_stage_and_stage_transitions() -> None:
    registry = ObservabilityRegistry(enabled=True)
    now = datetime.now(UTC)
    state = RunState(
        run_id="20260101T000000000000-aaaaaaaa",
        package_filename="input/package.zip",
        source_package_hash="a" * 64,
        current_stage=PipelineStage.COMPLETED,
        stages=[
            StageExecution(
                stage=PipelineStage.RECEIVED,
                status=StageStatus.SUCCEEDED,
                started_at=now,
                finished_at=now,
                duration_seconds=0.5,
            ),
            StageExecution(stage=PipelineStage.VALIDATED, status=StageStatus.PENDING),
        ],
        created_at=now,
        updated_at=now,
    )
    registry.observe_pipeline_run(state)
    output = _render(registry)
    assert 'final_stage="COMPLETED"' in output
    assert 'stage="RECEIVED",status="SUCCEEDED"' in output.replace(" ", "")
    # PENDING nunca se cuenta como transicion completa.
    assert 'status="PENDING"' not in output


def test_observe_pipeline_run_normalizes_unexpected_final_stage() -> None:
    registry = ObservabilityRegistry(enabled=True)
    now = datetime.now(UTC)
    state = RunState(
        run_id="20260101T000000000000-bbbbbbbb",
        package_filename="input/package.zip",
        source_package_hash="a" * 64,
        current_stage=PipelineStage.PARSED,
        stages=[],
        created_at=now,
        updated_at=now,
    )
    registry.observe_pipeline_run(state)
    output = _render(registry)
    assert 'final_stage="UNKNOWN"' in output


def test_executor_active_runs_gauge_and_capacity_rejections() -> None:
    registry = ObservabilityRegistry(enabled=True)
    registry.set_executor_active_runs(3)
    registry.inc_executor_capacity_rejection()
    registry.inc_executor_capacity_rejection()
    output = _render(registry)
    families = {f.name: f for f in text_string_to_metric_families(output)}
    gauge_samples = families["altamira_executor_active_runs"].samples
    assert gauge_samples[0].value == 3
    counter_samples = families["altamira_executor_capacity_rejections"].samples
    assert counter_samples[0].value == 2


def test_operational_action_normalizes_unknown_action_and_outcome() -> None:
    registry = ObservabilityRegistry(enabled=True)
    registry.inc_operational_action(action_type="DELETE_EVERYTHING", outcome="maybe")
    output = _render(registry)
    assert 'action_type="UNKNOWN"' in output
    assert 'outcome="UNKNOWN"' in output


def test_operational_action_known_values_recorded_as_is() -> None:
    registry = ObservabilityRegistry(enabled=True)
    registry.inc_operational_action(action_type="ACTIVATE_UNIFIED_CANARY", outcome="succeeded")
    output = _render(registry)
    assert 'action_type="ACTIVATE_UNIFIED_CANARY"' in output
    assert 'outcome="succeeded"' in output


def test_security_denial_normalizes_unknown_reason() -> None:
    registry = ObservabilityRegistry(enabled=True)
    registry.inc_security_denial(reason_code="something_made_up")
    output = _render(registry)
    assert 'reason_code="UNKNOWN"' in output


def test_security_denial_known_reason_recorded_as_is() -> None:
    registry = ObservabilityRegistry(enabled=True)
    registry.inc_security_denial(reason_code="forbidden")
    output = _render(registry)
    assert 'reason_code="forbidden"' in output


def test_no_functional_validation_metric_exists() -> None:
    """Cierre correctivo, Seccion 3: `altamira_functional_validation_total`
    se elimino deliberadamente -- validacion funcional es CLI-only, sin
    ningun punto de ejecucion dentro del proceso API. Mantener un
    collector sin productor real seria una metrica nominal, siempre en
    cero."""
    registry = ObservabilityRegistry(enabled=True)
    assert not hasattr(registry, "functional_validation_total")
    assert not hasattr(registry, "observe_functional_validation")
    output = _render(registry)
    assert "altamira_functional_validation_total" not in output


def test_disabled_registry_still_never_raises() -> None:
    """`enabled=False` no debe requerir que el llamador ramifique -- los
    metodos siguen siendo seguros de invocar (no exponerlos es
    responsabilidad de `/internal/metrics`, no de este objeto)."""
    registry = ObservabilityRegistry(enabled=False)
    registry.observe_http_request(method="GET", route="/x", status_code=200, duration_seconds=0.0)
    registry.inc_executor_capacity_rejection()
    registry.set_executor_active_runs(0)
    registry.inc_security_denial(reason_code="forbidden")
