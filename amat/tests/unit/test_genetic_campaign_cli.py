import argparse
import datetime as dt
import json
from pathlib import Path

import pytest

from autoresearch.genetic import cli, multitimeframe, shadow_campaign, shadow_intraday


@pytest.mark.parametrize(
    "command,arguments,module,function",
    [
        (
            "shadow-register",
            [
                "--artifact",
                "a.json",
                "--snapshot",
                "snapshot",
                "--intraday-data",
                "m.json",
                "--intraday-calibration-end",
                "2026-09-11",
            ],
            shadow_campaign,
            "register_campaign",
        ),
        (
            "shadow-poll",
            [
                "--campaign",
                "c.json",
                "--through",
                "2026-09-28",
                "--ledger",
                "l.json",
                "--intraday-data",
                "m.json",
            ],
            shadow_campaign,
            "poll_campaign",
        ),
        (
            "shadow-fetch-intraday",
            ["--symbols", "SPY", "QQQ", "--start", "2026-08-17", "--end", "2026-09-28"],
            shadow_intraday,
            "fetch_intraday",
        ),
        (
            "shadow-spot-intraday",
            [
                "--artifact",
                "a.json",
                "--intraday-data",
                "m.json",
                "--intraday-calibration-end",
                "2026-09-11",
                "--as-of",
                "2026-09-28T20:00:00+00:00",
            ],
            multitimeframe,
            "spot_intraday",
        ),
    ],
)
def test_campaign_cli_routes_typed_inputs(  # noqa: PLR0917 - pytest parameters
    monkeypatch, capsys, command, arguments, module, function
):
    called = []
    monkeypatch.setattr(module, function, lambda *a, **kw: called.append((a, kw)) or {"ok": True})
    parser = argparse.ArgumentParser()
    cli.configure_parser(parser)
    args = parser.parse_args([command, *arguments, "--output", "output.json"])
    assert cli.dispatch(args) == 0
    assert len(called) == 1
    assert capsys.readouterr().out.strip().startswith("{")
    if command == "shadow-register":
        assert called[0][1]["intraday_calibration_end"] == dt.date(2026, 9, 11)
        assert called[0][1]["intraday_data"] == Path("m.json")
    elif command == "shadow-poll":
        assert called[0][1]["through"] == dt.date(2026, 9, 28)
    elif command == "shadow-spot-intraday":
        assert called[0][1]["as_of"].tzinfo is not None
    else:
        assert called[0][0][1] == ("SPY", "QQQ")


def test_refresh_cli_routes_provider_and_frozen_inputs(monkeypatch):
    from amat.data import alpaca
    from autoresearch.genetic import shadow_refresh

    called = []
    fake = object()
    monkeypatch.setattr(alpaca, "AlpacaMarketData", lambda **_: fake)
    monkeypatch.setattr(
        shadow_refresh, "refresh_shadow", lambda *a, **kw: called.append((a, kw)) or {}
    )
    parser = argparse.ArgumentParser()
    cli.configure_parser(parser)
    args = parser.parse_args(
        [
            "shadow-refresh",
            "--previous-artifact",
            "a.json",
            "--snapshot",
            "old",
            "--receipts",
            "receipts",
            "--root",
            ".",
            "--snapshot-id",
            "new",
            "--baseline-end",
            "2026-09-25",
            "--output",
            "out",
        ]
    )
    assert cli.dispatch(args) == 0
    assert called[0][1]["client"] is fake
    assert called[0][1]["baseline_end"] == dt.date(2026, 9, 25)
    assert called[0][0] == (Path("a.json"), Path("old"), Path("receipts"))


def test_intraday_audit_cli_routes_typed_inputs(monkeypatch):
    from autoresearch.genetic import intraday_quality

    called = []
    monkeypatch.setattr(
        intraday_quality, "audit_intraday", lambda *a, **kw: called.append((a, kw)) or {}
    )
    parser = argparse.ArgumentParser()
    cli.configure_parser(parser)
    args = parser.parse_args(
        [
            "shadow-audit-intraday",
            "--intraday-data",
            "minutes.json",
            "--intraday-calibration-end",
            "2026-09-11",
            "--as-of",
            "2026-09-28T20:00:00+00:00",
            "--output",
            "audit.json",
        ]
    )
    assert cli.dispatch(args) == 0
    assert called[0][0] == (Path("minutes.json"), dt.date(2026, 9, 11))
    assert called[0][1]["as_of"].tzinfo is not None
    assert called[0][1]["output"] == Path("audit.json")


