"""V2 Agent job submission and polling contracts."""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field

from app.schemas.agent_run_v2 import AgentRunStateV2
from app.schemas.common import ContractModel


class AgentRunRequestV2(ContractModel):
    schema_version: Literal["2.0"]
    user_message: str = Field(min_length=1, max_length=4000)
    run_id: str | None = Field(default=None, pattern=r"^run:[A-Za-z0-9._:-]{1,96}$")


class AgentJobStatusV2(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    WAITING_FOR_INPUT = "waiting_for_input"
    FAILED = "failed"


class AgentJobAcceptedV2(ContractModel):
    schema_version: Literal["2.0"] = "2.0"
    job_id: str = Field(pattern=r"^job:[a-f0-9]{32}$")
    run_id: str = Field(pattern=r"^run:[A-Za-z0-9._:-]{1,96}$")
    status: AgentJobStatusV2 = Field(strict=False)
    status_url: str


class AgentJobStateV2(ContractModel):
    schema_version: Literal["2.0"] = "2.0"
    job_id: str = Field(pattern=r"^job:[a-f0-9]{32}$")
    run_id: str = Field(pattern=r"^run:[A-Za-z0-9._:-]{1,96}$")
    status: AgentJobStatusV2 = Field(strict=False)
    created_at: datetime = Field(strict=False)
    updated_at: datetime = Field(strict=False)
    result: AgentRunStateV2 | None = None
