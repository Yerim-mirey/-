"""Build the live Baidu providers behind one shared per-AK quota guard.

Both entry points that talk to Baidu — the Core HTTP API and
:meth:`MVPToolGateway.from_baidu_ak` — go through :func:`build_live_providers`
so that geocoding, POI and routing for a given AK draw from the same budget.
The V2 Gateway (:meth:`MVPToolGatewayV2.from_baidu_ak`) and the V2 API tool
endpoints go through :func:`build_live_providers_v2`, which adds the V2 POI
profile on top of the same guard. Without this, every request would build its
own providers and each would get its own private quota, which is exactly the gap
the 2026-09-22 acceptance worked around with a temporary pacing loop.

Configuration (all optional, read at call time):

``BAIDU_ROUTES_PER_SECOND``
    Route targets per second for the AK. Defaults to the free-tier 3 QPS.
``BAIDU_QUOTA_MAX_WAIT_SECONDS``
    Longest a call may queue before it is refused as retryable `RATE_LIMITED`.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

from app.providers.baidu.geocoding import BaiduGeocodingProvider
from app.providers.baidu.net import install_ipv4_only
from app.providers.baidu.poi import BaiduPOIProvider
from app.providers.baidu.quota import (
    DEFAULT_ROUTES_PER_SECOND,
    BaiduQuotaGuard,
    guard_for_ak,
)
from app.providers.baidu.routing import BaiduRoutingError, BaiduRoutingProvider


ROUTES_PER_SECOND_ENV = "BAIDU_ROUTES_PER_SECOND"
MAX_WAIT_ENV = "BAIDU_QUOTA_MAX_WAIT_SECONDS"


def _optional_positive_float(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name} 必须是数字。") from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} 必须是正数。")
    return value


@dataclass(frozen=True, slots=True)
class LiveProviders:
    """One AK's providers plus the guard they share, for reporting."""

    geocoding: BaiduGeocodingProvider
    poi: BaiduPOIProvider
    routing: BaiduRoutingProvider
    guard: BaiduQuotaGuard


def build_live_providers(ak: str) -> LiveProviders:
    """Construct live providers without issuing any network request."""
    if not isinstance(ak, str) or not ak.strip():
        raise ValueError("百度服务端 AK 不能为空。")
    key = ak.strip()
    # Pin Baidu traffic to IPv4 before any provider can open a connection: the
    # console whitelist records IPv4 addresses, while a dual-stack host would
    # otherwise reach Baidu over IPv6 and be refused with status 210.
    install_ipv4_only()
    guard = guard_for_ak(
        key,
        routes_per_second=_optional_positive_float(ROUTES_PER_SECOND_ENV),
        max_wait_seconds=_optional_positive_float(MAX_WAIT_ENV),
    )
    return LiveProviders(
        geocoding=BaiduGeocodingProvider(key, quota_guard=guard),
        poi=BaiduPOIProvider(ak=key, quota_guard=guard),
        routing=BaiduRoutingProvider(key, quota_guard=guard),
        guard=guard,
    )


# V2 spends a whole-run budget inside one POI search: at most 60 keywords
# against a 180-second deadline (POIServiceV2.MAX_SEARCH_TERMS_PER_REQUEST and
# DEFAULT_SEARCH_DEADLINE_SECONDS). Three seconds is that budget divided by those
# keywords, so one slow page cannot swallow a large share of the sweep; a longer
# timeout would directly cut how many categories ever get queried. The trade is
# that timeouts become an expected failure mode, which is why Runtime reports a
# timed-out category as missing evidence — never as an area without facilities.
V2_POI_TIMEOUT_SECONDS = 3.0
V2_POI_MAX_PAGES = 2


class UnavailableRoutingProvider:
    """Explains a missing AK without opening a connection."""

    def route_matrix(self, _origin, _destinations):
        raise BaiduRoutingError("PROVIDER_ERROR", "百度服务端 AK 未配置。", retryable=False)


@dataclass(frozen=True, slots=True)
class V2Providers:
    """One AK's V2 providers, all drawing on the same quota guard."""

    poi: BaiduPOIProvider
    routing: BaiduRoutingProvider | UnavailableRoutingProvider
    geocoding: BaiduGeocodingProvider | None
    guard: BaiduQuotaGuard | None


def build_live_providers_v2(ak: str) -> V2Providers:
    """Construct the V2 tool providers without issuing any network request.

    A blank AK yields explicit unavailable providers rather than a hidden
    connection attempt: the POI provider refuses with ``MISSING_AK`` before it
    builds a URL, and routing raises a non-retryable ``PROVIDER_ERROR``.
    """
    key = ak.strip() if isinstance(ak, str) else ""
    if not key:
        return V2Providers(
            poi=BaiduPOIProvider(ak="", max_pages=V2_POI_MAX_PAGES),
            routing=UnavailableRoutingProvider(),
            geocoding=None,
            guard=None,
        )
    shared = build_live_providers(key)
    return V2Providers(
        poi=BaiduPOIProvider(
            ak=key,
            timeout=V2_POI_TIMEOUT_SECONDS,
            quota_guard=shared.guard,
            max_pages=V2_POI_MAX_PAGES,
        ),
        routing=shared.routing,
        geocoding=shared.geocoding,
        guard=shared.guard,
    )


def default_guard() -> BaiduQuotaGuard:
    """A guard with the documented defaults, for offline reporting/tests."""
    return BaiduQuotaGuard(routes_per_second=DEFAULT_ROUTES_PER_SECOND)
