"""Reproducible return-target research with a frozen development/validation split.

Run from amat/: python scripts/return_target_study.py run|register|screen|validate
Each output directory is one immutable protocol. No broker or model-provider calls.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import pickle
import shutil
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

from amat.backtest.runner import BacktestResult, run_backtest
from amat.config.models import StrategyConfig
from amat.config.v6 import load_v6_config
from amat.data.snapshot import load_corporate_actions, load_manifest, load_panel
from amat.metrics import summarize
from autoresearch.objective import anchored_slice

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "outputs/return-target-2026-09-14/allocation-v1"
SNAPSHOT = ROOT / "data/snapshots/alpaca-sip-core-2026-09-14-verified"
START = dt.date(2018, 1, 2)
TRAIN_END = dt.date(2022, 12, 30)
END = dt.date(2026, 9, 11)
ANNUAL_SESSIONS = 252
TARGET = 0.10
DRAWDOWN_LIMIT = -0.25
MIN_POSITIVE_YEARS = 0.6
CAPS = {
    "us_equity": 0.70,
    "international": 0.35,
    "rates": 0.35,
    "metals": 0.35,
    "commodities": 0.30,
    "us_dollar": 0.15,
}
RECIPES: dict[str, dict[str, Any]] = {
    "baseline": {},
    "risk08": {"risk": 0.08},
    "risk10": {"risk": 0.10},
    "risk12": {"risk": 0.12},
    "wider_caps12": {"risk": 0.12, "caps": True},
    "signal_weighted06": {"power": 0},
    "signal_weighted12": {"risk": 0.12, "power": 0},
    "growth12": {"risk": 0.12, "caps": True, "power": 0},
    "slow_growth12": {"risk": 0.12, "caps": True, "power": 0, "slow": True},
    "rotation3_12": {"risk": 0.12, "caps": True, "power": 0, "slow": True, "top": 3},
    "rotation5_12": {"risk": 0.12, "caps": True, "power": 0, "slow": True, "top": 5},
    "growth16": {"risk": 0.16, "caps": True, "power": 0},
    "rotation3_16": {"risk": 0.16, "caps": True, "power": 0, "slow": True, "top": 3},
    "rotation5_16": {"risk": 0.16, "caps": True, "power": 0, "slow": True, "top": 5},
}


def json_ready(value: Any) -> Any:
    """Unavailable warm-up diagnostics are null, never a nonstandard JSON NaN."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: json_ready(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_ready(item) for item in value]
    return value


def write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_ready(payload), indent=2, default=str, allow_nan=False) + "\n")


def source_hashes() -> dict[str, str]:
    paths = [
        *sorted((ROOT / "src/amat").rglob("*.py")),
        Path(__file__),
        ROOT / "src/autoresearch/objective.py",
        ROOT / "configs/strategy/amat_v6.toml",
    ]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def make_config(name: str, recipe: dict[str, Any]) -> StrategyConfig:
    baseline = load_v6_config(ROOT / "configs/strategy/amat_v6.toml").config.strategy
    data = baseline.model_dump(mode="json", by_alias=True)
    if name != "baseline":
        data.update(name="amat-return-target-" + name, version="13.0.0")
    data["constraints"]["target_annualized_volatility"] = recipe.get("risk", 0.06)
    data["allocation"] = {"volatility_power": recipe.get("power", 1)}
    if recipe.get("caps"):
        data["constraints"]["max_symbol_weight"] = 0.35
        for cluster in data["universe"]["clusters"]:
            cluster["gross_cap"] = CAPS[cluster["name"]]
    if recipe.get("growth_caps"):
        data["constraints"]["max_symbol_weight"] = 0.45
        growth_caps = {
            **CAPS,
            "us_equity": 0.85,
            "international": 0.45,
            "metals": 0.45,
            "commodities": 0.35,
        }
        for cluster in data["universe"]["clusters"]:
            cluster["gross_cap"] = growth_caps[cluster["name"]]
    if "rebalance" in recipe:
        data["rebalance"]["min_weight_delta"] = recipe["rebalance"]
    if recipe.get("slow"):
        data["trend"]["horizons_sessions"] = [63, 126, 252]
    if recipe.get("top"):
        data["cross_sectional"].update(
            enabled=True, max_positions=recipe["top"], min_horizon_agreement=2
        )
    if "selection_interval" in recipe:
        data["cross_sectional"]["selection_interval_sessions"] = recipe["selection_interval"]
    return StrategyConfig.model_validate(data)


