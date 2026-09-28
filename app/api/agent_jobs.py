"""Bounded, in-process Agent jobs for the single-worker demo API."""

from __future__ import annotations

import uuid
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Callable

from app.schemas.agent import AgentRunState, RunStatus
from app.schemas.api import AgentJobAccepted, AgentJobState, AgentJobStatus, AgentRunRequest
from app.schemas.common import ErrorDetail


AgentRunner = Callable[[AgentRunRequest], AgentRunState]
TERMINAL = {AgentJobStatus.COMPLETED, AgentJobStatus.WAITING_FOR_INPUT, AgentJobStatus.FAILED}
RESULT_TTL = timedelta(minutes=15)
MAX_RETAINED_RESULTS = 32
logger = logging.getLogger(__name__)


class JobCapacityError(Exception):
    """One worker and one waiting slot are already occupied."""


@dataclass
class _Job:
    job_id: str
    run_id: str
    status: AgentJobStatus
    created_at: datetime
    updated_at: datetime
    result: AgentRunState | None = None

    def state(self) -> AgentJobState:
        return AgentJobState(
            job_id=self.job_id,
            run_id=self.run_id,
            status=self.status,
            created_at=self.created_at,
            updated_at=self.updated_at,
            result=self.result,
        )


class AgentJobManager:
    """Keep at most two active jobs and a short, bounded result history."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-job")
        self._jobs: dict[str, _Job] = {}

    def close(self) -> None:
        """Drain the worker when the owning process shuts down."""
        self._executor.shutdown(wait=True)

    def submit(self, request: AgentRunRequest, runner: AgentRunner) -> AgentJobAccepted:
        run_id = request.run_id or f"run:api-{uuid.uuid4().hex[:16]}"
        prepared = request.model_copy(update={"run_id": run_id})
        now = datetime.now(timezone.utc)
        job_id = f"job:{uuid.uuid4().hex}"
        with self._lock:
            self._cleanup(now)
            if sum(job.status not in TERMINAL for job in self._jobs.values()) >= 2:
                raise JobCapacityError
            job = _Job(job_id, run_id, AgentJobStatus.QUEUED, now, now)
            self._jobs[job_id] = job
            try:
                self._executor.submit(self._execute, job_id, prepared, runner)
            except Exception:
                del self._jobs[job_id]
                raise
            return AgentJobAccepted(
                job_id=job_id,
                run_id=run_id,
                status=job.status,
                status_url=f"/api/v1/agent/jobs/{job_id}",
            )

    def get(self, job_id: str) -> AgentJobState | None:
        with self._lock:
            self._cleanup(datetime.now(timezone.utc))
            job = self._jobs.get(job_id)
            return job.state() if job is not None else None

    def _execute(self, job_id: str, request: AgentRunRequest, runner: AgentRunner) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = AgentJobStatus.RUNNING
            job.updated_at = datetime.now(timezone.utc)
        try:
            result = runner(request)
            if result.run_id != job.run_id or result.status not in {
                RunStatus.COMPLETED, RunStatus.WAITING_FOR_INPUT, RunStatus.FAILED,
            }:
                raise ValueError("Agent runner returned an invalid terminal state")
        except Exception as exc:
            logger.error(
                "unexpected_api_failure endpoint=agent/jobs job_id=%s run_id=%s exception_class=%s",
                job.job_id, job.run_id, type(exc).__name__,
            )
            result = AgentRunState(
                schema_version="1.0",
                run_id=job.run_id,
                status=RunStatus.FAILED,
                completion_mode=None,
                user_message=request.user_message,
                brief=None,
                evidence=None,
                planning_proposal=None,
                review_result=None,
                planning_round=0,
                tool_retry_count=0,
                warnings=[],
                errors=[ErrorDetail(code="API_ERROR", message="Agent 执行暂不可用。", retryable=False)],
            )
        with self._lock:
            job.result = result
            job.status = AgentJobStatus(result.status.value)
            job.updated_at = datetime.now(timezone.utc)
            self._cleanup(job.updated_at)

    def _cleanup(self, now: datetime) -> None:
        expired = [
            job_id for job_id, job in self._jobs.items()
            if job.status in TERMINAL and now - job.updated_at >= RESULT_TTL
        ]
        for job_id in expired:
            del self._jobs[job_id]
        retained = sorted(
            (job for job in self._jobs.values() if job.status in TERMINAL),
            key=lambda job: job.updated_at,
        )
        for job in retained[:-MAX_RETAINED_RESULTS]:
            del self._jobs[job.job_id]
