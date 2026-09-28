from app.modules.poi.facility_catalog import load_facility_catalog
from app.modules.poi.service_v2 import POIServiceV2
from app.schemas.common import CenterPoint
from app.schemas.poi_v2 import POISearchRequestV2
from app.providers.baidu.poi import ProviderPOI, POIProviderError


class FakeProvider:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def search(self, keyword, center, radius_m):
        self.calls.append(keyword)
        response = self.responses[keyword]
        if isinstance(response, Exception):
            raise response
        return response


def _request(*facility_ids):
    return POISearchRequestV2(
        schema_version="2.0",
        center=CenterPoint(lng=114.3, lat=30.5, crs="BD09LL"),
        facility_ids=list(facility_ids),
        search_radius_m=1500,
    )


def test_v2_deduplicates_results_across_registry_search_terms():
    same = ProviderPOI("123", "市民图书馆", 114.3, 30.5, None)
    provider = FakeProvider({"图书馆": [same], "社区图书室": [same]})
    result = POIServiceV2(provider, load_facility_catalog()).search(_request("library"))

    assert result.ok is True
    assert len(result.data.pois) == 1
    assert result.data.counts[0].count == 1
    assert result.data.counts[0].status == "complete"


def test_v2_keeps_partial_and_unavailable_separate_from_confirmed_empty():
    provider = FakeProvider({
        "社区卫生服务中心": [],
        "社区卫生中心": POIProviderError("NETWORK_ERROR", "offline", True),
        "幼儿园": POIProviderError("NETWORK_ERROR", "offline", True),
    })
    result = POIServiceV2(provider, load_facility_catalog()).search(
        _request("community_health_center", "kindergarten")
    )

    counts = {item.facility_id: item for item in result.data.counts}
    assert counts["community_health_center"].status == "partial"
    assert counts["community_health_center"].count == 0
    assert counts["kindergarten"].status == "unavailable"
    assert counts["kindergarten"].count is None


def test_exploratory_search_is_marked_and_has_no_standard_counts():
    provider = FakeProvider({"宠物医院": [ProviderPOI("pet-1", "宠物医院", 114.3, 30.5, None)]})
    result = POIServiceV2(provider, load_facility_catalog()).search_exploratory(
        "宠物医院", CenterPoint(lng=114.3, lat=30.5, crs="BD09LL"), 1500
    )

    assert result.ok is True
    assert result.data.exploratory_query == "宠物医院"
    assert result.data.counts == []
    assert result.data.pois[0].exploratory is True
    assert result.data.pois[0].facility_id == "custom_query"


def test_request_keyword_budget_is_bounded_and_unsearched_terms_are_reported():
    class EmptyProvider:
        def __init__(self):
            self.calls = []

        def search(self, keyword, center, radius_m):
            self.calls.append(keyword)
            return []

    provider = EmptyProvider()
    catalog = load_facility_catalog()
    request = _request(*(item.id for item in catalog.facilities))
    result = POIServiceV2(provider, catalog).search(request)

    assert len(provider.calls) <= POIServiceV2.MAX_SEARCH_TERMS_PER_REQUEST
    assert any(item.error_code == "QUERY_BUDGET_LIMIT" for item in result.data.failed_queries)
    assert all(item.count is not None for item in result.data.counts)


def test_broad_search_queries_every_primary_term_before_adaptive_supplements():
    class EmptyProvider:
        def __init__(self):
            self.calls = []

        def search(self, keyword, center, radius_m):
            self.calls.append(keyword)
            return []

    catalog = load_facility_catalog()
    provider = EmptyProvider()
    result = POIServiceV2(provider, catalog).search(
        _request(*(item.id for item in catalog.facilities))
    )

    primary_terms = [item.search_terms[0] for item in catalog.facilities]
    assert provider.calls[:len(primary_terms)] == primary_terms
    assert len(provider.calls) == POIServiceV2.MAX_SEARCH_TERMS_PER_REQUEST
    assert len(provider.calls) > len(primary_terms)
    assert any(item.error_code == "QUERY_BUDGET_LIMIT" for item in result.data.failed_queries)


def test_adaptive_supplement_prefers_a_primary_keyword_with_no_results():
    catalog = load_facility_catalog()
    primary_terms = {item.search_terms[0] for item in catalog.facilities}

    class SparseProvider:
        def __init__(self):
            self.calls = []

        def search(self, keyword, center, radius_m):
            self.calls.append(keyword)
            if keyword == "菜市场":
                return []
            if keyword == "农贸市场":
                return [ProviderPOI("market-1", "中南农贸市场", center.lng, center.lat, None)]
            if keyword in primary_terms:
                return [ProviderPOI(keyword, keyword, center.lng, center.lat, None)]
            return []

    provider = SparseProvider()
    result = POIServiceV2(provider, catalog).search(
        _request(*(item.id for item in catalog.facilities))
    )

    assert provider.calls[len(catalog.facilities)] == "农贸市场"
    market = next(item for item in result.data.counts if item.facility_id == "market")
    assert market.count == 1


