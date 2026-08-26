"""Which engine is the candidate, and which failed experiment must survive.

This file records an engine decision and has now recorded three. The history is
the point, so it is kept rather than rewritten away:

1. v1 was dropped from the reporting surface on the impression that it performed
   poorly. Measured, it returned +39.77% against v2's +2.24% from the same
   capital, at Sharpe 0.55 against 0.18.
2. That was reversed and both engines were made first-class.
3. On 2026-08-10 v2 was rejected and its implementation deleted. It fails the
   year-concentration gate at 71.9% against a 40% cap -- an automatic rejection
   condition in ``.claude/references/promotion-gates.md`` ("depends primarily on
   one instrument, year, or handful of trades") -- and returned a tenth of v1's
   result at a fifth of its Sharpe.

Note that the figures in (1) are themselves superseded: they came from the v1
*runner* driving the *v2* configuration, so they describe a 29-symbol portfolio
rather than the frozen 12-symbol contract. See the supersession in
``docs/experiment-registry.md``. The comparison between the two engines survives
that correction, because both ran the same configuration.

The assertions below have flipped twice now, and that is legitimate: they encode
a decision, not a law. What has never flipped is the reason the file exists.

**A rejected experiment is evidence.** ``.claude/rules/30-quant-methodology.md``
requires every tested configuration to stay registered "including failures", and
``promotion-gates.md`` makes a report that "omits failed experiments" an
automatic rejection. What satisfies that is the registry entry and the hashed
configuration the results cite -- not a rejected engine left importable. So the
deletion is asserted, and so is the survival of the record, separately.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from amat.backtest.sleeves import ENGINE_LABELS, STUDY_ENGINES

_ROOT = Path(__file__).resolve().parents[2]

#: Commands that choose between runners.
_ENGINE_COMMANDS = ("study", "report")


def _engine_action(command: str) -> argparse.Action:
    """The ``--engine`` action belonging to one subcommand's parser."""
    from amat.cli.main import build_parser

    subparsers = [
        action
        for action in build_parser()._actions
        if isinstance(action, argparse._SubParsersAction)
    ]
    assert len(subparsers) == 1, "the CLI grew a second subparser group"
    parser = subparsers[0].choices[command]
    actions = [action for action in parser._actions if action.dest == "engine"]
    assert actions, f"`amat {command}` has no --engine option"
    return actions[0]


class TestV1IsTheCandidate:
    @pytest.mark.parametrize("command", _ENGINE_COMMANDS)
    def test_the_default_is_v1(self, command: str) -> None:
        action = _engine_action(command)
        assert action.default == "v1", (
            f"`amat {command}` defaults to {action.default!r}; v2 was rejected on "
            "2026-08-10 and must not be produced for users who pass no flag"
        )

    def test_the_plan_command_defaults_to_v1(self) -> None:
        """The plan command is the one that could reach an account. It defaults
        to the engine that is actually a candidate."""
        action = _engine_action("plan")
        assert action.default == "v1-frozen"

    def test_the_v1_configuration_is_shipped(self) -> None:
        from amat.config.loading import load_strategy_config

        config = _ROOT / "configs" / "strategy" / "amat_v1.toml"
        assert config.is_file(), "the v1 strategy configuration is no longer shipped"
        assert load_strategy_config(config).strategy.universe.symbols


class TestTheRejectedExperimentSurvivesAsEvidence:
    """The code is gone. The record is not, and deleting the record would be a
    reporting defect in its own right.

    ``.claude/rules/30-quant-methodology.md`` requires every tested configuration
    to stay registered "including failures", and ``promotion-gates.md`` makes a
    report that "omits failed experiments" an automatic rejection. Those are
    satisfied by the registry and by the configuration file the results cite --
    not by keeping a rejected engine importable. That the overlay was built,
    tuned, measured, and found to destroy 94% of the return is the result worth
    preserving; its implementation is not.
    """

    def test_the_v2_engine_is_gone(self) -> None:
        import importlib

        for module in (
            "amat.backtest.managed",
            "amat.risk.guards",
            "amat.risk.kelly",
            "amat.risk.trades",
            "amat.risk.expectancy",
        ):
            with pytest.raises(ModuleNotFoundError):
                importlib.import_module(module)

    def test_the_engine_registry_offers_only_the_survivor(self) -> None:
        assert STUDY_ENGINES == {"v1": ("v1-frozen",)}
        assert set(ENGINE_LABELS) == {"v1-frozen"}

    def test_the_rejection_is_recorded_in_the_registry(self) -> None:
        """In the registry, not the config file. A strategy config is hashed into
        every run manifest, so annotating one changes its digest and orphans
        every result that cited it -- demonstrated 2026-08-10, when adding this
        very note to ``amat_managed_v2.toml`` moved it from 72bff815 to
        dce36c9a. Decisions belong in the registry; the hashed artifact stays
        immutable."""
        registry = _ROOT / "docs" / "experiment-registry.md"
        assert registry.is_file(), "the experiment registry is missing"
        text = registry.read_text(encoding="utf-8")
        assert "REJECTED" in text
        assert "amat_managed_v2.toml" in text
        assert "72bff815afc9f513" in text, "the rejected config's digest is unrecorded"

    def test_the_registry_records_the_candidate_too(self) -> None:
        """A registry of only failures is as partial as one of only successes."""
        text = (_ROOT / "docs" / "experiment-registry.md").read_text(encoding="utf-8")
        assert "amat_v1.toml" in text
        assert "ea8cbd5ad7418a38" in text

    def test_the_rejected_configuration_is_still_shipped_and_still_loads(self) -> None:
        """It is the hashed artifact every recorded v2 result cites. Deleting it
        would make those results uncitable, which is the deletion that actually
        destroys evidence."""
        from amat.config.loading import load_strategy_config

        config = _ROOT / "configs" / "strategy" / "amat_managed_v2.toml"
        assert config.is_file(), "the rejected v2 configuration was deleted"
        resolved = load_strategy_config(config)
        assert resolved.content_sha256.startswith("72bff815afc9f513")
        assert len(resolved.strategy.universe.symbols) == 29


