from app.agent_runtime.runner_v2 import _route_accessible_sample, run_agent_v2
from app.agents.planning_v2 import facility_planning_limits
from app.modules.diagnosis.service_v2 import DiagnosisServiceV2
from app.modules.poi.facility_catalog import load_facility_catalog
from app.schemas.agent_v2 import AgentBriefV2, AgentIntentV2
from app.schemas.common import CenterPoint
from app.schemas.diagnosis_v2 import DiagnosisDataV2, FacilityEvidenceV2
from app.schemas.location import LocationData, LocationResult, LocationSuccess, LocationSource
from app.schemas.poi_v2 import POISearchDataV2, POISearchSuccessV2, POISearchFailureV2, POIResultV2, FacilityCountV2, FacilityQueryFailureV2
from app.schemas.routing import RoutingResult, RoutingSuccess
from app.schemas.routing_v2 import RoutingResponseV2
from app.schemas.planning_v2 import PlanningIssueV2, PlanningProposalV2, PlanningRecommendationV2, PlanningReviewResultV2


class FixedOrchestrator:
    def __init__(self, brief):
        self.brief = brief

    def create_brief(self, _message):
        return self.brief


class FixedPOIService:
    def __init__(self, result):
        self.result = result
        self.requests = []

    def search(self, request, *, deadline_monotonic=None, priority_facility_ids=()):
        self.requests.append(request)
        return self.result

    def search_exploratory(self, term, center, radius, *, deadline_monotonic=None):
        self.requests.append((term, center, radius))
        return self.result


class FixedLocationResolver:
    def __init__(self, result):
        self.result = result
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return self.result


class FixedRoutingService:
    def __init__(self, result):
        self.result = result
        self.requests = []

    def route(self, request, *, deadline_monotonic=None):
        self.requests.append(request)
        return self.result


class FixedGateway:
    def __init__(self, poi_service=None, location_resolver=None, routing_service=None):
        self.poi_service = poi_service
        self.location_resolver = location_resolver
        self.routing_service = routing_service

    def resolve_location(self, request, *, deadline_monotonic=None):
        return self.location_resolver(request)

    def search_pois(self, request, *, deadline_monotonic=None, priority_facility_ids=()):
        return self.poi_service.search(
            request, deadline_monotonic=deadline_monotonic,
            priority_facility_ids=priority_facility_ids,
        )

    def explore_pois(self, term, center, radius, *, deadline_monotonic=None):
        return self.poi_service.search_exploratory(
            term, center, radius, deadline_monotonic=deadline_monotonic
        )

    def calculate_walking_times(self, request, *, deadline_monotonic=None):
        return self.routing_service.route(request, deadline_monotonic=deadline_monotonic)

    def diagnose_community(self, request, evidence_by_id):
        return DiagnosisServiceV2().diagnose(
            request,
            evidence_provider=lambda definition: evidence_by_id.get(
                definition.id, FacilityEvidenceV2(status="unavailable")
            ),
            budget_units=len(request.facility_ids),
        )


def _brief(*, intent=AgentIntentV2.FACILITY_QUERY, ids=("market",), needs_diagnosis=False, needs_planning=False):
    return AgentBriefV2(
        schema_version="2.0",
        intent=intent,
        user_goal="查附近设施",
        location={"type": "address", "query": "武汉市武昌区中南路"},
        facility_ids=list(ids),
        exploratory_terms=[],
        unresolved_mentions=[],
        exploratory_only=False,
        capability_limitations=[],
        needs_diagnosis=needs_diagnosis,
        needs_planning=needs_planning,
        needs_review=needs_planning,
        missing_information=[],
    )


def _location(center=None):
    center = center or CenterPoint(lng=114.33, lat=30.54, crs="BD09LL")
    return LocationResult(root=LocationSuccess(
        ok=True,
        data=LocationData(center=center, label="武汉市武昌区中南路", formatted_address=None, source=LocationSource.BAIDU_GEOCODING),
        warnings=[],
        meta={"schema_version": "1.0", "provider": "baidu"},
    ))


