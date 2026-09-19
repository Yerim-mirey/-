from app.schemas.poi import POISearchRequest
from app.modules.poi.category_mapping import CATEGORY_KEYWORDS
from app.modules.poi.service import POIService
from app.providers.baidu.poi import POIProviderError, ProviderPOI


def request(*facility_types: str) -> POISearchRequest:
    return POISearchRequest.model_validate(
        {
            "schema_version": "1.0",
            "center": {"lng": 121.5, "lat": 31.2, "crs": "BD09LL"},
            "facility_types": list(facility_types),
            "search_radius_m": 1500,
        }
    )


class FakeProvider:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def search(self, keyword, center, radius_m):
        self.calls.append(keyword)
        response = self.responses.get(keyword, [])
        if isinstance(response, Exception):
            raise response
        return response


def poi(uid: str | None, name: str = "店") -> ProviderPOI:
    return ProviderPOI(uid, name, 121.51, 31.21, "示例路1号")


def test_keywords_are_mapped_and_duplicate_pois_are_merged() -> None:
    duplicate = poi("same", "同一超市")
    provider = FakeProvider(
        {
            "菜市场": [duplicate],
            "农贸市场": [duplicate, poi("other", "另一家")],
            "生鲜市场": [],
        }
    )

    result = POIService(provider).search(request("market"))

    assert provider.calls == list(CATEGORY_KEYWORDS["market"])
    assert result.root.ok is True
    assert result.root.data.counts.market == 2
    assert [item.poi_id for item in result.root.data.pois] == [
        "baidu:same",
        "baidu:other",
    ]


def test_one_failed_keyword_makes_category_count_unknown() -> None:
    provider = FakeProvider(
        {
            "菜市场": [poi("known")],
            "农贸市场": POIProviderError("TIMEOUT", "x", True),
            "生鲜市场": [],
        }
    )

    result = POIService(provider).search(request("market"))

    assert result.root.ok is True
    assert result.root.data.counts.market is None
    assert len(result.root.data.pois) == 1
    assert result.root.warnings[0].code == "PARTIAL_POI_RESULTS"


def test_empty_success_is_zero_and_not_failure() -> None:
    provider = FakeProvider({keyword: [] for keyword in CATEGORY_KEYWORDS["market"]})

    result = POIService(provider).search(request("market"))

    assert result.root.ok is True
    assert result.root.data.counts.market == 0
    assert result.root.warnings == []


def test_all_requested_keywords_failing_returns_failure_envelope() -> None:
    provider = FakeProvider(
        {keyword: POIProviderError("RATE_LIMITED", "secret detail", True)
         for keyword in CATEGORY_KEYWORDS["market"]}
    )

    result = POIService(provider).search(request("market"))

    assert result.root.ok is False
    assert result.root.error.code == "RATE_LIMITED"
    assert result.root.error.message == "百度地图 POI 服务请求失败。"
    assert result.root.warnings == []


def test_partial_category_failure_keeps_success_for_other_category() -> None:
    provider = FakeProvider(
        {
            "菜市场": POIProviderError("PROVIDER_ERROR", "x", True),
            "农贸市场": POIProviderError("PROVIDER_ERROR", "x", True),
            "生鲜市场": POIProviderError("PROVIDER_ERROR", "x", True),
            "药店": [],
            "药房": [],
        }
    )

    result = POIService(provider).search(request("market", "pharmacy"))

    assert result.root.ok is True
    assert result.root.data.counts.market is None
    assert result.root.data.counts.pharmacy == 0
    assert result.root.data.counts.primary_school is None
    assert result.root.warnings[0].code == "PARTIAL_POI_RESULTS"


def test_unrequested_categories_remain_unknown() -> None:
    provider = FakeProvider({keyword: [] for keyword in CATEGORY_KEYWORDS["market"]})

    result = POIService(provider).search(request("market"))

    assert result.root.ok is True
    assert result.root.data.counts.market == 0
    assert result.root.data.counts.pharmacy is None
    assert result.root.data.counts.primary_school is None
