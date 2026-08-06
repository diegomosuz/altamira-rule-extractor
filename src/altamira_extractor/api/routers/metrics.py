"""GET /internal/metrics (Fase 15B2-B, Seccion 8; corregido en el cierre
correctivo 15B2-B): expone el `CollectorRegistry` propio de esta app
(`app.state.observability`) en formato de texto Prometheus.

Excluido del OpenAPI (`include_in_schema=False`): no es parte de la API
publica documentada. `no-store` siempre -- nunca cacheable, ni por un
proxy intermedio. Deshabilitado (404, no 403: no revela si el endpoint
"existe" pero esta protegido) cuando `metrics.mode=DISABLED`. El token
de `INTERNAL_TOKEN` se compara en tiempo constante
(`secrets.compare_digest`) y NUNCA se loguea ni se refleja en la
respuesta -- ni siquiera en caso de fallo.

NO esta en `_MISCONFIGURED_ALLOWED_PATHS` (`api/app.py`): a diferencia
del diseno original de esta fase, este endpoint SI queda bloqueado por
`SecurityMisconfiguredGateMiddleware` cuando `config/security.yaml` esta
ausente o es invalido -- un token correcto NUNCA alcanza a este handler
en ese estado, la respuesta la produce el gate global (503,
`security_misconfigured`), coherente con el resto de la aplicacion.
Exponer metricas independientemente de la postura de seguridad conocida
habria sido una ruta alternativa a informacion operacional, exactamente
lo que el gate global existe para impedir."""

from __future__ import annotations

import os
import secrets

from fastapi import APIRouter, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter(tags=["internal"])

_METRICS_PATH = "/internal/metrics"


def _authorized(request: Request) -> bool:
    observability_config = getattr(request.app.state, "observability_config", None)
    if observability_config is None:
        return False
    metrics_config = observability_config.metrics
    if metrics_config.access_mode.value == "INTERNAL_TOKEN":
        assert metrics_config.access_token_env_var is not None
        expected = os.environ.get(metrics_config.access_token_env_var)
        if not expected:
            return False
        supplied = request.headers.get("X-Altamira-Metrics-Token")
        if not supplied:
            return False
        return secrets.compare_digest(supplied, expected)
    if metrics_config.access_mode.value == "TRUSTED_PROXY":
        assert metrics_config.trusted_proxy_marker_header is not None
        assert metrics_config.trusted_proxy_marker_value_env_var is not None
        expected = os.environ.get(metrics_config.trusted_proxy_marker_value_env_var)
        if not expected:
            return False
        supplied = request.headers.get(metrics_config.trusted_proxy_marker_header)
        if not supplied:
            return False
        return secrets.compare_digest(supplied, expected)
    return False


@router.get(_METRICS_PATH, include_in_schema=False)
def get_internal_metrics(request: Request) -> Response:
    observability_config = getattr(request.app.state, "observability_config", None)
    if observability_config is None or observability_config.metrics.mode.value != "ENABLED":
        return Response(status_code=404)
    if not _authorized(request):
        return Response(status_code=404)

    registry = request.app.state.observability
    payload = generate_latest(registry.registry)
    return Response(
        content=payload,
        media_type=CONTENT_TYPE_LATEST,
        headers={"Cache-Control": "no-store"},
    )


__all__ = ["router"]
