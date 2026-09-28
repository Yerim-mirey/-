"""End-to-end V2 facility run: extract, resolve, collect, analyze and answer."""

from __future__ import annotations

import math
import os
import re
import time
import uuid
from collections import defaultdict
from typing import Any

from app.agent_tools.gateway_v2 import ToolGatewayV2
from app.agent_tools.mvp_gateway_v2 import MVPToolGatewayV2
from app.agents.orchestrator_v2 import OrchestratorAgentV2
from app.modules.poi.facility_catalog import FacilityCatalog, load_facility_catalog
from app.providers.deepseek import DeepSeekLLM, api_key_from_env
from app.schemas.agent_run_v2 import AgentRunStateV2
from app.schemas.agent_v2 import AgentIntentV2
from app.schemas.common import CenterPoint, ErrorDetail
from app.schemas.diagnosis_v2 import DiagnosisRequestV2, FacilityEvidenceV2
from app.schemas.location import LocationFailure, LocationRequest, LocationResult
from app.schemas.poi_v2 import POISearchFailureV2, POISearchRequestV2, POISearchSuccessV2
from app.schemas.routing import RoutingFailure, RoutingRequest
from app.schemas.routing_v2 import RoutingRequestV2, RoutingResponseV2, RouteResultV2


POI_SEARCH_RADIUS_M = 1000
MAX_ROUTE_TARGETS_PER_RUN = 20
MAX_AGENT_RUN_SECONDS = 8 * 60
_FOCUS_SEGMENT = re.compile(
    r"(?:重点关注|优先关注|尤其关注|特别关注|重点看|特别想看|尤其是|特别是)"
    r"([^。；;！？\n]{1,80})"
)
_FOCUS_SEPARATORS = re.compile(r"[\s\u3000·•,，。；;、()（）_\-/]+")

