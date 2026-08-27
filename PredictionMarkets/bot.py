#!/usr/bin/env python3
"""Kalshi multi-asset 15-minute crypto trading bot (async).

Paper trading is the default. Live orders require both --live and
ENABLE_LIVE_TRADING=true.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
import time
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import httpx
from config import config_file as _config_file  # noqa: E402
from config import load_environment
from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import KRAKEN_PAIR_BY_ASSET, KalshiSettings  # noqa: E402
from core.strategy_brain.strategies.crypto_15min_predictor import (  # noqa: E402
    Crypto15MinutePredictor,
    Prediction,
)
from core.strategy_brain.strategies.regime_model import (  # noqa: E402
    load_for_assets,
)
from core.strategy_brain.strategies.commodity_model import load_commodity_models  # noqa: E402
from data_sources.kraken.rest import KrakenRestSource  # noqa: E402
from data_sources.kraken.stream import KrakenCandleStream  # noqa: E402
from data_sources.yahoo.rest import RoutedPriceSource, YahooFinanceSource  # noqa: E402
from data_sources.pyth.rest import PythCommoditySource  # noqa: E402
from execution.kalshi_client import (  # noqa: E402
    AsyncKalshiTradingClient,
    KalshiMarket,
    OrderBookSnapshot,
)
from execution.kalshi_book_stream import KalshiBookStream  # noqa: E402
from execution.paper_session import (  # noqa: E402
    PaperSessionReport,
    build_paper_session_report,
    format_paper_session_report,
)
from execution.decision_log import DecisionLog, DecisionTrace  # noqa: E402
from execution.mode_control import ModeControl  # noqa: E402
from execution.position_book import PositionBook  # noqa: E402
from execution.provenance import run_provenance  # noqa: E402
from execution.pair_arb_engine import PairArbEngine  # noqa: E402
from execution.scalp_engine import STRATEGY as SCALP_STRATEGY  # noqa: E402
from execution.scalp_engine import ScalpEngine  # noqa: E402
from execution.salvage_exit import SalvageExit  # noqa: E402
from execution.take_profit import TakeProfitExit  # noqa: E402
from execution.strategy_lab import LabObservation, StrategyLab  # noqa: E402
from execution.risk_gate import (  # noqa: E402
    GateResult,
    check_candle_freshness,
    check_capital_breaker,
    check_executable_edge_ceiling,
    check_model_market_divergence,
    check_quote_sanity,
    check_settlement_margin,
    check_size_scaled_probability,
    late_entry_thresholds,
    market_implied_probability,
    required_probability,
)
from execution.run_log import (  # noqa: E402
    COUNTDOWN_SECONDS,
    FILE_FORMAT,
    KIND_EVENT,
    KIND_SCAN,
    KIND_STATS,
    KIND_WAIT,
    LEDGER_AUDIT_NAME,
    DISABLED_LEDGER_AUDIT,
    HourlyClock,
    LedgerAudit,
    LifetimeStats,
    PeriodCounters,
    RunLogFile,
    console_sink_options,
    countdown_ticks,
    format_hourly_block,
    format_scan_line,
    format_trade_block,
    resolve_console_level,
)
from execution.trade_ledger import (  # noqa: E402
    TradeLedger,
    TradeRecord,
    calculate_paper_fill,
)


# Discovery outages are logged and recorded on the first empty cycle and once
# per minute after that, so a long outage stays visible without flooding the
# decision log at the poll interval.
EMPTY_DISCOVERY_RECORD_EVERY = 12
# One 15-minute market's worth of empty cycles at a 5s poll: past this the bot
# has missed an entire tradeable interval, which is an error, not a hiccup.
EMPTY_DISCOVERY_ALERT_CYCLES = 180
# A routed commodity read can otherwise spend one full HTTP timeout on Yahoo
# and then another on Pyth. One unavailable auxiliary feed must not postpone
# position servicing or the next Kalshi discovery pass for tens of seconds.
PRICE_FEED_READ_TIMEOUT_SECONDS = 12.0


def _risk_cluster(asset: str) -> str:
    """Coarse correlation bucket used to bound simultaneous predictor risk."""
    normalized = asset.upper()
    if normalized in {"GOLD", "SILVER"}:
        return "metals"
    if normalized == "WTI":
        return "energy"
    return "crypto"


# The console's four voices. Every line written for a person to read is emitted
# through one of these, and execution/run_log.py's filter decides which voices a
# given verbosity admits. Tagging rather than levelling is the point: an
# INFO-level pass summary has to reach the console while an INFO-level gate
# rejection must not, and no arrangement of severities expresses that. An
# untagged call lands in the run log and, unless it is a warning or worse,
# nowhere else.
_event = logger.bind(kind=KIND_EVENT)
_scan = logger.bind(kind=KIND_SCAN)
_wait = logger.bind(kind=KIND_WAIT)
_stats = logger.bind(kind=KIND_STATS)


def _console_is_interactive() -> bool:
    """Whether stderr is a terminal somebody is watching."""
    try:
        return bool(sys.stderr.isatty())
    except (AttributeError, ValueError):  # pragma: no cover - closed stream
        return False


@dataclass(frozen=True)
class TradeCandidate:
    market: KalshiMarket
    prediction: Prediction
    outcome: str
    outcome_probability: Decimal
    top_of_book_price: Decimal
    buffered_price: Decimal
    edge: Decimal
    # Carries the evaluation's reasoning into placement so the whole decision,
    # sizing and fill included, lands in one decision-log record.
    trace: DecisionTrace | None = None
    # The edge floor this candidate was admitted under. Placement re-reads the
    # book and re-checks the edge, and it must apply the *same* floor the
    # evaluation used — otherwise a late-market candidate admitted at 2% is
    # rejected seconds later by the standing 5% rule and the window never fires.
    edge_floor: Decimal = Decimal("0")
    is_late: bool = False
    consensus_tail: bool = False


def _consensus_tail_qualification(
    settings: Any,
    prediction: Prediction,
    outcome: str,
    executable_price: Decimal,
) -> tuple[bool, dict[str, Any]]:
    """Independent raw-model, venue, distance, regime and indicator agreement."""
    one = Decimal("1")
    raw_probability = (
        prediction.raw_probability_yes
        if outcome == "yes"
        else one - prediction.raw_probability_yes
    )
    aligned_z = prediction.distance_z if outcome == "yes" else -prediction.distance_z
    directional_yes = getattr(prediction, "directional_probability_yes", Decimal("0.5"))
    directional_probability = (
        directional_yes if outcome == "yes" else one - directional_yes
    )
    raw_edge = (
        raw_probability
        - executable_price
        - getattr(settings, "fee_reserve_per_contract", Decimal("0.015"))
    )
    indicator_required = getattr(
        settings, "consensus_tail_require_indicator_alignment", True
    )
    checks = {
        "raw_probability": raw_probability
        >= getattr(settings, "consensus_tail_min_raw_probability", Decimal("0.97")),
        "market_price": executable_price
        >= getattr(settings, "consensus_tail_min_market_price", Decimal("0.85")),
        "raw_edge": raw_edge
        >= getattr(settings, "consensus_tail_min_edge", Decimal("0.02")),
        "settlement_distance": aligned_z
        >= getattr(settings, "consensus_tail_min_settlement_sigma", Decimal("2.0")),
        "regime": "VOLATILE_CHOP" not in prediction.regime.upper(),
        "indicator_alignment": (
            directional_probability >= Decimal("0.50") if indicator_required else True
        ),
    }
    return all(checks.values()), {
        "qualified": all(checks.values()),
        "checks": checks,
        "raw_probability": raw_probability,
        "raw_edge": raw_edge,
        "aligned_distance_z": aligned_z,
        "directional_probability": directional_probability,
    }


def _prediction_payload(prediction: Prediction) -> dict[str, Any]:
    """Flatten a prediction for the decision log.

    Every input the probability depended on is included - regime label, the
    multipliers it implied, the pre-shrinkage probability, and the raw feature
    vector - so a line in the log can be re-derived without the candles.
    """
    payload = asdict(prediction)
    payload["regime_reasons"] = list(prediction.regime_reasons)
    return payload


def _strict_signal_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize one probability-bearing decision for the strict ledger.

    Early clock/feed refusals legitimately have no probability and remain in
    the diagnostic decision stream. Once both model and market probabilities
    exist, the evaluation is training data and must also be durable/queryable.
    Probabilities are always P(YES); ``outcome`` identifies the candidate side.
    """
    if payload.get("record_type") != "decision":
        return None
    prediction = payload.get("prediction")
    market = payload.get("market")
    if not isinstance(prediction, dict) or not isinstance(market, dict):
        return None
    probability_yes = prediction.get("probability_yes")
    market_probability_yes = market.get("implied_probability_yes")
    if probability_yes is None or market_probability_yes is None:
        return None
    candidate = payload.get("candidate")
    candidate = candidate if isinstance(candidate, dict) else {}
    outcome = candidate.get("outcome")
    if outcome not in {"yes", "no"}:
        outcome = None
    decision = str(payload.get("decision", "observed"))
    blocked_by = payload.get("blocked_by")
    if decision == "traded":
        verdict, gate = "approved", "approved"
    elif decision in {"skipped", "halted", "order_rejected", "error"}:
        verdict = "blocked"
        gate = str(blocked_by or ("ranking" if decision == "skipped" else decision))
    else:
        verdict, gate = "observed", str(blocked_by or decision)
    timestamp = payload.get("timestamp")
    evaluated_at = datetime.fromisoformat(str(timestamp)) if timestamp else None
    return {
        "cycle_id": str(payload.get("cycle_id", "adhoc")),
        "ticker": str(payload["ticker"]),
        "strategy": str(payload.get("strategy", "predictor")),
        "model_probability": Decimal(str(probability_yes)),
        "market_probability": Decimal(str(market_probability_yes)),
        "verdict": verdict,
        "gate": gate,
        "outcome": outcome,
        "executable_price": (
            Decimal(str(candidate["top_of_book_price"]))
            if candidate.get("top_of_book_price") is not None
            else None
        ),
        "net_edge": (
            Decimal(str(candidate["edge"]))
            if candidate.get("edge") is not None
            else None
        ),
        "features": {
            "asset": payload.get("asset"),
            "seconds_to_close": payload.get("seconds_to_close"),
            "prediction": prediction,
            "candidate": candidate,
            "thresholds": payload.get("thresholds", {}),
            "decision": decision,
            "decision_reason": payload.get("decision_reason"),
            "gates": payload.get("gates", []),
        },
        "evaluated_at": evaluated_at,
    }


