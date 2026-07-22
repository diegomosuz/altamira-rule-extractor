"""Configuracion base del bootstrap.

Etapas posteriores del pipeline (Neo4j, LLM, etc.) extenderan esta
configuracion; en esta etapa solo se define lo minimo para arrancar
logging y rutas de artefactos.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuracion de la aplicacion, poblada desde variables de entorno.

    No lee valores hardcodeados ni imprime secretos; los campos sensibles
    (credenciales Neo4j, API keys de LLM) se agregaran en etapas
    posteriores del pipeline, no en el bootstrap.
    """

    model_config = SettingsConfigDict(
        env_prefix="ALTAMIRA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = Field(default="local")
    log_level: str = Field(default="INFO")
    data_dir: Path = Field(default=Path("data"))
    runs_dir: Path = Field(default=Path("data/runs"))
    incoming_dir: Path = Field(default=Path("data/incoming"))


def load_settings() -> Settings:
    """Punto unico de carga de configuracion para CLI/API/pipeline."""
    return Settings()
