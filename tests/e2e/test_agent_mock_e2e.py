"""End-to-end Agent v1 chains over controlled models and Mock Tools.

Every scenario runs the real ``run_agent`` over the real Graph and condition
functions with a controlled ``StructuredLLM`` and Mock Tool exchanges, so the
matrix proves component boundaries, state invalidation, and routing together.
Scenarios are offline only.
"""

import pytest

from app.agent_tools.mock_gateway import MockToolGateway
from tests.e2e import helpers as e2e


# --- Intent sufficiency: Phase 7 gaps found by this phase --------------------


def test_accessibility_question_is_backed_by_walking_time_facts(no_network):
    """An accessibility question must carry Routing facts, not only POI counts."""
    llm = e2e.SceneLLM(outputs={
        "orchestrator": e2e.extraction(
            intent="accessibility_query",
            user_goal="查询到最近菜市场和药店的步行时间",
            facility_types=["pharmacy", "market"],
        ),
    })
    gateway = e2e.accessibility_gateway(
        pois=[e2e.NEARBY_POIS[0], e2e.NEARBY_POIS[1]],
        facility_types=["pharmacy", "market"],
    )
    state = e2e.run_scene(llm=llm, gateway=gateway, user_message="到最近的菜场和药店要走多久？")

    assert state.status.value == "completed"
    assert state.completion_mode.value == "normal"
    assert state.evidence.routing is not None, "可达性提问必须由 Routing 事实支撑"
    routes = {route.target_id: route for route in state.evidence.routing.root.data.routes}
    assert routes["baidu:e2e-pharmacy"].duration_s == 1012
    assert routes["baidu:e2e-market"].duration_s == 560
    assert e2e.called_methods(gateway) == [
        "resolve_location", "search_pois", "calculate_walking_times",
    ]
    assert any(ref.kind.value == "routing" for ref in state.evidence.refs)
    e2e.assert_evidence_refs_resolve(state)
    e2e.assert_planning_untouched(state)


def test_accessibility_without_pois_does_not_invent_walking_time(no_network):
    """No routable facility means no Routing call and an explicit limitation."""
    llm = e2e.SceneLLM(outputs={
        "orchestrator": e2e.extraction(
            intent="accessibility_query",
            user_goal="查询到最近药店的步行时间",
            facility_types=["pharmacy"],
        ),
    })
    gateway = e2e.gateway(
        e2e.location_exchange(),
        e2e.exchange(
            "search_pois",
            e2e.poi_request(facility_types=["pharmacy"]),
            e2e.poi_result(
                pois=[],
                counts={"market": None, "pharmacy": 0, "primary_school": None},
                requested=["pharmacy"],
            ),
        ),
    )
    state = e2e.run_scene(llm=llm, gateway=gateway, user_message="到最近的药店要走多久？")

    assert state.status.value == "completed"
    assert state.evidence.routing is None, "没有目标设施时不能编造步行时间"
    assert e2e.called_methods(gateway) == ["resolve_location", "search_pois"]
    assert any(item.code == "NO_ROUTING_TARGET" for item in state.warnings)
    e2e.assert_evidence_refs_resolve(state)


def test_blindspot_question_is_backed_by_diagnosis_coverage(no_network):
    """A blindspot question must carry Diagnosis coverage, not POI counts."""
    llm = e2e.SceneLLM(outputs={
        "orchestrator": e2e.standard_extraction(
            "blindspot_query", "查询该社区菜市场、药店和小学的服务盲区"
        ),
    })
    state = e2e.run_scene(
        llm=llm,
        gateway=MockToolGateway(),
        user_message="这个社区哪些地方是菜市场、药店和小学的服务盲区？",
    )

    assert state.brief.needs_full_diagnosis is True
    assert state.status.value == "completed"
    assert state.evidence.diagnosis is not None, "盲区提问必须由 Diagnosis 盲区覆盖支撑"
    assert state.evidence.poi is None, "Diagnosis 证据不得与低层 POI 结果混装"
    coverage = {
        item.facility_type.value for item in state.evidence.diagnosis.root.data.blindspot.coverage
    }
    assert coverage == {"market", "pharmacy", "primary_school"}
    e2e.assert_evidence_refs_resolve(state)
    e2e.assert_planning_untouched(state)


# --- Missing input ----------------------------------------------------------


