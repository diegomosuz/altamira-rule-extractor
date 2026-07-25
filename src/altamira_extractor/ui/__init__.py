"""UI HTML server-rendered (Prompt 13d): Jinja2 + HTMX minimo, same-origin.

Reutiliza exactamente los mismos casos de uso que la API JSON
(`api/reads.py`, `api/validation.py`, `api/downloads.py`,
`api/mappers.py`, `api/run_actions.py`) -- nunca llama a `/api/*` por
HTTP interno. Sin autenticacion (fuera de alcance V1, igual que
`api/app.py`): el acceso de red a este servicio debe restringirse
externamente."""

from __future__ import annotations

from pathlib import Path

# Relativos al paquete Python, nunca al CWD (funciona igual en
# desarrollo local y dentro del contenedor Docker, sea cual sea el
# directorio de trabajo desde el que se invoque Uvicorn/pytest).
TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
