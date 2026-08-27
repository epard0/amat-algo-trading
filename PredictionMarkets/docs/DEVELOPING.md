<img src="../assets/logo-wordmark.svg" alt="KX15" width="240">

# Working on this codebase

For people editing the bot — adding a signal, changing a gate, fixing a bug,
or trying to understand why something is written the way it is.

If you only want to *run* the bot, [`README.md`](../README.md) is the file you
want. [`ARCHITECTURE.md`](ARCHITECTURE.md) is the complete source and data-flow
map, and [`../CONTRIBUTING.md`](../CONTRIBUTING.md) owns the Git/PR workflow.
`CLAUDE.md`, when included in a local distribution, is the dense internal
invariant and incident-history reference; this document is the development tour.

---

## Getting set up

```bash
uv sync                      # uv.lock is the source of truth; pip also works
cp .env.example .env         # paper defaults, safe to run as-is
uv run pytest -q             # complete mocked regression suite; no live orders
uv run ruff check bot.py config.py core/strategy_brain/strategies execution \
  data_sources/kraken data_sources/yahoo data_sources/pyth \
  data_sources/polymarket data_sources/venues monitoring reporting research tests
```

Python **3.13 or newer**. That is not a preference — the official
`kalshi-python-async` SDK requires it.

Tests mock their network and order boundaries and never place a live order. If a
unit test starts depending on an external service, move that check to an
explicit integration/smoke job and keep the default suite deterministic.

