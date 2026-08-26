#!/usr/bin/env bash
#
# Comprehensive strategy evidence sweep.
#
# Runs every measurement this repository can produce against every strategy
# version, on ONE consistent basis, and writes a self-describing campaign
# directory that names what ran, what it cost, and what failed.
#
# ---------------------------------------------------------------------------
# Consistency, and what "avoid hindsight bias" means here
# ---------------------------------------------------------------------------
#
# Held fixed across every command in the run:
#
#   * one snapshot (immutable, content-addressed, recorded in the manifest);
#   * one opening cash figure;
#   * one END session -- the snapshot's own last session, never a chosen date;
#   * configured costs on both sides of every fill (no zero-cost row is
#     produced except inside `backtest-synthetic --cost-ladder`, which is a
#     labelled engine diagnostic on synthetic data, not a market result);
#   * the experiment split date 2022-12-30, frozen since experiment 0001 and
#     not a parameter of this script.
#
# Window starts are DERIVED from the end session by subtracting calendar
# months. No start date is searched over, no version gets its own window, and
# no configuration is selected from what the windows show -- the versions are
# the ones already registered and frozen before this script existed.
#
# What this does NOT escape, stated once here and again in every artifact:
# the whole sample is burned history that the frozen configuration was
# developed on. A trailing window is a restatement of that sample, not
# out-of-sample evidence, and nothing this script prints is a profitability
# claim or a forecast.
#
# ---------------------------------------------------------------------------
# The two horizon tables, and why they differ
# ---------------------------------------------------------------------------
#
# Phase 1 (`scripts/horizon_sweep.py`) runs each version ONCE over the full
# history and slices that single equity curve into the 6m/1y/2y/5y/10y
# windows. Warm-up and open positions carry into every window. THIS IS THE
# COMPARABLE TABLE.
#
# Phase 2 runs one windowed `amat backtest` per version per horizon to produce
# a standalone PDF per window. Those runs necessarily re-warm inside their own
# window: the engine refuses to trade until it has
# `warmup.min_completed_sessions` completed sessions, so each is given a
# 14-calendar-month data pad before the horizon it reports. Their headline
# metrics therefore cover pad + horizon and are NOT comparable to phase 1 or
# to each other. They are documents, not the table.
#
# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------
#
# Backtest-only. Nothing here submits an order to any venue, paper or live;
# `amat simulate` journals a paper account from snapshot data and is invoked
# with --offline so it performs no network fetch at all. The holdout boundary
# is enforced by every command that loads a panel and is never authorized past
# from this script -- `--allow-holdout` is deliberately absent, so a snapshot
# that crossed the boundary would fail the run closed rather than quietly
# widen the sample.
#
# Usage:
#   bash scripts/comprehensive_backtest.sh                 # everything
#   bash scripts/comprehensive_backtest.sh --tier fast     # skip the slow phases
#   bash scripts/comprehensive_backtest.sh --versions "v1 v6"
#   bash scripts/comprehensive_backtest.sh --initial-cash 250000
#   bash scripts/comprehensive_backtest.sh --dry-run       # print the plan only
#
set -Eeuo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

ALL_VERSIONS="v1 v3 v4 v5 v6"
HORIZONS="6m 1y 2y 5y 10y"

# Calendar months of data handed to a windowed run BEFORE the horizon it
# reports, so the engine can complete its 252-session warm-up inside the run.
WARMUP_PAD_MONTHS=14

# Frozen since experiment 0001. Ranking happens on data at or before this
# date; the validation window after it is measured ranking-blind. It is a
# constant, not a knob -- moving it would invalidate every registered result.
TRAIN_END="2022-12-30"

# Seeds are fixed so the whole campaign is reproducible from this file.
CONTROL_SEED=20260815
MONTECARLO_PATHS=200
PROJECTION_PATHS=2000
HEAVYTAIL_SCENARIOS=24
HEAVYTAIL_ALPHA=2.5
PROJECTION_HORIZONS="0.5 1 2 5 10"

