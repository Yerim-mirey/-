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
    LLMOutputTruncated,
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
                "choices": [{
                    "message": {"role": "assistant", "content": '{"answer": "ok"}'},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16},
            }
        return httpx.Response(status_code, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler), headers=headers), requests


def adapter(client, **kwargs) -> DeepSeekLLM:
    """Build an adapter whose retry backoff is virtual, never real.

    The default retry policy would otherwise make every transient-failure test
    sleep through its backoff, which is slow and says nothing about correctness.
    Tests that care about the delays pass their own recorder.
    """
    kwargs.setdefault("sleep", lambda _seconds: None)
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
    assert payload["thinking"] == {"type": "disabled"}, "结构化抽取必须留足 JSON 输出预算"
    assert payload["messages"][1] == {"role": "user", "content": "请回答"}
    system = payload["messages"][0]["content"]
    assert system.startswith("你是测试角色。")
    assert "json" in system.lower(), "DeepSeek 的 JSON 模式要求提示词包含 json 字样"
    assert json.dumps(SCHEMA, ensure_ascii=False, indent=2) in system, "必须给出格式示例"
    assert adapter(client).last_usage is None


def test_thinking_mode_is_opt_in(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_THINKING", raising=False)
    client, requests = recording_client()
    adapter(client, thinking=True).generate_object(
        system_prompt="角色", user_message="问题", response_schema=SCHEMA
    )
    assert sent_payload(requests)["thinking"] == {"type": "enabled"}

    monkeypatch.setenv("DEEPSEEK_THINKING", "1")
    client, requests = recording_client()
    adapter(client).generate_object(
        system_prompt="角色", user_message="问题", response_schema=SCHEMA
    )
    assert sent_payload(requests)["thinking"] == {"type": "enabled"}

    monkeypatch.setenv("DEEPSEEK_THINKING", "0")
    client, requests = recording_client()
    adapter(client, thinking=True).generate_object(
        system_prompt="角色", user_message="问题", response_schema=SCHEMA
    )
    assert sent_payload(requests)["thinking"] == {"type": "disabled"}


def test_truncated_completion_is_reported_as_truncation():
    """Thinking tokens share the max_tokens budget, so length must be explicit."""
    client, _ = recording_client(body={
        "model": DEFAULT_MODEL,
        "choices": [{
            "message": {"content": "", "reasoning_content": "思考中…"},
            "finish_reason": "length",
        }],
        "usage": {"completion_tokens": 8192},
    })
    with pytest.raises(LLMOutputTruncated, match="max_tokens"):
        adapter(client).generate_object(
            system_prompt="角色", user_message="问题", response_schema=SCHEMA
        )


def test_partial_json_on_a_length_finish_is_reported_as_truncation():
    client, _ = recording_client(body={
        "model": DEFAULT_MODEL,
        "choices": [{
            "message": {"content": '{"answer": "cut off'},
            "finish_reason": "length",
        }],
    })
    with pytest.raises(LLMOutputTruncated, match="incomplete"):
        adapter(client).generate_object(
            system_prompt="角色", user_message="问题", response_schema=SCHEMA
        )


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
    assert model.last_usage["finish_reason"] == "stop"


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


# --- transient-failure retry ------------------------------------------------


def json_ok_body(content: str = '{"status": 0}') -> dict:
    return {
        "model": "deepseek-flash",
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def scripted_client(outcomes):
    """A client whose handler raises or answers according to `outcomes`."""
    calls = []

    def handler(request):
        calls.append(request)
        outcome = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        status, body = outcome
        return httpx.Response(status, json=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return client, calls


def test_timeout_is_retried_then_succeeds():
    client, calls = scripted_client([
        httpx.ReadTimeout("timed out"),
        httpx.ReadTimeout("timed out"),
        (200, json_ok_body()),
    ])
    sleeps: list[float] = []
    llm = DeepSeekLLM("k", client=client, sleep=sleeps.append, retry_base_seconds=0.5)

    result = llm.generate_object(
        system_prompt="s", user_message="u", response_schema={"type": "object"}
    )

    assert result == {"status": 0}
    assert len(calls) == 3
    assert llm.last_attempts == 3
    # Exponential backoff: 0.5 then 1.0, never a real sleep.
    assert sleeps == [0.5, 1.0]


@pytest.mark.parametrize("status_code", [429, 500, 502, 503])
def test_retryable_http_statuses_are_retried(status_code):
    client, calls = scripted_client([
        (status_code, {"error": {"message": "busy"}}),
        (200, json_ok_body()),
    ])
    llm = DeepSeekLLM("k", client=client, sleep=lambda _s: None)

    assert llm.generate_object(
        system_prompt="s", user_message="u", response_schema={"type": "object"}
    ) == {"status": 0}
    assert len(calls) == 2


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
def test_permanent_http_statuses_are_not_retried(status_code):
    """A bad key or malformed request must fail fast, not hide behind retries."""
    client, calls = scripted_client([(status_code, {"error": {"message": "nope"}})])
    llm = DeepSeekLLM("k", client=client, sleep=lambda _s: None)

    with pytest.raises(LLMTransportError) as caught:
        llm.generate_object(
            system_prompt="s", user_message="u", response_schema={"type": "object"}
        )

    assert len(calls) == 1
    assert caught.value.retryable is False


def test_empty_content_is_retried_because_it_is_intermittent():
    """JSON mode documents an occasional empty response; a retry may fix it."""
    empty = {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]}
    client, calls = scripted_client([(200, empty), (200, json_ok_body())])
    llm = DeepSeekLLM("k", client=client, sleep=lambda _s: None)

    assert llm.generate_object(
        system_prompt="s", user_message="u", response_schema={"type": "object"}
    ) == {"status": 0}
    assert len(calls) == 2


def test_truncation_is_not_retried():
    """Hitting max_tokens is deterministic; a retry would truncate identically."""
    truncated = {
        "choices": [{"message": {"content": ""}, "finish_reason": "length"}]
    }
    client, calls = scripted_client([(200, truncated)])
    llm = DeepSeekLLM("k", client=client, sleep=lambda _s: None)

    with pytest.raises(LLMOutputTruncated):
        llm.generate_object(
            system_prompt="s", user_message="u", response_schema={"type": "object"}
        )

    assert len(calls) == 1


def test_malformed_content_is_not_retried():
    client, calls = scripted_client([(200, json_ok_body("not json at all"))])
    llm = DeepSeekLLM("k", client=client, sleep=lambda _s: None)

    with pytest.raises(LLMResponseError):
        llm.generate_object(
            system_prompt="s", user_message="u", response_schema={"type": "object"}
        )

    assert len(calls) == 1


def test_exhausted_retries_report_how_many_were_made():
    client, calls = scripted_client([httpx.ReadTimeout("down")])
    llm = DeepSeekLLM("k", client=client, sleep=lambda _s: None, max_retries=2)

    with pytest.raises(LLMTransportError) as caught:
        llm.generate_object(
            system_prompt="s", user_message="u", response_schema={"type": "object"}
        )

    assert len(calls) == 3
    assert "retried 2 time(s)" in str(caught.value)
    # The class survives the wrapping so callers still map it correctly.
    assert type(caught.value) is LLMTransportError


def test_zero_retries_disables_the_policy():
    client, calls = scripted_client([httpx.ReadTimeout("down")])
    llm = DeepSeekLLM("k", client=client, sleep=lambda _s: None, max_retries=0)

    with pytest.raises(LLMTransportError):
        llm.generate_object(
            system_prompt="s", user_message="u", response_schema={"type": "object"}
        )

    assert len(calls) == 1


@pytest.mark.parametrize("kwargs", [{"max_retries": -1}, {"retry_base_seconds": -0.1}])
def test_invalid_retry_configuration_is_rejected(kwargs):
    with pytest.raises(ValueError):
        DeepSeekLLM("k", **kwargs)


def test_a_successful_first_attempt_does_not_sleep():
    client, calls = scripted_client([(200, json_ok_body())])
    sleeps: list[float] = []
    llm = DeepSeekLLM("k", client=client, sleep=sleeps.append)

    llm.generate_object(
        system_prompt="s", user_message="u", response_schema={"type": "object"}
    )

    assert len(calls) == 1
    assert sleeps == []
    assert llm.last_attempts == 1