def _pois(center=None):
    center = center or CenterPoint(lng=114.33, lat=30.54, crs="BD09LL")
    return POISearchSuccessV2(
        ok=True,
        data=POISearchDataV2(
            center=center,
            pois=[POIResultV2(
                poi_id="baidu:market1", facility_id="market", name="中南菜市场",
                location={"lng": 114.331, "lat": 30.541, "crs": "BD09LL"},
                source="baidu", exploratory=False, query_term="菜市场", classification_confidence="medium",
            )],
            counts=[FacilityCountV2(facility_id="market", count=1, status="complete")],
        ),
    )


def test_waits_for_missing_location_without_calling_providers():
    brief = AgentBriefV2(
        schema_version="2.0", intent="facility_query", user_goal="找市场", location=None,
        facility_ids=["market"], unresolved_mentions=[], exploratory_terms=[], exploratory_only=False,
        capability_limitations=[], needs_diagnosis=False, needs_planning=False, needs_review=False,
        missing_information=[{"field": "location", "message": "请提供位置"}],
    )
    pois = FixedPOIService(_pois())
    result = run_agent_v2(
        run_id="run:test1", user_message="找市场", orchestrator=FixedOrchestrator(brief),
        gateway=FixedGateway(pois, FixedLocationResolver(_location()), FixedRoutingService(None)),
    )
    assert result.status == "waiting_for_input"
    assert result.answer
    assert pois.requests == []


def test_runs_address_poi_search_and_returns_grounded_answer():
    pois = FixedPOIService(_pois())
    result = run_agent_v2(
        run_id="run:test2", user_message="找武汉中南路附近菜市场", orchestrator=FixedOrchestrator(_brief()),
        gateway=FixedGateway(pois, FixedLocationResolver(_location()), FixedRoutingService(None)),
    )
    assert result.status == "completed"
    assert result.resolved_center.lng == 114.33
    assert len(result.poi_results) == 1
    assert "1" in result.answer
    assert "中南菜市场" in result.answer
    assert pois.requests[0].facility_ids == ["market"]


def test_broad_run_passes_explicit_focus_to_poi_service():
    class CapturingPOIService(FixedPOIService):
        def search(self, request, *, deadline_monotonic=None, priority_facility_ids=()):
            self.priority_facility_ids = list(priority_facility_ids)
            return super().search(request, deadline_monotonic=deadline_monotonic)

    catalog = load_facility_catalog()
    pois = CapturingPOIService(_pois())
    run_agent_v2(
        run_id="run:focus", user_message="请查学院路全部生活设施，重点关注药店和公园",
        orchestrator=FixedOrchestrator(_brief(ids=tuple(item.id for item in catalog.facilities))),
        gateway=FixedGateway(pois, FixedLocationResolver(_location()), FixedRoutingService(None)),
        catalog=catalog,
    )

    assert pois.priority_facility_ids == ["pharmacy", "park_green"]


def test_accessibility_routes_at_most_twenty_targets():
    routing = FixedRoutingService(RoutingResult(root=RoutingSuccess(
        ok=True,
        data={"origin": {"lng": 114.33, "lat": 30.54, "crs": "BD09LL"}, "travel_mode": "walking",
              "routes": [{"target_id": "market:baidu:market1", "location": {"lng": 114.331, "lat": 30.541, "crs": "BD09LL"}, "status": "success", "distance_m": 180, "duration_s": 190}],
              "summary": {"requested": 1, "success": 1, "no_route": 0, "unavailable": 0}},
        warnings=[], meta={"schema_version": "1.0", "provider": "baidu"},
    )))
    result = run_agent_v2(
        run_id="run:test3", user_message="走路多久", orchestrator=FixedOrchestrator(_brief(intent=AgentIntentV2.ACCESSIBILITY_QUERY)),
        gateway=FixedGateway(FixedPOIService(_pois()), FixedLocationResolver(_location()), routing),
    )
    assert result.status == "completed"
    assert len(routing.requests) == 1
    assert len(routing.requests[0].targets) <= 20
    assert result.routing.ok is True
    assert "190" in result.answer or "3" in result.answer


