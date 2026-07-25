"""Dependencias inyectables de la API (Prompt 13b).

`Settings` y el `RunExecutor` viven en `app.state` (poblados por
`create_app`/`lifespan`, ver `api/app.py`) -- nunca en un global de
modulo. Cada handler los recibe via `Depends`, nunca los relee de
variables de entorno directamente."""

from __future__ import annotations

from typing import cast

from fastapi import Request

from ..config import Settings
from .executor import RunExecutor


def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_executor(request: Request) -> RunExecutor:
    return cast(RunExecutor, request.app.state.executor)
