"""HTTP adapter for the existing Core contracts and the Agent Runtime."""

import os
import logging
import uuid
from collections.abc import Callable
from functools import partial

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.agent_jobs import AgentJobManager, JobCapacityError
from app.api.v2 import router as v2_router
from app.agent_runtime.runner import run_agent
from app.agent_tools.mvp_gateway import MVPToolGateway
from app.agents.orchestrator import OrchestratorAgent
from app.agents.planning import PlanningAgent
from app.agents.reviewer import ReviewerAgent
from app.modules.blindspot.service import analyze_blindspots
from app.modules.diagnosis.service import diagnose
from app.modules.isochrone.service import IsochroneService
from app.modules.location.service import resolve_location
from app.modules.poi.service import POIService
from app.modules.routing.service import RoutingService
from app.providers.baidu.live import build_live_providers
from app.providers.deepseek import DeepSeekLLM, api_key_from_env
from app.schemas.agent import AgentRunState, RunStatus
from app.schemas.api import AgentJobAccepted, AgentJobState, AgentRunRequest
from app.schemas.common import ErrorDetail, Meta
from app.schemas.diagnosis import DiagnosisFailure, DiagnosisRequest, DiagnosisResult
from app.schemas.location import LocationFailure, LocationRequest, LocationResult


LocationRunner = Callable[[LocationRequest], LocationResult]
DiagnosisRunner = Callable[[DiagnosisRequest], DiagnosisResult]
AgentRunner = Callable[[AgentRunRequest], AgentRunState]
logger = logging.getLogger(__name__)

# Raised into the Run when the server is not configured for real Agent work.
# It is a normal `failed` terminal state, not an HTTP error: the request was
# well formed, the deployment simply cannot serve it.
MISSING_LLM_KEY_CODE = "LLM_KEY_MISSING"
MISSING_BAIDU_KEY_CODE = "BAIDU_AK_MISSING"


def get_location_runner() -> LocationRunner:
    ak = os.environ.get("BAIDU_MAP_AK", "").strip()
    geocode = build_live_providers(ak).geocoding.geocode if ak else None
    return partial(resolve_location, geocode=geocode)


def get_diagnosis_runner() -> DiagnosisRunner:
    ak = os.environ.get("BAIDU_MAP_AK", "").strip()
    if not ak:
        return lambda _request: _diagnosis_failure("ISOCHRONE_FAILED", "百度服务端 AK 未配置。")
    # Geocoding, POI and routing share one per-AK quota guard, so concurrent
    # HTTP requests queue behind the same console quota instead of each
    # request creating its own private budget.
    providers = build_live_providers(ak)
    routing = RoutingService(providers.routing)
    return partial(
        diagnose,
        resolve_location=partial(resolve_location, geocode=providers.geocoding.geocode),
        search_pois=POIService(providers.poi).search,
        route=routing.route,
        generate_isochrone=IsochroneService(routing.route).generate,
        analyze_blindspots=analyze_blindspots,
    )


def _location_failure(code: str, message: str) -> LocationResult:
    return LocationResult(root=LocationFailure(
        ok=False, error={"code": code, "message": message, "retryable": False},
        warnings=[], meta=Meta(schema_version="1.0", provider=None),
    ))


def _diagnosis_failure(code: str, message: str) -> DiagnosisResult:
    return DiagnosisResult(root=DiagnosisFailure(
        ok=False, error={"code": code, "message": message, "retryable": False},
        warnings=[], meta=Meta(schema_version="1.0", provider=None),
    ))


def _run_id(request: AgentRunRequest) -> str:
    """Honour a caller-supplied Run ID, otherwise mint one."""
    if request.run_id:
        return request.run_id
    return f"run:api-{uuid.uuid4().hex[:16]}"


def get_agent_runner() -> AgentRunner:
    """Build the real Agent entry point, or a runner that reports why it cannot.

    Construction performs no network call; the Baidu providers are built once
    per request through the shared per-AK quota guard, so the Agent API and the
    Core API draw from the same budget.
    """
    llm_key = api_key_from_env()
    ak = os.environ.get("BAIDU_MAP_AK", "").strip()

    def unavailable(code: str, message: str) -> AgentRunner:
        def runner(request: AgentRunRequest) -> AgentRunState:
            return AgentRunState(
                schema_version="1.0",
                run_id=_run_id(request),
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
                errors=[ErrorDetail(code=code, message=message, retryable=False)],
            )
        return runner

    if llm_key is None:
        return unavailable(MISSING_LLM_KEY_CODE, "服务端未配置模型密钥，Agent 暂不可用。")
    if not ak:
        return unavailable(MISSING_BAIDU_KEY_CODE, "服务端未配置百度服务端 AK，Agent 暂不可用。")

    model = DeepSeekLLM(llm_key)
    gateway = MVPToolGateway.from_baidu_ak(ak)

    def runner(request: AgentRunRequest) -> AgentRunState:
        return run_agent(
            run_id=_run_id(request),
            user_message=request.user_message,
            orchestrator=OrchestratorAgent(model),
            gateway=gateway,
            planning_agent=PlanningAgent(model),
            reviewer_agent=ReviewerAgent(model),
        )

    return runner