class KalshiCryptoBot:
    # Class-level defaults, because a bot is also built without __init__ - the
    # tests construct one to exercise a single method, and so does any future
    # recovery tool. A diagnostic must degrade to a no-op there rather than
    # raise AttributeError on the path that is holding a position.
    _countdown_label: str | None = None
    _last_balance: Decimal | None = None
    # P(yes) per ticker for the cycle in progress, shared between the predictor's
    # evaluation and the scalper's. Class-level so a bot built without __init__ -
    # the tests do this, and so would any recovery tool - reads an empty map
    # instead of raising AttributeError.
    _cycle_predictions: dict[str, Prediction] = {}
    # Same reason: a bot constructed without __init__ must read "scalping is off"
    # rather than raise, so every scalp branch degrades to a no-op there.
    scalp: "ScalpEngine | None" = None
    # Same reason again, and it matters more here: this one is consulted on the
    # path that is holding a live position.
    salvage: "SalvageExit | None" = None
    take_profit: "TakeProfitExit | None" = None
    positions: PositionBook | None = None
    # Set for the length of a bounded paper session. None in continuous
    # mode, where there is no deadline to stop opening positions ahead of.
    _session_deadline: float | None = None
    _session_wall_deadline: float = 0.0
    # Monotonic stamp of the last scan line the console was shown.
    _last_scan_log: float = 0.0
    _cycle_phase: str = "idle"
    _completed_cycles: int = 0
    _safe_mode: bool = False

    def __init__(
        self,
        settings: KalshiSettings,
        live: bool,
        *,
        silent: bool = False,
    ) -> None:
        settings.validate_live(live)
        self.settings = settings
        self.live = live
        self.silent = silent
        self.mode = "live" if live else "paper"
        self._completed_cycles = 0
        self.client = AsyncKalshiTradingClient(settings, authenticated=live)
        # Public discovery must not queue behind authenticated balance, order,
        # or position reads. A portfolio outage can then suppress new entries
        # without making every active market disappear at once.
        self._discovery_client = AsyncKalshiTradingClient(settings, authenticated=False)
        # Analytical REST depth has its own transport and pacing gate. A slow
        # reconciliation therefore cannot queue discovery or execution calls
        # behind twelve all-asset book reads on the money-bearing client.
        self._book_reconcile_client = AsyncKalshiTradingClient(
            settings, authenticated=False
        )
        self._rest_prices = KrakenRestSource(concurrency=settings.request_concurrency)
        # The stream keeps the same interface and falls back to this REST
        # source whenever it cannot be trusted, so nothing downstream - the
        # freshness gate included - learns which one answered.
        self.prices: Any = self._rest_prices
        self._yahoo_prices = YahooFinanceSource(
            concurrency=min(3, settings.request_concurrency)
        )
        self._commodity_prices = PythCommoditySource(self._yahoo_prices)
        self.price_stream: KrakenCandleStream | None = None
        if getattr(settings, "kraken_stream_enabled", False):
            crypto_assets = tuple(
                asset
                for asset in settings.assets
                if not KRAKEN_PAIR_BY_ASSET[asset].endswith("=F")
            )
            self.price_stream = KrakenCandleStream(
                rest=self._rest_prices,
                assets=crypto_assets,
                pair_by_asset=KRAKEN_PAIR_BY_ASSET,
                stale_after_seconds=settings.kraken_stream_stale_seconds,
            )
            self.prices = self.price_stream
        self.prices = RoutedPriceSource(self.prices, self._commodity_prices)
        # An artifact is optional and defaults to absent, so the learned regime
        # path only engages when REGIME_MODEL_PATH is explicitly configured.
        # One predictor per asset, because a fitted mixture is per-asset: the
        # two volatility features are the only ones whose scale differs
        # materially between underlyings, and a shared standardisation puts a
        # high-volatility asset permanently off-centre. Assets with no artifact
        # keep the hand-tuned classifier, so a single-asset shadow is a
        # supported state rather than an all-or-nothing switch.
        mixtures = load_for_assets(settings.regime_model_path, settings.assets)
        commodity_models = load_commodity_models(settings.commodity_model_path)
        for asset, mixture in sorted(mixtures.items()):
            logger.warning(
                "{}: regime mixture loaded ({} learned states, fitted on {} "
                "independent samples); the hand-tuned multipliers are being "
                "replaced by a blend",
                asset,
                len(mixture.states),
                mixture.metadata.get("independent_samples", "unknown"),
            )
        self.predictor = Crypto15MinutePredictor(
            regime_aware=settings.regime_aware_model,
            regime_mixture=None,
        )
        self._predictors = {
            asset: Crypto15MinutePredictor(
                regime_aware=settings.regime_aware_model,
                regime_mixture=mixture,
                directional_model=commodity_models.get(asset),
            )
            for asset, mixture in {
                asset: mixtures.get(asset) for asset in settings.assets
            }.items()
            if mixture is not None or asset in commodity_models
        }
        self.ledger = TradeLedger(
            settings.ledger_path,
            paper_starting_balance=settings.paper_balance_usd,
        )
        strategy_label = (
            "all"
            if settings.pair_arb_enabled
            and settings.predictor_enabled
            and settings.scalp_enabled
            else "arb"
            if settings.pair_arb_enabled
            else "both"
            if settings.predictor_enabled and settings.scalp_enabled
            else "predictor"
            if settings.predictor_enabled
            else "scalper"
        )
        self._strategy_label = strategy_label
        self._run_provenance = run_provenance(settings)
        self.data_run_id = self.ledger.start_data_run(
            mode=self.mode,
            environment=settings.environment,
            strategy=strategy_label,
            assets=settings.assets,
            **self._run_provenance,
        )
        self.client.bind_event_ledger(self.ledger, lambda: self.data_run_id)
        self._data_run_completed = False
        self.decisions = DecisionLog(
            settings.decision_log_path,
            enabled=settings.decision_log_enabled,
            retention_seconds=settings.decision_log_retention_hours * 3600,
            record_sink=self._record_strict_signal_evaluation,
        )
        # The dashboard's half of the paper/live switch. It can only leave a
        # request here; this process decides whether to honour it, and never
        # while a position is open. See execution/mode_control.py.
        self.mode_control = (
            ModeControl(settings.mode_control_path)
            if settings.mode_control_enabled
            else None
        )
        # Concurrency and capital limits for every strategy in one place. The
        # predictor is pinned to one open position, which is what the dropped
        # idx_trades_one_open index used to guarantee - so with scalping off,
        # behaviour is identical to before the index was removed.
        self.positions = PositionBook(
            max_concurrent={
                "predictor": settings.predictor_max_concurrent,
                SCALP_STRATEGY: settings.scalp_max_concurrent,
            },
            default_max_concurrent=1,
        )
        self.scalp: ScalpEngine | None = None
        if settings.scalp_enabled:
            self.scalp = ScalpEngine(
                settings=settings,
                client=self.client,
                ledger=self.ledger,
                decisions=self.decisions,
                positions=self.positions,
                mode=self.mode,
                live=live,
            )
        self.kalshi_book_stream = KalshiBookStream(
            settings=settings,
            ledger=self.ledger,
            run_id_provider=lambda: self.data_run_id,
            on_analysis=self._on_kalshi_book,
        )
        self.pair_arb: PairArbEngine | None = None
        if settings.pair_arb_enabled:
            self.pair_arb = PairArbEngine(
                settings=settings,
                ledger=self.ledger,
                decisions=self.decisions,
                mode_provider=lambda: self.mode,
            )
        # Independent of scalp_enabled on purpose. The salvage exit applies to
        # any open position, and the mode this bot has actually run live in is
        # predictor-only - gating it on the scalper would have shipped it
        # unreachable, which has happened five times in this file's history.
        self.salvage: SalvageExit | None = None
        if getattr(settings, "salvage_enabled", False):
            self.salvage = SalvageExit(
                settings=settings,
                client=self.client,
                ledger=self.ledger,
                decisions=self.decisions,
                positions=self.positions,
                mode=self.mode,
                live=live,
            )
        self.take_profit: TakeProfitExit | None = None
        if getattr(settings, "take_profit_enabled", False) and (
            settings.predictor_enabled or settings.scalp_enabled
        ):
            self.take_profit = TakeProfitExit(
                settings=settings,
                client=self.client,
                ledger=self.ledger,
                decisions=self.decisions,
                positions=self.positions,
                mode=self.mode,
                live=live,
                run_id_provider=lambda: self.data_run_id,
            )
        self.strategy_lab: StrategyLab | None = None
        if not live and getattr(settings, "strategy_lab_enabled", True):
            self.strategy_lab = StrategyLab(
                settings.strategy_lab_path,
                starting_balance=settings.strategy_lab_balance_usd,
                stake=settings.trade_budget_usd,
            )
        self._last_mode_report = 0.0
        self._stopping = False
        self._wake_event = asyncio.Event()
        self._close_lock = asyncio.Lock()
        self._closed_complete = False
        # Ticker -> monotonic deadline. Set after a rejected or unfillable
        # order so a moving book cannot be hammered every poll interval.
        self._cooldowns: dict[str, float] = {}
        # A websocket book update may qualify while the polling cycle is also
        # placing. Serialize the final ledger/venue check so both paths share
        # one concurrency invariant and cannot double-enter.
        self._predictor_placement_lock = asyncio.Lock()
        self._current_markets: dict[str, KalshiMarket] = {}
        self._event_entry_last: dict[str, float] = {}
        self._event_entry_global_last = 0.0
        self._event_entry_tasks: set[asyncio.Task[Any]] = set()
        self._event_entry_inflight: set[str] = set()
        self._event_entry_failed_until: dict[str, float] = {}
        self._price_feed_failed_until: dict[str, float] = {}
        self._rest_book_cache: dict[str, OrderBookSnapshot] = {}
        self._rest_book_refresh_task: asyncio.Task[None] | None = None
        self._last_rest_book_refresh = 0.0
        self._cycle_phase = "idle"
        self._halt_reason: str | None = None
        self._peak_balance = Decimal("0")
        self._safe_mode = False
        self._update_safe_mode_from_ledger()
        # Consecutive cycles that discovered no market at all.
        self._empty_discovery_cycles = 0
        # One line per ledger write, in a file rotation never touches. The bot
        # is the ledger's only writer, so it is also the only thing that can
        # testify that a write happened.
        self._ledger_audit = LedgerAudit(settings.log_dir / LEDGER_AUDIT_NAME)
        self._period = PeriodCounters()
        # UTC, so the hour a block claims to cover is the hour stamped on the
        # lines above it. Log rotation is the one clock that stays local,
        # because a Sunday is an operator's week rather than the exchange's.
        self._hourly = HourlyClock(datetime.now(timezone.utc))
        # Ticker -> (asset, verdict) for the pass in progress. One console line
        # is assembled from this instead of ten per asset per gate.
        self._scan_verdicts: dict[str, tuple[str, str]] = {}

    @property
    def ledger_audit(self) -> LedgerAudit:
        return self.__dict__.get("_ledger_audit") or DISABLED_LEDGER_AUDIT

    @property
    def period(self) -> PeriodCounters:
        counters = self.__dict__.get("_period")
        if counters is None:
            counters = PeriodCounters()
            self._period = counters
        return counters

    @property
    def scan_verdicts(self) -> dict[str, tuple[str, str]]:
        verdicts = self.__dict__.get("_scan_verdicts")
        if verdicts is None:
            verdicts = {}
            self._scan_verdicts = verdicts
        return verdicts

    def stop(self, *_: Any) -> None:
        if getattr(self, "_stopping", False):
            return
        self._stopping = True
        self._wake_event.set()
        client = getattr(self, "client", None)
        if client is not None and hasattr(client, "stop_order_submission"):
            client.stop_order_submission()
        _event.warning("Shutdown requested; refusing new orders and cleaning up safely")

    async def close(self) -> None:
        """Best-effort, exactly-once teardown for signals and normal exits.

        A failed quote cancellation must not leak the websocket, HTTP pools,
        or SQLite handles behind it.  Each resource therefore gets an isolated
        cleanup attempt, while the lock makes concurrent/repeated shutdown
        requests harmless.
        """
        lock = getattr(self, "_close_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._close_lock = lock
        async with lock:
            if getattr(self, "_closed_complete", False):
                return

            self._stopping = True
            wake = getattr(self, "_wake_event", None)
            if wake is not None:
                wake.set()
            client = getattr(self, "client", None)
            if client is not None and hasattr(client, "stop_order_submission"):
                client.stop_order_submission()

            failures: list[str] = []

            async def async_step(label: str, action: Callable[[], Any]) -> None:
                try:
                    await action()
                except Exception as exc:  # noqa: BLE001 - continue teardown
                    failures.append(label)
                    logger.error("Shutdown step '{}' failed: {}", label, exc)

            def sync_step(label: str, action: Callable[[], Any]) -> None:
                try:
                    action()
                except Exception as exc:  # noqa: BLE001 - continue teardown
                    failures.append(label)
                    logger.error("Shutdown step '{}' failed: {}", label, exc)

            # First: a resting quote left on the venue after this process exits
            # is an order nothing is watching, and in live mode it can fill.
            scalp = getattr(self, "scalp", None)
            if scalp is not None:
                await async_step("cancel resting scalp orders", scalp.drain)
            salvage = getattr(self, "salvage", None)
            if salvage is not None:
                sync_step(
                    "record salvage funnel",
                    lambda: salvage.record_funnel("shutdown"),
                )
            take_profit = getattr(self, "take_profit", None)
            if take_profit is not None:
                sync_step(
                    "record take-profit funnel",
                    lambda: take_profit.record_funnel("shutdown"),
                )
            pair_arb = getattr(self, "pair_arb", None)
            if pair_arb is not None:
                sync_step(
                    "record pair-arb funnel",
                    lambda: pair_arb.record_funnel("shutdown"),
                )
            strategy_lab = getattr(self, "strategy_lab", None)
            if strategy_lab is not None:
                sync_step("snapshot strategy lab", strategy_lab.snapshot)
                sync_step("close strategy lab", strategy_lab.close)

            book_stream = getattr(self, "kalshi_book_stream", None)
            if book_stream is not None:
                await async_step("close Kalshi book stream", book_stream.close)
            rest_book_task = getattr(self, "_rest_book_refresh_task", None)
            if rest_book_task is not None:
                rest_book_task.cancel()
                await asyncio.gather(rest_book_task, return_exceptions=True)
                self._rest_book_refresh_task = None
            event_tasks = list(getattr(self, "_event_entry_tasks", ()))
            for task in event_tasks:
                task.cancel()
            if event_tasks:
                await asyncio.gather(*event_tasks, return_exceptions=True)

            price_stream = getattr(self, "price_stream", None)
            if price_stream is not None:
                await async_step("close Kraken stream", price_stream.close)
            for label, source in (
                ("close Kraken REST source", getattr(self, "_rest_prices", None)),
                ("close commodity source", getattr(self, "_commodity_prices", None)),
                ("close Yahoo source", getattr(self, "_yahoo_prices", None)),
                (
                    "close book reconciliation client",
                    getattr(self, "_book_reconcile_client", None),
                ),
                (
                    "close Kalshi discovery client",
                    getattr(self, "_discovery_client", None),
                ),
                ("close Kalshi client", client),
            ):
                if source is not None:
                    await async_step(label, source.close)

            ledger = getattr(self, "ledger", None)
            run_id = getattr(self, "data_run_id", None)
            if ledger is not None and run_id is not None:
                sync_step(
                    "finish data run",
                    lambda: ledger.finish_data_run(
                        run_id,
                        completed=getattr(self, "_data_run_completed", False),
                    ),
                )
            if ledger is not None:
                sync_step("close trade ledger", ledger.close)
            decisions = getattr(self, "decisions", None)
            if decisions is not None:
                # Last: it owns the open run for every ticker, including the
                # moment the process stopped.
                sync_step("close decision log", decisions.close)

            self._closed_complete = True
            if failures:
                logger.warning(
                    "Shutdown completed with {} cleanup failure(s): {}",
                    len(failures),
                    ", ".join(failures),
                )

    async def _start_price_stream(self) -> None:
        """Bring the candle stream up, or carry on without it.

        A stream that will not start is not a reason to refuse to trade: the
        REST source underneath it is the one the bot has always used, and
        ``get_one_minute_candles`` falls through to it on every read the stream
        cannot serve. Failing to start is therefore a downgrade, not an outage,
        and it says so rather than dying.
        """
        # Complementary-pair arbitrage is price-identity work on Kalshi's own
        # book. Starting Kraken there would add an unrelated venue dependency
        # to the explicitly Kalshi-only strategy.
        settings = getattr(self, "settings", None)
        if (
            getattr(settings, "pair_arb_enabled", False)
            and not getattr(settings, "predictor_enabled", False)
            and not getattr(settings, "scalp_enabled", False)
        ):
            return
        # getattr, because the test suite builds this object with
        # object.__new__ to drive one method without a venue or a network.
        stream = getattr(self, "price_stream", None)
        if stream is None:
            return
        try:
            await stream.start()
        except Exception as exc:  # noqa: BLE001 - a downgrade, never fatal
            logger.warning(
                "Kraken stream would not start ({}); falling back to REST "
                "polling for every read.",
                exc,
            )
            return
        health = stream.health()
        _event.info(
            "Kraken stream: {} assets warmed, {} candles each; REST remains "
            "the fallback past {:.0f}s without an update",
            health["assets"],
            min(health["windows"].values()) if health["windows"] else 0,
            self.settings.kraken_stream_stale_seconds,
        )

    async def _log_live_balance(self, message: str) -> Decimal:
        """Show aggregate cash and the matcher-specific amounts orders can use."""
        balance = await self.client.get_available_balance()
        get_breakdown = getattr(self.client, "cached_balance_breakdown", None)
        breakdown = get_breakdown() if callable(get_breakdown) else {}
        if breakdown:
            detail = ", ".join(
                f"index {index}=${amount.quantize(Decimal('0.01'))}"
                for index, amount in sorted(breakdown.items())
            )
            logger.warning(
                "{}. Aggregate Kalshi balance: ${} | spendable by exchange: {}",
                message,
                balance.quantize(Decimal("0.01")),
                detail,
            )
        else:
            logger.warning(
                "{}. Available Kalshi balance: ${}",
                message,
                balance.quantize(Decimal("0.01")),
            )
        return balance

    async def run(self, once: bool = False) -> None:
        mode_label = self.mode.upper()
        await self._start_price_stream()
        if self._stopping:
            return
        if getattr(self, "kalshi_book_stream", None) is not None:
            await self.kalshi_book_stream.start()
        if self._stopping:
            return
        _event.info(
            "{} mode | Kalshi {} | assets={} | fixed trade budget=${}",
            mode_label,
            self.settings.environment,
            ",".join(self.settings.assets),
            self.settings.trade_budget_usd,
        )
        if self.live:
            await self._log_live_balance("Live execution enabled")
        else:
            self._log_paper_summary()

        if self._stopping:
            return

        _scan.info(
            "scan  active | first pass starting | heartbeat every {:.0f}s",
            self.settings.scan_log_interval_seconds,
        )

        # The command line is the operator speaking now; a request left in the
        # control file is the operator speaking last week. Starting up adopts
        # the flag it was launched with rather than resuming a stale request.
        self._report_mode(adopt_request=True)

        while not self._stopping:
            try:
                self._maybe_log_hourly()
                # With scalping off this is unchanged: one open position takes
                # over the loop until it settles. With scalping on nothing may
                # block, because a scalper holds several positions at once and
                # sleeping on one of them would stop the others being exited -
                # which is the mechanism that bounds this strategy's loss. In
                # that mode every position, the predictor's included, is settled
                # by polling inside run_cycle.
                open_trade = (
                    None
                    if self._supervises_positions
                    else self.ledger.get_open_trade(
                        self.mode,
                        self.settings.environment,
                    )
                )
                if open_trade:
                    # A queued switch waits. Changing books under an open
                    # position would monitor it against the wrong ledger and
                    # settle it against the wrong balance.
                    self._report_mode()
                    await self._monitor_open_trade(open_trade, once=once)
                else:
                    await self._apply_requested_mode()
                    if self._halt_reason is not None:
                        logger.error(
                            "Trading halted: {}. No further orders will be placed.",
                            self._halt_reason,
                        )
                        return
                    cycle_started = time.monotonic()
                    placed = await self._run_cycle_with_status()
                    if not placed and not once:
                        elapsed = time.monotonic() - cycle_started
                        await self._interruptible_wait(
                            max(
                                Decimal("0.25"),
                                Decimal(
                                    str(self.settings.poll_interval_seconds - elapsed)
                                ),
                            )
                        )
            except Exception:
                logger.exception("Trading cycle failed")
                if not once:
                    await self._interruptible_wait(
                        Decimal(str(self.settings.poll_interval_seconds))
                    )
            if once:
                return

    # ------------------------------------------------------- mode switch --
    def _live_prerequisites(self) -> str | None:
        """The reason live is unavailable to *this process*, or None.

        Settings are read from .env once at startup, so editing .env under a
        running bot does not change the answer. The dashboard needs to be able
        to say exactly that, rather than report an opaque refusal, so the
        reason travels into the control file.
        """
        try:
            self.settings.validate_live(True)
        except RuntimeError as exc:
            return str(exc)
        return None

    def _report_mode(self, *, adopt_request: bool = False, force: bool = False) -> None:
        """Heartbeat: what is running, and whether it could run live.

        Throttled, because the alternative is an fsync every poll interval for
        a value that changes a few times a day. A dashboard reading a report
        older than a few minutes is meant to conclude nobody is listening.
        """
        if self.mode_control is None:
            return
        now = time.monotonic()
        if not (adopt_request or force) and (now - self._last_mode_report) < 30.0:
            return
        blocked = self._live_prerequisites()
        try:
            self.mode_control.report_active(
                self.mode,
                live_available=blocked is None,
                note="" if blocked is None else blocked,
                adopt_request=adopt_request,
            )
        except OSError as exc:
            # A control file that cannot be written is a lost dashboard
            # feature, never a reason to stop trading.
            logger.warning("Could not write the mode control file: {}", exc)
        self._last_mode_report = now

    async def _apply_requested_mode(self) -> None:
        """Adopt a mode requested by the dashboard. Called only when flat."""
        if self.mode_control is None:
            return
        state = self.mode_control.read()
        if state.requested == self.mode:
            self._report_mode()
            return

        target = state.requested
        if target == "live":
            blocked = self._live_prerequisites()
            if blocked is not None:
                # Refuse, and withdraw the request: leaving it standing would
                # re-refuse on every cycle and bury the reason in a log loop.
                logger.error(
                    "Refusing the dashboard's request to trade live: {}", blocked
                )
                self.decisions.record_event(
                    "mode_switch_refused",
                    requested=target,
                    active=self.mode,
                    reason=blocked,
                    requested_by=state.requested_by,
                )
                try:
                    self.mode_control.request(self.mode, by="bot (refused)")
                except OSError:
                    pass
                self._report_mode(force=True)
                return

        previous = self.mode
        logger.warning(
            "Switching from {} to {} at the request of {}",
            previous.upper(),
            target.upper(),
            state.requested_by or "the dashboard",
        )
        rest_book_task = getattr(self, "_rest_book_refresh_task", None)
        if rest_book_task is not None:
            rest_book_task.cancel()
            await asyncio.gather(rest_book_task, return_exceptions=True)
            self._rest_book_refresh_task = None
        getattr(self, "_rest_book_cache", {}).clear()
        self._last_rest_book_refresh = 0.0
        await self.client.close()
        ledger = getattr(self, "ledger", None)
        run_id = getattr(self, "data_run_id", None)
        if ledger is not None and run_id is not None:
            ledger.finish_data_run(run_id, completed=True)
        self.live = target == "live"
        self.mode = target
        if ledger is not None and run_id is not None:
            self.data_run_id = ledger.start_data_run(
                mode=self.mode,
                environment=self.settings.environment,
                strategy=getattr(self, "_strategy_label", "both"),
                assets=getattr(self.settings, "assets", ()),
                **getattr(self, "_run_provenance", {}),
            )
        self.client = AsyncKalshiTradingClient(self.settings, authenticated=self.live)
        if ledger is not None and hasattr(self.client, "bind_event_ledger"):
            self.client.bind_event_ledger(ledger, lambda: self.data_run_id)
        self._rebind_collaborators()
        # Both are per-book: a drawdown measured against the paper balance says
        # nothing about the live one, and a cooldown belongs to a book's orders.
        self._peak_balance = Decimal("0")
        self._cooldowns.clear()
        self.decisions.record_event(
            "mode_switch",
            previous=previous,
            active=self.mode,
            requested_by=state.requested_by,
            environment=self.settings.environment,
        )
        if self.live:
            await self._log_live_balance("LIVE execution is now enabled")
        else:
            self._log_paper_summary()
        self._report_mode(force=True)

    # Every collaborator that holds a client, a mode or a live flag. Adding an
    # engine means adding it here; test_mode_switch.py walks the bot's own
    # attributes and fails if one is left out, so this cannot be forgotten
    # silently the way it was.
    _COLLABORATORS = ("scalp", "salvage", "take_profit")

    def _rebind_collaborators(self) -> None:
        """Point every engine at the current client, mode and book.

        The switch above closes the old client and builds a new one. Engines
        captured the *old* object at construction, so without this they keep a
        closed aiohttp session - which is exactly the
        ``RuntimeError: Session is closed`` that surfaced from the scalper - and,
        worse and silently, a stale ``live`` flag that would have had them
        writing live ledger rows while the bot reported PAPER.
        """
        for name in self._COLLABORATORS:
            engine = getattr(self, name, None)
            if engine is None:
                continue
            engine.rebind(client=self.client, mode=self.mode, live=self.live)

    @staticmethod
    def _market_with_book(
        market: KalshiMarket,
        snapshot: OrderBookSnapshot | None,
    ) -> KalshiMarket:
        """Apply one internally consistent executable touch to discovery data."""
        analysis = snapshot.analysis if snapshot is not None else None
        if analysis is None:
            return market
        return replace(
            market,
            yes_bid=analysis.yes_bid,
            yes_ask=analysis.yes_ask,
            yes_bid_size=analysis.yes_bid_size,
            # Kalshi's NO bid is the executable YES ask.
            yes_ask_size=analysis.no_bid_size,
        )

    def _canonical_book_snapshots(
        self,
        markets: list[KalshiMarket],
    ) -> dict[str, OrderBookSnapshot]:
        """Choose the freshest coherent book without making a network request."""
        settings = getattr(self, "settings", None)
        maximum_age = float(getattr(settings, "kalshi_websocket_stale_seconds", 10.0))
        now = datetime.now(timezone.utc)
        stream = getattr(self, "kalshi_book_stream", None)
        rest_cache = getattr(self, "_rest_book_cache", {})
        selected: dict[str, OrderBookSnapshot] = {}
        for market in markets:
            snapshot = (
                stream.snapshot(market.ticker, max_age_seconds=maximum_age)
                if stream is not None
                else None
            )
            if snapshot is None:
                candidate = rest_cache.get(market.ticker)
                if candidate is not None:
                    age = (now - candidate.observed_at).total_seconds()
                    if age <= maximum_age:
                        snapshot = candidate
            if snapshot is not None:
                selected[market.ticker] = snapshot
        return selected

    def _schedule_rest_book_reconciliation(
        self,
        markets: list[KalshiMarket],
    ) -> None:
        """Refresh selected REST books independently of the decision cycle."""
        settings = getattr(self, "settings", None)
        if (
            settings is None
            or not getattr(settings, "market_data_enabled", False)
            or not hasattr(self.client, "get_orderbook_snapshot")
        ):
            return
        active = getattr(self, "_rest_book_refresh_task", None)
        if active is not None and not active.done():
            return
        now = time.monotonic()
        interval = float(getattr(settings, "orderbook_rest_reconcile_seconds", 30.0))
        if now - getattr(self, "_last_rest_book_refresh", 0.0) < interval:
            return
        recording_assets = set(
            getattr(settings, "orderbook_recording_assets", settings.assets)
        )
        targets = [market for market in markets if market.asset in recording_assets]
        if not targets:
            return
        self._last_rest_book_refresh = now

        async def refresh() -> None:
            try:
                client = getattr(self, "_book_reconcile_client", self.client)
                results = await asyncio.gather(
                    *(
                        client.get_orderbook_snapshot(market.ticker)
                        for market in targets
                    ),
                    return_exceptions=True,
                )
                cache = getattr(self, "_rest_book_cache", None)
                if cache is None:
                    cache = {}
                    self._rest_book_cache = cache
                for market, result in zip(targets, results):
                    if not isinstance(result, BaseException) and result is not None:
                        cache[market.ticker] = result
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # diagnostics/reconciliation is non-fatal
                logger.warning("REST book reconciliation failed: {}", exc)
            finally:
                self._rest_book_refresh_task = None

        self._rest_book_refresh_task = asyncio.create_task(
            refresh(), name="rest-book-reconciliation"
        )

    async def run_cycle(
        self,
        evaluation_counts: dict[str, int] | None = None,
    ) -> bool:
        """Scan all assets concurrently and place at most one highest-edge trade."""
        self._cycle_phase = "discovering Kalshi markets"
        self._update_safe_mode_from_ledger()
        discovery = getattr(self, "_discovery_client", self.client)
        fresh_markets = await discovery.get_active_markets()
        if getattr(self, "_stopping", False):
            return False
        failed_assets = set(
            getattr(discovery, "last_discovery_failed_assets", frozenset())
        )
        markets = list(fresh_markets)
        degraded_assets: set[str] = set()
        if fresh_markets and failed_assets:
            now = datetime.now(timezone.utc)
            fresh_assets = {market.asset for market in fresh_markets}
            cached = [
                market
                for market in getattr(self, "_current_markets", {}).values()
                if market.asset in failed_assets
                and market.asset not in fresh_assets
                and market.close_time > now
            ]
            markets.extend(cached)
            degraded_assets.update(market.asset for market in cached)
        discovery_degraded = False
        if not markets:
            now = datetime.now(timezone.utc)
            markets = [
                market
                for market in getattr(self, "_current_markets", {}).values()
                if market.asset in self.settings.assets and market.close_time > now
            ]
            discovery_degraded = bool(markets)
            degraded_assets.update(market.asset for market in markets)
            if discovery_degraded:
                logger.warning(
                    "Kalshi REST discovery is unavailable; retaining {} current "
                    "market(s) for position supervision only—new entries are blocked",
                    len(markets),
                )
        self._discovery_degraded = discovery_degraded
        self._degraded_discovery_assets = degraded_assets
        if markets and not discovery_degraded:
            self._current_markets = {market.ticker: market for market in markets}
        self._cycle_phase = f"synchronizing {len(markets)} market books"
        if markets and getattr(self, "kalshi_book_stream", None) is not None:
            await self.kalshi_book_stream.update_markets(
                market.ticker for market in markets
            )
        if not markets:
            self._empty_discovery_cycles += 1
            # A cycle that discovers nothing writes no decision records, so an
            # outage leaves a hole in decisions.jsonl that reads exactly like a
            # stopped process. Record the outage itself, and escalate once it
            # outlasts a single 15-minute market, so neither the log nor the
            # console can present a blind bot as a working one.
            if (
                self._empty_discovery_cycles == 1
                or self._empty_discovery_cycles % EMPTY_DISCOVERY_RECORD_EVERY == 0
            ):
                self.decisions.record_event(
                    "discovery_empty",
                    mode=self.mode,
                    environment=self.settings.environment,
                    assets=list(self.settings.assets),
                    consecutive_empty_cycles=self._empty_discovery_cycles,
                )
                emit = (
                    logger.error
                    if self._empty_discovery_cycles >= EMPTY_DISCOVERY_ALERT_CYCLES
                    else logger.warning
                )
                emit(
                    "No active requested Kalshi 15-minute markets "
                    "({} consecutive cycle(s)); no market can be evaluated",
                    self._empty_discovery_cycles,
                )
            return False
        if self._empty_discovery_cycles and not discovery_degraded:
            logger.warning(
                "Market discovery recovered after {} empty cycle(s)",
                self._empty_discovery_cycles,
            )
            self.decisions.record_event(
                "discovery_recovered",
                mode=self.mode,
                environment=self.settings.environment,
                empty_cycles=self._empty_discovery_cycles,
            )
            self._empty_discovery_cycles = 0

        # The stream/cache read is local and immediate. REST depth refreshes are
        # deliberately scheduled after discovery and never awaited here, so
        # analytical capture cannot stretch the trading cadence.
        self._schedule_rest_book_reconciliation(markets)
        book_snapshots = self._canonical_book_snapshots(markets)
        markets = [
            self._market_with_book(market, book_snapshots.get(market.ticker))
            for market in markets
        ]
        self._current_markets = {market.ticker: market for market in markets}

        if evaluation_counts is not None:
            for market in markets:
                evaluation_counts[market.asset] = (
                    evaluation_counts.get(market.asset, 0) + 1
                )

        cycle_id = uuid.uuid4().hex[:12]
        self.scan_verdicts.clear()
        self._cycle_predictions = {}
        self._cycle_prediction_attempts: set[str] = set()
        settings = getattr(self, "settings", None)
        self._cycle_phase = f"evaluating {len(markets)} markets"
        # The predictor still *evaluates* when disabled - its predictions are
        # what the scalper's pattern gate reads - but it never places. Skipping
        # evaluation entirely would blind the scalper to every market the
        # predictor happens to look at first.
        results = (
            [None for _ in markets]
            if (
                getattr(settings, "pair_arb_enabled", False)
                and not getattr(settings, "predictor_enabled", False)
                and not getattr(settings, "scalp_enabled", False)
            )
            else await asyncio.gather(
                *(self._evaluate_market(market, cycle_id) for market in markets),
                return_exceptions=True,
            )
        )
        self._cycle_phase = f"recording {len(markets)} market snapshots"
        await self._record_data_pass(cycle_id, markets, book_snapshots)
        if getattr(self, "pair_arb", None) is not None:
            for market in markets:
                snapshot = book_snapshots.get(market.ticker)
                if snapshot is not None:
                    self.pair_arb.scan(
                        run_id=self.data_run_id,
                        cycle_id=cycle_id,
                        market=market,
                        snapshot=snapshot,
                    )
        # The scalper runs on the same discovery and the same predictions, after
        # every market has been evaluated so it sees a complete probability map.
        # It is deliberately independent of the predictor's verdict: a market the
        # predictor refused for want of directional edge is still quotable.
        self._cycle_phase = "servicing positions and scalp quotes"
        await self._service_positions_pass(markets, book_snapshots)
        candidates: list[TradeCandidate] = []
        for market, result in zip(markets, results):
            if isinstance(result, BaseException):
                if isinstance(result, (httpx.HTTPError, asyncio.TimeoutError)):
                    logger.warning(
                        "{}: price feed unavailable ({}); skipped this pass and "
                        "entered the feed cooldown",
                        market.asset,
                        type(result).__name__,
                    )
                else:
                    logger.opt(exception=result).error(
                        "{}: evaluation failed for {}", market.asset, market.ticker
                    )
                continue
            if result is not None:
                candidates.append(result)

        if discovery_degraded:
            for candidate in candidates:
                if candidate.trace is not None:
                    candidate.trace.gate(
                        "fresh_market_discovery",
                        False,
                        "Kalshi REST discovery is degraded; supervision only",
                    )
                    candidate.trace.finish("skipped", "discovery degraded")
            self._log_scan(None)
            return False

        # A signal can arrive while the concurrent evaluations are in flight.
        # Their diagnostics are safe to finish, but no candidate from that
        # now-stopped pass may cross the order boundary.
        if getattr(self, "_stopping", False):
            self._log_scan(None)
            return False

        candidates.sort(
            key=lambda candidate: (
                candidate.consensus_tail,
                candidate.edge,
                candidate.outcome_probability,
            ),
            reverse=True,
        )
        if not candidates:
            self._log_scan(None)
            return False
        limit = getattr(settings, "predictor_max_candidates_per_cycle", 1)
        ledger = getattr(self, "ledger", None)
        open_clusters = (
            {
                _risk_cluster(trade.asset)
                for trade in ledger.get_open_trades(
                    self.mode, settings.environment, "predictor"
                )
            }
            if ledger is not None and settings is not None
            else set()
        )
        placed_count = 0
        self._cycle_phase = f"ranking {len(candidates)} eligible candidates"
        first_selected: TradeCandidate | None = None
        for candidate in candidates:
            cluster = _risk_cluster(candidate.market.asset)
            trace = candidate.trace
            if cluster in open_clusters:
                if trace is not None:
                    trace.gate(
                        "risk_cluster",
                        False,
                        f"an open predictor position already occupies {cluster}",
                    )
                    trace.finish("skipped", "correlated risk cluster already open")
                continue
            if placed_count >= limit:
                if trace is not None:
                    trace.gate(
                        "cycle_candidate_limit",
                        False,
                        f"this pass already placed {placed_count} candidate(s)",
                    )
                    trace.finish("skipped", "per-cycle candidate limit")
                continue
            if first_selected is None:
                first_selected = candidate
            logger.debug(
                "Selected {} {} ({}) from {} eligible candidate(s), edge {:.1%}",
                candidate.market.asset,
                candidate.outcome.upper(),
                cluster,
                len(candidates),
                float(candidate.edge),
            )
            if await self._place_candidate(candidate):
                placed_count += 1
                open_clusters.add(cluster)
        self._log_scan(first_selected)
        return placed_count > 0

    async def _run_cycle_with_status(
        self,
        evaluation_counts: dict[str, int] | None = None,
    ) -> bool:
        """Run one pass and report liveness while a slow pass is unfinished.

        ``_log_scan`` can only summarize a completed pass. External feeds,
        transport recovery, and durable depth recording can make one pass last
        longer than the configured console interval, which previously made an
        active bot look frozen. This heartbeat reports the current phase until
        the ordinary per-asset summary becomes available.
        """
        interval = float(
            getattr(
                getattr(self, "settings", None),
                "scan_log_interval_seconds",
                10.0,
            )
        )
        if interval <= 0:
            return await self.run_cycle(evaluation_counts=evaluation_counts)

        started = time.monotonic()
        pass_number = getattr(self, "_completed_cycles", 0) + 1

        async def heartbeat() -> None:
            while not getattr(self, "_stopping", False):
                await asyncio.sleep(interval)
                if getattr(self, "_stopping", False):
                    return
                price_health = "price stream disabled"
                price_stream = getattr(self, "price_stream", None)
                if price_stream is not None:
                    health = price_stream.health()
                    age = health.get("seconds_since_message")
                    price_health = (
                        "Kraken live" if health.get("live") else "Kraken REST fallback"
                    )
                    if age is not None:
                        price_health += f" age={age:.1f}s"
                book_health = "book stream disabled"
                book_stream = getattr(self, "kalshi_book_stream", None)
                if book_stream is not None:
                    health = book_stream.health()
                    book_health = (
                        f"books={health.get('books', 0)} "
                        f"connected={'yes' if health.get('connected') else 'no'}"
                    )
                _scan.info(
                    "scan  working {:.0f}s | pass {} | {} | {} | {}",
                    time.monotonic() - started,
                    pass_number,
                    getattr(self, "_cycle_phase", "working"),
                    price_health,
                    book_health,
                )
                self._report_mode()

        task = asyncio.create_task(heartbeat(), name="scan-status-heartbeat")
        try:
            result = await self.run_cycle(evaluation_counts=evaluation_counts)
            self._completed_cycles = pass_number
            return result
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._cycle_phase = "idle"

    async def _on_kalshi_book(self, ticker: str, analysis: Any) -> None:
        """Turn each valid book change into exit and same-gate entry checks."""
        if getattr(self, "_stopping", False):
            return
        take_profit = getattr(self, "take_profit", None)
        if take_profit is not None:
            try:
                await take_profit.on_book(ticker, analysis)
            except Exception:  # noqa: BLE001 - REST timer remains the fallback
                logger.exception(
                    "Event-triggered take-profit evaluation failed for {}", ticker
                )

        settings = getattr(self, "settings", None)
        market = getattr(self, "_current_markets", {}).get(ticker)
        if (
            settings is None
            or market is None
            or not getattr(settings, "event_driven_entry_enabled", False)
            or not getattr(settings, "predictor_enabled", True)
            or getattr(self, "_halt_reason", None) is not None
            or self._market_discovery_degraded(market)
        ):
            return
        now_mono = time.monotonic()
        inflight = self.__dict__.setdefault("_event_entry_inflight", set())
        failed_until = self.__dict__.setdefault("_event_entry_failed_until", {})
        if ticker in inflight or now_mono < failed_until.get(ticker, 0.0):
            return
        minimum = getattr(settings, "event_entry_min_interval_seconds", 2.0)
        last_by_ticker = getattr(self, "_event_entry_last", {}).get(ticker, 0.0)
        last_global = getattr(self, "_event_entry_global_last", 0.0)
        if now_mono - last_by_ticker < minimum or now_mono - last_global < 0.5:
            return
        self._event_entry_last[ticker] = now_mono
        self._event_entry_global_last = now_mono
        inflight.add(ticker)
        task = asyncio.create_task(
            self._event_entry_check(market, analysis),
            name=f"event-entry-{ticker}",
        )
        self._event_entry_tasks.add(task)

        def completed(done: asyncio.Task[Any]) -> None:
            self._event_entry_tasks.discard(done)
            inflight.discard(ticker)

        task.add_done_callback(completed)

    async def _event_entry_check(self, market: KalshiMarket, analysis: Any) -> None:
        """Re-evaluate one changed book through the production entry pipeline."""
        try:
            if getattr(self, "_stopping", False):
                return
            updated = replace(
                market,
                yes_bid=analysis.yes_bid,
                yes_ask=analysis.yes_ask,
                yes_bid_size=analysis.yes_bid_size,
                # NO bids are the offers consumed by a YES buy.
                yes_ask_size=analysis.no_bid_size,
            )
            remaining = (
                updated.close_time - datetime.now(timezone.utc)
            ).total_seconds()
            if not (
                self.settings.minimum_seconds_to_close
                <= remaining
                <= self.settings.entry_window_seconds
            ):
                return
            candidate = await self._evaluate_market(
                updated, f"event-{uuid.uuid4().hex[:10]}"
            )
            if candidate is not None and not getattr(self, "_stopping", False):
                await self._place_candidate(candidate)
        except (httpx.HTTPError, asyncio.TimeoutError) as exc:
            cooldown = float(
                getattr(self.settings, "order_failure_cooldown_seconds", 60)
            )
            failed_until = self.__dict__.setdefault("_event_entry_failed_until", {})
            failed_until[market.ticker] = time.monotonic() + cooldown
            logger.warning(
                "{}: event entry skipped because its price feed failed ({}); "
                "REST polling remains active, retrying event evaluation in {:.0f}s",
                market.asset,
                type(exc).__name__,
                cooldown,
            )
        except Exception:  # noqa: BLE001 - polling remains authoritative
            logger.exception(
                "Event-triggered entry evaluation failed for {}", market.ticker
            )

    @property
    def _supervises_positions(self) -> bool:
        """Whether the cycle polls open positions instead of sleeping on one.

        True when the scalper may hold several positions or take-profit must
        inspect a predictor position repeatedly. Blocking on the oldest would
        make either service unreachable until settlement.

        getattr, because the tests drive this class with instances built via
        ``object.__new__``: the predicate must degrade to "do not supervise"
        rather than raise on the path holding a position.
        """
        return (
            getattr(self, "scalp", None) is not None
            or getattr(self, "take_profit", None) is not None
        )

    async def _service_positions_pass(
        self,
        markets: list[KalshiMarket],
        book_snapshots: dict[str, Any] | None = None,
    ) -> None:
        """Service every open position, then offer what capital is left.

        Order matters and is not arbitrary. Settlement frees capital, exits free
        more, resting quotes are advanced before new ones are considered, and
        only then is what remains offered to the book. Quoting first would size
        against capital that a settlement in the same cycle was about to
        release, and would refuse trades it could afford.

        A failure here is contained: an exception while servicing must not take
        down the evaluation path or strand an open position.
        """
        if getattr(self, "_stopping", False):
            return
        if not self._supervises_positions:
            return
        books = {market.ticker: market for market in markets}
        predictions = dict(self._cycle_predictions)
        try:
            # The predictor refuses a market outside ENTRY_WINDOW_SECONDS before
            # it ever computes a probability, so _cycle_predictions is empty for
            # exactly the markets a maker most wants: the ones with time left to
            # rest a quote and be filled. Fill the gaps here rather than reorder
            # the predictor's gates, which would spend a Kraken call on every
            # market on every pass whether anything needed it or not.
            self._cycle_phase = "pricing fallback markets"
            await self._fill_missing_predictions(markets, predictions)
            if getattr(self, "_stopping", False):
                return
            # The quote and position services only need P(yes); assess()
            # needs the whole prediction, because the pattern gate reads the
            # regime label and the settlement margin off it.
            probabilities = {
                ticker: prediction.probability_yes
                for ticker, prediction in predictions.items()
            }
            try:
                self._cycle_phase = "updating strategy-lab shadow wallets"
                await self._strategy_lab_pass(
                    markets, predictions, book_snapshots or {}
                )
            except Exception as exc:  # shadow wallets never block live supervision
                logger.warning(
                    "Strategy-lab observation skipped: Kalshi read unavailable ({})",
                    type(exc).__name__,
                )
            if getattr(self, "_stopping", False):
                return
            # Model-only reversal stays in the isolated strategy lab. The
            # production path exits on executable net profit and can later make
            # one bounded, opposite-side re-entry after it is durably flat.
            # Before settlement: a position sold in its final seconds must
            # leave the OPEN set before the settler sees it, or the ledger
            # would be asked to both exit and settle the same row.
            try:
                self._cycle_phase = "checking final-window salvage"
                await self._salvage_pass()
            except Exception as exc:
                logger.warning(
                    "Salvage pass deferred: Kalshi read unavailable ({})",
                    type(exc).__name__,
                )
            self._cycle_phase = "checking position settlements"
            await self._settle_scalp_positions(books)
            if getattr(self, "_stopping", False):
                return
            if self.take_profit is not None:
                self._cycle_phase = "reassessing live exits"
                analyses = {
                    ticker: snapshot.analysis
                    for ticker, snapshot in (book_snapshots or {}).items()
                    if snapshot.analysis is not None
                }
                await self.take_profit.run(
                    self.ledger.get_open_trades(self.mode, self.settings.environment),
                    analyses=analyses,
                    probabilities=probabilities,
                    predictions=predictions,
                )
            if getattr(self, "_stopping", False):
                return
            if self.scalp is None:
                return
            self._cycle_phase = "servicing existing scalp quotes"
            await self.scalp.service_quotes(books, probabilities)
            if getattr(self, "_discovery_degraded", False):
                self.scalp.stats.block("discovery_degraded")
                return
            if self._safe_mode:
                self.scalp.stats.block("safe_mode")
                return

            try:
                self._cycle_phase = "verifying spendable balance"
                available = await self._available_cash()
            except Exception as exc:  # fail closed; position service already ran
                self.scalp.stats.block("balance_unavailable")
                now_mono = time.monotonic()
                last = getattr(self, "_last_balance_warning", 0.0)
                if now_mono - last >= 60.0:
                    self._last_balance_warning = now_mono
                    logger.warning(
                        "New scalp entries skipped: Kalshi balance unavailable ({})",
                        type(exc).__name__,
                    )
                return
            if getattr(self, "_stopping", False):
                return
            # Shared by every quote in this pass: the exposure denominator must
            # not drift between two quotes placed in the same cycle, or the
            # second is measured against an account the first already changed.
            # _available_cash has just read it, so this costs nothing.
            portfolio = getattr(self, "_last_portfolio_value", None)
            self._cycle_phase = "assessing new scalp entries"
            for market in markets:
                if getattr(self, "_stopping", False):
                    return
                if self._market_discovery_degraded(market):
                    self.scalp.stats.block("discovery_degraded")
                    continue
                if self.scalp.quotes.has(SCALP_STRATEGY, market.ticker):
                    continue
                # Never open a position the session will not be around to
                # supervise. A market closing after the run ends has no exit
                # path and settles unwatched, which is exactly how the largest
                # single loss of the first session happened.
                if self._session_deadline is not None:
                    # The requirement is only that the market close - and be
                    # settled - before the run ends. A blanket "no new positions
                    # in the last N seconds" also refused markets closing well
                    # inside the window: on measured data that cost 434 refusals
                    # in one session, most of them for trades that would have
                    # settled comfortably in time.
                    settles_by = (
                        market.close_time.timestamp()
                        + self.settings.status_check_lead_seconds
                    )
                    if settles_by > self._session_wall_deadline:
                        self.scalp.stats.block("session_tail")
                        continue
                prediction = predictions.get(market.ticker)
                if prediction is None:
                    continue
                seconds_to_close = Decimal(
                    str(
                        (market.close_time - datetime.now(timezone.utc)).total_seconds()
                    )
                )
                # One depth read per candidate market, not per pass: the book
                # analysis is only consulted for markets that already cleared
                # the cheap gates, so this does not add a request per asset per
                # cycle.
                book = None
                if self.settings.scalp_orderbook_enabled:
                    snapshot = (book_snapshots or {}).get(market.ticker)
                    book = (
                        snapshot.analysis
                        if snapshot is not None
                        else await self.client.get_book_analysis(market.ticker)
                    )
                if getattr(self, "_stopping", False):
                    return
                quote = self.scalp.assess(
                    market,
                    prediction,
                    seconds_to_close=seconds_to_close,
                    available_cash=available,
                    book=book,
                )
                if quote is None:
                    continue
                if getattr(self, "_stopping", False):
                    return
                placed = await self.scalp.place_quote(
                    market,
                    quote,
                    available_cash=available,
                    book=book,
                    portfolio_value=portfolio,
                )
                if placed is not None:
                    available -= placed.contracts * placed.price
        except Exception:
            logger.exception("Position servicing failed; evaluation is unaffected")

    async def _strategy_lab_pass(
        self,
        markets: list[KalshiMarket],
        predictions: dict[str, Prediction],
        book_snapshots: dict[str, Any],
    ) -> None:
        """Give every experimental wallet the same executable observation."""
        lab = getattr(self, "strategy_lab", None)
        if lab is None:
            return
        now = datetime.now(timezone.utc)
        status_client = getattr(self, "_discovery_client", self.client)
        await lab.settle_expired(now, status_client.get_market_status)
        for market in markets:
            if (
                self._session_deadline is not None
                and market.close_time.timestamp() > self._session_wall_deadline
            ):
                continue
            prediction = predictions.get(market.ticker)
            if prediction is None:
                continue
            snapshot = book_snapshots.get(market.ticker)
            analysis = snapshot.analysis if snapshot is not None else None
            yes_bid = analysis.yes_bid if analysis is not None else market.yes_bid
            yes_ask = analysis.yes_ask if analysis is not None else market.yes_ask
            yes_bid_size = (
                analysis.yes_bid_size if analysis is not None else market.yes_bid_size
            )
            no_bid_size = (
                analysis.no_bid_size if analysis is not None else market.yes_ask_size
            )
            total = yes_bid_size + no_bid_size
            imbalance = (
                (yes_bid_size - no_bid_size) / total if total > 0 else Decimal("0")
            )
            midpoint = (yes_bid + yes_ask) / Decimal("2")
            lab.observe(
                LabObservation(
                    ticker=market.ticker,
                    asset=market.asset,
                    observed_at=now,
                    close_time=market.close_time,
                    target_price=market.target_price,
                    spot=prediction.current_spot,
                    probability_yes=prediction.probability_yes,
                    distance_z=prediction.distance_z,
                    momentum=prediction.raw_momentum_return,
                    regime=prediction.regime,
                    regime_confidence=prediction.regime_confidence,
                    yes_bid=yes_bid,
                    yes_ask=yes_ask,
                    yes_bid_size=yes_bid_size,
                    no_bid_size=no_bid_size,
                    microprice=(
                        analysis.microprice if analysis is not None else midpoint
                    ),
                    imbalance=(
                        analysis.imbalance if analysis is not None else imbalance
                    ),
                    toxicity=(
                        analysis.toxicity if analysis is not None else abs(imbalance)
                    ),
                    fee_multiplier=market.fee_multiplier,
                    raw_probability_yes=prediction.raw_probability_yes,
                    directional_probability_yes=(
                        prediction.directional_probability_yes
                    ),
                )
            )
        lab.snapshot(now)

    async def _record_data_pass(
        self,
        cycle_id: str,
        markets: list[KalshiMarket],
        book_snapshots: dict[str, Any],
    ) -> None:
        """Persist strict market, depth, and portfolio observations."""
        settings = getattr(self, "settings", None)
        if settings is None or not getattr(settings, "market_data_enabled", False):
            return
        observed_at = datetime.now(timezone.utc)
        try:
            records: list[dict[str, Any]] = []
            for market in markets:
                snapshot = book_snapshots.get(market.ticker)
                snapshot_time = snapshot.observed_at if snapshot else observed_at
                records.append(
                    {
                        "run_id": self.data_run_id,
                        "cycle_id": cycle_id,
                        "mode": self.mode,
                        "environment": self.settings.environment,
                        "market": market,
                        "observed_at": snapshot_time,
                        "recorded_at": observed_at,
                        "yes_levels": (list(snapshot.yes_levels) if snapshot else None),
                        "no_levels": (list(snapshot.no_levels) if snapshot else None),
                        "analysis": snapshot.analysis if snapshot else None,
                        "book_source": snapshot.source if snapshot else "discovery",
                        "book_sequence": snapshot.sequence if snapshot else None,
                        "book_age_ms": max(
                            0.0,
                            (observed_at - snapshot_time).total_seconds() * 1000,
                        ),
                    }
                )
            self.ledger.record_market_snapshots(records)

            if self.live:
                cached_balance = getattr(self.client, "cached_available_balance", None)
                if callable(cached_balance):
                    available = cached_balance()
                    # Market telemetry is diagnostic. If no successful balance
                    # read exists yet, omit this portfolio point instead of
                    # delaying position service with another network request.
                    if available is None:
                        return
                else:  # narrow test doubles and legacy clients
                    available = await self.client.get_available_balance()
                locked = self._locked_capital()
                current = available + locked
                realized = sum(
                    (
                        trade.pnl or Decimal("0")
                        for trade in self.ledger.list_trades(
                            mode="live",
                            environment=self.settings.environment,
                            limit=10_000,
                        )
                    ),
                    Decimal("0"),
                )
                open_positions = self.ledger.count_open_trades(
                    "live", self.settings.environment
                )
            else:
                summary = self.ledger.paper_summary(self.settings.environment)
                current = summary.current_balance
                available = summary.available_balance
                locked = summary.locked_capital
                realized = summary.net_pnl
                open_positions = summary.open_trades
            self.ledger.record_portfolio_snapshot(
                run_id=self.data_run_id,
                mode=self.mode,
                environment=self.settings.environment,
                current_balance=current,
                available_balance=available,
                locked_capital=locked,
                realized_pnl=realized,
                open_positions=open_positions,
                observed_at=observed_at,
            )
        except Exception as exc:  # diagnostics never stop execution
            logger.warning(
                "Strict market-data capture failed ({}): {}",
                type(exc).__name__,
                str(exc) or "no detail",
            )

    async def _fill_missing_predictions(
        self,
        markets: list[KalshiMarket],
        predictions: dict[str, Any],
    ) -> None:
        """Compute a prediction for markets the evaluation path did not price.

        Two populations need one:

        *Quotable markets the predictor skipped* - it refuses anything outside
        ENTRY_WINDOW_SECONDS before it ever computes a probability, which is
        exactly the set a maker most wants.

        *Markets we already hold a position in* - and this one is not optional.
        The moment the predictor enters a ticker, its own ``not_already_traded``
        gate short-circuits evaluation **before** the prediction is computed, so
        from the next cycle onward there is no probability for that market at
        all. Exit-on-reversal reads exactly that probability, so without this the
        feature is unreachable for every position it was built for: measured on
        a live session, a predictor position entered at 21:58:30 had no
        prediction from 21:58:40 until it settled at a loss. The close-time
        filter is skipped for these, because a held position needs valuing right
        up to settlement, not only while it would still be worth quoting.
        """
        now = datetime.now(timezone.utc)
        held = {
            trade.ticker
            for trade in self.ledger.get_open_trades(
                self.mode, self.settings.environment
            )
        }
        wanted = [
            market
            for market in markets
            if market.ticker not in predictions
            and market.ticker not in getattr(self, "_cycle_prediction_attempts", set())
            and time.monotonic()
            >= getattr(self, "_price_feed_failed_until", {}).get(market.asset, 0.0)
            and (
                market.ticker in held
                or (market.close_time - now).total_seconds()
                > self.settings.scalp_min_seconds_to_close
            )
        ]
        if not wanted:
            return
        self._cycle_phase = f"pricing {len(wanted)} fallback markets"

        async def one(market: KalshiMarket) -> tuple[str, Prediction] | None:
            candles = await self._get_price_candles(
                market.asset,
                limit=(
                    600 if KRAKEN_PAIR_BY_ASSET[market.asset].endswith("=F") else 60
                ),
            )
            freshness = check_candle_freshness(
                candles[-1].timestamp, now, self.settings.max_candle_age_seconds
            )
            if not freshness.allowed:
                return None
            prediction = self._predictors.get(market.asset, self.predictor).predict(
                candles=candles,
                target_price=market.target_price,
                close_time=market.close_time,
                now=now,
            )
            return market.ticker, prediction

        for market, result in zip(
            wanted,
            await asyncio.gather(
                *(one(market) for market in wanted), return_exceptions=True
            ),
        ):
            if isinstance(result, BaseException):
                logger.debug(
                    "{}: scalp prediction failed for {}: {}",
                    market.asset,
                    market.ticker,
                    result,
                )
                continue
            if result is not None:
                predictions[result[0]] = result[1]

    async def _get_price_candles(
        self,
        asset: str,
        *,
        limit: int,
    ) -> list[Any]:
        """Read one asset without allowing a failed route to stall all scans."""
        failed_until = self.__dict__.setdefault("_price_feed_failed_until", {})
        now = time.monotonic()
        if now < failed_until.get(asset, 0.0):
            raise RuntimeError(f"{asset} price feed is in retry cooldown")
        try:
            return await asyncio.wait_for(
                self.prices.get_one_minute_candles(
                    KRAKEN_PAIR_BY_ASSET[asset],
                    limit=limit,
                ),
                timeout=PRICE_FEED_READ_TIMEOUT_SECONDS,
            )
        except Exception:
            failed_until[asset] = time.monotonic() + float(
                getattr(self.settings, "order_failure_cooldown_seconds", 60)
            )
            raise

    async def _salvage_pass(self) -> int:
        """Offer every position in its final window the chance to be sold.

        Reads the ledger rather than taking a list, because it is called from
        both position-holding paths and the OPEN set is the only thing both
        agree on. Returns how many positions were sold, which the caller in
        ``_monitor_open_trade`` uses to decide whether there is still a trade to
        monitor.
        """
        if self.salvage is None:
            return 0
        return await self.salvage.run(
            self.ledger.get_open_trades(self.mode, self.settings.environment),
            portfolio_value=await self._portfolio_value(),
        )

    async def _portfolio_value(self) -> Decimal:
        """Total account value, the denominator of every exposure fraction.

        Cash *plus* what is already committed, not available cash alone: a
        position's share of the portfolio must not grow simply because that
        position was funded. Measuring against available cash would make the
        second of two equal positions look twice as exposed as the first.

        Re-read every pass rather than cached, because it compounds. A failure
        to read it returns zero, which ``exposure_fraction`` deliberately reads
        as fully exposed - an account whose value is unknown is the last one
        that should have its supervision reduced.
        """
        try:
            if self.live:
                cash = await self.client.get_available_balance()
                return cash + self._locked_capital()
            summary = self.ledger.paper_summary(self.settings.environment)
            return summary.current_balance
        except Exception:
            logger.debug("Portfolio value unavailable; treating as fully exposed")
            return Decimal("0")

    def _locked_capital(self) -> Decimal:
        """Capital sitting in open positions, across every strategy.

        Read from the LEDGER, not from ``PositionBook``. PositionBook is
        reserved by the scalper alone - ``execution/scalp_engine.py`` is its
        only caller - so it cannot see a predictor position at all, and with
        scalping on the predictor is not limited to one. Using it here omitted
        the predictor's share of the account from every exposure fraction.

        This only matters in live mode. Kalshi's ``get_available_balance`` is
        free cash with every locked position already deducted, so the portfolio
        total is that cash plus what is locked; the paper ledger's
        ``current_balance`` is already the total and needs no such addition.
        That asymmetry is the shape of three bugs CLAUDE.md records - correct
        in paper, wrong in live, invisible until two numbers disagree.
        """
        try:
            return sum(
                (
                    trade.total_debit
                    for trade in self.ledger.get_open_trades(
                        self.mode, self.settings.environment
                    )
                ),
                Decimal("0"),
            )
        except Exception:
            logger.debug("Open-position value unavailable; treating as zero")
            return Decimal("0")

    async def _settle_scalp_positions(self, books: dict[str, Any]) -> None:
        """Settle every open position whose market has closed.

        With scalping enabled nothing blocks, so ``_monitor_open_trade`` never
        runs and this is the only thing that closes a position out. It covers
        *all* strategies for that reason: scoping it to the scalper would leave
        a predictor trade open forever in this mode.
        """
        now = datetime.now(timezone.utc)
        statuses: dict[str, Any] = {}

        async def status_for(ticker: str) -> Any | None:
            if ticker in statuses:
                return statuses[ticker]
            try:
                status_client = getattr(self, "_discovery_client", self.client)
                status = await status_client.get_market_status(ticker)
            except Exception as exc:
                warned = self.__dict__.setdefault("_status_warning_at", {})
                now_mono = time.monotonic()
                if now_mono - warned.get(ticker, 0.0) >= 60.0:
                    warned[ticker] = now_mono
                    logger.warning(
                        "Settlement status for {} deferred: Kalshi REST unavailable ({})",
                        ticker,
                        type(exc).__name__,
                    )
                return None
            statuses[ticker] = status
            return status

        for trade in self.ledger.get_open_trades(self.mode, self.settings.environment):
            if trade.market_close_time > now:
                continue
            status = await status_for(trade.ticker)
            if status is None:
                continue
            if status.result not in {"yes", "no", "void"}:
                continue
            settled = self.ledger.settle_trade(
                trade.trade_id, status.result, status.status, settled_at=now
            )
            self.ledger.record_probability_calibration(
                settled, status.result, status.settlement_time or now
            )
            await self.positions.close(trade.trade_id)
            if settled.strategy == SCALP_STRATEGY and self.scalp is not None:
                self.scalp.stats.settlements += 1
            self._append_audit_event("settle_trade", settled)
            _event.warning(
                "{} SETTLED {} {} -> {} | P&L ${}",
                settled.strategy.upper(),
                settled.asset,
                settled.ticker,
                settled.status,
                settled.pnl,
            )
        for trade in self.ledger.get_unlabeled_exited_trades(
            self.mode, self.settings.environment, now
        ):
            status = await status_for(trade.ticker)
            if status is None:
                continue
            if status.result not in {"yes", "no", "void"}:
                continue
            self.ledger.record_exit_counterfactual(
                trade.trade_id,
                status.result,
                status.settlement_time or now,
            )
            self.decisions.record_event(
                "exit_counterfactual",
                trade_id=trade.trade_id,
                ticker=trade.ticker,
                outcome=trade.outcome,
                market_result=status.result,
                realized_exit_pnl=trade.pnl,
            )

    async def _available_cash(self) -> Decimal:
        """Cash the scalper may commit right now, after what is already at risk.

        Two things this must get right, both learned from a live fill:

        **The balance has to be the real one.** This read ``paper_summary``
        unconditionally, so a live session sized against the *paper* ledger -
        a number with no connection to the account being spent. It happened to
        be smaller than the real balance, so nothing overdrew, but that was
        luck: a paper ledger that had compounded to $500 would have sized $250
        orders against a $30 account.

        **``LIVE_CAPITAL_LIMIT_USD`` has to apply here too.** The predictor
        passes it into ``build_order_plan`` as ``maximum_budget``; the scalper
        had no equivalent and so ignored it entirely. The first live scalp
        debited $11.82 against a $10 cap.
        """
        if self.live:
            balance = await self.client.get_available_balance()
            committed = self.positions.committed(SCALP_STRATEGY)
            available = max(Decimal("0"), balance - committed)
        else:
            summary = self.ledger.paper_summary(self.settings.environment)
            balance = summary.current_balance
            available = summary.available_balance

        # Stashed so exposure-based admission checks cost no extra round trip.
        # NOT simply ``balance``: in live mode that is free cash, with
        # every open position already deducted by Kalshi, so using it as the
        # exposure denominator understates the account by exactly the capital
        # at risk - and therefore overstates every position's share of it. The
        # paper branch needs no addition because ``current_balance`` is already
        # the total.
        self._last_portfolio_value = (
            balance + self._locked_capital() if self.live else balance
        )
        # Identical in paper and live. Each position is separately capped at
        # TRADE_BUDGET_USD by ScalpEngine; this is the aggregate strategy cap.
        ceiling = min(
            balance * self.settings.scalp_max_capital_fraction,
            getattr(
                self.settings,
                "trade_budget_usd",
                getattr(self.settings, "live_capital_limit_usd", Decimal("10")),
            )
            * getattr(
                self.settings,
                "scalp_max_concurrent",
                self.positions.limit_for(SCALP_STRATEGY),
            ),
        )
        self.positions.set_ceiling(SCALP_STRATEGY, ceiling)
        return max(Decimal("0"), min(available, ceiling))

    def _log_scan(self, winner: TradeCandidate | None) -> None:
        """One line per pass, throttled to a readable cadence on the console.

        Ten gate lines per asset every five seconds is weather rather than
        information. The reasoning those lines carried is written in full to
        decisions.jsonl on every path, so nothing is lost by summarising here.

        At a fast poll interval even one line per pass is clutter - twelve a
        minute scrolls an event off the screen before it can be read. So the
        console gets one every ``SCAN_LOG_INTERVAL_SECONDS``, while a pass that
        actually selected a candidate always prints. The *file* keeps every
        line at DEBUG, so throttling costs no forensic detail: the terminal is
        for watching, the transcript is for reconstructing.
        """
        line = format_scan_line(
            sorted(self.scan_verdicts.values()),
            selected=winner.market.asset if winner else None,
        )
        now = time.monotonic()
        interval = getattr(
            getattr(self, "settings", None), "scan_log_interval_seconds", 0
        )
        due = winner is not None or (now - self._last_scan_log) >= interval
        if due:
            self._last_scan_log = now
            _scan.info(line)
        else:
            _scan.debug(line)
        self._cycle_phase = "idle"

    async def run_paper_session(
        self,
        *,
        duration_seconds: float = 3600,
        evaluation_passes: int = 360,
    ) -> PaperSessionReport:
        """Run a bounded paper benchmark with evenly spaced evaluation passes."""
        await self._start_price_stream()
        if self.live:
            raise RuntimeError("Timed performance sessions are paper-only")
        if duration_seconds <= 0:
            raise ValueError("duration_seconds must be greater than zero")
        if evaluation_passes <= 0:
            raise ValueError("evaluation_passes must be greater than zero")

        started_at = datetime.now(timezone.utc)
        started_monotonic = time.monotonic()
        session_deadline = started_monotonic + duration_seconds
        self._session_deadline = session_deadline
        self._session_wall_deadline = started_at.timestamp() + duration_seconds
        pass_interval = duration_seconds / evaluation_passes
        first_pass_delay = (
            self._aligned_first_pass_delay(started_at, pass_interval)
            if duration_seconds >= 900
            else 0.0
        )
        baseline = self.ledger.paper_summary(self.settings.environment)
        recovered_open = self.ledger.get_open_trade(
            self.mode,
            self.settings.environment,
        )
        recovered_trade_ids = {recovered_open.trade_id} if recovered_open else set()
        evaluation_counts = {asset: 0 for asset in self.settings.assets}
        completed_passes = 0
        skipped_passes = 0

        _event.info(
            "Starting paper benchmark: {:.1f} minutes, {} passes, "
            "{:.1f}s spacing, first pass in {:.1f}s, starting balance ${:.2f}",
            duration_seconds / 60,
            evaluation_passes,
            pass_interval,
            first_pass_delay,
            float(baseline.current_balance),
        )

        for pass_index in range(evaluation_passes):
            if self._stopping:
                break
            self._maybe_log_hourly()
            pass_deadline = (
                started_monotonic + first_pass_delay + pass_index * pass_interval
            )
            if pass_deadline >= session_deadline:
                skipped_passes += 1
                _event.info(
                    "Paper pass {}/{} falls outside the session window",
                    pass_index + 1,
                    evaluation_passes,
                )
                continue
            blocked_past_deadline = await self._wait_for_session_deadline(pass_deadline)
            if self._stopping:
                break

            # Scalping holds positions continuously by design, so the "skip this
            # pass, something is still open" rule would skip every pass after the
            # first fill and the session would evaluate nothing. In that mode
            # positions are serviced inside the pass instead of blocking it.
            open_trade = (
                None
                if self._supervises_positions
                else self.ledger.get_open_trade(
                    self.mode,
                    self.settings.environment,
                )
            )
            if blocked_past_deadline or open_trade is not None:
                skipped_passes += 1
                _event.info(
                    "Paper pass {}/{} skipped because a prior trade "
                    "remained open at its scheduled time",
                    pass_index + 1,
                    evaluation_passes,
                )
                continue
            if self._halt_reason is not None:
                skipped_passes += 1
                logger.error(
                    "Paper pass {}/{} skipped; trading halted: {}",
                    pass_index + 1,
                    evaluation_passes,
                    self._halt_reason,
                )
                continue

            logger.debug(
                "Paper evaluation pass {}/{}",
                pass_index + 1,
                evaluation_passes,
            )
            try:
                await self._run_cycle_with_status(evaluation_counts=evaluation_counts)
                completed_passes += 1
            except Exception:
                skipped_passes += 1
                logger.exception(
                    "Paper evaluation pass {}/{} failed",
                    pass_index + 1,
                    evaluation_passes,
                )

        await self._wait_for_session_deadline(session_deadline)

        # Do not leave a final paper position unresolved merely because its
        # exchange settlement arrives a few seconds after the one-hour mark.
        while not self._stopping:
            if self._supervises_positions:
                # Several positions may be open at once here, so drain them by
                # polling rather than by blocking on the oldest. Stop when the
                # last one has settled, or when a pass makes no progress -
                # otherwise a market that never reports a result spins forever.
                open_trades = self.ledger.get_open_trades(
                    self.mode, self.settings.environment
                )
                if not open_trades:
                    break
                # Wait for the LAST market to close, then keep polling. The
                # previous version gave up as soon as one poll made no progress,
                # which for a position whose market was still eight minutes from
                # closing meant abandoning it: nothing monitored it, no exit
                # could fire, and it rode to settlement unsupervised. That single
                # position lost more than every managed trade in the session
                # made. A bounded session may overrun to close its own book.
                latest_close = max(trade.market_close_time for trade in open_trades)
                now = datetime.now(timezone.utc)
                if now < latest_close:
                    _event.info(
                        "Session over with {} position(s) open; supervising "
                        "until the last market closes at {}",
                        len(open_trades),
                        latest_close.isoformat(timespec="seconds"),
                    )
                    await self._service_positions_pass(
                        await self.client.get_active_markets()
                    )
                    await self._interruptible_wait(
                        Decimal(str(self.settings.poll_interval_seconds))
                    )
                    continue
                await self._settle_scalp_positions({})
                if self.ledger.count_open_trades(
                    self.mode, self.settings.environment
                ) == len(open_trades):
                    await self._interruptible_wait(
                        Decimal(str(self.settings.settlement_poll_seconds))
                    )
                continue
            open_trade = self.ledger.get_open_trade(
                self.mode,
                self.settings.environment,
            )
            if open_trade is None:
                break
            await self._monitor_open_trade(open_trade, once=False)

        if self.scalp is not None:
            self.scalp.record_funnel("session_end")
        if self.salvage is not None:
            self.salvage.record_funnel("session_end")
        ended_at = datetime.now(timezone.utc)
        ending = self.ledger.paper_summary(self.settings.environment)
        session_trades = [
            trade
            for trade in self.ledger.list_trades(
                mode="paper",
                environment=self.settings.environment,
                limit=10_000,
            )
            if (trade.placed_at >= started_at or trade.trade_id in recovered_trade_ids)
        ]
        report = build_paper_session_report(
            started_at=started_at,
            ended_at=ended_at,
            requested_duration_seconds=duration_seconds,
            scheduled_passes=evaluation_passes,
            completed_passes=completed_passes,
            skipped_passes=skipped_passes,
            starting_balance=baseline.current_balance,
            ending_balance=ending.current_balance,
            configured_assets=self.settings.assets,
            evaluation_counts=evaluation_counts,
            trades=session_trades,
        )
        if not getattr(self, "silent", False):
            print(format_paper_session_report(report), flush=True)
        self._write_report("session")
        return report

    def _write_report(self, occasion: str) -> None:
        """Write a PDF performance report, and never fail the run over it.

        Imported here rather than at module scope: `reporting` reads the ledger
        through `monitoring`, and a bot that cannot start because a *report*
        module failed to import would be trading capability lost to a
        diagnostic. The same reason execution/decision_log.py swallows.
        """
        # getattr, and defaulting to off: the tests drive this class with stub
        # settings objects, and a test double must not write a PDF into the
        # operator's home directory as a side effect of asserting something
        # else. A real KalshiSettings always carries the field.
        if not getattr(self.settings, "reports_enabled", False):
            return
        try:
            from reporting.report import build_report

            path = build_report(
                ledger_path=self.settings.ledger_path,
                output_dir=self.settings.log_dir,
                mode=self.mode,
                environment=self.settings.environment,
            )
        except Exception as exc:  # noqa: BLE001 - a report is not the trade
            _event.warning("Could not write the {} report: {}", occasion, exc)
            return
        _event.info("Report: {}", path)

    @staticmethod
    def _aligned_first_pass_delay(
        started_at: datetime,
        pass_interval: float,
    ) -> float:
        """Align 7.5-minute passes so alternating scans occur at close-2m."""
        if pass_interval <= 0:
            return 0.0
        intervals_per_market = 900.0 / pass_interval
        if abs(intervals_per_market - round(intervals_per_market)) > 0.001:
            return 0.0
        phase = (started_at.timestamp() + 120.0) % pass_interval
        if phase < 0.001 or pass_interval - phase < 0.001:
            return 0.0
        return pass_interval - phase

    async def _wait_for_session_deadline(self, deadline: float) -> bool:
        """Wait without scanning; return true if an open trade crossed the slot."""
        blocked_past_deadline = False
        while not self._stopping:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return blocked_past_deadline
            # With scalping on, the gap between passes is short and positions are
            # serviced inside each pass, so waiting it out is correct. Blocking
            # here would stop quotes being cancelled and positions being exited
            # for the whole gap.
            open_trade = (
                None
                if self._supervises_positions
                else self.ledger.get_open_trade(
                    self.mode,
                    self.settings.environment,
                )
            )
            if open_trade is None:
                await self._interruptible_wait(Decimal(str(remaining)))
                continue

            await self._monitor_open_trade(open_trade, once=False)
            if time.monotonic() >= deadline:
                blocked_past_deadline = True
        return blocked_past_deadline

    async def _evaluate_market(
        self,
        market: KalshiMarket,
        cycle_id: str = "adhoc",
    ) -> TradeCandidate | None:
        """Evaluate one market, recording the reasoning either way.

        A failed evaluation is itself a decision worth keeping: a Kraken
        timeout that silently drops out of the log looks identical to a market
        that was never scanned.
        """
        trace = self.decisions.trace(
            cycle_id=cycle_id,
            mode=self.mode,
            environment=self.settings.environment,
            asset=market.asset,
            ticker=market.ticker,
        )
        try:
            return await self._evaluate_market_traced(market, trace)
        except Exception as exc:
            trace.gate("evaluation_completed", False, repr(exc))
            trace.finish("error", f"{type(exc).__name__}: {exc}")
            self._note_refusal(market, "evaluation_completed", "error")
            raise

    def _note_refusal(self, market: KalshiMarket, gate: str, verdict: str) -> None:
        """Record why one market was passed over, for the pass line and the hour.

        Nothing is emitted here. The full reasoning - the gate, its threshold
        and the numbers it compared - is already written to decisions.jsonl by
        the trace; the console only needs the verdict, and it gets it once per
        pass rather than once per gate.
        """
        self.scan_verdicts[market.ticker] = (market.asset, verdict)
        self.period.record_rejection(gate)

    def _record_strict_signal_evaluation(self, payload: dict[str, Any]) -> None:
        normalized = _strict_signal_payload(payload)
        if normalized is None:
            return
        self.ledger.record_signal_evaluation(
            run_id=self.data_run_id,
            **normalized,
        )

    async def _evaluate_market_traced(
        self,
        market: KalshiMarket,
        trace: DecisionTrace,
    ) -> TradeCandidate | None:
        now = datetime.now(timezone.utc)
        seconds_to_close = int((market.close_time - now).total_seconds())
        self.period.record_evaluation()
        trace.update(
            {
                "seconds_to_close": seconds_to_close,
                "market": {
                    "event_ticker": market.event_ticker,
                    "target_price": market.target_price,
                    "close_time": market.close_time,
                    "yes_bid": market.yes_bid,
                    "yes_ask": market.yes_ask,
                    "no_ask": market.no_ask,
                    "implied_probability_yes": market_implied_probability(
                        market.yes_bid, market.yes_ask
                    ),
                    "fee_multiplier": market.fee_multiplier,
                },
                "thresholds": {
                    "min_probability": self.settings.min_probability,
                    "min_edge": self.settings.min_edge,
                    "max_divergence": self.settings.max_model_market_divergence,
                    "entry_window_seconds": self.settings.entry_window_seconds,
                },
            }
        )

        if not trace.gate(
            "cooldown",
            not self._in_cooldown(market.ticker),
            "ticker is in post-failure cooldown",
        ):
            trace.finish("skipped", "cooldown")
            self._note_refusal(market, "cooldown", "cooldown")
            return None

        history_allowed, history_reason = self.ledger.can_enter_ticker(
            mode=self.mode,
            environment=self.settings.environment,
            ticker=market.ticker,
            strategy="predictor",
            outcome=None,
            maximum_entries=self.settings.max_entries_per_market,
        )
        if not trace.gate("entry_history", history_allowed, history_reason):
            trace.finish("skipped", history_reason)
            self._note_refusal(market, "entry_history", "traded")
            return None
        if not trace.gate(
            "entry_window",
            seconds_to_close <= self.settings.entry_window_seconds,
            f"{seconds_to_close}s to close is outside the "
            f"{self.settings.entry_window_seconds}s entry window",
        ):
            trace.finish("skipped", "outside entry window")
            self._note_refusal(market, "entry_window", f"window {seconds_to_close}s")
            return None
        if not trace.gate(
            "minimum_time_to_close",
            seconds_to_close >= self.settings.minimum_seconds_to_close,
            f"only {seconds_to_close}s remain",
        ):
            trace.finish("skipped", "too close to settlement")
            self._note_refusal(
                market, "minimum_time_to_close", f"expiring {seconds_to_close}s"
            )
            return None

        feed_retry_at = getattr(self, "_price_feed_failed_until", {}).get(
            market.asset, 0.0
        )
        feed_ready = time.monotonic() >= feed_retry_at
        if not trace.gate(
            "price_feed_cooldown",
            feed_ready,
            (
                "price feed retry is available"
                if feed_ready
                else f"retry in {max(0.0, feed_retry_at - time.monotonic()):.0f}s"
            ),
        ):
            trace.finish("skipped", "price feed cooldown")
            self._note_refusal(market, "price_feed_cooldown", "feed-cooldown")
            return None

        cycle_id = str(trace.payload.get("cycle_id", ""))
        if not cycle_id.startswith("event-"):
            attempts = self.__dict__.setdefault("_cycle_prediction_attempts", set())
            attempts.add(market.ticker)
        candles = await self._get_price_candles(
            market.asset,
            limit=(600 if KRAKEN_PAIR_BY_ASSET[market.asset].endswith("=F") else 60),
        )
        freshness = check_candle_freshness(
            candles[-1].timestamp,
            now,
            self.settings.max_candle_age_seconds,
        )
        trace.set("latest_candle_time", candles[-1].timestamp)
        if not trace.gate("candle_freshness", freshness.allowed, freshness.reason):
            # Stale spot is a data fault rather than a verdict on the market,
            # but it fails every asset at once and every five seconds, so it
            # travels in the pass summary instead of as its own warning.
            logger.debug("{}: no trade; {}", market.asset, freshness.reason)
            trace.finish("skipped", freshness.reason)
            self._note_refusal(market, "candle_freshness", "stale-candles")
            return None

        # The asset's own fitted mixture when it has one, otherwise the shared
        # hand-tuned predictor. `.get` and not a lookup: a partial rollout is a
        # supported state.
        prediction = self._predictors.get(market.asset, self.predictor).predict(
            candles=candles,
            target_price=market.target_price,
            close_time=market.close_time,
            now=now,
        )
        # Stash P(yes) for the scalper. It quotes the YES side only, and it needs
        # a probability for markets the predictor rejected as well as the ones it
        # liked - a market with no directional edge can still be worth quoting,
        # which is the entire difference between the two strategies. Fetching
        # candles a second time to recompute this would double the Kraken load
        # for a number already in hand.
        self._cycle_predictions[market.ticker] = prediction
        outcome, outcome_probability, executable_price = self._choose_outcome(
            prediction,
            market,
        )
        direction_allowed, direction_reason = self.ledger.can_enter_ticker(
            mode=self.mode,
            environment=self.settings.environment,
            ticker=market.ticker,
            strategy="predictor",
            outcome=outcome,
            maximum_entries=self.settings.max_entries_per_market,
        )
        if not trace.gate("reentry_direction", direction_allowed, direction_reason):
            trace.finish("skipped", direction_reason)
            self._note_refusal(market, "reentry_direction", "same-side")
            return None
        buffered_price = min(
            Decimal("0.99"),
            executable_price
            + self.settings.max_slippage
            + self.settings.fee_reserve_per_contract,
        )
        edge = outcome_probability - buffered_price
        trace.update(
            {
                "prediction": _prediction_payload(prediction),
                "candidate": {
                    "outcome": outcome,
                    "outcome_probability": outcome_probability,
                    "top_of_book_price": executable_price,
                    "buffered_price": buffered_price,
                    "edge": edge,
                },
            }
        )

        logger.debug(
            "{} {} | {} | target={} spot={} | P({})={:.1%} market={:.1%} "
            "top={:.2f} buffered={:.2f} edge={:.1%} | {}s",
            market.asset,
            market.ticker,
            prediction.regime,
            market.target_price,
            prediction.current_spot,
            outcome.upper(),
            float(outcome_probability),
            float(market_implied_probability(market.yes_bid, market.yes_ask)),
            float(executable_price),
            float(buffered_price),
            float(edge),
            seconds_to_close,
        )
        if prediction.regime_reasons:
            logger.debug(
                "{}: regime {} ({:.0%} confidence) because {}",
                market.asset,
                prediction.regime,
                float(prediction.regime_confidence),
                "; ".join(prediction.regime_reasons),
            )

        divergence = check_model_market_divergence(
            prediction.probability_yes,
            market.yes_bid,
            market.yes_ask,
            self.settings.max_model_market_divergence,
        )
        if not trace.gate(
            "model_market_divergence", divergence.allowed, divergence.reason
        ):
            trace.finish("skipped", divergence.reason)
            self._note_refusal(market, "model_market_divergence", "divergence")
            return None

        # Inside the final seconds the terminal distribution is much tighter, so
        # a smaller edge is worth taking — but only against a stricter
        # probability floor and a settlement margin the model can actually see.
        probability_floor, edge_floor, is_late = late_entry_thresholds(
            seconds_to_close,
            late_window_seconds=self.settings.late_entry_seconds,
            min_probability=self.settings.min_probability,
            min_edge=self.settings.min_edge,
            late_min_probability=self.settings.late_min_probability,
            late_min_edge=self.settings.late_min_edge,
        )
        trace.update(
            {
                "late_entry": is_late,
                "applied_floors": {
                    "min_probability": probability_floor,
                    "min_edge": edge_floor,
                },
                # Both, explicitly. "settlement_sigma" is what the guard is
                # judged on (distance only); "settlement_sigma_with_drift" is
                # the model's full z. Recording one number under a name that
                # means the other is how this went wrong in the first place.
                "settlement_sigma": prediction.distance_z,
                "settlement_sigma_with_drift": prediction.z_score,
            }
        )

        if is_late:
            # The strike must be far enough away in standard deviations that a
            # Kraken/CF-Benchmarks disagreement cannot decide the settlement.
            # Probability alone cannot distinguish "comfortably clear" from
            # "one tick above with ten seconds left".
            # distance_z, not z_score. The aggregate z folds in the drift
            # forecast, and this guard exists to ask a question about the
            # present: is spot far enough from the strike that CF Benchmarks
            # and Kraken cannot disagree their way to the other outcome. A
            # confident drift term satisfying a distance floor is how a coin
            # flip gets admitted as a 0.58-sigma trade.
            margin = check_settlement_margin(
                prediction.distance_z,
                self.settings.late_min_settlement_sigma,
            )
            if not trace.gate("settlement_margin", margin.allowed, margin.reason):
                trace.finish("skipped", "inside the settlement margin")
                self._note_refusal(
                    market,
                    "settlement_margin",
                    f"margin {float(abs(prediction.distance_z)):.2f}sigma",
                )
                return None

        if not trace.gate(
            "min_probability",
            outcome_probability >= probability_floor,
            f"probability {float(outcome_probability):.1%} is below "
            f"{float(probability_floor):.1%}"
            + (" (late-market floor)" if is_late else ""),
        ):
            trace.finish("skipped", "probability below floor")
            self._note_refusal(
                market,
                "min_probability",
                f"low-prob {float(outcome_probability):.3f}",
            )
            return None
        if not trace.gate(
            "min_edge",
            edge >= edge_floor,
            f"buffered edge {float(edge):.1%} is below "
            f"{float(edge_floor):.1%}" + (" (late-market floor)" if is_late else ""),
        ):
            trace.finish("skipped", "edge below floor")
            self._note_refusal(market, "min_edge", f"no-edge {float(edge):.3f}")
            return None

        consensus_tail, consensus_details = _consensus_tail_qualification(
            self.settings,
            prediction,
            outcome,
            executable_price,
        )
        trace.set("consensus_tail", consensus_details)

        if is_late:
            logger.debug(
                "{} {}: late-market entry at {}s to close | P={:.1%} edge={:.1%} "
                "margin={:.2f}σ (floors {:.0%}/{:.1%})",
                market.asset,
                market.ticker,
                seconds_to_close,
                float(outcome_probability),
                float(edge),
                float(abs(prediction.distance_z)),
                float(probability_floor),
                float(edge_floor),
            )

        self.scan_verdicts[market.ticker] = (
            market.asset,
            f"edge {float(edge):.3f}" + (" late" if is_late else ""),
        )
        return TradeCandidate(
            market=market,
            prediction=prediction,
            outcome=outcome,
            outcome_probability=outcome_probability,
            top_of_book_price=executable_price,
            buffered_price=buffered_price,
            edge=edge,
            edge_floor=edge_floor,
            is_late=is_late,
            consensus_tail=(
                consensus_tail
                if getattr(self.settings, "consensus_tail_preferred", True)
                else False
            ),
            trace=trace,
        )

    async def _place_candidate(self, candidate: TradeCandidate) -> bool:
        """Serialize polling and websocket placements before final checks."""
        lock = getattr(self, "_predictor_placement_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._predictor_placement_lock = lock
        async with lock:
            return await self._place_candidate_locked(candidate)

    def _market_discovery_degraded(self, market: KalshiMarket) -> bool:
        """Whether this market came only from a previous discovery pass.

        Older tests and narrow callers set only the global flag, so absence of
        the per-asset set deliberately falls back to that behavior.
        """
        degraded = getattr(self, "_degraded_discovery_assets", None)
        if degraded is None:
            return bool(getattr(self, "_discovery_degraded", False))
        return market.asset in degraded

    async def _place_candidate_locked(self, candidate: TradeCandidate) -> bool:
        market = candidate.market
        trace = candidate.trace or self.decisions.trace(
            cycle_id="adhoc",
            mode=self.mode,
            environment=self.settings.environment,
            asset=market.asset,
            ticker=market.ticker,
        )
        if getattr(self, "_stopping", False):
            trace.gate("shutdown", False, "shutdown requested before placement")
            trace.finish("skipped", "shutdown requested")
            return False
        if self._market_discovery_degraded(market):
            trace.gate(
                "fresh_market_discovery",
                False,
                "Kalshi REST discovery is degraded; supervision only",
            )
            trace.finish("skipped", "discovery degraded")
            return False
        # With scalping enabled run() no longer sleeps on an open position, so
        # the loop that used to enforce "one predictor trade at a time" is gone
        # and this is what replaces it. Without this the predictor would open a
        # position on every pass, which is a different strategy entirely and not
        # one anybody chose.
        if not getattr(self.settings, "predictor_enabled", True):
            if not trace.gate(
                "predictor_enabled",
                False,
                "the predictor is disabled for this run; one strategy per process",
            ):
                trace.finish("skipped", "predictor disabled")
                self._note_refusal(market, "predictor_enabled", "disabled")
                return False

        if self._supervises_positions:
            open_trades = self.ledger.get_open_trades(
                self.mode, self.settings.environment, "predictor"
            )
            open_predictor = len(open_trades)
            maximum = getattr(self.settings, "predictor_max_concurrent", 1)
            if not trace.gate(
                "predictor_concurrency",
                open_predictor < maximum,
                f"the predictor already holds {open_predictor}/{maximum} positions",
            ):
                trace.finish("skipped", "predictor concurrency limit")
                self._note_refusal(market, "predictor_concurrency", "position-open")
                return False
            cluster = _risk_cluster(market.asset)
            occupied = sorted(
                trade.asset
                for trade in open_trades
                if _risk_cluster(trade.asset) == cluster
            )
            if not trace.gate(
                "risk_cluster",
                not occupied,
                f"{cluster} already occupied by {','.join(occupied)}",
            ):
                trace.finish("skipped", "correlated risk cluster already open")
                self._note_refusal(market, "risk_cluster", "cluster-open")
                return False

        breaker = await self._check_capital_breaker()
        if not trace.gate("capital_breaker", breaker.allowed, breaker.reason):
            self._halt_reason = breaker.reason
            logger.error("Refusing to trade: {}", breaker.reason)
            trace.finish("halted", breaker.reason)
            self._note_refusal(market, "capital_breaker", "halted")
            return False

        if self._safe_mode:
            safe_probability = getattr(
                self.settings, "safe_mode_min_probability", Decimal("0.92")
            )
            safe_sigma = getattr(
                self.settings, "safe_mode_min_settlement_sigma", Decimal("2.0")
            )
            safe_allowed = (
                candidate.outcome_probability >= safe_probability
                and abs(candidate.prediction.distance_z) >= safe_sigma
                and candidate.prediction.regime.upper() != "VOLATILE_CHOP"
                and (
                    candidate.consensus_tail
                    or not getattr(self.settings, "safe_mode_consensus_only", True)
                )
            )
            if not trace.gate(
                "safe_mode",
                safe_allowed,
                f"recovery mode requires P>={safe_probability:.0%}, "
                f"|distance_z|>={safe_sigma} and non-chop regime",
            ):
                trace.finish("skipped", "safe-mode confidence floor")
                self._note_refusal(market, "safe_mode", "safe-mode")
                return False

        if self.live and not trace.gate(
            "venue_duplicate_check",
            not await self.client.already_traded(market.ticker),
            "Kalshi portfolio already holds a trade for this ticker",
        ):
            logger.warning(
                "{}: Kalshi account already traded {}; refusing a duplicate",
                market.asset,
                market.ticker,
            )
            trace.finish("skipped", "venue reports an existing position")
            self._note_refusal(market, "venue_duplicate_check", "venue-duplicate")
            return False

        # Re-read the live book immediately before sizing. The snapshot that
        # produced this candidate is seconds old, and these markets are thin.
        quote = await self.client.get_quote(market.ticker, candidate.outcome)
        if not trace.gate(
            "live_liquidity",
            quote is not None and quote.depth_at_limit > 0,
            "no resting size at the limit price",
            depth_at_limit=(quote.depth_at_limit if quote else None),
        ):
            logger.debug(
                "{}: no resting {} liquidity for {}; cooling down",
                market.asset,
                candidate.outcome.upper(),
                market.ticker,
            )
            self._start_cooldown(market.ticker)
            trace.finish("skipped", "no live liquidity")
            self._note_refusal(market, "live_liquidity", "no-liquidity")
            return False

        # Model confidence is not enough for a recovery trade.  Safe mode also
        # requires the executable venue price to show broad market agreement.
        # The normal positive-edge check below remains mandatory, so this
        # cannot pay 90c merely because a contract looks likely.
        if self._safe_mode:
            market_floor = getattr(
                self.settings, "safe_mode_min_market_price", Decimal("0.85")
            )
            if not trace.gate(
                "safe_mode_market_consensus",
                quote.best_price >= market_floor,
                f"recovery mode requires executable market probability "
                f">={market_floor:.0%}",
                best_price=quote.best_price,
            ):
                trace.finish("skipped", "safe-mode market consensus floor")
                self._note_refusal(market, "safe_mode", "market-floor")
                return False

        buffered_price = min(
            Decimal("0.99"),
            quote.limit_price + self.settings.fee_reserve_per_contract,
        )
        live_edge = candidate.outcome_probability - buffered_price
        trace.set(
            "live_quote",
            {
                "best_price": quote.best_price,
                "limit_price": quote.limit_price,
                "depth_at_limit": quote.depth_at_limit,
                "buffered_price": buffered_price,
                "live_edge": live_edge,
            },
        )
        # Apply the floor this candidate was admitted under, not the standing
        # one: a late-market candidate qualified at 2% and must be judged at 2%.
        live_edge_floor = candidate.edge_floor or self.settings.min_edge
        if not trace.gate(
            "live_edge",
            live_edge >= live_edge_floor,
            f"quote moved to {float(quote.best_price):.2f}; live edge "
            f"{float(live_edge):.1%} is below "
            f"{float(live_edge_floor):.1%}",
        ):
            logger.debug(
                "{}: quote moved to {:.2f}; live edge {:.1%} is below {:.1%}",
                market.asset,
                float(quote.best_price),
                float(live_edge),
                float(live_edge_floor),
            )
            trace.finish("skipped", "live edge decayed before sizing")
            self._note_refusal(market, "live_edge", f"decayed {float(live_edge):.3f}")
            return False

        # The two guards below both exist because the pre-order book re-read can
        # come back wrong, and a wrong read that favours us is indistinguishable
        # from a windfall until it settles. The divergence gate cannot catch it:
        # that ran against the discovery snapshot, before this quote existed.
        sanity = check_quote_sanity(
            candidate.top_of_book_price,
            quote.best_price,
            self.settings.max_quote_improvement,
        )
        if not trace.gate("quote_sanity", sanity.allowed, sanity.reason):
            logger.warning(
                "{}: {}; cooling down {}",
                market.asset,
                sanity.reason,
                market.ticker,
            )
            self._start_cooldown(market.ticker)
            trace.finish("skipped", "implausible live quote")
            self._note_refusal(market, "quote_sanity", "bad-quote")
            return False

        ceiling = check_executable_edge_ceiling(live_edge, self.settings.max_edge)
        if not trace.gate("edge_ceiling", ceiling.allowed, ceiling.reason):
            # Both of these mean a book read came back wrong, which is a fault
            # and not a windfall. A fault the operator never hears about is one
            # nobody ever fixes, so they stay on the console.
            logger.warning("{}: {}", market.asset, ceiling.reason)
            self._start_cooldown(market.ticker)
            trace.finish("skipped", "edge above the plausibility ceiling")
            self._note_refusal(market, "edge_ceiling", "edge-ceiling")
            return False

        if self.live:
            try:
                available_balance = await self.client.get_available_balance(
                    market.exchange_index
                )
            except Exception as exc:  # no verified cash means no new entry
                logger.warning(
                    "{}: entry skipped because indexed balance is unavailable ({})",
                    market.asset,
                    type(exc).__name__,
                )
                trace.gate(
                    "available_balance",
                    False,
                    f"indexed balance unavailable: {type(exc).__name__}",
                )
                trace.finish("skipped", "balance unavailable")
                self._note_refusal(market, "available_balance", "balance-unavailable")
                return False
        else:
            available_balance = self.ledger.paper_summary(
                environment=self.settings.environment
            ).available_balance
        trace.set("available_balance", available_balance)
        if not trace.gate(
            "available_balance", available_balance > 0, "no available balance"
        ):
            logger.error("No available {} balance remains", self.mode)
            trace.finish("skipped", "no available balance")
            self._note_refusal(market, "available_balance", "no-balance")
            return False

        try:
            plan = self.client.build_order_plan(
                market=market,
                quote=quote,
                available_balance=available_balance,
                model_probability=candidate.outcome_probability,
                maximum_budget=getattr(
                    self.settings,
                    "trade_budget_usd",
                    getattr(self.settings, "live_capital_limit_usd", Decimal("10")),
                ),
            )
        except ValueError as exc:
            trace.gate("sizeable", False, str(exc))
            logger.debug("{}: not sizeable right now: {}", market.asset, exc)
            self._start_cooldown(market.ticker)
            trace.finish("skipped", "order below the minimum size")
            self._note_refusal(market, "sizeable", "unsizeable")
            return False
        trace.gate("sizeable", True)

        # Optional exposure-scaled admission runs here because the fixed stake
        # is not known until depth clips the plan. Refusing after sizing costs
        # one book read and no money; sizing after refusing is impossible.
        # Guarded on the uplift, and not merely for speed: the exposure needs
        # the account's total value, which in live mode is an authenticated
        # round trip. Spending one immediately before an IOC order on a market
        # measured in seconds, to evaluate a gate that is switched off, would
        # be latency bought for nothing.
        uplift = getattr(self.settings, "size_probability_uplift", Decimal("0"))
        if uplift > 0:
            portfolio = await self._portfolio_value()
            intended_exposure = (
                plan.maximum_total_debit / portfolio
                if portfolio > 0
                # Unknown is fully exposed, as everywhere else here: a balance
                # that cannot be read must not switch the gate off.
                else Decimal("1")
            )
            required = required_probability(
                base=self.settings.min_probability,
                intended_exposure=intended_exposure,
                full_exposure=self.settings.size_probability_full_exposure,
                uplift=uplift,
            )
        else:
            intended_exposure = Decimal("0")
            required = Decimal("0")
        if uplift > 0 and not trace.gate(
            "size_scaled_probability",
            check_size_scaled_probability(
                candidate.outcome_probability, intended_exposure, required
            ).allowed,
            f"P={float(candidate.outcome_probability):.1%} below the "
            f"{float(required):.1%} floor for a position worth "
            f"{float(intended_exposure):.1%} of the account",
            intended_exposure=str(intended_exposure),
            required_probability=str(required),
        ):
            logger.debug(
                "{}: {:.1%} position needs P>={:.1%}, model says {:.1%}",
                market.asset,
                float(intended_exposure),
                float(required),
                float(candidate.outcome_probability),
            )
            self._start_cooldown(market.ticker)
            trace.finish("skipped", "too large for the confidence behind it")
            self._note_refusal(market, "size_scaled_probability", "size-vs-prob")
            return False

        trace.set(
            "order_plan",
            {
                "contracts": plan.contracts,
                "outcome_limit_price": plan.outcome_limit_price,
                "sizing_fraction": plan.sizing_fraction,
                "maximum_total_debit": plan.maximum_total_debit,
                "client_order_id": plan.client_order_id,
            },
        )

        placed_at = datetime.now(timezone.utc)
        status_check_time = market.close_time - timedelta(
            seconds=self.settings.status_check_lead_seconds
        )

        # The checks above contain network awaits. Ctrl+C may have arrived
        # during any of them, so re-check at the final point before either a
        # live order or a simulated fill mutates state.
        if getattr(self, "_stopping", False):
            trace.gate("shutdown", False, "shutdown requested before order")
            trace.finish("skipped", "shutdown requested")
            return False

        if self.live:
            result = await self.client.place_order(plan)
            if not trace.gate(
                "order_filled",
                result.filled,
                result.error_code or "no resting size at limit",
                fill_count=result.fill_count,
                remaining_count=result.remaining_count,
            ):
                logger.warning(
                    "{}: IOC order did not fill ({}); cooling down {}",
                    market.asset,
                    result.error_code or "no resting size at limit",
                    market.ticker,
                )
                self._start_cooldown(market.ticker)
                trace.finish("order_rejected", result.error_code)
                self._note_refusal(market, "order_filled", "unfilled")
                return False
            filled_contracts = result.fill_count
            yes_leg_fill_price = (
                result.average_fill_price
                if result.average_fill_price is not None
                else plan.sdk_yes_leg_price
            )
            entry_price = (
                yes_leg_fill_price
                if candidate.outcome == "yes"
                else Decimal("1") - yes_leg_fill_price
            )
            order_id = result.order_id
            if result.remaining_count > 0:
                _event.info(
                    "{}: partial fill {} of {} contracts; remainder cancelled",
                    market.asset,
                    filled_contracts,
                    plan.contracts,
                )
        else:
            filled_contracts = plan.contracts
            # Use the limit price as a conservative simulated taker fill.
            entry_price = plan.outcome_limit_price
            order_id = f"paper-{plan.client_order_id}"

        simulated_fill = calculate_paper_fill(
            contracts=filled_contracts,
            price=entry_price,
            fee_multiplier=market.fee_multiplier,
        )
        # Everything between the venue fill above and the ledger row below is
        # the one window this bot cannot make atomic: in live mode the money is
        # already committed, and an INSERT that raises here would unwind to
        # run()'s catch-all, leaving a funded position with no ledger row and
        # nothing monitoring it to settlement. It cannot be prevented from
        # here - execution/trade_ledger.py carries no except by design, because
        # the caller's ability to see the failure is what distinguishes a
        # recorded trade from a lost one - so it is made loud and recoverable
        # instead.
        try:
            trade = self.ledger.record_open_trade(
                mode=self.mode,
                environment=self.settings.environment,
                strategy="predictor",
                asset=market.asset,
                ticker=market.ticker,
                event_ticker=market.event_ticker,
                order_id=order_id,
                client_order_id=plan.client_order_id,
                outcome=candidate.outcome,
                placed_at=placed_at,
                market_open_time=market.open_time,
                market_close_time=market.close_time,
                status_check_time=status_check_time,
                contracts=filled_contracts,
                entry_price=entry_price,
                entry_cost=simulated_fill.entry_cost,
                fees_paid=simulated_fill.fees_paid,
                total_debit=simulated_fill.total_debit,
                target_price=market.target_price,
                model_probability=candidate.outcome_probability,
                model_edge=live_edge,
                metadata={
                    "top_of_book_price": str(quote.best_price),
                    "buffered_price": str(buffered_price),
                    "depth_at_limit": str(quote.depth_at_limit),
                    "sizing_fraction": str(plan.sizing_fraction),
                    "signal_edge": str(candidate.edge),
                    "maximum_total_debit": str(plan.maximum_total_debit),
                    "exchange_index": market.exchange_index,
                    "prediction": {
                        key: str(value) if isinstance(value, Decimal) else value
                        for key, value in asdict(candidate.prediction).items()
                    },
                    "fee_model": "kalshi_quadratic_taker_estimate",
                    "fee_multiplier": str(market.fee_multiplier),
                    "decision_id": trace.decision_id,
                    "regime": candidate.prediction.regime,
                },
            )
        except Exception as exc:
            self._report_unrecorded_position(
                exc,
                market=market,
                outcome=candidate.outcome,
                order_id=order_id,
                client_order_id=plan.client_order_id,
                contracts=filled_contracts,
                entry_price=entry_price,
                total_debit=simulated_fill.total_debit,
                placed_at=placed_at,
            )
            trace.finish("error", f"ledger write failed: {exc}")
            raise

        self.ledger_audit.record(
            "record_open_trade",
            trade_id=trade.trade_id,
            ticker=trade.ticker,
            mode=trade.mode,
            contracts=trade.contracts,
            price=trade.entry_price,
            result=f"read back status={trade.status}",
            order_id=order_id,
            total_debit=trade.total_debit,
        )
        self.period.record_trade()
        trace.update(
            {
                "trade_id": trade.trade_id,
                "fill": {
                    "contracts": filled_contracts,
                    "entry_price": entry_price,
                    "entry_cost": simulated_fill.entry_cost,
                    "fees_paid": simulated_fill.fees_paid,
                    "total_debit": simulated_fill.total_debit,
                },
            }
        )
        trace.finish("traded", f"{candidate.outcome.upper()} at {entry_price}")
        self._append_audit_event("opened", trade)
        _event.warning(
            format_trade_block(
                mode=self.mode,
                asset=market.asset,
                ticker=market.ticker,
                outcome=candidate.outcome,
                contracts=filled_contracts,
                entry_price=entry_price,
                total_debit=simulated_fill.total_debit,
                probability=candidate.outcome_probability,
                edge=live_edge,
                edge_floor=live_edge_floor,
                regime=candidate.prediction.regime,
                late=candidate.is_late,
                sizing_fraction=plan.sizing_fraction,
                gates_passed=[gate.name for gate in trace.gates if gate.allowed],
                settles_at=market.close_time,
            )
        )
        return True

    def _report_unrecorded_position(
        self,
        error: BaseException,
        *,
        market: KalshiMarket,
        outcome: str,
        order_id: str,
        client_order_id: str,
        contracts: Decimal,
        entry_price: Decimal,
        total_debit: Decimal,
        placed_at: datetime,
    ) -> None:
        """A fill the ledger never took. Say so loudly, in both places.

        In live mode this is a real position nobody is watching, so everything
        needed to rebuild the row by hand goes into the audit log, and the run
        halts: a bot that keeps scanning around an invisible position will size
        the next one against a bankroll that is already spent.
        """
        self.ledger_audit.failure(
            "record_open_trade",
            error=error,
            trade_id=None,
            ticker=market.ticker,
            mode=self.mode,
            contracts=contracts,
            price=entry_price,
            order_id=order_id,
            client_order_id=client_order_id,
            outcome=outcome,
            total_debit=total_debit,
            placed_at=placed_at.isoformat(),
            market_close_time=market.close_time.isoformat(),
        )
        logger.error(
            "{} {}: the ledger refused the {} fill of {} @ ${} (order {}): {}. "
            "Reconstruct it from {}",
            market.asset,
            market.ticker,
            self.mode.upper(),
            contracts,
            entry_price,
            order_id,
            error,
            LEDGER_AUDIT_NAME,
        )
        if self.live:
            self._halt_reason = (
                f"a live fill of {contracts} {market.ticker} was not recorded "
                f"in the ledger (order {order_id})"
            )

    async def _monitor_open_trade(self, trade: TradeRecord, once: bool) -> None:
        """Back off until close-5s, then poll Kalshi until the result is final."""
        now = datetime.now(timezone.utc)

        # The salvage window opens before the first status check, so without
        # this the feature would be unreachable in exactly the mode this bot has
        # run live in: the loop sleeps straight through close-30s to close-5s.
        # Waking at whichever comes first costs one extra wake per position.
        until_next_check: float | None = None
        if self.salvage is not None:
            # Exposure is measured against the live account, and this path asks
            # is_due before run() has had a chance to supply it.
            self.salvage.set_portfolio_value(await self._portfolio_value())
            if self.salvage.is_due(trade, now):
                if await self._salvage_pass():
                    # Sold. The row is EXITED, so there is nothing left to
                    # monitor and run() must go back to scanning rather than
                    # poll a closed position to settlement.
                    return
            # Asked AFTER the check, and unconditionally. This loop does not
            # poll - it sleeps to the status check in one call - so a refused
            # check used to end supervision for the position: the extra checks
            # an exposed position had earned were scheduled into the middle of
            # that sleep and never happened. Waking at whichever comes first is
            # what makes the budget real rather than merely configured.
            until_next_check = self.salvage.seconds_until_next_check(
                trade, datetime.now(timezone.utc)
            )

        if now < trade.status_check_time:
            seconds = Decimal(str((trade.status_check_time - now).total_seconds()))
            label = "status check"
            reason = "First status check in {:.1f}s (14:55 of interval)"
            if until_next_check is not None and 0 <= until_next_check < float(seconds):
                seconds = Decimal(str(max(until_next_check, 0.0)))
                label = "salvage check"
                reason = "Next salvage check in {:.1f}s, then the status check"
            _wait.info(
                "{} {} remains OPEN; no new scans. " + reason,
                trade.asset,
                trade.ticker,
                float(seconds),
            )
            if not once:
                self._countdown_label = label
                await self._interruptible_wait(seconds)
            return

        status_client = getattr(self, "_discovery_client", self.client)
        market_status = await status_client.get_market_status(trade.ticker)
        checked_at = datetime.now(timezone.utc)
        self.ledger.record_status_check(
            trade.trade_id,
            market_status.status,
            checked_at=checked_at,
        )
        self.ledger_audit.record(
            "record_status_check",
            trade_id=trade.trade_id,
            ticker=trade.ticker,
            mode=trade.mode,
            contracts=trade.contracts,
            price=trade.entry_price,
            result=f"exchange_status={market_status.status}",
        )
        logger.debug(
            "{} {} status check: exchange_status={} result={}",
            trade.asset,
            trade.ticker,
            market_status.status,
            market_status.result or "pending",
        )

        if market_status.result in {"yes", "no", "void"}:
            # settle_trade is idempotent: handed a row that is no longer OPEN it
            # returns the existing record unchanged. Appending to trades.jsonl
            # regardless put a second "settled" line in the audit stream for a
            # trade that settled once, so ask the ledger first.
            before = self.ledger.get_trade(trade.trade_id)
            was_open = before is not None and before.status == "OPEN"
            settled = self.ledger.settle_trade(
                trade.trade_id,
                market_result=market_status.result,
                exchange_status=market_status.status,
                settled_at=market_status.settlement_time or checked_at,
            )
            self.ledger.record_probability_calibration(
                settled,
                market_status.result,
                market_status.settlement_time or checked_at,
            )
            self.ledger_audit.record(
                "settle_trade",
                trade_id=settled.trade_id,
                ticker=settled.ticker,
                mode=settled.mode,
                contracts=settled.contracts,
                price=settled.entry_price,
                result=(
                    f"read back status={settled.status} pnl={settled.pnl}"
                    if was_open
                    else f"already settled as {settled.status}; no row changed"
                ),
                market_result=settled.market_result,
            )
            if was_open:
                self._append_audit_event("settled", settled)
                self.period.record_settlement(settled.status, settled.pnl)
            # The settlement is the label for the decision that opened this
            # trade. Without it the decision log records predictions that can
            # never be scored.
            self.decisions.record_event(
                "settlement",
                trade_id=settled.trade_id,
                decision_id=settled.metadata.get("decision_id"),
                regime=settled.metadata.get("regime"),
                mode=settled.mode,
                environment=settled.environment,
                asset=settled.asset,
                ticker=settled.ticker,
                outcome=settled.outcome,
                market_result=settled.market_result,
                status=settled.status,
                exchange_status=market_status.status,
                model_probability=settled.model_probability,
                model_edge=settled.model_edge,
                contracts=settled.contracts,
                entry_price=settled.entry_price,
                total_debit=settled.total_debit,
                gross_payout=settled.gross_payout,
                pnl=settled.pnl,
            )
            _event.warning(
                "{} {} {}: {} | result={} | P&L={:+.2f}",
                settled.asset,
                settled.ticker,
                settled.outcome.upper(),
                settled.status,
                settled.market_result.upper() if settled.market_result else "?",
                float(settled.pnl or Decimal("0")),
            )
            if not self.live:
                self._log_paper_summary()
            self._maybe_log_hourly()
            return

        if once:
            return

        now = datetime.now(timezone.utc)
        if now < trade.market_close_time:
            wait_seconds = Decimal(
                str((trade.market_close_time - now).total_seconds() + 0.5)
            )
            _wait.info(
                "14:55 status is pending; waiting {:.1f}s for market close",
                float(wait_seconds),
            )
            self._countdown_label = "close"
            await self._interruptible_wait(wait_seconds)
        else:
            logger.debug(
                "Market is closed but not finalized; polling settlement again in {}s",
                self.settings.settlement_poll_seconds,
            )
            await self._interruptible_wait(
                Decimal(str(self.settings.settlement_poll_seconds))
            )

    async def _check_capital_breaker(self) -> GateResult:
        """Stop trading after a loss rail and persist the halt across restarts."""
        if self.live:
            balance = await self.client.get_available_balance()
        else:
            balance = self.ledger.paper_summary(
                environment=self.settings.environment
            ).current_balance
        # The hourly block reports this rather than asking the venue again: a
        # log line has no business making a network call.
        self._last_balance = balance
        risk_date = datetime.now(timezone.utc).date().isoformat()
        state = self.ledger.get_risk_state(
            mode=self.mode,
            environment=self.settings.environment,
            risk_date=risk_date,
        )
        if state is not None and bool(state["halted"]):
            reason = str(state["halt_reason"] or "persistent daily risk halt")
            if not (
                getattr(self.settings, "safe_mode_enabled", True)
                and "MAX_CONSECUTIVE_LOSSES" in reason
            ):
                return GateResult(False, reason)

        starting_balance = (
            Decimal(str(state["starting_balance"])) if state is not None else balance
        )
        durable_peak = (
            Decimal(str(state["peak_balance"])) if state is not None else balance
        )
        self._peak_balance = max(self._peak_balance, durable_peak, balance)
        settled_statuses = [
            trade.status
            for trade in self.ledger.list_trades(
                mode=self.mode,
                environment=self.settings.environment,
                limit=50,
            )
            if trade.status != "OPEN"
        ]
        self._update_safe_mode_from_ledger()
        result = check_capital_breaker(
            current_balance=balance,
            peak_balance=self._peak_balance,
            settled_statuses=settled_statuses,
            maximum_consecutive_losses=(
                10**9
                if getattr(self.settings, "safe_mode_enabled", True)
                else self.settings.max_consecutive_losses
            ),
            maximum_drawdown_fraction=self.settings.max_drawdown_fraction,
        )
        realized_pnl = balance - starting_balance
        daily_limit = getattr(self.settings, "max_daily_loss_usd", Decimal("0"))
        if result.allowed and daily_limit > 0 and realized_pnl <= -daily_limit:
            result = GateResult(
                False,
                f"UTC-day loss ${-realized_pnl:.2f} reached "
                f"MAX_DAILY_LOSS_USD=${daily_limit:.2f}",
            )
        loss_streak = 0
        for status in settled_statuses:
            if status == "LOST":
                loss_streak += 1
            elif status in {"WON", "VOID", "EXITED"}:
                break
        self.ledger.upsert_risk_state(
            mode=self.mode,
            environment=self.settings.environment,
            risk_date=risk_date,
            starting_balance=starting_balance,
            peak_balance=self._peak_balance,
            current_balance=balance,
            realized_pnl=realized_pnl,
            consecutive_losses=loss_streak,
            halted=not result.allowed,
            halt_reason=result.reason,
        )
        return result

    def _update_safe_mode_from_ledger(self) -> None:
        """Enter after three losses and leave after the next non-loss.

        Safe mode is selective recovery, not stake escalation: it disables new
        scalper quotes and raises probability/distance floors while retaining
        the same fixed dollar budget. Drawdown and daily-loss breakers remain
        hard halts.
        """
        if not getattr(getattr(self, "settings", None), "safe_mode_enabled", True):
            self._safe_mode = False
            return
        try:
            completed = [
                trade
                for trade in self.ledger.list_trades(
                    mode=self.mode,
                    environment=self.settings.environment,
                    limit=50,
                )
                if trade.status != "OPEN"
            ]
        except Exception:
            return
        streak = 0
        for trade in completed:
            lost = trade.status == "LOST" or (
                trade.status == "EXITED" and (trade.pnl or Decimal("0")) <= 0
            )
            if lost:
                streak += 1
            else:
                break
        trigger = getattr(self.settings, "safe_mode_loss_trigger", 3)
        active = streak >= trigger
        if active != self._safe_mode:
            self._safe_mode = active
            decisions = getattr(self, "decisions", None)
            if decisions is not None:
                decisions.record_event(
                    "safe_mode_changed",
                    mode=self.mode,
                    environment=self.settings.environment,
                    enabled=active,
                    consecutive_losses=streak,
                    probability_floor=str(
                        getattr(
                            self.settings, "safe_mode_min_probability", Decimal("0.92")
                        )
                    ),
                    market_price_floor=str(
                        getattr(
                            self.settings, "safe_mode_min_market_price", Decimal("0.85")
                        )
                    ),
                    settlement_sigma_floor=str(
                        getattr(
                            self.settings,
                            "safe_mode_min_settlement_sigma",
                            Decimal("2.0"),
                        )
                    ),
                )
            emit = logger.warning if active else logger.info
            emit(
                "SAFE MODE {} after {} consecutive loss(es); fixed stake remains ${}",
                "ENABLED" if active else "CLEARED",
                streak,
                getattr(self.settings, "trade_budget_usd", Decimal("2")),
            )

    def _in_cooldown(self, ticker: str) -> bool:
        deadline = self._cooldowns.get(ticker)
        if deadline is None:
            return False
        if time.monotonic() >= deadline:
            del self._cooldowns[ticker]
            return False
        return True

    def _start_cooldown(self, ticker: str) -> None:
        self._cooldowns[ticker] = (
            time.monotonic() + self.settings.order_failure_cooldown_seconds
        )

    @staticmethod
    def _choose_outcome(
        prediction: Prediction,
        market: KalshiMarket,
    ) -> tuple[str, Decimal, Decimal]:
        yes_probability = prediction.probability_yes
        no_probability = Decimal("1") - yes_probability
        yes_edge = yes_probability - market.yes_ask
        no_edge = no_probability - market.no_ask
        if yes_edge >= no_edge:
            return "yes", yes_probability, market.yes_ask
        return "no", no_probability, market.no_ask

    def _append_audit_event(self, event: str, trade: TradeRecord) -> None:
        self.settings.trade_log_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trade_id": trade.trade_id,
            "mode": trade.mode,
            "environment": trade.environment,
            "asset": trade.asset,
            "ticker": trade.ticker,
            "outcome": trade.outcome,
            "status": trade.status,
            "market_result": trade.market_result,
            "contracts": str(trade.contracts),
            "entry_price": str(trade.entry_price),
            "entry_cost": str(trade.entry_cost),
            "fees_paid": str(trade.fees_paid),
            "total_debit": str(trade.total_debit),
            "gross_payout": (
                str(trade.gross_payout) if trade.gross_payout is not None else None
            ),
            "pnl": str(trade.pnl) if trade.pnl is not None else None,
        }
        with self.settings.trade_log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(payload, separators=(",", ":")) + "\n")

    def _lifetime_stats(self) -> LifetimeStats:
        """The all-time picture, taken from the ledger rather than the process."""
        if not self.live:
            summary = self.ledger.paper_summary(environment=self.settings.environment)
            return LifetimeStats(
                trades=summary.total_trades,
                wins=summary.wins,
                losses=summary.losses,
                voids=summary.voids,
                pnl=summary.net_pnl,
                balance=summary.current_balance,
            )
        trades = self.ledger.list_trades(
            mode=self.mode,
            environment=self.settings.environment,
            limit=10_000,
        )
        settled = [trade for trade in trades if trade.status != "OPEN"]
        return LifetimeStats(
            trades=len(trades),
            wins=sum(1 for trade in settled if trade.status == "WON"),
            losses=sum(1 for trade in settled if trade.status == "LOST"),
            voids=sum(1 for trade in settled if trade.status == "VOID"),
            pnl=sum(
                (trade.pnl or Decimal("0") for trade in settled),
                Decimal("0"),
            ),
            balance=self._last_balance,
        )

    def _maybe_log_hourly(self, now: datetime | None = None) -> None:
        """Emit the hour's block if a wall-clock hour has turned.

        Asked, never scheduled. The check runs at points the loop is already
        awake, so a due block can never insert itself between an open position
        and its settlement poll, and a process that slept through three hours
        prints one block covering all three rather than a backlog.
        """
        clock = self.__dict__.get("_hourly")
        if clock is None:
            return
        moment = now or datetime.now(timezone.utc)
        if not clock.due(moment):
            return
        started, ended = clock.mark(moment)
        _stats.info(
            format_hourly_block(
                period_start=started,
                period_end=ended,
                period=self.period,
                lifetime=self._lifetime_stats(),
                mode=self.mode,
            )
        )
        self.period.reset()

    def log_stopped(self) -> None:
        """One shutdown line, whichever way the process is ending."""
        try:
            lifetime = self._lifetime_stats()
        except Exception:  # pragma: no cover - a closing ledger is not news
            _event.info("{} bot stopped", self.mode.upper())
            return
        _event.info(
            "{} bot stopped | all-time trades {} | W/L/V {}/{}/{} | pnl {:+.2f}",
            self.mode.upper(),
            lifetime.trades,
            lifetime.wins,
            lifetime.losses,
            lifetime.voids,
            lifetime.pnl,
        )

    def _log_paper_summary(self) -> None:
        summary = self.ledger.paper_summary(environment=self.settings.environment)
        _event.info(
            "PAPER LEDGER | balance=${:.2f} available=${:.2f} locked=${:.2f} "
            "| trades={} open={} W/L/V={}/{}/{} win_rate={:.1%} net_pnl={:+.2f}",
            float(summary.current_balance),
            float(summary.available_balance),
            float(summary.locked_capital),
            summary.total_trades,
            summary.open_trades,
            summary.wins,
            summary.losses,
            summary.voids,
            float(summary.win_rate),
            float(summary.net_pnl),
        )

    async def _interruptible_wait(self, seconds: Decimal) -> None:
        """Sleep until the deadline or until stop(), counting the last seconds.

        The countdown is armed by ``_countdown_label`` rather than passed in,
        because this is the one wait primitive the whole bot shares and every
        caller would otherwise have to carry a display concern it has no
        opinion about. Only the two waits that end at something an operator
        cares about - the 14:55 status check and market close - arm it.
        """
        countdown = self._countdown_label
        self._countdown_label = None
        total = max(0.0, float(seconds))
        lead = float(COUNTDOWN_SECONDS) if total > COUNTDOWN_SECONDS else 0.0
        if countdown is None or lead <= 0:
            await self._sleep_until_woken(total)
            return

        await self._sleep_until_woken(total - lead)
        if self._stopping:
            return
        ticks = countdown_ticks(
            COUNTDOWN_SECONDS,
            tty=_console_is_interactive(),
        )
        pause = lead / len(ticks)
        for tick in ticks:
            _wait.info("{}s to {}", tick, countdown)
            await self._sleep_until_woken(pause)
            if self._stopping:
                return

    async def _sleep_until_woken(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(
                self._wake_event.wait(),
                timeout=max(0.0, timeout),
            )
        except (asyncio.TimeoutError, TimeoutError):
            return
        finally:
            self._wake_event.clear()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Trade Kalshi 15-minute crypto and commodity markets "
            "(including Gold, Silver, and WTI)"
        )
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Place real orders (also requires ENABLE_LIVE_TRADING=true)",
    )
    run_mode = parser.add_mutually_exclusive_group()
    run_mode.add_argument(
        "--once",
        action="store_true",
        help="Run one market/status scan and exit without waiting",
    )
    run_mode.add_argument(
        "--continuous",
        action="store_true",
        help="Run paper mode continuously instead of its one-hour benchmark",
    )
    parser.add_argument(
        "--strategy",
        choices=("all", "both", "predictor", "scalper", "arb"),
        default="all",
        help=(
            "Which strategy runs. Default 'all' enables predictor, scalper, "
            "paper strategy wallets, and the arb shadow scanner. 'arb' is an isolated, "
            "paper-only Kalshi complementary-pair shadow scanner. Each is tagged in the "
            "ledger, so per-strategy P&L stays separable even though they "
            "share one capital pool. Naming one isolates it, which is the "
            "only way to attribute a session's result to it alone."
        ),
    )
    parser.add_argument(
        "--session-seconds",
        type=float,
        default=3600,
        help="Paper benchmark duration in seconds (default: 3600)",
    )
    parser.add_argument(
        "--session-passes",
        type=int,
        default=360,
        help="Evenly spaced paper evaluation passes (default: 360, every 10s)",
    )
    parser.add_argument(
        "--assets",
        help="Comma-separated subset, e.g. BTC,ETH,SOL",
    )
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument(
        "--silent",
        action="store_true",
        help=(
            "Drop routine per-market scan output. Fills, settlements, halts, "
            "lost market discovery, and errors still print; durable logs "
            "remain enabled"
        ),
    )
    verbosity.add_argument(
        "--verbose",
        action="store_true",
        help="Print every evaluation line to the console, as the file already gets",
    )
    return parser.parse_args(argv)