def test_missing_location_waits_for_input_without_any_tool_call(no_network):
    llm = e2e.SceneLLM(outputs={
        "orchestrator": e2e.extraction(location=None, facility_types=["market"]),
    })
    gateway = e2e.facility_query_gateway()
    state = e2e.run_scene(llm=llm, gateway=gateway, user_message="帮我看看这边的菜市场")

    assert state.status.value == "waiting_for_input"
    assert state.completion_mode is None
    assert state.brief is not None and state.brief.missing_information
    assert state.brief.missing_information[0].field.value == "location"
    e2e.assert_no_tool_calls(gateway)
    assert llm.roles_called() == ["orchestrator"], "缺信息时不得调用 Planning/Reviewer"
    e2e.assert_planning_untouched(state)


def test_missing_facility_types_waits_for_input(no_network):
    llm = e2e.SceneLLM(outputs={
        "orchestrator": e2e.extraction(facility_types=[], use_standard_facilities=False),
    })
    gateway = e2e.facility_query_gateway()
    state = e2e.run_scene(llm=llm, gateway=gateway, user_message="帮我看看附近的设施")

    assert state.status.value == "waiting_for_input"
    assert state.brief.missing_information[0].field.value == "facility_types"
    e2e.assert_no_tool_calls(gateway)
    assert llm.roles_called() == ["orchestrator"]


def test_coordinate_entry_takes_the_same_evidence_path(no_network):
    """A BD09LL coordinate Brief must reach the same facts as an address Brief."""
    coordinate = {"type": "coordinate", **e2e.CENTER}
    llm = e2e.SceneLLM(outputs={"orchestrator": e2e.extraction(location=coordinate)})
    gateway = e2e.gateway(
        e2e.location_exchange(coordinate),
        e2e.exchange("search_pois", e2e.poi_request(), e2e.poi_result()),
    )
    state = e2e.run_scene(llm=llm, gateway=gateway, user_message="121.506123,31.282456 这里的设施")

    assert state.status.value == "completed"
    assert state.evidence.location.root.data.source.value == "input_coordinate"
    center = state.evidence.location.root.data.center
    assert (center.lng, center.lat) == (e2e.CENTER["lng"], e2e.CENTER["lat"])
    assert e2e.called_methods(gateway) == ["resolve_location", "search_pois"]
    e2e.assert_evidence_refs_resolve(state)


# --- Facility query, including 0 versus null --------------------------------


def test_facility_query_completes_with_confirmed_counts(no_network):
    llm = e2e.SceneLLM(outputs={"orchestrator": e2e.extraction()})
    gateway = e2e.facility_query_gateway()
    state = e2e.run_scene(llm=llm, gateway=gateway, user_message="看看菜场、药店和小学在哪")

    assert state.status.value == "completed"
    assert state.completion_mode.value == "normal"
    counts = state.evidence.poi.root.data.counts
    assert (counts.market, counts.pharmacy, counts.primary_school) == (1, 1, 1)
    refs = {ref.evidence_id for ref in state.evidence.refs}
    assert refs == {"location:center", "poi:facility-0", "poi:facility-1", "poi:facility-2"}
    assert e2e.called_methods(gateway) == ["resolve_location", "search_pois"]
    e2e.assert_evidence_refs_resolve(state)
    e2e.assert_planning_untouched(state)


def test_confirmed_absence_and_unknown_counts_stay_distinct(no_network):
    """0 is a confirmed absence; null is unknown and must carry a warning."""
    empty_llm = e2e.SceneLLM(outputs={"orchestrator": e2e.extraction()})
    empty = e2e.run_scene(
        llm=empty_llm,
        gateway=e2e.facility_query_gateway(e2e.empty_poi_result()),
        user_message="这里有没有菜场、药店和小学？",
    )
    counts = empty.evidence.poi.root.data.counts
    assert (counts.market, counts.pharmacy, counts.primary_school) == (0, 0, 0)
    assert empty.evidence.poi.root.data.pois == []
    assert empty.warnings == []

    unknown_llm = e2e.SceneLLM(outputs={"orchestrator": e2e.extraction()})
    unknown = e2e.run_scene(
        llm=unknown_llm,
        gateway=e2e.facility_query_gateway(e2e.unknown_count_poi_result()),
        user_message="这里有没有菜场、药店和小学？",
    )
    counts = unknown.evidence.poi.root.data.counts
    assert counts.market == 1
    assert counts.pharmacy is None and counts.primary_school is None
    assert any(item.code == "PARTIAL_POI_RESULTS" for item in unknown.warnings)
    e2e.assert_evidence_refs_resolve(unknown)


