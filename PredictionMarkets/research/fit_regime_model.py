#!/usr/bin/env python3
"""Fit a Gaussian mixture over market regimes and export it as JSON.

Offline only. This is the one place scikit-learn is allowed, and it is a dev
dependency: what ships to the trading host is the JSON artifact, scored in pure
Python by ``core.strategy_brain.strategies.regime_model``. Nothing under
``research/`` is importable from ``bot.py``, by design.

    # 1. see what the data supports before fitting anything
    uv run --extra research python research/fit_regime_model.py --report-only

    # 2. fit, with model selection over k by BIC on a held-out split
    uv run --extra research python research/fit_regime_model.py \
        --decisions decisions.jsonl --out models/regime_mixture.json

    # 3. the bot only uses it once you point at it explicitly
    REGIME_MODEL_PATH=models/regime_mixture.json python bot.py --once

The per-state multipliers are *not* learned — they are seeded from the nearest
hand-tuned regime and then damped toward neutral in proportion to how uncertain
the state is. Learning the multipliers from outcomes needs settled trades, and
there are 17. Learning the *states* needs only features, of which there are
thousands. This script does the half the data supports and refuses the half it
does not.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import SERIES_BY_ASSET  # noqa: E402
from core.strategy_brain.strategies.market_regime import (  # noqa: E402
    CONSOLIDATION,
    NEUTRAL,
    REVERSAL,
    TREND,
    VOLATILE_CHOP,
)
from core.strategy_brain.strategies.regime_model import (  # noqa: E402
    SCHEMA_VERSION,
    RegimeMixture,
)

# The 15 fields RegimeFeatures exposes, in a fixed order. The artifact records
# this list, so a later feature addition cannot silently shift the vector.
FEATURE_ORDER = (
    "realized_vol_fast",
    "realized_vol_slow",
    "vol_ratio",
    "efficiency_ratio",
    "return_autocorrelation",
    "range_compression",
    "impulse_sigma",
    "impulse_direction",
    "minutes_since_impulse",
    "reversal_retracement",
    "close_location",
    "wick_rejection",
    "travel_direction",
    "recent_travel_sigma",
)
# `bars_available` was here and is gone. Measured over 25,196 samples it has a
# standard deviation of exactly zero and one distinct value: the bot always
# feeds the classifier a full 45-candle window, so the feature records that
# fact and nothing else. It cost two free parameters per state and survived
# only because `scales[scales <= 0] = 1.0` below papered over the division it
# would otherwise have caused. A feature that cannot vary is not a feature.

# Seeds taken from the hand-tuned classifier, used only to initialise a learned
# state's response from whichever hand label its members mostly carry.
HAND_MULTIPLIERS: dict[str, dict[str, float]] = {
    TREND: {
        "drift_multiplier": 1.0,
        "momentum_half_life_minutes": 6.0,
        "volatility_multiplier": 1.0,
        "extra_shrinkage": 0.0,
    },
    CONSOLIDATION: {
        "drift_multiplier": 0.35,
        "momentum_half_life_minutes": 2.5,
        "volatility_multiplier": 0.9,
        "extra_shrinkage": 0.05,
    },
    REVERSAL: {
        "drift_multiplier": 0.5,
        "momentum_half_life_minutes": 3.0,
        "volatility_multiplier": 1.1,
        "extra_shrinkage": 0.10,
    },
    VOLATILE_CHOP: {
        "drift_multiplier": 0.25,
        "momentum_half_life_minutes": 2.0,
        "volatility_multiplier": 1.3,
        "extra_shrinkage": 0.20,
    },
    NEUTRAL: {
        "drift_multiplier": 0.7,
        "momentum_half_life_minutes": 3.0,
        "volatility_multiplier": 1.0,
        "extra_shrinkage": 0.05,
    },
}


def load_candle_samples(
    candle_dir: Path, *, asset: str | None = None, window: int = 45, stride: int = 1
) -> tuple[list[list[float]], list[str], list[str], list[str]]:
    """Feature vectors reconstructed from historical one-minute candles.

    The same `extract_features` and `classify` the bot runs, over a sliding
    window of the same length, so a row here is indistinguishable from a row the
    live bot would have produced at that minute. That is the whole point: the
    decision log is only a record of the minutes the bot happened to be running,
    and there is no reason for the training set to inherit that limitation.

    Minutes with no trades are forward-filled — open=high=low=close at the last
    price, zero volume — because a thin pair genuinely has no bar there and the
    window needs to be contiguous. The alternative, skipping them, silently
    splices together candles that were an hour apart.
    """
    from core.strategy_brain.strategies.candles import PriceCandle
    from core.strategy_brain.strategies.market_regime import classify, extract_features

    sys.path.insert(0, str(PROJECT_ROOT / "research"))
    from backfill_candles import read_candles

    assets = [asset] if asset else sorted(
        path.name.split("_")[0] for path in Path(candle_dir).glob("*_1m.jsonl")
    )
    vectors: list[list[float]] = []
    labels: list[str] = []
    stamps: list[str] = []
    rows_asset: list[str] = []

    for name in assets:
        raw = read_candles(Path(candle_dir) / f"{name}_1m.jsonl")
        if len(raw) < window:
            continue
        by_minute = {candle.minute: candle for candle in raw}
        first, last = raw[0].minute, raw[-1].minute
        filled: list[PriceCandle] = []
        previous = raw[0]
        for minute in range(first, last + 60, 60):
            candle = by_minute.get(minute)
            if candle is None:
                close = previous.close
                filled.append(PriceCandle(
                    timestamp=datetime.fromtimestamp(minute, timezone.utc),
                    open=close, high=close, low=close, close=close,
                    volume=Decimal("0"),
                ))
            else:
                filled.append(PriceCandle(
                    timestamp=datetime.fromtimestamp(minute, timezone.utc),
                    open=candle.open, high=candle.high, low=candle.low,
                    close=candle.close, volume=candle.volume,
                ))
                previous = candle

        for end in range(window, len(filled) + 1, stride):
            chunk = filled[end - window:end]
            features = extract_features(chunk)
            values = features.as_dict() if hasattr(features, "as_dict") else vars(features)
            vector = []
            usable = True
            for field_name in FEATURE_ORDER:
                value = values.get(field_name)
                if not isinstance(value, (int, float)) or not math.isfinite(value):
                    usable = False
                    break
                vector.append(float(value))
            if not usable:
                continue
            vectors.append(vector)
            labels.append(str(classify(chunk).regime))
            stamps.append(chunk[-1].timestamp.isoformat())
            rows_asset.append(name)
    return (vectors, labels, stamps, rows_asset)


def load_samples(
    path: Path, *, asset: str | None = None
) -> tuple[list[list[float]], list[str], list[str], list[str]]:
    """Read (feature vectors, hand labels, timestamps, assets) from a decision log.

    ``asset`` filters to one underlying, for a per-asset fit.
    """
    vectors: list[list[float]] = []
    labels: list[str] = []
    stamps: list[str] = []
    assets: list[str] = []
    if not path.is_file():
        # Four elements, like every other exit. This returned three, so a
        # missing decision log raised "not enough values to unpack" from the
        # caller's destructuring rather than reporting the missing file - an
        # error about tuple arity for a problem that is a wrong --decisions path.
        return (vectors, labels, stamps, assets)
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("record_type") != "decision":
                continue
            row_asset = str(record.get("asset") or "")
            if asset is not None and row_asset != asset:
                continue
            prediction = record.get("prediction") or {}
            features = prediction.get("regime_features") or {}
            if not features:
                continue
            vector = []
            usable = True
            for name in FEATURE_ORDER:
                value = features.get(name)
                if not isinstance(value, (int, float)) or not math.isfinite(value):
                    usable = False
                    break
                vector.append(float(value))
            if not usable:
                continue
            vectors.append(vector)
            labels.append(str(prediction.get("regime") or NEUTRAL))
            stamps.append(str(record.get("timestamp") or ""))
            assets.append(row_asset)
    return (vectors, labels, stamps, assets)


# One evaluation reads a 45-candle window, so two rows describe genuinely
# different market states only when they are at least that far apart. Anything
# closer is a re-reading of mostly the same candles.
WINDOW_MINUTES = 45


def effective_samples(stamps: Sequence[str], *, window_minutes: int = WINDOW_MINUTES) -> int:
    """How many of these rows are independent of each other.

    A greedy walk that keeps a row only once the window has fully turned over.
    This is a generous upper bound — it treats a 45-minute gap as full
    independence when the underlying price series is still autocorrelated — and
    it is still two orders of magnitude below the row count.

    It exists because `--min-samples` used to count rows: at 5,090 rows per
    asset the guard reported "plenty" while the independent count was 71, which
    is fewer data points than a three-state fit has parameters. A safety check
    that passes precisely when the fit is worthless is worse than none.
    """
    moments = []
    for stamp in stamps:
        if not stamp:
            continue
        try:
            moments.append(datetime.fromisoformat(stamp))
        except ValueError:
            continue
    if not moments:
        return 0
    moments.sort()
    kept = 0
    last = None
    for moment in moments:
        if last is None or (moment - last).total_seconds() >= window_minutes * 60:
            kept += 1
            last = moment
    return kept


def free_parameters(components: int, dimensions: int) -> int:
    """Free parameters in a diagonal-covariance mixture: means, variances, weights."""
    return components * (2 * dimensions + 1) - 1


def report(vectors: Sequence[Sequence[float]], labels: Sequence[str],
           stamps: Sequence[str]) -> None:
    """State plainly what the data can and cannot support."""
    print(f"samples with a full feature vector : {len(vectors)}")
    if not vectors:
        print("nothing to fit")
        return
    span = [s for s in stamps if s]
    if span:
        print(f"time span                          : {min(span)[:16]} -> {max(span)[:16]}")
        try:
            lo = datetime.fromisoformat(min(span)).astimezone(timezone.utc)
            hi = datetime.fromisoformat(max(span)).astimezone(timezone.utc)
            days = (hi - lo).total_seconds() / 86400
            print(f"calendar days                      : {days:.1f}")
        except ValueError:
            pass
    print(f"hand-label distribution            : {dict(Counter(labels))}")
    print(f"feature dimensionality             : {len(FEATURE_ORDER)}")
    independent = effective_samples(stamps)
    print(f"independent samples (>={WINDOW_MINUTES}m apart)   : {independent}")
    if independent:
        print(f"oversampling factor                : {len(vectors) / independent:.0f}x")
    print()
    print("What that supports, in free parameters per independent observation:")
    print(f"  {'k':>3} {'params':>8} {'per obs':>9}")
    for k in (2, 3, 4, 5, 6, 8):
        params = free_parameters(k, len(FEATURE_ORDER))
        ratio = params / independent if independent else float("inf")
        verdict = "" if ratio < 0.5 else ("  thin" if ratio < 1 else "  MORE UNKNOWNS THAN DATA")
        print(f"  {k:>3} {params:>8} {ratio:>8.1f}x{verdict}")


def walk_forward_splits(
    stamps: Sequence[str],
    folds: int,
    *,
    window_minutes: int = WINDOW_MINUTES,
) -> list[tuple[list[int], list[int]]]:
    """Rolling-origin train/test index pairs, ordered in time and purged.

    Two properties do all the work here, and both exist because of how this
    training set is built rather than as generic good practice.

    **Order is by timestamp, never by row.** ``load_candle_samples`` appends one
    asset at a time, so a pooled nine-asset matrix is asset-blocked, not
    chronological. Splitting that by row index would train on all of BTC and
    test on all of ZEC, which measures cross-asset transfer and calls it
    time-series validation.

    **The boundary is purged in TIME, not in rows.** Consecutive samples share
    44 of their 45 candles, so a naive cut puts near-copies of training rows in
    the test block. Purging a fixed *number* of rows would be wrong for the
    same reason ordering by row is: with nine assets sampled at the same minute,
    45 rows is five minutes of purge. So every training row within
    ``window_minutes`` of the test block's first timestamp is dropped, whichever
    asset it came from.

    Without the purge this reports a flattering held-out score that is really an
    in-sample score wearing a different label - the exact 72x oversampling trap
    the row-counting ``--min-samples`` guard already fell into once.
    """
    if folds < 1:
        return []
    parsed: list[tuple[datetime, int]] = []
    for index, stamp in enumerate(stamps):
        if not stamp:
            continue
        try:
            parsed.append((datetime.fromisoformat(stamp), index))
        except ValueError:
            continue
    if len(parsed) < folds + 1:
        return []
    parsed.sort(key=lambda item: item[0])

    # folds+1 contiguous blocks: block 0 is train-only seed, then each
    # subsequent block is tested against everything before it.
    blocks = folds + 1
    size, remainder = divmod(len(parsed), blocks)
    if size == 0:
        return []
    bounds: list[int] = [0]
    for i in range(blocks):
        bounds.append(bounds[-1] + size + (1 if i < remainder else 0))

    purge = timedelta(minutes=window_minutes)
    splits: list[tuple[list[int], list[int]]] = []
    for fold in range(folds):
        cut = bounds[fold + 1]
        test_slice = parsed[cut:bounds[fold + 2]]
        if not test_slice:
            continue
        test_start = test_slice[0][0]
        train = [
            index for moment, index in parsed[:cut]
            if moment <= test_start - purge
        ]
        test = [index for _, index in test_slice]
        if train and test:
            splits.append((train, test))
    return splits


def walk_forward_scores(
    vectors: Sequence[Sequence[float]],
    labels: Sequence[str],
    stamps: Sequence[str],
    *,
    components: Sequence[int],
    seed: int,
    covariance_floor: float,
    folds: int,
) -> dict[int, dict[str, float]]:
    """Score every candidate k on data it was not fitted on.

    Returns ``{k: {"log_likelihood": .., "variance_retained": .., "folds": ..}}``.

    This replaces selection by BIC, which ``fit`` computes on the very matrix it
    just fitted - an in-sample number that always prefers the more flexible
    model as the sample thins. Two things are measured per fold:

    * **held-out log-likelihood per sample**, which says whether the states
      describe minutes the fit never saw; and
    * **held-out variance retained**, the same shadow statistic the write gate
      uses, but computed on unseen rows. An artifact can reproduce the
      hand-tuned response on its own training data and be inert everywhere
      else, and only this number can tell the difference.

    Standardisation is re-derived **inside each fold from the training rows
    only**. Using the global mean and scale would leak the test block's
    distribution into the transform, which is the quiet way a walk-forward ends
    up measuring nothing.
    """
    import numpy as np
    from sklearn.mixture import GaussianMixture

    splits = walk_forward_splits(stamps, folds)
    if not splits:
        return {}

    matrix = np.asarray(vectors, dtype=float)
    results: dict[int, dict[str, float]] = {}

    print(f"\nwalk-forward: {len(splits)} folds, purged {WINDOW_MINUTES}m at each boundary")
    print(f"{'k':>3} {'folds':>6} {'held-out LL/obs':>17} {'held-out var kept':>19}")
    for k in components:
        lls: list[float] = []
        retentions: list[float] = []
        for train_idx, test_idx in splits:
            train = matrix[train_idx]
            test = matrix[test_idx]
            if k < 1 or k > len(train):
                continue
            means = train.mean(axis=0)
            scales = train.std(axis=0)
            scales[scales <= 0] = 1.0
            try:
                model = GaussianMixture(
                    n_components=k,
                    covariance_type="diag",
                    random_state=seed,
                    reg_covar=covariance_floor,
                    n_init=5,
                    max_iter=500,
                ).fit((train - means) / scales)
            except ValueError:
                continue
            lls.append(float(model.score((test - means) / scales)))

            fold_artifact = {
                "schema_version": SCHEMA_VERSION,
                "feature_names": list(FEATURE_ORDER),
                "feature_means": [float(v) for v in means],
                "feature_scales": [float(v) for v in scales],
                "states": build_states(
                    model,
                    [labels[i] for i in train_idx],
                    model.predict((train - means) / scales),
                    k,
                ),
                "metadata": {},
            }
            # A fold whose test block is entirely one hand label has no
            # response variance to reproduce, so "retained" is undefined there
            # rather than zero. shadow() returns 0.0 for it, and averaging that
            # in would drag the gate value down for a fold that measured
            # nothing - penalising a model for a quiet stretch of market.
            test_seeds = {
                HAND_MULTIPLIERS.get(labels[i], HAND_MULTIPLIERS[NEUTRAL])[
                    "drift_multiplier"
                ]
                for i in test_idx
            }
            if len(test_seeds) > 1:
                retentions.append(
                    shadow(
                        fold_artifact,
                        [vectors[i] for i in test_idx],
                        [labels[i] for i in test_idx],
                        quiet=True,
                    )
                )

        if not lls:
            continue
        results[k] = {
            "log_likelihood": statistics.fmean(lls),
            "variance_retained": statistics.fmean(retentions) if retentions else 0.0,
            "folds": float(len(lls)),
        }
        print(
            f"{k:>3} {len(lls):>6} {results[k]['log_likelihood']:>17.4f} "
            f"{results[k]['variance_retained']:>18.1%}"
        )
    return results


def build_states(
    model: Any,
    labels: Sequence[str],
    assignments: Sequence[int],
    k: int,
    *,
    verbose: bool = False,
) -> list[dict[str, Any]]:
    """Turn a fitted mixture into the artifact's per-state response block.

    Extracted from ``fit`` so a walk-forward fold can build the same artifact a
    real fit would and measure its *out-of-sample* response. Scoring a fold with
    anything other than the shipped construction would be measuring a model the
    bot will never run.
    """
    states: list[dict[str, Any]] = []
    for index in range(k):
        member_labels = [
            labels[i] for i, a in enumerate(assignments) if a == index
        ]
        counts = Counter(member_labels)
        nearest = counts.most_common(1)[0][0] if counts else NEUTRAL
        purity = (counts[nearest] / len(member_labels)) if member_labels else 0.0
        seed_multipliers = HAND_MULTIPLIERS.get(nearest, HAND_MULTIPLIERS[NEUTRAL])

        # Damp toward neutral in proportion to impurity. A state whose members
        # are 55% TREND should not be trusted with TREND's full drift; the
        # response is only as confident as the state is coherent.
        drift = seed_multipliers["drift_multiplier"] * purity + 0.7 * (1 - purity)
        shrinkage = seed_multipliers["extra_shrinkage"] * purity + 0.10 * (1 - purity)

        states.append(
            {
                "name": f"S{index}_{nearest}",
                "weight": float(model.weights_[index]),
                "means": [float(v) for v in model.means_[index]],
                "variances": [float(v) for v in model.covariances_[index]],
                "drift_multiplier": float(max(0.0, drift)),
                "momentum_half_life_minutes": float(
                    seed_multipliers["momentum_half_life_minutes"]
                ),
                "volatility_multiplier": float(
                    seed_multipliers["volatility_multiplier"]
                ),
                "extra_shrinkage": float(min(1.0, max(0.0, shrinkage))),
                "description": (
                    f"{len(member_labels)} members, {purity:.0%} {nearest}; "
                    f"multipliers seeded from {nearest} and damped by impurity"
                ),
            }
        )
        if verbose:
            print(
                f"  S{index}_{nearest:<14} n={len(member_labels):5} "
                f"purity={purity:5.0%} weight={model.weights_[index]:.3f} "
                f"drift x{drift:.2f}"
            )
    return states


def fit(
    vectors: Sequence[Sequence[float]],
    labels: Sequence[str],
    *,
    components: Sequence[int],
    seed: int,
    covariance_floor: float,
    asset: str | None = None,
    independent: int | None = None,
    walk_forward: dict[int, dict[str, float]] | None = None,
) -> dict[str, Any]:
    """Fit diagonal GMMs across k and export the winner.

    ``walk_forward`` selects k by mean **held-out** log-likelihood. Without it
    the winner is chosen by BIC computed on the matrix it was just fitted to,
    which is an in-sample number: it penalises parameters but cannot see
    overfitting to the 44-of-45-candle overlap between neighbouring rows.
    """
    try:
        import numpy as np
        from sklearn.mixture import GaussianMixture
    except ImportError as exc:  # pragma: no cover - dev-only path
        raise SystemExit(
            "scikit-learn and numpy are required to fit.\n"
            "  uv sync --extra research\n"
            f"({exc})"
        ) from exc

    matrix = np.asarray(vectors, dtype=float)
    means = matrix.mean(axis=0)
    scales = matrix.std(axis=0)
    # A constant feature would divide by zero; give it unit scale so it
    # contributes nothing rather than exploding.
    scales[scales <= 0] = 1.0
    standardised = (matrix - means) / scales

    best = None
    fitted_by_k: dict[int, Any] = {}
    print(f"{'k':>3} {'BIC':>14} {'AIC':>14}  converged")
    for k in components:
        if k < 1 or k > len(standardised):
            continue
        model = GaussianMixture(
            n_components=k,
            covariance_type="diag",
            random_state=seed,
            reg_covar=covariance_floor,
            n_init=5,
            max_iter=500,
        ).fit(standardised)
        bic = model.bic(standardised)
        fitted_by_k[k] = (bic, model)
        print(f"{k:>3} {bic:>14.1f} {model.aic(standardised):>14.1f}  {model.converged_}")
        if best is None or bic < best[0]:
            best = (bic, k, model)

    if best is None:
        raise SystemExit("no candidate k could be fitted")

    selection = "BIC"
    if walk_forward:
        # Only consider k values that actually completed folds AND were fitted
        # on the full matrix, so the winner is always a model that exists.
        eligible = {
            k: scores for k, scores in walk_forward.items() if k in fitted_by_k
        }
        # ...and among those, prefer the ones that clear the behavioural floor.
        #
        # Log-likelihood measures how well the states describe the density;
        # variance retained measures whether they produce *different responses*.
        # Those are different questions and they disagree: on 231 days of ETH,
        # k=2/4/5 each retained 25.1% while k=8 fitted the density best and
        # retained 21.8%. Selecting on likelihood alone therefore picked the one
        # candidate the write gate would reject, and discarded three it would
        # have accepted.
        #
        # This is constrained selection, not fitting to the metric: an artifact
        # below the floor cannot ship at all, so it was never a candidate. The
        # floor predates this selector and is measured out of sample. The real
        # cost is a winner's curse - the retention of the k chosen *because* it
        # scored highest is optimistically biased - so a model clearing by a
        # fraction of a point is noise, not evidence.
        shippable = {
            k: s for k, s in eligible.items()
            if s["variance_retained"] >= MIN_VARIANCE_RETAINED
        }
        if shippable:
            eligible = shippable
        if eligible:
            chosen = max(eligible, key=lambda k: eligible[k]["log_likelihood"])
            bic, model = fitted_by_k[chosen]
            k = chosen
            selection = (
                "held-out log-likelihood among k clearing the floor"
                if shippable else "held-out log-likelihood"
            )
            print(
                f"\nselected k={k} by {selection} "
                f"({eligible[k]['log_likelihood']:.4f}/obs over "
                f"{int(eligible[k]['folds'])} folds); in-sample BIC would have "
                f"chosen k={best[1]}"
            )
        else:
            bic, k, model = best
            print(f"\nselected k={k} by BIC ({bic:.1f}); no fold completed")
    else:
        bic, k, model = best
        print(f"\nselected k={k} by BIC ({bic:.1f})")

    assignments = model.predict(standardised)
    states = build_states(model, labels, assignments, k, verbose=True)

    return {
        "schema_version": SCHEMA_VERSION,
        "feature_names": list(FEATURE_ORDER),
        "feature_means": [float(v) for v in means],
        "feature_scales": [float(v) for v in scales],
        "states": states,
        "metadata": {
            "fitted_at": datetime.now(timezone.utc).isoformat(),
            "asset": asset or "ALL",
            "samples": int(len(standardised)),
            "independent_samples": int(independent) if independent is not None else None,
            "free_parameters": int(free_parameters(k, matrix.shape[1])),
            "components": int(k),
            "bic": float(bic),
            "selected_by": selection,
            "walk_forward": (
                {
                    str(key): {
                        "log_likelihood": float(value["log_likelihood"]),
                        "variance_retained": float(value["variance_retained"]),
                        "folds": int(value["folds"]),
                    }
                    for key, value in walk_forward.items()
                }
                if walk_forward
                else None
            ),
            "seed": int(seed),
            "covariance_type": "diag",
            "warning": (
                "States are learned; per-state multipliers are seeded from the "
                "hand-tuned classifier and damped by cluster purity. They are "
                "NOT fitted to trade outcomes - there is not enough settled "
                "data for that. Shadow before trusting. Check "
                "independent_samples against free_parameters before believing "
                "any of it: rows overlap by 44 of 45 candles."
            ),
        },
    }


# Derived, never repeated. This was a hardcoded five-tuple, so adding a series
# to config.py left the fitter silently fitting the old set - and --per-asset
# would report success having skipped the new assets entirely.
ASSETS = tuple(SERIES_BY_ASSET)


def artifact_path(base: Path, asset: str | None) -> Path:
    """`models/regime_mixture.json`, or `..._BTC.json` for one asset."""
    if asset is None:
        return base
    return base.with_name(f"{base.stem}_{asset}{base.suffix}")


# A regime model whose states all give nearly the same answer is not a regime
# model. Below this fraction of the hand-tuned classifier's variance, enabling
# the artifact is equivalent to deleting regime awareness and hardcoding the
# average — while the startup banner reports that a learned model is active.
MIN_VARIANCE_RETAINED = 0.25


def shadow(
    artifact: dict[str, Any],
    vectors: Sequence[Sequence[float]],
    labels: Sequence[str],
    *,
    quiet: bool = False,
) -> float:
    """Compare the fitted response with the hand-tuned one on the fit's own data.

    Returns the fraction of the hand-tuned classifier's drift-multiplier
    variance the mixture reproduces. This runs on every fit because "validated
    against the runtime loader" is true of an artifact that is structurally
    perfect and behaviourally inert, and that combination is exactly what a
    small, unstructured sample produces.
    """
    mixture = RegimeMixture.from_dict(artifact)
    names = list(artifact["feature_names"])
    hand, fitted = [], []
    for vector, label in zip(vectors, labels):
        seed = HAND_MULTIPLIERS.get(label, HAND_MULTIPLIERS[NEUTRAL])
        hand.append(seed["drift_multiplier"])
        fitted.append(mixture.blend(dict(zip(names, vector))).drift_multiplier)

    hand_var = statistics.pvariance(hand) if len(hand) > 1 else 0.0
    fit_var = statistics.pvariance(fitted) if len(fitted) > 1 else 0.0
    retained = (fit_var / hand_var) if hand_var > 0 else 0.0
    if quiet:
        return retained

    print("\nshadow against the hand-tuned classifier (drift multiplier):")
    print(f"  hand-tuned  {min(hand):.2f} .. {max(hand):.2f}   sd {statistics.pstdev(hand):.3f}")
    print(f"  fitted      {min(fitted):.2f} .. {max(fitted):.2f}   sd {statistics.pstdev(fitted):.3f}")
    print(f"  variance retained: {retained:.1%}")
    if retained < MIN_VARIANCE_RETAINED:
        print(
            f"  ^ the states barely differ. Enabling this is close to "
            f"hardcoding drift x{statistics.fmean(fitted):.2f} and deleting the "
            f"regime classifier."
        )
    return retained


def fit_one(
    args, asset: str | None
) -> int:
    """Fit and write a single artifact. Returns a process exit code."""
    label = asset or "ALL ASSETS"
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")

    # Weights already on disk are left alone. Re-fitting an existing artifact on
    # a longer window silently replaces the model a live bot may be scoring
    # against, and it does it *while looking like progress*: the run prints a
    # fit, a shadow and a "wrote" line whether or not anything needed doing.
    #
    # The reason this is the default rather than a flag is the asymmetry. A
    # skipped fit costs a command; an unintended one costs the provenance of the
    # artifact that was there - the window it was fitted on, the k that was
    # selected, and the held-out numbers it cleared the floor with.
    existing = artifact_path(args.out, asset)
    if existing.exists() and not args.refit and not args.report_only:
        try:
            meta = json.loads(existing.read_text()).get("metadata", {})
        except (OSError, ValueError):
            meta = {}
        detail = ", ".join(
            part for part in (
                f"k={meta['components']}" if meta.get("components") else "",
                f"fitted {meta['fitted_at'][:16]}" if meta.get("fitted_at") else "",
                (
                    f"{float(meta['variance_retained']):.0%} variance retained"
                    if meta.get("variance_retained") is not None else ""
                ),
            ) if part
        )
        print(
            f"skipping {label}: {existing} already exists"
            + (f" ({detail})" if detail else "")
            + "\n  pass --refit to replace it."
        )
        return 0
    if args.candles:
        vectors, labels, stamps, _ = load_candle_samples(
            args.candles, asset=asset, stride=args.stride
        )
        print(f"source: rebuilt candles under {args.candles}")
    else:
        vectors, labels, stamps, _ = load_samples(args.decisions, asset=asset)
        print(f"source: decision log {args.decisions}")
    report(vectors, labels, stamps)
    if args.report_only:
        return 0
    if not vectors:
        print(f"no samples for {label}", file=sys.stderr)
        return 1

    independent = effective_samples(stamps)
    if len(vectors) < args.min_rows and not args.force:
        print(
            f"\nrefusing to fit {label}: {len(vectors)} rows is below "
            f"--min-rows {args.min_rows}.",
            file=sys.stderr,
        )
        return 1
    if independent < args.min_samples and not args.force:
        smallest = free_parameters(min(
            int(c) for c in args.components.split(",") if c.strip()
        ), len(FEATURE_ORDER))
        print(
            f"\nrefusing to fit {label}: {independent} independent samples is "
            f"below --min-samples {args.min_samples}.\n"
            f"  {len(vectors)} rows, but consecutive evaluations re-read the "
            f"same {WINDOW_MINUTES}-candle window, so they are not {len(vectors)} "
            f"observations.\n"
            f"  The smallest mixture you asked for already has {smallest} free "
            f"parameters.\n"
            f"  Collect more calendar time — not more rows per minute, which "
            f"changes nothing — or pass --force and treat the result as a toy.",
            file=sys.stderr,
        )
        return 1

    components = [int(c) for c in args.components.split(",") if c.strip()]
    scores: dict[int, dict[str, float]] = {}
    if getattr(args, "walk_forward", 0):
        scores = walk_forward_scores(
            vectors, labels, stamps,
            components=components, seed=args.seed,
            covariance_floor=args.covariance_floor,
            folds=args.walk_forward,
        )
        if not scores:
            print(
                "  (no fold survived purging - the span is shorter than the "
                "folds need; falling back to in-sample BIC)"
            )
    artifact = fit(
        vectors, labels,
        components=components, seed=args.seed,
        covariance_floor=args.covariance_floor,
        asset=asset, independent=independent,
        walk_forward=scores or None,
    )

    # Validate through the runtime loader before writing: an artifact that the
    # bot would reject must never reach disk looking usable.
    RegimeMixture.from_dict(artifact)
    retained = shadow(artifact, vectors, labels)
    artifact["metadata"]["variance_retained"] = float(retained)

    # The gate prefers the HELD-OUT number when there is one. In-sample variance
    # retained is measured on the rows the states were drawn from, so a mixture
    # that has memorised its own training window scores well there and does
    # nothing on the next window. Which is precisely the failure this floor
    # exists to catch, so measuring it in-sample defeats the guard.
    gate_value, gate_source = retained, "in-sample"
    selected_k = artifact["metadata"]["components"]
    if scores.get(selected_k):
        gate_value = scores[selected_k]["variance_retained"]
        gate_source = f"held-out over {int(scores[selected_k]['folds'])} folds"
        artifact["metadata"]["variance_retained_out_of_sample"] = float(gate_value)
        print(f"  held-out variance retained: {gate_value:.1%}")
    if gate_value < MIN_VARIANCE_RETAINED and not args.force:
        print(
            f"\nrefusing to write {label}: the fitted states reproduce only "
            f"{gate_value:.1%} ({gate_source}) of the hand-tuned classifier's "
            f"response variance (floor {MIN_VARIANCE_RETAINED:.0%}).\n"
            f"  Every state landed on the same hand label, so every state "
            f"inherited the same seed multipliers.\n"
            f"  That is what an unstructured sample looks like, not a better "
            f"model. Pass --force to write it anyway.",
            file=sys.stderr,
        )
        return 1

    out = artifact_path(args.out, asset)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {out} ({len(artifact['states'])} states)")
    print("validated against the runtime loader")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fit the regime mixture")
    parser.add_argument("--decisions", type=Path, default=PROJECT_ROOT / "decisions.jsonl")
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "models" / "regime_mixture.json")
    parser.add_argument("--components", default="2,3,4,5,6,7,8",
                        help="comma-separated k values to try (BIC selects)")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--covariance-floor", type=float, default=1e-4,
                        help="reg_covar; keeps a thin cluster from a singular fit")
    parser.add_argument("--min-samples", type=int, default=400,
                        help="refuse below this many INDEPENDENT samples "
                             f"(rows at least {WINDOW_MINUTES} minutes apart)")
    parser.add_argument("--min-rows", type=int, default=2000,
                        help="refuse below this many raw rows")
    parser.add_argument("--asset", choices=ASSETS,
                        help="fit one asset instead of the pooled model")
    parser.add_argument("--per-asset", action="store_true",
                        help="fit a separate artifact for every asset")
    parser.add_argument("--candles", type=Path,
                        help="fit from rebuilt one-minute candles in this "
                             "directory instead of the decision log "
                             "(see research/backfill_candles.py)")
    parser.add_argument("--stride", type=int, default=5,
                        help="minutes between sampled windows. 45 gives fully "
                             "independent rows; smaller oversamples on purpose, "
                             "which helps the mixture without inflating the "
                             "independent count the guard measures")
    parser.add_argument(
        "--walk-forward", type=int, default=5, metavar="N",
        help="rolling-origin folds used to select k on held-out data "
             "(0 disables and falls back to in-sample BIC)",
    )
    parser.add_argument(
        "--refit", action="store_true",
        help="re-fit and overwrite artifacts that already exist on disk "
             "(default: leave them alone)",
    )
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--force", action="store_true",
                        help="fit anyway below the minimums (will overfit)")
    args = parser.parse_args(argv)

    if args.per_asset and args.asset:
        parser.error("--per-asset and --asset are mutually exclusive")

    if args.per_asset:
        codes = [fit_one(args, asset) for asset in ASSETS]
        worst = max(codes)
        if not args.report_only:
            print(f"\n{'=' * 70}")
            print(f"{sum(1 for c in codes if c == 0)} of {len(ASSETS)} assets fitted")
            if worst:
                print("Assets that were refused are listed above with the reason.")
        return worst

    code = fit_one(args, args.asset)
    if code == 0 and not args.report_only:
        print("\nThis artifact is NOT active. Enable it deliberately:")
        print(f"  REGIME_MODEL_PATH={artifact_path(args.out, args.asset)}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
