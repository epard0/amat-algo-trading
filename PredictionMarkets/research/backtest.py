"""Backtest the sizing and gating changes against collected data.

Two halves, deliberately, because **the two questions have different evidence
behind them and merging them would launder the weaker one**:

**Half A - the market half.** `data/dataset/samples.jsonl` carries real Kalshi
book reads (`mkt_yes_bid`/`mkt_yes_ask`) beside the model's probability and the
venue's own settlement. It is the only source here with a real *price*, so it
is the only source that can produce a real *P&L*. It is also small: a few
hundred unique markets. Every figure from this half is deduplicated to one row
per ticker before anything is believed, because the log oversamples each market
roughly sevenfold and the un-deduplicated number is both larger and wrong -
CLAUDE.md records the earlier occasion this exact trap produced a confident
headline pointing the wrong way.

**Half B - the model half.** `data/candles/*.jsonl` is 40 to 231 days of
one-minute Kraken candles per asset. From it, the 15-minute intervals Kalshi
lists can be reconstructed exactly (strike = the interval's opening price,
settlement = its closing price) and the *real* predictor run at each evaluation
instant. That yields tens of thousands of independent observations - but no
price, so no P&L. What it can answer, and answer well, is the question actually
asked: does raising the probability floor improve the realised hit rate, and by
how much.

Neither half is a substitute for the other. Half B says whether the model's
confidence means anything; half A says whether acting on it pays after the
spread and Kalshi's quadratic fee. A claim needs both.

    uv run python research/backtest.py --report
    uv run python research/backtest.py --assets BTC,ETH --stride 5
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    # Run directly as a script, so the repo root is not on the path. The other
    # research tools are launched the same way and do the same thing.
    sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
from core.strategy_brain.strategies.candles import PriceCandle  # noqa: E402
from core.strategy_brain.strategies.crypto_15min_predictor import (  # noqa: E402
    Crypto15MinutePredictor,
)
from execution.kalshi_client import (  # noqa: E402
    AsyncKalshiTradingClient,
    BookQuote,
    KalshiMarket,
)
from execution.risk_gate import required_probability  # noqa: E402
from execution.trade_ledger import calculate_paper_fill  # noqa: E402

CANDLE_DIR = PROJECT_ROOT / "data" / "candles"
SAMPLE_FILE = PROJECT_ROOT / "data" / "dataset" / "samples.jsonl"

D = Decimal
INTERVAL_MINUTES = 15
WINDOW = 45  # the live bot's candle window, exactly

# No fee arithmetic lives here. `research/replay.py` reconciles 54 settled rows
# and 5 recorded orders against the production functions exactly; a second
# implementation in this file would be a second thing to keep in step, and the
# first version of it was wrong in three ways at once - it charged the
# quadratic fee PER CONTRACT rather than once on the order, sized with
# `stake / (price + fee)` instead of build_order_plan's budget arithmetic, and
# computed P&L without the cent-rounding that `fees_paid` actually absorbs.


# --------------------------------------------------------------- half A ----


@dataclass(frozen=True)
class Market:
    """One real market: what the model said, what it cost, how it settled."""

    ticker: str
    asset: str
    probability: Decimal
    yes_bid: Decimal
    yes_ask: Decimal
    settled_yes: bool
    fee_multiplier: Decimal
    seconds_to_close: float
    executed_price: Decimal | None = None
    """The limit the bot would actually pay, when it got far enough to read it.

    None means the price is NOT MEASURABLE for this market, never that the
    snapshot will do: the snapshot is 10.1 cents from the executed limit on
    average and worse in 53 of 58 measured cases - twice MIN_EXECUTABLE_EDGE.
    """

    def side(self) -> tuple[str, Decimal, Decimal]:
        """The side the model prefers, its ask, and the model's P for that side.

        Kalshi publishes one YES book: buying NO crosses the YES bids, so the
        NO ask is ``1 - yes_bid``. This mirrors ``KalshiMarket.no_ask`` rather
        than introducing a second convention.
        """
        if self.probability >= D("0.5"):
            outcome, snapshot, probability = "yes", self.yes_ask, self.probability
        else:
            outcome = "no"
            snapshot = D("1") - self.yes_bid
            probability = D("1") - self.probability
        return outcome, (self.executed_price or snapshot), probability

    def won(self, outcome: str) -> bool:
        return self.settled_yes if outcome == "yes" else not self.settled_yes


def load_markets(
    path: Path = SAMPLE_FILE,
    assets: Sequence[str] | None = None,
) -> list[Market]:
    """Real markets, venue-labelled, one row per ticker.

    The dedup is not tidying. 1,275 rows are a few hundred markets, and every
    per-market statistic computed before collapsing is an average over how
    often the bot happened to look at a market rather than over markets.
    Keeping the row closest to close keeps the most informed read of each.
    """
    if not path.exists():
        return []
    selected_assets = {asset.upper() for asset in assets} if assets else None
    best: dict[str, dict] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("label_source") != "venue":
            continue
        if row.get("label_settled_yes") is None:
            continue
        if row.get("model_probability_yes") is None:
            continue
        if selected_assets is not None and str(row.get("asset", "")).upper() not in selected_assets:
            continue
        if not row.get("mkt_yes_bid") or not row.get("mkt_yes_ask"):
            continue
        ticker = row["ticker"]
        prior = best.get(ticker)
        if prior is None:
            best[ticker] = row
            continue
        # A row carrying the executed quote always wins: it is the only one
        # that can be priced at what the trade would actually have cost.
        has_exec = bool(row.get("exec_limit_price"))
        prior_exec = bool(prior.get("exec_limit_price"))
        if has_exec != prior_exec:
            if has_exec:
                best[ticker] = row
            continue
        if float(row.get("seconds_to_close") or 1e9) < float(
            prior.get("seconds_to_close") or 1e9
        ):
            best[ticker] = row

    markets: list[Market] = []
    for row in best.values():
        try:
            markets.append(
                Market(
                    ticker=row["ticker"],
                    asset=row["asset"],
                    probability=D(str(row["model_probability_yes"])),
                    yes_bid=D(str(row["mkt_yes_bid"])),
                    yes_ask=D(str(row["mkt_yes_ask"])),
                    settled_yes=bool(row["label_settled_yes"]),
                    fee_multiplier=D(str(row.get("mkt_fee_multiplier") or "1")),
                    seconds_to_close=float(row.get("seconds_to_close") or 0.0),
                    executed_price=(
                        D(str(row["exec_limit_price"]))
                        if row.get("exec_limit_price")
                        else None
                    ),
                )
            )
        except (ArithmeticError, ValueError, TypeError):
            continue
    return markets


def base_settings() -> "config.KalshiSettings":
    """The shipped configuration, read from .env exactly as the bot reads it."""
    return config.KalshiSettings.from_env()


def variant(name: str, **overrides):
    """A named settings variant. Real KalshiSettings, so real validation.

    Deliberately not a parallel dataclass of the fields this file happens to
    care about: a simulator whose knobs are a hand-copied subset of the bot's
    drifts from it silently, and the copy is what would then be measured.
    """
    from dataclasses import replace

    return (name, replace(base_settings(), **overrides))


def client_for(settings) -> AsyncKalshiTradingClient:
    instance = object.__new__(AsyncKalshiTradingClient)
    instance.settings = settings
    instance.authenticated = True
    return instance


def _market_for(market: Market) -> KalshiMarket:
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    return KalshiMarket(
        asset=market.asset,
        ticker=market.ticker,
        event_ticker=market.ticker.rsplit("-", 1)[0],
        title="",
        target_price=D("1"),
        open_time=now - timedelta(minutes=14),
        close_time=now + timedelta(minutes=1),
        yes_bid=market.yes_bid,
        yes_ask=market.yes_ask,
        yes_bid_size=D("1000"),
        yes_ask_size=D("1000"),
        fee_multiplier=market.fee_multiplier,
    )


@dataclass
class Result:
    trades: int = 0
    wins: int = 0
    staked: Decimal = D("0")
    pnl: Decimal = D("0")
    refused_by_size_gate: int = 0
    returns: list = None

    def __post_init__(self) -> None:
        if self.returns is None:
            self.returns = []

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades else 0.0

    @property
    def roi(self) -> float:
        return float(self.pnl / self.staked) if self.staked else 0.0


def simulate(markets, settings, bankroll: Decimal) -> Result:
    """Run one configuration over the real markets, on the production path.

    Every number here comes from ``build_order_plan`` and
    ``calculate_paper_fill`` - the same functions the bot calls, reconciled
    against 54 settled rows by ``research/replay.py``. Flat bankroll, because
    these are a deduplicated set rather than a time series and compounding
    would make the answer depend on the order they happen to appear in.
    """
    out = Result()
    client = client_for(settings)
    for market in markets:
        outcome, price, probability = market.side()
        if not (D("0") < price < D("1")):
            continue
        if probability < settings.min_probability:
            continue
        # The bot's own edge gates, in the bot's own order.
        if probability - price > settings.max_edge:
            continue
        try:
            plan = client.build_order_plan(
                market=_market_for(market),
                quote=BookQuote(
                    outcome=outcome,
                    best_price=price,
                    limit_price=price,
                    depth_at_limit=D("100000"),
                ),
                available_balance=bankroll,
                model_probability=probability,
                maximum_budget=settings.trade_budget_usd,
            )
        except (ValueError, ArithmeticError):
            continue

        fill = calculate_paper_fill(plan.contracts, price, market.fee_multiplier)
        # Buffered edge, measured on what the fill actually costs per contract
        # rather than on a fee estimate, so the gate sees the real number.
        per_contract = fill.total_debit / plan.contracts
        if probability - per_contract - settings.max_slippage < settings.min_edge:
            continue

        exposure = fill.total_debit / bankroll if bankroll else D("1")
        floor = required_probability(
            base=settings.min_probability,
            intended_exposure=exposure,
            full_exposure=settings.size_probability_full_exposure,
            uplift=settings.size_probability_uplift,
        )
        if probability < floor:
            out.refused_by_size_gate += 1
            continue

        won = market.won(outcome)
        payout = plan.contracts if won else D("0")
        pnl = payout - fill.total_debit

        out.trades += 1
        out.wins += int(won)
        out.staked += fill.total_debit
        out.pnl += pnl
        out.returns.append(float(pnl / fill.total_debit))
    return out


def bootstrap_ci(
    values: Sequence[float], draws: int = 4000, seed: int = 20260820
) -> tuple[float, float]:
    """Percentile bootstrap of the mean. Small samples need their error bars."""
    if len(values) < 2:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(draws):
        means.append(sum(values[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    return (means[int(0.025 * draws)], means[int(0.975 * draws)])


# --------------------------------------------------------------- half B ----


def load_candles(asset: str) -> list[tuple[int, PriceCandle]]:
    path = CANDLE_DIR / f"{asset}_1m.jsonl"
    if not path.exists():
        return []
    rows: list[tuple[int, PriceCandle]] = []
    with path.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                stamp = int(raw["t"])
                rows.append(
                    (
                        stamp,
                        PriceCandle(
                            timestamp=datetime.fromtimestamp(stamp, timezone.utc),
                            open=D(raw["o"]),
                            high=D(raw["h"]),
                            low=D(raw["l"]),
                            close=D(raw["c"]),
                            volume=D(raw["v"]),
                        ),
                    )
                )
            except (KeyError, ValueError, ArithmeticError):
                continue
    rows.sort(key=lambda item: item[0])
    return rows


@dataclass
class Observation:
    """One reconstructed evaluation: what the model said, and what happened."""

    asset: str
    probability: float
    settled_yes: bool
    minutes_remaining: int


def reconstruct(
    asset: str,
    *,
    stride: int,
    evaluate_at: Sequence[int],
    limit: int | None = None,
) -> list[Observation]:
    """Rebuild Kalshi's 15-minute intervals and price them with the real model.

    The strike is the interval's **opening** price and settlement is its
    **closing** price, which is how the series is defined. CLAUDE.md records
    that a synthesised strike misses Kalshi's CF Benchmarks strike by a p95 of
    0.13% - so these labels are right about the interval and approximate about
    the venue. That is acceptable here precisely because this half never
    produces a P&L: it measures whether the model's probability tracks the
    interval's own outcome, which is a question about the interval.
    """
    rows = load_candles(asset)
    if len(rows) < WINDOW + INTERVAL_MINUTES:
        return []
    stamps = [stamp for stamp, _ in rows]
    by_stamp = {stamp: candle for stamp, candle in rows}
    predictor = Crypto15MinutePredictor()

    observations: list[Observation] = []
    # Interval boundaries are wall-clock aligned, as Kalshi lists them.
    first = rows[WINDOW][0]
    start = first - (first % (INTERVAL_MINUTES * 60))
    last_open = rows[-1][0] - INTERVAL_MINUTES * 60

    step = INTERVAL_MINUTES * 60 * max(1, stride)
    for open_stamp in range(start, last_open, step):
        close_stamp = open_stamp + INTERVAL_MINUTES * 60
        opening = by_stamp.get(open_stamp)
        closing = by_stamp.get(close_stamp - 60)
        if opening is None or closing is None:
            continue
        strike = opening.open
        if strike <= 0:
            continue
        settled_yes = closing.close > strike

        for minutes_left in evaluate_at:
            at = close_stamp - minutes_left * 60
            # NO LOOKAHEAD. A one-minute candle stamped t covers [t, t+60) and
            # is only fully known at t+60, so evaluating at `at` may use bars
            # stamped at or before `at - 60` and no later. Taking the bar
            # stamped exactly `at` would hand the model the close of a minute
            # that has not happened yet - with two minutes left on a 15-minute
            # market, that single bar is most of the answer.
            index = bisect_right(stamps, at - 60) - 1
            if index < WINDOW - 1:
                continue
            window = [candle for _, candle in rows[index - WINDOW + 2 : index + 1]]
            if len(window) < WINDOW - 1:
                continue

            # The live bot always sees one more bar than this: Kraken's last
            # OHLC row is the minute in progress, and its close is live spot.
            # Reconstruct that bar from the OPEN of the minute containing `at`,
            # which is known at `at`. Its shape is never read - extract_features
            # takes close_location/wick_rejection from candles[:-1] precisely
            # because the forming bar has no meaningful shape yet - so only the
            # close matters, and this is spot.
            forming_index = bisect_right(stamps, at) - 1
            if forming_index < 0:
                continue
            spot = rows[forming_index][1].open
            window = window + [
                PriceCandle(
                    timestamp=datetime.fromtimestamp(at, timezone.utc),
                    open=spot,
                    high=spot,
                    low=spot,
                    close=spot,
                    volume=D("0"),
                )
            ]
            if len(window) < WINDOW:
                continue
            try:
                prediction = predictor.predict(
                    window,
                    strike,
                    datetime.fromtimestamp(close_stamp, timezone.utc),
                    now=datetime.fromtimestamp(at, timezone.utc),
                )
            except (ValueError, ArithmeticError):
                continue
            observations.append(
                Observation(
                    asset=asset,
                    probability=float(prediction.probability_yes),
                    settled_yes=settled_yes,
                    minutes_remaining=minutes_left,
                )
            )
            if limit and len(observations) >= limit:
                return observations
    return observations


def hit_rate_by_floor(
    observations: Sequence[Observation], floors: Sequence[float]
) -> list[tuple[float, int, float, float]]:
    """For each probability floor: trades admitted, hit rate, and its CI.

    Directional trades only: a floor of 0.62 admits a YES read above 0.62 and
    a NO read below 0.38, since the bot trades whichever side the model
    prefers. Counting only the YES side would understate every floor.
    """
    out = []
    for floor in floors:
        wins = total = 0
        for obs in observations:
            if obs.probability >= floor:
                total += 1
                wins += int(obs.settled_yes)
            elif obs.probability <= 1 - floor:
                total += 1
                wins += int(not obs.settled_yes)
        rate = wins / total if total else 0.0
        low, high = wilson(wins, total)
        out.append((floor, total, rate, high - rate if total else 0.0))
        out[-1] = (floor, total, rate, (low, high))
    return out


def wilson(wins: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson interval - the normal approximation is wrong near 0 and 1."""
    if total == 0:
        return (0.0, 0.0)
    p = wins / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def brier(observations: Sequence[Observation]) -> float:
    if not observations:
        return float("nan")
    return sum(
        (obs.probability - (1.0 if obs.settled_yes else 0.0)) ** 2
        for obs in observations
    ) / len(observations)


