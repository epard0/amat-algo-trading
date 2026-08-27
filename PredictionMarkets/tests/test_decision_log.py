import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from execution.decision_log import SCHEMA_VERSION, DecisionLog


def log_for(tmp_path: Path) -> DecisionLog:
    return DecisionLog(tmp_path / "decisions.jsonl")


def trace_for(log: DecisionLog):
    return log.trace(
        cycle_id="cycle-1",
        mode="paper",
        environment="production",
        asset="BTC",
        ticker="KXBTC15M-TEST",
    )


def test_skipped_evaluation_records_the_blocking_gate(tmp_path: Path) -> None:
    log = log_for(tmp_path)
    trace = trace_for(log)

    assert trace.gate("cooldown", True) is True
    assert trace.gate("min_edge", False, "edge 2.0% is below 5.0%") is False
    trace.finish("skipped", "edge below floor")

    (record,) = log.read_all()
    assert record["record_type"] == "decision"
    assert record["schema_version"] == SCHEMA_VERSION
    assert record["decision"] == "skipped"
    assert record["blocked_by"] == "min_edge"
    assert record["gates"][1]["reason"] == "edge 2.0% is below 5.0%"
    assert record["cycle_id"] == "cycle-1"
    assert record["asset"] == "BTC"


def test_money_survives_the_round_trip_as_strings(tmp_path: Path) -> None:
    """Decimals must not be written as floats.

    ``0.1 + 0.2`` in a log file is worse than useless when the whole point is
    reconciling sizing and fees after the fact.
    """
    log = log_for(tmp_path)
    trace = trace_for(log)
    trace.set("candidate", {"edge": Decimal("0.0700"), "price": Decimal("0.31")})
    trace.finish("traded")

    (record,) = log.read_all()
    assert record["candidate"]["edge"] == "0.0700"
    assert Decimal(record["candidate"]["price"]) == Decimal("0.31")


def test_nested_structures_and_tuples_are_encoded(tmp_path: Path) -> None:
    log = log_for(tmp_path)
    trace = trace_for(log)
    trace.set(
        "prediction",
        {
            "regime": "REVERSAL",
            "regime_reasons": ("retraced 82%", "wick rejection"),
            "features": {"efficiency_ratio": 0.18},
        },
    )
    trace.finish("skipped", "divergence")

    (record,) = log.read_all()
    assert record["prediction"]["regime_reasons"] == [
        "retraced 82%",
        "wick rejection",
    ]
    assert record["prediction"]["features"]["efficiency_ratio"] == 0.18


def test_strict_sink_receives_raw_records_even_when_jsonl_is_disabled(
    tmp_path: Path,
) -> None:
    received: list[dict] = []
    log = DecisionLog(
        tmp_path / "disabled.jsonl",
        enabled=False,
        record_sink=received.append,
    )
    trace = trace_for(log)
    trace.set("prediction", {"probability_yes": Decimal("0.77")})
    trace.finish("skipped", "edge")

    assert len(received) == 1
    assert received[0]["decision"] == "skipped"
    assert received[0]["prediction"]["probability_yes"] == "0.77"
    assert not (tmp_path / "disabled.jsonl").exists()


def test_every_evaluation_appends_its_own_line(tmp_path: Path) -> None:
    log = log_for(tmp_path)
    for index in range(3):
        trace = trace_for(log)
        trace.gate("min_edge", index == 2)
        trace.finish("traded" if index == 2 else "skipped")

    records = log.read_all()
    assert len(records) == 3
    assert [record["decision"] for record in records] == [
        "skipped",
        "skipped",
        "traded",
    ]
    assert len({record["decision_id"] for record in records}) == 3


def test_settlement_events_link_back_to_a_decision(tmp_path: Path) -> None:
    log = log_for(tmp_path)
    trace = trace_for(log)
    trace.finish("traded")
    decision_id = trace.decision_id

    log.record_event(
        "settlement",
        trade_id=7,
        decision_id=decision_id,
        market_result="yes",
        pnl=Decimal("1.23"),
    )

    decision, settlement = log.read_all()
    assert settlement["record_type"] == "settlement"
    assert settlement["decision_id"] == decision["decision_id"]
    assert settlement["pnl"] == "1.23"


def test_disabled_log_writes_nothing(tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "decisions.jsonl", enabled=False)
    trace_for(log).finish("skipped")

    assert log.read_all() == []
    assert not (tmp_path / "decisions.jsonl").exists()


