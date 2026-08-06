"""Harness hermetico reutilizable para ejecutar `run_ingestion` sin NUNCA
inicializar un proveedor LLM real ni cargar `.env` (Fase 15B2-A, cierre).

Defecto que este modulo corrige: un script ad-hoc de esta misma fase
(`run_six_packages.py`, ya eliminado) llamo `run_ingestion(settings=
load_settings())` sin construir `Settings` sinteticas ni deshabilitar la
carga de `.env` -- `load_settings()` resolvio un `LLM_PROVIDER` real
(desde `.env`, que `pydantic-settings` carga automaticamente salvo que se
deshabilite explicitamente) y 6 paquetes 100% sinteticos activaron
llamadas reales a un proveedor LLM. Ningun dato de cliente estuvo en
riesgo (el contenido era sintetico), pero fue una ejecucion no hermetica
inaceptable. Ver `tests/test_hermetic_harness_regression.py` para el test
que reproduce el defecto exacto y prueba esta correccion.

Cinco capas de defensa, cada una independiente (si una fallara, las
demas siguen protegiendo):

1. `build_hermetic_settings` construye `Settings(_env_file=None, ...)`
   -- `_env_file=None` es el mecanismo REAL de `pydantic-settings` para
   deshabilitar la carga de cualquier `.env` en esa instancia especifica,
   sin importar que archivo exista en el CWD/raiz del repo.
2. El perfil LLM resuelto (`OPENAI_BASE_URL`/`OPENAI_API_KEY`/
   `OPENAI_MODEL`) usa un host `.invalid` (RFC 2606: TLD reservado para
   NUNCA resolver via DNS) -- incluso si las capas 3-5 fallaran, cualquier
   intento de red real fallaria de inmediato en resolucion DNS.
3. `hermetic_llm_and_network_guard()` reemplaza `OpenAICompatibleChatClient`
   en AMBOS modulos reales que lo importan (`rule_drafts_generated_stage`/
   `guardrails_applied_stage`) por `_DeterministicFakeClient`, y ADEMAS
   reemplaza la clase real en `llm_client` con un stub que lanza si
   alguna vez se instancia (nunca deberia alcanzarse si 1-2 estan bien).
4. `socket.create_connection`/`socket.socket.connect` quedan bloqueados
   para cualquier destino que no sea el Neo4j hermetico o localhost --
   defensa a nivel de proceso Python.
5. El contenedor runner que ejecuta este harness esta conectado
   EXCLUSIVAMENTE a una red Docker `--internal` (sin ruta de salida a
   Internet en absoluto) -- defensa a nivel de infraestructura, cubre
   inclusive subprocesos (p. ej. el JAR de ProLeap) que un patch de
   `socket` en Python no alcanza."""

from __future__ import annotations

import contextlib
import json
import socket as socket_module
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest import mock

from altamira_extractor.config import Settings
from altamira_extractor.contracts.context_package import ContextPackage
from altamira_extractor.pipeline import (
    guardrails_applied_stage,
    llm_client,
    rule_drafts_generated_stage,
)
from altamira_extractor.pipeline.evidence_catalog import build_evidence_catalog

_JSON_DECODER = json.JSONDecoder()

FAKE_LLM_BASE_URL = "http://llm-fake.invalid.test/v1"
FAKE_LLM_API_KEY = "fake-hermetic-key-never-real"
FAKE_LLM_MODEL = "fake-hermetic-model"

# Sustrings de host permitidos para socket.create_connection -- el Neo4j
# hermetico (nombre de contenedor variable por corrida, se pasa
# explicitamente) mas localhost/loopback para uso interno de librerias.
_DEFAULT_ALLOWED_HOST_SUBSTRINGS: tuple[str, ...] = ("127.0.0.1", "localhost", "::1")


class HermeticNetworkViolation(RuntimeError):
    """Se lanza cuando el harness hermetico detecta un intento de
    conexion de socket o de instanciacion del cliente LLM real fuera de
    lo explicitamente permitido. Nunca se atrapa silenciosamente en
    ningun llamador de este modulo."""


