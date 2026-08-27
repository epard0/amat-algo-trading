"""The bot's half of the paper/live switch.

The dashboard can only ask. Everything that decides whether the ask is honoured
lives here, so these are the tests that stand between a web request and a real
order.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

import bot as bot_module
from bot import KalshiCryptoBot
from execution.decision_log import DecisionLog
from execution.mode_control import ModeControl
from execution.trade_ledger import TradeLedger


class FakeClient:
    """Records that it was closed, so a leaked session shows up as a failure."""

    instances: list["FakeClient"] = []

    def __init__(self, settings, authenticated: bool) -> None:
        self.settings = settings
        self.authenticated = authenticated
        self.closed = False
        FakeClient.instances.append(self)

    async def close(self) -> None:
        self.closed = True

    async def get_available_balance(self) -> Decimal:
        return Decimal("42.00")


def make_bot(
    tmp_path: Path,
    *,
    live: bool = False,
    live_error: str | None = None,
    control_enabled: bool = True,
) -> KalshiCryptoBot:
    def validate_live(cli_live: bool) -> None:
        if cli_live and live_error:
            raise RuntimeError(live_error)

    instance = object.__new__(KalshiCryptoBot)
    instance.live = live
    instance.mode = "live" if live else "paper"
    instance.silent = True
    instance.settings = SimpleNamespace(
        environment="production",
        validate_live=validate_live,
        paper_balance_usd=Decimal("20"),
        mode_control_enabled=control_enabled,
        mode_control_path=tmp_path / "runtime_mode.json",
    )
    instance.client = FakeClient(instance.settings, authenticated=live)
    instance.ledger = TradeLedger(tmp_path / "ledger.db", Decimal("20"))
    instance.decisions = DecisionLog(tmp_path / "decisions.jsonl")
    instance.mode_control = (
        ModeControl(tmp_path / "runtime_mode.json") if control_enabled else None
    )
    instance._last_mode_report = 0.0
    instance._cooldowns = {}
    instance._halt_reason = None
    instance._peak_balance = Decimal("0")
    return instance


@pytest.fixture(autouse=True)
def fake_client(monkeypatch):
    FakeClient.instances = []
    monkeypatch.setattr(bot_module, "AsyncKalshiTradingClient", FakeClient)
    return FakeClient


# ------------------------------------------------------- prerequisite read --

def test_prerequisites_are_none_when_live_is_configured(tmp_path) -> None:
    assert make_bot(tmp_path)._live_prerequisites() is None


def test_prerequisites_return_the_reason_verbatim(tmp_path) -> None:
    """The dashboard shows this string, so it has to be the real one."""
    reason = "Live trading requires ENABLE_LIVE_TRADING=true in .env"
    instance = make_bot(tmp_path, live_error=reason)

    assert instance._live_prerequisites() == reason


# ------------------------------------------------------------- heartbeats ---

def test_reporting_records_what_is_running(tmp_path) -> None:
    instance = make_bot(tmp_path)

    instance._report_mode(force=True)

    state = instance.mode_control.read()
    assert state.active == "paper"
    assert state.live_available is True


def test_reporting_carries_the_reason_live_is_unavailable(tmp_path) -> None:
    instance = make_bot(tmp_path, live_error="key missing")

    instance._report_mode(force=True)

    state = instance.mode_control.read()
    assert state.live_available is False
    assert state.note == "key missing"


def test_reports_are_throttled(tmp_path) -> None:
    """One fsync per poll interval, for a value that changes twice a day."""
    instance = make_bot(tmp_path)
    instance._report_mode(force=True)
    first = instance.mode_control.read().active_at

    instance._report_mode()

    assert instance.mode_control.read().active_at == first


def test_a_control_file_that_cannot_be_written_does_not_stop_trading(
    tmp_path, monkeypatch
) -> None:
    instance = make_bot(tmp_path)

    def boom(*_args, **_kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(instance.mode_control, "report_active", boom)

    instance._report_mode(force=True)  # must not raise


# ---------------------------------------------------------------- switches --

async def test_a_matching_request_changes_nothing(tmp_path) -> None:
    instance = make_bot(tmp_path)
    instance.mode_control.request("paper", by="test")
    original = instance.client

    await instance._apply_requested_mode()

    assert instance.mode == "paper"
    assert instance.client is original


async def test_switching_to_live_rebuilds_an_authenticated_client(tmp_path) -> None:
    instance = make_bot(tmp_path)
    instance.mode_control.request("live", by="dashboard")
    original = instance.client

    await instance._apply_requested_mode()

    assert instance.mode == "live"
    assert instance.live is True
    assert original.closed is True          # no leaked aiohttp session
    assert instance.client is not original
    assert instance.client.authenticated is True


async def test_switching_back_to_paper_drops_authentication(tmp_path) -> None:
    instance = make_bot(tmp_path, live=True)
    instance.mode_control.request("paper", by="dashboard")

    await instance._apply_requested_mode()

    assert instance.mode == "paper"
    assert instance.live is False
    assert instance.client.authenticated is False


async def test_a_switch_resets_per_book_state(tmp_path) -> None:
    """Peak balance and cooldowns belong to one book, not to the process.

    Carrying a paper drawdown peak into the live book would arm the circuit
    breaker against a number that never had anything to do with real money.
    """
    instance = make_bot(tmp_path)
    instance._peak_balance = Decimal("1000")
    instance._cooldowns = {"KXBTC15M-X": 123.0}
    instance.mode_control.request("live", by="dashboard")

    await instance._apply_requested_mode()

    assert instance._peak_balance == Decimal("0")
    assert instance._cooldowns == {}


async def test_a_switch_is_written_to_the_decision_log(tmp_path) -> None:
    instance = make_bot(tmp_path)
    instance.mode_control.request("live", by="dashboard@127.0.0.1")

    await instance._apply_requested_mode()

    records = instance.decisions.read_all()
    switch = [r for r in records if r["record_type"] == "mode_switch"]
    assert switch and switch[0]["previous"] == "paper"
    assert switch[0]["active"] == "live"
    assert switch[0]["requested_by"] == "dashboard@127.0.0.1"


# ---------------------------------------------------------------- refusals --

async def test_live_is_refused_when_the_process_cannot_trade_live(tmp_path) -> None:
    """The file cannot grant a permission .env withheld."""
    instance = make_bot(tmp_path, live_error="ENABLE_LIVE_TRADING is false")
    instance.mode_control.request("live", by="dashboard")

    await instance._apply_requested_mode()

    assert instance.mode == "paper"
    assert instance.live is False
    assert instance.client.authenticated is False


async def test_a_refused_request_is_withdrawn(tmp_path) -> None:
    """Otherwise it re-refuses every cycle and buries the reason in a log loop."""
    instance = make_bot(tmp_path, live_error="ENABLE_LIVE_TRADING is false")
    instance.mode_control.request("live", by="dashboard")

    await instance._apply_requested_mode()

    state = instance.mode_control.read()
    assert state.requested == "paper"
    assert state.note == "ENABLE_LIVE_TRADING is false"


async def test_a_refusal_is_recorded_with_its_reason(tmp_path) -> None:
    instance = make_bot(tmp_path, live_error="key file is world readable")
    instance.mode_control.request("live", by="dashboard")

    await instance._apply_requested_mode()

    refusals = [
        r for r in instance.decisions.read_all()
        if r["record_type"] == "mode_switch_refused"
    ]
    assert refusals and refusals[0]["reason"] == "key file is world readable"


async def test_disabling_control_ignores_the_file_entirely(tmp_path) -> None:
    """MODE_CONTROL_ENABLED=false nails the switch shut for the process's life."""
    instance = make_bot(tmp_path, control_enabled=False)
    ModeControl(tmp_path / "runtime_mode.json").request("live", by="dashboard")

    await instance._apply_requested_mode()
    instance._report_mode(force=True)

    assert instance.mode == "paper"
    assert ModeControl(tmp_path / "runtime_mode.json").read().active is None