def test_a_broken_log_never_breaks_a_trading_cycle(tmp_path: Path) -> None:
    """Diagnostics are not allowed to abort a cycle or strand a position."""
    blocked = tmp_path / "a-directory"
    blocked.mkdir()
    log = DecisionLog(blocked)

    trace_for(log).finish("traded")  # must not raise

    assert log.read_all() == []


def test_read_all_skips_corrupt_lines(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    path.write_text('{"record_type":"decision"}\nnot json\n\n{"a":1}\n')

    records = DecisionLog(path).read_all()

    assert len(records) == 2


# --------------------------------------------------------------------------
# Retention: one rolling day, previous window discarded
# --------------------------------------------------------------------------


class _Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, hours: float) -> None:
        self.now += timedelta(hours=hours)


def _log(tmp_path, clock):
    from execution.decision_log import DecisionLog

    return DecisionLog(tmp_path / "decisions.jsonl", now=clock)


def test_a_log_inside_the_window_is_untouched(tmp_path) -> None:
    clock = _Clock(datetime(2026, 8, 1, tzinfo=timezone.utc))
    log = _log(tmp_path, clock)
    log.record_event("decision", asset="BTC")
    clock.advance(23)
    log.record_event("decision", asset="ETH")
    assert len(log.read_all()) == 2


def test_the_previous_day_is_discarded_not_archived(tmp_path) -> None:
    clock = _Clock(datetime(2026, 8, 1, tzinfo=timezone.utc))
    log = _log(tmp_path, clock)
    for _ in range(5):
        log.record_event("decision", asset="BTC")
    clock.advance(25)
    log.record_event("decision", asset="ETH")

    remaining = log.read_all()
    assert len(remaining) == 1
    assert remaining[0]["asset"] == "ETH"
    # Discarded, so there is no sibling holding yesterday.
    assert [p.name for p in tmp_path.iterdir()] == ["decisions.jsonl"]


def test_the_clock_starts_from_the_file_not_the_process(tmp_path) -> None:
    """A bot restarted hourly must still rotate once a day.

    The age comes from the log's own first record, so a fresh DecisionLog
    inherits the age of the file it is appending to.
    """
    clock = _Clock(datetime(2026, 8, 1, tzinfo=timezone.utc))
    first = _log(tmp_path, clock)
    first.record_event("decision", asset="BTC")

    clock.advance(25)
    restarted = _log(tmp_path, clock)          # a new process, same file
    restarted.record_event("decision", asset="ETH")

    remaining = restarted.read_all()
    assert len(remaining) == 1
    assert remaining[0]["asset"] == "ETH"


def test_rotation_can_be_switched_off(tmp_path) -> None:
    from execution.decision_log import DecisionLog

    clock = _Clock(datetime(2026, 8, 1, tzinfo=timezone.utc))
    log = DecisionLog(tmp_path / "d.jsonl", retention_seconds=0, now=clock)
    log.record_event("decision", asset="BTC")
    clock.advance(1000)
    log.record_event("decision", asset="ETH")
    assert len(log.read_all()) == 2


def test_an_unparseable_first_line_does_not_lose_the_log(tmp_path) -> None:
    """A corrupt head must not read as 'infinitely old' and wipe the file."""
    path = tmp_path / "decisions.jsonl"
    path.write_text("not json at all\n")
    clock = _Clock(datetime(2026, 8, 1, tzinfo=timezone.utc))
    from execution.decision_log import DecisionLog

    log = DecisionLog(path, now=clock)
    log.record_event("decision", asset="BTC")
    assert path.read_text().count("\n") == 2


def test_rotation_never_raises_into_the_trading_path(tmp_path, monkeypatch) -> None:
    clock = _Clock(datetime(2026, 8, 1, tzinfo=timezone.utc))
    log = _log(tmp_path, clock)
    log.record_event("decision", asset="BTC")
    clock.advance(48)

    def boom(*_a, **_k):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(Path, "open", boom)
    log.record_event("decision", asset="ETH")      # must not raise


# --------------------------------------------------------------- run folding


