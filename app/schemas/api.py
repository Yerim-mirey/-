"""API-layer request structures.

These are deliberately thin and separate from `app/schemas/agent.py`: the Agent
contracts are frozen and shared with the Runtime, while this module only
describes what an HTTP caller may send. The response reuses `AgentRunState`
directly so the API cannot drift from the Runtime's own state.
"""

from __future__ import annotations

from pydantic import Field

from app.schemas.agent import RunId
from app.schemas.common import ContractModel, SchemaVersion


class AgentRunRequest(ContractModel):
    """One user request for the Agent Runtime."""

    schema_version: SchemaVersion
    user_message: str = Field(min_length=1, max_length=4000)
    # Optional so a caller can correlate a retry in its own logs. The server
    # generates one when omitted; a client value is still validated against the
    # shared RunId pattern so a bad id fails as a 422 rather than as a 500.
    run_id: RunId | None = None
