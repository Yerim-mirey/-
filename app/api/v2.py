"""Version 2 facility catalog and generalized POI endpoints."""

import os
import re

from fastapi import APIRouter, Depends, HTTPException, Response

from app.api.agent_jobs_v2 import AgentJobManagerV2, JobCapacityErrorV2
from app.agents.orchestrator_v2 import OrchestratorAgentV2
from app.modules.poi.facility_catalog import load_facility_catalog
from app.modules.poi.service_v2 import POIServiceV2
from app.modules.routing.service import RoutingService
from app.providers.baidu.live import build_live_providers_v2
from app.providers.deepseek import DeepSeekLLM, api_key_from_env
from app.schemas.facility_catalog import (
    FacilityCatalogEntryV2,
    FacilityCatalogGroupV2,
    FacilityCatalogResultV2,
)
from app.schemas.agent_v2 import AgentBriefRequestV2, AgentBriefV2
from app.schemas.agent_jobs_v2 import (
    AgentJobAcceptedV2,
    AgentJobStateV2,
    AgentRunRequestV2,
)
from app.schemas.agent_run_v2 import AgentRunStateV2
from app.schemas.common import ErrorDetail
from app.schemas.poi_v2 import (
    POIExploreRequestV2,
    POISearchFailureV2,
    POISearchRequestV2,
    POISearchSuccessV2,
)
from app.schemas.routing import RoutingFailure, RoutingRequest
from app.schemas.routing_v2 import RoutingRequestV2, RoutingResponseV2, RouteResultV2


router = APIRouter(prefix="/api/v2", tags=["v2"])
agent_job_manager_v2 = AgentJobManagerV2()
_FACILITY_TERMS = (
    "医院", "诊所", "门诊", "药店", "药房", "学校", "幼儿园", "托育", "服务中心",
    "服务站", "服务点", "活动中心", "图书馆", "阅览室", "体育馆", "运动场", "公园",
    "广场", "市场", "超市", "便利店", "餐厅", "食堂", "银行", "快递驿站", "邮局",
    "公交站", "地铁站", "停车场", "公共厕所", "公厕", "养老院", "养老服务", "宠物店",
)


def _is_explicit_facility_term(query: str) -> bool:
    normalized = query.strip().casefold()
    return any(term.casefold() in normalized for term in _FACILITY_TERMS)


def _contains_catalog_facility_reference(query: str) -> bool:
    clean = re.sub(r"[\s\u3000·•,，。；;、()（）_\-/]+", "", query).casefold()
    # Explicit qualifiers can distinguish an out-of-catalog subtype from a
    # broad in-catalog parent type (e.g. pet hospital vs. general hospital).
    if any(term in clean for term in ("宠物医院", "宠物诊所", "动物医院", "动物诊所")):
        return False
    for context in ("附近", "周边", "附近的", "周边的", "查询", "查找", "想找"):
        clean = clean.replace(context, "")
    catalog = load_facility_catalog()
    for facility in catalog.facilities:
        names = (facility.id, facility.name_zh, *facility.aliases)
        for name in names:
            term = re.sub(r"[\s\u3000·•,，。；;、()（）_\-/]+", "", name).casefold()
            if len(term) >= 2 and term in clean:
                return True
    return False


def get_poi_service_v2() -> POIServiceV2:
    providers = build_live_providers_v2(os.environ.get("BAIDU_MAP_AK", ""))
    return POIServiceV2(provider=providers.poi, catalog=load_facility_catalog())


def get_orchestrator_v2() -> OrchestratorAgentV2:
    key = api_key_from_env()
    if key is None:
        raise HTTPException(status_code=503, detail="服务端模型密钥未配置。")
    return OrchestratorAgentV2(DeepSeekLLM(key), load_facility_catalog())


def get_agent_runner_v2():
    """Build V2 runtime lazily; a missing runtime becomes a sanitized Run result."""
    def run(request: AgentRunRequestV2) -> AgentRunStateV2:
        try:
            from app.agent_runtime.runner_v2 import build_agent_runner_v2

            return build_agent_runner_v2()(request)
        except Exception:
            return AgentRunStateV2(
                run_id=request.run_id or "run:api-v2-unavailable",
                status="failed",
                user_message=request.user_message,
                errors=[ErrorDetail(
                    code="RUNTIME_UNAVAILABLE",
                    message="V2 Agent 暂不可用，请稍后重试。",
                    retryable=True,
                )],
            )

    return run


def get_routing_service_v2() -> RoutingService:
    providers = build_live_providers_v2(os.environ.get("BAIDU_MAP_AK", ""))
    return RoutingService(providers.routing)


