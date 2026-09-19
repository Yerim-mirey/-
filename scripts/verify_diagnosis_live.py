"""Opt-in, bounded real-provider smoke check for Tool 1–6.

Run with BAIDU_MAP_AK and RUN_BAIDU_SMOKE=1. Prints only public summary data.
"""

import os

from app.modules.blindspot.service import analyze_blindspots
from app.modules.diagnosis.service import diagnose
from app.modules.isochrone.service import IsochroneService
from app.modules.location.service import resolve_location
from app.modules.poi.service import POIService
from app.modules.routing.service import RoutingService
from app.providers.baidu.poi import BaiduPOIProvider
from app.providers.baidu.routing import BaiduRoutingProvider
from app.schemas.diagnosis import DiagnosisRequest
from app.schemas.poi import FacilityType


def main() -> int:
    if os.environ.get("RUN_BAIDU_SMOKE") != "1":
        print("Skipped: set RUN_BAIDU_SMOKE=1 to permit real Baidu requests.")
        return 0
    ak = os.environ.get("BAIDU_MAP_AK", "").strip()
    if not ak:
        print("Skipped: BAIDU_MAP_AK is not configured.")
        return 0

    routing = RoutingService(BaiduRoutingProvider(ak), max_retries=0)
    isochrone = IsochroneService(
        routing.route, direction_count=8, initial_radii_m=(400, 900, 1400),
        refinement_rounds=1, max_radius_m=2200,
    )
    poi = POIService(BaiduPOIProvider(ak=ak))
    request = DiagnosisRequest(
        schema_version="1.0",
        location={"type": "coordinate", "lng": 121.506123, "lat": 31.282456, "crs": "BD09LL"},
        facility_types=[FacilityType.PRIMARY_SCHOOL],
    )

    def bounded_blindspot(blindspot_request, route):
        return analyze_blindspots(blindspot_request.model_copy(update={"max_route_pairs": 8}), route)

    result = diagnose(
        request,
        resolve_location=resolve_location,
        search_pois=poi.search,
        route=routing.route,
        generate_isochrone=isochrone.generate,
        analyze_blindspots=bounded_blindspot,
    ).root
    if not result.ok:
        print(f"Diagnosis failed: {result.error.code.value}")
        return 1
    metric = result.data.metrics[0]
    summary = {
        "ok": True,
        "poi_search_radius_m": result.data.poi_search_radius_m,
        "poi_count": metric.poi_count,
        "confirmed_reachable_15m_count": metric.confirmed_reachable_15m_count,
        "blind_ratio": metric.blind_ratio,
        "unknown_ratio": metric.unknown_ratio,
        "stages": result.data.stages.model_dump(mode="json"),
        "quality": result.data.quality.confidence.value,
        "warning_codes": [item.code for item in result.warnings],
    }
    print(summary)
    return 0 if result.data.stages.blindspot.value != "unavailable" else 1


if __name__ == "__main__":
    raise SystemExit(main())