def _allowed_origins() -> list[str]:
    raw = os.environ.get("CORS_ORIGINS") or "http://localhost:5173,http://127.0.0.1:5173"
    origins = [item.strip() for item in raw.split(",") if item.strip()]
    if "*" in origins:
        raise ValueError("CORS_ORIGINS must list explicit origins, not '*'.")
    return origins


app = FastAPI(title="15 分钟生活圈 Core MVP API", version="2.0")
app.include_router(v2_router)
agent_job_manager = AgentJobManager()
app.add_middleware(
    CORSMiddleware, allow_origins=_allowed_origins(),
    allow_methods=["GET", "POST"], allow_headers=["Content-Type"],
)


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    if request.url.path == "/api/v1/location/resolve":
        result = _location_failure("INVALID_REQUEST", "位置请求格式无效。")
    elif request.url.path == "/api/v1/diagnosis":
        result = _diagnosis_failure("INVALID_REQUEST", "体检请求格式无效。")
    else:
        return await request_validation_exception_handler(request, exc)
    return JSONResponse(status_code=422, content=result.model_dump(mode="json"))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/agent/runs", response_model=AgentRunState)
def agent_run_api(
    request: AgentRunRequest, run: AgentRunner = Depends(get_agent_runner)
) -> AgentRunState:
    """Run one Agent request to a terminal state and return that state.

    The response *is* `AgentRunState`, so the API cannot drift from the
    Runtime. A well-formed request always returns 200: `failed`,
    `waiting_for_input` and `completed` are all legitimate terminal states and
    are distinguished by `status`, `completion_mode` and `errors`, not by the
    HTTP code. Malformed bodies are rejected as 422 by FastAPI.
    """
    try:
        return run(request)
    except Exception as exc:
        # `run_agent` already converts its own failures into a failed Run, so
        # reaching here means the runner itself broke. Report it in the state
        # rather than leaking an internal message.
        logger.error(
            "unexpected_api_failure endpoint=agent/runs run_id=%s exception_class=%s",
            _run_id(request), type(exc).__name__,
        )
        return AgentRunState(
            schema_version="1.0",
            run_id=_run_id(request),
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
            errors=[
                ErrorDetail(code="API_ERROR", message="Agent 执行暂不可用。", retryable=False)
            ],
        )


@app.post("/api/v1/agent/jobs", response_model=AgentJobAccepted, status_code=202)
def agent_job_submit_api(
    request: AgentRunRequest, run: AgentRunner = Depends(get_agent_runner)
) -> AgentJobAccepted:
    try:
        return agent_job_manager.submit(request, run)
    except JobCapacityError:
        raise HTTPException(status_code=429, detail="Agent 任务队列已满，请稍后重试。") from None


@app.get("/api/v1/agent/jobs/{job_id}", response_model=AgentJobState)
def agent_job_get_api(job_id: str, response: Response) -> AgentJobState:
    response.headers["Cache-Control"] = "no-store"
    job = agent_job_manager.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Agent 任务不存在或已过期。",
            headers={"Cache-Control": "no-store"},
        )
    return job


@app.post("/api/v1/location/resolve", response_model=LocationResult)
def location_api(request: LocationRequest, run: LocationRunner = Depends(get_location_runner)) -> LocationResult:
    try:
        return run(request)
    except Exception as exc:
        logger.error(
            "unexpected_api_failure endpoint=location/resolve exception_class=%s",
            type(exc).__name__,
        )
        return _location_failure("PROVIDER_ERROR", "位置解析暂不可用。")


@app.post("/api/v1/diagnosis", response_model=DiagnosisResult)
def diagnosis_api(request: DiagnosisRequest, run: DiagnosisRunner = Depends(get_diagnosis_runner)) -> DiagnosisResult:
    try:
        return run(request)
    except Exception as exc:
        logger.error(
            "unexpected_api_failure endpoint=diagnosis exception_class=%s",
            type(exc).__name__,
        )
        return _diagnosis_failure("COMPUTATION_ERROR", "社区体检暂不可用。")
