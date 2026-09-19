"""POI output joins to Routing results only through stable POI ids."""

import json
from pathlib import Path

from app.modules.routing.service import RoutingService
from app.providers.baidu.routing import ProviderRoute
from app.schemas.poi import POISearchResult
from app.schemas.routing import RouteTarget, RoutingRequest


CONTRACTS = Path(__file__).resolve().parents[1] / "contracts" / "v1"


class _RoutingProvider:
    def route_matrix(self, origin, destinations):
        return [
            ProviderRoute(distance_m=400 + index * 100, duration_s=300 + index * 60)
            for index, _ in enumerate(destinations)
        ]


def test_poi_results_flow_into_routing_and_join_by_target_id() -> None:
    payload = json.loads(
        (CONTRACTS / "poi-search-result.example.json").read_text(encoding="utf-8")
    )
    poi_result = POISearchResult.model_validate(payload).root
    assert poi_result.ok is True

    routing_request = RoutingRequest(
        schema_version="1.0",
        origin=poi_result.data.center,
        targets=[
            RouteTarget(target_id=poi.poi_id, location=poi.location)
            for poi in poi_result.data.pois
        ],
        travel_mode="walking",
    )
    routing_result = RoutingService(_RoutingProvider()).route(routing_request).root

    assert routing_result.ok is True
    pois_by_id = {poi.poi_id: poi for poi in poi_result.data.pois}
    joined = [
        (pois_by_id[route.target_id].name, route.duration_s)
        for route in routing_result.data.routes
    ]
    assert joined == [("示例大药房", 300)]
