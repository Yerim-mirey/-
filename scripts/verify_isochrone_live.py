"""Run the real Routing -> Isochrone chain without printing or saving the AK."""

import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.modules.isochrone.service import IsochroneService
from app.modules.routing.service import RoutingService
from app.providers.baidu.routing import BaiduRoutingProvider
from app.schemas.isochrone import IsochroneRequest


def main() -> None:
    ak = os.environ.get("BAIDU_MAP_AK", "").strip()
    if not ak:
        raise SystemExit("请先设置 BAIDU_MAP_AK。")
    request = IsochroneRequest.model_validate(
        {
            "schema_version": "1.0",
            "center": {"lng": 121.506123, "lat": 31.282456, "crs": "BD09LL"},
            "time_limit_s": 900,
        }
    )
    routing = RoutingService(
        BaiduRoutingProvider(ak, timeout_seconds=12.0),
        retry_delay_seconds=0.5,
    )
    result = IsochroneService(routing.route).generate(request).root
    print("ok:", result.ok)
    if not result.ok:
        print("error:", result.error.code.value, result.error.message)
        raise SystemExit(1)

    output = ROOT / "data" / "samples" / "isochrone-live.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("boundary_directions:", result.data.quality.valid_boundary_directions)
    print("routing_samples:", result.data.quality.routing_sample_count)
    print("confidence:", result.data.quality.confidence.value)
    print("saved:", output.relative_to(ROOT))


if __name__ == "__main__":
    main()
