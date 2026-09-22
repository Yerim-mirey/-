"""Offline contract tests for the DeepSeek structured-output adapter.

No test here touches the network: every request is served by an injected
``httpx.MockTransport`` that records the exact payload the adapter sends.
"""

import json

import httpx
import pytest

from app.providers.deepseek import (
    API_KEY_ENV,
    DEFAULT_MODEL,
    DeepSeekLLM,
    LLMResponseError,
    LLMTransportError,
    api_key_from_env,
    render_system_prompt,
)

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}


def recording_client(*, status_code=200, body=None, raise_error=None, headers=None):
    """Return (client, requests) with a MockTransport that records each call."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if raise_error is not None:
            raise raise_error
        payload = body
        if payload is None:
            payload = {
                "model": DEFAULT_MODEL,
                "choices": [{"message": {"role": "assistant", "content": '{"answer": "ok"}'}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16},
            }
        return httpx.Response(status_code, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler), headers=headers), requests


def adapter(client, **kwargs) -> DeepSeekLLM:
    return DeepSeekLLM("test-key", client=client, **kwargs)


def sent_payload(requests: list[httpx.Request]) -> dict:
    assert len(requests) == 1
    return json.loads(requests[0].content.decode("utf-8"))


def test_request_carries_schema_instruction_and_json_mode():
    client, requests = recording_client(headers={"Authorization": "Bearer test-key"})
    result = adapter(client).generate_object(
        system_prompt="你是测试角色。",
        user_message="请回答",
        response_schema=SCHEMA,
    )

    assert result == {"answer": "ok"}
    request = requests[0]
    assert str(request.url) == "https://api.deepseek.com/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key"

    payload = sent_payload(requests)
    assert payload["model"] == DEFAULT_MODEL
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["stream"] is False
    assert payload["max_tokens"] > 0
    assert payload["messages"][1] == {"role": "user", "content": "请回答"}
    system = payload["messages"][0]["content"]
    assert system.startswith("你是测试角色。")
    assert "json" in system.lower(), "DeepSeek 的 JSON 模式要求提示词包含 json 字样"
    assert json.dumps(SCHEMA, ensure_ascii=False, indent=2) in system, "必须给出格式示例"
    assert adapter(client).last_usage is None


def test_client_and_environment_configuration(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://example.test/v1/")
    client, requests = recording_client()
    model = adapter(client)
    model.generate_object(system_prompt="角色", user_message="问题", response_schema=SCHEMA)

    assert model.model == "deepseek-v4-pro"
    assert str(requests[0].url) == "https://example.test/v1/chat/completions"
    assert model.last_usage["model"] == DEFAULT_MODEL
    assert model.last_usage["usage"]["total_tokens"] == 16


def test_api_key_comes_from_the_environment(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    assert api_key_from_env() is None
    monkeypatch.setenv(API_KEY_ENV, "   ")
    assert api_key_from_env() is None
    monkeypatch.setenv(API_KEY_ENV, "  sk-test  ")
    assert api_key_from_env() == "sk-test"


def test_missing_or_invalid_inputs_are_rejected_before_any_request():
    client, requests = recording_client()
    with pytest.raises(ValueError):
        DeepSeekLLM("   ")
    with pytest.raises(ValueError):
        adapter(client).generate_object(
            system_prompt="  ", user_message="问题", response_schema=SCHEMA
        )
    with pytest.raises(ValueError):
        adapter(client).generate_object(
            system_prompt="角色", user_message="  ", response_schema=SCHEMA
        )
    assert requests == []


def test_trailing_code_fence_is_tolerated():
    client, _ = recording_client(body={
        "model": DEFAULT_MODEL,
        "choices": [{"message": {"content": '```json\n{"answer": "fenced"}\n```'}}],
    })
    assert adapter(client).generate_object(
        system_prompt="角色", user_message="问题", response_schema=SCHEMA
    ) == {"answer": "fenced"}


def test_empty_content_is_a_response_error():
    """Documented JSON-mode behaviour: content may come back empty."""
    client, _ = recording_client(body={
        "model": DEFAULT_MODEL,
        "choices": [{"message": {"content": "   "}}],
    })
    with pytest.raises(LLMResponseError, match="empty content"):
        adapter(client).generate_object(
            system_prompt="角色", user_message="问题", response_schema=SCHEMA
        )


@pytest.mark.parametrize("content", ["not json at all", "[1, 2, 3]"])
def test_non_object_content_is_a_response_error(content):
    client, _ = recording_client(body={
        "model": DEFAULT_MODEL,
        "choices": [{"message": {"content": content}}],
    })
    with pytest.raises(LLMResponseError, match="not a JSON object"):
        adapter(client).generate_object(
            system_prompt="角色", user_message="问题", response_schema=SCHEMA
        )


def test_missing_choices_is_a_response_error():
    client, _ = recording_client(body={"model": DEFAULT_MODEL})
    with pytest.raises(LLMResponseError, match="no choices"):
        adapter(client).generate_object(
            system_prompt="角色", user_message="问题", response_schema=SCHEMA
        )


@pytest.mark.parametrize("status_code", [400, 401, 402, 422, 429, 500, 503])
def test_http_failures_are_transport_errors_with_provider_detail(status_code):
    client, _ = recording_client(
        status_code=status_code,
        body={"error": {"message": f"upstream says {status_code}"}},
    )
    with pytest.raises(LLMTransportError) as error:
        adapter(client).generate_object(
            system_prompt="角色", user_message="问题", response_schema=SCHEMA
        )
    assert str(status_code) in str(error.value)
    assert f"upstream says {status_code}" in str(error.value)


def test_http_error_without_json_body_still_reports_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="<html>bad gateway</html>")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(LLMTransportError, match="502"):
        adapter(client).generate_object(
            system_prompt="角色", user_message="问题", response_schema=SCHEMA
        )


def test_network_failures_are_transport_errors():
    client, _ = recording_client(raise_error=httpx.ConnectTimeout("timed out"))
    with pytest.raises(LLMTransportError, match="timed out"):
        adapter(client).generate_object(
            system_prompt="角色", user_message="问题", response_schema=SCHEMA
        )


def test_render_system_prompt_keeps_the_role_prompt_intact():
    rendered = render_system_prompt("原始提示词", SCHEMA)
    assert rendered.startswith("原始提示词\n\n")
    assert "JSON" in rendered