def run_agent_v2(
    *,
    run_id: str,
    user_message: str,
    orchestrator: Any,
    gateway: ToolGatewayV2,
    planning_agent: Any | None = None,
    reviewer_agent: Any | None = None,
    catalog: FacilityCatalog | None = None,
    run_deadline_monotonic: float | None = None,
) -> AgentRunStateV2:
    """Run one catalog-backed request with bounded provider work and explicit limits.

    Gateway and Agent dependencies are injected so this entry point can be
    exercised completely offline. The outer job manager owns queued/running
    status; this function returns a terminal, serializable result.
    """
    if not isinstance(user_message, str) or not user_message.strip():
        raise ValueError("user_message must contain non-whitespace text")
    catalog = catalog or load_facility_catalog()
    if run_deadline_monotonic is None:
        run_deadline_monotonic = time.monotonic() + MAX_AGENT_RUN_SECONDS
    base = {
        "schema_version": "2.0",
        "run_id": run_id,
        "user_message": user_message.strip(),
    }
    try:
        brief = orchestrator.create_brief(user_message)
    except Exception:
        return AgentRunStateV2(
            **base,
            status="failed",
            poi_results=[],
            limitations=[],
            errors=[ErrorDetail(code="BRIEF_EXTRACTION_FAILED", message="暂时无法解析需求，请稍后重试。", retryable=True)],
        )

    if brief.missing_information:
        questions = "；".join(item.message for item in brief.missing_information)
        return AgentRunStateV2(
            **base,
            status="waiting_for_input",
            brief=brief,
            poi_results=[],
            answer=questions or "请补充设施类别和位置。",
            limitations=_brief_limitations(brief),
        )

    location_request = LocationRequest(schema_version="1.0", input=brief.location)
    try:
        location_result = gateway.resolve_location(
            location_request, deadline_monotonic=run_deadline_monotonic
        ).root
    except Exception:
        return AgentRunStateV2(
            **base,
            status="completed",
            brief=brief,
            poi_results=[],
            limitations=_brief_limitations(brief),
            errors=[ErrorDetail(code="LOCATION_RESOLUTION_FAILED", message="位置解析暂不可用，无法可靠确定查询中心。", retryable=True)],
            answer="暂时无法确定查询位置，因此没有对周边设施作判断。请检查地址，或稍后重试。",
        )
    if isinstance(location_result, LocationFailure):
        return AgentRunStateV2(
            **base,
            status="completed",
            brief=brief,
            poi_results=[],
            limitations=_brief_limitations(brief),
            errors=[ErrorDetail(
                code=str(location_result.error.code),
                message=location_result.error.message,
                retryable=location_result.error.retryable,
            )],
            answer=f"暂时无法定位“{_location_text(brief.location)}”，因此没有检索周边设施。请补充更具体的地址或附近地标。",
        )

    center = location_result.data.center
    limitations = _brief_limitations(brief)
    poi_results = []
    if brief.facility_ids:
        try:
            search_request = POISearchRequestV2(
                schema_version="2.0",
                center=center,
                facility_ids=brief.facility_ids,
                search_radius_m=POI_SEARCH_RADIUS_M,
            )
            poi_results.append(gateway.search_pois(
                search_request,
                deadline_monotonic=run_deadline_monotonic,
                priority_facility_ids=_explicit_priority_facility_ids(
                    user_message, brief.facility_ids, catalog
                ),
            ))
        except Exception:
            poi_results.append(POISearchFailureV2(
                ok=False, error_code="POI_SEARCH_FAILED", message="设施地图检索暂不可用。", retryable=True
            ))
            limitations.append("设施地图检索失败；本次无法据此判断周边点位数量。")

    # Out-of-catalog facilities stay in their own exploratory envelopes and
    # never enter the standard count, diagnosis, or planning evidence.
    for term in brief.exploratory_terms:
        try:
            poi_results.append(gateway.explore_pois(
                term, center, POI_SEARCH_RADIUS_M,
                deadline_monotonic=run_deadline_monotonic,
            ))
        except Exception:
            poi_results.append(POISearchFailureV2(
                ok=False, error_code="EXPLORATORY_SEARCH_FAILED", message="探索性地图检索暂不可用。", retryable=True
            ))
            limitations.append(f"“{term}”的探索性地图检索失败；该类别未进入标准诊断。")

    routing = None
    if brief.intent is AgentIntentV2.ACCESSIBILITY_QUERY or brief.needs_diagnosis:
        if time.monotonic() >= run_deadline_monotonic:
            limitations.append("本次运行达到时间预算，已跳过步行路线计算；已有地图结果仍可查看。")
        else:
            routing, sampled, total, unsampled_facilities = _route_accessible_sample(
                center, brief.facility_ids, poi_results, gateway, catalog,
                deadline_monotonic=run_deadline_monotonic,
            )
            if total == 0:
                limitations.append("没有可用于算路的检索点位，本次无法给出步行时间。")
            elif total > sampled:
                limitations.append(
                    f"步行路线最多抽取 {MAX_ROUTE_TARGETS_PER_RUN} 个代表点（本次 {sampled}/{total} 个）；路线结果不能代表所有点位。"
                )
            if unsampled_facilities:
                names = [catalog.get(item).name_zh for item in unsampled_facilities]
                limitations.append("以下类别本次没有路线样本：" + "、".join(names) + "。")
            if routing is not None and not routing.ok:
                limitations.append("步行路线服务不可用；仍保留地图点位检索结果。")

    diagnosis = None
    if brief.needs_diagnosis and time.monotonic() >= run_deadline_monotonic:
        limitations.append("本次运行达到时间预算，已跳过诊断计算；没有将未完成证据当成完整结论。")
    elif brief.needs_diagnosis:
        try:
            evidence_by_id = _facility_evidence(poi_results, routing)
            diagnosis = gateway.diagnose_community(
                DiagnosisRequestV2(schema_version="2.0", facility_ids=brief.facility_ids),
                evidence_by_id,
            )
        except Exception:
            limitations.append("标准诊断阶段暂不可用；地图检索结果仍可查看。")
        if diagnosis is not None:
            for metric in diagnosis.metrics:
                if metric.status != "complete" and metric.reason_code != "DIAGNOSIS_NOT_SUPPORTED":
                    limitations.append(
                        f"{catalog.get(metric.facility_id).name_zh}的诊断证据不完整；不能据此给出完整覆盖率或供需结论。"
                    )

    planning_proposal = None
    review_result = None
    run_errors: list[ErrorDetail] = []
    if brief.needs_planning and time.monotonic() >= run_deadline_monotonic:
        limitations.append("本次运行达到时间预算，已跳过规划生成与审查。")
    elif brief.needs_planning and planning_agent is not None and diagnosis is not None:
        try:
            planning_evidence = _build_planning_evidence(run_id, brief, poi_results, diagnosis, catalog)
        except Exception as exc:
            limitations.append("规划证据整理失败；不会据此补造设施配置建议。")
            run_errors.append(_planning_stage_error("PLANNING_EVIDENCE_FAILED", exc))
        else:
            unavailable = [
                item.facility_name for item in planning_evidence.items
                if item.poi_status == "unavailable"
            ]
            if unavailable:
                limitations.append(
                    "规划所需的" + "、".join(unavailable)
                    + "地图检索不可用；本次不生成缺少关键证据的规划建议。"
                )
                run_errors.append(ErrorDetail(
                    code="PLANNING_EVIDENCE_UNAVAILABLE",
                    message="规划所需地图证据暂不可用。",
                    retryable=True,
                ))
            else:
                try:
                    planning_proposal = planning_agent.create_proposal(
                        brief, planning_evidence, planning_round=1
                    )
                except Exception as exc:
                    limitations.append("规划建议生成失败；不会据此补造设施配置建议。")
                    run_errors.append(_planning_stage_error("PLANNING_GENERATION_FAILED", exc))
                    planning_proposal = None
            if planning_proposal is not None and time.monotonic() >= run_deadline_monotonic:
                limitations.append("本次运行在规划建议生成后达到时间预算；未启动审查，因此不返回未经审查的建议。")
                planning_proposal = None
            elif planning_proposal is not None and reviewer_agent is None:
                limitations.append("规划建议尚未经过审查，暂不作为正式建议返回。")
                planning_proposal = None
            elif planning_proposal is not None:
                try:
                    review_result = reviewer_agent.review(
                        brief, planning_evidence, planning_proposal
                    )
                except Exception as exc:
                    limitations.append("规划审查失败；未经审查的建议不会返回。")
                    run_errors.append(_planning_stage_error("PLANNING_REVIEW_FAILED", exc))
                    planning_proposal = None
                    review_result = None
                else:
                    if not _review_accepted(review_result):
                        limitations.append("规划建议未通过证据审查，已从最终建议中剔除。")
                        planning_proposal = None
    elif brief.needs_planning:
        limitations.append("当前规划建议流程暂不可用；现有地图点位不足以直接推导规划配置。")

    limitations.extend(_result_limitations(poi_results, brief, catalog))
    limitations = list(dict.fromkeys(limitations))
    answer = _format_answer(brief, poi_results, routing, diagnosis, planning_proposal, limitations, catalog)
    return AgentRunStateV2(
        **base,
        status="completed",
        brief=brief,
        resolved_center=center,
        poi_results=poi_results,
        routing=routing,
        diagnosis=diagnosis,
        planning_proposal=_to_json_dict(planning_proposal),
        review_result=_to_json_dict(review_result),
        limitations=limitations,
        answer=answer,
        errors=run_errors,
    )


