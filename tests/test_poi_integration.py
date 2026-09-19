"""POI service + Baidu adapter without a real AK or network request."""

import json
from urllib.parse import parse_qs, urlsplit

from app.modules.poi.service import search_pois
from app.providers.baidu.poi import BaiduPOIProvider
from app.schemas.poi import POISearchRequest


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def close(self) -> None:
        pass


def test_baidu_response_flows_through_service_contract() -> None:
    def http_get(url: str, *, timeout: float) -> _Response:
        query = parse_qs(urlsplit(url).query)
        keyword = query["query"][0]
        page = int(query["page_num"][0])
        results = (
            [
                {
                    "uid": "same-market",
                    "name": "示例菜市场",
                    "location": {"lng": 121.51, "lat": 31.28},
                    "address": "示例路1号",
                    "raw_baidu_field": "must not escape",
                }
            ]
            if page == 0 and keyword in {"菜市场", "农贸市场"}
            else []
        )
        return _Response({"status": 0, "total": 1, "results": results})

    request = POISearchRequest.model_validate(
        {
            "schema_version": "1.0",
            "center": {"lng": 121.506123, "lat": 31.282456, "crs": "BD09LL"},
            "facility_types": ["market", "pharmacy", "primary_school"],
            "search_radius_m": 1500,
        }
    )
    result = search_pois(
        request, BaiduPOIProvider(ak="test-ak", http_get=http_get)
    )

    assert result.root.ok is True
    assert result.root.data.counts.market == 1
    assert result.root.data.counts.pharmacy == 0
    assert result.root.data.counts.primary_school == 0
    assert len(result.root.data.pois) == 1
    assert "raw_baidu_field" not in result.model_dump_json()
    assert "test-ak" not in result.model_dump_json()
