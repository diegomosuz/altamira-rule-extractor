"""Observabilidad (Fase 15B2-B): logging estructurado, metricas
Prometheus, health/readiness y diagnostico de componentes.

Nunca modifica el resultado funcional del pipeline ni introduce un
nuevo gate global de fail-closed (a diferencia de `security/`) -- ver
`contracts/observability.py`."""

from __future__ import annotations