def _explicit_priority_facility_ids(
    user_message: str, facility_ids: list[str], catalog: FacilityCatalog
) -> list[str]:
    """Use only explicit focus phrases; ordinary broad requests stay neutral."""
    segments = _FOCUS_SEGMENT.findall(user_message)
    if not segments:
        return []
    focused_text = "".join(_FOCUS_SEPARATORS.sub("", segment).casefold() for segment in segments)
    result = []
    for facility_id in facility_ids:
        definition = catalog.get(facility_id)
        names = (definition.name_zh, *definition.aliases)
        if any(
            len(normalized) >= 2 and normalized in focused_text
            for normalized in (_FOCUS_SEPARATORS.sub("", name).casefold() for name in names)
        ):
            result.append(facility_id)
    return result


def _route_accessible_sample(
    center, facility_ids, poi_results, gateway: ToolGatewayV2, catalog, *, deadline_monotonic=None
):
    grouped: dict[str, list[Any]] = defaultdict(list)
    for envelope in poi_results:
        if not isinstance(envelope, POISearchSuccessV2):
            continue
        for poi in envelope.data.pois:
            if (
                not poi.exploratory
                and poi.facility_id in facility_ids
                and "routing" in catalog.get(poi.facility_id).capabilities
            ):
                grouped[poi.facility_id].append(poi)
    route_facilities = [
        facility_id for facility_id in facility_ids
        if "routing" in catalog.get(facility_id).capabilities and grouped.get(facility_id)
    ]
    unsampled_facilities = []
    if len(route_facilities) > MAX_ROUTE_TARGETS_PER_RUN:
        indices = {
            round(index * (len(route_facilities) - 1) / (MAX_ROUTE_TARGETS_PER_RUN - 1))
            for index in range(MAX_ROUTE_TARGETS_PER_RUN)
        }
        selected_facilities = [item for index, item in enumerate(route_facilities) if index in indices]
        selected_set = set(selected_facilities)
        unsampled_facilities = [item for item in route_facilities if item not in selected_set]
    else:
        selected_facilities = route_facilities
    for values in grouped.values():
        values.sort(key=lambda poi: _distance_m(center, poi.location))
    # Round-robin the nearest points by category so broad requests do not let
    # the largest single category consume the whole 20-target route budget.
    selected = []
    depth = 0
    while len(selected) < MAX_ROUTE_TARGETS_PER_RUN:
        appended = False
        for facility_id in selected_facilities:
            values = grouped.get(facility_id, [])
            if depth < len(values):
                selected.append(values[depth])
                appended = True
                if len(selected) == MAX_ROUTE_TARGETS_PER_RUN:
                    break
        if not appended:
            break
        depth += 1
    total = sum(map(len, grouped.values()))
    if not selected:
        return None, 0, total, unsampled_facilities
    request = RoutingRequest(
        schema_version="1.0",
        origin=center,
        targets=[
            {"target_id": f"{poi.facility_id}:{poi.poi_id}", "location": poi.location}
            for poi in selected
        ],
        travel_mode="walking",
    )
    try:
        result = gateway.calculate_walking_times(
            request, deadline_monotonic=deadline_monotonic
        ).root
    except Exception:
        return RoutingResponseV2(
            ok=False, error_code="ROUTING_FAILED", message="步行路线服务请求失败。", retryable=True
        ), len(selected), total, unsampled_facilities
    if isinstance(result, RoutingFailure):
        return RoutingResponseV2(
            ok=False,
            error_code=str(result.error.code),
            message=result.error.message,
            retryable=result.error.retryable,
        ), len(selected), total, unsampled_facilities
    by_target = {
        f"{poi.facility_id}:{poi.poi_id}": poi.facility_id for poi in selected
    }
    return RoutingResponseV2(
        ok=True,
        routes=[RouteResultV2(
            target_id=route.target_id,
            facility_id=by_target[route.target_id],
            location=route.location,
            status=route.status.value,
            distance_m=route.distance_m,
            duration_s=route.duration_s,
        ) for route in result.data.routes],
        summary=result.data.summary.model_dump(),
    ), len(selected), total, unsampled_facilities


