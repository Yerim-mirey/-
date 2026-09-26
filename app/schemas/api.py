"""API-layer request structures.

These are deliberately thin and separate from `app/schemas/agent.py`: the Agent
contracts are frozen and shared with the Runtime. The synchronous response
reuses `AgentRunState` directly; job DTOs only wrap that same final state.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field

from app.schemas.agent import AgentRunState, RunId
from app.schemas.common import ContractModel, SchemaVersion


class AgentRunRequest(ContractModel):
    """One user request for the Agent Runtime."""

    schema_version: SchemaVersion
    user_message: str = Field(min_length=1, max_length=4000)
    # Optional so a caller can correlate a retry in its own logs. The server
    # generates one when omitted; a client value is still validated against the
    # shared RunId pattern so a bad id fails as a 422 rather than as a 500.
    run_id: RunId | None = None


class AgentJobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    WAITING_FOR_INPUT = "waiting_for_input"
    FAILED = "failed"


class AgentJobAccepted(ContractModel):
    schema_version: SchemaVersion = "1.0"
    job_id: str = Field(pattern=r"^job:[a-f0-9]{32}$")
    run_id: RunId
    status: AgentJobStatus
    status_url: str


class AgentJobState(ContractModel):
    schema_version: SchemaVersion = "1.0"
    job_id: str = Field(pattern=r"^job:[a-f0-9]{32}$")
    run_id: RunId
    status: AgentJobStatus
    created_at: datetime
    updated_at: datetime
    result: AgentRunState | None = None
