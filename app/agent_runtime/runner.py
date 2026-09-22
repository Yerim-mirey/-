"""Execute one Agent v1 run over the declarative graph."""

from dataclasses import dataclass
from typing import Any

from app.agent_runtime import graph
from app.agent_runtime.conditions import (
    MAX_PLANNING_ROUNDS,
    MAX_TOOL_RETRIES,
    can_retry_tool,
)
from app.agent_runtime.state import new_run, update_run
from app.agents.orchestrator import OrchestratorAgent
from app.agents.planning import PlanningAgent
from app.agents.reviewer import ReviewerAgent
from app.agent_tools.gateway import ToolGateway
from app.schemas.agent import (
    AgentIntent,
    AgentRunState,
    CompletionMode,
    EvidenceBundle,
    EvidenceRef,
    LifeCircleBrief,
    RunStatus,
)
from app.schemas.common import CenterPoint, ErrorDetail, WarningItem
from app.schemas.diagnosis import DiagnosisRequest, DiagnosisResult
from app.schemas.location import CoordinateInput, LocationRequest, LocationResult
from app.schemas.poi import POISearchRequest, POISearchResult
from app.schemas.routing import RouteTarget, RoutingRequest, RoutingResult

# 15-minute walking scale; the Diagnosis request carries its own radius.
POI_SEARCH_RADIUS_M = 1000

INSUFFICIENT_EVIDENCE_CODE = "INSUFFICIENT_EVIDENCE"
NO_ROUTING_TARGET_CODE = "NO_ROUTING_TARGET"
TOOL_FAILED_CODE = "TOOL_FAILED"
GATEWAY_ERROR_CODE = "GATEWAY_ERROR"
UNEXPECTED_ERROR_CODE = "UNEXPECTED_ERROR"


@dataclass(frozen=True)
class _ToolFailure:
    """One failed Tool or Gateway exchange, before it becomes a Run error."""

    code: str
    message: str
    retryable: bool
    warnings: list[WarningItem]


def run_agent(
    *,
    run_id: str,
    user_message: str,
    orchestrator: OrchestratorAgent,
    gateway: ToolGateway,
    planning_agent: PlanningAgent | None = None,
    reviewer_agent: ReviewerAgent | None = None,
) -> AgentRunState:
    """Run one user request to a terminal RunStatus through the Agent graph."""
    if not isinstance(user_message, str) or not user_message.strip():
        raise ValueError("user_message must contain non-whitespace text")
    if not isinstance(orchestrator, OrchestratorAgent):
        raise TypeError("orchestrator must be an OrchestratorAgent")
    if not isinstance(gateway, ToolGateway):
        raise TypeError("gateway must satisfy the ToolGateway protocol")
    if planning_agent is not None and not isinstance(planning_agent, PlanningAgent):
        raise TypeError("planning_agent must be a PlanningAgent")
    if reviewer_agent is not None and not isinstance(reviewer_agent, ReviewerAgent):
        raise TypeError("reviewer_agent must be a ReviewerAgent")
    state = new_run(run_id, user_message)
    try:
        node: str = graph.START
        while node not in {graph.FINALIZE, graph.REQ_WAIT_FOR_INPUT}:
            if node == graph.START:
                node = graph.ORCHESTRATE
                continue
            if node == graph.ORCHESTRATE:
                state = _orchestrate(state, orchestrator)
                requirement = graph.terminal_requirement(state, node)
            elif node == graph.FETCH_EVIDENCE:
                state = _fetch_evidence(state, gateway)
                if state.status is RunStatus.FAILED:
                    return state
                # A consumed Tool retry has no evidence yet; loop back to this node.
                requirement = (
                    graph.REQ_FETCH_EVIDENCE
                    if state.evidence is None
                    else graph.terminal_requirement(state, node)
                )
            elif node == graph.PLAN:
                state = _plan(state, planning_agent)
                requirement = graph.terminal_requirement(state, node)
            elif node == graph.REVIEW:
                state = _review(state, reviewer_agent)
                requirement = graph.terminal_requirement(state, node)
            else:
                raise RuntimeError(f"Unknown graph node: {node}")
            if requirement == graph.REQ_WAIT_FOR_INPUT:
                return state
            node = graph.next_node(node, requirement)
        return _finalize(state)
    except Exception as exc:  # noqa: BLE001 - the runner owns the top-level error
        return _failed(state, UNEXPECTED_ERROR_CODE, exc)


def _orchestrate(state: AgentRunState, orchestrator: OrchestratorAgent) -> AgentRunState:
    brief = orchestrator.create_brief(state.user_message)
    status = RunStatus.WAITING_FOR_INPUT if brief.missing_information else RunStatus.RUNNING
    return update_run(state, status=status, brief=brief)


