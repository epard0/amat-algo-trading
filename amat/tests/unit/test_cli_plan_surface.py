"""The ``plan`` command's surface and the position file it trusts.

A position file is the one input to this repository that describes real money.
Everything else is either immutable evidence or a validated configuration; this
is a hand-maintained or externally generated file naming what an account holds,
and every plan is sized against it.

Its failure mode is quiet. A symbol dropped by a parser looks identical to a
symbol the account does not hold, and the plan built from it will buy something
already owned. So the loader fails closed on anything it cannot interpret, and
these tests hold that line one malformation at a time.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from amat.cli.main import _load_positions, build_parser
from amat.errors import ConfigurationError


def write(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "positions.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestTheCommandExists:
    def test_plan_is_a_subcommand(self) -> None:
        args = build_parser().parse_args(
            ["plan", "config.toml", "--snapshot", "snap", "--equity", "100000"]
        )
        assert args.command == "plan"

    def test_equity_is_required_rather_than_assumed(self) -> None:
        """A default account size would size real trades from a guess."""
        with pytest.raises(SystemExit):
            build_parser().parse_args(["plan", "config.toml", "--snapshot", "snap"])

    def test_equity_parses_as_decimal_not_float(self) -> None:
        args = build_parser().parse_args(
            ["plan", "config.toml", "--snapshot", "snap", "--equity", "123456.78"]
        )
        assert args.equity == Decimal("123456.78")

    def test_news_asset_artifact_is_an_explicit_plan_input(self) -> None:
        args = build_parser().parse_args(
            [
                "plan",
                "config.toml",
                "--snapshot",
                "snap",
                "--equity",
                "1",
                "--news-assets",
                "assets.json",
            ]
        )
        assert args.news_assets == Path("assets.json")

    def test_only_the_surviving_engine_is_reachable(self) -> None:
        """This assertion is the reverse of what it was, and the reversal is the
        point.

        It previously required *both* ``v1-frozen`` and ``v2-managed`` to parse.
        When v2 was deleted on 2026-08-10 the implementation went but the
        ``--engine`` choice list did not, so the flag kept advertising an engine
        that no longer existed -- and this test kept passing, which is exactly
        how the stale surface survived the deletion. A dead choice on the one
        command that sizes real trades is worse than a missing one: it parses,
        then fails somewhere less obvious.
        """
        args = build_parser().parse_args(
            ["plan", "config.toml", "--snapshot", "snap", "--equity", "1", "--engine", "v1-frozen"]
        )
        assert args.engine == "v1-frozen"

    def test_the_deleted_engine_is_refused_at_the_parser(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(
                [
                    "plan",
                    "config.toml",
                    "--snapshot",
                    "snap",
                    "--equity",
                    "1",
                    "--engine",
                    "v2-managed",
                ]
            )


class TestPositionLoading:
    def test_no_file_means_a_flat_account(self) -> None:
        assert _load_positions(None) == {}

    def test_share_counts_are_exact(self, tmp_path: Path) -> None:
        """Read as Decimal from text: a float round-trip is a wrong share count."""
        path = write(tmp_path, {"SPY": "120.123456789"})
        assert _load_positions(path) == {"SPY": Decimal("120.123456789")}

    def test_symbols_are_normalized(self, tmp_path: Path) -> None:
        path = write(tmp_path, {" spy ": "10"})
        assert _load_positions(path) == {"SPY": Decimal("10")}

    def test_a_missing_file_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="could not read positions"):
            _load_positions(tmp_path / "absent.json")

    def test_malformed_json_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "positions.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ConfigurationError, match="could not read positions"):
            _load_positions(path)

    def test_a_json_list_is_refused(self, tmp_path: Path) -> None:
        path = write(tmp_path, ["SPY", "10"])
        with pytest.raises(ConfigurationError, match="mapping symbol to share count"):
            _load_positions(path)

    def test_a_non_numeric_share_count_is_refused(self, tmp_path: Path) -> None:
        path = write(tmp_path, {"SPY": "many"})
        with pytest.raises(ConfigurationError, match="non-numeric share count"):
            _load_positions(path)

    def test_a_nan_share_count_is_refused(self, tmp_path: Path) -> None:
        """``Decimal("NaN")`` parses without error and poisons every sum."""
        path = write(tmp_path, {"SPY": "NaN"})
        with pytest.raises(ConfigurationError, match="non-finite share count"):
            _load_positions(path)

    def test_an_infinite_share_count_is_refused(self, tmp_path: Path) -> None:
        path = write(tmp_path, {"SPY": "Infinity"})
        with pytest.raises(ConfigurationError, match="non-finite share count"):
            _load_positions(path)

    def test_a_short_position_is_refused(self, tmp_path: Path) -> None:
        """Long-or-cash: there is no correct plan against a short."""
        path = write(tmp_path, {"SPY": "-10"})
        with pytest.raises(ConfigurationError, match="long-or-cash"):
            _load_positions(path)

    def test_an_empty_symbol_is_refused(self, tmp_path: Path) -> None:
        path = write(tmp_path, {"   ": "10"})
        with pytest.raises(ConfigurationError, match="non-empty symbol"):
            _load_positions(path)

    def test_a_zero_holding_is_kept_rather_than_dropped(self, tmp_path: Path) -> None:
        """Explicitly flat and unmentioned are the same here, but say so once."""
        path = write(tmp_path, {"SPY": "0"})
        assert _load_positions(path) == {"SPY": Decimal("0")}
