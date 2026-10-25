import math

import pytest

from autoresearch.periods import period_reports


def test_quarters_and_blocks_chain_without_dropping_boundary_returns():
    dates = ["2025-03-28", "2025-03-31", "2025-04-01", "2025-06-30", "2025-07-01"]
    values = [100, 90, 99, 110, 88]
    rows = [
        {"session": date, "equity": equity, "return": equity / values[i - 1] - 1 if i else 0}
        for i, (date, equity) in enumerate(zip(dates, values, strict=True))
    ]
    result = period_reports(rows)
    for kind in ("calendar_quarter", "historical_block"):
        slices = [row for row in result if row["kind"] == kind]
        assert sum(row["observations"] for row in slices) == 4
        assert math.prod(1 + row["total_return"] for row in slices) - 1 == pytest.approx(-0.12)
    assert result[0]["max_drawdown"] == pytest.approx(-0.2)
    assert next(row for row in result if row["period"] == "2025-Q2")[
        "total_return"
    ] == pytest.approx(110 / 90 - 1)
    rows[2]["return"] = 0
    with pytest.raises(ValueError, match="disagree"):
        period_reports(rows)