def _configure_logging(
    *,
    silent: bool = False,
    verbose: bool = False,
    configured: str | None = None,
) -> str:
    """Point stderr at one console policy and return the level it resolved to.

    The threshold for --silent is WARNING, not ERROR, on purpose. Every event
    that tells an operator the bot is alive and what it did with real money -
    live enablement and balance, fills and partial fills, settlement P&L,
    halts, and lost market discovery - is logged at WARNING. Silencing those
    made a blind live bot look exactly like a working one with no edge: silent,
    and placing no orders.

    The quiet default is a different mechanism for the same goal: it admits the
    lines that were tagged for a person plus everything at WARNING or worse, so
    a routine gate rejection can be dropped without dropping a fill that
    happens to be logged at the same severity.
    """
    level = resolve_console_level(
        silent=silent,
        verbose=verbose,
        configured=configured,
    )
    logger.remove()
    logger.add(
        sys.stderr,
        backtrace=False,
        diagnose=False,
        **console_sink_options(level),
    )
    return level


def _install_file_sink(settings: KalshiSettings) -> RunLogFile | None:
    """Attach the rotating run log. It takes everything, including DEBUG.

    A log directory that cannot be written is a lost diagnostic and never a
    reason to refuse to trade, so the failure is a warning and the run goes on
    with the console alone.
    """
    sink = RunLogFile(settings.log_dir, max_bytes=settings.log_max_bytes)
    try:
        sink.open()
    except OSError as exc:
        logger.warning("No run log: {} could not be opened ({})", settings.log_dir, exc)
        return None
    logger.add(
        sink,
        level="DEBUG",
        format=FILE_FORMAT,
        colorize=False,
        backtrace=True,
        diagnose=False,
    )
    return sink