def calibration(
    observations: Sequence[Observation], buckets: int = 10
) -> list[tuple[float, float, int, float]]:
    """Predicted vs realised, bucketed. The check the headline hides."""
    bins: dict[int, list[Observation]] = defaultdict(list)
    for obs in observations:
        bins[min(buckets - 1, int(obs.probability * buckets))].append(obs)
    rows = []
    for index in sorted(bins):
        group = bins[index]
        predicted = sum(o.probability for o in group) / len(group)
        realised = sum(o.settled_yes for o in group) / len(group)
        rows.append(((index + 0.5) / buckets, predicted, len(group), realised))
    return rows


# ----------------------------------------------------------------- report --


def format_market_half(markets: Sequence[Market], regimes: Iterable) -> str:
    lines = [
        "",
        "=" * 78,
        "HALF A - real Kalshi prices, real venue settlements",
        "=" * 78,
        f"unique markets after dedup: {len(markets)}",
    ]
    if not markets:
        lines.append("  no venue-labelled samples on disk; run research/settlements.py")
        return "\n".join(lines)

    by_asset: dict[str, int] = defaultdict(int)
    for market in markets:
        by_asset[market.asset] += 1
    lines.append(
        "  by asset: "
        + ", ".join(f"{a} {n}" for a, n in sorted(by_asset.items(), key=lambda x: -x[1]))
    )
    settled = sum(m.settled_yes for m in markets)
    lines.append(f"  base rate P(yes) = {settled / len(markets):.1%}")

    priced = [m for m in markets if m.executed_price is not None]
    lines.append("")
    lines.append(
        f"  priced at the EXECUTED limit : {len(priced)} of {len(markets)}"
    )
    if len(priced) < len(markets):
        lines.append(
            f"  priced at the discovery snapshot: {len(markets) - len(priced)} "
            "- and the snapshot is not what a trade costs."
        )
        lines.append(
            "    Measured over 58 records carrying both, the executed limit is"
        )
        lines.append(
            "    10.1c from the snapshot on average and WORSE in 53 of 58 -"
        )
        lines.append(
            "    twice MIN_EXECUTABLE_EDGE. Snapshot-priced rows describe a"
        )
        lines.append(
            "    cheaper game than the one played, and rate sizing too kindly."
        )
    if not priced:
        lines.append("")
        lines.append(
            "  NO market can be priced faithfully yet, so no P&L is reported."
        )
        return "\n".join(lines)
    lines.append("")
    lines.append(f"  P&L below is over the {len(priced)} executed-price markets only.")
    lines.append("")
    lines.append(
        f"  {'regime':<26}{'trades':>7}{'win':>8}{'ROI/$':>9}"
        f"{'95% CI on ROI':>22}{'refused':>9}"
    )
    lines.append("  " + "-" * 74)
    for name, settings in regimes:
        result = simulate(priced, settings, D("100"))
        low, high = bootstrap_ci(result.returns)
        ci = f"[{low:+.3f}, {high:+.3f}]" if result.trades > 1 else "n/a"
        lines.append(
            f"  {name:<26}{result.trades:>7}{result.win_rate:>8.1%}"
            f"{result.roi:>+9.3f}{ci:>22}{result.refused_by_size_gate:>9}"
        )
    return "\n".join(lines)


