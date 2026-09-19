"""Run a real Baidu Walking RouteMatrix check without printing the AK."""

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.modules.routing.service import RoutingService
from app.providers.baidu.routing import BaiduRoutingProvider
from app.schemas.routing import RoutingRequest


def main() -> None:
    ak = os.environ.get("BAIDU_MAP_AK", "").strip()
    if not ak:
        raise SystemExit("请先设置 BAIDU_MAP_AK。")
    request = RoutingRequest.model_validate(
        {
            "schema_version": "1.0",
            "origin": {"lng": 121.506123, "lat": 31.282456, "crs": "BD09LL"},
            "targets": [
                {
                    "target_id": "live:east",
                    "location": {"lng": 121.515101, "lat": 31.2868, "crs": "BD09LL"},
                },
                {
                    "target_id": "live:south",
                    "location": {"lng": 121.5011, "lat": 31.2764, "crs": "BD09LL"},
                },
            ],
            "travel_mode": "walking",
        }
    )
    result = RoutingService(BaiduRoutingProvider(ak)).route(request).root
    print("ok:", result.ok)
    if result.ok:
        print(
            "routes:",
            [
                (route.target_id, route.status.value, route.distance_m, route.duration_s)
                for route in result.data.routes
            ],
        )
        print("warnings:", [warning.code for warning in result.warnings])
    else:
        print("error:", result.error.code.value, result.error.message)


if __name__ == "__main__":
    main()
