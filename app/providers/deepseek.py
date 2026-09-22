"""DeepSeek chat-completions adapter for the provider-neutral LLM protocol.

DeepSeek's JSON Output mode guarantees a valid JSON string but does not accept a
JSON Schema, so the schema is rendered into the system prompt and the returned
object is validated by the caller's Pydantic model, exactly as the controlled
models in tests are. Transport, HTTP, empty-content, and malformed-JSON failures
are raised as distinct errors so the Runtime can turn them into a failed Run.
"""

import json
import os
from typing import Any, Mapping

import httpx

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_TOKENS = 8192
API_KEY_ENV = "DEEPSEEK_API_KEY"


class LLMError(RuntimeError):
    """Base class for provider and response failures."""


class LLMTransportError(LLMError):
    """The model service could not be reached, timed out, or returned HTTP >= 400."""


class LLMResponseError(LLMError):
    """The model service answered but the payload was empty or not valid JSON."""


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
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("DeepSeekLLM requires a non-empty API key")
        self.model = model or os.getenv("DEEPSEEK_MODEL") or DEFAULT_MODEL
        self.max_tokens = max_tokens
        # Structured extraction needs the whole budget for the JSON object, so
        # thinking mode is off by default; it can still be enabled per adapter.
        self.thinking = thinking
        self.last_usage: dict[str, Any] | None = None
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
        response = self._post(payload)
        content, finish_reason = self._content_of(response)
        return self._parse_object(content, response, finish_reason)

    def _thinking_enabled(self) -> bool:
        override = os.getenv("DEEPSEEK_THINKING")
        if override is not None and override.strip():
            return override.strip() == "1"
        return self.thinking

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.post(f"{self._endpoint}/chat/completions", json=payload)
        except httpx.HTTPError as exc:
            raise LLMTransportError(f"DeepSeek request failed: {exc}") from exc
        if response.status_code >= 400:
            raise LLMTransportError(
                f"DeepSeek returned HTTP {response.status_code}: {self._error_detail(response)}"
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
            raise LLMResponseError("DeepSeek returned empty content")
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