def test_explicit_focus_gets_supplemented_before_other_empty_categories():
    class EmptyProvider:
        def __init__(self):
            self.calls = []

        def search(self, keyword, center, radius_m):
            self.calls.append(keyword)
            return []

    catalog = load_facility_catalog()
    provider = EmptyProvider()
    result = POIServiceV2(provider, catalog).search(
        _request(*(item.id for item in catalog.facilities)),
        priority_facility_ids=["market"],
    )

    assert provider.calls[len(catalog.facilities):len(catalog.facilities) + 2] == [
        "农贸市场", "生鲜市场",
    ]
    market = next(item for item in result.data.counts if item.facility_id == "market")
    assert market.count == 0
    assert market.status == "complete"


def test_internal_poi_deadline_stops_supplements_and_keeps_remaining_terms_unknown(monkeypatch):
    elapsed = [0.0]
    monkeypatch.setattr("app.modules.poi.service_v2.time.monotonic", lambda: elapsed[0])

    class SlowProvider:
        def __init__(self):
            self.calls = []

        def search(self, keyword, center, radius_m):
            self.calls.append(keyword)
            elapsed[0] += 4.0
            return []

    catalog = load_facility_catalog()
    provider = SlowProvider()
    result = POIServiceV2(provider, catalog).search(
        _request(*(item.id for item in catalog.facilities)), deadline_monotonic=1000.0
    )

    assert len(provider.calls) == len(catalog.facilities)
    assert any(item.error_code == "RUN_DEADLINE_EXCEEDED" for item in result.data.failed_queries)
    assert not any(item.error_code == "QUERY_BUDGET_LIMIT" for item in result.data.failed_queries)


def test_all_provider_failures_keep_each_failed_query_reason():
    class FailingProvider:
        def search(self, keyword, center, radius_m):
            raise POIProviderError("NETWORK_ERROR", "offline", True)

    result = POIServiceV2(FailingProvider(), load_facility_catalog()).search(
        _request("community_health_center", "kindergarten")
    )

    assert result.ok is False
    assert {item.facility_id for item in result.failed_queries} == {
        "community_health_center", "kindergarten"
    }
    assert all(item.error_code == "NETWORK_ERROR" for item in result.failed_queries)


def test_partial_page_cap_keeps_returned_pois_and_marks_count_partial():
    class CappedProvider:
        def search(self, keyword, center, radius_m):
            raise POIProviderError(
                "INCOMPLETE_RESULTS", "page budget reached", False,
                partial_results=[ProviderPOI("page-1", "社区图书室", center.lng, center.lat, None)],
            )

    result = POIServiceV2(CappedProvider(), load_facility_catalog()).search(_request("library"))

    assert result.ok is True
    assert result.data.counts[0].status == "partial"
    assert result.data.counts[0].count == 1
    assert result.data.pois[0].name == "社区图书室"
    assert result.data.failed_queries[0].error_code == "INCOMPLETE_RESULTS"


def test_unexpected_provider_programming_error_is_not_hidden_as_partial_data():
    class BrokenProvider:
        def search(self, keyword, center, radius_m):
            raise RuntimeError("bug")

    import pytest
    with pytest.raises(RuntimeError, match="bug"):
        POIServiceV2(BrokenProvider(), load_facility_catalog()).search(_request("kindergarten"))


def test_shared_quota_refusal_short_circuits_remaining_terms_and_categories():
    class RateLimitedProvider:
        def __init__(self):
            self.calls = []

        def search(self, keyword, center, radius_m):
            self.calls.append(keyword)
            raise POIProviderError("RATE_LIMITED", "quota wait exceeded", True)

    provider = RateLimitedProvider()
    result = POIServiceV2(provider, load_facility_catalog()).search(
        _request("market", "pharmacy", "primary_school")
    )

    assert result.ok is False
    assert len(provider.calls) == 1
    assert {item.facility_id for item in result.failed_queries} == {
        "market", "pharmacy", "primary_school"
    }
    assert all(item.error_code == "RATE_LIMITED" for item in result.failed_queries)


def test_search_deadline_preserves_unknown_instead_of_calling_provider():
    class NeverCalledProvider:
        def search(self, keyword, center, radius_m):
            raise AssertionError("deadline should stop before provider access")

    result = POIServiceV2(NeverCalledProvider(), load_facility_catalog()).search(
        _request("market", "pharmacy"), deadline_monotonic=0.0
    )

    assert result.ok is False
    assert result.error_code == "RUN_DEADLINE_EXCEEDED"
    assert result.retryable is False
    assert {item.facility_id for item in result.failed_queries} == {"market", "pharmacy"}
