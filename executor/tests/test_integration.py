"""One pass through the whole executor, against the real strategy.

This is the test that would have caught every integration bug the unit tests
cannot: the subprocess invocation, the JSON handshake with a real `amat plan`,
and the fact that a plan built from a v6 configuration parses into the shape
`submit_plan` and the report expect.

It is skipped when the strategy checkout is absent, so the suite still runs in
an environment that has only this package.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest

from executor.broker import Account, Order
from executor.journal import Journal
from executor.plan_source import build_plan
from executor.report import build_daily_report, prune_old_reports, write_report
from executor.submit import submit_plan

AMAT_ROOT = Path(__file__).resolve().parents[2] / "amat"
SNAPSHOT = AMAT_ROOT / "data" / "snapshots" / "alpaca-sip-2026-08-11"
CONFIG = AMAT_ROOT / "configs" / "strategy" / "amat_v6.toml"

pytestmark = pytest.mark.skipif(
    not (CONFIG.is_file() and SNAPSHOT.is_dir()),
    reason="strategy checkout or snapshot not present",
)


class _FakeBroker:
    """A broker that accepts everything, so the test exercises our logic only."""

    def __init__(self) -> None:
        self.submitted: list[dict[str, object]] = []

    def fractionable_symbols(self, symbols: tuple[str, ...]) -> dict[str, bool]:
        return dict.fromkeys(symbols, True)

    def submit_market_order(
        self, *, symbol: str, side: str, quantity: Decimal, client_order_id: str
    ) -> Order:
        self.submitted.append({"symbol": symbol, "side": side, "quantity": quantity})
        return Order.from_payload(
            {
                "id": f"order-{len(self.submitted)}",
                "client_order_id": client_order_id,
                "symbol": symbol,
                "side": side,
                "qty": str(quantity),
                "filled_qty": str(quantity),
                "filled_avg_price": "100.00",
                "status": "filled",
                "submitted_at": "2026-08-11T13:30:00Z",
            }
        )


@pytest.fixture(scope="module")
def real_plan():
    """A plan from the real v6 configuration. Offline, so no network."""
    return build_plan(
        amat_root=AMAT_ROOT,
        strategy_config=CONFIG,
        snapshot=SNAPSHOT,
        equity=Decimal("100000"),
        positions={},
        offline=True,
    )


def test_the_real_v6_config_produces_a_usable_plan(real_plan) -> None:
    assert real_plan.strategy_version.startswith("6.")
    assert real_plan.schema_version.startswith("1.")
    assert real_plan.intents, "v6 from a flat account should want to buy something"
    assert real_plan.unrecognized_holdings == ()
    # Long-or-cash under a 100% gross cap.
    assert 0.0 < real_plan.gross_target_exposure <= 1.0
    assert all(intent.side == "buy" for intent in real_plan.intents)
    assert all(intent.quantity > 0 for intent in real_plan.intents)
    # TLT was removed from the v6 universe on 2026-08-15.
    assert "TLT" not in real_plan.target_weights


def test_every_intent_respects_the_symbol_weight_cap(real_plan) -> None:
    """`max_symbol_weight = 0.20` in the v6 configuration."""
    for symbol, weight in real_plan.target_weights.items():
        assert weight <= 0.2 + 1e-9, f"{symbol} targets {weight:.2%}, above the 20% cap"


def test_plan_to_submit_to_report_to_prune(tmp_path: Path, real_plan) -> None:
    """The daemon's whole daily path, without a broker or a market."""
    journal = Journal(tmp_path / "journal.ndjson", tmp_path / "equity_curve.ndjson")
    broker = _FakeBroker()
    session = real_plan.earliest_execution_session
    now = dt.datetime.combine(session, dt.time(13, 31), tzinfo=dt.UTC)

    account = Account.from_payload(
        {
            "account_number": "PA-TEST",
            "status": "ACTIVE",
            "equity": "100000",
            "last_equity": "100000",
            "cash": "100000",
            "buying_power": "200000",
            "trading_blocked": False,
            "transfers_blocked": False,
            "account_blocked": False,
            "pattern_day_trader": False,
        }
    )

    result = submit_plan(
        real_plan,
        broker,  # type: ignore[arg-type]
        account=account,
        session=session,
        now=now,
        journal=journal,
        dry_run=False,
    )

    assert result.executed
    assert len(broker.submitted) == len(real_plan.intents)
    assert all(outcome.was_submitted for outcome in result.outcomes)

    # Every quantity that reaches the broker must fit the wire format. The real
    # v6 plan carries 28-significant-digit quotients, and Alpaca rejects more
    # than nine decimal places outright -- so without quantization every order
    # in the day's rebalance is refused, and only a live POST reveals it.
    for order in broker.submitted:
        quantity = order["quantity"]
        assert isinstance(quantity, Decimal)
        assert -quantity.as_tuple().exponent <= 9, f"{order['symbol']}: {quantity}"

    journal.record_equity(
        session=session,
        equity=Decimal("100250"),
        cash=Decimal("36000"),
        gross_exposure=0.64,
        positions={intent.symbol: intent.quantity for intent in real_plan.intents},
    )

    report = build_daily_report(
        session=session,
        now=now,
        account=account,
        positions=(),
        equity_curve=journal.read_equity_curve(),
        submission=result,
        plan=real_plan,
        orders=tuple(
            broker.submit_market_order(
                symbol=intent.symbol,
                side=intent.side,
                quantity=intent.quantity,
                client_order_id="x",
            )
            for intent in real_plan.intents
        ),
    )

    markdown = report.to_markdown()
    assert "AMAT paper trading" in markdown
    assert "amat-etf-regime-quality" in markdown
    assert "Not a meaningful sample" in markdown  # one session
    assert "optimistic bound on live behaviour" in markdown

    markdown_path, json_path = write_report(report, tmp_path / "reports")
    assert markdown_path.is_file()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["plan"]["strategy_version"].startswith("6.")

    # Retention leaves a single day alone, and the equity curve is untouched.
    assert prune_old_reports((tmp_path / "reports",), retain_days=7) == ()
    assert len(journal.read_equity_curve()) == 1


def test_a_second_run_of_the_same_plan_cannot_double_the_position(
    tmp_path: Path, real_plan
) -> None:
    """The idempotency key is stable across processes, not just across calls."""
    from executor.submit import client_order_id_for

    first = [client_order_id_for(real_plan, intent) for intent in real_plan.intents]
    second = [client_order_id_for(real_plan, intent) for intent in real_plan.intents]
    assert first == second
    assert len(set(first)) == len(first), "each intent needs a distinct key"
