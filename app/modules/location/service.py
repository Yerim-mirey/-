"""Location Tool service orchestration."""

from collections.abc import Callable

from app.providers.baidu.geocoding import BaiduGeocodingError, GeocodingMatch
from app.schemas.common import CenterPoint, Meta
from app.schemas.location import (
    AddressInput,
    LocationData,
    LocationErrorCode,
    LocationErrorDetail,
    LocationFailure,
    LocationRequest,
    LocationResult,
    LocationSource,
    LocationSuccess,
)


Geocode = Callable[[str, str | None], GeocodingMatch]


def resolve_location(
    request: LocationRequest,
    geocode: Geocode | None = None,
) -> LocationResult:
    """Resolve one validated Location request into the public envelope."""

    if not isinstance(request.input, AddressInput):
        center = CenterPoint(
            lng=request.input.lng,
            lat=request.input.lat,
            crs=request.input.crs,
        )
        return LocationResult(
            root=LocationSuccess(
                ok=True,
                data=LocationData(
                    center=center,
                    label=None,
                    formatted_address=None,
                    source=LocationSource.INPUT_COORDINATE,
                ),
                warnings=[],
                meta=Meta(schema_version="1.0", provider=None),
            )
        )

    if geocode is None:
        return _failure(
            code=LocationErrorCode.PROVIDER_ERROR,
            message="地址输入需要配置百度地理编码 Provider。",
            retryable=False,
        )

    try:
        match = geocode(request.input.query, request.input.city)
    except BaiduGeocodingError as exc:
        return _failure(
            code=exc.code,
            message=str(exc),
            retryable=exc.retryable,
        )

    return LocationResult(
        root=LocationSuccess(
            ok=True,
            data=LocationData(
                center=match.center,
                label=match.label or request.input.query,
                formatted_address=match.formatted_address,
                source=LocationSource.BAIDU_GEOCODING,
            ),
            warnings=[],
            meta=Meta(schema_version="1.0", provider="baidu"),
        )
    )


def _failure(
    *,
    code: LocationErrorCode,
    message: str,
    retryable: bool,
) -> LocationResult:
    return LocationResult(
        root=LocationFailure(
            ok=False,
            error=LocationErrorDetail(
                code=code,
                message=message,
                retryable=retryable,
            ),
            warnings=[],
            meta=Meta(schema_version="1.0", provider="baidu"),
        )
    )
