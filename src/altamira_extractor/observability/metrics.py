"""Metricas Prometheus (Fase 15B2-B).

`ObservabilityRegistry` es SIEMPRE una instancia por-app (nunca un
`Counter`/`Histogram`/`Gauge` de modulo registrado contra el
`CollectorRegistry` global de `prometheus_client`): la suite de tests
construye una app FastAPI nueva (`create_app(settings)`) por cada
funcion de test (ver `tests/api/conftest.py`), y un colector de modulo
lanzaria `ValueError: Duplicated timeseries` en la segunda instancia.
Cada `ObservabilityRegistry` crea su propio `CollectorRegistry` y vive
en `app.state.observability`.

Deliberadamente NO existe una metrica `altamira_functional_validation_total`
(cierre correctivo de esta fase, Seccion 3): la validacion funcional
(`functional-validate`/`functional-validate-dataset`) es exclusivamente
un comando CLI de un solo disparo -- no hay ningun punto de ejecucion
dentro del proceso API cuyo `ObservabilityRegistry` sea el mismo que
expone `/internal/metrics`. Mantener un collector sin productor real
seria una metrica nominal, siempre en cero, que nunca refleja actividad
real -- ver `docs/METRICS.md` para el detalle de esta decision de
alcance.

Ninguna etiqueta usa un identificador de alta cardinalidad (`run_id`,
`candidate_id`, `generation_id`, `program`, `paragraph`, `principal_id`,
`correlation_id`, `filename`, nombre de paquete, path crudo, query
string, nombre de modelo LLM arbitrario, mensaje de error libre) --
Principio B/C de la Fase 15B2-B. Cualquier valor recibido que no
pertenezca al conjunto cerrado esperado se normaliza a `UNKNOWN` antes
de usarse como etiqueta; ninguna llamada a estos metodos puede lanzar
(los fallos se tragan silenciosamente, nunca deben romper el request/
pipeline que instrumentan)."""

from __future__ import annotations

import logging

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from ..contracts.enums import PipelineStage, StageStatus
from ..contracts.run_state import RunState

logger = logging.getLogger(__name__)

_UNKNOWN = "UNKNOWN"

_ALLOWED_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
_ALLOWED_OUTCOMES = frozenset({"succeeded", "failed"})
_ALLOWED_OPERATIONAL_ACTIONS = frozenset(
    {
        "ACTIVATE_UNIFIED_CANARY",
        "ACTIVATE_UNIFIED_PRIMARY",
        "FALLBACK_TO_V1",
        "ROLLBACK_TO_PREVIOUS",
        "ROLLBACK_TO_GENERATION",
    }
)
_ALLOWED_SECURITY_DENIAL_REASONS = frozenset(
    {
        "forbidden",
        "unauthenticated",
        "csrf_rejected",
        "security_misconfigured",
    }
)
_ALLOWED_FINAL_STAGES = frozenset({PipelineStage.COMPLETED.value, PipelineStage.FAILED.value})


def _normalize(value: str, allowed: frozenset[str]) -> str:
    return value if value in allowed else _UNKNOWN