def test_a_repeated_verdict_on_one_ticker_collapses_to_a_run(tmp_path) -> None:
    """The dominant record is a countdown re-derived every pass.

    121 byte-identical "not in the entry window yet" lines for one ticker is
    one fact. It must survive as one fact, not vanish and not repeat.
    """
    clock = _Clock(datetime(2026, 8, 1, tzinfo=timezone.utc))
    log = DecisionLog(tmp_path / "d.jsonl", now=clock)
    for _ in range(121):
        trace = trace_for(log)
        trace.gate("entry_window", False, "711s to close is outside 180s")
        trace.finish("skipped", "outside entry window")
        clock.advance(2 / 3600)      # 242s in all, inside one run window

    records = log.read_all()

    assert len(records) == 2, "an opening record and one closing summary"
    assert records[0]["blocked_by"] == "entry_window"
    assert "evaluations" not in records[0], "the opener stands for itself"
    assert records[1]["evaluations"] == 120
    assert sum(r.get("evaluations", 1) for r in records) == 121


def test_a_changed_verdict_is_written_the_moment_it_changes(tmp_path) -> None:
    clock = _Clock(datetime(2026, 8, 1, tzinfo=timezone.utc))
    log = DecisionLog(tmp_path / "d.jsonl", now=clock)
    for _ in range(4):
        trace_for(log).gate("entry_window", False, "outside")
        trace_for(log)  # unused; the gate above belongs to its own trace
    for _ in range(4):
        trace = trace_for(log)
        trace.gate("entry_window", False, "outside")
        trace.finish("skipped", "outside entry window")
    trace = trace_for(log)
    trace.gate("entry_window", True)
    trace.gate("min_edge", False, "2.0% below 5.0%")
    trace.finish("skipped", "edge below floor")

    blocked = [r["blocked_by"] for r in log.read_all()]

    assert blocked == ["entry_window", "entry_window", "min_edge"]


def test_a_trade_is_never_folded_and_never_waits(tmp_path) -> None:
    log = DecisionLog(tmp_path / "d.jsonl")
    for _ in range(3):
        trace = trace_for(log)
        trace.gate("min_edge", False, "below")
        trace.finish("skipped", "edge below floor")
    trace = trace_for(log)
    trace.finish("traded", "YES at 0.62")

    # Read the file directly: nothing may be sitting in memory at this point.
    lines = (tmp_path / "d.jsonl").read_text().splitlines()
    assert json.loads(lines[-1])["decision"] == "traded"


def test_a_run_is_capped_so_a_live_tail_keeps_moving(tmp_path) -> None:
    clock = _Clock(datetime(2026, 8, 1, tzinfo=timezone.utc))
    log = DecisionLog(tmp_path / "d.jsonl", max_run_seconds=60, now=clock)
    for _ in range(30):
        trace = trace_for(log)
        trace.gate("entry_window", False, "outside")
        trace.finish("skipped", "outside entry window")
        clock.advance(10 / 3600)      # 10s a pass, 300s in total

    records = log.read_all()

    assert 4 <= len(records) <= 8, f"periodic, not per-pass: {len(records)}"
    assert sum(r.get("evaluations", 1) for r in records) == 30


def test_shutdown_writes_the_run_that_was_still_open(tmp_path) -> None:
    log = DecisionLog(tmp_path / "d.jsonl")
    for _ in range(9):
        trace = trace_for(log)
        trace.gate("entry_window", False, "outside")
        trace.finish("skipped", "outside entry window")

    log.close()

    records = [json.loads(line) for line in (tmp_path / "d.jsonl").read_text().splitlines()]
    assert sum(r.get("evaluations", 1) for r in records) == 9


def test_a_passing_gate_does_not_carry_the_failure_reason(tmp_path) -> None:
    """`"allowed": true, "reason": "ticker is in cooldown"` said the opposite."""
    log = log_for(tmp_path)
    trace = trace_for(log)
    trace.gate("cooldown", True, "ticker is in post-failure cooldown")
    trace.gate("min_edge", False, "2.0% is below 5.0%")
    trace.finish("skipped", "edge below floor")

    (record,) = log.read_all()

    assert "reason" not in record["gates"][0]
    assert record["gates"][1]["reason"] == "2.0% is below 5.0%"


def test_the_funnel_counts_evaluations_not_lines(tmp_path) -> None:
    from monitoring.performance_tracker import gate_funnel

    clock = _Clock(datetime(2026, 8, 1, tzinfo=timezone.utc))
    log = DecisionLog(tmp_path / "d.jsonl", now=clock)
    for _ in range(50):
        trace = trace_for(log)
        trace.gate("entry_window", False, "outside")
        trace.finish("skipped", "outside entry window")
        clock.advance(1 / 3600)

    funnel = gate_funnel(log.read_all())

    assert funnel.evaluations == 50
    assert dict(funnel.blocked)["entry_window"] == 50
