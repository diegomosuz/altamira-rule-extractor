"""Configuracion base del bootstrap.

Etapas posteriores del pipeline (Neo4j, LLM, etc.) extenderan esta
configuracion; en esta etapa solo se define lo minimo para arrancar
logging y rutas de artefactos.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Extensiones admitidas en paquetes Altamira (docs/PACKAGE_CONTRACT.md +
# correccion explicita del Prompt 3: agrega .cob, .copy y .txt).
DEFAULT_ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".cbl",
        ".cob",
        ".cpy",
        ".copy",
        ".dcl",
        ".sql",
        ".ddl",
        ".csv",
        ".xml",
        ".txt",
    }
)


def _discover_repo_root(start: Path | None = None) -> Path:
    """Ubica la raiz del repositorio buscando pyproject.toml hacia arriba.

    Evita depender del directorio de trabajo actual (CWD): funciona igual
    en desarrollo local (Windows) y dentro del contenedor Docker, donde
    todo el contexto de build se copia a un WORKDIR fijo. `start` es
    unicamente para tests (permite forzar el caso "no encontrado" sin
    tocar archivos reales); en uso normal siempre se omite.
    """
    current = (start or Path(__file__)).resolve()
    for parent in current.parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    raise RuntimeError(
        "no se pudo localizar la raiz del repositorio (no se encontro pyproject.toml "
        f"en ningun ancestro de {current})"
    )


def _default_manifest_xsd_path() -> Path:
    return _discover_repo_root() / "schemas" / "manifest.xsd"


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

    # Localizacion estable de schemas/manifest.xsd (no depende del CWD).
    manifest_xsd_path: Path = Field(default_factory=_default_manifest_xsd_path)

    # Limites de seguridad para PackageValidator/SafeExtractor
    # (.claude/rules/security.md: "Limitar tamano, cantidad de archivos y
    # ratio de expansion", "Rechazar extensiones no permitidas").
    max_package_size_bytes: int = Field(default=200 * 1024 * 1024, gt=0)
    max_entry_count: int = Field(default=5000, gt=0)
    max_single_entry_uncompressed_bytes: int = Field(default=50 * 1024 * 1024, gt=0)
    max_total_uncompressed_bytes: int = Field(default=1024 * 1024 * 1024, gt=0)
    max_compression_ratio: float = Field(default=100.0, gt=0)
    allowed_extensions: frozenset[str] = Field(default_factory=lambda: DEFAULT_ALLOWED_EXTENSIONS)


def load_settings() -> Settings:
    """Punto unico de carga de configuracion para CLI/API/pipeline."""
    return Settings()
