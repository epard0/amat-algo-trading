"""Offline reconciliation retains cumulative fills through cancellation races."""

from decimal import Decimal

import pytest

from executor.intraday_adapter import OfflineOrderBook


def test_duplicate_callback_and_restart_do_not_duplicate_order():
    book = OfflineOrderBook()
    key = book.authorize("decision", "SPY", "buy", Decimal(10))
    book.observe("event1", key, cumulative_filled=Decimal(3), status="partial")
    book.observe("event1", key, cumulative_filled=Decimal(3), status="partial")
    restored = OfflineOrderBook.restore(book.checkpoint())
    assert restored.remaining(key) == 7
    assert restored.authorize("decision", "SPY", "buy", Decimal(10)) == key
    restored.observe("event2", key, cumulative_filled=Decimal(3), status="cancel_pending")
    assert not restored.may_replace(key)
    restored.observe("event3", key, cumulative_filled=Decimal(5), status="cancelled")
    assert restored.may_replace(key)
    assert restored.remaining(key) == 5
    with pytest.raises(ValueError, match="cumulative"):
        restored.observe("event4", key, cumulative_filled=Decimal(11), status="filled")


def test_changed_checkpoint_and_callback_are_rejected():
    book = OfflineOrderBook()
    key = book.authorize("d", "SPY", "buy", Decimal(1))
    book.observe("e", key, cumulative_filled=Decimal(0), status="unknown")
    assert not book.may_replace(key)
    with pytest.raises(ValueError, match="duplicate"):
        book.observe("e", key, cumulative_filled=Decimal(1), status="filled")
    state = book.checkpoint()
    state["filled"][key] = "1"
    with pytest.raises(ValueError, match="checkpoint"):
        OfflineOrderBook.restore(state)
