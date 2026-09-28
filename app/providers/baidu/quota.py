"""Shared per-AK quota guard for Baidu Map Web Service calls.

The Core Tools were verified once against the free console quota (3 QPS on the
walking route matrix). That success depended on a temporary pacing loop inside
the acceptance harness; the services themselves had no cross-request limit, so
two concurrent Diagnoses on one AK could exceed the console quota and produce
`RATE_LIMITED` results.

This module puts the protection where the limit is actually enforced — in
front of the HTTP call — while leaving the Tool contracts untouched:

* one :class:`BaiduQuotaGuard` per AK, shared process-wide, so every provider
  built for that AK queues behind the same budget;
* a route target consumes one quota unit; a geocoding or POI HTTP request
  consumes one quota unit. A rolling one-second window admits at most
  ``routes_per_second`` units, so all Baidu calls share one conservative
  process-wide budget;
* requests wait for their turn in FIFO order and only fail once
  ``max_wait_seconds`` would be exceeded.

The window is the only constraint by design. A minimum gap between consecutive
requests would additionally throttle throughput to roughly one request per
second whatever the configured rate, so a larger quota would buy nothing.

Providers call :meth:`BaiduQuotaGuard.acquire_routes` (routes) or
:meth:`BaiduQuotaGuard.acquire_call` (geocoding, POI) immediately before their
transport runs.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass


# Free console tier: 3 QPS on the walking route matrix. Conservative default;
# paid quotas raise it through the environment.
DEFAULT_ROUTES_PER_SECOND = 3.0
DEFAULT_WINDOW_SECONDS = 1.0
DEFAULT_MAX_WAIT_SECONDS = 180.0
# Floor for how long a *waiting* call sleeps before re-checking its turn. The
# call that is actually admitted still sleeps exactly its remaining wait, so
# pacing accuracy is unaffected; queued callers only need their turn to arrive.
WAITER_TICK_SECONDS = 0.05


class QuotaExceededError(RuntimeError):
    """The shared guard refused a call because it would wait too long."""

    def __init__(
        self,
        message: str,
        *,
        waited_seconds: float,
        deadline_exceeded: bool = False,
    ) -> None:
        super().__init__(message)
        self.waited_seconds = waited_seconds
        self.deadline_exceeded = deadline_exceeded


class BaiduQuotaGuard:
    """Thread-safe sliding-window limiter counting conservative quota units per AK."""

    def __init__(
        self,
        *,
        routes_per_second: float = DEFAULT_ROUTES_PER_SECOND,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        max_wait_seconds: float = DEFAULT_MAX_WAIT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        for name, value in (
            ("routes_per_second", routes_per_second),
            ("window_seconds", window_seconds),
            ("max_wait_seconds", max_wait_seconds),
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} 必须是正数。")
        self.routes_per_second = routes_per_second
        self.window_seconds = window_seconds
        self.max_wait_seconds = max_wait_seconds
        self._clock = clock
        self._sleep = sleep
        # One lock serialises every acquisition, which also makes the wait
        # queue FIFO: a thread either gets its grant or sleeps zero/one step
        # while still holding its place in this loop.
        self._lock = threading.Lock()
        self._grants: deque[tuple[float, int]] = deque()
        self._total_granted = 0
        self._total_routes = 0
        self._total_calls = 0

    @property
    def granted_routes(self) -> int:
        """Route targets granted so far; used by acceptance reporting."""
        with self._lock:
            return self._total_routes

    @property
    def granted_calls(self) -> int:
        """Non-routing Baidu HTTP calls admitted by this guard."""
        with self._lock:
            return self._total_calls

    @property
    def granted_quota_units(self) -> int:
        """All routing-target and non-routing call units admitted so far."""
        with self._lock:
            return self._total_granted

    @property
    def max_batch_routes(self) -> int:
        """Largest single request this budget admits inside one window."""
        return max(1, int(self.routes_per_second * self.window_seconds))

    def acquire_routes(
        self, targets: int, *, deadline_monotonic: float | None = None
    ) -> None:
        """Reserve route-target units, blocking until the budget allows them."""
        if isinstance(targets, bool) or not isinstance(targets, int) or targets <= 0:
            raise ValueError("targets 必须是正整数。")
        if targets > self.max_batch_routes:
            raise ValueError(
                f"单批 {targets} 个目标超过每秒 {self.routes_per_second:g} 的配额，"
                "必须先拆批。"
            )
        self._acquire_units(
            targets, route_targets=targets, calls=0,
            deadline_monotonic=deadline_monotonic,
        )

    def acquire_call(self, *, deadline_monotonic: float | None = None) -> None:
        """Reserve one non-route HTTP-call unit (geocoding or POI page)."""
        self._acquire_units(
            1, route_targets=0, calls=1, deadline_monotonic=deadline_monotonic
        )

    def _acquire_units(
        self,
        units: int,
        *,
        route_targets: int,
        calls: int,
        deadline_monotonic: float | None = None,
    ) -> None:
        started = self._clock()
        with self._lock:
            while True:
                now = self._clock()
                deadline_remaining = (
                    None if deadline_monotonic is None else deadline_monotonic - now
                )
                if deadline_remaining is not None and deadline_remaining <= 0:
                    raise QuotaExceededError(
                        "本次运行已达到时间预算，已放弃百度地图请求。",
                        waited_seconds=now - started,
                        deadline_exceeded=True,
                    )
                wait = self._wait_seconds_locked(units)
                if wait <= 0:
                    self._grant_locked(units, route_targets=route_targets, calls=calls)
                    return
                wait_budget = self.max_wait_seconds - (now - started)
                if deadline_remaining is not None:
                    wait_budget = min(wait_budget, deadline_remaining)
                if wait > wait_budget:
                    message = (
                        "本次运行已达到时间预算，已放弃百度地图请求。"
                        if deadline_remaining is not None and wait > deadline_remaining
                        else "百度配额等待超出上限，已放弃本次调用。"
                    )
                    raise QuotaExceededError(
                        message,
                        waited_seconds=now - started,
                        deadline_exceeded=(
                            deadline_remaining is not None
                            and wait > deadline_remaining
                        ),
                    )
                # Waiting threads sleep in longer steps than the tick that
                # admitted them: they only need their turn to arrive, and a
                # tight loop would spin the lock under concurrency.
                sleep_seconds = max(wait, WAITER_TICK_SECONDS)
                if deadline_remaining is not None:
                    sleep_seconds = min(sleep_seconds, deadline_remaining)
                self._sleep(sleep_seconds)

    def _wait_seconds_locked(self, targets: int) -> float:
        now = self._clock()
        while self._grants and now - self._grants[0][0] >= self.window_seconds:
            self._grants.popleft()
        pending = sum(count for _, count in self._grants)
        if not self._grants:
            return 0.0
        if pending + targets <= self.routes_per_second:
            return 0.0
        # The window is full: wait for the oldest grant to age out. Multiple
        # batches queued inside one window therefore drain one window apart,
        # which is what makes the rate hold over any one-second interval.
        return max(0.0, self.window_seconds - (now - self._grants[0][0]))

    def _grant_locked(self, units: int, *, route_targets: int, calls: int) -> None:
        self._grants.append((self._clock(), units))
        self._total_granted += units
        self._total_routes += route_targets
        self._total_calls += calls


_GUARDS: dict[str, BaiduQuotaGuard] = {}
_GUARDS_LOCK = threading.Lock()


def guard_for_ak(
    ak: str,
    *,
    routes_per_second: float | None = None,
    max_wait_seconds: float | None = None,
) -> BaiduQuotaGuard:
    """Return the process-wide guard shared by every provider using ``ak``.

    Concurrent FastAPI requests build their own provider objects per request;
    keying the guard by AK is what makes their combined call rate respect one
    console quota instead of one quota per request.
    """
    key = ak.strip()
    if not key:
        raise ValueError("百度服务端 AK 不能为空。")
    overrides: dict[str, float] = {}
    if routes_per_second is not None:
        overrides["routes_per_second"] = routes_per_second
    if max_wait_seconds is not None:
        overrides["max_wait_seconds"] = max_wait_seconds
    with _GUARDS_LOCK:
        guard = _GUARDS.get(key)
        if guard is None:
            guard = BaiduQuotaGuard(**overrides)
            _GUARDS[key] = guard
        elif overrides and any(
            getattr(guard, name) != value for name, value in overrides.items()
        ):
            raise ValueError("同一 AK 的配额参数已经确定，不能被后续调用改写。")
        return guard


def reset_guards() -> None:
    """Drop cached guards. Test-only helper; never call while requests run."""
    with _GUARDS_LOCK:
        _GUARDS.clear()


@dataclass(frozen=True, slots=True)
class QuotaSnapshot:
    """Public, secret-free view of the active quota, for logs and reports."""

    routes_per_second: float
    window_seconds: float
    max_wait_seconds: float
    max_batch_routes: int
    granted_routes: int
    granted_calls: int
    granted_quota_units: int

    def describe(self) -> str:
        return (
            f"{self.routes_per_second:g} 配额单位/秒，窗口 {self.window_seconds:g}s，"
            f"最大单批 {self.max_batch_routes}，最长等待 {self.max_wait_seconds:g}s，"
            f"已放行路线目标 {self.granted_routes}，其他请求 {self.granted_calls}，"
            f"配额单位 {self.granted_quota_units}"
        )


def snapshot(guard: BaiduQuotaGuard) -> QuotaSnapshot:
    return QuotaSnapshot(
        routes_per_second=guard.routes_per_second,
        window_seconds=guard.window_seconds,
        max_wait_seconds=guard.max_wait_seconds,
        max_batch_routes=guard.max_batch_routes,
        granted_routes=guard.granted_routes,
        granted_calls=guard.granted_calls,
        granted_quota_units=guard.granted_quota_units,
    )
