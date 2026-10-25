# AMAT commands, processes and important paths

Verified against the local CLI parsers, `--help` output and configuration on **2026-09-12**. This sheet documents commands; creating it did not start a research run, review, scheduler or executor.

## 1. Set up one terminal

```bash
AMAT=/home/supasuge/Documents/amat-workspace/amat
RESEARCH_ROOT=/home/supasuge/Documents/amat-research
CHAIN="$AMAT/configs/autoresearch/llm-chain.example.toml"
ENV_FILE="$AMAT/.env"
SNAPSHOT="$AMAT/data/snapshots/alpaca-sip-2026-08-11"
SESSION="$RESEARCH_ROOT/workflow-four-20260912-143700-run4"
CANDIDATE=a7273f02c96bc7a08ae47ffd4f9c60f98b1abf26cafed960899648a3cf8d4332
cd "$AMAT"
```

`SESSION` and `CANDIDATE` initially point to Run 4 from the recent daily study. Change them when working on another daily session. Replace `ENV_FILE` if your keys live in another dotenv file. Model commands below explicitly load this file; `research.sh` itself does not load dotenv files. Existing exported credentials may take precedence over dotenv entries.

The commands below assume this working directory unless a different `cd` is shown. Optional short functions:

```bash
# New-session and report shortcuts, with credentials loaded by uv.
research() {
  uv run --locked --env-file "$ENV_FILE" ./research "$@"
}

# Use the selected daily session's frozen controller.
session() {
  uv run --locked --env-file "$ENV_FILE" "$SESSION/research.sh" "$@"
}

# Independent portfolio-team operations using that same frozen runtime.
team() {
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$SESSION/runtime" \
    uv run --locked --env-file "$ENV_FILE" \
    python -m portfolio_team.cli --root "$SESSION" "$@"
}
```

These functions are optional conveniences for the current terminal, not installed commands or scheduled jobs. `team()` assumes a daily session that includes the new frozen portfolio-team package, such as the four recent daily runs.

## 2. Start daily strategy research

| Process | Command | Behavior |
|---|---|---|
| Normal research | `research 5` | New session; five distinct completed candidates requested; attempts live model proposals, normal built-in fallback and model summaries |
| One quick historical run | `research 1 --robustness historical --no-open` | Same historical robustness mode used in the four-run comparison |
| Extended stress scenarios | `research 5 --robustness extended` | Includes additional hypothetical diagnostics; this is the normal shortcut's default |
| Give research direction | `research 5 --brief "Test one causal change at a time; specify mechanism and falsification."` | Brief is frozen into the new session |
| Choose a chain file | `research 5 --chain "$CHAIN"` | Explicit ordered model routing |
| Set model accounting budget | `research 5 --max-model-tokens-per-day 300000` | Per-session daily budget; proposals and summaries share it |
| Built-in proposals | `research 5 --built-in --no-open` | Deterministic proposal source; **model reviews are still attempted** |
| Name the session directory | `research 5 --workspace "$RESEARCH_ROOT/my-new-session" --no-open` | Directory must be new and outside the project checkout |

The normal shortcut uses the operator-selected research baseline, currently **E73**. It requests `3 × N` maximum experiment attempts and a stall limit of `3 × N`, uses a 900-second worker limit, and defaults to a 300,000-token daily accounting budget. It freezes development end **2022-12-30** and checkpoint end **2026-08-10**. `--robustness historical` and `extended` are different evaluation contracts.

**Four separate sequential runs**, allowing each new session to freeze earlier compatible results:

```bash
for run_number in 1 2 3 4; do
  research 1 --robustness historical --no-open || break
done
```

This differs from `research 4`, which tests four candidates inside one session. Four new sessions have four separate model budgets. Their baseline stays fixed unless an operator changes the active selection between runs. The four-run study itself froze the selection once; its exact runner is saved with the study artifacts.

**New research with no model calls**:

```bash
uv run --locked --no-env-file env \
  -u GEMINI_API_KEY -u OPENROUTER_API_KEY -u NVIDIA_API_KEY -u OPENAI_API_KEY \
  autoresearch start 5 --built-in --robustness historical --no-open
```

