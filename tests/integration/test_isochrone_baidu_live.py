import os

import pytest

from app.modules.isochrone.service import IsochroneService
from app.modules.routing.service import RoutingService
from app.providers.baidu.routing import BaiduRoutingProvider
from app.schemas.isochrone import IsochroneRequest


@pytest.mark.skipif(
    os.environ.get("RUN_BAIDU_SMOKE") != "1" or not os.environ.get("BAIDU_MAP_AK"),
    reason="set RUN_BAIDU_SMOKE=1 and BAIDU_MAP_AK to run live Baidu tests",
)
def test_real_baidu_routing_builds_isochrone() -> None:
    request = IsochroneRequest.model_validate(
        {
            "schema_version": "1.0",
            "center": {"lng": 121.506123, "lat": 31.282456, "crs": "BD09LL"},
            "time_limit_s": 900,
        }
    )
    routing = RoutingService(
        BaiduRoutingProvider(os.environ["BAIDU_MAP_AK"], timeout_seconds=12.0)
    )

    result = IsochroneService(routing.route).generate(request).root

    assert result.ok is True
    assert result.data.quality.valid_boundary_directions >= 3
    assert result.data.geometry.coordinates[0][0] == result.data.geometry.coordinates[0][-1]
