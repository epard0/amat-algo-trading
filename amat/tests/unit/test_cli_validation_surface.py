"""The validation commands' argument surface.

Parser-level only, mirroring ``test_cli_plan_surface.py``: these tests pin
the flags and defaults the documentation promises, without running a
backtest. Handler behaviour is covered by the module tests
(walk-forward/montecarlo/regimes/sweep) and by the run-record tests.
"""

from __future__ import annotations

import argparse
from decimal import Decimal
from pathlib import Path

import pytest

from amat.cli.main import _parse_target_list, build_parser


class TestValidationCommandSurface:
    def test_every_validation_command_is_registered(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["walkforward", "config.toml", "--snapshot", "snap"])
        assert args.command == "walkforward"
        assert args.initial_cash == Decimal("100000")
        assert args.allow_holdout is None
        assert args.run_root == Path("outputs/runs")
        assert args.no_run_record is False

        args = parser.parse_args(["declare-holdout", "--snapshot", "snap", "--reason", "why"])
        assert args.command == "declare-holdout"

        args = parser.parse_args(["montecarlo", "config.toml", "--snapshot", "snap"])
        assert args.paths == 200
        assert args.block_sessions == 21
        assert args.seed == 20260812
        assert args.workers is None
        assert args.pdf is None

        args = parser.parse_args(
            [
                "sweep-target-vol",
                "config.toml",
                "--snapshot",
                "snap",
                "--train-end",
                "2022-12-30",
            ]
        )
        assert args.targets == (0.05, 0.06, 0.10, 0.12)

        args = parser.parse_args(["regime-experiment", "config.toml", "--snapshot", "snap"])
        assert args.reference == "SPY"
        assert args.quantile == 0.80
        assert args.reduced_scalar == 0.50

        args = parser.parse_args(
            ["sweep-exit-rules", "config.toml", "--snapshot", "snap", "--train-end", "2022-12-30"]
        )
        assert args.stop_losses == (0.05, 0.10, 0.15)
        assert args.take_profits == (0.10, 0.20)
        assert args.embargo_sessions == 21

    def test_train_end_is_required_for_the_sweep(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["sweep-target-vol", "config.toml", "--snapshot", "snap"])

    def test_studies_carry_the_run_record_options(self) -> None:
        parser = build_parser()
        for command in (
            ["study", "config.toml", "--snapshot", "snap"],
            ["study-v4", "config.toml", "--snapshot", "snap"],
        ):
            args = parser.parse_args(command)
            assert args.run_root == Path("outputs/runs")
            assert args.no_run_record is False

    def test_every_panel_command_carries_the_holdout_escape_hatch(self) -> None:
        # Regression for the defect where only walkforward could authorize
        # (or refuse) holdout access; every panel-measuring command enforces
        # the boundary now, so every one needs the recorded escape hatch.
        parser = build_parser()
        for command in (
            ["walkforward", "config.toml", "--snapshot", "snap"],
            ["montecarlo", "config.toml", "--snapshot", "snap"],
            ["regime-experiment", "config.toml", "--snapshot", "snap"],
            [
                "sweep-target-vol",
                "config.toml",
                "--snapshot",
                "snap",
                "--train-end",
                "2022-12-30",
            ],
            ["study", "config.toml", "--snapshot", "snap"],
            ["study-v4", "config.toml", "--snapshot", "snap"],
        ):
            args = parser.parse_args(command)
            assert args.allow_holdout is None
        args = parser.parse_args(
            ["montecarlo", "config.toml", "--snapshot", "snap", "--allow-holdout", "why"]
        )
        assert args.allow_holdout == "why"


class TestTargetListParsing:
    def test_targets_parse_and_validate(self) -> None:
        assert _parse_target_list("0.06, 0.10") == (0.06, 0.10)

    @pytest.mark.parametrize("raw", ["", "abc", "0.0", "1.5", "0.06,zero"])
    def test_unusable_targets_are_refused(self, raw: str) -> None:
        with pytest.raises(argparse.ArgumentTypeError):
            _parse_target_list(raw)


class TestV6ConfigFallback:
    """Measuring commands accept the v6 superset file via the v6 loader."""

    def test_the_v6_file_resolves_to_its_strategy_core(self) -> None:
        from amat.cli.main import _load_any_strategy_config

        resolved = _load_any_strategy_config(Path("configs/strategy/amat_v6.toml"))
        assert resolved.strategy.version.startswith("6.")
        assert resolved.content_sha256
        assert resolved.source_path is not None

    def test_a_genuinely_malformed_file_keeps_the_plain_error(self, tmp_path: Path) -> None:
        from amat.cli.main import _load_any_strategy_config
        from amat.errors import ConfigurationError

        bad = tmp_path / "bad.toml"
        bad.write_text('name = "x"\nversion = "1.0.0"\n', encoding="utf-8")
        with pytest.raises(ConfigurationError, match="invalid strategy configuration"):
            _load_any_strategy_config(bad)


class TestInspectionCommandsAcceptEverySchema:
    """`validate-config`/`show-config`/`show-universe` on all three schemas.

    Regression for a real defect: these commands called the plain loader
    directly, so they reported *every* v6 and v4 file in ``configs/strategy/``
    as invalid — naming the superset tables as "Extra inputs are not
    permitted" — while the engine ran those same files daily. A checker that
    fails valid input is a broken checker.
    """

    @pytest.mark.parametrize(
        ("config", "expected_schema", "expected_extra"),
        [
            ("amat_v1.toml", "v1", ()),
            ("amat_v3.toml", "v1", ()),
            ("amat_v5.toml", "v1", ()),
            ("amat_v4.toml", "v4", ("trend", "switching", "defensive")),
            ("amat_v6.toml", "v6", ("asset_regime", "liquidity", "risk_learning")),
        ],
    )
    def test_every_checked_in_config_resolves_to_its_own_schema(
        self, config: str, expected_schema: str, expected_extra: tuple[str, ...]
    ) -> None:
        from amat.cli.main import _resolve_config_for_inspection, _schema_key

        resolved, extra = _resolve_config_for_inspection(Path("configs/strategy") / config)
        assert extra == expected_extra
        assert _schema_key(extra) == expected_schema
        # Whichever schema matched, the caller always receives a usable strategy
        # core -- that is the contract the three commands depend on.
        assert resolved.strategy.universe.symbols
        assert resolved.content_sha256

    def test_a_malformed_file_still_reports_the_plain_loader_error(self, tmp_path: Path) -> None:
        from amat.cli.main import _resolve_config_for_inspection
        from amat.errors import ConfigurationError

        bad = tmp_path / "bad.toml"
        bad.write_text('name = "x"\n', encoding="utf-8")
        with pytest.raises(ConfigurationError, match="invalid strategy configuration"):
            _resolve_config_for_inspection(bad)

    def test_an_unknown_table_on_an_otherwise_v1_file_is_not_silently_accepted(
        self, tmp_path: Path
    ) -> None:
        """The fallbacks must not become a hole that swallows typos.

        A v1 file with a misspelled table matches no schema, so the plain
        loader's error is what surfaces -- naming the offending table.
        """
        from amat.cli.main import _resolve_config_for_inspection
        from amat.errors import ConfigurationError

        source = Path("configs/strategy/amat_v1.toml").read_text(encoding="utf-8")
        typo = tmp_path / "typo.toml"
        typo.write_text(source + "\n[warmupp]\nmin_completed_sessions = 252\n", encoding="utf-8")
        with pytest.raises(ConfigurationError, match="Extra inputs are not permitted"):
            _resolve_config_for_inspection(typo)
