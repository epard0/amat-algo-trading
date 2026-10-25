"""Verified research inspection with numeric evidence accompanying every chart."""

from __future__ import annotations

import json
import math
import re
from html import escape
from pathlib import Path
from typing import Any

from autoresearch.contracts import Candidate
from autoresearch.memory import related_experiments
from autoresearch.telemetry import analysis_lock, daily_metrics, read_telemetry
from autoresearch.workspace import digest_file, load_program, write_json


def _svg(series: dict[str, list[float]], label: str, dates: list[str]) -> str:
    colors = ("#5eead4", "#60a5fa", "#fbbf24", "#f472b6", "#c4b5fd")
    low = min(min(values) for values in series.values())
    high = max(max(values) for values in series.values())
    scale = max(high - low, 1e-9)
    lines = []
    for index, (name, values) in enumerate(series.items()):
        color = colors[index % len(colors)]
        points = " ".join(
            f"{65 + 790 * i / max(1, len(values) - 1):.2f},{245 - 205 * (v - low) / scale:.2f}"
            for i, v in enumerate(values)
        )
        lines.append(
            f'<polyline fill="none" stroke="{color}" stroke-width="1.5" points="{points}"/>'
        )
        lines.append(f'<text x="65" y="{295 + index * 20}" fill="{color}">{escape(name)}</text>')
    return (
        f'<svg viewBox="0 0 900 {320 + len(series) * 20}" role="img" aria-label="{escape(label)}">'
        f'<text x="65" y="22" fill="white">{escape(label)}</text>'
        f'<text x="0" y="45" fill="white">{high:.2f}</text>'
        f'<text x="0" y="245" fill="white">{low:.2f}</text>'
        f'<text x="65" y="270" fill="white">{escape(dates[0])}</text>'
        f'<text x="760" y="270" fill="white">{escape(dates[-1])}</text>' + "".join(lines) + "</svg>"
    )


def render_charts(root: Path, candidate_id: str, result: dict[str, Any]) -> dict[str, Any]:
    if re.fullmatch(r"[a-f0-9]{64}", candidate_id) is None:
        raise ValueError("candidate must be a content digest")
    hashes = result.get("artifact_hashes", {})
    if not hashes:
        raise ValueError("charts require verified measurement artifacts")
    for relative, expected in hashes.items():
        path = root / relative
        if (
            not path.resolve().is_relative_to(root)
            or path.is_symlink()
            or digest_file(path) != expected
        ):
            raise ValueError("chart source artifact changed")
    prefix = f"outputs/runs/{candidate_id}/"
    paths = {
        "Candidate": prefix + "primary/daily.json",
        "Candidate at stressed costs": prefix + "stress/daily.json",
    }
    for relative in sorted(hashes):
        if (
            relative.startswith(prefix + "controls/")
            and relative.endswith("/daily.json")
            and "exposure" in relative
        ):
            label = {
                "exposure_candidate": "Exposure-matched candidate",
                "exposure_control": "Exposure-matched baseline",
            }.get(relative.split("/")[-2], relative.split("/")[-2].replace("_", " "))
            paths[label] = relative
    tables, equity, drawdown, exposure = {}, {}, {}, {}
    dates: list[str] = []
    for label, relative in paths.items():
        if relative not in hashes:
            raise ValueError("chart input is not in the measurement manifest")
        rows = json.loads((root / relative).read_text())
        if not daily_metrics(rows)["available"]:
            raise ValueError("charts require at least two measured observations")
        sample = [r["session"] for r in rows]
        if dates and sample != dates:
            raise ValueError("chart comparisons require matched measured dates")
        dates = sample
        values = [float(r["equity"]) for r in rows]
        equity[label] = [100 * (value / values[0]) for value in values]
        peak = values[0]
        underwater = []
        for value in values:
            peak = max(peak, value)
            underwater.append(100 * (value / peak - 1))
        drawdown[label] = underwater
        exposure[label] = [100 * float(r["gross_exposure"]) for r in rows]
        if any(not math.isfinite(value) or value < 0 for value in exposure[label]):
            raise ValueError("invalid exposure in chart source")
        tables[label] = {
            "source": relative,
            "source_sha256": hashes[relative],
            "rows": [
                {
                    "session": day,
                    "equity_index_100": eq,
                    "drawdown_percent": dd,
                    "gross_exposure_percent": ex,
                }
                for day, eq, dd, ex in zip(
                    dates, equity[label], drawdown[label], exposure[label], strict=True
                )
            ],
        }
    target = root / "analysis" / f"charts-{candidate_id}"
    write_json(target.with_suffix(".json"), {"candidate": candidate_id, "series": tables})
    content = (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        "<title>Strategy diagnostics</title><style>body{background:#10202d;color:white;"
        "font:16px system-ui;max-width:1100px;margin:40px auto;padding:20px}"
        "svg{width:100%;margin:20px 0}a{color:#5eead4}</style><h1>Strategy diagnostics</h1>"
        "<p>Adaptive development evidence. Exposure matching is retrospective. "
        "All series share measured dates; equity starts at 100.</p>"
    )
    content += "".join(
        _svg(values, label, dates)
        for label, values in (
            ("Equity index", equity),
            ("Drawdown (%)", drawdown),
            ("Gross exposure (%)", exposure),
        )
    )
    content += (
        f'<p><a href="{target.name}.json">Numeric chart data and source hashes</a> · '
        '<a href="../report.html">Strategy comparison</a></p></html>'
    )
    target.with_suffix(".html").write_text(content)
    return {
        "html": str(target.with_suffix(".html")),
        "data": str(target.with_suffix(".json")),
        "html_sha256": digest_file(target.with_suffix(".html")),
        "data_sha256": digest_file(target.with_suffix(".json")),
    }


def inspect_tool(
    root: Path, operation: str, candidate_id: str | None, query: str = ""
) -> dict[str, Any]:
    root = root.resolve()
    program = load_program(root)
    # Inspection may precede the first backtest. Establish the shared lock
    # without creating an empty experiment ledger or starting a research run.
    (root / "writer.lock").touch(exist_ok=True)
    with analysis_lock(root):
        telemetry = (
            read_telemetry(root)
            if (root / "research.sqlite3").exists()
            else {"state": {}, "experiments": []}
        )
        key = candidate_id or telemetry["state"].get("incumbent") or program.baseline.digest()
        if re.fullmatch(r"[a-f0-9]{64}", key) is None:
            raise ValueError("candidate must be a content digest")
        candidate = (
            program.baseline
            if key == program.baseline.digest()
            else Candidate.model_validate_json((root / "candidates" / f"{key}.json").read_text())
        )
        if candidate.digest() != key:
            raise ValueError("candidate identity mismatch")
        if operation == "related":
            return {"related_experiments": related_experiments(root, program, candidate, query)}
        if operation != "charts":
            raise ValueError("unknown inspection tool")
        row = next(
            (
                r
                for r in reversed(telemetry["experiments"])
                if r["candidate"] == key and r["result"]
            ),
            None,
        )
        if row is None:
            raise ValueError("no completed candidate measurement")
        return render_charts(root, key, json.loads(row["result"]))
