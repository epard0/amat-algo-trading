# Test fixtures

Every file here is **hand-built** and contains no market data. Values are chosen
to be small, exactly representable, and checkable with a calculator, so a test
failure points at the implementation rather than at an opaque reference file.

No fixture may be regenerated from a live API, and no fixture may be edited to
make a failing implementation pass.

## `valid_daily_bars.csv`

Two synthetic instruments over five consecutive synthetic sessions.

| Property exercised | Where |
|---|---|
| Ordinary bar | every `AAA` row except 2024-01-05 |
| `open == high` boundary | `AAA` 2024-01-04 |
| Zero volume, still a well-formed bar | `AAA` 2024-01-05 |
| Adjusted close differing from raw close | `BBB` 2024-01-02 .. 2024-01-05 |
| Synthetic 2:1 split on 2024-01-08 | `BBB`, where adjusted rejoins raw |

`BBB` is priced so the split is exact: pre-split adjusted closes are exactly
half the raw closes, and on 2024-01-08 the raw price halves and adjusted equals
raw. A split must preserve equity apart from rounding, which makes this file
usable by the corporate-action tests in the data-foundation phase.

Session dates are synthetic and deliberately skip 2024-01-06 and 2024-01-07 (a
weekend) so that calendar handling cannot pass by assuming consecutive calendar
days.

## `invalid_daily_bars.csv`

One row per invariant, with a `violation` column naming the invariant the row
breaks. Each row violates exactly one rule and is otherwise well-formed, so a
table-driven test proves each check fails closed independently.
