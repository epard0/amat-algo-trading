import datetime as dt
from dataclasses import replace
from pathlib import Path

import exchange_calendars as xcals
import numpy as np
import pytest

from amat.backtest.panel import MarketPanel
from autoresearch.genetic.models import parse_pattern
from autoresearch.genetic.multitimeframe import aggregate_completed, multi_timeframe_report
from autoresearch.genetic.shadow import _prefix

DOCS = Path(__file__).parents[2] / "docs/genetic-patterns"


def panel(start="2023-01-01", end="2024-07-05"):
    sessions = tuple(x.date() for x in xcals.get_calendar("XNYS", start=start, end=end).sessions)
    c = (100 + np.arange(len(sessions), dtype=float))[:, None]
    return MarketPanel(
        sessions=sessions,
        symbols=("SPY",),
        adjusted_closes=c,
        raw_closes=c,
        window_vwaps=c,
        window_volumes=np.ones_like(c),
        source="fixture",
        adjusted_opens=c - 0.5,
        adjusted_highs=c + 1,
        adjusted_lows=c - 1,
        session_volumes=np.full_like(c, 100),
    )


def test_holiday_week_ohlcv_and_no_unfinished_period():
    data = panel("2024-07-01", "2024-07-05")
    week = aggregate_completed(data, "1w")
    assert week.sessions == (dt.date(2024, 7, 5),)
    assert week.adjusted_opens[0, 0] == 99.5
    assert week.adjusted_highs[0, 0] == 104
    assert week.adjusted_lows[0, 0] == 99
    assert week.adjusted_closes[0, 0] == 103
    assert week.session_volumes[0, 0] == 400
    assert len(aggregate_completed(_prefix(data, dt.date(2024, 7, 3)), "1w")) == 0
    assert len(aggregate_completed(data, "1mo")) == 0


def test_good_friday_is_closed_thursday_and_partial_first_week_omitted():
    data = panel("2024-03-25", "2024-03-28")
    assert aggregate_completed(data, "1w").sessions == (dt.date(2024, 3, 28),)
    shortened = replace(
        data,
        sessions=data.sessions[1:],
        **{
            name: getattr(data, name)[1:]
            for name in (
                "adjusted_closes",
                "raw_closes",
                "window_vwaps",
                "window_volumes",
                "adjusted_opens",
                "adjusted_highs",
                "adjusted_lows",
                "session_volumes",
            )
        },
    )
    assert len(aggregate_completed(shortened, "1w")) == 0


def test_gaps_rejected_missing_values_propagate():
    data = panel("2024-07-01", "2024-07-05")
    # Replace the first session with the preceding trading date: one expected day is missing.
    with pytest.raises(ValueError, match="missing"):
        aggregate_completed(
            replace(data, sessions=(dt.date(2024, 6, 28), *data.sessions[1:])), "1w"
        )
    data.adjusted_closes[1, 0] = np.nan
    assert np.isnan(aggregate_completed(data, "1w").adjusted_closes[0, 0])


@pytest.mark.parametrize("filename", ["candle.example.json", "symbolic.example.json"])
def test_each_timeframe_has_explicit_pattern_and_own_calibration(filename):
    data = panel()
    pattern = parse_pattern((DOCS / filename).read_text())
    reference = _prefix(data, dt.date(2023, 12, 29))
    report = multi_timeframe_report(pattern, reference, data)
    assert report["execution_authority"] == "none"
    assert list(report["views"]) == ["1d", "1w", "1mo"]
    for tf, view in report["views"].items():
        assert view["pattern"]["timeframe"] == tf
        assert not view["historical_validation_transferred"]
        assert view["calibration_bars"] < view["available_bars"]
    if filename.startswith("symbolic"):
        assert report["views"]["1mo"]["status"] == "unavailable_or_insufficient_history"


def test_future_mutation_does_not_change_completed_week():
    data = panel("2024-06-03", "2024-07-03")
    earlier = aggregate_completed(_prefix(data, dt.date(2024, 6, 28)), "1w")
    data.adjusted_closes[-1] *= 100
    later = aggregate_completed(data, "1w")
    np.testing.assert_equal(earlier.adjusted_closes, later.adjusted_closes)