def measured(result: BacktestResult, start: dt.date, end: dt.date) -> BacktestResult:
    dates = result.session_dates
    first = next(i for i, d in enumerate(dates) if d >= start)
    stop = max(i for i, d in enumerate(dates) if d <= end) + 1
    return anchored_slice(result, first, stop)


def details(result: BacktestResult, *, target_cagr: float = TARGET) -> dict[str, Any]:
    summary = summarize(result).as_dict()
    equity = np.array([float(r.equity) for r in result.sessions])
    daily = equity[1:] / equity[:-1] - 1
    sd = daily.std(ddof=1)
    folds: dict[str, dict[str, Any]] = {}
    for year in sorted({r.session.year for r in result.sessions[1:]}):
        part = measured(result, max(START, dt.date(year, 1, 1)), dt.date(year, 12, 31))
        folds[str(year)] = summarize(part).as_dict()
    annual = (
        equity[ANNUAL_SESSIONS:] / equity[:-ANNUAL_SESSIONS] - 1
        if len(equity) > ANNUAL_SESSIONS
        else np.array([])
    )
    notional = sum(
        float(f.price * f.quantity) / float(row.equity)
        for row in result.sessions[1:]
        for f in row.fills
    )
    summary.update(
        conventional_sharpe=float(daily.mean() / sd * np.sqrt(252)) if sd else 0,
        annual_folds=folds,
        positive_year_fraction=sum(float(f["total_return"]) > 0 for f in folds.values())
        / len(folds),
        annual_one_way_turnover=notional * 252 / len(daily),
        rolling_year_fraction_above_target=float(np.mean(annual >= target_cagr))
        if len(annual)
        else None,
        worst_rolling_year=float(annual.min()) if len(annual) else None,
        mean_forecast_volatility=float(
            np.mean([r.forecast_volatility_post for r in result.sessions[1:]])
        ),
        binding_counts=dict(Counter(c for r in result.sessions[1:] for c in r.binding_constraints)),
    )
    return summary


class Study:
    def __init__(self, output: Path, end: dt.date) -> None:
        self.output = output
        self.protocol = read_protocol(output)
        if source_hashes() != self.protocol["source_sha256"]:
            raise ValueError("Source changed after registration; register a new output directory.")
        baseline = make_config("baseline", {})
        self.panel, manifest = load_panel(SNAPSHOT, baseline.universe.symbols, end=end)
        if manifest.partition_sha256 != self.protocol["partition_sha256"]:
            raise ValueError("Snapshot changed after registration.")
        self.actions = load_corporate_actions(
            SNAPSHOT, self.panel.symbols, sessions=self.panel.sessions
        )

    def run(
        self,
        name: str,
        *,
        cost: float = 2,
        lag: int = 1,
        missed: int | None = None,
        scalar: float = 1,
    ) -> BacktestResult:
        config = StrategyConfig.model_validate(self.protocol["configs"][name])
        data = config.model_dump(mode="json", by_alias=True)
        data["execution"]["signal_to_execution_lag_sessions"] = lag
        config = StrategyConfig.model_validate(data)
        key = f"{name}-{self.panel.sessions[-1]}-{cost}-{lag}-{missed}-{scalar}"
        folder = self.output / "runs" / key
        cache = folder / "result.pickle"
        if cache.exists():
            # Locally generated, never imported from external run artifacts.
            with cache.open("rb") as handle:
                cached = pickle.load(handle)  # noqa: S301
                if not isinstance(cached, BacktestResult):
                    raise ValueError("Invalid cached backtest result")
                return cached
        result = run_backtest(
            config,
            self.panel,
            config.to_universe(),
            actions_by_session=self.actions,
            one_way_bps=cost,
            max_entry_window_participation=0.01,
            drop_every_nth_fill=missed,
            weight_scalars=np.full(len(self.panel), scalar),
        )
        folder.mkdir(parents=True, exist_ok=True)
        with cache.open("wb") as handle:
            pickle.dump(result, handle)
        write(folder / "daily.json", [asdict(row) for row in result.sessions])
        write(
            folder / "summary.json",
            {
                **summarize(result).as_dict(),
                "config": config.model_dump(mode="json", by_alias=True),
                "warnings": result.warnings,
                "scalar": scalar,
                "missed_every": missed,
            },
        )
        return result