def _facility_evidence(poi_results, routing):
    counts = {}
    for envelope in poi_results:
        if isinstance(envelope, POISearchSuccessV2):
            counts.update({item.facility_id: item for item in envelope.data.counts})
    route_durations: dict[str, list[int]] = defaultdict(list)
    route_failed: set[str] = set()
    if routing is not None and routing.ok:
        for route in routing.routes:
            if route.status == "success" and route.duration_s is not None:
                route_durations[route.facility_id].append(route.duration_s)
            else:
                route_failed.add(route.facility_id)
    result = {}
    for facility_id, count in counts.items():
        durations = route_durations.get(facility_id)
        route_sample_incomplete = (
            count.count is not None
            and count.count > 0
            and (durations is None or len(durations) != count.count)
        )
        is_partial = (
            count.status != "complete"
            or facility_id in route_failed
            or route_sample_incomplete
        )
        result[facility_id] = FacilityEvidenceV2(
            status="partial" if is_partial else "complete",
            poi_count=count.count,
            route_durations_s=durations,
            # No V2 isochrone/population collector exists yet. Keep this null:
            # zero is not evidence of zero population coverage.
            covered_ratio=None,
        )
    return result


def _build_planning_evidence(run_id, brief, poi_results, diagnosis, catalog):
    """Adapt only supported, observed facts to the V2 planner's typed bundle."""
    # V2 planner interface is intentionally duck-typed while its contract is
    # independently developed. It gets only facility-scoped counts/metrics.
    from app.schemas.planning_v2 import PlanningEvidenceBundleV2, PlanningEvidenceItemV2

    counts = {
        item.facility_id: item
        for envelope in poi_results if isinstance(envelope, POISearchSuccessV2)
        for item in envelope.data.counts
    }
    metrics = {item.facility_id: item for item in diagnosis.metrics}
    items = []
    for facility_id in brief.facility_ids:
        definition = catalog.get(facility_id)
        count = counts.get(facility_id)
        metric = metrics.get(facility_id)
        if "planning" not in definition.capabilities:
            continue
        items.append(PlanningEvidenceItemV2(
            facility_id=facility_id,
            facility_name=definition.name_zh,
            poi_count=count.count if count is not None else None,
            poi_status=count.status if count is not None else "unavailable",
            reachable_count=metric.reachable_count if metric is not None else None,
            covered_ratio=metric.covered_ratio if metric is not None else None,
            metric_status=metric.status if metric is not None else "unavailable",
            source="baidu_poi_plus_v2_diagnosis",
            evidence_id=f"{run_id}:evidence:{facility_id}",
        ))
    return PlanningEvidenceBundleV2(
        schema_version="2.0",
        evidence_bundle_id=f"{run_id}:planning-evidence",
        items=items,
    )