def _fetch_evidence(state: AgentRunState, gateway: ToolGateway) -> AgentRunState:
    brief = _brief(state)
    if brief.location is None:
        raise ValueError("Run cannot fetch evidence without a location")
    # A fetch after existing evidence is an evidence refresh and consumes one
    # unit of the shared Tool retry budget. Tool failures spend their own unit
    # in _failure_state and are only resumed while budget remains.
    if state.evidence is not None:
        if not can_retry_tool(state):
            return _failed(
                state,
                GATEWAY_ERROR_CODE,
                RuntimeError("Tool retry budget exhausted before the evidence refresh"),
            )
        state = update_run(state, tool_retry_count=state.tool_retry_count + 1)
    # Attempt 1 is the first fetch; attempt n follows n-1 consumed retries.
    attempt = state.tool_retry_count + 1
    return _fetch_from_tools(state, gateway, brief, attempt)


def _failure_state(state: AgentRunState, failure: _ToolFailure) -> AgentRunState:
    """Spend one Tool retry for a failed exchange, or fail the Run."""
    if not failure.retryable:
        return _failed(
            state, failure.code, RuntimeError(failure.message), warnings=failure.warnings
        )
    spent = state.tool_retry_count + 1
    return _retry_or_fail(
        update_run(state, tool_retry_count=spent), failure
    )


def _fetch_from_tools(
    state: AgentRunState,
    gateway: ToolGateway,
    brief: LifeCircleBrief,
    attempt: int,
) -> AgentRunState:
    """Call the Tools needed by one fetch attempt and store the evidence."""

    try:
        location_result = gateway.resolve_location(
            LocationRequest(schema_version="1.0", input=brief.location)
        )
    except Exception as exc:  # noqa: BLE001 - Mock and MVP Gateways may raise
        return _failure_state(
            state,
            _ToolFailure(code=GATEWAY_ERROR_CODE, message=str(exc), retryable=True, warnings=[]),
        )
    if not location_result.root.ok:
        return _failure_state(
            state, _failure(TOOL_FAILED_CODE, "resolve_location", location_result)
        )
    location = location_result.root.data.center

    if brief.needs_full_diagnosis:
        bundle = _fetch_diagnosis(state, gateway, brief, location, attempt)
    else:
        bundle = _fetch_pois(state, gateway, brief, location, location_result, attempt)
        if isinstance(bundle, EvidenceBundle) and _asks_for_walking_time(brief):
            bundle = _add_routing(state, gateway, location, bundle)
    if isinstance(bundle, AgentRunState):
        return bundle
    return _store_evidence(state, bundle, brief.needs_planning)


def _asks_for_walking_time(brief: LifeCircleBrief) -> bool:
    """Only accessibility questions need Routing on top of the POI facts."""
    return brief.intent is AgentIntent.ACCESSIBILITY_QUERY


def _add_routing(
    state: AgentRunState,
    gateway: ToolGateway,
    origin: CenterPoint,
    bundle: EvidenceBundle,
) -> EvidenceBundle | AgentRunState:
    """Route from the resolved centre to every POI; POI-only evidence if none."""
    pois = bundle.poi.root.data.pois
    if not pois:
        return _with_warning(
            bundle,
            WarningItem(
                code=NO_ROUTING_TARGET_CODE,
                message="本次检索没有可用设施，无法给出步行时间事实。",
            ),
        )
    request = RoutingRequest(
        schema_version="1.0",
        origin=origin,
        targets=[RouteTarget(target_id=poi.poi_id, location=poi.location) for poi in pois],
        travel_mode="walking",
    )
    try:
        routing = gateway.calculate_walking_times(request)
    except Exception as exc:  # noqa: BLE001 - Mock and MVP Gateways may raise
        return _failure_state(
            state,
            _ToolFailure(code=GATEWAY_ERROR_CODE, message=str(exc), retryable=True, warnings=[]),
        )
    if not routing.root.ok:
        return _failure_state(
            state, _failure(TOOL_FAILED_CODE, "calculate_walking_times", routing)
        )
    return _with_routing(bundle, routing)


def _with_warning(bundle: EvidenceBundle, warning: WarningItem) -> EvidenceBundle:
    return EvidenceBundle.model_validate({
        **bundle.model_dump(mode="json"),
        "warnings": _merged_warnings(bundle.warnings, [warning]),
    })


def _with_routing(bundle: EvidenceBundle, routing: RoutingResult) -> EvidenceBundle:
    return EvidenceBundle.model_validate({
        **bundle.model_dump(mode="json"),
        "refs": [ref.model_dump(mode="json") for ref in bundle.refs]
        + [ref.model_dump(mode="json") for ref in _routing_refs(routing)],
        "routing": routing.model_dump(mode="json"),
        "warnings": _merged_warnings(bundle.warnings, list(routing.root.warnings)),
    })


