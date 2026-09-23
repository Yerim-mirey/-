"""DeepSeek chat-completions adapter for the provider-neutral LLM protocol.

DeepSeek's JSON Output mode guarantees a valid JSON string but does not accept a
JSON Schema, so the schema is rendered into the system prompt and the returned
object is validated by the caller's Pydantic model, exactly as the controlled
models in tests are. Transport, HTTP, empty-content, and malformed-JSON failures
are raised as distinct errors so the Runtime can turn them into a failed Run.

Transient failures are retried in-process, because the Runtime deliberately
retries only *Tool* failures: a single model timeout would otherwise end the
whole Run as `failed`. In a full diagnosis that Run can already represent
minutes of work, so one dropped connection must not discard it. Retries are
bounded and apply only to failures that can plausibly succeed on a second
attempt — timeouts, connection errors, HTTP 429/5xx, and the documented
intermittent empty-content response. Malformed or truncated content is not
retried: the same request would produce the same result.
"""

import json
import os
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_TOKENS = 8192
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_BASE_SECONDS = 0.5
API_KEY_ENV = "DEEPSEEK_API_KEY"


class LLMError(RuntimeError):
    """Base class for provider and response failures."""


class LLMTransportError(LLMError):
    """The model service could not be reached, timed out, or returned HTTP >= 400.

    ``retryable`` marks the subset that the adapter will attempt again: network
    and timeout failures, HTTP 429 and HTTP 5xx. Other 4xx codes are permanent.
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class LLMResponseError(LLMError):
    """The model service answered but the payload was empty or not valid JSON.

    Empty content is the documented intermittent JSON-mode response and is
    retryable; malformed content is not.
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class LLMOutputTruncated(LLMResponseError):
    """The completion hit max_tokens, so no complete JSON object came back.

    Thinking tokens count towards ``max_tokens``, so this is the failure mode to
    watch for when thinking mode is enabled.
    """


def api_key_from_env() -> str | None:
    """Read the server-side key; the real value only ever lives in a local .env."""
    value = os.getenv(API_KEY_ENV)
    if not value:
        return None
    return value.strip() or None


def render_system_prompt(system_prompt: str, response_schema: Mapping[str, Any]) -> str:
    """Append the required JSON instruction and schema example to the role prompt.

    DeepSeek requires the word "json" plus a format example in the prompt; the
    caller's ``response_schema`` already describes the exact object, so it is
    embedded verbatim as both instruction and example.
    """
    schema_text = json.dumps(response_schema, ensure_ascii=False, indent=2)
    return (
        f"{system_prompt}\n\n"
        "输出要求：只输出一个合法的 JSON 对象，不要输出解释、Markdown 代码块或任何额外文字。\n"
        "EXAMPLE JSON OUTPUT (schema):\n"
        f"{schema_text}"
    )


