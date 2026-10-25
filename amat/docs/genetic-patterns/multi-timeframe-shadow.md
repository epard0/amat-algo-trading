# P6: completed-bar campaigns and multi-timeframe spotting

P6 adds manual, report-only shadow campaigns and five explicit pattern views:
15 minutes (`15m`), hourly (`1h`), daily (`1d`), weekly (`1w`), and monthly (`1mo`).
The daily strategy adapter remains daily. Other views display the same frozen
expression evaluated on a different bar grid, with a distinct pattern hash and
calibration. They do not inherit daily validation, select new expressions, or
modify portfolio weights. Existing rejected patterns remain rejected.

## Timeframe contract

| View | Source | Alignment and completion | Calibration |
|---|---|---|---|
| 15m | All-adjusted SIP minute OHLCV | XNYS open; all 15 constituent minutes; interval closed | Separately frozen minute history |
| 1h | All-adjusted SIP minute OHLCV | XNYS open; all 60 minutes; final 30-minute tail omitted | Separately frozen minute history |
| 1d | Verified adjusted daily panel | Completed exchange session | Original artifact calibration |
| 1w | Daily OHLCV | First through last XNYS session in the week; incomplete edge weeks omitted | Complete weeks within original calibration |
| 1mo | Daily OHLCV | First through last XNYS session in the month; incomplete edge months omitted | Complete months within original calibration |

The schema now accepts `15m`, `1h`, `1d`, `1w`, and `1mo` explicitly (legacy `5m`
remains a schema value, without a new evaluator or ingestion implementation).
A 21-bar lookback means 21 bars of the selected timeframe. Recursive indicators
continue across regular-session bars; overnight time does not create fictitious
bars. Numeric feature normalization remains causal and per timeframe. The fixed
252 multiplier inside the legacy volatility terminal is a formula convention;
coarse/intraday views do not present it as annualized risk. Reported pattern
values are normalized expression outputs, not returns or buy/sell instructions.

Minute bars are actually downloaded, not synthesized from daily candles. Missing
minutes invalidate the whole aggregate for that asset, retaining its calendar
row. The exchange calendar accounts for holidays, early closes and daylight
saving. Raw prices and opening-window execution features are unavailable in
adjusted-only intraday data; weekly/monthly execution-window features are also
unavailable. A stale source is labelled `stale_source`, with expected and actual
latest interval timestamps. Insufficient history/calibration remains explicit.

## Commands

Run from the `amat` directory. Use a new output path for each immutable artifact.
First freeze a P4 shortlist member using `shadow-freeze` as documented in
[shadow-inference.md](shadow-inference.md). Source changes require a new artifact;
prior phase artifacts and evidence remain immutable. P6 changes the registry source
hash; `shadow-freeze --allow-registry-rebind` explicitly permits diagnostic reuse
of the old expression. It retains the historical candidate hash and clears
historical approval/selection transfer. The P6 evidence includes exact daily
output parity against the archived P5 runtime across the full snapshot.

```sh
uv run --locked autoresearch patterns shadow-fetch-intraday \
  --symbols SPY QQQ IWM EFA EEM IEF GLD SLV DBC USO UUP \
  --start 2026-08-17 --end 2026-09-28 \
  --output data/research/my-shadow/minutes.json

uv run --locked autoresearch patterns shadow-spot-intraday \
  --artifact outputs/my-shadow/artifact.json \
  --intraday-data data/research/my-shadow/minutes.json \
  --intraday-calibration-end 2026-09-11 \
  --as-of 2026-09-28T20:00:00+00:00 \
  --output outputs/my-shadow/intraday-views.json

uv run --locked autoresearch patterns shadow-register \
  --artifact outputs/my-shadow/artifact.json \
  --snapshot data/snapshots/alpaca-sip-core-2026-09-14-verified \
  --intraday-data data/research/my-shadow/minutes.json \
  --intraday-calibration-end 2026-09-11 \
  --output outputs/my-shadow/campaign.json

uv run --locked autoresearch patterns shadow-poll \
  --campaign outputs/my-shadow/campaign.json --through 2026-09-28 \
  --intraday-data data/research/my-shadow/minutes.json \
  --ledger outputs/my-shadow/ledger.json \
  --output outputs/my-shadow/2026-09-28.json
```

The minute downloader uses the existing read-only market-data credentials and
currently requests completed exchange days. Aggregation supports intraday
cutoffs within that data. Campaign polling runs once per explicit completed
daily session; this phase does not start an intraday scheduler or broker loop.
The standalone spotting command is historical diagnostic evidence, even when
its most recent bars are recent. Its calibration cutoff is not a registration.

## Campaign evidence and failure behavior

Registration binds actual UTC time, artifact/runtime, source snapshot and full
baseline panel, ordered universe, five-timeframe policy, and optional intraday
calibration. A session is prospective only when its exchange open is strictly
later than registration. Registration during a session cannot turn that day's
already-observed data into prospective evidence.

The daily feed refetches raw and adjusted history, verifies every frozen daily
OHLCV value, and requires complete symbol/session coverage before appending.
An adjustment or vendor revision fails closed. It must be reconciled through a
new reviewed data/calibration artifact; it is not silently rebased. This strict
policy can prevent extending older snapshots after distributions or revisions.
Previously observed daily and intraday history also cannot silently change.

Each successful poll writes a durable, checksummed receipt of its complete
inputs before appending the hash-chained observation ledger. Retries reuse the
original observation and do not refetch. Publication can recover after a receipt
or ledger write. Suspension stays latched and daily reported contributions stay
neutral. Missing minute data is explicitly unavailable, never a synthetic view.
All artifacts retain `diagnostic_only: true`, `execution_authority: none`, and
`promotion_authority: none`. Local checksums detect changes; they are not remote
attestations or cryptographic authentication of the provider.

## Coverage investigation

P8 adds `shadow-audit-intraday` to explain source gaps and continuous calibration
coverage at 15m and 1h. See [p8-verification.md](p8-verification.md) for commands,
the June–September history extension, and remaining per-asset limits.

## P10 offline portfolio research

The separately selected native-15m research contract now composes session-open
hourly bars from four observed components and aligns all five completed views to
the offline bot decision close. See [parameter-search.md](parameter-search.md)
for the portfolio blend, global/local search, exit semantics and scope. This
research path does not replace the strict minute contract in an existing shadow
campaign or clear the suspended P7 state.
