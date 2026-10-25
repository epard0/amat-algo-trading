"""Plain-language labels, risk levels and pre-configured workflows for every capability.

Presentation metadata only: it never changes what an action does or whether it is
supported. Unknown capabilities fall back to their id so nothing is ever hidden.
"""

from __future__ import annotations

import os

# risk -> (badge text, rank). Higher rank wins when a workflow mixes steps.
RISKS = {
    "read": ("Read-only", 0),
    "writes": ("Writes local files", 1),
    "network": ("Contacts a provider", 2),
    "control": ("Safety control", 3),
    "orders": ("Can place paper orders", 4),
}

# id -> (title, category, risk, one-line summary)
META = {
    "perps.start": (
        "Start remote paper engine",
        "Kalshi perps",
        "orders",
        "Start the VPS Kalshi paper container; simulated positions persist.",
    ),
    "perps.restart": (
        "Restart remote paper engine",
        "Kalshi perps",
        "orders",
        "Restart the VPS runner using its durable paper account.",
    ),
    "vps.executor.start": (
        "Start remote Alpaca executor",
        "Remote executor",
        "orders",
        "Start the VPS Alpaca paper daemon and its native schedule.",
    ),
    "vps.executor.stop": (
        "Stop remote Alpaca executor",
        "Remote executor",
        "control",
        "Stop the VPS paper daemon; accepted broker orders remain.",
    ),
    "vps.executor.restart": (
        "Restart remote Alpaca executor",
        "Remote executor",
        "orders",
        "Restart the VPS Alpaca paper daemon and recover its durable receipts.",
    ),
    "vps.executor.status": (
        "Inspect remote Alpaca executor",
        "Remote executor",
        "read",
        "Read remote paper readiness and container state.",
    ),
    "perps.status": (
        "Inspect paper accounts",
        "Kalshi perps",
        "read",
        "Read strategy state and account performance.",
    ),
    "perps.observe": (
        "Run one paper cycle",
        "Kalshi perps",
        "network",
        "Evaluate one public-market observation; native strategy gates apply.",
    ),
    "perps.stop": (
        "Stop paper accounts",
        "Kalshi perps",
        "control",
        "Request a stop, optionally flattening simulated positions.",
    ),
    "perps.start_comparison": (
        "Start paper comparison",
        "Kalshi perps",
        "orders",
        "Start the supervised local paper comparison service.",
    ),
    "perps.stop_comparison": (
        "Stop paper comparison",
        "Kalshi perps",
        "control",
        "Stop the supervised service, retaining positions.",
    ),
    "executor.stop_submissions": (
        "Stop all submissions",
        "Safety",
        "control",
        "Immediately blocks new orders. Orders the broker already accepted are not cancelled.",
    ),
    "executor.resume_submissions": (
        "Resume submissions",
        "Safety",
        "control",
        "Lets the daemon submit orders again. Reconcile uncertain orders first.",
    ),
    "executor.status": (
        "Executor status",
        "Executor",
        "read",
        "Shows the recorded state of every order.",
    ),
    "executor.health": (
        "Executor health",
        "Executor",
        "read",
        "Checks the daemon heartbeat and recent progress.",
    ),
    "executor.readiness": (
        "Readiness check",
        "Executor",
        "read",
        "Local checks that say whether the executor could trade right now.",
    ),
    "executor.account": (
        "Refresh account",
        "Executor",
        "network",
        "Reads equity and positions from the paper broker.",
    ),
    "executor.check": (
        "Pre-flight check",
        "Executor",
        "network",
        "Contacts the paper broker and verifies the account, clock and plan are ready.",
    ),
    "executor.trades": (
        "Rebuild trade report",
        "Executor",
        "writes",
        "Rebuilds trade-monitor.json from the journal.",
    ),
    "executor.reconcile_submissions": (
        "Reconcile orders",
        "Executor",
        "network",
        "Asks the broker about uncertain orders. Never sends, cancels or replaces one.",
    ),
    "executor.report": (
        "Build daily report",
        "Executor",
        "writes",
        "Records equity, writes reports and may send notifications.",
    ),
    "executor.submit": (
        "Submit orders now",
        "Executor",
        "orders",
        "Runs the submission phase immediately. Normally the daemon does this by itself.",
    ),
    "news.coverage": (
        "News store coverage",
        "News",
        "read",
        "Lists every ingestion run and whether it completed.",
    ),
    "news.knowable": (
        "Browse recent articles",
        "News",
        "read",
        "Shows articles the store could have known about now.",
    ),
    "news.ingest_alpaca": (
        "Fetch latest news",
        "News",
        "network",
        "Downloads articles from Alpaca into the news store.",
    ),
    "news.discover": (
        "Find assets from news",
        "News",
        "writes",
        "Matches headlines to reviewed themes and writes the candidate list. Never sets weights or orders.",
    ),
    "amat.config.validate": (
        "Validate strategy config",
        "Strategy",
        "read",
        "Checks a strategy configuration against the contract.",
    ),
    "amat.config.show": (
        "Show strategy config",
        "Strategy",
        "read",
        "Prints the validated configuration and its digest.",
    ),
    "amat.config.universe": (
        "Show trading universe",
        "Strategy",
        "read",
        "Lists the symbols, clusters and exposure caps.",
    ),
    "amat.scan": (
        "Rank assets by trend",
        "Strategy",
        "read",
        "Ranks symbols by the strategy's own trend strength. Not a forecast.",
    ),
    "amat.backtest.synthetic": (
        "Engine self-test",
        "Research",
        "read",
        "Runs the engine on synthetic data to confirm it works. Not a market result.",
    ),
    "amat.backtest": (
        "Backtest on real history",
        "Research",
        "writes",
        "Runs a strategy version on a frozen snapshot and writes a PDF report.",
    ),
    "amat.walkforward": (
        "Walk-forward test",
        "Research",
        "writes",
        "Slices history into folds to check the strategy holds up over time.",
    ),
    "amat.montecarlo": (
        "Monte Carlo stress test",
        "Research",
        "writes",
        "Reshuffles history to see the range of outcomes.",
    ),
    "amat.research.launch": (
        "Run automated research",
        "Research",
        "writes",
        "Runs built-in research proposals against a frozen snapshot. Can take up to an hour.",
    ),
    "amat.status": (
        "Research status",
        "Research",
        "read",
        "Shows the state of the research workspace.",
    ),
    "amat.active": (
        "Active research run",
        "Research",
        "read",
        "Shows which research run is currently active.",
    ),
}