def _store_evidence(
    state: AgentRunState, bundle: EvidenceBundle, needs_planning: bool
) -> AgentRunState:
    """Store evidence, clearing stale downstream results in the same update."""
    replacing = state.evidence is not None and state.evidence != bundle
    changes: dict[str, Any] = {
        "evidence": bundle,
        "warnings": _merged_warnings(state.warnings, bundle.warnings),
    }
    if needs_planning:
        changes["planning_round"] = 1
    elif replacing:
        changes["planning_round"] = 0
    if replacing:
        changes["planning_proposal"] = None
        changes["review_result"] = None
    return update_run(state, **changes)


def _fetch_diagnosis(
    state: AgentRunState,
    gateway: ToolGateway,
    brief: LifeCircleBrief,
    location: CenterPoint,
    attempt: int,
) -> EvidenceBundle | AgentRunState:
    try:
        diagnosis = gateway.diagnose_community(
            DiagnosisRequest(
                schema_version="1.0",
                location=CoordinateInput(
                    type="coordinate", lng=location.lng, lat=location.lat, crs=location.crs
                ),
                facility_types=brief.facility_types,
            )
        )
    except Exception as exc:  # noqa: BLE001
        return _failure_state(
            state,
            _ToolFailure(code=GATEWAY_ERROR_CODE, message=str(exc), retryable=True, warnings=[]),
        )
    if not diagnosis.root.ok:
        return _failure_state(
            state, _failure(TOOL_FAILED_CODE, "diagnose_community", diagnosis)
        )
    return _diagnosis_evidence(f"evidence:{_slug(state.run_id)}-d{attempt}", diagnosis)


def _fetch_pois(
    state: AgentRunState,
    gateway: ToolGateway,
    brief: LifeCircleBrief,
    location: CenterPoint,
    location_result: LocationResult,
    attempt: int,
) -> EvidenceBundle | AgentRunState:
    try:
        poi = gateway.search_pois(
            POISearchRequest(
                schema_version="1.0",
                center=location,
                facility_types=brief.facility_types,
                search_radius_m=POI_SEARCH_RADIUS_M,
            )
        )
    except Exception as exc:  # noqa: BLE001
        return _failure_state(
            state,
            _ToolFailure(code=GATEWAY_ERROR_CODE, message=str(exc), retryable=True, warnings=[]),
        )
    if not poi.root.ok:
        return _failure_state(state, _failure(TOOL_FAILED_CODE, "search_pois", poi))
    return _poi_evidence(f"evidence:{_slug(state.run_id)}-p{attempt}", location_result, poi)


def _plan(state: AgentRunState, planning_agent: PlanningAgent | None) -> AgentRunState:
    if planning_agent is None:
        raise ValueError("Planning requires a PlanningAgent")
    if state.planning_round >= 1 and state.review_result is not None:
        # A consumed round is finished: advance it and drop the reviewed proposal
        # in the same atomic update so no stale pair can survive.
        state = update_run(
            state,
            planning_round=state.planning_round + 1,
            planning_proposal=None,
            review_result=None,
        )
    proposal = planning_agent.create_proposal(
        _brief(state), _evidence(state), state.planning_round
    )
    return update_run(state, planning_proposal=proposal)

def _review(state: AgentRunState, reviewer_agent: ReviewerAgent | None) -> AgentRunState:
    if reviewer_agent is None:
        raise ValueError("Review requires a ReviewerAgent")
    result = reviewer_agent.review_proposal(_evidence(state), _proposal(state))
    state = update_run(state, review_result=result)
    if result.status.value == "revision_required":
        return state
    if result.status.value == "insufficient_evidence" and not _has_refresh_budget(state):
        # The last evidence refresh cannot close the gap. The warning is the
        # deterministic limited-completion flag that routes REVIEW to FINALIZE.
        return update_run(
            state,
            warnings=_merged_warnings(
                state.warnings,
                [
                    WarningItem(
                        code=INSUFFICIENT_EVIDENCE_CODE,
                        message="补证据重试预算已用尽，只能按现有证据带限制完成。",
                    )
                ],
            ),
        )
    return state


def _finalize(state: AgentRunState) -> AgentRunState:
    brief = _brief(state)
    if state.evidence is None:
        return _failed(
            state, UNEXPECTED_ERROR_CODE, ValueError("Run reached finalize without evidence")
        )
    mode = CompletionMode.NORMAL
    if brief.needs_planning:
        review = state.review_result
        if review is None or review.status.value != "approved":
            mode = CompletionMode.WITH_LIMITATIONS
    return update_run(state, status=RunStatus.COMPLETED, completion_mode=mode)


def _has_refresh_budget(state: AgentRunState) -> bool:
    """A refresh needs room for one more Tool retry beyond the ones already spent."""
    return state.tool_retry_count + 1 <= MAX_TOOL_RETRIES


