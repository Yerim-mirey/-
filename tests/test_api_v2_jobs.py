"""V2 asynchronous Agent resource lifecycle without live providers."""

import json
import time
from threading import Event

import pytest
from fastapi.testclient import TestClient

from app.api import v2 as api_v2
from app.api.agent_jobs_v2 import AgentJobManagerV2
from app.api.main import app
from app.schemas.agent_run_v2 import AgentRunStateV2
from app.schemas.common import ErrorDetail


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("BAIDU_MAP_AK", raising=False)
    app.dependency_overrides.clear()
    manager = AgentJobManagerV2()
    monkeypatch.setattr(api_v2, "agent_job_manager_v2", manager)
    yield TestClient(app)
    manager.close()
    app.dependency_overrides.clear()


def _failed(request, run_id=None):
    return AgentRunStateV2(
        run_id=run_id or request.run_id,
        status="failed",
        user_message=request.user_message,
        errors=[ErrorDetail(code="OFFLINE", message="离线测试结束。", retryable=False)],
    )


def _wait(client, status_url, expected="failed"):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = client.get(status_url)
        if response.status_code == 200 and response.json()["status"] == expected:
            return response.json()
        time.sleep(0.01)
    pytest.fail(f"V2 job did not reach {expected}")


def test_v2_job_accepts_and_returns_v2_run_state(client):
    app.dependency_overrides[api_v2.get_agent_runner_v2] = lambda: _failed
    accepted = client.post("/api/v2/agent/jobs", json={
        "schema_version": "2.0", "user_message": "查附近菜市场", "run_id": "run:v2-case",
    })
    assert accepted.status_code == 202
    body = accepted.json()
    assert body["schema_version"] == "2.0"
    assert body["run_id"] == "run:v2-case"
    assert body["status_url"] == f"/api/v2/agent/jobs/{body['job_id']}"
    assert client.get(body["status_url"]).headers["cache-control"] == "no-store"
    result = _wait(client, body["status_url"])
    assert result["result"]["schema_version"] == "2.0"
    assert result["result"]["run_id"] == body["run_id"]
    assert result["result"]["errors"][0]["code"] == "OFFLINE"


def test_v2_job_capacity_is_one_running_plus_one_queued(client):
    started = Event()
    release = Event()

    def controlled(request):
        started.set()
        assert release.wait(3)
        return _failed(request)

    app.dependency_overrides[api_v2.get_agent_runner_v2] = lambda: controlled
    payload = {"schema_version": "2.0", "user_message": "排队测试"}
    try:
        first = client.post("/api/v2/agent/jobs", json=payload)
        assert first.status_code == 202
        assert started.wait(1)
        second = client.post("/api/v2/agent/jobs", json=payload)
        assert second.status_code == 202
        assert second.json()["status"] == "queued"
        assert client.post("/api/v2/agent/jobs", json=payload).status_code == 429
    finally:
        release.set()
    assert _wait(client, first.json()["status_url"])["status"] == "failed"
    assert _wait(client, second.json()["status_url"])["status"] == "failed"


def test_v2_job_sanitizes_runner_exception(client, caplog):
    def broken(_request):
        raise RuntimeError("sensitive-provider-detail")

    app.dependency_overrides[api_v2.get_agent_runner_v2] = lambda: broken
    accepted = client.post("/api/v2/agent/jobs", json={
        "schema_version": "2.0", "user_message": "错误处理测试",
    })
    result = _wait(client, accepted.json()["status_url"])
    assert result["result"]["errors"][0]["code"] == "API_ERROR"
    assert "sensitive-provider-detail" not in json.dumps(result, ensure_ascii=False)
    assert "sensitive-provider-detail" not in caplog.text


def test_v2_job_validation_and_unknown_id(client):
    assert client.post("/api/v2/agent/jobs", json={
        "schema_version": "1.0", "user_message": "错版本",
    }).status_code == 422
    response = client.get("/api/v2/agent/jobs/job:" + "0" * 32)
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"


def test_v2_openapi_exposes_job_contracts(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v2/agent/jobs" in paths
    assert "/api/v2/agent/jobs/{job_id}" in paths


def test_v2_job_result_expires(client, monkeypatch):
    app.dependency_overrides[api_v2.get_agent_runner_v2] = lambda: _failed
    accepted = client.post("/api/v2/agent/jobs", json={
        "schema_version": "2.0", "user_message": "过期测试",
    })
    _wait(client, accepted.json()["status_url"])
    from app.api import agent_jobs_v2

    monkeypatch.setattr(agent_jobs_v2, "RESULT_TTL", agent_jobs_v2.timedelta(0))
    expired = client.get(accepted.json()["status_url"])
    assert expired.status_code == 404
    assert expired.headers["cache-control"] == "no-store"
