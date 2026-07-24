"""Tests del cliente HTTP OpenAI-compatible (Prompt 11).

Todo con `httpx.MockTransport`: ninguna llamada de red real (CLAUDE.md,
testing.md). No hay marcador `llm_integration`: Prompt 11 se valida
completamente con dobles locales.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from pydantic import ValidationError as PydanticValidationError

from altamira_extractor.config import Settings
from altamira_extractor.contracts.enums import LlmProvider
from altamira_extractor.pipeline.errors import (
    LlmAuthenticationError,
    LlmConfigurationError,
    LlmRateLimitError,
    LlmRequestError,
    LlmResponseFormatError,
    LlmResponseParsingError,
    LlmTimeoutError,
    LlmUnavailableError,
)
from altamira_extractor.pipeline.llm_client import (
    ChatMessage,
    LlmProfile,
    OpenAICompatibleChatClient,
    _compute_backoff_seconds,
    parse_strict_json_object,
    resolve_llm_profile,
)

Handler = Callable[[httpx.Request], httpx.Response]

_LOGGER_NAME = "altamira_extractor.pipeline.llm_client"


def _profile(**overrides: Any) -> LlmProfile:
    defaults: dict[str, Any] = {
        "provider": LlmProvider.OPENAI,
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-test-key",
        "model": "gpt-4o-test",
        "timeout_seconds": 5.0,
        "http_retries": 2,
    }
    defaults.update(overrides)
    return LlmProfile(**defaults)


def _openai_settings(**overrides: Any) -> Settings:
    # Settings usa validation_alias en todos los campos LLM_*/OPENAI_*/
    # PWC_GENAI_* (igual que NEO4J_*): sin populate_by_name=True, hay que
    # construirlos por su alias, nunca por el nombre de campo Python.
    defaults: dict[str, Any] = {
        "LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": "sk-test-key",
        "OPENAI_BASE_URL": "https://api.openai.com/v1",
        "OPENAI_MODEL": "gpt-4o-test",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _envelope(content: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": "chatcmpl-test",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    payload.update(extra)
    return payload


def _messages() -> list[ChatMessage]:
    return [
        ChatMessage(role="system", content="eres un analista"),
        ChatMessage(role="user", content="genera el borrador"),
    ]


class _RecordingSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _counting_handler(
    build: Callable[[httpx.Request], httpx.Response],
) -> tuple[Handler, list[httpx.Request]]:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return build(request)

    return handler, calls


def _always_raising_handler(
    build_exc: Callable[[httpx.Request], BaseException],
) -> tuple[Handler, list[httpx.Request]]:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise build_exc(request)

    return handler, calls


def _sequence_handler(
    items: list[httpx.Response | BaseException],
) -> tuple[Handler, list[httpx.Request]]:
    calls: list[httpx.Request] = []
    iterator = iter(items)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        item = next(iterator)
        if isinstance(item, BaseException):
            raise item
        return item

    return handler, calls


def _client_for(
    profile: LlmProfile,
    handler: Handler,
    *,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> OpenAICompatibleChatClient:
    transport = httpx.MockTransport(handler)
    return OpenAICompatibleChatClient(
        profile, transport=transport, sleep=sleep or _RecordingSleep()
    )


# --- Configuracion --------------------------------------------------------


def test_resolve_profile_requires_provider() -> None:
    settings = Settings()
    with pytest.raises(LlmConfigurationError):
        resolve_llm_profile(settings)


def test_resolve_profile_rejects_invalid_provider() -> None:
    settings = _openai_settings(LLM_PROVIDER="anthropic")
    with pytest.raises(LlmConfigurationError):
        resolve_llm_profile(settings)


def test_resolve_profile_requires_openai_credentials() -> None:
    settings = Settings(LLM_PROVIDER="openai")
    with pytest.raises(LlmConfigurationError):
        resolve_llm_profile(settings)


def test_resolve_profile_requires_pwc_credentials() -> None:
    settings = Settings(LLM_PROVIDER="pwc_gateway")
    with pytest.raises(LlmConfigurationError):
        resolve_llm_profile(settings)


def test_resolve_profile_only_requires_selected_provider() -> None:
    settings = _openai_settings()
    profile = resolve_llm_profile(settings)
    assert profile.provider == LlmProvider.OPENAI
    # Nunca exige simultaneamente las credenciales de pwc_gateway.
    assert settings.pwc_genai_api_key is None


def test_resolve_profile_pwc_gateway_only_requires_pwc_credentials() -> None:
    settings = Settings(
        LLM_PROVIDER="pwc_gateway",
        PWC_GENAI_API_KEY="pwc-secret",
        PWC_GENAI_BASE_URL="https://gateway.internal/v1",
        PWC_GENAI_MODEL="openai.gpt-4o",
    )
    profile = resolve_llm_profile(settings)
    assert profile.provider == LlmProvider.PWC_GATEWAY
    assert settings.openai_api_key is None


def test_settings_construction_never_requires_llm_config() -> None:
    settings = Settings()
    assert settings.llm_provider is None


def test_profile_rejects_invalid_base_url() -> None:
    with pytest.raises(PydanticValidationError):
        _profile(base_url="not-a-url")


def test_resolve_profile_rejects_invalid_base_url() -> None:
    settings = _openai_settings(OPENAI_BASE_URL="ftp://example.com")
    with pytest.raises(LlmConfigurationError):
        resolve_llm_profile(settings)


def test_profile_rejects_base_url_with_userinfo() -> None:
    with pytest.raises(PydanticValidationError):
        _profile(base_url="https://user:pass@api.openai.com/v1")


def test_resolve_profile_rejects_base_url_with_userinfo() -> None:
    settings = _openai_settings(OPENAI_BASE_URL="https://user:pass@api.openai.com/v1")
    with pytest.raises(LlmConfigurationError):
        resolve_llm_profile(settings)


def test_profile_rejects_base_url_that_is_full_endpoint() -> None:
    with pytest.raises(PydanticValidationError):
        _profile(base_url="https://api.openai.com/v1/chat/completions")


def test_resolve_profile_rejects_empty_model() -> None:
    settings = _openai_settings(OPENAI_MODEL="")
    with pytest.raises(LlmConfigurationError):
        resolve_llm_profile(settings)


def test_resolve_profile_rejects_non_positive_timeout() -> None:
    settings = _openai_settings(LLM_TIMEOUT_SECONDS=0)
    with pytest.raises(LlmConfigurationError):
        resolve_llm_profile(settings)


def test_resolve_profile_rejects_negative_retries() -> None:
    settings = _openai_settings(LLM_HTTP_RETRIES=-1)
    with pytest.raises(LlmConfigurationError):
        resolve_llm_profile(settings)


def test_resolve_profile_rejects_retries_above_five() -> None:
    settings = _openai_settings(LLM_HTTP_RETRIES=6)
    with pytest.raises(LlmConfigurationError):
        resolve_llm_profile(settings)


def test_settings_rejects_temperature_other_than_zero() -> None:
    with pytest.raises(PydanticValidationError):
        Settings(LLM_TEMPERATURE=1)


def test_api_key_never_appears_in_profile_repr_or_str() -> None:
    profile = _profile(api_key="sk-super-secret-value")
    assert "sk-super-secret-value" not in repr(profile)
    assert "sk-super-secret-value" not in str(profile)


def test_api_key_never_appears_in_configuration_error() -> None:
    settings = _openai_settings(OPENAI_API_KEY=None)
    with pytest.raises(LlmConfigurationError) as excinfo:
        resolve_llm_profile(settings)
    assert "sk-test-key" not in str(excinfo.value)


# --- Ciclo de vida / request -----------------------------------------------


async def test_request_hits_exact_chat_completions_endpoint() -> None:
    handler, calls = _counting_handler(lambda req: httpx.Response(200, json=_envelope("{}")))
    async with _client_for(_profile(base_url="https://api.openai.com/v1"), handler) as client:
        await client.complete(_messages())
    assert str(calls[0].url) == "https://api.openai.com/v1/chat/completions"


async def test_request_headers_are_correct() -> None:
    handler, calls = _counting_handler(lambda req: httpx.Response(200, json=_envelope("{}")))
    async with _client_for(_profile(api_key="sk-abc123"), handler) as client:
        await client.complete(_messages())
    headers = calls[0].headers
    assert headers["authorization"] == "Bearer sk-abc123"
    assert headers["accept"] == "application/json"
    assert headers["content-type"] == "application/json"


async def test_request_body_has_model_messages_and_temperature() -> None:
    handler, calls = _counting_handler(lambda req: httpx.Response(200, json=_envelope("{}")))
    async with _client_for(_profile(model="gpt-4o-mini"), handler) as client:
        await client.complete(_messages())
    body = json.loads(calls[0].content)
    assert body["model"] == "gpt-4o-mini"
    assert body["temperature"] == 0
    assert body["messages"] == [
        {"role": "system", "content": "eres un analista"},
        {"role": "user", "content": "genera el borrador"},
    ]


async def test_openai_profile_completes_successfully() -> None:
    handler, _ = _counting_handler(lambda req: httpx.Response(200, json=_envelope('{"ok": true}')))
    async with _client_for(_profile(provider=LlmProvider.OPENAI), handler) as client:
        result = await client.complete(_messages())
    assert result == {"ok": True}


async def test_pwc_gateway_profile_completes_successfully() -> None:
    profile = _profile(
        provider=LlmProvider.PWC_GATEWAY,
        base_url="https://gateway.internal/v1",
        api_key="pwc-secret",
        model="openai.gpt-4o",
    )
    handler, calls = _counting_handler(
        lambda req: httpx.Response(200, json=_envelope('{"ok": true}'))
    )
    async with _client_for(profile, handler) as client:
        result = await client.complete(_messages())
    assert result == {"ok": True}
    assert str(calls[0].url) == "https://gateway.internal/v1/chat/completions"


async def test_single_async_client_instance_is_reused_across_calls() -> None:
    handler, calls = _counting_handler(lambda req: httpx.Response(200, json=_envelope("{}")))
    injected = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = OpenAICompatibleChatClient(_profile(), client=injected, sleep=_RecordingSleep())
    try:
        await client.complete(_messages())
        await client.complete(_messages())
    finally:
        await injected.aclose()
    assert len(calls) == 2
    assert client._client is injected  # type: ignore[attr-defined]


async def test_injected_client_is_never_closed_automatically() -> None:
    handler, _ = _counting_handler(lambda req: httpx.Response(200, json=_envelope("{}")))
    injected = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = OpenAICompatibleChatClient(_profile(), client=injected, sleep=_RecordingSleep())
    async with client:
        await client.complete(_messages())
    assert injected.is_closed is False
    await injected.aclose()


async def test_owned_client_is_closed_on_context_exit() -> None:
    handler, _ = _counting_handler(lambda req: httpx.Response(200, json=_envelope("{}")))
    transport = httpx.MockTransport(handler)
    client = OpenAICompatibleChatClient(_profile(), transport=transport, sleep=_RecordingSleep())
    async with client:
        await client.complete(_messages())
    assert client._client.is_closed is True  # type: ignore[attr-defined]


# --- Respuesta ---------------------------------------------------------


async def test_valid_json_object_content_is_returned() -> None:
    handler, _ = _counting_handler(
        lambda req: httpx.Response(200, json=_envelope('{"a": 1, "b": [1, 2]}'))
    )
    async with _client_for(_profile(), handler) as client:
        result = await client.complete(_messages())
    assert result == {"a": 1, "b": [1, 2]}


async def test_empty_choices_raises_response_format_error() -> None:
    empty_envelope = _envelope("{}") | {"choices": []}
    handler, calls = _counting_handler(lambda req: httpx.Response(200, json=empty_envelope))
    async with _client_for(_profile(), handler) as client:
        with pytest.raises(LlmResponseFormatError):
            await client.complete(_messages())
    assert len(calls) == 1


async def test_missing_message_raises_response_format_error() -> None:
    envelope = {"id": "x", "choices": [{"index": 0}]}
    handler, calls = _counting_handler(lambda req: httpx.Response(200, json=envelope))
    async with _client_for(_profile(), handler) as client:
        with pytest.raises(LlmResponseFormatError):
            await client.complete(_messages())
    assert len(calls) == 1


async def test_missing_content_raises_response_format_error() -> None:
    envelope = {"id": "x", "choices": [{"message": {"role": "assistant"}}]}
    handler, calls = _counting_handler(lambda req: httpx.Response(200, json=envelope))
    async with _client_for(_profile(), handler) as client:
        with pytest.raises(LlmResponseFormatError):
            await client.complete(_messages())
    assert len(calls) == 1


async def test_non_string_content_raises_response_format_error() -> None:
    envelope = {"id": "x", "choices": [{"message": {"content": 123}}]}
    handler, calls = _counting_handler(lambda req: httpx.Response(200, json=envelope))
    async with _client_for(_profile(), handler) as client:
        with pytest.raises(LlmResponseFormatError):
            await client.complete(_messages())
    assert len(calls) == 1


async def test_extra_envelope_fields_are_accepted() -> None:
    handler, _ = _counting_handler(
        lambda req: httpx.Response(
            200, json=_envelope('{"ok": true}', system_fingerprint="fp_123", created=123456)
        )
    )
    async with _client_for(_profile(), handler) as client:
        result = await client.complete(_messages())
    assert result == {"ok": True}


async def test_json_array_root_is_rejected() -> None:
    handler, calls = _counting_handler(lambda req: httpx.Response(200, json=_envelope("[1, 2, 3]")))
    async with _client_for(_profile(), handler) as client:
        with pytest.raises(LlmResponseParsingError):
            await client.complete(_messages())
    assert len(calls) == 1


@pytest.mark.parametrize("scalar", ['"a string"', "42", "true", "null"])
async def test_json_scalar_root_is_rejected(scalar: str) -> None:
    handler, calls = _counting_handler(lambda req: httpx.Response(200, json=_envelope(scalar)))
    async with _client_for(_profile(), handler) as client:
        with pytest.raises(LlmResponseParsingError):
            await client.complete(_messages())
    assert len(calls) == 1


async def test_extra_text_around_json_is_rejected() -> None:
    handler, calls = _counting_handler(
        lambda req: httpx.Response(200, json=_envelope('here is your json: {"a": 1}'))
    )
    async with _client_for(_profile(), handler) as client:
        with pytest.raises(LlmResponseParsingError):
            await client.complete(_messages())
    assert len(calls) == 1


async def test_markdown_fences_are_rejected_not_stripped() -> None:
    handler, calls = _counting_handler(
        lambda req: httpx.Response(200, json=_envelope('```json\n{"a": 1}\n```'))
    )
    async with _client_for(_profile(), handler) as client:
        with pytest.raises(LlmResponseParsingError):
            await client.complete(_messages())
    assert len(calls) == 1


def test_duplicate_keys_are_rejected() -> None:
    with pytest.raises(LlmResponseParsingError):
        parse_strict_json_object('{"a": 1, "a": 2}')


@pytest.mark.parametrize("content", ['{"a": NaN}', '{"a": Infinity}', '{"a": -Infinity}'])
def test_nan_and_infinity_are_rejected(content: str) -> None:
    with pytest.raises(LlmResponseParsingError):
        parse_strict_json_object(content)


def test_strict_parser_strips_only_external_whitespace() -> None:
    result = parse_strict_json_object('  \n {"a": " b "}  \n')
    assert result == {"a": " b "}


# --- Reintentos -------------------------------------------------------


async def test_429_is_retried_and_eventually_succeeds() -> None:
    handler, calls = _sequence_handler(
        [httpx.Response(429), httpx.Response(200, json=_envelope('{"ok": true}'))]
    )
    async with _client_for(_profile(http_retries=1), handler) as client:
        result = await client.complete(_messages())
    assert result == {"ok": True}
    assert len(calls) == 2


@pytest.mark.parametrize("status", [502, 503, 504])
async def test_5xx_retryable_statuses_are_retried(status: int) -> None:
    handler, calls = _sequence_handler(
        [httpx.Response(status), httpx.Response(200, json=_envelope('{"ok": true}'))]
    )
    async with _client_for(_profile(http_retries=1), handler) as client:
        result = await client.complete(_messages())
    assert result == {"ok": True}
    assert len(calls) == 2


async def test_connect_timeout_is_retried() -> None:
    def build(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_envelope('{"ok": true}'))

    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectTimeout("timeout", request=request)
        return build(request)

    async with _client_for(_profile(http_retries=1), handler) as client:
        result = await client.complete(_messages())
    assert result == {"ok": True}
    assert len(calls) == 2


async def test_connect_error_is_retried() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json=_envelope('{"ok": true}'))

    async with _client_for(_profile(http_retries=1), handler) as client:
        result = await client.complete(_messages())
    assert result == {"ok": True}
    assert len(calls) == 2


async def test_pool_timeout_is_retried() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            raise httpx.PoolTimeout("boom", request=request)
        return httpx.Response(200, json=_envelope('{"ok": true}'))

    async with _client_for(_profile(http_retries=1), handler) as client:
        result = await client.complete(_messages())
    assert result == {"ok": True}
    assert len(calls) == 2


async def test_retry_after_numeric_header_is_honored() -> None:
    handler, _ = _sequence_handler(
        [
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json=_envelope('{"ok": true}')),
        ]
    )
    sleeper = _RecordingSleep()
    async with _client_for(_profile(http_retries=1), handler, sleep=sleeper) as client:
        await client.complete(_messages())
    assert sleeper.calls == [7.0]


async def test_non_numeric_retry_after_falls_back_to_exponential_backoff() -> None:
    handler, _ = _sequence_handler(
        [
            httpx.Response(429, headers={"Retry-After": "not-a-number"}),
            httpx.Response(200, json=_envelope('{"ok": true}')),
        ]
    )
    sleeper = _RecordingSleep()
    async with _client_for(_profile(http_retries=1), handler, sleep=sleeper) as client:
        await client.complete(_messages())
    assert sleeper.calls == [1.0]


def test_backoff_is_bounded_and_exponential_without_jitter() -> None:
    assert _compute_backoff_seconds(1, None) == 1.0
    assert _compute_backoff_seconds(2, None) == 2.0
    assert _compute_backoff_seconds(3, None) == 4.0
    assert _compute_backoff_seconds(10, None) == 30.0


def test_backoff_caps_retry_after_too() -> None:
    assert _compute_backoff_seconds(1, 999.0) == 30.0


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, 500])
async def test_non_retryable_statuses_are_not_retried(status: int) -> None:
    handler, calls = _counting_handler(lambda req: httpx.Response(status))
    expected = LlmAuthenticationError if status in (401, 403) else LlmRequestError
    async with _client_for(_profile(http_retries=3), handler) as client:
        with pytest.raises(expected):
            await client.complete(_messages())
    assert len(calls) == 1


async def test_read_timeout_is_not_retried() -> None:
    handler, calls = _always_raising_handler(lambda req: httpx.ReadTimeout("boom", request=req))
    async with _client_for(_profile(http_retries=3), handler) as client:
        with pytest.raises(LlmTimeoutError):
            await client.complete(_messages())
    assert len(calls) == 1


async def test_write_timeout_is_not_retried() -> None:
    handler, calls = _always_raising_handler(lambda req: httpx.WriteTimeout("boom", request=req))
    async with _client_for(_profile(http_retries=3), handler) as client:
        with pytest.raises(LlmTimeoutError):
            await client.complete(_messages())
    assert len(calls) == 1


async def test_2xx_with_invalid_json_content_is_not_retried() -> None:
    handler, calls = _counting_handler(lambda req: httpx.Response(200, json=_envelope("not json")))
    async with _client_for(_profile(http_retries=3), handler) as client:
        with pytest.raises(LlmResponseParsingError):
            await client.complete(_messages())
    assert len(calls) == 1


async def test_429_exhaustion_raises_rate_limit_error() -> None:
    handler, calls = _counting_handler(lambda req: httpx.Response(429))
    async with _client_for(_profile(http_retries=2), handler) as client:
        with pytest.raises(LlmRateLimitError):
            await client.complete(_messages())
    assert len(calls) == 3


async def test_5xx_exhaustion_raises_unavailable_error() -> None:
    handler, calls = _counting_handler(lambda req: httpx.Response(503))
    async with _client_for(_profile(http_retries=1), handler) as client:
        with pytest.raises(LlmUnavailableError):
            await client.complete(_messages())
    assert len(calls) == 2


async def test_connect_timeout_exhaustion_raises_timeout_error() -> None:
    handler, calls = _always_raising_handler(lambda req: httpx.ConnectTimeout("boom", request=req))
    async with _client_for(_profile(http_retries=1), handler) as client:
        with pytest.raises(LlmTimeoutError):
            await client.complete(_messages())
    assert len(calls) == 2


async def test_connect_error_exhaustion_raises_unavailable_error() -> None:
    handler, calls = _always_raising_handler(lambda req: httpx.ConnectError("boom", request=req))
    async with _client_for(_profile(http_retries=1), handler) as client:
        with pytest.raises(LlmUnavailableError):
            await client.complete(_messages())
    assert len(calls) == 2


async def test_remote_protocol_error_is_not_retried() -> None:
    handler, calls = _always_raising_handler(
        lambda req: httpx.RemoteProtocolError("boom", request=req)
    )
    async with _client_for(_profile(http_retries=3), handler) as client:
        with pytest.raises(LlmRequestError):
            await client.complete(_messages())
    assert len(calls) == 1


async def test_zero_retries_means_single_attempt() -> None:
    handler, calls = _counting_handler(lambda req: httpx.Response(429))
    async with _client_for(_profile(http_retries=0), handler) as client:
        with pytest.raises(LlmRateLimitError):
            await client.complete(_messages())
    assert len(calls) == 1


# --- Seguridad / logging ------------------------------------------------


async def test_api_key_and_authorization_never_appear_in_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    handler, _ = _counting_handler(
        lambda req: httpx.Response(200, json=_envelope('{"ok": true}'))
    )
    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        async with _client_for(_profile(api_key="sk-should-not-leak"), handler) as client:
            await client.complete(_messages())
    log_text = "\n".join(record.getMessage() + repr(record.__dict__) for record in caplog.records)
    assert "sk-should-not-leak" not in log_text
    assert "Bearer" not in log_text


async def test_message_content_never_appears_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    handler, _ = _counting_handler(lambda req: httpx.Response(200, json=_envelope('{"ok": true}')))
    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        async with _client_for(_profile(), handler) as client:
            sensitive = ChatMessage(role="user", content="informacion-muy-sensible-unica")
            await client.complete([sensitive])
    log_text = "\n".join(record.getMessage() + repr(record.__dict__) for record in caplog.records)
    assert "informacion-muy-sensible-unica" not in log_text


async def test_response_body_never_appears_in_error_message() -> None:
    handler, _ = _counting_handler(
        lambda req: httpx.Response(500, json={"error": {"message": "MARCADOR_SECRETO_DEL_BODY"}})
    )
    async with _client_for(_profile(), handler) as client:
        with pytest.raises(LlmRequestError) as excinfo:
            await client.complete(_messages())
    assert "MARCADOR_SECRETO_DEL_BODY" not in str(excinfo.value)


def test_userinfo_in_base_url_is_rejected_before_any_request() -> None:
    # La validacion ocurre en LlmProfile.__init__: si falla, ni siquiera
    # existe un LlmProfile con el que construir un cliente, por lo que
    # ningun request puede llegar a ejecutarse.
    with pytest.raises(PydanticValidationError):
        _profile(base_url="https://user:pass@api.openai.com/v1")