This uses the existing daily snapshot, suppresses dotenv loading and removes model keys only from the child process. Model summaries will be recorded as unavailable. It does not change your saved credentials.

## 3. Inspect runs, evidence and charts

| Process | Command |
|---|---|
| Latest session status | `research status` |
| Latest session report | `research view` |
| Explicit daily session status | `session status --human` |
| Explicit daily session report | `session view` |
| Export recorded analysis | `session analyze` |
| Open the session's failure report | `session failures` |
| Build/open the cross-session failure library | `research failures --all` |
| Retrieve compatible prior evidence | `session related --query "momentum confirmation"` |
| Retrieve using a particular recipe | `session related --candidate "$CANDIDATE"` |
| Generate verified charts | `session charts --candidate "$CANDIDATE"` |
| Read cached quantitative diagnostics | `session diagnostics --candidate "$CANDIDATE"` |
| List available feature formulas | `session features` |
| Retry an experiment's model summary | `session summarize --experiment 2 --summary-chain "$CHAIN"` |

Without `--candidate`, charts/diagnostics target the incumbent. Summary retry uses the existing session budget. `analyze`, `charts` and `diagnostics` inspect recorded measurements; they do not start a new strategy experiment. Export/inspection operations may refuse while a writer owns the session; status remains available.

**Shortcut distinction:** `./research charts` and `./research related` are not valid top-level shortcuts. Use `session charts`, `session related`, or the explicit `autoresearch` subcommand with a workspace argument.

**Latest-pointer distinction:** at verification time, `latest-session.json` points to:

`/home/supasuge/Documents/amat-research/intraday-50-final-20260912`

The four-run daily study did not update that pointer. Use `SESSION` or an explicit workspace to inspect the daily results. New ordinary `research N` runs update the latest pointer.

## 4. Continue a daily session or execute a specified recipe

```bash
# Model proposals: this lower-level loop pauses if no provider produces a proposal.
# It does not enable the automatic fallback used by the normal start shortcut.
session loop --iterations 1 --chain "$CHAIN"

# Built-in proposals in the existing session; its configured summaries still apply.
session loop --iterations 1

# Execute a complete example Proposal JSON through the frozen measurement engine.
session run-one "$AMAT/configs/autoresearch/trend-breakout.example.json"
```

The session's existing experiment, stall and model budgets still apply; continuing does not reset them. Use the session's frozen launcher after application source changes. Do not edit its `program.json`, frozen runtime, snapshot or hashes to change its contract; initialize a new session instead.

### Advanced: explicit program initialization

```bash
NEW_SESSION="$RESEARCH_ROOT/custom-program-01"
uv run --locked --env-file "$ENV_FILE" autoresearch init "$NEW_SESSION" \
  --config "$AMAT/configs/strategy/amat_v6.toml" \
  --snapshot "$SNAPSHOT" \
  --name custom-program-01 \
  --train-end 2022-12-30 --validation-end 2026-08-10 \
  --max-experiments 15 --stall-limit 15 \
  --timeout-seconds 900 --max-model-tokens-per-day 300000 \
  --robustness historical --summary-chain "$CHAIN"

SESSION="$NEW_SESSION"
session loop --iterations 5 --chain "$CHAIN"
```

`init` creates the frozen program but does not run experiments or update the latest pointer. Unlike normal `start`, it uses the supplied config directly and **does not apply the active E73 promotion**. Omitting `--summary-chain` does not disable summaries when the default chain file exists.

### Deliberate checkpoint evaluation

```bash
session checkpoint
```

This evaluates the baseline and incumbent on the frozen validation period and records checkpoint evidence. It changes the session's evidence exposure: checkpoint-exposed sessions are excluded from new cross-session proposal memory. Use it as a deliberate validation step, not a routine status command.

## 5. Run the independent multi-agent portfolio review

The short summaries inside daily research and the `pmteam` committee are separate processes. `pmteam` supports `fake`, `gemini` and `openai`; it does not consume the daily OpenRouter/NVIDIA chain file. Its default provider is `fake`, so specify a real provider explicitly for real reviews.

For the selected daily session:

