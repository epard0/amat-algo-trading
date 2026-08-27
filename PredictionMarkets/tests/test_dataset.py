"""The training dataset: what a row means, and the traps it exists to avoid.

The dataset is one row per *evaluation*, not per trade, because the refusals are
the majority of the signal. Most of these tests guard against silently producing
a dataset that looks usable and is not.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))

from dataset import (  # noqa: E402
    FEATURE_FIELDS,
    SCHEMA_VERSION,
    build_sample,
    decompose_z,
    schema_of,
)


def prediction(**over):
    base = {
        "probability_yes": "0.7184648853636378",
        "raw_probability_yes": "0.72",
        "z_score": "0.58",
        "distance_return": "0.0001028",
        "momentum_return": "0.00002",
        "volatility_per_minute": "0.000367",
        "effective_volatility": "0.000367",
        "effective_drift_minutes": "1.3",
        "minutes_remaining": "3.1667",
        "applied_shrinkage": "0.25",
        "regime": "CONSOLIDATION",
        "regime_confidence": "0.55",
        "current_spot": "69312.5",
        "regime_features": {name: 1.0 for name in FEATURE_FIELDS},
    }
    base.update(over)
    return base


def record(**over):
    base = {
        "record_type": "decision",
        "decision_id": "abc123",
        "cycle_id": "c1",
        "timestamp": "2026-08-20T04:26:50+00:00",
        "mode": "live",
        "environment": "production",
        "asset": "BTC",
        "ticker": "KXBTC15M-26AUG200030-30",
        "seconds_to_close": 190,
        "market": {
            "event_ticker": "KXBTC15M-26AUG200030",
            "target_price": "69305.38",
            "yes_bid": "0.6000",
            "yes_ask": "0.6100",
            "no_ask": "0.4000",
            "implied_probability_yes": "0.6050",
            "fee_multiplier": "1",
            "close_time": "2026-08-20T04:30:00+00:00",
        },
        "thresholds": {"min_probability": "0.62"},
        "gates": [{"name": "cooldown", "allowed": True},
                  {"name": "min_edge", "allowed": False, "reason": "too thin"}],
        "blocked_by": "min_edge",
        "decision": "skipped",
        "decision_reason": "edge below floor",
        "prediction": prediction(),
    }
    base.update(over)
    return base


SETTLED_NO = {"KXBTC15M-26AUG200030-30": {"ticker": "KXBTC15M-26AUG200030-30",
                                          "result": "no", "status": "settled"}}


# ------------------------------------------------------ the z decomposition --


def test_z_splits_into_distance_and_drift() -> None:
    """The two halves behave completely differently and the aggregate hides it.

    On the -$8.66 BTC loss the total z was 0.58 - past the 0.50 floor meant to
    refuse too-close-to-call markets - while distance alone was 0.16σ. A search
    over that threshold that cannot see the halves re-derives the same gate.
    """
    sigma, z_dist, z_drift = decompose_z(prediction())
    assert sigma is not None
    assert abs(z_dist - 0.157) < 0.01          # distance alone: nowhere near 0.50
    assert abs((z_dist + z_drift) - 0.58) < 1e-6   # the halves reconstruct the whole


def test_the_decomposition_declines_rather_than_dividing_by_zero() -> None:
    for bad in ({"minutes_remaining": "0"}, {"effective_volatility": "0"},
                {"distance_return": None}):
        assert decompose_z(prediction(**bad)) == (None, None, None)


# ------------------------------------------------------------ what is a row --


def test_an_evaluation_with_no_prediction_is_not_a_sample() -> None:
    """445 evaluations are refused on entry_window before the model ever runs.
    They are facts about the bot's behaviour, not training data.
    """
    assert build_sample(record(prediction=None), {}, {}) is None
    bare = record()
    del bare["prediction"]
    assert build_sample(bare, {}, {}) is None


def test_a_refused_evaluation_is_still_a_labelled_sample() -> None:
    """This is the whole point: the counterfactuals outnumber the trades ~250:1."""
    s = build_sample(record(), SETTLED_NO, {})
    assert s["act_decision"] == "skipped"
    assert s["act_blocked_by"] == "min_edge"
    assert s["label_settled_yes"] is False
    assert s["label_source"] == "venue"
    assert s["res_trade_id"] is None


def test_every_regime_feature_survives_into_the_row() -> None:
    s = build_sample(record(), SETTLED_NO, {})
    for name in FEATURE_FIELDS:
        assert s[f"feat_{name}"] == 1.0


def test_gate_outcomes_are_preserved_for_replay() -> None:
    """Parameter search replays gates at new thresholds, so it needs which gates
    ran, which failed, and what the thresholds were at the time.
    """
    s = build_sample(record(), SETTLED_NO, {})
    assert s["act_gates_evaluated"] == ["cooldown", "min_edge"]
    assert s["act_gates_failed"] == ["min_edge"]
    assert s["act_thresholds"]["min_probability"] == "0.62"


# -------------------------------------------------------------- the labels --


def test_an_unsettled_market_yields_no_label_rather_than_a_guess() -> None:
    s = build_sample(record(), {}, {})
    assert s["label_settled_yes"] is None
    assert s["label_source"] is None


def test_a_void_market_is_not_labelled_yes_or_no() -> None:
    void = {"KXBTC15M-26AUG200030-30": {"ticker": "KXBTC15M-26AUG200030-30",
                                        "result": "void", "status": "settled"}}
    s = build_sample(record(), void, {})
    assert s["label_settled_yes"] is None
    assert s["label_result"] == "void"


def test_label_margin_reports_how_safe_the_label_is() -> None:
    """A strike synthesised from the Kraken open misses Kalshi's CF Benchmarks
    strike by a p95 of 0.9σ, so a row settled near its strike is a coin flip
    that two indices can legitimately disagree about. Consumers filter on this.
    """
    s = build_sample(record(), SETTLED_NO, {})
    assert s["label_margin_sigma"] == abs(s["model_z_from_distance"])


# --------------------------------------------------------------- the money --


def test_money_stays_exact_text() -> None:
    """Prices and P&L are strings everywhere else in this project; a dataset
    that quietly floats them is the one place the invariant would be lost.
    """
    s = build_sample(record(), SETTLED_NO, {})
    for key in ("mkt_yes_bid", "mkt_yes_ask", "mkt_target_price", "mkt_spot"):
        assert isinstance(s[key], str), key
    assert isinstance(s["model_probability_yes"], float)   # not money


def test_a_traded_evaluation_carries_its_realized_outcome() -> None:
    trades = {"KXBTC15M-26AUG200030-30": [{
        "trade_id": "t1", "ticker": "KXBTC15M-26AUG200030-30", "mode": "live",
        "strategy": "scalper", "outcome": "yes", "status": "LOST",
        "contracts": "15", "entry_price": "0.56", "total_debit": "8.66",
        "pnl": "-8.66",
    }]}
    s = build_sample(record(decision="traded", trade_id="t1"), SETTLED_NO, trades)
    assert s["res_trade_id"] == "t1"
    assert s["res_pnl"] == "-8.66"
    assert s["res_status"] == "LOST"


# --------------------------------------------------------------- the schema --


def test_the_schema_is_derived_from_the_rows_it_describes() -> None:
    """Declared separately, it would drift from what was actually written."""
    rows = [build_sample(record(), SETTLED_NO, {})]
    schema = schema_of(rows)
    assert schema["schema_version"] == SCHEMA_VERSION
    assert schema["rows"] == 1
    assert set(schema["columns"]) == set(rows[0])
    assert schema["columns"]["res_pnl"]["null_fraction"] == 1.0
    assert schema["columns"]["asset"]["null_fraction"] == 0.0


def test_rows_are_json_serialisable_as_written() -> None:
    s = build_sample(record(), SETTLED_NO, {})
    assert json.loads(json.dumps(s, sort_keys=True)) == s
