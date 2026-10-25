import importlib.util
from pathlib import Path
import pytest

p = Path(__file__).resolve().parents[2] / "scripts/observe_walkforward_market.py"
spec = importlib.util.spec_from_file_location("walk_observer", p)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_book_walk_depth_cost_and_integer_contract_rounding():
    q = dict(bids=[["99", "2"], ["98", "10"]], asks=[["101", "2"], ["102", "10"]])
    r = module.book_walk(q, notional=550)
    assert r["asks"]["requested_contracts"] == 5
    assert r["asks"]["filled_contracts"] == "5"
    assert float(r["asks"]["vwap"]) == pytest.approx(101.6)
    assert r["asks"]["one_way_cost_bps_including_4bps_fee"] == pytest.approx(164)
    assert r["bids"]["one_way_cost_bps_including_4bps_fee"] == pytest.approx(164)


def test_book_walk_reports_unfilled_quantity_without_inventing_depth():
    q = dict(bids=[["99", "1"]], asks=[["101", "1"]])
    r = module.book_walk(q, 1000)
    assert r["asks"]["requested_contracts"] == 10
    assert r["asks"]["unfilled_contracts"] == "9"
    assert r["asks"]["filled_contracts"] == "1"


def test_book_walk_is_invariant_to_raw_order_and_zero_levels():
    sorted_quote = dict(bids=[["99", "2"], ["98", "10"]], asks=[["101", "2"], ["102", "10"]])
    shuffled = dict(
        bids=[["97", "0"], ["98", "10"], ["99", "2"]],
        asks=[["103", "0"], ["102", "10"], ["101", "2"]],
    )
    assert module.book_walk(shuffled, 550) == module.book_walk(sorted_quote, 550)
    for side in module.book_walk(shuffled, 550).values():
        assert side["one_way_cost_bps_including_4bps_fee"] >= 4


def test_invalid_books_cannot_produce_diagnostics():
    for q in (
        dict(bids=[["102", "1"]], asks=[["101", "1"]]),
        dict(bids=[["99", "0"]], asks=[["101", "1"]]),
    ):
        with pytest.raises(ValueError):
            module.book_walk(q)