CATEGORY_ORDER = ["Safety", "Executor", "News", "Strategy", "Research"]

# Pre-configured workflows. Steps use each action's safe defaults; they run in order
# and the run stops at the first step that fails unless it is a read-only check
# routine marked keep_going. None of them places orders.
WORKFLOWS = [
    {
        "id": "morning-check",
        "title": "Morning check",
        "summary": "Is everything healthy before the market opens?",
        "steps": ["executor.status", "executor.health", "news.coverage"],
        "keep_going": True,
    },
    {
        "id": "refresh-news",
        "title": "Refresh news and find assets",
        "summary": "Download the latest articles, then match them to themes.",
        "steps": ["news.ingest_alpaca", "news.discover"],
    },
    {
        "id": "research-strategy",
        "title": "Research a strategy",
        "summary": "Validate the config, backtest it, then test it walk-forward.",
        "steps": ["amat.config.validate", "amat.backtest", "amat.walkforward"],
    },
    {
        "id": "stress-test",
        "title": "Stress test",
        "summary": "Rank assets by trend, then run a Monte Carlo test.",
        "steps": ["amat.scan", "amat.montecarlo"],
    },
    {
        "id": "end-of-day",
        "title": "End-of-day review",
        "summary": "Refresh the account, settle uncertain orders and rebuild the trade report.",
        "steps": ["executor.account", "executor.reconcile_submissions", "executor.trades"],
    },
    {
        "id": "emergency-stop",
        "title": "Emergency stop",
        "summary": "Block all new order submissions right now.",
        "steps": ["executor.stop_submissions"],
    },
]


def decorate(capability: dict) -> dict:
    title, category, risk, summary = META.get(
        capability["id"], (capability["id"], "Other", "read", "")
    )
    if capability["id"].startswith("perps.") and any(
        "configured VPS" in effect for effect in capability.get("effects", [])
    ):
        title = {
            "perps.status": "Inspect remote Kalshi paper engine",
            "perps.stop": "Stop remote Kalshi paper engine",
        }.get(capability["id"], title)
        summary = {
            "perps.status": "Read the VPS paper engine state.",
            "perps.stop": "Stop the VPS paper runner; simulated positions and durable state remain.",
        }.get(capability["id"], summary)
    if os.getenv("TERMINAL_PERPS_TARGET") == "remote" and capability["id"].startswith("executor."):
        title = "Local · " + title
        if category == "Executor":
            category = "Local executor"
    return {
        **capability,
        "title": title,
        "category": category,
        "risk": risk,
        "risk_label": RISKS[risk][0],
        "summary": summary,
    }


def workflows(capabilities: list[dict]) -> list[dict]:
    """Workflows with per-step titles and availability computed from live capabilities."""
    by_id = {c["id"]: c for c in capabilities}
    result = []
    for flow in WORKFLOWS:
        steps = []
        for cap_id in flow["steps"]:
            cap = by_id.get(cap_id)
            steps.append(
                {
                    "capability": cap_id,
                    "title": cap["title"] if cap else cap_id,
                    "risk_label": cap["risk_label"] if cap else "Unavailable",
                    "effects": cap["effects"] if cap else [],
                    "supported": bool(cap and cap["supported"]),
                    "reason": None
                    if cap and cap["supported"]
                    else (cap or {}).get("reason") or "Not available",
                }
            )
        blocked = next((s for s in steps if not s["supported"]), None)
        rank = max(
            (RISKS[(by_id.get(s["capability"]) or {"risk": "read"})["risk"]][1] for s in steps),
            default=0,
        )
        result.append(
            {
                "id": flow["id"],
                "title": flow["title"],
                "summary": flow["summary"],
                "keep_going": flow.get("keep_going", False),
                "steps": steps,
                "risk_label": next(label for label, r in RISKS.values() if r == rank),
                "available": blocked is None,
                "reason": None if blocked is None else f"{blocked['title']}: {blocked['reason']}",
            }
        )
    return result
