"""Simple batch launching and safe, human-readable performance reports."""

import json
from types import SimpleNamespace

from autoresearch.cli import frozen_start_batch, main
from autoresearch.presentation import write_html_report


def test_report_contains_performance_decisions_and_escapes_untrusted_prose(tmp_path):
    metrics = {"annualized_return": 0.125, "sharpe": 1.25, "max_drawdown": -0.15}
    result = {
        "train": metrics,
        "stress": {"sharpe": 0.75},
        "score": 1.0,
        "selection_control_score": 0.5,
        "selection_control": "base",
        "failed_gates": [],
    }
    rows = [
        {
            "id": 1,
            "candidate": "base",
            "parent": "",
            "status": "baseline",
            "proposal": "baseline",
            "result": json.dumps(result),
        },
        {
            "id": 2,
            "candidate": "candidate",
            "parent": "base",
            "status": "keep",
            "proposal": json.dumps({"hypothesis": '<script>alert("x")</script>'}),
            "result": json.dumps(result),
        },
    ]
    payload = {
        "experiments": rows,
        "program": "fixture",
        "status": "batch_completed",
        "incumbent": "candidate",
        "llm_summaries": [],
        "batch": {"requested": 1, "completed": 1, "escalated": 1, "experiment_ids": [2]},
    }
    write_html_report(tmp_path, payload)
    html = (tmp_path / "report.html").read_text()
    assert "1 of 1 strategies backtested" in html
    assert "12.50%" in html and "-15.00%" in html and "0.750" in html
    assert "Escalated" in html and "Strategy 1" in html and "Baseline" in html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "LLM review unavailable" in html


def test_start_defaults_to_five_and_view_reuses_saved_location(tmp_path, monkeypatch):
    source = tmp_path / "checkout" / "amat"
    config = source / "configs/autoresearch"
    config.mkdir(parents=True)
    (config / "engineering.json").write_text('{"enabled": true}')
    (config / "llm-chain.example.toml").write_text(
        '[[targets]]\nprovider="gemini"\nmodel="fixture"\n'
    )
    captured = {}

    def initialize(root, *_args, **options):
        root.mkdir(parents=True)
        captured["root"] = root
        captured["options"] = options

    def run_loop(root, **options):
        captured["loop"] = options
        (root / "report.html").write_text("<html>fixture</html>")
        return {
            "status": "batch_completed",
            "batch": {"requested": 5, "completed": 5, "escalated": 2},
        }

    opened = []
    monkeypatch.setattr("autoresearch.cli.initialize", initialize)
    monkeypatch.setattr("autoresearch.cli.run_loop", run_loop)
    # Keep this presentation test in process across the frozen-controller
    # boundary; dedicated controller tests exercise the actual child process.
    monkeypatch.setattr("autoresearch.cli.run_frozen_start", frozen_start_batch)
    monkeypatch.setattr(
        "autoresearch.cli.load_program",
        lambda _root: SimpleNamespace(summary_chain=captured["options"]["summary_chain"]),
    )
    monkeypatch.setattr("autoresearch.cli.webbrowser.open", lambda uri: opened.append(uri) or True)
    assert main(["start", "--source", str(source)]) == 0
    assert captured["loop"]["iterations"] == 5
    assert captured["loop"]["fallback_search"] is True
    assert captured["loop"]["chain"] is not None
    assert captured["options"]["max_experiments"] == 15
    assert captured["options"]["independent_calendar"] is True
    assert captured["options"]["engineering_policy"] == {"enabled": True}
    assert captured["options"]["evaluation"]["robustness"] == "extended"
    assert len(opened) == 1
    assert main(["view", "--source", str(source)]) == 0
    assert opened[0] == opened[1]
    assert main(["view", "--source", str(source), "--no-open"]) == 0
    assert len(opened) == 2