def build_hermetic_settings(tmp_path: Path, **overrides: Any) -> Settings:
    """Construye `Settings` 100% sinteticas: `_env_file=None` explicito
    (nunca carga `.env`, sin importar cual exista en el CWD/raiz del
    repo), `data_dir`/`runs_dir`/`incoming_dir` bajo `tmp_path` (nunca
    reutiliza un data root existente), y un perfil LLM que apunta
    exclusivamente al sentinel `.invalid` (nunca un proveedor real)."""
    defaults: dict[str, Any] = {
        "data_dir": tmp_path / "data",
        "runs_dir": tmp_path / "data" / "runs",
        "incoming_dir": tmp_path / "data" / "incoming",
        "LLM_PROVIDER": "openai",
        "OPENAI_BASE_URL": FAKE_LLM_BASE_URL,
        "OPENAI_API_KEY": FAKE_LLM_API_KEY,
        "OPENAI_MODEL": FAKE_LLM_MODEL,
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[arg-type, call-arg]


def _extract_context_package_json(message_content: str) -> dict[str, Any]:
    """Extrae el primer objeto JSON valido embebido en un mensaje de
    usuario ya renderizado (el ContextPackage real). Misma logica que
    `tests/e2e_support.py::extract_context_package_json`, no reimportada
    para no acoplar este modulo (usable fuera de pytest) a un modulo de
    test que si depende de `pytest`."""
    start = message_content.index("{")
    obj, _ = _JSON_DECODER.raw_decode(message_content, start)
    return obj  # type: ignore[no-any-return]


def _build_generic_valid_payload(context_package_dict: dict[str, Any]) -> dict[str, Any]:
    """Construye un `RuleDraft` payload fake pero estructuralmente
    valido para CUALQUIER candidato real (no un fixture fijo): lee el
    `ContextPackage` real recibido en el prompt, reconstruye su
    `EvidenceCatalog` con la MISMA funcion de produccion
    (`build_evidence_catalog`) y arma `claims[].evidence_refs` con
    alias reales de ESA corrida -- nunca un `evidence_id`/
    `evidence_path` inventado. Un candidato real siempre tiene al menos
    una evidencia (la propia Decision), asi que el catalogo nunca esta
    vacio."""
    package = ContextPackage.model_validate(context_package_dict)
    catalog = build_evidence_catalog(package)
    first_alias = catalog.entries[0].alias
    second_alias = catalog.entries[1].alias if len(catalog.entries) > 1 else first_alias
    first_evidence_id = context_package_dict["evidence"][0]["evidence_id"]
    return {
        "title": "FAKE_HERMETIC_TITLE",
        "context": "FAKE_HERMETIC_CONTEXT",
        "statement": "FAKE_HERMETIC_STATEMENT",
        "condition": "FAKE_HERMETIC_CONDITION",
        "parameters": [],
        "effect": "FAKE_HERMETIC_EFFECT",
        "parameter_source": None,
        "traceability": [first_evidence_id],
        "limitations": ["Requiere revision funcional (harness hermetico Fase 15B2-A)"],
        "claims": [
            {"claim_id": "c1", "field": "condition", "evidence_refs": [first_alias]},
            {"claim_id": "c2", "field": "effect", "evidence_refs": [second_alias]},
        ],
    }


class _DeterministicFakeClient:
    """Cliente fake determinístico: nunca abre un socket, siempre
    devuelve un payload FIJO y reconocible como fake (nunca contenido
    variable que pudiera confundirse con una respuesta real). Valida en
    `__init__` que el perfil recibido use el sentinel `.invalid` -- una
    Settings mal construida (apuntando a un proveedor real por error)
    hace que el fake mismo rechace instanciarse."""

    call_count = 0

    def __init__(self, profile: Any, **kwargs: Any) -> None:
        self.profile = profile
        if FAKE_LLM_BASE_URL not in profile.base_url:
            raise HermeticNetworkViolation(
                f"perfil LLM inesperado en el fake client: base_url={profile.base_url!r} "
                "(el harness hermetico exige el sentinel .invalid; una Settings real se "
                "coló antes de instalar el fake)"
            )

    async def __aenter__(self) -> _DeterministicFakeClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def complete(self, messages: list[Any]) -> dict[str, Any]:
        type(self).call_count += 1
        user_message = next(message for message in messages if message.role == "user")
        context_package_dict = _extract_context_package_json(user_message.content)
        return _build_generic_valid_payload(context_package_dict)


class _RealClientBlocked:
    """Reemplazo del `OpenAICompatibleChatClient` REAL en el modulo
    `llm_client` mismo: si algun codigo (actual o futuro) llega a
    instanciarlo directamente en vez de via los dos modulos de etapa ya
    parcheados, falla de inmediato en vez de abrir una conexion real."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise HermeticNetworkViolation(
            "OpenAICompatibleChatClient REAL fue instanciado dentro del harness hermetico "
            "-- esto nunca deberia ocurrir; revisar que hermetic_llm_and_network_guard() "
            "siga cubriendo todos los sitios de construccion reales"
        )


def _make_guarded_create_connection(
    real_create_connection: Any, allowed_host_substrings: tuple[str, ...]
) -> Any:
    def _guarded(address: Any, *args: Any, **kwargs: Any) -> Any:
        host = address[0] if isinstance(address, tuple) else address
        if not any(allowed in str(host) for allowed in allowed_host_substrings):
            raise HermeticNetworkViolation(
                f"intento de conexion de socket externa BLOQUEADO a {host!r} "
                "(harness hermetico: solo Neo4j hermetico/localhost permitidos)"
            )
        return real_create_connection(address, *args, **kwargs)

    return _guarded


@contextlib.contextmanager
def hermetic_llm_and_network_guard(
    *, allowed_neo4j_host: str | None = None
) -> Iterator[type[_DeterministicFakeClient]]:
    """Instala las capas de defensa 3 y 4 (ver docstring del modulo).
    Restaura todo al salir, incluso si el bloque lanza. Devuelve la clase
    fake para que el llamador pueda inspeccionar `call_count` despues."""
    allowed = _DEFAULT_ALLOWED_HOST_SUBSTRINGS
    if allowed_neo4j_host:
        allowed = (*allowed, allowed_neo4j_host)

    _DeterministicFakeClient.call_count = 0
    real_create_connection = socket_module.create_connection
    guarded_create_connection = _make_guarded_create_connection(real_create_connection, allowed)

    with (
        mock.patch.object(
            rule_drafts_generated_stage,
            "OpenAICompatibleChatClient",
            _DeterministicFakeClient,
        ),
        mock.patch.object(
            guardrails_applied_stage, "OpenAICompatibleChatClient", _DeterministicFakeClient
        ),
        mock.patch.object(llm_client, "OpenAICompatibleChatClient", _RealClientBlocked),
        mock.patch.object(socket_module, "create_connection", guarded_create_connection),
    ):
        yield _DeterministicFakeClient