class ObservabilityRegistry:
    """Fachada tipada sobre un `CollectorRegistry` propio de esta app.
    `enabled=False` (metrics.mode=DISABLED) mantiene los colectores
    creados -- simplifica el codigo llamador, que nunca necesita
    ramificar en `if enabled` -- pero ningun metodo se usa para exponer
    nada salvo que `/internal/metrics` este habilitado (ver
    `api/routers/metrics.py`)."""

    def __init__(self, *, enabled: bool) -> None:
        self.enabled = enabled
        self.registry = CollectorRegistry()

        self.http_requests_total = Counter(
            "altamira_http_requests_total",
            "Total de requests HTTP completados",
            ["http_method", "http_route", "http_status_code"],
            registry=self.registry,
        )
        self.http_request_duration_seconds = Histogram(
            "altamira_http_request_duration_seconds",
            "Duracion de requests HTTP completados",
            ["http_method", "http_route"],
            registry=self.registry,
        )
        self.pipeline_runs_total = Counter(
            "altamira_pipeline_runs_total",
            "Total de ejecuciones de pipeline por estado final",
            ["final_stage"],
            registry=self.registry,
        )
        self.pipeline_stage_total = Counter(
            "altamira_pipeline_stage_total",
            "Total de transiciones de etapa del pipeline por estado",
            ["stage", "status"],
            registry=self.registry,
        )
        self.pipeline_stage_duration_seconds = Histogram(
            "altamira_pipeline_stage_duration_seconds",
            "Duracion de cada etapa del pipeline",
            ["stage"],
            registry=self.registry,
        )
        self.executor_active_runs = Gauge(
            "altamira_executor_active_runs",
            "Runs activos (programados o en ejecucion) en el executor local",
            registry=self.registry,
        )
        self.executor_capacity_rejections_total = Counter(
            "altamira_executor_capacity_rejections_total",
            "Total de submits rechazados por capacidad agotada del executor local",
            registry=self.registry,
        )
        self.operational_actions_total = Counter(
            "altamira_operational_actions_total",
            "Total de acciones operativas (canary/primary/fallback/rollback) por resultado",
            ["action_type", "outcome"],
            registry=self.registry,
        )
        self.security_denials_total = Counter(
            "altamira_security_denials_total",
            "Total de denegaciones de seguridad por codigo de motivo",
            ["reason_code"],
            registry=self.registry,
        )

    def observe_http_request(
        self, *, method: str, route: str, status_code: int, duration_seconds: float
    ) -> None:
        try:
            normalized_method = _normalize(method, _ALLOWED_HTTP_METHODS)
            self.http_requests_total.labels(
                http_method=normalized_method, http_route=route, http_status_code=str(status_code)
            ).inc()
            self.http_request_duration_seconds.labels(
                http_method=normalized_method, http_route=route
            ).observe(duration_seconds)
        except Exception:
            logger.warning("fallo no fatal registrando metrica http_request", exc_info=False)

    def observe_pipeline_run(self, state: RunState) -> None:
        """Lee el `RunState` final ya persistido (mismo dato que produce
        `pipeline/runner.py::_mark_succeeded`/`_mark_failed`) -- nunca
        modifica el pipeline para instrumentarlo."""
        try:
            final_stage = _normalize(state.current_stage.value, _ALLOWED_FINAL_STAGES)
            self.pipeline_runs_total.labels(final_stage=final_stage).inc()
            for stage_execution in state.stages:
                if stage_execution.status not in (StageStatus.SUCCEEDED, StageStatus.FAILED):
                    continue
                self.pipeline_stage_total.labels(
                    stage=stage_execution.stage.value, status=stage_execution.status.value
                ).inc()
                if stage_execution.duration_seconds is not None:
                    self.pipeline_stage_duration_seconds.labels(
                        stage=stage_execution.stage.value
                    ).observe(stage_execution.duration_seconds)
        except Exception:
            logger.warning("fallo no fatal registrando metrica pipeline_run", exc_info=False)

    def set_executor_active_runs(self, count: int) -> None:
        try:
            self.executor_active_runs.set(count)
        except Exception:
            logger.warning("fallo no fatal registrando metrica executor_active_runs")

    def inc_executor_capacity_rejection(self) -> None:
        try:
            self.executor_capacity_rejections_total.inc()
        except Exception:
            logger.warning("fallo no fatal registrando metrica executor_capacity_rejections")

    def inc_operational_action(self, *, action_type: str, outcome: str) -> None:
        try:
            self.operational_actions_total.labels(
                action_type=_normalize(action_type, _ALLOWED_OPERATIONAL_ACTIONS),
                outcome=_normalize(outcome, _ALLOWED_OUTCOMES),
            ).inc()
        except Exception:
            logger.warning("fallo no fatal registrando metrica operational_action")

    def inc_security_denial(self, *, reason_code: str) -> None:
        try:
            self.security_denials_total.labels(
                reason_code=_normalize(reason_code, _ALLOWED_SECURITY_DENIAL_REASONS)
            ).inc()
        except Exception:
            logger.warning("fallo no fatal registrando metrica security_denial")


__all__ = ["ObservabilityRegistry"]