def _install_signal_handlers(bot: KalshiCryptoBot) -> Callable[[], None]:
    """Route SIGINT/SIGTERM into graceful stop and return an uninstaller."""
    loop = asyncio.get_running_loop()
    loop_handlers: list[signal.Signals] = []
    fallback_handlers: dict[signal.Signals, Any] = {}
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, bot.stop)
            loop_handlers.append(signal_name)
        except (NotImplementedError, RuntimeError):  # non-POSIX / non-main loop
            previous = signal.getsignal(signal_name)
            fallback_handlers[signal_name] = previous

            def forward_to_loop(*_: Any, target: KalshiCryptoBot = bot) -> None:
                loop.call_soon_threadsafe(target.stop)

            signal.signal(signal_name, forward_to_loop)

    def uninstall() -> None:
        for signal_name in loop_handlers:
            loop.remove_signal_handler(signal_name)
        for signal_name, previous in fallback_handlers.items():
            signal.signal(signal_name, previous)

    return uninstall


async def run_bot(args: argparse.Namespace, settings: KalshiSettings) -> None:
    # Both strategies by default. They share a capital pool, so the ledger's
    # `strategy` column is what keeps their results separable - a shared pool
    # means a session's TOTAL is joint, while each strategy's own P&L is still
    # exactly attributable.
    strategy = args.strategy or "all"
    if strategy == "arb" and args.live:
        raise RuntimeError(
            "The Kalshi pair-arb scanner is shadow-only: the venue has no "
            "atomic two-leg order, so --strategy arb cannot run with --live"
        )
    settings = replace(
        settings,
        scalp_enabled=strategy in {"all", "both", "scalper"},
        predictor_enabled=strategy in {"all", "both", "predictor"},
        pair_arb_enabled=strategy in {"all", "arb"},
    )
    if strategy == "arb":
        _event.info(
            "Strategy: isolated Kalshi complementary-pair shadow (no order path)"
        )
    elif strategy == "all":
        _event.info(
            "Strategies: predictor + scalper + strategy lab + Kalshi pair-arb shadow"
        )
    elif strategy == "both":
        _event.info("Strategies: predictor + scalper (shared capital pool)")
    else:
        _event.info("Strategy: {} only (the other is disabled)", strategy)
    # Announced, because it is armed to sell a real position and an operator
    # reading a transcript afterwards should not have to infer it from .env.
    if settings.salvage_enabled:
        _event.info(
            "Salvage exit ARMED: one check {}s before close; sells at or below "
            "{} when the spread is at most {}",
            settings.salvage_window_seconds,
            settings.salvage_max_bid,
            settings.salvage_max_spread,
        )
        # Without this the line above is wrong whenever oversight is on: it
        # says "one check, 30s" while an exposed position is being examined
        # four times from five minutes out. A banner that misdescribes what is
        # armed against a real position is worse than no banner.
        if settings.oversight_enabled:
            _event.info(
                "  oversight ON: a position risking {:.0%}+ of the account is "
                "examined from {}s out, up to {} times, {}s apart. The price it "
                "sells at is unchanged at {}; {} is recorded only.",
                float(settings.oversight_full_exposure),
                settings.oversight_max_window_seconds,
                settings.oversight_max_checks,
                settings.oversight_min_spacing_seconds,
                settings.salvage_max_bid,
                settings.oversight_shadow_max_bid,
            )
    elif settings.oversight_enabled:
        # Oversight has no runtime of its own: it only shapes when the salvage
        # rule is asked. On without SALVAGE_ENABLED it is switched on and
        # unreachable, which is the exact shape of five past failures here.
        _event.warning(
            "OVERSIGHT_ENABLED is set but SALVAGE_ENABLED is not. Oversight "
            "only schedules the salvage exit, so nothing will examine a "
            "position and no shadow data will be recorded."
        )
    bot = KalshiCryptoBot(settings, live=args.live, silent=args.silent)
    uninstall_signal_handlers = _install_signal_handlers(bot)
    try:
        if not args.live and not args.once and not args.continuous:
            await bot.run_paper_session(
                duration_seconds=args.session_seconds,
                evaluation_passes=args.session_passes,
            )
        else:
            await bot.run(once=args.once)
        # SIGINT/SIGTERM is a clean process shutdown but not a completed data
        # collection run. Preserve that distinction for downstream analysis.
        if not bot._stopping:
            bot._data_run_completed = True
    finally:
        try:
            try:
                bot.log_stopped()
            finally:
                await bot.close()
        finally:
            uninstall_signal_handlers()