The audited workspace contains local packaging, licensing, and storefront code
that `.gitignore` excludes. A clean public clone cannot run those tests until the
maintainer publishes that subsystem or removes the dependent tests. See
[`ARCHITECTURE.md`](ARCHITECTURE.md#known-architecturerepository-inconsistencies).

### The end-to-end check

```bash
./scripts/e2e.sh              # build, sell, install, configure, run, update
./scripts/e2e.sh --offline    # skip the two steps that need Kalshi and Kraken
./scripts/e2e.sh --keep       # leave it all running to poke at
```

Two minutes, and it walks the entire customer journey in a throwaway directory:
packages the release, starts the storefront on a free port, "buys" a licence
with a correctly-signed test webhook, installs through the real `install.sh`,
configures it, runs a live market scan, serves the dashboard, and updates.

It exists because this chain fails in ways the unit tests cannot see. Every
assertion in it corresponds to something that was actually broken: `kx15 update`
deleting the ledger, the launcher reporting paths it did not use, the shipped
test suite failing to collect, the licence key travelling in a URL. None of
those involve a function returning the wrong value — they are about how the
pieces meet.

Your own `~/.config/kx15` and real ledger are never touched; it uses a temp
`KX15_CONFIG` throughout, and no Stripe account or money is involved.

### Testing a deployed storefront

```bash
export DEV_LICENSE_SECRET=...            # the same value the server holds
python -m storefront.developer --hours 2 # mint a token
./scripts/e2e.sh --remote https://yourdomain
```

A developer token is a signed, expiring credential that authenticates like a
licence key without a purchase behind it. `--remote` skips the packaging and
webhook steps — nothing is forged against production — and runs install through
update using the token.

Three things about it are load-bearing. It is **HMAC-verified, not secret**:
nothing in `storefront/developer.py` is confidential, and with
`DEV_LICENSE_SECRET` unset the path is dead. It **expires**, so a token in a
shell history or a CI log stops working on its own. And it is **exempt from
sharing detection** — it is meant to be installed repeatedly from throwaway
machines by one person, and scored as a licence it blocks itself on the third
run, which is exactly what it did the first time this was wired up.

### Running it while you work

```bash
python bot.py --once                        # one public market scan, no waiting
python bot.py --session-seconds 60 --session-passes 8   # short paper benchmark
python -m monitoring.server                 # dashboard on :8787
python view_decisions.py --tail 5           # why the last few evaluations went as they did
```

`--once` is the fastest honest feedback loop: it hits real Kalshi and Kraken
endpoints read-only, prices every live market, and writes a decision record for
each. Most model changes show up there within seconds.

---

## How the code is arranged

```
bot.py                       the run loop, the gates in order, the order path
config.py                    every environment variable, validated at startup
core/strategy_brain/         the model: predictor, regime classifier, mixture
execution/                   Kalshi client, risk gates, ledger, decision log
data_sources/kraken/         active crypto reference candles
data_sources/{yahoo,pyth}/   active commodity history/recent-price route
data_sources/polymarket/     standalone read-only research collector
monitoring/                  read-only dashboard, metrics, chart
licensing/                   activation client (never imported by bot.py)
storefront/                  the sales site, licensing, fraud scoring
research/                    offline fitting; not imported at runtime
tests/                       mirrors the above, one file per subject
assets/                      the logo
```

Three groups behave differently and it is worth knowing which you are in:

| Group | Rule |
| --- | --- |
| **Pure** — `risk_gate.py`, `crypto_15min_predictor.py`, `market_regime.py`, `regime_model.py`, `performance_tracker.py`, `fraud.py` | No clock, no network, no I/O. Everything arrives as an argument. Keep it that way; it is why these are cheap to test. |
| **Stateful** — `trade_ledger.py`, `decision_log.py`, `mode_control.py` | Own a file. One writer, defined failure behaviour. |
| **Orchestration** — `bot.py`, `kalshi_client.py`, `monitoring/server.py`, `storefront/app.py` | Where I/O and error handling live. |

New logic belongs in the pure group by default. If a function needs the current
time, take it as a parameter with a `now: datetime | None = None` default —
that is the pattern used throughout and it is what makes the time-dependent
tests possible.

### The dead tree

`core/ingestion/`, `core/nautilus_core/`, `core/strategy_brain/signal_processors/`,
`core/strategy_brain/fusion_engine/`, `btc_15min_strategy.py`,
`data_sources/{binance,coinbase,solana,news_social}/`, `feedback/`, and
`execution/risk_engine.py` are left over from an earlier Polymarket +
NautilusTrader design. Nothing in `bot.py`'s import graph touches them, their
old in-tree smoke scripts are outside configured `testpaths`, and `ruff` still
reports pre-existing warnings there.

Do not extend them. If you need something they contain, move the piece you need
into the live tree with a test.

---

## The rules that are not style preferences

These come from things that went wrong. Each is cheap to respect and expensive
to rediscover.

**Money is `Decimal`.** Every price, size, fee, balance and P&L. Floats appear
only inside the predictor's mathematics and in log formatting. Sizing rounds
`ROUND_DOWN`, fees round `ROUND_CEILING`. A float that reaches the ledger is a
bug even when the number looks right.

**Gates are pure and run before orders.** `execution/risk_gate.py` takes values
and returns a `GateResult`. No clock, no network, no ledger. That is what lets
every guard be tested in three lines, and it is why the gate order in `bot.py`
is readable at all.

**Every evaluation writes a decision record.** Each exit path in
`_evaluate_market` / `_place_candidate` must call `trace.finish(...)`, including
the boring ones. A missing `finish()` is a silently lost record, and the
decision log is the only way to answer "why didn't it trade?" afterwards.

**A venue error must not abort the cycle.** Order rejections that mean "the book
moved" return a zero-fill `OrderResult` and put the ticker in a cooldown. A
`409 fill_or_kill_insufficient_resting_volume` used to crash the loop and retry
every five seconds.

**Size against the live book.** The discovery snapshot is seconds stale and its
top-of-book size cannot be trusted. `_place_candidate` re-reads the book,
re-checks the edge, and clips to `depth_at_limit`.

**One YES book.** `side="bid"` buys YES, `side="ask"` sells YES, and buying NO
is `1 - price` on the YES book. Do not introduce a second book abstraction.

**Regime multipliers never flip a sign.** They decide how far measured drift is
trusted, never its direction. A classifier that can reverse the model is a
second, untested strategy hiding inside the first — `RegimeMixture.validate`
enforces this at load time and should stay enforced.

**`bot.py` imports nothing from `monitoring/` or `licensing/`.** The dashboard
and the licence check are observers. A bug in either must not be able to reach
an open position. There is a test asserting the `licensing` half of this.

---

## Making specific changes

### Adding or changing a safety gate

1. Write the pure function in `execution/risk_gate.py`. It takes values,
   returns `GateResult(passed, reason)`, and knows nothing about the bot.
2. Test it in `tests/test_risk_gate.py`, including the boundary. Gates are
   mostly interesting exactly at their threshold.
3. Call it from `_evaluate_market` or `_place_candidate` through
   `trace.gate(...)` so the result reaches the decision log.
4. Add the threshold to `config.py` **and** `.env.example` with the same
   default. Three settings once diverged between the dataclass default and
   `from_env`, and the operator's tightened `MAX_CONSECUTIVE_LOSSES` silently
   had no effect in production. Add coverage to the relevant configuration or
   environment-loading tests; there is currently no `tests/test_config.py`.

### Changing the model

`crypto_15min_predictor.py` is a pure function of candles and a strike. Change
it, then:

```bash
uv run pytest tests/test_predictor.py -q
python bot.py --once                    # does it still price live markets sanely?
python view_decisions.py --calibration  # predicted probability vs settlement
```

The calibration view is the one that matters. A model that is better at
predicting is one whose 70% bucket wins about 70% of the time; a model with a
higher win rate and worse calibration has usually just started betting on
favourites.

### Adding an asset

Add it to the relevant canonical maps in `config.py` and to the documented
`TRADING_ASSETS` default in `.env.example`. Add a Kraken route for crypto or the
Yahoo/Pyth commodity mappings for a commodity; add model/training data only when
the source and validation policy support it. Check the Kalshi series ticker
exists first — `python bot.py --once --assets NEW` reports empty discovery rather
than inventing a market.

### Touching the dashboard

`monitoring/` opens the ledger read-only and must keep doing so. With control
enabled, the one thing it writes is `runtime_mode.json`, and it writes a
*request* there that the bot is free to refuse. A container must therefore run
with `--no-control` on a read-only state mount or receive a separate narrow
writable control mount. Chart indicators call `market_regime.classify` directly
over a rolling window — do not reimplement the maths for display, or the chart
and the trader will disagree and the chart will be the one believed. Metrics and
dashboard work must follow
[`OBSERVABILITY_DATA_PIPELINE_PLAN.md`](OBSERVABILITY_DATA_PIPELINE_PLAN.md).

### Touching the storefront front-end

The CSP is `default-src 'self'; style-src 'self'; script-src 'self'`. Three
consequences, all of which have already cost something here:

- An inline `<script>` is dropped silently. The success page's licence-key
  logic was inline and therefore never displayed a key to a paying customer.
- An inline style is dropped too, **including one JavaScript writes** — Chrome
  treats `element.style = ...` and `style="..."` in injected markup as inline
  style. Use a class and put the rule in `theme.css`.
- No CDN. Bulma is vendored as `web/bulma.min.css`; the background is a
  gradient stack and a canvas. nginx gzips it — 662KB becomes 64KB.

`tests/test_storefront_app.py` fails the build if a script reintroduces either.

---

## Tests

`pytest` with `asyncio_mode = "auto"`, so `async def test_*` needs no decorator.
Async fakes must be real coroutine functions (`async def`), not lambdas, because
the bot awaits them.

What the suite is for, in rough order of value:

1. **Invariants that cost money if broken** — sizing arithmetic, fee rounding,
   the gate thresholds, "one trade at a time", the paper/live switch refusing
   live without the prerequisites.
2. **Failure paths** — a moving book, a corrupt artifact, an unreachable
   server, a malformed control file. Most of these tests exist because the
   failure happened.
3. **Contracts between layers** — what the API returns, what the ledger stores.

Some conventions worth matching:

- One assertion subject per test, and a name that states the claim rather than
  the mechanism: `test_a_machine_that_worked_yesterday_is_never_cut_off`, not
  `test_assess_returns_review`.
- When a test encodes something non-obvious, say why in the docstring. A
  future reader needs to know whether a failing test found a bug or is simply
  out of date.
- Fakes over mocks. `tests/test_placement.py` builds a bot with
  `object.__new__` and assigns the handful of attributes the path under test
  needs; that is usually less work than patching and it fails loudly when the
  real object changes shape.

Run one file while iterating; run everything before you stop:

```bash
uv run pytest tests/test_risk_gate.py -q
uv run pytest tests/test_predictor.py::test_name -q
uv run pytest -q
```

---

## Style

Match the file you are in. Beyond that:

- Comments explain **why**, and especially why the obvious alternative is
  wrong. There are a lot of comments in this codebase that read like arguments,
  because most of them are: someone will otherwise "simplify" the code back
  into the bug it was written to avoid.
- Follow the repository's Ruff behavior and the surrounding file. Lint the
  active paths with the setup command above before review. The full-workspace
  command currently includes known legacy and generated-notebook findings.
- Type annotations on anything crossing a module boundary.
- No new runtime dependencies without a good reason. An undeclared `urllib3`
  import from the SDK and a dead `import systemd.daemon` have each broken this
  repo once. The Prometheus exposition and the canvas charts are hand-rolled
  deliberately.

---

## Releasing

The release/storefront paths below are local and currently ignored by Git. They
are usable only in a distribution that deliberately includes that subsystem.

```bash
uv run python -m storefront.release        # allow-list tarball + credential scan
```

`storefront/release.py` packages from an explicit allow-list, then scans the
result for private keys, Stripe secrets and API keys and **refuses to write the
archive** if it finds any. If you add a top-level directory that customers
need, add it to `INCLUDE`; if you add a test fixture that looks like a
credential, assemble the literal at runtime so the scanner does not trip on its
own test data.

---

## Where to look when something is wrong

| Symptom | Look at |
| --- | --- |
| It is not trading | `python view_decisions.py --tail 20` — every rejection is recorded with its gate |
| It traded and you disagree | The same record: it carries the full feature vector and every multiplier |
| Model looks over-confident | `python view_decisions.py --calibration` |
| Dashboard disagrees with the ledger | The dashboard caches for 3s; then check you are on the same book (paper vs live) |
| Storefront page looks unstyled | Browser console — almost always a CSP refusal |
| Projection numbers look wrong | `storefront/model_stats.py` reads the ledger; check `basis` in `/api/performance` is `model-output` and not `flat-win-rate` |
| A test fails only sometimes | Concurrency. Two have been found here, both in `LicenseStore`; see `docs/LICENSING.md` |
