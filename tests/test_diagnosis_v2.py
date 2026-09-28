from types import SimpleNamespace
import pytest

from app.modules.poi.facility_catalog import FacilityDefinition, load_facility_catalog

try:
    from app.modules.diagnosis.service_v2 import DiagnosisServiceV2
    from app.schemas.diagnosis_v2 import DiagnosisRequestV2, FacilityEvidenceV2
except ModuleNotFoundError:
    DiagnosisServiceV2 = None
    DiagnosisRequestV2 = None
    FacilityEvidenceV2 = None


def _request(*facility_ids: str) -> DiagnosisRequestV2:
    assert DiagnosisRequestV2 is not None, "V2 diagnosis contracts are not implemented"
    return DiagnosisRequestV2(schema_version="2.0", facility_ids=list(facility_ids))


def _service(catalog):
    assert DiagnosisServiceV2 is not None, "V2 diagnosis service is not implemented"
    return DiagnosisServiceV2(catalog=catalog)


def test_routing_only_facility_is_unavailable_and_never_runs_diagnostic_evidence():
    service = _service(load_facility_catalog())
    calls: list[str] = []

    result = service.diagnose(
        _request("hospital"),
        evidence_provider=lambda facility: calls.append(facility.id) or FacilityEvidenceV2(poi_count=4),
    )

    metric = result.metrics[0]
    assert metric.status == "unavailable"
    assert metric.poi_count is None
    assert "standard_diagnosis" in metric.unsupported_dimensions
    assert calls == []


def test_v2_withholds_population_coverage_until_a_collector_exists():
    service = _service(load_facility_catalog())

    result = service.diagnose(
        _request("market"),
        evidence_provider=lambda _facility: FacilityEvidenceV2(
            status="complete", poi_count=3, route_durations_s=[600, 901, 800], covered_ratio=0.75
        ),
    )

    metric = result.metrics[0]
    assert metric.metric_profile == "legacy_community_15m_1km"
    assert metric.reachable_count == 2
    assert metric.covered_ratio is None
    assert metric.service_distance_m is None
    assert "spatial_coverage" in metric.unsupported_dimensions
    assert metric.status == "complete"


def test_diagnosis_capability_without_metric_profile_is_gated():
    definition = FacilityDefinition(
        id="future_facility", group_id="daily_commerce", name_zh="未来设施", aliases=("未来设施",),
        search_terms=("未来设施",), capabilities=frozenset({"poi_search", "routing", "diagnosis"}),
        metric_profile=None, data_notes="test", basis="test",
    )
    catalog = SimpleNamespace(get=lambda _facility_id: definition)
    service = _service(catalog)
    calls: list[str] = []

    result = service.diagnose(
        _request("future_facility"),
        evidence_provider=lambda facility: calls.append(facility.id) or FacilityEvidenceV2(poi_count=2),
    )

    assert result.metrics[0].status == "unavailable"
    assert "metric_policy" in result.metrics[0].unsupported_dimensions
    assert calls == []


def test_budget_exhaustion_marks_remaining_eligible_categories_unavailable():
    service = _service(load_facility_catalog())
    calls: list[str] = []

    result = service.diagnose(
        _request("market", "pharmacy"),
        evidence_provider=lambda facility: calls.append(facility.id) or FacilityEvidenceV2(
            status="complete", poi_count=1, route_durations_s=[700], covered_ratio=1.0
        ),
        budget_units=1,
    )

    assert [item.status for item in result.metrics] == ["complete", "unavailable"]
    assert result.metrics[1].reason_code == "BUDGET_EXHAUSTED"
    assert calls == ["market"]


def test_mixed_category_results_report_category_and_overall_quality_separately():
    service = _service(load_facility_catalog())

    result = service.diagnose(
        _request("market", "hospital"),
        evidence_provider=lambda _facility: FacilityEvidenceV2(
            status="complete", poi_count=2, route_durations_s=[700, 900], covered_ratio=0.5
        ),
    )

    assert [item.status for item in result.metrics] == ["complete", "unavailable"]
    assert result.quality == "mixed"


def test_partial_evidence_does_not_claim_complete_diagnosis():
    service = _service(load_facility_catalog())

    result = service.diagnose(
        _request("pharmacy"),
        evidence_provider=lambda _facility: FacilityEvidenceV2(
            status="partial", poi_count=None, route_durations_s=None, covered_ratio=None
        ),
    )

    metric = result.metrics[0]
    assert metric.status == "partial"
    assert metric.poi_count is None
    assert metric.reachable_count is None
    assert metric.covered_ratio is None
    assert result.quality == "partial"


def test_complete_evidence_cannot_omit_routes_for_known_pois():
    with pytest.raises(ValueError):
        FacilityEvidenceV2(
            status="complete", poi_count=2, route_durations_s=[], covered_ratio=0.5
        )


def test_programming_errors_from_evidence_provider_are_not_hidden_as_missing_data():
    service = _service(load_facility_catalog())
    with pytest.raises(RuntimeError, match="bug"):
        service.diagnose(
            _request("market"),
            evidence_provider=lambda _facility: (_ for _ in ()).throw(RuntimeError("bug")),
        )