# ------------------------------------------- collaborators after a switch --


def _bot_with_engines(tmp_path):
    """A bot carrying both engines, built without __init__ as the tests do."""
    from types import SimpleNamespace

    import bot as bot_module
    from execution.position_book import PositionBook
    from execution.salvage_exit import SalvageExit
    from execution.scalp_engine import ScalpEngine
    from execution.take_profit import TakeProfitExit

    instance = object.__new__(bot_module.KalshiCryptoBot)
    instance.mode = "live"
    instance.live = True
    instance.client = SimpleNamespace(name="OLD")
    settings = SimpleNamespace(environment="prod", scalp_max_concurrent=9,
                               salvage_window_seconds=30)
    decisions = SimpleNamespace(record_event=lambda *a, **k: None)
    positions = PositionBook(max_concurrent={"scalper": 9})
    instance.scalp = ScalpEngine(
        settings=settings, client=instance.client, ledger=None,
        decisions=decisions, positions=positions, mode="live", live=True,
    )
    instance.salvage = SalvageExit(
        settings=settings, client=instance.client, ledger=None,
        decisions=decisions, positions=positions, mode="live", live=True,
    )
    instance.take_profit = TakeProfitExit(
        settings=settings, client=instance.client, ledger=None,
        decisions=decisions, positions=positions, mode="live", live=True,
    )
    return instance