# --- Full diagnosis ---------------------------------------------------------


def test_full_diagnosis_uses_three_facility_metrics(no_network):
    llm = e2e.SceneLLM(outputs={
        "orchestrator": e2e.standard_extraction("community_diagnosis", "完整体检社区设施"),
    })
    state = e2e.run_scene(
        llm=llm,
        gateway=MockToolGateway(),
        user_message="请对这个社区做一次 15 分钟生活圈完整体检",
    )

    assert state.status.value == "completed"
    assert state.completion_mode.value == "normal"
    diagnosis = state.evidence.diagnosis
    assert diagnosis is not None
    assert state.evidence.poi is None and state.evidence.routing is None
    metrics = [metric.facility_type.value for metric in diagnosis.root.data.metrics]
    assert metrics == e2e.ALL_FACILITIES
    assert {ref.evidence_id for ref in state.evidence.refs} == {
        "diagnosis:facility-market",
        "diagnosis:facility-pharmacy",
        "diagnosis:facility-primary_school",
    }
    e2e.assert_evidence_refs_resolve(state)
    e2e.assert_planning_untouched(state)


# --- Planning loop ----------------------------------------------------------


def test_planning_analysis_approved_end_to_end(no_network):
    bundle_id = "evidence:e2e-0001-d1"
    proposal = e2e.proposal_for(1, bundle_id, e2e.ALL_FACILITIES)
    llm = e2e.SceneLLM(outputs={
        "orchestrator": e2e.standard_extraction("planning_analysis", "体检并提出建议"),
        "planning": proposal,
        "reviewer": e2e.review_for("approved", proposal, bundle_id),
    })
    state = e2e.run_scene(
        llm=llm,
        gateway=MockToolGateway(),
        user_message="请体检这个社区并给出改善建议",
    )

    assert state.status.value == "completed"
    assert state.completion_mode.value == "normal"
    assert state.planning_round == 1
    assert state.review_result.status.value == "approved"
    assert llm.roles_called() == ["orchestrator", "planning", "reviewer"]
    e2e.assert_evidence_refs_resolve(state)
    e2e.assert_planning_chain(state)


def test_revision_required_replans_and_approves(no_network):
    """The second round uses a new round number and drops the consumed review."""
    seen_rounds = []

    def planning_call(message):
        proposal = e2e.proposal_from_message(message)
        seen_rounds.append(proposal["planning_round"])
        return proposal

    def reviewer_call(message):
        current = e2e.payload(message)["proposal"]["planning_round"]
        status = "approved" if current == 2 else "revision_required"
        return e2e.review_from_message(message, status)

    llm = e2e.SceneLLM(
        outputs={"orchestrator": e2e.standard_extraction("planning_analysis", "体检并提出建议")},
        callables={"planning": planning_call, "reviewer": reviewer_call},
    )
    state = e2e.run_scene(
        llm=llm,
        gateway=MockToolGateway(),
        user_message="请体检这个社区并给出改善建议",
    )

    assert seen_rounds == [1, 2]
    assert state.status.value == "completed"
    assert state.completion_mode.value == "normal"
    assert state.planning_round == 2
    assert state.planning_proposal.planning_round == 2
    assert state.review_result.status.value == "approved"
    assert state.review_result.reviewed_planning_round == 2
    assert state.review_result.reviewed_proposal_id == state.planning_proposal.proposal_id
    e2e.assert_planning_chain(state)


def test_planning_round_cap_completes_with_limitations(no_network):
    seen_rounds = []

    def planning_call(message):
        proposal = e2e.proposal_from_message(message)
        seen_rounds.append(proposal["planning_round"])
        return proposal

    llm = e2e.SceneLLM(
        outputs={"orchestrator": e2e.standard_extraction("planning_analysis", "体检并提出建议")},
        callables={
            "planning": planning_call,
            "reviewer": lambda message: e2e.review_from_message(message, "revision_required"),
        },
    )
    state = e2e.run_scene(
        llm=llm,
        gateway=MockToolGateway(),
        user_message="请体检这个社区并给出改善建议",
    )

    assert seen_rounds == [1, 2, 3]
    assert state.status.value == "completed"
    assert state.completion_mode.value == "with_limitations"
    assert state.planning_round == 3
    e2e.assert_planning_chain(state)


