"""Bounded in-process resource lifecycle for long-running V2 Agent work."""

from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Callable

from app.schemas.agent_jobs_v2 import (
    AgentJobAcceptedV2,
    AgentJobStateV2,
    AgentJobStatusV2,
    AgentRunRequestV2,
)
from app.schemas.agent_run_v2 import AgentRunStateV2
from app.schemas.common import ErrorDetail


AgentRunnerV2 = Callable[[AgentRunRequestV2], AgentRunStateV2]
TERMINAL = {
    AgentJobStatusV2.COMPLETED,
    AgentJobStatusV2.WAITING_FOR_INPUT,
    AgentJobStatusV2.FAILED,
}
RESULT_TTL = timedelta(minutes=15)
MAX_RETAINED_RESULTS = 32
logger = logging.getLogger(__name__)


class JobCapacityErrorV2(Exception):
    """The one running and one waiting slot are occupied."""


@dataclass
class _Job:
    job_id: str
    run_id: str
    status: AgentJobStatusV2
    created_at: datetime
    updated_at: datetime
    result: AgentRunStateV2 | None = None

    def state(self) -> AgentJobStateV2:
        return AgentJobStateV2(
            job_id=self.job_id,
            run_id=self.run_id,
            status=self.status,
            created_at=self.created_at,
            updated_at=self.updated_at,
            result=self.result,
        )


class AgentJobManagerV2:
    """Allow one active run, one queued run and bounded short-lived history."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-v2-job")
        self._jobs: dict[str, _Job] = {}
        self._closed = False

    def close(self) -> None:
        """Drain accepted work when the owning process shuts down."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=True)

    def submit(self, request: AgentRunRequestV2, runner: AgentRunnerV2) -> AgentJobAcceptedV2:
        run_id = request.run_id or f"run:api-v2-{uuid.uuid4().hex[:16]}"
        prepared = request.model_copy(update={"run_id": run_id})
        now = datetime.now(timezone.utc)
        job_id = f"job:{uuid.uuid4().hex}"
        with self._lock:
            self._cleanup(now)
            if self._closed or sum(job.status not in TERMINAL for job in self._jobs.values()) >= 2:
                raise JobCapacityErrorV2
            job = _Job(job_id, run_id, AgentJobStatusV2.QUEUED, now, now)
            self._jobs[job_id] = job
            try:
                self._executor.submit(self._execute, job_id, prepared, runner)
            except Exception:
                del self._jobs[job_id]
                raise
            return AgentJobAcceptedV2(
                job_id=job_id,
                run_id=run_id,
                status=job.status,
                status_url=f"/api/v2/agent/jobs/{job_id}",
            )

    def get(self, job_id: str) -> AgentJobStateV2 | None:
        with self._lock:
            self._cleanup(datetime.now(timezone.utc))
            job = self._jobs.get(job_id)
            return job.state() if job is not None else None

    def _execute(
        self, job_id: str, request: AgentRunRequestV2, runner: AgentRunnerV2
    ) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = AgentJobStatusV2.RUNNING
            job.updated_at = datetime.now(timezone.utc)
        try:
            result = runner(request)
            if result.run_id != job.run_id or result.status not in {
                "completed", "waiting_for_input", "failed",
            }:
                raise ValueError("V2 Agent runner returned an invalid terminal state")
        except Exception as exc:  # sanitize provider/model details at the API boundary
            logger.error(
                "unexpected_api_failure endpoint=agent/v2/jobs job_id=%s run_id=%s exception_class=%s",
                job.job_id, job.run_id, type(exc).__name__,
            )
            result = AgentRunStateV2(
                run_id=job.run_id,
                status="failed",
                user_message=request.user_message,
                errors=[ErrorDetail(
                    code="API_ERROR", message="Agent 执行暂不可用。", retryable=False
                )],
            )
        finished = datetime.now(timezone.utc)
        result = result.model_copy(update={
            "created_at": result.created_at or job.created_at,
            "updated_at": finished,
        })
        with self._lock:
            job.result = result
            job.status = AgentJobStatusV2(result.status)
            job.updated_at = finished
            self._cleanup(finished)

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
