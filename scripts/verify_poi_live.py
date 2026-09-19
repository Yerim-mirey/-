"""Run one real Baidu POI check without printing the server AK."""

import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.modules.poi.service import search_pois
from app.providers.baidu.poi import BaiduPOIProvider
from app.schemas.poi import POISearchRequest


def _read_ak() -> str:
    ak = os.environ.get("BAIDU_MAP_AK", "").strip()
    if ak:
        return ak
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            name, separator, value = line.partition("=")
            if separator and name.strip() == "BAIDU_MAP_AK":
                return value.strip().strip('"\'')
    raise SystemExit("请先在项目根目录的 .env 中配置 BAIDU_MAP_AK。")


def main() -> None:
    payload = json.loads(
        (ROOT / "contracts/v1/poi-search-request.example.json").read_text(
            encoding="utf-8"
        )
    )
    result = search_pois(
        POISearchRequest.model_validate(payload),
        BaiduPOIProvider(ak=_read_ak()),
    ).root
    print("ok:", result.ok)
    if result.ok:
        print("counts:", result.data.counts.model_dump())
        print("sample names:", [poi.name for poi in result.data.pois[:5]])
        print("warnings:", [item.code for item in result.warnings])
    else:
        print("error:", result.error.code, result.error.message)


if __name__ == "__main__":
    main()
