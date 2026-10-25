"""Fixed sensitivity and exposure controls for an already selected return recipe."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import pickle
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
from return_target_study import END, START, TRAIN_END, Study, details, measured, write

from amat.backtest.runner import run_backtest
from amat.config.models import StrategyConfig
from amat.metrics import summarize

EXPOSURE_TOLERANCE = 0.02
DRAWDOWN_LIMIT = -0.25


def main() -> None:  # noqa: PLR0915 - registered controls and exact exposure replays
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    study = Study(output, END)
    selected = json.loads((output / "selection.json").read_text())["selected"]
    original = study.protocol["configs"][selected]
    changes = {
        "risk16": ("constraints", "target_annualized_volatility", 0.16),
        "risk24": ("constraints", "target_annualized_volatility", 0.24),
        "selection4": ("cross_sectional", "selection_interval_sessions", 4),
        "selection6": ("cross_sectional", "selection_interval_sessions", 6),
        "daily_ranking": ("cross_sectional", "selection_interval_sessions", 1),
        "inverse_volatility": ("allocation", "volatility_power", 1),
    }
    configs = {}
    for name, (section, key, value) in changes.items():
        config = copy.deepcopy(original)
        config.setdefault(section, {})[key] = value
        configs[name] = StrategyConfig.model_validate(config)
    protocol = {
        "selected": selected,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "configs": {name: c.model_dump(mode="json", by_alias=True) for name, c in configs.items()},
        "cost_bps": 6,
        "rule": "Sensitivity only; never reselect on these results. Neighbors should retain "
        "positive later return and drawdown above -25%. Report their median later CAGR. "
        "Daily ranking and inverse-volatility controls are ablations, not neighbors.",
        "exposure_match": "Retrospectively reduce the more exposed strategy; recompute fills at "
        "2bps and require mean-exposure error <=2 percentage points. "
        "These period-specific controls are not deployable policies.",
    }
    path = output / "control-protocol.json"
    if path.exists() and json.loads(path.read_text()) != protocol:
        raise ValueError("Controls changed after registration.")
    write(path, protocol)
    comparisons: dict[str, Any] = {}
    for name, config in configs.items():
        folder = output / "runs" / ("control-" + name)
        cache = folder / "result.pickle"
        if cache.exists():
            with cache.open("rb") as handle:
                result = pickle.load(handle)  # noqa: S301 - local generated cache
        else:
            result = run_backtest(
                config,
                study.panel,
                config.to_universe(),
                actions_by_session=study.actions,
                one_way_bps=6,
                max_entry_window_participation=0.01,
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
                },
            )
        comparisons[name] = {
            "development": details(measured(result, START, TRAIN_END)),
            "later": details(measured(result, dt.date(2023, 1, 3), END)),
        }
        write(output / "controls.json", comparisons)
        print(name, comparisons[name]["later"]["annualized_return"], flush=True)
    matches = {}
    for period, start, end in (
        ("development", START, TRAIN_END),
        ("later", dt.date(2023, 1, 3), END),
    ):
        baseline = measured(study.run("baseline"), start, end)
        candidate = measured(study.run(selected), start, end)
        base_exposure = summarize(baseline).average_gross_exposure
        candidate_exposure = summarize(candidate).average_gross_exposure
        higher = selected if candidate_exposure > base_exposure else "baseline"
        target = min(base_exposure, candidate_exposure)
        scalar = target / max(base_exposure, candidate_exposure)
        for _ in range(4):
            matched = measured(study.run(higher, scalar=scalar), start, end)
            exposure = summarize(matched).average_gross_exposure
            if abs(exposure - target) <= EXPOSURE_TOLERANCE:
                break
            scalar = min(1, scalar * target / exposure)
        left, right = (matched, baseline) if higher == selected else (candidate, matched)
        a, b = summarize(left), summarize(right)
        matches[period] = {
            "candidate": a.as_dict(),
            "baseline": b.as_dict(),
            "scalar": scalar,
            "exposure_error": abs(a.average_gross_exposure - b.average_gross_exposure),
            "sharpe_difference": a.sharpe - b.sharpe,
            "cagr_difference": a.annualized_return - b.annualized_return,
        }
        write(output / "exposure-matches.json", matches)
    neighbors = [comparisons[n]["later"] for n in ("risk16", "risk24", "selection4", "selection6")]
    write(
        output / "control-outcome.json",
        {
            "neighbor_median_later_cagr": float(
                np.median([n["annualized_return"] for n in neighbors])
            ),
            "neighbors_pass": all(
                n["annualized_return"] > 0 and n["max_drawdown"] >= DRAWDOWN_LIMIT
                for n in neighbors
            ),
            "matches_resolved": all(
                m["exposure_error"] <= EXPOSURE_TOLERANCE for m in matches.values()
            ),
            "no_reselection": True,
        },
    )


if __name__ == "__main__":
    main()
