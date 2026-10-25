"""Contract and causal semantics for inert genetic patterns."""

from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from autoresearch.genetic import canonical_genome_digest, evaluate_candles, parse_pattern

DOCS = Path(__file__).parents[2] / "docs" / "genetic-patterns"


def payload():
    return parse_pattern((DOCS / "candle.example.json").read_text()).model_dump(by_alias=True)


def rule(left, right, left_lag=0, right_lag=0):
    return {
        "left": {"field": left, "lag": left_lag},
        "op": "lt",
        "right": {"field": right, "lag": right_lag},
    }


def bars():
    return {
        name: np.array(values, dtype=float)[:, None]
        for name, values in {
            "open": [10, 11, 12, 8],
            "high": [12, 13, 14, 11],
            "low": [9, 10, 11, 7],
            "close": [11, 12, 13, 10],
        }.items()
    }


def test_examples_and_round_trip():
    for filename in ("candle.example.json", "symbolic.example.json"):
        pattern = parse_pattern((DOCS / filename).read_text())
        assert parse_pattern(pattern.model_dump_json(by_alias=True)) == pattern
        with pytest.raises(ValidationError):
            pattern.name = "changed"


def test_hand_calculated_candle_and_strict_equality():
    data = payload()
    data["genome"]["rules"] = [rule("open", "close", right_lag=1)]
    result = evaluate_candles(parse_pattern(data), bars())
    np.testing.assert_equal(result[:, 0], [np.nan, 0, 0, 1])


def test_strict_missing_even_when_other_rule_is_false():
    data = payload()
    data["genome"]["rules"] = [rule("open", "close", right_lag=1), rule("open", "close")]
    fields = bars()
    fields["high"][1] = np.nan
    np.testing.assert_equal(
        evaluate_candles(parse_pattern(data), fields)[:, 0], [np.nan, np.nan, np.nan, 1]
    )


def test_prefix_and_future_mutation_invariance():
    pattern = parse_pattern(payload())
    fields = bars()
    full = evaluate_candles(pattern, fields)
    for stop in range(5):
        prefix = {field: values[:stop] for field, values in fields.items()}
        np.testing.assert_equal(evaluate_candles(pattern, prefix), full[:stop])
    fields["close"][3] = np.inf
    np.testing.assert_equal(evaluate_candles(pattern, fields)[:3], full[:3])


@pytest.mark.parametrize("bad", [np.nan, np.inf, -1, 0, 100])
def test_invalid_ohlc_propagates(bad):
    data = payload()
    data["genome"]["rules"] = [rule("open", "close")]
    fields = bars()
    fields["low"][2] = bad
    result = evaluate_candles(parse_pattern(data), fields)
    assert np.isnan(result[2, 0])
    assert np.isfinite(result[[0, 1, 3]]).all()


def test_canonical_rule_order_orientation_and_name():
    original = payload()
    changed = deepcopy(original)
    changed["name"] = "another_label"
    changed["genome"]["rules"] = list(reversed(changed["genome"]["rules"]))
    for comparison in changed["genome"]["rules"]:
        comparison["left"], comparison["right"] = comparison["right"], comparison["left"]
        comparison["op"] = "lt" if comparison["op"] == "gt" else "gt"
    assert canonical_genome_digest(parse_pattern(original)) == canonical_genome_digest(
        parse_pattern(changed)
    )
    changed["binding"]["maxEffect"] = 0.1
    assert canonical_genome_digest(parse_pattern(original)) != canonical_genome_digest(
        parse_pattern(changed)
    )


@pytest.mark.parametrize(
    "rules",
    [
        [rule("open", "open")],
        [rule("high", "low")],
        [rule("open", "low")],
        [rule("high", "close")],
        [rule("open", "close"), rule("close", "open")],
        [rule("open", "close"), rule("open", "close")],
        [rule("open", "close", 0, 1), rule("close", "low", 1, 0)],
        [rule("open", "close", 0, 1), rule("high", "low", 1, 0)],
    ],
)
def test_reject_impossible_rules_and_strict_cycles(rules):
    data = payload()
    data["genome"]["rules"] = rules
    with pytest.raises(ValidationError):
        parse_pattern(data)


def test_reject_reversed_duplicate():
    data = payload()
    same = rule("open", "close")
    reverse = {"left": same["right"], "op": "gt", "right": same["left"]}
    data["genome"]["rules"] = [same, reverse]
    with pytest.raises(ValidationError):
        parse_pattern(data)


def test_nonstrict_ohlc_is_not_strict_tautology():
    data = payload()
    data["genome"]["rules"] = [rule("low", "high")]
    fields = {name: np.array([[1.0], [2.0]]) for name in bars()}
    np.testing.assert_equal(evaluate_candles(parse_pattern(data), fields), [[0.0], [0.0]])


@pytest.mark.parametrize("lag", [-1, 21, True, "1", 3])
def test_reject_invalid_lag(lag):
    data = payload()
    data["genome"]["maxLag"] = 2
    data["genome"]["rules"][0]["left"]["lag"] = lag
    with pytest.raises(ValidationError):
        parse_pattern(data)


@pytest.mark.parametrize("value", [np.nan, np.inf, "0.1", True])
def test_reject_nonfinite_or_coerced_binding(value):
    data = payload()
    data["binding"]["maxEffect"] = value
    with pytest.raises(ValidationError):
        parse_pattern(data)


def test_reject_unknown_fields():
    data = payload()
    data["evidence"] = {"profit": 100}
    with pytest.raises(ValidationError):
        parse_pattern(data)


def test_explicit_symbolic_refusal_and_tree_bounds():
    data = parse_pattern((DOCS / "symbolic.example.json").read_text()).model_dump(by_alias=True)
    with pytest.raises(ValueError, match="not implemented"):
        evaluate_candles(parse_pattern(data), bars())
    for _ in range(4):
        data["genome"]["root"] = {"node": "unary", "op": "neg", "arg": data["genome"]["root"]}
    with pytest.raises(ValidationError, match="exceeds depth"):
        parse_pattern(data)


def test_shape_and_missing_field_refusal():
    pattern = parse_pattern(payload())
    with pytest.raises(ValueError, match="all four"):
        evaluate_candles(pattern, {})
    fields = bars()
    fields["open"] = fields["open"].ravel()
    with pytest.raises(ValueError, match="shape"):
        evaluate_candles(pattern, fields)


def test_assets_are_independent_and_lag_beyond_panel_is_missing():
    data = payload()
    data["genome"]["rules"] = [rule("open", "close")]
    fields = {name: np.repeat(value, 2, axis=1) for name, value in bars().items()}
    fields["high"][2, 1] = np.nan
    result = evaluate_candles(parse_pattern(data), fields)
    np.testing.assert_equal(result[:, 0], [1, 1, 1, 1])
    np.testing.assert_equal(result[:, 1], [1, 1, np.nan, 1])
    data["genome"]["maxLag"] = 20
    data["genome"]["rules"] = [rule("open", "close", right_lag=20)]
    assert np.isnan(evaluate_candles(parse_pattern(data), fields)).all()


def test_symbolic_commutative_canonicalization_and_finite_constants():
    data = parse_pattern((DOCS / "symbolic.example.json").read_text()).model_dump(by_alias=True)
    original = canonical_genome_digest(parse_pattern(data))
    product = data["genome"]["root"]["left"]
    product["left"], product["right"] = product["right"], product["left"]
    assert canonical_genome_digest(parse_pattern(data)) == original
    for value in (np.nan, np.inf, True, "1"):
        data["genome"]["root"] = {"node": "constant", "value": value}
        with pytest.raises(ValidationError):
            parse_pattern(data)