class TestBacktestShortcut:
    """`amat backtest -v vN`: the one-line front end over the study pipeline."""

    def test_month_bounds_resolve_to_month_edges(self) -> None:
        import datetime as dt

        from amat.cli.main import _month_end, _month_start

        assert _month_start("2019-06") == dt.date(2019, 6, 1)
        assert _month_end("2019-06") == dt.date(2019, 6, 30)
        assert _month_end("2020-02") == dt.date(2020, 2, 29)  # leap year
        assert _month_end("2024-12") == dt.date(2024, 12, 31)  # no successor month
        # Full dates pass through untouched, so precise windows stay available.
        assert _month_start("2019-06-17") == dt.date(2019, 6, 17)
        assert _month_end("2019-06-17") == dt.date(2019, 6, 17)

    def test_nonsense_bounds_are_refused(self) -> None:
        from amat.cli.main import _month_end, _month_start

        with pytest.raises(ValueError, match="month"):
            _month_start("2019-13")
        with pytest.raises(ValueError, match="Invalid isoformat"):
            _month_end("June 2019")

    def test_every_version_choice_maps_to_a_shipped_config_or_a_refusal(self) -> None:
        from amat.cli.main import _BACKTEST_VERSION_CONFIGS, build_parser

        parser = build_parser()
        args = parser.parse_args(["backtest", "-v", "v1"])
        choices = ("v1", "v2", "v3", "v4", "v5", "v6")
        assert args.strategy_version == "v1"
        for version in choices:
            if version == "v2":
                # v2 has no config mapping: the handler refuses it by design.
                assert version not in _BACKTEST_VERSION_CONFIGS
            else:
                assert _BACKTEST_VERSION_CONFIGS[version].exists(), version

    def test_v2_refuses_with_the_registry_as_the_record(self, capsys) -> None:
        from amat.cli.main import _command_backtest, build_parser

        args = build_parser().parse_args(["backtest", "-v", "v2"])
        assert _command_backtest(args) == 2
        message = capsys.readouterr().err
        assert "rejected 2026-08-10" in message
        assert "experiment-registry" in message

    def test_scalars_outside_v3_is_refused(self) -> None:
        from amat.cli.main import _command_backtest, build_parser
        from amat.errors import ConfigurationError

        args = build_parser().parse_args(["backtest", "-v", "v1", "--scalars", "somewhere.csv"])
        with pytest.raises(ConfigurationError, match="v3"):
            _command_backtest(args)

    def test_the_default_out_path_is_dated_and_versioned(self, monkeypatch) -> None:
        # The default is resolved inside the handler; intercept the dispatch to
        # observe the namespace the study pipeline would receive. importlib,
        # because `amat.cli.main` the attribute is the entry-point *function*
        # re-exported by the package, which shadows the module on plain import.
        import importlib

        cli = importlib.import_module("amat.cli.main")

        captured: dict[str, object] = {}

        def fake_study(ns: argparse.Namespace) -> int:
            captured["pdf"] = ns.pdf
            captured["config"] = ns.config
            return 0

        monkeypatch.setattr(cli, "_command_study", fake_study)
        args = cli.build_parser().parse_args(["backtest", "-v", "v5", "--snapshot", "x"])
        assert cli._command_backtest(args) == 0
        pdf = captured["pdf"]
        assert isinstance(pdf, Path)
        assert pdf.parent == Path("outputs/reports")
        assert pdf.name.startswith("v5-") and pdf.suffix == ".pdf"
        assert captured["config"] == Path("configs/strategy/amat_v5.toml")

    def test_the_newest_snapshot_is_chosen_lexically(self, tmp_path: Path) -> None:
        from amat.cli.main import _newest_snapshot
        from amat.errors import ConfigurationError

        (tmp_path / "alpaca-sip-2026-08-08").mkdir()
        (tmp_path / "alpaca-sip-2026-08-11").mkdir()
        (tmp_path / "stray-file").touch()
        assert _newest_snapshot(tmp_path).name == "alpaca-sip-2026-08-11"
        with pytest.raises(ConfigurationError, match="no snapshot"):
            _newest_snapshot(tmp_path / "empty")