def test_every_collaborator_follows_the_bot_across_a_switch(tmp_path) -> None:
    """The bug behind ``RuntimeError: Session is closed``.

    The switch closes the old client and builds a new one. Engines captured the
    old object, so they kept a **closed aiohttp session** and every order failed.
    """
    instance = _bot_with_engines(tmp_path)
    from types import SimpleNamespace

    instance.client = SimpleNamespace(name="NEW")
    instance.mode = "paper"
    instance.live = False
    instance._rebind_collaborators()

    for name in instance._COLLABORATORS:
        engine = getattr(instance, name)
        assert engine.client.name == "NEW", f"{name} kept the closed client"


def test_the_stale_live_flag_is_the_dangerous_half(tmp_path) -> None:
    """The crash hid this one. After a switch to paper the scalper still
    believed live=True, so it would have taken the real order path and written
    live ledger rows while the banner said PAPER. Only the dead session stopped
    it spending money.
    """
    instance = _bot_with_engines(tmp_path)
    from types import SimpleNamespace

    instance.client = SimpleNamespace(name="NEW")
    instance.mode = "paper"
    instance.live = False
    instance._rebind_collaborators()

    for name in instance._COLLABORATORS:
        engine = getattr(instance, name)
        assert engine.live is False, f"{name} still thinks it is trading live"
        assert engine.mode == "paper", f"{name} would write {engine.mode} rows"


def test_no_collaborator_is_left_out_of_the_rebind(tmp_path) -> None:
    """A drift guard for the next engine somebody adds.

    Walks the bot's own attributes looking for anything that holds a client and
    a live flag, and fails if it is not in _COLLABORATORS. The original bug was
    not that rebinding was hard - it was that nothing noticed it was missing.
    """
    instance = _bot_with_engines(tmp_path)
    holders = {
        name for name, value in vars(instance).items()
        if not name.startswith("_")
        and hasattr(value, "client")
        and hasattr(value, "live")
        and hasattr(value, "rebind")
    }
    missing = holders - set(instance._COLLABORATORS)
    assert not missing, (
        f"these hold a client but are never rebound on a mode switch: {missing}"
    )


async def test_the_switch_itself_rebinds_and_not_merely_the_helper(tmp_path) -> None:
    """Exercise `_apply_requested_mode`, not `_rebind_collaborators`.

    A test that calls the helper directly still passes when the *call site* is
    deleted, which is how five features in this project shipped correct and
    unreachable. This one drives the real switch.
    """
    from types import SimpleNamespace

    from execution.mode_control import ModeControl

    instance = _bot_with_engines(tmp_path)
    control = ModeControl(tmp_path / "runtime_mode.json")
    control.request("paper", by="test")

    instance.mode_control = control
    instance.settings = SimpleNamespace(environment="prod")
    instance.decisions = SimpleNamespace(record_event=lambda *a, **k: None)
    instance._live_prerequisites = lambda: None
    instance._report_mode = lambda **k: None
    instance._log_paper_summary = lambda: None
    instance._peak_balance = Decimal("0")
    instance._cooldowns = {}

    closed = []

    class OldClient:
        name = "OLD"

        async def close(self):
            closed.append(True)

    instance.client = OldClient()
    instance.scalp.client = instance.client
    instance.salvage.client = instance.client
    instance.take_profit.client = instance.client

    built = []

    import bot as bot_module

    original = bot_module.AsyncKalshiTradingClient
    bot_module.AsyncKalshiTradingClient = lambda *a, **k: built.append(1) or SimpleNamespace(name="NEW")
    try:
        await instance._apply_requested_mode()
    finally:
        bot_module.AsyncKalshiTradingClient = original

    assert closed == [True], "the old client must be closed"
    assert instance.mode == "paper" and instance.live is False
    # The point of the test: the engines followed, via the switch itself.
    for name in instance._COLLABORATORS:
        engine = getattr(instance, name)
        assert engine.client.name == "NEW", f"{name} kept the closed client"
        assert engine.live is False and engine.mode == "paper"
