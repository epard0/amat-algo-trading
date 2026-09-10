"""Sliding-window health tracking for a single LLM provider.

Each provider gets one mutable :class:`ProviderHealth` that a router consults
before dispatch (:meth:`ProviderHealth.should_skip`) and updates after every
call (:meth:`ProviderHealth.record`). Not thread-safe; the router serialises
access.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from typing import Literal

from llmrouter.contracts import ErrorClass, Record

State = Literal["available", "cooling", "disabled"]
_TRIPPING: frozenset[ErrorClass] = frozenset({"transport", "server", "malformed"})


class HealthPolicy(Record):
    window_requests: int = 10
    unknown_error_threshold: int = 3
    failure_ratio_threshold: float = 0.5
    failure_ratio_window_seconds: float = 300
    failure_ratio_min_observations: int = 4
    timeout_threshold: int = 2
    timeout_window_requests: int = 5
    cooldown_seconds: float = 600
    max_cooldown_seconds: float = 3600
    rate_limit_cooldown_seconds: float = 900


class ProviderHealth:
    """Sliding-window outcome tracker for one provider. Not thread-safe; the
    router serialises access."""

    def __init__(self, policy: HealthPolicy, clock: Callable[[], float] = time.monotonic) -> None:
        self.policy = policy
        self._clock = clock
        self._recent: deque[tuple[float, ErrorClass]] = deque(
            maxlen=max(policy.window_requests, policy.timeout_window_requests)
        )
        self.state: State = "available"
        self.cooling_until: float | None = None
        self.consecutive_cooldowns = 0
        self._probe_pending = False
        self.last_reason: str | None = None

    def should_skip(self) -> bool:
        if self.state == "disabled":
            return True
        if self.state == "cooling":
            if self.cooling_until is None:
                return False
            if self._clock() < self.cooling_until:
                return True
            self.state = "available"
            self.cooling_until = None
            self._probe_pending = True
        return False

    def record(self, error_class: ErrorClass, *, retry_after_seconds: float | None = None) -> None:
        now = self._clock()
        self._recent.append((now, error_class))
        if error_class == "ok":
            cooldown_elapsed = (
                self.state == "cooling"
                and self.cooling_until is not None
                and now >= self.cooling_until
            )
            if cooldown_elapsed:
                # The cooldown has already elapsed in wall-clock terms even
                # though nothing has called `should_skip` yet to perform the
                # cooling -> available transition; a direct success report
                # still counts as the recovery probe.
                self.state = "available"
                self.cooling_until = None
                self._probe_pending = False
                self.consecutive_cooldowns = 0
            elif self._probe_pending:
                self.consecutive_cooldowns = 0
                self._probe_pending = False
            return
        if error_class in ("auth", "no_credential"):
            self.state = "disabled"
            self.last_reason = error_class
            return
        if error_class in ("rate_limit", "quota"):
            seconds = (
                retry_after_seconds
                if retry_after_seconds
                else self.policy.rate_limit_cooldown_seconds
            )
            self._cool(now, seconds, error_class)
            return
        if error_class == "timeout":
            window = list(self._recent)[-self.policy.timeout_window_requests :]
            if sum(1 for _, item in window if item == "timeout") >= self.policy.timeout_threshold:
                self._trip(now, "timeouts")
            return
        if error_class in _TRIPPING:
            window = list(self._recent)[-self.policy.window_requests :]
            unknown = sum(1 for _, item in window if item in _TRIPPING)
            if unknown >= self.policy.unknown_error_threshold:
                self._trip(now, "unknown_errors")
                return
            since = now - self.policy.failure_ratio_window_seconds
            recent = [item for at, item in self._recent if at >= since]
            failures = sum(1 for item in recent if item != "ok")
            if (
                len(recent) >= self.policy.failure_ratio_min_observations
                and failures / len(recent) > self.policy.failure_ratio_threshold
            ):
                self._trip(now, "failure_ratio")

    def _trip(self, now: float, reason: str) -> None:
        seconds = min(
            self.policy.cooldown_seconds * (2**self.consecutive_cooldowns),
            self.policy.max_cooldown_seconds,
        )
        self.consecutive_cooldowns += 1
        self._cool(now, seconds, reason)
        self._recent.clear()

    def _cool(self, now: float, seconds: float, reason: str) -> None:
        self.state = "cooling"
        self.cooling_until = now + seconds
        self.last_reason = reason

    def snapshot(self) -> dict[str, object]:
        return {
            "state": self.state,
            "cooling_until": self.cooling_until,
            "consecutive_cooldowns": self.consecutive_cooldowns,
            "last_reason": self.last_reason,
            "recent": [item for _, item in self._recent],
        }
