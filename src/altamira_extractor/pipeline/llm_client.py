"""Cliente HTTP asincrono OpenAI-compatible (Prompt 11).

Una sola implementacion de transporte (`OpenAICompatibleChatClient`) para
los dos proveedores soportados (`LlmProvider.OPENAI`/`PWC_GATEWAY`): la
diferencia entre ambos se limita al `LlmProfile` resuelto
(base_url/api_key/model), nunca a una subclase por proveedor.

Alcance deliberadamente angosto (autorizacion de Prompt 11): este modulo
NO lee ContextPackage, NO integra prompts/, NO construye RuleDraft, NO
valida contra rule-draft.schema.json y NO implementa guardrails ni el
repair loop. `complete()` recibe mensajes ya ensamblados por el llamador
y devuelve exactamente el objeto JSON que el modelo escribio, sin
completar campos ni conocer su significado funcional. Todo eso es
responsabilidad de Prompt 12.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from types import TracebackType
from typing import Any, Literal, Self
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

from ..config import Settings
from ..contracts.enums import LlmProvider
from .errors import (
    LlmAuthenticationError,
    LlmConfigurationError,
    LlmRateLimitError,
    LlmRequestError,
    LlmResponseFormatError,
    LlmResponseParsingError,
    LlmTimeoutError,
    LlmUnavailableError,
)

logger = logging.getLogger("altamira_extractor.pipeline.llm_client")

_MIN_HTTP_RETRIES = 0
_MAX_HTTP_RETRIES = 5
_BASE_BACKOFF_SECONDS = 1.0
_MAX_BACKOFF_SECONDS = 30.0
_RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})


class ChatMessage(BaseModel):
    """Un mensaje de la conversacion enviada al modelo.

    Modelo interno cerrado, no persistido: vive unicamente en memoria
    durante una llamada a `complete()`. Nunca se exporta como contrato de
    dominio ni tiene JSON Schema propio (autorizacion de Prompt 11)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1)


class LlmProfile(BaseModel):
    """Perfil resuelto (proveedor + base_url + api_key + model + limites)
    con el que se construye un `OpenAICompatibleChatClient`.

    Modelo interno cerrado, no persistido, igual que `ChatMessage`."""

    model_config = ConfigDict(extra="forbid")

    provider: LlmProvider
    base_url: str
    api_key: SecretStr
    model: str = Field(min_length=1)
    timeout_seconds: float = Field(gt=0)
    http_retries: int = Field(ge=_MIN_HTTP_RETRIES, le=_MAX_HTTP_RETRIES)
    temperature: Literal[0] = 0

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("base_url no puede estar vacia")
        parts = urlsplit(stripped)
        if parts.scheme not in ("http", "https"):
            raise ValueError("base_url debe usar esquema http o https")
        if not parts.netloc:
            raise ValueError("base_url debe incluir un host")
        if "@" in parts.netloc:
            raise ValueError("base_url no puede contener userinfo")
        if stripped.rstrip("/").lower().endswith("/chat/completions"):
            raise ValueError(
                "base_url debe ser el prefijo del API, no el endpoint completo "
                "/chat/completions"
            )
        return stripped

    @field_validator("api_key")
    @classmethod
    def _validate_api_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("api_key no puede estar vacia")
        return value


def resolve_llm_profile(settings: Settings) -> LlmProfile:
    """Resuelve y valida el perfil LLM activo desde `Settings`.

    Unico punto donde la configuracion LLM se vuelve obligatoria:
    `Settings()` en si misma nunca exige proveedor/credenciales (las
    etapas deterministicas del pipeline no dependen de esto); llamar esta
    funcion es lo que activa esa exigencia, y solo para el proveedor
    efectivamente seleccionado (nunca se exigen ambos perfiles a la vez).
    """
    provider_raw = settings.llm_provider
    if not provider_raw:
        raise LlmConfigurationError("LLM_PROVIDER no esta definido")
    try:
        provider = LlmProvider(provider_raw)
    except ValueError as exc:
        raise LlmConfigurationError(
            "LLM_PROVIDER invalido: se esperaba 'openai' o 'pwc_gateway'"
        ) from exc

    if provider is LlmProvider.OPENAI:
        base_url = settings.openai_base_url
        api_key = settings.openai_api_key
        model = settings.openai_model
    else:
        base_url = settings.pwc_genai_base_url
        api_key = settings.pwc_genai_api_key
        model = settings.pwc_genai_model

    if not base_url:
        raise LlmConfigurationError(f"falta base_url del proveedor seleccionado ({provider.value})")
    if api_key is None or not api_key.get_secret_value().strip():
        raise LlmConfigurationError(f"falta api_key del proveedor seleccionado ({provider.value})")
    if not model:
        raise LlmConfigurationError(f"falta model del proveedor seleccionado ({provider.value})")
    if settings.llm_timeout_seconds <= 0:
        raise LlmConfigurationError("llm_timeout_seconds debe ser positivo")
    if not (_MIN_HTTP_RETRIES <= settings.llm_http_retries <= _MAX_HTTP_RETRIES):
        raise LlmConfigurationError(
            f"llm_http_retries debe estar entre {_MIN_HTTP_RETRIES} y {_MAX_HTTP_RETRIES}"
        )

    try:
        return LlmProfile(
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=settings.llm_timeout_seconds,
            http_retries=settings.llm_http_retries,
            temperature=settings.llm_temperature,
        )
    except ValidationError as exc:
        raise LlmConfigurationError(f"perfil LLM invalido ({provider.value})") from exc


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError("clave duplicada en el JSON del modelo")
        seen[key] = value
    return seen