def _retry_or_fail(state: AgentRunState, failure: _ToolFailure) -> AgentRunState:
    if failure.retryable and can_retry_tool(state):
        warnings = state.warnings
        if failure.code == GATEWAY_ERROR_CODE:
            warnings = _merged_warnings(warnings, failure.warnings)
        return update_run(state, warnings=warnings)
    return _failed(state, failure.code, RuntimeError(failure.message), warnings=failure.warnings)


def _failed(
    state: AgentRunState,
    code: str,
    error: Exception,
    warnings: list[WarningItem] | None = None,
) -> AgentRunState:
    changes: dict[str, Any] = {
        "status": RunStatus.FAILED,
        "errors": [ErrorDetail(code=code, message=str(error), retryable=False)],
    }
    if warnings:
        changes["warnings"] = _merged_warnings(state.warnings, warnings)
    return update_run(state, **changes)


def _failure(code: str, method: str, result: Any) -> _ToolFailure:
    detail = result.root.error
    warnings = list(result.root.warnings)
    message = f"{method} failed: {detail.code} {detail.message}"
    if warnings:
        message = f"{message} | " + "; ".join(f"{item.code} {item.message}" for item in warnings)
    return _ToolFailure(
        code=code,
        message=message,
        retryable=bool(detail.retryable),
        warnings=warnings,
    )


def _brief(state: AgentRunState):
    if state.brief is None:
        raise ValueError("Run has no Brief")
    return state.brief


def _evidence(state: AgentRunState) -> EvidenceBundle:
    if state.evidence is None:
        raise ValueError("Run has no Evidence")
    return state.evidence


def _proposal(state: AgentRunState):
    if state.planning_proposal is None:
        raise ValueError("Run has no PlanningProposal")
    return state.planning_proposal


def _slug(run_id: str) -> str:
    return run_id.split(":", 1)[1]


def _pointer(*tokens: object) -> str:
    escaped = [str(token).replace("~", "~0").replace("/", "~1") for token in tokens]
    return "/" + "/".join(escaped)


def _location_ref(location: LocationResult) -> EvidenceRef:
    center = location.root.data.center
    return EvidenceRef(
        evidence_id="location:center",
        kind="location",
        json_pointer=_pointer("location", "data", "center"),
        summary=(
            f"中心点 ({center.lng}, {center.lat}) 由 "
            f"{location.root.data.source.value} 解析得到"
        ),
    )


def _poi_refs(poi: POISearchResult) -> list[EvidenceRef]:
    return [
        EvidenceRef(
            evidence_id=f"poi:facility-{index}",
            kind="poi",
            json_pointer=_pointer("poi", "data", "pois", index),
            summary=f"已检索设施：{item.name}（{item.facility_type.value}）",
        )
        for index, item in enumerate(poi.root.data.pois)
    ]


def _routing_refs(routing: RoutingResult) -> list[EvidenceRef]:
    return [
        EvidenceRef(
            evidence_id=f"routing:target-{index}",
            kind="routing",
            json_pointer=_pointer("routing", "data", "routes", index),
            summary=f"步行路由 {route.target_id} 的状态与时长来自 Routing Tool",
        )
        for index, route in enumerate(routing.root.data.routes)
    ]


def _metric_refs(diagnosis: DiagnosisResult) -> list[EvidenceRef]:
    return [
        EvidenceRef(
            evidence_id=f"diagnosis:facility-{metric.facility_type.value}",
            kind="diagnosis",
            json_pointer=_pointer("diagnosis", "data", "metrics", index, "blind_ratio"),
            summary=(
                f"{metric.facility_type.value} 盲区比例来自 Diagnosis 指标"
                f"（POI 数量 {metric.poi_count}）"
            ),
        )
        for index, metric in enumerate(diagnosis.root.data.metrics)
    ]


def _diagnosis_evidence(bundle_id: str, diagnosis: DiagnosisResult) -> EvidenceBundle:
    return EvidenceBundle(
        schema_version="1.0",
        bundle_id=bundle_id,
        refs=_metric_refs(diagnosis),
        diagnosis=diagnosis,
        warnings=list(diagnosis.root.warnings),
    )


def _poi_evidence(
    bundle_id: str, location: LocationResult, poi: POISearchResult
) -> EvidenceBundle:
    return EvidenceBundle(
        schema_version="1.0",
        bundle_id=bundle_id,
        refs=[_location_ref(location), *_poi_refs(poi)],
        location=location,
        poi=poi,
        warnings=list(location.root.warnings) + list(poi.root.warnings),
    )


def _merged_warnings(
    existing: list[WarningItem], extra: list[WarningItem]
) -> list[WarningItem]:
    merged = list(existing)
    seen = {(item.code, item.message) for item in merged}
    for item in extra:
        if (item.code, item.message) not in seen:
            merged.append(item)
            seen.add((item.code, item.message))
    return merged