def format_model_half(observations: Sequence[Observation]) -> str:
    lines = [
        "",
        "=" * 78,
        "HALF B - reconstructed 15-minute intervals, real predictor, no prices",
        "=" * 78,
        f"independent evaluations: {len(observations):,}",
    ]
    if not observations:
        lines.append("  no candles on disk; run research/backfill_candles.py")
        return "\n".join(lines)

    by_asset: dict[str, int] = defaultdict(int)
    for obs in observations:
        by_asset[obs.asset] += 1
    lines.append(
        "  by asset: "
        + ", ".join(f"{a} {n:,}" for a, n in sorted(by_asset.items()))
    )
    lines.append(f"  Brier score: {brier(observations):.4f}   (0.25 = coin flip)")
    lines.append("")
    lines.append("  probability floor -> realised hit rate of the side taken")
    lines.append(
        f"  {'floor':>7}{'trades':>10}{'hit rate':>11}{'95% CI':>22}{'vs 0.62':>10}"
    )
    lines.append("  " + "-" * 60)
    rows = hit_rate_by_floor(
        observations, [0.55, 0.60, 0.62, 0.66, 0.70, 0.72, 0.75, 0.80, 0.85]
    )
    base = next((r for r in rows if abs(r[0] - 0.62) < 1e-9), None)
    for floor, total, rate, (low, high) in rows:
        delta = f"{rate - base[2]:+.1%}" if base and base[1] else "n/a"
        lines.append(
            f"  {floor:>7.2f}{total:>10,}{rate:>11.1%}"
            f"{f'[{low:.1%}, {high:.1%}]':>22}{delta:>10}"
        )

    lines.append("")
    lines.append("  calibration (predicted vs realised, YES side)")
    lines.append(f"  {'bucket':>8}{'predicted':>12}{'realised':>11}{'n':>10}")
    lines.append("  " + "-" * 41)
    for _, predicted, count, realised in calibration(observations):
        lines.append(
            f"  {predicted:>8.2f}{predicted:>12.1%}{realised:>11.1%}{count:>10,}"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", default=None, help="comma list; default all")
    parser.add_argument(
        "--stride",
        type=int,
        default=4,
        help="evaluate every Nth 15-minute interval (4 = hourly)",
    )
    parser.add_argument(
        "--evaluate-at",
        default="10,5,2",
        help="minutes before close to evaluate at",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--skip-candles", action="store_true")
    args = parser.parse_args()

    config.load_environment(PROJECT_ROOT)
    assets = (
        tuple(a.strip().upper() for a in args.assets.split(",") if a.strip())
        if args.assets
        else tuple(config.SERIES_BY_ASSET)
    )
    evaluate_at = [int(m) for m in args.evaluate_at.split(",") if m.strip()]

    baseline = base_settings()
    regimes = [
        (f"baseline fixed ${baseline.trade_budget_usd}", baseline),
        variant("probability floor 0.70", min_probability=D("0.70")),
        variant("probability floor 0.72", min_probability=D("0.72")),
        variant("probability floor 0.75", min_probability=D("0.75")),
        variant("edge ceiling 0.10", max_edge=D("0.10")),
        variant("edge ceiling 0.12", max_edge=D("0.12")),
    ]

    report = [format_market_half(load_markets(assets=assets), regimes)]

    if not args.skip_candles:
        observations: list[Observation] = []
        for asset in assets:
            found = reconstruct(
                asset,
                stride=args.stride,
                evaluate_at=evaluate_at,
                limit=args.limit,
            )
            print(f"  {asset}: {len(found):,} evaluations", flush=True)
            observations.extend(found)
        report.append(format_model_half(observations))

    text = "\n".join(report) + "\n"
    print(text)
    if args.output:
        Path(args.output).write_text(text)


if __name__ == "__main__":
    main()