def config_file() -> Path:
    """Where this process reads its `.env` from.

    Delegates to `config.config_file`, which is the single resolver every tool
    shares. Kept as a name here because the launcher and the tests both use it.
    """
    return _config_file(PROJECT_ROOT)


def main() -> int:
    load_environment(PROJECT_ROOT)
    args = parse_args()
    # Configured twice on purpose: once from the command line alone, so a
    # settings error has somewhere to be printed, and again once .env has been
    # read and KX15_LOG_CONSOLE is known.
    _configure_logging(silent=args.silent, verbose=args.verbose)
    run_log_file: RunLogFile | None = None
    try:
        settings = KalshiSettings.from_env(PROJECT_ROOT)
        if args.assets:
            assets = tuple(
                dict.fromkeys(
                    asset.strip().upper()
                    for asset in args.assets.split(",")
                    if asset.strip()
                )
            )
            unsupported = sorted(set(assets) - set(settings.assets))
            if unsupported:
                raise ValueError(
                    "--assets must be a subset of TRADING_ASSETS; invalid: "
                    + ", ".join(unsupported)
                )
            settings = replace(settings, assets=assets)

        _configure_logging(
            silent=args.silent,
            verbose=args.verbose,
            configured=settings.log_console_level,
        )
        run_log_file = _install_file_sink(settings)
        if run_log_file is not None:
            _event.info("Run log: {}", run_log_file.path)

        asyncio.run(run_bot(args, settings))
        return 0
    except KeyboardInterrupt:
        # Covers Ctrl+C before run_bot has installed its loop-level handler.
        # Once installed, the normal path above exits without an exception.
        logger.warning("Interrupted during startup; exiting safely")
        return 130
    except Exception as exc:
        logger.error("Startup failed: {}", exc)
        return 1
    finally:
        if run_log_file is not None:
            run_log_file.close()


if __name__ == "__main__":
    raise SystemExit(main())