def register(
    output: Path, recipes_path: Path | None = None, *, target_cagr: float = TARGET
) -> None:
    target_cagr = parse_target(target_cagr)
    if (output / "protocol.json").exists():
        raise ValueError("Protocol already exists. Use screen/validate or a new output directory.")
    manifest = load_manifest(SNAPSHOT)
    recipes = json.loads(recipes_path.read_text()) if recipes_path else RECIPES
    if recipes.get("baseline") != {}:
        raise ValueError("A study requires the unchanged baseline recipe.")
    configs = {
        name: make_config(name, recipe).model_dump(mode="json", by_alias=True)
        for name, recipe in recipes.items()
    }
    write(
        output / "protocol.json",
        {
            "schema": "return-target/2",
            "registered_at": dt.datetime.now(dt.UTC),
            "source_sha256": source_hashes(),
            "snapshot": str(SNAPSHOT),
            "partition_sha256": manifest.partition_sha256,
            "development": [START, TRAIN_END],
            "later_historical_validation": ["2023-01-03", END],
            "target_cagr": target_cagr,
            "drawdown_limit": 0.25,
            "max_gross": 1,
            "cash_yield": 0,
            "costs_bps": [2, 6, 10],
            "entry_window_participation": 0.01,
            "recipes": recipes,
            "configs": configs,
            "candidate_count": len(configs) - 1,
            "selection_rule": (
                "Select highest 6bps development CAGR among candidates with >baseline 2bps CAGR, "
                "drawdown >=-25% at both costs, and >=60% positive development years. "
                "Validate at most one. No later-period retuning."
            ),
            "historical_target_rule": (
                f"Selected candidate must achieve >={target_cagr:.2%} CAGR "
                "in development and later validation "
                "at 2 and 6bps, drawdown >=-25%, and positive return at 10bps, lag2, and "
                "every-fifth-fill missed. Report uncertainty, concentration, "
                "and benchmarks separately."
            ),
            "evidence_limit": (
                "All available history has been used in prior research. Later validation is "
                "chronological but not pristine. No realized or guaranteed future annual return "
                "is established."
            ),
        },
    )
    (output / "protocol.sha256").write_text(
        hashlib.sha256((output / "protocol.json").read_bytes()).hexdigest() + "\n"
    )
    for relative in source_hashes():
        target = output / "source" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    print(f"Registered {len(configs) - 1} candidates in {output}", flush=True)


def screen(output: Path) -> None:
    study = Study(output, TRAIN_END)
    rows: dict[str, dict[str, Any]] = {}
    for name in study.protocol["configs"]:
        rows[name] = {}
        for cost in (2, 6):
            result = measured(study.run(name, cost=cost), START, TRAIN_END)
            rows[name][str(cost)] = details(result, target_cagr=study.protocol["target_cagr"])
        write(output / "development.json", rows)
        a, b = rows[name]["2"], rows[name]["6"]
        print(
            f"{name}: CAGR {a['annualized_return']:.2%}, 6bps {b['annualized_return']:.2%}, "
            f"DD {a['max_drawdown']:.2%}, exposure {a['average_gross_exposure']:.2%}",
            flush=True,
        )
    eligible = [
        name
        for name, row in rows.items()
        if name != "baseline"
        and row["2"]["annualized_return"] > rows["baseline"]["2"]["annualized_return"]
        and all(
            row[c]["max_drawdown"] >= DRAWDOWN_LIMIT
            and row[c]["positive_year_fraction"] >= MIN_POSITIVE_YEARS
            for c in ("2", "6")
        )
    ]
    selected = (
        max(eligible, key=lambda name: rows[name]["6"]["annualized_return"]) if eligible else None
    )
    write(
        output / "selection.json",
        {
            "selected": selected,
            "selected_at": dt.datetime.now(dt.UTC),
            "eligible": eligible,
            "development_sha256": hashlib.sha256(
                (output / "development.json").read_bytes()
            ).hexdigest(),
        },
    )
    print(f"Selected: {selected}", flush=True)