def _review_accepted(review_result):
    if isinstance(review_result, dict):
        return review_result.get("status") in {"approved", "accepted", "passed"}
    status = getattr(review_result, "status", None)
    status = getattr(status, "value", status)
    return status in {"approved", "accepted", "passed"}


def _planning_stage_error(code: str, exc: Exception) -> ErrorDetail:
    """Return a stage-specific public error without leaking provider/model text."""
    return ErrorDetail(
        code=code,
        message="规划阶段暂不可用，请稍后重试。",
        retryable=bool(getattr(exc, "retryable", False)),
    )


def _format_answer(brief, poi_results, routing, diagnosis, planning, limitations, catalog):
    lines = []
    if brief.facility_ids:
        counts = {
            item.facility_id: item
            for envelope in poi_results if isinstance(envelope, POISearchSuccessV2)
            for item in envelope.data.counts
        }
        for facility_id in brief.facility_ids:
            definition = catalog.get(facility_id)
            count = counts.get(facility_id)
            if count is None or count.count is None:
                lines.append(f"{definition.name_zh}：地图检索结果暂不可用，不能判断数量。")
                continue
            qualifier = "" if count.status == "complete" else "（检索不完整）"
            examples = [
                poi.name for envelope in poi_results if isinstance(envelope, POISearchSuccessV2)
                for poi in envelope.data.pois if poi.facility_id == facility_id
            ][:3]
            sample = f"，例如：{'、'.join(examples)}" if examples else ""
            lines.append(f"{definition.name_zh}：百度地图在查询范围内检索到 {count.count} 个点位{qualifier}{sample}。")
    for envelope in poi_results:
        if isinstance(envelope, POISearchSuccessV2) and envelope.data.exploratory_query:
            lines.append(f"探索查询“{envelope.data.exploratory_query}”：检索到 {len(envelope.data.pois)} 个候选点位；此结果未纳入标准诊断或规划。")
        elif isinstance(envelope, POISearchFailureV2):
            lines.append(f"一项地图检索失败（{envelope.error_code}），结果不完整。")

    if routing is not None and routing.ok:
        route_groups: dict[str, list[RouteResultV2]] = defaultdict(list)
        for item in routing.routes:
            route_groups[item.facility_id].append(item)
        for facility_id, routes in route_groups.items():
            valid = [item for item in routes if item.status == "success" and item.duration_s is not None]
            if valid:
                nearest = min(valid, key=lambda item: item.duration_s)
                lines.append(
                    f"步行路线抽样中，最近的{catalog.get(facility_id).name_zh}约 {nearest.duration_s // 60} 分钟、{nearest.distance_m} 米；这是路线服务返回值。"
                )
    elif routing is not None:
        lines.append("步行路线暂不可用。")

    if diagnosis is not None:
        for metric in diagnosis.metrics:
            name = catalog.get(metric.facility_id).name_zh
            if metric.status == "unavailable":
                lines.append(f"{name}：当前没有可应用的标准诊断指标或证据。")
            else:
                count_text = "数量未知" if metric.poi_count is None else f"检索到 {metric.poi_count} 个点位"
                lines.append(f"{name}：按目录配置的指标可报告{count_text}；诊断状态为{metric.status}。")
    if planning is not None:
        lines.append("规划建议已通过 V2 证据审查，详见结构化 planning_proposal 字段。")
    if limitations:
        lines.append("限制说明：" + "；".join(limitations))
    lines.append("地图 POI 只能说明地图收录的候选位置，不能证明实际营业、容量、服务质量或人口覆盖；没有检索到也不等于当地一定没有该设施。")
    return "\n".join(lines) if lines else "本次未获得可用设施证据，请补充设施类别或更明确的位置。"


