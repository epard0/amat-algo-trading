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
    """Eleven requests -- more than the count-based windows' size -- with six
    failures, the first two of which are old enough that a deque bounded to
    ``window_requests`` (10, unused here since it is overridden to 99) would
    have evicted them by the 11th request. The ratio path is time-bounded,
    not count-bounded, so it must still see them: excluding the first two
    failures the last-10-only ratio would be 5/10 = 0.5 (no trip); including
    them, the true ratio is 6/11 > 0.5 (trip).
    """
    health, _clock = _health(unknown_error_threshold=99, window_requests=99)
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
