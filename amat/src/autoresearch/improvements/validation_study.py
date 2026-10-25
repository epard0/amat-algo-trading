"""Fixed long-history exit-policy experiment; chronological, purged annual ML folds."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import numpy as np

# Fixed, preregistered research thresholds are deliberately literal.
# ruff: noqa: PLR2004
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, confusion_matrix, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from amat.backtest.runner import run_backtest
from amat.config.loading import load_strategy_config
from amat.config.models import StrategyConfig
from amat.data.snapshot import load_corporate_actions, load_manifest, load_panel
from autoresearch.improvements.study import digest, metrics, source_hashes, verify, write

LABELS = {
    "baseline": "v13 reference",
    "B1": "Daily selection",
    "groups": "Groups every 5 days",
    "core_daily": "Daily groups",
    "quality": "Daily groups + trend quality",
    "rules": "Daily groups + simple exit",
    "recovery": "Simple exit + staged recovery",
    "ml": "Daily groups + ML exit",
    "ml_recovery": "ML exit + staged recovery",
}
SLICES = {
    "full": ("2017-01-01", "2026-12-31"),
    "walk_forward": ("2020-01-01", "2026-12-31"),
    **{str(y): (f"{y}-01-01", f"{y}-12-31") for y in range(2017, 2027)},
    "2026_ytd": ("2026-01-01", "2026-12-31"),
    "recent": ("2026-07-13", "2026-12-31"),
    "stress_2018": ("2018-10-01", "2018-12-31"),
    "stress_covid": ("2020-02-19", "2020-04-30"),
    "stress_2022": ("2022-01-01", "2022-12-31"),
}
FEATURES = [
    "qqq_5d",
    "qqq_20d",
    "qqq_vs_200d",
    "breadth_63d",
    "cross_section_dispersion_20d",
    "qqq_vol_20d",
    "strategy_5d",
    "strategy_20d",
    "strategy_drawdown_63d",
]


def feature_matrix(prices: np.ndarray, equity: np.ndarray, benchmark: int) -> np.ndarray:
    """Close-time trailing features. No fitted preprocessing or future values."""
    out = np.full((len(prices), len(FEATURES)), np.nan)
    ret = prices[1:] / prices[:-1] - 1
    for t in range(200, len(prices)):
        q = prices[:, benchmark]
        out[t] = [
            q[t] / q[t - 5] - 1,
            q[t] / q[t - 20] - 1,
            q[t] / q[t - 199 : t + 1].mean() - 1,
            np.mean(prices[t] / prices[t - 63] > 1),
            np.std(prices[t] / prices[t - 20] - 1),
            np.std(ret[t - 20 : t, benchmark], ddof=1) * np.sqrt(252),
            equity[t] / equity[t - 5] - 1,
            equity[t] / equity[t - 20] - 1,
            equity[t] / np.max(equity[t - 62 : t + 1]) - 1,
        ]
    return out


def adverse_labels(equity: np.ndarray, horizon: int = 10) -> np.ndarray:
    """Label t matures at close t+10; adverse = any >3% loss from close t."""
    labels = np.full(len(equity), np.nan)
    for t in range(len(equity) - horizon):
        labels[t] = float(np.min(equity[t + 1 : t + horizon + 1]) / equity[t] - 1 <= -0.03)
    return labels


def walk_forward(
    sessions: tuple[dt.date, ...], features: np.ndarray, labels: np.ndarray, horizon: int = 10
) -> tuple[np.ndarray, list[dict[str, Any]], list[dict[str, Any]]]:
    """Fit once before each test year; purge labels reaching the first test date."""
    probabilities = np.full(len(sessions), np.nan)
    folds = []
    models = []
    for year in range(2020, sessions[-1].year + 1):
        test = np.array([i for i, d in enumerate(sessions) if d.year == year])
        if not len(test):
            continue
        first = int(test[0])
        train = np.array(
            [
                i
                for i in range(first - horizon)
                if sessions[i].year >= 2017
                and np.isfinite(features[i]).all()
                and np.isfinite(labels[i])
            ]
        )
        if len(train) < 250 or len(np.unique(labels[train])) < 2:
            raise ValueError("Insufficient mature training labels; refuse silent model fallback")
        model = make_pipeline(
            StandardScaler(), LogisticRegression(C=0.1, max_iter=1000, random_state=0)
        )
        model.fit(features[train], labels[train])
        probabilities[test] = model.predict_proba(features[test])[:, 1]
        valid = test[np.isfinite(labels[test])]
        y, p = labels[valid], probabilities[valid]
        folds.append(
            {
                "year": year,
                "train_rows": len(train),
                "train_start": str(sessions[train[0]]),
                "train_end": str(sessions[train[-1]]),
                "last_label_close": str(sessions[train[-1] + horizon]),
                "test_start": str(sessions[first]),
                "test_end": str(sessions[test[-1]]),
                "positive_rate": float(labels[train].mean()),
                "brier": float(brier_score_loss(y, p)),
                "base_rate_brier": float(
                    brier_score_loss(y, np.full(len(y), labels[train].mean()))
                ),
                "auc": float(roc_auc_score(y, p)) if len(np.unique(y)) > 1 else None,
                "confusion": confusion_matrix(y, p >= 0.35, labels=[0, 1]).tolist(),
                "scored_rows": len(valid),
                "exit_fraction": float(np.mean(p >= 0.35)),
            }
        )
        models.append(
            {
                "year": year,
                "features": FEATURES,
                "mean": model[0].mean_.tolist(),
                "scale": model[0].scale_.tolist(),
                "coefficient": model[1].coef_[0].tolist(),
                "intercept": float(model[1].intercept_[0]),
            }
        )
    return probabilities, folds, models


def recovery_scalars(danger: np.ndarray, staged: bool) -> np.ndarray:
    """25% risk quota; five-session minimum pause, three healthy closes per 25% step."""
    out = np.ones(len(danger))
    quota, pause, healthy = 1.0, 0, 0
    for t, bad in enumerate(danger):
        if bad:
            quota, pause, healthy = 0.25, 5, 0
        elif staged and quota < 1:
            if pause:
                pause -= 1
            else:
                healthy += 1
                if healthy >= 3:
                    quota, healthy = min(1.0, quota + 0.25), 0
        else:
            quota = 1.0
        out[t] = quota
    return out


def sliced_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, (start, end) in SLICES.items():
        indices = [i for i, r in enumerate(rows) if start <= r["session"] <= end]
        opening = rows[indices[0] - 1]["equity"] if indices and indices[0] else 100000.0
        result[key] = metrics([rows[i] for i in indices], opening)
    return result


def monthly_metrics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for month in dict.fromkeys(r["session"][:7] for r in rows):
        indices = [i for i, r in enumerate(rows) if r["session"].startswith(month)]
        opening = rows[indices[0] - 1]["equity"] if indices[0] else 100000.0
        output.append({"month": month, **metrics([rows[i] for i in indices], opening)})
    return output


def recovery_episodes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    peak, peak_date = 100000.0, rows[0]["session"]
    episode = None
    result = []
    for i, r in enumerate(rows):
        if r["equity"] >= peak:
            if episode:
                result.append(
                    {**episode, "recovered": r["session"], "sessions": i - episode["start_index"]}
                )
                episode = None
            peak, peak_date = r["equity"], r["session"]
        else:
            dd = r["equity"] / peak - 1
            if episode is None:
                episode = {
                    "peak": peak_date,
                    "start_index": i,
                    "trough": r["session"],
                    "drawdown": dd,
                }
            elif dd < episode["drawdown"]:
                episode.update(trough=r["session"], drawdown=dd)
    if episode:
        result.append(
            {**episode, "recovered": None, "sessions": len(rows) - 1 - episode["start_index"]}
        )
    return sorted(result, key=lambda r: r["drawdown"])[:10]


def register(root: Path, snapshot: Path, output: Path) -> dict[str, Any]:
    if output.exists():
        raise ValueError("Use a new immutable output")
    base = load_strategy_config(root / "configs/strategy/amat_v13_return_target.toml").strategy
    daily = load_strategy_config(root / "configs/strategy/amat_group_selection_daily.toml").strategy
    configs = {}
    for arm in LABELS:
        cfg = (base if arm in {"baseline", "B1"} else daily).model_dump(mode="json")
        if arm == "B1":
            cfg["cross_sectional"]["selection_interval_sessions"] = 1
        if arm == "groups":
            cfg["cross_sectional"]["selection_interval_sessions"] = 5
        if arm == "quality":
            cfg["structure"]["enabled"] = True
        configs[arm] = StrategyConfig.model_validate(cfg).model_dump(mode="json")
    manifest = load_manifest(snapshot)
    protocol = {
        "schema": "daily-group-validation/1",
        "root": str(root.resolve()),
        "snapshot": str(snapshot.resolve()),
        "snapshot_id": manifest.snapshot_id,
        "partition_sha256": manifest.partition_sha256,
        "source_sha256": source_hashes(root),
        "configs": configs,
        "labels": LABELS,
        "costs_bps": [2, 6, 10],
        "initial_cash": 100000,
        "execution_start": "2017-01-03",
        "ml_start": "2020-01-01",
        "features": FEATURES,
        "label_horizon_sessions": 10,
        "label": (
            "Next ten closes of ungated daily-group 2bps NAV fall at least 3% from current close"
        ),
        "ml": {
            "C": 0.1,
            "threshold": 0.35,
            "fit": ("expanding annually, purged 10 sessions; scaler trained on training only"),
        },
        "recovery": {
            "minimum_quota": 0.25,
            "pause_sessions": 5,
            "healthy_closes_per_step": 3,
            "step": 0.25,
        },
        "rules": (
            "QQQ 5-session loss worse than 4%, OR QQQ below trailing "
            "200-close mean AND negative 20-session return"
        ),
        "cost_scope": (
            "Observed 09:30-09:35 VWAP, dividends/splits, 1% volume participation, "
            "fixed 2/6/10bps per side. Spread/impact assumptions, not measured quotes. "
            "No interest on cash."
        ),
        "history_scope": (
            "2016 signal warmup; invested Jan 2017-Oct 2026. ML inactive before 2020. "
            "Common 2020+ walk-forward slice carries existing holdings; "
            "no annual account resets. All dates previously researched."
        ),
        "promotion_authority": False,
        "research_only": True,
    }
    output.mkdir(parents=True)
    write(output / "protocol.json", protocol)
    (output / "protocol.sha256").write_text(digest(protocol))
    return protocol


def run(output: Path) -> None:
    protocol = verify(output)
    snapshot = Path(protocol["snapshot"])
    core = StrategyConfig.model_validate(protocol["configs"]["core_daily"])
    symbols = list(map(str, core.universe.symbols))
    panel, _ = load_panel(snapshot, symbols, with_context=True)
    if not np.isfinite(panel.window_vwaps).all() or np.any(panel.window_volumes <= 0):
        raise ValueError("Incomplete observed execution windows")
    actions = load_corporate_actions(snapshot, symbols, sessions=panel.sessions)
    kwargs: dict[str, Any] = dict(  # noqa: C408 - keyword bundle
        actions_by_session=actions,
        execution_start=dt.date(2017, 1, 3),
        max_entry_window_participation=0.01,
    )
    reference = run_backtest(core, panel, core.to_universe(), one_way_bps=2, **kwargs)
    nav = np.full(len(panel.sessions), 100000.0)
    index = {d: i for i, d in enumerate(panel.sessions)}
    for r in reference.sessions:
        nav[index[r.session]] = float(r.equity)
    features = feature_matrix(panel.adjusted_closes, nav, symbols.index("QQQ"))
    labels = adverse_labels(nav)
    prob, folds, models = walk_forward(panel.sessions, features, labels)
    rules = np.nan_to_num(features[:, 0]) < -0.04
    rules |= (np.nan_to_num(features[:, 2]) < 0) & (np.nan_to_num(features[:, 1]) < 0)
    ml = np.isfinite(prob) & (prob >= 0.35)
    overlays = {
        "rules": recovery_scalars(rules, False),
        "recovery": recovery_scalars(rules, True),
        "ml": recovery_scalars(ml, False),
        "ml_recovery": recovery_scalars(ml, True),
    }
    diagnostics = {
        "protocol_sha256": digest(protocol),
        "folds": folds,
        "models": models,
        "predictions": [
            {
                "date": str(d),
                "probability": float(prob[i]),
                "label": float(labels[i]) if np.isfinite(labels[i]) else None,
                "rule_quota": float(overlays["recovery"][i]),
                "ml_quota": float(overlays["ml_recovery"][i]),
            }
            for i, d in enumerate(panel.sessions)
            if np.isfinite(prob[i])
        ],
        "window_audit": {"observed_cells": int(panel.window_vwaps.size), "missing_cells": 0},
    }
    write(output / "diagnostics.json", diagnostics)
    (output / "diagnostics.sha256").write_text(digest(diagnostics))
    for arm in LABELS:
        strategy = StrategyConfig.model_validate(protocol["configs"][arm])
        for cost in protocol["costs_bps"]:
            destination = output / f"{arm}-{cost}bps.json"
            if destination.exists():
                raise ValueError("Refuse overwriting a run")
            result = (
                reference
                if arm == "core_daily" and cost == 2
                else run_backtest(
                    strategy,
                    panel,
                    strategy.to_universe(),
                    one_way_bps=cost,
                    weight_scalars=overlays.get(arm),
                    **kwargs,
                )
            )
            rows = [
                {
                    "session": str(r.session),
                    "equity": float(r.equity),
                    "fees": float(r.attribution.fees),
                    "exposure": r.gross_exposure,
                    "fills": len(r.fills),
                    "missed": len(r.missed),
                }
                for r in result.sessions
            ]
            record = {
                "arm": arm,
                "cost_bps": cost,
                "protocol_sha256": digest(protocol),
                "metrics": sliced_metrics(rows),
                "sessions": rows,
                "monthly": monthly_metrics(rows),
                "recovery_episodes": recovery_episodes(rows),
                "warnings": list(result.warnings),
            }
            write(destination, record)
            destination.with_suffix(".sha256").write_text(digest(record))
            print(  # noqa: T201 - CLI progress
                f"Completed {arm} {cost}bps: {record['metrics']['full']['net_return']:.2%}",
                flush=True,
            )
    (output / "report.md").write_text(
        "# Long-history daily-group validation\n\n27 completed fixed trials. "
        "See sealed protocol, diagnostics and run records. No promotion.\n"
    )