```bash
# Import its declared candidate/control comparisons.
team import-catalog "$SESSION/catalog.toml"

# Check dispatch readiness without making model API calls.
# experiment_2 is the comparison ID in each recent one-candidate session.
team preflight experiment_2 --provider gemini --model gemini-3.8-flash

# Review one declared comparison.
team review experiment_2 --provider gemini --model gemini-3.8-flash

# Or import and review up to three declared comparisons in one bounded cycle.
team cycle "$SESSION/catalog.toml" --max-reviews 3 \
  --provider gemini --model gemini-3.8-flash

team status
```

Choose `review` or `cycle` for the desired job. Completed review state is durable; a repeated command may reuse existing work rather than create a fresh independent opinion. Use comparison IDs from the catalog, not candidate digests. Model availability and provider quotas still apply.

Use the printed review ID for inspection:

```bash
REVIEW_ID='PASTE_REVIEW_ID'
team show "$REVIEW_ID"
team replay "$REVIEW_ID" --offline
```

Useful review options: `--single-reviewer` on `review`/`preflight`; `--request-timeout 90`; `--review-timeout 480`; `--concurrent-requests 1` (allowed 1–3). `--chief-model MODEL` routes the chief to OpenAI and requires `OPENAI_API_KEY`; it is not a generic provider-chain switch. `--provider fake` is scripted workflow testing, not a real model assessment.

## 6. Plan and execute a matched follow-up

Requires a completed source review with an eligible proposed task:

```bash
team plan "$REVIEW_ID"

# Read research_queue.v2.json (or the legacy queue file) in the review directory.
TASK_ID='PASTE_TASK_ID'
team approve-task "$REVIEW_ID" "$TASK_ID" --by YOUR_NAME
team dispatch-task "$REVIEW_ID" "$TASK_ID"

# Run the approved operation through the frozen research worker.
session follow-up "$REVIEW_ID" "$TASK_ID"
```

`dispatch-task` emits the validated task specification; it does not run the backtest. The registered adapter currently supports **parameter-free matched_backtest** tasks from compatible daily-session evidence. It runs candidate and control, registers new evidence, and reuses a verified completed side after interruption. Other task operations remain unsupported. The receipt identifies the new catalog for the next `team cycle`.

`team execute-task "$REVIEW_ID" "$TASK_ID" --run` is the equivalent explicit execution route when the frozen team function above is used. Without `--run`, it only produces the execution-gate result.

## 7. Start or resume intraday research

The intraday workflow is separate from daily sessions and uses actual minute data:

```bash
MINUTE_DATA="$RESEARCH_ROOT/intraday-data-sip-2022-2025"

# New intraday session using the verified existing minute dataset.
research 5 --intraday --minute-data "$MINUTE_DATA" --no-open

# Resume the existing intraday program; requested count comes from its frozen state.
research --intraday --resume \
  --workspace "$RESEARCH_ROOT/intraday-50-final-20260912" --no-open

# Explicit inspection.
research status "$RESEARCH_ROOT/intraday-50-final-20260912"
research view "$RESEARCH_ROOT/intraday-50-final-20260912"
research analyze "$RESEARCH_ROOT/intraday-50-final-20260912"
```

Omitting the minute dataset can trigger an Alpaca minute-data download when the default dataset is absent. Intraday does not support `--built-in` or `--brief`. Do not use the daily `session charts`, `related`, or `loop` commands on an intraday workspace.

## 8. Select a starting strategy for future daily research

```bash
research active

# Explicit operator selection: replace all placeholders before use.
SELECTION_SESSION='/absolute/path/to/the/source/daily-session'
SELECTED_CANDIDATE='PASTE_CANDIDATE_DIGEST'
research promote "$SELECTION_SESSION" \
  --candidate "$SELECTED_CANDIDATE" \
  --label 'Descriptive strategy label' \
  --reason 'Documented reason for this operator selection'
```

This changes the starting baseline for **new normal daily research sessions**. It preserves the recorded automatic decision, can explicitly select a measured discarded candidate, and does not deploy to the executor. Current selection: E73, candidate `305cedf4d1f02f4d51777f3527c1fafb91373ed08563e4e671b034718cff652a`.