def validate(output: Path) -> None:
    selection = json.loads((output / "selection.json").read_text())
    if (
        selection["development_sha256"]
        != hashlib.sha256((output / "development.json").read_bytes()).hexdigest()
    ):
        raise ValueError("Development results changed after selection.")
    selected = selection["selected"]
    if selected is None:
        raise ValueError("No development candidate selected; validation remains closed.")
    study = Study(output, END)
    rows: dict[str, dict[str, Any]] = {}
    for name in ("baseline", selected):
        rows[name] = {}
        for label, options in {
            "base": {},
            "cost6": {"cost": 6},
            "cost10": {"cost": 10},
            "lag2": {"lag": 2},
            "miss20": {"missed": 5},
        }.items():
            result = study.run(name, **options)
            rows[name][label] = {
                "later": details(
                    measured(result, dt.date(2023, 1, 3), END),
                    target_cagr=study.protocol["target_cagr"],
                ),
                "full": details(
                    measured(result, START, END), target_cagr=study.protocol["target_cagr"]
                ),
            }
            write(output / "validation.json", rows)
            metrics = rows[name][label]["later"]
            print(
                f"{name} {label}: later CAGR {metrics['annualized_return']:.2%}, "
                f"DD {metrics['max_drawdown']:.2%}",
                flush=True,
            )
    development = json.loads((output / "development.json").read_text())
    target = all(
        development[selected][c]["annualized_return"] >= study.protocol["target_cagr"]
        and rows[selected][label]["later"]["annualized_return"] >= study.protocol["target_cagr"]
        and rows[selected][label]["later"]["max_drawdown"] >= DRAWDOWN_LIMIT
        for c, label in (("2", "base"), ("6", "cost6"))
    )
    stress = all(
        rows[selected][label]["later"]["annualized_return"] > 0
        and rows[selected][label]["later"]["max_drawdown"] >= DRAWDOWN_LIMIT
        for label in ("cost10", "lag2", "miss20")
    )
    write(
        output / "decision.json",
        {
            "selected": selected,
            "historical_target_met": target and stress,
            "target_cagr": study.protocol["target_cagr"],
            "future_target_established": False,
            "status": "research_only",
            "evidence_limit": study.protocol["evidence_limit"],
        },
    )

    from autoresearch.best_strategies import preserve_return_reference  # noqa: PLC0415

    preserve_return_reference(output, ROOT.parent.parent / "amat-research")


def parse_target(value: str | float) -> float:
    target = float(value)
    if not math.isfinite(target) or not 0 < target <= 1:
        raise ValueError("target CAGR must be a decimal in (0, 1], e.g. 0.20")
    return target


def read_protocol(output: Path) -> dict[str, Any]:
    body = (output / "protocol.json").read_bytes()
    protocol = json.loads(body)
    if not isinstance(protocol, dict):
        raise ValueError("Invalid return-target protocol")
    seal = output / "protocol.sha256"
    if (protocol.get("schema") == "return-target/2" or seal.exists()) and (
        seal.read_text().strip() != hashlib.sha256(body).hexdigest()
    ):
        raise ValueError("Protocol changed after registration; use a new output directory.")
    parse_target(protocol["target_cagr"])
    return protocol


def run_study(output: Path, recipes: Path | None, *, target_cagr: float) -> None:
    """One bounded registered study, with at most one later-period candidate."""
    register(output, recipes, target_cagr=target_cagr)
    screen(output)
    if json.loads((output / "selection.json").read_text())["selected"] is None:
        write(
            output / "decision.json",
            {
                "selected": None,
                "historical_target_met": False,
                "target_cagr": target_cagr,
                "future_target_established": False,
                "status": "no_eligible_development_candidate",
                "validation_opened": False,
            },
        )
        print("No eligible development candidate; validation remains closed.", flush=True)
        return
    validate(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("run", "register", "screen", "validate"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--recipes", type=Path, help="Explicit fixed recipes for a new protocol")
    parser.add_argument(
        "--target-cagr",
        type=parse_target,
        default=None,
        help="Frozen target for a new run/register, e.g. 0.20 for 20%%",
    )
    args = parser.parse_args()
    if args.phase in {"screen", "validate"} and args.target_cagr is not None:
        parser.error("The target is frozen; --target-cagr is only for run/register")
    if args.phase in {"run", "register"}:
        action = run_study if args.phase == "run" else register
        action(args.output.resolve(), args.recipes, target_cagr=args.target_cagr or TARGET)
    else:
        {"screen": screen, "validate": validate}[args.phase](args.output.resolve())


if __name__ == "__main__":
    main()
