"""Application service that turns provider POIs into the public POI contract."""

from hashlib import sha256
import json

from app.providers.baidu.poi import (
    BaiduPOIProvider,
    POIProviderError,
    ProviderPOI,
)
from app.schemas.common import Meta, WarningItem
from app.schemas.poi import (
    FacilityType,
    POI,
    POIErrorCode,
    POICounts,
    POISearchData,
    POISearchFailure,
    POISearchRequest,
    POISearchResult,
    POISearchSuccess,
)

from .category_mapping import CATEGORY_KEYWORDS


_PUBLIC_PROVIDER = "baidu"
_PROVIDER_FAILURE_MESSAGE = "百度地图 POI 服务请求失败。"
_PARTIAL_WARNING_CODE = "PARTIAL_POI_RESULTS"
_CATEGORY_LABELS = {
    "market": "市场",
    "pharmacy": "药房",
    "primary_school": "小学",
}
_CONTRACT_ERROR_CODES = {item.value for item in POIErrorCode}


def _facility_value(value: FacilityType | str) -> str:
    return value.value if isinstance(value, FacilityType) else value


def _uid_key(uid: str) -> str:
    value = uid.strip()
    return value.removeprefix("baidu:").strip()


def _dedupe_key(item: ProviderPOI) -> tuple[str, str] | tuple[str, str, float, float, str]:
    if isinstance(item.uid, str) and _uid_key(item.uid):
        return ("uid", _uid_key(item.uid))
    return (
        "value",
        item.name.strip(),
        item.lng,
        item.lat,
        (item.address or "").strip(),
    )


def _public_id(item: ProviderPOI) -> str:
    if isinstance(item.uid, str) and _uid_key(item.uid):
        return f"baidu:{_uid_key(item.uid)}"

    raw = json.dumps(
        [item.name, item.lng, item.lat, item.address or ""],
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return f"baidu:{sha256(raw.encode("utf-8")).hexdigest()[:24]}"


def _to_public_poi(item: ProviderPOI, facility_type: str) -> POI:
    return POI(
        poi_id=_public_id(item),
        name=item.name,
        facility_type=facility_type,
        location={"lng": item.lng, "lat": item.lat, "crs": "BD09LL"},
        address=item.address or None,
        source="baidu",
    )


def _as_provider_error(error: Exception) -> POIProviderError:
    if isinstance(error, POIProviderError):
        return error
    if isinstance(error, TimeoutError):
        return POIProviderError("TIMEOUT", "provider timeout", True)
    return POIProviderError("PROVIDER_ERROR", "provider request failed", True)


def _contract_error(error: POIProviderError) -> tuple[POIErrorCode, bool]:
    code = error.code.value if isinstance(error.code, POIErrorCode) else str(error.code)
    code = code.upper()
    if code not in _CONTRACT_ERROR_CODES:
        code = POIErrorCode.PROVIDER_ERROR.value
    return POIErrorCode(code), bool(error.retryable)


class POIService:
    """Search requested facility types and build one strict contract result."""

    def __init__(self, provider: BaiduPOIProvider) -> None:
        self.provider = provider

    def search(self, request: POISearchRequest) -> POISearchResult:
        requested = list(dict.fromkeys(_facility_value(item) for item in request.facility_types))
        counts = {facility_type: None for facility_type in CATEGORY_KEYWORDS}
        all_pois: list[POI] = []
        warnings: list[WarningItem] = []
        first_error: POIProviderError | None = None
        any_keyword_succeeded = False

        for facility_type in requested:
            keywords = CATEGORY_KEYWORDS[facility_type]
            by_key: dict[tuple[object, ...], POI] = {}
            category_failed = False

            for keyword in keywords:
                try:
                    provider_items = self.provider.search(
                        keyword, request.center, request.search_radius_m
                    )
                    if not isinstance(provider_items, list):
                        raise POIProviderError(
                            "PROVIDER_ERROR", "invalid provider response", True
                        )
                    public_items = [
                        (_dedupe_key(item), _to_public_poi(item, facility_type))
                        for item in provider_items
                    ]
                except Exception as exc:
                    category_failed = True
                    provider_error = _as_provider_error(exc)
                    first_error = first_error or provider_error
                    continue

                any_keyword_succeeded = True
                for key, public_item in public_items:
                    by_key.setdefault(key, public_item)

            if category_failed:
                counts[facility_type] = None
                warnings.append(
                    WarningItem(
                        code=_PARTIAL_WARNING_CODE,
                        message=f"{_CATEGORY_LABELS[facility_type]}设施数据可能不完整。",
                    )
                )
            else:
                counts[facility_type] = len(by_key)
            all_pois.extend(by_key.values())

        meta = Meta(schema_version="1.0", provider=_PUBLIC_PROVIDER)
        if not any_keyword_succeeded:
            error_code, retryable = _contract_error(
                first_error
                or POIProviderError("PROVIDER_ERROR", "provider request failed", True)
            )
            return POISearchResult(
                root=POISearchFailure(
                    ok=False,
                    error={
                        "code": error_code,
                        "message": _PROVIDER_FAILURE_MESSAGE,
                        "retryable": retryable,
                    },
                    warnings=[],
                    meta=meta,
                )
            )

        return POISearchResult(
            root=POISearchSuccess(
                ok=True,
                data=POISearchData(
                    center=request.center,
                    pois=all_pois,
                    counts=POICounts(**counts),
                ),
                warnings=warnings,
                meta=meta,
            )
        )


def search_pois(
    request: POISearchRequest, provider: BaiduPOIProvider | None = None
) -> POISearchResult:
    """Run one POI search with an injected or default Baidu provider."""

    return POIService(
        provider if provider is not None else BaiduPOIProvider()
    ).search(request)
