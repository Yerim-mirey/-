import os

import pytest

from app.modules.routing.service import RoutingService
from app.providers.baidu.routing import BaiduRoutingProvider
from app.schemas.routing import RoutingRequest


@pytest.mark.skipif(
    os.environ.get("RUN_BAIDU_SMOKE") != "1" or not os.environ.get("BAIDU_MAP_AK"),
    reason="set RUN_BAIDU_SMOKE=1 and BAIDU_MAP_AK to run live Baidu tests",
)
def test_real_baidu_walking_matrix() -> None:
    request = RoutingRequest.model_validate(
        {
            "schema_version": "1.0",
            "origin": {"lng": 121.506123, "lat": 31.282456, "crs": "BD09LL"},
            "targets": [
                {
                    "target_id": "live:east",
                    "location": {"lng": 121.515101, "lat": 31.2868, "crs": "BD09LL"},
                }
            ],
            "travel_mode": "walking",
        }
    )
    result = RoutingService(
        BaiduRoutingProvider(os.environ["BAIDU_MAP_AK"], timeout_seconds=12.0)
    ).route(request).root
    assert result.ok is True
    assert result.data.routes[0].distance_m > 0
    assert result.data.routes[0].duration_s > 0
