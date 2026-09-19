"""Minimal Baidu Walking RouteMatrix v2 provider."""

from __future__ import annotations

import json
import math
import socket
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from app.schemas.common import CenterPoint


ROUTING_URL = "https://api.map.baidu.com/routematrix/v2/walking"
MAX_ROUTES = 50
Transport = Callable[[str, float], Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class ProviderRoute:
    distance_m: int
    duration_s: int


class BaiduRoutingError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class BaiduRoutingProvider:
    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 8.0,
        transport: Transport | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("百度服务端 AK 不能为空。")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须是正数。")
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._transport = transport or _get_json

    def route_matrix(
        self, origin: CenterPoint, destinations: Sequence[CenterPoint]
    ) -> list[ProviderRoute]:
        if not 1 <= len(destinations) <= MAX_ROUTES:
            raise BaiduRoutingError(
                "INVALID_REQUEST",
                "单批步行目标数必须在 1 到 50 之间。",
                retryable=False,
            )
        params = {
            "origins": _format_point(origin),
            "destinations": "|".join(_format_point(point) for point in destinations),
            "coord_type": "bd09ll",
            "output": "json",
            "ak": self._api_key,
        }
        payload = self._transport(
            f"{ROUTING_URL}?{urlencode(params)}", self._timeout_seconds
        )
        status = _as_int(payload.get("status"))
        if status != 0:
            raise _status_error(status)
        raw_results = payload.get("result")
        if not isinstance(raw_results, list) or len(raw_results) != len(destinations):
            raise BaiduRoutingError(
                "INVALID_RESPONSE",
                "百度步行批量算路返回数量无效。",
                retryable=False,
            )
        return [_parse_route(item) for item in raw_results]


def _format_point(point: CenterPoint) -> str:
    if point.crs != "BD09LL":
        raise BaiduRoutingError(
            "INVALID_REQUEST", "Routing 仅接受 BD09LL 坐标。", retryable=False
        )
    return f"{point.lat:.6f},{point.lng:.6f}"


def _parse_route(item: Any) -> ProviderRoute:
    if not isinstance(item, Mapping):
        raise _invalid_response()
    distance = item.get("distance")
    duration = item.get("duration")
    if not isinstance(distance, Mapping) or not isinstance(duration, Mapping):
        raise _invalid_response()
    distance_m = _nonnegative_int(distance.get("value"))
    duration_s = _nonnegative_int(duration.get("value"))
    if distance_m is None or duration_s is None or ((distance_m == 0) != (duration_s == 0)):
        raise _invalid_response()
    return ProviderRoute(distance_m=distance_m, duration_s=duration_s)


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return int(round(number))


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _status_error(status: int | None) -> BaiduRoutingError:
    if status == 2:
        return BaiduRoutingError(
            "INVALID_REQUEST", "百度步行批量算路参数无效。", retryable=False
        )
    if status in {4, 302, 401}:
        return BaiduRoutingError(
            "RATE_LIMITED", "百度步行批量算路配额受限。", retryable=True
        )
    return BaiduRoutingError(
        "PROVIDER_ERROR",
        "百度步行批量算路服务返回错误。",
        retryable=status == 1,
    )


def _invalid_response() -> BaiduRoutingError:
    return BaiduRoutingError(
        "INVALID_RESPONSE", "百度步行批量算路响应格式无效。", retryable=False
    )


def _get_json(url: str, timeout_seconds: float) -> Mapping[str, Any]:
    try:
        with urlopen(url, timeout=timeout_seconds) as response:
            payload = json.load(response)
    except HTTPError as exc:
        code = "RATE_LIMITED" if exc.code == 429 else "PROVIDER_ERROR"
        raise BaiduRoutingError(
            code,
            "百度步行批量算路 HTTP 请求失败。",
            retryable=exc.code == 429 or exc.code >= 500,
        ) from None
    except (socket.timeout, TimeoutError):
        raise BaiduRoutingError(
            "TIMEOUT", "百度步行批量算路请求超时。", retryable=True
        ) from None
    except URLError as exc:
        is_timeout = isinstance(getattr(exc, "reason", None), (socket.timeout, TimeoutError))
        raise BaiduRoutingError(
            "TIMEOUT" if is_timeout else "PROVIDER_ERROR",
            "百度步行批量算路请求失败。",
            retryable=True,
        ) from None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise BaiduRoutingError(
            "PROVIDER_ERROR", "百度步行批量算路响应无效。", retryable=False
        ) from None
    if not isinstance(payload, Mapping):
        raise _invalid_response()
    return payload
