"""Build the canonical portable-report artifact from analyzed simulation ledgers."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any


TITLE = "Kalshi 15-minute strategy and exit-policy analysis"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"${value:+.2f}"


def _source(
    summary: dict[str, Any], summary_path: str, generated_at: str
) -> dict[str, Any]:
    attach = []
    selects = []
    tables = []
    for index, run in enumerate(summary["runs"]):
        ledger_alias = f"ledger_{index}"
        lab_alias = f"lab_{index}"
        directory = run["directory"]
        label = str(run["label"]).replace("'", "''")
        attach.extend(
            [
                f"ATTACH DATABASE 'data/simulations/{directory}/trade_ledger.db' AS {ledger_alias};",
                f"ATTACH DATABASE 'data/simulations/{directory}/strategy_lab.db' AS {lab_alias};",
            ]
        )
        tables.extend(
            [
                f"{ledger_alias}.data_runs",
                f"{ledger_alias}.market_snapshots",
                f"{ledger_alias}.trades",
                f"{ledger_alias}.position_evaluations",
                f"{ledger_alias}.pair_arb_observations",
                f"{lab_alias}.lab_positions",
                f"{lab_alias}.lab_observations",
            ]
        )
        selects.extend(
            [
                (
                    f"SELECT '{label}' AS run_label, 'main_trade' AS record_type, "
                    f"json_object('trade_id', trade_id, 'ticker', ticker, 'asset', asset, "
                    f"'outcome', outcome, 'status', status, 'pnl', pnl) AS record_json "
                    f"FROM {ledger_alias}.trades"
                ),
                (
                    f"SELECT '{label}', 'lab_position', "
                    f"json_object('position_id', position_id, 'strategy', strategy, "
                    f"'ticker', ticker, 'outcome', outcome, 'status', status, 'pnl', pnl) "
                    f"FROM {lab_alias}.lab_positions"
                ),
                (
                    f"SELECT '{label}', 'market_count', "
                    f"json_object('snapshots', COUNT(*), 'contracts', COUNT(DISTINCT ticker)) "
                    f"FROM {ledger_alias}.market_snapshots"
                ),
            ]
        )
    extraction_sql = "\n".join(attach) + "\n" + "\nUNION ALL\n".join(selects) + ";"
    return {
        "id": "simulation_analysis",
        "label": "Isolated simulation ledgers and derived analysis",
        "path": summary_path,
        "query": {
            "engine": "sqlite",
            "language": "sql",
            "sql": extraction_sql,
            "description": (
                "Extracts the raw main trades, strategy-lab positions, and market counts used "
                "by the reproducible Python analysis."
            ),
            "executed_at": generated_at,
            "tables_used": tables,
            "filters": [
                "Only explicitly selected isolated simulation directories are attached.",
                "Known-invalid calibration sessions are excluded before this query is built.",
            ],
            "metric_definitions": [
                "Net P&L is the sum of recorded fee-adjusted closed-position P&L in USD.",
                "Win rate is positive-P&L closed positions divided by all closed positions.",
                "Winner upside capture is exit-policy P&L divided by matched hold-to-settlement P&L where the held side won.",
                "Loss avoided is exit-policy P&L minus matched hold-to-settlement P&L where the held side lost.",
            ],
        },
    }


def _final_exit_rows(final: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in final["matched_exit_comparison"]:
        rows.append(
            {
                "strategy": item["strategy"],
                "matched_positions": item["matched_positions"],
                "actual_pnl": item["actual_pnl"],
                "hold_counterfactual_pnl": item["hold_counterfactual_pnl"],
                "delta_vs_hold": item["delta_vs_hold"],
                "winner_markets": item["winner_markets"],
                "winner_upside_capture": item["winner_upside_capture"],
                "loss_avoided": item["loss_avoided"],
            }
        )
    return rows


def build_artifact(summary: dict[str, Any], summary_path: str) -> dict[str, Any]:
    final = summary["runs"][-1]
    aggregate = summary["aggregate"]
    final_main = final["main"]
    strategies = sorted(final["strategy_lab"], key=lambda row: row["net_pnl"], reverse=True)
    exit_rows = _final_exit_rows(final)
    fixed = next(row for row in exit_rows if row["strategy"] == "fixed_take_profit")
    adaptive = next(row for row in exit_rows if row["strategy"] == "adaptive_take_profit")
    comparable = adaptive["matched_positions"] > 0 and fixed["matched_positions"] > 0
    if comparable:
        exit_finding = (
            f"Adaptive exit captured {_pct(adaptive['winner_upside_capture'])} of matched "
            f"winning-market upside versus {_pct(fixed['winner_upside_capture'])} for the old "
            f"fixed rule. Its matched P&L was {_money(adaptive['actual_pnl'])}, compared with "
            f"{_money(fixed['actual_pnl'])} for fixed exit."
        )
    else:
        exit_finding = (
            "This market window did not produce enough matched adaptive and fixed exits to "
            "estimate an exit-policy effect. The rule remains protected by the exact SILVER "
            "regression test and requires additional independent windows."
        )
    top_eligible = [row for row in strategies if row["closed"] > 0]
    top = top_eligible[0] if top_eligible else None
    top_finding = (
        "No strategy closed a position in the final window."
        if top is None
        else (
            f"{top['strategy']} ranked first in the final isolated window at "
            f"{_money(top['net_pnl'])} across {top['closed']} closed positions "
            f"({_pct(top['win_rate'])} win rate). This is a descriptive ranking, not a "
            "promotion decision."
        )
    )
    generated_at = summary["generated_at"]
    source = _source(summary, summary_path, generated_at)
    run_rows = []
    for run in summary["runs"]:
        run_rows.append(
            {
                "run": run["label"],
                "status": run["run"]["status"],
                "snapshots": run["market"]["market_snapshots"],
                "depth_rows": run["market"]["orderbook_levels"],
                "main_closed": run["main"]["closed"],
                "main_win_rate": run["main"]["win_rate"],
                "main_pnl": run["main"]["net_pnl"],
                "pair_arb_approved": run["pair_arb"]["approved"],
                "quality_passed": run["quality"]["passed"],
            }
        )
    headline = [
        {
            "final_main_pnl": final_main["net_pnl"],
            "final_main_win_rate": final_main["win_rate"] or 0,
            "final_main_closed": final_main["closed"],
            "reviewed_snapshots": aggregate["market_snapshots"],
            "reviewed_depth_rows": aggregate["orderbook_levels"],
            "pair_arb_approved": aggregate["pair_arb_approved"],
        }
    ]
    models = [
        {**row, "threshold": 0.55, "random_baseline": 0.50}
        for row in sorted(summary["models"], key=lambda row: row["auc"], reverse=True)
    ]
    blocks = [
        {"id": "title", "type": "markdown", "body": f"# {TITLE}"},
        {
            "id": "technical_summary",
            "type": "markdown",
            "sourceId": "simulation_analysis",
            "body": (
                "## Technical summary\n\n"
                f"The final paper wallet closed **{final_main['closed']}** trades at "
                f"**{_pct(final_main['win_rate'])}** win rate and "
                f"**{_money(final_main['net_pnl'])}** net P&L. Across the selected valid "
                f"runs, the analysis reviewed **{aggregate['market_snapshots']:,}** market "
                f"snapshots and **{aggregate['orderbook_levels']:,}** depth rows. "
                "The strongest evidence is the same-window isolated-wallet comparison; "
                "cross-run results are descriptive because market windows and configuration "
                "hashes differ."
            ),
        },
        {
            "id": "strategy_finding",
            "type": "markdown",
            "sourceId": "simulation_analysis",
            "body": f"## Strategy performance\n\n{top_finding}",
        },
        {"id": "strategy_chart_block", "type": "chart", "chartId": "strategy_pnl"},
        {
            "id": "exit_finding",
            "type": "markdown",
            "sourceId": "simulation_analysis",
            "body": (
                "## Exit-policy effectiveness\n\n"
                f"{exit_finding} Winning-market upside and losing-market protection are "
                "reported separately because a high exit win rate can still have negative "
                "expectancy."
            ),
        },
        {"id": "exit_chart_block", "type": "chart", "chartId": "exit_capture"},
        {"id": "exit_table_block", "type": "table", "tableId": "exit_comparison"},
        {
            "id": "scope_methods",
            "type": "markdown",
            "body": (
                "## Scope, data, and methodology\n\n"
                "The source is strict schema-versioned SQLite telemetry from isolated paper "
                "sessions. Quotes use executable top-of-book prices and recorded depth; P&L "
                "includes the configured Kalshi fee model. Main-paper and strategy-lab trades "
                "are never pooled at the row level. The exit comparison matches only the same "
                "session, ticker, and outcome against hold-to-settlement. Model validation uses "
                "chronological train/test splits on 1-minute OHLCV with a 15-row purge."
            ),
        },
        {"id": "run_table_block", "type": "table", "tableId": "run_quality"},
        {
            "id": "model_finding",
            "type": "markdown",
            "sourceId": "simulation_analysis",
            "body": (
                "## Model diagnostics\n\n"
                "Only HYPE cleared the 0.55 AUC deployment threshold and improved on its "
                "indicator-free baseline, so the other asset models remain shadow-only with "
                "zero blend weight. Near-random AUC is not evidence for increasing stake size."
            ),
        },
        {"id": "model_chart_block", "type": "chart", "chartId": "model_auc"},
        {
            "id": "limitations",
            "type": "markdown",
            "body": (
                "## Limitations, uncertainty, and robustness\n\n"
                "The sample is small relative to the number of strategies and assets. Strategy "
                "signals overlap, closed-position counts vary, and multiple comparisons make "
                "the best observed wallet vulnerable to selection bias. Cross-run comparisons "
                "are confounded by different markets and configuration hashes. Paper fills do "
                "not fully capture maker queue position, network latency, partial-fill legging, "
                "or live adverse selection. No confidence interval is reported where the sample "
                "is too sparse to support one."
            ),
        },
        {
            "id": "recommendations",
            "type": "markdown",
            "body": (
                "## Recommendations\n\n"
                "1. Keep the adaptive take-profit rule enabled and retain the old fixed exit as "
                "a shadow control. Do not promote a directional lab strategy until it has "
                "positive fee-adjusted expectancy across several independent windows.\n"
                "2. Keep the $10 per-trade cap and $100 paper wallet. Raising size would scale "
                "both edge and drawdown, while current model discrimination and sample size do "
                "not justify more exposure.\n"
                "3. Continue the complementary-pair scanner in shadow mode. An arb requires both "
                "executable asks, fees, and matched depth to leave pair cost below $1; no "
                "approved opportunity should be inferred from bid or midpoint gaps.\n"
                "4. Promote based on expectancy, maximum drawdown, downside tail, calibration, "
                "and fill quality—not win rate or P&L alone."
            ),
        },
        {
            "id": "further_questions",
            "type": "markdown",
            "body": (
                "## Further questions\n\n"
                "Would adaptive exit remain superior over at least 100 matched entries and three "
                "market regimes? Which refusal gates remove negative-expectancy trades without "
                "suppressing valid complement-side entries? Does an asset-specific calibration "
                "layer improve probability reliability after fees? Can maker fill probability be "
                "estimated from queue-aware replay before enabling any live arb execution?"
            ),
        },
    ]
    concise_exit = (
        f"adaptive capture {_pct(adaptive['winner_upside_capture'])} versus fixed "
        f"{_pct(fixed['winner_upside_capture'])}"
        if comparable
        else "the matched exit sample was insufficient"
    )
    # The packaged reader currently overflows at its mobile breakpoint when a
    # report contains several long narrative cards. Keep the durable HTML
    # synopsis compact; the executed notebook and summary JSON carry the full
    # methods, limitations, diagnostics, and row-level tables.
    blocks = [
        blocks[0],
        {
            "id": "technical_assessment",
            "type": "markdown",
            "sourceId": "simulation_analysis",
            "body": (
                "## Technical assessment\n\n"
                f"**Summary:** {final_main['closed']} final trades, "
                f"{_pct(final_main['win_rate'])} wins, {_money(final_main['net_pnl'])}; "
                f"{concise_exit}. **Method:** strict fee-aware ledgers and matched exits. "
                "**Limit:** small paper sample. **Action:** keep adaptive exit and the $10 cap; "
                "require independent replication. **Question:** does this persist over 100 matches?"
            ),
        },
        {"id": "strategy_chart_block", "type": "chart", "chartId": "strategy_pnl"},
    ]
    cards: list[dict[str, Any]] = []
    charts = [
        {
            "id": "strategy_pnl",
            "title": "Final-run strategy-lab net P&L",
            "subtitle": "Wallets are isolated; counts and win rates remain available in tooltips.",
            "type": "horizontalBar",
            "dataset": "final_strategies",
            "sourceId": "simulation_analysis",
            "encodings": {
                "x": {"field": "strategy", "type": "nominal", "label": "Strategy"},
                "y": {"field": "net_pnl", "type": "quantitative", "format": "currency", "label": "Net P&L"},
                "tooltip": [
                    {"field": "closed", "type": "quantitative", "label": "Closed positions"},
                    {"field": "win_rate", "type": "quantitative", "format": "percent", "label": "Win rate"},
                ],
            },
            "referenceLines": [{"value": 0, "label": "$0"}],
            "maxRows": 20,
        },
        {
            "id": "exit_capture",
            "title": "Matched winning-market upside capture by exit policy",
            "subtitle": "Only same-run ticker/outcome matches against hold-to-settlement are eligible.",
            "type": "bar",
            "dataset": "exit_comparison",
            "sourceId": "simulation_analysis",
            "encodings": {
                "x": {"field": "strategy", "type": "nominal", "label": "Exit policy"},
                "y": {"field": "winner_upside_capture", "type": "quantitative", "format": "percent", "label": "Upside captured"},
                "tooltip": [
                    {"field": "matched_positions", "type": "quantitative", "label": "Matched positions"},
                    {"field": "actual_pnl", "type": "quantitative", "format": "currency", "label": "Actual P&L"},
                    {"field": "loss_avoided", "type": "quantitative", "format": "currency", "label": "Loss avoided"},
                ],
            },
            "referenceLines": [{"value": 0.75, "label": "75% target"}],
            "emptyState": "No matched winning markets were available in this window.",
        },
        {
            "id": "model_auc",
            "title": "Chronological out-of-sample AUC by asset",
            "subtitle": "Only models at or above 0.55 with non-negative indicator lift are deployable.",
            "type": "horizontalBar",
            "dataset": "models",
            "sourceId": "simulation_analysis",
            "encodings": {
                "x": {"field": "asset", "type": "nominal", "label": "Asset"},
                "y": {"field": "auc", "type": "quantitative", "format": "number", "label": "ROC AUC"},
                "tooltip": [
                    {"field": "baseline_auc", "type": "quantitative", "label": "Indicator-free AUC"},
                    {"field": "indicator_delta_auc", "type": "quantitative", "label": "Indicator lift"},
                    {"field": "deployment_status", "type": "nominal", "label": "Status"},
                    {"field": "weight", "type": "quantitative", "label": "Blend weight"},
                ],
            },
            "referenceLines": [
                {"value": 0.50, "label": "Random"},
                {"value": 0.55, "label": "Deploy threshold"},
            ],
        },
    ]
    tables = [
        {
            "id": "exit_comparison",
            "title": "Matched exit-policy details",
            "subtitle": "P&L and counterfactuals are fee-adjusted within the isolated lab.",
            "dataset": "exit_comparison",
            "sourceId": "simulation_analysis",
            "defaultSort": {"field": "actual_pnl", "direction": "desc"},
            "columns": [
                {"field": "strategy", "label": "Exit policy", "type": "text"},
                {"field": "matched_positions", "label": "Matched", "type": "number"},
                {"field": "actual_pnl", "label": "Actual P&L", "type": "currency", "format": "currency", "movement": True},
                {"field": "hold_counterfactual_pnl", "label": "Hold P&L", "type": "currency", "format": "currency", "movement": True},
                {"field": "winner_upside_capture", "label": "Winner capture", "type": "percent", "format": "percent"},
                {"field": "loss_avoided", "label": "Loss avoided", "type": "currency", "format": "currency", "movement": True},
            ],
        },
        {
            "id": "run_quality",
            "title": "Run scope, provenance, and quality",
            "dataset": "runs",
            "sourceId": "simulation_analysis",
            "defaultSort": {"field": "run", "direction": "asc"},
            "columns": [
                {"field": "run", "label": "Run", "type": "text"},
                {"field": "status", "label": "Status", "type": "text"},
                {"field": "snapshots", "label": "Snapshots", "type": "number", "format": "compact"},
                {"field": "depth_rows", "label": "Depth rows", "type": "number", "format": "compact"},
                {"field": "main_closed", "label": "Main closed", "type": "number"},
                {"field": "main_win_rate", "label": "Main win rate", "type": "percent", "format": "percent"},
                {"field": "main_pnl", "label": "Main P&L", "type": "currency", "format": "currency", "movement": True},
                {"field": "pair_arb_approved", "label": "Approved arbs", "type": "number"},
                {"field": "quality_passed", "label": "Quality passed", "type": "text"},
            ],
        },
    ]
    return {
        "surface": "report",
        "manifest": {
            "version": 1,
            "surface": "report",
            "title": TITLE,
            "generatedAt": generated_at,
            "description": "Technical evaluation of isolated Kalshi 15-minute simulation sessions.",
            "cards": cards,
            "charts": charts,
            "tables": tables,
            "sources": [source],
            "blocks": blocks,
        },
        "snapshot": {
            "version": 1,
            "generatedAt": generated_at,
            "status": "ready",
            "datasets": {
                "headline": headline,
                "final_strategies": strategies,
                "exit_comparison": exit_rows,
                "runs": run_rows,
                "models": models,
            },
        },
        "sources": [source],
        "package_info": {},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--source-path",
        default="reports/20260824_simulation_analysis/analysis_summary.json",
    )
    args = parser.parse_args()
    summary = json.loads(args.summary.read_text())
    artifact = build_artifact(summary, args.source_path)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2) + "\n")
    print(f"artifact: {args.output} ({datetime.now().astimezone().isoformat()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
