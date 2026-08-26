"""The v3 file contract: newsdesk writes it, amat reads it, neither imports the other.

The cross-check at the bottom is the point of the file: the exporter's real
output must load through the consumer's real parser. Everything else pins the
fail-closed edges on both sides.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

# Sibling test module: pytest's rootdir insertion makes it importable by name.
from test_newsdesk_store import article

from amat.data.scalars import load_weight_scalars
from amat.errors import DataIntegrityError
from newsdesk.errors import NewsdeskError
from newsdesk.features import AttentionParameters, attention_scalars, render_scalar_csv
from newsdesk.store import NewsStore

SESSIONS = [dt.date(2026, 8, 3) + dt.timedelta(days=offset) for offset in range(5)]


def _write(tmp_path, text: str):
    path = tmp_path / "scalars.csv"
    path.write_text(text, encoding="utf-8")
    return path


class TestTheLoaderFailsClosed:
    def test_a_complete_file_loads_in_session_order(self, tmp_path) -> None:
        lines = ["# comment", "session,scalar"]
        lines += [f"{session.isoformat()},0.5" for session in SESSIONS]
        lines += ["2026-09-01,1.0"]  # outside the panel: ignored
        scalars = load_weight_scalars(_write(tmp_path, "\n".join(lines)), SESSIONS)
        assert scalars.shape == (len(SESSIONS),)
        assert np.all(scalars == 0.5)

    def test_a_missing_session_is_fatal_not_defaulted(self, tmp_path) -> None:
        lines = ["session,scalar"] + [f"{s.isoformat()},1.0" for s in SESSIONS[:-1]]
        with pytest.raises(DataIntegrityError, match="lacks 1 of the run's 5 sessions"):
            load_weight_scalars(_write(tmp_path, "\n".join(lines)), SESSIONS)

    def test_a_duplicate_session_is_fatal(self, tmp_path) -> None:
        lines = ["session,scalar"] + [f"{s.isoformat()},1.0" for s in SESSIONS]
        lines.append(f"{SESSIONS[0].isoformat()},0.5")
        with pytest.raises(DataIntegrityError, match="duplicate session"):
            load_weight_scalars(_write(tmp_path, "\n".join(lines)), SESSIONS)

    def test_a_scalar_above_one_is_fatal(self, tmp_path) -> None:
        lines = ["session,scalar", f"{SESSIONS[0].isoformat()},1.2"]
        with pytest.raises(DataIntegrityError, match=r"outside \[0, 1\]"):
            load_weight_scalars(_write(tmp_path, "\n".join(lines)), SESSIONS[:1])

    def test_a_wrong_header_is_fatal(self, tmp_path) -> None:
        with pytest.raises(DataIntegrityError, match="header must be exactly"):
            load_weight_scalars(_write(tmp_path, "date,value\n2026-08-03,1.0"), SESSIONS[:1])


@pytest.fixture
def store(tmp_path):
    with NewsStore(tmp_path / "news.sqlite3") as opened:
        yield opened


def _seed(store, *, day: str, count: int, start_index: int) -> None:
    provider = store.register_provider("testwire", "example.test")
    run = store.open_run(provider)
    records = [
        article(
            vendor_uuid=f"u{start_index + offset}",
            url=f"https://example.test/{start_index + offset}",
        )
        for offset in range(count)
    ]
    store.store(
        records,
        provider_id=provider,
        run_id=run,
        ingested_at=f"{day}T12:00:00.000Z",
    )


class TestTheAttentionRule:
    def test_an_empty_store_exports_all_ones(self, store) -> None:
        rows = attention_scalars(store, start=dt.date(2026, 8, 1), end=dt.date(2026, 8, 10))
        assert len(rows) == 10
        assert all(scalar == 1.0 for _date, scalar, _count in rows)

    def test_a_spike_over_the_trailing_baseline_derisks_that_date(self, store) -> None:
        parameters = AttentionParameters(
            baseline_sessions=5, spike_multiple=3.0, min_baseline_articles=1, derisk_scalar=0.5
        )
        # Quiet days: one article each on Aug 1-6; then 30 on Aug 8.
        for offset in range(6):
            day = (dt.date(2026, 8, 1) + dt.timedelta(days=offset)).isoformat()
            _seed(store, day=day, count=1, start_index=offset * 100)
        _seed(store, day="2026-08-08", count=30, start_index=10_000)

        rows = attention_scalars(
            store,
            start=dt.date(2026, 8, 1),
            end=dt.date(2026, 8, 10),
            parameters=parameters,
        )
        by_date = {date: (scalar, count) for date, scalar, count in rows}
        assert by_date[dt.date(2026, 8, 8)] == (0.5, 30)
        assert by_date[dt.date(2026, 8, 6)][0] == 1.0  # quiet day stays full size
        assert by_date[dt.date(2026, 8, 3)][0] == 1.0  # inside baseline warm-up

    def test_the_warmup_never_derisks_no_matter_the_count(self, store) -> None:
        _seed(store, day="2026-08-02", count=50, start_index=0)
        rows = attention_scalars(
            store,
            start=dt.date(2026, 8, 1),
            end=dt.date(2026, 8, 4),
            parameters=AttentionParameters(baseline_sessions=5),
        )
        assert all(scalar == 1.0 for _date, scalar, _count in rows)

    def test_an_inverted_window_is_rejected(self, store) -> None:
        with pytest.raises(NewsdeskError, match="inverted"):
            attention_scalars(store, start=dt.date(2026, 8, 2), end=dt.date(2026, 8, 1))


class TestTheExportedFileLoadsThroughTheConsumer:
    def test_round_trip_from_exporter_to_loader(self, store, tmp_path) -> None:
        """The real contract: `newsdesk export-scalars` output must be exactly
        what `amat study --scalars` accepts, with the panel a strict subset of
        the exported calendar dates."""
        parameters = AttentionParameters()
        rows = attention_scalars(
            store, start=dt.date(2026, 8, 1), end=dt.date(2026, 8, 14), parameters=parameters
        )
        text = render_scalar_csv(
            rows,
            symbols=["SPY", "QQQ"],
            parameters=parameters,
            generated_at="2026-08-11T00:00:00.000Z",
        )
        path = tmp_path / "exported.csv"
        path.write_text(text, encoding="utf-8")

        weekday_sessions = [
            date
            for date, _scalar, _count in rows
            if date.weekday() < 5  # a session calendar is a subset of weekdays
        ]
        scalars = load_weight_scalars(path, weekday_sessions)
        assert scalars.shape == (len(weekday_sessions),)
        assert np.all(scalars == 1.0)
        assert "# spike_multiple: 3.0" in text
