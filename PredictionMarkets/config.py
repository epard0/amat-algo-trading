"""Validated runtime configuration for the Kalshi crypto trading bot."""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


PRODUCTION_REST_URL = "https://external-api.kalshi.com/trade-api/v2"
DEMO_REST_URL = "https://external-api.demo.kalshi.co/trade-api/v2"
PRODUCTION_WS_URL = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
DEMO_WS_URL = "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2"

# Every supported 15-minute series. Commodity tickers were verified against
# Kalshi's GOLD/SILVER/WTI 15-minute listings on 24 Aug 2026.
# two maps are the single source of truth for "which assets exist": the history
# reporter, the monitoring chart, the backfill CLI and the model fitter all
# derive their asset lists from here rather than repeating one.
#
# Listing an asset here does NOT trade it. TRADING_ASSETS does, and it defaults
# to all of them - so an operator who never set it will pick up new series on
# upgrade, which is the intended behaviour for a bot whose whole edge is
# breadth across simultaneous 15-minute intervals.
SERIES_BY_ASSET = {
    "BTC": "KXBTC15M",
    "ETH": "KXETH15M",
    "SOL": "KXSOL15M",
    "HYPE": "KXHYPE15M",
    "DOGE": "KXDOGE15M",
    "XRP": "KXXRP15M",
    "BNB": "KXBNB15M",
    "NEAR": "KXNEAR15M",
    "ZEC": "KXZEC15M",
    "GOLD": "KXGOLD15M",
    "SILVER": "KXSILVER15M",
    "WTI": "KXWTI15M",
}

# Kraken *altnames*, which is what /0/public/Trades and /0/public/OHLC accept.
# Note the response key is not always the request pair - XBTUSD answers under
# XXBTZUSD, XRPUSD under XXRPZUSD, ZECUSD under XZECZUSD - so every reader here
# takes the single key out of the result rather than looking up the pair it
# asked for.
KRAKEN_PAIR_BY_ASSET = {
    "BTC": "XBTUSD",
    "ETH": "ETHUSD",
    "SOL": "SOLUSD",
    "HYPE": "HYPEUSD",
    "DOGE": "DOGEUSD",
    "XRP": "XRPUSD",
    "BNB": "BNBUSD",
    "NEAR": "NEARUSD",
    "ZEC": "ZECUSD",
    # Yahoo continuous futures symbols. Runtime routing sends only these three
    # through Yahoo; Kraken never sees them.
    "GOLD": "GC=F",
    "SILVER": "SI=F",
    "WTI": "CL=F",
}


# This file's own directory. Every path default is resolved against it unless
# the caller passes a root explicitly.
PROJECT_ROOT = Path(__file__).resolve().parent


def config_file(project_root: Path | None = None) -> Path:
    """Where this process should read its `.env` from.

    An installed copy keeps its configuration outside the install directory, so
    that `kx15 update` — which replaces that directory wholesale — cannot take
    the operator's credentials with it. The launcher points KX15_STATE_DIR at
    the config directory and `cd`s into it.

    This lives here, rather than in `bot.py`, because every tool that builds a
    `KalshiSettings` needs the same answer. It used to be `bot.py`'s alone, and
    `kalshi_history.py` and `view_paper_trades.py` each hardcoded
    ``PROJECT_ROOT / ".env"`` instead — which for an installed copy names a file
    that by design never exists. Nothing raises: `load_dotenv` on a missing path
    is a silent no-op, so those tools came up on pure defaults, reporting the
    **demo** exchange while the bot beside them traded production.
    """
    root = project_root or PROJECT_ROOT
    for candidate in (
        Path(os.environ["KX15_STATE_DIR"]) / ".env"
        if os.getenv("KX15_STATE_DIR")
        else None,
        Path.cwd() / ".env",
        root / ".env",
    ):
        if candidate is not None and candidate.is_file():
            return candidate
    return root / ".env"


def load_environment(project_root: Path | None = None) -> Path:
    """Load `.env` into the process, once, and return the file used.

    **Every entry point that builds a `KalshiSettings` must call this**, and
    `tests/test_environment_loading.py` fails if one does not. Loading is not
    done inside `from_env()` on purpose: `.env` on a developer machine carries
    Fingerprint and developer-licence secrets, and a test suite that reads them
    would pass for the seller and fail for a buyer.

    `load_dotenv` never overrides a variable that is already set, so an explicit
    ``SALVAGE_ENABLED=true python bot.py`` still wins over the file.
    """
    from dotenv import load_dotenv

    path = config_file(project_root)
    load_dotenv(path)
    return path


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _decimal_env(name: str, default: str) -> Decimal:
    try:
        value = Decimal(os.getenv(name, default))
    except Exception as exc:
        raise ValueError(f"{name} must be a decimal number") from exc
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
    return value


