"""Fixtures globales de toda la suite (checkpoint correctivo: aislamiento
de `.env`).

`Settings` (`altamira_extractor.config`) declara `model_config =
SettingsConfigDict(env_file=".env", ...)`: por diseno, CUALQUIER
`Settings()` construido durante un test intenta leer un `.env` real
relativo al directorio de trabajo del proceso pytest. Un `.env` real de
uso manual del proyecto (con `LLM_PROVIDER=openai`/`OPENAI_API_KEY`/etc.
reales, nunca versionado ni leido aqui) contaminaria en silencio
cualquier test que construya `Settings()` sin sobreescribir
explicitamente esos campos -- desde aserciones de "sin configuracion LLM"
hasta, mucho peor, una llamada de red real a un proveedor LLM real
durante `pytest` (CLAUDE.md/testing.md: "Ninguna llamada externa en la
suite por defecto").

Este fixture `autouse` neutraliza UNICAMENTE la fuente `env_file` de
pydantic-settings (nunca lee ni borra el archivo real en si) durante la
duracion de cada test, restaurandola automaticamente despues
(`monkeypatch`). Los tests que necesitan un `Settings` con configuracion
LLM especifica siguen funcionando identico: pasan los campos explicitos
como kwargs (maxima prioridad en pydantic-settings, por encima de
cualquier fuente de archivo o de entorno)."""

from __future__ import annotations

import pytest

from altamira_extractor.config import Settings


@pytest.fixture(autouse=True)
def _no_real_dotenv_in_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
