"""Reproducible analysis for the August 24 simulation experiments.

This module reads the immutable per-session SQLite ledgers in read-only mode.
It intentionally keeps live trades, paper trades, and strategy-lab positions
separate.  Counterfactual exit comparisons are only made between lab wallets
that entered the same ticker/outcome in the same isolated session.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ExperimentRun:
    label: str
    directory: Path

    @property
    def ledger(self) -> Path:
        return self.directory / "trade_ledger.db"

    @property
    def lab(self) -> Path:
        return self.directory / "strategy_lab.db"


def _connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _number(value: Any) -> float:
    return 0.0 if value in (None, "") else float(value)


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * fraction))
    return ordered[index]


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _run_record(connection: sqlite3.Connection) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM data_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise ValueError("ledger has no data_runs record")
    return row


def _trade_rows(connection: sqlite3.Connection, run: sqlite3.Row) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT * FROM trades
        WHERE placed_at >= ? AND (? IS NULL OR placed_at <= ?)
        ORDER BY placed_at, trade_id
        """,
        (run["started_at"], run["ended_at"], run["ended_at"]),
    ).fetchall()


def _quality(connection: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    quality_events = (
        connection.execute(
            "SELECT COUNT(*) FROM data_quality_events WHERE run_id=?", (run_id,)
        ).fetchone()[0]
        if _table_exists(connection, "data_quality_events")
        else 0
    )
    duplicate_snapshots = connection.execute(
        """
        SELECT COUNT(*) FROM (
          SELECT snapshot_id FROM market_snapshots WHERE run_id=?
          GROUP BY snapshot_id HAVING COUNT(*) > 1
        )
        """,
        (run_id,),
    ).fetchone()[0]
    orphan_depth = connection.execute(
        """
        SELECT COUNT(*) FROM orderbook_levels l
        LEFT JOIN market_snapshots s ON s.snapshot_id=l.snapshot_id
        WHERE s.snapshot_id IS NULL
        """
    ).fetchone()[0]
    invalid_books = connection.execute(
        """
        SELECT COUNT(*) FROM market_snapshots
        WHERE run_id=? AND (
          CAST(yes_bid AS REAL) < 0 OR CAST(yes_ask AS REAL) > 1 OR
          CAST(yes_bid AS REAL) > CAST(yes_ask AS REAL) OR
          CAST(spread AS REAL) < 0
        )
        """,
        (run_id,),
    ).fetchone()[0]
    return {
        "integrity": integrity,
        "quality_events": quality_events,
        "duplicate_snapshot_ids": duplicate_snapshots,
        "orphan_orderbook_levels": orphan_depth,
        "invalid_top_of_book_rows": invalid_books,
        "passed": integrity == "ok"
        and quality_events == 0
        and duplicate_snapshots == 0
        and orphan_depth == 0
        and invalid_books == 0,
    }


def _market_summary(connection: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT ticker, asset, observed_at, spread, implied_probability_yes
        FROM market_snapshots WHERE run_id=? ORDER BY ticker, observed_at
        """,
        (run_id,),
    ).fetchall()
    spreads = [_number(row["spread"]) for row in rows]
    cadence: list[float] = []
    switches = 0
    by_ticker: dict[str, list[sqlite3.Row]] = defaultdict(list)
    asset_counts: Counter[str] = Counter()
    for row in rows:
        by_ticker[row["ticker"]].append(row)
        asset_counts[row["asset"]] += 1
    for ticker_rows in by_ticker.values():
        times = [datetime.fromisoformat(row["observed_at"]) for row in ticker_rows]
        cadence.extend(
            (right - left).total_seconds()
            for left, right in zip(times, times[1:])
            if right > left
        )
        favored = [_number(row["implied_probability_yes"]) >= 0.5 for row in ticker_rows]
        switches += sum(left != right for left, right in zip(favored, favored[1:]))
    depth_count = connection.execute(
        """
        SELECT COUNT(*) FROM orderbook_levels
        WHERE snapshot_id IN (
          SELECT snapshot_id FROM market_snapshots WHERE run_id=?
        )
        """,
        (run_id,),
    ).fetchone()[0]
    portfolio_count = connection.execute(
        "SELECT COUNT(*) FROM portfolio_snapshots WHERE run_id=?", (run_id,)
    ).fetchone()[0]
    return {
        "market_snapshots": len(rows),
        "orderbook_levels": depth_count,
        "portfolio_snapshots": portfolio_count,
        "contracts": len(by_ticker),
        "assets": dict(sorted(asset_counts.items())),
        "median_cadence_seconds": median(cadence) if cadence else None,
        "median_spread": median(spreads) if spreads else None,
        "p90_spread": _percentile(spreads, 0.90),
        "favored_side_switches": switches,
    }


def _main_summary(
    connection: sqlite3.Connection, run: sqlite3.Row
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = _trade_rows(connection, run)
    closed = [row for row in rows if row["pnl"] is not None]
    detail: list[dict[str, Any]] = []
    for row in rows:
        maximum_win_pnl = _number(row["contracts"]) - _number(row["total_debit"])
        pnl = None if row["pnl"] is None else _number(row["pnl"])
        capture = (
            pnl / maximum_win_pnl
            if pnl is not None and pnl > 0 and maximum_win_pnl > 0
            else None
        )
        detail.append(
            {
                "trade_id": row["trade_id"],
                "asset": row["asset"],
                "ticker": row["ticker"],
                "outcome": row["outcome"],
                "status": row["status"],
                "exchange_status": row["exchange_status"],
                "placed_at": row["placed_at"],
                "exited_at": row["exited_at"],
                "contracts": _number(row["contracts"]),
                "entry_price": _number(row["entry_price"]),
                "exit_price": None if row["exit_price"] is None else _number(row["exit_price"]),
                "entry_probability": _number(row["model_probability"]),
                "pnl": pnl,
                "maximum_pnl_if_winner": maximum_win_pnl,
                "positive_exit_capture_ratio": capture,
            }
        )
    pnl_values = [_number(row["pnl"]) for row in closed]
    by_asset: dict[str, dict[str, Any]] = {}
    for asset in sorted({row["asset"] for row in rows}):
        selected = [row for row in closed if row["asset"] == asset]
        by_asset[asset] = {
            "trades": len([row for row in rows if row["asset"] == asset]),
            "closed": len(selected),
            "wins": sum(_number(row["pnl"]) > 0 for row in selected),
            "losses": sum(_number(row["pnl"]) < 0 for row in selected),
            "net_pnl": sum(_number(row["pnl"]) for row in selected),
        }
    return (
        {
            "trades": len(rows),
            "closed": len(closed),
            "open": sum(row["status"] == "OPEN" for row in rows),
            "wins": sum(value > 0 for value in pnl_values),
            "losses": sum(value < 0 for value in pnl_values),
            "net_pnl": sum(pnl_values),
            "win_rate": (
                sum(value > 0 for value in pnl_values) / len(pnl_values)
                if pnl_values
                else None
            ),
            "take_profit_exits": sum(
                row["exchange_status"] == "take_profit" for row in rows
            ),
            "by_asset": by_asset,
        },
        detail,
    )


def _pair_summary(connection: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    if not _table_exists(connection, "pair_arb_observations"):
        return {"scans": 0, "approved": 0, "simulated": 0}
    row = connection.execute(
        """
        SELECT COUNT(*) scans, COALESCE(SUM(approved),0) approved,
               COALESCE(SUM(simulated),0) simulated,
               COALESCE(SUM(CAST(modeled_profit AS REAL)),0) modeled_pnl,
               MIN(CASE WHEN gate NOT IN ('book','stale_leg')
                   THEN CAST(pair_cost AS REAL) END) minimum_guarded_pair_cost,
               MAX(CASE WHEN gate NOT IN ('book','stale_leg')
                   THEN CAST(net_edge AS REAL) END) maximum_guarded_net_edge
        FROM pair_arb_observations WHERE run_id=?
        """,
        (run_id,),
    ).fetchone()
    gates = {
        item["gate"]: item["count"]
        for item in connection.execute(
            """
            SELECT gate, COUNT(*) count FROM pair_arb_observations
            WHERE run_id=? GROUP BY gate ORDER BY gate
            """,
            (run_id,),
        )
    }
    return {**dict(row), "gate_counts": gates}


def _exit_evaluations(
    connection: sqlite3.Connection, run_id: str
) -> dict[str, Any]:
    if not _table_exists(connection, "position_evaluations"):
        return {"evaluations": 0, "gate_counts": {}}
    rows = connection.execute(
        "SELECT * FROM position_evaluations WHERE run_id=? ORDER BY evaluated_at",
        (run_id,),
    ).fetchall()
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(position_evaluations)")
    }
    gates = Counter(row["gate"] for row in rows)
    verdicts = Counter(row["verdict"] for row in rows)
    return {
        "evaluations": len(rows),
        "gate_counts": dict(sorted(gates.items())),
        "verdict_counts": dict(sorted(verdicts.items())),
        "adaptive_fields_populated": (
            sum(
                row["required_gain_per_contract"] is not None
                and row["maximum_settlement_gain_per_contract"] is not None
                for row in rows
            )
            if {
                "required_gain_per_contract",
                "maximum_settlement_gain_per_contract",
            }.issubset(columns)
            else 0
        ),
    }


def _exit_counterfactuals(connection: sqlite3.Connection) -> dict[str, Any]:
    if not _table_exists(connection, "exit_counterfactuals"):
        return {"labeled_exits": 0, "hold_better": 0, "exit_better": 0}
    rows = connection.execute("SELECT * FROM exit_counterfactuals").fetchall()
    deltas = [_number(row["delta_hold_minus_exit"]) for row in rows]
    return {
        "labeled_exits": len(rows),
        "hold_better": sum(delta > 0 for delta in deltas),
        "exit_better": sum(delta < 0 for delta in deltas),
        "same": sum(delta == 0 for delta in deltas),
        "total_delta_hold_minus_exit": sum(deltas),
        "mean_delta_hold_minus_exit": sum(deltas) / len(deltas) if deltas else None,
    }


def _calibration_summary(connection: sqlite3.Connection) -> dict[str, Any]:
    if not _table_exists(connection, "probability_calibrations"):
        return {"labels": 0, "by_asset_time_bucket": []}
    rows = connection.execute(
        """SELECT asset,time_bucket,COUNT(*) labels,
                  AVG(brier_model) brier_model,
                  AVG(brier_raw) brier_raw,
                  AVG(brier_market) brier_market
           FROM probability_calibrations
           GROUP BY asset,time_bucket ORDER BY asset,time_bucket"""
    ).fetchall()
    return {
        "labels": sum(int(row["labels"]) for row in rows),
        "by_asset_time_bucket": [dict(row) for row in rows],
    }
def _lab_summary(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    connection = _connection(path)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        wallets = connection.execute(
            "SELECT * FROM lab_wallets ORDER BY strategy"
        ).fetchall()
        positions = connection.execute(
            "SELECT * FROM lab_positions ORDER BY opened_at, position_id"
        ).fetchall()
        observations = connection.execute(
            "SELECT strategy, action, reason FROM lab_observations"
        ).fetchall()
        position_details = [dict(row) for row in positions]
        strategies: list[dict[str, Any]] = []
        for wallet in wallets:
            selected = [row for row in positions if row["strategy"] == wallet["strategy"]]
            closed = [row for row in selected if row["pnl"] is not None]
            pnl = [_number(row["pnl"]) for row in closed]
            strategies.append(
                {
                    "strategy": wallet["strategy"],
                    "starting_balance": _number(wallet["starting_balance"]),
                    "ending_cash": _number(wallet["cash_balance"]),
                    "positions": len(selected),
                    "closed": len(closed),
                    "open": sum(row["status"] == "OPEN" for row in selected),
                    "wins": sum(value > 0 for value in pnl),
                    "losses": sum(value < 0 for value in pnl),
                    "win_rate": sum(value > 0 for value in pnl) / len(pnl) if pnl else None,
                    "net_pnl": sum(pnl),
                    "wallet_reported_pnl": _number(wallet["realized_pnl"]),
                }
            )
        actions: dict[str, Counter[str]] = defaultdict(Counter)
        reasons: dict[str, Counter[str]] = defaultdict(Counter)
        for row in observations:
            actions[row["strategy"]][row["action"]] += 1
            reasons[row["strategy"]][row["reason"]] += 1
        decision_summary = {
            strategy: {
                "actions": dict(sorted(actions[strategy].items())),
                "top_reasons": reasons[strategy].most_common(10),
            }
            for strategy in sorted(actions)
        }
        terminal_missing_pnl = sum(
            row["status"] != "OPEN" and row["pnl"] is None for row in positions
        )
        open_positions = sum(row["status"] == "OPEN" for row in positions)
        wallet_pnl_mismatches = 0
        closed_wallet_balance_mismatches = 0
        for wallet in wallets:
            selected = [row for row in positions if row["strategy"] == wallet["strategy"]]
            position_pnl = sum(
                _number(row["pnl"]) for row in selected if row["pnl"] is not None
            )
            if abs(position_pnl - _number(wallet["realized_pnl"])) > 0.000001:
                wallet_pnl_mismatches += 1
            if not any(row["status"] == "OPEN" for row in selected):
                expected_cash = _number(wallet["starting_balance"]) + position_pnl
                if abs(expected_cash - _number(wallet["cash_balance"])) > 0.000001:
                    closed_wallet_balance_mismatches += 1
        return strategies, position_details, {
            "integrity": integrity,
            "observations": len(observations),
            "open_positions": open_positions,
            "terminal_positions_missing_pnl": terminal_missing_pnl,
            "wallet_pnl_mismatches": wallet_pnl_mismatches,
            "closed_wallet_balance_mismatches": closed_wallet_balance_mismatches,
            "passed": integrity == "ok"
            and open_positions == 0
            and terminal_missing_pnl == 0
            and wallet_pnl_mismatches == 0
            and closed_wallet_balance_mismatches == 0,
            "decisions": decision_summary,
        }
    finally:
        connection.close()


def _matched_exit_comparison(positions: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = list(positions)
    holds = {
        (row["ticker"], row["outcome"]): row
        for row in rows
        if row["strategy"] == "hold_to_settlement" and row["pnl"] is not None
    }
    output: list[dict[str, Any]] = []
    for strategy in ("fixed_take_profit", "adaptive_take_profit"):
        pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for candidate in rows:
            if candidate["strategy"] != strategy or candidate["pnl"] is None:
                continue
            hold = holds.get((candidate["ticker"], candidate["outcome"]))
            if hold is not None:
                pairs.append((candidate, hold))
        winner_pairs = [pair for pair in pairs if _number(pair[1]["pnl"]) > 0]
        loser_pairs = [pair for pair in pairs if _number(pair[1]["pnl"]) < 0]
        actual = sum(_number(candidate["pnl"]) for candidate, _ in pairs)
        hold_pnl = sum(_number(hold["pnl"]) for _, hold in pairs)
        winner_exit = sum(_number(candidate["pnl"]) for candidate, _ in winner_pairs)
        winner_hold = sum(_number(hold["pnl"]) for _, hold in winner_pairs)
        loser_exit = sum(_number(candidate["pnl"]) for candidate, _ in loser_pairs)
        loser_hold = sum(_number(hold["pnl"]) for _, hold in loser_pairs)
        output.append(
            {
                "strategy": strategy,
                "matched_positions": len(pairs),
                "actual_pnl": actual,
                "hold_counterfactual_pnl": hold_pnl,
                "delta_vs_hold": actual - hold_pnl,
                "winner_markets": len(winner_pairs),
                "winner_upside_capture": winner_exit / winner_hold if winner_hold > 0 else None,
                "winner_pnl": winner_exit,
                "winner_hold_pnl": winner_hold,
                "loser_markets": len(loser_pairs),
                "loser_exit_pnl": loser_exit,
                "loser_hold_pnl": loser_hold,
                "loss_avoided": loser_exit - loser_hold,
            }
        )
    return output


def _decision_summary(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"records": 0, "evaluations": 0, "invalid_lines": 0}
    records = 0
    evaluations = 0
    invalid_lines = 0
    record_types: Counter[str] = Counter()
    decisions: Counter[str] = Counter()
    gates: Counter[str] = Counter()
    candidate_outcomes: Counter[str] = Counter()
    by_asset_gate: dict[str, Counter[str]] = defaultdict(Counter)
    pair_gates: Counter[str] = Counter()
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            invalid_lines += 1
            continue
        records += 1
        record_type = str(payload.get("record_type", "unknown"))
        record_types[record_type] += 1
        weight = max(1, int(payload.get("evaluations", 1)))
        if record_type == "pair_arb_scan":
            pair_gates[str(payload.get("gate", "unknown"))] += weight
            continue
        if record_type != "decision":
            continue
        evaluations += weight
        decision = str(payload.get("decision", "unknown"))
        gate = str(payload.get("blocked_by") or "approved")
        asset = str(payload.get("asset", "unknown"))
        decisions[decision] += weight
        gates[gate] += weight
        by_asset_gate[asset][gate] += weight
        candidate = payload.get("candidate")
        if isinstance(candidate, dict) and candidate.get("outcome") in {"yes", "no"}:
            candidate_outcomes[str(candidate["outcome"])] += weight
    return {
        "records": records,
        "evaluations": evaluations,
        "invalid_lines": invalid_lines,
        "record_type_counts": dict(record_types.most_common()),
        "decision_counts": dict(sorted(decisions.items())),
        "gate_counts": dict(gates.most_common()),
        "candidate_outcomes": dict(sorted(candidate_outcomes.items())),
        "pair_gate_counts": dict(pair_gates.most_common()),
        "by_asset_gate": {
            asset: dict(counts.most_common())
            for asset, counts in sorted(by_asset_gate.items())
        },
    }


def analyze_run(experiment: ExperimentRun) -> dict[str, Any]:
    connection = _connection(experiment.ledger)
    try:
        run = _run_record(connection)
        main, trades = _main_summary(connection, run)
        result = {
            "label": experiment.label,
            "directory": experiment.directory.name,
            "run": dict(run),
            "quality": _quality(connection, run["run_id"]),
            "market": _market_summary(connection, run["run_id"]),
            "main": main,
            "main_trade_details": trades,
            "pair_arb": _pair_summary(connection, run["run_id"]),
            "exit_evaluations": _exit_evaluations(connection, run["run_id"]),
            "exit_counterfactuals": _exit_counterfactuals(connection),
            "probability_calibration": _calibration_summary(connection),
        }
    finally:
        connection.close()
    lab, lab_positions, lab_quality = _lab_summary(experiment.lab)
    result["strategy_lab"] = lab
    result["strategy_lab_quality"] = lab_quality
    result["matched_exit_comparison"] = _matched_exit_comparison(lab_positions)
    result["decisions"] = _decision_summary(experiment.directory / "decisions.jsonl")
    result["quality"]["run_completed"] = run["status"] == "COMPLETED"
    result["quality"]["open_main_positions"] = result["main"]["open"]
    result["quality"]["invalid_decision_lines"] = result["decisions"][
        "invalid_lines"
    ]
    result["quality"]["passed"] = (
        result["quality"]["passed"]
        and result["quality"]["run_completed"]
        and result["quality"]["open_main_positions"] == 0
        and result["quality"]["invalid_decision_lines"] == 0
        and lab_quality["passed"]
    )
    return result


def _aggregate(runs: list[dict[str, Any]]) -> dict[str, Any]:
    strategy_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        for row in run["strategy_lab"]:
            strategy_rows[row["strategy"]].append(row)
    strategies = []
    for strategy, rows in sorted(strategy_rows.items()):
        closed = sum(row["closed"] for row in rows)
        wins = sum(row["wins"] for row in rows)
        losses = sum(row["losses"] for row in rows)
        strategies.append(
            {
                "strategy": strategy,
                "runs_present": len(rows),
                "closed": closed,
                "wins": wins,
                "losses": losses,
                "win_rate": wins / closed if closed else None,
                "net_pnl": sum(row["net_pnl"] for row in rows),
                "pnl_per_closed_position": (
                    sum(row["net_pnl"] for row in rows) / closed if closed else None
                ),
            }
        )
    main_closed = sum(run["main"]["closed"] for run in runs)
    main_wins = sum(run["main"]["wins"] for run in runs)
    return {
        "runs": len(runs),
        "main": {
            "trades": sum(run["main"]["trades"] for run in runs),
            "closed": main_closed,
            "wins": main_wins,
            "losses": sum(run["main"]["losses"] for run in runs),
            "win_rate": main_wins / main_closed if main_closed else None,
            "net_pnl": sum(run["main"]["net_pnl"] for run in runs),
        },
        "strategy_lab": strategies,
        "market_snapshots": sum(run["market"]["market_snapshots"] for run in runs),
        "orderbook_levels": sum(run["market"]["orderbook_levels"] for run in runs),
        "pair_arb_scans": sum(run["pair_arb"]["scans"] for run in runs),
        "pair_arb_approved": sum(run["pair_arb"]["approved"] for run in runs),
    }


def load_model_metrics(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text())
    models = payload.get("models", payload.get("assets", {}))
    output = []
    for asset, model in sorted(models.items()):
        metrics = model.get("metrics", model)
        output.append(
            {
                "asset": asset,
                "auc": metrics.get("test_auc", metrics.get("auc")),
                "baseline_auc": metrics.get(
                    "baseline_test_auc", metrics.get("baseline_auc")
                ),
                "indicator_delta_auc": metrics.get(
                    "indicator_auc_delta", metrics.get("indicator_delta_auc")
                ),
                "deployment_status": model.get("deployment_status", model.get("status")),
                "weight": model.get(
                    "blend_weight", model.get("weight", model.get("model_weight", 0))
                ),
            }
        )
    return output


def analyze_experiment(runs: list[ExperimentRun], model_path: Path) -> dict[str, Any]:
    analyzed = [analyze_run(run) for run in runs]
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "scope": {
            "valid_run_labels": [run.label for run in runs],
            "model_artifact": model_path.name,
            "main_and_lab_results_kept_separate": True,
            "counterfactual_policy": (
                "Only matched isolated-lab ticker/outcome entries are compared; "
                "no hindsight best-quote paths are counted as fills."
            ),
        },
        "runs": analyzed,
        "aggregate": _aggregate(analyzed),
        "models": load_model_metrics(model_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run", action="append", nargs=2, metavar=("LABEL", "DIRECTORY"), required=True
    )
    parser.add_argument(
        "--models", type=Path, default=ROOT / "models" / "commodity_directional.json"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    runs = [ExperimentRun(label, Path(directory)) for label, directory in args.run]
    report = analyze_experiment(runs, args.models)
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