@router.get("/facilities", response_model=FacilityCatalogResultV2)
def facility_catalog_api() -> FacilityCatalogResultV2:
    catalog = load_facility_catalog()
    return FacilityCatalogResultV2(
        schema_version="2.0",
        catalog_version=catalog.version,
        groups=[FacilityCatalogGroupV2(id=item.id, name_zh=item.name_zh) for item in catalog.groups],
        facilities=[
            FacilityCatalogEntryV2(
                id=item.id,
                group_id=item.group_id,
                name_zh=item.name_zh,
                aliases=list(item.aliases),
                capabilities=sorted(item.capabilities),
                metric_profile=item.metric_profile,
                data_notes=item.data_notes,
                basis=item.basis,
            )
            for item in catalog.facilities
        ],
    )


@router.post("/agent/brief", response_model=AgentBriefV2)
def agent_brief_v2_api(
    request: AgentBriefRequestV2,
    orchestrator: OrchestratorAgentV2 = Depends(get_orchestrator_v2),
) -> AgentBriefV2:
    try:
        return orchestrator.create_brief(request.user_message)
    except Exception:
        raise HTTPException(status_code=502, detail="需求解析暂不可用，请稍后重试。") from None


@router.post(
    "/pois/search", response_model=POISearchSuccessV2 | POISearchFailureV2
)
def poi_search_v2_api(
    request: POISearchRequestV2,
    service: POIServiceV2 = Depends(get_poi_service_v2),
):
    try:
        return service.search(request)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.post(
    "/pois/explore", response_model=POISearchSuccessV2 | POISearchFailureV2
)
def poi_explore_v2_api(
    payload: POIExploreRequestV2,
    service: POIServiceV2 = Depends(get_poi_service_v2),
):
    if _contains_catalog_facility_reference(payload.query):
        raise HTTPException(
            status_code=422,
            detail="该设施已在标准目录中，请使用 /api/v2/pois/search。",
        )
    if not _is_explicit_facility_term(payload.query):
        raise HTTPException(
            status_code=422,
            detail="目录外探索仅接受明确命名的设施或服务点，例如“宠物医院”。",
        )
    return service.search_exploratory(
        payload.query, payload.center, payload.search_radius_m
    )


@router.post("/routing/walking", response_model=RoutingResponseV2)
def walking_routes_v2_api(
    payload: RoutingRequestV2,
    service: RoutingService = Depends(get_routing_service_v2),
) -> RoutingResponseV2:
    catalog = load_facility_catalog()
    for target in payload.targets:
        if target.facility_id == "custom_query":
            continue
        try:
            definition = catalog.get(target.facility_id)
        except KeyError:
            raise HTTPException(status_code=422, detail="路线目标设施 ID 不在目录中。") from None
        if "routing" not in definition.capabilities:
            raise HTTPException(status_code=422, detail="该设施类别未开放步行路线能力。")
    result = service.route(RoutingRequest(
        schema_version="1.0",
        origin=payload.origin,
        targets=[
            {"target_id": item.target_id, "location": item.location}
            for item in payload.targets
        ],
        travel_mode="walking",
    )).root
    if isinstance(result, RoutingFailure):
        return RoutingResponseV2(
            ok=False,
            error_code=result.error.code.value,
            message=result.error.message,
            retryable=result.error.retryable,
        )
    target_by_id = {item.target_id: item for item in payload.targets}
    return RoutingResponseV2(
        ok=True,
        routes=[
            RouteResultV2(
                target_id=item.target_id,
                facility_id=target_by_id[item.target_id].facility_id,
                location=item.location,
                status=item.status.value,
                distance_m=item.distance_m,
                duration_s=item.duration_s,
            )
            for item in result.data.routes
        ],
        summary=result.data.summary.model_dump(),
    )


@router.post("/agent/jobs", response_model=AgentJobAcceptedV2, status_code=202)
def agent_job_submit_v2_api(
    request: AgentRunRequestV2,
    run=Depends(get_agent_runner_v2),
) -> AgentJobAcceptedV2:
    """Accept a long-running V2 Agent request and return its polling resource."""
    try:
        return agent_job_manager_v2.submit(request, run)
    except JobCapacityErrorV2:
        raise HTTPException(status_code=429, detail="Agent 任务队列已满，请稍后重试。") from None


@router.get("/agent/jobs/{job_id}", response_model=AgentJobStateV2)
def agent_job_get_v2_api(job_id: str, response: Response) -> AgentJobStateV2:
    response.headers["Cache-Control"] = "no-store"
    job = agent_job_manager_v2.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Agent 任务不存在或已过期。",
            headers={"Cache-Control": "no-store"},
        )
    return job
