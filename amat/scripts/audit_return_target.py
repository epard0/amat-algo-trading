"""Audit saved return-target simulations and report benchmark/uncertainty evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np

from amat.data.snapshot import load_corporate_actions, load_panel
from amat.domain.actions import ActionKind
from amat.domain.types import Symbol

ANNUAL = 252
BOOTSTRAPS = 2000
BLOCK = 60
TARGET = 0.10
TOLERANCE = Decimal("0.000001")


def write(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, default=str, allow_nan=False) + "\n")


def path_statistics(equity: np.ndarray) -> dict[str, float]:
    returns = equity[1:] / equity[:-1] - 1
    volatility = float(returns.std(ddof=1) * np.sqrt(ANNUAL))
    cagr = float((equity[-1] / equity[0]) ** (ANNUAL / len(returns)) - 1)
    return {
        "cagr": cagr,
        "volatility": volatility,
        "max_drawdown": float((equity / np.maximum.accumulate(equity) - 1).min()),
        "conventional_sharpe": float(returns.mean() * ANNUAL / volatility) if volatility else 0,
    }


def uncertainty(returns: np.ndarray) -> dict[str, Any]:
    rng = np.random.default_rng(20260914)
    estimates = []
    for _ in range(BOOTSTRAPS):
        starts = rng.integers(0, len(returns) - BLOCK + 1, size=(len(returns) + BLOCK - 1) // BLOCK)
        indices = (starts[:, None] + np.arange(BLOCK)).ravel()[: len(returns)]
        estimates.append(float(np.exp(np.log1p(returns[indices]).mean() * ANNUAL) - 1))
    return {
        "block_sessions": BLOCK,
        "resamples": BOOTSTRAPS,
        "cagr_90pct_interval": np.quantile(estimates, [0.05, 0.95]).tolist(),
        "resample_fraction_cagr_at_least_10pct": float(np.mean(np.array(estimates) >= TARGET)),
        "interpretation": "Conditional historical block-bootstrap diagnostic; overlapping blocks "
        "and adaptive selection prevent interpreting this as future odds.",
    }


def prefix_check(training: list[dict[str, Any]], extended: list[dict[str, Any]]) -> dict[str, Any]:
    """A one-session-lag run omits only the terminal decision past its end date.

    Terminal equity, cash, exposure, fills, misses and attribution must still
    match. Every field of every earlier record must match exactly.
    """
    omitted = {"forecast_volatility_pre", "forecast_volatility_post", "binding_constraints"}
    if not training or len(extended) < len(training):
        return {"passed": False, "reason": "missing prefix records"}
    financial_equal = {k: v for k, v in training[-1].items() if k not in omitted} == {
        k: v for k, v in extended[len(training) - 1].items() if k not in omitted
    }
    earlier_equal = training[:-1] == extended[: len(training) - 1]
    return {
        "passed": financial_equal and earlier_equal,
        "all_preterminal_fields_equal": earlier_equal,
        "terminal_financial_fields_equal": financial_equal,
        "terminal_decision_difference": "Training run omits allocation diagnostics for the "
        "decision that would execute after its terminal date.",
    }


def main() -> None:  # noqa: PLR0912, PLR0915 - sequential evidence audit
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    protocol = json.loads((output / "protocol.json").read_text())
    symbols = tuple(
        s for c in protocol["configs"]["baseline"]["universe"]["clusters"] for s in c["symbols"]
    )
    panel, manifest = load_panel(Path(protocol["snapshot"]), symbols)
    if manifest.partition_sha256 != protocol["partition_sha256"]:
        raise ValueError("Audit snapshot differs from the registered data.")
    actions = load_corporate_actions(Path(protocol["snapshot"]), symbols, sessions=panel.sessions)
    index = {str(d): i for i, d in enumerate(panel.sessions)}
    columns = {str(s): i for i, s in enumerate(panel.symbols)}
    report: dict[str, Any] = {"scope": "Historical simulated execution", "runs": {}}
    for directory in sorted((output / "runs").iterdir()):
        daily = json.loads((directory / "daily.json").read_text())
        metadata = json.loads((directory / "summary.json").read_text())
        quantities: dict[str, Decimal] = defaultdict(Decimal)
        previous_values: dict[str, Decimal] = defaultdict(Decimal)
        pnl: dict[str, Decimal] = defaultdict(Decimal)
        weights: dict[str, list[float]] = defaultdict(list)
        errors: list[Any] = []
        identity_error = Decimal(0)
        position_error = Decimal(0)
        largest_buy = 0.0
        for number, row in enumerate(daily):
            idx = index[row["session"]]
            dividends: dict[str, Decimal] = defaultdict(Decimal)
            trading: dict[str, Decimal] = defaultdict(Decimal)
            costs: dict[str, Decimal] = defaultdict(Decimal)
            bought: dict[str, Decimal] = defaultdict(Decimal)
            for action in actions.get(panel.sessions[idx], ()):
                symbol = str(action.symbol)
                if action.kind == ActionKind.SPLIT:
                    quantities[symbol] *= action.ratio
                else:
                    dividends[symbol] += action.cash_credit(quantities[symbol])
            for fill in row["fills"]:
                symbol = fill["symbol"]
                column = columns[symbol]
                qty, price = Decimal(fill["quantity"]), Decimal(fill["price"])
                expected_price = Decimal(str(round(float(panel.window_vwaps[idx, column]), 6)))
                if (
                    price != expected_price
                    or panel.window_volumes[idx, column] <= 0
                    or fill["execution_session"] != row["session"]
                ):
                    errors.append([row["session"], symbol, "execution window mismatch"])
                expected_cost = qty * price * Decimal(str(metadata["one_way_bps"])) / 10000
                if abs(Decimal(fill["cost_cash"]) - expected_cost) > TOLERANCE:
                    errors.append([row["session"], symbol, "cost mismatch"])
                signed = qty if fill["side"] == "buy" else -qty
                quantities[symbol] += signed
                trading[symbol] += signed * price
                costs[symbol] += Decimal(fill["cost_cash"])
                if signed > 0:
                    bought[symbol] += qty
                if quantities[symbol] < 0:
                    errors.append([row["session"], symbol, "negative position"])
            for symbol, qty in bought.items():
                fraction = float(qty) / panel.window_volumes[idx, columns[symbol]]
                largest_buy = max(largest_buy, fraction)
                if fraction > 0.01 + 1e-12:
                    errors.append([row["session"], symbol, "participation exceeded"])
            marks = panel.marks_at(idx)
            values = {symbol: quantities[symbol] * marks[Symbol(symbol)] for symbol in symbols}
            equity, cash = Decimal(row["equity"]), Decimal(row["cash"])
            position_error = max(position_error, abs(equity - cash - sum(values.values())))
            if cash < 0:
                errors.append([row["session"], "negative cash"])
            if number:
                a = {key: Decimal(value) for key, value in row["attribution"].items()}
                change = (
                    a["market_pnl"]
                    + a["distributions"]
                    + a["cash_yield"]
                    - a["fees"]
                    - a["slippage"]
                )
                identity_error = max(
                    identity_error, abs(equity - Decimal(daily[number - 1]["equity"]) - change)
                )
            for symbol in symbols:
                if row["session"] >= protocol["development"][0]:
                    pnl[symbol] += (
                        values[symbol]
                        - previous_values[symbol]
                        - trading[symbol]
                        + dividends[symbol]
                        - costs[symbol]
                    )
                    weights[symbol].append(float(values[symbol] / equity))
                previous_values[symbol] = values[symbol]
        passed = not errors and max(identity_error, position_error) <= TOLERANCE
        report["runs"][directory.name] = {
            "passed": passed,
            "errors": errors,
            "identity_error": identity_error,
            "position_reconciliation_error": position_error,
            "max_buy_window_participation": largest_buy,
            "minimum_cash": min(Decimal(row["cash"]) for row in daily),
            "missed_reasons": dict(Counter(m["reason"] for row in daily for m in row["missed"])),
            "symbol_pnl_including_dividends_and_costs": pnl,
            "average_symbol_weights": {
                symbol: float(np.mean(values)) for symbol, values in weights.items()
            },
        }
    report["passed"] = all(r["passed"] for r in report["runs"].values())
    write(output / "execution-audit.json", report)

    selected = json.loads((output / "selection.json").read_text())["selected"]
    evidence = {}
    for name in ("baseline", selected):
        files = list((output / "runs").glob(f"{name}-2026-09-11-2-1-None-1/daily.json"))
        if not files:
            continue
        full = json.loads(files[0].read_text())
        train = json.loads(
            (output / "runs" / f"{name}-2022-12-30-2-1-None-1/daily.json").read_text()
        )
        prefix = prefix_check(train, full)
        evidence[name] = {"development_prefix_check": prefix}
        if not prefix["passed"]:
            report["passed"] = False
        for period, first, last in (
            ("development", "2018-01-02", "2022-12-30"),
            ("later", "2023-01-03", "2026-09-11"),
            ("full", "2018-01-02", "2026-09-11"),
        ):
            start = next(i for i, row in enumerate(full) if row["session"] >= first)
            stop = max(i for i, row in enumerate(full) if row["session"] <= last) + 1
            rows = full[start - 1 : stop]
            equity = np.array([float(row["equity"]) for row in rows])
            dates = [index[row["session"]] for row in rows]
            prices = panel.adjusted_closes[dates]
            spy = prices[:, columns["SPY"]] / prices[0, columns["SPY"]]
            equal = np.mean(prices / prices[0], axis=1)
            returns = equity[1:] / equity[:-1] - 1
            strategy = path_statistics(equity)
            spy_returns = spy[1:] / spy[:-1] - 1
            beta = float(np.cov(returns, spy_returns)[0, 1] / np.var(spy_returns, ddof=1))
            evidence[name][period] = {
                "strategy": strategy,
                "bootstrap": uncertainty(returns),
                "spy_frictionless_total_return": path_statistics(spy),
                "equal_weight_buy_hold_frictionless": path_statistics(equal),
                "correlation_spy": float(np.corrcoef(returns, spy_returns)[0, 1]),
                "beta_spy": beta,
                "benchmark_limit": "Adjusted-close benchmarks include reinvested distributions "
                "and omit trading costs; strategy pays costs and receives cash distributions.",
            }
    write(output / "diagnostics.json", evidence)
    write(output / "execution-audit.json", report)
    write(
        output / "audit-source.json",
        {"script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
    )
    print(json.dumps({"passed": report["passed"], "audited_runs": len(report["runs"])}))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