def test_diagnosis_does_not_claim_complete_without_coverage_evidence():
    result = run_agent_v2(
        run_id="run:test4", user_message="诊断菜市场", orchestrator=FixedOrchestrator(
            _brief(intent=AgentIntentV2.COMMUNITY_DIAGNOSIS, needs_diagnosis=True)
        ), gateway=FixedGateway(
            FixedPOIService(_pois()), FixedLocationResolver(_location()), FixedRoutingService(None)
        ),
    )
    assert isinstance(result.diagnosis, DiagnosisDataV2)
    assert result.diagnosis.metrics[0].status == "partial"
    assert any("覆盖" in item or "人口" in item for item in result.limitations)
    assert "不能据此给出完整覆盖率" in result.answer


def test_planning_output_is_returned_only_after_review_approval():
    class Planner:
        def __init__(self):
            self.evidence = None

        def create_proposal(self, _brief, evidence, planning_round):
            self.evidence = evidence
            assert planning_round == 1
            return PlanningProposalV2(
                schema_version="2.0", catalog_version="2026.1", proposal_id="proposal-1",
                planning_round=1, evidence_bundle_id=evidence.evidence_bundle_id,
                summary="依据可用证据提出有限建议。",
                issues=[PlanningIssueV2(issue_id="issue-1", facility_id="market", title="核对设施点位",
                    description="确认地图点位与实际服务设施一致。", priority="medium",
                    evidence_refs=[evidence.items[0].evidence_id])],
                recommendations=[PlanningRecommendationV2(recommendation_id="rec-1", facility_id="market",
                    title="补充现场核查", rationale="现有资料仅反映地图收录点位。", priority="medium",
                    actions=["核对设施位置与服务属性。"], evidence_refs=[evidence.items[0].evidence_id])],
                unsupported_dimensions=facility_planning_limits(load_facility_catalog(), ["market"]),
            )

    class Reviewer:
        def review(self, _brief, evidence, proposal):
            assert evidence.evidence_bundle_id.endswith(":planning-evidence")
            assert proposal.proposal_id == "proposal-1"
            return PlanningReviewResultV2(status="approved", reviewed_proposal_id=proposal.proposal_id,
                reviewed_planning_round=proposal.planning_round,
                reviewed_evidence_bundle_id=evidence.evidence_bundle_id)

    planner = Planner()
    result = run_agent_v2(
        run_id="run:test5", user_message="规划菜市场", orchestrator=FixedOrchestrator(
            _brief(intent=AgentIntentV2.PLANNING_ANALYSIS, needs_diagnosis=True, needs_planning=True)
        ), gateway=FixedGateway(
            FixedPOIService(_pois()), FixedLocationResolver(_location()), FixedRoutingService(None)
        ), planning_agent=planner, reviewer_agent=Reviewer(),
    )
    assert planner.evidence.items[0].facility_id == "market"
    assert planner.evidence.items[0].poi_count == 1
    assert result.planning_proposal.proposal_id == "proposal-1"
    assert result.review_result.status == "approved"
    assert "已通过 V2 证据审查" in result.answer


def test_planning_generation_failure_is_reported_as_a_stage_error():
    class FailingPlanner:
        def create_proposal(self, *_args, **_kwargs):
            raise RuntimeError("private provider detail")

    result = run_agent_v2(
        run_id="run:test-planning-error", user_message="规划学院路",
        orchestrator=FixedOrchestrator(_brief(
            intent=AgentIntentV2.PLANNING_ANALYSIS,
            needs_diagnosis=True,
            needs_planning=True,
        )),
        gateway=FixedGateway(
            FixedPOIService(_pois()), FixedLocationResolver(_location()), FixedRoutingService(None)
        ),
        planning_agent=FailingPlanner(), reviewer_agent=object(),
    )

    assert [item.code for item in result.errors] == ["PLANNING_GENERATION_FAILED"]
    assert "private provider detail" not in result.errors[0].message