def _check_key_permissions(path: Path) -> None:
    """Refuse a private key that anyone but its owner can read.

    On POSIX this is the mode bits. On Windows it cannot be: Python synthesises
    st_mode from the read-only attribute alone and reports 0o666 (or 0o444)
    whatever the ACL says, so the POSIX test refused every key file on Windows
    and made live trading impossible there regardless of how the file was
    secured. The real access control on Windows is the DACL, so that is what
    gets read — via icacls, which is present on every supported version and
    avoids a pywin32 dependency for one check.

    A machine where the check itself cannot run is not thereby safe: an
    unreadable ACL is a refusal, not a pass.
    """
    if os.name != "nt":
        if path.stat().st_mode & 0o077:
            raise RuntimeError(
                f"Kalshi private key permissions are too broad; run chmod 600 {path}"
            )
        return

    import getpass
    import subprocess

    try:
        listing = subprocess.run(
            ["icacls", str(path)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            f"Could not read the permissions on {path} ({exc}). Live trading "
            "will not start until they can be verified."
        ) from exc

    user = getpass.getuser().lower()
    # Trustees that are the owner, and trustees that are everybody else. The
    # built-in administrators group and SYSTEM are unavoidable on Windows and
    # are not what this check is about; a key readable by Users or Everyone is.
    broad = (
        "everyone",
        "users",
        "authenticated users",
        "interactive",
        "\\users",
        "todos",
        "jeder",
    )
    offenders = [
        line.strip()
        for line in listing.splitlines()
        if line.strip()
        and any(name in line.lower() for name in broad)
        and user not in line.lower()
    ]
    if offenders:
        raise RuntimeError(
            f"Kalshi private key {path} is readable beyond its owner:\n  "
            + "\n  ".join(offenders)
            + "\nLock it down with:\n"
            f'  icacls "{path}" /inheritance:r /grant:r "%USERNAME%:R"'
        )


@dataclass(frozen=True)
class KalshiSettings:
    """All settings needed by the bot.

    Live execution intentionally has two gates: ``ENABLE_LIVE_TRADING=true`` in
    the environment and the explicit ``--live`` command-line flag.
    """

    environment: str
    rest_url: str
    websocket_url: str
    api_key_id: str | None
    private_key_path: Path
    public_key_path: Path
    subaccount: int
    enable_live_trading: bool
    live_capital_limit_usd: Decimal
    position_fraction: Decimal
    paper_balance_usd: Decimal
    poll_interval_seconds: float
    entry_window_seconds: int
    minimum_seconds_to_close: int
    min_probability: Decimal
    min_edge: Decimal
    max_slippage: Decimal
    fee_reserve_per_contract: Decimal
    assets: tuple[str, ...]
    ledger_path: Path
    trade_log_path: Path
    status_check_lead_seconds: int
    settlement_poll_seconds: float
    # Fields below have defaults so existing callers/tests stay valid.
    # One fixed dollar budget is used by every strategy in paper and live
    # modes. Probability is an admission signal, never a licence to bet more.
    trade_budget_usd: Decimal = Decimal("50")
    kelly_fraction: Decimal = Decimal("0.25")
    max_model_market_divergence: Decimal = Decimal("0.125")
    # Execution-time plausibility guards. Defaulted ON: a caller that forgets
    # them gets the safe behaviour, not an unguarded order path.
    # Kept in step with the MAX_EXECUTABLE_EDGE default in from_env below. If
    # the two drift apart, a direct construction (tests, any non-env caller)
    # silently runs a different ceiling than the live bot does.
    max_edge: Decimal = Decimal("0.12")
    max_quote_improvement: Decimal = Decimal("0.10")
    # Late-market entry. Inside the final `late_entry_seconds` the edge floor
    # drops and the probability floor rises, gated on settlement margin.
    # Set late_entry_seconds to 0 to disable the window entirely.
    late_entry_seconds: int = 60
    late_min_probability: Decimal = Decimal("0.85")
    late_min_edge: Decimal = Decimal("0.02")
    late_min_settlement_sigma: Decimal = Decimal("1.50")
    # Rank independently confirmed tail contracts ahead of ordinary edge-only
    # candidates without suppressing the fallback strategy while evidence is small.
    consensus_tail_preferred: bool = True
    consensus_tail_min_raw_probability: Decimal = Decimal("0.97")
    consensus_tail_min_market_price: Decimal = Decimal("0.85")
    consensus_tail_min_edge: Decimal = Decimal("0.02")
    consensus_tail_min_settlement_sigma: Decimal = Decimal("2.0")
    consensus_tail_require_indicator_alignment: bool = True
    # Optional fitted regime mixture (see core/strategy_brain/strategies/
    # regime_model.py). None means the hand-tuned classifier is used, which is
    # the default: the learned path only engages when an artifact is supplied.
    regime_model_path: Path | None = None
    commodity_model_path: Path | None = Path("models/commodity_directional.json")
    max_candle_age_seconds: int = 120
    order_failure_cooldown_seconds: int = 60
    max_consecutive_losses: int = 3
    max_drawdown_fraction: Decimal = Decimal("0.60")
    # Optional absolute UTC-day loss limit. Zero disables only this additional
    # rail; consecutive-loss and drawdown breakers remain active.
    max_daily_loss_usd: Decimal = Decimal("0")
    min_contracts: Decimal = Decimal("1")
    request_concurrency: int = 4
    # Smooth bursts across many assets. Concurrency caps in-flight requests but
    # does not cap requests/second when responses are fast; this interval does.
    kalshi_request_min_interval_seconds: float = 0.35
    decision_log_path: Path = Path("decisions.jsonl")
    decision_log_enabled: bool = True
    # Hours before the decision log is discarded. 0 keeps everything.
    decision_log_retention_hours: float = 24.0
    regime_aware_model: bool = True
    # Where the dashboard leaves a paper/live request and the bot reports what
    # it is actually running. See execution/mode_control.py.
    mode_control_path: Path = Path("runtime_mode.json")
    # The bot's half of that channel. False makes the process ignore the file
    # entirely, so the mode it was started with is the mode it keeps for its
    # whole life - the right setting for an unattended box where nobody should
    # be able to change what it trades without an ssh session.
    mode_control_enabled: bool = True
    # Where the human-readable run log and the PDF performance reports land.
    # Deliberately NOT the state directory: the ledger is the machine's record
    # and belongs beside the config, whereas these two are for a person to read,
    # so they go somewhere a person will find them.
    log_dir: Path = Path("kx15_Performance_Logs")
    # Rotate at half a gigabyte. The weekly Sunday-00:00 rotation is the normal
    # trigger; this is the backstop for a week that produces an unusual amount.
    log_max_bytes: int = 512 * 1024 * 1024
    # Console verbosity for the run log. "quiet" prints trades, settlements,
    # halts and errors; "verbose" restores the old per-evaluation commentary.
    # Seconds between console scan lines. Events - a trade, a settlement,
    # a halt - are never throttled, and the file keeps every line at DEBUG.
    scan_log_interval_seconds: float = 10.0
    log_console_level: str = "quiet"
    # Whether the bot writes a PDF performance report of its own accord. The
    # `kx15 report` command works regardless; this only governs the unattended
    # one at the end of a paper session.
    reports_enabled: bool = True

    # Normalized training/analysis capture. Every configured asset is retained;
    # depth comes from the canonical WebSocket cache and a slow independent REST
    # reconciliation rather than blocking the trading loop asset by asset.
    market_data_enabled: bool = True
    orderbook_recording_assets: tuple[str, ...] = tuple(SERIES_BY_ASSET)
    orderbook_rest_reconcile_seconds: float = 30.0
    # The websocket is still consumed at full rate to maintain the executable
    # in-memory book. Persisting every delta is a separate concern: on active
    # markets it can exceed 1,000 SQLite rows/second and starve the asyncio
    # loop that owns discovery, balance reads, exits, and order submission.
    # Periodic full-depth market_snapshots remain enabled for every asset.
    raw_book_event_recording_enabled: bool = False
    # Public prints are useful training data and much lower volume than book
    # deltas. They are buffered into one durable batch per second by the ledger
    # rather than committing once per websocket message.
    public_trade_recording_enabled: bool = True

    # Executable profit capture. This is price-based, not the noisy model-
    # reversal exit that was removed: the held outcome must be saleable for a
    # net gain after the exit fee, with enough depth for a complete exit.
    take_profit_enabled: bool = True
    take_profit_per_contract: Decimal = Decimal("0.05")
    # Avoid clipping a high-confidence winner after capturing only a small
    # fraction of its available $1 settlement upside.
    take_profit_min_capture_fraction: Decimal = Decimal("0.75")
    take_profit_confidence_margin: Decimal = Decimal("0.02")
    # Once the book is exceptionally strong, or the executable market and our
    # model agree that the held side is winning, routine take-profit cannot clip
    # the position. Zero seconds means this veto applies for the full window.
    take_profit_winning_hold_enabled: bool = True
    take_profit_winning_hold_min_bid: Decimal = Decimal("0.85")
    take_profit_winning_hold_min_probability: Decimal = Decimal("0.65")
    take_profit_winning_hold_strong_book_bid: Decimal = Decimal("0.95")
    take_profit_winning_hold_max_seconds_to_close: int = 0
    take_profit_winning_hold_confirmations: int = 2
    # Reversal evidence remains available to the strategy lab, but may not
    # liquidate a production position.  It produced a false SILVER exit on
    # 2026-08-25, turning a winning settlement into a realized loss.
    confirmed_reversal_enabled: bool = False
    confirmed_reversal_confirmations: int = 2
    confirmed_reversal_min_signals: int = 3
    confirmed_reversal_probability_max: Decimal = Decimal("0.48")
    confirmed_reversal_peak_drawdown: Decimal = Decimal("0.15")
    take_profit_max_spread: Decimal = Decimal("0.03")
    take_profit_min_seconds_to_close: int = 30
    take_profit_check_interval_seconds: float = 15.0
    take_profit_fast_window_seconds: int = 120
    take_profit_fast_check_seconds: float = 5.0
    # Sequenced venue books trigger an immediate reassessment; the timer above
    # remains the fallback. A small throttle collapses bursty deltas without
    # letting a 15-second poll miss a briefly executable exit.
    kalshi_websocket_enabled: bool = True
    kalshi_websocket_stale_seconds: float = 10.0
    event_reassessment_min_interval_seconds: float = 0.5
    # Entry throughput controls. Book events re-run the ordinary predictor
    # gates for one cached current market; they never own a looser signal path.
    event_driven_entry_enabled: bool = True
    event_entry_min_interval_seconds: float = 2.0
    predictor_max_concurrent: int = 2
    predictor_max_candidates_per_cycle: int = 2
    # Initial entry plus at most one opposite-side re-entry after a profitable
    # exit. Same-side churn is always refused.
    max_entries_per_market: int = 2
    safe_mode_enabled: bool = True
    safe_mode_loss_trigger: int = 3
    safe_mode_min_probability: Decimal = Decimal("0.92")
    safe_mode_min_market_price: Decimal = Decimal("0.85")
    safe_mode_min_settlement_sigma: Decimal = Decimal("2.0")
    safe_mode_consensus_only: bool = True

    # Paper-only A/B laboratory. Each policy owns an independent wallet in a
    # separate strict database and sees the same observations.
    strategy_lab_enabled: bool = True
    strategy_lab_path: Path = Path("strategy_lab.db")
    strategy_lab_balance_usd: Decimal = Decimal("100")

    # Kalshi-only complementary-pair shadow scanner. It is isolated from the
    # directional strategies and never owns a live order path.
    pair_arb_enabled: bool = False
    pair_arb_min_net_edge: Decimal = Decimal("0.02")
    pair_arb_min_leg_price: Decimal = Decimal("0.02")

    # --- Scalping mode (maker-side quoting on the favourite band) -------------
    # Ships off. Enabling it grants no live permission of its own: live trading
    # still requires both ENABLE_LIVE_TRADING=true and --live.
    scalp_enabled: bool = False
    # The predictor's own switch. The default CLI runs predictor and scalper
    # together; ledger strategy tags and the paper lab's independent wallets
    # retain attribution while the production paths share capital/risk rails.
    predictor_enabled: bool = True
    # Price-based take-profit is the default exit. Model-only reversal remains
    # experimental in the paper strategy lab because a prior live reversal
    # converted transient noise into a realised loss and round-trip fee.
    # The favourite band. The floor is the win-rate constraint expressed as a
    # price: break-even needs price+fee, so a 0.60 floor means any EV-positive
    # strategy must win more than 60% of the time. Seeded from Bürgi, Deng &
    # Whelan (2026) over 313,972 contracts, NOT fitted to this bot's own history
    # — every quote is logged with what it would take to re-fit it.
    scalp_band_low: Decimal = Decimal("0.70")
    scalp_band_high: Decimal = Decimal("0.92")
    scalp_min_ev_per_dollar: Decimal = Decimal("0.015")
    scalp_max_spread: Decimal = Decimal("0.02")
    scalp_min_book_depth: Decimal = Decimal("100")
    scalp_quote_ttl_seconds: float = 20.0
    scalp_requote_ticks: int = 2
    # One slot per traded asset: every market that clears the band should be
    # able to carry a quote, rather than the first three to qualify.
    scalp_max_concurrent: int = 9
    # How far above the EV floor a setup must sit before it is worth paying
    # the spread for. Maker-heavy by construction: 1.0 would cross on every
    # expired quote and make this the taker strategy it exists to improve on.
    scalp_taker_edge_multiple: Decimal = Decimal("2.0")
    scalp_max_capital_fraction: Decimal = Decimal("0.50")
    scalp_taker_fallback: bool = True
    # "taker" crosses the spread immediately; "maker" rests a post_only quote
    # and falls back to crossing. Taker is the default because five paper
    # sessions produced zero maker fills, and Becker measures the maker-taker
    # gap in the Finance category - where 15-minute crypto sits - at 0.17pp.
    scalp_order_style: str = "taker"
    # Seconds before close inside which a new quote is refused: a fill there
    # could not be exited, which is the risk the exit path exists to remove.
    scalp_min_seconds_to_close: int = 120
    # --- Pattern gates: only quote structure the classifier can name ------
    # VOLATILE_CHOP is, by its own definition, the regime where the shape
    # carries no information. Quoting it is quoting noise.
    scalp_blocked_regimes: tuple[str, ...] = ("VOLATILE_CHOP",)
    # 0.0 refuses only malformed (negative) assessments. See check_pattern:
    # a higher floor refuses the calm books this strategy wants.
    scalp_min_regime_confidence: Decimal = Decimal("0.0")
    # Minimum |z| between spot and the strike. A contract priced near the
    # middle is a coin flip whatever the regime says, and it is where the
    # settlement index can disagree with our feed and decide the outcome.
    scalp_min_abs_z: Decimal = Decimal("0.50")
    # Seconds before the end of a bounded session inside which no NEW
    # position is opened, because nothing will be watching it settle.
    scalp_session_tail_seconds: int = 900
    # --- Order-book microstructure (Bartlett & O'Hara 2026; Becker) ----------
    # Reading the book before trading. Off means the strategy trades on price
    # and model alone, as it did through session 5.
    scalp_orderbook_enabled: bool = True
    # One-sided resting liquidity is the static proxy for VPIN order-flow
    # toxicity, which Bartlett & O'Hara find predicts maker losses in
    # single-name markets. Above this, stand aside.
    scalp_max_toxicity: Decimal = Decimal("0.85")
    # Refuse a scalp whose immediately executable round trip could lose more
    # than this before any forecast error. This is a per-trade dollar bound.
    scalp_max_modeled_exit_loss_usd: Decimal = Decimal("1.18")
    # Weight on the resting-depth adjustment. ZERO because the SIGN is
    # unresolved: makers earn +1.12% (lean toward depth) but visible pressure
    # marks crowd overpayment (lean against it), and the published abstracts do
    # not settle which applies here. The imbalance is logged on every entry so
    # the sign can be fitted from our own fills. See orderbook.contrarian_bonus.
    scalp_crowd_weight: Decimal = Decimal("0.0")

    # --- Salvage exit (deep-longshot loss reduction, default OFF) ------------
    # One evaluation per position, SALVAGE_WINDOW_SECONDS before its market
    # closes: sell the whole position back into the book, or carry it. It is
    # PRICE-triggered and never consults the model, which is the entire
    # difference between it and the exit-on-reversal that was removed for
    # selling a winner. See strategies/salvage.py.
    salvage_enabled: bool = False
    salvage_window_seconds: int = 30
    # The longshot ceiling. Above this the favourite-longshot bias is not
    # measured, so selling costs fee plus spread and buys nothing.
    salvage_max_bid: Decimal = Decimal("0.15")
    # The exit is paid out of the spread; measured sensitivity shows the edge
    # gone by 3-4c even deep in the band.
    salvage_max_spread: Decimal = Decimal("0.02")
    # How far a longshot's price overstates its worth. Burgi, Deng & Whelan
    # (2026) measure >=60% at or below 10c over 313,972 contracts; 0.40 is
    # deliberately weaker than that, and the sample is 24h-10d markets rather
    # than 15-minute crypto.
    salvage_longshot_haircut: Decimal = Decimal("0.40")
    # Per contract. Stops a marginal case churning a position for a rounding.
    salvage_min_gain_per_contract: Decimal = Decimal("0.01")

    # --- Deprecated sizing inputs -------------------------------------------
    # Parsed so an old .env remains loadable, but no execution path reads them.
    # TRADE_BUDGET_USD is the only per-entry sizing input.
    sizing_confidence_gain: Decimal = Decimal("1.0")
    sizing_confidence_floor: Decimal = Decimal("0.60")
    sizing_confidence_full: Decimal = Decimal("0.90")
    sizing_max_fraction: Decimal = Decimal("0.20")

    # --- Exposure-scaled oversight (default OFF) -----------------------------
    # How closely a position is watched, from the share of the portfolio it is
    # risking. It changes WHEN and HOW OFTEN the salvage rule is asked, never
    # the price at which it acts - that stays SALVAGE_MAX_BID, the only region
    # where the overpricing was measured. See strategies/oversight.py.
    oversight_enabled: bool = False
    oversight_full_exposure: Decimal = Decimal("0.25")
    oversight_max_window_seconds: int = 300
    oversight_max_checks: int = 4
    oversight_min_spacing_seconds: float = 20.0
    # Recorded, never obeyed. Every check logs what this wider band WOULD have
    # sold, so "should we cut earlier?" becomes a question the settled data can
    # answer instead of one this file has to guess at.
    oversight_shadow_max_bid: Decimal = Decimal("0.35")
    # Time to cut. Below this many seconds to close no check is scheduled: an
    # exit is an atomic order that must reach the venue and fill. The floor
    # rises with exposure to leave a larger lot more transport runway.
    salvage_min_actionable_seconds: float = 0.0
    oversight_extra_actionable_seconds: float = 0.0

    # --- Kraken candle stream ------------------------------------------------
    # Streams the in-progress one-minute candle instead of re-polling every
    # asset every pass. REST stays underneath as the fallback: a stream that
    # dies quietly keeps a well-formed candle in memory that simply stops
    # advancing, which is the one failure polling does not have.
    # Credit a paper maker fill when the queue ahead has been consumed, not
    # only when the book trades THROUGH the price. Off by default: the paper
    # queue is inferred from resting size, which also falls on cancellations,
    # so it decays faster than a real price-time queue and would make paper
    # fills optimistic. `scalp_queue_position` records both regardless, and
    # live queue positions come from Kalshi itself - comparing them is what
    # would justify turning this on.
    scalp_queue_aware_fills: bool = False

    kraken_stream_enabled: bool = True
    kraken_stream_stale_seconds: float = 90.0

    # --- Size-scaled probability floor (default INERT) ----------------------
    # A position taking a larger share of the account must clear a higher
    # probability to be opened at all. Confidence scaling makes the good reads
    # bigger; this is what stops a merely-adequate read from becoming big.
    size_probability_uplift: Decimal = Decimal("0")
    size_probability_full_exposure: Decimal = Decimal("0.25")

    # --- Ladder arbitrage (daily strike ladder, secondary) --------------------
    # Off by default. The violations measured on the live ladder were the same
    # order of magnitude as spread plus fees, and a partially filled butterfly
    # is a naked directional position nothing in this design wanted to hold.
    ladder_arb_enabled: bool = False
    ladder_arb_series: str = "KXBTCD"
    ladder_arb_buffer: Decimal = Decimal("0.02")
    ladder_arb_max_contracts: Decimal = Decimal("25")

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> "KalshiSettings":
        root = (project_root or Path(__file__).resolve().parent).resolve()
        # Where mutable state goes when a path is relative. Defaults to the
        # source tree, which is right for a checkout; the installed launcher
        # points it at the user's config directory, because the install
        # directory is replaced wholesale by `kx15 update` and anything living
        # there - a ledger, a decision log - is deleted with it.
        state_root = Path(os.getenv("KX15_STATE_DIR") or root).expanduser().resolve()
        environment = os.getenv("KALSHI_ENVIRONMENT", "demo").strip().lower()
        if environment not in {"production", "demo"}:
            raise ValueError("KALSHI_ENVIRONMENT must be 'production' or 'demo'")

        if environment == "production":
            rest_url = os.getenv("KALSHI_PRODUCTION", PRODUCTION_REST_URL)
            websocket_url = os.getenv("KALSHI_WEBSOCKET_API_PROD", PRODUCTION_WS_URL)
        else:
            rest_url = os.getenv("KALSHI_DEMO", DEMO_REST_URL)
            websocket_url = os.getenv("KALSHI_WEBSOCKET_API_DEMO", DEMO_WS_URL)

        raw_assets = os.getenv("TRADING_ASSETS", ",".join(SERIES_BY_ASSET)).split(",")
        assets = tuple(
            dict.fromkeys(
                asset.strip().upper() for asset in raw_assets if asset.strip()
            )
        )
        unsupported = sorted(set(assets) - set(SERIES_BY_ASSET))
        if unsupported:
            raise ValueError(f"Unsupported TRADING_ASSETS: {', '.join(unsupported)}")

        position_fraction = _decimal_env("POSITION_FRACTION", "0.20")
        if not Decimal("0") < position_fraction <= Decimal("1"):
            raise ValueError("POSITION_FRACTION must be greater than 0 and at most 1")

        capital_limit = _decimal_env("LIVE_CAPITAL_LIMIT_USD", "10")
        if capital_limit <= 0:
            raise ValueError("LIVE_CAPITAL_LIMIT_USD must be greater than 0")

        # Fixed upper bound for every entry. Probability affects admission,
        # never size; depth and available cash may still clip below this cap.
        trade_budget = _decimal_env("TRADE_BUDGET_USD", "50")
        if trade_budget <= 0:
            raise ValueError("TRADE_BUDGET_USD must be greater than 0")

        min_probability = _decimal_env("MIN_MODEL_PROBABILITY", "0.72")
        if not Decimal("0.50") < min_probability < Decimal("1"):
            raise ValueError("MIN_MODEL_PROBABILITY must be between 0.50 and 1")

        min_edge = _decimal_env("MIN_EXECUTABLE_EDGE", "0.05")
        if not Decimal("0") < min_edge < Decimal("1"):
            raise ValueError("MIN_EXECUTABLE_EDGE must be between 0 and 1")

        # Ceiling on the executable edge. An edge larger than this against a
        # live book has, empirically, been a bad book read rather than an
        # opportunity: across the first 14 live trades, all four above 0.193
        # lost and all ten below 0.139 won. Anything in 0.14-0.19 separates the
        # observed set identically, so the exact value is not load-bearing —
        # the existence of a ceiling is. Set to 0 to disable it.
        max_edge = _decimal_env("MAX_EXECUTABLE_EDGE", "0.12")
        if not Decimal("0") <= max_edge < Decimal("1"):
            raise ValueError("MAX_EXECUTABLE_EDGE must be between 0 and 1")
        if max_edge and max_edge <= min_edge:
            raise ValueError(
                "MAX_EXECUTABLE_EDGE must exceed MIN_EXECUTABLE_EDGE, "
                f"got {max_edge} <= {min_edge}"
            )

        # How far the pre-order book re-read may improve on the discovery
        # snapshot before it is treated as a fault. Set to 0 to disable.
        max_quote_improvement = _decimal_env("MAX_QUOTE_IMPROVEMENT", "0.10")
        if not Decimal("0") <= max_quote_improvement <= Decimal("1"):
            raise ValueError("MAX_QUOTE_IMPROVEMENT must be between 0 and 1")

        entry_window_seconds = int(os.getenv("ENTRY_WINDOW_SECONDS", "180"))
        if entry_window_seconds <= 0:
            raise ValueError("ENTRY_WINDOW_SECONDS must be positive")

        # Late-market entry window. Inside this many seconds of close the model
        # is measuring a much tighter terminal distribution, so a smaller edge
        # is worth taking — provided the strike is far enough away in sigma
        # terms that index disagreement cannot decide the outcome.
        late_entry_seconds = int(os.getenv("LATE_ENTRY_SECONDS", "60"))
        if late_entry_seconds < 0:
            raise ValueError("LATE_ENTRY_SECONDS cannot be negative")
        if late_entry_seconds > entry_window_seconds:
            raise ValueError(
                "LATE_ENTRY_SECONDS must sit inside ENTRY_WINDOW_SECONDS, "
                f"got {late_entry_seconds} > {entry_window_seconds}"
            )

        late_min_probability = _decimal_env("LATE_MIN_PROBABILITY", "0.85")
        if not Decimal("0.50") < late_min_probability < Decimal("1"):
            raise ValueError("LATE_MIN_PROBABILITY must be between 0.50 and 1")

        late_min_edge = _decimal_env("LATE_MIN_EDGE", "0.02")
        if not Decimal("0") < late_min_edge < Decimal("1"):
            raise ValueError("LATE_MIN_EDGE must be between 0 and 1")

        late_min_settlement_sigma = _decimal_env("LATE_MIN_SETTLEMENT_SIGMA", "1.50")
        if late_min_settlement_sigma < 0:
            raise ValueError("LATE_MIN_SETTLEMENT_SIGMA cannot be negative")
        consensus_raw = _decimal_env("CONSENSUS_TAIL_MIN_RAW_PROBABILITY", "0.97")
        consensus_market = _decimal_env("CONSENSUS_TAIL_MIN_MARKET_PRICE", "0.85")
        consensus_edge = _decimal_env("CONSENSUS_TAIL_MIN_EDGE", "0.02")
        consensus_sigma = _decimal_env("CONSENSUS_TAIL_MIN_SETTLEMENT_SIGMA", "2.0")
        if not Decimal("0.50") < consensus_raw < Decimal("1"):
            raise ValueError(
                "CONSENSUS_TAIL_MIN_RAW_PROBABILITY must be between 0.50 and 1"
            )
        if not Decimal("0.50") < consensus_market < Decimal("1"):
            raise ValueError(
                "CONSENSUS_TAIL_MIN_MARKET_PRICE must be between 0.50 and 1"
            )
        if not Decimal("0") < consensus_edge < Decimal("1"):
            raise ValueError("CONSENSUS_TAIL_MIN_EDGE must be between 0 and 1")
        if consensus_sigma < 0:
            raise ValueError("CONSENSUS_TAIL_MIN_SETTLEMENT_SIGMA cannot be negative")

        regime_model_raw = os.getenv("REGIME_MODEL_PATH", "").strip()
        regime_model_path = None
        if regime_model_raw:
            candidate = Path(regime_model_raw)
            if not candidate.is_absolute():
                candidate = root / candidate
            if not candidate.is_file():
                raise ValueError(
                    f"REGIME_MODEL_PATH is set but no artifact exists at {candidate}"
                )
            regime_model_path = candidate

        commodity_model_raw = os.getenv(
            "COMMODITY_MODEL_PATH", str(root / "models/commodity_directional.json")
        ).strip()
        commodity_model_path = None
        if commodity_model_raw:
            candidate = Path(commodity_model_raw).expanduser()
            if not candidate.is_absolute():
                candidate = root / candidate
            # Missing is a supported pre-training state; commodity predictions
            # fall back to the transparent base model until an artifact exists.
            commodity_model_path = candidate if candidate.is_file() else None

        max_slippage = _decimal_env("MAX_SLIPPAGE_DOLLARS", "0.01")
        if not Decimal("0") <= max_slippage <= Decimal("0.10"):
            raise ValueError("MAX_SLIPPAGE_DOLLARS must be between 0 and 0.10")

        fee_reserve = _decimal_env("KALSHI_FEE_RESERVE_PER_CONTRACT", "0.02")
        if not Decimal("0") <= fee_reserve <= Decimal("0.10"):
            raise ValueError(
                "KALSHI_FEE_RESERVE_PER_CONTRACT must be between 0 and 0.10"
            )

        private_key = Path(
            os.getenv("KALSHI_PRIVATE_KEY_PATH", str(root / "private_key.pem"))
        ).expanduser()
        public_key = Path(
            os.getenv("KALSHI_PUBLIC_KEY_PATH", str(root / "public_key.pem"))
        ).expanduser()
        if not private_key.is_absolute():
            private_key = root / private_key
        if not public_key.is_absolute():
            public_key = root / public_key
        ledger_path = Path(
            os.getenv("TRADE_LEDGER_PATH", str(state_root / "trade_ledger.db"))
        ).expanduser()
        trade_log_path = Path(
            os.getenv("TRADE_LOG_PATH", str(state_root / "trades.jsonl"))
        ).expanduser()
        if not ledger_path.is_absolute():
            ledger_path = state_root / ledger_path
        if not trade_log_path.is_absolute():
            trade_log_path = state_root / trade_log_path
        strategy_lab_path = Path(
            os.getenv("STRATEGY_LAB_PATH", str(state_root / "strategy_lab.db"))
        ).expanduser()
        if not strategy_lab_path.is_absolute():
            strategy_lab_path = state_root / strategy_lab_path

        # Reports and logs are written for a person to open, so they go to the
        # place that platform's people look. Windows gets the Desktop folder
        # named in the spec; everywhere else gets a directory in $HOME.
        default_log_dir = (
            Path.home() / "Desktop" / "kx15_Performance_Reports"
            if os.name == "nt"
            else Path.home() / "kx15_Performance_Logs"
        )
        log_dir = Path(os.getenv("KX15_LOG_DIR", str(default_log_dir))).expanduser()
        if not log_dir.is_absolute():
            log_dir = state_root / log_dir
        log_max_bytes = int(os.getenv("KX15_LOG_MAX_BYTES", str(512 * 1024 * 1024)))
        if log_max_bytes < 1024 * 1024:
            raise ValueError("KX15_LOG_MAX_BYTES must be at least 1 MiB")
        log_console_level = os.getenv("KX15_LOG_CONSOLE", "quiet").strip().lower()
        if log_console_level not in {"quiet", "verbose", "silent"}:
            raise ValueError("KX15_LOG_CONSOLE must be one of: quiet, verbose, silent")

        status_check_lead_seconds = int(os.getenv("STATUS_CHECK_LEAD_SECONDS", "5"))
        if not 0 <= status_check_lead_seconds < 900:
            raise ValueError("STATUS_CHECK_LEAD_SECONDS must be between 0 and 899")
        settlement_poll_seconds = float(os.getenv("SETTLEMENT_POLL_SECONDS", "2"))
        if settlement_poll_seconds <= 0:
            raise ValueError("SETTLEMENT_POLL_SECONDS must be greater than 0")

        # Deprecated and ignored by execution; retained for old configuration.
        kelly_fraction = _decimal_env("KELLY_FRACTION", "0.25")
        if not Decimal("0") <= kelly_fraction <= Decimal("1"):
            raise ValueError("KELLY_FRACTION must be between 0 and 1")

        # A model that disagrees with a liquid market by more than this is far
        # more likely to be mispriced/stale than to be right.
        max_divergence = _decimal_env("MAX_MODEL_MARKET_DIVERGENCE", "0.125")
        if not Decimal("0") < max_divergence <= Decimal("1"):
            raise ValueError("MAX_MODEL_MARKET_DIVERGENCE must be between 0 and 1")

        max_candle_age_seconds = int(os.getenv("MAX_CANDLE_AGE_SECONDS", "120"))
        if not 0 < max_candle_age_seconds <= 900:
            raise ValueError("MAX_CANDLE_AGE_SECONDS must be between 1 and 900")

        cooldown_seconds = int(os.getenv("ORDER_FAILURE_COOLDOWN_SECONDS", "60"))
        if not 0 <= cooldown_seconds <= 900:
            raise ValueError("ORDER_FAILURE_COOLDOWN_SECONDS must be between 0 and 900")

        max_consecutive_losses = int(os.getenv("MAX_CONSECUTIVE_LOSSES", "3"))
        if max_consecutive_losses < 1:
            raise ValueError("MAX_CONSECUTIVE_LOSSES must be at least 1")

        max_drawdown_fraction = _decimal_env("MAX_DRAWDOWN_FRACTION", "0.60")
        if not Decimal("0") < max_drawdown_fraction <= Decimal("1"):
            raise ValueError("MAX_DRAWDOWN_FRACTION must be between 0 and 1")
        max_daily_loss = _decimal_env("MAX_DAILY_LOSS_USD", "0")
        if max_daily_loss < 0:
            raise ValueError("MAX_DAILY_LOSS_USD cannot be negative")

        min_contracts = _decimal_env("MIN_CONTRACTS", "1")
        if min_contracts < Decimal("0.01"):
            raise ValueError("MIN_CONTRACTS must be at least 0.01")

        request_concurrency = int(os.getenv("REQUEST_CONCURRENCY", "4"))
        if not 1 <= request_concurrency <= 16:
            raise ValueError("REQUEST_CONCURRENCY must be between 1 and 16")
        kalshi_request_interval = float(
            os.getenv("KALSHI_REQUEST_MIN_INTERVAL_SECONDS", "0.35")
        )
        if not 0 <= kalshi_request_interval <= 2:
            raise ValueError(
                "KALSHI_REQUEST_MIN_INTERVAL_SECONDS must be between 0 and 2"
            )

        decision_log_path = Path(
            os.getenv("DECISION_LOG_PATH", str(state_root / "decisions.jsonl"))
        ).expanduser()
        if not decision_log_path.is_absolute():
            decision_log_path = state_root / decision_log_path

        # Defaults beside the ledger rather than beside the code: the two are
        # the same process's mutable state and move together when the install
        # directory is read-only.
        decision_log_retention_hours = float(
            os.getenv("DECISION_LOG_RETENTION_HOURS", "24")
        )
        if decision_log_retention_hours < 0:
            raise ValueError("DECISION_LOG_RETENTION_HOURS cannot be negative")

        mode_control_path = Path(
            os.getenv(
                "MODE_CONTROL_PATH", str(ledger_path.parent / "runtime_mode.json")
            )
        ).expanduser()
        if not mode_control_path.is_absolute():
            mode_control_path = state_root / mode_control_path

        scalp_band_low = _decimal_env("SCALP_PRICE_BAND_LOW", "0.70")
        scalp_band_high = _decimal_env("SCALP_PRICE_BAND_HIGH", "0.92")
        if not Decimal("0") < scalp_band_low < scalp_band_high < Decimal("1"):
            raise ValueError(
                "SCALP_PRICE_BAND_LOW and _HIGH must satisfy "
                f"0 < low < high < 1, got {scalp_band_low} and {scalp_band_high}"
            )
        # The band floor is the win-rate guarantee. Below 0.55 an EV-positive
        # strategy is no longer forced above a 55% win rate, so the stated
        # objective silently stops holding.
        if scalp_band_low < Decimal("0.55"):
            raise ValueError(
                "SCALP_PRICE_BAND_LOW below 0.55 breaks the >=55% win-rate "
                "guarantee, since break-even requires price + fee"
            )

        scalp_min_ev = _decimal_env("SCALP_MIN_EV_PER_DOLLAR", "0.015")
        if scalp_min_ev <= 0:
            raise ValueError("SCALP_MIN_EV_PER_DOLLAR must be greater than 0")

        scalp_max_spread = _decimal_env("SCALP_MAX_SPREAD", "0.02")
        if not Decimal("0") < scalp_max_spread < Decimal("1"):
            raise ValueError("SCALP_MAX_SPREAD must be between 0 and 1")

        scalp_min_depth = _decimal_env("SCALP_MIN_BOOK_DEPTH", "100")
        if scalp_min_depth < 0:
            raise ValueError("SCALP_MIN_BOOK_DEPTH cannot be negative")

        scalp_quote_ttl = float(os.getenv("SCALP_QUOTE_TTL_SECONDS", "20"))
        if scalp_quote_ttl <= 0:
            raise ValueError("SCALP_QUOTE_TTL_SECONDS must be positive")

        scalp_requote_ticks = int(os.getenv("SCALP_REQUOTE_TICKS", "2"))
        if scalp_requote_ticks <= 0:
            raise ValueError("SCALP_REQUOTE_TICKS must be positive")

        scalp_min_regime_confidence = _decimal_env("SCALP_MIN_REGIME_CONFIDENCE", "0.0")
        if not Decimal("-1") <= scalp_min_regime_confidence <= Decimal("1"):
            raise ValueError("SCALP_MIN_REGIME_CONFIDENCE must be between 0 and 1")

        scalp_min_abs_z = _decimal_env("SCALP_MIN_ABS_Z", "0.50")
        if scalp_min_abs_z < 0:
            raise ValueError("SCALP_MIN_ABS_Z cannot be negative")

        blocked_raw = os.getenv("SCALP_BLOCKED_REGIMES", "VOLATILE_CHOP")
        scalp_blocked = tuple(
            part.strip().upper() for part in blocked_raw.split(",") if part.strip()
        )

        scalp_tail = int(os.getenv("SCALP_SESSION_TAIL_SECONDS", "900"))
        if scalp_tail < 0:
            raise ValueError("SCALP_SESSION_TAIL_SECONDS cannot be negative")

        # One quote slot per asset, derived rather than written down. The
        # documented invariant is that every market clearing the band can carry
        # a quote; a literal 5 quietly became "the first five of nine" the
        # moment the series list grew. This does not raise total exposure -
        # SCALP_MAX_CAPITAL_FRACTION and LIVE_CAPITAL_LIMIT_USD still bound the
        # pool - it only lets the same capital spread across every market.
        scalp_max_concurrent = int(
            os.getenv("SCALP_MAX_CONCURRENT", str(len(SERIES_BY_ASSET)))
        )
        if scalp_max_concurrent <= 0:
            raise ValueError("SCALP_MAX_CONCURRENT must be positive")

        scalp_taker_multiple = _decimal_env("SCALP_TAKER_EDGE_MULTIPLE", "2.0")
        if scalp_taker_multiple < 1:
            raise ValueError(
                "SCALP_TAKER_EDGE_MULTIPLE must be at least 1; below that a crossed\n"
                "order would be taken on a weaker edge than a rested one"
            )

        scalp_capital_fraction = _decimal_env("SCALP_MAX_CAPITAL_FRACTION", "0.50")
        if not Decimal("0") < scalp_capital_fraction <= Decimal("1"):
            raise ValueError(
                "SCALP_MAX_CAPITAL_FRACTION must be greater than 0 and at most 1"
            )

        scan_log_interval = float(os.getenv("SCAN_LOG_INTERVAL_SECONDS", "10"))
        if scan_log_interval < 0:
            raise ValueError("SCAN_LOG_INTERVAL_SECONDS cannot be negative")

        safe_mode_loss_trigger = int(os.getenv("SAFE_MODE_LOSS_TRIGGER", "3"))
        if safe_mode_loss_trigger < 1:
            raise ValueError("SAFE_MODE_LOSS_TRIGGER must be positive")
        safe_mode_min_probability = _decimal_env("SAFE_MODE_MIN_PROBABILITY", "0.92")
        if not Decimal("0.50") < safe_mode_min_probability < Decimal("1"):
            raise ValueError("SAFE_MODE_MIN_PROBABILITY must be between 0.50 and 1")
        safe_mode_min_market_price = _decimal_env("SAFE_MODE_MIN_MARKET_PRICE", "0.85")
        if not Decimal("0.50") < safe_mode_min_market_price < Decimal("1"):
            raise ValueError("SAFE_MODE_MIN_MARKET_PRICE must be between 0.50 and 1")
        safe_mode_min_sigma = _decimal_env("SAFE_MODE_MIN_SETTLEMENT_SIGMA", "2.0")
        if safe_mode_min_sigma < 0:
            raise ValueError("SAFE_MODE_MIN_SETTLEMENT_SIGMA cannot be negative")

        scalp_order_style = os.getenv("SCALP_ORDER_STYLE", "taker").strip().lower()
        if scalp_order_style not in {"taker", "maker"}:
            raise ValueError("SCALP_ORDER_STYLE must be 'taker' or 'maker'")

        scalp_max_toxicity = _decimal_env("SCALP_MAX_TOXICITY", "0.85")
        if not Decimal("0") < scalp_max_toxicity <= Decimal("1"):
            raise ValueError("SCALP_MAX_TOXICITY must be between 0 and 1")
        scalp_max_exit_loss = _decimal_env("SCALP_MAX_MODELED_EXIT_LOSS_USD", "1.18")
        if scalp_max_exit_loss <= 0:
            raise ValueError("SCALP_MAX_MODELED_EXIT_LOSS_USD must be positive")

        scalp_crowd_weight = _decimal_env("SCALP_CROWD_WEIGHT", "0.0")
        if scalp_crowd_weight < 0:
            raise ValueError("SCALP_CROWD_WEIGHT cannot be negative")

        scalp_min_seconds = int(os.getenv("SCALP_MIN_SECONDS_TO_CLOSE", "120"))
        if scalp_min_seconds < 0:
            raise ValueError("SCALP_MIN_SECONDS_TO_CLOSE cannot be negative")

        salvage_window = int(os.getenv("SALVAGE_WINDOW_SECONDS", "30"))
        if not 0 < salvage_window <= 900:
            raise ValueError(
                "SALVAGE_WINDOW_SECONDS must be between 1 and 900; it is one "
                "evaluation before close, not a monitoring mode"
            )

        salvage_max_bid = _decimal_env("SALVAGE_MAX_BID", "0.15")
        if not Decimal("0") < salvage_max_bid <= Decimal("0.20"):
            raise ValueError(
                "SALVAGE_MAX_BID must be above 0 and at most 0.20. The rule "
                "rests on the favourite-longshot bias, which is measured at "
                "the extremes; above ~0.20 there is no overpricing to collect "
                "and this becomes the model-driven reversal exit that was "
                "removed for selling a position that went on to win."
            )

        salvage_max_spread = _decimal_env("SALVAGE_MAX_SPREAD", "0.02")
        if not Decimal("0") < salvage_max_spread <= Decimal("0.03"):
            raise ValueError(
                "SALVAGE_MAX_SPREAD must be above 0 and at most 0.03; the exit "
                "edge is paid out of the spread and does not survive a wider one"
            )

        salvage_haircut = _decimal_env("SALVAGE_LONGSHOT_HAIRCUT", "0.40")
        if not Decimal("0") <= salvage_haircut < Decimal("1"):
            raise ValueError("SALVAGE_LONGSHOT_HAIRCUT must be at least 0 and below 1")

        salvage_min_gain = _decimal_env("SALVAGE_MIN_GAIN_PER_CONTRACT", "0.01")
        if salvage_min_gain < 0:
            raise ValueError("SALVAGE_MIN_GAIN_PER_CONTRACT cannot be negative")

        sizing_gain = _decimal_env("SIZING_CONFIDENCE_GAIN", "1.0")
        if not Decimal("1") <= sizing_gain <= Decimal("3"):
            raise ValueError(
                "SIZING_CONFIDENCE_GAIN must be between 1.0 and 3.0. 1.0 is "
                "off; above 3.0 a single confident quote would dominate the "
                "book and the account, which is not a sizing rule but a bet."
            )

        sizing_floor = _decimal_env("SIZING_CONFIDENCE_FLOOR", "0.60")
        if not Decimal("0.5") <= sizing_floor < Decimal("1"):
            raise ValueError(
                "SIZING_CONFIDENCE_FLOOR must be at least 0.50 and below 1; "
                "below a bare majority there is no confidence to scale on"
            )

        sizing_full = _decimal_env("SIZING_CONFIDENCE_FULL", "0.90")
        if not sizing_floor < sizing_full <= Decimal("1"):
            raise ValueError(
                "SIZING_CONFIDENCE_FULL must be above SIZING_CONFIDENCE_FLOOR "
                "and at most 1.0"
            )

        sizing_max_fraction = _decimal_env(
            "SIZING_MAX_FRACTION", str(position_fraction)
        )
        if not Decimal("0") < sizing_max_fraction <= Decimal("0.50"):
            raise ValueError("SIZING_MAX_FRACTION must be above 0 and at most 0.50")
        if sizing_max_fraction < position_fraction:
            raise ValueError(
                "SIZING_MAX_FRACTION cannot be below POSITION_FRACTION; it is "
                "the ceiling the scaled fraction is clamped to, so a lower "
                "value would silently shrink ordinary sizing instead of "
                "bounding the scaled case"
            )

        oversight_full_exposure = _decimal_env("OVERSIGHT_FULL_EXPOSURE", "0.25")
        if not Decimal("0") < oversight_full_exposure <= Decimal("1"):
            raise ValueError("OVERSIGHT_FULL_EXPOSURE must be above 0 and at most 1.0")

        oversight_max_window = int(os.getenv("OVERSIGHT_MAX_WINDOW_SECONDS", "300"))
        if not salvage_window <= oversight_max_window <= 900:
            raise ValueError(
                "OVERSIGHT_MAX_WINDOW_SECONDS must be at least "
                "SALVAGE_WINDOW_SECONDS and at most 900; it extends the base "
                "window for exposed positions, it does not replace it"
            )

        oversight_max_checks = int(os.getenv("OVERSIGHT_MAX_CHECKS", "4"))
        if not 1 <= oversight_max_checks <= 20:
            raise ValueError(
                "OVERSIGHT_MAX_CHECKS must be between 1 and 20. A rule re-asked "
                "every poll eventually says yes to noise, which is how the "
                "exit-on-reversal failed; the cap is what keeps this "
                "supervision rather than a random number generator."
            )

        oversight_spacing = float(os.getenv("OVERSIGHT_MIN_SPACING_SECONDS", "20"))
        if oversight_spacing < 0:
            raise ValueError("OVERSIGHT_MIN_SPACING_SECONDS cannot be negative")

        oversight_shadow_bid = _decimal_env("OVERSIGHT_SHADOW_MAX_BID", "0.35")
        if not salvage_max_bid <= oversight_shadow_bid < Decimal("1"):
            raise ValueError(
                "OVERSIGHT_SHADOW_MAX_BID must be at least SALVAGE_MAX_BID and "
                "below 1. It is recorded, never acted on - a shadow band below "
                "the real one would log a decision stricter than the one "
                "actually taken, which answers no question worth asking."
            )

        salvage_actionable = float(os.getenv("SALVAGE_MIN_ACTIONABLE_SECONDS", "0"))
        if not 0 <= salvage_actionable < salvage_window:
            raise ValueError(
                "SALVAGE_MIN_ACTIONABLE_SECONDS must be at least 0 and below "
                "SALVAGE_WINDOW_SECONDS. At or above it the base window closes "
                "before it opens and the salvage exit is switched on and "
                "unreachable - the exact shape of five past failures here."
            )

        oversight_extra_actionable = float(
            os.getenv("OVERSIGHT_EXTRA_ACTIONABLE_SECONDS", "0")
        )
        if oversight_extra_actionable < 0:
            raise ValueError("OVERSIGHT_EXTRA_ACTIONABLE_SECONDS cannot be negative")
        if salvage_actionable + oversight_extra_actionable >= oversight_max_window:
            raise ValueError(
                "SALVAGE_MIN_ACTIONABLE_SECONDS + "
                "OVERSIGHT_EXTRA_ACTIONABLE_SECONDS must stay below "
                "OVERSIGHT_MAX_WINDOW_SECONDS, or the most exposed position - "
                "the one this exists for - is the one that never gets checked"
            )

        kraken_stream_stale = float(os.getenv("KRAKEN_STREAM_STALE_SECONDS", "90"))
        if kraken_stream_stale <= 0:
            raise ValueError(
                "KRAKEN_STREAM_STALE_SECONDS must be positive; it is how long "
                "a silent stream may still be believed, and zero would mean "
                "never trusting it at all"
            )

        size_probability_uplift = _decimal_env("SIZE_PROBABILITY_UPLIFT", "0")
        if not Decimal("0") <= size_probability_uplift <= Decimal("0.30"):
            raise ValueError(
                "SIZE_PROBABILITY_UPLIFT must be between 0 and 0.30. It is "
                "added to MIN_MODEL_PROBABILITY at full exposure; beyond 0.30 "
                "the floor exceeds any probability this model produces and the "
                "strategy stops trading rather than trading carefully."
            )

        size_probability_full = _decimal_env("SIZE_PROBABILITY_FULL_EXPOSURE", "0.25")
        if not Decimal("0") < size_probability_full <= Decimal("1"):
            raise ValueError(
                "SIZE_PROBABILITY_FULL_EXPOSURE must be above 0 and at most 1.0"
            )

        ladder_buffer = _decimal_env("LADDER_ARB_BUFFER", "0.02")
        if ladder_buffer < 0:
            raise ValueError("LADDER_ARB_BUFFER cannot be negative")

        ladder_max_contracts = _decimal_env("LADDER_ARB_MAX_CONTRACTS", "25")
        if ladder_max_contracts <= 0:
            raise ValueError("LADDER_ARB_MAX_CONTRACTS must be positive")

        raw_recording_assets = os.getenv("ORDERBOOK_RECORDING_ASSETS", "ALL").split(",")
        requested_recording = tuple(
            dict.fromkeys(
                asset.strip().upper() for asset in raw_recording_assets if asset.strip()
            )
        )
        recording_assets = (
            assets if "ALL" in requested_recording else requested_recording
        )
        unsupported_recording = sorted(set(recording_assets) - set(SERIES_BY_ASSET))
        if unsupported_recording:
            raise ValueError(
                "Unsupported ORDERBOOK_RECORDING_ASSETS: "
                + ", ".join(unsupported_recording)
            )
        rest_reconcile_seconds = float(
            os.getenv("ORDERBOOK_REST_RECONCILE_SECONDS", "30")
        )
        if rest_reconcile_seconds <= 0:
            raise ValueError("ORDERBOOK_REST_RECONCILE_SECONDS must be positive")

        take_profit = _decimal_env("TAKE_PROFIT_PER_CONTRACT", "0.05")
        if not Decimal("0") < take_profit < Decimal("1"):
            raise ValueError("TAKE_PROFIT_PER_CONTRACT must be between 0 and 1")
        take_profit_capture = _decimal_env("TAKE_PROFIT_MIN_CAPTURE_FRACTION", "0.75")
        if not Decimal("0") <= take_profit_capture <= Decimal("1"):
            raise ValueError("TAKE_PROFIT_MIN_CAPTURE_FRACTION must be between 0 and 1")
        take_profit_confidence = _decimal_env("TAKE_PROFIT_CONFIDENCE_MARGIN", "0.02")
        if not Decimal("0") <= take_profit_confidence < Decimal("1"):
            raise ValueError("TAKE_PROFIT_CONFIDENCE_MARGIN must be between 0 and 1")
        winning_hold_bid = _decimal_env("TAKE_PROFIT_WINNING_HOLD_MIN_BID", "0.85")
        if not Decimal("0") < winning_hold_bid < Decimal("1"):
            raise ValueError("TAKE_PROFIT_WINNING_HOLD_MIN_BID must be between 0 and 1")
        winning_hold_probability = _decimal_env(
            "TAKE_PROFIT_WINNING_HOLD_MIN_PROBABILITY", "0.65"
        )
        if not Decimal("0") < winning_hold_probability < Decimal("1"):
            raise ValueError(
                "TAKE_PROFIT_WINNING_HOLD_MIN_PROBABILITY must be between 0 and 1"
            )
        winning_hold_strong_book_bid = _decimal_env(
            "TAKE_PROFIT_WINNING_HOLD_STRONG_BOOK_BID", "0.95"
        )
        if not Decimal("0") < winning_hold_strong_book_bid <= Decimal("1"):
            raise ValueError(
                "TAKE_PROFIT_WINNING_HOLD_STRONG_BOOK_BID must be greater than "
                "0 and at most 1"
            )
        winning_hold_seconds = int(
            os.getenv("TAKE_PROFIT_WINNING_HOLD_MAX_SECONDS_TO_CLOSE", "0")
        )
        if winning_hold_seconds < 0:
            raise ValueError(
                "TAKE_PROFIT_WINNING_HOLD_MAX_SECONDS_TO_CLOSE cannot be negative"
            )
        winning_hold_confirmations = int(
            os.getenv("TAKE_PROFIT_WINNING_HOLD_CONFIRMATIONS", "2")
        )
        reversal_confirmations = int(os.getenv("CONFIRMED_REVERSAL_CONFIRMATIONS", "2"))
        reversal_min_signals = int(os.getenv("CONFIRMED_REVERSAL_MIN_SIGNALS", "3"))
        if winning_hold_confirmations < 1 or reversal_confirmations < 1:
            raise ValueError("Exit confirmation counts must be positive")
        if not 1 <= reversal_min_signals <= 5:
            raise ValueError("CONFIRMED_REVERSAL_MIN_SIGNALS must be between 1 and 5")
        reversal_probability = _decimal_env(
            "CONFIRMED_REVERSAL_PROBABILITY_MAX", "0.48"
        )
        reversal_drawdown = _decimal_env("CONFIRMED_REVERSAL_PEAK_DRAWDOWN", "0.15")
        if not Decimal("0") < reversal_probability < Decimal("0.50"):
            raise ValueError(
                "CONFIRMED_REVERSAL_PROBABILITY_MAX must be between 0 and 0.50"
            )
        if not Decimal("0") < reversal_drawdown < Decimal("1"):
            raise ValueError("CONFIRMED_REVERSAL_PEAK_DRAWDOWN must be between 0 and 1")
        take_profit_spread = _decimal_env("TAKE_PROFIT_MAX_SPREAD", "0.03")
        if not Decimal("0") <= take_profit_spread < Decimal("1"):
            raise ValueError("TAKE_PROFIT_MAX_SPREAD must be between 0 and 1")
        take_profit_min_seconds = int(
            os.getenv("TAKE_PROFIT_MIN_SECONDS_TO_CLOSE", "30")
        )
        if take_profit_min_seconds < 0:
            raise ValueError("TAKE_PROFIT_MIN_SECONDS_TO_CLOSE cannot be negative")
        take_profit_check_interval = float(
            os.getenv("TAKE_PROFIT_CHECK_INTERVAL_SECONDS", "15")
        )
        take_profit_fast_window = int(
            os.getenv("TAKE_PROFIT_FAST_WINDOW_SECONDS", "120")
        )
        take_profit_fast_check = float(os.getenv("TAKE_PROFIT_FAST_CHECK_SECONDS", "5"))
        if take_profit_check_interval <= 0 or take_profit_fast_check <= 0:
            raise ValueError("Take-profit check intervals must be positive")
        websocket_stale_seconds = float(
            os.getenv("KALSHI_WEBSOCKET_STALE_SECONDS", "10")
        )
        event_reassessment_interval = float(
            os.getenv("EVENT_REASSESSMENT_MIN_INTERVAL_SECONDS", "0.5")
        )
        event_entry_interval = float(
            os.getenv("EVENT_ENTRY_MIN_INTERVAL_SECONDS", "2.0")
        )
        if websocket_stale_seconds <= 0:
            raise ValueError("KALSHI_WEBSOCKET_STALE_SECONDS must be positive")
        if event_reassessment_interval <= 0:
            raise ValueError("EVENT_REASSESSMENT_MIN_INTERVAL_SECONDS must be positive")
        if event_entry_interval <= 0:
            raise ValueError("EVENT_ENTRY_MIN_INTERVAL_SECONDS must be positive")
        predictor_max_concurrent = int(os.getenv("PREDICTOR_MAX_CONCURRENT", "2"))
        predictor_candidates = int(os.getenv("PREDICTOR_MAX_CANDIDATES_PER_CYCLE", "2"))
        if not 1 <= predictor_max_concurrent <= 2:
            raise ValueError("PREDICTOR_MAX_CONCURRENT must be between 1 and 2")
        if not 1 <= predictor_candidates <= predictor_max_concurrent:
            raise ValueError(
                "PREDICTOR_MAX_CANDIDATES_PER_CYCLE must be between 1 and "
                "PREDICTOR_MAX_CONCURRENT"
            )
        if take_profit_fast_window < take_profit_min_seconds:
            raise ValueError(
                "TAKE_PROFIT_FAST_WINDOW_SECONDS must be at least "
                "TAKE_PROFIT_MIN_SECONDS_TO_CLOSE"
            )
        max_entries_per_market = int(os.getenv("MAX_ENTRIES_PER_MARKET", "2"))
        if not 1 <= max_entries_per_market <= 4:
            raise ValueError("MAX_ENTRIES_PER_MARKET must be between 1 and 4")
        strategy_lab_balance = _decimal_env("STRATEGY_LAB_BALANCE_USD", "100")
        if strategy_lab_balance <= 0:
            raise ValueError("STRATEGY_LAB_BALANCE_USD must be positive")
        pair_arb_min_edge = _decimal_env("PAIR_ARB_MIN_NET_EDGE", "0.02")
        pair_arb_min_leg = _decimal_env("PAIR_ARB_MIN_LEG_PRICE", "0.02")
        if not Decimal("0") < pair_arb_min_edge < Decimal("1"):
            raise ValueError("PAIR_ARB_MIN_NET_EDGE must be between 0 and 1")
        if not Decimal("0") < pair_arb_min_leg < Decimal("0.5"):
            raise ValueError("PAIR_ARB_MIN_LEG_PRICE must be between 0 and 0.5")

        return cls(
            environment=environment,
            rest_url=rest_url.rstrip("/"),
            websocket_url=websocket_url,
            api_key_id=os.getenv("KALSHI_API_KEY") or None,
            private_key_path=private_key,
            public_key_path=public_key,
            subaccount=int(os.getenv("KALSHI_SUBACCOUNT", "0")),
            enable_live_trading=_bool_env("ENABLE_LIVE_TRADING", False),
            live_capital_limit_usd=capital_limit,
            trade_budget_usd=trade_budget,
            position_fraction=position_fraction,
            paper_balance_usd=_decimal_env("PAPER_BALANCE_USD", "100"),
            poll_interval_seconds=float(os.getenv("POLL_INTERVAL_SECONDS", "5")),
            entry_window_seconds=entry_window_seconds,
            late_entry_seconds=late_entry_seconds,
            late_min_probability=late_min_probability,
            late_min_edge=late_min_edge,
            late_min_settlement_sigma=late_min_settlement_sigma,
            consensus_tail_preferred=_bool_env("CONSENSUS_TAIL_PREFERRED", True),
            consensus_tail_min_raw_probability=consensus_raw,
            consensus_tail_min_market_price=consensus_market,
            consensus_tail_min_edge=consensus_edge,
            consensus_tail_min_settlement_sigma=consensus_sigma,
            consensus_tail_require_indicator_alignment=_bool_env(
                "CONSENSUS_TAIL_REQUIRE_INDICATOR_ALIGNMENT", True
            ),
            regime_model_path=regime_model_path,
            commodity_model_path=commodity_model_path,
            minimum_seconds_to_close=int(os.getenv("MINIMUM_SECONDS_TO_CLOSE", "20")),
            min_probability=min_probability,
            min_edge=min_edge,
            max_edge=max_edge,
            max_quote_improvement=max_quote_improvement,
            max_slippage=max_slippage,
            fee_reserve_per_contract=fee_reserve,
            assets=assets,
            ledger_path=ledger_path,
            trade_log_path=trade_log_path,
            status_check_lead_seconds=status_check_lead_seconds,
            settlement_poll_seconds=settlement_poll_seconds,
            kelly_fraction=kelly_fraction,
            max_model_market_divergence=max_divergence,
            max_candle_age_seconds=max_candle_age_seconds,
            order_failure_cooldown_seconds=cooldown_seconds,
            max_consecutive_losses=max_consecutive_losses,
            max_drawdown_fraction=max_drawdown_fraction,
            max_daily_loss_usd=max_daily_loss,
            min_contracts=min_contracts,
            request_concurrency=request_concurrency,
            kalshi_request_min_interval_seconds=kalshi_request_interval,
            decision_log_path=decision_log_path,
            decision_log_enabled=_bool_env("DECISION_LOG_ENABLED", True),
            decision_log_retention_hours=decision_log_retention_hours,
            regime_aware_model=_bool_env("REGIME_AWARE_MODEL", True),
            mode_control_path=mode_control_path,
            mode_control_enabled=_bool_env("MODE_CONTROL_ENABLED", True),
            log_dir=log_dir,
            log_max_bytes=log_max_bytes,
            log_console_level=log_console_level,
            reports_enabled=_bool_env("KX15_REPORTS_ENABLED", True),
            market_data_enabled=_bool_env("MARKET_DATA_ENABLED", True),
            orderbook_recording_assets=recording_assets,
            orderbook_rest_reconcile_seconds=rest_reconcile_seconds,
            raw_book_event_recording_enabled=_bool_env(
                "RAW_BOOK_EVENT_RECORDING_ENABLED", False
            ),
            public_trade_recording_enabled=_bool_env(
                "PUBLIC_TRADE_RECORDING_ENABLED", True
            ),
            take_profit_enabled=_bool_env("TAKE_PROFIT_ENABLED", True),
            take_profit_per_contract=take_profit,
            take_profit_min_capture_fraction=take_profit_capture,
            take_profit_confidence_margin=take_profit_confidence,
            take_profit_winning_hold_enabled=_bool_env(
                "TAKE_PROFIT_WINNING_HOLD_ENABLED", True
            ),
            take_profit_winning_hold_min_bid=winning_hold_bid,
            take_profit_winning_hold_min_probability=winning_hold_probability,
            take_profit_winning_hold_strong_book_bid=winning_hold_strong_book_bid,
            take_profit_winning_hold_max_seconds_to_close=winning_hold_seconds,
            take_profit_winning_hold_confirmations=winning_hold_confirmations,
            confirmed_reversal_enabled=_bool_env("CONFIRMED_REVERSAL_ENABLED", False),
            confirmed_reversal_confirmations=reversal_confirmations,
            confirmed_reversal_min_signals=reversal_min_signals,
            confirmed_reversal_probability_max=reversal_probability,
            confirmed_reversal_peak_drawdown=reversal_drawdown,
            take_profit_max_spread=take_profit_spread,
            take_profit_min_seconds_to_close=take_profit_min_seconds,
            take_profit_check_interval_seconds=take_profit_check_interval,
            take_profit_fast_window_seconds=take_profit_fast_window,
            take_profit_fast_check_seconds=take_profit_fast_check,
            kalshi_websocket_enabled=_bool_env("KALSHI_WEBSOCKET_ENABLED", True),
            kalshi_websocket_stale_seconds=websocket_stale_seconds,
            event_reassessment_min_interval_seconds=event_reassessment_interval,
            event_driven_entry_enabled=_bool_env("EVENT_DRIVEN_ENTRY_ENABLED", True),
            event_entry_min_interval_seconds=event_entry_interval,
            predictor_max_concurrent=predictor_max_concurrent,
            predictor_max_candidates_per_cycle=predictor_candidates,
            max_entries_per_market=max_entries_per_market,
            safe_mode_enabled=_bool_env("SAFE_MODE_ENABLED", True),
            safe_mode_loss_trigger=safe_mode_loss_trigger,
            safe_mode_min_probability=safe_mode_min_probability,
            safe_mode_min_market_price=safe_mode_min_market_price,
            safe_mode_min_settlement_sigma=safe_mode_min_sigma,
            safe_mode_consensus_only=_bool_env("SAFE_MODE_CONSENSUS_ONLY", True),
            strategy_lab_enabled=_bool_env("STRATEGY_LAB_ENABLED", True),
            strategy_lab_path=strategy_lab_path,
            strategy_lab_balance_usd=strategy_lab_balance,
            pair_arb_enabled=False,
            pair_arb_min_net_edge=pair_arb_min_edge,
            pair_arb_min_leg_price=pair_arb_min_leg,
            scan_log_interval_seconds=scan_log_interval,
            scalp_enabled=_bool_env("SCALP_ENABLED", False),
            scalp_band_low=scalp_band_low,
            scalp_band_high=scalp_band_high,
            scalp_min_ev_per_dollar=scalp_min_ev,
            scalp_max_spread=scalp_max_spread,
            scalp_min_book_depth=scalp_min_depth,
            scalp_quote_ttl_seconds=scalp_quote_ttl,
            scalp_requote_ticks=scalp_requote_ticks,
            scalp_max_concurrent=scalp_max_concurrent,
            scalp_taker_edge_multiple=scalp_taker_multiple,
            scalp_max_capital_fraction=scalp_capital_fraction,
            scalp_taker_fallback=_bool_env("SCALP_TAKER_FALLBACK", True),
            scalp_order_style=scalp_order_style,
            scalp_min_seconds_to_close=scalp_min_seconds,
            scalp_blocked_regimes=scalp_blocked,
            scalp_min_regime_confidence=scalp_min_regime_confidence,
            scalp_min_abs_z=scalp_min_abs_z,
            scalp_session_tail_seconds=scalp_tail,
            scalp_orderbook_enabled=_bool_env("SCALP_ORDERBOOK_ENABLED", True),
            scalp_max_toxicity=scalp_max_toxicity,
            scalp_max_modeled_exit_loss_usd=scalp_max_exit_loss,
            scalp_crowd_weight=scalp_crowd_weight,
            sizing_confidence_gain=sizing_gain,
            sizing_confidence_floor=sizing_floor,
            sizing_confidence_full=sizing_full,
            sizing_max_fraction=sizing_max_fraction,
            oversight_enabled=_bool_env("OVERSIGHT_ENABLED", False),
            oversight_full_exposure=oversight_full_exposure,
            oversight_max_window_seconds=oversight_max_window,
            oversight_max_checks=oversight_max_checks,
            oversight_min_spacing_seconds=oversight_spacing,
            oversight_shadow_max_bid=oversight_shadow_bid,
            scalp_queue_aware_fills=_bool_env("SCALP_QUEUE_AWARE_FILLS", False),
            kraken_stream_enabled=_bool_env("KRAKEN_STREAM_ENABLED", True),
            kraken_stream_stale_seconds=kraken_stream_stale,
            salvage_min_actionable_seconds=salvage_actionable,
            oversight_extra_actionable_seconds=oversight_extra_actionable,
            size_probability_uplift=size_probability_uplift,
            size_probability_full_exposure=size_probability_full,
            salvage_enabled=_bool_env("SALVAGE_ENABLED", False),
            salvage_window_seconds=salvage_window,
            salvage_max_bid=salvage_max_bid,
            salvage_max_spread=salvage_max_spread,
            salvage_longshot_haircut=salvage_haircut,
            salvage_min_gain_per_contract=salvage_min_gain,
            ladder_arb_enabled=_bool_env("LADDER_ARB_ENABLED", False),
            ladder_arb_series=os.getenv("LADDER_ARB_SERIES", "KXBTCD").strip().upper(),
            ladder_arb_buffer=ladder_buffer,
            ladder_arb_max_contracts=ladder_max_contracts,
        )

    def validate_live(self, cli_live: bool) -> None:
        """Raise if live trading was requested without every safety prerequisite."""
        if not cli_live:
            return
        if not self.enable_live_trading:
            # --live is not worth naming here: this method returns early unless
            # the caller already asked for live, so the only thing left to fix
            # is the file. The dashboard shows this string verbatim.
            raise RuntimeError("Live trading requires ENABLE_LIVE_TRADING=true in .env")
        if not self.api_key_id:
            raise RuntimeError("KALSHI_API_KEY is required for live trading")
        if not self.private_key_path.is_file():
            raise RuntimeError(
                f"Kalshi private key not found at {self.private_key_path}"
            )
        _check_key_permissions(self.private_key_path)
        if self.subaccount < 0:
            raise RuntimeError("KALSHI_SUBACCOUNT cannot be negative")
