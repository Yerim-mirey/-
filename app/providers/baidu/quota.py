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
* the budget is counted in **route targets**, not HTTP calls, because the
  console meters destinations;
* two constraints, both needed for the observed 3 QPS tier: at most
  ``routes_per_second`` route targets inside any one-second window, and a
  minimum spacing of ``1 - 1/routes_per_second`` between consecutive route
  requests so a batch never bursts the whole window at once;
* requests wait for their turn in FIFO order and only fail once
  ``max_wait_seconds`` would be exceeded.

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


class QuotaExceededError(RuntimeError):
    """The shared guard refused a call because it would wait too long."""

    def __init__(self, message: str, *, waited_seconds: float) -> None:
        super().__init__(message)
        self.waited_seconds = waited_seconds


class BaiduQuotaGuard:
    """Thread-safe sliding-window limiter counting route targets per AK."""

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

    @property
    def granted_routes(self) -> int:
        """Route targets granted so far; used by acceptance reporting."""
        with self._lock:
            return self._total_granted

    @property
    def max_batch_routes(self) -> int:
        """Largest single request this budget admits inside one window."""
        return max(1, int(self.routes_per_second * self.window_seconds))

    def acquire_routes(self, targets: int) -> None:
        """Reserve ``targets`` route targets, blocking until the budget allows it."""
        if isinstance(targets, bool) or not isinstance(targets, int) or targets <= 0:
            raise ValueError("targets 必须是正整数。")
        if targets > self.max_batch_routes:
            raise ValueError(
                f"单批 {targets} 个目标超过每秒 {self.routes_per_second:g} 的配额，"
                "必须先拆批。"
            )
        started = self._clock()
        with self._lock:
            while True:
                wait = self._wait_seconds_locked(targets)
                if wait <= 0:
                    self._grant_locked(targets)
                    return
                if self._clock() - started + wait > self.max_wait_seconds:
                    raise QuotaExceededError(
                        "百度配额等待超出上限，已放弃本次调用。",
                        waited_seconds=self._clock() - started,
                    )
                self._sleep(wait)

    def acquire_call(self) -> None:
        """Reserve one non-route call (geocoding, POI page) against the same AK."""
        self.acquire_routes(1)

    def _wait_seconds_locked(self, targets: int) -> float:
        now = self._clock()
        while self._grants and now - self._grants[0][0] >= self.window_seconds:
            self._grants.popleft()
        pending = sum(count for _, count in self._grants)
        if not self._grants:
            return 0.0
        # Constraint 1: the rolling window may not exceed the per-second budget.
        if pending + targets > self.routes_per_second:
            return max(0.0, self.window_seconds - (now - self._grants[0][0]))
        # Constraint 2: keep consecutive route requests spaced out so a single
        # batch cannot consume the entire window's budget in one burst.
        spacing = self.window_seconds * (1.0 - 1.0 / self.routes_per_second)
        elapsed = now - self._grants[-1][0]
        return max(0.0, spacing - elapsed)

    def _grant_locked(self, targets: int) -> None:
        self._grants.append((self._clock(), targets))
        self._total_granted += targets


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

    def describe(self) -> str:
        return (
            f"{self.routes_per_second:g} 目标/秒，窗口 {self.window_seconds:g}s，"
            f"最大单批 {self.max_batch_routes}，最长等待 {self.max_wait_seconds:g}s，"
            f"已放行 {self.granted_routes} 个目标"
        )


def snapshot(guard: BaiduQuotaGuard) -> QuotaSnapshot:
    return QuotaSnapshot(
        routes_per_second=guard.routes_per_second,
        window_seconds=guard.window_seconds,
        max_wait_seconds=guard.max_wait_seconds,
        max_batch_routes=guard.max_batch_routes,
        granted_routes=guard.granted_routes,
    )
