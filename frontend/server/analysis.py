"""Read-only chart projections from registered, source-produced research evidence."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from pathlib import Path

import polars as pl

PERCENTILE_SIZING_ARMS = {
    "core_daily": "Current best · all groups + volatility switching",
    "p80_reduce": "Own 80th percentile · reduce only",
    "p90_reduce": "Own 90th percentile · reduce only",
    "p95_reduce": "Own 95th percentile · reduce only",
    "p80_relative": "Own 80th percentile · relative sizing",
    "p90_relative": "Own 90th percentile · relative sizing",
    "p95_relative": "Own 95th percentile · relative sizing",
}

ASSET_SIZING_ARMS = {
    "core_daily": "Current best · all groups + volatility switching",
    "weak_reduce": "Asset weak trend · reduce only",
    "vol_reduce": "Asset volatility stress · reduce only",
    "either_reduce": "Asset weak trend or volatility stress · reduce only",
    "weak_relative": "Asset weak trend · relative sizing",
    "vol_relative": "Asset volatility stress · relative sizing",
    "either_relative": "Asset weak trend or volatility stress · relative sizing",
}

GROUP_SIZING_ARMS = {
    "core_daily": "Current best · all groups + volatility switching",
    "weak_trend": "Group sizing · own weak trend",
    "volatility": "Group sizing · own volatility stress",
    "either": "Group sizing · own weak trend or volatility stress",
}

SIZING_ARMS = {
    "core_daily": "Current best · all groups + volatility switching",
    "weak_trend": "Inverse-volatility only below QQQ 200-day average",
    "volatility": "Inverse-volatility only during QQQ volatility stress",
    "either": "Inverse-volatility during weak trend or volatility stress",
}

NEXT_ARMS = {
    "core_daily": "Current best · all groups + volatility switching",
    "wider_band": "Wider rebalance band · 3%",
    "confirm_two": "Two-day replacement confirmation",
    "larger_hurdle": "Cost hurdle · 1.5 times round-trip cost",
    "concentration_cap": "Lower single-asset cap · 40%",
    "inverse_volatility": "Inverse-volatility allocation",
}

ARMS = {
    "baseline": "Current reference",
    "B1": "Daily asset selection",
    "A1": "Lower metals exposure",
    "A3": "Volatility-aware allocation",
    "C2": "Shorter trends",
    "A2": "Smaller individual positions",
    "B2": "More frequent rebalancing",
    "C1": "Medium-length trends",
    "D1": "Cash reserve and proportional buys",
}
ADAPTIVE_ARMS = {
    "baseline": "v13 reference",
    "B1": "Daily selection",
    "groups": "Group selection only",
    "residual": "Group-adjusted ranking only",
    "regime": "Slow/fast price-risk only",
    "hysteresis": "Turnover buffer only",
    "combined": "Adaptive rotation combined",
    "structure": "Existing trend-quality filter",
    "allocation": "Existing volatility-aware allocation",
    "daily_buffer": "Daily selection + turnover buffer",
}
GROUP_ARMS = {
    "baseline": "v13 · core ETFs",
    "B1": "Daily selection · core ETFs",
    "groups": "Groups · core ETFs",
    "core_daily": "Groups + daily · core ETFs",
    "core_quality": "Groups + quality · core ETFs",
    "core_both": "Groups + daily + quality · core ETFs",
    "wide_baseline": "v13-style · broader ETFs",
    "wide_daily_baseline": "Daily selection · broader ETFs",
    "wide_groups": "Groups · broader ETFs",
    "wide_daily": "Groups + daily · broader ETFs",
    "wide_quality": "Groups + quality · broader ETFs",
    "wide_both": "Groups + daily + quality · broader ETFs",
}
INTERACTION_ARMS = {
    "core_daily": "Daily core groups · original reference",
    "all_groups": "All groups · parent reference",
    "conditional_reference": "Conditional switching · parent reference",
    "all_volatility": "All groups + volatility cost hurdle",
    "all_combined": "All groups + combined-condition hurdle",
    "all_combined_double": "All groups + double combined hurdle",
    "all_always": "All groups + always-on cost hurdle",
    "all_always_double": "All groups + always-on double hurdle",
}
RECOVERY_ARMS = {
    "core_daily": "Daily core groups · reference",
    "faster6": "Group ranking · six completed weeks",
    "faster4": "Group ranking · four completed weeks",
    "broad_benchmark": "Group ranking · SPY reference",
    "all_groups": "Allow all three groups",
    "faster6_spy": "Six-week groups + SPY reference",
    "all_groups_spy": "All groups + SPY reference",
}
QUALITY_RECOVERY_ARMS = {
    "core_daily": "Daily core groups · reference",
    "agreement3": "Require all three trend horizons",
    "shorter_trend": "Trend horizons · 21/63/126 days",
    "agreement3_shorter": "Shorter trends + full agreement",
}
INDEX_ARMS = {
    "core_daily": "Daily core groups · default reference",
    "conditional_reference": "Retained conditional switching reference",
    "qqq_trend": "Core + QQQ directional trend",
    "spy_trend": "Core + SPY slow trend",
    "iwm_breakout": "Core + IWM breakout",
    "index_basket": "Core + tailored index basket",
    "qqq_pullback": "Core + QQQ pullback/bounce",
    "relative_pair": "Core + QQQ/SPY relative momentum",
    "qqq_long_only": "Core + QQQ long-only control",
    "basket_long_only": "Core + index long-only control",
}
OPPORTUNISTIC_ARMS = {**INDEX_ARMS, "matched_control": "Core in netted ledger · execution control"}
OPPORTUNISTIC_ARMS.update(
    {
        k: v.replace("Core + ", "Unused cash · ")
        for k, v in INDEX_ARMS.items()
        if k not in {"core_daily", "conditional_reference"}
    }
)
CONDITIONAL_ARMS = {
    "core_daily": "Daily core groups · default reference",
    "volatility": "Cost hurdle during high volatility",
    "liquidity": "Cost hurdle during thin liquidity",
    "churn": "Cost hurdle during rank churn",
    "inefficient": "Cost hurdle during choppy trends",
    "vol_or_liquidity": "Volatility or thin liquidity",
    "vol_and_liquidity": "Volatility and thin liquidity",
    "combined": "Volatility, liquidity or rank churn",
    "combined_double": "Combined conditions · double hurdle",
}
SWITCHING_ARMS = {
    "core_daily": "Daily core groups · current best",
    "confirm2": "Confirm rank for two days",
    "confirm3": "Confirm rank for three days",
    "cost1": "Switch only above round-trip cost",
    "cost2": "Switch above twice round-trip cost",
    "confirm2_cost1": "Two days + cost hurdle",
    "confirm3_cost1": "Three days + cost hurdle",
    "confirm2_cost2": "Two days + double cost hurdle",
    "confirm3_cost2": "Three days + double cost hurdle",
}
EFFICIENCY_ARMS = {
    "core_daily": "Daily core groups · current best",
    "band3": "Trade beyond 3% drift",
    "band4": "Trade beyond 4% drift",
    "band5": "Trade beyond 5% drift",
    "group_trend": "Group weakness filter",
    "group_shock": "Group shock filter",
    "group_stress": "Group volatility filter",
    "band3_trend": "3% drift + group weakness",
    "band4_trend": "4% drift + group weakness",
}
VALIDATION_ARMS = {
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
VALIDATION_PERIODS = {
    "walk_forward": ("2020-01-01", "2026-12-31"),
    **{str(y): (f"{y}-01-01", f"{y}-12-31") for y in range(2017, 2027)},
    "stress_2018": ("2018-10-01", "2018-12-31"),
    "stress_covid": ("2020-02-19", "2020-04-30"),
    "stress_2022": ("2022-01-01", "2022-12-31"),
}
CAMPAIGN_ROOT = "amat/outputs/research-campaigns/2026-10-19-12h-performance"

STUDIES = {
    "campaign": CAMPAIGN_ROOT,
    "percentile_sizing": "amat/outputs/analysis/2026-10-19-percentile-sizing/study",
    "asset_sizing": "amat/outputs/analysis/2026-10-19-asset-sizing/study",
    "group_sizing": "amat/outputs/analysis/2026-10-19-group-sizing/study",
    "conditional_sizing": "amat/outputs/analysis/2026-10-19-conditional-sizing-v2/study",
    "volatility_next": "amat/outputs/analysis/2026-10-19-volatility-next/study",
    "interaction": "amat/outputs/analysis/2026-10-19-all-groups-switching/study",
    "opportunistic": "amat/outputs/analysis/2026-10-19-opportunistic-netted-sleeves/study",
    "recovery": "amat/outputs/analysis/2026-10-19-native-group-recovery/study",
    "quality_recovery": "amat/outputs/analysis/2026-10-19-native-quality-recovery/study",
    "indices": "amat/outputs/analysis/2026-10-20-index-sleeves-v2/study",
    "conditional": "amat/outputs/analysis/2026-10-20-conditional-switching/study",
    "switching": "amat/outputs/analysis/2026-10-19-allocation-switching/study",
    "efficiency": "amat/outputs/analysis/2026-10-19-group-efficiency/study",
    "validation": "amat/outputs/analysis/2026-10-19-daily-group-validation/study",
    "reference": "amat/outputs/analysis/2026-10-14-strategy-implementation/daily-study",
    "adaptive": "amat/outputs/analysis/2026-10-19-adaptive-rotation/study",
    "groups": "amat/outputs/analysis/2026-10-19-group-selection-expansion/study",
}
PERIODS = {
    "2026_ytd": "2026 to date",
    "2025": "2025",
    "2024": "2024",
    "recent": "July–October 2026",
    "full": "Full available history",
    **{str(y): str(y) for y in range(2017, 2027)},
    "walk_forward": "Walk-forward comparison · 2020 onward",
    "stress_2018": "2018 fourth-quarter selloff",
    "stress_covid": "COVID crash and initial rebound",
    "stress_2022": "2022 bear market",
}


def _read(path: Path):
    if path.is_symlink() or path.stat().st_size > 8_000_000:
        raise ValueError("Research evidence is not a readable registered file")
    return json.loads(path.read_text())


def _record(path: Path):
    value = _read(path)
    expected = path.with_suffix(".sha256").read_text().strip()
    actual = hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()
    if actual != expected:
        raise ValueError("Research evidence hash changed")
    return value


def _slice(record, period):
    data = record["sessions"]
    selected = []
    for index, row in enumerate(data):
        session = row["session"]
        include = period == "full" or (period in {"2024", "2025"} and session[:4] == period)
        include |= period == "2026_ytd" and session >= "2026-01-01"
        include |= period == "recent" and session >= "2026-07-13"
        if period in VALIDATION_PERIODS:
            start, end = VALIDATION_PERIODS[period]
            include = start <= session <= end
        if include:
            selected.append((index, row))
    if not selected:
        return [], None
    first = selected[0][0]
    opening = data[first - 1] if first else {"session": data[0]["session"], "equity": 100000}
    rows = [opening, *[r for _, r in selected if r["session"] != opening["session"]]]
    return rows, float(opening["equity"])


def _curve(rows, opening):
    peak = opening
    points = []
    for row in rows:
        equity = float(row["equity"])
        if not math.isfinite(equity) or equity <= 0:
            raise ValueError("Invalid measured equity path")
        peak = max(peak, equity)
        points.append(
            {
                "date": row["session"],
                "growth": (equity / opening - 1) * 100,
                "drawdown": (equity / peak - 1) * 100,
            }
        )
    return points


def _monthly_detail(record, period):
    rows, opening = _slice(record, period)
    months = {}
    previous = opening
    for row in rows[1:]:
        month = row["session"][:7]
        value = months.setdefault(
            month,
            {
                "month": month,
                "opening": previous,
                "peak": previous,
                "max_drawdown": 0.0,
                "costs": 0.0,
                "exposure": [],
                "start": row["session"],
                "end": row["session"],
            },
        )
        equity = float(row["equity"])
        value["peak"] = max(value["peak"], equity)
        value["max_drawdown"] = min(value["max_drawdown"], equity / value["peak"] - 1)
        value["net_return"] = equity / value["opening"] - 1
        value["costs"] += float(row.get("fees", 0))
        value["exposure"].append(float(row.get("exposure", 0)))
        value["end"] = row["session"]
        previous = equity
    return [
        {
            **{k: v for k, v in m.items() if k not in {"peak", "opening", "exposure"}},
            "average_exposure": sum(m["exposure"]) / len(m["exposure"]),
        }
        for m in months.values()
    ]


def _campaign_study(root: Path):
    publication = _record(root / CAMPAIGN_ROOT / "latest-study.json")
    relative = Path(publication["study_relative_path"])
    if relative.is_absolute():
        raise ValueError("Campaign study path must be relative")
    study = (root / relative).resolve()
    allowed = (root / CAMPAIGN_ROOT / "cycles").resolve()
    if not study.is_relative_to(allowed) or study.name != "study":
        raise ValueError("Campaign publication escapes its cycle directory")
    protocol = _record(study / "protocol.json")
    if (
        hashlib.sha256(json.dumps(protocol, sort_keys=True, allow_nan=False).encode()).hexdigest()
        != publication["protocol_sha256"]
    ):
        raise ValueError("Campaign publication protocol differs")
    labels = protocol["labels"]
    if (
        not isinstance(labels, dict)
        or not 1 <= len(labels) <= 16
        or "core_daily" not in labels
        or any(
            not re.fullmatch(r"[a-zA-Z0-9_]{1,64}", key)
            or not isinstance(value, str)
            or len(value) > 200
            for key, value in labels.items()
        )
    ):
        raise ValueError("Invalid campaign approach labels")
    if protocol.get("whole_share_buys") is True:
        labels = {
            arm: label for arm, label in labels.items()
            if protocol.get("whole_share_buys_by_arm", {}).get(arm, True) is True
        }
        if "core_daily" not in labels:
            raise ValueError("Campaign control is not a whole-share comparison")
    return study, labels, publication


def overview(root: Path, *, period="2026_ytd", strategy="baseline", cost=2, study_id="reference"):
    campaign_study, campaign_arms, campaign_publication = None, None, None
    if study_id == "campaign":
        try:
            campaign_study, campaign_arms, campaign_publication = _campaign_study(root)
        except (ValueError, OSError, KeyError, TypeError):
            return {
                "status": "unavailable",
                "reason": "No completed, verified campaign cycle is published yet.",
            }
    arms = (
        campaign_arms
        if study_id == "campaign"
        else PERCENTILE_SIZING_ARMS
        if study_id == "percentile_sizing"
        else ASSET_SIZING_ARMS
        if study_id == "asset_sizing"
        else GROUP_SIZING_ARMS
        if study_id == "group_sizing"
        else SIZING_ARMS
        if study_id == "conditional_sizing"
        else NEXT_ARMS
        if study_id == "volatility_next"
        else INTERACTION_ARMS
        if study_id == "interaction"
        else RECOVERY_ARMS
        if study_id == "recovery"
        else QUALITY_RECOVERY_ARMS
        if study_id == "quality_recovery"
        else OPPORTUNISTIC_ARMS
        if study_id == "opportunistic"
        else INDEX_ARMS
        if study_id == "indices"
        else CONDITIONAL_ARMS
        if study_id == "conditional"
        else SWITCHING_ARMS
        if study_id == "switching"
        else EFFICIENCY_ARMS
        if study_id == "efficiency"
        else VALIDATION_ARMS
        if study_id == "validation"
        else GROUP_ARMS
        if study_id == "groups"
        else ADAPTIVE_ARMS
        if study_id == "adaptive"
        else ARMS
    )
    if (
        study_id not in STUDIES
        or period not in PERIODS
        or strategy not in arms
        or (
            study_id
            not in {
                "validation",
                "efficiency",
                "switching",
                "conditional",
                "indices",
                "opportunistic",
                "recovery",
                "quality_recovery",
                "interaction",
                "volatility_next",
                "conditional_sizing",
                "group_sizing",
                "asset_sizing",
                "percentile_sizing",
                "campaign",
            }
            and period not in {"full", "recent", "2024", "2025", "2026_ytd"}
        )
        or cost not in {2, 6, 10}
    ):
        raise ValueError("Unsupported chart period, strategy or cost assumption")
    study = campaign_study if campaign_study is not None else root / STUDIES[study_id]
    if not study.is_dir():
        return {
            "status": "unavailable",
            "reason": "No registered strategy comparison is available.",
        }
    if (
        study_id
        in {
            "adaptive",
            "groups",
            "validation",
            "efficiency",
            "switching",
            "conditional",
            "indices",
            "opportunistic",
            "recovery",
            "quality_recovery",
            "interaction",
            "volatility_next",
            "conditional_sizing",
            "group_sizing",
            "asset_sizing",
            "percentile_sizing",
            "campaign",
        }
        and not (study / "report.md").is_file()
    ):
        return {
            "status": "unavailable",
            "reason": "This registered research comparison is not complete yet.",
        }
    try:
        protocol = _record(study / "protocol.json")
        protocol_hash = (study / "protocol.sha256").read_text().strip()
        records = {arm: _record(study / f"{arm}-{cost}bps.json") for arm in arms}
        if any(r["protocol_sha256"] != protocol_hash for r in records.values()):
            raise ValueError("Comparison protocol changed")
        series = []
        for arm in dict.fromkeys(
            [
                strategy,
                *(["all_groups"] if study_id == "interaction" else []),
                *(["matched_control"] if study_id == "opportunistic" else []),
                *(
                    ["conditional_reference"]
                    if study_id in {"indices", "opportunistic", "interaction"}
                    else []
                ),
                *(
                    ["core_daily"]
                    if study_id
                    in {
                        "validation",
                        "efficiency",
                        "switching",
                        "conditional",
                        "indices",
                        "opportunistic",
                        "recovery",
                        "quality_recovery",
                        "interaction",
                        "volatility_next",
                        "conditional_sizing",
                        "group_sizing",
                        "asset_sizing",
                        "percentile_sizing",
                        "campaign",
                    }
                    else ["groups"]
                    if study_id == "groups"
                    else []
                ),
                "core_daily"
                if study_id
                in {
                    "efficiency",
                    "switching",
                    "conditional",
                    "indices",
                    "opportunistic",
                    "recovery",
                    "quality_recovery",
                    "interaction",
                    "volatility_next",
                    "conditional_sizing",
                    "group_sizing",
                    "asset_sizing",
                    "percentile_sizing",
                    "campaign",
                }
                else "baseline",
                *(["B1"] if study_id in {"adaptive", "groups", "validation"} else []),
            ]
        ):
            rows, opening = _slice(records[arm], period)
            if opening is not None:
                series.append({"id": arm, "name": arms[arm], "points": _curve(rows, opening)})
        dates = [p["date"] for p in series[0]["points"]]
        snapshot_id = protocol.get("snapshot_id", "alpaca-sip-core-2026-10-13-verified")
        base = (root / "amat/data/snapshots").resolve()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", snapshot_id):
            raise ValueError("Invalid benchmark snapshot identity")
        snapshot = base / snapshot_id
        if snapshot.is_symlink() or not snapshot.resolve().is_relative_to(base):
            raise ValueError("Benchmark source escapes registered data")
        manifest = _read(snapshot / "manifest.json")
        expected = manifest["partition_sha256"]["daily.parquet"]
        if protocol["partition_sha256"]["daily.parquet"] != expected:
            raise ValueError("Benchmark source differs from study source")
        if hashlib.sha256((snapshot / "daily.parquet").read_bytes()).hexdigest() != expected:
            raise ValueError("Benchmark source changed")
        # Benchmark source is fixed to the same completed-session data as this study.
        daily_prices = pl.read_parquet(snapshot / "daily.parquet")
        prices = daily_prices.filter(
            pl.col("symbol").is_in(["SPY", "QQQ"])
            & pl.col("session_date").cast(pl.String).is_in(dates)
        )
        benchmarks = []
        for symbol in ("SPY", "QQQ"):
            data = prices.filter(pl.col("symbol") == symbol).sort("session_date")
            if [str(d) for d in data["session_date"].to_list()] != dates:
                continue  # a partial benchmark is never drawn across unobserved dates
            first = float(data["adjusted_close"][0])
            benchmark_rows = [
                {"session": str(r["session_date"]), "equity": r["adjusted_close"]}
                for r in data.to_dicts()
            ]
            benchmarks.append(
                {
                    "id": symbol,
                    "name": symbol + " buy & hold",
                    "points": _curve(benchmark_rows, first),
                }
            )
        rows, opening = _slice(records[strategy], period)
        months = {}
        previous = opening
        for row in rows[1:]:
            month = row["session"][:7]
            months.setdefault(month, {"opening": previous, "ending": previous})["ending"] = float(
                row["equity"]
            )
            previous = float(row["equity"])
        comparison = [
            {"id": arm, "name": arms[arm], **record["metrics"][period]}
            for arm, record in records.items()
        ]
        sensitivity = []
        if study_id in {
            "adaptive",
            "groups",
            "validation",
            "efficiency",
            "switching",
            "conditional",
            "indices",
            "opportunistic",
            "recovery",
            "quality_recovery",
            "interaction",
            "volatility_next",
            "conditional_sizing",
            "group_sizing",
            "asset_sizing",
            "percentile_sizing",
            "campaign",
        }:
            for arm in arms:
                values = {}
                for fee in (2, 6, 10):
                    record = (
                        records[arm] if fee == cost else _record(study / f"{arm}-{fee}bps.json")
                    )
                    if record["protocol_sha256"] != protocol_hash:
                        raise ValueError("Cost comparison protocol changed")
                    values[str(fee)] = record["metrics"][period]["net_return"]
                sensitivity.append({"id": arm, "name": arms[arm], "values": values})
        factorial = []
        if study_id == "groups":
            for label, core, wide in [
                ("Every 5 trading days", "groups", "wide_groups"),
                ("Daily selection", "core_daily", "wide_daily"),
                ("5-day selection + quality", "core_quality", "wide_quality"),
                ("Daily + quality", "core_both", "wide_both"),
            ]:
                factorial.append(
                    {
                        "label": label,
                        "core": {"id": core, **records[core]["metrics"][period]},
                        "wide": {"id": wide, **records[wide]["metrics"][period]},
                    }
                )
        efficiency = None
        if study_id in {
            "efficiency",
            "switching",
            "conditional",
            "indices",
            "opportunistic",
            "recovery",
            "quality_recovery",
            "interaction",
            "volatility_next",
            "conditional_sizing",
            "group_sizing",
            "asset_sizing",
            "percentile_sizing",
            "campaign",
        }:
            decisions = _record(study / "decisions.json")
            if decisions["protocol_sha256"] != protocol_hash:
                raise ValueError("Candidate decisions differ from the measured protocol")
            if study_id == "recovery":
                selection = _record(study / "selection.json")
                if selection["protocol_sha256"] != protocol_hash:
                    raise ValueError("Selection protocol mismatch")
                for duplicate, canonical in selection["duplicates"].items():
                    if duplicate not in arms or canonical not in arms:
                        raise ValueError("Unknown duplicate")
                    for fee in [2, 6, 10]:
                        if (
                            _record(study / f"{duplicate}-{fee}bps.json")["sessions"]
                            != _record(study / f"{canonical}-{fee}bps.json")["sessions"]
                        ):
                            raise ValueError("Duplicate histories differ")
                    decisions["decisions"][duplicate] = {
                        **decisions["decisions"][duplicate],
                        "status": "duplicate",
                        "reason": f"Equivalent to {canonical}; removed from active selection",
                    }
            approved = {
                arm
                for arm, value in decisions["decisions"].items()
                if value["status"] in {"incumbent", "retained", "reference"}
            }
            if strategy not in approved:
                raise ValueError("Rejected research changes are unavailable for active selection")
            from server.native_annual import published_comparison

            efficiency = {
                "native_annual": published_comparison(root) if study_id == "campaign" else None,
                "campaign": campaign_publication,
                "current_default": "All groups + volatility-triggered cost-aware switching",
                "sizing_conditions": study_id
                in {"conditional_sizing", "group_sizing", "asset_sizing", "percentile_sizing"},
                "group_sizing": study_id == "group_sizing",
                "asset_sizing": study_id in {"asset_sizing", "percentile_sizing"},
                "percentile_sizing": study_id == "percentile_sizing",
                "asset_activation_columns": [
                    {"key": key, "label": label}
                    for key, label in (
                        [
                            ("p80", "80th percentile"),
                            ("p90", "90th percentile"),
                            ("p95", "95th percentile"),
                        ]
                        if study_id == "percentile_sizing"
                        else [
                            ("weak", "Weak trend"),
                            ("vol", "Volatility stress"),
                            ("either", "Either condition"),
                        ]
                    )
                ],
                "parent_id": "all_groups" if study_id == "interaction" else None,
                "preservation_2023": [
                    {
                        "id": arm,
                        "name": arms[arm],
                        "return": _record(study / f"{arm}-6bps.json")["metrics"]["2023"][
                            "net_return"
                        ],
                        "status": decisions["decisions"][arm]["status"],
                        "passes": decisions["decisions"][arm].get("preserves_2023"),
                    }
                    for arm in arms
                ]
                if study_id == "interaction"
                else None,
                "uncertainty_reference": "current best · all groups + volatility switching"
                if study_id
                in {
                    "volatility_next",
                    "conditional_sizing",
                    "group_sizing",
                    "asset_sizing",
                    "percentile_sizing",
                    "campaign",
                }
                else "all-groups parent"
                if study_id == "interaction"
                else "daily core groups",
                "parent_metrics": records["all_groups"]["metrics"][period]
                if study_id == "interaction"
                else None,
                "reference_count": sum(
                    v["status"] in {"incumbent", "reference"}
                    for v in decisions["decisions"].values()
                ),
                "candidate_count": sum(
                    v["status"]
                    not in {"incumbent", "reference", "historical_comparator", "diagnostic"}
                    for v in decisions["decisions"].values()
                ),
                "index_sleeves": None,
                "condition_summary": [
                    {"id": arm, "name": arms[arm], "fraction": record.get("condition_fraction")}
                    for arm, record in records.items()
                    if record.get("condition_fraction") is not None
                ],
                "condition_rules": protocol.get(
                    "conditional_rules", protocol.get("condition_rules")
                ),
                "audit_label": "campaign research cycle"
                if study_id == "campaign"
                else "asset volatility percentile sizing"
                if study_id == "percentile_sizing"
                else "asset-specific inverse-volatility sizing"
                if study_id == "asset_sizing"
                else "group-specific inverse-volatility sizing"
                if study_id == "group_sizing"
                else "conditional inverse-volatility sizing"
                if study_id
                in {"conditional_sizing", "group_sizing", "asset_sizing", "percentile_sizing"}
                else "five improvements to the current best"
                if study_id
                in {
                    "volatility_next",
                    "conditional_sizing",
                    "group_sizing",
                    "asset_sizing",
                    "percentile_sizing",
                    "campaign",
                }
                else "all-groups switching combinations"
                if study_id == "interaction"
                else "native 2023 recovery experiments"
                if study_id in {"recovery", "quality_recovery"}
                else "unused-cash netted sleeve audit"
                if study_id == "opportunistic"
                else "index long/short sleeve audit"
                if study_id == "indices"
                else "conditional switching audit"
                if study_id == "conditional"
                else "cost-aware switching and rank stability"
                if study_id == "switching"
                else "trade reduction and group-risk audit",
                "decisions": decisions["decisions"],
                "criterion": protocol["retention"],
                "approved": sorted(approved),
                "all_results": [
                    {**r, "status": decisions["decisions"][r["id"]]["status"]} for r in comparison
                ],
                "yearly": [
                    {
                        "year": str(y),
                        "approaches": [
                            {"id": arm, "name": arms[arm], **record["metrics"][str(y)]}
                            for arm, record in records.items()
                            if arm in approved
                            or study_id
                            in {
                                "conditional_sizing",
                                "group_sizing",
                                "asset_sizing",
                                "percentile_sizing",
                                "campaign",
                            }
                        ],
                    }
                    for y in range(2020, 2027)
                ],
                "stress": [
                    {
                        "label": PERIODS[key],
                        "approaches": [
                            {"id": arm, "name": arms[arm], **record["metrics"][key]}
                            for arm, record in records.items()
                            if arm in approved
                            or study_id
                            in {
                                "conditional_sizing",
                                "group_sizing",
                                "asset_sizing",
                                "percentile_sizing",
                                "campaign",
                            }
                        ],
                    }
                    for key in ["stress_2018", "stress_covid", "stress_2022"]
                ],
                "monthly": {
                    arm: _monthly_detail(record, period)
                    for arm, record in records.items()
                    if arm in approved
                    or study_id
                    in {"conditional_sizing", "group_sizing", "asset_sizing", "percentile_sizing"}
                },
                "baseline_metrics": records["core_daily"]["metrics"][period],
                "cost_grid": [
                    {
                        "id": arm,
                        "name": arms[arm],
                        "status": decisions["decisions"][arm]["status"],
                        "values": {
                            str(fee): _record(study / f"{arm}-{fee}bps.json")["metrics"][period][
                                "net_return"
                            ]
                            for fee in [2, 6, 10]
                        },
                    }
                    for arm in arms
                ],
                "rejected_count": sum(
                    value["status"] == "rejected" for value in decisions["decisions"].values()
                )
                if study_id == "campaign"
                else len(arms) - len(approved),
            }
            if study_id in {
                "conditional_sizing",
                "group_sizing",
                "asset_sizing",
                "percentile_sizing",
            }:
                activation = _record(study / "activation.json")
                if activation["protocol_sha256"] != protocol_hash:
                    raise ValueError("Sizing activation protocol mismatch")
                if set(activation["conditions"]) != set(arms) - {"core_daily"}:
                    raise ValueError("Sizing activation arms mismatch")
                summaries = []
                for arm, value in activation["conditions"].items():
                    groups = (
                        value
                        if study_id in {"group_sizing", "asset_sizing", "percentile_sizing"}
                        else {"portfolio": value}
                    )
                    for group, rows in groups.items():
                        selected = [r for r in rows if dates[0] <= r["session"] <= dates[-1]]
                        if not selected or any(type(r["active"]) is not bool for r in selected):
                            raise ValueError("Invalid sizing activation history")
                        summaries.append(
                            {
                                "id": f"{arm}-{group}",
                                "arm": arm,
                                "member": group,
                                "name": arms[arm]
                                + (
                                    " · "
                                    + {
                                        "growth": "Shares",
                                        "real_assets": "Real assets",
                                        "defensive": "Defensive assets",
                                    }.get(group, group)
                                    if group != "portfolio"
                                    else ""
                                ),
                                "fraction": sum(r["active"] for r in selected) / len(selected),
                            }
                        )
                efficiency["condition_summary"] = summaries
                if study_id in {"asset_sizing", "percentile_sizing"}:
                    assets = {}
                    for row in summaries:
                        if not row["arm"].endswith("_reduce"):
                            continue
                        symbol = row["member"]
                        asset = assets.setdefault(symbol, {"symbol": symbol})
                        asset[row["arm"].split("_")[0]] = row["fraction"]
                    efficiency["asset_activation"] = list(assets.values())
                    efficiency["condition_summary"] = []
            if study_id in {"indices", "opportunistic"}:
                borrow_rows = []
                for arm in arms:
                    if arm in {"core_daily", "conditional_reference", "matched_control"}:
                        continue
                    cases = {
                        suffix: _record(study / f"{arm}-6bps{suffix}.json")
                        for suffix in ["", "-borrow6", "-no-borrow"]
                    }
                    if any(r["protocol_sha256"] != protocol_hash for r in cases.values()):
                        raise ValueError("Borrowing stress differs from registered protocol")
                    borrow_rows.append(
                        {
                            "id": arm,
                            "name": arms[arm],
                            "status": decisions["decisions"][arm]["status"],
                            "normal": cases[""]["metrics"]["full"],
                            "expensive": cases["-borrow6"]["metrics"]["full"],
                            "unavailable": cases["-no-borrow"]["metrics"]["full"],
                            "borrow_cost": cases[""]["borrow_total"],
                            "short_days": cases[""]["sleeve_short_days"],
                            "margin_days": cases[""]["sleeve_margin_breach_days"],
                        }
                    )
                efficiency["index_sleeves"] = {
                    "allocation": protocol["allocation"],
                    "rules": protocol["rules"],
                    "cost_scope": protocol["cost_scope"],
                    "borrow_rows": borrow_rows,
                }
            comparison = [r for r in comparison if r["id"] in approved]
            sensitivity = [r for r in sensitivity if r["id"] in approved]
        validation = None
        if study_id == "validation":
            diagnostics = _record(study / "diagnostics.json")
            if diagnostics["protocol_sha256"] != protocol_hash:
                raise ValueError("Exit diagnostics protocol changed")
            monthly_details = {
                arm: _monthly_detail(record, period) for arm, record in records.items()
            }
            asset_history = {
                symbol: list(
                    zip(
                        frame["session_date"].cast(pl.String).to_list(),
                        frame["adjusted_close"].to_list(),
                    )
                )
                for symbol in [
                    "SPY",
                    "QQQ",
                    "IWM",
                    "EFA",
                    "EEM",
                    "IEF",
                    "GLD",
                    "SLV",
                    "DBC",
                    "USO",
                    "UUP",
                ]
                if not (
                    frame := daily_prices.filter(pl.col("symbol") == symbol).sort("session_date")
                ).is_empty()
            }

            def market_context(month):
                result = []
                for symbol, history in asset_history.items():
                    prior = [price for day, price in history if day < month["start"]]
                    observed = [
                        price for day, price in history if month["start"] <= day <= month["end"]
                    ]
                    if prior and observed:
                        result.append({"symbol": symbol, "return": observed[-1] / prior[-1] - 1})
                return result

            core = records["core_daily"]["metrics"][period]
            reference = records["baseline"]["metrics"][period]
            learned = records["ml_recovery"]["metrics"][period]
            recover = records["recovery"]["metrics"][period]
            worse_calibration = sum(f["brier"] > f["base_rate_brier"] for f in diagnostics["folds"])
            validation = {
                "findings": [
                    f"At the selected cost and dates, daily groups returned {core['net_return']:.2%}; "
                    f"v13 returned {reference['net_return']:.2%}. Their largest declines were "
                    f"{abs(core['max_drawdown']):.2%} and {abs(reference['max_drawdown']):.2%}.",
                    f"Rejected: simple exit with recovery returned {recover['net_return']:.2%}, with a "
                    f"{abs(recover['max_drawdown']):.2%} largest decline. Compare the return "
                    "given up with the reduction in losses before adopting a recovery rule.",
                    f"Rejected: ML with recovery returned {learned['net_return']:.2%}. Its probability "
                    f"error exceeded the constant historical-risk estimate in {worse_calibration} "
                    f"of {len(diagnostics['folds'])} yearly tests. This version is not ready for promotion.",
                ],
                "folds": diagnostics["folds"],
                "signals": diagnostics["predictions"],
                "recovery_policy": protocol["recovery"],
                "recovery_episodes": records[strategy]["recovery_episodes"],
                "months": [
                    {
                        "month": m["month"],
                        "start": m["start"],
                        "end": m["end"],
                        "market_context": market_context(m),
                        "approaches": [
                            {
                                "id": arm,
                                "name": arms[arm],
                                **next(row for row in detail if row["month"] == m["month"]),
                            }
                            for arm, detail in monthly_details.items()
                        ],
                    }
                    for m in monthly_details["core_daily"]
                ],
                "yearly": [
                    {
                        "year": str(y),
                        "approaches": [
                            {"id": arm, "name": arms[arm], **record["metrics"][str(y)]}
                            for arm, record in records.items()
                        ],
                    }
                    for y in range(2020, 2027)
                ],
                "stress": [
                    {
                        "period": p,
                        "label": PERIODS[p],
                        "approaches": [
                            {"id": arm, "name": arms[arm], **record["metrics"][p]}
                            for arm, record in records.items()
                        ],
                    }
                    for p in ["stress_2018", "stress_covid", "stress_2022"]
                ],
                "scope": protocol["history_scope"],
                "cost_scope": protocol["cost_scope"],
            }
        diagnostic_2023 = None
        if study_id in {"recovery", "quality_recovery", "opportunistic"}:
            diagnostic_2023 = _record(
                root / "amat/outputs/analysis/2026-10-19-2023-attribution/attribution.json"
            )
            native = _record(study / "core_daily-6bps.json")
            identity = hashlib.sha256(
                json.dumps(native["sessions"], sort_keys=True, allow_nan=False).encode()
            ).hexdigest()
            if identity != diagnostic_2023["baseline_sessions_sha256"]:
                raise ValueError("Attribution baseline changed")
            improved = _record(
                root
                / "amat/outputs/analysis/2026-10-19-2023-all-groups-attribution/attribution.json"
            )
            improved_record = _record(root / STUDIES["recovery"] / "all_groups-6bps.json")
            improved_identity = hashlib.sha256(
                json.dumps(improved_record["sessions"], sort_keys=True, allow_nan=False).encode()
            ).hexdigest()
            if improved_identity != improved["candidate_sessions_sha256"]:
                raise ValueError("Improved attribution history differs")
            diagnostic_2023["improved_variant"] = improved

        return {
            "diagnostic_2023": diagnostic_2023,
            "candidate_policy": {
                "incumbent": "core_daily",
                "references": ["baseline", "B1"],
                "rejected": [
                    arm for arm in VALIDATION_ARMS if arm not in {"core_daily", "baseline", "B1"}
                ],
                "criterion": "Match or exceed daily-group net return in the full and walk-forward primary-cost comparisons.",
            }
            if study_id == "validation"
            else None,
            "efficiency": efficiency,
            "validation": validation,
            "factorial": factorial,
            "comparison_scope": protocol.get("comparison_scope"),
            "window_audit": protocol.get("window_audit"),
            "study_id": study_id,
            "cost_sensitivity": sensitivity,
            "quantity_scope": (
                "Whole-share purchases and rebalances; native full exits are preserved."
                if protocol.get("whole_share_buys") is True
                else "Earlier research: whole-share execution has not been verified. "
                "These results need a corrected-contract replay before promotion."
            ),
            "study_scope": protocol.get("historical_scope", protocol.get("history_scope")),
            "execution_scope": protocol.get("execution_scope", protocol.get("cost_scope")),
            "weekly_scope": protocol.get("weekly_scope", protocol.get("group_scope")),
            "status": "available",
            "scope": "Historical simulation on previously researched data. Overall default: all groups + volatility-triggered cost-aware switching. New experiments require the registered acceptance checks.",
            "period": period,
            "period_label": PERIODS[period],
            "strategy": strategy,
            "cost_bps_per_side": cost,
            "start": dates[0],
            "end": dates[-1],
            "series": series,
            "benchmarks": benchmarks,
            "benchmark_scope": "Adjusted-close buy-and-hold references before trading costs; different investment mandates.",
            "metrics": records[strategy]["metrics"][period],
            "comparison": comparison,
            "monthly": [
                {"month": key, "return": (value["ending"] / value["opening"] - 1) * 100}
                for key, value in months.items()
            ],
            "source": str(study.relative_to(root)),
            "protocol_sha256": records[strategy]["protocol_sha256"],
        }
    except (OSError, ValueError, KeyError, IndexError, TypeError, pl.exceptions.PolarsError) as exc:
        return {
            "status": "unavailable",
            "reason": "The saved research evidence could not be verified.",
            "error_type": type(exc).__name__,
        }


def saved_screen(root: Path):
    path = root / "amat/outputs/analysis/2026-10-18-codebase-review/diverse-five-day-screen.json"
    try:
        value = _read(path)
        if value.get("schema") != "strategy-condition-screen/1":
            raise ValueError("Unknown screening evidence")
        # Validate the underlying snapshot still exists before advertising its result.
        snapshot_id = value["snapshot_id"]
        base = (root / "amat/data/snapshots").resolve()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", snapshot_id):
            raise ValueError("Invalid snapshot identity")
        target = base / snapshot_id
        if target.is_symlink() or not target.resolve().is_relative_to(base):
            raise ValueError("Snapshot is outside registered data")
        manifest = _read(target / "manifest.json")
        if manifest["snapshot_id"] != snapshot_id:
            raise ValueError("Snapshot identity changed")
        return {
            "status": "available",
            "screen": {k: v for k, v in value.items() if k != "excluded"},
            "dataset": {
                k: manifest[k]
                for k in ["first_session", "last_session", "daily_rows", "window_rows"]
            },
        }
    except (OSError, ValueError, KeyError):
        return {
            "status": "unavailable",
            "reason": "No saved asset screen is available. Choose Find assets to create one.",
        }


def account_history(state: Path):
    """Reported account values remain separate from simulated research curves."""
    path = state / "equity_curve.ndjson"
    try:
        if path.is_symlink() or path.stat().st_size > 8_000_000:
            raise ValueError("Account history cannot be read")
        rows = []
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            date = str(row["session"])
            dt.date.fromisoformat(date)
            value = float(row["equity"])
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Invalid account history value")
            rows.append({"date": date, "value": value})
        if len({r["date"] for r in rows}) != len(rows):
            raise ValueError("Duplicate account dates require reconciliation")
        rows.sort(key=lambda r: r["date"])
        return {
            "status": "available" if len(rows) >= 2 else "insufficient_history",
            "points": rows,
            "source": "executor equity_curve.ndjson",
            "scope": "Recorded paper-account values; gaps are not filled.",
        }
    except (OSError, ValueError, KeyError, TypeError):
        return {
            "status": "unavailable",
            "points": [],
            "reason": "Recorded account history is unavailable.",
        }
