"""Small adapter for Baidu Maps Place API v2 circle searches."""

from __future__ import annotations

import json
import math
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from app.schemas.common import CenterPoint


@dataclass(frozen=True, slots=True)
class ProviderPOI:
    """Provider-neutral POI data returned by a search adapter."""

    uid: str | None
    name: str
    lng: float
    lat: float
    address: str | None


class POIProviderError(RuntimeError):
    """A normalized, non-secret error from a POI provider."""

    def __init__(self, code: str, message: str, retryable: bool) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(message)


class BaiduPOIProvider:
    """Fetch basic POIs from Baidu's Place API v2 circle-search endpoint."""

    API_URL = "https://api.map.baidu.com/place/v2/search"
    PAGE_SIZE = 20
    # Baidu documents a maximum total of 150 results for a paged request.
    MAX_PAGES = math.ceil(150 / PAGE_SIZE)

    def __init__(
        self,
        *,
        ak: str | None = None,
        timeout: float = 10.0,
        http_get: Callable[..., Any] | None = None,
        endpoint: str = API_URL,
    ) -> None:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a positive finite number")
        self._ak = (os.environ.get("BAIDU_MAP_AK", "") if ak is None else ak).strip()
        self._timeout = timeout
        self._http_get = http_get
        self._endpoint = endpoint

    def search(
        self, keyword: str, center: CenterPoint, radius_m: int
    ) -> list[ProviderPOI]:
        """Search all available pages and return only normalized POI fields."""

        keyword, lng, lat, radius_m = self._validate_request(
            keyword, center, radius_m
        )
        if not self._ak:
            raise POIProviderError(
                "MISSING_AK", "Baidu Maps AK is not configured.", False
            )

        pois: list[ProviderPOI] = []
        for page_num in range(self.MAX_PAGES):
            params = {
                "query": keyword,
                "location": f"{lat},{lng}",
                "radius": str(radius_m),
                "radius_limit": "true",
                "output": "json",
                "coord_type": "3",
                "page_size": str(self.PAGE_SIZE),
                "page_num": str(page_num),
                "ak": self._ak,
            }
            payload = self._get_json(params)
            page = self._parse_page(payload)
            # radius_limit=true can make total and per-page sizes inaccurate.
            if not page.results:
                return pois
            pois.extend(self._parse_poi(item) for item in page.results)

        raise POIProviderError(
            "INCOMPLETE_RESULTS",
            "Baidu Place API pagination reached the result limit.",
            False,
        )

    def _get_json(self, params: Mapping[str, str]) -> Mapping[str, Any]:
        url = f"{self._endpoint}?{urllib.parse.urlencode(params)}"
        try:
            response = (
                self._http_get if self._http_get is not None else urllib.request.urlopen
            )(
                url, timeout=self._timeout
            )
            try:
                body = response.read() if hasattr(response, "read") else response
            finally:
                close = getattr(response, "close", None)
                if close is not None:
                    close()
        except urllib.error.HTTPError as exc:
            status = getattr(exc, "code", None)
            retryable = isinstance(status, int) and (status >= 500 or status in {408, 429})
            code = (
                "RATE_LIMITED"
                if status == 429
                else "TIMEOUT"
                if status == 408
                else "HTTP_ERROR"
            )
            raise POIProviderError(
                code,
                "Baidu Place API returned an HTTP error.",
                retryable,
            ) from None
        except (socket.timeout, TimeoutError):
            raise POIProviderError(
                "TIMEOUT", "Baidu Place API request timed out.", True
            ) from None
        except urllib.error.URLError as exc:
            if isinstance(getattr(exc, "reason", None), (socket.timeout, TimeoutError)):
                raise POIProviderError(
                    "TIMEOUT", "Baidu Place API request timed out.", True
                ) from None
            raise POIProviderError(
                "NETWORK_ERROR", "Baidu Place API request failed.", True
            ) from None
        except OSError:
            raise POIProviderError(
                "NETWORK_ERROR", "Baidu Place API request failed.", True
            ) from None
        except Exception:
            raise POIProviderError(
                "NETWORK_ERROR", "Baidu Place API request failed.", True
            ) from None

        if isinstance(body, Mapping):
            payload = body
        else:
            try:
                payload = json.loads(
                    body.decode("utf-8") if isinstance(body, bytes) else body
                )
            except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise POIProviderError(
                    "INVALID_RESPONSE", "Baidu Place API returned invalid JSON.", False
                ) from exc
        if not isinstance(payload, Mapping):
            raise POIProviderError(
                "INVALID_RESPONSE", "Baidu Place API returned an invalid object.", False
            )
        return payload

    def _parse_page(self, payload: Mapping[str, Any]) -> _Page:
        status = _as_int(payload.get("status"))
        if status is None:
            raise POIProviderError(
                "INVALID_RESPONSE", "Baidu Place API response has no valid status.", False
            )
        if status != 0:
            if status in {4, 302, 401}:
                code = "RATE_LIMITED"
            elif status == 2:
                code = "INVALID_REQUEST"
            elif status == 1:
                code = "TIMEOUT"
            else:
                code = f"BAIDU_STATUS_{status}"
            raise POIProviderError(
                code, "Baidu Place API request failed.", status in {1, 4, 302, 401}
            )

        raw_results = payload.get("results", [])
        if raw_results is None:
            raw_results = []
        if not isinstance(raw_results, list):
            raise POIProviderError(
                "INVALID_RESPONSE", "Baidu Place API results are not a list.", False
            )
        total = payload.get("total")
        if total is not None:
            total = _as_int(total)
            if total is None or total < 0:
                raise POIProviderError(
                    "INVALID_RESPONSE", "Baidu Place API total is invalid.", False
                )
        return _Page(raw_results, total)

    @staticmethod
    def _parse_poi(item: Any) -> ProviderPOI:
        if not isinstance(item, Mapping):
            raise POIProviderError(
                "INVALID_RESPONSE", "Baidu Place API returned an invalid POI.", False
            )
        name = item.get("name")
        location = item.get("location")
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(location, Mapping)
        ):
            raise POIProviderError(
                "INVALID_RESPONSE", "Baidu Place API returned an incomplete POI.", False
            )
        lng = _as_float(location.get("lng"))
        lat = _as_float(location.get("lat"))
        if (
            lng is None
            or lat is None
            or not (-180 <= lng <= 180 and -90 <= lat <= 90)
        ):
            raise POIProviderError(
                "INVALID_RESPONSE", "Baidu Place API returned invalid POI coordinates.", False
            )

        uid = item.get("uid")
        if uid is not None and not isinstance(uid, str):
            raise POIProviderError(
                "INVALID_RESPONSE", "Baidu Place API returned an invalid POI uid.", False
            )
        address = item.get("address")
        if address is not None and not isinstance(address, str):
            raise POIProviderError(
                "INVALID_RESPONSE", "Baidu Place API returned an invalid POI address.", False
            )
        return ProviderPOI(
            uid=uid,
            name=name.strip(),
            lng=lng,
            lat=lat,
            address=address.strip() or None if address is not None else None,
        )

    @staticmethod
    def _validate_request(
        keyword: str, center: CenterPoint, radius_m: int
    ) -> tuple[str, float, float, int]:
        if not isinstance(keyword, str) or not keyword.strip():
            raise POIProviderError(
                "INVALID_REQUEST", "keyword must be a non-empty string.", False
            )
        if isinstance(radius_m, bool) or not isinstance(radius_m, int) or radius_m <= 0:
            raise POIProviderError(
                "INVALID_REQUEST", "radius_m must be a positive integer.", False
            )
        try:
            lng = float(center.lng)
            lat = float(center.lat)
            crs = center.crs
        except (AttributeError, TypeError, ValueError) as exc:
            raise POIProviderError(
                "INVALID_REQUEST", "center must be a valid BD09LL point.", False
            ) from exc
        if (
            crs != "BD09LL"
            or not math.isfinite(lng)
            or not math.isfinite(lat)
            or not (-180 <= lng <= 180 and -90 <= lat <= 90)
        ):
            raise POIProviderError(
                "INVALID_REQUEST", "center must be a valid BD09LL point.", False
            )
        return keyword.strip(), lng, lat, radius_m


@dataclass(frozen=True, slots=True)
class _Page:
    results: list[Any]
    total: int | None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
