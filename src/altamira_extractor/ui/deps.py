"""Dependencias inyectables de la UI (Prompt 13d).

Mismo patron que `api/deps.py`: `Jinja2Templates` vive en `app.state`
(poblado por `create_app`/`lifespan`, ver `api/app.py`), nunca en un
global de modulo. Cada handler lo recibe via `Depends`."""

from __future__ import annotations

from typing import cast

from fastapi import Request
from fastapi.templating import Jinja2Templates

from ..api.executor import RunExecutor
from ..config import Settings


def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_executor(request: Request) -> RunExecutor:
    return cast(RunExecutor, request.app.state.executor)


def get_templates(request: Request) -> Jinja2Templates:
    return cast(Jinja2Templates, request.app.state.templates)