def test_rate_limited_planning_evidence_skips_model_and_explains_missing_data():
    class CountingPlanner:
        calls = 0

        def create_proposal(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("Planner must not run on unavailable POI evidence")

    planner = CountingPlanner()
    center = _location().root.data.center
    poi_result = POISearchSuccessV2(ok=True, data=POISearchDataV2(
        center=center,
        pois=[_pois().data.pois[0].model_copy(update={
            "poi_id": "baidu:school1", "facility_id": "primary_school", "name": "中南小学",
        })],
        counts=[
            FacilityCountV2(facility_id="primary_school", count=1, status="complete"),
            FacilityCountV2(facility_id="pharmacy", count=None, status="unavailable"),
            FacilityCountV2(facility_id="market", count=None, status="unavailable"),
        ],
        failed_queries=[
            FacilityQueryFailureV2(facility_id="pharmacy", query_term="药店", error_code="RATE_LIMITED", retryable=True),
            FacilityQueryFailureV2(facility_id="market", query_term="菜市场", error_code="RATE_LIMITED", retryable=True),
        ],
    ))
    result = run_agent_v2(
        run_id="run:rate-limited-planning", user_message="规划全部服务设施",
        orchestrator=FixedOrchestrator(_brief(
            intent=AgentIntentV2.PLANNING_ANALYSIS,
            ids=("primary_school", "pharmacy", "market"),
            needs_diagnosis=True, needs_planning=True,
        )),
        gateway=FixedGateway(
            FixedPOIService(poi_result), FixedLocationResolver(_location()), FixedRoutingService(None)
        ),
        planning_agent=planner, reviewer_agent=object(),
    )

    assert planner.calls == 0
    assert [item.code for item in result.errors] == ["PLANNING_EVIDENCE_UNAVAILABLE"]
    assert result.planning_proposal is None
    assert any("百度地图" in item and "限额" in item for item in result.limitations)
    assert "检索不可用" in result.answer


def test_planning_review_failure_drops_unreviewed_proposal_and_reports_stage():
    class FixedPlanner:
        def create_proposal(self, _brief, evidence, planning_round):
            evidence_id = evidence.items[0].evidence_id
            return PlanningProposalV2(
                schema_version="2.0", catalog_version="2026.1", proposal_id="proposal-review-error",
                planning_round=planning_round, evidence_bundle_id=evidence.evidence_bundle_id,
                summary="依据现有地图点位建议开展核查。",
                issues=[PlanningIssueV2(
                    issue_id="issue-review-error", facility_id="market", title="核查市场点位",
                    description="确认地图收录点位是否对应服务设施。", priority="medium",
                    evidence_refs=[evidence_id],
                )],
                recommendations=[PlanningRecommendationV2(
                    recommendation_id="rec-review-error", facility_id="market", title="补充核查",
                    rationale="现有证据仅来自地图点位。", priority="medium",
                    actions=["通过现场或管理台账核对设施属性。"], evidence_refs=[evidence_id],
                )],
                unsupported_dimensions=facility_planning_limits(load_facility_catalog(), ["market"]),
            )

    class FailingReviewer:
        def review(self, *_args, **_kwargs):
            raise RuntimeError("private reviewer detail")

    result = run_agent_v2(
        run_id="run:test-review-error", user_message="规划学院路",
        orchestrator=FixedOrchestrator(_brief(
            intent=AgentIntentV2.PLANNING_ANALYSIS,
            needs_diagnosis=True,
            needs_planning=True,
        )),
        gateway=FixedGateway(
            FixedPOIService(_pois()), FixedLocationResolver(_location()), FixedRoutingService(None)
        ),
        planning_agent=FixedPlanner(), reviewer_agent=FailingReviewer(),
    )

    assert [item.code for item in result.errors] == ["PLANNING_REVIEW_FAILED"]
    assert "private reviewer detail" not in result.errors[0].message
    assert result.planning_proposal is None
    assert result.review_result is None


def test_broad_routing_samples_categories_evenly_and_reports_unsampled_ones():
    catalog = load_facility_catalog()
    facility_ids = [item.id for item in catalog.facilities]
    pois = [POIResultV2(
        poi_id=f"baidu:{facility_id}", facility_id=facility_id, name=f"{facility_id}点位",
        location={"lng": 114.331, "lat": 30.541, "crs": "BD09LL"}, source="baidu", exploratory=False,
        query_term=facility_id, classification_confidence="medium",
    ) for facility_id in facility_ids]
    routing_service = FixedRoutingService(None)
    _routing, sampled, total, unsampled = _route_accessible_sample(
        CenterPoint(lng=114.33, lat=30.54, crs="BD09LL"), facility_ids,
        [POISearchSuccessV2(ok=True, data=POISearchDataV2(
            center=CenterPoint(lng=114.33, lat=30.54, crs="BD09LL"), pois=pois, counts=[]
        ))], FixedGateway(routing_service=routing_service), catalog,
    )
    sampled_categories = {target.target_id.removeprefix("baidu:") for target in routing_service.requests[0].targets}
    assert sampled == 20
    assert total == 45
    assert len(sampled_categories) == 20
    assert len(unsampled) == 25
    assert sampled_categories.isdisjoint(unsampled)


def test_broad_routing_uses_available_poi_categories_before_spending_slots():
    catalog = load_facility_catalog()
    center = CenterPoint(lng=114.33, lat=30.54, crs="BD09LL")
    pois = [POIResultV2(
        poi_id=f"baidu:{facility_id}", facility_id=facility_id, name=f"{facility_id}点位",
        location={"lng": 114.331, "lat": 30.541, "crs": "BD09LL"}, source="baidu",
        exploratory=False, query_term=facility_id, classification_confidence="medium",
    ) for facility_id in ("kindergarten", "primary_school")]
    routing_service = FixedRoutingService(None)

    _routing, sampled, total, unsampled = _route_accessible_sample(
        center, [item.id for item in catalog.facilities],
        [POISearchSuccessV2(ok=True, data=POISearchDataV2(
            center=center, pois=pois, counts=[]
        ))], FixedGateway(routing_service=routing_service), catalog,
    )

    assert sampled == total == 2
    assert {item.target_id.split(":", 1)[0] for item in routing_service.requests[0].targets} == {
        "kindergarten", "primary_school",
    }
    assert unsampled == []


def test_same_map_poi_classified_in_two_facilities_gets_unique_route_target_ids():
    center = CenterPoint(lng=114.33, lat=30.54, crs="BD09LL")
    shared_poi = "baidu:shared-place"
    pois = [
        POIResultV2(
            poi_id=shared_poi, facility_id=facility_id, name="社区综合服务点",
            location={"lng": 114.331, "lat": 30.541, "crs": "BD09LL"},
            source="baidu", exploratory=False, query_term=facility_id,
            classification_confidence="medium",
        )
        for facility_id in ("market", "convenience_store")
    ]

    class CapturingRoutingService:
        requests = []

        def route(self, request, *, deadline_monotonic=None):
            self.requests.append(request)
            routes = [
                {"target_id": item.target_id, "location": item.location.model_dump(),
                 "status": "success", "distance_m": 100, "duration_s": 90}
                for item in request.targets
            ]
            return RoutingResult(root=RoutingSuccess(
                ok=True,
                data={"origin": center.model_dump(), "travel_mode": "walking", "routes": routes,
                      "summary": {"requested": len(routes), "success": len(routes), "no_route": 0, "unavailable": 0}},
                warnings=[], meta={"schema_version": "1.0", "provider": "baidu"},
            ))

    routing_service = CapturingRoutingService()
    routing, sampled, total, _unsampled = _route_accessible_sample(
        center, ["market", "convenience_store"],
        [POISearchSuccessV2(ok=True, data=POISearchDataV2(center=center, pois=pois, counts=[]))],
        FixedGateway(routing_service=routing_service), load_facility_catalog(),
    )

    target_ids = [item.target_id for item in routing_service.requests[0].targets]
    assert sampled == total == 2
    assert len(set(target_ids)) == 2
    assert routing.ok is True
    assert {item.facility_id for item in routing.routes} == {"market", "convenience_store"}


def test_timed_out_category_is_reported_as_missing_evidence_not_as_absence():
    """V2 runs on a 3-second POI timeout, so timeouts are an expected failure.

    A timed-out category must stay visibly unknown: it is neither counted as
    zero facilities nor left to read as "this area has none".
    """
    poi_result = POISearchSuccessV2(ok=True, data=POISearchDataV2(
        center=_location().root.data.center,
        pois=[_pois().data.pois[0]],
        counts=[
            FacilityCountV2(facility_id="market", count=1, status="complete"),
            FacilityCountV2(facility_id="pharmacy", count=None, status="unavailable"),
        ],
        failed_queries=[
            FacilityQueryFailureV2(
                facility_id="pharmacy", query_term="药店", error_code="TIMEOUT", retryable=True
            ),
        ],
    ))
    result = run_agent_v2(
        run_id="run:timeout-limits", user_message="查附近菜市场和药店",
        orchestrator=FixedOrchestrator(_brief(ids=("market", "pharmacy"))),
        gateway=FixedGateway(
            FixedPOIService(poi_result), FixedLocationResolver(_location()), FixedRoutingService(None)
        ),
    )

    timeout_limits = [item for item in result.limitations if "超时" in item]
    assert timeout_limits, result.limitations
    assert any("没有此类设施" in item for item in timeout_limits)
    pharmacy = next(item for item in poi_result.data.counts if item.facility_id == "pharmacy")
    assert pharmacy.status == "unavailable"
    assert pharmacy.count is None


def test_timed_out_poi_request_is_not_reported_as_zero_facilities():
    failure = POISearchFailureV2(
        ok=False, error_code="TIMEOUT", message="设施地图检索超时。", retryable=True
    )
    result = run_agent_v2(
        run_id="run:timeout-envelope", user_message="查附近药店",
        orchestrator=FixedOrchestrator(_brief(ids=("pharmacy",))),
        gateway=FixedGateway(
            FixedPOIService(failure), FixedLocationResolver(_location()), FixedRoutingService(None)
        ),
    )

    assert any("超时" in item and "没有此类设施" in item for item in result.limitations)
    assert any("没有将失败解释为设施数量为零" in item for item in result.limitations)


def test_any_failed_query_keeps_the_unknown_is_not_zero_caveat():
    """The caveat cannot depend on the failure code we happened to special-case."""
    poi_result = POISearchSuccessV2(ok=True, data=POISearchDataV2(
        center=_location().root.data.center,
        pois=[],
        counts=[FacilityCountV2(facility_id="pharmacy", count=None, status="unavailable")],
        failed_queries=[
            FacilityQueryFailureV2(
                facility_id="pharmacy", query_term="药店", error_code="NETWORK_ERROR", retryable=True
            ),
        ],
    ))
    result = run_agent_v2(
        run_id="run:network-error-limits", user_message="查附近药店",
        orchestrator=FixedOrchestrator(_brief(ids=("pharmacy",))),
        gateway=FixedGateway(
            FixedPOIService(poi_result), FixedLocationResolver(_location()), FixedRoutingService(None)
        ),
    )

    assert any("不等于该地区没有此类设施" in item for item in result.limitations)
