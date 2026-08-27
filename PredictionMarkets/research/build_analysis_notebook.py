"""Build and execute the reproducible simulation-analysis notebook."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import nbformat
from nbclient import NotebookClient


def _markdown(text: str) -> nbformat.NotebookNode:
    return nbformat.v4.new_markdown_cell(text)


def _code(text: str) -> nbformat.NotebookNode:
    return nbformat.v4.new_code_cell(text)


def build_notebook(summary_path: Path) -> nbformat.NotebookNode:
    summary = json.loads(summary_path.read_text())
    run_specs = [
        (run["label"], f"data/simulations/{run['directory']}") for run in summary["runs"]
    ]
    final = summary["runs"][-1]
    final_main = final["main"]
    adaptive = next(
        (
            row
            for row in final["matched_exit_comparison"]
            if row["strategy"] == "adaptive_take_profit"
        ),
        None,
    )
    fixed = next(
        (
            row
            for row in final["matched_exit_comparison"]
            if row["strategy"] == "fixed_take_profit"
        ),
        None,
    )
    headline = (
        f"The final main paper wallet closed {final_main['closed']} trades at "
        f"{(final_main['win_rate'] or 0):.1%} win rate and "
        f"${final_main['net_pnl']:+.2f} net P&L."
    )
    exit_text = (
        "The matched exit comparison was not estimable in this window."
        if not adaptive or not adaptive["matched_positions"]
        else (
            f"Across {adaptive['matched_positions']} matched lab markets, adaptive exit "
            f"captured {(adaptive['winner_upside_capture'] or 0):.1%} of winning-market "
            f"hold upside; fixed exit captured "
            f"{((fixed or {}).get('winner_upside_capture') or 0):.1%}."
        )
    )
    notebook = nbformat.v4.new_notebook()
    notebook["metadata"] = {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3"},
    }
    notebook["cells"] = [
        _markdown(
            "# Kalshi 15-minute strategy experiment\n\n"
            "Executed companion analysis for the isolated paper ledgers and strategy-lab "
            "wallets. Results are descriptive, fee-aware, and do not treat hindsight quotes "
            "as achievable fills."
        ),
        _markdown(f"## TL;DR\n\n- {headline}\n- {exit_text}"),
        _code(
            "from pathlib import Path\n"
            "import json, sys\n"
            "import pandas as pd\n\n"
            "ROOT = Path.cwd().resolve()\n"
            "if not (ROOT / 'research' / 'final_experiment_analysis.py').exists():\n"
            "    raise RuntimeError('Execute this notebook from the repository root')\n"
            "sys.path.insert(0, str(ROOT))\n"
            "from research.final_experiment_analysis import (\n"
            "    ExperimentRun, analyze_experiment\n"
            ")\n"
            f"RUN_SPECS = {run_specs!r}\n"
            "runs = [ExperimentRun(label, ROOT / directory) for label, directory in RUN_SPECS]\n"
            "analysis = analyze_experiment(runs, ROOT / 'models/commodity_directional.json')\n"
            f"saved = json.loads((ROOT / {str(summary_path)!r}).read_text())\n"
            "assert analysis['aggregate'] == saved['aggregate']\n"
            "print('Independent recomputation matches saved aggregate metrics.')"
        ),
        _markdown(
            "## Context and methods\n\n"
            "Main-paper trades are assigned to a run by its recorded UTC start/end bounds. "
            "Strategy-lab wallets are isolated SQLite files. Fixed and adaptive exits are "
            "compared only when a hold-to-settlement wallet entered the same ticker and side "
            "in the same session. Winning-market upside capture and losing-market loss "
            "avoidance are reported separately."
        ),
        _code(
            "run_rows = []\n"
            "for run in analysis['runs']:\n"
            "    run_rows.append({\n"
            "        'run': run['label'], 'status': run['run']['status'],\n"
            "        'snapshots': run['market']['market_snapshots'],\n"
            "        'depth_rows': run['market']['orderbook_levels'],\n"
            "        'main_trades': run['main']['closed'],\n"
            "        'main_win_rate': run['main']['win_rate'],\n"
            "        'main_pnl': run['main']['net_pnl'],\n"
            "        'quality_passed': run['quality']['passed'],\n"
            "        'model_hash': run['run']['model_hash'],\n"
            "        'config_hash': run['run']['config_hash'],\n"
            "    })\n"
            "pd.DataFrame(run_rows)"
        ),
        _markdown("## Decision funnel and refusal metadata"),
        _code(
            "gate_rows = []\n"
            "for run in analysis['runs']:\n"
            "    total = run['decisions']['evaluations']\n"
            "    for gate, count in run['decisions'].get('gate_counts', {}).items():\n"
            "        gate_rows.append({\n"
            "            'run': run['label'], 'gate': gate, 'evaluations': count,\n"
            "            'share': count / total if total else None,\n"
            "            'candidate_yes': run['decisions'].get('candidate_outcomes', {}).get('yes', 0),\n"
            "            'candidate_no': run['decisions'].get('candidate_outcomes', {}).get('no', 0),\n"
            "        })\n"
            "pd.DataFrame(gate_rows).sort_values(['run', 'evaluations'], ascending=[True, False])"
        ),
        _markdown("## Strategy results"),
        _code(
            "strategy = pd.DataFrame(analysis['aggregate']['strategy_lab'])\n"
            "strategy.sort_values(['net_pnl', 'closed'], ascending=[False, False])"
        ),
        _markdown("## Matched exit-policy comparison"),
        _code(
            "exit_rows = []\n"
            "for run in analysis['runs']:\n"
            "    for row in run['matched_exit_comparison']:\n"
            "        exit_rows.append({'run': run['label'], **row})\n"
            "pd.DataFrame(exit_rows)"
        ),
        _markdown("## Main-paper trade decisions"),
        _code(
            "detail = []\n"
            "for run in analysis['runs']:\n"
            "    detail.extend({'run': run['label'], **row} for row in run['main_trade_details'])\n"
            "pd.DataFrame(detail)"
        ),
        _markdown("## Model diagnostics"),
        _code(
            "models = pd.DataFrame(analysis['models'])\n"
            "models.sort_values('auc', ascending=False)"
        ),
        _markdown(
            "## Limitations and takeaways\n\n"
            "- The runs cover different market windows and configuration hashes; cross-run "
            "differences are descriptive, not causal. The same-window strategy-lab comparison "
            "is the strongest available evidence.\n"
            "- A high win rate can coexist with negative P&L when losses dominate many small "
            "wins. Promotion therefore requires positive fee-adjusted expectancy, adequate "
            "sample size, and stable results across independent windows.\n"
            "- Model AUCs near 0.50 imply weak ranking power. Only artifacts that cleared the "
            "deployment threshold were allowed a non-zero blend weight.\n"
            "- No result guarantees live profitability; fill latency, queue position, and "
            "partial fills remain material execution risks."
        ),
    ]
    return notebook


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    notebook = build_notebook(args.summary)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    nbformat.write(notebook, args.output)
    client = NotebookClient(notebook, timeout=600, kernel_name="python3")
    client.execute(cwd=str(Path.cwd()))
    nbformat.write(notebook, args.output)
    print(f"executed notebook: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