def _brief_limitations(brief):
    return [item.message for item in brief.capability_limitations]


def _result_limitations(poi_results, brief, catalog):
    limits = []
    for envelope in poi_results:
        if isinstance(envelope, POISearchSuccessV2):
            if envelope.data.failed_queries:
                limits.append("部分设施关键词查询失败或超出本次检索预算，相关类别结果可能不完整。")
            if any(item.error_code == "RATE_LIMITED" for item in envelope.data.failed_queries):
                limits.append("百度地图 POI 检索触发限额，部分设施类别尚未查询；不能把不可用结果当作没有设施。")
            if any(item.error_code == "RUN_DEADLINE_EXCEEDED" for item in envelope.data.failed_queries):
                limits.append("本次运行达到时间预算；未完成的设施类别保留为未知，未视为零供给。")
            for count in envelope.data.counts:
                if count.status == "partial":
                    limits.append(f"{catalog.get(count.facility_id).name_zh}检索不完整；检索数量不能视为完整供给总量。")
        elif isinstance(envelope, POISearchFailureV2):
            limits.append("地图 POI 服务请求失败；没有将失败解释为设施数量为零。")
            if envelope.error_code == "RATE_LIMITED":
                limits.append("百度地图 POI 检索触发限额，未完成的设施类别尚未查询。")
            if envelope.error_code == "RUN_DEADLINE_EXCEEDED":
                limits.append("本次运行达到时间预算，地图检索提前停止。")
    return limits


def _location_text(location):
    if hasattr(location, "query"):
        return location.query
    return "用户提供的位置"


def _distance_m(a: CenterPoint, b: CenterPoint) -> float:
    radius = 6_371_000
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp = p2 - p1
    dl = math.radians(b.lng - a.lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(h)))


def _to_json_dict(value):
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return value
    raise TypeError("V2 planner and reviewer outputs must be serializable contracts")


def build_agent_runner_v2():
    """Build the real V2 entry point without making network calls at startup."""
    from app.schemas.agent_jobs_v2 import AgentRunRequestV2

    catalog = load_facility_catalog()
    ak = os.environ.get("BAIDU_MAP_AK", "").strip()
    gateway = MVPToolGatewayV2.from_baidu_ak(ak, catalog)

    llm_key = api_key_from_env()
    if not llm_key:
        def unavailable(request: AgentRunRequestV2) -> AgentRunStateV2:
            return AgentRunStateV2(
                run_id=request.run_id or f"run:v2-{uuid.uuid4().hex[:16]}",
                status="failed",
                user_message=request.user_message,
                errors=[ErrorDetail(code="LLM_KEY_MISSING", message="服务端模型密钥未配置。", retryable=False)],
            )
        return unavailable

    from app.agents.planning_v2 import PlanningAgentV2
    from app.agents.reviewer_v2 import ReviewerAgentV2

    # Three V2 model stages can be used on planning requests. Keep a firm
    # per-call ceiling and one retry so a model outage cannot monopolize the
    # single-worker job queue for many minutes.
    llm = DeepSeekLLM(llm_key, timeout_seconds=30.0, max_retries=1)
    orchestrator = OrchestratorAgentV2(llm, catalog)
    planning_agent = PlanningAgentV2(llm, catalog)
    reviewer_agent = ReviewerAgentV2(llm, catalog)

    def run(request: AgentRunRequestV2) -> AgentRunStateV2:
        run_id = request.run_id or f"run:v2-{uuid.uuid4().hex[:16]}"
        return run_agent_v2(
            run_id=run_id,
            user_message=request.user_message,
            orchestrator=orchestrator,
            gateway=gateway,
            planning_agent=planning_agent,
            reviewer_agent=reviewer_agent,
            catalog=catalog,
        )
    return run
