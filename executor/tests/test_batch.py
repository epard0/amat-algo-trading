"""Fault-injected staged rotations; no network, credentials or broker orders."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal

import pytest
from tests.test_plan_source import _plan_payload
from tests.test_submit import _account, _limits, _now

from executor.batch import BatchPolicy, advance_batch, digest, load_batch, save_batch
from executor.broker import Order
from executor.errors import ConfigurationError, OrderOutcomeUnknownError
from executor.journal import Journal
from executor.plan_source import parse_plan

_NOW = _now().replace(minute=31)


class Broker:
    def __init__(self):
        self.account = _account(equity="1000", last_equity="1000", cash="0", buying_power="0")
        self.positions = {"SPY": Decimal(10)}
        self.orders = {}
        self.posts = []
        self.uncertain = False

    def get_account(self):
        return self.account

    def position_quantities(self):
        return self.positions.copy()

    def fractionable_symbols(self, symbols):
        return dict.fromkeys(symbols, True)

    def list_orders(self, status="open"):
        assert status == "open"
        return tuple(o for o in self.orders.values() if not o.is_terminal)

    def get_order_by_client_id(self, identity):
        return self.orders.get(identity)

    def _submit(self, **body):
        self.posts.append(body)
        order = Order.from_payload(
            {
                "id": f"broker-{len(self.posts)}",
                "client_order_id": body["client_order_id"],
                "symbol": body["symbol"],
                "side": body["side"],
                "qty": str(body["quantity"]),
                "filled_qty": "0",
                "status": "accepted",
                "submitted_at": _now().isoformat(),
                "type": "limit" if "limit_price" in body else "market",
                "limit_price": str(body.get("limit_price", "0")),
            }
        )
        self.orders[order.client_order_id] = order
        if self.uncertain:
            raise OrderOutcomeUnknownError("acknowledgement lost")
        return order

    def submit_market_order(self, **body):
        return self._submit(**body)

    def submit_limit_order(self, **body):
        return self._submit(**body)

    def cancel_order(self, order_id):
        for identity, order in self.orders.items():
            if order.id == order_id:
                self.orders[identity] = replace(order, status="pending_cancel")

    def fill(self, identity, quantity, *, status="filled", price=Decimal(100)):
        old = self.orders[identity]
        delta = Decimal(quantity) - old.filled_quantity
        cash = self.account.cash + delta * price * (1 if old.side == "sell" else -1)
        self.account = replace(self.account, cash=cash, buying_power=cash)
        self.positions[old.symbol] = self.positions.get(old.symbol, Decimal(0)) + delta * (
            -1 if old.side == "sell" else 1
        )
        self.orders[identity] = replace(
            old,
            filled_quantity=Decimal(quantity),
            filled_avg_price=price if Decimal(quantity) else None,
            status=status,
        )


def plan():
    payload = _plan_payload(account_equity="1000", current_positions={"SPY": "10"})
    template = payload["intents"][0]
    payload["intents"] = [
        {
            **template,
            "symbol": "SPY",
            "side": "sell",
            "quantity": "10",
            "reference_price": "100",
            "notional": "1000",
            "target_weight": 0,
        },
        {
            **template,
            "symbol": "QQQ",
            "side": "buy",
            "quantity": "20",
            "reference_price": "100",
            "notional": "2000",
            "target_weight": 0.9,
        },
    ]
    return parse_plan(payload)


def advance(root, broker, original=None, **kwargs):
    return advance_batch(
        original or plan(),
        broker,
        session=dt.date(2026, 8, 11),
        now=kwargs.pop("now", _NOW),
        journal=Journal(root / "journal.ndjson", root / "equity.ndjson"),
        limits=kwargs.pop("limits", _limits()),
        **kwargs,
    )


def test_sells_must_fill_before_buys_and_restart_never_repeats_sales(tmp_path):
    broker = Broker()
    first = advance(tmp_path, broker)
    assert first.status == "waiting_for_sales"
    assert len(broker.posts) == 1
    assert broker.posts[0]["side"] == "sell"
    second = advance(tmp_path, broker)
    assert second.pending
    assert len(broker.posts) == 1
    sell_id = broker.posts[0]["client_order_id"]
    broker.fill(sell_id, 10)
    third = advance(tmp_path, broker)
    assert third.status == "waiting_for_fills"
    assert len(broker.posts) == 2
    buy = broker.posts[1]
    assert buy["side"] == "buy"
    assert buy["quantity"] < 10
    assert buy["quantity"] * buy["limit_price"] * Decimal("1.0002") <= Decimal(990)
    assert third.state["pending_cash_commitments"] != "0"
    assert third.state["cash_observation"]["confirmed_cash"] == "1000"
    fourth = advance(tmp_path, broker)
    assert len(broker.posts) == 2
    assert fourth.pending
    broker.fill(buy["client_order_id"], buy["quantity"], price=buy["limit_price"])
    final = advance(tmp_path, broker)
    assert final.status == "complete_with_residual"
    assert not final.pending
    buy_state = final.state["intents"][1]
    assert Decimal(buy_state["original_authorized_quantity"]) == 20
    assert (
        Decimal(buy_state["cumulative_filled_quantity"])
        + Decimal(buy_state["remaining_intent_quantity"])
        == 20
    )
    assert final.state["pending_cash_commitments"] == "0"
    assert len(broker.posts) == 2


def test_partial_sale_does_not_fund_a_buy_until_terminal_then_uses_only_real_cash(tmp_path):
    broker = Broker()
    advance(tmp_path, broker)
    identity = broker.posts[0]["client_order_id"]
    broker.fill(identity, 4, status="partially_filled")
    assert advance(tmp_path, broker).pending
    assert len(broker.posts) == 1
    broker.fill(identity, 4, status="canceled")
    result = advance(tmp_path, broker)
    assert len(broker.posts) == 2
    assert broker.posts[1]["quantity"] * broker.posts[1]["limit_price"] * Decimal(
        "1.0002"
    ) <= Decimal(390)
    assert result.state["intents"][0]["remaining_intent_quantity"] == "6"


def test_lost_ack_is_reconciled_by_identity_without_another_post(tmp_path):
    broker = Broker()
    broker.uncertain = True
    first = advance(tmp_path, broker)
    assert first.status == "unknown"
    broker.uncertain = False
    assert advance(tmp_path, broker).pending
    assert len(broker.posts) == 1


def test_missing_broker_evidence_never_authorizes_a_retry(tmp_path):
    broker = Broker()
    advance(tmp_path, broker)
    broker.orders.clear()
    result = advance(tmp_path, broker)
    assert result.status == "unknown"
    assert len(broker.posts) == 1


def test_expiration_cannot_release_buys_after_late_sale_fill(tmp_path):
    broker = Broker()
    advance(tmp_path, broker)
    broker.fill(broker.posts[0]["client_order_id"], 10)
    expired = replace(plan(), expires_at=_NOW - dt.timedelta(seconds=1))
    # A changed expiration is itself rejected rather than changing original authority.
    with pytest.raises(ConfigurationError, match="expires_at"):
        advance(tmp_path, broker, expired)
    original = plan()
    original = replace(original, expires_at=_NOW + dt.timedelta(seconds=1))
    root = tmp_path / "expiry"
    other = Broker()
    advance(root, other, original)
    other.fill(other.posts[0]["client_order_id"], 10)
    result = advance(root, other, original, now=_NOW + dt.timedelta(seconds=2))
    assert result.status == "expired"
    assert len(other.posts) == 1


@pytest.mark.parametrize("change", ["plan", "policy", "account", "checkpoint", "fill"])
def test_changed_identity_or_invalid_progress_fails_closed(tmp_path, change):
    broker = Broker()
    advance(tmp_path, broker)
    original = plan()
    kwargs = {}
    if change == "plan":
        payload = dict(original.raw)
        payload["snapshot_id"] = "changed"
        original = parse_plan(payload)
    if change == "policy":
        kwargs["policy"] = BatchPolicy(reserve_fraction=Decimal(".02"))
    if change == "account":
        broker.account = replace(broker.account, account_number="another")
    if change == "checkpoint":
        path = tmp_path / "batches/2026-08-11.json"
        path.write_text(path.read_text().replace('"selling"', '"changed"') + "corrupt")
    if change == "fill":
        identity = broker.posts[0]["client_order_id"]
        broker.orders[identity] = replace(broker.orders[identity], filled_quantity=Decimal(11))
    with pytest.raises(ConfigurationError):
        advance(tmp_path, broker, original, **kwargs)
    assert len(broker.posts) == 1


def test_dry_run_writes_no_batch_or_submission(tmp_path):
    broker = Broker()
    result = advance(tmp_path, broker, dry_run=True)
    assert result.status == "dry_run"
    assert not broker.posts
    assert not (tmp_path / "batches").exists()
    assert not (tmp_path / "submissions").exists()


def test_external_open_order_blocks_batch(tmp_path):
    broker = Broker()
    broker.orders["external"] = Order.from_payload(
        {
            "id": "outside",
            "client_order_id": "external",
            "symbol": "SPY",
            "side": "buy",
            "qty": "1",
            "filled_qty": "0",
            "status": "accepted",
        }
    )
    with pytest.raises(ConfigurationError, match="outside"):
        advance(tmp_path, broker)
    assert not broker.posts


def test_pending_cash_and_cumulative_fill_checkpoint_are_validated(tmp_path):
    broker = Broker()
    advance(tmp_path, broker)
    path = tmp_path / "batches/2026-08-11.json"
    body = load_batch(path)
    body["intents"][0]["remaining_intent_quantity"] = "9"
    save_batch(path, body)
    with pytest.raises(ConfigurationError, match="inconsistent"):
        load_batch(path)
    assert digest(BatchPolicy().payload()) == BatchPolicy().identity


def test_expiry_requests_cancel_and_keeps_commitment_until_broker_confirms(tmp_path):
    broker = Broker()
    advance(tmp_path, broker)
    sell = broker.posts[0]["client_order_id"]
    broker.fill(sell, 10)
    advance(tmp_path, broker)
    buy = broker.posts[1]["client_order_id"]
    expired = advance(tmp_path, broker, now=_NOW.replace(minute=35))
    assert expired.status == "expired_pending_cancellation"
    assert expired.pending
    assert Decimal(expired.state["pending_cash_commitments"]) > 0
    assert broker.orders[buy].status == "pending_cancel"
    # A fill can race cancellation; only confirmed cumulative broker state releases cash.
    broker.fill(buy, 1, status="canceled", price=Decimal(100))
    final = advance(tmp_path, broker, now=_NOW.replace(minute=36))
    assert final.status == "expired"
    assert not final.pending
    assert final.state["intents"][1]["cumulative_filled_quantity"] == "1"
    assert final.state["pending_cash_commitments"] == "0"
    assert len(broker.posts) == 2


def test_existing_buy_commitments_are_not_subtracted_twice_from_buying_power(tmp_path):
    broker = Broker()
    original = plan()
    payload = dict(original.raw)
    second = dict(payload["intents"][1])
    second.update(symbol="IWM", quantity="20")
    payload["intents"] = [*payload["intents"], second]
    original = parse_plan(payload)
    advance(tmp_path, broker, original)
    broker.fill(broker.posts[0]["client_order_id"], 10)
    actual_submit = broker.submit_limit_order

    def reserve(**body):
        result = actual_submit(**body)
        broker.account = replace(
            broker.account,
            buying_power=broker.account.buying_power - body["quantity"] * body["limit_price"],
        )
        return result

    broker.submit_limit_order = reserve
    result = advance(tmp_path, broker, original)
    assert len(broker.posts) == 3
    assert abs(broker.posts[1]["quantity"] - broker.posts[2]["quantity"]) <= Decimal(".000000001")
    assert Decimal(result.state["pending_cash_commitments"]) <= Decimal(990)


def test_frozen_original_authorization_cannot_be_enlarged_even_with_a_rehashed_checkpoint(tmp_path):
    broker = Broker()
    advance(tmp_path, broker)
    path = tmp_path / "batches/2026-08-11.json"
    state = load_batch(path)
    state["intents"][1]["original_authorized_quantity"] = "200"
    state["intents"][1]["remaining_intent_quantity"] = "200"
    save_batch(path, state)
    with pytest.raises(ConfigurationError, match="authorization changed"):
        advance(tmp_path, broker)


def test_limit_buffers_respect_session_and_symbol_notional_caps(tmp_path):
    broker = Broker()
    broker.account = replace(
        broker.account, equity=Decimal(2000), cash=Decimal(2000), buying_power=Decimal(2000)
    )
    broker.positions = {}
    original = plan()
    payload = dict(original.raw)
    payload.update(account_equity="2000", current_positions={})
    payload["intents"] = [{**payload["intents"][1], "quantity": "10", "notional": "1000"}]
    original = parse_plan(payload)
    result = advance(
        tmp_path,
        broker,
        original,
        limits=_limits(max_session_notional=Decimal(1000), max_symbol_notional=Decimal(1000)),
    )
    assert result.pending
    assert len(broker.posts) == 1
    assert broker.posts[0]["quantity"] * broker.posts[0]["limit_price"] <= Decimal(1000)


def test_whole_share_policy_rounds_buys_preserves_fractional_exit_and_authorization(tmp_path):
    broker=Broker();broker.account=replace(broker.account,cash=Decimal('1000'),buying_power=Decimal('1000'))
    payload=plan().raw.copy();payload['intents']=[dict(payload['intents'][1],quantity='9.75')]
    original=parse_plan(payload);policy=BatchPolicy(whole_share_buys=True)
    result=advance(tmp_path,broker,original,policy=policy)
    assert broker.posts[0]['quantity']==Decimal(9)
    row=result.state['intents'][0]
    assert row['original_authorized_quantity']=='9.75'
    assert Decimal(row['remaining_intent_quantity'])==Decimal('9.75')
    advance(tmp_path,broker,original,policy=policy);assert len(broker.posts)==1
    with pytest.raises(ConfigurationError):advance(tmp_path,broker,original,policy=BatchPolicy())
    assert BatchPolicy().identity!=policy.identity


def test_whole_share_purchases_do_not_strand_legacy_fractional_exits(tmp_path):
    broker=Broker();broker.positions={'SPY':Decimal('10.25')}
    payload=plan().raw.copy();payload['intents']=[dict(payload['intents'][0],quantity='10.25')]
    result=advance(tmp_path,broker,parse_plan(payload),policy=BatchPolicy(whole_share_buys=True))
    assert broker.posts[0]['side']=='sell' and broker.posts[0]['quantity']==Decimal('10.25')
    assert result.state['intents'][0]['original_authorized_quantity']=='10.25'
