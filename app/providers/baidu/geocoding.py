"""Minimal Baidu geocoding V3 provider."""

from __future__ import annotations

import json
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from pydantic import ValidationError

from app.schemas.common import CenterPoint
from app.schemas.location import LocationErrorCode


GEOCODING_URL = "https://api.map.baidu.com/geocoding/v3/"
Transport = Callable[[str, float], Mapping[str, Any]]


@dataclass(frozen=True)
class GeocodingMatch:
    center: CenterPoint
    label: str | None = None
    formatted_address: str | None = None


class BaiduGeocodingError(RuntimeError):
    def __init__(
        self,
        code: LocationErrorCode,
        message: str,
        *,
        retryable: bool,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class BaiduGeocodingProvider:
    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 5.0,
        transport: Transport | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("百度服务端 AK 不能为空。")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0。")
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._transport = transport or _get_json

    def geocode(self, query: str, city: str | None = None) -> GeocodingMatch:
        params = {
            "address": query,
            "ak": self._api_key,
            "output": "json",
            "extension_poi_infos": "true",
        }
        if city:
            params["city"] = city

        url = f"{GEOCODING_URL}?{urlencode(params)}"
        payload = self._transport(url, self._timeout_seconds)
        status = payload.get("status")

        if status != 0:
            raise _status_error(status)

        result = payload.get("result")
        if result is None or result == {}:
            raise BaiduGeocodingError(
                LocationErrorCode.LOCATION_NOT_FOUND,
                "未找到匹配的位置。",
                retryable=False,
            )
        if not isinstance(result, Mapping):
            raise BaiduGeocodingError(
                LocationErrorCode.PROVIDER_ERROR,
                "百度地图地理编码响应格式无效。",
                retryable=False,
            )

        location = result.get("location")
        if not isinstance(location, Mapping):
            raise BaiduGeocodingError(
                LocationErrorCode.PROVIDER_ERROR,
                "百度地图地理编码响应缺少坐标。",
                retryable=False,
            )
        try:
            center = CenterPoint(
                lng=float(location["lng"]),
                lat=float(location["lat"]),
                crs="BD09LL",
            )
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise BaiduGeocodingError(
                LocationErrorCode.PROVIDER_ERROR,
                "百度地图地理编码返回了无效坐标。",
                retryable=False,
            ) from exc

        poi_info = _first_mapping(payload.get("poi_infos"))
        label = _clean_text(poi_info.get("name")) if poi_info else None
        formatted_address = (
            _clean_text(poi_info.get("formatted_address")) if poi_info else None
        )
        return GeocodingMatch(
            center=center,
            label=label or query,
            formatted_address=formatted_address,
        )


def _get_json(url: str, timeout_seconds: float) -> Mapping[str, Any]:
    try:
        with urlopen(url, timeout=timeout_seconds) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except (TimeoutError, socket.timeout) as exc:
        raise BaiduGeocodingError(
            LocationErrorCode.TIMEOUT,
            "百度地图地理编码请求超时。",
            retryable=True,
        ) from exc
    except HTTPError as exc:
        if exc.code == 429:
            raise BaiduGeocodingError(
                LocationErrorCode.RATE_LIMITED,
                "百度地图地理编码服务触发限流。",
                retryable=True,
            ) from exc
        raise BaiduGeocodingError(
            LocationErrorCode.PROVIDER_ERROR,
            "百度地图地理编码服务请求失败。",
            retryable=500 <= exc.code < 600,
        ) from exc
    except URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise BaiduGeocodingError(
                LocationErrorCode.TIMEOUT,
                "百度地图地理编码请求超时。",
                retryable=True,
            ) from exc
        raise BaiduGeocodingError(
            LocationErrorCode.PROVIDER_ERROR,
            "无法连接百度地图地理编码服务。",
            retryable=True,
        ) from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BaiduGeocodingError(
            LocationErrorCode.PROVIDER_ERROR,
            "百度地图地理编码响应无法解析。",
            retryable=False,
        ) from exc

    if not isinstance(payload, Mapping):
        raise BaiduGeocodingError(
            LocationErrorCode.PROVIDER_ERROR,
            "百度地图地理编码响应格式无效。",
            retryable=False,
        )
    return payload


def _status_error(status: object) -> BaiduGeocodingError:
    if status == 2:
        return BaiduGeocodingError(
            LocationErrorCode.INVALID_REQUEST,
            "百度地图拒绝了地理编码请求参数。",
            retryable=False,
        )
    if status == 4 or status == 401 or (
        isinstance(status, int) and 300 <= status < 400
    ):
        return BaiduGeocodingError(
            LocationErrorCode.RATE_LIMITED,
            "百度地图地理编码服务配额或并发已达上限。",
            retryable=True,
        )
    return BaiduGeocodingError(
        LocationErrorCode.PROVIDER_ERROR,
        "百度地图地理编码服务返回错误。",
        retryable=status == 1,
    )


def _first_mapping(value: object) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, list) and value and isinstance(value[0], Mapping):
        return value[0]
    return None


def _clean_text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
