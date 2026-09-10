from __future__ import annotations

from llmrouter.contracts import classify_http
from llmrouter.health import HealthPolicy, ProviderHealth


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _health(**overrides: object) -> tuple[ProviderHealth, _Clock]:
    clock = _Clock()
    return ProviderHealth(HealthPolicy(**overrides), clock=clock), clock  # type: ignore[arg-type]


def test_rate_limit_cools_for_the_provider_supplied_window() -> None:
    health, clock = _health()
    health.record("rate_limit", retry_after_seconds=42)
    assert health.state == "cooling" and health.should_skip()
    clock.now += 41
    assert health.should_skip()
    clock.now += 2
    assert not health.should_skip()


def test_auth_error_disables_for_the_process_lifetime() -> None:
    health, clock = _health()
    health.record("auth")
    clock.now += 10**6
    assert health.state == "disabled" and health.should_skip()


def test_unknown_errors_trip_after_three_in_ten_with_backoff() -> None:
    health, clock = _health(cooldown_seconds=10, max_cooldown_seconds=25)
    for _ in range(7):
        health.record("ok")
    health.record("server")
    health.record("transport")
    assert not health.should_skip()
    health.record("malformed")
    assert health.should_skip()
    clock.now += 11
    assert not health.should_skip()
    for _ in range(3):
        health.record("server")
    assert health.cooling_until is not None and health.cooling_until - clock.now == 20
    clock.now += 21
    for _ in range(3):
        health.record("server")
    assert health.cooling_until is not None and health.cooling_until - clock.now == 25


def test_failure_ratio_over_five_minutes_trips() -> None:
    health, _clock = _health(unknown_error_threshold=99, window_requests=99)
    health.record("ok")
    health.record("server")
    health.record("ok")
    health.record("server")
    assert not health.should_skip()
    health.record("server")
    assert health.should_skip()


def test_two_timeouts_in_five_requests_trip() -> None:
    health, _ = _health()
    health.record("ok")
    health.record("timeout")
    health.record("ok")
    assert not health.should_skip()
    health.record("timeout")
    assert health.should_skip()


def test_success_after_cooldown_resets_backoff() -> None:
    health, clock = _health(cooldown_seconds=10)
    for _ in range(3):
        health.record("server")
    clock.now += 11
    health.record("ok")
    assert health.consecutive_cooldowns == 0


def test_snapshot_reports_state_after_a_trip() -> None:
    health, clock = _health(cooldown_seconds=10)
    for _ in range(3):
        health.record("server")
    assert health.snapshot() == {
        "state": "cooling",
        "cooling_until": clock.now + 10,
        "consecutive_cooldowns": 1,
        "last_reason": "unknown_errors",
        "recent": [],  # `_trip` clears the window it tripped from
    }


def test_failure_ratio_window_is_not_truncated_by_request_count() -> None:
    """Eleven requests at the *default* ``window_requests`` (10) and
    ``timeout_window_requests`` (5) -- so a `_recent` deque bounded to
    ``max(window_requests, timeout_window_requests)`` (10, the pre-fix
    formula) would evict the first request by the 11th. Only
    ``unknown_error_threshold`` is raised, to keep the count-based trip path
    from firing on its own and isolate the ratio path.

    Six of the eleven are failures, with the first two old enough to be the
    ones a `maxlen=10` deque would evict: excluding them, the last 10 records
    alone are 5 failures / 10 = 0.5 -- at, not over, the strict `> 0.5`
    threshold, so a truncated window never trips. Including them (all 11
    retained, as `ratio_window_capacity` requires), the true ratio is
    6 / 11 (~0.545) > 0.5, and it trips. This fails without
    `ratio_window_capacity` folded into the deque bound; see the fix report
    for the confirming run with `ratio_window_capacity=10`.
    """
    health, _clock = _health(unknown_error_threshold=99)
    outcomes = ["server", "server", "ok", "ok", "ok", "ok", "ok", "server", "server", "server"]
    for outcome in outcomes:
        health.record(outcome)
    assert not health.should_skip()
    health.record("server")
    assert health.should_skip()


def test_http_classification() -> None:
    assert classify_http(401, "") == "auth"
    assert classify_http(404, '{"error":{"message":"model not found"}}') == "auth"
    assert classify_http(429, '{"error":{"type":"insufficient_quota"}}') == "quota"
    assert classify_http(429, "slow down") == "rate_limit"
    assert classify_http(503, "") == "server"
    assert classify_http(408, "") == "timeout"
    assert classify_http(418, "") == "transport"