# v3 IS its scalar series -- its TOML differs from v1's only in name and
# version string. Without this file every v3 row would be v1 wearing a v3
# label, so the default points at the checked-in export and the script says so
# out loud when it is missing.
SCALARS_DEFAULT="outputs/scalars/news_scalars_2016-2026.csv"

VERSIONS="$ALL_VERSIONS"
SCALARS=""
SNAPSHOT=""
INITIAL_CASH="100000"
OUT_ROOT="outputs/campaigns"
OUT_DIR=""
TIER="full"
DRY_RUN=0
WORKERS="$(( $(nproc 2>/dev/null || echo 4) > 8 ? 8 : $(nproc 2>/dev/null || echo 4) ))"

# ---------------------------------------------------------------------------
# Argument parsing, with validation. Every value that later reaches a command
# line is checked against an allowlist or a pattern first: this script
# interpolates paths, dates, symbols and numbers into commands, and an
# unvalidated one is a shell injection.
# ---------------------------------------------------------------------------

die() { printf 'comprehensive_backtest: %s\n' "$*" >&2; exit 2; }

usage() {
  sed -n '2,70p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --versions)      VERSIONS="${2:-}"; shift 2 ;;
    --snapshot)      SNAPSHOT="${2:-}"; shift 2 ;;
    --scalars)       SCALARS="${2:-}"; shift 2 ;;
    --initial-cash)  INITIAL_CASH="${2:-}"; shift 2 ;;
    --out-dir)       OUT_DIR="${2:-}"; shift 2 ;;
    --tier)          TIER="${2:-}"; shift 2 ;;
    --workers)       WORKERS="${2:-}"; shift 2 ;;
    --dry-run)       DRY_RUN=1; shift ;;
    -h|--help)       usage ;;
    *)               die "unknown option: $1 (try --help)" ;;
  esac
done

[[ "$TIER" == "fast" || "$TIER" == "full" ]] || die "--tier must be fast or full"
[[ "$INITIAL_CASH" =~ ^[0-9]+(\.[0-9]+)?$ ]] || die "--initial-cash must be a positive number"
[[ "$WORKERS" =~ ^[0-9]+$ ]] && (( WORKERS >= 1 )) || die "--workers must be a positive integer"

for version in $VERSIONS; do
  case "$version" in
    v1|v3|v4|v5|v6) ;;
    v2) die "v2 was rejected 2026-08-10 and its runner removed; docs/experiment-registry.md is the record" ;;
    *)  die "unknown strategy version: $version" ;;
  esac
done

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
[[ -f pyproject.toml && -d src/amat ]] || die "must run from the amat repository root"

# This sandbox has a read-only ~/.cache; without these two the first uv call
# fails on a lock it cannot take and matplotlib fails on its font cache.
export UV_CACHE_DIR="${UV_CACHE_DIR:-${TMPDIR:-/tmp}/uv-cache}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-${TMPDIR:-/tmp}/mpl}"
mkdir -p "$UV_CACHE_DIR" "$MPLCONFIGDIR"

# Backtest-only, asserted rather than assumed.
export AMAT_LIVE_TRADING_ENABLED=0

command -v uv >/dev/null 2>&1 || die "uv is not on PATH"

if [[ -z "$SNAPSHOT" ]]; then
  SNAPSHOT="$(find data/snapshots -mindepth 1 -maxdepth 1 -type d | sort | tail -1)"