def test_unknown_and_duplicate_timeframes_rejected():
    data = panel()
    pattern = parse_pattern((DOCS / "candle.example.json").read_text())
    with pytest.raises(ValueError, match="unique"):
        multi_timeframe_report(pattern, data, data, timeframes=("1d", "1d"))
    with pytest.raises(ValueError, match="supported"):
        aggregate_completed(data, "15m")


def test_intraday_view_calibration_parity_and_explicit_15m_hourly_schema(tmp_path):
    import json

    from autoresearch.genetic.data import digest_json
    from autoresearch.genetic.multitimeframe import (
        freeze_intraday_reference,
        intraday_timeframe_report,
    )

    rows = []
    for day in (26, 28):
        for minute in range(390 if day == 26 else 210):
            price = 100 + minute * 0.01 + day
            rows.append(
                {
                    "symbol": "SPY",
                    "t": (
                        dt.datetime(2025, 11, day, 14, 30, tzinfo=dt.UTC)
                        + dt.timedelta(minutes=minute)
                    ).isoformat(),
                    "o": price,
                    "h": price + 2,
                    "l": price - 1,
                    "c": price + 1,
                    "v": 10.0,
                }
            )
    payload = {
        "schema_version": "shadow-intraday-data/1",
        "provider": "alpaca",
        "feed": "sip",
        "adjustment": "all",
        "symbols": ["SPY"],
        "start": "2025-11-26",
        "end": "2025-11-28",
        "collected_at": "2025-11-28T18:01:00+00:00",
        "minute_records": rows,
        "timestamp_semantics": "interval start; available after one minute",
        "volume_basis": "vendor all-adjusted volume",
    }
    path = tmp_path / "minute.json"

    def save():
        path.write_text(json.dumps({"payload": payload, "payload_sha256": digest_json(payload)}))

    save()
    reference = freeze_intraday_reference(path, dt.date(2025, 11, 26))
    pattern = parse_pattern((DOCS / "candle.example.json").read_text())
    report = intraday_timeframe_report(
        pattern, reference, path, as_of=dt.datetime(2025, 11, 28, 18, 1, tzinfo=dt.UTC)
    )
    assert set(report["views"]) == {"15m", "1h"}
    assert report["views"]["15m"]["calibration_bars"] == 26
    assert report["views"]["1h"]["calibration_bars"] == 6
    for tf, view in report["views"].items():
        assert view["pattern"]["timeframe"] == tf
        assert view["status"] == "ready"
    # Symbolic kernels also run with an explicit intraday binding.
    data = parse_pattern((DOCS / "symbolic.example.json").read_text()).model_dump(by_alias=True)
    data["genome"]["root"] = {"node": "constant", "value": 0.5}
    symbolic = intraday_timeframe_report(
        parse_pattern(data), reference, path, as_of=dt.datetime(2025, 11, 28, 18, 1, tzinfo=dt.UTC)
    )
    assert symbolic["views"]["1h"]["assets"][0]["pattern_value"] == pytest.approx(np.tanh(0.5))
    rows[0]["o"] += 0.1
    save()
    with pytest.raises(ValueError, match="calibration changed"):
        intraday_timeframe_report(
            pattern, reference, path, as_of=dt.datetime(2025, 11, 28, 18, 1, tzinfo=dt.UTC)
        )


def test_intraday_latest_must_match_expected_closed_interval(tmp_path):

    from test_genetic_shadow_intraday import DAY, NOW, Feed

    from autoresearch.genetic.multitimeframe import (
        freeze_intraday_reference,
        intraday_timeframe_report,
    )
    from autoresearch.genetic.shadow_intraday import fetch_intraday

    path = tmp_path / "minutes.json"
    fetch_intraday(path, ("SPY",), DAY, DAY, client=Feed(), now=NOW)
    reference = freeze_intraday_reference(path, DAY)
    pattern = parse_pattern((DOCS / "candle.example.json").read_text())
    report = intraday_timeframe_report(
        pattern, reference, path, as_of=dt.datetime(2025, 12, 1, 21, tzinfo=dt.UTC)
    )
    assert all(view["status"] == "stale_source" for view in report["views"].values())
    assert report["views"]["1h"]["expected_latest_close"] == "2025-12-01T20:30:00+00:00"