def _reject_constant(constant: str) -> Any:
    raise ValueError("constante JSON no estrictamente valida (NaN/Infinity/-Infinity)")


def parse_strict_json_object(content: str) -> dict[str, Any]:
    """Parsea `content` COMPLETO (tras recortar unicamente whitespace
    externo) como un unico objeto JSON estricto.

    Nunca busca un fragmento JSON dentro de prosa, nunca limpia fences
    Markdown, nunca repara nada: cualquier desviacion (texto extra antes
    o despues, raiz que no es un objeto, claves duplicadas, NaN/
    Infinity/-Infinity) es un error terminal, visible para el repair loop
    de Prompt 12."""
    stripped = content.strip()
    if not stripped:
        raise LlmResponseParsingError("el contenido del modelo esta vacio")
    try:
        parsed = json.loads(
            stripped,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise LlmResponseParsingError(
            "el contenido del modelo no es un objeto JSON estrictamente valido"
        ) from exc
    if not isinstance(parsed, dict):
        raise LlmResponseParsingError(
            f"el contenido del modelo debe ser un objeto JSON, no {type(parsed).__name__}"
        )
    return parsed


def _extract_message_content(envelope: dict[str, Any]) -> str:
    """Valida la forma MINIMA del envelope 2xx. Los proveedores
    compatibles pueden agregar campos extra (`usage`, `id`, `created`,
    etc.) sin que eso sea un error: solo se exige `choices` no vacio y
    `choices[0].message.content` como string."""
    choices = envelope.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LlmResponseFormatError("el envelope no tiene 'choices' no vacio")
    first = choices[0]
    if not isinstance(first, dict):
        raise LlmResponseFormatError("choices[0] no es un objeto")
    message = first.get("message")
    if not isinstance(message, dict):
        raise LlmResponseFormatError("choices[0].message no es un objeto")
    content = message.get("content")
    if not isinstance(content, str):
        raise LlmResponseFormatError("choices[0].message.content no es un string")
    return content


def _parse_retry_after(raw: str | None) -> float | None:
    """Solo se respeta Retry-After cuando es un numero de segundos valido
    (no se interpreta el formato HTTP-date)."""
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if value < 0:
        return None
    return value


def _compute_backoff_seconds(attempt: int, retry_after: float | None) -> float:
    """Backoff exponencial deterministico, sin jitter, acotado a
    `_MAX_BACKOFF_SECONDS`. `attempt` es el numero de intento fallido ya
    realizado (1 = el primero)."""
    if retry_after is not None:
        return min(retry_after, _MAX_BACKOFF_SECONDS)
    # Base 2.0 (float), no int: `int ** int` con exponente no literal
    # tipa a `Any` en typeshed (el signo del exponente decide int/float
    # en runtime); `float ** int` siempre devuelve float sin ambiguedad.
    exponential = _BASE_BACKOFF_SECONDS * (2.0 ** (attempt - 1))
    return min(exponential, _MAX_BACKOFF_SECONDS)


class OpenAICompatibleChatClient:
    """Cliente HTTP asincrono OpenAI-compatible para `LlmProvider.OPENAI`
    y `LlmProvider.PWC_GATEWAY`.

    Reutiliza una unica instancia de `httpx.AsyncClient` durante toda su
    vida util (nunca crea un cliente nuevo por request). La propiedad del
    `httpx.AsyncClient` es explicita: si este objeto lo crea, lo cierra;
    si lo recibe inyectado, nunca lo cierra."""

    def __init__(
        self,
        profile: LlmProfile,
        *,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._profile = profile
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            transport=transport, timeout=profile.timeout_seconds
        )
        self._sleep = sleep or asyncio.sleep

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def complete(self, messages: Sequence[ChatMessage]) -> dict[str, Any]:
        """Ejecuta una unica llamada de chat completions y devuelve el
        contenido del modelo ya parseado como un objeto JSON estricto.

        Nunca devuelve RuleDraft, nunca completa statuses, nunca conoce
        ContextPackage: el `dict` devuelto es exactamente lo que el
        modelo escribio."""
        url = f"{self._profile.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._profile.api_key.get_secret_value()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {
            "model": self._profile.model,
            "messages": [message.model_dump() for message in messages],
            "temperature": self._profile.temperature,
        }

        max_attempts = self._profile.http_retries + 1
        attempt = 0
        while True:
            attempt += 1
            started_at = time.monotonic()
            try:
                response = await self._client.post(url, headers=headers, json=body)
            except (httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
                self._log_attempt(attempt, None, time.monotonic() - started_at, exc)
                if attempt < max_attempts:
                    await self._sleep(_compute_backoff_seconds(attempt, None))
                    continue
                raise LlmTimeoutError(
                    f"proveedor={self._profile.provider.value} tipo={type(exc).__name__} "
                    f"agotados {max_attempts} intentos"
                ) from exc
            except httpx.ConnectError as exc:
                self._log_attempt(attempt, None, time.monotonic() - started_at, exc)
                if attempt < max_attempts:
                    await self._sleep(_compute_backoff_seconds(attempt, None))
                    continue
                raise LlmUnavailableError(
                    f"proveedor={self._profile.provider.value} tipo=ConnectError "
                    f"agotados {max_attempts} intentos"
                ) from exc
            except (httpx.ReadTimeout, httpx.WriteTimeout) as exc:
                # Respuesta incierta: el proveedor pudo haber recibido o
                # procesado la solicitud. Nunca reintentable (evita
                # duplicar una generacion paga).
                self._log_attempt(attempt, None, time.monotonic() - started_at, exc)
                raise LlmTimeoutError(
                    f"proveedor={self._profile.provider.value} tipo={type(exc).__name__} "
                    "(respuesta incierta, no reintentable)"
                ) from exc
            except httpx.HTTPError as exc:
                # httpx.RemoteProtocolError y cualquier otro error de
                # transporte no cubierto explicitamente arriba: nunca
                # reintentable.
                self._log_attempt(attempt, None, time.monotonic() - started_at, exc)
                raise LlmRequestError(
                    f"proveedor={self._profile.provider.value} tipo={type(exc).__name__}"
                ) from exc

            duration = time.monotonic() - started_at
            status = response.status_code
            self._log_attempt(attempt, status, duration, None)

            if status in _RETRYABLE_STATUS_CODES:
                if attempt < max_attempts:
                    retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                    await self._sleep(_compute_backoff_seconds(attempt, retry_after))
                    continue
                if status == 429:
                    raise LlmRateLimitError(
                        f"proveedor={self._profile.provider.value} status=429 "
                        f"agotados {max_attempts} intentos"
                    )
                raise LlmUnavailableError(
                    f"proveedor={self._profile.provider.value} status={status} "
                    f"agotados {max_attempts} intentos"
                )

            if status in (401, 403):
                raise LlmAuthenticationError(
                    f"proveedor={self._profile.provider.value} status={status}"
                )

            if 200 <= status < 300:
                # Tras un 2xx nunca se reintenta: fallo terminal si el
                # envelope o el contenido no son validos (evita repetir
                # una generacion ya recibida).
                envelope = self._parse_envelope(response)
                content = _extract_message_content(envelope)
                return parse_strict_json_object(content)

            raise LlmRequestError(f"proveedor={self._profile.provider.value} status={status}")

    def _parse_envelope(self, response: httpx.Response) -> dict[str, Any]:
        try:
            envelope = response.json()
        except ValueError as exc:
            raise LlmResponseFormatError(
                f"proveedor={self._profile.provider.value}: el envelope HTTP no es JSON valido"
            ) from exc
        if not isinstance(envelope, dict):
            raise LlmResponseFormatError(
                f"proveedor={self._profile.provider.value}: el envelope HTTP no es un objeto"
            )
        return envelope

    def _log_attempt(
        self,
        attempt: int,
        status: int | None,
        duration: float,
        error: BaseException | None,
    ) -> None:
        """Registra unicamente: proveedor, modelo, numero de intento,
        status HTTP, duracion y clasificacion del error (nunca content de
        messages, JSON generado, prompt, contextos, headers ni
        secretos)."""
        logger.info(
            "llm_request",
            extra={
                "llm_provider": self._profile.provider.value,
                "llm_model": self._profile.model,
                "llm_attempt": attempt,
                "llm_status": status,
                "llm_duration_seconds": round(duration, 3),
                "llm_error_class": type(error).__name__ if error is not None else None,
            },
        )
