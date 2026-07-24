"""Configuracion base del bootstrap.

Etapas posteriores del pipeline (Neo4j, LLM, etc.) extenderan esta
configuracion; en esta etapa solo se define lo minimo para arrancar
logging y rutas de artefactos.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
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


def _default_parser_jar_path() -> Path:
    return _discover_repo_root() / "parser" / "target" / "altamira-cobol-parser.jar"


def _default_semantic_tags_path() -> Path:
    return _discover_repo_root() / "config" / "semantic-tags.yml"


def _default_domain_glossary_path() -> Path:
    return _discover_repo_root() / "config" / "domain-glossary.example.yml"


def _default_invariants_cypher_path() -> Path:
    return _discover_repo_root() / "queries" / "v1" / "invariants.cypher"


def _default_q0_candidates_cypher_path() -> Path:
    return _discover_repo_root() / "queries" / "v1" / "q0_candidates.cypher"


def _default_context_query_path(filename: str) -> Path:
    return _discover_repo_root() / "queries" / "v1" / filename


def _default_context_package_schema_path() -> Path:
    return _discover_repo_root() / "schemas" / "context-package.schema.json"


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

    # Integracion con el parser Java (etapa PARSED, ver pipeline/parser_client.py).
    java_bin: str = Field(default="java")
    parser_jar_path: Path = Field(default_factory=_default_parser_jar_path)
    parser_timeout_seconds: float = Field(default=120.0, gt=0)

    # Configuracion de parametria/semantic tagging (etapa
    # SEMANTIC_ENRICHMENT_BUILT, ver pipeline/semantic_enrichment_stage.py).
    semantic_tags_path: Path = Field(default_factory=_default_semantic_tags_path)
    domain_glossary_path: Path = Field(default_factory=_default_domain_glossary_path)
    max_parameter_entries_per_table: int = Field(default=10_000, gt=0)

    # Conexion Neo4j (etapas SEMANTIC_GRAPH_LOADED/GRAPH_VALIDATED, ver
    # pipeline/neo4j_repository.py). NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD/
    # NEO4J_DATABASE se declaran sin prefijo ALTAMIRA_ (ya publicadas asi en
    # .env.example y compartidas con el servicio `neo4j` de docker-compose);
    # se leen con validation_alias explicito para no romper esa convencion
    # pese al env_prefix="ALTAMIRA_" global de este modelo.
    neo4j_uri: str = Field(default="bolt://localhost:7687", validation_alias="NEO4J_URI")
    neo4j_user: str = Field(default="neo4j", validation_alias="NEO4J_USER")
    neo4j_password: SecretStr = Field(
        default=SecretStr("neo4j"), validation_alias="NEO4J_PASSWORD"
    )
    neo4j_database: str = Field(default="neo4j", validation_alias="NEO4J_DATABASE")
    neo4j_connection_timeout_seconds: float = Field(default=30.0, gt=0)
    neo4j_max_transaction_retry_time_seconds: float = Field(default=30.0, gt=0)
    neo4j_load_batch_size: int = Field(default=500, gt=0)

    # Localizacion estable de queries/v1/invariants.cypher (etapa
    # GRAPH_VALIDATED, ver pipeline/graph_invariant_validator.py). Mismo
    # patron que manifest_xsd_path/semantic_tags_path: no depende del CWD.
    invariants_cypher_path: Path = Field(default_factory=_default_invariants_cypher_path)

    # Localizacion estable de queries/v1/q0_candidates.cypher (etapa
    # CANDIDATES_DETECTED, ver pipeline/candidate_detector.py). Mismo
    # patron que invariants_cypher_path: no depende del CWD.
    q0_candidates_cypher_path: Path = Field(default_factory=_default_q0_candidates_cypher_path)

    # Localizacion estable de Q1-Q7 (etapa CONTEXTS_BUILT, ver
    # pipeline/context_package_builder.py) y del schema que las gobierna.
    # Mismo patron que invariants_cypher_path/q0_candidates_cypher_path.
    q1_scope_cypher_path: Path = Field(
        default_factory=lambda: _default_context_query_path("q1_scope.cypher")
    )
    q2_code_slice_cypher_path: Path = Field(
        default_factory=lambda: _default_context_query_path("q2_code_slice.cypher")
    )
    q3a_parameter_context_cypher_path: Path = Field(
        default_factory=lambda: _default_context_query_path("q3a_parameter_context.cypher")
    )
    q3b_transactional_tables_cypher_path: Path = Field(
        default_factory=lambda: _default_context_query_path("q3b_transactional_tables.cypher")
    )
    q4_decision_cypher_path: Path = Field(
        default_factory=lambda: _default_context_query_path("q4_decision.cypher")
    )
    q5a_return_effect_cypher_path: Path = Field(
        default_factory=lambda: _default_context_query_path("q5a_return_effect.cypher")
    )
    q5b_table_effects_cypher_path: Path = Field(
        default_factory=lambda: _default_context_query_path("q5b_table_effects.cypher")
    )
    q6_batch_cypher_path: Path = Field(
        default_factory=lambda: _default_context_query_path("q6_batch.cypher")
    )
    q7_glossary_cypher_path: Path = Field(
        default_factory=lambda: _default_context_query_path("q7_glossary.cypher")
    )
    context_package_schema_path: Path = Field(
        default_factory=_default_context_package_schema_path
    )

    # Profundidad de DATA_DEPENDS_ON/CONTROL_DEPENDS_ON usada por
    # Q2/Q3a/Q3b/Q5b (siempre la misma dentro de una misma corrida de
    # CONTEXTS_BUILT, para que D2/D3/D5 sean coherentes entre si). Cypher
    # no admite un parametro en el limite de una relacion de longitud
    # variable (verificado contra Neo4j 5 real): se sustituye como texto,
    # siempre validado contra este rango cerrado antes de tocar el Cypher.
    dependency_depth: int = Field(default=4, ge=1, le=10)

    # Limites de tamano de un ContextPackage (nunca truncamiento
    # silencioso: exceder cualquiera de estos es un error fatal).
    max_code_slice_paragraphs: int = Field(default=200, ge=1, le=2000)
    max_transactional_tables: int = Field(default=100, ge=1, le=1000)
    # Distinto de max_parameter_entries_per_table (Prompt 7, limita
    # ingestion por tabla): este limita el tamano total de un
    # ContextPackage individual.
    max_parameter_entries_per_context: int = Field(default=1000, ge=1, le=10_000)

    # Cliente LLM OpenAI-compatible (Prompt 11, ver pipeline/llm_client.py).
    # Todos los campos de proveedor (selector + credenciales por
    # proveedor) son opcionales: la creacion general de Settings NUNCA
    # debe exigir configuracion LLM, porque las etapas deterministicas
    # (RECEIVED..CONTEXTS_BUILT) deben seguir funcionando sin un
    # proveedor configurado. La validacion obligatoria (LLM_PROVIDER
    # definido y valido, credenciales del proveedor seleccionado
    # presentes, timeout positivo, retries en rango) ocurre recien al
    # resolver el perfil del cliente (`resolve_llm_profile`), nunca aqui.
    # Los nombres de variable de entorno se conservan tal como ya estaban
    # en .env.example desde el bootstrap (Prompt 1): sin prefijo
    # ALTAMIRA_, igual que NEO4J_*, via validation_alias explicito.
    llm_provider: str | None = Field(default=None, validation_alias="LLM_PROVIDER")
    llm_timeout_seconds: float = Field(default=120.0, validation_alias="LLM_TIMEOUT_SECONDS")
    llm_http_retries: int = Field(default=3, validation_alias="LLM_HTTP_RETRIES")
    # Literal[0]: CLAUDE.md ("Temperatura 0") — nunca configurable a otro
    # valor via variable de entorno, para no introducir no-determinismo.
    # Se conserva el nombre LLM_TEMPERATURE por compatibilidad con
    # .env.example, pero cualquier valor distinto de 0 falla al construir
    # Settings (no se difiere a la resolucion del perfil, a diferencia de
    # timeout/retries/credenciales).
    llm_temperature: Literal[0] = Field(default=0, validation_alias="LLM_TEMPERATURE")

    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_base_url: str | None = Field(default=None, validation_alias="OPENAI_BASE_URL")
    openai_model: str | None = Field(default=None, validation_alias="OPENAI_MODEL")

    pwc_genai_api_key: SecretStr | None = Field(default=None, validation_alias="PWC_GENAI_API_KEY")
    pwc_genai_base_url: str | None = Field(default=None, validation_alias="PWC_GENAI_BASE_URL")
    pwc_genai_model: str | None = Field(default=None, validation_alias="PWC_GENAI_MODEL")


def load_settings() -> Settings:
    """Punto unico de carga de configuracion para CLI/API/pipeline."""
    return Settings()