## 9. Standalone backtesting, validation and observations

These commands use the specified/versioned TOML strategy; they **do not load the active E73 candidate JSON**. Their load-start/warm-up rules also differ from a frozen autoresearch measured anchor, so use the recorded sample dates when comparing results.

```bash
# Standard v6 backtest on real snapshot data, ending within the development period.
uv run --locked amat backtest -v v6 --snapshot "$SNAPSHOT" \
  --end 2022-12-30 --no-sleeves

# Frozen v1 core examples for the general study/validation commands.
CORE_CONFIG="$AMAT/configs/strategy/amat_v1.toml"
uv run --locked amat study "$CORE_CONFIG" --snapshot "$SNAPSHOT" \
  --end 2022-12-30 --no-sleeves --html "$AMAT/outputs/core-study.html"
uv run --locked amat walkforward "$CORE_CONFIG" --snapshot "$SNAPSHOT" --end 2022-12-30
uv run --locked amat stress "$CORE_CONFIG" --snapshot "$SNAPSHOT" --end 2022-12-30
uv run --locked amat montecarlo "$CORE_CONFIG" --snapshot "$SNAPSHOT" \
  --end 2022-12-30 --paths 200 --seed 42 --workers 2

# One observation-only market desk pass: morning, midday or evening.
uv run --locked --env-file "$ENV_FILE" deskreview morning "$CORE_CONFIG" \
  --snapshot "$SNAPSHOT" --json

# News-store coverage; ingestion contacts the configured vendor.
uv run --locked newsdesk coverage
uv run --locked --env-file "$ENV_FILE" newsdesk ingest-alpaca \
  --symbols SPY,QQQ --start 2026-09-01 --end 2026-09-02
```

Historical news ingestion records the actual ingestion time; it does not make old articles newly knowable to an old backtest. The `amat report` command uses **synthetic data**; use `backtest` or `study` for market-history results. The retired v2 runner remains unavailable even though the backtest version selector retains its rejection record.

## 10. Paper executor operations — separate process

The executor uses its own directory and dotenv configuration:

```bash
cd /home/supasuge/Documents/amat-workspace/executor

# Inspect the paper account or verify settings and planning; no orders placed.
uv run --locked --env-file .env executor account
uv run --locked --env-file .env executor check

# Container status and logs.
docker compose ps
docker compose logs --tail 100 executor

# Start the configured paper-trading daemon; it can submit paper orders.
docker compose up -d executor

# Stop the daemon.
docker compose stop executor

cd "$AMAT"
```

`executor` is installed in the executor project's environment, not AMAT's. Local `account`/`check` require `.env` paths appropriate for the host; Docker supplies its container-specific `AMAT_ROOT` and state paths. `docker compose up -d` starts the configured image; deploying changed source also requires a deliberate image rebuild.

Additional explicit commands: `executor calendar`, `executor daemon`, `executor submit --session YYYY-MM-DD`, and `executor report --session YYYY-MM-DD`. `submit` places eligible paper orders; `report` records equity and may send configured email. `DRY_RUN=true` suppresses submission; its code default is false. The running container's named state volume is distinct from the checkout's local state directory.

## 11. Important paths and configs

