"""Provider-neutral structured model interface used by Agent roles."""

from typing import Any, Mapping, Protocol


class StructuredLLM(Protocol):
    """Return a JSON object; concrete adapters own model setup and transport."""

    def generate_object(
        self,
        *,
        system_prompt: str,
        user_message: str,
        response_schema: dict[str, Any],
    ) -> Mapping[str, Any]: ...