class DeepSeekLLM:
    """Structured object generation through the DeepSeek chat completions API."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        thinking: bool = False,
        client: httpx.Client | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_base_seconds: float = DEFAULT_RETRY_BASE_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("DeepSeekLLM requires a non-empty API key")
        if max_retries < 0:
            raise ValueError("max_retries must not be negative")
        if retry_base_seconds < 0:
            raise ValueError("retry_base_seconds must not be negative")
        self.model = model or os.getenv("DEEPSEEK_MODEL") or DEFAULT_MODEL
        self.max_tokens = max_tokens
        # Structured extraction needs the whole budget for the JSON object, so
        # thinking mode is off by default; it can still be enabled per adapter.
        self.thinking = thinking
        self.max_retries = max_retries
        self.retry_base_seconds = retry_base_seconds
        self.last_usage: dict[str, Any] | None = None
        # How many attempts the most recent call needed, for diagnostics.
        self.last_attempts: int = 0
        self._sleep = sleep
        self._endpoint = (base_url or os.getenv("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self._client = client or httpx.Client(
            timeout=timeout_seconds,
            headers={
                "Authorization": f"Bearer {api_key.strip()}",
                "Content-Type": "application/json",
            },
        )

    def __repr__(self) -> str:
        return f"DeepSeekLLM(model={self.model!r})"

    def generate_object(
        self,
        *,
        system_prompt: str,
        user_message: str,
        response_schema: dict[str, Any],
    ) -> Mapping[str, Any]:
        if not isinstance(system_prompt, str) or not system_prompt.strip():
            raise ValueError("system_prompt must contain non-whitespace text")
        if not isinstance(user_message, str) or not user_message.strip():
            raise ValueError("user_message must contain non-whitespace text")
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": render_system_prompt(system_prompt.strip(), response_schema),
                },
                {"role": "user", "content": user_message.strip()},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": self.max_tokens,
            "stream": False,
            # OpenAI-format toggle; also switchable through DEEPSEEK_THINKING=1.
            "thinking": {"type": "enabled" if self._thinking_enabled() else "disabled"},
        }
        return self._with_retry(payload)

    def _thinking_enabled(self) -> bool:
        override = os.getenv("DEEPSEEK_THINKING")
        if override is not None and override.strip():
            return override.strip() == "1"
        return self.thinking

    def _with_retry(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        """Run one completion, retrying only failures that may succeed again.

        The loop covers the whole exchange, not just the HTTP call, because the
        documented intermittent empty-content response is produced by the
        payload rather than by the transport. Non-retryable problems (bad key,
        malformed request, truncated or malformed content) fail immediately so a
        misconfiguration is not hidden behind retries.
        """
        self.last_attempts = 0
        for attempt in range(self.max_retries + 1):
            self.last_attempts = attempt + 1
            try:
                response = self._post_once(payload)
                content, finish_reason = self._content_of(response)
                return self._parse_object(content, response, finish_reason)
            except LLMError as exc:
                if not getattr(exc, "retryable", False) or attempt == self.max_retries:
                    if attempt:
                        raise type(exc)(
                            f"{exc} (retried {attempt} time(s), all failed)"
                        ) from exc
                    raise
                # Exponential backoff: base, 2x base, 4x base, ...
                self._sleep(self.retry_base_seconds * (2 ** attempt))
        raise AssertionError("unreachable")

    def _post_once(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.post(f"{self._endpoint}/chat/completions", json=payload)
        except httpx.HTTPError as exc:
            # Timeouts and connection resets are the common case here; they are
            # exactly what a retry is for.
            raise LLMTransportError(
                f"DeepSeek request failed: {exc}", retryable=True
            ) from exc
        if response.status_code >= 400:
            retryable = response.status_code == 429 or response.status_code >= 500
            raise LLMTransportError(
                f"DeepSeek returned HTTP {response.status_code}: {self._error_detail(response)}",
                retryable=retryable,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise LLMResponseError("DeepSeek response body is not JSON") from exc

    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return response.text[:200]
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        return json.dumps(body, ensure_ascii=False)[:200]

    @staticmethod
    def _content_of(body: Mapping[str, Any]) -> tuple[str, str | None]:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMResponseError("DeepSeek response contains no choices")
        choice = choices[0] if isinstance(choices[0], dict) else {}
        finish_reason = choice.get("finish_reason")
        message = choice.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            if finish_reason == "length":
                raise LLMOutputTruncated(
                    "DeepSeek completion hit max_tokens before any JSON content was produced"
                )
            # Documented JSON-mode behaviour: the API may return empty content.
            # It is intermittent, so the same request can succeed on a retry.
            raise LLMResponseError("DeepSeek returned empty content", retryable=True)
        return content.strip(), finish_reason

    def _parse_object(
        self, content: str, body: Mapping[str, Any], finish_reason: str | None
    ) -> Mapping[str, Any]:
        usage = body.get("usage")
        self.last_usage = {
            "model": body.get("model", self.model),
            "usage": usage if isinstance(usage, dict) else None,
            "finish_reason": finish_reason,
        }
        try:
            parsed = json.loads(content)
        except ValueError:
            stripped = _strip_code_fence(content)
            try:
                parsed = json.loads(stripped)
            except ValueError as exc:
                if finish_reason == "length":
                    raise LLMOutputTruncated(
                        "DeepSeek completion hit max_tokens and the JSON object is incomplete"
                    ) from exc
                raise LLMResponseError("DeepSeek content is not a JSON object") from exc
        if not isinstance(parsed, dict):
            raise LLMResponseError("DeepSeek content is not a JSON object")
        return parsed


def _strip_code_fence(content: str) -> str:
    """Tolerate a fenced JSON block even though JSON mode forbids prose."""
    text = content.strip()
    if not text.startswith("```"):
        return text
    lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
    return "\n".join(lines).strip()