def test_native_fetch_cli_routes_separate_contract(monkeypatch):
    from autoresearch.genetic import native_intraday

    called = []
    monkeypatch.setattr(
        native_intraday, "fetch_native_intraday", lambda *a, **kw: called.append((a, kw)) or {}
    )
    parser = argparse.ArgumentParser()
    cli.configure_parser(parser)
    args = parser.parse_args(
        [
            "shadow-fetch-native",
            "--symbols",
            "SPY",
            "IEF",
            "--start",
            "2026-06-01",
            "--end",
            "2026-09-28",
            "--timeframe",
            "1h",
            "--output",
            "native.json",
        ]
    )
    assert cli.dispatch(args) == 0
    assert called[0][0] == (
        Path("native.json"),
        ("SPY", "IEF"),
        dt.date(2026, 6, 1),
        dt.date(2026, 9, 28),
    )
    assert called[0][1] == {"timeframe": "1h"}


def test_native_comparison_cli_routes_typed_inputs(monkeypatch):
    from autoresearch.genetic import native_comparison

    called = []
    monkeypatch.setattr(
        native_comparison, "audit_native_intraday", lambda *a, **kw: called.append((a, kw)) or {}
    )
    parser = argparse.ArgumentParser()
    cli.configure_parser(parser)
    args = parser.parse_args(
        [
            "shadow-compare-native",
            "--native-data",
            "native.json",
            "--intraday-data",
            "minutes.json",
            "--intraday-calibration-end",
            "2026-09-11",
            "--as-of",
            "2026-09-28T20:00:00+00:00",
            "--output",
            "comparison.json",
        ]
    )
    assert cli.dispatch(args) == 0
    assert called[0][0] == (Path("native.json"), Path("minutes.json"), dt.date(2026, 9, 11))
    assert called[0][1]["as_of"].tzinfo is not None
    assert called[0][1]["output"] == Path("comparison.json")


def test_parameter_search_cli_routes_budget_and_dates(monkeypatch):
    from autoresearch.genetic import parameter_study

    called = []
    monkeypatch.setattr(parameter_study, "run_study", lambda *a, **kw: called.append((a, kw)) or {})
    parser = argparse.ArgumentParser()
    cli.configure_parser(parser)
    args = parser.parse_args(
        [
            "search-parameters",
            "--artifact",
            "a.json",
            "--snapshot",
            "snapshot",
            "--native-data",
            "native.json",
            "--measured-start",
            "2020-01-02",
            "--train-end",
            "2023-12-29",
            "--validation-end",
            "2024-12-31",
            "--global-trials",
            "2048",
            "--local-trials",
            "256",
            "--workers",
            "6",
            "--output",
            "study",
        ]
    )
    assert cli.dispatch(args) == 0
    assert called[0][0] == (Path("a.json"), Path("snapshot"), Path("native.json"))
    assert called[0][1]["global_trials"] == 2048
    assert called[0][1]["train_end"] == dt.date(2023, 12, 29)
    assert called[0][1]["workers"] == 6


def test_walkforward_cli_routes_registered_folds_and_budget(monkeypatch, tmp_path):
    from autoresearch.genetic import walkforward_study

    called = []
    monkeypatch.setattr(
        walkforward_study, "run_walkforward_study", lambda *a, **kw: called.append((a, kw)) or {}
    )
    folds = [
        {
            "name": "outer-2024",
            "measured_start": "2020-01-02",
            "train_end": "2022-12-30",
            "validation_end": "2023-12-29",
            "test_end": "2024-12-31",
        }
    ]
    path = tmp_path / "folds.json"
    path.write_text(json.dumps(folds))
    parser = argparse.ArgumentParser()
    cli.configure_parser(parser)
    args = parser.parse_args(
        [
            "walkforward-parameters",
            "--artifact",
            "a.json",
            "--snapshot",
            "snapshot",
            "--native-data",
            "native.json",
            "--folds",
            str(path),
            "--output",
            "study",
        ]
    )
    assert cli.dispatch(args) == 0
    assert called[0][0] == (Path("a.json"), Path("snapshot"), Path("native.json"))
    assert called[0][1]["folds"] == folds
    assert called[0][1]["global_trials"] == 640
    assert called[0][1]["local_trials"] == 128


def test_component_cli_routes_frozen_study_and_costs(monkeypatch):
    from autoresearch.genetic import component_study

    called = []
    monkeypatch.setattr(
        component_study, "run_component_study", lambda *a, **kw: called.append((a, kw)) or {}
    )
    parser = argparse.ArgumentParser()
    cli.configure_parser(parser)
    args = parser.parse_args(
        [
            "compare-components",
            "--artifact",
            "a.json",
            "--snapshot",
            "snapshot",
            "--native-data",
            "native.json",
            "--p11-study",
            "p11/study",
            "--costs",
            "2",
            "10",
            "20",
            "--workers",
            "3",
            "--output",
            "p12/study",
        ]
    )
    assert cli.dispatch(args) == 0
    assert called[0][0] == (Path("a.json"), Path("snapshot"), Path("native.json"))
    assert called[0][1] == {
        "p11_study": Path("p11/study"),
        "output": Path("p12/study"),
        "costs": (2.0, 10.0, 20.0),
        "workers": 3,
    }
