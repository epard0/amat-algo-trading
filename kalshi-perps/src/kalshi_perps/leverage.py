"""Conservative paper use of published side/size margin estimates."""

from .domain import ZERO, dec


def maintenance_leverage(metadata, side, notional):
    curve = metadata["long" if side > 0 else "short"]
    points = sorted((dec(n), dec(value)) for n, value in curve.items())
    if not points or any(n <= 0 or value <= 0 for n, value in points):
        raise ValueError("positive side/size leverage estimates required")
    # Use the next larger published position bucket; do not extrapolate above it.
    for size, leverage in points:
        if notional <= size:
            return leverage
    raise ValueError("position exceeds published leverage curve coverage")


def margin_required(state, *, initial):
    total = ZERO
    for ticker, p in state["positions"].items():
        count = dec(p["count"])
        if count:
            notional = abs(count) * dec(p["mark"])
            metadata = state["leverage"][ticker]
            requirement = notional / maintenance_leverage(
                metadata, 1 if count > 0 else -1, notional
            )
            if initial:
                requirement *= dec(metadata["initial_margin_multiplier"])
            total += requirement
    return total


def maximum_notional(metadata, side, budget):
    if budget <= 0:
        return ZERO
    curve = metadata["long" if side > 0 else "short"]
    high = max(dec(size) for size in curve)
    low = ZERO
    multiplier = dec(metadata["initial_margin_multiplier"])
    if multiplier < 1:
        raise ValueError("invalid initial margin multiplier")
    for _ in range(80):
        middle = (low + high) / 2
        requirement = middle / maintenance_leverage(metadata, side, middle) * multiplier
        if requirement <= budget:
            low = middle
        else:
            high = middle
    return low


def maximum_weights(signal_weights, allocations, quotes, equity):
    active = {t: dec(allocations[t]) for t, w in signal_weights.items() if dec(w)}
    total = sum(active.values(), ZERO)
    weights, limits = {}, {}
    for ticker, weight in signal_weights.items():
        if not dec(weight) or equity <= 0:
            weights[ticker] = ZERO
            continue
        side = 1 if dec(weight) > 0 else -1
        # Retain 2% for admission costs/spread. Use all remaining active capital;
        # this explicitly overrides original volatility/exposure sizing.
        budget = equity * dec(".98") * active[ticker] / total
        metadata = quotes[ticker]["leverage"]
        notional = maximum_notional(metadata, side, budget)
        weights[ticker] = side * notional / equity
        limits[ticker] = dict(
            maintenance_leverage=str(maintenance_leverage(metadata, side, notional)),
            initial_leverage=str(
                maintenance_leverage(metadata, side, notional)
                / dec(metadata["initial_margin_multiplier"])
            ),
            margin_budget=str(budget),
            target_notional=str(notional),
            source="published side/size estimates",
        )
    return weights, limits