| Path | Purpose / what to change here |
|---|---|
| [/home/supasuge/Documents/amat-workspace/research](/home/supasuge/Documents/amat-workspace/research) | Main convenience launcher; AMAT's `research` file forwards here |
| [/home/supasuge/Documents/amat-workspace/amat](/home/supasuge/Documents/amat-workspace/amat) | AMAT project and working directory for research commands |
| [/home/supasuge/Documents/amat-research](/home/supasuge/Documents/amat-research) | Isolated daily/intraday workspaces and selection records |
| [/home/supasuge/Documents/amat-research/latest-session.json](/home/supasuge/Documents/amat-research/latest-session.json) | Target of latest status/view shortcuts; currently an intraday session |
| [/home/supasuge/Documents/amat-research/active-strategy.json](/home/supasuge/Documents/amat-research/active-strategy.json) | Hash-verified pointer to the operator-selected baseline record; use `research promote` to change selection |
| [/home/supasuge/Documents/amat-research/promotions](/home/supasuge/Documents/amat-research/promotions) | Immutable operator-selection records, including full candidate recipes |
| [/home/supasuge/Documents/amat-workspace/amat/configs/autoresearch/llm-chain.example.toml](/home/supasuge/Documents/amat-workspace/amat/configs/autoresearch/llm-chain.example.toml) | Ordered daily proposal/review model targets; copy to a new file for a variant chain |
| [/home/supasuge/Documents/amat-workspace/amat/configs/autoresearch](/home/supasuge/Documents/amat-workspace/amat/configs/autoresearch) | Complete Proposal examples: trend-breakout, momentum-volume, compression-breakout, fitted-trend-structure, regime-router, breadth-router, relative-participation, path-recovery-participation and combined-patterns |
| [/home/supasuge/Documents/amat-workspace/amat/configs/strategy/amat_v6.toml](/home/supasuge/Documents/amat-workspace/amat/configs/strategy/amat_v6.toml) | Base daily configuration; active E73 adds its stored candidate signal on top |
| [/home/supasuge/Documents/amat-workspace/amat/configs/strategy/amat_v1.toml](/home/supasuge/Documents/amat-workspace/amat/configs/strategy/amat_v1.toml) | Frozen core configuration for general study/validation examples |
| [/home/supasuge/Documents/amat-workspace/amat/configs/governance/initial-reviews.toml](/home/supasuge/Documents/amat-workspace/amat/configs/governance/initial-reviews.toml) | Declared project-level comparisons; a session's own catalog is used for session reviews |
| [/home/supasuge/Documents/amat-workspace/amat/configs/governance/review-evaluation.toml](/home/supasuge/Documents/amat-workspace/amat/configs/governance/review-evaluation.toml) | Portfolio-review evaluation cases |
| [/home/supasuge/Documents/amat-workspace/amat/configs/data/sources.toml](/home/supasuge/Documents/amat-workspace/amat/configs/data/sources.toml) | Data capability catalog and credential variable names; some sources are declaration-only |
| [/home/supasuge/Documents/amat-workspace/amat/data/snapshots/alpaca-sip-2026-08-11](/home/supasuge/Documents/amat-workspace/amat/data/snapshots/alpaca-sip-2026-08-11) | Daily snapshot used by current examples |
| [/home/supasuge/Documents/amat-research/intraday-data-sip-2022-2025](/home/supasuge/Documents/amat-research/intraday-data-sip-2022-2025) | Existing minute-data dataset and manifest |
| [/home/supasuge/Documents/amat-workspace/amat/configs/news/themes.toml](/home/supasuge/Documents/amat-workspace/amat/configs/news/themes.toml) | News theme configuration |
| [/home/supasuge/Documents/amat-workspace/amat/.env](/home/supasuge/Documents/amat-workspace/amat/.env) | Research/data dotenv file; other local dotenv variants also exist, so select one explicitly |
| [/home/supasuge/Documents/amat-workspace/executor/.env](/home/supasuge/Documents/amat-workspace/executor/.env) | Paper broker keys, paths, risk limits, scheduling, dry-run and email settings |
| [/home/supasuge/Documents/amat-workspace/executor/.env.example](/home/supasuge/Documents/amat-workspace/executor/.env.example) | Executor setting names and example values |
| [/home/supasuge/Documents/amat-workspace/executor/docker-compose.yml](/home/supasuge/Documents/amat-workspace/executor/docker-compose.yml) | Paper daemon container, mounts, named volumes and restart policy |
| [/home/supasuge/Documents/amat-workspace/amat/deploy/amat-research.service.example](/home/supasuge/Documents/amat-workspace/amat/deploy/amat-research.service.example) | Example one-shot portfolio-review service; separate from daily strategy generation |
| [/home/supasuge/Documents/amat-workspace/amat/deploy/amat-research.timer.example](/home/supasuge/Documents/amat-workspace/amat/deploy/amat-research.timer.example) | Example 15-minute timer; templates are not enabled by running research. Review their older model settings before adoption |

### Current model and credential names