def test_insufficient_evidence_refreshes_then_completes_with_limitations(no_network):
    """A refresh replaces Evidence, so the consumed chain must not survive."""
    planning_bundles = []

    def planning_call(message):
        data = e2e.payload(message)
        planning_bundles.append(data["evidence"]["bundle_id"])
        return e2e.proposal_from_message(message)

    def reviewer_call(message):
        return e2e.review_from_message(message, "insufficient_evidence")

    scripted = e2e.refresh_gateway(complete_first=True)
    llm = e2e.SceneLLM(
        outputs={"orchestrator": e2e.standard_extraction("planning_analysis", "体检并提出建议")},
        callables={"planning": planning_call, "reviewer": reviewer_call},
    )
    state = e2e.run_scene(
        llm=llm, gateway=scripted, user_message="请体检这个社区并给出改善建议"
    )

    assert state.status.value == "completed"
    assert state.completion_mode.value == "with_limitations"
    assert state.tool_retry_count == 2, "补证据必须消耗 Tool 重试预算"
    assert state.evidence.diagnosis.root.data.stages.poi.value == "partial"
    assert planning_bundles == [
        "evidence:e2e-0001-d1",
        "evidence:e2e-0001-d2",
        "evidence:e2e-0001-d3",
    ]
    assert any(item.code == "PARTIAL_DIAGNOSIS" for item in state.warnings)
    assert any(item.code == "INSUFFICIENT_EVIDENCE" for item in state.warnings)
    e2e.assert_evidence_refs_resolve(state)
    e2e.assert_planning_chain(state)


# --- Tool failure handling --------------------------------------------------


def test_retryable_tool_failure_then_success(no_network):
    gateway = e2e.ScriptedToolGateway(
        {"resolve_location": [RuntimeError("mock transport failure")]},
        fallback=e2e.facility_query_gateway(),
    )
    llm = e2e.SceneLLM(outputs={"orchestrator": e2e.extraction()})
    state = e2e.run_scene(llm=llm, gateway=gateway, user_message="看看菜场、药店和小学在哪")

    assert state.status.value == "completed"
    assert state.tool_retry_count == 1
    assert e2e.called_methods(gateway) == ["resolve_location", "resolve_location", "search_pois"]
    assert state.errors == []
    e2e.assert_evidence_refs_resolve(state)


def test_exhausted_tool_retries_fail_without_partial_evidence(no_network):
    gateway = e2e.ScriptedToolGateway(
        {"resolve_location": [RuntimeError("down"), RuntimeError("down"), RuntimeError("down")]},
    )
    llm = e2e.SceneLLM(outputs={"orchestrator": e2e.extraction()})
    state = e2e.run_scene(llm=llm, gateway=gateway, user_message="看看菜场、药店和小学在哪")

    assert state.status.value == "failed"
    assert state.completion_mode is None
    assert state.tool_retry_count == 2
    assert state.errors[0].code == "GATEWAY_ERROR"
    assert state.evidence is None, "失败时不得留下半成品证据"


def test_non_retryable_tool_failure_fails_immediately(no_network):
    gateway = e2e.ScriptedToolGateway(
        {"search_pois": [e2e.poi_failure_result(retryable=False)]},
        fallback=e2e.facility_query_gateway(),
    )
    llm = e2e.SceneLLM(outputs={"orchestrator": e2e.extraction()})
    state = e2e.run_scene(llm=llm, gateway=gateway, user_message="看看菜场、药店和小学在哪")

    assert state.status.value == "failed"
    assert state.errors[0].code == "TOOL_FAILED"
    assert "search_pois failed" in state.errors[0].message
    assert state.evidence is None
    assert e2e.called_methods(gateway) == ["resolve_location", "search_pois"]


def test_unconfigured_mock_exchange_fails_instead_of_faking_success(no_network):
    """A request the Mock does not serve must fail loudly, not pass silently."""
    llm = e2e.SceneLLM(outputs={"orchestrator": e2e.extraction()})
    state = e2e.run_scene(
        llm=llm,
        gateway=e2e.gateway(e2e.location_exchange()),
        user_message="看看菜场、药店和小学在哪",
    )

    assert state.status.value == "failed"
    assert state.errors[0].code == "GATEWAY_ERROR"
    assert "No Mock exchange configured" in state.errors[0].message
    assert state.evidence is None