fi
[[ -n "$SNAPSHOT" ]] || die "no snapshot directories under data/snapshots/"
[[ -d "$SNAPSHOT" ]] || die "snapshot is not a directory: $SNAPSHOT"
[[ "$SNAPSHOT" != *".."* ]] || die "snapshot path may not contain '..'"
SNAPSHOT_ABS="$(cd "$SNAPSHOT" && pwd)"
case "$SNAPSHOT_ABS" in
  "$REPO_ROOT"/data/snapshots/*) ;;
  *) die "snapshot must resolve under $REPO_ROOT/data/snapshots (got $SNAPSHOT_ABS)" ;;
esac

if [[ -z "$SCALARS" && -f "$SCALARS_DEFAULT" ]]; then
  SCALARS="$SCALARS_DEFAULT"
fi
if [[ -n "$SCALARS" ]]; then
  [[ "$SCALARS" != *".."* ]] || die "--scalars may not contain '..'"
  [[ -f "$SCALARS" ]] || die "no scalars file at $SCALARS"
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
if [[ -z "$OUT_DIR" ]]; then
  OUT_DIR="$OUT_ROOT/$STAMP"
fi
[[ "$OUT_DIR" != *".."* ]] || die "--out-dir may not contain '..'"

LOG_DIR="$OUT_DIR/logs"
PDF_DIR="outputs/reports"
mkdir -p "$LOG_DIR" "$PDF_DIR"

STEPS_TSV="$OUT_DIR/steps.tsv"
printf 'phase\tstep\tstatus\texit\tseconds\tlog\n' > "$STEPS_TSV"

# ---------------------------------------------------------------------------
# Step runner: never aborts the campaign, always records the outcome.
# A failed step is evidence too, and hiding it behind `set -e` would turn a
# partial campaign into one that merely looks complete.
# ---------------------------------------------------------------------------

PHASE=""
STEP_OK=0
STEP_FAIL=0
STEP_SKIP=0
FAILED_STEPS=()

phase() {
  PHASE="$1"
  printf '\n\033[1m=== %s ===\033[0m\n' "$1"
}

skip_step() {
  local name="$1" reason="$2"
  STEP_SKIP=$(( STEP_SKIP + 1 ))
  printf '  \033[33mSKIP\033[0m  %-44s %s\n' "$name" "$reason"
  printf '%s\t%s\tskipped\t-\t0\t%s\n' "$PHASE" "$name" "$reason" >> "$STEPS_TSV"
}

run_step() {
  local name="$1"; shift
  local log="$LOG_DIR/${name}.log"
  local started ended elapsed rc

  if (( DRY_RUN )); then
    printf '  \033[36mPLAN\033[0m  %-44s %s\n' "$name" "$*"
    printf '%s\t%s\tplanned\t-\t0\t%s\n' "$PHASE" "$name" "$log" >> "$STEPS_TSV"
    return 0
  fi

  printf '  ....  %-44s' "$name"
  started="$(date +%s)"
  {
    printf '### %s\n### %s\n### cwd=%s\n\n' "$name" "$*" "$PWD"
  } > "$log"
  set +e
  "$@" >> "$log" 2>&1
  rc=$?
  set -e
  ended="$(date +%s)"
  elapsed=$(( ended - started ))

  if (( rc == 0 )); then
    STEP_OK=$(( STEP_OK + 1 ))
    printf '\r  \033[32mOK\033[0m    %-44s %4ds  %s\n' "$name" "$elapsed" "$log"
    printf '%s\t%s\tok\t0\t%s\t%s\n' "$PHASE" "$name" "$elapsed" "$log" >> "$STEPS_TSV"
  else
    STEP_FAIL=$(( STEP_FAIL + 1 ))
    FAILED_STEPS+=("$name (exit $rc) -> $log")
    printf '\r  \033[31mFAIL\033[0m  %-44s %4ds  exit %d  %s\n' "$name" "$elapsed" "$rc" "$log"
    printf '%s\t%s\tfail\t%s\t%s\t%s\n' "$PHASE" "$name" "$rc" "$elapsed" "$log" >> "$STEPS_TSV"
  fi
  return 0
}

amat() { uv run amat "$@"; }
pyrun() { uv run python "$@"; }

# ---------------------------------------------------------------------------
# Version -> configuration, and which versions the measuring commands accept.
#
# The measuring commands (stress, walkforward, montecarlo, project) take a
# plain strategy configuration or the v6 superset. v4's file is a SWITCHING
# configuration with [trend]/[defensive]/[switching] tables that the plain
# loader rejects by name, so v4 is measurable by `amat backtest`/`study-v4`
# only. That is a real coverage gap, recorded rather than papered over.
# ---------------------------------------------------------------------------

config_for() {
  case "$1" in
    v1) printf 'configs/strategy/amat_v1.toml' ;;
    v3) printf 'configs/strategy/amat_v3.toml' ;;
    v4) printf 'configs/strategy/amat_v4.toml' ;;
    v5) printf 'configs/strategy/amat_v5.toml' ;;
    v6) printf 'configs/strategy/amat_v6.toml' ;;
    *)  die "no configuration for version $1" ;;
  esac
}

# Empty output means "this version is measurable"; anything else is the reason
# it is not, printed beside the skipped step so a gap is never a silent one.
validation_skip_reason() {
  case "$1" in
    v4)
      printf 'switching config carries [trend]/[defensive]/[switching] tables the plain loader rejects'
      ;;
    v3)
      # Running these against amat_v3.toml would succeed and produce numbers --
      # which is exactly the danger. None of the four commands takes a scalars
      # series, so the run would be the identical v1 core printed under a v3
      # heading. Duplicating v1's battery is not more evidence; it is a second
      # copy of the same evidence with a misleading label.
      printf 'no --scalars flag on these commands; the run would be the identical v1 core mislabelled as v3'
      ;;
    *) printf '' ;;
  esac
}

horizon_months() {
  case "$1" in
    6m) printf '6' ;; 1y) printf '12' ;; 2y) printf '24' ;;
    5y) printf '60' ;; 10y) printf '120' ;;
    *)  die "unknown horizon $1" ;;
  esac
}

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------

GIT_REV="$(git rev-parse --short HEAD 2>/dev/null || printf 'no-git')"
if [[ -n "$(git status --porcelain 2>/dev/null | head -1)" ]]; then
  GIT_REV="$GIT_REV+dirty"
fi

cat <<BANNER
============================================================================
AMAT comprehensive backtest campaign
============================================================================
  snapshot      $SNAPSHOT
  v3 scalars    ${SCALARS:-<none> -- v3 rows would be the identical v1 core}
  versions      $VERSIONS
  horizons      $HORIZONS
  initial cash  $INITIAL_CASH
  tier          $TIER
  workers       $WORKERS
  train split   $TRAIN_END (frozen; not a parameter of this run)
  campaign dir  $OUT_DIR
  git           $GIT_REV
  live trading  AMAT_LIVE_TRADING_ENABLED=$AMAT_LIVE_TRADING_ENABLED (backtest only)

  Every window ends on the snapshot's last session. Starts are derived from
  that end. Nothing here is out-of-sample evidence or a profitability claim.
BANNER

CAMPAIGN_START="$(date +%s)"

# Written before anything runs, so a campaign that dies mid-way still leaves a
# record of what it was trying to do and on which inputs.
if (( DRY_RUN == 0 )); then
  cat > "$OUT_DIR/manifest.json" <<MANIFEST
{
  "campaign": "$STAMP",
  "started_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "snapshot": "$SNAPSHOT",
  "versions": "$VERSIONS",
  "horizons": "$HORIZONS",
  "initial_cash": "$INITIAL_CASH",
  "train_end": "$TRAIN_END",
  "warmup_pad_months": $WARMUP_PAD_MONTHS,
  "montecarlo_paths": $MONTECARLO_PATHS,
  "projection_paths": $PROJECTION_PATHS,
  "heavytail_scenarios": $HEAVYTAIL_SCENARIOS,
  "heavytail_tail_index": $HEAVYTAIL_ALPHA,
  "control_seed": $CONTROL_SEED,
  "tier": "$TIER",
  "workers": $WORKERS,
  "git_rev": "$GIT_REV",
  "live_trading_enabled": "$AMAT_LIVE_TRADING_ENABLED",
  "caveat": "Burned history. Not out-of-sample evidence, not a forecast, not a profitability claim."
}
MANIFEST
fi

# ---------------------------------------------------------------------------
# PHASE 1 -- the comparable horizon table
# ---------------------------------------------------------------------------

phase "PHASE 1  trailing horizons (one run per version, sliced -- the comparable table)"

SWEEP_JSON="$OUT_DIR/horizon-sweep.json"
sweep_scalars=()
if [[ -n "$SCALARS" ]]; then
  sweep_scalars=(--scalars "$SCALARS")
fi
# shellcheck disable=SC2086  # $VERSIONS and $HORIZONS are validated allowlists
run_step "horizon-sweep" pyrun scripts/horizon_sweep.py \
  --versions $VERSIONS \
  --horizons $HORIZONS \
  --snapshot "$SNAPSHOT" \
  --initial-cash "$INITIAL_CASH" \
  "${sweep_scalars[@]}" \
  --out "$SWEEP_JSON"

# The end session comes from the sweep artifact, so every windowed run below
# is anchored to the snapshot's own last session rather than to today's date
# or to anything a human picked.
END_SESSION=""
if [[ -s "$SWEEP_JSON" ]]; then
  END_SESSION="$(pyrun -c '
import json, sys
print(json.load(open(sys.argv[1]))["end_session"])' "$SWEEP_JSON" 2>/dev/null || printf '')"
fi
if [[ ! "$END_SESSION" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  if (( DRY_RUN )); then
    END_SESSION="$(date -u +%Y-%m-%d)"
  else
    die "could not read the end session from $SWEEP_JSON; phase 1 must succeed first"
  fi
fi
printf '\n  end session: %s (every window below ends here)\n' "$END_SESSION"

start_for_horizon() {
  # Data start for a windowed run: the horizon plus the warm-up pad, so the
  # engine finishes its 252-session warm-up before the window it reports.
  local months back
  months="$(horizon_months "$1")"
  back=$(( months + WARMUP_PAD_MONTHS ))
  date -u -d "$END_SESSION -${back} months" +%Y-%m-%d
}

# ---------------------------------------------------------------------------
# PHASE 2 -- one windowed PDF per version per horizon
# ---------------------------------------------------------------------------

phase "PHASE 2  windowed reports ($(printf '%s' "$VERSIONS" | wc -w) versions x $(printf '%s' "$HORIZONS" | wc -w) horizons)"
printf '  Each run is padded %d months for warm-up; its headline metrics cover\n' "$WARMUP_PAD_MONTHS"
printf '  pad+horizon and are NOT the comparable table. Phase 1 is.\n\n'

for version in $VERSIONS; do
  for horizon in $HORIZONS; do
    start="$(start_for_horizon "$horizon")"
    out="$PDF_DIR/${version}-${horizon}-${STAMP}.pdf"
    # Sleeves are twelve extra per-asset runs. Keep them on the full-history
    # report where the per-asset diagnostic is read, off on the short windows
    # where it is noise -- a uniform rule, applied to every version alike.
    sleeve_flag=(--no-sleeves)
    if [[ "$horizon" == "10y" ]]; then
      sleeve_flag=()
    fi
    scalar_flag=()
    if [[ "$version" == "v3" && -n "$SCALARS" ]]; then
      scalar_flag=(--scalars "$SCALARS")
    fi
    run_step "backtest-${version}-${horizon}" \
      amat backtest -v "$version" \
        --snapshot "$SNAPSHOT" \
        --start "$start" \
        --end "$END_SESSION" \
        --initial-cash "$INITIAL_CASH" \
        --out "$out" \
        "${scalar_flag[@]}" \
        "${sleeve_flag[@]}"
  done
done

# ---------------------------------------------------------------------------
# PHASE 3 -- robustness batteries
# ---------------------------------------------------------------------------

phase "PHASE 3  robustness: stress battery, walk-forward, Monte Carlo, projections"

for version in $VERSIONS; do
  config="$(config_for "$version")"
  reason="$(validation_skip_reason "$version")"
  if [[ -n "$reason" ]]; then
    for command_name in stress heavytail walkforward montecarlo project; do
      skip_step "${command_name}-${version}" "$reason"
    done
    continue
  fi

  run_step "stress-${version}" \
    amat stress "$config" --snapshot "$SNAPSHOT" --initial-cash "$INITIAL_CASH"

  # Synthetic, and the only harness that can exceed the sample's own worst
  # move. Calibrated from the snapshot; no observed return sequence reaches
  # the strategy, so its output is never a backtest result.
  run_step "heavytail-${version}" \
    amat stress-heavytail "$config" --snapshot "$SNAPSHOT" \
      --initial-cash "$INITIAL_CASH" \
      --scenarios "$HEAVYTAIL_SCENARIOS" \
      --tail-index "$HEAVYTAIL_ALPHA" \
      --seed "$CONTROL_SEED"

  run_step "walkforward-${version}" \
    amat walkforward "$config" --snapshot "$SNAPSHOT" --initial-cash "$INITIAL_CASH"

  # shellcheck disable=SC2086  # PROJECTION_HORIZONS is a fixed numeric literal
  run_step "project-${version}" \
    amat project "$config" --snapshot "$SNAPSHOT" \
      --initial-cash "$INITIAL_CASH" \
      --funding "$INITIAL_CASH" \
      --horizon-years $PROJECTION_HORIZONS \
      --paths "$PROJECTION_PATHS" \
      --seed "$CONTROL_SEED" \
      --pdf "$PDF_DIR/${version}-projection-${STAMP}.pdf"

  if [[ "$TIER" == "full" ]]; then
    run_step "montecarlo-${version}" \
      amat montecarlo "$config" --snapshot "$SNAPSHOT" \
        --initial-cash "$INITIAL_CASH" \
        --paths "$MONTECARLO_PATHS" \
        --seed "$CONTROL_SEED" \
        --workers "$WORKERS" \
        --pdf "$PDF_DIR/${version}-montecarlo-${STAMP}.pdf"
  else
    skip_step "montecarlo-${version}" "--tier fast"
  fi
done

# ---------------------------------------------------------------------------
# PHASE 4 -- registered experiments and controls
#
# These run against v1 only and against the frozen split. A winner here earns
# a registry entry and the full protocol; it never becomes an in-place change,
# and this script has no mechanism to make one.
# ---------------------------------------------------------------------------

phase "PHASE 4  registered experiments and controls (v1, split $TRAIN_END, ranking-blind validation)"

V1_CONFIG="$(config_for v1)"

if [[ "$TIER" == "full" ]]; then
  run_step "sweep-exit-rules" \
    amat sweep-exit-rules "$V1_CONFIG" --snapshot "$SNAPSHOT" \
      --initial-cash "$INITIAL_CASH" \
      --train-end "$TRAIN_END" \
      --random-control 20 \
      --control-seed "$CONTROL_SEED"

  run_step "sweep-target-vol" \
    amat sweep-target-vol "$V1_CONFIG" --snapshot "$SNAPSHOT" \
      --initial-cash "$INITIAL_CASH" \
      --train-end "$TRAIN_END"

  run_step "regime-experiment" \
    amat regime-experiment "$V1_CONFIG" --snapshot "$SNAPSHOT" \
      --initial-cash "$INITIAL_CASH" \
      --seed "$CONTROL_SEED"

  run_step "experiment-0004-tlt-variants" pyrun scripts/experiment_0004_tlt_variants.py
  run_step "experiment-0005-neighborhood" pyrun scripts/parameter_neighborhood.py
else
  skip_step "sweep-exit-rules"             "--tier fast"
  skip_step "sweep-target-vol"             "--tier fast"
  skip_step "regime-experiment"            "--tier fast"
  skip_step "experiment-0004-tlt-variants" "--tier fast"
  skip_step "experiment-0005-neighborhood" "--tier fast"
fi

# ---------------------------------------------------------------------------
# PHASE 5 -- per-asset attribution
# ---------------------------------------------------------------------------

phase "PHASE 5  per-asset audits and trade quality"

run_step "benchmarks" pyrun scripts/benchmarks.py --snapshot "$SNAPSHOT"
run_step "trade-quality-v1" pyrun scripts/trade_quality.py \
  --config "$V1_CONFIG" --snapshot "$SNAPSHOT" --initial-cash "$INITIAL_CASH"

if [[ "$TIER" == "full" ]]; then
  UNIVERSE="$(pyrun -c '
from amat.config.loading import load_strategy_config
strategy = load_strategy_config("configs/strategy/amat_v1.toml").strategy
print(" ".join(str(s) for s in strategy.universe.symbols))' 2>/dev/null || printf '')"
  for symbol in $UNIVERSE; do
    # Symbols come from the validated configuration, not from user input, and
    # are re-checked here before reaching a command line.
    [[ "$symbol" =~ ^[A-Z][A-Z0-9.]{0,9}$ ]] || die "refusing malformed symbol: $symbol"
    run_step "asset-audit-${symbol}" pyrun scripts/asset_audit.py --symbol "$symbol"
  done
else
  run_step "asset-audit-TLT" pyrun scripts/asset_audit.py --symbol TLT
fi

# ---------------------------------------------------------------------------
# PHASE 6 -- engine and operational checks
# ---------------------------------------------------------------------------

phase "PHASE 6  engine diagnostics and operational surfaces"

run_step "synthetic-cost-ladder" \
  amat backtest-synthetic "$V1_CONFIG" --cost-ladder --seed 7 --initial-cash "$INITIAL_CASH"

run_step "validate-config-v1" amat validate-config "$V1_CONFIG"
run_step "show-universe-v1"   amat show-universe "$V1_CONFIG"
run_step "show-environment"   amat show-environment

# --offline: replay the snapshot, perform no network fetch. Neither command
# places an order; `plan` prints target positions and `simulate` journals a
# paper account. The repository has no order-submission path at all.
run_step "plan-v1" \
  amat plan "$V1_CONFIG" --snapshot "$SNAPSHOT" --equity "$INITIAL_CASH" --offline \
    --out "$OUT_DIR/plan-v1.json"

run_step "simulate-v1" \
  amat simulate "$V1_CONFIG" --snapshot "$SNAPSHOT" --equity "$INITIAL_CASH" --offline \
    --journal "$OUT_DIR/journal-v1"

if [[ "$TIER" == "full" ]]; then
  run_step "simulate-v6" \
    amat simulate "$(config_for v6)" --snapshot "$SNAPSHOT" --equity "$INITIAL_CASH" --offline \
      --journal "$OUT_DIR/journal-v6"
else
  skip_step "simulate-v6" "--tier fast"
fi

# ---------------------------------------------------------------------------
# PHASE 7 -- gates
# ---------------------------------------------------------------------------

phase "PHASE 7  quality gates"

if [[ "$TIER" == "full" ]]; then
  run_step "pytest"          uv run pytest -q
  run_step "ruff-check"      uv run ruff check .
  run_step "ruff-format"     uv run ruff format --check .
  run_step "mypy"            uv run mypy src
  run_step "quality-gate"    bash .claude/scripts/quality_gate.sh full
  run_step "validate-bundle" pyrun .claude/scripts/validate_bundle.py
else
  run_step "ruff-check"   uv run ruff check .
  run_step "mypy"         uv run mypy src
  skip_step "pytest"          "--tier fast"
  skip_step "ruff-format"     "--tier fast"
  skip_step "quality-gate"    "--tier fast"
  skip_step "validate-bundle" "--tier fast"
fi

# ---------------------------------------------------------------------------
# Manifest and summary
# ---------------------------------------------------------------------------

CAMPAIGN_END="$(date +%s)"
ELAPSED=$(( CAMPAIGN_END - CAMPAIGN_START ))

if (( DRY_RUN == 0 )); then
  pyrun - "$OUT_DIR" "$STEPS_TSV" <<'PY'
"""Fold the step log into a campaign manifest beside the artifacts."""
import json
import sys
from pathlib import Path

out_dir = Path(sys.argv[1])
rows = []
with Path(sys.argv[2]).open(encoding="utf-8") as handle:
    header = handle.readline().rstrip("\n").split("\t")
    for line in handle:
        parts = line.rstrip("\n").split("\t")
        if len(parts) == len(header):
            rows.append(dict(zip(header, parts, strict=True)))

manifest_path = out_dir / "manifest.json"
existing = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
existing["steps"] = rows
existing["counts"] = {
    status: sum(1 for row in rows if row["status"] == status)
    for status in ("ok", "fail", "skipped", "planned")
}
manifest_path.write_text(json.dumps(existing, indent=2, sort_keys=True), encoding="utf-8")
print(f"manifest: {manifest_path}")
PY

  cat > "$OUT_DIR/README.md" <<MD
# Comprehensive backtest campaign $STAMP

Produced by \`scripts/comprehensive_backtest.sh\`. Measurement only: nothing in
this directory is out-of-sample evidence, a forecast, or a profitability claim.

| | |
|---|---|
| snapshot | \`$SNAPSHOT\` |
| end session | $END_SESSION (every window ends here) |
| versions | $VERSIONS |
| horizons | $HORIZONS |
| initial cash | $INITIAL_CASH |
| training split | $TRAIN_END (frozen; not a parameter of this run) |
| tier | $TIER |
| git | $GIT_REV |
| elapsed | ${ELAPSED}s |

## What to read, in order

1. **\`horizon-sweep.json\`** and \`logs/horizon-sweep.log\` — the comparable
   table. Each version ran once over the full history; every horizon is a
   trailing slice of that single equity curve, measured by one function, with
   warm-up and open positions carried in.
2. \`logs/stress-*.log\` — cost ladder, execution delays, missed fills,
   session-close fills, leave-one-out survivorship bound, trade and year
   removals, the signal-free allocator control, benchmark correlations.
3. \`logs/project-*.log\` and the projection PDFs — outcome distributions from
   resampling each run's own net returns. Within-resampling frequencies,
   never forecasts.
4. \`outputs/reports/v*-{6m,1y,2y,5y,10y}-$STAMP.pdf\` — standalone windowed
   reports. **Their headline metrics are not comparable** to item 1 or to each
   other: each was padded $WARMUP_PAD_MONTHS months so the engine could
   complete warm-up inside the run, and that pad is inside the reported span.

## Coverage gaps in this campaign

- **v4** is measurable by \`amat backtest\` / \`amat study-v4\` only. Its
  switching configuration carries \`[trend]\`/\`[defensive]\`/\`[switching]\`
  tables that the plain strategy loader used by \`stress\`, \`walkforward\`,
  \`montecarlo\` and \`project\` rejects by name. Those four rows are skipped,
  not silently omitted.
- **v3 is its scalar series.** Phase 1 and phase 2 pass
  \`${SCALARS:-<none>}\`, so those rows are genuinely conditioned. Phase 3 is
  skipped for v3 because \`stress\`, \`walkforward\`, \`montecarlo\` and
  \`project\` have no scalars flag — they would have run the identical v1 core
  and printed it under a v3 heading.
- Engine parity does not exist (single research runner), the holdout stays
  locked, and no paper period has been served.

## Files

- \`steps.tsv\` / \`manifest.json\` — every step, its exit code and duration.
- \`logs/\` — full stdout and stderr per step.
- \`plan-v1.json\`, \`journal-v1/\`, \`journal-v6/\` — operational surfaces,
  produced \`--offline\` from snapshot data. No order was submitted.
MD
fi

printf '\n============================================================================\n'
printf 'CAMPAIGN COMPLETE  %ds elapsed\n' "$ELAPSED"
printf '============================================================================\n'
printf '  ok %d   failed %d   skipped %d\n' "$STEP_OK" "$STEP_FAIL" "$STEP_SKIP"
printf '  campaign dir: %s\n' "$OUT_DIR"
printf '  reports:      %s/v*-%s.pdf\n' "$PDF_DIR" "$STAMP"

if (( STEP_FAIL > 0 )); then
  printf '\n  \033[31mfailed steps:\033[0m\n'
  for failure in "${FAILED_STEPS[@]}"; do
    printf '    - %s\n' "$failure"
  done
  printf '\n  A partial campaign is reported as partial. Read the logs above.\n'
  exit 1
fi

printf '\n  Every step exited 0. Read %s/README.md before citing any number.\n' "$OUT_DIR"