| Workflow | Configuration |
|---|---|
| Daily proposal/review order | `gemini / gemini-3.8-flash` → `openrouter / openrouter/free` → `nvidia / nvidia/nemotron-3-ultra-550b-a55b` |
| Model environment variables | `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `NVIDIA_API_KEY`; `OPENAI_API_KEY` for explicitly configured OpenAI calls |
| Market data / Alpaca news | `ALPACA_MARKET_API_KEY`, `ALPACA_MARKET_API_SECRET` |
| Paper account | `ALPACA_PAPER_API_KEY`, `ALPACA_PAPER_API_SECRET` in the executor environment |
| Executor paths and mode | `AMAT_ROOT`, `STRATEGY_CONFIG`, `SNAPSHOT`, `STATE_ROOT`, `DRY_RUN` |

Model names above are the current saved configuration, not a promise of provider availability. No credential values are included in this sheet.

### Inside the selected daily session

Paths below expand under the concrete `SESSION` set at the top:

| Relative to `SESSION` | Meaning |
|---|---|
| `research.sh` | Frozen CLI launcher |
| `program.json`, `program.sha256` | Evaluation contract and integrity hash |
| `base.toml`, `uv.lock` | Base config and dependency lock |
| `runtime/`, `snapshot/` | Frozen implementation and data |
| `runtime/research-memory.json` | Frozen compatible prior evidence and exclusion reasons |
| `runtime/research-brief.txt` | Research brief |
| `runtime/research-promotion.json` | Selected starting recipe/provenance, when promotion was applied |
| `research.sqlite3` | Experiments, model calls, events, cache and durable state |
| `candidates/` | Content-addressed executable candidate recipes |
| `outputs/runs/<candidate>/` | Primary/stress/control runs, objective and diagnostics |
| `outputs/summaries/` | Model-review outcomes, including unavailable reviews |
| `report.html`, `report.json`, `report.md` | Session reports |
| `analysis/` | Analysis exports, failure report and chart HTML/JSON |
| `catalog.toml` | Candidate/control comparisons for portfolio review |
| `data/governance/pmteam/registry.sqlite3` | Session-specific review evidence and decisions |
| `outputs/governance/<review_id>/` | Review reports, task queue, approvals and execution receipts |
| `outputs/followups/` | Matched follow-up runs and registered comparison catalogs |
| `checkpoint.json`, `checkpoints/` | Deliberate validation outputs, when requested |

The relative session paths above are templates, not files to edit to override the frozen contract. Project-level `pmteam` without `--root "$SESSION"` instead writes under AMAT's own `data/governance/pmteam` and `outputs/governance`.

## 12. Recent results and verification

- [Four-run analysis](/home/supasuge/Documents/amat-workspace/amat/docs/workflow-four-run-results-2026-09-12.md)
- [Four-run artifacts and manifest](/home/supasuge/Documents/amat-research/workflow-four-results-20260912-143700)
- [Daily Run 1](/home/supasuge/Documents/amat-research/workflow-four-20260912-143700-run1/report.html)
- [Daily Run 2](/home/supasuge/Documents/amat-research/workflow-four-20260912-143700-run2/report.html)
- [Daily Run 3](/home/supasuge/Documents/amat-research/workflow-four-20260912-143700-run3/report.html)
- [Daily Run 4](/home/supasuge/Documents/amat-research/workflow-four-20260912-143700-run4/report.html)
- [Current latest intraday report](/home/supasuge/Documents/amat-research/intraday-50-final-20260912/report.html)
- [Research-tools guide](/home/supasuge/Documents/amat-workspace/amat/docs/research-tools.md)

Known workflow limitations from the four-run study still apply: incomplete model recipes can disagree with their explanations; automated reviews can be unavailable; only the first matched-follow-up operation is registered. The commands above expose current behavior rather than implying those remaining improvements are implemented.

For code validation, from AMAT:

```bash
uv run --locked ruff check src
uv run --locked mypy src
uv run --locked pytest -q
```

For any additional command options, use `uv run --locked autoresearch --help`, `uv run --locked pmteam --help`, `uv run --locked amat --help`, or the corresponding subcommand followed by `--help`.
